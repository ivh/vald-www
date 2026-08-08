"""Hardening of import_users: validation, collision reporting, counts."""
import io

import pytest
from django.core.management import call_command

from vald.models import User, UserEmail


def run(tmp_path, text, *args):
    reg = tmp_path / 'clients.register'
    reg.write_text(text, encoding='utf-8')
    out = io.StringIO()
    call_command('import_users', '--file', str(reg), *args, stdout=out)
    return out.getvalue()


TWO_GOOD = """\
#$ Alice Adams
#  Some Institute
alice@example.com

#$ Bob Barnes
bob@example.edu
"""


@pytest.mark.django_db
def test_dry_run_counts_are_not_zero(tmp_path):
    """The old summary always said 'Processed 0 users' in dry-run."""
    out = run(tmp_path, TWO_GOOD, '--dry-run')
    assert 'Would create 2 user(s)' in out
    assert '2 email(s)' in out
    assert User.objects.count() == 0        # dry-run really changed nothing


@pytest.mark.django_db
def test_real_run_creates_users(tmp_path):
    out = run(tmp_path, TWO_GOOD)
    assert User.objects.count() == 2
    assert UserEmail.objects.count() == 2
    assert 'Created 2 user(s)' in out


@pytest.mark.django_db
@pytest.mark.parametrize('bad', [
    'foo@bar.',                       # trailing dot
    'foo@bar#',                       # stray comment marker
    'a@b.na@b.nll',                   # two addresses concatenated
    'not-an-email',
])
def test_malformed_emails_are_skipped_and_reported(tmp_path, bad):
    text = f"#$ Test Person\n{bad}\ngood@example.com\n"
    out = run(tmp_path, text)
    assert 'skipping malformed email' in out
    assert bad in out
    # the good address still imports; the bad one is not stored
    assert UserEmail.objects.filter(email='good@example.com').exists()
    assert not UserEmail.objects.filter(email=bad).exists()


@pytest.mark.django_db
def test_record_with_only_a_bad_email_creates_no_user(tmp_path):
    run(tmp_path, "#$ Ghost\nnot-an-email\n")
    assert User.objects.count() == 0


@pytest.mark.django_db
def test_shared_email_between_different_names_is_reported(tmp_path):
    text = """\
#$ David Mkrtichian
shared@tls.de

#$ David Wockel
shared@tls.de
"""
    out = run(tmp_path, text, '--dry-run')
    assert 'COLLISION' in out
    assert 'shared@tls.de' in out
    assert '1 email(s) shared between differently-named records' in out
    # dry-run predicts the merge, so counts reflect a real import
    assert 'Would create 1 user(s)' in out
    assert 'would merge 1 into existing' in out


@pytest.mark.django_db
def test_same_name_twice_is_not_flagged_as_collision(tmp_path):
    text = """\
#$ Raul Puebla
raul@example.com

#$ Raul Puebla
raul2@example.com
"""
    out = run(tmp_path, text, '--dry-run')
    assert 'COLLISION' not in out


@pytest.mark.django_db
def test_real_run_merges_shared_email_and_says_so(tmp_path):
    text = """\
#$ First Name
shared@example.com

#$ Second Name
shared@example.com
extra@example.com
"""
    out = run(tmp_path, text)
    assert User.objects.count() == 1                 # merged, not two users
    assert 'Merged into existing user' in out
    user = User.objects.get()
    assert user.name == 'Second Name'                # last name wins
    assert 'First Name' in user.affiliation          # earlier name preserved
    assert set(user.emails.values_list('email', flat=True)) == {
        'shared@example.com', 'extra@example.com'}


@pytest.mark.django_db
def test_non_utf8_bytes_do_not_abort_the_import(tmp_path):
    """Legacy registers are not clean UTF-8; a stray byte must not kill the run."""
    reg = tmp_path / 'clients.register'
    # 0xf0 is the exact bad byte in the real register's affiliation lines
    reg.write_bytes(
        b'#$ Jose Nunez\n'
        b'#  Observatorio de M\xf0xico\n'
        b'jose@example.com\n'
    )
    out = io.StringIO()
    call_command('import_users', '--file', str(reg), stdout=out)
    assert User.objects.filter(name='Jose Nunez').exists()
    assert UserEmail.objects.filter(email='jose@example.com').exists()


# --- the dry run must answer the question you are actually asking -----------
#
# It predicted new-vs-merge from the file alone, so against a database that
# already held the register it reported "Would create 3934" where a real import
# created 1. Re-running the dry run to check a delta is the only reason to run
# it twice, and that was exactly the case it got wrong.

@pytest.mark.django_db
def test_dry_run_against_a_populated_database_counts_merges(tmp_path):
    run(tmp_path, TWO_GOOD)                          # first, for real
    out = run(tmp_path, TWO_GOOD, '--dry-run')       # then ask again

    assert 'Would create 0 user(s)' in out
    assert 'would merge 2 into existing' in out
    assert 'Would create user: Alice Adams' not in out
    assert 'Would merge into existing user: Alice Adams' in out


@pytest.mark.django_db
def test_dry_run_reports_only_the_new_record(tmp_path):
    """The delta case: one name added to a register already imported."""
    run(tmp_path, TWO_GOOD)
    out = run(tmp_path, TWO_GOOD + "\n#$ Carol Chen\ncarol@example.org\n", '--dry-run')

    assert 'Would create 1 user(s)' in out
    assert 'would merge 2 into existing' in out
    assert 'Would create user: Carol Chen' in out


@pytest.mark.django_db
def test_two_records_with_the_same_name_and_address_predict_a_merge(tmp_path):
    """No COLLISION is reported (same person), but a merge still happens - and
    the dry run used to count both records as creations."""
    text = """\
#$ Raul Puebla
raul@example.com

#$ Raul Puebla
raul@example.com
other@example.com
"""
    out = run(tmp_path, text, '--dry-run')
    assert 'COLLISION' not in out
    assert 'Would create 1 user(s)' in out
    assert 'would merge 1 into existing' in out


@pytest.mark.django_db
def test_dry_run_predictions_match_the_real_import(tmp_path):
    """The property that matters: predicted counts equal actual counts."""
    text = TWO_GOOD + """
#$ Carol Chen
carol@example.org

#$ Carol C. Chen
carol@example.org
"""
    predicted = run(tmp_path, text, '--dry-run')
    actual = run(tmp_path, text)

    assert 'Would create 3 user(s)' in predicted
    assert 'Created 3 user(s)' in actual
    assert 'would merge 1 into existing' in predicted
    assert 'merged 1 into existing' in actual
    assert User.objects.count() == 3
