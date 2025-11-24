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
- `JobQueue` - Thread pool for parallel processing

**vald/models.py**:
- `Request` - Tracks submissions (UUID, JSONField parameters, status)
- `User`, `UserEmail` - Password authentication with activation tokens
- `UserPreferences` - File-based per-user settings (energy units, etc.)
- `PersonalConfig`, `LineList` - Custom linelist configurations

**vald/views.py**:
- `handle_extract_request()` - Routes to direct or email mode
- `request_detail()` - Status page with auto-refresh
- `download_request()` - Serves output files (checks ownership)

**vald/forms.py**:
- Server-side validation for all request types
- Fixed: viaftp empty string, showline None values, JS numeric validation bugs

## Critical Bug Fixes

### 1. Showline None Values (2025-11-24)
**Problem**: Empty form fields stored as Python `None` → written as string `"None"` in request file → parser errors
**Fix**: `format_request_file()` now checks `if wvl is not None and win is not None` before adding lines
**Location**: vald/backend.py:470-479

### 2. Parserequest Working Directory (2025-11-24)
**Problem**: `parserequest` extracts ID from filename → creates `pres_in.000000` when run as `parserequest 917714/request.917714`
**Fix**: Run `parserequest` FROM job subdirectory, not parent → creates `pres_in.917714` correctly
**Location**: vald/backend.py:174-220
**Impact**: All extract requests (extractall/element/stellar)

### 3. Show_in File Movement (2025-11-24)
**Problem**: `show_in.NNNNNN_*` files not moved to job subdirectory → job script fails with "No such file"
**Fix**: Added glob pattern to move all `show_in.*` files
**Location**: vald/backend.py:297-300

### 4. Showline vs Extract Output (2025-11-24)
**Problem**: Code expected `.gz` files for all requests, showline creates `result.NNNNNN` text file
**Fix**: Check `request_type == 'showline'` → move `result.*` to FTP as `.txt`, skip bib file handling
**Location**: vald/backend.py:336-383

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
- UUID → 6-digit conversion for backend compatibility
- Synchronous job execution in direct mode (simpler, immediate feedback)
- Job isolation via subdirectories (prevents race conditions)
- Keep email mode available (proven system, gradual migration)
- Reuse original HTML/CSS (user familiarity)

## Adding New Request Types

1. Add form class in `vald/forms.py`
2. Add template in `vald/templates/vald/`
3. Add request template in `requests/`
4. Add view + URL route
5. Update `format_request_file()` in backend.py
6. Handle output file type if different from `.gz`
