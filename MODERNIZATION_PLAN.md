# VALD Backend Modernization

## Overview

**Current Status**: Phase 1 in progress (Python backend branch)

The goal is to replace all Fortran subprocess calls with native Python code that uses
a C extension for the performance-critical compressed database reading.

### Architecture

```
User Request → Django views → Python job processor
                                      ↓
                              ┌───────────────────┐
                              │  vald/lib/vald3/  │  ← nanobind C extension
                              │  (LZW decompress) │
                              └───────────────────┘
                                      ↓
                              ┌───────────────────┐
                              │  Python merging   │  ← Port from Fortran
                              │  & formatting     │
                              └───────────────────┘
                                      ↓
                              Output files (same format as before)
```

### Implementation Phases

| Phase | Component | Status | Notes |
|-------|-----------|--------|-------|
| 1 | DB Reader (vald3_decompress) | In Progress | nanobind wrapper for unkompress3.c |
| 2 | presformat replacement | Planned | Python output formatting |
| 3 | showline replacement | Planned | Simpler than preselect |
| 4 | select (stellar) | Deferred | Complex atmosphere models |

---

## Historical Context

The `job_runner.py` module replaced the legacy orchestration system that used C parsing
and shell scripts. It currently manages Fortran binary pipelines via `subprocess`.

Linelist configurations are stored in Django database models instead of `.cfg` files.

---

## Phase 1: C Extension for Database Reading

### Components

The `vald/lib/vald3/` directory contains:

1. **`unkompress3.c`** - C library for LZW decompression of CVALD3 binary files
   - `ukopen_()` - Open compressed data + descriptor files
   - `ukread_()` - Read lines in wavelength range → arrays
   - `uknext_()` - Read next record (for iteration)
   - `ukclose_()` - Cleanup

2. **`vald3_decompress.cpp`** - nanobind wrapper exposing `VALD3Reader` class
   - `query_range(wl_min, wl_max)` → dict of numpy arrays

3. **`vald3_reader.py`** - High-level Python interface
   - Wavelength queries, DataFrame conversion, air/vacuum conversion

### Build System

The C extension is built via scikit-build-core + CMake + nanobind.
See `vald/lib/vald3/CMakeLists.txt` and `pyproject.toml`.

### Data Format

CVALD3 files are LZW-compressed binary records:
- Each record = 1024 lines × 270 bytes/line (uncompressed)
- Descriptor file (.DSC3) = wavelength index for binary search
- Line format: wavelength(8) + species(4) + loggf(4) + energies(16) + J(8) + 
  Landé(8) + damping(12) + terms(210)

---

## Phase 2: Output Formatting (presformat replacement)

### Goal

Replace `presformat5` Fortran binary with Python formatting code.

### Components

1. **Unit conversions** (from `air_vac.f90`, `lconv.f90`)
   - Vacuum ↔ air wavelength
   - eV ↔ cm⁻¹ energy
   - Å ↔ nm ↔ cm⁻¹ wavelength

2. **Output formats**
   - Short format (single line per transition)
   - Long format (multi-line with term designations)
   - Model format (for synthesis codes)

3. **Bibliography tracking** - Already in Django models

---

## Phase 3: Showline (showline4.1 replacement)

Single-query line lookups. Simpler than preselect since no merging needed.

---

## Phase 4: Stellar Extraction (Deferred)

The `select5` binary involves:
- Model atmosphere reading
- Opacity calculations
- Line depth estimation

This is kept as subprocess for now.

---

## Legacy System Reference

### Old Flow (Before job_runner.py)

```
User Request
     ↓
backend.py: format_request_file() → request.NNNNNN
     ↓
parserequest.c: Parse request file → Generate job.NNNNNN shell script
                                   → Generate pres_in.NNNNNN
     ↓
backend.py: Patch pres_in with user preferences
     ↓
Execute job.NNNNNN shell script:
     ┌─────────────────────────────────────────────────┐
     │  #!/bin/sh                                      │
     │  cd /working/NNNNNN                             │
     │  preselect5 < pres_in.NNNNNN | \                │
     │  presformat5 > output.NNNNNN                    │
     │  gzip output.NNNNNN                             │
     │  mv output.NNNNNN.gz /FTP/                      │
     └─────────────────────────────────────────────────┘
     ↓
Output files in public_html/FTP/
```

### Problems Solved

1. **C dependency**: Required compiling `parserequest.c` on each platform
2. **Fragile parsing**: C code parsed request files with fixed-format assumptions
3. **Shell script generation**: Text-based script generation prone to escaping issues
4. **File patching**: Had to modify generated files to inject user preferences
5. **No error context**: Shell script failures gave minimal diagnostics
6. **Hard to extend**: Adding new request types required C code changes
7. **Config files**: Manual editing of `.cfg` files, no validation, no audit trail

---

## Current System (job_runner.py with subprocess)

```
User Request
     ↓
backend.py: Create Request model with parameters
     ↓
job_runner.py: create_job_config() → JobConfig dataclass
     ↓
job_runner.py: JobRunner.run()
     ├── Generate pres_in.NNNNNN directly from JobConfig
     ├── Generate config.cfg from database (Config model)
     └── Execute pipeline via subprocess.Popen:
         
         preselect5 ──pipe──→ presformat5 ──→ output file
              ↑                    
         pres_in.NNNNNN            
     ↓
job_runner.py: Compress and move to FTP directory
     ↓
Output files in public_html/FTP/
```

---

## Target System (Pure Python with C extension)

```
User Request
     ↓
backend.py: Create Request model with parameters
     ↓
job_runner.py: create_job_config() → JobConfig dataclass
     ↓
vald/extraction.py: extract()
     ├── Load linelist configs from database
     ├── For each linelist:
     │   └── vald3_reader.query_range() → numpy arrays
     ├── Merge lines (K-way merge by wavelength)
     ├── Apply element filter if needed
     ├── Format output (short/long format)
     └── Write + compress output
     ↓
Output files in public_html/FTP/
```

---

## Key Components

### JobConfig Dataclass

Holds all parameters needed to run a job:

```python
@dataclass
class JobConfig:
    job_id: int              # 6-digit job ID
    job_dir: Path            # Working directory
    client_name: str         # User's name for output files
    request_type: str        # extractall, extractelement, extractstellar, showline
    wl_start: float          # Wavelength range start
    wl_end: float            # Wavelength range end
    max_lines: int           # Maximum lines to extract
    element: str             # Element filter (for extractelement)
    config_path: str         # Path to linelist config file
    format_flags: List[int]  # 13 format flags for preselect
    # ... stellar parameters, showline queries, etc.
```

### JobRunner Class

Manages the execution of Fortran binaries:

```python
class JobRunner:
    def __init__(self):
        self.preselect = settings.VALD_BIN / 'preselect5'
        self.presformat = settings.VALD_BIN / 'presformat5'
        self.select = settings.VALD_BIN / 'select5'
        # ...

    def run(self, config: JobConfig) -> Tuple[bool, str]:
        """Execute the appropriate pipeline based on request type."""
        if config.request_type == 'showline':
            return self._run_showline(config)
        elif config.request_type == 'extractstellar':
            return self._run_stellar(config)
        else:
            return self._run_extract(config)
```

### Pipeline Execution

Pipelines are constructed using `subprocess.Popen` with pipes:

```python
def _run_pipeline_simple(self, pres_in, output_file, bib_file, cwd):
    """Run preselect | presformat pipeline."""
    
    # Start preselect, reading from pres_in file
    preselect_proc = subprocess.Popen(
        [str(self.preselect)],
        stdin=pres_in,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd
    )
    
    # Pipe preselect output to presformat
    out = open(output_file, 'w')
    presformat_proc = subprocess.Popen(
        [str(self.presformat)],
        stdin=preselect_proc.stdout,
        stdout=out,
        stderr=subprocess.PIPE,
        cwd=cwd
    )
    
    # Close pipe in parent to allow SIGPIPE propagation
    preselect_proc.stdout.close()
    
    # Wait for completion
    _, presformat_stderr = presformat_proc.communicate(timeout=3600)
    out.close()
    preselect_proc.wait()
    
    # Check return codes (downstream first for better error messages)
    if presformat_proc.returncode != 0:
        return (False, f"presformat failed: {presformat_stderr.decode()}")
    if preselect_proc.returncode != 0:
        return (False, f"preselect failed with code {preselect_proc.returncode}")
    
    return (True, str(output_file))
```

---

## Pipeline Types

### Extract All / Extract Element
```
preselect5 < pres_in | presformat5 > output
```

### Extract All / Element with HFS
```
preselect5 < pres_in | presformat5 | hfs_split | post_hfs_format5 > output
```

### Extract Stellar
```
preselect5 < pres_in | select5 > output
```

### Extract Stellar with HFS
```
preselect5 < pres_in | select5 | hfs_split | post_hfs_format5 > output
```

### Show Line
```
showline5 < show_in > output  (run once per query)
```

---

## Configuration System

### Database Models

Linelist configurations are stored in Django models:

1. **`Linelist`** - Master catalog of all available linelists (377 imported from default.cfg)
2. **`Config`** - Configuration sets (system default or user-specific)
3. **`ConfigLinelist`** - Links configs to linelists with priority and rank weights

### Config Generation

The job runner generates a temporary `config.cfg` file from the database:

```python
def get_config_path_for_user(user, job_dir, use_personal=True):
    config = Config.get_user_config(user) if use_personal else Config.get_default_config()
    
    # Generate temp config file
    temp_config_path = job_dir / 'config.cfg'
    with open(temp_config_path, 'w') as f:
        f.write(config.generate_cfg_content())
    
    return str(temp_config_path)
```

### Benefits of DB Configs

- No manual `.cfg` file editing
- Validation (can't create invalid configs)
- Audit trail (who changed what when)
- Query: "Which users use deprecated linelist X?"
- Admin UI for config management

---

## Benefits of the New System

1. **No C compilation**: Pure Python, works on any platform with Python 3.11+
2. **Better error handling**: Full Python tracebacks, stderr capture from Fortran
3. **Database integration**: Configs stored in Django models, editable via admin
4. **Testable**: Can unit test job creation without running Fortran
5. **Extensible**: Adding new request types is just Python code
6. **Type-safe**: Dataclasses with type hints catch errors early
7. **Debuggable**: Can inspect JobConfig before execution, log all steps

---

## File Locations

- **C extension**: `vald/lib/vald3/` (unkompress3.c, vald3_decompress.cpp)
- **Python extraction**: `vald/extraction.py` (to be created)
- **Job runner**: `vald/job_runner.py`
- **Config models**: `vald/models.py` (Linelist, Config, ConfigLinelist)
- **Backend**: `vald/backend.py`

---

## Development Notes

### Building the C Extension

```bash
cd vald-www
uv sync  # Installs dependencies and builds extension
```

### Testing

```bash
uv run pytest tests/test_vald3_reader.py -v
```

### Data Files

CVALD3 test data in `~/VALD3/CVALD3/ATOMS/`:
- `H_lines_NIST+Kurucz.CVALD3` / `.DSC3` - Hydrogen lines
- `Fe1_K14_*.CVALD3` - Iron lines (many files)

