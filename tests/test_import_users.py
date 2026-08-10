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
    # dry-run predicts the pooling, so counts reflect a real import
    assert 'Would create 1 user(s)' in out
    # the second record carries nothing the first did not, so nothing is added
    assert '1 already up to date' in out


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
def test_real_run_pools_shared_email_and_says_so(tmp_path):
    text = """\
#$ First Name
shared@example.com

#$ Second Name
shared@example.com
extra@example.com
"""
    out = run(tmp_path, text)
    assert User.objects.count() == 1                 # pooled, not two users
    assert 'Added 1 email(s) to existing user: First Name' in out
    user = User.objects.get()
    assert user.name == 'First Name'                 # the account keeps its name
    assert user.affiliation == ''                    # and the old name is not stashed here
    assert set(user.emails.values_list('email', flat=True)) == {
        'shared@example.com', 'extra@example.com'}


# --- identity is the database's, not the register's -------------------------
#
# A register is by definition an older snapshot of the database, and both fields
# are now visible to the account holder on /account/, so a re-import must not
# rewrite them. This also removes the old dependence on the order registers were
# imported in, where the last record to mention an address won the name.

@pytest.mark.django_db
def test_reimport_does_not_rename_an_existing_account(tmp_path):
    run(tmp_path, "#$ Ada Lovelace\nada@example.com\n")
    run(tmp_path, "#$ A. A. Lovelace\nada@example.com\n")

    assert User.objects.get().name == 'Ada Lovelace'


@pytest.mark.django_db
def test_reimport_does_not_overwrite_an_affiliation(tmp_path):
    run(tmp_path, "#$ Ada Lovelace\n#  Uppsala University\nada@example.com\n")
    run(tmp_path, "#$ Ada Lovelace\n#  Somewhere Else\nada@example.com\n")

    assert User.objects.get().affiliation == 'Uppsala University'


@pytest.mark.django_db
def test_a_user_edit_survives_a_later_import(tmp_path):
    """The case the account page created: the register must not undo an edit."""
    run(tmp_path, "#$ Ada Lovelace\n#  Uppsala University\nada@example.com\n")
    user = User.objects.get()
    user.affiliation = 'ESO, Garching\nStaff astronomer'
    user.save()

    run(tmp_path, "#$ Ada Lovelace\n#  Uppsala University\nada@example.com\n")

    user.refresh_from_db()
    assert user.affiliation == 'ESO, Garching\nStaff astronomer'


@pytest.mark.django_db
def test_a_blank_affiliation_is_still_filled_in(tmp_path):
    """The one case with nothing to overwrite - 323 of the imported accounts."""
    run(tmp_path, "#$ Ada Lovelace\nada@example.com\n")
    assert User.objects.get().affiliation == ''

    run(tmp_path, "#$ Ada Lovelace\n#  Uppsala University\nada@example.com\n")
    assert User.objects.get().affiliation == 'Uppsala University'


@pytest.mark.django_db
def test_pooling_still_adds_new_addresses(tmp_path):
    """Not a blanket skip: pooling addresses is why a second register is imported."""
    run(tmp_path, "#$ Ada Lovelace\nada@example.com\n")
    run(tmp_path, "#$ Ada Lovelace\nada@example.com\nada@other.example.com\n")

    assert User.objects.count() == 1
    assert set(User.objects.get().emails.values_list('email', flat=True)) == {
        'ada@example.com', 'ada@other.example.com'}


# --- and it must not claim to have done anything when it did not ------------

@pytest.mark.django_db
def test_reimporting_an_unchanged_register_reports_no_changes(tmp_path):
    """Now that identity is never rewritten, a second run of the same file is a
    no-op - and used to announce an addition for every one of its records."""
    run(tmp_path, TWO_GOOD)
    out = run(tmp_path, TWO_GOOD)

    assert 'Created 0 user(s)' in out
    assert 'added to 0 existing account(s)' in out
    assert '2 already up to date' in out
    assert 'Already up to date: Alice Adams' in out
    assert 'Added' not in out


@pytest.mark.django_db
def test_an_address_already_known_is_marked_as_such(tmp_path):
    run(tmp_path, "#$ Ada Lovelace\nada@example.com\n")
    out = run(tmp_path, "#$ Ada Lovelace\nada@example.com\nada@other.example.com\n",
              '--dry-run')

    assert '- ada@example.com (already known)' in out
    assert '- ada@other.example.com\n' in out          # no marker: this one is new


@pytest.mark.django_db
def test_filling_a_blank_affiliation_alone_is_reported_as_such(tmp_path):
    """The record adds no address, so the affiliation is the only change."""
    run(tmp_path, "#$ Ada Lovelace\nada@example.com\n")
    out = run(tmp_path, "#$ Ada Lovelace\n#  Uppsala University\nada@example.com\n")

    assert 'Added an affiliation to existing user: Ada Lovelace' in out
    assert 'added to 1 existing account(s)' in out
    assert 'email(s) to existing' not in out


@pytest.mark.django_db
def test_both_changes_are_reported_together(tmp_path):
    run(tmp_path, "#$ Ada Lovelace\nada@example.com\n")
    out = run(tmp_path, "#$ Ada Lovelace\n#  Uppsala University\n"
                        "ada@example.com\nada@other.example.com\n")

    assert 'Added 1 email(s) and an affiliation to existing user: Ada Lovelace' in out


@pytest.mark.django_db
def test_dry_run_says_when_an_affiliation_would_not_be_applied(tmp_path):
    run(tmp_path, "#$ Ada Lovelace\n#  Uppsala University\nada@example.com\n")
    out = run(tmp_path, "#$ Ada Lovelace\n#  Somewhere Else\nada@example.com\n", '--dry-run')

    assert 'Affiliation: Somewhere Else (not applied - the account already has one)' in out


@pytest.mark.django_db
def test_dry_run_does_not_warn_when_the_affiliation_would_be_applied(tmp_path):
    run(tmp_path, "#$ Ada Lovelace\nada@example.com\n")
    out = run(tmp_path, "#$ Ada Lovelace\n#  Uppsala University\nada@example.com\n", '--dry-run')

    assert 'Affiliation: Uppsala University' in out
    assert 'not applied' not in out


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
# It predicted new-vs-pooled from the file alone, so against a database that
# already held the register it reported "Would create 3934" where a real import
# created 1. Re-running the dry run to check a delta is the only reason to run
# it twice, and that was exactly the case it got wrong.

@pytest.mark.django_db
def test_dry_run_against_a_populated_database_counts_pooled_records(tmp_path):
    run(tmp_path, TWO_GOOD)                          # first, for real
    out = run(tmp_path, TWO_GOOD, '--dry-run')       # then ask again

    assert 'Would create 0 user(s)' in out
    assert 'would add to 0 existing account(s)' in out
    assert '2 already up to date' in out
    assert 'Would create user: Alice Adams' not in out
    assert 'Already up to date: Alice Adams' in out


@pytest.mark.django_db
def test_dry_run_reports_only_the_new_record(tmp_path):
    """The delta case: one name added to a register already imported."""
    run(tmp_path, TWO_GOOD)
    out = run(tmp_path, TWO_GOOD + "\n#$ Carol Chen\ncarol@example.org\n", '--dry-run')

    assert 'Would create 1 user(s)' in out
    assert '2 already up to date' in out
    assert 'Would create user: Carol Chen' in out


@pytest.mark.django_db
def test_two_records_with_the_same_name_and_address_predict_pooling(tmp_path):
    """No COLLISION is reported (same person), but pooling still happens - and
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
    assert 'would add to 1 existing account(s)' in out
    assert 'Would add 1 email(s) to existing user' in out


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
    assert 'would add to 0 existing account(s)' in predicted
    assert 'added to 0 existing account(s)' in actual
    assert '1 already up to date' in predicted
    assert '1 already up to date' in actual
    assert User.objects.count() == 3
