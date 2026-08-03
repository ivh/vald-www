"""Input validation and control-file formatting.

The values covered here are written into the control files the Fortran binaries
read, where a newline shifts every following line and missing quotes make a whole
block get skipped. Both were real defects.
"""
import pytest

from vald import abundances
from vald.forms import (
    ExtractElementForm, ExtractStellarForm, ShowLineForm, ShowLineOnlineForm,
)

ELEMENT_BASE = {'stwvl': '5700', 'endwvl': '5720', 'format': 'short',
                'viaftp': 'via ftp', 'pconf': 'default'}
STELLAR_BASE = {'stwvl': '5700', 'endwvl': '5703', 'dlimit': '0.05', 'micturb': '2',
                'teff': '8000', 'logg': '4.5', 'format': 'short',
                'viaftp': 'via ftp', 'pconf': 'default'}
SHOWLINE_BASE = {'wvl0': '5000', 'win0': '0.5', 'viaftp': 'via ftp',
                 'pconf': 'default', 'isotopic_scaling': 'on'}


# --- R3: element + ionization ----------------------------------------------

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
    """Format per CheckAbund() in backend/parserequest.c.

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
