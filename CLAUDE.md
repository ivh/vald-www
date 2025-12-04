# VALD Django Implementation - Technical Documentation for Claude

Technical context for Claude Code sessions. See README.md for user documentation.

## Project Overview

**Goal**: Django drop-in replacement for 30-year-old PHP VALD web interface.

**Status**: Production-ready. All request types working (Extract All/Element/Stellar, Show Line, Show Line ONLINE).

**User**: Tom (sysadmin, prefers concise technical explanations, "pls" not "please")

## Architecture

**Job Execution**: Python-based via `job_runner.py`
- Generates `pres_in` files directly from request parameters
- Calls Fortran binaries via `subprocess.Popen` pipes
- No shell scripts, no C compilation required

**Linelist Configuration**: Database-backed via Django models
- `Linelist` - Master catalog (377 linelists imported from default.cfg)
- `Config` - Configuration sets (system default or user-specific)
- `ConfigLinelist` - Junction table with priority and rank weights
- Generates `.cfg` file on-the-fly for each job in the working directory

## Key Files

**vald/backend.py** - Request submission:
- `submit_request_direct()` - Main entry point for job execution
- `uuid_to_6digit()` - Converts UUID to 6-digit number via SHA256
- `JobQueue` - Thread pool for parallel processing

**vald/job_runner.py** - Job execution:
- `JobConfig` - Dataclass with all job parameters
- `JobRunner` - Runs Fortran binaries via subprocess
- `create_job_config()` - Creates JobConfig from Request model
- `get_config_path_for_user()` - Generates temp config file from database

**vald/models.py**:
- `Request` - Tracks submissions (UUID, FK to User, JSONField parameters, status)
- `User`, `UserEmail` - Password authentication with activation tokens
- `UserPreferences` - Unit preferences (energyunit, waveunit, medium, vdwformat)
- `Linelist` - Master catalog of available linelists
- `Config` - Configuration sets (user-specific or system default)
- `ConfigLinelist` - Many-to-many with priority and rank weights

**vald/views.py**:
- `get_current_user()` - Gets User from `session['user_id']`
- `handle_extract_request()` - Routes to direct or email mode
- `request_detail()` - Status page with auto-refresh
- `download_request()` - Serves output files

## pres_in File Format

The `pres_in.NNNNNN` file controls preselect5 behavior:
- Line 1: wavelength range (start,end)
- Line 2: max lines
- Line 3: element filter (empty for extract all)
- Line 4: config file path (quoted, absolute path)
- Line 5: 13 flags (a b c d e f g h i j k l m)

**Flags:**
- a: format (0=short eV, 1=long eV, 3=short cm⁻¹, 4=long cm⁻¹)
- b-f: have rad/stark/waals/lande/term
- g: extended vdw (1 if vdwformat='extended')
- h-i: Zeeman/Stark (not implemented)
- j: medium (0=air, 1=vacuum)
- k: waveunit (0=Å, 1=nm, 2=cm⁻¹)
- l: isotopic scaling (0=off, 1=on)
- m: HFS splitting

## Output Files

- **Extract requests**: `{ClientName}.NNNNNN.gz` + `{ClientName}.NNNNNN.bib.gz` (optional)
- **Showline requests**: `{ClientName}.NNNNNN.txt` (no bib file)
- **ID format**: 6-digit from UUID hash
- **Location**: `public_html/FTP/`

## Common Errors

**"Output file not found"**: Job execution failed or files not moved correctly
**"Can't open input data file"**: pres_in file missing/misnamed
**"No default config found"**: Run `python manage.py import_default_config /path/to/default.cfg`

## Design Decisions

- Single `Request` model for all types (JSONField for flexibility)
- Request ownership via User FK (not email string) - works across multiple emails
- Session stores `user_id` for efficient lookups
- UserPreferences in database for atomic updates
- Linelist configs in database (migrated from .cfg files)
- UUID → 6-digit conversion for backend compatibility
- Job isolation via subdirectories (prevents race conditions)

## Migration Commands

```bash
# Import system default config (required before first use)
python manage.py import_default_config /path/to/default.cfg

# Import existing personal configs from files
python manage.py import_persconf --all  # All files in config/personal_configs/
python manage.py import_persconf ThomasMarquart.cfg  # Single file
```
