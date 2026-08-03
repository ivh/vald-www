# VALD Web Interface - Django Version

Django replacement for the 30-year-old PHP-based VALD (Vienna Atomic Line Database) web interface.

## Features

- Password authentication with activation tokens
- 5 request forms: Extract All/Element/Stellar, Show Line, Show Line ONLINE
- **Request tracking** - real-time status updates, download links
- **Direct backend submission** - calls VALD binaries directly, bypasses email
- **Hybrid architecture** - supports both direct and email-based modes
- User preferences stored in database (energy units, wavelength, medium)
- Personal linelist configurations (file-based)
- Job queue system with parallel processing
- Re-uses original HTML/CSS for familiarity

## Requirements

- Python 3.11+
- SQLite (included)
- VALD binaries (in `$VALD_HOME/bin/`)
- SMTP server (optional, for email mode)

## Installation

1. **Install dependencies:**
   ```bash
   python -m pip install -r requirements.txt
   ```

2. **Run migrations:**
   ```bash
   python manage.py migrate
   ```

3. **Sync user register:**
   ```bash
   python manage.py sync_register_files
   ```

4. **Set VALD_HOME environment variable:**
   ```bash
   export VALD_HOME=/path/to/VALD3
   ```

## Running

```bash
python manage.py runserver
```

Server runs at http://127.0.0.1:8000/

## Production deployment

The app runs under gunicorn via `vald.service`. Two further pieces handle
scheduled maintenance:

| file | purpose |
|------|---------|
| `vald-cleanup.timer` / `.service` | delete expired result files daily at 02:23 |
| `bin/vald-manage` | run any management command with the production environment |

### Why not cron

A plain crontab line does **not** work:

```cron
# BROKEN - do not use
23 02 * * * /home/vald/vald-www.git/.venv/bin/python manage.py cleanup_old_results
```

`manage.py` defaults to `DJANGO_SETTINGS_MODULE=vald_web.settings` (development),
and cron supplies almost no environment, so the command resolves
`VALD_FTP_DIR` to `BASE_DIR/public_html/FTP` instead of
`$VALD_HOME/WWW/public_html/FTP` and sweeps a directory the app never writes to.
Note that `VALD_WORKING_DIR` is the same in both settings modules, so such a job
appears to work — the job subdirectories really are cleaned — while result files
accumulate forever. Setting only `DJANGO_SETTINGS_MODULE` is not enough either:
`settings_deploy` reads `SECRET_KEY` from the environment and will raise
`KeyError`. As of the current version the command refuses to run when
`VALD_FTP_DIR` is missing, so this misconfiguration now fails loudly.

### Per-site configuration

Everything that differs between mirrors lives in `secrets.txt`, so no tracked
file needs editing and nothing conflicts on pull. It is the successor to the PHP
interface's `config/site_config_local.php`.

```bash
cp secrets.txt.example secrets.txt
chmod 600 secrets.txt
$EDITOR secrets.txt      # at minimum: SECRET_KEY, VALD_HOME, VALD_ALLOWED_HOSTS
```

Every value has an Uppsala default in `vald_web/settings_deploy.py`, so an
existing deployment keeps working unchanged. See the comments in
`secrets.txt.example` for the full list — site name, hostnames, trusted origins,
base URL, URL prefix, and the three contact addresses.

`VALD_URL_PREFIX` is the sub-path the app is served under (`/new` today, empty
for the site root). The session and CSRF cookie paths and `STATIC_URL` all derive
from it, so it is the only place the prefix is configured — but changing it signs
out every logged-in user, because the cookie paths change with it.

Values are read both by systemd (`EnvironmentFile=`) and by `bin/vald-manage`
(which sources the file with `sh`), so **any value containing a space must be
double-quoted** — an unquoted space makes `sh` try to execute the rest of the
line and abort.

`bin/vald-manage` needs no editing: it derives the application directory from its
own location and reads `VALD_HOME` from `secrets.txt`. Set `VALD_APP_DIR` if you
invoke it through a symlink.

The systemd units cannot derive paths, so they are the one thing each site must
still adjust. For a mirror installed somewhere other than `/home/vald`:

```bash
sed -i -e 's#/home/vald/vald-www\.git#/srv/vald/app#g' \
       -e 's#/home/vald/VALD3#/srv/vald/VALD3#g' \
       vald.service vald-cleanup.service
```

Also check `User=`/`Group=` in both units.

### Installing the timer

```bash
# 1. See what the first run would remove - it may be a lot on an existing
#    site, since result files were previously never swept
bin/vald-manage cleanup_old_results --dry-run | tail -20

# 2. Install and enable
sudo cp vald-cleanup.service vald-cleanup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vald-cleanup.timer

# 3. Verify
systemctl list-timers vald-cleanup.timer
sudo systemctl start vald-cleanup.service   # run once now
journalctl -u vald-cleanup.service -n 30
```

Output goes to journald, so no logfile needs rotating. The retention period
(`--age`, default `2D`) should match the "available for 48 hours" wording in the
completion emails.

### Manual maintenance commands

```bash
bin/vald-manage cleanup_old_results --dry-run --age 7D
bin/vald-manage reconcile_stuck_requests --dry-run
bin/vald-manage migrate
```

`reconcile_stuck_requests` fails requests left in `pending`/`processing` by a
worker that was restarted mid-job, and marks one complete instead if its output
file turns out to exist. It has no timer — run it by hand if requests appear
stuck.

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
