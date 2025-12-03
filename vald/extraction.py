"""
VALD line extraction - replaces preselect5 Fortran binary.

This module extracts spectral lines from multiple CVALD3 files,
merges them by wavelength using the full VALD merge algorithm,
and applies element filters.
"""

import heapq
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

import numpy as np
from django.conf import settings

from vald.models import Config, ConfigLinelist
from vald.species import get_species
from vald.vald3_reader import VALD3Reader


# Merge parameters (from VALD config)
DEFAULT_WL_WINDOW_REF = 0.05  # Å - default wavelength tolerance
DEFAULT_WL_REF = 5000.0  # Å - reference wavelength for scaling
REL_ENERGY_TOLERANCE = 0.001  # 0.1% relative tolerance on upper energy


@dataclass
class LineData:
    """Container for spectral line data from one or more linelists."""
    wavelength: np.ndarray  # Vacuum wavelengths (Å)
    species_code: np.ndarray  # Species index
    loggf: np.ndarray  # log10(gf)
    e_lower: np.ndarray  # Lower energy level (eV)
    e_upper: np.ndarray  # Upper energy level (eV)
    j_lower: np.ndarray  # Lower J quantum number
    j_upper: np.ndarray  # Upper J quantum number
    lande_lower: np.ndarray  # Lower Landé factor
    lande_upper: np.ndarray  # Upper Landé factor
    gamma_rad: np.ndarray  # Radiative damping (log10)
    gamma_stark: np.ndarray  # Stark damping (log10)
    gamma_vdw: np.ndarray  # Van der Waals damping (log10)
    string_data: Optional[bytes] = None  # Raw term/reference data
    
    # Source tracking (which linelist each line came from)
    linelist_idx: Optional[np.ndarray] = None
    
    # Rank weights per line (9 values: wl, gf, E_low, E_high, lande, rad, stark, vdw, term)
    ranks: Optional[np.ndarray] = None  # Shape: (nlines, 9)
    
    # Merge flags per line
    mergeable: Optional[np.ndarray] = None  # bool array
    
    # Replacement list flag - lines from replacement lists only provide parameters,
    # they don't create new output lines if unmerged
    is_replacement_list: Optional[np.ndarray] = None  # bool array
    
    # Forbidden line flag - character at position 191 of info string
    # ' ' = allowed, 'A' = autoionizing, other = forbidden with selection rule
    forbid_flag: Optional[np.ndarray] = None  # uint8 array (ASCII char codes)
    
    @property
    def nlines(self) -> int:
        return len(self.wavelength)
    
    def __len__(self) -> int:
        return self.nlines
    
    @classmethod
    def empty(cls) -> 'LineData':
        """Create empty LineData."""
        return cls(
            wavelength=np.array([], dtype=np.float64),
            species_code=np.array([], dtype=np.int32),
            loggf=np.array([], dtype=np.float32),
            e_lower=np.array([], dtype=np.float64),
            e_upper=np.array([], dtype=np.float64),
            j_lower=np.array([], dtype=np.float32),
            j_upper=np.array([], dtype=np.float32),
            lande_lower=np.array([], dtype=np.float32),
            lande_upper=np.array([], dtype=np.float32),
            gamma_rad=np.array([], dtype=np.float32),
            gamma_stark=np.array([], dtype=np.float32),
            gamma_vdw=np.array([], dtype=np.float32),
        )
    
    @classmethod
    def from_query_result(
        cls, 
        result: dict, 
        linelist_idx: int = 0,
        ranks: Optional[tuple] = None,
        mergeable: bool = True,
        is_replacement_list: bool = False,
    ) -> 'LineData':
        """
        Create LineData from VALD3Reader.query_range() result.
        
        Args:
            result: Dict from query_range()
            linelist_idx: Index of source linelist
            ranks: Tuple of 9 rank values (wl, gf, E_low, E_high, lande, rad, stark, vdw, term)
            mergeable: Whether lines from this source can be merged
            is_replacement_list: If True, lines only provide parameters, not new output lines
        """
        if result['nlines'] == 0:
            return cls.empty()
        
        nlines = result['nlines']
        
        # Create rank array if provided
        if ranks is not None:
            rank_array = np.tile(np.array(ranks, dtype=np.int8), (nlines, 1))
        else:
            rank_array = np.full((nlines, 9), 3, dtype=np.int8)  # Default rank 3
        
        # Extract forbid flag from string_data position 190 (Fortran position 191)
        # ' ' = allowed, 'A' = autoionizing, other = forbidden with selection rule
        forbid_array = np.full(nlines, ord(' '), dtype=np.uint8)
        string_data = result.get('string_data')
        if string_data and len(string_data) >= nlines:
            line_len = len(string_data) // nlines
            if line_len >= 191:  # Need at least 191 bytes per line
                for i in range(nlines):
                    forbid_array[i] = string_data[i * line_len + 190]
        
        return cls(
            wavelength=np.asarray(result['wavelength'], dtype=np.float64),
            species_code=np.asarray(result['species_code'], dtype=np.int32),
            loggf=np.asarray(result['loggf'], dtype=np.float32),
            e_lower=np.asarray(result['e_lower'], dtype=np.float64),
            e_upper=np.asarray(result['e_upper'], dtype=np.float64),
            j_lower=np.asarray(result['j_lower'], dtype=np.float32),
            j_upper=np.asarray(result['j_upper'], dtype=np.float32),
            lande_lower=np.asarray(result['lande_lower'], dtype=np.float32),
            lande_upper=np.asarray(result['lande_upper'], dtype=np.float32),
            gamma_rad=np.asarray(result['gamma_rad'], dtype=np.float32),
            gamma_stark=np.asarray(result['gamma_stark'], dtype=np.float32),
            gamma_vdw=np.asarray(result['gamma_vdw'], dtype=np.float32),
            string_data=string_data,
            linelist_idx=np.full(nlines, linelist_idx, dtype=np.int32),
            ranks=rank_array,
            mergeable=np.full(nlines, mergeable, dtype=bool),
            is_replacement_list=np.full(nlines, is_replacement_list, dtype=bool),
            forbid_flag=forbid_array,
        )
    
    def filter_by_species(self, species_codes: list[int]) -> 'LineData':
        """Return new LineData with only lines matching given species codes."""
        if not species_codes:
            return self
        
        mask = np.isin(self.species_code, species_codes)
        return self._apply_mask(mask)
    
    def filter_by_wavelength(self, wl_min: float, wl_max: float) -> 'LineData':
        """Return new LineData with only lines in wavelength range."""
        mask = (self.wavelength >= wl_min) & (self.wavelength <= wl_max)
        return self._apply_mask(mask)
    
    def _apply_mask(self, mask: np.ndarray) -> 'LineData':
        """Apply boolean mask to all arrays."""
        return LineData(
            wavelength=self.wavelength[mask],
            species_code=self.species_code[mask],
            loggf=self.loggf[mask],
            e_lower=self.e_lower[mask],
            e_upper=self.e_upper[mask],
            j_lower=self.j_lower[mask],
            j_upper=self.j_upper[mask],
            lande_lower=self.lande_lower[mask],
            lande_upper=self.lande_upper[mask],
            gamma_rad=self.gamma_rad[mask],
            gamma_stark=self.gamma_stark[mask],
            gamma_vdw=self.gamma_vdw[mask],
            string_data=None,  # String data indexing is complex, skip for now
            linelist_idx=self.linelist_idx[mask] if self.linelist_idx is not None else None,
            ranks=self.ranks[mask] if self.ranks is not None else None,
            mergeable=self.mergeable[mask] if self.mergeable is not None else None,
            is_replacement_list=self.is_replacement_list[mask] if self.is_replacement_list is not None else None,
            forbid_flag=self.forbid_flag[mask] if self.forbid_flag is not None else None,
        )


def get_linelist_paths(linelist: 'Linelist') -> tuple[Path, Path]:
    """
    Get the full paths to CVALD3 and DSC3 files for a linelist.
    
    Args:
        linelist: Linelist model instance
        
    Returns:
        Tuple of (data_path, desc_path)
    """
    vald_home = Path(getattr(settings, 'VALD_HOME', Path.home() / 'VALD3'))
    
    # Path in DB is like '/CVALD3/ATOMS/Fe_NBS_cut_V3'
    # Need to add VALD_HOME prefix and file extensions
    base_path = linelist.path.lstrip('/')
    
    data_path = vald_home / f"{base_path}.CVALD3"
    desc_path = vald_home / f"{base_path}.DSC3"
    
    return data_path, desc_path


def _compute_wl_window(
    wl: float,
    wl_window_ref: float = DEFAULT_WL_WINDOW_REF,
    wl_ref: float = DEFAULT_WL_REF,
) -> float:
    """
    Compute wavelength window scaled by wavelength.
    
    The window scales linearly with wavelength, clamped to [0.01*ref, 100*ref].
    """
    wl_window_min = wl_window_ref * 0.01
    wl_window_max = wl_window_ref * 100.0
    return min(max(wl_window_ref * wl / wl_ref, wl_window_min), wl_window_max)


def _lines_are_equivalent(
    wl_i: float, species_i: int, j_low_i: float, j_high_i: float, e_high_i: float, linelist_i: int,
    wl_k: float, species_k: int, j_low_k: float, j_high_k: float, e_high_k: float, linelist_k: int,
    wl_window: float,
) -> bool:
    """
    Check if two lines are equivalent and should be merged.
    
    Lines are equivalent if:
    1. Same species
    2. From different linelists
    3. Wavelength within tolerance
    4. Same J quantum numbers (or Fe I which gets special treatment)
    5. Upper energy levels within 0.1% relative tolerance
    """
    # Must be same species
    if species_i != species_k:
        return False
    
    # Must be from different linelists
    if linelist_i == linelist_k:
        return False
    
    # Wavelength within tolerance
    if abs(wl_k - wl_i) > wl_window:
        return False
    
    # J quantum numbers must match (Fe I code 326 is exempt)
    if species_i != 326:
        if j_low_i != j_low_k or j_high_i != j_high_k:
            return False
    
    # Upper energy within relative tolerance
    if e_high_i > 0 and abs(e_high_k - e_high_i) > REL_ENERGY_TOLERANCE * e_high_i:
        return False
    
    return True


def _merge_lines_full(
    line_lists: list[LineData],
    config_linelists: list['ConfigLinelist'],
    wl_window_ref: float = DEFAULT_WL_WINDOW_REF,
    wl_ref: float = DEFAULT_WL_REF,
) -> LineData:
    """
    Merge multiple LineData objects using the full VALD merge algorithm.
    
    This implements the same logic as the Fortran mergep subroutine:
    1. Sort all lines by wavelength
    2. For each line, look for equivalent lines within wavelength window
    3. Merge parameters, keeping the highest-ranked value for each
    4. Mark merged lines as "used" so they're not output twice
    
    Args:
        line_lists: List of LineData from different linelists
        config_linelists: List of ConfigLinelist with merge settings
        wl_window_ref: Reference wavelength window (Å)
        wl_ref: Reference wavelength for window scaling (Å)
        
    Returns:
        Merged LineData with duplicates removed and best parameters kept
    """
    if len(line_lists) == 0:
        return LineData.empty()
    
    if len(line_lists) == 1:
        return line_lists[0]
    
    # Build replacement window per linelist
    replacement_windows = {}
    mergeable_flags = {}
    for idx, cl in enumerate(config_linelists):
        replacement_windows[idx] = cl.replacement_window
        # mergeable=0: can merge, mergeable=1: standalone, mergeable=2: replacement list
        mergeable_flags[idx] = (cl.mergeable != 1)
    
    # Concatenate all lines
    all_wl = np.concatenate([ld.wavelength for ld in line_lists])
    all_species = np.concatenate([ld.species_code for ld in line_lists])
    all_loggf = np.concatenate([ld.loggf for ld in line_lists])
    all_e_lower = np.concatenate([ld.e_lower for ld in line_lists])
    all_e_upper = np.concatenate([ld.e_upper for ld in line_lists])
    all_j_lower = np.concatenate([ld.j_lower for ld in line_lists])
    all_j_upper = np.concatenate([ld.j_upper for ld in line_lists])
    all_lande_lower = np.concatenate([ld.lande_lower for ld in line_lists])
    all_lande_upper = np.concatenate([ld.lande_upper for ld in line_lists])
    all_gamma_rad = np.concatenate([ld.gamma_rad for ld in line_lists])
    all_gamma_stark = np.concatenate([ld.gamma_stark for ld in line_lists])
    all_gamma_vdw = np.concatenate([ld.gamma_vdw for ld in line_lists])
    all_linelist_idx = np.concatenate([ld.linelist_idx for ld in line_lists])
    all_ranks = np.concatenate([ld.ranks for ld in line_lists])
    all_mergeable = np.concatenate([ld.mergeable for ld in line_lists])
    all_is_replacement_list = np.concatenate([ld.is_replacement_list for ld in line_lists])
    all_forbid = np.concatenate([ld.forbid_flag for ld in line_lists])
    
    n = len(all_wl)
    
    # Sort by wavelength
    sort_idx = np.argsort(all_wl)
    all_wl = all_wl[sort_idx]
    all_species = all_species[sort_idx]
    all_loggf = all_loggf[sort_idx]
    all_e_lower = all_e_lower[sort_idx]
    all_e_upper = all_e_upper[sort_idx]
    all_j_lower = all_j_lower[sort_idx]
    all_j_upper = all_j_upper[sort_idx]
    all_lande_lower = all_lande_lower[sort_idx]
    all_lande_upper = all_lande_upper[sort_idx]
    all_gamma_rad = all_gamma_rad[sort_idx]
    all_gamma_stark = all_gamma_stark[sort_idx]
    all_gamma_vdw = all_gamma_vdw[sort_idx]
    all_linelist_idx = all_linelist_idx[sort_idx]
    all_ranks = all_ranks[sort_idx]
    all_mergeable = all_mergeable[sort_idx]
    all_is_replacement_list = all_is_replacement_list[sort_idx]
    all_forbid = all_forbid[sort_idx]
    
    # Track which lines have been merged into another
    used = np.zeros(n, dtype=bool)
    
    # Merge loop
    for i in range(n):
        if used[i]:
            continue
        if not all_mergeable[i]:
            continue
        
        wl_i = all_wl[i]
        species_i = all_species[i]
        linelist_i = all_linelist_idx[i]
        
        # Get replacement window for this linelist
        repl_window_i = replacement_windows.get(linelist_i, wl_window_ref)
        
        # Look forward for merge candidates
        k = i + 1
        while k < n:
            wl_k = all_wl[k]
            
            # Get combined window (max of both linelists' windows)
            linelist_k = all_linelist_idx[k]
            repl_window_k = replacement_windows.get(linelist_k, wl_window_ref)
            combined_window = max(repl_window_i, repl_window_k)
            wl_window = _compute_wl_window(wl_i, combined_window, wl_ref)
            
            # Past wavelength window - stop searching
            if wl_k - wl_i > wl_window:
                break
            
            # Skip already-used or non-mergeable lines
            if used[k] or not all_mergeable[k]:
                k += 1
                continue
            
            # Check forbid flag compatibility (Fortran logic from preselect5.f90:1192-1195)
            # Don't merge if one is forbidden and other is allowed (unless autoionizing)
            # Note: forbid flags only apply to atoms (species < 10000). Molecules use
            # position 191 for parity/branch info, so we skip this check for molecules.
            forbid_i = all_forbid[i]
            forbid_k = all_forbid[k]
            SPACE = ord(' ')
            AUTO = ord('A')
            is_molecule = (species_i >= 10000)
            if not is_molecule:
                # For atoms: Allow merge if same forbid flag, or one is autoionizing and other is allowed
                forbid_compatible = (forbid_i == forbid_k) or \
                                   (forbid_i == AUTO and forbid_k == SPACE) or \
                                   (forbid_i == SPACE and forbid_k == AUTO)
                if not forbid_compatible:
                    k += 1
                    continue
            
            # Check if lines are equivalent
            if _lines_are_equivalent(
                wl_i, species_i, all_j_lower[i], all_j_upper[i], all_e_upper[i], linelist_i,
                wl_k, all_species[k], all_j_lower[k], all_j_upper[k], all_e_upper[k], linelist_k,
                wl_window,
            ):
                # Lines are equivalent - merge parameters
                # The line with higher wavelength rank becomes the "primary"
                # and the other is marked as used
                
                rank_wl_i = all_ranks[i, 0]
                rank_wl_k = all_ranks[k, 0]
                
                if rank_wl_i >= rank_wl_k:
                    # Line i is primary, merge k into i
                    _merge_parameters(
                        i, k,
                        all_loggf, all_e_lower, all_e_upper,
                        all_lande_lower, all_lande_upper,
                        all_gamma_rad, all_gamma_stark, all_gamma_vdw,
                        all_ranks,
                    )
                    used[k] = True
                else:
                    # Line k is primary (better wavelength), merge i into k
                    _merge_parameters(
                        k, i,
                        all_loggf, all_e_lower, all_e_upper,
                        all_lande_lower, all_lande_upper,
                        all_gamma_rad, all_gamma_stark, all_gamma_vdw,
                        all_ranks,
                    )
                    used[i] = True
                    break  # Line i is now used, move to next
            
            k += 1
    
    # Filter out used lines AND unmerged replacement list lines
    # Replacement list lines only provide parameters to other lines, they don't
    # create new output lines if they don't find a merge partner
    keep = ~used & ~all_is_replacement_list
    
    return LineData(
        wavelength=all_wl[keep],
        species_code=all_species[keep],
        loggf=all_loggf[keep],
        e_lower=all_e_lower[keep],
        e_upper=all_e_upper[keep],
        j_lower=all_j_lower[keep],
        j_upper=all_j_upper[keep],
        lande_lower=all_lande_lower[keep],
        lande_upper=all_lande_upper[keep],
        gamma_rad=all_gamma_rad[keep],
        gamma_stark=all_gamma_stark[keep],
        gamma_vdw=all_gamma_vdw[keep],
        linelist_idx=all_linelist_idx[keep],
        ranks=all_ranks[keep],
        mergeable=all_mergeable[keep],
        is_replacement_list=all_is_replacement_list[keep],
        forbid_flag=all_forbid[keep],
    )


def _merge_parameters(
    primary: int,
    secondary: int,
    loggf: np.ndarray,
    e_lower: np.ndarray,
    e_upper: np.ndarray,
    lande_lower: np.ndarray,
    lande_upper: np.ndarray,
    gamma_rad: np.ndarray,
    gamma_stark: np.ndarray,
    gamma_vdw: np.ndarray,
    ranks: np.ndarray,
) -> None:
    """
    Merge parameters from secondary line into primary line.
    
    For each parameter, keep the value with the higher rank.
    Modifies arrays in place.
    
    Rank indices:
        0: wavelength (already handled by caller)
        1: loggf
        2: E_lower
        3: E_upper
        4: Landé factors
        5: gamma_rad
        6: gamma_stark
        7: gamma_vdw
        8: term designation (not handled here - needs string data)
    """
    # loggf (rank index 1)
    if ranks[secondary, 1] > ranks[primary, 1]:
        loggf[primary] = loggf[secondary]
        ranks[primary, 1] = ranks[secondary, 1]
    
    # E_lower (rank index 2)
    if ranks[secondary, 2] > ranks[primary, 2]:
        e_lower[primary] = e_lower[secondary]
        ranks[primary, 2] = ranks[secondary, 2]
    
    # E_upper (rank index 3)
    if ranks[secondary, 3] > ranks[primary, 3]:
        e_upper[primary] = e_upper[secondary]
        ranks[primary, 3] = ranks[secondary, 3]
    
    # Landé factors (rank index 4) - both lower and upper together
    if ranks[secondary, 4] > ranks[primary, 4]:
        lande_lower[primary] = lande_lower[secondary]
        lande_upper[primary] = lande_upper[secondary]
        ranks[primary, 4] = ranks[secondary, 4]
    # Also handle case where primary has missing value (99.0 = unknown)
    elif lande_lower[primary] == 99.0 and lande_lower[secondary] != 99.0:
        lande_lower[primary] = lande_lower[secondary]
        lande_upper[primary] = lande_upper[secondary]
        ranks[primary, 4] = ranks[secondary, 4]
    
    # gamma_rad (rank index 5)
    if ranks[secondary, 5] > ranks[primary, 5]:
        gamma_rad[primary] = gamma_rad[secondary]
        ranks[primary, 5] = ranks[secondary, 5]
    elif gamma_rad[primary] == 0.0 and gamma_rad[secondary] != 0.0:
        gamma_rad[primary] = gamma_rad[secondary]
        ranks[primary, 5] = ranks[secondary, 5]
    
    # gamma_stark (rank index 6)
    if ranks[secondary, 6] > ranks[primary, 6]:
        gamma_stark[primary] = gamma_stark[secondary]
        ranks[primary, 6] = ranks[secondary, 6]
    elif gamma_stark[primary] == 0.0 and gamma_stark[secondary] != 0.0:
        gamma_stark[primary] = gamma_stark[secondary]
        ranks[primary, 6] = ranks[secondary, 6]
    
    # gamma_vdw (rank index 7)
    if ranks[secondary, 7] > ranks[primary, 7]:
        gamma_vdw[primary] = gamma_vdw[secondary]
        ranks[primary, 7] = ranks[secondary, 7]
    elif gamma_vdw[primary] == 0.0 and gamma_vdw[secondary] != 0.0:
        gamma_vdw[primary] = gamma_vdw[secondary]
        ranks[primary, 7] = ranks[secondary, 7]


def extract_lines(
    config: Config,
    wl_min: float,
    wl_max: float,
    element_filter: Optional[str] = None,
    max_lines: int = 500000,
) -> LineData:
    """
    Extract spectral lines from all linelists in a configuration.
    
    This replaces the preselect5 Fortran binary.
    
    Args:
        config: Configuration specifying which linelists to use
        wl_min: Minimum wavelength (Å, vacuum)
        wl_max: Maximum wavelength (Å, vacuum)
        element_filter: Optional element filter string (e.g., "Fe 1", "Ca")
        max_lines: Maximum number of lines to return
        
    Returns:
        LineData with merged lines from all linelists, sorted by wavelength
    """
    # Parse element filter to species codes
    filter_species = _parse_element_filter(element_filter) if element_filter else None
    
    # Get enabled linelists sorted by priority
    config_linelists = list(
        config.configlinelist_set
        .filter(is_enabled=True)
        .select_related('linelist')
        .order_by('priority')
    )
    
    # Collect lines from each linelist
    all_lines: list[LineData] = []
    
    for idx, cl in enumerate(config_linelists):
        data_path, desc_path = get_linelist_paths(cl.linelist)
        
        # Skip if files don't exist
        if not data_path.exists() or not desc_path.exists():
            continue
        
        # Check if linelist could contain the requested element
        if filter_species:
            # Simple check: if element range doesn't overlap, skip
            ll_min, ll_max = cl.linelist.element_min, cl.linelist.element_max
            if not any(ll_min <= s <= ll_max for s in filter_species):
                continue
        
        # Determine if this linelist is mergeable
        # mergeable: 0=can merge, 1=standalone (never merge), 2=replacement list (always merge)
        is_mergeable = (cl.mergeable != 1)
        # Replacement lists only provide parameters, they don't add new lines
        is_replacement = (cl.mergeable == 2)
        
        # Get rank weights
        ranks = (
            cl.rank_wl,
            cl.rank_gf,
            cl.rank_rad,  # Note: config uses different order than internal
            cl.rank_stark,
            cl.rank_waals,
            cl.rank_lande,
            cl.rank_term,
            cl.rank_ext_vdw,
            cl.rank_zeeman,
        )
        # Reorder to match internal order: wl, gf, E_low, E_high, lande, rad, stark, vdw, term
        # Config order: wl, gf, rad, stark, waals, lande, term, ext_vdw, zeeman
        # We'll use a simplified mapping for now
        ranks_internal = (
            cl.rank_wl,      # 0: wavelength
            cl.rank_gf,      # 1: loggf
            3,               # 2: E_lower (use default)
            3,               # 3: E_upper (use default)
            cl.rank_lande,   # 4: Landé
            cl.rank_rad,     # 5: gamma_rad
            cl.rank_stark,   # 6: gamma_stark
            cl.rank_waals,   # 7: gamma_vdw
            cl.rank_term,    # 8: term
        )
        
        try:
            with VALD3Reader(data_path, desc_path) as reader:
                result = reader.query_range(wl_min, wl_max, max_lines)
                
                if result['nlines'] > 0:
                    lines = LineData.from_query_result(
                        result, 
                        linelist_idx=idx,
                        ranks=ranks_internal,
                        mergeable=is_mergeable,
                        is_replacement_list=is_replacement,
                    )
                    
                    # Apply element filter
                    if filter_species:
                        lines = lines.filter_by_species(filter_species)
                    
                    if lines.nlines > 0:
                        all_lines.append(lines)
        except Exception as e:
            # Log error but continue with other linelists
            import logging
            logging.warning(f"Error reading {data_path}: {e}")
            continue
    
    if not all_lines:
        return LineData.empty()
    
    # Merge all lines using full VALD algorithm
    merged = _merge_lines_full(
        all_lines, 
        config_linelists,
        wl_window_ref=config.wl_window_ref,
        wl_ref=config.wl_ref,
    )
    
    # Limit to max_lines
    if merged.nlines > max_lines:
        merged = merged._apply_mask(np.arange(merged.nlines) < max_lines)
    
    return merged


def _parse_element_filter(filter_str: str) -> list[int]:
    """
    Parse element filter string to list of species codes.
    
    Examples:
        "Fe" -> [326, 327, 328, ...]  (all iron ions)
        "Fe 1" -> [326]  (Fe I only)
        "Fe 1, Ca 2" -> [326, 192]  (Fe I and Ca II)
    """
    from vald.species import find_species_by_name
    
    species_codes = []
    
    # Split by comma for multiple elements
    parts = [p.strip() for p in filter_str.split(',')]
    
    for part in parts:
        tokens = part.split()
        if not tokens:
            continue
        
        element = tokens[0]
        
        # Parse ionization stage if present
        if len(tokens) > 1:
            try:
                # "Fe 1" means neutral (charge=0 in VALD)
                ion_stage = int(tokens[1])
                charge = ion_stage - 1  # VALD uses 0-based
            except ValueError:
                charge = None
        else:
            charge = None
        
        # Find matching species
        matches = find_species_by_name(element, charge=charge)
        species_codes.extend(s.index for s in matches)
    
    return species_codes


# Keep simple merge for backward compatibility / testing
def _merge_lines_simple(line_lists: list[LineData]) -> LineData:
    """
    Simple merge: concatenate and sort by wavelength.
    No duplicate removal or parameter merging.
    """
    if len(line_lists) == 1:
        return line_lists[0]
    
    # Concatenate all arrays
    merged = LineData(
        wavelength=np.concatenate([ld.wavelength for ld in line_lists]),
        species_code=np.concatenate([ld.species_code for ld in line_lists]),
        loggf=np.concatenate([ld.loggf for ld in line_lists]),
        e_lower=np.concatenate([ld.e_lower for ld in line_lists]),
        e_upper=np.concatenate([ld.e_upper for ld in line_lists]),
        j_lower=np.concatenate([ld.j_lower for ld in line_lists]),
        j_upper=np.concatenate([ld.j_upper for ld in line_lists]),
        lande_lower=np.concatenate([ld.lande_lower for ld in line_lists]),
        lande_upper=np.concatenate([ld.lande_upper for ld in line_lists]),
        gamma_rad=np.concatenate([ld.gamma_rad for ld in line_lists]),
        gamma_stark=np.concatenate([ld.gamma_stark for ld in line_lists]),
        gamma_vdw=np.concatenate([ld.gamma_vdw for ld in line_lists]),
        linelist_idx=np.concatenate([
            ld.linelist_idx for ld in line_lists if ld.linelist_idx is not None
        ]) if all(ld.linelist_idx is not None for ld in line_lists) else None,
        ranks=np.concatenate([
            ld.ranks for ld in line_lists if ld.ranks is not None
        ]) if all(ld.ranks is not None for ld in line_lists) else None,
        mergeable=np.concatenate([
            ld.mergeable for ld in line_lists if ld.mergeable is not None
        ]) if all(ld.mergeable is not None for ld in line_lists) else None,
    )
    
    # Sort by wavelength
    sort_idx = np.argsort(merged.wavelength)
    
    return LineData(
        wavelength=merged.wavelength[sort_idx],
        species_code=merged.species_code[sort_idx],
        loggf=merged.loggf[sort_idx],
        e_lower=merged.e_lower[sort_idx],
        e_upper=merged.e_upper[sort_idx],
        j_lower=merged.j_lower[sort_idx],
        j_upper=merged.j_upper[sort_idx],
        lande_lower=merged.lande_lower[sort_idx],
        lande_upper=merged.lande_upper[sort_idx],
        gamma_rad=merged.gamma_rad[sort_idx],
        gamma_stark=merged.gamma_stark[sort_idx],
        gamma_vdw=merged.gamma_vdw[sort_idx],
        linelist_idx=merged.linelist_idx[sort_idx] if merged.linelist_idx is not None else None,
        ranks=merged.ranks[sort_idx] if merged.ranks is not None else None,
        mergeable=merged.mergeable[sort_idx] if merged.mergeable is not None else None,
    )
