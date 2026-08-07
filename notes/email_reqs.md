# Running the legacy email request path alongside the Django app

The old system accepts extraction requests by email: an incoming mail is drained
by `parsemail`, each request turned into a job script by `parserequest`, and the
concatenated `process` script runs the same Fortran binaries the app uses. The
web app used to generate that same email format; now it runs the binaries
directly via `job_runner.py` and never touches the mail path.

Question addressed here: can the email path keep running for the few power-users
who want it, alongside the new app, without interference?

**Verdict: yes.** The two paths are well isolated. The only shared resource is
the results directory, and the risks there are negligible. The real gotchas are
*divergence* in behaviour between the two doors, not interference.

## What the two paths share — and don't

| Resource | Email path (`backend/service-ems.sh`) | New app | Clash? |
|----------|---------------------------------------|---------|--------|
| Working dir | `$VALD_HOME/EMS/TMP_WORKING` | `BASE_DIR/working/{id}/` (isolated per job) | No — separate trees |
| Results dir | `$VALD_HOME/WWW/public_html/FTP` | **same** | Shared — see below |
| Database | does not touch SQLite | reads/writes it | No contention |
| Binaries | `preselect5` / `select5` / `showline` / … | same | No — stateless per run, different cwds |
| Config source | file-based `.cfg` | DB-generated `.cfg` | Divergence, not interference |
| User validation | `clients.register` (file) | DB (imported from `clients.register`) | Divergence — keep in sync |

## The shared FTP directory — both safe

1. **Filename collisions: real but negligible.** Both write
   `{ClientName}.{NNNNNN}.gz` into `$VALD_HOME/WWW/public_html/FTP`. Legacy uses a
   sequential job number; the app uses a SHA256-derived 6-digit hash of the
   request UUID. A clash needs the *same user* and the same 6-digit value at once
   — vanishingly unlikely for a handful of power-users, but if it happened one
   file would silently overwrite the other. Acceptable; just a known edge.

2. **Two cleaners, harmless.** `service-ems.sh` already deletes FTP files older
   than 2 days on every run (`find $VALD_FTP_DIR -maxdepth 1 -ctime +2 -type f`),
   and the new `cleanup_old_results` timer does the same by mtime. Redundant but
   consistent (both 2-day); each also removes the other's old output, so nothing
   leaks. Don't assume only one is running.

## The real caveats — behavioural divergence

A power-user can get *different results* through the two doors. Nothing breaks;
set expectations.

- **Config drift.** The email path reads file-based configs
  (`$VALD_HOME/CONFIG/default.cfg` + file personal configs). The app reads
  configs generated from the database. A user who customises their linelist
  config in the web app will NOT get that customisation by email — email gives
  them the file-based/default config. Fine if the email users rely on the default
  config; a surprise if they expect their web customisations to apply.

- **User drift.** The email path validates the sender against `clients.register`;
  the app validates against the DB (which was imported from `clients.register`).
  They are in sync immediately after an import, but anyone who registers ONLY
  through the app (admin-approved, not added to `clients.register`) will be
  REJECTED by the email path. **Keep `clients.register` authoritative** — add
  new email-eligible users there too, and re-run `import_users` to fold
  them into the DB.

- **No tracking.** Email jobs never touch the database, so they will not appear
  in the app's "My Requests" page. Expected.

## Operational notes

- The email path still needs its C binaries (`parsemail`, `parserequest`)
  compiled, plus the MTA wiring (mail alias / procmail → `service-ems.sh`) and
  its schedule (cron/atd). The app uses none of this — it is an independent
  subsystem beside the app.
- Nothing in the app writes to `EMS/TMP_WORKING` or reads the mail spool, so
  there is no interference in that direction either.
- Both paths call the same binaries under `$VALD_HOME/bin`, so nothing extra to
  build for the app's sake.

## How much is it actually used? (measured 2026-08-05)

Measured from `$VALD_HOME/LOGS/requests.log` (21,798 requests, Oct 2020 – 2026).
Each request carries full mail headers; the old PHP webapp submits *as the user*
(its `From:` header is the user's address), so the discriminator is the **envelope
sender / `Received` path**: the webapp is injected locally by the web server
(`www-data@neon.physics.uu.se`, uid 33), genuine email arrives from elsewhere.

| Source | Requests | Share |
|--------|----------|-------|
| Old PHP webapp (`www-data@neon`, uid 33) | 21,627 | 99.26% |
| Everything else | 162 | 0.74% |

All 162 non-webapp requests are from 2024 onward. By sender:

| Sender | Reqs | When | Note |
|--------|------|------|------|
| georges.kordopatis@oca.eu (two addresses) | 138 | 2024, early 2025 | historical heavy user; silent in 2026 |
| solomoncang@gmail.com | 11 | all 2026 | the one currently active email user |
| thomas.marquart@physics.uu.se | 10 | — | admin's own local/CLI submissions (uid 501), not external |
| jonathan.remmert.5375@student.uu.se | 1 | 2025 | one-off |
| ilyin@aip.de | 1 | 2025 | one-off (likely the AIP mirror operator) |
| apscelmio@gmail.com | 1 | 2026 | one-off |

**5 genuine external users ever** (excluding the admin's own 10). In 2026 only two
external people have used email — `solomoncang@gmail.com` (11) and
`apscelmio@gmail.com` (1) — and Kordopatis, who was 85% of all email use, has
stopped.

**Conclusion: retire it.** Over 5+ years the email path carried a rounding error of
genuine traffic, and today it is effectively one person. Retiring removes a whole
class of caveats at once (config/user drift, the shared FTP dir, the second
cleanup); mechanically it is just disabling the MTA wiring / `service-ems.sh`
schedule — the app is unaffected.

Before switching it off, give the active senders a nudge to the web app rather than
cutting them off silently:
- `solomoncang@gmail.com` — actively relying on it in 2026; confirm they have an
  activatable web account.
- `apscelmio@gmail.com` — one 2026 request; same courtesy.
- `georges.kordopatis@oca.eu` — the historical power user; a heads-up is polite
  even though he has gone quiet.
- The remaining one-offs need no individual handling.

*Method, if it ever needs re-running: the webapp's envelope sender is the tell —
`grep '^From ' requests.log | awk '{print $2}' | sort | uniq -c | sort -rn`, then
everything that is not `www-data@neon...` is non-webapp. Once the PHP app is off,
`requests.log`/`statistics.log` become email-only and a plain line count suffices.*

## If you keep it running — the short checklist

1. Keep `clients.register` updated with any new email-eligible users, and
   `import_users` them into the DB so both doors agree.
2. Tell email power-users that extractions use the shared default config, not
   their web-app config customisations.
3. Leave the FTP cleanup as-is (either cleaner suffices; running both is fine).

*Analysis based on the committed `backend/service-ems.sh`, `parsemail.c`,
`parserequest.c` and the deploy settings. The server's exact mail routing and
schedule aren't visible here, but the shared-resource analysis holds regardless
of how the service is triggered.*
