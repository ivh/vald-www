"""End-to-end tests against the real Fortran binaries.

Skipped automatically when VALD_HOME has no binaries, so the rest of the suite
still runs on a machine without a VALD installation:

    uv run pytest -m "not vald_binaries"     # skip these explicitly

These are the only tests that can catch a defect where the app writes a control
file the binaries misread - which is how custom abundances ended up being
silently ignored.
"""
import gzip
import re
from pathlib import Path
from typing import NamedTuple

import pytest

from vald.job_runner import JobRunner, JobConfig

pytestmark = pytest.mark.vald_binaries

WL = (5700.0, 5703.0)
# The same window as wavenumbers, for the cm^-1 cases: flag 10 sets the units of
# the requested range too, not just of the output.
WAVENUMBERS = (1e8 / WL[1], 1e8 / WL[0])


class Outcome(NamedTuple):
    ok: bool
    result: str
    text: str
    job_dir: Path
    truncated: bool


@pytest.fixture(scope='session')
def job_results():
    """Cache of finished extractions, keyed by job parameters.

    A run costs ~2 s almost independently of the wavelength window: the fixed
    cost is preselect5 opening the ~200 linelists of default.cfg. Most tests
    below compare a flag against the same unflagged baseline, so without this
    the suite extracts identical output nearly a dozen times.
    """
    return {}


@pytest.fixture
def run_job(tmp_path_factory, vald_home, job_results):
    """Run a JobConfig through the real pipeline, reusing an identical earlier run."""
    def execute(params):
        base = tmp_path_factory.mktemp('job')
        job, ftp = base / 'job', base / 'ftp'
        job.mkdir()
        ftp.mkdir()

        runner = JobRunner()
        runner.ftp_dir = ftp
        config = JobConfig(job_dir=job, **params)
        ok, result = runner.run(config)

        text = ''
        for candidate in ftp.iterdir():
            if candidate.suffix == '.gz' and '.bib' not in candidate.name:
                with gzip.open(candidate, 'rt', errors='replace') as f:
                    text = f.read()
        return Outcome(ok, result, text, job, config.truncated)

    def run(**kwargs):
        params = dict(
            job_id=1, client_name='Tester',
            wl_start=WL[0], wl_end=WL[1],
            config_path=str(vald_home / 'CONFIG' / 'default.cfg'),
        )
        params.update(kwargs)
        key = tuple(sorted((name, repr(value)) for name, value in params.items()))
        if key not in job_results:
            job_results[key] = execute(params)
        return job_results[key]

    return run


def line_depths(text, species='Fe 1'):
    """Central depth per wavelength, from stellar output rows."""
    depths = {}
    for line in text.splitlines():
        match = re.match(rf"\s*'{re.escape(species)}',\s*([\d.]+),.*?,\s*([\d.]+),\s*'",
                         line.strip())
        if match:
            depths[match.group(1)] = match.group(2)
    return depths


def data_rows(text):
    return [l for l in text.splitlines() if re.match(r"^'[A-Za-z]", l.strip())]


def stellar(**overrides):
    """Stellar job parameters; any of them can be overridden."""
    params = dict(request_type='extractstellar', max_lines=0, select_max_lines=500000,
                  depth_limit=0.05, microturbulence=2.0, teff=8000.0, logg=4.5)
    params.update(overrides)
    return params


# --- R28: custom abundances must actually reach select5 --------------------

def test_stellar_extraction_runs(run_job):
    run = run_job(**stellar(abundances=''))
    assert run.ok, run.result
    assert data_rows(run.text), 'no data rows in output'


# --- uploaded model atmospheres --------------------------------------------

@pytest.fixture
def uploaded_model(vald_home):
    """A copy of a grid model at a path select5 can actually hold.

    Not tmp_path: MONAME is a CHARACTER*120 holding the whole path, and
    pytest's temporary directories are long enough on their own to overrun it -
    select5 then truncates the path and reports "Wrong model atmosphere
    fielname". Real job directories are ~40 characters, and
    vald.forms._model_name_budget() trims the filename against that, so this is
    a property of the test environment rather than of the code. Kept short here
    deliberately, since a fixture that silently tested the truncation instead
    would have looked like a bug in the upload path.
    """
    import shutil
    import tempfile

    directory = Path(tempfile.mkdtemp(dir='/tmp', prefix='k'))
    model = directory / 'marcs_p8000_g45_m-0.5.krz'
    model.write_text((vald_home / 'MODELS' / 'STELLAR' / '08000G45.KRZ').read_text())
    assert len(str(model)) <= 120, 'fixture path would overrun MONAME'
    yield model
    shutil.rmtree(directory, ignore_errors=True)


def test_an_uploaded_model_gives_the_same_lines_as_the_grid_model(
        run_job, uploaded_model):
    """A model select5 opens by an absolute path outside MODELS/STELLAR, under a
    name that is not the grid's Teff/log g convention, must behave exactly like
    the grid file it is a copy of. _find_model() is bypassed entirely, so this
    is what proves an upload reaches RDMODL intact."""
    from vald.converters.parser import _is_record_start

    from_grid = run_job(**stellar(abundances=''))
    from_upload = run_job(**stellar(abundances='',
                                    model_path=str(uploaded_model)))

    assert from_upload.ok, from_upload.result

    # _is_record_start rather than data_rows(): the model name is printed into
    # the output as a quoted field of its own, and an uploaded name beginning
    # with a letter sits exactly where data_rows() looks for a species. The
    # parser's species pattern is what tells the two apart, so it is the honest
    # way to ask "which lines are transitions".
    def transitions(text):
        return [l for l in text.splitlines() if _is_record_start(l.strip())]

    assert transitions(from_upload.text), 'no transitions from the uploaded model'
    assert transitions(from_upload.text) == transitions(from_grid.text)


def test_an_uploaded_name_is_not_mistaken_for_a_transition(run_job, uploaded_model):
    """The uploaded name lands in the output where a species field would be.

    Every export goes through converters/parser.py, so if its species pattern
    matched "marcs_p8000_g45_m-0.5.krz" the name would become a row of data in
    the CSV, VOTable and FITS conversions. Grid names begin with a digit and
    could never do this; uploaded ones can, so it is worth pinning.
    """
    from vald.converters import parser

    # Long format, because that is the only one the converters read - and so the
    # only one where the name could reach an exported table.
    run = run_job(**stellar(abundances='', model_path=str(uploaded_model),
                            format_flags=flags(fmt=1, rad=1, stark=1, waals=1,
                                               lande=1, term=1)))
    assert run.ok, run.result

    assert not parser._is_record_start(f"'{uploaded_model.name}',")

    linelist = parser.parse(run.text)
    assert linelist.rows, 'nothing parsed out of the result'
    assert uploaded_model.name in '\n'.join(linelist.meta['trailer'])


def test_the_uploaded_name_reaches_the_result_header(run_job, uploaded_model):
    """select5 prints MONAME's basename into the output (select5.f:2264), and
    converters/parser.py carries that line into every exported format. Once the
    job directory has been swept it is the only record of which atmosphere
    produced a result, which is why the upload keeps its own name."""
    run = run_job(**stellar(abundances='', model_path=str(uploaded_model)))

    assert run.ok, run.result
    assert f"'{uploaded_model.name}'," in run.text


def test_custom_abundances_change_the_result(run_job):
    """The defect: unquoted abundances were skipped by RDABND and solar used.

    Fe at -3.0 is ~1.4 dex above solar, so its lines must get markedly deeper.
    """
    solar = run_job(**stellar(abundances=''))
    enhanced = run_job(**stellar(abundances='Fe: -3.0'))
    assert enhanced.ok, enhanced.result

    solar_depths = line_depths(solar.text)
    enhanced_depths = line_depths(enhanced.text)
    shared = sorted(set(solar_depths) & set(enhanced_depths))
    assert shared, 'no Fe I lines in common to compare'

    changed = [wl for wl in shared if solar_depths[wl] != enhanced_depths[wl]]
    assert changed, 'enhancing Fe by 1.4 dex changed nothing - abundances ignored'
    for wl in changed:
        assert float(enhanced_depths[wl]) > float(solar_depths[wl]), (
            f'Fe I {wl} got weaker when Fe was enhanced')


def test_metallicity_shorthand_is_accepted_by_select(run_job):
    """M/H is a legacy parserequest.c feature; select5 must accept the token."""
    run = run_job(**stellar(abundances='MH: -1.0'))
    assert run.ok, run.result
    assert data_rows(run.text)


# --- select.input line cap -------------------------------------------------

def test_line_cap_truncates_with_a_warning(run_job):
    """MAXLIN>0 truncates and says so; hitting it must not fail the job.

    The upstream stage takes SIGPIPE when select stops early - that used to be
    reported as "preselect5 failed with code -13".
    """
    run = run_job(**stellar(abundances='', select_max_lines=5))
    assert run.ok, run.result
    assert 'truncat' in run.text.lower(), 'no truncation warning in output'


def test_zero_line_cap_means_unlimited(run_job):
    run = run_job(**stellar(abundances='', select_max_lines=0))
    assert run.ok, run.result
    assert 'truncat' not in run.text.lower()


# --- pres_in flag mapping --------------------------------------------------

def flags(fmt=0, rad=0, stark=0, waals=0, lande=0, term=0, ext_vdw=0,
          vacuum=0, waveunit=0, isotopic=1, hfs=0):
    """Build the 13 pres_in flags in job_runner's documented order."""
    return [fmt, rad, stark, waals, lande, term, ext_vdw, 0, 0,
            vacuum, waveunit, isotopic, hfs]


def test_extract_all_runs_and_flags_are_positional(run_job):
    """preselect5 compresses the flag line and reads it character by character,
    so every flag must be a single digit or all later flags shift."""
    run = run_job(request_type='extractall', max_lines=500000,
                  format_flags=flags())
    assert run.ok, run.result
    assert data_rows(run.text)
    written = (run.job_dir / 'pres_in.000001').read_text().splitlines()[4]
    assert all(len(token) == 1 for token in written.split()), (
        f'a flag is not a single character: {written!r}')


def test_vacuum_flag_shifts_wavelengths(run_job):
    """Air and vacuum wavelengths differ by ~1.4 Angstrom at 5700 A."""
    def wavelengths(text):
        out = {}
        for line in data_rows(text):
            m = re.match(r"^'([^']+)',\s*([\d.]+)", line.strip())
            if m:
                out.setdefault(m.group(1), []).append(float(m.group(2)))
        return out

    air = run_job(request_type='extractall', max_lines=500000,
                  format_flags=flags(vacuum=0))
    vac = run_job(request_type='extractall', max_lines=500000,
                  format_flags=flags(vacuum=1))
    assert vac.ok, vac.result

    air_wl, vac_wl = wavelengths(air.text), wavelengths(vac.text)
    species = sorted(set(air_wl) & set(vac_wl))
    assert species, 'no species in common'
    # compare the smallest wavelength of a shared species
    first = species[0]
    shift = min(vac_wl[first]) - min(air_wl[first])
    assert 1.0 < shift < 2.5, f'air->vacuum shift was {shift:.4f} A, expected ~1.6'


def test_medium_flag_is_inert_under_wavenumber_output(run_job):
    """preselect5 ignores flag 9 whenever flag 10 says cm^-1.

    The two are independent positions in pres_in and parserequest.c parsed them
    into unrelated variables, so nothing upstream couples them - but the binary
    hard-codes cm^-1 to vacuum wavenumbers, which leaves the medium choice with
    nothing to do. The unit selectors rely on this: under cm^-1 they disable the
    medium control rather than converting or rejecting anything.
    """
    air = run_job(request_type='extractall', max_lines=500000,
                  wl_start=WAVENUMBERS[0], wl_end=WAVENUMBERS[1],
                  format_flags=flags(vacuum=0, waveunit=2))
    vac = run_job(request_type='extractall', max_lines=500000,
                  wl_start=WAVENUMBERS[0], wl_end=WAVENUMBERS[1],
                  format_flags=flags(vacuum=1, waveunit=2))
    assert vac.ok, vac.result
    assert data_rows(air.text), 'no data rows, so identical output proves nothing'
    assert air.text == vac.text, 'the medium flag changed cm^-1 output'


def test_wavenumber_output_is_vacuum_wavenumbers(run_job):
    """Which medium cm^-1 is hard-coded to, i.e. why the flag above is inert.

    sigma = 1e8 / lambda_vac, so the same window requested as cm^-1 must return
    the vacuum line set and not the air one - the two differ by ~1% in row count
    here, which is what makes this a real check.
    """
    air = run_job(request_type='extractall', max_lines=500000,
                  format_flags=flags(vacuum=0))
    vac = run_job(request_type='extractall', max_lines=500000,
                  format_flags=flags(vacuum=1))
    cm = run_job(request_type='extractall', max_lines=500000,
                 wl_start=WAVENUMBERS[0], wl_end=WAVENUMBERS[1],
                 format_flags=flags(waveunit=2))
    assert cm.ok, cm.result

    air_rows, vac_rows, cm_rows = data_rows(air.text), data_rows(vac.text), data_rows(cm.text)
    assert len(air_rows) != len(vac_rows), 'air and vacuum agree, so this cannot discriminate'
    assert len(cm_rows) == len(vac_rows), (
        f'cm^-1 returned {len(cm_rows)} rows, vacuum {len(vac_rows)}, air {len(air_rows)}')

    def leading(rows):
        return sorted(float(re.match(r"^'[^']+',\s*([\d.eE+-]+)", r.strip()).group(1))
                      for r in rows)

    # 1e8/sigma must reproduce the vacuum wavelengths to printed precision
    back = sorted(1e8 / sigma for sigma in leading(cm_rows))
    worst = max(abs(b - w) for b, w in zip(back, leading(vac_rows)))
    assert worst < 1e-3, f'1e8/sigma differs from lambda_vac by up to {worst:.2e} A'


def test_energy_unit_flag_switches_excitation_scale(run_job):
    """eV vs cm^-1 differ by the ~8065.5 conversion factor."""
    def first_excitation(text):
        for line in data_rows(text):
            parts = [p.strip() for p in line.split(',')]
            if len(parts) > 2:
                try:
                    return float(parts[2])
                except ValueError:
                    continue
        return None

    ev = run_job(request_type='extractall', max_lines=500000,
                 format_flags=flags(fmt=0))
    cm = run_job(request_type='extractall', max_lines=500000,
                 format_flags=flags(fmt=3))
    assert cm.ok, cm.result

    ratio = first_excitation(cm.text) / first_excitation(ev.text)
    assert 7500 < ratio < 8600, f'eV->cm^-1 ratio was {ratio:.1f}, expected ~8065'


@pytest.mark.parametrize('flag_name', ['stark', 'waals', 'lande'])
def test_have_flags_restrict_the_line_list(run_job, flag_name):
    """"Have X" keeps only lines carrying that parameter, so output must shrink."""
    unfiltered = run_job(request_type='extractall', max_lines=500000,
                         format_flags=flags())
    filtered = run_job(request_type='extractall', max_lines=500000,
                       format_flags=flags(**{flag_name: 1}))
    assert filtered.ok, filtered.result
    assert len(data_rows(filtered.text)) < len(data_rows(unfiltered.text)), (
        f'have_{flag_name} did not restrict the output')


def test_isotopic_scaling_flag_changes_output(run_job):
    on = run_job(request_type='extractall', max_lines=500000,
                 format_flags=flags(isotopic=1))
    off = run_job(request_type='extractall', max_lines=500000,
                  format_flags=flags(isotopic=0))
    assert off.ok, off.result
    assert on.text != off.text, 'isotopic scaling flag had no effect'


def test_extended_vdw_flag_changes_output(run_job):
    default = run_job(request_type='extractall', max_lines=500000,
                      format_flags=flags(ext_vdw=0))
    extended = run_job(request_type='extractall', max_lines=500000,
                       format_flags=flags(ext_vdw=1))
    assert extended.ok, extended.result
    assert extended.text != default.text, 'extended van der Waals flag had no effect'


def test_long_format_wavenumbers_run(run_job):
    """presformat5 FORMAT 301 (long format + cm^-1 energy + default vdW) had a
    missing comma between two repeat groups. Harmless under -std=legacy, it
    became a hard runtime error once presformat5 dropped that flag - so any
    long/cm^-1 extract crashed. fmt=4 is exactly that branch."""
    run = run_job(request_type='extractall', max_lines=500000,
                  format_flags=flags(fmt=4))
    assert run.ok, run.result
    assert data_rows(run.text), 'no lines formatted on the long/cm^-1 path'


# --- HFS ------------------------------------------------------------------

def test_hfs_works_for_an_element_filtered_request(run_job):
    """HFS is usable when a species is named. See finds.md R36 for Extract All."""
    run = run_job(request_type='extractelement', element='Mn 1',
                  max_lines=500000, wl_start=4030.0, wl_end=4031.0,
                  format_flags=flags(hfs=1))
    assert run.ok, run.result
    assert data_rows(run.text)


def test_hfs_works_for_extract_all(run_job):
    """Unfiltered HFS extraction. Region-dependent on some installations - see
    finds.md R36 - so this uses a range known to work rather than asserting that
    every range does."""
    run = run_job(request_type='extractall', max_lines=500000,
                  wl_start=15000.0, wl_end=15000.5,
                  format_flags=flags(hfs=1))
    assert run.ok, run.result
    assert data_rows(run.text)


@pytest.mark.xfail(reason='finds.md R36: on some installations the HFS chain fails '
                          'for particular wavelength regions. Reproduces with the '
                          'legacy shell pipeline too, so it is not an app defect. '
                          'XPASS here means the installation is healthy.',
                   strict=False)
def test_hfs_extract_all_in_the_optical(run_job):
    run = run_job(request_type='extractall', max_lines=500000,
                  wl_start=5000.0, wl_end=5001.0,
                  format_flags=flags(hfs=1))
    assert run.ok, run.result


# --- the line cap must be visible to the app, not silent -------------------

def test_extract_truncation_is_reported(run_job):
    """preselect5 stops at the pres_in cap with rc=0; only its stderr says so."""
    run = run_job(request_type='extractall', max_lines=5,
                  format_flags=[0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0])
    assert run.ok, run.result
    assert len(data_rows(run.text)) == 5
    assert run.truncated
    assert 'VALD-TRUNCATED' in (run.job_dir / 'preselect5.err').read_text()


def test_extract_within_the_cap_is_not_reported_as_truncated(run_job):
    run = run_job(request_type='extractall', max_lines=500000,
                  format_flags=[0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0])
    assert run.ok, run.result
    assert not run.truncated


def test_stellar_truncation_is_reported(run_job):
    """select5 announces the cap in the output file rather than on stderr."""
    run = run_job(**stellar(abundances='', select_max_lines=2))
    assert run.ok, run.result
    assert run.truncated
    assert 'WARNING: Output was truncated to' in run.text


def test_stellar_within_the_cap_is_not_reported_as_truncated(run_job):
    run = run_job(**stellar(abundances=''))
    assert run.ok, run.result
    assert not run.truncated
