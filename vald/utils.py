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
    """
    if not message or len(message) < 10:
        return False

    # Remove spaces for checking
    message_no_spaces = message.replace(" ", "")

    # Check for suspicious content
    suspicious_patterns = [
        "ahref=",
        "[url",
        "[/url",
        "http://",
        "https://",
    ]

    for pattern in suspicious_patterns:
        if pattern in message_no_spaces.lower():
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

    # Replace template variables
    for key, value in context.items():
        if value:
            # Replace $key with value
            content = re.sub(rf'\${key}\b', str(value), content)
        else:
            # If no value, remove the key (and optional trailing comma)
            content = re.sub(rf'\${key},?', '', content)

    # Remove any remaining unmatched $-strings
    content = re.sub(r'\$\w+', '', content)

    return content
