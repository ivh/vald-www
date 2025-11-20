"""
Personal configuration management - handles linelist configuration files
File-based implementation (no database models)
"""
import re
from pathlib import Path


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


def write_persconfig_file(filepath, hidden_params, linelists):
    """
    Write a personal configuration file to disk.

    Args:
        filepath: Path to write the config file
        hidden_params: List of 4 hidden parameters (strings)
        linelists: List of linelist dicts with keys:
            - 'commented': bool
            - 'params': list of 15 parameter strings
            - 'name': linelist name
            - 'id': linelist ID (optional, for sorting)
    """
    with open(filepath, 'w') as f:
        # Write hidden parameters as first line
        hidden_line = ','.join(str(p) for p in hidden_params[:4])
        f.write(hidden_line + '\n')

        # Write each linelist
        for ll in linelists:
            # Build the line with 15 fields
            params = ll['params'][:15]  # Ensure we have exactly 15 fields

            # Pad with empty strings if needed
            while len(params) < 14:
                params.append('')

            # Build comma-separated fields (first 14 params)
            fields = ','.join(str(p) for p in params[:14])

            # Add name field in quotes as 15th field
            name = ll['name']
            line = f"{fields},'{name}'"

            # Add comment marker if commented
            if ll.get('commented', False):
                line = ';' + line

            f.write(line + '\n')


# Old DB-based functions removed - file-based implementation only
