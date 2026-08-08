"""Result file resolution, expiry, and download containment."""
import datetime
import gzip
import re

import pytest
from django.utils import timezone

from vald.models import Request


@pytest.fixture
def ftp_dir(tmp_path, settings):
    d = tmp_path / 'FTP'
    d.mkdir()
    settings.VALD_FTP_DIR = d
    return d


def make_result(directory, name='Result.000001.gz', body=b'line\n' * 20):
    with gzip.open(directory / name, 'wb') as f:
        f.write(body)
    with gzip.open(directory / name.replace('.gz', '.bib.gz'), 'wb') as f:
        f.write(b'@article{x}\n')
    return directory / name


def complete_request(user, output_file, age_days=0):
    req = Request.objects.create(user=user, request_type='extractall',
                                 parameters={}, status='complete',
                                 output_file=str(output_file))
    when = timezone.now() - datetime.timedelta(days=age_days)
    Request.objects.filter(pk=req.pk).update(created_at=when, completed_at=when)
    req.refresh_from_db()
    return req


# --- R18: resolve against VALD_FTP_DIR rather than storing absolute paths ---

@pytest.mark.django_db
def test_bare_filename_resolves_against_ftp_dir(approved_user, ftp_dir):
    make_result(ftp_dir)
    req = complete_request(approved_user, 'Result.000001.gz')
    assert req.output_path == ftp_dir / 'Result.000001.gz'
    assert req.output_exists()
    assert req.bib_output_exists()
    assert req.get_output_size() is not None


@pytest.mark.django_db
def test_result_follows_ftp_dir_when_it_moves(approved_user, ftp_dir, tmp_path, settings):
    """The cutover moves VALD_FTP_DIR; new-style rows must follow it."""
    make_result(ftp_dir)
    req = complete_request(approved_user, 'Result.000001.gz')

    moved = tmp_path / 'new-FTP'
    moved.mkdir()
    make_result(moved)
    settings.VALD_FTP_DIR = moved

    assert req.output_path == moved / 'Result.000001.gz'
    assert req.output_exists()


@pytest.mark.django_db
def test_absolute_paths_still_work(approved_user, ftp_dir, tmp_path, settings):
    """Rows written before the change hold absolute paths and must keep resolving."""
    elsewhere = tmp_path / 'legacy'
    elsewhere.mkdir()
    absolute = make_result(elsewhere)
    req = complete_request(approved_user, absolute)

    settings.VALD_FTP_DIR = ftp_dir     # different directory entirely
    assert req.output_exists()
    assert req.output_path == absolute


# --- R21: expired results are not the same as missing ones -----------------

@pytest.mark.django_db
def test_present_result_is_neither_expired_nor_missing(approved_user, ftp_dir):
    make_result(ftp_dir)
    req = complete_request(approved_user, 'Result.000001.gz')
    assert not req.results_expired()
    assert not req.results_missing()


@pytest.mark.django_db
def test_old_result_whose_file_is_gone_reads_as_expired(approved_user, ftp_dir, settings):
    req = complete_request(approved_user, 'Gone.000001.gz',
                           age_days=settings.VALD_RESULT_RETENTION_DAYS + 1)
    assert req.results_expired()
    assert not req.results_missing()


@pytest.mark.django_db
def test_recent_result_whose_file_is_gone_reads_as_missing(approved_user, ftp_dir):
    req = complete_request(approved_user, 'Gone.000001.gz', age_days=0)
    assert not req.results_expired()
    assert req.results_missing(), 'too recent for the sweep to explain'


@pytest.mark.django_db
def test_unfinished_request_is_neither(approved_user, ftp_dir):
    req = Request.objects.create(user=approved_user, request_type='extractall',
                                 parameters={}, status='processing')
    assert not req.results_expired()
    assert not req.results_missing()


@pytest.mark.django_db
def test_expired_and_missing_render_differently(logged_in_client, approved_user, ftp_dir, settings):
    expired = complete_request(approved_user, 'Gone.000001.gz',
                               age_days=settings.VALD_RESULT_RETENTION_DAYS + 1)
    missing = complete_request(approved_user, 'Gone.000002.gz', age_days=0)

    expired_page = logged_in_client.get(f'/request/{expired.uuid}/').content.decode()
    missing_page = logged_in_client.get(f'/request/{missing.uuid}/').content.decode()

    assert 'expired' in expired_page.lower()
    assert 'not found' in missing_page.lower()
    assert 'expired' not in missing_page.lower()


@pytest.mark.django_db
def test_retention_description_follows_the_setting(settings):
    settings.VALD_RESULT_RETENTION_DAYS = 2
    assert Request.retention_description() == '48 hours'
    settings.VALD_RESULT_RETENTION_DAYS = 1
    assert Request.retention_description() == '24 hours'


# --- R18: downloads must stay inside the results directory -----------------

@pytest.mark.django_db
def test_download_serves_a_normal_result(logged_in_client, approved_user, ftp_dir):
    make_result(ftp_dir)
    req = complete_request(approved_user, 'Result.000001.gz')
    assert logged_in_client.get(f'/request/{req.uuid}/download/').status_code == 200
    assert logged_in_client.get(f'/request/{req.uuid}/download-bib/').status_code == 200


@pytest.mark.django_db
def test_download_refuses_a_path_outside_the_ftp_dir(logged_in_client, approved_user,
                                                     ftp_dir, tmp_path):
    outside = tmp_path / 'secret.gz'
    with gzip.open(outside, 'wb') as f:
        f.write(b'should not be served\n')
    req = complete_request(approved_user, outside)

    response = logged_in_client.get(f'/request/{req.uuid}/download/')
    assert response.status_code != 200


@pytest.mark.django_db
def test_download_requires_ownership(approved_user, ftp_dir, db):
    from django.test import Client
    from vald.models import User, UserEmail

    make_result(ftp_dir)
    req = complete_request(approved_user, 'Result.000001.gz')

    other = User.objects.create(name='Other', is_active=True)
    other.set_password('pw-for-testing-123')
    other.save()
    UserEmail.objects.create(user=other, email='other@example.com', is_primary=True)
    client = Client()
    client.post('/login/', {'user': 'other@example.com', 'password': 'pw-for-testing-123'})

    assert client.get(f'/request/{req.uuid}/download/').status_code != 200


# --- the tab title tracks the job, so a background tab reports it finishing ---

def title_of(html):
    return re.search(r'<title>(.*?)</title>', html, re.S).group(1).strip()


@pytest.mark.django_db
@pytest.mark.parametrize('status, expected', [
    ('pending', '⋯ Queued'),
    ('processing', '⟳ Running'),
    ('complete', '✓ Done'),
    ('failed', '✗ Failed'),
])
def test_title_reports_the_status_first(logged_in_client, approved_user, status, expected):
    req = Request.objects.create(user=approved_user, request_type='extractall',
                                 parameters={}, status=status)
    title = title_of(logged_in_client.get(f'/request/{req.uuid}/').content.decode())
    assert title.startswith(expected), \
        'the status must lead, or a truncated tab title shows only the site name'


@pytest.mark.django_db
def test_expired_results_do_not_claim_to_be_done(logged_in_client, approved_user, ftp_dir,
                                                 settings):
    settings.VALD_RESULT_RETENTION_DAYS = 2
    req = complete_request(approved_user, 'Gone.000001.gz', age_days=30)
    title = title_of(logged_in_client.get(f'/request/{req.uuid}/').content.decode())
    assert title.startswith('Expired')


@pytest.mark.django_db
def test_the_title_still_names_the_site(logged_in_client, approved_user, settings):
    req = Request.objects.create(user=approved_user, request_type='extractall',
                                 parameters={}, status='processing')
    title = title_of(logged_in_client.get(f'/request/{req.uuid}/').content.decode())
    assert settings.SITENAME in title


@pytest.mark.django_db
def test_a_running_request_reloads_itself(logged_in_client, approved_user):
    """Without the refresh the title would only ever show the status at load."""
    req = Request.objects.create(user=approved_user, request_type='extractall',
                                 parameters={}, status='processing')
    body = logged_in_client.get(f'/request/{req.uuid}/').content.decode()
    assert 'http-equiv="refresh"' in body


@pytest.mark.django_db
def test_other_pages_keep_the_plain_title(logged_in_client, settings):
    title = title_of(logged_in_client.get('/').content.decode())
    assert title == settings.SITENAME
