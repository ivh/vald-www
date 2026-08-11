"""The two personal-configuration states, and the transitions between them (R47).

    no personal config - requests use the VALD default, and pick up whatever a
                         future VALD release adds to it.
    personal config    - a snapshot taken at the first edit, plus the user's
                         edits. It does not follow the VALD default.

Only the second used to be reachable: opening the page created a config, which
silently froze the visitor at that day's linelists with nothing saying so.
"""
import pytest

from vald.models import Config, ConfigLinelist, Linelist


@pytest.fixture
def vald_default(db):
    """A system default holding three linelists."""
    config = Config.objects.create(name='Default', user=None, is_default=True)
    for i in range(3):
        ll = Linelist.objects.create(path=f'/CVALD3/ATOMS/x{i}', name=f'List {i}',
                                     element_min=1, element_max=99)
        ConfigLinelist.objects.create(config=config, linelist=ll, priority=10 * i)
    return config


def add_linelist_to_default(config, path='/CVALD3/ATOMS/new', name='Added in 2027'):
    """What a new VALD release does."""
    ll = Linelist.objects.create(path=path, name=name, element_min=1, element_max=99)
    ConfigLinelist.objects.create(config=config, linelist=ll, priority=99)
    return ll


def edit_first_linelist(client, config):
    entry = config.configlinelist_set.order_by('priority').first()
    payload = {'action': 'save', 'editid': str(entry.linelist_id), 'linelist-checked': 'on'}
    payload.update({f'edit-val-{j}': '7' for j in range(9)})
    return client.post('/persconf/', payload)


# --- looking is not customising --------------------------------------------

@pytest.mark.django_db
def test_viewing_the_page_creates_nothing(logged_in_client, vald_default, approved_user):
    assert logged_in_client.get('/persconf/').status_code == 200
    assert not Config.objects.filter(user=approved_user).exists()
    assert ConfigLinelist.objects.filter(config__user__isnull=False).count() == 0


@pytest.mark.django_db
def test_viewing_the_page_shows_the_default_contents(logged_in_client, vald_default):
    body = logged_in_client.get('/persconf/').content.decode()
    assert 'List 0' in body and 'List 2' in body
    assert 'using the VALD default configuration' in body


@pytest.mark.django_db
def test_a_user_without_a_config_tracks_the_evolving_default(logged_in_client, vald_default,
                                                             approved_user):
    """The state that did not exist before."""
    logged_in_client.get('/persconf/')
    add_linelist_to_default(vald_default)

    used = Config.get_user_config(approved_user)
    assert 'ATOMS/new' in used.generate_cfg_content()


# --- first edit is the transition ------------------------------------------

@pytest.mark.django_db
def test_editing_creates_the_personal_config(logged_in_client, vald_default, approved_user):
    edit_first_linelist(logged_in_client, vald_default)

    mine = Config.objects.get(user=approved_user)
    assert mine.configlinelist_set.count() == 3
    assert mine.configlinelist_set.order_by('priority').first().rank_wl == 7


@pytest.mark.django_db
def test_the_transition_is_announced(logged_in_client, vald_default):
    response = edit_first_linelist(logged_in_client, vald_default)
    said = ' '.join(str(m) for m in response.context['messages'])
    assert 'created your personal configuration' in said


@pytest.mark.django_db
def test_a_personal_config_does_not_follow_the_default(logged_in_client, vald_default,
                                                       approved_user):
    edit_first_linelist(logged_in_client, vald_default)
    add_linelist_to_default(vald_default)

    used = Config.get_user_config(approved_user)
    assert 'ATOMS/new' not in used.generate_cfg_content(), 'snapshot silently moved'


@pytest.mark.django_db
def test_the_page_says_what_a_frozen_config_is_missing(logged_in_client, vald_default):
    """Caveat 2: a snapshot whose age is invisible is the same trap."""
    edit_first_linelist(logged_in_client, vald_default)
    add_linelist_to_default(vald_default)

    body = logged_in_client.get('/persconf/').content.decode()
    assert 'You have a personal configuration' in body
    assert '1 linelist' in body and 'added to the VALD default since' in body
    assert 'Added in 2027' in body


@pytest.mark.django_db
def test_no_warning_when_the_snapshot_is_current(logged_in_client, vald_default):
    edit_first_linelist(logged_in_client, vald_default)
    body = logged_in_client.get('/persconf/').content.decode()
    assert 'added to the VALD default since' not in body


# --- remove: back to tracking ----------------------------------------------

@pytest.mark.django_db
def test_remove_deletes_the_config_rather_than_re_snapshotting(logged_in_client, vald_default,
                                                               approved_user):
    """The old single 'reset' button deleted and immediately re-created."""
    edit_first_linelist(logged_in_client, vald_default)
    logged_in_client.post('/persconf/', {'action': 'remove'})

    assert not Config.objects.filter(user=approved_user).exists()


@pytest.mark.django_db
def test_after_remove_the_user_tracks_the_default_again(logged_in_client, vald_default,
                                                        approved_user):
    edit_first_linelist(logged_in_client, vald_default)
    logged_in_client.post('/persconf/', {'action': 'remove'})
    add_linelist_to_default(vald_default)

    used = Config.get_user_config(approved_user)
    assert 'ATOMS/new' in used.generate_cfg_content()


@pytest.mark.django_db
def test_remove_with_nothing_to_remove_is_harmless(logged_in_client, vald_default,
                                                   approved_user):
    assert logged_in_client.post('/persconf/', {'action': 'remove'}).status_code == 200
    assert not Config.objects.filter(user=approved_user).exists()


# --- set to current default: stay frozen, discard edits ---------------------

@pytest.mark.django_db
def test_set_to_current_default_from_no_config(logged_in_client, vald_default, approved_user):
    logged_in_client.post('/persconf/', {'action': 'set_to_current_default'})

    mine = Config.objects.get(user=approved_user)
    assert mine.configlinelist_set.count() == 3


@pytest.mark.django_db
def test_set_to_current_default_discards_edits(logged_in_client, vald_default, approved_user):
    edit_first_linelist(logged_in_client, vald_default)
    logged_in_client.post('/persconf/', {'action': 'set_to_current_default'})

    mine = Config.objects.get(user=approved_user)
    assert mine.configlinelist_set.order_by('priority').first().rank_wl == 3


@pytest.mark.django_db
def test_set_to_current_default_picks_up_additions_then_stops(logged_in_client, vald_default,
                                                              approved_user):
    """It re-snapshots, so it collects what was added - and freezes again after."""
    edit_first_linelist(logged_in_client, vald_default)
    add_linelist_to_default(vald_default)
    logged_in_client.post('/persconf/', {'action': 'set_to_current_default'})

    used = Config.get_user_config(approved_user)
    assert 'ATOMS/new' in used.generate_cfg_content()

    add_linelist_to_default(vald_default, '/CVALD3/ATOMS/newer', 'Added in 2028')
    used = Config.get_user_config(approved_user)
    assert 'ATOMS/newer' not in used.generate_cfg_content()


# --- restore never freezes anyone ------------------------------------------

@pytest.mark.django_db
def test_restore_does_not_create_a_config(logged_in_client, vald_default, approved_user):
    """Restoring to default while already at the default must stay a no-op."""
    entry = vald_default.configlinelist_set.first()
    logged_in_client.post('/persconf/',
                          {'action': 'restore', 'editid': str(entry.linelist_id)})
    assert not Config.objects.filter(user=approved_user).exists()


@pytest.mark.django_db
def test_restore_still_works_on_a_personal_config(logged_in_client, vald_default,
                                                  approved_user):
    edit_first_linelist(logged_in_client, vald_default)
    mine = Config.objects.get(user=approved_user)
    entry = mine.configlinelist_set.order_by('priority').first()

    logged_in_client.post('/persconf/',
                          {'action': 'restore', 'editid': str(entry.linelist_id)})
    entry.refresh_from_db()
    assert entry.rank_wl == 3


# --- junk input ------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize('editid', ['not-a-number', '', '999999', '-1'])
def test_a_bogus_linelist_id_creates_nothing(logged_in_client, vald_default,
                                             approved_user, editid):
    response = logged_in_client.post('/persconf/', {'action': 'save', 'editid': editid})
    assert response.status_code == 200
    assert not Config.objects.filter(user=approved_user).exists(), \
        'invalid input left a personal configuration behind'


# --- retired linelists ------------------------------------------------------

@pytest.mark.django_db
def test_a_retired_linelist_is_left_out_of_the_generated_cfg(logged_in_client, vald_default,
                                                             approved_user):
    """A snapshot can outlive the linelists in it; the .cfg must not name a
    data file that has left the SVN tree."""
    edit_first_linelist(logged_in_client, vald_default)
    Linelist.objects.filter(path='/CVALD3/ATOMS/x2').update(is_active=False)

    content = Config.get_user_config(approved_user).generate_cfg_content()
    assert 'ATOMS/x2' not in content
    assert 'ATOMS/x1' in content


# --- the one-off cleanup of configs nobody asked for ------------------------

def run_cleanup_migration():
    """Call migration 0011's function against the live models.

    The historical and current models are identical for the fields it touches,
    so passing the real registry exercises the actual code rather than a copy of
    it kept in the test.
    """
    from importlib import import_module
    from django.apps import apps
    module = import_module('vald.migrations.0011_drop_configs_identical_to_default')
    module.drop_configs_identical_to_default(apps, None)


@pytest.mark.django_db
def test_cleanup_drops_a_config_that_is_an_exact_copy(logged_in_client, vald_default,
                                                      approved_user):
    """What a bare GET used to create for every visitor."""
    from vald.persconfig import create_user_config
    create_user_config(approved_user)

    run_cleanup_migration()
    assert not Config.objects.filter(user=approved_user).exists()


@pytest.mark.django_db
@pytest.mark.parametrize('field,value', [
    ('is_enabled', False),
    ('rank_wl', 9),
    ('replacement_window', 0.08),
    ('priority', 12345),
    ('mergeable', 2),
])
def test_cleanup_keeps_a_config_that_differs_anywhere(logged_in_client, vald_default,
                                                      approved_user, field, value):
    """Including fields the web UI cannot edit - that is how the imported legacy
    persconf files differ."""
    from vald.persconfig import create_user_config
    mine = create_user_config(approved_user)
    entry = mine.configlinelist_set.first()
    setattr(entry, field, value)
    entry.save()

    run_cleanup_migration()
    assert Config.objects.filter(user=approved_user).exists(), \
        f'a real customisation in {field} was deleted'


@pytest.mark.django_db
def test_cleanup_keeps_a_config_differing_only_in_global_params(vald_default, approved_user):
    from vald.persconfig import create_user_config
    mine = create_user_config(approved_user)
    mine.max_ionization = 5
    mine.save()

    run_cleanup_migration()
    assert Config.objects.filter(user=approved_user).exists()


# --- the snapshot date must be the user's, not the import run's -------------

@pytest.mark.django_db
def test_imported_config_reports_the_source_file_date(vald_default, tmp_path, settings):
    """created_at is the import run - today - which says nothing about a config
    carried over from the legacy interface."""
    import os, datetime
    from django.core.management import call_command
    from django.utils.timezone import get_current_timezone
    from vald.models import User

    settings.PERSCONFIG_DIR = tmp_path
    User.objects.create(name='Jane Doe', is_active=True)
    cfg = tmp_path / 'JaneDoe.cfg'
    cfg.write_text(
        "0.05,5000.,9,150.\n"
        "'/CVALD3/ATOMS/x0', 0, 1, 99, 0, 9,3,3,3,3,3,3,3,3, 'List 0'\n"
        "'/CVALD3/ATOMS/x1', 10, 1, 99, 0, 3,3,3,3,3,3,3,3,3, 'List 1'\n"
        "'/CVALD3/ATOMS/x2', 20, 1, 99, 0, 3,3,3,3,3,3,3,3,3, 'List 2'\n")

    long_ago = datetime.datetime(2019, 5, 3, 14, 30, tzinfo=get_current_timezone())
    os.utime(cfg, (long_ago.timestamp(), long_ago.timestamp()))

    call_command('import_persconf', 'JaneDoe.cfg', verbosity=0)

    mine = Config.objects.get(user__name='Jane Doe')
    assert mine.snapshot_at.date() == long_ago.date()
    assert mine.snapshot_date.date() == long_ago.date()
    assert mine.created_at.date() != long_ago.date(), 'created_at should be the import run'


@pytest.mark.django_db
def test_a_config_made_here_falls_back_to_created_at(logged_in_client, vald_default,
                                                     approved_user):
    edit_first_linelist(logged_in_client, vald_default)
    mine = Config.objects.get(user=approved_user)
    assert mine.snapshot_at is None
    assert mine.snapshot_date == mine.created_at


@pytest.mark.django_db
def test_the_page_shows_the_source_file_date(logged_in_client, vald_default, tmp_path,
                                             settings, approved_user):
    import os, datetime
    from django.core.management import call_command
    from django.utils.timezone import get_current_timezone

    settings.PERSCONFIG_DIR = tmp_path
    cfg = tmp_path / 'TestUser.cfg'
    cfg.write_text(
        "0.05,5000.,9,150.\n"
        "'/CVALD3/ATOMS/x0', 0, 1, 99, 0, 9,3,3,3,3,3,3,3,3, 'List 0'\n"
        "'/CVALD3/ATOMS/x1', 10, 1, 99, 0, 3,3,3,3,3,3,3,3,3, 'List 1'\n"
        "'/CVALD3/ATOMS/x2', 20, 1, 99, 0, 3,3,3,3,3,3,3,3,3, 'List 2'\n")
    long_ago = datetime.datetime(2019, 5, 3, 14, 30, tzinfo=get_current_timezone())
    os.utime(cfg, (long_ago.timestamp(), long_ago.timestamp()))
    call_command('import_persconf', 'TestUser.cfg', verbosity=0)

    body = logged_in_client.get('/persconf/').content.decode()
    assert '3 May 2019' in body, 'the page still shows the import date'


# --- the "Custom" linelist choice, which only means something in state 2 -----
#
# Choosing it in state 1 used to do nothing observable: Config.get_user_config()
# falls back to the system default, so the job ran with the default config while
# the stored request claimed a custom one.

import re

from vald.forms import ExtractAllForm
from vald.persconfig import get_or_create_user_config

FORM_PAGES = ['/extractall/', '/extractelement/', '/extractstellar/',
              '/showline/', '/showline-online/']

EXTRACT_BASE = {'reqtype': 'extractall', 'stwvl': '5000', 'endwvl': '5002',
                'format': 'short', 'viaftp': 'via ftp'}


def personal_radio(body):
    return re.search(r'<input[^>]*value="personal"[^>]*>', body).group(0)


@pytest.mark.parametrize('page', FORM_PAGES)
def test_custom_config_is_greyed_out_without_a_personal_config(
        logged_in_client, vald_default, page):
    body = logged_in_client.get(page).content.decode()
    assert 'disabled' in personal_radio(body)
    assert 'Custom (no personal configuration saved)' in body


@pytest.mark.parametrize('page', FORM_PAGES)
def test_custom_config_is_offered_once_a_personal_config_exists(
        logged_in_client, approved_user, vald_default, page):
    get_or_create_user_config(approved_user)
    body = logged_in_client.get(page).content.decode()
    assert 'disabled' not in personal_radio(body)


def test_personal_choice_is_refused_when_the_user_has_none(approved_user, vald_default):
    """The disabled input is not submitted, so this is the gate that matters."""
    form = ExtractAllForm({**EXTRACT_BASE, 'pconf': 'personal'}, user=approved_user)
    assert not form.is_valid()
    assert 'pconf' in form.errors


def test_personal_choice_is_accepted_when_the_user_has_one(approved_user, vald_default):
    get_or_create_user_config(approved_user)
    form = ExtractAllForm({**EXTRACT_BASE, 'pconf': 'personal'}, user=approved_user)
    assert form.is_valid(), form.errors


def test_default_choice_still_works_without_a_personal_config(approved_user, vald_default):
    form = ExtractAllForm({**EXTRACT_BASE, 'pconf': 'default'}, user=approved_user)
    assert form.is_valid(), form.errors


def test_a_missing_user_fails_closed():
    """No HTTP path reaches this - require_login guards every form view - so a
    call site without a user should lose the option, not the check."""
    form = ExtractAllForm({**EXTRACT_BASE, 'pconf': 'personal'})
    assert not form.is_valid()
    assert 'pconf' in form.errors


def test_a_personal_prefill_falls_back_when_the_config_is_gone(approved_user, vald_default):
    """?modify= of a request made before the config was deleted (R47 state 1)."""
    form = ExtractAllForm(initial={'pconf': 'personal'}, user=approved_user)
    assert form.initial['pconf'] == 'default'
