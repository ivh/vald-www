# VALD Backend Modernization - Consolidated Plan

**Goal:** Replace file-based config and C/shell job handling with Django, leaving only Fortran binary DB access (wrapped via f2py).

**Current state:**
- ✅ Auth: Django-based (User, UserEmail, UserPreferences)
- ❌ Configs: File-based (default.cfg, personal_configs/*.cfg)
- ❌ Job handling: parserequest.c → job.NNNNNN shell scripts → Fortran
- ❌ Fortran: Standalone binaries called via subprocess

**Target state:**
- ✅ Auth: Django-based (already done)
- ✅ Configs: Django DB models
- ✅ Job handling: Python direct subprocess calls (no C, no shell scripts)
- ✅ Fortran: f2py wrapper for binary DB access only

---

## Phase 1: Replace parserequest.c with Python (1-2 days) ✅ COMPLETE

**Status:** Implemented in `vald/job_runner.py`

**What was done:**
- Created `JobConfig` dataclass for job parameters
- Created `JobRunner` class that:
  - Generates `pres_in.NNNNNN` files directly
  - Generates `select.input` for stellar extractions
  - Generates `show_in.NNNNNN_NNN` for showline queries
  - Runs Fortran binaries directly via subprocess.Popen pipes
  - Handles HFS splitting pipeline
  - Compresses and moves output to FTP directory
- Updated `backend.py` with:
  - `_submit_with_job_runner()` - New Python implementation
  - `_submit_with_parserequest()` - Legacy C implementation
  - `submit_request_direct()` dispatches based on `VALD_USE_JOB_RUNNER` setting
- Added `VALD_USE_JOB_RUNNER = False` setting (opt-in for testing)

**To enable:** Set `VALD_USE_JOB_RUNNER = True` in settings.py

---

## Phase 2: Config Management in Django DB (2-3 days) ✅ COMPLETE

**Status:** Implemented with models, migration, import command, and admin UI.

**What was done:**
- Created models in `vald/models.py`:
  - `Linelist` - Master catalog of available linelists
  - `Config` - Configuration sets (user-specific or system default)
  - `ConfigLinelist` - Many-to-many with priority and rank weights
- Created migration `0008_add_config_models.py`
- Created `import_default_config` management command
- Imported default.cfg (377 linelists)
- Created admin UI with inline editing
- Added `get_config_path_for_user()` to job_runner
- Added `VALD_USE_DB_CONFIG` setting

**To enable:** Set `VALD_USE_DB_CONFIG = True` in settings.py

### Benefits:
- No manual .cfg file editing
- Validation (can't create invalid configs)
- Audit trail (who changed what when)
- Query: "Which users use deprecated linelist X?"

---

## Phase 3: f2py Fortran Wrapper (DEFERRED - Complex)

**Status:** After analysis, full f2py wrapping is complex due to:
- 2227 lines of Fortran with 14+ STOP statements
- Heavy use of C interop (UKOPEN/UKREAD for binary DB)
- Complex global state in the merging algorithm
- Would require significant refactoring of production Fortran code

### Revised Approach: Hybrid

Instead of full f2py, implement a **hybrid approach**:

1. **Keep preselect5 as binary** - It reads the proprietary DB format via C functions
2. **Implement Python merging** - Port the merge algorithm to Python (optional, for flexibility)
3. **Implement Python formatting** - Port presformat to Python (simpler than f2py)

### What's Already Done:
- Phase 1: No more shell scripts or C parsing - Python calls Fortran directly via subprocess
- Phase 2: Config in database - no more file parsing for config

### Future Options:
1. **Minimal f2py:** Just wrap UKREAD to get raw line data, then process in Python
2. **Full Python:** Reverse-engineer binary format and read directly in Python
3. **Keep status quo:** The subprocess approach works well enough

### Benefits of Current State:
- No C compilation needed (parserequest.c eliminated)
- No shell script generation
- Config management in Django
- Better error handling via Python
- Foundation for future improvements

---

## Current Status Summary

| Component | Old | New | Status |
|-----------|-----|-----|--------|
| Request parsing | parserequest.c | job_runner.py | ✅ Done |
| Job execution | Shell scripts | subprocess.Popen | ✅ Done |
| Config storage | .cfg files | Django DB | ✅ Done |
| Config generation | Static files | On-the-fly from DB | ✅ Done |
| Binary DB access | Fortran subprocess | Fortran subprocess | No change |
| Line merging | Fortran | Fortran | No change |
| Output formatting | Fortran | Fortran | No change |

---

## How to Enable New Features

### Enable Python Job Runner:
```python
# In settings.py
VALD_USE_JOB_RUNNER = True
```

### Enable Database Configs:
```python
# In settings.py  
VALD_USE_DB_CONFIG = True
```

### Import Default Config:
```bash
python manage.py import_default_config /path/to/default.cfg
```

---

## Decisions Made

1. **Config migration:** Fresh start - no import of existing personal configs
2. **f2py scope:** DEFERRED - Fortran binaries called via subprocess (works well enough)
3. **No external binaries:** C parsing eliminated; Fortran called directly via subprocess
4. **Testing:** Manual verification via queries after implementation

---

## Files Created/Modified

### Phase 1 (Complete):
- CREATED: `vald/job_runner.py` - Direct Fortran execution
- MODIFIED: `vald/backend.py` - Added `_submit_with_job_runner()` and dispatch logic
- MODIFIED: `vald_web/settings.py` - Added `VALD_USE_JOB_RUNNER` setting

### Phase 2 (Complete):
- MODIFIED: `vald/models.py` - Added Linelist, Config, ConfigLinelist models
- CREATED: `vald/migrations/0008_add_config_models.py`
- CREATED: `vald/management/commands/import_default_config.py`
- MODIFIED: `vald/admin.py` - Added admin classes for config management
- MODIFIED: `vald_web/settings.py` - Added `VALD_USE_DB_CONFIG` setting
