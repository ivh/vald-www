# presformat5: HFS extraction fails under modern gfortran

## Symptom

"Extract all" (or element) with **HFS splitting** fails on some wavelength
regions with `FORMAT ERROR IN LINE #` from `presformat5`, aborting the
`preselect5 | presformat5 | hfs_pres | post_hfs_format5` pipeline. Regions
without the triggering data succeed, so it looks intermittent. Binaries built
with older gfortran are unaffected.

## Cause

In HFS mode `presformat5` reads each line with a fixed format ending
`...A16,9I4` -- a 16-character reference comment followed by 9 integer reference
IDs. Built with `-std=legacy` on **gfortran >= 13**, a comma *inside* the A16
comment (e.g. the reference `CNO, Na 1, Mg 1`) is treated as an input field
terminator. The A16 read stops at the comma, the following `9I4` reads from the
wrong column, and the record fails. Older gfortran did not comma-terminate
character fields under `-std=legacy`, which is why it worked before.

Minimal reproducer -- read `CNO, Na 1, Mg 1:1220...` with `(A16,9I4)`:
default gfortran gives `iostat=0`; `-std=legacy` gives `iostat=5010`.

## Why now

The source is unchanged; only the compiler moved. The binaries were rebuilt
with a newer gfortran (a homebrew GCC upgrade to 13/16). The comma-termination
of character fields under `-std=legacy` is a gfortran behaviour change between
the old and new versions, so the same source + flag that worked before now
breaks. Any mirror that rebuilds on a current toolchain will hit it.

`-std=legacy` could not simply be dropped at the time: `presformat5.f` had 9
output `FORMAT` statements with a missing comma between an `A14` descriptor and a
following quote literal (`...1X,A14''''`), a legacy extension that requires the
flag.

## Fix

1. Add the standard-required comma in those 9 formats: `1X,A14''''` becomes
   `1X,A14,''''`. This removes the only dependence on `-std=legacy`.
2. Build without `-std=legacy`.

Step 2 was first done as a target-specific override for `presformat5` alone,
`presformat5: F77FLAG := $(filter-out -std=legacy,$(F77FLAG))` in
`SOURCE/SELECT/Makefile`, on the assumption that other targets still needed the
flag. **Superseded 2026-09-07** -- see "Where the flag actually came from" below.
The override has been removed from both trees; the flag is simply gone from this
machine's `CONFIG/Makefile_local.inc`.

Verified on gfortran 13.4.0 at the time, and again on 16.2.0 after the flag was
dropped wholesale: HFS extract-all that previously failed returns correct output;
non-HFS and stellar paths unchanged.

## Where the flag actually came from

`-std=legacy` was never a VALD-wide setting. It comes from
`CONFIG/Makefile_local.inc`, which is unversioned and per-machine, and **only
this Mac had it**:

```
prod  F77FLAG = -O2
Mac   F77FLAG = -O2 -std=legacy
```

So every symptom in this note, and the whole Makefile-override workaround, was
local to one machine. Production could not reproduce any of it. Confirmed
2026-09-07 against the server's own copy of the file (stashed under
`~/vald-local-changes/PROD-r3754/CONFIG/`).

The flag was dropped from the Mac's `Makefile_local.inc` the same day and the
whole tree rebuilt: clean build, `pytest -m vald_binaries` 30 passed + 1 xpassed,
and `select5`'s non-HFS output byte-identical between the old `-std=legacy`
binary and the new one on a 12-million-line `preselect5` stream (500133 lines,
95630856 bytes either way). With the flag gone the target-specific override is a
no-op, and it has been removed from `SOURCE/SELECT/Makefile`, which now matches
SVN r3754 exactly.

**`select5` had the same bug, and it was never fixed -- only unmasked.** Its HFS
branch reads at `select5.f:1622` with a format ending `...1X,A16,9I4`, exactly
the shape described above, and `select5` was still compiled *with* `-std=legacy`
while `presformat5` was not. That is why stellar + HFS extraction failed on this
Mac at every wavelength range and worked on prod. See
`hfs_empty_stream_fix.md`, which walks through how that surfaced as a
`post_hfs_format5` error 100 lines away from the real cause.

## History: the Makefile half was the fragile one

Kept because it explains two past incidents, and because a mirror that still
carries `-std=legacy` will meet all of it again.


Both parts are needed, but they fail differently when lost. The `presformat5.f`
edit only *removes the dependence* on `-std=legacy`; it fixes nothing on its own.
The Makefile override is what actually drops the flag, so losing it silently
reinstates the bug while the source still looks fixed.

That is exactly what happened in the 2026-08-14 rsync from the Linux server:
`presformat5.f` survived, `SOURCE/SELECT/Makefile` was overwritten with upstream
r3750, and HFS extractions started failing again with the same `FORMAT ERROR IN
LINE #`. Both files are local-only changes that have never been committed to
SVN, so any sync from a mirror can take them.

It happened a second time in the 2026-09-07 rsync, the same way. That is what
finally prompted tracing the flag back to `Makefile_local.inc`, at which point
the override stopped being needed at all.

Neither file is tracked in `~/vald-local-changes/MANIFEST.tsv` any more:
`presformat5.f`'s fix reached the server, and `SOURCE/SELECT/Makefile` is back
to plain r3754 on both machines. The manifest now covers only files that are
still local-only -- the per-machine `CONFIG/` and `WWW/config/` ones. After any
rsync into `$VALD_HOME`, check the tree against that manifest before rebuilding.

`tests/test_backend_binaries.py::test_hfs_extract_all_in_the_optical` is the
quick check from this side: it is `xfail(strict=False)`, so an XPASS means the
installed binaries carry the fix and an XFAIL means they do not.
