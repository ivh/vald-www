# VALD Backend Modernization

## Overview

The `job_runner.py` module replaces the legacy orchestration system that used C parsing and shell scripts to execute VALD extraction jobs. Instead of generating and executing shell scripts, it directly manages Fortran binary pipelines from Python using `subprocess`.

Linelist configurations are now stored in Django database models instead of `.cfg` files.

## Legacy System (Before)

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

### Problems with the Legacy System

1. **C dependency**: Required compiling `parserequest.c` on each platform
2. **Fragile parsing**: C code parsed request files with fixed-format assumptions
3. **Shell script generation**: Text-based script generation prone to escaping issues
4. **File patching**: Had to modify generated files to inject user preferences
5. **No error context**: Shell script failures gave minimal diagnostics
6. **Hard to extend**: Adding new request types required C code changes
7. **Config files**: Manual editing of `.cfg` files, no validation, no audit trail

## New System (Current)

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

- **Job runner**: `vald/job_runner.py`
- **Config models**: `vald/models.py` (Linelist, Config, ConfigLinelist)
- **Backend**: `vald/backend.py`
- **Fortran binaries**: `$VALD_BIN/` (preselect5, presformat5, select5, etc.)

---

## Future: f2py Fortran Wrapper (DEFERRED)

### Current State

Fortran binaries are called via subprocess. This works well but requires:
- Compiling Fortran code on each platform
- `-std=legacy` flag for gfortran on macOS (old format strings)
- File-based IPC (pres_in files, stdout pipes)

### Potential f2py Approach

Instead of subprocess calls, wrap Fortran routines with f2py for direct Python calls:

1. **Wrap UKREAD** - Get raw line data directly into Python arrays
2. **Python merging** - Port the merge algorithm to Python
3. **Python formatting** - Port presformat to Python

### Challenges

After analysis, full f2py wrapping is complex due to:
- 2227 lines of Fortran with 14+ STOP statements
- Heavy use of C interop (UKOPEN/UKREAD for binary DB)
- Complex global state in the merging algorithm
- Would require significant refactoring of production Fortran code

### Decision

The subprocess approach works well enough. f2py is deferred until there's a compelling need (e.g., performance issues with very large extractions, or need to modify merge logic).

### Current Status

| Component | Implementation | Notes |
|-----------|---------------|-------|
| Request parsing | Python (job_runner.py) | ✅ No more C |
| Job execution | subprocess.Popen | ✅ No more shell scripts |
| Config storage | Django DB | ✅ No more .cfg files |
| Binary DB access | Fortran subprocess | Unchanged |
| Line merging | Fortran subprocess | Unchanged |
| Output formatting | Fortran subprocess | Unchanged |
