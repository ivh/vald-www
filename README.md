# VALD Web Interface - Django Version

Django replacement for the 30-year-old PHP-based VALD (Vienna Atomic Line Database) web interface.

## Features

- Password authentication with activation tokens
- 5 request forms: Extract All/Element/Stellar, Show Line, Show Line ONLINE
- **Request tracking** - real-time status updates, download links
- **Dual backend options**:
  - Fortran binaries (preselect5, presformat5) - production default
  - Python extraction (extraction.py with C++ extension) - modern alternative (~0.1% output difference)
- **Hybrid architecture** - supports both direct and email-based modes
- User preferences stored in database (energy units, wavelength, medium)
- Database-driven linelist configurations (replaces .cfg files)
- Job queue system with parallel processing
- Re-uses original HTML/CSS for familiarity

See **MODERNIZATION_PLAN.md** for Python extraction details and **EXTRACTION_COMPARISON.md** for performance comparison.

## Requirements

- Python 3.11+
- SQLite (included)
- **For Fortran backend**: VALD binaries in `$VALD_HOME/bin/` (preselect5, presformat5, select5, showline4.1)
- **For Python backend**: C++ compiler (for building extension), CMake, CVALD3 data files
- SMTP server (optional, for email mode)

## Installation

### Using uv (recommended)

```bash
# Install dependencies and build C++ extension
uv sync

# Run migrations
uv run python manage.py migrate

# Sync user register
uv run python manage.py sync_register_files

# Import linelists from config file
uv run python manage.py import_linelists config/default.cfg

# Set VALD_HOME environment variable
export VALD_HOME=/path/to/VALD3
```

### Using pip

```bash
# Install dependencies (builds C++ extension automatically)
python -m pip install -e .

# Run migrations
python manage.py migrate

# Sync user register
python manage.py sync_register_files

# Set VALD_HOME
export VALD_HOME=/path/to/VALD3
```

## Running

```bash
python manage.py runserver
```

Server runs at http://127.0.0.1:8000/

## Configuration

### Direct Submission Mode (Recommended)

Set in `vald_web/settings.py`:
```python
VALD_DIRECT_SUBMISSION = True  # Call binaries directly
VALD_MAX_WORKERS = 2           # Parallel job limit
```

Requires VALD binaries in `$VALD_HOME/bin/`: `parserequest`, `preselect5`, `select5`, `showline4.1`, etc.

### Email Mode (Legacy)

```python
VALD_DIRECT_SUBMISSION = False  # Use email system
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'localhost'
EMAIL_PORT = 25
```

### User Registration

Edit `config/clients.register`:
```
#$ Full Name
email@domain.com
```

Run `python manage.py sync_register_files` after changes.

## Architecture

**Direct Mode (default):**
1. Creates `request.NNNNNN` in job subdirectory
2. Runs `parserequest` from subdirectory (critical for correct file naming)
3. Executes generated `job.NNNNNN` script
4. Output: `{ClientName}.NNNNNN.gz` (extract) or `.txt` (showline)
5. Real-time status updates

**Email Mode:**
- Sends email to local mail spool
- Backend daemon processes requests asynchronously
- Uses sequential IDs instead of UUIDs

## Key Technical Notes

- **UUID to 6-digit conversion**: Backend expects numeric IDs, converts UUID via SHA256 hash
- **Parserequest working directory**: Must run FROM job subdirectory for correct `pres_in.NNNNNN` naming
- **Showline requests**: No bib files, output is `result.NNNNNN` → moved to FTP as `.txt`
- **Extract requests**: Create `.gz` and `.bib.gz` files
- **Job queue**: Thread pool limits parallel execution (default 2 workers)

## Troubleshooting

**"Output file not found"** → Check `parserequest` ran from correct directory
**"Can't open input data file"** → `pres_in.*` file missing or misnamed
**"User not registered"** → Run `python manage.py sync_register_files`

## References

- VALD website: http://vald.astro.uu.se/
- Django 5.2 docs
- Backend C sources in `backend/` (reference only)
