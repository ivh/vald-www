"""Turn a completed Request's ASCII output into a converted file, on demand.

The conversion is cached beside the original in VALD_FTP_DIR, keyed by the
converter's extension, and regenerated whenever the ASCII is newer than the
cache. Nothing here runs during a job: a request that has finished already has
everything a conversion needs.
"""

from pathlib import Path
import gzip
import logging
import os
import tempfile

from django.utils import timezone

from . import Converter, available_converters
from .parser import LineList, ParseError, parse

logger = logging.getLogger(__name__)


def is_convertible(req) -> bool:
    """True when this request's output is long-format ASCII we can convert.

    Show Line results are excluded: they are a different renderer's output, not
    a line list. So is the short format, which drops the columns that make a
    table worth having.
    """
    if req.request_type == 'showline':
        return False
    if (req.parameters or {}).get('format') != 'long':
        return False
    return bool(req.is_complete() and req.output_exists()
                and not req.output_is_empty())


def sweep_patterns() -> list[str]:
    """Glob patterns for the files conversion leaves in VALD_FTP_DIR.

    Derived from the registry so that adding a converter does not quietly add a
    file the cleanup command never deletes. Gzipped conversions are left out:
    the cleanup command's own '*.gz' already covers them, and listing them again
    only makes it walk the same files twice.
    """
    return sorted({f'*{c.extension}' for c in available_converters()
                   if not c.extension.endswith('.gz')})


def converted_path(req, converter: Converter) -> Path | None:
    """Where this request's conversion lives, whether or not it exists yet."""
    source = req.output_path
    if source is None:
        return None
    stem = source.name[:-3] if source.name.endswith('.gz') else source.stem
    return source.with_name(stem + converter.extension)


def _read_source(source: Path) -> str:
    if source.name.endswith('.gz'):
        with gzip.open(source, 'rt', encoding='utf-8', errors='replace') as fh:
            return fh.read()
    return source.read_text(encoding='utf-8', errors='replace')


def _request_metadata(req) -> dict:
    """Provenance worth carrying into the converted file.

    Deliberately no user name or email: the conversions are fetched with the
    same unauthenticated capability URL as the ASCII, so nothing personal goes
    into a file that anyone holding the link can read.
    """
    from ..backend import uuid_to_6digit

    params = req.parameters or {}
    meta = {
        'origin': 'VALD - Vienna Atomic Line Database',
        'request_uuid': str(req.uuid),
        'request_id': uuid_to_6digit(req.uuid),
        'request_type': req.request_type,
        'converted_at': timezone.localtime().isoformat(timespec='seconds'),
    }
    if req.completed_at:
        meta['completed_at'] = timezone.localtime(
            req.completed_at).isoformat(timespec='seconds')

    for key, name in (
        ('stwvl', 'requested_wavelength_start'),
        ('endwvl', 'requested_wavelength_end'),
        ('elmion', 'species_filter'),
        ('pconf', 'configuration'),
        ('isotopic_scaling', 'isotopic_scaling'),
        ('vdwformat', 'vdw_format'),
        ('teff', 'teff'),
        ('logg', 'logg'),
        ('microturbulence', 'microturbulence'),
    ):
        value = params.get(key)
        if value not in (None, ''):
            meta[name] = value
    if params.get('hfssplit'):
        meta['hfs_splitting'] = True
    return meta


def linelist_for_request(req) -> LineList:
    """Parse this request's output, with its provenance folded into the metadata."""
    linelist = parse(_read_source(req.output_path))
    # File-derived values win: they describe what the Fortran actually wrote,
    # which is the thing a consumer of the converted file needs to trust.
    linelist.meta = {**_request_metadata(req), **linelist.meta}
    return linelist


def ensure_converted(req, converter: Converter) -> Path:
    """Return the converted file, writing it first if the cache is cold.

    Raises ParseError if the ASCII is not what the parser expects, and OSError
    if the file cannot be written.
    """
    target = converted_path(req, converter)
    source = req.output_path
    if target is None or source is None:
        raise ParseError('this request has no output file')

    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return target

    linelist = linelist_for_request(req)

    # Written to a temporary name in the same directory and renamed, so a
    # concurrent download never sees a half-written table.
    fd, tmp_name = tempfile.mkstemp(dir=target.parent,
                                    prefix=f'.{target.name}.', suffix='.tmp')
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        converter.write(linelist, tmp)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    logger.info('Converted %s to %s (%d transitions)',
                source.name, converter.key, len(linelist.rows))
    return target
