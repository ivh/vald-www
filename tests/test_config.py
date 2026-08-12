"""
Tests for config generation from database.
"""
import pytest
import re
from pathlib import Path


@pytest.fixture
def default_cfg_path():
    """Path to the original default.cfg, resolved from VALD_HOME.

    Was hardcoded to /home/tom/VALD3, which only exists on one developer's
    machine - so these tests failed everywhere else (e.g. the server at
    /home/vald/VALD3). Derive it from settings and skip if the data isn't there.
    """
    from django.conf import settings
    path = Path(settings.VALD_HOME) / 'CONFIG' / 'default.cfg'
    if not path.exists():
        pytest.skip(f'default.cfg not found at {path}')
    return path


@pytest.fixture
def imported_default_config(db, default_cfg_path):
    """Import default.cfg and return the Config object."""
    from django.core.management import call_command
    from vald.models import Config

    call_command('import_default_config', str(default_cfg_path), verbosity=0)

    config = Config.get_default_config()
    assert config is not None, f'import produced no default config from {default_cfg_path}'
    return config


def normalize_cfg_line(line):
    """
    Normalize a config line by collapsing whitespace.
    The Fortran parser is flexible with whitespace, so we normalize for comparison.
    """
    line = line.strip()
    # Collapse multiple spaces to single space
    line = re.sub(r'\s+', ' ', line)
    # Remove spaces around commas
    line = re.sub(r'\s*,\s*', ',', line)
    return line


def parse_cfg_lines(content):
    """
    Parse config content into normalized lines, skipping pure comments.
    """
    lines = content.strip().split('\n')
    result = []
    for line in lines:
        line = line.rstrip()
        # Skip pure comment lines (;; or ; without quotes = no data)
        if line.startswith(';;'):
            continue
        if line.startswith(';') and "'" not in line:
            continue
        if not line:
            continue
        result.append(normalize_cfg_line(line))
    return result


@pytest.mark.django_db
def test_generated_config_matches_original(imported_default_config, default_cfg_path, tmp_path):
    """
    Test that Config.generate_cfg_content() produces output equivalent to the original default.cfg.
    
    Whitespace differences are normalized since Fortran is flexible with spacing.
    """
    # Generate config content from database
    generated_content = imported_default_config.generate_cfg_content()
    
    # Read original file
    original_content = default_cfg_path.read_text()
    
    generated_lines = parse_cfg_lines(generated_content)
    original_lines = parse_cfg_lines(original_content)
    
    # Compare line counts
    assert len(generated_lines) == len(original_lines), \
        f"Line count mismatch: generated {len(generated_lines)}, original {len(original_lines)}"
    
    # Compare each line
    mismatches = []
    for i, (gen, orig) in enumerate(zip(generated_lines, original_lines), 1):
        if gen != orig:
            mismatches.append(f"Line {i}:\n  Generated: {gen}\n  Original:  {orig}")
    
    if mismatches:
        # Show first 5 mismatches
        mismatch_report = '\n'.join(mismatches[:5])
        if len(mismatches) > 5:
            mismatch_report += f"\n... and {len(mismatches) - 5} more mismatches"
        pytest.fail(f"Config content mismatch:\n{mismatch_report}")


@pytest.mark.django_db
def test_config_can_be_written_to_file(imported_default_config, tmp_path):
    """Test that the generated config can be written to a file."""
    config_path = tmp_path / 'test_config.cfg'
    
    content = imported_default_config.generate_cfg_content()
    config_path.write_text(content)
    
    assert config_path.exists()
    assert config_path.stat().st_size > 0
    
    # Read it back
    read_content = config_path.read_text()
    assert read_content == content


@pytest.mark.django_db
def test_import_persconf_creates_user_config(imported_default_config, tmp_path):
    """Test that import_persconf creates a user-specific config with differences."""
    from django.core.management import call_command
    from vald.models import User, Config, ConfigLinelist
    
    # Create a test user
    user = User.objects.create(name='Test User', password='dummy')
    
    # Create a test personal config file with modified ranks
    test_cfg = tmp_path / 'TestUser.cfg'
    
    # Copy content from default config but modify first linelist's ranks
    content = imported_default_config.generate_cfg_content()
    lines = content.split('\n')
    
    # Find first non-commented linelist and change a rank
    for i, line in enumerate(lines):
        if line.startswith("'") and not line.startswith(";"):
            # Change rank from 3 to 9 in the middle of the line
            lines[i] = line.replace(',3,3,3,', ',9,9,9,', 1)
            break
    
    test_cfg.write_text('\n'.join(lines))
    
    # Import the personal config
    call_command('import_persconf', str(test_cfg), verbosity=0)
    
    # Verify user config was created, with the same linelist count as the
    # default it was copied from (not a hardcoded number - the count depends on
    # which default.cfg this site ships).
    user_config = Config.objects.filter(user=user).first()
    assert user_config is not None
    assert user_config.is_default is True
    assert user_config.configlinelist_set.count() == \
        imported_default_config.configlinelist_set.count()


# --- R32: the system default was unconstrained ------------------------------
#
# UniqueConstraint(fields=['user', 'is_default']) does not constrain rows where
# user IS NULL, because NULLs never compare equal in a unique index. Several
# system defaults could coexist and get_default_config() chose between them by
# name ordering.

@pytest.mark.django_db
def test_only_one_system_default_config_is_allowed():
    from django.db import IntegrityError
    from vald.models import Config

    Config.objects.create(name='Default', user=None, is_default=True)
    with pytest.raises(IntegrityError):
        Config.objects.create(name='Another default', user=None, is_default=True)


@pytest.mark.django_db
def test_a_non_default_system_config_is_still_allowed():
    from vald.models import Config

    Config.objects.create(name='Default', user=None, is_default=True)
    Config.objects.create(name='Archived 2019', user=None, is_default=False)
    assert Config.objects.filter(user__isnull=True).count() == 2


@pytest.mark.django_db
def test_each_user_may_still_have_their_own_default(approved_user):
    from vald.models import Config, User

    Config.objects.create(name='Default', user=None, is_default=True)
    other = User.objects.create(name='Other', is_active=True)
    Config.objects.create(name='Mine', user=approved_user, is_default=True)
    Config.objects.create(name='Theirs', user=other, is_default=True)
    assert Config.objects.filter(is_default=True).count() == 3


# The migration's demotion step is not reachable from a test: the constraint it
# precedes makes the multi-default state it repairs impossible to construct
# through the ORM. Verified against a copy of the real database instead, with
# two extra system defaults inserted by raw SQL - one sorting before 'Default'
# and one after, so the name ordering was actually exercised. The migration kept
# the row get_default_config() had been returning all along, demoted the other
# two without deleting them, and the constraint then rejected a third.


# --- R47: retiring linelists that leave default.cfg -------------------------

@pytest.mark.django_db
def test_reimport_retires_linelists_no_longer_in_the_cfg(tmp_path):
    """Personal configs keep their own rows, so a retired linelist would
    otherwise stay in every existing snapshot's generated .cfg forever."""
    from django.core.management import call_command
    from vald.models import Linelist, Config

    header = "0.05,5000.,9,150.\n"
    two = (header
           + "'/CVALD3/ATOMS/keep', 10, 1, 99, 0, 3,3,3,3,3,3,3,3,3, 'Keeper'\n"
           + "'/CVALD3/ATOMS/drop', 20, 1, 99, 0, 3,3,3,3,3,3,3,3,3, 'Goes away'\n")
    one = (header
           + "'/CVALD3/ATOMS/keep', 10, 1, 99, 0, 3,3,3,3,3,3,3,3,3, 'Keeper'\n")

    first = tmp_path / 'default.cfg'
    first.write_text(two)
    call_command('import_default_config', str(first), verbosity=0)
    assert Linelist.objects.filter(path='/CVALD3/ATOMS/drop', is_active=True).exists()

    second = tmp_path / 'default2.cfg'
    second.write_text(one)
    call_command('import_default_config', str(second), verbosity=0)

    assert not Linelist.objects.filter(path='/CVALD3/ATOMS/drop', is_active=True).exists()
    assert Linelist.objects.filter(path='/CVALD3/ATOMS/keep', is_active=True).exists()

    content = Config.get_default_config().generate_cfg_content()
    assert 'ATOMS/drop' not in content


@pytest.mark.django_db
def test_reimport_reactivates_a_linelist_that_comes_back(tmp_path):
    from django.core.management import call_command
    from vald.models import Linelist

    header = "0.05,5000.,9,150.\n"
    entry = "'/CVALD3/ATOMS/onoff', 10, 1, 99, 0, 3,3,3,3,3,3,3,3,3, 'Comes and goes'\n"

    with_it = tmp_path / 'a.cfg'
    with_it.write_text(header + entry)
    without = tmp_path / 'b.cfg'
    without.write_text(header)

    call_command('import_default_config', str(with_it), verbosity=0)
    call_command('import_default_config', str(without), verbosity=0)
    assert not Linelist.objects.get(path='/CVALD3/ATOMS/onoff').is_active

    call_command('import_default_config', str(with_it), verbosity=0)
    assert Linelist.objects.get(path='/CVALD3/ATOMS/onoff').is_active


# --- R50: a duplicate linelist path must not abort the import ---------------

@pytest.mark.django_db
def test_duplicate_path_in_default_cfg_does_not_crash(tmp_path):
    """ConfigLinelist is unique on (config, linelist); a repeated path used to
    raise IntegrityError partway through the rebuild."""
    from django.core.management import call_command
    from vald.models import Config

    cfg = tmp_path / 'default.cfg'
    cfg.write_text(
        "0.05,5000.,9,150.\n"
        "'/CVALD3/ATOMS/a', 10, 1, 99, 0, 3,3,3,3,3,3,3,3,3, 'List A'\n"
        "'/CVALD3/ATOMS/a', 20, 1, 99, 0, 9,9,9,9,9,9,9,9,9, 'List A again'\n"
        "'/CVALD3/ATOMS/b', 30, 1, 99, 0, 3,3,3,3,3,3,3,3,3, 'List B'\n")
    call_command('import_default_config', str(cfg), verbosity=0)

    config = Config.get_default_config()
    assert config.configlinelist_set.count() == 2
    first = config.configlinelist_set.get(linelist__path='/CVALD3/ATOMS/a')
    assert first.priority == 10, 'the first occurrence should win'


@pytest.mark.django_db
def test_duplicate_path_in_persconf_does_not_crash(tmp_path, settings):
    from django.core.management import call_command
    from vald.models import User, Config

    settings.PERSCONFIG_DIR = tmp_path
    default = tmp_path / 'default.cfg'
    default.write_text(
        "0.05,5000.,9,150.\n"
        "'/CVALD3/ATOMS/a', 10, 1, 99, 0, 3,3,3,3,3,3,3,3,3, 'List A'\n"
        "'/CVALD3/ATOMS/b', 20, 1, 99, 0, 3,3,3,3,3,3,3,3,3, 'List B'\n")
    call_command('import_default_config', str(default), verbosity=0)
    User.objects.create(name='Jane Doe', is_active=True)

    (tmp_path / 'JaneDoe.cfg').write_text(
        "0.05,5000.,9,150.\n"
        "'/CVALD3/ATOMS/a', 10, 1, 99, 0, 9,3,3,3,3,3,3,3,3, 'List A'\n"
        "'/CVALD3/ATOMS/a', 15, 1, 99, 0, 1,1,1,1,1,1,1,1,1, 'List A dup'\n"
        "'/CVALD3/ATOMS/b', 20, 1, 99, 0, 3,3,3,3,3,3,3,3,3, 'List B'\n")
    call_command('import_persconf', 'JaneDoe.cfg', verbosity=0)

    mine = Config.objects.get(user__name='Jane Doe')
    assert mine.configlinelist_set.count() == 2
    assert mine.configlinelist_set.get(linelist__path='/CVALD3/ATOMS/a').rank_wl == 9


@pytest.mark.django_db
def test_one_bad_file_does_not_abort_the_whole_run(tmp_path, settings, capsys):
    """--all used to catch only CommandError, so anything else killed the run
    and left the remaining files unprocessed."""
    from django.core.management import call_command
    from vald.models import User, Config

    settings.PERSCONFIG_DIR = tmp_path
    default = tmp_path / 'default.cfg'
    default.write_text(
        "0.05,5000.,9,150.\n"
        "'/CVALD3/ATOMS/a', 10, 1, 99, 0, 3,3,3,3,3,3,3,3,3, 'List A'\n")
    call_command('import_default_config', str(default), verbosity=0)
    for name in ['Aaa Aaa', 'Zzz Zzz']:
        User.objects.create(name=name, is_active=True)

    # sorts first, and is unparseable
    (tmp_path / 'AaaAaa.cfg').write_text('not a config at all\n')
    (tmp_path / 'ZzzZzz.cfg').write_text(
        "0.05,5000.,9,150.\n"
        "'/CVALD3/ATOMS/a', 10, 1, 99, 0, 7,3,3,3,3,3,3,3,3, 'List A'\n")

    call_command('import_persconf', '--all', verbosity=0)
    assert Config.objects.filter(user__name='Zzz Zzz').exists(), \
        'a later file was skipped because an earlier one failed'


# --- alternative system configs (VALD3_all.cfg and friends) ------------------
#
# Four .cfg files ship beside each other in $VALD_HOME/CONFIG, differing only in
# which entries are commented out - observed vs predicted lines, atoms vs atoms
# and molecules. They are imported as system configs alongside default.cfg and
# offered as a menu on the request forms.

HEADER = "0.05,5000.,9,150.\n"
KEEP = "'/CVALD3/ATOMS/keep', 10, 1, 99, 0, 3,3,3,3,3,3,3,3,3, 'Keeper'\n"
EXTRA = "'/CVALD3/ATOMS/pred', 20, 1, 99, 0, 3,3,3,3,3,3,3,3,3, 'Predicted'\n"


@pytest.fixture
def default_and_variant(tmp_path, db):
    """A system default, plus one variant carrying an extra linelist."""
    from django.core.management import call_command

    (tmp_path / 'default.cfg').write_text(HEADER + KEEP)
    (tmp_path / 'variant.cfg').write_text(HEADER + KEEP + EXTRA)
    call_command('import_default_config', str(tmp_path / 'default.cfg'), verbosity=0)
    call_command('import_default_config', str(tmp_path / 'variant.cfg'),
                 '--slug', 'vald3_all', '--config-name', 'All (with predicted)',
                 verbosity=0)
    return tmp_path


@pytest.mark.django_db
def test_a_variant_is_a_second_system_config(default_and_variant):
    from vald.models import Config
    from vald.persconfig import get_alternative_configs, get_default_config

    assert get_default_config().slug == 'default'
    assert [c.slug for c in get_alternative_configs()] == ['vald3_all']
    variant = Config.objects.get(slug='vald3_all')
    assert variant.user is None and variant.is_default is False
    assert 'ATOMS/pred' in variant.generate_cfg_content()
    assert 'ATOMS/pred' not in get_default_config().generate_cfg_content()


@pytest.mark.django_db
def test_reimporting_the_default_keeps_a_variants_own_linelists(default_and_variant):
    """Retirement judged one file at a time had each import deactivate whatever
    the previous one had added - VALD3_all.cfg carries two predicted-line lists
    default.cfg does not."""
    from django.core.management import call_command
    from vald.models import Config, Linelist

    call_command('import_default_config',
                 str(default_and_variant / 'default.cfg'), verbosity=0)

    assert Linelist.objects.get(path='/CVALD3/ATOMS/pred').is_active
    assert 'ATOMS/pred' in Config.objects.get(slug='vald3_all').generate_cfg_content()


@pytest.mark.django_db
def test_reimporting_a_variant_updates_it_in_place(default_and_variant):
    from django.core.management import call_command
    from vald.models import Config

    (default_and_variant / 'variant.cfg').write_text(HEADER + KEEP)
    call_command('import_default_config', str(default_and_variant / 'variant.cfg'),
                 '--slug', 'vald3_all', verbosity=0)

    assert Config.objects.filter(user__isnull=True).count() == 2
    assert 'ATOMS/pred' not in Config.objects.get(slug='vald3_all').generate_cfg_content()


@pytest.mark.django_db
@pytest.mark.parametrize('slug', ['default', 'personal'])
def test_reserved_slugs_are_refused(tmp_path, slug):
    """Both are what a stored request already means by something else."""
    from django.core.management import call_command
    from django.core.management.base import CommandError

    (tmp_path / 'v.cfg').write_text(HEADER + KEEP)
    with pytest.raises(CommandError):
        call_command('import_default_config', str(tmp_path / 'v.cfg'),
                     '--slug', slug, verbosity=0)


@pytest.mark.django_db
def test_the_variant_is_offered_on_the_request_forms(default_and_variant,
                                                     logged_in_client):
    body = logged_in_client.get('/extractall/').content.decode()
    assert '<option value="vald3_all">All (with predicted)</option>' in body
    assert 'value="default"' in body


@pytest.mark.django_db
def test_a_request_runs_with_the_config_it_named(default_and_variant, approved_user,
                                                 tmp_path):
    """The point of the whole exercise: pconf must pick the linelists."""
    from vald.job_runner import get_config_path_for_request

    job_dir = tmp_path / 'job'
    job_dir.mkdir()

    written = Path(get_config_path_for_request(approved_user, job_dir, 'vald3_all'))
    assert 'ATOMS/pred' in written.read_text()

    written = Path(get_config_path_for_request(approved_user, job_dir, 'default'))
    assert 'ATOMS/pred' not in written.read_text()


@pytest.mark.django_db
def test_a_vanished_config_fails_the_job_rather_than_substituting_the_default(
        default_and_variant, approved_user, tmp_path):
    """Running the default while the stored request names something else is the
    silent mismatch this whole area exists to prevent."""
    from vald.job_runner import get_config_path_for_request
    from vald.models import Config

    Config.objects.filter(slug='vald3_all').delete()
    job_dir = tmp_path / 'job'
    job_dir.mkdir()

    with pytest.raises(ValueError, match='vald3_all'):
        get_config_path_for_request(approved_user, job_dir, 'vald3_all')


@pytest.mark.django_db
def test_a_reimport_keeps_the_name_the_menu_shows(default_and_variant):
    """--config-name is the label in the request forms; picking up new linelists
    should not silently retitle it."""
    from django.core.management import call_command
    from vald.models import Config

    call_command('import_default_config', str(default_and_variant / 'variant.cfg'),
                 '--slug', 'vald3_all', verbosity=0)
    assert Config.objects.get(slug='vald3_all').name == 'All (with predicted)'

    call_command('import_default_config', str(default_and_variant / 'variant.cfg'),
                 '--slug', 'vald3_all', '--config-name', 'Renamed', verbosity=0)
    assert Config.objects.get(slug='vald3_all').name == 'Renamed'


# --- Config.description reaches the request forms ---------------------------
#
# It was a field nothing read: declared, admin-editable, and empty on every one
# of the 679 rows. The dropdown is where a description is actually useful.

DESCRIBED = 'Observed and predicted lines, atoms and molecules'


@pytest.fixture
def described_configs(db):
    from vald.models import Config
    default = Config.objects.create(name='Default', slug='default', user=None,
                                    is_default=True,
                                    description='Recommended for most work')
    Config.objects.create(name='All lines', slug='vald3_all', user=None,
                          is_default=False, description=DESCRIBED)
    return default


@pytest.mark.django_db
def test_the_menu_carries_each_config_description(logged_in_client, described_configs):
    body = logged_in_client.get('/extractall/').content.decode()

    assert f'data-description="{DESCRIBED}"' in body
    assert f'title="{DESCRIBED}"' in body, 'no hover text on the option'
    assert 'Recommended for most work' in body


@pytest.mark.django_db
def test_a_config_without_a_description_gets_no_empty_tooltip(logged_in_client):
    """An empty title renders an odd blank tooltip, so those are left off."""
    from vald.models import Config
    Config.objects.create(name='Default', slug='default', user=None,
                          is_default=True, description='')

    body = logged_in_client.get('/extractall/').content.decode()

    assert 'title=""' not in body and 'data-description=""' not in body


@pytest.mark.django_db
@pytest.mark.parametrize('page', ['/extractall/', '/extractelement/',
                                  '/extractstellar/', '/showline/'])
def test_every_form_shows_the_description_under_the_menu(logged_in_client,
                                                         described_configs, page):
    """The tooltip is desktop-only and silent to screen readers, so the same text
    is printed under the menu on every form that offers the choice."""
    body = logged_in_client.get(page).content.decode()

    assert 'id="pconf_description"' in body
    assert 'data-description' in body


@pytest.mark.django_db
def test_the_importer_can_set_and_keep_a_description(tmp_path):
    """Prose someone wrote by hand must survive picking up new linelists."""
    from django.core.management import call_command
    from vald.models import Config

    cfg = tmp_path / 'all.cfg'
    cfg.write_text("0.05,5000.,9,150.\n"
                   "'/CVALD3/ATOMS/a', 10, 1, 99, 0, 3,3,3,3,3,3,3,3,3, 'A'\n")
    call_command('import_default_config', str(cfg), '--slug', 'vald3_all',
                 '--description', DESCRIBED, verbosity=0)
    assert Config.objects.get(slug='vald3_all').description == DESCRIBED

    # re-import without the flag: the description stays
    call_command('import_default_config', str(cfg), '--slug', 'vald3_all',
                 verbosity=0)
    assert Config.objects.get(slug='vald3_all').description == DESCRIBED

    # ...and an explicit empty string clears it
    call_command('import_default_config', str(cfg), '--slug', 'vald3_all',
                 '--description', '', verbosity=0)
    assert Config.objects.get(slug='vald3_all').description == ''
