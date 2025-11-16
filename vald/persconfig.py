"""
Personal configuration management - handles linelist configuration
Similar to the PHP PersConfig class
"""
import re
from pathlib import Path
from .models import PersonalConfig, LineList


def read_persconfig_file(filepath):
    """
    Read a personal configuration file from disk.
    Returns (hidden_params, linelists) tuple.
    """
    if not Path(filepath).exists():
        return ([], [])

    with open(filepath, 'r') as f:
        lines = f.readlines()

    if not lines:
        return ([], [])

    # Extract hidden parameters from first line
    first_line = lines[0].strip()
    hidden_params = [p.strip() for p in first_line.split(',')][:4]

    # Parse linelists
    linelists = []
    for i in range(1, len(lines)):
        line = lines[i].strip()
        if not line:
            continue

        # Check if line is commented out (starts with ; but not ;;)
        commented = line.startswith(';') and not line.startswith(';;')
        if line.startswith(';;'):  # Skip fully commented lines
            continue

        # Remove comment marker
        if commented:
            line = line[1:]

        # Split into fields (expecting 15 fields)
        fields = [f.strip() for f in line.split(',', 14)]
        if len(fields) != 15:
            continue

        # Remove quotes from all fields except the last one
        for j in range(14):
            fields[j] = fields[j].strip("'\"")

        # Extract name from last field (format: 'name')
        name_match = re.search(r"'([^']*)'", fields[14])
        name = name_match.group(1) if name_match else fields[14]

        linelist = {
            'commented': commented,
            'params': fields,
            'name': name,
            'id': int(fields[1]) if fields[1].isdigit() else 0,
        }
        linelists.append(linelist)

    return (hidden_params, linelists)


def load_or_create_persconfig(email, default_config_path, user_config_path):
    """
    Load user's personal config or create from default.
    Returns PersonalConfig model instance.
    """
    # Try to get existing config from database
    try:
        persconf = PersonalConfig.objects.get(email=email)
        return persconf
    except PersonalConfig.DoesNotExist:
        pass

    # Read default config
    hidden_params, default_linelists = read_persconfig_file(default_config_path)

    # Create new config
    persconf = PersonalConfig.objects.create(
        email=email,
        hidden_param_0=hidden_params[0] if len(hidden_params) > 0 else '',
        hidden_param_1=hidden_params[1] if len(hidden_params) > 1 else '',
        hidden_param_2=hidden_params[2] if len(hidden_params) > 2 else '',
        hidden_param_3=hidden_params[3] if len(hidden_params) > 3 else '',
    )

    # Create linelists
    for ll_data in default_linelists:
        linelist = LineList.objects.create(
            personal_config=persconf,
            list_id=ll_data['id'],
            name=ll_data['name'],
            commented=ll_data['commented'],
        )

        # Set parameters
        for j in range(15):
            if j < len(ll_data['params']):
                linelist.set_param(j, ll_data['params'][j])

        linelist.save()

    return persconf


def compare_with_default(persconf, default_config_path):
    """
    Compare user config with default and mark modifications.
    Returns the updated persconf.
    """
    hidden_params, default_linelists = read_persconfig_file(default_config_path)

    # Update hidden params if they've changed in default
    if hidden_params:
        persconf.hidden_param_0 = hidden_params[0] if len(hidden_params) > 0 else ''
        persconf.hidden_param_1 = hidden_params[1] if len(hidden_params) > 1 else ''
        persconf.hidden_param_2 = hidden_params[2] if len(hidden_params) > 2 else ''
        persconf.hidden_param_3 = hidden_params[3] if len(hidden_params) > 3 else ''
        persconf.save()

    # Build lookup for default linelists
    default_lookup = {ll['id']: ll for ll in default_linelists}

    # Check each user linelist
    user_linelists = list(persconf.linelists.all())
    for linelist in user_linelists:
        default_ll = default_lookup.get(linelist.list_id)

        if not default_ll:
            # Linelist no longer exists in default - delete it
            linelist.delete()
            continue

        # Check if commented status differs
        if linelist.commented != default_ll['commented']:
            linelist.mod_comment = True

        # Check each parameter
        for j in range(1, 14):
            user_val = linelist.get_param(j)
            default_val = default_ll['params'][j] if j < len(default_ll['params']) else ''
            if user_val != default_val:
                linelist.set_mod_flag(j, True)

        linelist.save()

    # Add any new linelists from default
    user_ids = {ll.list_id for ll in user_linelists}
    for ll_data in default_linelists:
        if ll_data['id'] not in user_ids:
            linelist = LineList.objects.create(
                personal_config=persconf,
                list_id=ll_data['id'],
                name=ll_data['name'],
                commented=ll_data['commented'],
            )

            for j in range(15):
                if j < len(ll_data['params']):
                    linelist.set_param(j, ll_data['params'][j])

            linelist.save()

    return persconf


def restore_linelist_to_default(linelist, default_config_path):
    """Restore a single linelist to default values"""
    hidden_params, default_linelists = read_persconfig_file(default_config_path)

    # Find the default linelist
    default_ll = None
    for ll in default_linelists:
        if ll['id'] == linelist.list_id:
            default_ll = ll
            break

    if not default_ll:
        return linelist

    # Restore values
    linelist.commented = default_ll['commented']
    linelist.name = default_ll['name']

    for j in range(15):
        if j < len(default_ll['params']):
            linelist.set_param(j, default_ll['params'][j])
        linelist.set_mod_flag(j, False)

    linelist.mod_comment = False
    linelist.save()

    return linelist
