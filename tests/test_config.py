"""
Tests for config generation from database.
"""
import pytest
import re
from pathlib import Path


@pytest.fixture
def default_cfg_path():
    """Path to the original default.cfg file."""
    return Path('/home/tom/VALD3/CONFIG/default.cfg')


@pytest.fixture
def imported_default_config(db):
    """Import default.cfg and return the Config object."""
    from django.core.management import call_command
    from vald.models import Config
    
    # Import the default config
    call_command('import_default_config', '/home/tom/VALD3/CONFIG/default.cfg', verbosity=0)
    
    return Config.get_default_config()


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
    
    # Verify user config was created
    user_config = Config.objects.filter(user=user).first()
    assert user_config is not None
    assert user_config.is_default is True
    assert user_config.configlinelist_set.count() == 377
