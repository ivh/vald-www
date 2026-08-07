# VALD Django — pre-cutover review findings

Review date: 2026-08-02/03. Branch: `master` @ `f3055ee`.

**Scope reviewed:** `views.py`, `backend.py`, `job_runner.py`, `models.py`, `forms.py`,
`persconfig.py`, `utils.py`, `admin.py`, `cleanup_old_results.py`, both settings modules,
`vald.service`, `urls.py`, and the templates cited below.

**Not reviewed:** the `import_*` management commands in depth,
templates not cited here, and whether the flag→`pres_in` mapping matches the Fortran
source (that needs test runs against the binaries, not reading).

IDs are the numbers used in the review discussion, kept stable for cross-reference.
The document is ordered by priority, so IDs are not sequential.

---

## Status overview

| ID | Sev | Status | Finding |
|----|-----|--------|---------|
| R1 | Critical | ✅ fixed `232e0c9` | `is_active` never checked → admin approval bypassable |
| R2 | Critical | ✅ fixed `d756505` | IDOR: any user could rewrite the system default linelist config |
| R3 | High | ✅ fixed `f3055ee` | `elmion`/`chemcomp` injected into Fortran control files |
| R16 | High | ✅ fixed `2d01489` | `FORCE_SCRIPT_NAME` unset → literal `None` in email links |
| R4 | High| ✅ fixed `296a463` | Rate limiting ineffective: per-process cache, spoofable client IP |
| R5 | Medium| ✅ fixed `a0f0442` | No submit rate limit and no per-user in-flight cap (merged with R9) |
| R8 | High| ✅ fixed `e3196af` | Job timeouts don't bound the pipeline; orphaned processes |
| R7 | High| ✅ fixed `8348d98` | Jobs die with the gunicorn worker; no reconciliation |
| R13 | High| ✅ fixed `faa3831` | No `LOGGING`/`ADMINS` — production failures are invisible |
| R11 | High| ✅ fixed `d7964db` | Disk grows without bound (showline `.txt` never cleaned) |
| R12 | High| ✅ fixed `be7c718` | `manage.py` defaults to dev settings → cleanup targets wrong dir |
| R10 | Medium| ✅ fixed `b5c2466` | SQLite `journal_mode=delete`, no busy timeout |
| R6 | Medium| ✅ fixed `e36ff73` | Reset tokens never expire; password validators never invoked |
| R14 | Medium| ✅ fixed `d0243f3` | Contact form 500s on a message containing `\` + digit |
| R17 | Medium| ✅ fixed `41537f7` | `/new` hardcoded in 4 places; cutover invalidates all sessions |
| R18 | Medium| ✅ fixed `37d36fd` | `output_file` stores absolute paths |
| R19 | Medium | ✅ dev-only `77492d1`; N/A on server | `collectstatic` could publish result files — deploy trees separate + collectstatic unused |
| R37 | — | ✓ accepted by design | Proxy serves result files directly (no download auth) — kept, matches legacy; results are the user's own output |
| R28 | Medium | ✅ fixed `557d101` | `chemcomp` written unquoted into `select.input` |
| R9 | Medium | ✅ fixed `f0671c8` | Effective concurrency 4× configured; per-user cap (a0f0442) + single threaded worker (f0671c8) |
| R23 | Medium| ✅ fixed `a66e87f` | Failed showline queries reported as success |
| R36 | — | fixed locally (~/VALD3, uncommitted) | HFS read broke under gfortran `-std=legacy` (comma in A16 ref field); 9-comma source patch + no-legacy presformat5 build |
| R35 | Medium | ✅ fixed `a0f0442` | django-ratelimit block=True made every friendly "too many attempts" branch dead code |
| R15 | Medium | ✅ fixed `b61fa8c` | Effectively no test coverage |
| R29 | Low| ✅ fixed `90464f5` | `VALD_MAX_LINES_PER_REQUEST` is a phantom setting |
| R30 | Low| ✅ fixed (see R40) | Rank weights not range-checked before reaching the `.cfg` |
| R31 | Low| ⏸ won't-fix (accepted) | `ConfigLinelist.save()` silently overrides a deliberate rank of 3 |
| R32 | Low | ✅ fixed `0010` | Multiple system default configs possible; `.first()` picks arbitrarily |
| R27 | Low| ✅ fixed `3523937` | `save_units` writes unvalidated POST values |
| R25 | Low | ✅ fixed `b61fa8c` | `error_message` leaks internal paths to users |
| R26 | Low| ⏸ won't-fix (accepted) | Account enumeration in login/registration messages |
| R33 | Low| ✅ fixed `a38d4f1` | Completion email attaches the full result file |
| R34 | Low | open | `safe`-tagged messages render unescaped |
| R20 | UX | ✅ fixed `8647f39` | My Requests: no λ range/element, no pagination |
| R21 | UX | ✅ fixed `73de058` | Expired results still show status "Complete" |
| R22 | UX| ✅ fixed `8079afb` | Spam filter rejects any message containing a URL |
| R24 | UX | ✅ fixed (see R47) | "Custom" config falls back silently; persconf mutates on GET |
| R38 | High | ✅ fixed | Sessions outlive the account state they were granted under; no key rotation on login |
| R39 | Medium | ✅ fixed | `?modify=<non-uuid>` is an unhandled 500 on all four extraction forms |
| R40 | Low | ✅ fixed | Out-of-range persconf rank is a 500 (sqlite `OverflowError`); supersedes R30 |
| R41 | Medium | ✅ fixed | Django admin login unthrottled; admin-set passwords skip the validators |
| R42 | Low | ✅ removed | `MAINTENANCE` was documented and configurable but read by nothing |
| R43 | Low | ⏸ won't-fix (accepted) | No upper bound on the extraction wavelength range for download delivery |
| R44 | Low | ✅ fixed | showline error text skipped R25's scrubbing, and failed runs were published anyway |
| R45 | Low | ✅ fixed | `django_session` never swept — no `clearsessions` anywhere |
| R46 | — | ✓ not a defect | Result filename collisions are guarded by construction, not merely improbable |
| R47 | Medium | ✅ fixed `0011` | Personal configs were snapshots that only froze - "track the VALD default" was unreachable |
| R48 | Low | ✅ fixed | `Linelist.is_active` was never read or written; retiring a linelist did nothing |
| R49 | Cosmetic | ✅ fixed | Four multi-line `{# #}` template comments rendered as visible page text |

Dead schema noted under R7: `Request.completed_at` and `Request.queue_position` are
never written.

---

## P1 — fix before cutover

### R4. Rate limiting does not do what it says
**Where:** `vald_web/settings_deploy.py:85-90` (`CACHES`), `:136`
(`RATELIMIT_IP_META_KEY`), `vald.service` (`--workers 4`)

Two independent defects, either of which alone defeats the control:

1. `CACHES` is `LocMemCache`, which is **per-process**. Gunicorn runs 4 workers, so each
   has its own buckets: `5/m` on login is really up to 20/m, and which bucket you hit
   depends on which worker gunicorn picks.
2. `RATELIMIT_IP_META_KEY = 'HTTP_X_FORWARDED_FOR'` uses the **raw** header. A reverse
   proxy *appends*, so a client sending `X-Forwarded-For: <random>` produces
   `<random>, realip` — a fresh bucket per request. Rotating the prefix bypasses every
   limit in the app.

**Fix:** move to a shared cache (`DatabaseCache` is fine here — no Redis needed), and
either use a callable key that takes the **rightmost** XFF entry, or have the proxy
overwrite a dedicated header it fully controls. Worth confirming what the nginx/Apache
vhost currently sets.

### R5. No submit rate limit and no per-user in-flight cap
**Where:** `vald/views.py:563` (`submit_request`), `:716` (`handle_extract_request`),
`vald/backend.py:142` (`check_queue_capacity`)

The `@ratelimit` decorators cover contact/registration/auth only — the extraction
submission path has none, and there is no per-user concurrency limit
(`check_queue_capacity` counts globally). One user can therefore hold all 10 admission
slots indefinitely and every other user gets "Server is busy".

**This is a genuine regression, and it is about rate, not size.** Legacy VALD took
requests by email and ran them through `at`, so submissions were inherently serialized and
you could not fire them in a loop. The interactive path removes that, and R9's 20
concurrent pipelines amplify it. R5 and R9 are the same problem from two directions and
should be fixed together.

**Fix:** rate-limit the submit path; cap in-flight requests per user; derive the admission
check from the queue it is meant to guard.

**Explicitly _not_ a fix: capping the wavelength range.** Legacy applied no maximum either
— `parserequest.c:650-655` validates only `wlleft > wlright || wlleft <= 0` → "Bad
wavelength range". Wide ranges are legitimate science and were always allowed. An earlier
draft of this document called for a range cap; that was wrong.

### R8. Job timeouts don't bound the pipeline, and children are never killed
**Where:** `vald/job_runner.py:209`, `:213`, `:273`, `:277`, `:353-375`

Only the **last** process in each pipeline gets `communicate(timeout=3600)`. The upstream
`preselect_proc.wait()` / `select_proc.wait()` calls have **no timeout**, so a hung
`preselect5` parks a worker thread permanently — 5 of those and the queue is dead. And on
`TimeoutExpired` the handlers just return an error string without killing the children,
leaving orphaned Fortran processes holding CPU and file handles.

**Fix:** wrap the whole pipeline in one deadline; `kill()` + `wait()` every process in a
`finally`; log what was killed.

### R7. Background jobs die with the worker; nothing reconciles
**Where:** `vald/views.py:961` (`thread.start()`), `vald.service` (`--timeout 60`,
`--workers 4`)

Each request spawns a plain daemon `threading.Thread` inside a sync gunicorn worker. Any
worker recycle, timeout kill on an unrelated request, or deploy kills in-flight jobs, and
the `Request` row stays `processing` **forever** — there is no startup sweep.

Related dead schema: `Request.completed_at` and `Request.queue_position` are declared
(`models.py:30,39`) and shown in the admin, but **never written** anywhere. The detail
template's "Completed:" row therefore never renders, and the view recomputes queue
position on the fly.

**Fix:** on startup, mark orphaned `processing` rows as failed (or requeue). Set
`completed_at` when a job finishes. Consider dropping `queue_position` from the model.

### R13. No logging configuration at all
**Where:** both settings modules — no `LOGGING`, no `ADMINS`

With `DEBUG=False`, unhandled 500s and the several `logger.exception` calls in
`process_request_background` have no configured destination beyond whatever gunicorn
happens to capture on stderr. This is what makes R14 silent, and it will make every
post-cutover incident guesswork.

**Fix:** a small `LOGGING` dict to journald/file at INFO, plus `ADMINS` so `django.request`
ERROR mails out.

### R11. Disk grows without bound, two ways
**Where:** `vald/job_runner.py:567` (`_finalize_output`),
`vald/management/commands/cleanup_old_results.py:85`

1. `_finalize_output` gzips into the FTP dir but leaves the **uncompressed** original in
   the job dir, so every extraction costs roughly 2× until the job dir is swept.
2. The FTP sweep only globs `*.gz` and `*.bib.gz`. Showline results are written as
   `.txt` (`job_runner.py:452`) and are therefore **never deleted**. Observed rather than
   inferred: the local `public_html/FTP/` still holds `ThomasMarquart.042215.txt` dated
   2025-12-03, eight months on.

**Fix:** delete the uncompressed output after gzip; add `*.txt` to the FTP patterns.
(Minor: `*.gz` already matches `*.bib.gz`, so that pattern is redundant.)

### R12. `manage.py` defaults to the dev settings
**Where:** `manage.py:9` (`vald_web.settings`) vs `vald_web/wsgi.py`
(`vald_web.settings_deploy`)

`wsgi.py` correctly defaults to deploy, but `manage.py` does not. So
`manage.py cleanup_old_results` on the server without an explicit
`DJANGO_SETTINGS_MODULE` uses dev paths — `VALD_HOME=/home/tom/VALD3`,
`VALD_FTP_DIR=BASE_DIR/public_html/FTP` — cleans the **wrong** directory, reports "no old
files found", and the real FTP dir fills up. Same hazard for any import command.

**Fix:** make `manage.py` default to `settings_deploy` (with dev opting in), or hard-fail
when `VALD_HOME` doesn't exist. Also confirm a systemd timer or cron actually runs the
cleanup — none is present in the repo, and the completion email promises 48 hours.

### R10. SQLite in `journal_mode=delete` with no busy timeout
**Where:** `DATABASES` in both settings modules (no `OPTIONS`); verified on the current db:
`PRAGMA journal_mode` → `delete`

With R9's concurrency (up to 20 job threads across 4 processes) all writing status
updates, writer contention past the 5 s default surfaces as `database is locked`. When the
status save fails, the outer handler's own save can fail too, leaving the row stuck
(feeds back into R7).

**Fix:**
```python
"OPTIONS": {"timeout": 20, "init_command": "PRAGMA journal_mode=WAL;"}
```

---

## P2 — cutover mechanics and correctness

### R6. Password and token hygiene
**Where:** `vald/models.py:156-159`, `vald/views.py` (`request_password_reset`,
`reset_password`), `vald/forms.py:43-44`, `vald/views.py` (`set_password`)

- Reset tokens have **no expiry field and no expiry check**, while the email promises
  "expire in 7 days". A leaked link works until used.
- `AUTH_PASSWORD_VALIDATORS` is configured in both settings modules but
  `validate_password` is **never called** — `User.set_password` goes straight to
  `make_password`. The validators are decorative.
- Minimum length is inconsistent: 6 in `forms.py`, 8 in the activation view.

**Fix:** add `token_created_at` and reject stale tokens; call `validate_password` in both
password-setting paths; settle on one minimum.

### R14. Contact form 500s on a message containing `\` + digit
**Where:** `vald/utils.py:153`

`render_request_template` uses `re.sub` with a **user-controlled replacement string**, so
backslash escapes are interpreted. Verified:

```
'my format string is (F8.3\2X) and it fails'
→ PatternError: invalid group reference 2 at position 30
```

The call sits outside the `try/except` in `handle_contact_request`, so it is an uncaught
500 — and silent, per R13. Astronomers pasting Fortran formats, LaTeX or Windows paths
will hit this.

**Fix:** `str.replace`, or pass a lambda replacement to `re.sub`.

### R17. `/new` is hardcoded in four places
**Where:** `vald_web/settings_deploy.py:120` (`FORCE_SCRIPT_NAME`), `:123`
(`CSRF_COOKIE_PATH`), `:124` (`SESSION_COOKIE_PATH`), `:141` (`STATIC_URL`)

All four must change together at cutover. Note that changing the cookie paths
**invalidates every live session and CSRF token** — users mid-form will get a CSRF
failure. Worth doing at a quiet hour with a note on the page.

R16 (fixed) was the sharpest edge here: deleting the `FORCE_SCRIPT_NAME` line would have
put a literal `None` in every emailed link. The fix now coerces it, so removing the line
is safe.

### R18. `output_file` stores absolute paths
**Where:** `vald/models.py:33`, `:72` (`output_exists`)

If `VALD_FTP_DIR` moves at cutover, every historical request silently reports
"Output file not found" — `output_exists()` just returns `False`, so there is no
distinction between "moved", "expired" and "never produced" (see R21).

**Fix:** store the basename and resolve against `VALD_FTP_DIR` at read time.

### R19. `collectstatic` could publish result files — DEV-ONLY, N/A on server

`collectstatic` copies everything under `STATICFILES_DIRS` into `STATIC_ROOT`,
served publicly with no auth. The finding: if the results dir sits inside a
`STATICFILES_DIRS` tree, results get published.

**This was a dev-config problem only.** In dev, `VALD_FTP_DIR` was
`BASE_DIR/public_html/FTP`, inside `BASE_DIR/public_html` (a static dir) — and
this machine's `public_html/FTP/` held 16 real result files, so a dev
`collectstatic` would have published them.

**On the server it does not apply**, for two independent reasons:
1. Deploy `VALD_FTP_DIR` is `$VALD_HOME/WWW/public_html/FTP`
   (`/home/vald/VALD3/...`), a completely different tree from the static dirs
   under `BASE_DIR` (`/home/vald/vald-www.git/...`). Verified: results are inside
   neither `STATICFILES_DIRS` entry.
2. The deployment does not run `collectstatic` at all (static assets are served
   by the reverse proxy directly). `STATIC_ROOT` is declared in
   `settings_deploy.py` but unused.

**Fixed `77492d1`** for dev hygiene: moved dev `VALD_FTP_DIR` to
`BASE_DIR/ftp_results` (outside `public_html`), documented the invariant in both
settings, and added a test asserting `VALD_FTP_DIR` is inside no
`STATICFILES_DIRS`. No server action needed. Earlier advice to "check
`public_html/FTP` on the server before collectstatic" was over-cautious and does
not apply — retracted.

### R37. Does the reverse proxy serve the FTP results dir directly? — server/proxy config, needs check

**Not a code finding — flagged while re-examining R19.** The results directory
is `$VALD_HOME/WWW/public_html/FTP` — a web-served path by design. In legacy VALD,
"retrieve via ftp/download" worked because the web server served that directory
*directly* (results were reachable by guessable URL, no auth).

The Django app instead serves downloads through the authenticated
`download_request` view (with the ownership + path-containment checks from R18).
So the open question for the takeover: **does the nginx/Apache vhost still expose
`$VALD_HOME/WWW/public_html/FTP` directly?**

- If yes, result files remain downloadable by URL without the new login/ownership
  checks — same as legacy. May be intended (backward compatibility), or may be
  something to gate now that there is auth.
- If no, downloads go only through the app, and the direct path is dead.

Purely a reverse-proxy configuration question, outside this codebase. Worth a look
at the vhost during cutover; no code change implied either way.

**Decided (Tom):** the vhost does serve the FTP directory directly, kept as-is.
Rationale: result files are the user's own extraction output (not personal data),
VALD data is shared/scientific, files are transient (48 h cleanup), and it matches
the long-standing legacy download model. Consequence to be aware of: the
auth/ownership/containment checks on `download_request` (R18) therefore gate only
the in-app download button, not the raw URL — result files are reachable by anyone
who knows the filename (`{ClientName}.{6-digit}.gz`, the 6-digit a SHA256-derived
hash of the request UUID). Accepted.

### R28. `chemcomp` may be written in the wrong format — needs checking
**Where:** `vald/job_runner.py:479-499` (`_write_select_input`)

The abundances are written **unquoted**, followed by a quoted `'END'`. But the abundance
block echoed in `documentation/reqextstar.txt` is quoted Fortran strings —
`'H :  0.91','He: -1.05',` … `'Es:-20.00','END'`. If `select5` expects quoted entries,
non-solar abundances may be **silently ignored** and stellar extractions would return
solar-abundance results without complaint.

Not touched by the R3 fix, which deliberately preserved the existing byte format.

**Fix:** run one stellar extraction with e.g. `Fe: -3.0` and check whether the echoed
abundance table in the output reflects it. Correct the quoting if not.

### R9. Effective concurrency is 4× configured; admission check is decoupled
**Where:** `vald/backend.py:99-116` (`get_job_queue`), `:142-160`
(`check_queue_capacity`), `:36`

`_job_queue` is a **per-process** singleton, so 4 gunicorn workers × `VALD_MAX_WORKERS=5`
gives up to **20** concurrent Fortran pipelines, not 5. Separately,
`check_queue_capacity()` counts DB rows in a 30-minute window against
`VALD_MAX_QUEUE_SIZE`, which is a different mechanism from the real
`queue.Queue(maxsize=10)` — the admission check and the actual queue can disagree in both
directions, and the 30-minute window means stuck rows stop counting while still occupying
the real queue.

**Fix:** the honest options are a shared broker, or `--workers 1 --threads N` so there is
one queue. At minimum, derive the admission check from the queue it is supposed to guard.

### R23. Failed showline queries are reported as success
**Where:** `vald/job_runner.py:448-449`

The per-query `except` writes `Error processing query: …` **into the result file** and
`_run_showline` still returns `(True, …)`, so the request shows Complete with an error
inside it. Related fragility at `:439-443`: if the `Which data base information file`
sentinel is absent, `data_start` stays 0 and the raw interactive prompts land in the
user's output.

### R36. HFS failure: gfortran -std=legacy comma-termination (local VALD build)

Not an app bug - a Fortran toolchain issue in ~/VALD3, diagnosed and fixed
locally. Recorded here because the fix lives outside the git repo and is not
committed to VALD's SVN.

**Definitive check.** Tom's own request - extract all, 1000-1000.9 A, cm-1,
vacuum, HFS on - produced job.021685 for him but failed on this dev machine with
identical parameters. Same request, opposite outcome: the software is not at
fault, the local Fortran build is.

**Root cause.** In HFS mode presformat5 reads each line with a fixed format
ending A16,9I4 (16-char reference comment, then 9 integer reference IDs). Built
with -std=legacy on modern gfortran (>=13), a comma inside the A16 comment -
e.g. the reference "CNO, Na 1, Mg 1" on a nitrogen line near 1000 A - is treated
as an input field terminator. The A16 read stops at the comma, the following 9I4
reads from the wrong column, and the record fails with "FORMAT ERROR IN LINE #".
Lines whose reference has no comma are unaffected, so it looked region-dependent.
Minimal reproducer - read "CNO, Na 1, Mg 1:1220..." with format (A16,9I4):
default gfortran gives ios=0 (correct); -std=legacy gives ios=5010 (fails).
Reproduces on gfortran 13 and 16. "Worked before" = previously built with an
older gfortran that did not comma-terminate A-fields under -std=legacy.

**The trap.** Simply dropping -std=legacy breaks the other direction: presformat5.f
has 9 output FORMAT statements with a deliberately missing comma between an A14
descriptor and a following quote literal, a legacy extension that needs the flag.
So the flag is required for the writes but poisons the reads.

**Fix applied (local, uncommitted) - both halves:**
1. ~/VALD3/SOURCE/SELECT/presformat5.f: the 9 spots where an A14 descriptor is
   followed directly by a quote literal now have a comma inserted between them
   (the standard-required comma, removing the only need for -std=legacy).
2. ~/VALD3/SOURCE/SELECT/Makefile: target-specific
   presformat5: F77FLAG := $(filter-out -std=legacy,$(F77FLAG))
   so make builds just this binary without the flag; everything else keeps it.

Only presformat5 needed rebuilding - post_hfs_format5 reads presformat5's
reformatted output, not the raw comma-comment, so the bug was isolated there.
Verified: Tom's exact request returns 145 lines through the app, and
tests/test_backend_binaries.py is 15 passed + 1 xpass (the previously-xfailed
HFS-extract-all now passes).

**Not committed to SVN** per instruction. That working copy's svn pristine store
is incomplete (pre-existing), so svn revert will not work; the change is the two
edits above and is trivially reversible by hand.

**Upstream.** The missing-comma formats violate the standard and bite any modern
gfortran; worth reporting to whoever maintains VALD so mirrors rebuilding with a
current compiler do not hit it.

### R35. Every rate-limit message was dead code
**Where:** `vald/views.py` (all `@ratelimit` decorators), django-ratelimit 4.1.0

`@ratelimit` defaults to `block=True`, which raises `Ratelimited` (a
`PermissionDenied` subclass) before the view body runs. So all six
`if getattr(request, 'limited', False):` branches - each with a carefully worded
message like "Too many login attempts. Please try again in 1 minute." - could never
execute, and a rate-limited user got a bare 403 page instead. Found while testing R5.

`reset_password` had no `limited` branch at all, so it needed one adding before
`block=False` could be applied without silently dropping its limit.

### R15. Effectively no test coverage
**Where:** `tests/` — only `test_config.py` (config generation)

Nothing covers auth, the request pipeline, or the views. The scratch tests I wrote for
R1/R2/R3 are throwaway; worth promoting them into `tests/` as regression tests, since all
three were exploitable and nothing would catch a regression.

---

## P3 — smaller correctness issues

### R29. The output line cap lost its delivery-method distinction
`vald/job_runner.py:609` reads `VALD_MAX_LINES_PER_REQUEST` via `getattr(..., 500000)`,
but it is defined in **neither** settings module, so the fallback always wins and the
number is invisible and untunable.

More substantially, legacy had **two** caps chosen by delivery method
(`parserequest.c:659-663`, `:830-834`, `:1163-1164`):

```c
if(FTPretrieval)  fprintf(fo1, "...%d\n", ..., MAX_LINES_PER_FTP);
else              fprintf(fo1, "...%d\n", ..., MAX_LINES_PER_REQUEST);
```

| delivery | legacy | now |
|---|---|---|
| download | 100 000 | 500 000 |
| email | 1 000 | 500 000 |

The values live in `valdems.h` / `valdems_local.h` on the server, not in this repo; the
100 000 / 1 000 figures come from the comment the pre-jobrunner Python left behind, so
**confirm them with `grep MAX_LINES_PER $VALD_HOME/**/valdems*.h`** before acting.

Two consequences: the email path lost the 1 000-line cap that made attaching results safe
(see R33), and the download cap was raised 5× — note that legacy's 100 000 may have been
sized to match a fixed array dimension inside `preselect5`, so raising it is the direction
that carries risk. Worth one wide-range test extraction to see whether the binary warns on
truncation or just stops; nothing in `documentation/errors.html` documents either.

**Fix:** define the setting explicitly and restore the per-delivery distinction.

### R30. Rank weights are not range-checked
`vald/views.py:1136` coerces `edit-val-{j}` with `int()` and a fallback of 3, but never
bounds the result. Values outside 1–9 flow into the generated `.cfg` and thence
to the Fortran parser. Now user-scoped after R2, so blast radius is the user's own
extractions.

### R31. `ConfigLinelist.save()` silently overrides a deliberate rank of 3
`vald/models.py:579-601` treats `3` as "unset" and replaces it with the linelist default
on create. A user who genuinely wants rank 3 cannot express it. Use `None` for "inherit".

### R32. Multiple system default configs are possible
`vald/models.py:468-474`: the `UniqueConstraint` on `('user', 'is_default')` does not
apply when `user IS NULL`, because NULLs don't compare equal in a unique index. So several
system defaults can coexist and `get_default_config()` (`.first()` under
`ordering = ['user','name']`) picks by name. Add a partial constraint for the
`user IS NULL` case.

### R27. `save_units` writes unvalidated POST values
`vald/views.py:976-1000` assigns `request.POST.get(...)` straight onto the model. Django
does not enforce `choices` on `.save()`, and SQLite ignores `max_length`, so arbitrary
strings persist and then feed flag generation in `create_job_config`. Use a `ModelForm`.

### R25. `error_message` leaks internal paths
`request_detail.html:64` renders raw Fortran stderr and Python exception text, including
absolute server paths. Show a generic message; log the detail (needs R13).

### R26. Account enumeration
`login` distinguishes "not imported yet" from "not registered", and `RegistrationForm`
says "already registered". Probably acceptable for this user base — noting it as a
conscious choice rather than an oversight.

### R33. Completion email attaches the full result file
`vald/views.py:922-927` attaches results **and** includes download links. The 50 Å form
check bounds the size, but only loosely: the f2py comparison measured ~30 000 lines from
6 Å at H-alpha, so 50 Å in a dense region is plausibly a few hundred thousand — a
multi-MB attachment. Legacy capped email at 1 000 lines (R29), which is what made
attaching safe in the first place. Some mail servers will bounce it, and a bounce means
the user gets no notification at all despite the files being ready.

**Fix:** restore the email line cap (R29), or drop the attachment and rely on the links.

### R34. `safe`-tagged messages render unescaped
`base.html:85` renders `{{ message|safe }}` when `'safe' in message.tags`. Currently only
used for the "Forgot your password?" link with a `build_absolute_uri` value, so it is not
exploitable today — but it becomes XSS the moment user data goes into a `safe`-tagged
message.

---

## Second pass — 2026-08-07

Review of the same tree after the P1/P2 work, looking for what the first pass
missed. All five were confirmed by test before fixing; the regression tests live
in `tests/test_security.py`.

### R38. A session outlives the account state it was granted under
**Where:** `vald/views.py` — `require_login`, `get_current_user`, `login`, `set_password`

R1 fixed the `is_active` check *at the login gate*. Nothing rechecked it for an
already-established session, so three things that ought to end a session did not:

- **Suspension.** Unticking `is_active` left the user fully functional —
  submitting jobs, downloading, editing config — until the cookie expired,
  two weeks out by Django's default. The admin help page (`admin.py:94`)
  told the operator the opposite.
- **Password reset.** A stolen session survived the victim's own recovery.
- **Login itself.** No `cycle_key()`, so a session id fixed before login became
  an authenticated one (fixation).

**Fixed:** `User.session_auth_hash()` (a salted HMAC of the password field,
mirroring `django.contrib.auth`) is stored at login and compared on every
request, alongside a fresh `is_active` check, in `get_current_user()` — now the
single choke point, cached per request. `start_session()`/`end_session()`
replace the ad-hoc session writes and add `cycle_key()`. `SESSION_COOKIE_AGE`
also dropped from Django's two-week default to one week.

### R39. `?modify=` is raw query string, not a validated UUID
**Where:** `vald/views.py` — the four extraction form views

`Request.objects.get(uuid=...)` caught only `DoesNotExist`, but a value that is
not a UUID at all raises `ValidationError` from the field. Any hand-edited URL
was a 500 — and with R13's `ADMINS` wired up, an email per hit.

**Fixed:** one `modify_initial_data()` helper catching the full set, shared by
all four views, which were 95% duplicated. The ownership check itself was sound
and is now covered by a test.

### R40. Out-of-range rank weights are a 500, not just bad config
Supersedes the R30 won't-fix, which was accepted on the reasoning that the blast
radius is the user's own extractions. That holds for the *values*, but not for
the crash: `int()` accepts arbitrary precision and sqlite raises `OverflowError`
past 2^63, so `edit-val-0=9999…` (25 digits) was an unhandled 500 on
`/persconf/`. Moderate values persisted and reached the Fortran config parser.

**Fixed:** `persconfig.clamp_rank()`, applied both where the POST is parsed and
in `update_config_linelist()` — the latter being the choke point that persists,
so nothing downstream has to trust the view.

### R41. The admin is a public login form too
- `/admin/login/` had no throttle of its own while the VALD login next door is
  limited to 5/min, so staff passwords were guessable at full speed.
  **Fixed:** `admin.site.login` wrapped in `django-ratelimit`, `VALD_ADMIN_LOGIN_RATE`
  (default `10/h`), `block=True` — there is no admin template in which to render
  a friendly retry message, so 403 is the honest answer.
- `user_change_password` enforced `len >= 6` only, bypassing the
  `AUTH_PASSWORD_VALIDATORS` that R6 wired into every other path — the one
  password an admin sets by hand was the weakest the site allowed.
  **Fixed:** `validate_password(password, user)`.

Still open, and not a code question: which `django.contrib.auth` superusers exist
on the production database, and whether any carries a development-era password.

### R42. `MAINTENANCE` was a setting that did nothing
Defined in both settings modules and documented in `secrets.txt.example` as
"Show a maintenance notice instead of serving requests". No middleware, view or
template read it. Flipping it on cutover day would have appeared to work.
**Removed** rather than implemented — the dangerous state was the one where it
looked available.

### R43. No upper bound on the extraction wavelength range — accepted
`ExtractAllForm` accepts `stwvl=0.01, endwvl=1e30`; the only range check is the
50 Å email cap. `VALD_MAX_LINES_PER_REQUEST` and `VALD_JOB_TIMEOUT` bound the
work, so the cost ceiling is 5 in-flight jobs × 1 h per user.
**Decided (Tom):** leave it. The existing caps do their job.

---

### R44. showline missed both halves of R25
**Where:** `vald/job_runner.py` — `_run_showline`, and the generic handlers in
`run` / `_run_extract` / `_run_stellar`

`summarise_stage_error()` — which strips gfortran backtrace frames and reduces
absolute source paths to a basename — was reached from exactly one place,
`_stage_error()`. showline runs its own subprocess loop and never called it, so
raw stderr went to two destinations: `Request.error_message` (R25's target) and
the result file itself.

The result file made it worse. `_run_showline` moved the `.txt` into
`VALD_FTP_DIR` and chmod 644'd it *before* checking `failures`. On failure no
`Request` row ever points at that file, so it sat in a directory the vhost
serves directly (R37) for 48 h, containing a backtrace, referenced by nothing.

**Fixed:** scrub showline stderr through `summarise_stage_error()`; publish only
on success, keeping the file in the job directory next to the stage `.err` files
otherwise. The same scrubbing was extended to the three generic
`except Exception` handlers, which returned `str(e)` — a missing binary reports
its full path that way — so the invariant now holds for every path that reaches
a user, not just the pipeline stages.

### R45. Expired sessions are never deleted
Django ignores expired session rows on read but only deletes them when told to,
and nothing was telling it. Pure table growth, one row per login, in the same
sqlite file as everything else. **Fixed:** second `ExecStart=` in
`vald-cleanup.service` running `manage.py clearsessions`.

### R46. Result filename collisions — not a defect
Raised and withdrawn. `{ClientName}.{6-digit}` is not unique by construction:
two users whose alphanumeric-reduced names match could in principle collide.
But `submit_request_direct` checks `working/NNNNNN/.uuid` and increments the ID
on a mismatch, and `cleanup_old_results` sweeps job directories and result files
from a single `cutoff_time` — so the guard is live for exactly as long as the
file it protects. No collision window exists.

The coupling is worth remembering: job directories are kept for the full
retention period *only* because the collision guard reads them, though their
contents are dead on completion. In one dev tree, `TMP.LIST` intermediates were
71% of 106 MB in `working/` against 3.9 MB of delivered results — peak disk
runs 20-30x the delivered size. Sweeping job directories sooner would reclaim
that, but requires moving the guard to look at `VALD_FTP_DIR` instead.
**Decided (Tom):** leave it, the array is large.

---

### R32 (fixed). Partial constraint for the system default
Migration `0010_system_default_config_unique` adds
`UniqueConstraint(fields=['is_default'], condition=Q(user__isnull=True, is_default=True))`,
which the original constraint could not express because `user IS NULL` makes it
vacuous.

A `RunPython` step precedes it, because the constraint cannot be applied to a
database that already has the problem. It keeps the row `get_default_config()`
was *already* returning - `.first()` under `Meta.ordering = ['user', 'name']`,
so lowest name then pk - and demotes the rest to `is_default=False` rather than
deleting them, since they own `ConfigLinelist` rows.

Verified against a copy of the real database with two extra system defaults
inserted by raw SQL, one sorting before `'Default'` and one after: the migration
kept the row the app had been using, demoted the other two, and the constraint
then rejected a third.

**Operational note:** on a database that has this problem, the config that
survives is whichever one the app was already using - which is not necessarily
the one named `Default`. That is the safe choice (applying the migration cannot
silently change anyone's linelist selection), but it is worth checking
afterwards that the surviving system default is the intended one.

---

### R47. The personal config could only ever freeze
**Where:** `vald/persconfig.py`, `vald/views.py` (`persconf`), `persconf.html`

A personal config is a *snapshot* of the default, and `import_default_config`
only ever rebuilds the system config's rows - nothing reconciles user configs.
So a personal config never sees a linelist added by a later VALD release.
Measured on a three-linelist default: after adding a fourth, the user's
generated `.cfg` still had three.

The state "no personal config, follow the VALD default" existed in the code but
was unreachable, because opening `/persconf/` created a config as a side effect
of a GET (the original R24). Net effect: **clicking Personal Config once,
changing nothing, silently froze the user at that day's linelists** - with
nothing in the interface saying so.

**Fixed** by making both states reachable and each action mean one thing:

| state | meaning |
|-------|---------|
| no personal config | requests use the VALD default, including future additions |
| personal config | snapshot taken at the first edit, plus edits; does not follow the default |

- GET creates nothing. `get_user_config()` is read-only; `create_user_config()`
  and `get_or_create_user_config()` are separate and called only from paths that
  mean "customise".
- **Save** performs the transition and says so.
- **Remove** deletes the config, returning the user to tracking the default.
- **Set to current VALD default** re-snapshots: stay frozen, pinned to today.
- The page states which state you are in, when the snapshot was taken, and which
  linelists have been added to the default since - without that the snapshot is
  the same trap with better buttons.

Two consequences worth recording:

*The form now names a `Linelist`, not a `ConfigLinelist`.* It had to: a user
who tracks the default is shown the *system* config's rows, so a pk posted back
would name a row they do not own - exactly R2. Keying by linelist leaves the
choice of junction row to the view, always inside the poster's own config, so
that whole class of mistake became unrepresentable rather than guarded against.

*Migration `0011` deletes personal configs byte-identical to the default.* Under
the two-state model, holding a config means "do not follow the VALD default",
and the users who got one from a bare GET never asked for that. Configs
differing anywhere - including in fields the web UI cannot edit, which is how
the imported legacy persconf files differ - are left alone. Verified against a
copy of the real database: the one user config there differs in four entries
(one disabled list, one rank, one replacement window) and was correctly kept.

R24's second half dissolves rather than being fixed: `pconf=personal` with no
personal config returns the default, which under this model is the correct
answer rather than a silent fallback.

### R48. `Linelist.is_active` was decorative
Declared as "whether this linelist is currently available", never read by
`generate_cfg_content()` and never written by `import_default_config`. An admin
unticking it had no effect.

This is the mechanism a snapshot model needs most: a personal config can outlive
the linelists in it by years, and a retired linelist means a `.cfg` naming a
data file that may have left the SVN tree, which surfaces as an opaque
preselect5 failure. **Fixed:** generation skips inactive linelists, and
re-importing `default.cfg` retires anything absent from it (and reactivates
anything that returns).

### R49. Four template comments were rendering as page text
`{# #}` does not span lines - Django emits the remainder as content. Three
predated this work (`admin/index.html`, and the user changelist and change form)
and were visible on live admin pages. Found by looking at the rendered output
rather than the template. `tests/test_templates.py` now scans every template for
it.

---

## P4 — UX, low-hanging

### R20. My Requests is unusable at scale
`my_requests.html` shows type / status / date only — no wavelength range or element, so
40 requests are indistinguishable without opening each one. `req.parameters.stwvl` etc.
are already available in the template. Also no pagination, and each row triggers 3–4
filesystem stat calls via `output_exists` / `output_is_empty` / `bib_output_exists`.

**Highest value-per-line fix in this document.**

### R21. Expired results still show "Complete"
`cleanup_old_results` deletes files without touching the DB, so after 48 h the row still
says Complete with a `—` download cell. Say "Expired — results are kept 48 h". Needs R18
to distinguish expired from missing.

### R22. The spam filter rejects legitimate bug reports
`vald/utils.py:58-82` rejects any message containing `http://` or `https://`, and anything
under 10 characters, both reported to the user as "classed as spam". Someone linking a
paper or a screenshot cannot contact you at all. Verified.

### R24. "Custom" config falls back silently; persconf mutates on GET
Selecting Custom when you have no personal config silently uses the system default
(`Config.get_user_config` fallback) with no notice. Conversely, merely **visiting**
`/persconf/` calls `get_user_config()`, which creates a personal config and 377
`ConfigLinelist` rows as a side effect of a GET.
