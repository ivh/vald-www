# Telling the web app that an extraction hit VALD_MAX_LINES_PER_REQUEST

## The question

When a request is truncated at `VALD_MAX_LINES_PER_REQUEST` (500000), can the app
find out, so `request_detail.html` can show a "truncated" badge instead of
handing the user a silently short file?

Truncation is not a corner case. A 3 A window of `default.cfg` extract-all
yields 17376 records, so ~5800 records/A and the cap lands at roughly **86 A**.

## Status: implemented (2026-08-20), Option A

Upstream took Option A. `preselect5.f90:1615` now reads

```fortran
        IF(mxextr > 0 .AND. ntotal >= mxextr) STOP &
         'VALD-TRUNCATED: maximum number of lines reached'
```

Rebuilt with `make VALD_HOME=/Users/tom/VALD3 preselect5` in
`SOURCE/PRESELECT` and copied to `bin/` (not `make install`, which would have
replaced every binary in `EXE`). Measured after the rebuild:

| run | rc | stdout | stderr |
|---|---|---|---|
| cap 5, 17376 available | 0 | 12 lines = 5 records | `STOP VALD-TRUNCATED: ...` |
| cap 0 (unlimited) | 0 | 34754 lines | empty |
| cap 17376, 17376 available | 0 | 34754 lines | `STOP VALD-TRUNCATED: ...` (the exact-hit false positive) |

Uncapped stdout is **byte-identical** to the previous binary
(`sha1 f1e023b4a194e20c323448475593fcbbf0b24853`), so the only behavioural change
is the stderr line. `tests/test_backend_binaries.py` passes, including the
`xpass` that says the installed binaries still carry the HFS fix.

**The Fortran change is uncommitted in SVN** (working copy r3754 = HEAD, file
shows `M`), so it is in the same rsync-fragile category as the two changes in
`presformat_fix.md` and `hfs_split_fix.md` -- but it is *not* yet in
`~/vald-local-changes/MANIFEST.tsv`. If it gets reverted, detection degrades
silently to today's behaviour: no token on stderr, no badge, no error.

App side, in this repo:

- `job_runner.py` -- `PRESELECT_TRUNCATED` / `SELECT_TRUNCATED` tokens,
  `JobConfig.truncated`, and `JobRunner._was_truncated()` called from
  `_finalize_output()`, so extract and stellar go through one detector.
- `backend.py` -- `submit_request_direct()` records
  `parameters['truncated']` and `parameters['truncated_at']` on the Request;
  no migration, and the caller's existing `save()` persists them.
- `request_detail.html` -- "— truncated" beside Complete, plus a row naming the
  limit and what to do about it.
- `tests/test_truncation.py` (backend plumbing + page), plus real-binary cases in
  `test_backend_binaries.py` and stand-in cases in `test_pipeline.py`.

Verified end to end with the real binaries and `VALD_MAX_LINES_PER_REQUEST = 5`:
the request row came back `{'truncated': True, 'truncated_at': 5}` and the page
rendered both the badge and the explanation.

Deliberately **not** done: the delivered extract file is unchanged. Prepending
select5's warning line to it would give parity with stellar, but it shifts the
header line every existing extract parser expects. That is a data-format
decision, not a display one.

Incidental: `preselect5` holds the config path in `CHARACTER(LEN=80)`, so a
working directory deep enough to push the generated `.cfg` past 80 characters
fails with `PRESELECT: EOF when reading header of config file` -- on *stdout*,
which then kills `presformat5` at its `F15.5` read. Production paths are far
short of that; it only showed up under a pytest tmp path.

## Verdict

- **Return codes are useless.** Every stage exits 0 whether or not the cap bit.
- **Stellar already reports it**, in the output file, not on stderr.
- **Extract all/element reports nothing at all.** Making it report needs one
  line of Fortran; the cheap-looking alternative is worse than it looks.

## What actually enforces the cap

`create_job_config()` (`vald/job_runner.py:968-970`) splits the cap two ways:
extract sends it to preselect in `pres_in` line 2, stellar sends `0` to preselect
and the real cap to select via the last line of `select.input`.

| Pipeline | Enforcer | Signal on hit | rc |
|---|---|---|---|
| extractall / extractelement | `preselect5.f90:1615` -- `IF(mxextr > 0 .AND. ntotal >= mxextr) STOP` | **none** -- bare `STOP`, nothing on stdout or stderr | 0 |
| extractstellar | `select5.f:1601` -- `NSELCT.EQ.MAXLIN` | ` WARNING: Output was truncated to N lines` to **unit 12 = `select.out`**, i.e. line 1 of the delivered file | 0 |
| extractstellar + HFS | same | same warning to **stdout**, i.e. into the pipe feeding `hfs_pres` | 0 |
| showline | no cap in the path | -- | -- |

Verified by running the stages by hand, same window, `default.cfg`:

```
cap=5   preselect5 -> 12 lines   (5 records x 2 + 2 header), rc=0, stderr empty
                     presformat5 -> 12 lines, 5 data rows,   rc=0, stderr empty
cap=0   preselect5 -> 34754 lines (17376 x 2 + 2),           rc=0, stderr empty
                     presformat5 -> 17437 lines, 17376 rows, rc=0, stderr empty
```

`presformat5` has no cap logic and never learns the limit: no `MAXLIN`/`mxextr`
anywhere in its 1334 lines, nothing in `strings bin/presformat5`, and its only
input is preselect's stream. Stellar output additionally carries the counts for
free on the range line -- `5700.0, 5703.0, 5, 8811, 2.0 ... lines selected,
lines processed, Vmicro`.

## What the app can see today

`_check_stages()` (`vald/job_runner.py:245-274`) branches **only** on
`proc.returncode`; `_stage_error()` reads a `.err` file solely to compose the
message for a stage that already failed. So stderr bytes on an rc=0 stage are
inert, and adding some cannot change control flow.
`tests/test_pipeline.py:117` already writes to preselect5's stderr on a run it
then asserts succeeds.

## The original author's account

He recalled that for extract it is `presformat5` that enforces the limit and
emits a message to stderr, as select does for stellar. Neither half holds for the
tree as it stands (working copy is at HEAD, r3754; the only local mods are the
two build fixes in `presformat_fix.md` and `hfs_split_fix.md`). select's warning
goes into the *data stream*, never to stderr -- `select5.err` on a truncated
stellar run contains only the IEEE underflow note.

But he is remembering something real: **the receiving half of a presformat
truncation warning is already written and shipped.**

```fortran
! hfs_pres.f:390
      IF(INDEX(HEADER, 'WARNING:').GT.0) THEN
! Found a list size truncation warning. Skip it and read the next line.
```
```fortran
C post_hfs_format5.f:80
C Check if we found a list size truncation warning. Skip it and read the next line.
      IF(INDEX(STRING1, ' WARNING:').GT.0) THEN
```

Both checks sit *before* the branch deciding whether the stream came from
PRESFORMAT or from SELECT, and `post_hfs_format5.f:104` spells out why it is
repeated: *"for 'extract stellar' if present it will be after the name of the
output file while for other requests there is no output filename."* "Other
requests" is the presformat path. So it was designed for. Only the emitter was
never written.

## The constraint that decides between the options

select5 can put its warning at the **top** of the output because it buffers the
whole selection into `TMP.LIST` and writes the file at label 6, after the count
is known. preselect5 **streams**: the cap fires at `preselect5.f90:1615` inside
`mergep`, after N records have already gone down the pipe. So an in-stream
warning from preselect can only ever be **trailing**, and every downstream skip
check above is header-position only -- none of them looks in the record loop.

What a trailing warning line does to the current `presformat5`:

```
presformat5 < (preselect stream + " WARNING: Output was truncated to 5 lines")
  -> rc=0, 5 data rows, stderr empty,
     and " FORMAT ERROR IN LINE #     6" written into the user's output file
```

rc=0, so `_check_stages` passes, the request is marked complete, and the
delivered file ends in a bogus error line. Same signature as the bug in
`presformat_fix.md`. A warning inserted in the header region instead is at least
loud -- `presformat5.f:82` dies rc=2 with "Bad value during floating point read"
on the `READ(STRING1,'(F15.5)') WLmin` -- but there is no position at which the
current binary tolerates it.

## Option A -- preselect5 announces on stderr (recommended)

```fortran
! preselect5.f90:1615
        IF(mxextr > 0 .AND. ntotal >= mxextr) STOP &
          'VALD-TRUNCATED: maximum number of lines reached'
```

Motivation: stderr is a side channel, so the streaming constraint above stops
mattering -- position is irrelevant and no downstream stage ever sees the bytes.
It is one line in one file, and the idiom is already local: `preselect5.f90`
carries three message-bearing STOPs (lines 306, 311, 730) beside ~13 bare ones.

Verified on gfortran 16.1.0, the compiler that builds `bin/`:

```
$ ./stoptest 2>/dev/null        # stdout: complete, flushed, unchanged
$ ./stoptest 2>&1 >/dev/null    # stderr: "STOP <message>"
$ ./stoptest >/dev/null 2>&1; echo rc=$?
rc=0
```

`STOP` with a character code is still *normal* termination, so units flush
exactly as the bare `STOP` does now, and the downstream stage sees a clean EOF.
Confirmed against the real binary by firing the existing
`STOP 'Bad input for maximum number of lines.'` (`preselect5.f90:311`, reachable
with `max_lines=-1`) through the app: `preselect5.err` held
`STOP Bad input for maximum number of lines.`, preselect rc=0, and
`_check_stages` did not flag it.

Use a distinctive token, not "WARNING", so the detector cannot confuse it with
the other message-bearing STOPs. The message need not carry the count: the app
wrote the cap into `pres_in` itself. If the count is wanted anyway, `STOP` takes
a constant stop-code, so write it explicitly first and keep the bare STOP:

```fortran
          WRITE(ERROR_UNIT,'(A,I0,A)') 'VALD-TRUNCATED: ', ntotal, ' lines'
```

Residual risk is essentially nil for the app. The only way to break a consumer
is `2>&1 |` merging stderr into the pipe, and the one merging pattern in the tree
-- the legacy csh job scripts `bin/job.000000`, `EMS/job.013262` -- uses
`(... > out) >>& err.log`, which sends stderr to the log, not the pipe, and
neither path runs any more. An interactive user gets the message printed, which
beats today's silence. **The real exposure is the rebuild, not the line**:
`make clean` is mandatory, `VALD_HOME` is compiled in, and gfortran 16 needs the
`EXTERNAL split` fix -- see `hfs_split_fix.md` and the `~/vald-local-changes`
manifest.

## Option B -- in-stream warning, completing the author's design

preselect5 writes ` WARNING: Output was truncated to N lines` to stdout at the
cap, matching select5's convention, and the warning rides through to the output
file for extract exactly as it does for stellar.

Motivation: one convention for both pipelines, one detector in the app ("does
the head of the output contain `WARNING: Output was truncated to`"), the warning
visible to CLI and legacy consumers too, and it is not a new feature but the
missing half of one the author already built.

Cost, given the streaming constraint: the warning is trailing, so tolerance is
needed **in the record loops, not the header prologues**.

1. `preselect5.f90:1615` -- emit before stopping.
2. `presformat5.f` -- treat a `WARNING:` line as end-of-data, pass it through,
   stop. Without this the delivered file silently gains
   `FORMAT ERROR IN LINE #` (measured above). Not the ~5-line header-position
   mirror of `hfs_pres.f:390` that it first looks like.
3. `hfs_pres.f` + `post_hfs_format5.f` -- same, for extract+HFS, because their
   existing checks only cover the header.

Three files, and each one that gets missed corrupts output at rc=0 rather than
failing loudly. That is the exact failure mode `presformat_fix.md` documents
losing across an rsync. Do not take this option for the sake of tidiness; take
it only if in-stream parity with stellar is wanted for its own sake -- and if so,
note that Option A plus an app-side prepend at `_finalize_output()`
(`job_runner.py:839`) puts the same line at the *top* of the extract file, which
is where stellar's actually is, with none of the Fortran risk.

## Option C -- no Fortran change

Count data rows during the gzip pass in `_finalize_output()` and compare with
`config.max_lines`. Free, since the file is already streamed. Rejected as the
primary mechanism: `preselect5.f90:1596` increments `ntotal` for a record whose
data line it then suppresses when `hterm` is set and the term info is blank, so
with "have term" requested the row count can undershoot `mxextr` on a genuinely
truncated run and the flag misses silently. Fine as a stopgap until a rebuild is
scheduled, not as the answer.

## App side, either way

Stellar needs no Fortran change at all -- read the first two lines of
`select.out` in `_run_stellar()` before the move at `job_runner.py:587-589`.
That is exact today, and it also lets the warning be lifted out of the delivered
file and shown in the UI instead.

`JobRunner.run()` returns `Tuple[bool, str]`, so the flag needs a third channel:
simplest is a mutable `JobConfig.truncated` set by whichever stage detected it,
read after `run()` in `submit_request_direct()` (`backend.py:271`) and stored by
`views.py:981` into `req_obj.parameters['truncated']` -- JSONField, no migration
-- with a badge beside the `status == 'complete'` block in
`request_detail.html:27`.

One inherited quirk to preserve rather than fix: select5 tests `NSELCT.EQ.MAXLIN`,
so a run whose true count lands exactly on the cap is reported as truncated. The
false positive is in the Fortran and users already see it in their files.

## Side findings

- **extractstellar + HFS is broken independent of all this.**
  `post_hfs_format5` dies rc=2, "Bad real number in item 1 of list input" at
  `post_hfs_format5.f:113` -- the `READ(STRING1,*) WLstart,WLend,NLINES,NTOT,Vmicro`
  range-line read -- for both capped and uncapped runs, so it is not the
  truncation warning. Not investigated further here.
- The existing `STOP '<message>'` paths in preselect5 (illegal wavelength range,
  bad max-lines input) also exit 0, so the app currently cannot distinguish them
  from success either. Truncation *should* stay rc=0 -- truncated output is valid
  output -- but those two arguably should not.

## Re-checking any of this

```bash
# who enforces the cap
printf "5700.0,5703.0\n5\n\n'$VALD_HOME/CONFIG/default.cfg'\n0 0 0 0 0 0 0 0 0 0 0 1 0\n" > pres_in.test
$VALD_HOME/bin/preselect5 < pres_in.test > pre.out 2> pre.err; wc -l pre.out pre.err
$VALD_HOME/bin/presformat5 < pre.out > fmt.out 2> fmt.err; grep -cE "^'[A-Za-z]" fmt.out

# what a trailing warning does to presformat5
{ cat pre.out; echo " WARNING: Output was truncated to      5 lines"; } \
  | $VALD_HOME/bin/presformat5 2>/dev/null | tail -1

# which binaries know the word at all
for b in preselect5 presformat5 select5; do
  echo "$b: $(strings $VALD_HOME/bin/$b | grep -ci truncat)"
done
```
