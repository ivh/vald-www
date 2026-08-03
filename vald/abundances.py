"""Chemical composition parsing, shared by form validation and select.input.

The authoritative reference for the on-disk format is CheckAbund() in
backend/parserequest.c, which generated select.input for the legacy email
interface. select5 reads the abundance block as Fortran character literals, so
the quoting is not cosmetic - a bare "Fe: -4.50" is not equivalent to
"'Fe:-4.50',".
"""
import re

# Element name, or the metallicity shorthand parserequest.c also accepted
# ("MH:" / "m/h:" -> 'M/H:'). Value is a log10 abundance.
PAIR_RE = re.compile(
    r'^(M/H|MH|[A-Za-z]{1,2})\s*:\s*([+-]?\d+(?:\.\d+)?)$',
    re.IGNORECASE,
)

MAX_PAIRS = 200

# parserequest.c starts a new line once the current one exceeds 66 characters
LINE_WIDTH = 66


def parse(text):
    """Parse "El: value" pairs into a list of (name, value).

    Accepts commas between pairs and any number of lines, matching
    documentation/reqextstar.html. Raises ValueError carrying the offending
    token.
    """
    pairs = []
    for line in text.splitlines():
        for token in line.split(','):
            token = token.strip()
            if not token:
                continue
            match = PAIR_RE.match(token)
            if not match:
                raise ValueError(token)
            pairs.append((match.group(1), float(match.group(2))))
    return pairs


def canonical_name(name):
    """'fe' -> 'Fe', 'mh' -> 'M/H'. Legacy looked names up case-sensitively."""
    if name.upper() in ('MH', 'M/H'):
        return 'M/H'
    return name.capitalize()


def to_select_input(pairs):
    """Render pairs the way select5 expects them.

    Quoted, comma-terminated tokens with the value to two decimals, packed with
    no separator and wrapped past LINE_WIDTH - exactly what CheckAbund() emitted.

    The element field is padded to two characters ("'H :0.91',"), which is the
    form CheckAbund produced for input written as "H : 0.91" and the form shown
    in documentation/reqextstar.txt. The unpadded variant was also reachable in
    the legacy parser, so select5 accepts both, but this matches the documented
    output.
    """
    lines = []
    current = ''
    for name, value in pairs:
        if len(current) > LINE_WIDTH:
            lines.append(current)
            current = ''
        current += f"'{canonical_name(name):<2}:{value:.2f}',"
    if current:
        lines.append(current)
    return '\n'.join(lines)
