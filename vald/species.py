"""
VALD species lookup from CSV reference file.

Species codes in CVALD3 files are indices into the species list CSV.
This module provides fast lookup of species information.
"""

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from django.conf import settings


@dataclass(frozen=True)
class Species:
    """Species information from VALD species list."""
    index: int
    name: str
    charge: int
    mass: float
    ionization_energy: float
    
    @property
    def display_name(self) -> str:
        """Human-readable name like 'Fe I' or 'Ca II'."""
        if self.charge == 0:
            ion_str = "I"
        else:
            roman = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]
            ion_str = roman[self.charge] if self.charge < len(roman) else str(self.charge + 1)
        return f"{self.name} {ion_str}"
    
    @property
    def is_molecule(self) -> bool:
        """Check if this is a molecule (vs atom/ion)."""
        # Molecules have multi-character names or numbers
        return len(self.name) > 2 or any(c.isdigit() for c in self.name)


@lru_cache(maxsize=1)
def _load_species_dict() -> dict[int, Species]:
    """Load species list from CSV file. Cached."""
    species_file = getattr(settings, 'VALD_SPECIES_FILE', None)
    if species_file is None:
        vald_home = getattr(settings, 'VALD_HOME', Path.home() / 'VALD3')
        species_file = Path(vald_home) / 'CONFIG' / 'VALD_list_of_species.csv'
    else:
        species_file = Path(species_file)
    
    if not species_file.exists():
        raise FileNotFoundError(f"Species file not found: {species_file}")
    
    species_dict = {}
    with open(species_file, 'r', encoding='utf-8') as f:
        # Skip version comment line
        first_line = f.readline()
        if not first_line.startswith('#'):
            f.seek(0)  # Not a comment, rewind
        
        reader = csv.DictReader(f)
        for row in reader:
            try:
                index = int(row['Index'])
                species = Species(
                    index=index,
                    name=row['Name'],
                    charge=int(row['Charge']),
                    mass=float(row['Mass']),
                    ionization_energy=float(row['Ion. en.']),
                )
                species_dict[index] = species
            except (ValueError, KeyError, TypeError):
                continue
    
    return species_dict


def get_species(index: int) -> Optional[Species]:
    """
    Get species information by index.
    
    Args:
        index: Species index from CVALD3 file
        
    Returns:
        Species object or None if not found
    """
    return _load_species_dict().get(index)


def get_species_name(index: int) -> str:
    """
    Get human-readable species name.
    
    Args:
        index: Species index from CVALD3 file
        
    Returns:
        String like "Fe I" or "Unknown" if not found
    """
    species = get_species(index)
    return species.display_name if species else f"Unknown({index})"


def get_all_species() -> dict[int, Species]:
    """Get all species as a dictionary."""
    return _load_species_dict().copy()


def find_species_by_name(name: str, charge: Optional[int] = None) -> list[Species]:
    """
    Find species by name and optional charge.
    
    Args:
        name: Element/molecule name (e.g., "Fe", "TiO")
        charge: Optional ionization stage (0=neutral, 1=singly ionized, etc.)
        
    Returns:
        List of matching Species
    """
    results = []
    for species in _load_species_dict().values():
        if species.name.lower() == name.lower():
            if charge is None or species.charge == charge:
                results.append(species)
    return results


def clear_cache():
    """Clear the species cache (for testing)."""
    _load_species_dict.cache_clear()
