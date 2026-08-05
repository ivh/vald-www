"""R22 (spam filter), R27 (unit validation), R33 (email attach cap), R19 (static)."""
import time

import pytest

from vald.utils import spam_check


# --- R22: spam filter must allow legitimate links --------------------------

@pytest.mark.parametrize('message', [
    'Please see https://doi.org/10.1234/abcd for the line list I mean.',
    'The reference is http://arxiv.org/abs/2401.00001 - can you add it?',
    'Screenshot at https://example.edu/~me/bug.png shows the wrong gf value.',
    'Short but ok message.',
])
def test_legitimate_messages_with_links_pass(message):
    assert spam_check(message) is True


@pytest.mark.parametrize('message', [
    'buy now <a href="http://spam.example">cheap</a>',
    'check [url=http://spam.example]this[/url] out',
    ' '.join(f'http://spam{i}.example' for i in range(8)),   # link flood
    'short',                                                  # under 10 chars
    '',
])
def test_spam_or_trivial_messages_are_rejected(message):
    assert spam_check(message) is False


# --- R27: save_units must reject values outside the model's choices ---------

@pytest.mark.django_db
def test_valid_unit_preferences_are_saved(logged_in_client, approved_user):
    resp = logged_in_client.post('/save-units/', {
        'energyunit': '1/cm', 'medium': 'vacuum', 'waveunit': 'nm',
        'vdwformat': 'extended', 'isotopic_scaling': 'off',
    })
    assert resp.status_code == 200
    prefs = approved_user.get_preferences()
    assert prefs.energyunit == '1/cm'
    assert prefs.medium == 'vacuum'
    assert prefs.waveunit == 'nm'


@pytest.mark.django_db
def test_invalid_unit_value_is_rejected_and_not_persisted(logged_in_client, approved_user):
    before = approved_user.get_preferences().energyunit
    resp = logged_in_client.post('/save-units/', {
        'energyunit': "eV'; DROP TABLE", 'medium': 'air', 'waveunit': 'angstrom',
        'vdwformat': 'default', 'isotopic_scaling': 'on',
    })
    assert resp.status_code == 302        # redirected back with an error
    approved_user.get_preferences().refresh_from_db()
    assert approved_user.get_preferences().energyunit == before   # unchanged


# --- R33: large results are not attached to the completion email ------------

def _wait_for_mail(mailoutbox, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if mailoutbox:
            return mailoutbox[-1]
        time.sleep(0.05)
    raise AssertionError('no completion email was sent')


def _email_request(client, monkeypatch, settings, tmp_path, size_bytes):
    import os
    # output_path resolves against VALD_FTP_DIR, so the file must live there
    settings.VALD_FTP_DIR = tmp_path
    gz = tmp_path / 'TestUser.000001.gz'
    gz.write_bytes(os.urandom(size_bytes))     # incompressible: stat size == size_bytes
    monkeypatch.setattr('vald.backend.submit_request_direct',
                        lambda req: (True, str(gz)))
    client.post('/submit/', {
        'reqtype': 'extractall', 'stwvl': '5000', 'endwvl': '5010',
        'format': 'short', 'viaftp': 'email', 'pconf': 'default',
    })


@pytest.mark.django_db(transaction=True)
def test_small_result_is_attached(logged_in_client, monkeypatch, tmp_path, mailoutbox, settings):
    settings.VALD_MAX_EMAIL_ATTACH_BYTES = 1024 * 1024
    _email_request(logged_in_client, monkeypatch, settings, tmp_path, 500)
    mail = _wait_for_mail(mailoutbox)
    assert len(mail.attachments) >= 1


@pytest.mark.django_db(transaction=True)
def test_oversize_result_is_not_attached_but_links_remain(logged_in_client, monkeypatch,
                                                          tmp_path, mailoutbox, settings):
    settings.VALD_MAX_EMAIL_ATTACH_BYTES = 100      # tiny cap
    _email_request(logged_in_client, monkeypatch, settings, tmp_path, 5000)
    mail = _wait_for_mail(mailoutbox)
    assert len(mail.attachments) == 0
    assert 'too large to attach' in mail.body
    assert 'download' in mail.body.lower()          # links still offered


# --- R19: results directory must never sit inside a static tree -------------

def test_ftp_dir_is_outside_all_static_dirs(settings):
    """collectstatic publishes everything under STATICFILES_DIRS; results must
    not be reachable that way. This guards the dev-config overlap that regressed."""
    ftp = settings.VALD_FTP_DIR.resolve()
    for static_dir in settings.STATICFILES_DIRS:
        from pathlib import Path
        static_dir = Path(static_dir).resolve()
        assert not ftp.is_relative_to(static_dir), \
            f'VALD_FTP_DIR {ftp} is inside static dir {static_dir}'
