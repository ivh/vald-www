"""Writers that turn a parsed :class:`LineList` into a machine-readable file.

Every writer takes the same parsed object, so adding a format means adding one
function here and one entry in the registry - never touching the job pipeline.

CSV, ECSV, VOTable and SQLite are written with the standard library only, so
they work on any deployment. FITS and Parquet need astropy and pyarrow, which
are optional extras; the registry hides them when the import is unavailable
rather than offering a download that cannot be produced.
"""

from pathlib import Path
from typing import Any
import csv
import gzip
import json
import shutil
import sqlite3
import tempfile
import xml.etree.ElementTree as ET

from .parser import LineList


def _metadata_items(linelist: LineList) -> list[tuple[str, Any]]:
    """Flat, JSON-safe metadata, in a stable order."""
    skip = {'preamble', 'trailer'}
    items = [(k, v) for k, v in sorted(linelist.meta.items())
             if k not in skip and not isinstance(v, (list, dict))]
    return items


# --------------------------------------------------------------------------
# CSV


def write_csv(linelist: LineList, path: Path) -> None:
    """Comma-separated values, with the metadata as a '#' comment preamble.

    pandas, polars and astropy all skip '#' lines on request, so the file stays
    self-describing without stopping being a plain CSV.
    """
    with gzip.open(path, 'wt', newline='', encoding='utf-8') as fh:
        for key, value in _metadata_items(linelist):
            fh.write(f'# {key}: {value}\n')
        for note in linelist.notes:
            fh.write(f'# note: {note}\n')
        for number, text in sorted(linelist.references.items()):
            fh.write(f'# reference {number}: {text}\n')
        writer = csv.writer(fh)
        writer.writerow(linelist.column_names())
        for row in linelist.rows:
            writer.writerow(['' if v is None else v for v in row])


# --------------------------------------------------------------------------
# ECSV


def _yaml_scalar(value: Any) -> str:
    """Emit a YAML scalar. Strings are always double-quoted, so nothing in a
    term designation or a BibTeX key can be read as YAML syntax."""
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(str(value))  # JSON strings are valid YAML strings


_ECSV_DTYPES = {'str': 'string', 'int': 'int64', 'float': 'float64'}


def write_ecsv(linelist: LineList, path: Path) -> None:
    """Astropy's Enhanced CSV: a CSV body under a YAML header carrying dtypes,
    units, UCDs and the request metadata. Round-trips into an astropy Table."""
    lines = ['%ECSV 1.0', '---', 'delimiter: \',\'', 'datatype:']
    for col in linelist.columns:
        parts = [f'name: {_yaml_scalar(col.name)}',
                 f'datatype: {_ECSV_DTYPES[col.dtype]}']
        if col.unit:
            parts.append(f'unit: {_yaml_scalar(col.unit)}')
        if col.description:
            parts.append(f'description: {_yaml_scalar(col.description)}')
        if col.ucd:
            parts.append(f'meta: {{ucd: {_yaml_scalar(col.ucd)}}}')
        lines.append('- {' + ', '.join(parts) + '}')

    lines.append('meta: !!omap')
    for key, value in _metadata_items(linelist):
        lines.append(f'- {{{key}: {_yaml_scalar(value)}}}')
    if linelist.notes:
        notes = ', '.join(_yaml_scalar(n) for n in linelist.notes)
        lines.append(f'- {{notes: [{notes}]}}')
    if linelist.references:
        refs = ', '.join(f'{n}: {_yaml_scalar(t)}'
                         for n, t in sorted(linelist.references.items()))
        lines.append(f'- {{references: {{{refs}}}}}')
    lines.append('schema: astropy-2.0')

    with gzip.open(path, 'wt', newline='', encoding='utf-8') as fh:
        for line in lines:
            fh.write(f'# {line}\n')
        writer = csv.writer(fh)
        writer.writerow(linelist.column_names())
        for row in linelist.rows:
            writer.writerow(['' if v is None else v for v in row])


# --------------------------------------------------------------------------
# VOTable


_VOTABLE_DTYPES = {'str': 'char', 'int': 'int', 'float': 'double'}


def _votable_value(value: Any) -> str:
    if value is None:
        return ''
    return str(value)


def write_votable(linelist: LineList, path: Path) -> None:
    """VOTable 1.4 - what TOPCAT and Aladin open by double-click.

    Units and UCDs travel in the schema, so a consumer knows the wavelength is
    in air Angstroms without being told separately.
    """
    votable = ET.Element('VOTABLE', {
        'version': '1.4',
        'xmlns': 'http://www.ivoa.net/xml/VOTable/v1.3',
    })
    resource = ET.SubElement(votable, 'RESOURCE', {'name': 'VALD extraction'})

    for key, value in _metadata_items(linelist):
        ET.SubElement(resource, 'PARAM', {
            'name': key,
            'datatype': 'char',
            'arraysize': '*',
            'value': str(value),
        })
    for note in linelist.notes:
        ET.SubElement(resource, 'INFO', {'name': 'note', 'value': note}).text = note

    table = ET.SubElement(resource, 'TABLE', {'name': 'lines',
                                              'nrows': str(len(linelist.rows))})
    for col in linelist.columns:
        attrs = {'name': col.name, 'datatype': _VOTABLE_DTYPES[col.dtype]}
        if col.dtype == 'str':
            attrs['arraysize'] = '*'
        if col.unit:
            attrs['unit'] = col.unit
        if col.ucd:
            attrs['ucd'] = col.ucd
        field = ET.SubElement(table, 'FIELD', attrs)
        if col.description:
            ET.SubElement(field, 'DESCRIPTION').text = col.description

    data = ET.SubElement(ET.SubElement(table, 'DATA'), 'TABLEDATA')
    for row in linelist.rows:
        tr = ET.SubElement(data, 'TR')
        for value in row:
            ET.SubElement(tr, 'TD').text = _votable_value(value)

    if linelist.references:
        ref_table = ET.SubElement(resource, 'TABLE', {'name': 'references'})
        ET.SubElement(ref_table, 'FIELD',
                      {'name': 'number', 'datatype': 'int'})
        ET.SubElement(ref_table, 'FIELD',
                      {'name': 'reference', 'datatype': 'char',
                       'arraysize': '*'})
        ref_data = ET.SubElement(ET.SubElement(ref_table, 'DATA'), 'TABLEDATA')
        for number, text in sorted(linelist.references.items()):
            tr = ET.SubElement(ref_data, 'TR')
            ET.SubElement(tr, 'TD').text = str(number)
            ET.SubElement(tr, 'TD').text = text

    # Not indented: this is read by machines, and on a 500k-line extraction
    # pretty-printing costs three seconds and a tenth of the file for
    # whitespace. Pipe it through `xmllint --format` if you need to read one.
    tree = ET.ElementTree(votable)
    with gzip.open(path, 'wb') as fh:
        tree.write(fh, encoding='utf-8', xml_declaration=True)


# --------------------------------------------------------------------------
# SQLite


_SQLITE_DTYPES = {'str': 'TEXT', 'int': 'INTEGER', 'float': 'REAL'}


def write_sqlite(linelist: LineList, path: Path) -> None:
    """One relational file holding the transitions, their references and the
    request metadata - the reference numbers in `lines` are foreign keys into
    `references`, which is the join the ASCII format cannot express.

    Gzipped, like every other result the site hands out: an uncompressed
    database is the only download here that is not compressed either by its own
    container or by gzip, and at 500k transitions that is 133 MB against 25.
    Users gunzip it before opening it, exactly as they do the ASCII.
    """
    # sqlite3 needs a seekable file, so the database is built beside the target
    # and then compressed into it.
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.sqlite') as raw:
        _write_sqlite_database(linelist, Path(raw.name))
        with open(raw.name, 'rb') as plain, gzip.open(path, 'wb') as out:
            shutil.copyfileobj(plain, out)


def _write_sqlite_database(linelist: LineList, path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        columns = ', '.join(f'"{c.name}" {_SQLITE_DTYPES[c.dtype]}'
                            for c in linelist.columns)
        cur.execute(f'CREATE TABLE lines ({columns})')
        placeholders = ', '.join('?' * len(linelist.columns))
        cur.executemany(f'INSERT INTO lines VALUES ({placeholders})',
                        linelist.rows)
        cur.execute('CREATE INDEX lines_wavelength ON lines (wavelength)')
        cur.execute('CREATE INDEX lines_species ON lines (species)')

        cur.execute('CREATE TABLE "references" '
                    '(number INTEGER PRIMARY KEY, reference TEXT)')
        cur.executemany('INSERT INTO "references" VALUES (?, ?)',
                        sorted(linelist.references.items()))

        cur.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)')
        cur.executemany('INSERT INTO metadata VALUES (?, ?)',
                        [(k, str(v)) for k, v in _metadata_items(linelist)])

        cur.execute('CREATE TABLE notes (note TEXT)')
        cur.executemany('INSERT INTO notes VALUES (?)',
                        [(n,) for n in linelist.notes])

        # A column-level description has nowhere else to live in SQLite.
        cur.execute('CREATE TABLE columns '
                    '(name TEXT PRIMARY KEY, unit TEXT, ucd TEXT, '
                    'description TEXT)')
        cur.executemany('INSERT INTO columns VALUES (?, ?, ?, ?)',
                        [(c.name, c.unit, c.ucd, c.description)
                         for c in linelist.columns])
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# FITS (astropy)


# FITS integer columns have no null, so a missing value is written as this and
# declared in TNULLn - which is what a FITS reader looks at.
FITS_INT_NULL = -999999

# The FITS unit syntax has no '/', so the wavenumber unit every other format
# spells '1/cm' has to be written 'cm-1' here. Without this a cm^-1 extraction
# produces a TUNIT that astropy refuses to parse on the way back in.
_FITS_UNITS = {'1/cm': 'cm-1'}


def _fits_unit(unit: str) -> str | None:
    return _FITS_UNITS.get(unit, unit) or None


def write_fits(linelist: LineList, path: Path) -> None:
    """FITS binary table, gzipped, with the request metadata in the header and
    the reference list as a second extension.

    Gzipped because a FITS binary table pads every string cell to the longest
    value in its column: a 500k-line extraction is 200 MB on disk and 20 MB
    compressed, and astropy, fitsio and CFITSIO all read .fits.gz directly.
    """
    import numpy as np
    from astropy.io import fits

    def column(index: int, col):
        values = [row[index] for row in linelist.rows]
        if col.dtype == 'str':
            values = ['' if v is None else str(v) for v in values]
            width = max((len(v) for v in values), default=1) or 1
            return fits.Column(name=col.name, format=f'{width}A',
                               array=np.array(values), unit=_fits_unit(col.unit))
        if col.dtype == 'int':
            filled = [FITS_INT_NULL if v is None else int(v) for v in values]
            return fits.Column(name=col.name, format='K',
                               array=np.array(filled, dtype='int64'),
                               null=FITS_INT_NULL, unit=_fits_unit(col.unit))
        filled = [np.nan if v is None else float(v) for v in values]
        return fits.Column(name=col.name, format='D',
                           array=np.array(filled, dtype='float64'),
                           unit=_fits_unit(col.unit))

    hdu = fits.BinTableHDU.from_columns(
        [column(i, c) for i, c in enumerate(linelist.columns)], name='LINES')
    for i, col in enumerate(linelist.columns, start=1):
        if col.description:
            hdu.header[f'TCOMM{i}'] = col.description[:68]
        if col.ucd:
            hdu.header[f'TUCD{i}'] = col.ucd

    primary = fits.PrimaryHDU()
    primary.header['ORIGIN'] = 'VALD'
    for key, value in _metadata_items(linelist):
        # HIERARCH keeps the long names readable instead of truncating them.
        primary.header[f'HIERARCH {key}'] = value
    for note in linelist.notes:
        primary.header.add_comment(note)

    hdus = [primary, hdu]
    if linelist.references:
        numbers, texts = zip(*sorted(linelist.references.items()))
        width = max(len(t) for t in texts)
        hdus.append(fits.BinTableHDU.from_columns([
            fits.Column(name='number', format='J',
                        array=np.array(numbers, dtype='int32')),
            fits.Column(name='reference', format=f'{width}A',
                        array=np.array(texts)),
        ], name='REFS'))

    # Written uncompressed first and then gzipped: astropy gzips on its own
    # only when it can see a .gz filename, and this is handed a temporary name
    # ending in .tmp - and writeto() seeks backwards to pad, which a
    # write-mode GzipFile cannot do.
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.fits') as raw:
        fits.HDUList(hdus).writeto(raw.name, overwrite=True)
        with open(raw.name, 'rb') as plain, gzip.open(path, 'wb') as out:
            shutil.copyfileobj(plain, out)


# --------------------------------------------------------------------------
# Parquet (pyarrow)


def write_parquet(linelist: LineList, path: Path) -> None:
    """Columnar Parquet - what pandas, polars and duckdb read natively.

    The metadata, notes and reference list ride along in the schema's key-value
    metadata as JSON, under the 'vald' key.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    types = {'str': pa.string(), 'int': pa.int64(), 'float': pa.float64()}
    fields, arrays = [], []
    for index, col in enumerate(linelist.columns):
        values = [row[index] for row in linelist.rows]
        metadata = {k: v for k, v in
                    (('unit', col.unit), ('ucd', col.ucd),
                     ('description', col.description)) if v}
        fields.append(pa.field(col.name, types[col.dtype],
                               metadata=metadata or None))
        arrays.append(pa.array(values, type=types[col.dtype]))

    schema_meta = {
        'vald': json.dumps({
            'metadata': dict(_metadata_items(linelist)),
            'notes': linelist.notes,
            'references': {str(k): v for k, v in
                           sorted(linelist.references.items())},
        })
    }
    schema = pa.schema(fields, metadata=schema_meta)
    pq.write_table(pa.Table.from_arrays(arrays, schema=schema), path,
                   compression='zstd')
