# VALD Django Implementation - Technical Documentation for Claude

Technical context for Claude Code sessions. See README.md for user documentation.

## Project Overview

**Goal**: Django drop-in replacement for 30-year-old PHP VALD web interface.

**Status**: Production-ready. All request types working (Extract All/Element/Stellar, Show Line, Show Line ONLINE).

**User**: Tom (sysadmin, prefers concise technical explanations, "pls" not "please")

## Architecture

**Two Job Runner Modes (controlled by `VALD_USE_JOB_RUNNER` setting):**

### Legacy Mode (`VALD_USE_JOB_RUNNER = False`, default):
- Uses `parserequest.c` binary to generate job scripts
- Patches generated `pres_in.NNNNNN` and `job.NNNNNN` files
- Executes shell scripts that pipe Fortran binaries

### New Python Mode (`VALD_USE_JOB_RUNNER = True`):
- Uses `job_runner.py` for direct Fortran execution
- Generates `pres_in` files directly from request parameters
- Calls Fortran binaries via `subprocess.Popen` pipes
- No shell scripts, no C compilation required

**Two Config Modes (controlled by `VALD_USE_DB_CONFIG` setting):**

### File Mode (`VALD_USE_DB_CONFIG = False`, default):
- Uses `.cfg` files in `config/` directory
- Personal configs in `config/personal_configs/{ClientName}.cfg`

### Database Mode (`VALD_USE_DB_CONFIG = True`):
- Uses `Linelist`, `Config`, `ConfigLinelist` models
- Generates `.cfg` file on-the-fly from database
- Admin UI for linelist management

## Key Files

**vald/backend.py** - Request submission:
- `submit_request_direct()` - Dispatches to `_submit_with_job_runner()` or `_submit_with_parserequest()`
- `_submit_with_job_runner()` - New Python implementation
- `_submit_with_parserequest()` - Legacy C implementation
- `uuid_to_6digit()` - Converts UUID to 6-digit number via SHA256
- `JobQueue` - Thread pool for parallel processing

**vald/job_runner.py** - New Python job execution:
- `JobConfig` - Dataclass with all job parameters
- `JobRunner` - Runs Fortran binaries via subprocess
- `create_job_config()` - Creates JobConfig from Request model
- `get_config_path_for_user()` - Gets config file (file or database)

**vald/models.py**:
- `Request` - Tracks submissions (UUID, FK to User, JSONField parameters, status)
- `User`, `UserEmail` - Password authentication with activation tokens
- `UserPreferences` - Unit preferences (energyunit, waveunit, medium, vdwformat)
- `Linelist` - Master catalog of available linelists (377 imported from default.cfg)
- `Config` - Configuration sets (user-specific or system default)
- `ConfigLinelist` - Many-to-many with priority and rank weights

**vald/views.py**:
- `get_current_user()` - Gets User from `session['user_id']`
- `handle_extract_request()` - Routes to direct or email mode
- `request_detail()` - Status page with auto-refresh
- `download_request()` - Serves output files

## pres_in File Format

The `pres_in.NNNNNN` file controls preselect5 behavior:
- Line 3: element filter (empty for extract all)
- Line 4: config file path (quoted, absolute path)
- Line 5: 13 flags (a b c d e f g h i j k l m)

**Flags (from preselect3 docs):**
- a: format (0=short eV, 1=long eV, 3=short cm⁻¹, 4=long cm⁻¹)
- b-f: have rad/stark/waals/lande/term
- g: extended vdw (1 if vdwformat='extended')
- h-i: Zeeman/Stark (not implemented)
- j: medium (0=air, 1=vacuum)
- k: waveunit (0=Å, 1=nm, 2=cm⁻¹)
- l: isotopic scaling (0=off, 1=on)
- m: HFS splitting

## Critical Bug Fixes

### 1. Showline None Values (2025-11-24)
**Problem**: Empty form fields stored as Python `None` → written as string `"None"` in request file → parser errors
**Fix**: `format_request_file()` now checks `if wvl is not None and win is not None` before adding lines

### 2. Parserequest Working Directory (2025-11-24)
**Problem**: `parserequest` extracts ID from filename → creates `pres_in.000000` when run as `parserequest 917714/request.917714`
**Fix**: Run `parserequest` FROM job subdirectory, not parent → creates `pres_in.917714` correctly

### 3. User Preferences Not Applied (2025-11-25)
**Problem**: Unit preferences (waveunit, energyunit, medium) not included in request parameters
**Fix**: Merge `user.get_preferences().as_dict()` into params before creating Request; patch pres_in line 5 flags

## Output Files

- **Extract requests**: `{ClientName}.NNNNNN.gz` + `{ClientName}.NNNNNN.bib.gz` (optional)
- **Showline requests**: `result.NNNNNN` → moved to `{ClientName}.NNNNNN.txt` (no bib file)
- **ID format**: 6-digit from UUID hash (direct mode) or sequential (email mode)
- **Location**: `public_html/FTP/`

## Common Errors

**"Output file not found"**: parserequest ran from wrong directory or files not moved correctly
**"Can't open input data file"**: pres_in file missing/misnamed (check working directory)
**"SELECT ERROR: Can't open input data file"**: stellar extraction can't find pres_in.NNNNNN
**"Badly placed ()'s"**: Request file has invalid syntax (check for "None" strings)
**"User not registered"**: Run `python manage.py sync_register_files`

## Design Decisions

- Single `Request` model for all types (JSONField for flexibility)
- Request ownership via User FK (not email string) - works across multiple emails
- Session stores `user_id` for efficient lookups (not repeated email→user queries)
- UserPreferences in database (not file-based) for atomic updates
- Personal linelist configs remain file-based in `config/personal_configs/`
- UUID → 6-digit conversion for backend compatibility
- Job isolation via subdirectories (prevents race conditions)
- Keep email mode available (proven system, gradual migration)

## Adding New Request Types

1. Add form class in `vald/forms.py`
2. Add template in `vald/templates/vald/`
3. Add request template in `requests/`
4. Add view + URL route
5. Update `format_request_file()` in backend.py
6. Handle output file type if different from `.gz`
