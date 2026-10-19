"""The VALD species list, and checking a user's species against it.

preselect5 looks an element filter up in VALD_list_of_species.csv and, for a
name it does not find, writes an ERROR line into its output stream and exits 0 -
so the request fails one stage later with an EOF that says nothing about why.
Checking here, against the same file and by the same rules, turns that into a
form error that says what to type instead.

The rules, from parse of the element code in preselect5.f90 and
READ_SPECIES_LIST_BY_NAME in read_vald_species.f90:

- the name starts at the first uppercase letter, so an isotope prefix (48Ca) is
  skipped, and so is a lowercase name entirely - 'fe 1' matches nothing and the
  request "succeeds" with an empty line list;
- the name is compared case-sensitively (Co is cobalt, CO carbon monoxide);
- the ionization stage is the spectrum number, 1 for neutral, and is looked up
  as charge = stage - 1. Stage 0 is clamped to neutral.
"""
import csv
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, FrozenSet, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

ROMAN_STAGES = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5,
                'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10}

# 'FeII', 'HI', 'Mg2', 'O1': an atomic symbol with the stage run onto it.
RUN_ON_STAGE_RE = re.compile(r'^([A-Z][a-z]?)(I{1,3}|IV|VI{0,3}|IX|X|\d{1,2})$')

STAGE_HINT = 'Ionization stages start at 1 for neutral: "Fe 1" is Fe I, "Fe 2" is Fe II.'


def species_list_path() -> Path:
    return Path(getattr(settings, 'VALD_SPECIES_LIST',
                        Path(settings.VALD_HOME) / 'CONFIG' / 'VALD_list_of_species.csv'))


@lru_cache(maxsize=4)
def _load(path: Path) -> Optional[Dict[str, FrozenSet[int]]]:
    """Map each species name to the charges VALD has it in, or None if unreadable."""
    try:
        with open(path, newline='', encoding='latin-1') as f:
            rows = list(csv.reader(f))
    except OSError:
        logger.error('VALD species list %s is unreadable; element fields are '
                     'checked for shape only', path)
        return None

    charges = {}
    for row in rows:
        # Line 1 is the '#VER..' version tag, line 2 the column names.
        if len(row) < 4 or row[0].startswith('#') or not row[3].strip().isdigit():
            continue
        name = row[2].strip()
        if name:
            charges.setdefault(name, set()).add(int(row[3]))
    return {name: frozenset(c) for name, c in charges.items()}


def species_charges() -> Optional[Dict[str, FrozenSet[int]]]:
    return _load(species_list_path())


def _stages(charges) -> str:
    stages = sorted(c + 1 for c in charges)
    if len(stages) == 1:
        return f'stage {stages[0]}'
    if len(stages) > 2 and stages[-1] - stages[0] == len(stages) - 1:
        return f'stages {stages[0]}-{stages[-1]}'
    return 'stages ' + ', '.join(str(s) for s in stages)


def problem(value: str) -> Optional[str]:
    """Why preselect5 would not find this species, or None if it would.

    `value` is already in the "<name>[ <stage>]" shape the forms enforce.
    """
    known = species_charges()
    if known is None:
        return None

    name, _, stage_text = value.partition(' ')
    stage = int(stage_text) if stage_text else None
    lookup = name.lstrip('0123456789')

    if stage == 0:
        return STAGE_HINT

    if lookup in known:
        if stage is None or stage - 1 in known[lookup]:
            return None
        return (f'VALD has no {lookup} {stage}. It has {lookup} in ionization '
                f'{_stages(known[lookup])}. {STAGE_HINT}')

    run_on = RUN_ON_STAGE_RE.match(lookup)
    if run_on and stage is None and run_on.group(1) in known:
        symbol, suffix = run_on.groups()
        guess = int(suffix) if suffix.isdigit() else ROMAN_STAGES[suffix]
        hint = (f'"{value}" is not a VALD species. Give the ionization stage as a '
                f'separate number, where 1 is neutral')
        if guess - 1 in known[symbol]:
            return f'{hint} - did you mean "{symbol} {guess}"?'
        return (f'{hint}. VALD has {symbol} in ionization '
                f'{_stages(known[symbol])}.')

    folded = sorted(n for n in known if n.lower() == lookup.lower())
    if folded:
        suffix = f' {stage}' if stage is not None else ''
        options = ' or '.join(f'"{n}{suffix}"' for n in folded)
        return (f'Species names are case-sensitive (Co is cobalt, CO carbon '
                f'monoxide) - did you mean {options}?')

    return f'"{lookup}" is not in the VALD species list.'
