# Backend Processing Setup

## Overview

This directory contains the C source code for VALD's email-based request processing system.

## Direct Submission (Django Integration)

Django can now bypass the email system and call `parserequest` directly:

1. User submits form → Django creates `Request` record
2. Django creates `request.{UUID}` file in `EMS/DJANGO_WORKING/`
3. Django calls `parserequest request.{UUID} {ClientName}`
4. `parserequest` generates `job.{UUID}` script
5. Django executes `job.{UUID}`
6. Output appears as `{ClientName}.{UUID}.gz` in `public_html/FTP/`
7. Django updates `Request` status and `output_file` path

## Compiling parserequest

To compile the `parserequest` binary:

1. Create `valdems_local.h` with local configuration:
```c
#define VALD_HOME "/home/user/vald-www/"
#define VALD_FTP_DIR "/home/user/vald-www/public_html/FTP/"
#define VALD_FTP "http://your-server.com/FTP/"
// ... other defines
```

2. Create `valdems.h` with general VALD constants

3. Compile:
```bash
cd backend
gcc -o ../bin/parserequest parserequest.c -lm
gcc -o ../bin/parsemail parsemail.c
```

## File Naming

- **Email-based**: Uses sequential IDs `request.000123` → `ClientName.000123.gz`
- **Django-based**: Uses UUIDs `request.{uuid}` → `ClientName.{uuid}.gz`

This ensures no conflicts between the two systems during testing/transition.

## Settings

Configure in `vald_web/settings.py`:

```python
VALD_DIRECT_SUBMISSION = True  # Enable direct submission
VALD_PARSEREQUEST_BIN = BASE_DIR / 'bin' / 'parserequest'
VALD_WORKING_DIR = BASE_DIR / 'EMS' / 'DJANGO_WORKING'
VALD_FTP_DIR = BASE_DIR / 'public_html' / 'FTP'
```

## Dependencies

The parserequest binary calls other VALD extraction binaries:
- `PROG_PRESELECT` - Line preselection
- `PROG_FORMAT` - Output formatting
- `PROG_HFS_SPLIT` - Hyperfine structure splitting
- `PROG_POST_HFS_FORMAT` - Post-HFS formatting
- `PROG_SHOWLINE` - Show line display

These need to be in `bin/` and referenced in `valdems_local.h`.
