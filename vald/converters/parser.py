"""Parse the VALD *long* ASCII format back into typed rows.

Only the long format is handled. It is the superset - term designations, both
level energies, all three Lande factors, nine references per transition - so a
converter that reads it can produce anything, and there is exactly one parser to
keep in step with the Fortran instead of four.

The coupling to presformat5.f is real and cannot be designed away, so it is kept
as narrow as possible:

  * Columns come from the *header line*, matched against the vocabulary of
    labels presformat5.f can emit (see its WRITE block, ~lines 80-300). An
    unrecognised label raises rather than guessing, because a silently
    mis-labelled column is the one failure mode worth being loud about.
  * Data lines are comma-separated, not fixed-width, so column *widths* can
    drift in the Fortran without breaking anything here.

Only the term and reference lines are read positionally, because they have no
delimiters; the offsets there come from FORMAT 201 and 202 in presformat5.f.
"""

from dataclasses import dataclass, field
from typing import Any, Optional
import re


class ParseError(ValueError):
    """The file is not long-format VALD output we recognise."""


@dataclass(frozen=True)
class Column:
    """One output column: what to call it and what it means."""
    name: str
    dtype: str            # 'str', 'int' or 'float'
    unit: str = ''        # VOUnits, '' when dimensionless
    ucd: str = ''         # IVOA UCD1+, for VOTable/ECSV consumers
    description: str = ''


# Header labels presformat5.f/select4.f can write, longest first so that
# multi-word labels win over their prefixes.
_SPECIES_LABELS = ('Elm Ion', 'Spec Ion', 'Species')

_WAVELENGTH_LABELS = {
    'WL_air(A)': ('Angstrom', 'air'),
    'WL_vac(A)': ('Angstrom', 'vacuum'),
    'WL_air(nm)': ('nm', 'air'),
    'WL_vac(nm)': ('nm', 'vacuum'),
    'WL_air(cm^-1)': ('1/cm', 'air'),
    'WL_vac(cm^-1)': ('1/cm', 'vacuum'),
}

# Below 2000 A the air and vacuum scales coincide, and presformat5.f marks the
# column with a trailing '+' rather than switching the label to WL_vac.
_AIR_VAC_MARKER = '+'

_ENERGY_UNITS = {'eV': 'eV', 'cm^-1': '1/cm'}


def _energy_column(label: str, which: str) -> Optional[Column]:
    m = re.fullmatch(r'E_(low|up)\((eV|cm\^-1)\)', label)
    if not m or m.group(1) != which:
        return None
    unit = _ENERGY_UNITS[m.group(2)]
    role = 'lower' if which == 'low' else 'upper'
    return Column(f'e_{which}', 'float', unit, 'phys.energy',
                  f'Energy of the {role} level')


# The fixed tail of every long-format header, in order. Each entry is
# (header tokens, Column).
_TAIL_COLUMNS = [
    (('J', 'lo'), Column('j_low', 'float', '', 'phys.atmol.qn',
                         'Total angular momentum of the lower level')),
    (('J', 'up'), Column('j_up', 'float', '', 'phys.atmol.qn',
                         'Total angular momentum of the upper level')),
    (('lower',), Column('lande_lower', 'float', '', '',
                        'Lande factor of the lower level')),
    (('upper',), Column('lande_upper', 'float', '', '',
                        'Lande factor of the upper level')),
    (('mean',), Column('lande_mean', 'float', '', '',
                       'Effective Lande factor of the transition')),
    (('Rad.',), Column('rad_damping', 'float', '', 'phys.atmol.wl.broad',
                       'log of the radiative damping constant')),
    (('Stark',), Column('stark_damping', 'float', '', 'phys.atmol.wl.broad',
                        'log of the Stark damping constant')),
    (('Waals',), Column('waals_damping', 'float', '', 'phys.atmol.wl.broad',
                        'log of the van der Waals damping constant')),
    (('depth',), Column('central_depth', 'float', '', '',
                        'Central depth of the line in the model spectrum')),
]

# What each of the nine reference slots refers to. Named after the comments in
# preselect5.f90 (see the iref(N,...) assignments around lines 1230-1400), which
# is the code that fills them; presformat5.f only passes them through.
REFERENCE_SLOTS = (
    'wavelength', 'log_gf', 'e_low', 'e_up',
    'lande', 'rad_damping', 'stark_damping', 'waals_damping', 'term',
)


@dataclass
class LineList:
    """A parsed long-format extraction: columns, rows and everything around them."""
    columns: list[Column]
    rows: list[list[Any]]
    references: dict[int, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column(self, name: str) -> Column:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(name)


# select4.f leads a stellar extraction with its own summary line, e.g.
#   " 5026.61700, 5076.61700, 31, 3097, 3.6 Wavelength region, lines selected,
#    lines processed, Vmicro"
_STELLAR_PREAMBLE = re.compile(
    r'^\s*([-\d.eE+]+),\s*([-\d.eE+]+),\s*(\d+),\s*(\d+),\s*([-\d.eE+]+)\s+'
    r'Wavelength region')


def _parse_stellar_preamble(line: str) -> dict[str, Any]:
    m = _STELLAR_PREAMBLE.match(line)
    if not m:
        return {}
    return {
        'wavelength_start': float(m.group(1)),
        'wavelength_end': float(m.group(2)),
        'lines_selected': int(m.group(3)),
        'lines_processed': int(m.group(4)),
        'microturbulence_km_s': float(m.group(5)),
    }


def _split_species(species: str) -> tuple[str, Optional[int]]:
    """'Ca 1' -> ('Ca', 1). Molecules ('TiO 1') behave the same way."""
    parts = species.rsplit(None, 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0], int(parts[1])
    return species, None


def _parse_header(header: str) -> tuple[list[Column], dict[str, Any]]:
    """Turn the column header into Column specs, or raise ParseError."""
    tokens = header.split()
    meta: dict[str, Any] = {}
    columns: list[Column] = []

    def take(n: int) -> str:
        return ' '.join(tokens[:n])

    for label in _SPECIES_LABELS:
        n = len(label.split())
        if take(n) == label:
            del tokens[:n]
            break
    else:
        raise ParseError(f'unrecognised species column: {header[:40]!r}')

    columns.append(Column('species', 'str', '', 'phys.atmol.element',
                          'Element or molecule and ionisation stage'))
    columns.append(Column('element', 'str', '', 'phys.atmol.element',
                          'Element or molecule'))
    columns.append(Column('ion', 'int', '', 'phys.atmol.ionStage',
                          'Ionisation stage, 1 = neutral'))

    if not tokens:
        raise ParseError('header ends after the species column')
    wl_label = tokens.pop(0)
    air_equals_vacuum = wl_label.endswith(_AIR_VAC_MARKER)
    if air_equals_vacuum:
        wl_label = wl_label[:-1]
    if wl_label not in _WAVELENGTH_LABELS:
        raise ParseError(f'unrecognised wavelength column: {wl_label!r}')
    unit, medium = _WAVELENGTH_LABELS[wl_label]
    meta['wavelength_medium'] = medium
    meta['wavelength_unit'] = unit
    meta['air_equals_vacuum'] = air_equals_vacuum
    columns.append(Column('wavelength', 'float', unit, 'em.wl',
                          f'Transition wavelength ({medium})'))

    if take(2) != 'log gf*':
        raise ParseError(f'expected "log gf*", got {take(2)!r}')
    del tokens[:2]
    columns.append(Column('log_gf', 'float', '', 'phys.atmol.wOscStrength',
                          'log of the oscillator strength times statistical weight'))

    for which in ('low', 'up'):
        if not tokens:
            raise ParseError(f'header ends before E_{which}')
        col = _energy_column(tokens[0], which)
        if col is None:
            raise ParseError(f'unrecognised energy column: {tokens[0]!r}')
        tokens.pop(0)
        meta['energy_unit'] = col.unit
        columns.append(col)
        # J lo follows E_low, J up follows E_up.
        want = ('J', 'lo') if which == 'low' else ('J', 'up')
        if tuple(tokens[:2]) != want:
            raise ParseError(f'expected {" ".join(want)!r} after E_{which}')
        del tokens[:2]
        columns.append(dict(_TAIL_COLUMNS)[want])

    for want, col in _TAIL_COLUMNS:
        if want in (('J', 'lo'), ('J', 'up')):
            continue
        if tuple(tokens[:len(want)]) == want:
            del tokens[:len(want)]
            columns.append(col)
        elif col.name != 'central_depth':
            raise ParseError(
                f'expected {" ".join(want)!r}, got {" ".join(tokens[:2])!r}')

    if tokens:
        raise ParseError(f'unexpected trailing header columns: {tokens!r}')

    return columns, meta


def _split_data_line(line: str) -> list[str]:
    """Split a comma-separated record, honouring the quoted species field."""
    values, buf, in_quote = [], [], False
    for ch in line:
        if ch == "'":
            in_quote = not in_quote
        elif ch == ',' and not in_quote:
            values.append(''.join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = ''.join(buf).strip()
    if tail:
        values.append(tail)
    return values


def _unquote(line: str) -> str:
    line = line.rstrip('\n')
    if line.startswith("'") and line.endswith("'") and len(line) >= 2:
        return line[1:-1]
    return line


# FORMAT 201 in presformat5.f: '''',A4,A86,''''
_TERM_FLAG_WIDTH = 4


def _parse_term(line: str) -> tuple[str, str]:
    body = _unquote(line)
    return body[:_TERM_FLAG_WIDTH].strip(), body[_TERM_FLAG_WIDTH:].strip()


# FORMAT 202 in presformat5.f: '''',A1,A10,A16,9(I4,1X,A),1X,A14,''''
_ACC_FLAG_WIDTH, _ACCURACY_WIDTH, _COMMENT_WIDTH = 1, 10, 16
_REF_NUMBER_WIDTH = 4


def _parse_reference_line(line: str) -> dict[str, Any]:
    body = _unquote(line)
    pos = 0
    out: dict[str, Any] = {}
    out['accuracy_flag'] = body[pos:pos + _ACC_FLAG_WIDTH].strip()
    pos += _ACC_FLAG_WIDTH
    out['accuracy'] = body[pos:pos + _ACCURACY_WIDTH].strip()
    pos += _ACCURACY_WIDTH
    out['comment'] = body[pos:pos + _COMMENT_WIDTH].strip()
    pos += _COMMENT_WIDTH

    for slot in REFERENCE_SLOTS:
        raw = body[pos:pos + _REF_NUMBER_WIDTH]
        pos += _REF_NUMBER_WIDTH
        try:
            out[f'{slot}_ref'] = int(raw)
        except ValueError:
            # I4 overflows to '****' past 9999 references.
            out[f'{slot}_ref'] = None
        pos += 1  # the 1X separator
        end = body.find(' ', pos)
        if end == -1:
            end = len(body)
        # The bibkey carries a 'wl:'/'gf:' prefix on the slots preselect5.f90
        # marks explicitly; strip it, the slot already says which field it is.
        key = body[pos:end]
        out[f'{slot}_bibkey'] = key.split(':', 1)[1] if ':' in key else key
        # The next I4 begins at the blank that terminated this key: the A edit
        # descriptor writes the trimmed key with no separator of its own.
        pos = end

    out['species_long'] = body[pos:].strip()
    return out


REFERENCE_COLUMNS = (
    [Column('accuracy_flag', 'str', '', '', 'Accuracy class flag'),
     Column('accuracy', 'str', '', '', 'Quoted accuracy of the transition'),
     Column('comment', 'str', '', '', 'Source comment from the line list'),
     Column('species_long', 'str', '', 'phys.atmol.element',
            'Species as named by the source line list')]
    + [Column(f'{slot}_ref', 'int', '', 'meta.ref',
              f'Reference number for {slot.replace("_", " ")}')
       for slot in REFERENCE_SLOTS]
    + [Column(f'{slot}_bibkey', 'str', '', 'meta.bib',
              f'BibTeX key for {slot.replace("_", " ")}')
       for slot in REFERENCE_SLOTS]
)

TERM_COLUMNS = [
    Column('lower_coupling', 'str', '', '', 'Coupling scheme of the lower level'),
    Column('lower_term', 'str', '', 'phys.atmol.term',
           'Configuration and term of the lower level'),
    Column('upper_coupling', 'str', '', '', 'Coupling scheme of the upper level'),
    Column('upper_term', 'str', '', 'phys.atmol.term',
           'Configuration and term of the upper level'),
]


# A record's first field is the species, e.g. 'Ca 1', 'TiO 1', '(46)Ti 1'.
# Matching it is what keeps the stellar model header - '24000G30.KRZ', - and the
# abundance block that follows the transitions from being read as data.
_SPECIES_FIELD = re.compile(r"^\((\d+)\)?[A-Za-z][A-Za-z0-9]*\s+\d+$|^[A-Za-z][A-Za-z0-9]*\s+\d+$")


def _is_record_start(line: str) -> bool:
    if not line.startswith("'"):
        return False
    end = line.find("'", 1)
    return end > 1 and bool(_SPECIES_FIELD.match(line[1:end]))


def _coerce(value: str, dtype: str) -> Any:
    if value == '':
        return None
    if dtype == 'float':
        try:
            return float(value)
        except ValueError:
            return None
    if dtype == 'int':
        try:
            return int(value)
        except ValueError:
            return None
    return value


def parse(text: str) -> LineList:
    """Parse long-format VALD output. Raises ParseError on anything else."""
    lines = text.splitlines()

    header_index = None
    for i, line in enumerate(lines[:8]):
        if any(line.startswith(label) for label in _SPECIES_LABELS):
            header_index = i
            break
    if header_index is None:
        raise ParseError('no column header found in the first eight lines')

    data_columns, meta = _parse_header(lines[header_index])
    if not any(c.name == 'lande_mean' for c in data_columns):
        raise ParseError('this is not the long format')

    preamble = [line for line in lines[:header_index] if line.strip()]
    if preamble:
        meta['preamble'] = preamble
        meta.update(_parse_stellar_preamble(preamble[0]))

    columns = data_columns + TERM_COLUMNS + REFERENCE_COLUMNS
    rows: list[list[Any]] = []
    notes: list[str] = []
    references: dict[int, str] = {}
    trailer: list[str] = []

    i = header_index + 1
    n = len(lines)
    while i < n:
        line = lines[i]
        if not _is_record_start(line):
            break
        values = _split_data_line(line)
        if len(values) != len(data_columns) - 2:  # element and ion are derived
            raise ParseError(
                f'line {i + 1}: expected {len(data_columns) - 2} fields, '
                f'got {len(values)}')
        if i + 3 >= n:
            raise ParseError(f'line {i + 1}: truncated record')

        species = values[0]
        element, ion = _split_species(species)
        row: list[Any] = [species, element, ion]
        for col, raw in zip(data_columns[3:], values[1:]):
            row.append(_coerce(raw, col.dtype))

        lower_coupling, lower_term = _parse_term(lines[i + 1])
        upper_coupling, upper_term = _parse_term(lines[i + 2])
        row += [lower_coupling, lower_term, upper_coupling, upper_term]

        refs = _parse_reference_line(lines[i + 3])
        row += [refs[c.name] for c in REFERENCE_COLUMNS]

        rows.append(row)
        i += 4

    # Everything after the records: free-text notes, then a numbered reference
    # list, then (for stellar extractions) the model and its abundances.
    in_references = False
    for line in lines[i:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.rstrip(':').lower() == 'references':
            in_references = True
            continue
        m = re.match(r'(\d+)\.\s+(.*)', stripped)
        if in_references and m:
            references[int(m.group(1))] = m.group(2).strip()
        elif stripped.startswith('*'):
            notes.append(stripped.lstrip('* ').strip())
        else:
            trailer.append(line)

    if trailer:
        meta['trailer'] = trailer

    return LineList(columns=columns, rows=rows, references=references,
                    notes=notes, meta=meta)
