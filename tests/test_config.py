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
