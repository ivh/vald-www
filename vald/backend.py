"""
Backend processing functions for direct VALD request submission.

Uses Python job_runner module with direct Fortran execution.
"""
import os
import hashlib
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
    from .job_runner import JobRunner, create_job_config
    
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
    backend_id = uuid_to_6digit(request_obj.uuid)
    
    # Create isolated subdirectory for this job
    job_dir = working_dir / f"{backend_id:06d}"
    uuid_marker = job_dir / '.uuid'
    
    # Check for UUID collision
    max_collision_retries = 100
    for retry in range(max_collision_retries):
        if job_dir.exists() and uuid_marker.exists():
            try:
                existing_uuid = uuid_marker.read_text().strip()
                if existing_uuid == str(request_obj.uuid):
                    break  # Our directory, reuse it
                else:
                    # Collision! Try next ID
                    backend_id = (backend_id + 1) % 1000000
                    job_dir = working_dir / f"{backend_id:06d}"
                    uuid_marker = job_dir / '.uuid'
                    continue
            except Exception:
                backend_id = (backend_id + 1) % 1000000
                job_dir = working_dir / f"{backend_id:06d}"
                uuid_marker = job_dir / '.uuid'
                continue
        else:
            break
    else:
        return (False, f"Could not find available backend ID after {max_collision_retries} attempts")
    
    # Create directory and UUID marker
    try:
        job_dir.mkdir(exist_ok=True)
        uuid_marker.write_text(str(request_obj.uuid))
    except Exception as e:
        return (False, f"Failed to create job directory: {e}")
    
    # Create job config from request
    job_config = create_job_config(request_obj, backend_id, job_dir, client_name)
    
    # Define job execution function for queue
    def execute_job():
        runner = JobRunner()
        return runner.run(job_config)
    
    # Submit job to queue
    try:
        job_queue = get_job_queue()
        success, result = job_queue.submit(execute_job)
        return (success, result)
    except QueueFullError as e:
        notify_queue_full()
        return (False, str(e))
    except Exception as e:
        return (False, f"Error executing job: {e}")


def format_request_file(request_obj):
    """
    Format request parameters into VALD email request format.
    
    This format can be copy-pasted by users into an email to use
    the legacy email-based request system.

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
        lines.append("via ftp")
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

    # Flags
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
        for i in range(5):
            wvl = params.get(f'wvl{i}')
            win = params.get(f'win{i}')
            el = params.get(f'el{i}', '')
            if wvl is not None and win is not None:
                lines.append(f"{wvl}, {win},")
                if el:
                    lines.append(el)

    lines.append("end request")

    return '\n'.join(lines) + '\n'


