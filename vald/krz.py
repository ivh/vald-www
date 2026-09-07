"""Validation of user-uploaded model atmospheres in krz format.

krz is what select5 calls the Kurucz-style condensed plane-parallel model it
reads; `select5 -h` says so, and every file in $VALD_HOME/MODELS/STELLAR is one.
RDMODL (SOURCE/SELECT/select5.f:1429) parses it, and its failure modes are the
reason this module exists: a malformed header does not produce a diagnostic, it
produces a raw gfortran backtrace after a queue slot has already been spent.
Deleting just "WLSTD= 5000." from a working model gets you

    At line 1460 of file select5.f
    Fortran runtime error: End of file

so every check below is here to turn one of those into a sentence naming the
line. The checks are deliberately no stricter than RDMODL: anything this accepts
must run, and anything it rejects must be something RDMODL cannot handle.
"""
import math
import re
from dataclasses import dataclass

# RDMODL locates each value with INDEX() on these literals, in this order, after
# upcasing the line. A missing one leaves I1/I2 at 0 and the substring bounds go
# backwards.
_HEADER_KEYS = ('T EFF=', 'GRAV', 'MODEL TYPE=', 'WLSTD=')

# SIZES.SEL: MOSIZE=100*MDSTEP with MDSTEP=5, and RDMODL rejects NRHOX*MDSTEP >
# MOSIZE. So 100 layers, which every MARCS or ATLAS9 model is comfortably under.
MIN_LAYERS = 3
MAX_LAYERS = 100

# IFOP in COMMONS.SEL. Read list-directed, so trailing text on the line is fine,
# but 20 values must be there or the read runs on into the abundances.
OPACITY_SWITCHES = 20

# 99 elements (MAXELM), then NRHOX as the hundredth value on the same read.
N_ELEMENTS = 99

MODEL_TYPE_SPHERICAL = 3

# MONAME is CHARACTER*120 in COMMONS.SEL, and it holds the whole path select5
# opens, not just the basename.
MONAME_MAX = 120

_SAFE_NAME = re.compile(r'[^A-Za-z0-9._+-]+')


class KrzError(ValueError):
    """An uploaded file RDMODL could not read, with a message for the user."""


@dataclass(frozen=True)
class KrzModel:
    """What the header of an accepted model says about it."""
    teff: float
    logg: float
    model_type: int
    wlstd: float
    layers: int
    title: str


def _numbers(line):
    """The whitespace/comma-separated finite numbers at the start of a line.

    Stops at the first token that is not one, which is what a list-directed
    Fortran READ of a fixed-length array does with the "- OPACITY SWITCHES"
    comment the grid files carry.

    "Finite" is doing real work here. float() accepts 'nan', 'inf' and
    '1e400', and gfortran's list-directed READ accepts the first two as well,
    so without this they travel all the way into the physics. A NaN is the
    worse of the two: every comparison against it is False, so it satisfies
    each range check below by being unorderable rather than by being in range.
    Treating them as non-numbers means the count checks reject the file with a
    message about the block they are in.
    """
    values = []
    for token in line.replace(',', ' ').split():
        try:
            value = float(token)
        except ValueError:
            break
        if not math.isfinite(value):
            break
        values.append(value)
    return values


def _header_value(line, key, nxt):
    """The number between `key` and the following keyword, as RDMODL reads it."""
    start = line.index(key) + len(key)
    end = line.index(nxt) if nxt else len(line)
    values = _numbers(line[start:end])
    if not values:
        raise KrzError(
            f'Line 2 of the model has no number after "{key}".'
        )
    return values[0]


def parse(text):
    """Validate krz `text`, returning what its header declares.

    Raises KrzError naming the line for anything RDMODL would choke on.
    """
    # Also checked in the form, which can say "that file is binary" because it
    # still has the upload in hand. Repeated here because this function is the
    # boundary the file content actually crosses: everything past it is written
    # to disk and opened by Fortran, and a NUL would truncate the line RDMODL
    # reads without either side reporting it.
    if '\x00' in text:
        raise KrzError('The model contains a NUL byte, so it is not text.')

    lines = text.splitlines()
    if len(lines) < 4:
        raise KrzError(
            'That file is too short to be a model atmosphere: a krz model '
            'needs a title line, a parameter line, the opacity switches, the '
            'abundances and one row per depth point.'
        )

    upper = lines[1].upper()
    missing = [key for key in _HEADER_KEYS if key not in upper]
    if missing:
        raise KrzError(
            'Line 2 of the model must carry '
            + ', '.join(f'"{key}"' for key in _HEADER_KEYS)
            + ' - '
            + ', '.join(f'"{key}"' for key in missing)
            + ' is missing. The line looks like '
            '"T EFF= 5750. GRAV= 4.5  MODEL TYPE= 0 WLSTD= 5000.".'
        )
    # In order, because the values are read as the text between one keyword and
    # the next rather than by name.
    positions = [upper.index(key) for key in _HEADER_KEYS]
    if positions != sorted(positions):
        raise KrzError(
            'Line 2 of the model has "'
            + '", "'.join(_HEADER_KEYS)
            + '" out of order. select5 reads each value as the text between '
            'one keyword and the next, so the order is part of the format.'
        )

    teff = _header_value(upper, 'T EFF=', 'GRAV')
    logg = _header_value(upper, 'GRAV=', 'MODEL TYPE=')
    model_type = int(_header_value(upper, 'MODEL TYPE=', 'WLSTD='))
    wlstd = _header_value(upper, 'WLSTD=', None)

    if model_type == MODEL_TYPE_SPHERICAL:
        raise KrzError(
            'That is a spherical model atmosphere (MODEL TYPE= 3). The line '
            'selection can only use plane-parallel models, so most of the '
            'MARCS grid has to be converted to plane-parallel geometry before '
            'it can be used here.'
        )
    if teff <= 0:
        raise KrzError(f'The model gives a non-physical T EFF= {teff:g}.')

    switches = _numbers(lines[2])
    if len(switches) < OPACITY_SWITCHES:
        raise KrzError(
            f'Line 3 of the model must hold {OPACITY_SWITCHES} opacity '
            f'switches; {len(switches)} were found. The grid models use '
            '" 1 1 1 1 1 1 1 1 1 1 1 1 1 0 1 0 0 0 0 0 - OPACITY SWITCHES".'
        )

    # The abundances and NRHOX are one list-directed read of 100 values, so they
    # may be spread over any number of lines. The grid uses ten lines of ten.
    values = []
    consumed = 3
    while consumed < len(lines) and len(values) < N_ELEMENTS + 1:
        values.extend(_numbers(lines[consumed]))
        consumed += 1
    if len(values) < N_ELEMENTS + 1:
        raise KrzError(
            f'The model ends in the middle of the abundances: '
            f'{N_ELEMENTS} element abundances followed by the number of '
            f'depth points are needed, and {len(values)} numbers were found.'
        )

    abundances = values[:N_ELEMENTS]
    layers = int(values[N_ELEMENTS])

    if not MIN_LAYERS <= layers <= MAX_LAYERS:
        raise KrzError(
            f'The model declares {layers} depth points. select5 accepts '
            f'{MIN_LAYERS} to {MAX_LAYERS}.'
        )

    _check_abundances(abundances)

    rows = 0
    for line in lines[consumed:]:
        if not line.strip():
            continue
        if len(_numbers(line)) < 5:
            raise KrzError(
                f'Depth point {rows + 1} of the model does not have five '
                'columns. Each row is RHOX, T, XNE, XNA, RHO.'
            )
        rows += 1
        if rows == layers:
            break
    if rows < layers:
        raise KrzError(
            f'The model declares {layers} depth points but only {rows} rows '
            'follow the abundances.'
        )

    return KrzModel(
        teff=teff,
        logg=logg,
        model_type=model_type,
        wlstd=wlstd,
        layers=layers,
        title=lines[0].strip(),
    )


def _check_abundances(abundances):
    """Reject the abundance conventions RDMODL misreads without complaint.

    select5 decides per value, by its sign, which scale it is on: the
    conversion at select5.f:1232 turns anything below zero into 10**value and
    leaves anything at or above zero as the number fraction it already is. So
    the check has to be per value too. Real files use both conventions for
    helium - a MARCS model writes 0.078 and the ATLAS9 grid writes -1.110,
    which are the same abundance - and rejecting either would refuse a model
    select5 reads correctly.

    What stays worth refusing is the A(X)=12 dex scale that MARCS and most
    abundance tables publish. Nothing in the file says which scale it is on, so
    7.50 for iron is read as a number fraction of 7.5 and the result is quietly
    wrong rather than an error. A number fraction cannot exceed 1, which is
    what separates the two.
    """
    if not 0.0 < abundances[0] <= 1.0:
        raise KrzError(
            f'The hydrogen abundance is {abundances[0]:g}. The first value is '
            'a number fraction, so it must be between 0 and 1 (0.92 for a '
            'solar mixture).'
        )

    # Checked before the sign test below, which would otherwise catch these
    # first and blame the abundance scale for what is really a missing value.
    # LOG10 is taken of these again when the abundances are written into the
    # result header (select5.f:2279), so an exact zero is -Infinity in the
    # output. The grid files floor at -20.00.
    zeros = [i + 1 for i, value in enumerate(abundances[1:], start=1)
             if value == 0.0]
    if zeros:
        raise KrzError(
            'The abundances of elements '
            + ', '.join(str(z) for z in zeros[:5])
            + ('' if len(zeros) <= 5 else ', ...')
            + ' are exactly zero, which becomes -Infinity in the result '
            'header. Use -20.00 for an element you have no abundance for, as '
            'the VALD grid models do.'
        )

    # A positive value is taken as a number fraction, so it cannot exceed 1.
    # Anything larger is the giveaway that the whole block is on the dex scale.
    too_large = [i + 1 for i, value in enumerate(abundances[1:], start=1)
                 if value > 1.0]
    if too_large:
        raise KrzError(
            'The abundances of elements '
            + ', '.join(str(z) for z in too_large[:5])
            + ('' if len(too_large) <= 5 else ', ...')
            + ' are greater than 1. select5 reads any value at or above zero '
            'as a number fraction, which cannot exceed 1, and a negative one '
            'as its log10 - it never sees the A(X)=12 dex scale as an error, '
            'it just gives wrong results. If the file is on that scale, '
            'subtract 12.04 from every value.'
        )


def safe_filename(name, budget):
    """`name` reduced to something safe to write and to quote in select.input.

    The uploaded name is kept rather than replaced by a fixed one because
    select5 prints it into the result header (select5.f:2264) and
    converters/parser.py carries that line into every exported format. Once the
    job directory has been swept it is the only record of which atmosphere
    produced a result, so "marcs_p5750_g45.krz" is worth the sanitising that
    "model.krz" would avoid.

    `budget` is how many characters are left for the filename once the job
    directory is accounted for, because select5 holds the whole path in a
    CHARACTER*120.
    """
    name = _SAFE_NAME.sub('_', name.rsplit('/', 1)[-1]).strip('._') or 'model'
    stem, _, ext = name.rpartition('.')
    if not stem or ext.lower() != 'krz':
        stem, ext = name, 'krz'
    # Truncate the stem, never the extension: the name has to stay recognisable
    # as a krz file in the result header.
    stem = stem[:max(1, budget - len(ext) - 1)]
    return f'{stem}.{ext}'
