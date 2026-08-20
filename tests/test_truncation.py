"""Reporting a run that stopped at VALD_MAX_LINES_PER_REQUEST.

The cap is not an error - every stage exits 0 having quietly stopped early - so
the fact has to be carried out of the pipeline by hand. See
notes/trunc_fortran.md for which stage announces it where, and why.
"""
import pytest

from vald.backend import submit_request_direct
from vald.job_runner import JobConfig
from vald.models import Request


@pytest.fixture
def stub_job(monkeypatch, tmp_path, settings):
    """Run submit_request_direct with the Fortran replaced by a callable we control.

    Patched on vald.job_runner, not vald.backend: submit_request_direct imports
    both names inside the function body.
    """
    settings.VALD_WORKING_DIR = tmp_path / 'working'

    def install(truncated):
        config = JobConfig(job_id=1, job_dir=tmp_path, client_name='Tester',
                           request_type='extractall', wl_start=5000.0, wl_end=5010.0,
                           max_lines=500000)
        monkeypatch.setattr('vald.job_runner.create_job_config',
                            lambda *args, **kwargs: config)

        def fake_run(self, cfg):
            cfg.truncated = truncated
            return (True, str(tmp_path / 'Tester.000001.gz'))

        monkeypatch.setattr('vald.job_runner.JobRunner.run', fake_run)

    return install


def make_request(user, **parameters):
    return Request.objects.create(user=user, request_type='extractall',
                                  parameters=parameters or {'stwvl': 5000.0})


@pytest.mark.django_db
def test_truncated_run_is_recorded_on_the_request(approved_user, stub_job):
    stub_job(truncated=True)
    req = make_request(approved_user)

    ok, _ = submit_request_direct(req)

    assert ok
    assert req.parameters['truncated'] is True
    assert req.parameters['truncated_at'] == 500000


@pytest.mark.django_db
def test_complete_run_leaves_no_truncation_marker(approved_user, stub_job):
    stub_job(truncated=False)
    req = make_request(approved_user)

    ok, _ = submit_request_direct(req)

    assert ok
    assert 'truncated' not in req.parameters


@pytest.mark.django_db
def test_request_page_flags_a_truncated_result(logged_in_client, approved_user):
    req = Request.objects.create(
        user=approved_user, request_type='extractall', status='complete',
        parameters={'stwvl': 5000.0, 'truncated': True, 'truncated_at': 500000})

    page = logged_in_client.get(f'/request/{req.uuid}/').content.decode()

    assert 'truncated' in page
    assert '500000-line limit' in page


@pytest.mark.django_db
def test_request_page_says_nothing_when_the_result_is_complete(logged_in_client,
                                                               approved_user):
    req = Request.objects.create(user=approved_user, request_type='extractall',
                                 status='complete', parameters={'stwvl': 5000.0})

    page = logged_in_client.get(f'/request/{req.uuid}/').content.decode()

    assert 'truncated' not in page
