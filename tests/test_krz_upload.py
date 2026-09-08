"""Uploaded model atmospheres: validation, and the life of the uploaded file.

The design being tested is that an upload is never stored anywhere but the job
directory, so it survives exactly as long as the results it produced and a
re-run past that fails loudly instead of substituting a grid model. See
get_model_path_for_request().
"""
import io
import re
from pathlib import Path

import pytest

from vald import krz
from vald.models import Request


# A minimal but genuine krz model: two header lines, the opacity switches, 99
# abundances plus the layer count, then one row per layer. Built rather than
# copied from $VALD_HOME so the tests run without a VALD installation.
def make_krz(layers=3, teff=5750.0, logg=4.5, model_type=0,
             title='TITLE  [0.0] VTURB=2  L/H=1.25 NOVER NEW ODF',
             abundances=None):
    if abundances is None:
        # Hydrogen as a number fraction, everything after it as log10 of one.
        abundances = [0.920, -1.11] + [-20.00] * 97
    values = [f'{value:7.2f}' for value in abundances]
    rows = [
        f'{7.9e-3 * (i + 1):.9E}, {2081.2 + i:8.1f}, 1.56400E+05, '
        f'2.73563E+10, 5.73352E-14,'
        for i in range(layers)
    ]
    return '\n'.join([
        title,
        f'T EFF= {teff:.0f}. GRAV= {logg}  MODEL TYPE= {model_type} WLSTD= 5000.',
        ' 1 1 1 1 1 1 1 1 1 1 1 1 1 0 1 0 0 0 0 0 - OPACITY SWITCHES',
        *[''.join(values[i:i + 10]) for i in range(0, 90, 10)],
        ''.join(values[90:]) + f'{layers:3d}',
        *rows,
    ]) + '\n'


STELLAR = {
    'reqtype': 'extractstellar', 'stwvl': '4400', 'endwvl': '4402',
    'dlimit': '0.01', 'micturb': '1.5', 'format': 'long', 'pconf': 'default',
}


@pytest.fixture
def default_config(db):
    """create_job_config writes the request's .cfg before anything else, and
    refuses to run without one."""
    from vald.models import Config
    return Config.objects.create(name='Default', user=None, is_default=True)


def upload(text, name='marcs_p5750_g45.krz'):
    return io.BytesIO(text.encode()), name


def post_stellar(client, text=None, **overrides):
    data = dict(STELLAR, **overrides)
    if text is not None:
        content, name = upload(text)
        content.name = name
        data['model_file'] = content
    return client.post('/submit/', data)


# ---------------------------------------------------------------------------
# The validator, against what RDMODL can and cannot read
# ---------------------------------------------------------------------------

def test_a_wellformed_model_is_accepted():
    model = krz.parse(make_krz(layers=40, teff=4250.0, logg=1.5))
    assert (model.teff, model.logg, model.layers) == (4250.0, 1.5, 40)
    assert model.model_type == 0


def test_the_real_grid_is_accepted(vald_home):
    """Whatever this rejects must be something select5 cannot read, so the
    3800-odd models select5 reads every day are the floor for that."""
    models = sorted((vald_home / 'MODELS' / 'STELLAR').iterdir())
    models = [p for p in models if p.suffix.lower() == '.krz']
    assert len(models) > 100, 'grid not populated; nothing was checked'
    for path in models:
        krz.parse(path.read_text(errors='replace'))


def test_spherical_models_are_rejected_by_name():
    """RDMODL stops on MODEL TYPE=3, which is most of the MARCS grid."""
    with pytest.raises(krz.KrzError, match='spherical'):
        krz.parse(make_krz(model_type=3))


@pytest.mark.parametrize('missing', ['T EFF=', 'GRAV', 'MODEL TYPE=', 'WLSTD='])
def test_each_header_keyword_is_required(missing):
    """RDMODL finds every value with INDEX() on these, so a missing one is a
    backtrace rather than a diagnostic."""
    text = make_krz()
    lines = text.splitlines()
    lines[1] = lines[1].replace(missing, 'XXXX=')
    with pytest.raises(krz.KrzError, match='[Ll]ine 2'):
        krz.parse('\n'.join(lines))


def test_header_keywords_must_be_in_order():
    lines = make_krz().splitlines()
    lines[1] = 'GRAV= 4.5 T EFF= 5750. MODEL TYPE= 0 WLSTD= 5000.'
    with pytest.raises(krz.KrzError, match='out of order'):
        krz.parse('\n'.join(lines))


def test_short_opacity_switch_line_is_rejected():
    lines = make_krz().splitlines()
    lines[2] = ' 1 1 1 1 1 1 1 1 1 1'
    with pytest.raises(krz.KrzError, match='opacity switches'):
        krz.parse('\n'.join(lines))


@pytest.mark.parametrize('layers', [2, 101])
def test_layer_count_outside_what_select5_holds_is_rejected(layers):
    """SIZES.SEL gives room for 100 layers; RDMODL wants at least 3."""
    with pytest.raises(krz.KrzError, match='depth points'):
        krz.parse(make_krz(layers=layers))


def test_missing_depth_rows_are_rejected():
    text = make_krz(layers=10)
    truncated = '\n'.join(text.splitlines()[:-4])
    with pytest.raises(krz.KrzError, match='only 6 rows'):
        krz.parse(truncated)


def test_a_dex_scale_abundance_block_is_rejected():
    """The failure this exists for: select5 decides what scale a value is on by
    its sign, so an A(X)=12 file is read as number fractions and quietly gives
    wrong answers instead of an error."""
    dex = [0.920] + [value + 12.04 for value in [-1.11] + [-20.00] * 97]
    with pytest.raises(krz.KrzError, match='log10'):
        krz.parse(make_krz(abundances=dex))


def test_helium_may_be_a_fraction_or_a_log():
    """Both conventions occur in real files and select5 reads both.

    A MARCS model writes helium as 0.078 and the ATLAS9 grid writes -1.110,
    which are the same abundance - select5 decides per value by its sign
    (select5.f:1232). Requiring one of them rejected a genuine MARCS model.
    """
    as_log = krz.parse(make_krz(abundances=[0.921, -1.110] + [-20.00] * 97))
    as_fraction = krz.parse(make_krz(abundances=[0.921, 0.078] + [-20.00] * 97))

    assert as_log.layers == as_fraction.layers


def test_zero_abundances_are_rejected():
    """LOG10 is taken of these again when the result header is written."""
    with pytest.raises(krz.KrzError, match='-Infinity'):
        krz.parse(make_krz(abundances=[0.920, -1.11] + [0.0] * 97))


def test_a_nonfraction_hydrogen_abundance_is_rejected():
    with pytest.raises(krz.KrzError, match='hydrogen'):
        krz.parse(make_krz(abundances=[12.0, -1.11] + [-20.00] * 97))


# ---------------------------------------------------------------------------
# Hostile input: everything here must be one KrzError, never a traceback
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('label, mutate', [
    ('inf teff', lambda t: t.replace('T EFF= 5750.', 'T EFF= inf')),
    ('nan teff', lambda t: t.replace('T EFF= 5750.', 'T EFF= nan')),
    ('overflowing teff', lambda t: t.replace('T EFF= 5750.', 'T EFF= 1e400')),
    ('inf model type', lambda t: t.replace('MODEL TYPE= 0', 'MODEL TYPE= inf')),
    ('nan abundance', lambda t: t.replace(' -20.00', '    nan', 1)),
    ('-inf abundance', lambda t: t.replace(' -20.00', '   -inf', 1)),
    ('nul byte', lambda t: t.replace('TITLE', 'TIT\x00LE')),
    ('one enormous line', lambda t: 'TITLE\n' + 'x' * 400_000),
    ('nothing at all', lambda t: ''),
    ('overflowing depth row',
     lambda t: '\n'.join(t.splitlines()[:13] + ['1e400, 1e400, 1e400, 1e400, 1e400,'])),
])
def test_hostile_input_is_a_message_not_a_traceback(label, mutate):
    """float() accepts 'nan', 'inf' and '1e400', and gfortran's list-directed
    READ accepts the first two - so before _numbers() required finiteness these
    reached int() as an OverflowError, or passed every range check by being
    unorderable, and the user got an HTTP 500 or a silently wrong extraction.
    """
    with pytest.raises(krz.KrzError):
        krz.parse(mutate(make_krz()))


@pytest.mark.parametrize('label, mutate', [
    # RDMODL reads the parameter line into a CHARACTER*256 and then hunts for
    # the keywords with INDEX(), so a keyword pushed past column 256 is gone
    # before it looks. Verified against select5: "End of file" at line 1454.
    ('keyword past column 256',
     lambda t: '\n'.join([t.splitlines()[0], 'PAD ' + 'Z' * 260 + '  '
                          + t.splitlines()[1]] + t.splitlines()[2:])),
    # MOTYPE, IFOP and NRHOX are INTEGER; a list-directed READ into one refuses
    # a decimal point or an exponent outright - "Bad integer for item N in list
    # input" - where float()/int() would silently round.
    ('fractional model type', lambda t: t.replace('MODEL TYPE= 0', 'MODEL TYPE= 0.5')),
    ('model type with a trailing dot', lambda t: t.replace('MODEL TYPE= 0', 'MODEL TYPE= 0.')),
    ('fractional opacity switch', lambda t: t.replace(' 1 1', ' 1.5 1', 1)),
    ('depth count with a trailing dot',
     lambda t: '\n'.join(t.splitlines()[:12] + [t.splitlines()[12][:-2] + ' 72.']
                         + t.splitlines()[13:])),
    ('depth count in exponent form',
     lambda t: '\n'.join(t.splitlines()[:12] + [t.splitlines()[12][:-2] + '3.0E0']
                         + t.splitlines()[13:])),
    # T, XNE, XNA and RHO are plain REAL. A value finite in Python's double but
    # past the single-precision range reads in as Infinity, and select5 then
    # neither converges nor fails - the job holds a queue slot until it times
    # out. Observed: still iterating after several minutes.
    ('single-precision overflow in T',
     lambda t: t.replace('   2081.2,', '     1E39,', 1)),
])
def test_input_select5_rejects_is_rejected_here_too(label, mutate):
    """Every case here was checked against the binary: select5 fails or hangs
    on it, so accepting it would spend a queue slot to find that out."""
    with pytest.raises(krz.KrzError):
        krz.parse(mutate(make_krz()))


@pytest.mark.parametrize('label, mutate', [
    ('CRLF line endings', lambda t: t.replace('\n', '\r\n')),
    # The title line is read and discarded, so its length is never a problem,
    # and padding the parameter line with trailing spaces leaves the keywords
    # where INDEX() can still reach them. Both confirmed against select5.
    ('a 400-character title',
     lambda t: '\n'.join(['TITLE ' + 'Z' * 400] + t.splitlines()[1:])),
    ('parameter line padded past 256',
     lambda t: '\n'.join([t.splitlines()[0], t.splitlines()[1] + ' ' * 200]
                         + t.splitlines()[2:])),
    # RHOX is the one column select5 keeps in double precision, so the value
    # that overflows T is fine here.
    ('large RHOX, which is double precision',
     lambda t: t.replace('7.888140720E-03,', '1E39,', 1)),
    ('non-ASCII in the title', lambda t: t.replace('TITLE', 'TITLE café \U0001f600')),
    ('junk after the last depth row', lambda t: t + '\n' * 50_000),
])
def test_harmless_oddities_are_still_accepted(label, mutate):
    """The counterpart: rejecting these would refuse files select5 reads. The
    title is free text it never looks at, and it stops after NRHOX rows.
    """
    assert krz.parse(mutate(make_krz())).teff == 5750.0


# ---------------------------------------------------------------------------
# The filename, which reaches Fortran and the result header
# ---------------------------------------------------------------------------

def test_the_uploaded_name_survives_sanitising():
    assert krz.safe_filename('marcs_p5750_g4.5_m-0.5.krz', 60) == \
        'marcs_p5750_g4.5_m-0.5.krz'


@pytest.mark.parametrize('name, expected', [
    ('../../../etc/passwd', 'passwd.krz'),
    ("quote'inject.krz", 'quote_inject.krz'),
    ('has space.krz', 'has_space.krz'),
    ('', 'model.krz'),
])
def test_dangerous_names_are_neutralised(name, expected):
    """The name is written to disk and quoted into a Fortran literal in
    select.input, so a directory separator or an apostrophe must not survive."""
    assert krz.safe_filename(name, 60) == expected


def test_the_name_is_kept_inside_the_moname_budget():
    """select5 holds the whole path in a CHARACTER*120."""
    name = krz.safe_filename('x' * 300 + '.krz', 40)
    assert len(name) == 40 and name.endswith('.krz')


def test_the_budget_leaves_the_whole_path_inside_moname(settings):
    """The name is trimmed against the deployment's own working directory,
    since that is the part of the path select5 has to hold as well."""
    from vald.forms import _model_name_budget

    settings.VALD_WORKING_DIR = Path('/home/vald/vald-www.git/working')
    name = krz.safe_filename('x' * 300 + '.krz', _model_name_budget())

    longest = settings.VALD_WORKING_DIR / '000000' / name
    assert len(str(longest)) <= krz.MONAME_MAX


# ---------------------------------------------------------------------------
# Submission: what is stored, and what is not
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_upload_supplies_teff_and_logg(logged_in_client, no_background_worker):
    """select5 reads them from the model header whatever the form said, so the
    stored request has to agree with the file rather than with the fields."""
    post_stellar(logged_in_client, make_krz(teff=4250.0, logg=1.5),
                 teff='9999', logg='0.0')

    req = Request.objects.latest('created_at')
    assert req.parameters['teff'] == 4250.0
    assert req.parameters['logg'] == 1.5
    assert req.parameters['model_name'] == 'marcs_p5750_g45.krz'


@pytest.mark.django_db
def test_teff_and_logg_may_be_left_empty_when_a_model_is_uploaded(
        logged_in_client, no_background_worker):
    """The other half of the contract: with a model attached the two fields are
    not just overridden, they need not be filled in at all - there is nothing
    for them to select, and the header supplies both."""
    resp = post_stellar(logged_in_client, make_krz(teff=4250.0, logg=1.5))

    assert resp.status_code == 302, 'submission was rejected, not accepted'
    req = Request.objects.latest('created_at')
    assert (req.parameters['teff'], req.parameters['logg']) == (4250.0, 1.5)


@pytest.mark.django_db
def test_the_teff_and_logg_widgets_do_not_demand_a_value(logged_in_client):
    """A leftover HTML `required` would stop the browser before the POST that
    conditional validation is meant to allow."""
    resp = logged_in_client.get('/extractstellar/')
    html = resp.content.decode()

    for field in ('id_teff', 'id_logg'):
        tag = re.search(rf'<input[^>]*id="{field}"[^>]*>', html)
        assert tag, f'{field} not rendered'
        assert 'required' not in tag.group(0), f'{field} still demands a value'


@pytest.mark.django_db
def test_the_model_content_is_not_stored_on_the_request(logged_in_client,
                                                        no_background_worker):
    """The whole design: parameters carries the name, never the file."""
    post_stellar(logged_in_client, make_krz())

    req = Request.objects.latest('created_at')
    assert 'model_file' not in req.parameters
    assert 'T EFF=' not in str(req.parameters)


@pytest.mark.django_db
def test_teff_and_logg_are_still_required_without_an_upload(logged_in_client,
                                                            no_background_worker):
    resp = post_stellar(logged_in_client)
    assert resp.status_code == 200          # redisplayed, not submitted
    assert not Request.objects.exists()
    assert 'required unless you upload' in resp.content.decode()


@pytest.mark.django_db
def test_a_rejected_model_names_the_problem_on_the_form(logged_in_client,
                                                        no_background_worker):
    resp = post_stellar(logged_in_client, make_krz(model_type=3),
                        teff='5750', logg='4.5')
    assert resp.status_code == 200
    assert not Request.objects.exists()
    assert 'spherical' in resp.content.decode()


@pytest.mark.django_db
def test_an_oversized_upload_is_refused(logged_in_client, no_background_worker):
    resp = post_stellar(logged_in_client, 'x' * (600 * 1024),
                        teff='5750', logg='4.5')
    assert resp.status_code == 200
    assert not Request.objects.exists()


# ---------------------------------------------------------------------------
# The file's life in the job directory
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_the_model_is_written_into_the_job_directory(tmp_path, approved_user, default_config,
                                                     settings):
    """It lands where select.input will name it, under the uploaded name."""
    from vald.job_runner import create_job_config

    settings.VALD_WORKING_DIR = tmp_path
    job_dir = tmp_path / '000123'
    job_dir.mkdir()
    text = make_krz()
    req = Request.objects.create(
        user=approved_user, request_type='extractstellar', status='pending',
        parameters=dict(STELLAR, teff=5750.0, logg=4.5,
                        model_name='marcs_p5750_g45.krz'))

    config = create_job_config(req, 123, job_dir, 'TestUser', krz_content=text)

    written = job_dir / 'marcs_p5750_g45.krz'
    assert written.exists()
    assert written.read_text() == text
    assert config.model_path == str(written)


@pytest.mark.django_db
def test_select_input_points_at_the_uploaded_model(tmp_path, approved_user,
                                                   settings, default_config):
    """The path select5 opens, and the name it prints into the result header."""
    from vald.job_runner import JobRunner, create_job_config

    settings.VALD_WORKING_DIR = tmp_path
    job_dir = tmp_path / '000123'
    job_dir.mkdir()
    req = Request.objects.create(
        user=approved_user, request_type='extractstellar', status='pending',
        parameters=dict(STELLAR, teff=5750.0, logg=4.5,
                        model_name='marcs_p5750_g45.krz'))

    config = create_job_config(req, 123, job_dir, 'TestUser',
                               krz_content=make_krz())
    text = JobRunner()._select_input_text(config)

    assert f"'{job_dir / 'marcs_p5750_g45.krz'}'" in text


@pytest.mark.django_db
def test_a_rerun_uses_the_file_left_in_the_job_directory(tmp_path, approved_user,
                                                         settings, default_config):
    """Which is what makes the admin's rerun work without storing the upload."""
    from vald.job_runner import create_job_config

    settings.VALD_WORKING_DIR = tmp_path
    job_dir = tmp_path / '000123'
    job_dir.mkdir()
    req = Request.objects.create(
        user=approved_user, request_type='extractstellar', status='pending',
        parameters=dict(STELLAR, teff=5750.0, logg=4.5,
                        model_name='marcs_p5750_g45.krz'))
    create_job_config(req, 123, job_dir, 'TestUser', krz_content=make_krz())

    # No content this time, exactly as a rerun arrives.
    config = create_job_config(req, 123, job_dir, 'TestUser')

    assert config.model_path == str(job_dir / 'marcs_p5750_g45.krz')


@pytest.mark.django_db
def test_a_rerun_after_cleanup_fails_rather_than_using_a_grid_model(
        tmp_path, approved_user, settings, default_config):
    """The point of the whole arrangement: once cleanup_old_results has taken
    the job directory, the request must fail and say to upload again. Falling
    back to the nearest grid model would answer a different question under the
    same request id."""
    from vald.job_runner import create_job_config

    settings.VALD_WORKING_DIR = tmp_path
    job_dir = tmp_path / '000123'
    job_dir.mkdir()
    req = Request.objects.create(
        user=approved_user, request_type='extractstellar', status='pending',
        parameters=dict(STELLAR, teff=5750.0, logg=4.5,
                        model_name='marcs_p5750_g45.krz'))

    with pytest.raises(ValueError, match='been deleted'):
        create_job_config(req, 123, job_dir, 'TestUser')


@pytest.mark.django_db
def test_a_request_without_an_upload_still_uses_the_grid(tmp_path, approved_user,
                                                         settings, monkeypatch,
                                                         default_config):
    from vald.job_runner import JobRunner, create_job_config

    settings.VALD_WORKING_DIR = tmp_path
    job_dir = tmp_path / '000123'
    job_dir.mkdir()
    req = Request.objects.create(
        user=approved_user, request_type='extractstellar', status='pending',
        parameters=dict(STELLAR, teff=5750.0, logg=4.5))

    config = create_job_config(req, 123, job_dir, 'TestUser')
    assert config.model_path == ''

    monkeypatch.setattr(JobRunner, '_find_model',
                        lambda self, teff, logg: '/grid/05750G45.KRZ')
    assert "'/grid/05750G45.KRZ'" in JobRunner()._select_input_text(config)


@pytest.mark.django_db
def test_the_prefill_flow_warns_that_the_model_cannot_come_with_it(
        logged_in_client, approved_user, no_background_worker):
    """A file input cannot be pre-filled, and Teff/log g came out of the model's
    own header - so submitting as-is would silently use a grid model."""
    req = Request.objects.create(
        user=approved_user, request_type='extractstellar', status='complete',
        parameters=dict(STELLAR, teff=4250.0, logg=1.5,
                        model_name='marcs_p4250_g15.krz'))

    resp = logged_in_client.get(f'/extractstellar/?modify={req.uuid}')

    assert 'marcs_p4250_g15.krz' in resp.content.decode()
    assert 'could not be carried over' in resp.content.decode()


# --- the grid's own filenames ----------------------------------------------
#
# Teff and log g are recorded nowhere but the filename, so VALD_MODEL_NAME_FORMATS
# has to serve both directions: building a candidate name and reading the grid's
# names back. These are the regression tests for the 2026-09 rename, where
# _find_model() stopped recognising every file in MODELS/STELLAR and returned a
# name it had invented instead - select5 then refused to open it, printed the
# reason to a discarded stdout, and exited 0, so five stellar requests failed
# with nothing but "Output file not found".

def grid(tmp_path, names):
    """A MODELS/STELLAR holding empty files with these names."""
    stellar = tmp_path / 'MODELS' / 'STELLAR'
    stellar.mkdir(parents=True)
    for name in names:
        (stellar / name).touch()
    return stellar


def runner_for(settings, tmp_path, formats=None):
    from vald.job_runner import JobRunner

    settings.VALD_HOME = tmp_path
    if formats is not None:
        settings.VALD_MODEL_NAME_FORMATS = formats
    return JobRunner()


RENAMED = 'castelli_ap00k2_T%05dG%02d.krz'
BARE = '%05dG%02d.KRZ'


@pytest.mark.parametrize('name,node', [
    ('castelli_ap00k2_T03500G30.krz', (3500, 30)),
    ('castelli_ap00k2_T07900G00.krz', (7900, 0)),
    ('05500G35.KRZ', (5500, 35)),
    ('05500G35.krz', (5500, 35)),        # the rename also lowered the suffix
    ('T03500G30.krz', None),             # no configured format has a bare T
    ('castelli_ap00k2_T03500G30.krz.bak', None),
    ('README', None),
    # The six metallicity families sitting beside ap00k2 in the real
    # MODELS/STELLAR, and the MARCS models, must stay invisible: no request
    # says which grid it wants, so a match here would pick a metallicity by
    # filename sort order.
    ('castelli_ap05k2_T03500G30.krz', None),
    ('castelli_am20k2_T03500G30.krz', None),
    ('p2500_g+3.0_m0.0_t01_st_z-0.25_a+0.10_c+0.00_n+0.00_o+0.10_r+0.00_s+0.00.krz',
     None),
])
def test_both_naming_schemes_are_read_back(settings, tmp_path, name, node):
    runner = runner_for(settings, tmp_path, (RENAMED, BARE))
    assert runner._model_node(name) == node


def test_the_renamed_grid_resolves_to_the_nearest_node(settings, tmp_path):
    grid(tmp_path, [RENAMED % (t, g)
                    for t in (7500, 8000) for g in (5, 40, 45)])
    runner = runner_for(settings, tmp_path, (RENAMED, BARE))

    assert Path(runner._find_model(7900, 0.34)).name == RENAMED % (8000, 5)
    assert Path(runner._find_model(8000, 4.5)).name == RENAMED % (8000, 45)


def test_a_grid_no_format_explains_fails_instead_of_inventing_a_name(
        settings, tmp_path):
    """The bug itself: a grid full of files, none of them recognised.

    It used to return $VALD_HOME/MODELS/STELLAR/07900G03.KRZ - a Teff/log g pair
    off the 250 K / 0.5 dex grid, so a name that could not exist under any
    naming scheme.
    """
    grid(tmp_path, [RENAMED % (7500, 5), RENAMED % (8000, 10)])
    runner = runner_for(settings, tmp_path, (BARE,))

    with pytest.raises(ValueError) as excinfo:
        runner._find_model(7900, 0.34)
    assert BARE in str(excinfo.value)


def test_an_absent_grid_directory_says_so(settings, tmp_path):
    runner = runner_for(settings, tmp_path, (RENAMED,))

    with pytest.raises(ValueError, match='Cannot read the model atmosphere grid'):
        runner._find_model(7900, 0.34)


@pytest.mark.parametrize('teff,logg', [
    (7900, 0.34), (8000, 4.5), (2000, 5.0), (50000, 0.0), (5777, 4.44),
])
def test_the_returned_model_always_exists(settings, tmp_path, teff, logg):
    """The invariant that makes the failure mode impossible: every name comes
    out of the directory listing, so none can name a file that is not there."""
    grid(tmp_path, [RENAMED % (t, g)
                    for t in (3500, 7500, 8000) for g in (0, 5, 45, 50)])
    runner = runner_for(settings, tmp_path, (RENAMED, BARE))

    assert Path(runner._find_model(teff, logg)).exists()


def test_ties_resolve_the_same_way_every_time(settings, tmp_path):
    """7900 K sits exactly between two nodes; whichever wins must keep winning,
    or the same request answers differently on a re-run."""
    grid(tmp_path, [RENAMED % (7800, 30), RENAMED % (8000, 30)])
    formats = (RENAMED, BARE)

    chosen = {runner_for(settings, tmp_path, formats)._find_model(7900, 3.0)
              for _ in range(5)}
    assert len(chosen) == 1


@pytest.mark.parametrize('fmt', [
    'castelli_T%05d.krz',              # one field
    'castelli_T%05dG%02dM%02d.krz',    # three
    'castelli_T%dG%d.krz',             # unpadded, so no width to match on
])
def test_a_format_without_exactly_two_padded_fields_is_rejected(fmt):
    from django.core.exceptions import ImproperlyConfigured

    from vald.job_runner import model_name_pattern

    with pytest.raises(ImproperlyConfigured):
        model_name_pattern(fmt)
