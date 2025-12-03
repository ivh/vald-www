"""
Integration tests comparing Python extraction against preselect5 Fortran binary.

These tests are marked with 'preselect5' and skip if the binary is not available.
Run with: pytest -m preselect5
Skip by default: pytest (without -m preselect5)

Note: These tests use the actual production database and VALD3 data files,
so they can only run on systems with the full VALD infrastructure.
They bypass pytest-django's test database to access real data.
"""

import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

# Skip all tests in this module if preselect5 is not available
PRESELECT5_PATH = Path.home() / "VALD3" / "BIN" / "preselect5"
VALD_HOME = Path.home() / "VALD3"
DB_PATH = Path(__file__).parent.parent / "db.sqlite3"


def preselect5_available():
    """Check if preselect5 binary and production database are available."""
    return PRESELECT5_PATH.exists() and VALD_HOME.exists() and DB_PATH.exists()


pytestmark = pytest.mark.preselect5


def run_preselect5(wl_min: float, wl_max: float, element: str = "", max_lines: int = 0) -> int:
    """
    Run preselect5 and return the number of lines output.
    
    Args:
        wl_min: Minimum wavelength (Å)
        wl_max: Maximum wavelength (Å)
        element: Element filter (e.g., "Fe 1" or "" for all)
        max_lines: Maximum number of lines (0 = unlimited)
        
    Returns:
        Number of lines in output
    """
    config_path = VALD_HOME / "CONFIG" / "default.cfg"
    
    # Create pres_in content
    # Format: wl_range, max_lines, element, config_path, flags
    # Flags: format(0=short), rad, stark, waals, lande, term, ext_vdw, zeeman, stark_ext, vacuum, nm, iso, hfs
    pres_in = f"""{wl_min},{wl_max}
{max_lines}
{element}
'{config_path}'
0 0 0 0 0 0 0 0 0 0 0 0 0
"""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write pres_in file
        pres_in_path = Path(tmpdir) / "pres_in"
        pres_in_path.write_text(pres_in)
        
        # Run preselect5
        result = subprocess.run(
            [str(PRESELECT5_PATH)],
            stdin=open(pres_in_path),
            capture_output=True,
            text=True,
            cwd=str(VALD_HOME),
            timeout=60,
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"preselect5 failed: {result.stderr}")
        
        # Count output lines (each line starts with wavelength)
        # Skip header lines that don't start with a number
        nlines = 0
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if line and line[0].isdigit():
                nlines += 1
        
        return nlines


def get_real_config():
    """
    Get the default config directly from the production SQLite database.
    
    Bypasses Django ORM to avoid pytest-django test database isolation.
    """
    import sqlite3
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get the default system config
    cursor.execute("""
        SELECT id, wl_window_ref, wl_ref 
        FROM vald_config 
        WHERE user_id IS NULL AND is_default = 1
    """)
    config_row = cursor.fetchone()
    
    if config_row is None:
        conn.close()
        return None
    
    config_id = config_row['id']
    
    # Get all enabled linelists for this config
    cursor.execute("""
        SELECT 
            cl.priority, cl.mergeable, cl.replacement_window,
            cl.rank_wl, cl.rank_gf, cl.rank_rad, cl.rank_stark,
            cl.rank_waals, cl.rank_lande, cl.rank_term, cl.rank_ext_vdw, cl.rank_zeeman,
            ll.path, ll.element_min, ll.element_max
        FROM vald_configlinelist cl
        JOIN vald_linelist ll ON cl.linelist_id = ll.id
        WHERE cl.config_id = ? AND cl.is_enabled = 1
        ORDER BY cl.priority
    """, (config_id,))
    
    linelists = cursor.fetchall()
    conn.close()
    
    # Build a config-like object
    class FakeConfig:
        def __init__(self, wl_window_ref, wl_ref, linelists):
            self.wl_window_ref = wl_window_ref
            self.wl_ref = wl_ref
            self._linelists = linelists
    
    class FakeConfigLinelistSet:
        def __init__(self, linelists):
            self._linelists = linelists
        
        def filter(self, is_enabled=True):
            return self
        
        def select_related(self, *args):
            return self
        
        def order_by(self, *args):
            return self._linelists
    
    class FakeConfigLinelist:
        def __init__(self, row, idx):
            self.priority = row['priority']
            self.mergeable = row['mergeable']
            self.replacement_window = row['replacement_window']
            self.rank_wl = row['rank_wl']
            self.rank_gf = row['rank_gf']
            self.rank_rad = row['rank_rad']
            self.rank_stark = row['rank_stark']
            self.rank_waals = row['rank_waals']
            self.rank_lande = row['rank_lande']
            self.rank_term = row['rank_term']
            self.rank_ext_vdw = row['rank_ext_vdw']
            self.rank_zeeman = row['rank_zeeman']
            self.linelist = FakeLinelist(row)
    
    class FakeLinelist:
        def __init__(self, row):
            self.path = row['path']
            self.element_min = row['element_min']
            self.element_max = row['element_max']
    
    fake_linelists = [FakeConfigLinelist(row, idx) for idx, row in enumerate(linelists)]
    
    config = FakeConfig(
        config_row['wl_window_ref'],
        config_row['wl_ref'],
        fake_linelists,
    )
    config.configlinelist_set = FakeConfigLinelistSet(fake_linelists)
    
    return config


@pytest.mark.skipif(not preselect5_available(), reason="preselect5 or production DB not available")
class TestPreselect5Comparison:
    """Compare Python extraction with preselect5 output."""
    
    def test_fe_i_narrow_range(self):
        """Test Fe I extraction in narrow wavelength range."""
        from vald.extraction import extract_lines
        
        config = get_real_config()
        if config is None:
            pytest.skip("Default config not found")
        
        wl_min, wl_max = 5000, 5010
        
        # Run preselect5
        preselect_nlines = run_preselect5(wl_min, wl_max, element="Fe 1")
        
        # Run Python extraction
        lines = extract_lines(config, wl_min, wl_max, element_filter="Fe 1")
        python_nlines = lines.nlines
        
        print(f"Fe I {wl_min}-{wl_max}: preselect5={preselect_nlines}, Python={python_nlines}")
        
        # Allow 5% tolerance or 2 lines difference
        tolerance = max(2, int(preselect_nlines * 0.05))
        assert abs(preselect_nlines - python_nlines) <= tolerance, \
            f"Line count mismatch: preselect5={preselect_nlines}, Python={python_nlines}"
    
    def test_all_elements_narrow_range(self):
        """Test all elements extraction in narrow wavelength range."""
        from vald.extraction import extract_lines
        
        config = get_real_config()
        if config is None:
            pytest.skip("Default config not found")
        
        wl_min, wl_max = 5000, 5005
        
        # Run preselect5 (empty element = all)
        preselect_nlines = run_preselect5(wl_min, wl_max, element="")
        
        # Run Python extraction
        lines = extract_lines(config, wl_min, wl_max)
        python_nlines = lines.nlines
        
        print(f"All elements {wl_min}-{wl_max}: preselect5={preselect_nlines}, Python={python_nlines}")
        
        # Allow 5% tolerance
        tolerance = max(5, int(preselect_nlines * 0.05))
        assert abs(preselect_nlines - python_nlines) <= tolerance, \
            f"Line count mismatch: preselect5={preselect_nlines}, Python={python_nlines}"
    
    def test_ca_ii_wide_range(self):
        """Test Ca II extraction in wider wavelength range."""
        from vald.extraction import extract_lines
        
        config = get_real_config()
        if config is None:
            pytest.skip("Default config not found")
        
        wl_min, wl_max = 3900, 4000
        
        # Run preselect5
        preselect_nlines = run_preselect5(wl_min, wl_max, element="Ca 2")
        
        # Run Python extraction
        lines = extract_lines(config, wl_min, wl_max, element_filter="Ca 2")
        python_nlines = lines.nlines
        
        print(f"Ca II {wl_min}-{wl_max}: preselect5={preselect_nlines}, Python={python_nlines}")
        
        tolerance = max(2, int(preselect_nlines * 0.05))
        assert abs(preselect_nlines - python_nlines) <= tolerance, \
            f"Line count mismatch: preselect5={preselect_nlines}, Python={python_nlines}"
    
    def test_h_alpha_region(self):
        """Test H-alpha region extraction."""
        from vald.extraction import extract_lines
        
        config = get_real_config()
        if config is None:
            pytest.skip("Default config not found")
        
        wl_min, wl_max = 6560, 6566
        
        # Run preselect5
        preselect_nlines = run_preselect5(wl_min, wl_max, element="")
        
        # Run Python extraction
        lines = extract_lines(config, wl_min, wl_max)
        python_nlines = lines.nlines
        
        print(f"H-alpha region {wl_min}-{wl_max}: preselect5={preselect_nlines}, Python={python_nlines}")
        
        tolerance = max(5, int(preselect_nlines * 0.05))
        assert abs(preselect_nlines - python_nlines) <= tolerance, \
            f"Line count mismatch: preselect5={preselect_nlines}, Python={python_nlines}"
    
    def test_uv_region(self):
        """Test UV region extraction."""
        from vald.extraction import extract_lines
        
        config = get_real_config()
        if config is None:
            pytest.skip("Default config not found")
        
        wl_min, wl_max = 2000, 2010
        
        # Run preselect5
        preselect_nlines = run_preselect5(wl_min, wl_max, element="")
        
        # Run Python extraction  
        lines = extract_lines(config, wl_min, wl_max)
        python_nlines = lines.nlines
        
        print(f"UV region {wl_min}-{wl_max}: preselect5={preselect_nlines}, Python={python_nlines}")
        
        tolerance = max(10, int(preselect_nlines * 0.05))
        assert abs(preselect_nlines - python_nlines) <= tolerance, \
            f"Line count mismatch: preselect5={preselect_nlines}, Python={python_nlines}"
    
    def test_ir_region(self):
        """Test infrared region extraction."""
        from vald.extraction import extract_lines
        
        config = get_real_config()
        if config is None:
            pytest.skip("Default config not found")
        
        wl_min, wl_max = 15000, 15100
        
        # Run preselect5
        preselect_nlines = run_preselect5(wl_min, wl_max, element="")
        
        # Run Python extraction
        lines = extract_lines(config, wl_min, wl_max)
        python_nlines = lines.nlines
        
        print(f"IR region {wl_min}-{wl_max}: preselect5={preselect_nlines}, Python={python_nlines}")
        
        tolerance = max(5, int(preselect_nlines * 0.05))
        assert abs(preselect_nlines - python_nlines) <= tolerance, \
            f"Line count mismatch: preselect5={preselect_nlines}, Python={python_nlines}"
