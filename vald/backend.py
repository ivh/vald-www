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


class JobQueue:
    """
    FIFO job queue with worker threads to limit parallel job execution.

    Ensures only N jobs run concurrently while maintaining submission order.
    Jobs submitted while workers are busy will wait in queue.
    """

    def __init__(self, max_workers=2):
        """
        Initialize job queue with worker threads.

        Args:
            max_workers: Maximum number of jobs to run in parallel (default: 2)
        """
        self.job_queue = queue.Queue()
        self.max_workers = max_workers
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
            Exception: If job_func raises an exception
        """
        result_queue = queue.Queue()
        self.job_queue.put((job_func, result_queue))
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
                _job_queue = JobQueue(max_workers)

    return _job_queue


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
        is_local = False  # Database users are not local
    except UserEmail.DoesNotExist:
        # Fallback to clients.register files (legacy)
        is_valid, user_name, is_local = validate_user_email(user_email)
        if not is_valid:
            return None

    # Convert to alphanumeric only (matching parsemail.c logic line 86)
    client_name = ''.join(c for c in user_name if c.isalnum())

    if is_local:
        client_name += '_local'

    return client_name


def submit_request_direct(request_obj):
    """
    Submit a request directly to the backend processing system.

    Args:
        request_obj: Request model instance with user_email, request_type, parameters

    Returns:
        tuple: (success, output_file_path or error_message)
    """
    # Get client name
    client_name = get_client_name(request_obj.user_email)
    if not client_name:
        return (False, "User not registered")

    # Ensure working directory exists
    working_dir = settings.VALD_WORKING_DIR
    working_dir.mkdir(parents=True, exist_ok=True)

    # Convert UUID to 6-digit number for backend compatibility
    # parserequest uses atol() which can't parse UUIDs
    backend_id = uuid_to_6digit(request_obj.uuid)

    # Create request file with 6-digit ID
    request_file = working_dir / f"request.{backend_id:06d}"
    try:
        with open(request_file, 'w') as f:
            # Write request content in VALD format
            content = format_request_file(request_obj)
            f.write(content)
    except Exception as e:
        return (False, f"Failed to create request file: {e}")

    # Call parserequest binary
    try:
        parserequest_bin = settings.VALD_PARSEREQUEST_BIN
        if not parserequest_bin.exists():
            return (False, f"parserequest binary not found: {parserequest_bin}")

        # parserequest expects: ./parserequest request.NNNNNN ClientName
        # It will create job.NNNNNN in current directory
        result = subprocess.run(
            [str(parserequest_bin), str(request_file.name), client_name],
            cwd=working_dir,
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

    # Execute generated job script (parserequest creates job.NNNNNN)
    job_file = working_dir / f"job.{backend_id:06d}"
    if not job_file.exists():
        return (False, f"job script not created: {job_file}")

    # Create isolated subdirectory for this job to avoid race conditions
    # When multiple jobs run in parallel, they conflict on shared files like
    # err.log, selected.bib, etc. Running each in its own dir fixes this.
    job_dir = working_dir / f"{backend_id:06d}"
    try:
        job_dir.mkdir(exist_ok=True)

        # Move all job-related files into the subdirectory
        # parserequest creates: job.NNNNNN, pres_in.NNNNNN (for extract requests)
        import shutil
        shutil.move(str(request_file), str(job_dir / request_file.name))
        shutil.move(str(job_file), str(job_dir / job_file.name))

        # Move pres_in file if it exists (created for extract requests)
        pres_in_file = working_dir / f"pres_in.{backend_id:06d}"
        if pres_in_file.exists():
            shutil.move(str(pres_in_file), str(job_dir / pres_in_file.name))

        # Update job_file path to point to subdirectory
        job_file = job_dir / job_file.name
    except Exception as e:
        return (False, f"Failed to create job directory: {e}")

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
            timeout=300  # 5 minute timeout for extraction
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

    # Find output file
    # parserequest creates files like: ClientName.NNNNNN.gz
    output_file = settings.VALD_FTP_DIR / f"{client_name}.{backend_id:06d}.gz"
    bib_file = settings.VALD_FTP_DIR / f"{client_name}.{backend_id:06d}.bib.gz"

    # Check if output file exists in FTP directory
    if not output_file.exists():
        # Check if it's in working directory (might need to move it)
        working_output = working_dir / f"{client_name}.{backend_id:06d}.gz"
        if working_output.exists():
            # Move to FTP directory
            settings.VALD_FTP_DIR.mkdir(parents=True, exist_ok=True)
            working_output.rename(output_file)
        else:
            # Check in job subdirectory
            job_output = job_dir / f"{client_name}.{backend_id:06d}.gz"
            if job_output.exists():
                settings.VALD_FTP_DIR.mkdir(parents=True, exist_ok=True)
                job_output.rename(output_file)

    # Check if bib file exists in FTP directory, if not try to move it
    if not bib_file.exists():
        # Check if it's in working directory
        working_bib = working_dir / f"{client_name}.{backend_id:06d}.bib.gz"
        if working_bib.exists():
            settings.VALD_FTP_DIR.mkdir(parents=True, exist_ok=True)
            working_bib.rename(bib_file)
        else:
            # Check in job subdirectory
            job_bib = job_dir / f"{client_name}.{backend_id:06d}.bib.gz"
            if job_bib.exists():
                settings.VALD_FTP_DIR.mkdir(parents=True, exist_ok=True)
                job_bib.rename(bib_file)

    # Verify main output file exists (bib file is optional)
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
    for flag in ['hrad', 'hstark', 'hwaals', 'hlande', 'hterm', 'hfssplit']:
        if params.get(flag):
            # These are stored as their label values in parameters
            lines.append(params[flag])

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

            if wvl_key in params and win_key in params:
                lines.append(f"{params[wvl_key]}, {params[win_key]},")
                if el_key in params:
                    lines.append(params[el_key])

    lines.append("end request")

    return '\n'.join(lines) + '\n'
