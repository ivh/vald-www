"""Input validation and control-file formatting.

The values covered here are written into the control files the Fortran binaries
read, where a newline shifts every following line and missing quotes make a whole
block get skipped. Both were real defects.
"""
import re

import pytest

from vald import abundances
from vald.forms import (
    ExtractElementForm, ExtractStellarForm, ShowLineForm, ShowLineOnlineForm,
)

# The 'Linelist configuration' menu is built from the system configs in the
# database, so constructing any of these forms is a query - even though nothing
# below is about that field.
pytestmark = pytest.mark.django_db

ELEMENT_BASE = {'stwvl': '5700', 'endwvl': '5720', 'format': 'short',
                'pconf': 'default'}
STELLAR_BASE = {'stwvl': '5700', 'endwvl': '5703', 'dlimit': '0.05', 'micturb': '2',
                'teff': '8000', 'logg': '4.5', 'format': 'short',
                'pconf': 'default', 'modelgrid': 'atlas9'}
SHOWLINE_BASE = {'wvl0': '5000', 'win0': '0.5',
                 'pconf': 'default', 'isotopic_scaling': 'on'}


# --- R3: element + ionization ----------------------------------------------

# Enough of VALD_list_of_species.csv for every species named below, in its own
# layout: version tag, column names, then Use,Index,Name,Charge,...
SPECIES_CSV = """#VER03.03,,,,
Use,Index,Name,Charge,InChI
1,1,H,0,x
1,29,O,0,x
1,32,O,3,x
""" + "".join(f"1,{i},Fe,{c},x\n" for i, c in enumerate(range(26), start=326)) + """1,60,Ca,0,x
1,61,Ca,1,x
1,62,Co,0,x
1,63,N,0,x
1,10010,CO,0,x
1,10011,TiO,0,x
1,10020,H2O,0,x
1,10001,C2,0,x
1,10030,MgH,0,x
"""


@pytest.fixture(autouse=True)
def species_list(tmp_path, settings):
    from vald import species
    path = tmp_path / 'VALD_list_of_species.csv'
    path.write_text(SPECIES_CSV)
    settings.VALD_SPECIES_LIST = path
    yield
    species._load.cache_clear()


@pytest.mark.parametrize('value,normalised', [
    ('Fe', 'Fe'),
    ('Fe 1', 'Fe 1'),
    ('Fe 3', 'Fe 3'),
    ('Ca 2', 'Ca 2'),
    ('TiO', 'TiO'),          # molecules
    ('H2O', 'H2O'),
    ('C2', 'C2'),
    ('MgH', 'MgH'),
    ('48Ca 2', '48Ca 2'),    # isotopes
    ('  Fe   3  ', 'Fe 3'),  # whitespace collapsed
])
def test_documented_element_forms_are_accepted(value, normalised):
    form = ExtractElementForm({**ELEMENT_BASE, 'elmion': value})
    assert form.is_valid(), form.errors
    assert form.cleaned_data['elmion'] == normalised


@pytest.mark.parametrize('value', [
    "Fe 1\n'/tmp/evil.cfg'",   # newline shifts pres_in's config-path line
    'Fe I',                    # roman numeral
    "'quoted'",
    '../../etc/passwd',
    'Fe 1, 5000, 6000',
    'Fe\t1\n0 0 0 0',
])
def test_malformed_element_is_rejected(value):
    assert not ExtractElementForm({**ELEMENT_BASE, 'elmion': value}).is_valid()


def element_error(value):
    form = ExtractElementForm({**ELEMENT_BASE, 'elmion': value})
    assert not form.is_valid()
    return form.errors['elmion'][0]


# Each of these reached preselect5, which wrote an ERROR into its output stream
# and exited 0 - failing the request with presformat5's EOF, or for the
# lowercase name completing it with no lines at all.
@pytest.mark.parametrize('value,hint', [
    ('FeI', '"Fe 1"'),
    ('OI', '"O 1"'),
    ('O1', '"O 1"'),
    ('Fe2', '"Fe 2"'),
    ('NI', '"N 1"'),           # nitrogen, not nickel
    ('Fe I', '"Fe 1"'),
    ('fe 1', '"Fe 1"'),
    ('o 4', '"O 4"'),
    ('co', '"CO" or "Co"'),
])
def test_misspelt_species_gets_the_spelling_vald_wants(value, hint):
    assert hint in element_error(value)


@pytest.mark.parametrize('value', ['HII', 'H 2', 'O 2'])
def test_stage_vald_lacks_names_the_stages_it_has(value):
    assert 'ionization stage' in element_error(value)


@pytest.mark.parametrize('value', ['Fe 0', 'FeI', 'H 2', 'Fe I'])
def test_stage_errors_say_that_one_is_neutral(value):
    assert re.search(r'1 (is|for) neutral', element_error(value))


def test_unknown_species_is_named():
    assert element_error('AlH 1') == '"AlH" is not in the VALD species list.'


def test_unreadable_species_list_falls_back_to_shape_only(settings, tmp_path):
    settings.VALD_SPECIES_LIST = tmp_path / 'missing.csv'
    assert ExtractElementForm({**ELEMENT_BASE, 'elmion': 'AlH 1'}).is_valid()


@pytest.mark.parametrize('form_class,extra', [
    (ShowLineOnlineForm, {}),
    (ShowLineForm, {}),
])
def test_showline_element_is_validated(form_class, extra):
    good = form_class({**SHOWLINE_BASE, 'el0': 'Fe 1', **extra})
    assert good.is_valid(), good.errors
    bad = form_class({**SHOWLINE_BASE, 'el0': "Fe 1\n'x.cfg'", **extra})
    assert not bad.is_valid()


def test_showline_validates_every_query_slot():
    """el1..el4 are only reachable by a crafted POST, so they need checking too."""
    data = {**SHOWLINE_BASE, 'el0': 'Fe 1',
            'wvl2': '6000', 'win2': '0.5', 'el2': 'Fe 1\n/tmp/evil.cfg'}
    assert not ShowLineForm(data).is_valid()


# --- R3/R28: chemical composition ------------------------------------------

@pytest.mark.parametrize('value', [
    '',
    'Fe:-4.7',
    'Sr: -4.67, Cr: -3.37,\nEu: -5.53',   # the example in reqextstar.html
    'H :  0.91',
    'Fe: +4',
    'Tc:-20.00',
    'MH: -0.5',      # metallicity shorthand accepted by parserequest.c
    'm/h: -0.5',
])
def test_documented_abundance_forms_are_accepted(value):
    form = ExtractStellarForm({**STELLAR_BASE, 'chemcomp': value})
    assert form.is_valid(), form.errors


@pytest.mark.parametrize('value', [
    'Fe -4.5',                                    # no colon
    "Fe -4.5\n'END'\n'Synth'\n'/tmp/pwned.out'",  # inject select's output filename
    'Iron: -4.5',                                 # not an element field
    "'END'",
    "Fe: -4.5\n'select.out'",
    'Fe: abc',
])
def test_malformed_abundances_are_rejected(value):
    assert not ExtractStellarForm({**STELLAR_BASE, 'chemcomp': value}).is_valid()


def test_abundance_pair_limit_is_enforced():
    too_many = ', '.join(['Fe: -4.5'] * (abundances.MAX_PAIRS + 1))
    assert not ExtractStellarForm({**STELLAR_BASE, 'chemcomp': too_many}).is_valid()


@pytest.mark.parametrize('text,expected', [
    ('Fe: -4.37', "'Fe:-4.37',"),
    ('Fe:-4.7', "'Fe:-4.70',"),                  # two decimals always
    ('H : 0.91, He: -1.05', "'H :0.91','He:-1.05',"),
    ('fe: -4.5', "'Fe:-4.50',"),                 # case normalised
    ('MH: -0.5', "'M/H:-0.50',"),
    ('m/h:0.3', "'M/H:0.30',"),
    ('Sr: -4.67, Cr: -3.37,\nEu: -5.53', "'Sr:-4.67','Cr:-3.37','Eu:-5.53',"),
])
def test_select_input_format_matches_legacy_checkabund(text, expected):
    """Format per CheckAbund() in old/backend/parserequest.c.

    select5's RDABND locates tokens by scanning for "'" and silently skips any
    line without one, so unquoted abundances were discarded and solar values used.
    """
    assert abundances.to_select_input(abundances.parse(text)) == expected


def test_abundances_wrap_like_the_legacy_generator():
    """parserequest.c starts a new line once the current one exceeds 66 chars."""
    rendered = abundances.to_select_input(
        abundances.parse(', '.join(f'Fe: -{i / 10:.2f}' for i in range(12))))
    lines = rendered.splitlines()
    assert len(lines) > 1
    # every line but the last was cut only after passing the threshold
    for line in lines[:-1]:
        assert len(line) > abundances.LINE_WIDTH
        assert line.endswith("',")


def test_every_abundance_token_is_quoted():
    """The property that actually matters to RDABND."""
    rendered = abundances.to_select_input(abundances.parse('Fe: -4.5, Ni: -5.8\nMH: -0.2'))
    for line in rendered.splitlines():
        assert line.count("'") % 2 == 0
        assert line.startswith("'")
