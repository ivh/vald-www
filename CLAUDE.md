# VALD Django Implementation - Technical Documentation for Claude

This document provides technical context for future Claude Code sessions working on this project.

## Project Overview

**Goal**: Django drop-in replacement for 30-year-old PHP-based VALD (Vienna Atomic Line Database) web interface.

**Status**: Functional replacement with enhanced features (request tracking, direct backend submission).

**User**: Tom (system administrator, familiar with the legacy system, prefers concise technical communication)

## Architecture Evolution

### Original PHP System (30+ years old)
- Users submit forms → emails sent to local mail spool
- Backend daemon (`service-ems.sh` via at-jobs) runs every 10 minutes:
  - `parsemail` parses mail spool, extracts requests, assigns sequential IDs
  - Creates `request.NNNNNN` files
  - Generates `process` shell script
  - `parserequest` generates `job.NNNNNN` scripts
  - Jobs call VALD extraction binaries (preselect, format, hfs_split, etc.)
  - Results saved as `{ClientName}.NNNNNN.gz` in `public_html/FTP/`
  - Files deleted after 48 hours
  - Email reply with download link sent to user

### Django Implementation (Current)

**Hybrid Architecture**: Supports both email-based (legacy) and direct submission modes.

#### Email Mode (`VALD_DIRECT_SUBMISSION = False`)
- Django sends email to local mail spool
- Backend daemon processes as before
- Request records created for tracking but status stays "pending"
- Users get email with download link (legacy behavior)

#### Direct Mode (`VALD_DIRECT_SUBMISSION = True`)
- Django bypasses email entirely
- Creates `request.{UUID}` files in `EMS/DJANGO_WORKING/`
- Calls `parserequest` binary directly with ClientName parameter
- Executes generated `job.{UUID}` script synchronously
- Output: `{ClientName}.{UUID}.gz` in `public_html/FTP/`
- Updates Request model: pending → processing → complete/failed
- User redirected to request detail page with auto-refresh

**Key Insight**: UUID-based filenames ensure no conflicts between email and direct modes during parallel testing.

## Critical Files & Their Roles

### Backend Processing (Reference Only)
- `backend/parsemail.c` - Email parser (assigns sequential IDs, validates users)
- `backend/parserequest.c` - Request parser (generates job scripts)
- These are **reference only** - deployed systems have compiled binaries in `bin/`

### Django App Structure

**vald/models.py**:
- `Request` - Tracks all submissions (email + direct)
  - UUID for URL-safe identifiers
  - JSONField for flexible parameter storage (all request types use one model)
  - Status: pending/processing/complete/failed
  - Methods: `output_exists()`, `get_output_size()`, `is_pending()`, `is_complete()`
- `User`, `UserEmail` - File-based authentication (from clients.register)
- `UserPreferences` - Energy units, wavelength units, medium, VdW format
- `PersonalConfig`, `LineList` - Personal linelist configurations

**vald/backend.py** (Direct Submission):
- `get_client_name(email)` - Extracts alphanumeric ClientName from register
- `format_request_file(request_obj)` - Converts Django form data to VALD request format
- `submit_request_direct(request_obj)` - Main submission handler:
  1. Creates `request.{UUID}` file
  2. Calls `parserequest request.{UUID} {ClientName}`
  3. Executes `job.{UUID}`
  4. Returns output file path or error

**vald/views.py**:
- `handle_extract_request()` - Checks `VALD_DIRECT_SUBMISSION` setting
  - If True: calls `submit_request_direct()`, updates status
  - If False: sends email (legacy)
- `request_detail()` - Shows request status, auto-refreshes if pending
- `download_request()` - Serves output files securely (checks user ownership)
- `my_requests()` - Lists all user requests with status counts

**vald/forms.py**:
- Django Forms for all request types (server-side validation)
- Fixed issues: `viaftp` empty string → `'email'`, removed buggy JS validation
- `ShowLineOnlineForm` - Numeric validation for window size (was string comparison bug)

**vald/utils.py**:
- `validate_user_email()` - Checks clients.register files
- `render_request_template()` - PHP-style template variable substitution
- `spam_check()` - Content filtering for contact form

### Templates

**Key templates**:
- `vald/templates/vald/base.html` - Main layout (reuses original PHP HTML/CSS)
- `vald/templates/vald/request_detail.html` - Status page with auto-refresh meta tag
- `vald/templates/vald/my_requests.html` - Request list with status badges

**Template filter** (`vald/templatetags/vald_extras.py`):
- `pprint` - Pretty-prints JSONField parameters

### Configuration Files

**Request templates** (`requests/*.txt`):
- VALD-specific format with `$variable` placeholders
- Example: `begin request\nextract all\n$waveunit\n$energyunit\nend request`

**Client registers** (`config/clients.register`):
```
#$ Full Name
# affiliation line
email1@domain.com
email2@domain.com

#$ Another User
...
```

**Management command**: `python manage.py sync_register_files [--file=/path/to/register] [--dry-run]`

## Important Technical Details

### Form Validation Bug Fixes
1. **viaftp field**: Empty string `""` treated as "no value" by Django → changed to `'email'`
2. **Show Line ONLINE JS**: String comparison `"1" > "5"` → removed all JS, added server-side numeric validation

### Email Connection Issue
- `[Errno 111] Connection refused` on localhost:587
- Resolution: Configure proper SMTP or use console backend for testing

### Request File Format

Example generated by `format_request_file()`:
```
begin request
extract all
default configuration
via ftp
short format
waveunit angstrom
energyunit eV
medium air
isotopic scaling on
5000.0, 6000.0
end request
```

### ClientName Extraction
From `parsemail.c` line 86: Only alphanumeric characters allowed.
- "John Doe" → "JohnDoe"
- Local users get "_local" suffix

### Output Files
- Pattern: `{ClientName}.{ID}.gz` and `{ClientName}.{ID}.bib.gz`
- Email mode: ID = 6-digit sequential (000123)
- Direct mode: ID = UUID (e.g., a1b2c3d4-...)
- Location: `public_html/FTP/`
- Auto-delete after 48 hours (email mode only, handled by service-ems.sh)

### Database Models

**Request.parameters** JSONField examples:
```json
{
  "stwvl": "5000.0",
  "endwvl": "6000.0",
  "format": "short",
  "waveunit": "angstrom",
  "energyunit": "eV",
  "medium": "air",
  "viaftp": "email",
  "pconf": "default",
  "isotopic_scaling": "on"
}
```

## Common Pitfalls & Solutions

### 1. VALD Binaries Not Available
**Problem**: Development environment doesn't have VALD binaries
**Solution**: Set `VALD_DIRECT_SUBMISSION = False` to use email mode, or use console email backend for testing

### 2. Working Directory Permissions
**Problem**: Django can't write to `EMS/DJANGO_WORKING/`
**Solution**: Ensure directory exists and is writable by web server user

### 3. Request Status Stuck on "Pending"
- Email mode: Expected behavior (backend updates status if integrated)
- Direct mode: Check binary paths, execution permissions, error logs

### 4. Output File Not Found
- Check `VALD_FTP_DIR` path is correct
- Verify `parserequest` created files in expected location
- Look for job script errors in working directory

## Development Workflow

### Testing Direct Submission
```python
# 1. Create test request
from vald.models import Request
req = Request.objects.create(
    user_email='test@example.com',
    user_name='Test User',
    request_type='extractall',
    parameters={'stwvl': '5000', 'endwvl': '6000', ...},
    status='pending'
)

# 2. Submit directly
from vald.backend import submit_request_direct
success, result = submit_request_direct(req)

# 3. Check result
if success:
    print(f"Output: {result}")
    print(f"Status: {req.status}")
else:
    print(f"Error: {result}")
```

### Adding New Request Types
1. Add form class in `vald/forms.py`
2. Add template in `vald/templates/vald/`
3. Add request template in `requests/`
4. Add view handler in `vald/views.py`
5. Add URL route in `vald/urls.py`
6. Update `format_request_file()` in `vald/backend.py`

### Database Migrations
```bash
python manage.py makemigrations vald
python manage.py migrate
```

## User's Preferences

**Tom's communication style**:
- Concise, technical explanations
- "pls" instead of "please"
- Appreciates when asked clarifying questions
- Prefers showing code over explaining
- Comfortable with system administration tasks

**Design decisions made**:
- Single Request model for all types (using JSONField)
- UUID-based naming for isolation from email system
- Synchronous processing in direct mode (simpler, immediate feedback)
- Keep email mode available (proven system, don't force migration)
- Reuse original HTML/CSS (users familiar with interface)

## Git Workflow

Branch: `claude/review-php-forms-018bX5D7779ZNHxwLxooEFbW`

Commit message style: Detailed with bullet points, includes reasoning.

## Next Steps / TODO

- [ ] Test direct submission with actual VALD binaries
- [ ] Implement user password activation flow (mentioned but not priority)
- [ ] Consider async processing for direct mode (Celery/background tasks)
- [ ] Add monitoring/logging for production
- [ ] Document deployment process for production VALD server

## Key Lessons Learned

1. Django ChoiceField treats empty strings specially in validation
2. JavaScript string comparison bugs can persist for decades
3. UUID-based naming prevents conflicts during parallel system testing
4. File-based authentication is simpler than expected with Django
5. Hybrid architectures allow gradual migration without forcing users
6. The old system's email-based architecture is actually quite elegant for async processing

## Questions to Ask User

When uncertain about:
- VALD binary paths/availability
- Whether to prioritize email vs direct mode
- File permissions and deployment environment
- How much to preserve from original PHP vs modernize
- Performance requirements (sync vs async processing)

## References

- VALD website: http://vald.astro.uu.se/
- Django 5.2.8 documentation
- Original PHP code in `public_html/php/vald.php` (reference)
- Backend C sources in `backend/` (reference only, binaries provided on deployment)
