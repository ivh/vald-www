"""Tests for VALD3 compressed database reader."""

import pytest
import numpy as np
from pathlib import Path

from vald.vald3_reader import (
    VALD3Reader,
    get_species,
    get_species_name,
    vacuum_to_air,
    air_to_vacuum,
)
from vald.species import Species, find_species_by_name, clear_cache


# Test data paths
VALD3_DATA_DIR = Path.home() / "VALD3" / "CVALD3" / "ATOMS"
H_DATA = VALD3_DATA_DIR / "H_lines_NIST+Kurucz.CVALD3"
H_DESC = VALD3_DATA_DIR / "H_lines_NIST+Kurucz.DSC3"


@pytest.fixture
def h_reader():
    """Create reader for hydrogen test data."""
    if not H_DATA.exists():
        pytest.skip(f"Test data not found: {H_DATA}")
    reader = VALD3Reader(H_DATA, H_DESC)
    yield reader
    reader.close()


class TestVALD3Reader:
    """Tests for VALD3Reader class."""

    def test_open_close(self, h_reader):
        """Test that reader opens and closes correctly."""
        assert h_reader.is_open()
        h_reader.close()
        assert not h_reader.is_open()

    def test_query_range_returns_dict(self, h_reader):
        """Test that query_range returns a dictionary."""
        result = h_reader.query_range(4000.0, 5000.0)
        assert isinstance(result, dict)
        assert 'nlines' in result

    def test_query_range_finds_lines(self, h_reader):
        """Test that query_range finds lines in valid range."""
        # Visible range should have hydrogen lines
        result = h_reader.query_range(4000.0, 7000.0)
        assert result['nlines'] > 0

    def test_query_range_has_expected_keys(self, h_reader):
        """Test that result has all expected keys."""
        result = h_reader.query_range(4000.0, 5000.0)
        
        expected_keys = [
            'nlines', 'wavelength', 'species_code', 'loggf',
            'e_lower', 'e_upper', 'j_lower', 'j_upper',
            'lande_lower', 'lande_upper',
            'gamma_rad', 'gamma_stark', 'gamma_vdw',
        ]
        for key in expected_keys:
            assert key in result, f"Missing key: {key}"

    def test_query_range_arrays_have_correct_length(self, h_reader):
        """Test that all arrays have the same length as nlines."""
        result = h_reader.query_range(4000.0, 5000.0)
        nlines = result['nlines']
        
        if nlines > 0:
            for key, value in result.items():
                if key != 'nlines' and key != 'string_data':
                    assert len(value) == nlines, f"{key} has wrong length"

    def test_query_range_wavelengths_in_range(self, h_reader):
        """Test that returned wavelengths are within query range."""
        wl_min, wl_max = 4000.0, 5000.0
        result = h_reader.query_range(wl_min, wl_max)
        
        if result['nlines'] > 0:
            wavelengths = result['wavelength']
            assert np.all(wavelengths >= wl_min)
            assert np.all(wavelengths <= wl_max)

    def test_query_range_invalid_range_raises(self, h_reader):
        """Test that invalid range raises ValueError."""
        with pytest.raises(ValueError):
            h_reader.query_range(5000.0, 4000.0)  # min > max

    def test_context_manager(self):
        """Test that reader works as context manager."""
        if not H_DATA.exists():
            pytest.skip(f"Test data not found: {H_DATA}")
        
        with VALD3Reader(H_DATA, H_DESC) as reader:
            assert reader.is_open()
            result = reader.query_range(4000.0, 5000.0)
            assert result['nlines'] >= 0
        
        assert not reader.is_open()

    def test_file_not_found(self):
        """Test that missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            VALD3Reader("/nonexistent/file.CVALD3", "/nonexistent/file.DSC3")


class TestSpecies:
    """Tests for species lookup."""

    def test_get_species_hydrogen(self):
        """Test getting hydrogen species."""
        species = get_species(1)
        assert species is not None
        assert species.name == "H"
        assert species.charge == 0

    def test_get_species_iron(self):
        """Test getting Fe I species (index 326)."""
        species = get_species(326)
        assert species is not None
        assert species.name == "Fe"
        assert species.charge == 0
        assert species.display_name == "Fe I"

    def test_get_species_name(self):
        """Test get_species_name function."""
        assert get_species_name(1) == "H I"
        assert get_species_name(326) == "Fe I"
        assert "Unknown" in get_species_name(999999)

    def test_find_species_by_name(self):
        """Test finding species by element name."""
        fe_species = find_species_by_name("Fe")
        assert len(fe_species) > 0
        assert all(s.name == "Fe" for s in fe_species)

    def test_find_species_by_name_and_charge(self):
        """Test finding species by name and ionization."""
        fe_ii = find_species_by_name("Fe", charge=1)
        assert len(fe_ii) >= 1
        assert all(s.charge == 1 for s in fe_ii)


class TestWavelengthConversion:
    """Tests for air/vacuum wavelength conversion."""

    def test_vacuum_to_air_below_2000(self):
        """Test that wavelengths below 2000 Å are unchanged."""
        wl = np.array([1000.0, 1500.0, 1999.0])
        result = vacuum_to_air(wl)
        np.testing.assert_array_equal(result, wl)

    def test_vacuum_to_air_above_2000(self):
        """Test that wavelengths above 2000 Å are converted."""
        wl = np.array([5000.0, 6000.0, 7000.0])
        result = vacuum_to_air(wl)
        
        # Air wavelengths should be slightly smaller
        assert np.all(result < wl)
        # But not by much (< 0.03%)
        assert np.all((wl - result) / wl < 0.0003)

    def test_air_to_vacuum_roundtrip(self):
        """Test that air->vacuum->air gives close to original values."""
        wl_air = np.array([5000.0, 5500.0, 6000.0])
        wl_vacuum = air_to_vacuum(wl_air)
        wl_air_back = vacuum_to_air(wl_vacuum)
        # Formula is approximate, so allow small error
        np.testing.assert_allclose(wl_air_back, wl_air, rtol=1e-8)

    def test_known_value_halpha(self):
        """Test H-alpha wavelength conversion."""
        # H-alpha vacuum: 6564.614 Å, air: 6562.808 Å
        wl_vacuum = np.array([6564.614])
        wl_air = vacuum_to_air(wl_vacuum)
        assert abs(wl_air[0] - 6562.808) < 0.01
