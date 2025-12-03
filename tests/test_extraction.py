"""Tests for the extraction module."""

import pytest
import numpy as np
from pathlib import Path

from vald.extraction import (
    LineData,
    extract_lines,
    get_linelist_paths,
    _parse_element_filter,
    _merge_lines_full,
    _merge_lines_simple,
    _lines_are_equivalent,
    _compute_wl_window,
)
from vald.species import find_species_by_name


class TestLineData:
    """Tests for LineData dataclass."""

    def test_empty(self):
        """Test creating empty LineData."""
        ld = LineData.empty()
        assert ld.nlines == 0
        assert len(ld) == 0

    def test_from_query_result(self):
        """Test creating LineData from query result."""
        result = {
            'nlines': 3,
            'wavelength': np.array([5000.0, 5001.0, 5002.0]),
            'species_code': np.array([326, 326, 327]),
            'loggf': np.array([-1.0, -2.0, -1.5]),
            'e_lower': np.array([0.0, 1.0, 2.0]),
            'e_upper': np.array([2.5, 3.5, 4.5]),
            'j_lower': np.array([0.5, 1.5, 2.5]),
            'j_upper': np.array([1.5, 2.5, 3.5]),
            'lande_lower': np.array([1.0, 1.0, 1.0]),
            'lande_upper': np.array([1.0, 1.0, 1.0]),
            'gamma_rad': np.array([7.0, 7.0, 7.0]),
            'gamma_stark': np.array([-5.0, -5.0, -5.0]),
            'gamma_vdw': np.array([-7.0, -7.0, -7.0]),
        }
        
        ld = LineData.from_query_result(result, linelist_idx=5, ranks=(3,3,3,3,3,3,3,3,3))
        assert ld.nlines == 3
        assert ld.linelist_idx is not None
        assert np.all(ld.linelist_idx == 5)
        assert ld.ranks is not None
        assert ld.ranks.shape == (3, 9)

    def test_filter_by_species(self):
        """Test filtering by species code."""
        ld = LineData(
            wavelength=np.array([5000.0, 5001.0, 5002.0, 5003.0]),
            species_code=np.array([326, 327, 326, 328]),
            loggf=np.array([-1.0, -2.0, -1.5, -3.0]),
            e_lower=np.zeros(4),
            e_upper=np.zeros(4),
            j_lower=np.zeros(4),
            j_upper=np.zeros(4),
            lande_lower=np.zeros(4),
            lande_upper=np.zeros(4),
            gamma_rad=np.zeros(4),
            gamma_stark=np.zeros(4),
            gamma_vdw=np.zeros(4),
        )
        
        filtered = ld.filter_by_species([326])
        assert filtered.nlines == 2
        assert np.all(filtered.species_code == 326)
        
        filtered = ld.filter_by_species([326, 327])
        assert filtered.nlines == 3

    def test_filter_by_wavelength(self):
        """Test filtering by wavelength range."""
        ld = LineData(
            wavelength=np.array([4999.0, 5000.0, 5001.0, 5002.0]),
            species_code=np.array([326, 326, 326, 326]),
            loggf=np.zeros(4),
            e_lower=np.zeros(4),
            e_upper=np.zeros(4),
            j_lower=np.zeros(4),
            j_upper=np.zeros(4),
            lande_lower=np.zeros(4),
            lande_upper=np.zeros(4),
            gamma_rad=np.zeros(4),
            gamma_stark=np.zeros(4),
            gamma_vdw=np.zeros(4),
        )
        
        filtered = ld.filter_by_wavelength(5000.0, 5001.0)
        assert filtered.nlines == 2
        assert filtered.wavelength[0] == 5000.0
        assert filtered.wavelength[1] == 5001.0


class TestLineEquivalence:
    """Tests for line equivalence checking."""
    
    def test_same_species_different_linelist(self):
        """Test that same species from different linelists are equivalent."""
        assert _lines_are_equivalent(
            wl_i=5000.0, species_i=326, j_low_i=0.5, j_high_i=1.5, e_high_i=2.5, linelist_i=0,
            wl_k=5000.01, species_k=326, j_low_k=0.5, j_high_k=1.5, e_high_k=2.5, linelist_k=1,
            wl_window=0.05,
        )
    
    def test_different_species_not_equivalent(self):
        """Test that different species are not equivalent."""
        assert not _lines_are_equivalent(
            wl_i=5000.0, species_i=326, j_low_i=0.5, j_high_i=1.5, e_high_i=2.5, linelist_i=0,
            wl_k=5000.01, species_k=327, j_low_k=0.5, j_high_k=1.5, e_high_k=2.5, linelist_k=1,
            wl_window=0.05,
        )
    
    def test_same_linelist_not_equivalent(self):
        """Test that lines from same linelist are not equivalent (no self-merge)."""
        assert not _lines_are_equivalent(
            wl_i=5000.0, species_i=326, j_low_i=0.5, j_high_i=1.5, e_high_i=2.5, linelist_i=0,
            wl_k=5000.01, species_k=326, j_low_k=0.5, j_high_k=1.5, e_high_k=2.5, linelist_k=0,
            wl_window=0.05,
        )
    
    def test_different_j_not_equivalent(self):
        """Test that lines with different J are not equivalent."""
        # Use Ca I (191) which is NOT exempt from J check (only Fe I 326 is exempt)
        assert not _lines_are_equivalent(
            wl_i=5000.0, species_i=191, j_low_i=0.5, j_high_i=1.5, e_high_i=2.5, linelist_i=0,
            wl_k=5000.01, species_k=191, j_low_k=1.5, j_high_k=1.5, e_high_k=2.5, linelist_k=1,
            wl_window=0.05,
        )
    
    def test_fe_i_exempt_from_j_check(self):
        """Test that Fe I (code 326) is exempt from J quantum number check."""
        # Fe I with different J should still merge
        assert _lines_are_equivalent(
            wl_i=5000.0, species_i=326, j_low_i=0.5, j_high_i=1.5, e_high_i=2.5, linelist_i=0,
            wl_k=5000.01, species_k=326, j_low_k=1.5, j_high_k=2.5, e_high_k=2.5, linelist_k=1,
            wl_window=0.05,
        )
    
    def test_outside_wl_window_not_equivalent(self):
        """Test that lines outside wavelength window are not equivalent."""
        assert not _lines_are_equivalent(
            wl_i=5000.0, species_i=326, j_low_i=0.5, j_high_i=1.5, e_high_i=2.5, linelist_i=0,
            wl_k=5000.10, species_k=326, j_low_k=0.5, j_high_k=1.5, e_high_k=2.5, linelist_k=1,
            wl_window=0.05,
        )
    
    def test_different_e_upper_not_equivalent(self):
        """Test that lines with very different upper energy are not equivalent."""
        assert not _lines_are_equivalent(
            wl_i=5000.0, species_i=326, j_low_i=0.5, j_high_i=1.5, e_high_i=2.5, linelist_i=0,
            wl_k=5000.01, species_k=326, j_low_k=0.5, j_high_k=1.5, e_high_k=3.0, linelist_k=1,  # 20% diff
            wl_window=0.05,
        )


class TestWlWindow:
    """Tests for wavelength window computation."""
    
    def test_at_reference_wavelength(self):
        """Test window at reference wavelength."""
        window = _compute_wl_window(5000.0, wl_window_ref=0.05, wl_ref=5000.0)
        assert abs(window - 0.05) < 1e-10
    
    def test_scaled_by_wavelength(self):
        """Test that window scales with wavelength."""
        window_10000 = _compute_wl_window(10000.0, wl_window_ref=0.05, wl_ref=5000.0)
        assert abs(window_10000 - 0.10) < 1e-10
    
    def test_min_clamp(self):
        """Test minimum window clamping."""
        window = _compute_wl_window(10.0, wl_window_ref=0.05, wl_ref=5000.0)
        assert window == 0.0005  # 0.05 * 0.01
    
    def test_max_clamp(self):
        """Test maximum window clamping."""
        window = _compute_wl_window(1000000.0, wl_window_ref=0.05, wl_ref=5000.0)
        assert window == 5.0  # 0.05 * 100


class TestMergeLines:
    """Tests for line merging."""

    def test_merge_single(self):
        """Test merging a single LineData (no-op)."""
        ld = LineData(
            wavelength=np.array([5000.0, 5001.0]),
            species_code=np.array([326, 326]),
            loggf=np.array([-1.0, -2.0]),
            e_lower=np.zeros(2),
            e_upper=np.zeros(2),
            j_lower=np.zeros(2),
            j_upper=np.zeros(2),
            lande_lower=np.zeros(2),
            lande_upper=np.zeros(2),
            gamma_rad=np.zeros(2),
            gamma_stark=np.zeros(2),
            gamma_vdw=np.zeros(2),
        )
        
        merged = _merge_lines_simple([ld])
        assert merged.nlines == 2

    def test_merge_multiple_sorted(self):
        """Test merging multiple LineData objects."""
        ld1 = LineData(
            wavelength=np.array([5000.0, 5002.0]),
            species_code=np.array([326, 326]),
            loggf=np.array([-1.0, -2.0]),
            e_lower=np.zeros(2),
            e_upper=np.zeros(2),
            j_lower=np.zeros(2),
            j_upper=np.zeros(2),
            lande_lower=np.zeros(2),
            lande_upper=np.zeros(2),
            gamma_rad=np.zeros(2),
            gamma_stark=np.zeros(2),
            gamma_vdw=np.zeros(2),
            linelist_idx=np.array([0, 0]),
            ranks=np.full((2, 9), 3, dtype=np.int8),
            mergeable=np.ones(2, dtype=bool),
        )
        
        ld2 = LineData(
            wavelength=np.array([5001.0, 5003.0]),
            species_code=np.array([327, 327]),
            loggf=np.array([-1.5, -2.5]),
            e_lower=np.zeros(2),
            e_upper=np.zeros(2),
            j_lower=np.zeros(2),
            j_upper=np.zeros(2),
            lande_lower=np.zeros(2),
            lande_upper=np.zeros(2),
            gamma_rad=np.zeros(2),
            gamma_stark=np.zeros(2),
            gamma_vdw=np.zeros(2),
            linelist_idx=np.array([1, 1]),
            ranks=np.full((2, 9), 3, dtype=np.int8),
            mergeable=np.ones(2, dtype=bool),
        )
        
        merged = _merge_lines_simple([ld1, ld2])
        assert merged.nlines == 4
        # Should be sorted by wavelength
        assert list(merged.wavelength) == [5000.0, 5001.0, 5002.0, 5003.0]
        # Check linelist_idx preserved
        assert list(merged.linelist_idx) == [0, 1, 0, 1]


class TestParseElementFilter:
    """Tests for element filter parsing."""

    def test_single_element_all_ions(self):
        """Test parsing single element without ionization."""
        codes = _parse_element_filter("Fe")
        assert len(codes) > 0
        # Should include Fe I (326), Fe II (327), etc.
        assert 326 in codes
        assert 327 in codes

    def test_single_element_specific_ion(self):
        """Test parsing element with ionization stage."""
        codes = _parse_element_filter("Fe 1")
        # Fe 1 means neutral (Fe I, charge=0)
        # Should include main isotope (326) and all Fe isotopes
        assert 326 in codes
        # All should be Fe with charge 0
        for code in codes:
            from vald.species import get_species
            s = get_species(code)
            assert s.name == "Fe"
            assert s.charge == 0

    def test_multiple_elements(self):
        """Test parsing multiple elements."""
        codes = _parse_element_filter("Fe 1, Ca 2")
        assert 326 in codes  # Fe I
        assert 192 in codes  # Ca II

    def test_empty_filter(self):
        """Test empty filter string."""
        codes = _parse_element_filter("")
        assert codes == []
