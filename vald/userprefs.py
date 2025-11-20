"""
User preferences management - handles HTMLdefs configuration files
File-based implementation (no database models)
"""
from pathlib import Path
from django.conf import settings


# Default preferences
DEFAULT_PREFERENCES = {
    'energyunit': 'eV',
    'medium': 'air',
    'waveunit': 'angstrom',
    'vdwformat': 'default',
    'isotopic_scaling': 'on',
}


def read_userprefs_file(filepath):
    """
    Read a user preferences file from disk.
    Returns dict with preference keys and values.
    """
    if not Path(filepath).exists():
        return DEFAULT_PREFERENCES.copy()

    prefs = DEFAULT_PREFERENCES.copy()

    try:
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                # Split on whitespace (tab or spaces)
                parts = line.split(None, 1)
                if len(parts) == 2:
                    key, value = parts
                    if key in prefs:
                        prefs[key] = value.strip()
    except Exception:
        # If file is corrupted, return defaults
        return DEFAULT_PREFERENCES.copy()

    return prefs


def write_userprefs_file(filepath, prefs):
    """
    Write user preferences to disk.

    Args:
        filepath: Path to write the preferences file
        prefs: Dict with keys: energyunit, medium, waveunit, vdwformat, isotopic_scaling
    """
    with open(filepath, 'w') as f:
        # Write in consistent order
        for key in ['energyunit', 'medium', 'waveunit', 'vdwformat', 'isotopic_scaling']:
            value = prefs.get(key, DEFAULT_PREFERENCES[key])
            # Use tab separator to match original format
            f.write(f"{key}\t{value}\n")


def get_userprefs_path(client_name, is_local=False):
    """
    Get the file path for a user's preferences file.

    Args:
        client_name: Alphanumeric client name (e.g., 'ThomasMarquart')
        is_local: True if this is a local user

    Returns:
        Path object for the user's HTMLdefs file
    """
    suffix = '-HTMLdefs.cfg_local' if is_local else '-HTMLdefs.cfg'
    return settings.PERSCONFIG_DIR / f"{client_name}{suffix}"


def load_user_preferences(client_name, is_local=False):
    """
    Load user preferences from file, or return defaults if file doesn't exist.

    Args:
        client_name: Alphanumeric client name
        is_local: True if this is a local user

    Returns:
        Dict with preference keys and values
    """
    filepath = get_userprefs_path(client_name, is_local)
    return read_userprefs_file(filepath)


def save_user_preferences(client_name, prefs, is_local=False):
    """
    Save user preferences to file.

    Args:
        client_name: Alphanumeric client name
        prefs: Dict with preference keys and values
        is_local: True if this is a local user
    """
    filepath = get_userprefs_path(client_name, is_local)
    write_userprefs_file(filepath, prefs)
