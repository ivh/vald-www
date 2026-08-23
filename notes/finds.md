# VALD Django — review findings

Findings from the pre-cutover review (2026-08-02/03, `master` @ `f3055ee`) and the
second pass (2026-08-07). IDs are the numbers used in the review discussion, kept
stable because **71 comments and tests across the codebase cite them** — a
`(R19)` in a settings comment is only useful while this file resolves it.

**Pruned 2026-08-23.** Findings that were both fixed *and* uncited were removed;
the table below still names every one of them, and the full original text is in
git at `5db5abe`. What survives is the reasoning the code depends on: invariants
a future change could break, decisions that were made deliberately, and the two
items still open.

Line numbers in `**Where:**` lines are as of the review commit and have drifted.
File and symbol names are the reliable part.

---

## Status overview

Every ID, including those whose detail section was pruned.

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
| R19 | Medium | ✅ dev-only `77492d1`; N/A on server | `collectstatic` could publish result files — deploy trees are separate |
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
| R34 | Low | **open** | `safe`-tagged messages render unescaped |
| R20 | UX | ✅ fixed `8647f39` | My Requests: no λ range/element, no pagination |
| R21 | UX | ✅ fixed `73de058` | Expired results still show status "Complete" |
| R22 | UX| ✅ fixed `8079afb` | Spam filter rejects any message containing a URL |
| R24 | UX | ✅ fixed (see R47) | "Custom" config falls back silently; persconf mutates on GET |
| R38 | High | ✅ fixed | Sessions outlive the account state they were granted under; no key rotation on login |
| R39 | Medium | ✅ fixed | `?modify=<non-uuid>` is an unhandled 500 on all four extraction forms |
| R40 | Low | ✅ fixed | Out-of-range persconf rank is a 500 (sqlite `OverflowError`); supersedes R30 |
| R41 | Medium | ✅ fixed (one operational question open) | Django admin login unthrottled; admin-set passwords skip the validators |
| R42 | Low | ✅ removed | `MAINTENANCE` was documented and configurable but read by nothing |
| R43 | Low | ⏸ won't-fix (accepted) | No upper bound on the extraction wavelength range for download delivery |
| R44 | Low | ✅ fixed | showline error text skipped R25's scrubbing, and failed runs were published anyway |
| R45 | Low | ✅ fixed | `django_session` never swept — no `clearsessions` anywhere |
| R46 | — | ✓ not a defect | Result filename collisions are guarded by construction, not merely improbable |
| R47 | Medium | ✅ fixed `0011` | Personal configs were snapshots that only froze - "track the VALD default" was unreachable |
| R48 | Low | ✅ fixed | `Linelist.is_active` was never read or written; retiring a linelist did nothing |
| R49 | Cosmetic | ✅ fixed | Four multi-line `{# #}` template comments rendered as visible page text |
| R50 | — | ✅ added | Admin had no way to see a user's linelist config; the inline omits ranks and the diff |

---

## Still open

### R34. `safe`-tagged messages render unescaped
`base.html` renders `{{ message|safe }}` when `'safe' in message.tags`. Only the
"Forgot your password?" link uses it, with a `build_absolute_uri` value, so it is
not exploitable today — **but it becomes XSS the moment user data goes into a
`safe`-tagged message.** Nothing enforces that it does not.

### R41 (remainder). Production superusers are unaudited
The code half is fixed (below). Still unanswered, and not a code question: which
`django.contrib.auth` superusers exist on the production database, and whether
any carries a development-era password.

---

## Accepted, with consequences to remember

### R37. The vhost serves the results directory directly
Results live in `$VALD_HOME/WWW/public_html/FTP`, which the vhost serves
directly — as legacy did, where "retrieve via ftp" meant exactly that.

**Decided (Tom): kept as-is.** Result files are the user's own extraction output,
not personal data; VALD data is shared and scientific; files are transient (48 h).

The consequence to keep in mind: `download_request`'s auth/ownership/containment
checks therefore gate **only the in-app download button, not the raw URL**.
Result files are reachable by anyone who knows the filename —
`{ClientName}.{6-digit}.gz`, the 6-digit a SHA256-derived hash of the request
UUID. Since the raw path is public anyway, those two views no longer require a
session or check ownership: the uuid4 is the capability. That is what makes a
link copied out of the completion email work under `wget` — before, those clients
had no cookie, followed the login redirect and saved the landing page as the
"download". Failures on both views are status codes with a plain-text body, never
redirects, so a failed fetch fails loudly.

### R31. A deliberate rank of 3 cannot be expressed
`ConfigLinelist.save()` treats `3` as "unset" and replaces it with the linelist
default on create. `None` would be the right sentinel for "inherit". Left alone.

### R43. No upper bound on the wavelength range
`ExtractAllForm` accepts `stwvl=0.01, endwvl=1e30`. `VALD_MAX_LINES_PER_REQUEST`
and `VALD_JOB_TIMEOUT` bound the work, so the ceiling is 5 in-flight jobs × 1 h
per user. **Left alone: the existing caps do their job.** Note this is also a
deliberate match to legacy, which validated only `wlleft > wlright || wlleft <= 0`
(`parserequest.c:650-655`) — wide ranges are legitimate science and were always
allowed. An early draft of this document called for a range cap; that was wrong.

### R26. Account enumeration
`login` distinguishes "not imported yet" from "not registered", and
`RegistrationForm` says "already registered". Acceptable for this user base —
recorded as a conscious choice, not an oversight.

### R46. Result filenames cannot collide — but the guard costs disk
`{ClientName}.{6-digit}` is not unique by construction: two users whose
alphanumeric-reduced names match could collide. `submit_request_direct` checks
`working/NNNNNN/.uuid` and increments on mismatch, and `cleanup_old_results`
sweeps job directories and result files from a **single** `cutoff_time` — so the
guard is live exactly as long as the file it protects. No collision window
exists.

The coupling is the thing to remember: **job directories are retained for the
full period only because the collision guard reads them**, though their contents
are dead on completion. In one dev tree `TMP.LIST` intermediates were 71% of
106 MB in `working/` against 3.9 MB of delivered results — peak disk runs 20-30×
the delivered size. Sweeping sooner requires moving the guard to look at
`VALD_FTP_DIR` instead. **Decided (Tom):** leave it, the array is large.

---

## Security fixes with regression tests

The tests in `tests/test_security.py` are named for these; do not weaken them
without reading the finding.

### R1, R2, R3. The three exploitable ones
- **R1** (`tests/test_security.py:44`): `is_active` was never checked, so admin
  approval was bypassable. See R38 for the half this missed — the login gate was
  fixed here, established sessions were not.
- **R2** (`tests/test_security.py:90`, `vald/persconfig.py:263`): IDOR — any user
  could rewrite the *system default* linelist config. The system default owns
  low, guessable pks, which is why the tests probe the id space rather than a
  fixed pk (see also R47, which made the whole class unrepresentable by keying
  the form on `Linelist` rather than `ConfigLinelist`).
- **R3** (`tests/test_validation.py:28`): `elmion` / `chemcomp` were injected
  straight into Fortran control files.

### R4. Rate limiting did not do what it said
**Where:** `settings_deploy.py` (`CACHES`, `RATELIMIT_IP_META_KEY`), `vald.service`

Two independent defects, either alone defeating the control:
1. `LocMemCache` is **per-process**; 4 gunicorn workers meant `5/m` was really up
   to 20/m, depending on which worker you landed on.
2. `RATELIMIT_IP_META_KEY = 'HTTP_X_FORWARDED_FOR'` used the **raw** header. A
   proxy *appends*, so a client sending its own value got a fresh bucket per
   request — rotating the prefix bypassed every limit.

The live invariant: use the **rightmost** XFF entry, never the raw header. This is
why `settings.py` sets `RATELIMIT_CLIENT_IP_HEADER` for `vald.ratelimit.client_ip`
and warns against `RATELIMIT_IP_META_KEY` in a comment.

### R5. No submit rate limit, no per-user in-flight cap
**Where:** `views.py` (`handle_extract_request`), `backend.py` (`check_queue_capacity`)

The `@ratelimit` decorators covered contact/registration/auth only; the extraction
path had none, and `check_queue_capacity` counted globally. One user could hold
every admission slot and everyone else got "Server is busy".

**A genuine regression, and about rate, not size.** Legacy took requests by email
through `at`, so submissions were inherently serialized — you could not fire them
in a loop. R5 and R9 were the same problem from two directions and were fixed
together. Cited by both settings modules at `VALD_MAX_QUEUE_SIZE`.

### R6. Password and token hygiene
Reset tokens had **no expiry field and no expiry check** while the email promised
7 days; `AUTH_PASSWORD_VALIDATORS` was configured but `validate_password` was
**never called** (`User.set_password` went straight to `make_password`); the
minimum length was 6 in `forms.py` and 8 in the activation view. All three fixed.
The token lifetime setting in both settings modules cites this.

### R38. A session outlived the account state it was granted under
**Where:** `views.py` — `require_login`, `get_current_user`, `login`, `set_password`

R1 fixed `is_active` *at the login gate*; nothing rechecked it for an established
session. Three things that ought to end a session did not:
- **Suspension.** Unticking `is_active` left the user fully functional until the
  cookie expired — two weeks by default. The admin help said the opposite.
- **Password reset.** A stolen session survived the victim's own recovery.
- **Login itself.** No `cycle_key()`, so a session id fixed before login became
  an authenticated one (fixation).

**Fixed:** `User.session_auth_hash()` — a salted HMAC of the password field,
mirroring `django.contrib.auth` — is stored at login and compared on every
request alongside a fresh `is_active` check, in `get_current_user()`, now the
single choke point and cached per request. `start_session()` / `end_session()`
replace the ad-hoc session writes and add `cycle_key()`. `SESSION_COOKIE_AGE`
dropped from two weeks to one.

This is why a forged session needs `auth_hash` and not just `user_id`.

### R39. `?modify=` was raw query string, not a validated UUID
`Request.objects.get(uuid=...)` caught only `DoesNotExist`, but a non-UUID value
raises `ValidationError` from the field — so any hand-edited URL was a 500, and
with R13's `ADMINS` wired up, an email per hit. **Fixed:** one
`modify_initial_data()` helper catching the full set, shared by the four views
that were 95% duplicated. The ownership check was already sound.

### R40. Out-of-range rank weights were a 500, not just bad config
Supersedes the **R30** won't-fix, which was accepted because the blast radius is
the user's own extractions. True of the *values*, not of the crash: `int()` accepts
arbitrary precision and sqlite raises `OverflowError` past 2^63, so
`edit-val-0=9999…` (25 digits) was an unhandled 500 on `/persconf/`.

**Fixed:** `persconfig.clamp_rank()`, applied both where the POST is parsed **and**
in `update_config_linelist()` — the latter being the choke point that persists, so
nothing downstream has to trust the view.

### R41. The admin is a public login form too
- `/admin/login/` had no throttle while the VALD login next door was 5/min, so
  staff passwords were guessable at full speed. **Fixed:** `admin.site.login`
  wrapped in `django-ratelimit`, `VALD_ADMIN_LOGIN_RATE` (default `10/h`),
  `block=True` — there is no admin template in which to render a friendly retry,
  so 403 is the honest answer.
- `user_change_password` enforced `len >= 6` only, bypassing the validators R6
  wired into every other path — the one password an admin set by hand was the
  weakest the site allowed. **Fixed:** `validate_password(password, user)`.

### R27. `save_units` wrote unvalidated POST values
Assigned `request.POST.get(...)` straight onto the model. Django does not enforce
`choices` on `.save()` and SQLite ignores `max_length`, so arbitrary strings
persisted and then fed flag generation in `create_job_config`. Now a `ModelForm`.

### R25 / R44. Internal paths must not reach users
**R25:** `request_detail.html` rendered raw Fortran stderr and Python exception
text, absolute server paths included.

**R44:** `summarise_stage_error()` — which strips gfortran backtrace frames and
reduces absolute paths to a basename — was reached from exactly one place,
`_stage_error()`. showline ran its own subprocess loop and never called it, so raw
stderr went to two destinations: `Request.error_message` and **the result file
itself**. Worse, `_run_showline` moved the `.txt` into `VALD_FTP_DIR` and chmod
644'd it *before* checking `failures` — so on failure a backtrace sat for 48 h in
a directory the vhost serves directly (R37), referenced by no `Request` row.

**The invariant now:** every path that reaches a user is scrubbed, not just the
pipeline stages — including the three generic `except Exception` handlers, which
returned `str(e)` (a missing binary reports its full path that way). Failed
showline runs stay in the job directory next to the stage `.err` files.

---

## Data and configuration

### R18. `output_file` stored absolute paths
If `VALD_FTP_DIR` moved, every historical request silently reported "Output file
not found" — `output_exists()` just returned `False`, with no distinction between
"moved", "expired" and "never produced". **Fixed:** store the basename, resolve
against `VALD_FTP_DIR` at read time. Absolute values are still honoured so
existing rows keep working.

### R19. Results must never sit inside a `STATICFILES_DIRS` tree
`collectstatic` copies everything under `STATICFILES_DIRS` into `STATIC_ROOT`,
which is served publicly with no auth. If the results directory sits inside one,
results get published.

**This was a dev-config problem only.** Dev had `VALD_FTP_DIR` at
`BASE_DIR/public_html/FTP`, inside a static dir, and that machine held 16 real
result files. On the server it does not apply: deploy `VALD_FTP_DIR` is
`$VALD_HOME/WWW/public_html/FTP` (`/home/vald/VALD3/...`), a different tree
entirely from the static dirs under `BASE_DIR` (`/home/vald/vald-www.git/...`).

**The live invariant**, documented in both settings modules and asserted by
`tests/test_finds_batch.py`: `VALD_FTP_DIR` is inside no `STATICFILES_DIRS` entry.
Dev is safe with results under `public_html/FTP` *only* because dev's
`STATICFILES_DIRS` is just `style/`; deploy adds `public_html` and therefore keeps
results under `VALD_HOME` instead. Adding `public_html` to dev re-opens this.

> **Corrected 2026-08-23.** This finding used to claim, as a second reason, that
> the deployment never runs `collectstatic` and `STATIC_ROOT` is unused. That is
> **wrong**: the deploy does run it, and the web server serves the `staticfiles/`
> snapshot rather than `style/` — so a CSS change needs
> `bin/vald-manage collectstatic` or it silently does nothing. The deploy
> checklist at `/admin/help/` is authoritative. The same wrong claim was in the
> admin help's "things that surprise people" and was removed at the same time.

### R29. The output line cap lost its delivery-method distinction
`VALD_MAX_LINES_PER_REQUEST` was read via `getattr(..., 500000)` but defined in
neither settings module, so the fallback always won and the number was invisible
and untunable.

More substantially, legacy had **two** caps chosen by delivery method
(`parserequest.c:659-663`, `:830-834`, `:1163-1164`):

| delivery | legacy | now |
|---|---|---|
| download | 100 000 | 500 000 |
| email | 1 000 | 500 000 |

The real values live in `valdems.h` / `valdems_local.h` on the server, not in this
repo; those figures come from a comment the pre-jobrunner Python left behind, so
confirm with `grep MAX_LINES_PER $VALD_HOME/**/valdems*.h` before acting on them.

**Decided:** a single 500 000 for both, cited at the setting in both modules. Note
legacy's 100 000 may have been sized to match a fixed array dimension inside
`preselect5`, so raising it is the direction that carries risk; nothing in
`documentation/errors.html` says whether the binary warns on truncation or just
stops. The email path's safety now comes from R33's byte cap instead.

### R33. The completion email attached the full result file
Attached results **and** included download links. Legacy capped email at 1 000
lines (R29), which is what made attaching safe; without that cap a dense 50 Å
region is plausibly a few hundred thousand lines — a multi-MB attachment. Some
mail servers bounce it, and a bounce means **no notification at all despite the
files being ready**.

**Fixed:** the attachment is conditional on `VALD_MAX_EMAIL_ATTACH_BYTES`; over
that the mail carries links only. The old 50 Å form check was removed as
redundant once the size guard existed — it was rejecting requests the backend
handles fine.

### R32. Multiple system default configs were possible
The `UniqueConstraint` on `('user', 'is_default')` does not apply when
`user IS NULL`, because NULLs don't compare equal in a unique index. So several
system defaults could coexist and `get_default_config()` picked by name.

**Fixed** by migration `0010_system_default_config_unique`:
`UniqueConstraint(fields=['is_default'], condition=Q(user__isnull=True, is_default=True))`.
A `RunPython` step precedes it, because the constraint cannot be applied to a
database that already has the problem. It keeps the row `get_default_config()`
was *already* returning — `.first()` under `Meta.ordering = ['user', 'name']`, so
lowest name then pk — and demotes the rest rather than deleting them, since they
own `ConfigLinelist` rows.

Verified against a copy of the real database with two extra system defaults
inserted by raw SQL, one sorting before `'Default'` and one after: the migration
kept the row the app had been using, demoted the other two, and the constraint
then rejected a third.

**Operational note:** on a database that has this problem, the config that
survives is whichever the app was already using — **not necessarily the one named
`Default`**. That is the safe choice, since applying the migration cannot silently
change anyone's linelist selection, but check afterwards that the survivor is the
intended one.

### R47. The personal config could only ever freeze
**Where:** `vald/persconfig.py`, `views.py` (`persconf`), `persconf.html`

A personal config is a *snapshot* of the default, and `import_default_config` only
rebuilds the system config's rows — nothing reconciled user configs, so a personal
config never saw a linelist added by a later VALD release. Measured on a
three-linelist default: after adding a fourth, the user's generated `.cfg` still
had three.

The state "no personal config, follow the VALD default" existed in the code but
was **unreachable**, because opening `/persconf/` created a config as a side
effect of a GET (the original R24). Net effect: clicking Personal Config once and
changing nothing silently froze the user at that day's linelists, with nothing in
the interface saying so.

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
  linelists have been added to the default since — without that the snapshot is
  the same trap with better buttons.

Two consequences worth recording:

*The form names a `Linelist`, not a `ConfigLinelist`.* It had to: a user who
tracks the default is shown the *system* config's rows, so a pk posted back would
name a row they do not own — exactly R2. Keying by linelist leaves the choice of
junction row to the view, always inside the poster's own config, making that whole
class of mistake unrepresentable rather than guarded against.

*Migration `0011` deletes personal configs byte-identical to the default.* Under
the two-state model, holding a config means "do not follow the VALD default", and
users who got one from a bare GET never asked for that. Configs differing anywhere
— including in fields the web UI cannot edit, which is how the imported legacy
persconf files differ — are left alone. Verified against a copy of the real
database: the one user config there differs in four entries (one disabled list,
one rank, one replacement window) and was correctly kept.

**R24 dissolves rather than being fixed:** `pconf=personal` with no personal
config returns the default, which under this model is the correct answer rather
than a silent fallback.

### R50. The admin could not see a user's linelist config
Added a read-only view: the stock inline omits ranks and the diff against the
system default, which are the two things you actually need when a user reports
wrong extraction results.

---

## The backend and the binaries

### R28. `chemcomp` was written unquoted into `select.input`
**Where:** `job_runner.py` (`_write_select_input`)

Abundances were written unquoted, followed by a quoted `'END'`, while the block
echoed in `documentation/reqextstar.txt` is quoted Fortran strings —
`'H :  0.91','He: -1.05',` … `'Es:-20.00','END'`. If `select5` expects quoted
entries, non-solar abundances would be **silently ignored** and stellar
extractions would return solar-abundance results without complaint. Not touched by
the R3 fix, which deliberately preserved the existing byte format. Fixed and
covered by `tests/test_backend_binaries.py`.

### R36. HFS failure: gfortran `-std=legacy` comma-termination
Not an app bug — a Fortran toolchain issue in `~/VALD3`, diagnosed and fixed
locally. **Recorded here because the fix lives outside this git repo and is not
committed to VALD's SVN.**

**Definitive check.** Tom's own request — extract all, 1000-1000.9 Å, cm⁻¹,
vacuum, HFS on — produced `job.021685` for him but failed on the dev machine with
identical parameters. Same request, opposite outcome: the software is not at
fault, the local Fortran build is.

**Root cause.** In HFS mode `presformat5` reads each line with a fixed format
ending `A16,9I4` (16-char reference comment, then 9 integer reference IDs). Built
with `-std=legacy` on modern gfortran (≥13), a comma **inside** the A16 comment —
e.g. the reference `CNO, Na 1, Mg 1` on a nitrogen line near 1000 Å — is treated
as an input field terminator. The A16 read stops at the comma, the following `9I4`
reads from the wrong column, and the record fails with "FORMAT ERROR IN LINE #".
Lines whose reference has no comma are unaffected, so it looked region-dependent.
Minimal reproducer — read `"CNO, Na 1, Mg 1:1220..."` with format `(A16,9I4)`:
default gfortran gives `ios=0`, `-std=legacy` gives `ios=5010`. Reproduces on
gfortran 13 and 16. "Worked before" = built with an older gfortran that did not
comma-terminate A-fields under the flag.

**The trap.** Simply dropping `-std=legacy` breaks the other direction:
`presformat5.f` has 9 output FORMAT statements with a deliberately missing comma
between an A14 descriptor and a following quote literal — a legacy extension that
needs the flag. **The flag is required for the writes but poisons the reads.**

**Fix applied (local, uncommitted), both halves:**
1. `~/VALD3/SOURCE/SELECT/presformat5.f`: the 9 spots where an A14 descriptor is
   followed directly by a quote literal now have the standard-required comma
   inserted, removing the only need for `-std=legacy`.
2. `~/VALD3/SOURCE/SELECT/Makefile`: target-specific
   `presformat5: F77FLAG := $(filter-out -std=legacy,$(F77FLAG))`, so make builds
   just this binary without the flag and everything else keeps it.

Only `presformat5` needed rebuilding — `post_hfs_format5` reads its reformatted
output, not the raw comma-comment, so the bug was isolated there. Verified: Tom's
exact request returns 145 lines through the app.

**Not committed to SVN** per instruction. That working copy's svn pristine store
is incomplete (pre-existing), so `svn revert` will not work; the change is the two
edits above and is trivially reversible by hand.

**Upstream.** The missing-comma formats violate the standard and bite any modern
gfortran; worth reporting to whoever maintains VALD so mirrors rebuilding with a
current compiler do not hit it.

### R7. Background jobs died with the worker
Each request spawned a plain daemon `threading.Thread` inside a gunicorn worker.
Any worker recycle, unrelated timeout kill, or deploy killed in-flight jobs and
left the `Request` row at `processing` **forever** — there was no startup sweep.
Fixed; `reconcile_stuck_requests` is the sweep, and `completed_at` is now written.

This is why a restart during extraction is still worth avoiding, and why the
deploy checklist says to deploy when nothing is in flight.

---

## UX findings that shaped the current pages

### R20. My Requests was unusable at scale
Showed type / status / date only — no wavelength range or element, so 40 requests
were indistinguishable without opening each one. Also no pagination, and each row
triggered 3-4 filesystem stat calls. Fixed; `Request.describe()` is the summary
that came out of it, and `tests/test_my_requests.py` covers the page.

### R21. Expired results still showed "Complete"
`cleanup_old_results` deletes files without touching the database, so after the
retention window the row still said Complete with a `—` download cell — which
reads as a bug. Needed R18 to tell expired apart from missing. `results_expired()`
is the result; the retention setting in both modules cites this.

### R22. The spam filter rejected legitimate bug reports
`utils.py` rejected any message containing `http://` or `https://`, and anything
under 10 characters, both reported as "classed as spam". Someone linking a paper
or a screenshot could not contact you at all. Verified, then fixed.

### R15. There was effectively no test coverage
Only `test_config.py` existed — nothing covered auth, the request pipeline or the
views, and the exploits for R1/R2/R3 had no regression tests. The suite grew out
of this finding.
