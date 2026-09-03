"""Parsing long-format output and converting it to machine-readable formats.

The ASCII fixtures below are verbatim extracts of real output from the local
VALD installation, trailing whitespace and all - a hand-tidied approximation
would test the parser against a format nothing produces. The tests marked
vald_binaries generate fresh output instead, which is what catches the Fortran
drifting away from the parser.
"""
import datetime
import gzip
import sqlite3
from pathlib import Path

import pytest
from django.utils import timezone

from vald.converters import CONVERTERS, available_converters, get_converter
from vald.converters.parser import ParseError, parse
from vald.converters.service import (
    converted_path, ensure_converted, is_convertible, linelist_for_request,
    sweep_patterns,
)
from vald.models import Request


LONG_EV_AIR = """\
                                                                     Lande factors        Damping parameters
Elm Ion       WL_air(A)  log gf* E_low(eV) J lo  E_up(eV) J up   lower   upper    mean   Rad.  Stark    Waals
'Ca 1',      7844.17349,  -3.789,  4.4410,  3.0,  6.0211,  2.0,  1.080,  1.500,  0.650, 8.310,-4.710,-7.660,
'  LS                                                                         3p6.3d.4p 3F*'
'  LS                                                                            3p6.3d2 3P'
'_          Kurucz CaI 2007    1 wl:K07   1 gf:K07   1 K07   1 K07   1 K07   1 K07   1 K07   1 K07   1 K07             Ca'
'Fe 2',      7845.05927,  -9.834,  5.9465,  2.0,  7.5264,  3.0,  1.500,  0.870,  0.240, 6.300,-3.040,-6.660,
  LS                                                                        3p6.4s.11p 3P*
  LS                                                                          3p6.3d.7g 3G
'_      A   Kurucz FeII 2013   1 wl:K13  22 gf:RU   1 K13   1 K13   1 K13   1 K13   1 K13  19 BA-J   1 K13            Fe+'
* oscillator strengths were scaled by the solar isotopic ratios.
 References:
  1. Kurucz obs. energy level: Ca 1
 19. Barklem, Anstee & O'Mara broadening
 22. Raassen unpublished
"""

LONG_CM_VAC = """\
                                                                            Lande factors       Damping parameters
Elm Ion       WL_vac(A)  log gf* E_low(cm^-1) J lo  E_up(cm^-1)  J up  lower   upper    mean    Rad.  Stark  Waals
'O 4',       4000.52807,  -2.104, 514371.300,   0.5, 539368.000,   0.5,  0.670,  2.000,  1.330,10.560,-5.490,-7.620,
'  LS                                                                    2s.2p.(3P*).3d 2P*'
'  LS                                                                             2s2.5s 2S'
'_          Kurucz OIV 2011    1 wl:K11   1 gf:K11   1 K11   1 K11   1 K11   1 K11   1 K11   1 K11   1 K11            O+3'
 References:
  1. Kurucz obs. energy level: O 4
"""

LONG_STELLAR = """\
 5026.61700, 5076.61700, 1, 3097, 3.6 Wavelength region, lines selected, lines processed, Vmicro
                                                                     Lande factors       Damping parameters  Central
Spec Ion       WL_air(A)  log gf* E_low(eV) J lo E_up(eV)  J up  lower   upper    mean   Rad.   Stark  Waals  depth
'Fe 1',      5049.81960,  -1.355,  2.2786,  3.0,  4.7332,  2.0,  1.240,  1.100,  1.610, 7.980,-6.170,-7.790, 0.632,
'  LS                                                                       3d7.(4F).4s a3F'
'  LS                                                                     3d7.(4F).4p z3D*'
'_      B   Fe I Nave 1994     1 wl:N94   2 gf:BWL   1 N94   1 N94   1 N94   1 N94   1 N94   3 BA-J   1 N94            Fe'
'19000G50.KRZ',
'H :  0.92','He: -1.11',
'Es:-20.00','END'
* oscillator strengths were scaled by the solar isotopic ratios.
 References:
  1. Nave et al. 1994
  2. Wisconsin exp. data
  3. Barklem, Anstee & O'Mara broadening
"""

# select4.f still writes its model and abundance block when nothing was
# selected, so an empty stellar result is not an empty file.
EMPTY_STELLAR = """\
 5077.34000, 5082.34000, 0, 314, 4.8 Wavelength region, lines selected, lines processed, Vmicro
                                                                     Lande factors       Damping parameters  Central
Spec Ion       WL_air(A)  log gf* E_low(eV) J lo E_up(eV)  J up  lower   upper    mean   Rad.   Stark  Waals  depth
'24000G30.KRZ',
'H :  0.92','He: -1.11',
'Es:-20.00','END'
"""

SHORT_EV_AIR = """\
                                             Damping parameters   Lande
Elm Ion       WL_air(A) Excit(eV) log gf*   Rad.  Stark    Waals  factor   References
'Ti 2',      3416.95680,    1.237, -1.540, 8.350,-6.530,-7.840,  1.270,'   1 wl:Sal12a   1 Sal12a   1 gf:WLSC   2 K16   2 K16   2 K16   2 K16    Ti+'
 References:
  1. Si, Sc-Ni Wisconsin exp. data
"""


# --- the parser -----------------------------------------------------------

def test_columns_come_from_the_header():
    ll = parse(LONG_EV_AIR)
    assert ll.column_names()[:9] == [
        'species', 'element', 'ion', 'wavelength', 'log_gf',
        'e_low', 'j_low', 'e_up', 'j_up']
    assert ll.column('wavelength').unit == 'Angstrom'
    assert ll.column('e_low').unit == 'eV'
    assert ll.meta['wavelength_medium'] == 'air'
    assert ll.meta['energy_unit'] == 'eV'


def test_wavenumber_and_vacuum_headers_change_the_units():
    ll = parse(LONG_CM_VAC)
    assert ll.column('e_low').unit == '1/cm'
    assert ll.meta['wavelength_medium'] == 'vacuum'
    assert ll.rows[0][ll.column_names().index('e_low')] == 514371.300


def test_each_record_is_four_lines():
    ll = parse(LONG_EV_AIR)
    assert len(ll.rows) == 2
    names = ll.column_names()
    first = dict(zip(names, ll.rows[0]))
    assert first['species'] == 'Ca 1'
    assert first['element'] == 'Ca'
    assert first['ion'] == 1
    assert first['wavelength'] == 7844.17349
    assert first['log_gf'] == -3.789
    assert first['lande_mean'] == 0.650
    assert first['waals_damping'] == -7.660
    assert first['lower_term'] == '3p6.3d.4p 3F*'
    assert first['upper_term'] == '3p6.3d2 3P'
    assert first['lower_coupling'] == 'LS'


def test_term_lines_parse_with_or_without_quotes():
    """presformat5 output reaches us both ways; neither may lose a character."""
    ll = parse(LONG_EV_AIR)
    quoted, unquoted = (dict(zip(ll.column_names(), row)) for row in ll.rows)
    assert quoted['lower_term'] == '3p6.3d.4p 3F*'
    assert unquoted['lower_term'] == '3p6.4s.11p 3P*'
    assert unquoted['upper_coupling'] == 'LS'


def test_reference_slots_are_named_by_what_they_cite():
    """BA-J is a van der Waals broadening reference, so it must land there.

    This is the assertion that pins the nine reference slots to their meanings;
    getting it wrong mislabels someone's provenance silently.
    """
    ll = parse(LONG_EV_AIR)
    row = dict(zip(ll.column_names(), ll.rows[1]))
    assert row['waals_damping_bibkey'] == 'BA-J'
    assert row['waals_damping_ref'] == 19
    assert row['log_gf_bibkey'] == 'RU'
    assert row['log_gf_ref'] == 22
    assert row['wavelength_bibkey'] == 'K13'
    assert row['e_low_bibkey'] == 'K13'
    assert row['term_bibkey'] == 'K13'


def test_the_bibkey_prefix_is_dropped():
    """'wl:' and 'gf:' only repeat what the column name already says."""
    row = dict(zip(parse(LONG_EV_AIR).column_names(), parse(LONG_EV_AIR).rows[0]))
    assert row['wavelength_bibkey'] == 'K07'
    assert row['log_gf_bibkey'] == 'K07'


def test_accuracy_and_comment_come_off_the_reference_line():
    ll = parse(LONG_EV_AIR)
    first, second = (dict(zip(ll.column_names(), row)) for row in ll.rows)
    assert first['accuracy'] == ''
    assert first['comment'] == 'Kurucz CaI 2007'
    assert first['species_long'] == 'Ca'
    assert second['accuracy'] == 'A'
    assert second['species_long'] == 'Fe+'


def test_trailing_reference_list_and_notes_are_kept():
    ll = parse(LONG_EV_AIR)
    assert ll.references[1] == 'Kurucz obs. energy level: Ca 1'
    assert ll.references[19] == "Barklem, Anstee & O'Mara broadening"
    assert ll.notes == [
        'oscillator strengths were scaled by the solar isotopic ratios.']


def test_stellar_output_has_a_depth_column_and_a_summary_line():
    ll = parse(LONG_STELLAR)
    assert 'central_depth' in ll.column_names()
    row = dict(zip(ll.column_names(), ll.rows[0]))
    assert row['central_depth'] == 0.632
    assert ll.meta['wavelength_start'] == 5026.617
    assert ll.meta['lines_selected'] == 1
    assert ll.meta['lines_processed'] == 3097
    assert ll.meta['microturbulence_km_s'] == 3.6


def test_the_stellar_abundance_block_is_not_read_as_data():
    """'19000G50.KRZ', looks like a record until the species field is checked."""
    ll = parse(LONG_STELLAR)
    assert len(ll.rows) == 1
    assert "'19000G50.KRZ',".strip() in [l.strip() for l in ll.meta['trailer']]


def test_a_stellar_result_with_no_lines_parses_to_no_rows():
    ll = parse(EMPTY_STELLAR)
    assert ll.rows == []
    assert ll.meta['lines_selected'] == 0


def test_the_short_format_is_refused():
    with pytest.raises(ParseError):
        parse(SHORT_EV_AIR)


def test_an_unknown_header_raises_rather_than_guessing():
    broken = LONG_EV_AIR.replace('WL_air(A)', 'WL_moon(furlong)')
    with pytest.raises(ParseError, match='wavelength column'):
        parse(broken)

    renamed = LONG_EV_AIR.replace('E_low(eV)', 'E_bottom(eV)')
    with pytest.raises(ParseError, match='energy column'):
        parse(renamed)


def test_a_truncated_record_raises():
    truncated = '\n'.join(LONG_EV_AIR.splitlines()[:4])
    with pytest.raises(ParseError, match='truncated'):
        parse(truncated)


def test_a_record_with_the_wrong_field_count_raises():
    mangled = LONG_EV_AIR.replace(' 8.310,-4.710,-7.660,', ' 8.310,-4.710,')
    with pytest.raises(ParseError, match='fields'):
        parse(mangled)


# --- the writers ----------------------------------------------------------

@pytest.fixture
def linelist():
    ll = parse(LONG_EV_AIR)
    ll.meta['request_uuid'] = '11111111-2222-3333-4444-555555555555'
    return ll


@pytest.mark.parametrize('converter', CONVERTERS, ids=lambda c: c.key)
def test_every_writer_produces_a_non_empty_file(converter, linelist, tmp_path):
    if not converter.available():
        pytest.skip(f'{converter.key} needs {", ".join(converter.requires)}')
    target = tmp_path / f'out{converter.extension}'
    converter.write(linelist, target)
    assert target.stat().st_size > 0


def test_csv_carries_the_metadata_as_comments(linelist, tmp_path):
    target = tmp_path / 'out.csv.gz'
    get_converter('csv').write(linelist, target)
    with gzip.open(target, 'rt') as fh:
        text = fh.read()
    assert '# request_uuid: 11111111-2222-3333-4444-555555555555' in text
    assert '# reference 19: ' in text
    body = [l for l in text.splitlines() if not l.startswith('#')]
    assert body[0].startswith('species,element,ion,wavelength')
    assert len(body) == 1 + len(linelist.rows)


def test_sqlite_joins_transitions_to_their_references(linelist, tmp_path):
    target = tmp_path / 'out.sqlite'
    get_converter('sqlite').write(linelist, target)
    conn = sqlite3.connect(target)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'lines', 'references', 'metadata', 'notes', 'columns'} <= tables
        assert conn.execute('SELECT count(*) FROM lines').fetchone()[0] == 2
        joined = conn.execute(
            'SELECT l.species, r.reference FROM lines l '
            'JOIN "references" r ON r.number = l.waals_damping_ref '
            'WHERE l.species = ?', ('Fe 2',)).fetchone()
        assert joined == ('Fe 2', "Barklem, Anstee & O'Mara broadening")
        assert conn.execute("SELECT value FROM metadata WHERE key='request_uuid'"
                            ).fetchone()[0] == linelist.meta['request_uuid']
        assert conn.execute("SELECT unit FROM columns WHERE name='wavelength'"
                            ).fetchone()[0] == 'Angstrom'
    finally:
        conn.close()


def test_sqlite_overwrites_a_stale_file(linelist, tmp_path):
    """ensure_converted() hands the writer an existing temporary file."""
    target = tmp_path / 'out.sqlite'
    target.write_bytes(b'not a database')
    get_converter('sqlite').write(linelist, target)
    conn = sqlite3.connect(target)
    try:
        assert conn.execute('SELECT count(*) FROM lines').fetchone()[0] == 2
    finally:
        conn.close()


def test_ecsv_round_trips_through_astropy(linelist, tmp_path):
    table = pytest.importorskip('astropy.table')
    target = tmp_path / 'out.ecsv.gz'
    get_converter('ecsv').write(linelist, target)
    t = table.Table.read(target, format='ascii.ecsv')

    assert len(t) == 2
    assert str(t['wavelength'].unit) == 'Angstrom'
    assert str(t['e_low'].unit) == 'eV'
    assert t['log_gf'].description.startswith('log of the oscillator strength')
    assert t['species'][0] == 'Ca 1'
    assert t['lower_term'][0] == '3p6.3d.4p 3F*'
    assert t.meta['request_uuid'] == linelist.meta['request_uuid']
    assert t.meta['references'][19] == "Barklem, Anstee & O'Mara broadening"


def test_votable_round_trips_and_carries_units_and_ucds(linelist, tmp_path):
    votable_mod = pytest.importorskip('astropy.io.votable')
    target = tmp_path / 'out.vot.gz'
    get_converter('votable').write(linelist, target)
    vot = votable_mod.parse(str(target))

    tables = {t.name: t for t in vot.iter_tables()}
    assert set(tables) == {'lines', 'references'}
    lines = tables['lines']
    assert len(lines.array) == 2
    assert lines.get_field_by_id_or_name('wavelength').ucd == 'em.wl'
    assert str(lines.get_field_by_id_or_name('wavelength').unit) == 'Angstrom'
    assert lines.array['species'][0] == 'Ca 1'
    assert len(tables['references'].array) == 3


def test_fits_round_trips_with_metadata_in_the_header(linelist, tmp_path):
    fits = pytest.importorskip('astropy.io.fits')
    from astropy.table import Table

    target = tmp_path / 'out.fits'
    get_converter('fits').write(linelist, target)

    t = Table.read(target, hdu='LINES')
    assert len(t) == 2
    assert t['species'][0] == 'Ca 1'
    assert t['wavelength'][0] == pytest.approx(7844.17349)
    with fits.open(target) as hdul:
        assert hdul[0].header['request_uuid'] == linelist.meta['request_uuid']
        assert hdul[0].header['energy_unit'] == 'eV'
        assert 'REFS' in [h.name for h in hdul]


def test_parquet_round_trips_with_metadata_in_the_schema(linelist, tmp_path):
    pq = pytest.importorskip('pyarrow.parquet')
    import json

    target = tmp_path / 'out.parquet'
    get_converter('parquet').write(linelist, target)
    t = pq.read_table(target)

    assert t.num_rows == 2
    assert t.column('species').to_pylist() == ['Ca 1', 'Fe 2']
    assert t.schema.field('wavelength').metadata[b'unit'] == b'Angstrom'
    meta = json.loads(t.schema.metadata[b'vald'])
    assert meta['metadata']['request_uuid'] == linelist.meta['request_uuid']
    assert meta['references']['19'] == "Barklem, Anstee & O'Mara broadening"


@pytest.mark.parametrize('converter', CONVERTERS, ids=lambda c: c.key)
def test_a_result_with_no_transitions_still_writes(converter, tmp_path):
    if not converter.available():
        pytest.skip(f'{converter.key} needs {", ".join(converter.requires)}')
    converter.write(parse(EMPTY_STELLAR), tmp_path / f'out{converter.extension}')


# --- the caching layer ----------------------------------------------------

@pytest.fixture
def ftp_dir(tmp_path, settings):
    d = tmp_path / 'FTP'
    d.mkdir()
    settings.VALD_FTP_DIR = d
    return d


def make_long_result(directory, name='Result.000001.gz', body=LONG_EV_AIR):
    with gzip.open(directory / name, 'wt') as f:
        f.write(body)
    return name


def long_request(user, output_file, request_type='extractelement', **params):
    parameters = {'format': 'long', 'stwvl': 7844, 'endwvl': 7846}
    parameters.update(params)
    req = Request.objects.create(user=user, request_type=request_type,
                                 parameters=parameters, status='complete',
                                 output_file=output_file)
    Request.objects.filter(pk=req.pk).update(completed_at=timezone.now())
    req.refresh_from_db()
    return req


@pytest.mark.django_db
def test_only_completed_long_format_extractions_convert(approved_user, ftp_dir):
    name = make_long_result(ftp_dir)
    assert is_convertible(long_request(approved_user, name))

    short = long_request(approved_user, name, format='short')
    assert not is_convertible(short)

    showline = long_request(approved_user, name, request_type='showline')
    assert not is_convertible(showline)

    pending = long_request(approved_user, name)
    pending.status = 'processing'
    assert not is_convertible(pending)

    missing = long_request(approved_user, 'NoSuchFile.000002.gz')
    assert not is_convertible(missing)


@pytest.mark.django_db
def test_conversion_is_cached_beside_the_original(approved_user, ftp_dir):
    req = long_request(approved_user, make_long_result(ftp_dir))
    converter = get_converter('sqlite')

    target = ensure_converted(req, converter)
    assert target == ftp_dir / 'Result.000001.sqlite'
    assert target == converted_path(req, converter)

    first = target.stat().st_mtime_ns
    assert ensure_converted(req, converter).stat().st_mtime_ns == first, (
        'a second download reconverted instead of using the cache')


@pytest.mark.django_db
def test_a_newer_result_invalidates_the_cache(approved_user, ftp_dir):
    req = long_request(approved_user, make_long_result(ftp_dir))
    converter = get_converter('csv')
    target = ensure_converted(req, converter)

    # Rerunning a request rewrites the ASCII in place; the conversion must not
    # keep serving the old rows.
    make_long_result(ftp_dir, body=LONG_CM_VAC)
    future = datetime.datetime.now().timestamp() + 10
    import os
    os.utime(req.output_path, (future, future))

    with gzip.open(ensure_converted(req, converter), 'rt') as fh:
        assert 'O 4' in fh.read()


@pytest.mark.django_db
def test_no_temporary_files_are_left_behind(approved_user, ftp_dir):
    req = long_request(approved_user, make_long_result(ftp_dir))
    for converter in available_converters():
        ensure_converted(req, converter)
    assert not list(ftp_dir.glob('.*tmp')), 'a temporary file survived'


@pytest.mark.django_db
def test_conversions_are_as_readable_as_the_ascii_beside_them(
        approved_user, ftp_dir):
    """The vhost serves this directory from disk; mkstemp's 0600 would 403."""
    req = long_request(approved_user, make_long_result(ftp_dir))
    for converter in available_converters():
        mode = ensure_converted(req, converter).stat().st_mode & 0o777
        assert mode & 0o044, f'{converter.key} is not world-readable ({mode:o})'


@pytest.mark.django_db
def test_request_provenance_reaches_the_metadata(approved_user, ftp_dir):
    req = long_request(approved_user, make_long_result(ftp_dir),
                       elmion='Ca 1', pconf='default')
    ll = linelist_for_request(req)
    assert ll.meta['request_uuid'] == str(req.uuid)
    assert ll.meta['request_type'] == 'extractelement'
    assert ll.meta['species_filter'] == 'Ca 1'
    assert ll.meta['requested_wavelength_start'] == 7844
    # What the file says about its own units must win over anything inferred
    # from the request.
    assert ll.meta['wavelength_unit'] == 'Angstrom'


@pytest.mark.django_db
def test_the_metadata_carries_nothing_personal(approved_user, ftp_dir):
    """The conversions are fetched with the same capability URL as the ASCII."""
    req = long_request(approved_user, make_long_result(ftp_dir))
    values = ' '.join(str(v) for v in linelist_for_request(req).meta.values())
    assert approved_user.name not in values
    assert 'test@example.com' not in values


def test_the_cleanup_sweep_covers_every_uncompressed_conversion():
    patterns = sweep_patterns()
    for converter in available_converters():
        if converter.extension.endswith('.gz'):
            continue  # the command's own '*.gz' already matches these
        assert f'*{converter.extension}' in patterns


# --- the download view ----------------------------------------------------

@pytest.mark.django_db
def test_the_detail_page_offers_the_menu_for_long_results(
        logged_in_client, approved_user, ftp_dir):
    req = long_request(approved_user, make_long_result(ftp_dir))
    body = logged_in_client.get(f'/request/{req.uuid}/').content.decode()
    assert 'name="fmt"' in body
    for converter in available_converters():
        assert f'value="{converter.key}"' in body


@pytest.mark.django_db
def test_the_detail_page_offers_nothing_for_short_results(
        logged_in_client, approved_user, ftp_dir):
    req = long_request(approved_user, make_long_result(ftp_dir), format='short')
    body = logged_in_client.get(f'/request/{req.uuid}/').content.decode()
    assert 'name="fmt"' not in body


@pytest.mark.django_db
def test_a_download_redirects_to_the_url_ending_in_the_filename(
        client, approved_user, ftp_dir):
    """wget names the saved file after the last URL segment."""
    req = long_request(approved_user, make_long_result(ftp_dir))
    response = client.get(f'/request/{req.uuid}/as/csv/')
    assert response.status_code == 302
    assert response['Location'].endswith('/as/csv/Result.000001.csv.gz')

    served = client.get(response['Location'])
    assert served.status_code == 200
    assert 'Result.000001.csv.gz' in served['Content-Disposition']
    assert b'Ca 1' in gzip.decompress(b''.join(served.streaming_content))


@pytest.mark.django_db
def test_the_menu_submits_to_the_bare_url(client, approved_user, ftp_dir):
    req = long_request(approved_user, make_long_result(ftp_dir))
    response = client.get(f'/request/{req.uuid}/as/', {'fmt': 'sqlite'})
    assert response.status_code == 302
    assert response['Location'].endswith('/as/sqlite/Result.000001.sqlite')


@pytest.mark.django_db
def test_an_unknown_format_is_a_404_not_a_redirect(client, approved_user, ftp_dir):
    req = long_request(approved_user, make_long_result(ftp_dir))
    for url in (f'/request/{req.uuid}/as/xlsx/',
                f'/request/{req.uuid}/as/'):
        response = client.get(url)
        assert response.status_code == 404
        assert response['Content-Type'].startswith('text/plain')


@pytest.mark.django_db
def test_a_short_format_result_cannot_be_converted(client, approved_user, ftp_dir):
    req = long_request(approved_user, make_long_result(ftp_dir), format='short')
    response = client.get(f'/request/{req.uuid}/as/csv/')
    assert response.status_code == 404
    assert b'long-format' in response.content


@pytest.mark.django_db
def test_output_the_parser_cannot_read_fails_this_download_only(
        client, approved_user, ftp_dir):
    """The ASCII download must stay usable when a conversion cannot be made."""
    req = long_request(approved_user, make_long_result(ftp_dir, body=SHORT_EV_AIR))
    response = client.get(f'/request/{req.uuid}/as/csv/')
    assert response.status_code == 422
    assert b'ASCII download is unaffected' in response.content
    assert client.get(f'/request/{req.uuid}/download/').status_code == 302


@pytest.mark.django_db
def test_an_expired_result_says_so(approved_user, ftp_dir, client, settings):
    from django.test import Client
    req = long_request(approved_user, 'Gone.000003.gz')
    old = timezone.now() - datetime.timedelta(
        days=getattr(settings, 'VALD_RESULT_RETENTION_DAYS', 2) + 1)
    Request.objects.filter(pk=req.pk).update(created_at=old, completed_at=old)

    response = Client().get(f'/request/{req.uuid}/as/csv/')
    assert response.status_code == 404
    assert b'expired' in response.content


@pytest.mark.django_db
def test_a_mismatched_filename_is_refused(client, approved_user, ftp_dir):
    req = long_request(approved_user, make_long_result(ftp_dir))
    response = client.get(f'/request/{req.uuid}/as/csv/Something.Else.csv.gz')
    assert response.status_code == 404


# --- against the real binaries -------------------------------------------

@pytest.mark.vald_binaries
@pytest.mark.parametrize('energy,waveunit,medium,labelled_medium', [
    ('eV', 'angstrom', 'air', 'air'),
    ('eV', 'nm', 'vacuum', 'vacuum'),
    ('1/cm', 'angstrom', 'vacuum', 'vacuum'),
    # Asking for the wavelength as a wavenumber overrides the medium flag:
    # presformat5.f labels that column WL_vac(cm^-1) either way, wavenumbers
    # being a vacuum quantity by convention. The parser must report what the
    # file says, not what the request asked for.
    ('1/cm', '1/cm', 'air', 'vacuum'),
])
def test_freshly_extracted_long_output_parses(
        energy, waveunit, medium, labelled_medium, vald_home, tmp_path):
    """Runs the real pipeline, so the parser is tested against today's Fortran.

    Every long-format header presformat5 can write goes through here; a change
    to one of them fails this rather than silently mislabelling a column.
    """
    from vald.job_runner import JobConfig, JobRunner

    job, ftp = tmp_path / 'job', tmp_path / 'ftp'
    job.mkdir()
    ftp.mkdir()

    flags = [0] * 13
    flags[0] = 4 if energy == '1/cm' else 1
    flags[9] = 1 if medium == 'vacuum' else 0
    flags[10] = {'angstrom': 0, 'nm': 1, '1/cm': 2}[waveunit]
    flags[11] = 1

    wl = (5700.0, 5703.0)
    if waveunit == '1/cm':
        wl = (1e8 / wl[1], 1e8 / wl[0])

    runner = JobRunner()
    runner.ftp_dir = ftp
    ok, result = runner.run(JobConfig(
        job_id=1, client_name='Tester', job_dir=job, request_type='extractall',
        wl_start=wl[0], wl_end=wl[1], format_flags=flags,
        config_path=str(vald_home / 'CONFIG' / 'default.cfg')))
    assert ok, result

    output = next(p for p in ftp.iterdir()
                  if p.suffix == '.gz' and '.bib' not in p.name)
    with gzip.open(output, 'rt', errors='replace') as fh:
        linelist = parse(fh.read())

    assert linelist.rows, 'no transitions in a window that has them'
    assert linelist.meta['wavelength_medium'] == labelled_medium
    assert linelist.meta['energy_unit'] == ('1/cm' if energy == '1/cm' else 'eV')

    row = dict(zip(linelist.column_names(), linelist.rows[0]))
    assert row['species'] and row['wavelength'] and row['lower_term']
    assert row['lande_mean'] is not None


@pytest.mark.vald_binaries
def test_every_converter_handles_a_real_extraction(vald_home, tmp_path):
    """A full-size extraction through all six writers, checked by row count."""
    from vald.job_runner import JobConfig, JobRunner

    job, ftp = tmp_path / 'job', tmp_path / 'ftp'
    job.mkdir()
    ftp.mkdir()

    flags = [0] * 13
    flags[0] = 1
    flags[11] = 1
    runner = JobRunner()
    runner.ftp_dir = ftp
    ok, result = runner.run(JobConfig(
        job_id=2, client_name='Tester', job_dir=job, request_type='extractall',
        wl_start=5700.0, wl_end=5710.0, format_flags=flags,
        config_path=str(vald_home / 'CONFIG' / 'default.cfg')))
    assert ok, result

    output = next(p for p in ftp.iterdir()
                  if p.suffix == '.gz' and '.bib' not in p.name)
    with gzip.open(output, 'rt', errors='replace') as fh:
        text = fh.read()
    linelist = parse(text)

    # Cross-check the parser's record count against the ASCII itself: one
    # record per line that starts with a quoted species.
    import re
    ascii_records = len([l for l in text.splitlines()
                         if re.match(r"^'[A-Za-z][A-Za-z0-9]*\s+\d+',", l)])
    assert len(linelist.rows) == ascii_records > 10

    for converter in available_converters():
        target = tmp_path / f'real{converter.extension}'
        converter.write(linelist, target)
        assert target.stat().st_size > 0

    conn = sqlite3.connect(tmp_path / 'real.sqlite')
    try:
        assert conn.execute('SELECT count(*) FROM lines').fetchone()[0] == \
            len(linelist.rows)
    finally:
        conn.close()
