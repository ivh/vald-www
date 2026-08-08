"""Importing the legacy unit preferences (<Name>-HTMLdefs.cfg).

These files sit in PERSCONFIG_DIR alongside the personal linelist configs and
were swept up by `--all`'s *.cfg glob, where every one of them failed the
user-name match and was counted as a failure.
"""
import pytest
from django.core.management import call_command

from vald.models import User, UserPreferences, Config, Linelist, ConfigLinelist


DEFAULTS = "energyunit\teV\nmedium\tair\nwaveunit\tangstrom\n" \
           "vdwformat\tdefault\nisotopic_scaling\ton\n"


@pytest.fixture
def persconf_dir(tmp_path, settings):
    settings.PERSCONFIG_DIR = tmp_path
    return tmp_path


@pytest.fixture
def jane(db):
    return User.objects.create(name='Jane Doe', is_active=True)


def write_htmldefs(directory, filename, text):
    path = directory / filename
    path.write_text(text)
    return path


@pytest.mark.django_db
def test_preferences_are_imported(persconf_dir, jane):
    write_htmldefs(persconf_dir, 'JaneDoe-HTMLdefs.cfg',
                   "energyunit\t1/cm\nmedium\tvacuum\nwaveunit\tnm\n"
                   "vdwformat\textended\nisotopic_scaling\toff\n")

    call_command('import_persconf', 'JaneDoe-HTMLdefs.cfg', verbosity=0)

    prefs = UserPreferences.objects.get(user=jane)
    assert prefs.energyunit == '1/cm'
    assert prefs.medium == 'vacuum'
    assert prefs.waveunit == 'nm'
    assert prefs.vdwformat == 'extended'
    assert prefs.isotopic_scaling == 'off'


@pytest.mark.django_db
def test_missing_key_keeps_the_model_default(persconf_dir, jane):
    """Four of the legacy files predate isotopic_scaling entirely."""
    write_htmldefs(persconf_dir, 'JaneDoe-HTMLdefs.cfg',
                   "energyunit\teV\nmedium\tvacuum\nwaveunit\tangstrom\n"
                   "vdwformat\tdefault\n")

    call_command('import_persconf', 'JaneDoe-HTMLdefs.cfg', verbosity=0)

    prefs = UserPreferences.objects.get(user=jane)
    assert prefs.isotopic_scaling == 'on'
    assert prefs.medium == 'vacuum'


@pytest.mark.django_db
def test_the_file_updates_the_existing_row(persconf_dir, jane):
    """get_preferences() has already made a row for anyone who opened the page."""
    jane.get_preferences()
    write_htmldefs(persconf_dir, 'JaneDoe-HTMLdefs.cfg',
                   DEFAULTS.replace('medium\tair', 'medium\tvacuum'))

    call_command('import_persconf', 'JaneDoe-HTMLdefs.cfg', verbosity=0)

    assert UserPreferences.objects.filter(user=jane).count() == 1
    assert UserPreferences.objects.get(user=jane).medium == 'vacuum'


# --- values this version does not know about must not reach the database ----

@pytest.mark.django_db
def test_unknown_key_is_ignored(persconf_dir, jane):
    write_htmldefs(persconf_dir, 'JaneDoe-HTMLdefs.cfg',
                   DEFAULTS + "spectrograph\tCRIRES\n")

    call_command('import_persconf', 'JaneDoe-HTMLdefs.cfg', verbosity=0)

    prefs = UserPreferences.objects.get(user=jane)
    assert not hasattr(prefs, 'spectrograph')
    assert prefs.energyunit == 'eV'


@pytest.mark.django_db
def test_value_outside_the_models_choices_is_ignored(persconf_dir, jane):
    write_htmldefs(persconf_dir, 'JaneDoe-HTMLdefs.cfg',
                   DEFAULTS.replace('waveunit\tangstrom', 'waveunit\tfurlongs'))

    call_command('import_persconf', 'JaneDoe-HTMLdefs.cfg', verbosity=0)

    assert UserPreferences.objects.get(user=jane).waveunit == 'angstrom'


@pytest.mark.django_db
def test_a_file_with_nothing_usable_fails(persconf_dir, jane):
    from django.core.management.base import CommandError
    write_htmldefs(persconf_dir, 'JaneDoe-HTMLdefs.cfg', "nonsense\n")

    with pytest.raises(CommandError):
        call_command('import_persconf', 'JaneDoe-HTMLdefs.cfg', verbosity=0)

    assert not UserPreferences.objects.filter(user=jane).exists()


# --- choices already made in this interface win -----------------------------

@pytest.mark.django_db
def test_a_choice_made_here_is_not_overwritten(persconf_dir, jane):
    prefs = jane.get_preferences()
    prefs.medium = 'vacuum'
    prefs.save()
    write_htmldefs(persconf_dir, 'JaneDoe-HTMLdefs.cfg', DEFAULTS)

    call_command('import_persconf', 'JaneDoe-HTMLdefs.cfg', verbosity=0)

    assert UserPreferences.objects.get(user=jane).medium == 'vacuum'


@pytest.mark.django_db
def test_force_overwrites_a_choice_made_here(persconf_dir, jane):
    prefs = jane.get_preferences()
    prefs.medium = 'vacuum'
    prefs.save()
    write_htmldefs(persconf_dir, 'JaneDoe-HTMLdefs.cfg', DEFAULTS)

    call_command('import_persconf', 'JaneDoe-HTMLdefs.cfg', '--force', verbosity=0)

    assert UserPreferences.objects.get(user=jane).medium == 'air'


@pytest.mark.django_db
def test_dry_run_writes_nothing(persconf_dir, jane):
    write_htmldefs(persconf_dir, 'JaneDoe-HTMLdefs.cfg',
                   DEFAULTS.replace('medium\tair', 'medium\tvacuum'))

    call_command('import_persconf', 'JaneDoe-HTMLdefs.cfg', '--dry-run', verbosity=0)

    assert not UserPreferences.objects.filter(user=jane).exists()


# --- the batch run ----------------------------------------------------------

@pytest.fixture
def default_config(db):
    config = Config.objects.create(name='Default', user=None, is_default=True)
    ll = Linelist.objects.create(path='/CVALD3/ATOMS/a', name='List A',
                                 element_min=1, element_max=99)
    ConfigLinelist.objects.create(config=config, linelist=ll, priority=10)
    return config


@pytest.mark.django_db
def test_all_imports_both_kinds(persconf_dir, default_config, jane, capsys):
    write_htmldefs(persconf_dir, 'JaneDoe-HTMLdefs.cfg',
                   DEFAULTS.replace('medium\tair', 'medium\tvacuum'))
    (persconf_dir / 'JaneDoe.cfg').write_text(
        "0.05,5000.,9,150.\n"
        "'/CVALD3/ATOMS/a', 10, 1, 99, 0, 9,3,3,3,3,3,3,3,3, 'List A'\n")

    call_command('import_persconf', '--all', verbosity=0)

    assert UserPreferences.objects.get(user=jane).medium == 'vacuum'
    assert Config.objects.filter(user=jane).exists()
    assert 'Imported: 2, Failed: 0' in capsys.readouterr().out, \
        'the -HTMLdefs file was still counted as a failed linelist config'


@pytest.mark.django_db
def test_prefs_only_leaves_the_linelist_config_alone(persconf_dir, default_config, jane):
    """The reason the flag exists: importing a linelist config replaces the one
    in the database, discarding whatever the user has edited here since."""
    from vald.persconfig import create_user_config
    mine = create_user_config(jane)
    mine.max_ionization = 5
    mine.save()

    write_htmldefs(persconf_dir, 'JaneDoe-HTMLdefs.cfg',
                   DEFAULTS.replace('medium\tair', 'medium\tvacuum'))
    (persconf_dir / 'JaneDoe.cfg').write_text(
        "0.05,5000.,9,150.\n"
        "'/CVALD3/ATOMS/a', 10, 1, 99, 0, 9,3,3,3,3,3,3,3,3, 'List A'\n")

    call_command('import_persconf', '--all', '--prefs-only', verbosity=0)

    assert UserPreferences.objects.get(user=jane).medium == 'vacuum'
    assert Config.objects.get(user=jane).max_ionization == 5


@pytest.mark.django_db
def test_a_file_for_a_departed_user_costs_only_that_file(persconf_dir, default_config, jane):
    write_htmldefs(persconf_dir, 'AaaGone-HTMLdefs.cfg', DEFAULTS)  # sorts first
    write_htmldefs(persconf_dir, 'JaneDoe-HTMLdefs.cfg',
                   DEFAULTS.replace('waveunit\tangstrom', 'waveunit\tnm'))

    call_command('import_persconf', '--all', verbosity=0)

    assert UserPreferences.objects.get(user=jane).waveunit == 'nm'
