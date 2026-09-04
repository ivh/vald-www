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

    def fake_submit(req_obj):
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
    def fake_submit(req_obj):
        return (False, 'preselect5 failed: something specific')
    monkeypatch.setattr('vald.backend.submit_request_direct', fake_submit)

    logged_in_client.post('/submit/', EXTRACT)
    wait_for_worker()

    from vald.models import Request
    req = Request.objects.latest('created_at')

    assert req.status == 'failed'
    assert 'something specific' in req.error_message
    assert req.completed_at is not None


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
