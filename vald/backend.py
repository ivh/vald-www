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
    Extract ClientName from user email by looking up in clients.register.
    Returns alphanumeric-only version of the name.
    """
    from .utils import validate_user_email

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

    # Fix race condition: replace shared err.log with unique filename
    # parserequest generates scripts that use hardcoded "err.log"
    # When multiple jobs run in parallel, they conflict on this file
    try:
        with open(job_file, 'r') as f:
            job_script = f.read()
        job_script = job_script.replace('err.log', f'err.{backend_id:06d}.log')
        with open(job_file, 'w') as f:
            f.write(job_script)
    except Exception as e:
        return (False, f"Failed to fix job script: {e}")

    # Define job execution function for queue
    def execute_job():
        """Execute the job script - this runs in worker thread."""
        # Make job executable
        os.chmod(job_file, 0o755)

        # Execute job script
        # Note: job script expects to run in working directory
        result = subprocess.run(
            [str(job_file)],
            cwd=working_dir,
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

    if output_file.exists():
        return (True, str(output_file))
    else:
        # Check if it's in working directory (might need to move it)
        working_output = working_dir / f"{client_name}.{backend_id:06d}.gz"
        if working_output.exists():
            # Move to FTP directory
            settings.VALD_FTP_DIR.mkdir(parents=True, exist_ok=True)
            working_output.rename(output_file)
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
            lines.append(f"{params['stwvl']}, {params['endwvl']}")
        if 'element' in params:
            lines.append(params['element'])

    elif reqtype == 'extractstellar':
        if 'stwvl' in params and 'endwvl' in params:
            lines.append(f"{params['stwvl']}, {params['endwvl']}")
        if 'teff' in params and 'logg' in params:
            lines.append(f"{params['teff']}, {params['logg']}")

    elif reqtype == 'showline':
        if 'wvl0' in params and 'win0' in params:
            lines.append(f"{params['wvl0']}, {params['win0']}")
        if 'el0' in params:
            lines.append(params['el0'])

    lines.append("end request")

    return '\n'.join(lines) + '\n'
