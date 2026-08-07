"""
Utility functions for VALD web interface
"""
import re
from pathlib import Path
from django.conf import settings


def validate_user_email(email):
    """
    Validate user email against client register file.
    Returns (is_valid, user_name) tuple.
    """
    email = email.lower().strip()

    # Check main register
    result = _check_register_file(settings.CLIENTS_REGISTER, email)
    if result:
        return (True, result)

    return (False, None)


def _check_register_file(filepath, email):
    """
    Check a single register file for an email address.
    Returns the user's full name if found, None otherwise.
    """
    if not Path(filepath).exists():
        return None

    try:
        with open(filepath, 'r') as f:
            current_name = None
            for line in f:
                line = line.strip()

                # Extract full user name from comments
                match = re.match(r'^#\$\s+(.*)$', line)
                if match:
                    current_name = match.group(1).strip()
                    continue

                # Skip other comments
                if line.startswith('#') or not line:
                    continue

                # Check if this line matches the email
                if line.lower() == email:
                    return current_name

    except Exception as e:
        print(f"Error reading register file {filepath}: {e}")

    return None


def spam_check(message):
    """
    Check if message appears to be spam.
    Returns True if message is OK, False if it's spam.

    A plain URL is NOT spam - astronomers legitimately link papers, DOIs and
    screenshots, and the old filter rejected any message containing http(s):// ,
    silently blocking real bug reports. Reject instead on markup that is almost
    always spam (HTML/BBCode links) or an implausible number of links.
    """
    if not message or len(message.strip()) < 10:
        return False

    lowered = message.lower()
    compact = lowered.replace(" ", "")

    # Link markup - genuine spam/injection signal, not something a scientist types
    markup_patterns = ["ahref=", "[url", "[/url", "</a>"]
    if any(p in compact for p in markup_patterns):
        return False

    # A handful of links is fine; a wall of them is not
    if lowered.count("http://") + lowered.count("https://") > 5:
        return False

    return True


def read_config_file(filepath):
    """
    Read a configuration file and return its contents as a list of lines.
    Performs basic directory traversal attack prevention.
    """
    try:
        # Resolve the full path
        full_path = Path(filepath).resolve()

        # Basic security: ensure the resolved path is within expected directories
        base_dirs = [
            settings.BASE_DIR,
            settings.DOCUMENTATION_DIR,
            settings.PERSCONFIG_DIR,
        ]

        # Check if path is under any allowed base directory
        is_safe = any(
            str(full_path).startswith(str(Path(base_dir).resolve()))
            for base_dir in base_dirs
        )

        if not is_safe:
            return [f"Error: Access denied to {filepath}\n"]

        if not full_path.exists():
            return []

        with open(full_path, 'r') as f:
            return f.readlines()

    except Exception as e:
        return [f"Error reading file: {e}\n"]


def get_request_template_path(reqtype):
    """Get the path to a request template file"""
    template_map = {
        'contact': 'contact-req.txt',
        'extractall': 'extractall-req.txt',
        'extractelement': 'extractelement-req.txt',
        'extractstellar': 'extractstellar-req.txt',
        'showline': 'showline-req.txt',
    }

    filename = template_map.get(reqtype)
    if not filename:
        return None

    return settings.BASE_DIR / 'requests' / filename


def render_request_template(reqtype, context):
    """
    Render a request template with the given context.
    Similar to the PHP EditLine function.
    """
    template_path = get_request_template_path(reqtype)
    if not template_path or not template_path.exists():
        return ""

    with open(template_path, 'r') as f:
        content = f.read()

    # Replace template variables.
    # The replacement is passed as a callable because re.sub interprets escapes
    # in a string replacement: a user message containing "\2" (a Fortran format
    # spec, a LaTeX macro, a Windows path) otherwise raised
    # "invalid group reference" and crashed the contact form.
    for key, value in context.items():
        pattern_key = re.escape(str(key))
        if value:
            # Replace $key with value
            content = re.sub(rf'\${pattern_key}\b', lambda m, v=value: str(v), content)
        else:
            # If no value, remove the key (and optional trailing comma)
            content = re.sub(rf'\${pattern_key},?', '', content)

    # Remove any remaining unmatched $-strings
    content = re.sub(r'\$\w+', '', content)

    return content
