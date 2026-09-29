"""The background request handler in handle_extract_request.

This is the code path R15's other tests skipped: they exercised JobRunner and the
model layer directly, but never process_request - the daemon thread
that records the outcome. An UnboundLocalError there marked every successful
extraction as Failed, and nothing caught it.
"""
import re

import pytest

from vald.models import Request


EXTRACT = {
    'reqtype': 'extractall', 'stwvl': '5000', 'endwvl': '5010',
    'format': 'short', 'pconf': 'default',
}


@pytest.mark.django_db(transaction=True)
def test_successful_extraction_is_recorded_complete(logged_in_client, wait_for_worker,
                                                    monkeypatch, tmp_path):
    """A job that succeeds must end up 'complete' with the output filename stored.

    Regression: the worker did `output_file = Path(result).name`
    while a nested `from pathlib import Path` later in the same function made Path
    a local, so this raised UnboundLocalError and the request was marked Failed.
    """
    gz = tmp_path / 'TestUser.000001.gz'
    gz.write_bytes(b'\x1f\x8b' + b'0' * 200)

    def fake_submit(req_obj, **kw):
        return (True, str(gz))
    monkeypatch.setattr('vald.backend.submit_request_direct', fake_submit)

    resp = logged_in_client.post('/submit/', EXTRACT)
    assert resp.status_code == 302        # redirect to the detail page
    wait_for_worker()

    from vald.models import Request
    req = Request.objects.latest('created_at')

    assert req.status == 'complete', f'error_message: {req.error_message}'
    assert req.output_file == 'TestUser.000001.gz'   # basename only (R18)
    assert req.completed_at is not None              # R7


@pytest.mark.django_db(transaction=True)
def test_failed_extraction_records_the_error(logged_in_client, wait_for_worker, monkeypatch):
    def fake_submit(req_obj, **kw):
        return (False, 'preselect5 failed: something specific')
    monkeypatch.setattr('vald.backend.submit_request_direct', fake_submit)

    logged_in_client.post('/submit/', EXTRACT)
    wait_for_worker()

    from vald.models import Request
    req = Request.objects.latest('created_at')

    assert req.status == 'failed'
    assert 'something specific' in req.error_message
    assert req.completed_at is not None


# --- site-wide admission: VALD_MAX_QUEUE_SIZE counts what is in flight -------

@pytest.mark.django_db
def test_a_long_running_job_still_counts_against_the_queue(
        logged_in_client, no_background_worker, monkeypatch, settings):
    """The gate used to count rows created in the last 30 minutes, so anything
    running longer than that freed its place while still occupying a thread."""
    settings.VALD_MAX_QUEUE_SIZE = 2
    monkeypatch.setattr('vald.backend._active_uuids', {'older-than-30-min', 'another'})
    resp = logged_in_client.post('/submit/', EXTRACT)
    assert resp.status_code == 200
    assert 'Server is busy' in resp.content.decode()
    assert not Request.objects.exists()        # refused before a row was made


@pytest.mark.django_db
def test_a_stranded_row_does_not_block_the_queue(
        logged_in_client, no_background_worker, approved_user, settings):
    settings.VALD_MAX_QUEUE_SIZE = 1
    Request.objects.create(user=approved_user, request_type='extractall',
                           parameters={}, status='processing')
    assert logged_in_client.post('/submit/', EXTRACT).status_code == 302


@pytest.mark.django_db(transaction=True)
def test_a_finished_job_gives_its_place_back(logged_in_client, wait_for_worker,
                                             monkeypatch, settings):
    import vald.backend
    settings.VALD_MAX_QUEUE_SIZE = 1
    seen = []

    def fake_submit(req_obj, **kw):
        seen.append(vald.backend.is_request_active(req_obj.uuid))
        return (False, 'whatever')
    monkeypatch.setattr('vald.backend.submit_request_direct', fake_submit)

    assert logged_in_client.post('/submit/', EXTRACT).status_code == 302
    wait_for_worker()
    assert seen == [True]
    assert not vald.backend._active_uuids
    assert logged_in_client.post('/submit/', EXTRACT).status_code == 302


# --- extraction format: long by default, so results can be converted ---------

@pytest.mark.parametrize('page', ['extractall', 'extractelement', 'extractstellar'])
@pytest.mark.django_db
def test_long_format_is_preselected(page, logged_in_client):
    """Only the long format can be converted to CSV, FITS, Parquet or SQLite,
    and the radio label is the only place the page says so."""
    body = logged_in_client.get(f'/{page}/').content.decode()
    checked = re.findall(r'<input[^>]*name="format"[^>]*checked[^>]*>', body)
    assert len(checked) == 1, 'exactly one format radio should be preselected'
    assert 'value="long"' in checked[0]
    assert 'Long format (with conversion options)' in body


@pytest.mark.django_db
def test_modifying_a_short_request_keeps_short(logged_in_client, approved_user):
    """The default applies to fresh forms, not to a rerun of an old request."""
    req = Request.objects.create(
        user=approved_user, request_type='extractall', status='complete',
        parameters={'format': 'short', 'stwvl': 5000, 'endwvl': 5010})
    body = logged_in_client.get(f'/extractall/?modify={req.uuid}').content.decode()
    checked = re.findall(r'<input[^>]*name="format"[^>]*checked[^>]*>', body)
    assert len(checked) == 1 and 'value="short"' in checked[0]
