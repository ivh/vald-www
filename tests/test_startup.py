"""The startup sweep for requests a restart left unfinished.

The queue and its worker threads live in the application process, so anything in
flight dies with it and its row keeps saying 'processing' - the pipeline timeout
is a deadline held by a thread that no longer exists. Nothing else in the system
ever resolves those rows.
"""
import pytest

from vald.models import Request, User, UserEmail
from vald.startup import STRANDED_MESSAGE, reconcile_stranded_requests, run


@pytest.fixture
def captured_reruns(monkeypatch):
    """Collect the workers the sweep would start, without running them."""
    started = []
    monkeypatch.setattr('vald.views.start_background_worker',
                        lambda target: started.append(target))
    return started


def make_request(status, name='Owner'):
    user = User.objects.filter(name=name).first()
    if user is None:
        user = User.objects.create(name=name, is_active=True)
        UserEmail.objects.create(user=user, email=f'{name.lower()}@example.com',
                                 is_primary=True)
    return Request.objects.create(user=user, request_type='extractall',
                                  parameters={}, status=status)


@pytest.mark.django_db
def test_a_quiet_start_touches_nothing(captured_reruns):
    make_request('complete')
    make_request('failed')

    assert reconcile_stranded_requests() == (0, 0)
    assert not captured_reruns


@pytest.mark.django_db
def test_a_few_stranded_requests_are_re_run_for_their_owners(captured_reruns, settings):
    """Both unfinished states, because at startup neither is being looked after:
    a row queued but not yet started is as stranded as one mid-pipeline."""
    settings.VALD_STRANDED_RERUN_MAX = 5
    processing = make_request('processing')
    pending = make_request('pending')

    assert reconcile_stranded_requests() == (2, 0)

    for req in (processing, pending):
        req.refresh_from_db()
        assert req.status == 'pending'
        assert req.error_message is None
    assert len(captured_reruns) == 2


@pytest.mark.django_db
def test_a_flood_is_failed_and_reported_instead(captured_reruns, settings, mailoutbox):
    """Replaying a boot-time flood would fill the queue and reject live users,
    and that many at once is a thing to look at rather than replay unattended."""
    settings.VALD_STRANDED_RERUN_MAX = 2
    settings.VALD_WEBMASTER_EMAIL = 'webmaster@example.com'
    stranded = [make_request('processing') for _ in range(3)]

    assert reconcile_stranded_requests() == (0, 3)

    for req in stranded:
        req.refresh_from_db()
        assert req.status == 'failed'
        assert req.error_message == STRANDED_MESSAGE
        assert req.completed_at is not None       # or the duration column stays blank
    assert not captured_reruns

    assert len(mailoutbox) == 1
    assert '3 requests failed' in mailoutbox[0].subject
    assert str(stranded[0].uuid) in mailoutbox[0].body


@pytest.mark.django_db
def test_the_owner_keeps_the_request(captured_reruns, settings):
    """The whole point of re-running in place: the uuid, and so every link the
    user already has, survives."""
    settings.VALD_STRANDED_RERUN_MAX = 5
    req = make_request('processing')
    before = (req.uuid, req.user_id)

    reconcile_stranded_requests()

    req.refresh_from_db()
    assert (req.uuid, req.user_id) == before


@pytest.mark.django_db
def test_a_broken_sweep_never_stops_the_app_booting(monkeypatch, caplog):
    """gunicorn reports an exception at import as only "Worker failed to boot",
    and none of this is worth not serving the site for."""
    monkeypatch.setattr('vald.startup.reconcile_stranded_requests',
                        lambda: 1 / 0)

    run()      # must not raise

    assert 'Startup reconcile' in caplog.text


# --- the daily timer's report on requests stuck while the process stayed up ---

def run_cleanup(**options):
    """cleanup_old_results with its file sweeping given nothing to do."""
    from io import StringIO
    from django.core.management import call_command

    out = StringIO()
    call_command('cleanup_old_results', stdout=out, stderr=out, **options)
    return out.getvalue()


@pytest.fixture
def empty_dirs(settings, tmp_path):
    """Point the command's two trees at empty directories, so only the request
    check has anything to say."""
    ftp = tmp_path / 'FTP'
    working = tmp_path / 'working'
    ftp.mkdir()
    working.mkdir()
    settings.VALD_FTP_DIR = ftp
    settings.VALD_WORKING_DIR = working


def age_by(req, seconds):
    from datetime import timedelta
    from django.utils import timezone
    Request.objects.filter(pk=req.pk).update(
        created_at=timezone.now() - timedelta(seconds=seconds))
    req.refresh_from_db()
    return req


@pytest.mark.django_db
def test_a_long_processing_request_is_reported(empty_dirs, settings, mailoutbox):
    settings.VALD_JOB_TIMEOUT = 3600
    settings.VALD_STUCK_JOB_TIMEOUT_FACTOR = 2
    settings.VALD_WEBMASTER_EMAIL = 'webmaster@example.com'
    stuck = age_by(make_request('processing'), 3 * 3600)

    output = run_cleanup()

    assert str(stuck.uuid) in output
    assert len(mailoutbox) == 1
    assert 'stuck in processing' in mailoutbox[0].subject
    assert str(stuck.uuid) in mailoutbox[0].body


@pytest.mark.django_db
def test_a_request_inside_its_budget_is_not_reported(empty_dirs, settings, mailoutbox):
    """'processing' is set before the job is queued, so queue wait is inside the
    age - which is why the threshold is a multiple of the timeout, not the
    timeout."""
    settings.VALD_JOB_TIMEOUT = 3600
    settings.VALD_STUCK_JOB_TIMEOUT_FACTOR = 2
    settings.VALD_WEBMASTER_EMAIL = 'webmaster@example.com'
    age_by(make_request('processing'), 5400)      # 1.5x, still plausible

    output = run_cleanup()

    assert 'None' in output.split('stuck requests')[1]
    assert not mailoutbox


@pytest.mark.django_db
def test_a_dry_run_reports_without_mailing(empty_dirs, settings, mailoutbox):
    settings.VALD_JOB_TIMEOUT = 3600
    settings.VALD_WEBMASTER_EMAIL = 'webmaster@example.com'
    stuck = age_by(make_request('processing'), 3 * 3600)

    output = run_cleanup(dry_run=True)

    assert str(stuck.uuid) in output
    assert not mailoutbox


@pytest.mark.django_db
def test_a_finished_request_is_never_stuck(empty_dirs, settings, mailoutbox):
    settings.VALD_JOB_TIMEOUT = 3600
    settings.VALD_WEBMASTER_EMAIL = 'webmaster@example.com'
    age_by(make_request('complete'), 40 * 24 * 3600)

    run_cleanup()

    assert not mailoutbox
