"""What has to happen once, when the application process starts.

Called from vald_web/wsgi.py rather than AppConfig.ready(), which also fires for
migrate, collectstatic, the cleanup timer and the test suite - none of which own
the job queue, and all of which would then be reconciling requests they cannot
run. wsgi.py is loaded by gunicorn and by nothing else.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone

logger = logging.getLogger(__name__)

# Statuses that mean "the queue is looking after this". At startup nothing is,
# by definition - see reconcile_stranded_requests.
UNFINISHED = ('pending', 'processing')

STRANDED_MESSAGE = ('Interrupted by a server restart before it finished. '
                    'Please submit it again.')


def reconcile_stranded_requests():
    """Resolve requests left unfinished by whatever stopped the last process.

    The job queue and its worker threads live in the application process's
    memory, so a restart takes any job in flight with it and leaves its row
    saying 'processing' forever - the pipeline timeout is a deadline held by a
    thread that no longer exists, and nothing scans the table.

    Safe to do here, and only here, because when this process is starting there
    is nothing running to misjudge: one gunicorn worker (VALD_MAX_THREADS is the
    concurrency knob, not --workers), one instance per host. Raising --workers
    above 1 would break that assumption and make this mark a live worker's jobs
    as dead.

    A few are re-run for their owners, which is nearly always what a restart
    stranded and what the user wants. Past VALD_STRANDED_RERUN_MAX they are
    failed instead and the webmaster is told: a boot-time flood would fill
    VALD_MAX_QUEUE_SIZE and start rejecting live users, and a restart that
    stranded that many is a thing to look at rather than to replay unattended.
    """
    from .models import Request
    from .views import rerun_request

    stranded = list(Request.objects.filter(status__in=UNFINISHED))
    if not stranded:
        return 0, 0

    limit = getattr(settings, 'VALD_STRANDED_RERUN_MAX', 5)
    if len(stranded) <= limit:
        for req in stranded:
            logger.warning('Re-running request %s, stranded at %r by a restart',
                           req.uuid, req.status)
            rerun_request(req)
        return len(stranded), 0

    logger.error('%d requests stranded by a restart, more than the %d this will '
                 're-run - marking them failed', len(stranded), limit)
    for req in stranded:
        req.status = 'failed'
        req.error_message = STRANDED_MESSAGE
        req.completed_at = timezone.now()
        req.save()
    _notify_stranded(stranded, limit)
    return 0, len(stranded)


def _notify_stranded(stranded, limit):
    webmaster_email = getattr(settings, 'VALD_WEBMASTER_EMAIL', None)
    if not webmaster_email:
        return
    try:
        send_mail(
            subject=f'[VALD] {len(stranded)} requests failed by a server restart',
            message=render_to_string('vald/email/stranded_requests.txt', {
                'requests': stranded,
                'limit': limit,
                'sitename': settings.SITENAME,
            }),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[webmaster_email],
            fail_silently=True,
        )
    except Exception:
        # Never let the notification be the reason the app fails to boot.
        logger.exception('Could not send stranded-request notification')


def run():
    """Every startup task, wrapped so none of them can stop the app booting.

    A worker that dies at import is reported by gunicorn only as "Worker failed
    to boot", with the real traceback nowhere useful - and none of this is worth
    not serving the site for.
    """
    try:
        reconcile_stranded_requests()
    except Exception:
        logger.exception('Startup reconcile of stranded requests failed')
