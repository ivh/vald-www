"""Machine-readable renderings of a completed long-format extraction.

Conversion happens lazily, on request, from the ASCII the job already produced
- never inside the job pipeline. A converter that breaks therefore costs a
failed download, not a failed extraction, and adding one touches nothing the
Fortran runs.

Only the long format can be converted; the short format drops the columns that
make the result worth putting in a table. See parser.py for why.
"""

from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Callable

from .parser import LineList, ParseError, parse
from . import writers


@dataclass(frozen=True)
class Converter:
    key: str
    label: str
    extension: str
    description: str
    write: Callable[[LineList, Path], None]
    requires: tuple[str, ...] = ()

    def available(self) -> bool:
        """False when an optional dependency is missing, which hides the format
        from the menu instead of offering a download that cannot be produced."""
        return all(find_spec(module) is not None for module in self.requires)


CONVERTERS: tuple[Converter, ...] = (
    Converter(
        key='csv',
        label='CSV',
        extension='.csv.gz',
        description='Plain comma-separated values, metadata in a # preamble.',
        write=writers.write_csv,
    ),
    Converter(
        key='ecsv',
        label='ECSV (astropy)',
        extension='.ecsv.gz',
        description='CSV under a YAML header carrying units, types and metadata.',
        write=writers.write_ecsv,
    ),
    Converter(
        key='votable',
        label='VOTable',
        extension='.vot.gz',
        description='IVOA VOTable with units and UCDs, for TOPCAT and Aladin.',
        write=writers.write_votable,
    ),
    Converter(
        key='fits',
        label='FITS table',
        extension='.fits.gz',
        description='FITS binary table, references in a second extension.',
        write=writers.write_fits,
        requires=('astropy', 'numpy'),
    ),
    Converter(
        key='parquet',
        label='Parquet',
        extension='.parquet',
        description='Columnar Parquet, read natively by pandas, polars and duckdb.',
        write=writers.write_parquet,
        requires=('pyarrow',),
    ),
    Converter(
        key='sqlite',
        label='SQLite',
        extension='.sqlite.gz',
        description='Relational database: transitions joined to their references.',
        write=writers.write_sqlite,
    ),
)

_BY_KEY = {c.key: c for c in CONVERTERS}


def get_converter(key: str) -> Converter | None:
    converter = _BY_KEY.get(key)
    if converter is None or not converter.available():
        return None
    return converter


def available_converters() -> list[Converter]:
    return [c for c in CONVERTERS if c.available()]


__all__ = ['CONVERTERS', 'Converter', 'LineList', 'ParseError',
           'available_converters', 'get_converter', 'parse']
