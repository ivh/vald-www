"""
VALD3 compressed database reader.

This module provides Python access to VALD3 compressed spectral line data
using a fast C extension for LZW decompression.

Usage:
    from vald.vald3_reader import VALD3Reader

    reader = VALD3Reader('/path/to/data.CVALD3', '/path/to/data.DSC3')
    result = reader.query_range(5000.0, 6000.0)
    print(f"Found {result['nlines']} lines")
"""

from pathlib import Path
from typing import Dict, Union

import numpy as np

try:
    from vald import vald3_decompress
except ImportError:
    raise ImportError(
        "vald3_decompress module not found. Build with: uv sync"
    )

# Re-export species functions for convenience
from vald.species import get_species, get_species_name, Species


class VALD3Reader:
    """
    Reader for VALD3 compressed spectral line data files.

    The CVALD3 format stores spectral line data in LZW-compressed binary records.
    Each record contains up to 1024 lines with wavelength, species, oscillator
    strength, energy levels, quantum numbers, damping constants, and term
    designations.

    Example:
        >>> reader = VALD3Reader('Fe1_K14.CVALD3', 'Fe1_K14.DSC3')
        >>> data = reader.query_range(5000.0, 6000.0)
        >>> wavelengths = data['wavelength']
        >>> loggf = data['loggf']
    """

    # String data layout (210 bytes per line)
    STR_BYTES_PER_LINE = 210

    def __init__(self, data_file: Union[str, Path], desc_file: Union[str, Path]):
        """
        Initialize reader with compressed data and descriptor files.

        Args:
            data_file: Path to the compressed VALD3 data file (.CVALD3)
            desc_file: Path to the descriptor file (.DSC3)

        Raises:
            FileNotFoundError: If either file doesn't exist
            RuntimeError: If files can't be opened
        """
        self.data_file = Path(data_file)
        self.desc_file = Path(desc_file)

        if not self.data_file.exists():
            raise FileNotFoundError(f"Data file not found: {self.data_file}")
        if not self.desc_file.exists():
            raise FileNotFoundError(f"Descriptor file not found: {self.desc_file}")

        self._reader = vald3_decompress.VALD3Reader(
            str(self.data_file), str(self.desc_file)
        )

    def query_range(
        self, wl_min: float, wl_max: float, max_lines: int = 100000
    ) -> Dict[str, np.ndarray]:
        """
        Query spectral lines in a wavelength range.

        Args:
            wl_min: Minimum wavelength in Angstroms (vacuum)
            wl_max: Maximum wavelength in Angstroms (vacuum)
            max_lines: Maximum number of lines to return

        Returns:
            Dictionary with numpy arrays:
            - nlines: Number of lines found
            - wavelength: Vacuum wavelengths (Å)
            - species_code: VALD species codes (ZZIIAA format)
            - loggf: log10(gf) oscillator strengths
            - e_lower, e_upper: Energy levels (eV)
            - j_lower, j_upper: J quantum numbers
            - lande_lower, lande_upper: Landé g-factors
            - gamma_rad, gamma_stark, gamma_vdw: Damping constants (log10)
            - string_data: Raw string data (terms, references)

        Raises:
            ValueError: If wl_min >= wl_max
            RuntimeError: If read fails
        """
        if wl_min >= wl_max:
            raise ValueError("wl_min must be less than wl_max")

        result = self._reader.query_range(wl_min, wl_max, max_lines)

        # Rename wavelength_vacuum to wavelength for consistency
        if 'wavelength_vacuum' in result:
            result['wavelength'] = result.pop('wavelength_vacuum')

        # Convert lists to numpy arrays
        for key in list(result.keys()):
            if key != 'nlines' and key != 'string_data' and isinstance(result[key], list):
                result[key] = np.array(result[key])

        return result

    def is_open(self) -> bool:
        """Check if the file is open."""
        return self._reader.is_open()

    def close(self):
        """Close the file."""
        self._reader.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __repr__(self):
        return f"VALD3Reader('{self.data_file.name}')"


# Air/vacuum wavelength conversion (from VALD Fortran code)
def vacuum_to_air(wavelength: np.ndarray) -> np.ndarray:
    """
    Convert vacuum wavelengths to air wavelengths.

    Uses the IAU standard formula. Only wavelengths > 2000 Å are converted;
    shorter wavelengths are returned unchanged.

    Args:
        wavelength: Vacuum wavelengths in Angstroms

    Returns:
        Air wavelengths in Angstroms
    """
    wl = np.asarray(wavelength, dtype=np.float64)
    result = wl.copy()

    # Only convert above 2000 Å
    mask = wl > 2000.0
    if np.any(mask):
        s2 = 1e8 / (wl[mask] ** 2)
        n = 1 + 8.34254e-5 + 2.406147e-2 / (130 - s2) + 1.5998e-4 / (38.9 - s2)
        result[mask] = wl[mask] / n

    return result


def air_to_vacuum(wavelength: np.ndarray) -> np.ndarray:
    """
    Convert air wavelengths to vacuum wavelengths.

    Uses the IAU standard formula (inverse of vacuum_to_air).

    Args:
        wavelength: Air wavelengths in Angstroms

    Returns:
        Vacuum wavelengths in Angstroms
    """
    wl = np.asarray(wavelength, dtype=np.float64)
    result = wl.copy()

    # Only convert above 2000 Å
    mask = wl > 2000.0
    if np.any(mask):
        s2 = 1e8 / (wl[mask] ** 2)
        n = 1 + 8.34254e-5 + 2.406147e-2 / (130 - s2) + 1.5998e-4 / (38.9 - s2)
        result[mask] = wl[mask] * n

    return result
