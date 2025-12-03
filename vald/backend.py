"""
Backend processing functions for direct VALD request submission.
Bypasses email system and calls parserequest binary directly.
"""
import os
import re
import subprocess
import queue
import threading
from pathlib import Path
from django.conf import settings
from django.core.mail import send_mail


class QueueFullError(Exception):
    """Raised when job queue is full and cannot accept new requests."""
    pass


class JobQueue:
    """
    FIFO job queue with worker threads to limit parallel job execution.

    Ensures only N jobs run concurrently while maintaining submission order.
    Jobs submitted while workers are busy will wait in queue.
    """

    def __init__(self, max_workers=2, max_queue_size=10):
        """
        Initialize job queue with worker threads.

        Args:
            max_workers: Maximum number of jobs to run in parallel (default: 2)
            max_queue_size: Maximum number of jobs waiting in queue (default: 10)
        """
        self.job_queue = queue.Queue(maxsize=max_queue_size)
        self.max_workers = max_workers
        self.max_queue_size = max_queue_size
        self.workers = []
        self._start_workers()

    def _start_workers(self):
        """Start worker threads that process jobs from the queue."""
        for i in range(self.max_workers):
            worker = threading.Thread(
                target=self._worker,
                name=f"VALDJobWorker-{i}",
                daemon=True
            )
            worker.start()
            self.workers.append(worker)

    def _worker(self):
        """Worker thread function - processes jobs from queue until program exits."""
        while True:
            job_func, result_queue = self.job_queue.get()
            try:
                result = job_func()
                result_queue.put(('success', result))
            except Exception as e:
                result_queue.put(('error', str(e)))
            finally:
                self.job_queue.task_done()

    def submit(self, job_func):
        """
        Submit a job to the queue and wait for result.

        Args:
            job_func: Callable that executes the job and returns result

        Returns:
            Result from job_func()

        Raises:
            QueueFullError: If queue is full and cannot accept new jobs
            Exception: If job_func raises an exception
        """
        result_queue = queue.Queue()
        try:
            self.job_queue.put_nowait((job_func, result_queue))
        except queue.Full:
            raise QueueFullError(
                f"Server is busy processing requests. Queue limit ({self.max_queue_size}) reached. "
                "Please try again in a few minutes."
            )
        status, result = result_queue.get()

        if status == 'error':
            raise Exception(result)
        return result


# Global job queue instance
_job_queue = None
_queue_lock = threading.Lock()


def get_job_queue():
    """
    Get or create the global job queue instance.

    Returns:
        JobQueue: Singleton job queue instance
    """
    global _job_queue

    if _job_queue is None:
        with _queue_lock:
            # Double-check locking pattern
            if _job_queue is None:
                max_workers = getattr(settings, 'VALD_MAX_WORKERS', 2)
                max_queue_size = getattr(settings, 'VALD_MAX_QUEUE_SIZE', 10)
                _job_queue = JobQueue(max_workers, max_queue_size)

    return _job_queue


def notify_queue_full():
    """Send email notification to webmaster when queue is full."""
    webmaster_email = getattr(settings, 'VALD_WEBMASTER_EMAIL', None)
    if not webmaster_email:
        return

    try:
        send_mail(
            subject='[VALD] Job queue full - requests being rejected',
            message=(
                'The VALD job queue has reached its maximum size and is rejecting new requests.\n\n'
                'This may indicate high load or stuck jobs. Please check the server.\n\n'
                f'Queue settings: VALD_MAX_QUEUE_SIZE={getattr(settings, "VALD_MAX_QUEUE_SIZE", 10)}, '
                f'VALD_MAX_WORKERS={getattr(settings, "VALD_MAX_WORKERS", 2)}'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[webmaster_email],
            fail_silently=True,
        )
    except Exception:
        pass  # Don't let email failure break request handling


def check_queue_capacity():
    """
    Check if the job queue has capacity for new requests.
    Only counts requests from the last 30 minutes to avoid stuck requests blocking the queue.
    
    Returns:
        tuple: (has_capacity: bool, current_count: int, max_size: int)
    """
    from django.utils import timezone
    from datetime import timedelta
    from .models import Request
    
    cutoff = timezone.now() - timedelta(minutes=30)
    pending_count = Request.objects.filter(
        status__in=['pending', 'processing'],
        created_at__gte=cutoff
    ).count()
    max_queue_size = getattr(settings, 'VALD_MAX_QUEUE_SIZE', 10)
    return (pending_count < max_queue_size, pending_count, max_queue_size)


def uuid_to_6digit(uuid_obj):
    """
    Convert a UUID to a 6-digit number for legacy backend compatibility.

    The parserequest binary expects numeric IDs and uses atol() to parse them.
    We hash the UUID to get a deterministic 6-digit number.

    Args:
        uuid_obj: UUID object or string representation

    Returns:
        int: 6-digit number (0-999999)
    """
    import hashlib

    uuid_str = str(uuid_obj)
    # Use SHA256 for deterministic hashing (unlike Python's hash())
    hash_bytes = hashlib.sha256(uuid_str.encode()).digest()
    # Convert first 4 bytes to integer
    hash_val = int.from_bytes(hash_bytes[:4], byteorder='big')
    # Return 6-digit number
    return hash_val % 1000000


def get_client_name(user_email):
    """
    Extract ClientName from user email by looking up in User model or clients.register.
    Returns alphanumeric-only version of the name.
    """
    from .models import UserEmail
    from .utils import validate_user_email

    # First try to get from database (new auth system)
    try:
        user_email_obj = UserEmail.objects.select_related('user').get(email=user_email.lower())
        user_name = user_email_obj.user.name
    except UserEmail.DoesNotExist:
        # Fallback to clients.register file (legacy)
        is_valid, user_name = validate_user_email(user_email)
        if not is_valid:
            return None

    # Convert to alphanumeric only (matching parsemail.c logic line 86)
    client_name = ''.join(c for c in user_name if c.isalnum())

    return client_name


def submit_request_direct(request_obj):
    """
    Submit a request directly to the backend processing system.

    Args:
        request_obj: Request model instance with user, request_type, parameters

    Returns:
        tuple: (success, output_file_path or error_message)
    """
    # Get client name from user
    if not request_obj.user:
        return (False, "User not found")
    client_name = request_obj.user.client_name
    if not client_name:
        return (False, "User not registered")

    # Ensure working directory exists
    working_dir = settings.VALD_WORKING_DIR
    working_dir.mkdir(parents=True, exist_ok=True)

    # Convert UUID to 6-digit number for backend compatibility
    # parserequest uses atol() which can't parse UUIDs
    backend_id = uuid_to_6digit(request_obj.uuid)

    # Create isolated subdirectory for this job first
    # parserequest must run in the job directory to create files with correct names
    job_dir = working_dir / f"{backend_id:06d}"
    uuid_marker = job_dir / '.uuid'

    # Check for UUID collision - if directory exists with different UUID, retry with incremented ID
    max_collision_retries = 100
    for retry in range(max_collision_retries):
        if job_dir.exists() and uuid_marker.exists():
            # Check if this is our UUID or a collision
            try:
                existing_uuid = uuid_marker.read_text().strip()
                if existing_uuid == str(request_obj.uuid):
                    # This is our directory (maybe from a retry), reuse it
                    break
                else:
                    # Collision! Try next ID
                    backend_id = (backend_id + 1) % 1000000
                    job_dir = working_dir / f"{backend_id:06d}"
                    uuid_marker = job_dir / '.uuid'
                    continue
            except Exception:
                # Can't read marker file, assume collision and retry
                backend_id = (backend_id + 1) % 1000000
                job_dir = working_dir / f"{backend_id:06d}"
                uuid_marker = job_dir / '.uuid'
                continue
        else:
            # Directory doesn't exist or no marker - we can use this ID
            break
    else:
        # Exhausted all retries
        return (False, f"Could not find available backend ID after {max_collision_retries} attempts")

    # Create directory and UUID marker file
    try:
        job_dir.mkdir(exist_ok=True)
        uuid_marker.write_text(str(request_obj.uuid))
    except Exception as e:
        return (False, f"Failed to create job directory: {e}")

    # Create request file in subdirectory
    request_file = job_dir / f"request.{backend_id:06d}"
    try:
        with open(request_file, 'w') as f:
            # Write request content in VALD format
            content = format_request_file(request_obj)
            f.write(content)
    except Exception as e:
        return (False, f"Failed to create request file: {e}")

    # Call parserequest binary from subdirectory
    try:
        parserequest_bin = settings.VALD_PARSEREQUEST_BIN
        if not parserequest_bin.exists():
            return (False, f"parserequest binary not found: {parserequest_bin}")

        # parserequest expects: ./parserequest request.NNNNNN ClientName
        # It will create job.NNNNNN, pres_in.NNNNNN, show_in.NNNNNN_* in current directory
        # MUST run from job subdirectory for correct file naming
        result = subprocess.run(
            [str(parserequest_bin), request_file.name, client_name],
            cwd=job_dir,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            return (False, f"parserequest failed: {result.stderr}")

    except subprocess.TimeoutExpired:
        return (False, "parserequest timed out")
    except Exception as e:
        return (False, f"Error calling parserequest: {e}")

    # Check that job script was created
    job_file = job_dir / f"job.{backend_id:06d}"
    if not job_file.exists():
        return (False, f"job script not created: {job_file}")

    # Fix job script to handle missing bib files
    # presformat5 doesn't always create selected.bib (e.g., no matching lines)
    # post_hfs_format5 often fails to create post_selected.bib (Fortran errors)
    # Make all bib file operations conditional to prevent job failures
    try:
        with open(job_file, 'r') as f:
            job_script = f.read()

        bib_name = f'{client_name}.{backend_id:06d}.bib'
        bib_gz = f'{bib_name}.gz'
        ftp_dir = str(settings.VALD_FTP_DIR)

        modified = False

        # Fix HFS case: post_selected.bib -> fallback to selected.bib
        if 'mv post_selected.bib' in job_script:
            job_script = job_script.replace(
                f'mv post_selected.bib {bib_name}',
                f'test -f post_selected.bib && mv post_selected.bib {bib_name} || test -f selected.bib && mv selected.bib {bib_name}'
            )
            modified = True

        # Fix non-HFS case: make selected.bib handling conditional
        elif f'mv selected.bib {bib_name}' in job_script:
            job_script = job_script.replace(
                f'mv selected.bib {bib_name}',
                f'test -f selected.bib && mv selected.bib {bib_name}'
            )
            modified = True

        # Make subsequent bib operations conditional too
        # Check for already-conditional gzip first
        if f'test -f {bib_name} && gzip {bib_name}' not in job_script and f'gzip {bib_name}' in job_script:
            job_script = job_script.replace(
                f'gzip {bib_name}',
                f'test -f {bib_name} && gzip {bib_name}'
            )
            modified = True

        # Make mv of gzipped bib conditional
        # Match lines like: mv TestUser.NNNNNN.bib.gz /path/to/ftp
        import re
        mv_bib_pattern = rf'^mv {re.escape(bib_gz)} '
        for line in job_script.split('\n'):
            if re.match(mv_bib_pattern, line) and 'test -f' not in line:
                new_line = f'test -f {bib_gz} && {line}'
                job_script = job_script.replace(line, new_line)
                modified = True
                break

        # Make chmod conditional
        # Match lines like: chmod a+r /path/to/ftp/TestUser.NNNNNN.bib.gz
        chmod_search = f'chmod a+r {ftp_dir}/{bib_gz}'
        for line in job_script.split('\n'):
            if chmod_search in line and 'test -f' not in line:
                new_line = f'test -f {ftp_dir}/{bib_gz} && {line}'
                job_script = job_script.replace(line, new_line)
                modified = True
                break

        # Remove cleanup of pres_in so we can debug
        if f'rm pres_in.{backend_id:06d}' in job_script:
            job_script = job_script.replace(f'rm pres_in.{backend_id:06d}\n', '')
            modified = True

        if modified:
            with open(job_file, 'w') as f:
                f.write(job_script)
    except Exception as e:
        return (False, f"Failed to patch job script: {e}")

    # All files are already in job_dir since parserequest ran there
    # No need to move files

    # Increase line limits (parserequest defaults to 100000 for FTP, 1000 for email)
    max_lines = getattr(settings, 'VALD_MAX_LINES_PER_REQUEST', 500000)

    # Extract Stellar: modify select.input (last line is limit)
    # Note: pres_in.NNNNNN for stellar should stay 0 (no limit) for preselection
    select_input = job_dir / 'select.input'
    if select_input.exists():
        try:
            with open(select_input, 'r') as f:
                lines = f.readlines()
            if lines:
                lines[-1] = f"{max_lines}\n"
                with open(select_input, 'w') as f:
                    f.writelines(lines)
        except Exception:
            pass

    # Patch pres_in.NNNNNN file
    # Line 2: max lines
    # Line 4: config file path
    # Line 5: flags (a b c d e f g h i j k l m)
    pres_in = job_dir / f"pres_in.{backend_id:06d}"
    if pres_in.exists():
        try:
            with open(pres_in, 'r') as f:
                lines = f.readlines()
            
            modified = False
            
            # Line 2: Set max lines (for extractall/extractelement only)
            if request_obj.request_type in ['extractall', 'extractelement'] and len(lines) >= 2:
                lines[1] = f"{max_lines}\n"
                modified = True
            
            # Line 4: Set personal config file path if user selected "personal"
            params = request_obj.parameters
            pconf = params.get('pconf')
            if pconf == 'personal' and len(lines) >= 4:
                user_config = settings.PERSCONFIG_DIR / f"{client_name}.cfg"
                if user_config.exists():
                    lines[3] = f"'{user_config}'\n"
                    modified = True
            
            # Line 5: Set flags based on user preferences
            if len(lines) >= 5:
                # Parse current flags
                current_flags = lines[4].strip().split()
                if len(current_flags) >= 13:
                    # Map preferences to flag positions
                    # a (0): format - 0=short eV, 1=long eV, 3=short cm⁻¹, 4=long cm⁻¹
                    format_val = params.get('format', 'short')
                    energy = params.get('energyunit', 'eV')
                    if energy == '1/cm':
                        current_flags[0] = '4' if format_val == 'long' else '3'
                    else:
                        current_flags[0] = '1' if format_val == 'long' else '0'
                    
                    # g (6): extended vdw - 1 if vdwformat is 'extended'
                    if params.get('vdwformat') == 'extended':
                        current_flags[6] = '1'
                    
                    # j (9): medium - 0=air, 1=vacuum
                    if params.get('medium') == 'vacuum':
                        current_flags[9] = '1'
                    else:
                        current_flags[9] = '0'
                    
                    # k (10): waveunit - 0=Å, 1=nm, 2=cm⁻¹
                    waveunit = params.get('waveunit', 'angstrom')
                    if waveunit == 'nm':
                        current_flags[10] = '1'
                    elif waveunit == '1/cm':
                        current_flags[10] = '2'
                    else:
                        current_flags[10] = '0'
                    
                    # l (11): isotopic scaling - default 1
                    if params.get('isotopic_scaling') == 'off':
                        current_flags[11] = '0'
                    else:
                        current_flags[11] = '1'
                    
                    lines[4] = ' '.join(current_flags) + '\n'
                    modified = True
            
            if modified:
                with open(pres_in, 'w') as f:
                    f.writelines(lines)
        except Exception:
            pass

    # Define job execution function for queue
    def execute_job():
        """Execute the job script - this runs in worker thread."""
        # Make job executable
        os.chmod(job_file, 0o755)

        # Execute job script in its isolated subdirectory
        result = subprocess.run(
            [str(job_file)],
            cwd=job_dir,
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour timeout for extraction
        )

        if result.returncode != 0:
            raise Exception(f"Job execution failed: {result.stderr}")

        return result

    # Submit job to queue (blocks until job completes)
    try:
        job_queue = get_job_queue()
        result = job_queue.submit(execute_job)
    except subprocess.TimeoutExpired:
        return (False, "Job execution timed out")
    except Exception as e:
        return (False, f"Error executing job: {e}")

    # Find output file - different for showline vs extract requests
    settings.VALD_FTP_DIR.mkdir(parents=True, exist_ok=True)

    if request_obj.request_type == 'showline':
        # Showline creates result.NNNNNN in job directory
        result_file = job_dir / f"result.{backend_id:06d}"
        output_file = settings.VALD_FTP_DIR / f"{client_name}.{backend_id:06d}.txt"

        if result_file.exists():
            # Move result file to FTP directory with .txt extension
            import shutil
            shutil.move(str(result_file), str(output_file))
        else:
            return (False, f"Result file not found: {result_file}")
    else:
        # Extract requests create ClientName.NNNNNN.gz
        output_file = settings.VALD_FTP_DIR / f"{client_name}.{backend_id:06d}.gz"
        bib_file = settings.VALD_FTP_DIR / f"{client_name}.{backend_id:06d}.bib.gz"

        # Check if output file exists in FTP directory
        if not output_file.exists():
            # Check if it's in working directory (might need to move it)
            working_output = working_dir / f"{client_name}.{backend_id:06d}.gz"
            if working_output.exists():
                working_output.rename(output_file)
            else:
                # Check in job subdirectory
                job_output = job_dir / f"{client_name}.{backend_id:06d}.gz"
                if job_output.exists():
                    job_output.rename(output_file)

        # Check if bib file exists in FTP directory, if not try to move it
        if not bib_file.exists():
            # Check if it's in working directory
            working_bib = working_dir / f"{client_name}.{backend_id:06d}.bib.gz"
            if working_bib.exists():
                working_bib.rename(bib_file)
            else:
                # Check in job subdirectory
                job_bib = job_dir / f"{client_name}.{backend_id:06d}.bib.gz"
                if job_bib.exists():
                    job_bib.rename(bib_file)

    # Verify main output file exists (bib file is optional for extract requests)
    # Job directory cleanup handled by cronjob with --age=2D for debugging purposes
    if output_file.exists():
        return (True, str(output_file))
    else:
        return (False, f"Output file not found: {output_file}")


def format_request_file(request_obj):
    """
    Format request parameters into VALD request file format.
    This is the format that parserequest expects.

    Args:
        request_obj: Request model instance

    Returns:
        str: Formatted request content
    """
    params = request_obj.parameters
    reqtype = request_obj.request_type

    lines = ["begin request"]

    # Request type
    type_map = {
        'extractall': 'extract all',
        'extractelement': 'extract element',
        'extractstellar': 'extract stellar',
        'showline': 'show line',
    }
    lines.append(type_map.get(reqtype, reqtype))

    # Configuration
    pconf = params.get('pconf', 'default')
    lines.append(f"{pconf} configuration")

    # Extract requests have via ftp and format, showline doesn't
    if reqtype in ['extractall', 'extractelement', 'extractstellar']:
        # Retrieval method - always use viaftp for direct submissions
        lines.append("via ftp")

        # Format
        if 'format' in params:
            lines.append(f"{params['format']} format")

    # Units and medium
    if 'waveunit' in params:
        lines.append(f"waveunit {params['waveunit']}")
    if 'energyunit' in params:
        lines.append(f"energyunit {params['energyunit']}")
    if 'medium' in params:
        lines.append(f"medium {params['medium']}")

    # Isotopic scaling
    if 'isotopic_scaling' in params:
        lines.append(f"isotopic scaling {params['isotopic_scaling']}")

    # VdW format
    if 'vdwformat' in params and params['vdwformat'] != 'default':
        lines.append(f"{params['vdwformat']} waals")

    # Flags (hrad, hstark, hwaals, hlande, hterm, hfssplit)
    # Map flag names to their VALD request format strings (matching email system)
    flag_labels = {
        'hfssplit': 'HFS splitting',
        'hrad': 'have rad',
        'hstark': 'have stark',
        'hwaals': 'have waals',
        'hlande': 'have lande',
        'hterm': 'have term',
    }
    for flag, label in flag_labels.items():
        flag_value = params.get(flag)
        # Handle both boolean and string values
        if flag_value is True or (isinstance(flag_value, str) and flag_value):
            lines.append(label if flag_value is True else flag_value)

    # Request-specific parameters
    if reqtype == 'extractall':
        if 'stwvl' in params and 'endwvl' in params:
            lines.append(f"{params['stwvl']}, {params['endwvl']}")

    elif reqtype == 'extractelement':
        if 'stwvl' in params and 'endwvl' in params:
            lines.append(f"{params['stwvl']}, {params['endwvl']},")
        if 'elmion' in params:
            lines.append(params['elmion'])

    elif reqtype == 'extractstellar':
        if 'stwvl' in params and 'endwvl' in params:
            lines.append(f"{params['stwvl']}, {params['endwvl']},")
        if 'dlimit' in params and 'micturb' in params:
            lines.append(f"{params['dlimit']}, {params['micturb']},")
        if 'teff' in params and 'logg' in params:
            lines.append(f"{params['teff']}, {params['logg']},")
        if 'chemcomp' in params:
            lines.append(params['chemcomp'])

    elif reqtype == 'showline':
        # Showline can have up to 5 sets of wavelength/window/element
        for i in range(5):
            wvl_key = f'wvl{i}'
            win_key = f'win{i}'
            el_key = f'el{i}'

            wvl = params.get(wvl_key)
            win = params.get(win_key)
            el = params.get(el_key, '')

            # Skip if wavelength or window is None/empty
            if wvl is not None and win is not None:
                lines.append(f"{wvl}, {win},")
                # Add element if it's not empty
                if el:
                    lines.append(el)

    lines.append("end request")

    return '\n'.join(lines) + '\n'
