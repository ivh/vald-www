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

import pytest

from vald.job_runner import JobRunner, JobConfig

pytestmark = pytest.mark.vald_binaries

WL = (5700.0, 5703.0)
# The same window as wavenumbers, for the cm^-1 cases: flag 10 sets the units of
# the requested range too, not just of the output.
WAVENUMBERS = (1e8 / WL[1], 1e8 / WL[0])


@pytest.fixture
def run_job(tmp_path, vald_home):
    """Run a JobConfig through the real pipeline and return (ok, result, output text)."""
    counter = {'n': 0}

    def run(**kwargs):
        counter['n'] += 1
        job = tmp_path / f'job{counter["n"]}'
        ftp = tmp_path / f'ftp{counter["n"]}'
        job.mkdir()
        ftp.mkdir()

        runner = JobRunner()
        runner.ftp_dir = ftp

        params = dict(
            job_id=1, job_dir=job, client_name='Tester',
            wl_start=WL[0], wl_end=WL[1],
            config_path=str(vald_home / 'CONFIG' / 'default.cfg'),
        )
        params.update(kwargs)
        ok, result = runner.run(JobConfig(**params))

        text = ''
        for candidate in ftp.iterdir():
            if candidate.suffix == '.gz' and '.bib' not in candidate.name:
                with gzip.open(candidate, 'rt', errors='replace') as f:
                    text = f.read()
        return ok, result, text

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
    ok, result, text = run_job(**stellar(abundances=''))
    assert ok, result
    assert data_rows(text), 'no data rows in output'


def test_custom_abundances_change_the_result(run_job):
    """The defect: unquoted abundances were skipped by RDABND and solar used.

    Fe at -3.0 is ~1.4 dex above solar, so its lines must get markedly deeper.
    """
    _, _, solar_text = run_job(**stellar(abundances=''))
    ok, result, enhanced_text = run_job(**stellar(abundances='Fe: -3.0'))
    assert ok, result

    solar_depths = line_depths(solar_text)
    enhanced_depths = line_depths(enhanced_text)
    shared = sorted(set(solar_depths) & set(enhanced_depths))
    assert shared, 'no Fe I lines in common to compare'

    changed = [wl for wl in shared if solar_depths[wl] != enhanced_depths[wl]]
    assert changed, 'enhancing Fe by 1.4 dex changed nothing - abundances ignored'
    for wl in changed:
        assert float(enhanced_depths[wl]) > float(solar_depths[wl]), (
            f'Fe I {wl} got weaker when Fe was enhanced')


def test_metallicity_shorthand_is_accepted_by_select(run_job):
    """M/H is a legacy parserequest.c feature; select5 must accept the token."""
    ok, result, text = run_job(**stellar(abundances='MH: -1.0'))
    assert ok, result
    assert data_rows(text)


# --- select.input line cap -------------------------------------------------

def test_line_cap_truncates_with_a_warning(run_job):
    """MAXLIN>0 truncates and says so; hitting it must not fail the job.

    The upstream stage takes SIGPIPE when select stops early - that used to be
    reported as "preselect5 failed with code -13".
    """
    ok, result, text = run_job(**stellar(abundances='', select_max_lines=5))
    assert ok, result
    assert 'truncat' in text.lower(), 'no truncation warning in output'


def test_zero_line_cap_means_unlimited(run_job):
    ok, result, text = run_job(**stellar(abundances='', select_max_lines=0))
    assert ok, result
    assert 'truncat' not in text.lower()


# --- pres_in flag mapping --------------------------------------------------

def flags(fmt=0, rad=0, stark=0, waals=0, lande=0, term=0, ext_vdw=0,
          vacuum=0, waveunit=0, isotopic=1, hfs=0):
    """Build the 13 pres_in flags in job_runner's documented order."""
    return [fmt, rad, stark, waals, lande, term, ext_vdw, 0, 0,
            vacuum, waveunit, isotopic, hfs]


def test_extract_all_runs_and_flags_are_positional(run_job, tmp_path):
    """preselect5 compresses the flag line and reads it character by character,
    so every flag must be a single digit or all later flags shift."""
    ok, result, text = run_job(request_type='extractall', max_lines=500000,
                               format_flags=flags())
    assert ok, result
    assert data_rows(text)
    written = next(tmp_path.glob('job*/pres_in.000001')).read_text().splitlines()[4]
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

    _, _, air = run_job(request_type='extractall', max_lines=500000,
                        format_flags=flags(vacuum=0))
    ok, result, vac = run_job(request_type='extractall', max_lines=500000,
                              format_flags=flags(vacuum=1))
    assert ok, result

    air_wl, vac_wl = wavelengths(air), wavelengths(vac)
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
    _, _, air = run_job(request_type='extractall', max_lines=500000,
                        wl_start=WAVENUMBERS[0], wl_end=WAVENUMBERS[1],
                        format_flags=flags(vacuum=0, waveunit=2))
    ok, result, vac = run_job(request_type='extractall', max_lines=500000,
                              wl_start=WAVENUMBERS[0], wl_end=WAVENUMBERS[1],
                              format_flags=flags(vacuum=1, waveunit=2))
    assert ok, result
    assert data_rows(air), 'no data rows, so identical output proves nothing'
    assert air == vac, 'the medium flag changed cm^-1 output'


def test_wavenumber_output_is_vacuum_wavenumbers(run_job):
    """Which medium cm^-1 is hard-coded to, i.e. why the flag above is inert.

    sigma = 1e8 / lambda_vac, so the same window requested as cm^-1 must return
    the vacuum line set and not the air one - the two differ by ~1% in row count
    here, which is what makes this a real check.
    """
    _, _, air = run_job(request_type='extractall', max_lines=500000,
                        format_flags=flags(vacuum=0))
    _, _, vac = run_job(request_type='extractall', max_lines=500000,
                        format_flags=flags(vacuum=1))
    ok, result, cm = run_job(request_type='extractall', max_lines=500000,
                             wl_start=WAVENUMBERS[0], wl_end=WAVENUMBERS[1],
                             format_flags=flags(waveunit=2))
    assert ok, result

    air_rows, vac_rows, cm_rows = data_rows(air), data_rows(vac), data_rows(cm)
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

    _, _, ev = run_job(request_type='extractall', max_lines=500000,
                       format_flags=flags(fmt=0))
    ok, result, cm = run_job(request_type='extractall', max_lines=500000,
                             format_flags=flags(fmt=3))
    assert ok, result

    ratio = first_excitation(cm) / first_excitation(ev)
    assert 7500 < ratio < 8600, f'eV->cm^-1 ratio was {ratio:.1f}, expected ~8065'


@pytest.mark.parametrize('flag_name', ['stark', 'waals', 'lande'])
def test_have_flags_restrict_the_line_list(run_job, flag_name):
    """"Have X" keeps only lines carrying that parameter, so output must shrink."""
    _, _, unfiltered = run_job(request_type='extractall', max_lines=500000,
                               format_flags=flags())
    ok, result, filtered = run_job(request_type='extractall', max_lines=500000,
                                   format_flags=flags(**{flag_name: 1}))
    assert ok, result
    assert len(data_rows(filtered)) < len(data_rows(unfiltered)), (
        f'have_{flag_name} did not restrict the output')


def test_isotopic_scaling_flag_changes_output(run_job):
    _, _, on = run_job(request_type='extractall', max_lines=500000,
                       format_flags=flags(isotopic=1))
    ok, result, off = run_job(request_type='extractall', max_lines=500000,
                              format_flags=flags(isotopic=0))
    assert ok, result
    assert on != off, 'isotopic scaling flag had no effect'


def test_extended_vdw_flag_changes_output(run_job):
    _, _, default = run_job(request_type='extractall', max_lines=500000,
                            format_flags=flags(ext_vdw=0))
    ok, result, extended = run_job(request_type='extractall', max_lines=500000,
                                   format_flags=flags(ext_vdw=1))
    assert ok, result
    assert extended != default, 'extended van der Waals flag had no effect'


# --- HFS ------------------------------------------------------------------

def test_hfs_works_for_an_element_filtered_request(run_job):
    """HFS is usable when a species is named. See finds.md R36 for Extract All."""
    ok, result, text = run_job(request_type='extractelement', element='Mn 1',
                               max_lines=500000, wl_start=4030.0, wl_end=4031.0,
                               format_flags=flags(hfs=1))
    assert ok, result
    assert data_rows(text)


def test_hfs_works_for_extract_all(run_job):
    """Unfiltered HFS extraction. Region-dependent on some installations - see
    finds.md R36 - so this uses a range known to work rather than asserting that
    every range does."""
    ok, result, text = run_job(request_type='extractall', max_lines=500000,
                               wl_start=15000.0, wl_end=15000.5,
                               format_flags=flags(hfs=1))
    assert ok, result
    assert data_rows(text)


@pytest.mark.xfail(reason='finds.md R36: on some installations the HFS chain fails '
                          'for particular wavelength regions. Reproduces with the '
                          'legacy shell pipeline too, so it is not an app defect. '
                          'XPASS here means the installation is healthy.',
                   strict=False)
def test_hfs_extract_all_in_the_optical(run_job):
    ok, result, _ = run_job(request_type='extractall', max_lines=500000,
                            wl_start=5000.0, wl_end=5001.0,
                            format_flags=flags(hfs=1))
    assert ok, result
