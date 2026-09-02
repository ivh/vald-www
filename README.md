# VALD Web Interface - Django Version

Django replacement for the 30-year-old PHP-based VALD (Vienna Atomic Line Database) web interface.

## Features

- Password authentication with activation tokens
- 5 request forms: Extract All/Element/Stellar, Show Line, Show Line ONLINE
- **Request tracking** - real-time status updates, download links
- **Direct backend submission** - calls the VALD binaries directly; email is a
  delivery option, not a separate execution path
- User preferences stored in database (energy units, wavelength, medium)
- Personal linelist configurations (database-backed, opt-in)
- Job queue system with parallel processing
- Re-uses original HTML/CSS for familiarity

## Requirements

- Python 3.11+
- SQLite (included)
- VALD binaries (in `$VALD_HOME/bin/`)
- SMTP server (optional, for email mode)

## Installation

1. **Install dependencies** (pinned in `uv.lock`):
   ```bash
   uv sync
   ```

2. **Run migrations:**
   ```bash
   uv run python manage.py migrate
   ```

3. **Import existing users** (migration only; new accounts come from the
   registration form):
   ```bash
   uv run python manage.py import_users --file /path/to/clients.register
   ```

4. **Set VALD_HOME environment variable:**
   ```bash
   export VALD_HOME=/path/to/VALD3
   ```

## Running

```bash
uv run python manage.py runserver
```

Server runs at http://127.0.0.1:8000/

## Production deployment

The app runs under gunicorn via `vald.service`, with two timers for scheduled
maintenance:

| file | purpose |
|------|---------|
| `vald-cleanup.timer` / `.service` | daily at 02:23 - delete expired result files, then expired sessions, and report any request stuck in `processing` |
| `vald-backup.timer` / `.service` | daily at 03:17 - snapshot the database, tagged with the git revision |
| `bin/vald-manage` | run any management command with the production environment (not a timer) |
| `vald.caddy` | Caddy site body for the vhost - `import` it from the site block in `/etc/caddy/Caddyfile` |

**The step-by-step deploy procedure lives on the admin help page**
(`/admin/help/`), where the install path and unit file list are read from the
running instance instead of being written down here.

### Requests a restart interrupted

The job queue and its worker threads live in the gunicorn process, so restarting
the service ends any extraction in flight and leaves its row saying `processing`
forever — the pipeline timeout is a deadline held by a thread that no longer
exists, and nothing scans the table.

`vald/startup.py` resolves those when the app next starts, called from
`vald_web/wsgi.py` (which gunicorn loads and management commands do not). Up to
`VALD_STRANDED_RERUN_MAX` are re-run for their owners, keeping the same uuid so
links their owners already hold still work; above that they are marked failed and
the webmaster is mailed, because replaying a flood on boot would fill
`VALD_MAX_QUEUE_SIZE` and start rejecting live users.

**This assumes one worker process and one instance per host** — which is what
`vald.service` runs, deliberately, since the queue is in-process. Raising
`--workers` above 1 would make the sweep mark another live worker's jobs as dead.

Anything still `processing` while the process stayed up is a different fault (a
wedged pipeline, a failed status write); the cleanup timer reports those daily
past `VALD_STUCK_JOB_TIMEOUT_FACTOR × VALD_JOB_TIMEOUT`. Either way the fix is
the same: **Re-run now, for the original user** in the request admin.

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
interface's `config/site_config_local.php` (kept for reference under
`old/config/`).

```bash
cp secrets.txt.example secrets.txt
chmod 600 secrets.txt
$EDITOR secrets.txt      # at minimum: SECRET_KEY, VALD_HOME, VALD_ALLOWED_HOSTS
```

Every value has an Uppsala default in `vald_web/settings_deploy.py`, so an
existing deployment keeps working unchanged. See the comments in
`secrets.txt.example` for the full list — site name, hostnames, trusted origins,
base URL, URL prefix, and the three contact addresses.

`VALD_URL_PREFIX` is the sub-path the app is served under (empty for the site
root, `/new` while the legacy PHP interface still held the root). The session and
CSRF cookie paths and `STATIC_URL` all derive from it, so it is the only place
the prefix is configured — but changing it signs out every logged-in user,
because the cookie paths change with it. It has to agree with the web server:
`vald.caddy` assumes the empty prefix, and keeps redirecting `/new/*` to the root
because completion emails sent under the old prefix live on in mailboxes.

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
       vald.service vald-cleanup.service vald-backup.service
```

Also check `User=`/`Group=` in all three units.

### Installing the timers

```bash
# 1. See what the first run would remove - it may be a lot on an existing
#    site, since result files were previously never swept
bin/vald-manage cleanup_old_results --dry-run | tail -20

# 2. Install and enable
sudo cp vald-cleanup.service vald-cleanup.timer \
        vald-backup.service vald-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vald-cleanup.timer vald-backup.timer

# 3. Verify
systemctl list-timers 'vald*'
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

### Migrating from the legacy interface

One-off, in this order — each step is the comparison baseline for the next:

```bash
bin/vald-manage import_default_config $VALD_HOME/CONFIG/default.cfg
# ...and the alternatives offered beside it, see "Alternative configurations"
bin/vald-manage import_users --file /path/to/clients.register --dry-run
bin/vald-manage import_users --file /path/to/clients.register
cp /path/to/legacy/*.cfg config/personal_configs/
bin/vald-manage import_persconf --all --dry-run
bin/vald-manage import_persconf --all
```

`import_persconf` handles both kinds of legacy file, telling them apart by name:
`<Name>.cfg` is the personal linelist configuration, `<Name>-HTMLdefs.cfg` the
unit preferences (energy unit, medium, wavelength unit, van der Waals format,
isotopic scaling). Both go through the same user matching: filename with
whitespace removed, so `JeffA.Valenti.cfg` finds `Jeff A. Valenti`. Anything
else is reported as `No user matches` and skipped — expect a tail of those for
people who left, and for files written under an older naming convention whose
modern twin imported successfully. A linelist file identical to the default
produces no configuration at all, which is correct: no personal configuration
means the user tracks the VALD default.

Preferences are not overwritten if the user has already changed them here, since
that choice is newer than the legacy file; `--force` overrides. A row created by
merely visiting the units page holds nothing but defaults and is not treated as
a choice.

All three are re-runnable, but `import_persconf` replaces a user's linelist
configuration outright, so re-running it after the site is live would discard
anything they have changed through the web interface. Use `--prefs-only` to pick
up the `-HTMLdefs` preferences without touching the linelist configurations.

## Adding a new linelist

The application reads its linelist catalogue and configuration from the
**database**, not from `default.cfg` directly — the `.cfg` is only the import
source, and is regenerated on the fly per job (see *Architecture* below). So
editing `default.cfg` alone changes nothing; the database must be re-imported.

1. **Put the data file in place.** Linelist data lives on disk as a compressed
   binary pair, not raw text:

   ```
   $VALD_HOME/CVALD3/ATOMS/<name>.CVALD3     data
   $VALD_HOME/CVALD3/ATOMS/<name>.DSC3       descriptor
   ```

   Convert the linelist into that form with the VALD data tools (`kompress3` /
   `ukconvert2-3` in `$VALD_HOME/bin/`) and place both files under `CVALD3/ATOMS/`
   (or `CVALD3/MOLECULES/`). Molecular lists are detected by `/MOLECULES/` in the
   path.

2. **Add a line to `$VALD_HOME/CONFIG/default.cfg`.** The path is extensionless —
   the binaries append `.CVALD3`/`.DSC3`:

   ```
   '/CVALD3/ATOMS/<name>', <priority>, <elem_min>, <elem_max>, <mergeable>, r1,r2,r3,r4,r5,r6,r7,r8,r9, '<description>'
   ```

   where the nine `r` values are the quality ranks (wl, gf, rad, stark, waals,
   lande, term, ext_vdw, zeeman). A leading `;` disables the list while still
   registering it. **Each line needs all 13 numbers** — the importer silently
   skips any line with fewer and only warns, so check the imported count matches.

3. **Re-import into the database:**

   ```bash
   bin/vald-manage import_default_config $VALD_HOME/CONFIG/default.cfg
   ```

   This adds the new `Linelist` to the catalogue and rebuilds the **system
   default** configuration. Verify the count:

   ```bash
   bin/vald-manage import_default_config $VALD_HOME/CONFIG/default.cfg --dry-run
   ```

Repeat all three steps on each mirror — every site has its own database and its
own copy of the data files.

**Effect on users.** Every user is in one of two states:

| state | effect of the re-import |
|-------|-------------------------|
| no personal configuration | picks up the new list automatically |
| has a personal configuration | keeps their existing selection; does **not** get the new list |

A personal configuration is a *snapshot* taken when the user first edits
something, and is deliberately left untouched — but the Personal Configuration
page now tells them so, naming the linelists added to the VALD default since
their snapshot was taken, with buttons to remove the personal configuration (and
track the default again) or to re-copy the current default. Simply *viewing* that
page creates nothing, so a user who has never customised anything stays in the
first row.

Changing an existing linelist's *element range* is the exception: that field
lives on the shared `Linelist` row, so it reaches everyone either way.

**Removing a linelist** is handled by leaving it out of `default.cfg` and
re-importing: the importer deactivates any linelist that is then in *no* system
configuration, and generated `.cfg` files skip inactive linelists. That is what
stops an old snapshot from naming a data file that has left the SVN tree. A list
that reappears in a later import is reactivated. Note the scope: a list dropped
from `default.cfg` but still present in one of the alternatives below stays
active, so remove it from all of them.

## Alternative configurations

`$VALD_HOME/CONFIG` holds several `.cfg` files that differ only in which entries
are commented out. Import each one as a system configuration; the request forms
offer them as a menu, and a request records the `--slug` of the one it used.

```bash
bin/vald-manage import_default_config $VALD_HOME/CONFIG/default.cfg \
  --config-name 'Default (observed, atoms and molecules)'
bin/vald-manage import_default_config $VALD_HOME/CONFIG/VALD3_all.cfg \
  --slug vald3_all --config-name 'All (observed and predicted, atoms and molecules)'
bin/vald-manage import_default_config $VALD_HOME/CONFIG/VALD3_all_atoms.cfg \
  --slug vald3_all_atoms --config-name 'All atoms (observed and predicted, atoms only)'
bin/vald-manage import_default_config $VALD_HOME/CONFIG/VALD3_default_atoms.cfg \
  --slug vald3_default_atoms --config-name 'Default atoms (observed, atoms only)'
```

Without `--slug` the import replaces the system **default** — the configuration
a request gets when it names no other, what a personal configuration is
snapshotted from, and what the Personal Configuration page compares against.
There is exactly one of those. With `--slug` it maintains an alternative under
that identifier, re-importing in place.

`--config-name` is the label the menu shows, so make it say what the file is;
the slug is what a submitted request stores, so renaming a configuration is
safe but changing its slug orphans requests that already chose it. `default` and
`personal` are reserved: a request has always used those two words for the
system default and for the user's own snapshot.

A personal configuration is still a snapshot of the system **default** only —
choosing an alternative is per request and does not interact with it.

## Configuration

### Job Execution

Set in `vald_web/settings.py` (development) or `vald_web/settings_deploy.py` (production):
```python
VALD_MAX_THREADS = 5      # Parallel job limit (threads, not processes)
VALD_MAX_QUEUE_SIZE = 10  # Pending jobs before new requests are rejected
```

Requires the VALD Fortran binaries in `$VALD_HOME/bin/`: `preselect5`, `presformat5`,
`select5`, `showline4.1`, `hfs_pres`, `post_hfs_format5`.

### Result Delivery

Results are always produced by running the binaries directly; email is a delivery
option, not a separate execution path. Completion mail goes out via the local MTA:

```python
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'localhost'
EMAIL_PORT = 25
```

### User Registration

Users live in the database. Accounts are created by self-registration through
the contact form (then approved in the admin), or in bulk from a legacy
`clients.register` file:

```
#$ Full Name
email@domain.com
```

```bash
bin/vald-manage import_users --file /path/to/clients.register --dry-run
bin/vald-manage import_users --file /path/to/clients.register
```

This is a one-off migration path, not something the running app consults —
nothing reads the file at request time. `--file` is required so that a stale
copy sitting in the repo cannot silently become the source for a real import.

## Architecture

1. Each job gets its own subdirectory under `working/`, named with its 6-digit ID
2. `job_runner.py` writes `pres_in.NNNNNN` and a per-job `.cfg` generated from the database
3. Binaries are run as a subprocess pipeline, e.g. `preselect5 | presformat5`
   (extract), plus `hfs_pres | post_hfs_format5` when HFS splitting is on
4. Output: `{ClientName}.NNNNNN.gz` (extract) or `.txt` (showline), moved to `$VALD_FTP_DIR`
5. Real-time status updates on the request detail page

## Key Technical Notes

- **UUID to 6-digit conversion**: Backend expects numeric IDs, converts UUID via SHA256 hash
- **Job working directory**: Binaries run FROM the job subdirectory, for correct `pres_in.NNNNNN` naming and to keep concurrent jobs isolated
- **Showline requests**: No bib files, output is `result.NNNNNN` → moved to FTP as `.txt`
- **Extract requests**: Create `.gz` and `.bib.gz` files
- **Job queue**: Thread pool limits parallel execution (`VALD_MAX_THREADS`, 5 in production).
  Threads, not processes: gunicorn runs one process so the in-process queue is a real ceiling

## Troubleshooting

**"Output file not found"** → Job execution failed; check the per-stage `.err` files
(`preselect5.err`, `presformat5.err`, ...) in the job subdirectory under `working/`
**"Can't open input data file"** → `pres_in.*` file missing or misnamed

## References

- VALD website: http://vald.astro.uu.se/
- Django 5.2 docs
- Legacy PHP interface and C sources in `old/` (reference only; superseded by `job_runner.py`)
- Linelists, configs and the Fortran backend live in the separate VALD3 SVN repository
