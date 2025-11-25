# VALD Django Implementation - Technical Documentation for Claude

Technical context for Claude Code sessions. See README.md for user documentation.

## Project Overview

**Goal**: Django drop-in replacement for 30-year-old PHP VALD web interface.

**Status**: Production-ready. All request types working (Extract All/Element/Stellar, Show Line, Show Line ONLINE).

**User**: Tom (sysadmin, prefers concise technical explanations, "pls" not "please")

## Architecture

**Direct Mode (default, `VALD_DIRECT_SUBMISSION = True`):**
- Converts UUID → 6-digit ID via SHA256 hash (backend uses `atol()`)
- Creates job subdirectory: `working/NNNNNN/`
- Creates `request.NNNNNN` in subdirectory
- **CRITICAL**: Runs `parserequest` FROM subdirectory for correct file naming
- Patches `pres_in.NNNNNN` with user preferences (config path, unit flags)
- Executes `job.NNNNNN` script in isolated directory
- Status tracking: pending → processing → complete/failed
- Job queue with worker threads (default 2, configurable via `VALD_MAX_WORKERS`)

**Email Mode (`VALD_DIRECT_SUBMISSION = False`):**
- Sends email to mail spool, backend daemon processes async
- Sequential IDs, no real-time status updates

## Key Files

**vald/backend.py** - Direct submission logic:
- `uuid_to_6digit()` - Converts UUID to 6-digit number via SHA256
- `submit_request_direct()` - Main handler, runs parserequest from job subdirectory
- `format_request_file()` - Converts form data to VALD request format
- Patches `pres_in.NNNNNN`: line 4 (config path), line 5 (flags for units/format)
- `JobQueue` - Thread pool for parallel processing

**vald/models.py**:
- `Request` - Tracks submissions (UUID, FK to User, JSONField parameters, status)
- `User`, `UserEmail` - Password authentication with activation tokens
- `UserPreferences` - OneToOne with User, stores unit preferences (energyunit, waveunit, medium, vdwformat, isotopic_scaling)
- `User.client_name` property - Alphanumeric name for file paths
- `User.primary_email` property - Primary email address
- `User.get_preferences()` - Returns UserPreferences, creates defaults if needed

**vald/views.py**:
- `get_current_user()` - Gets User from `session['user_id']`
- `handle_extract_request()` - Routes to direct or email mode, merges user prefs into params
- `request_detail()` - Status page with auto-refresh
- `download_request()` - Serves output files (checks ownership via User FK)

**vald/forms.py**:
- Server-side validation for all request types
- Fixed: viaftp empty string, showline None values, JS numeric validation bugs

## pres_in File Format

The `pres_in.NNNNNN` file controls preselect3 behavior:
- Line 1: wavelength range
- Line 2: max lines
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
