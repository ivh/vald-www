# The HFS stages abort on an empty stream

## Symptom

An extract with **HFS splitting** over a range that matches no lines fails with

```
post_hfs_format5 failed: At line 33 of file post_hfs_format5.f
(unit = 5, file = 'stdin') Fortran runtime error: End of file
```

Two production requests hit it on 2026-09-07 (ids 3005 and 3007, both `Hg 2`
over 3983-3985 A, long format, cm^-1, HFS on) -- the only failed rows in the
database. The same request with HFS off completes, which is what makes it look
like an HFS bug rather than an empty-result bug.

## Cause

`Hg 2` genuinely has no lines in that window. The pipeline then degrades
unevenly:

| stage | on an empty result |
|---|---|
| `preselect5` | writes its 2 header lines, no `WLmin` line, exits **0** |
| `presformat5` | first data read is `READ(*,'(A)',END=2)` -- branches, exits **0**, no output |
| `hfs_pres` | first read at line 380 has **no `END=`** -- aborts on EOF, rc=2 |
| `post_hfs_format5` | first read at line 33, same, aborts |

So `presformat5` already handled the case correctly and the two HFS stages did
not. Confirmed by feeding the same `pres_in` through the stages by hand:

```
3983-3985  Hg 2  hfs=1   pre_lines=2   fmt_lines=0    <- the failing case
3900-4100  Hg 2  hfs=1   pre_lines=2   fmt_lines=0
3983-3985  Fe 1  hfs=1   pre_lines=32  fmt_lines=84   <- HFS path itself is fine
```

## Fix

Give the two HFS stages the quiet exit `presformat5` already has, jumping to a
label that already exists in each program unit:

```fortran
!  hfs_pres.f:380          5 is the existing "STOP"
      READ(*,'(A)',END=5) REFFIL
!  post_hfs_format5.f:33   2 is the existing "CONTINUE / END"
      READ(*,'(A)',END=2) REFFIL
```

Only the **first** read of each is guarded, so a stream truncated part-way
through still fails loudly. Patch against SVN r3754 kept at
`~/vald-local-changes/PATCHES/hfs-empty-stream.patch`.

**Status: applied on both the server and this Mac** (2026-09-07), rebuilt and
installed in both `bin/`. Not committed to SVN.

An empty HFS extract now behaves like a non-HFS one: `complete`, with an empty
38-byte `.gz`. Whether that is the right answer is a separate question -- see
"Still open" below.

## The stellar red herring

While confirming the fix, stellar + HFS was found to fail on this Mac at every
wavelength range with a *different* error, and was initially written up as a
second latent production bug. It is not: production request 3015 (500-501 nm,
Teff 7900, log g 0, HFS on) completes in 21 s.

It was `-std=legacy`, which only this machine had. `select5`'s HFS branch reads
at `select5.f:1622` with a format ending `...1X,A16,9I4`, the same shape that
broke `presformat5`, and `select5` was still being compiled with the flag. Under
it the read misparses on the 31st line, and `select5` writes its complaint into
**stdout** rather than stderr:

```
SELECT detected format error in sp. line #          31
```

`hfs_pres` passes those lines through unexamined, and `post_hfs_format5:117`
then tries to read the complaint as the range header
(`READ(STRING1,*) WLstart,WLend,NLINES,NTOT,Vmicro`), giving

```
post_hfs_format5 failed: At line 117 of file post_hfs_format5.f
Fortran runtime error: Bad real number in item 1 of list input
```

Rebuilding `select5` without the flag: 291265 lines out, correct range header,
both HFS stages rc=0, 59008 lines in `select.out` plus `post_selected.bib`. The
flag was then dropped from `CONFIG/Makefile_local.inc` wholesale -- see
`presformat_fix.md`.

Two lessons worth keeping:

1. **Check whether a Fortran-stage failure reproduces on prod before treating it
   as a VALD bug.** The build flags differ per machine and are unversioned.
2. **The reported stage is not the failing stage.** Both of the errors in this
   note name `post_hfs_format5`, and in neither case is it the cause.

## Still open

- `JobRunner._check_stages()` (`vald/job_runner.py:262`) checks downstream-first
  so that a downstream stop reported as upstream SIGPIPE does not mislead. When
  two stages both die with a real error, though, it names the downstream one:
  it reported `post_hfs_format5` when `hfs_pres` was the first real failure.
- A zero-match extraction returns an empty 38-byte `.gz` marked `complete`, in
  both the HFS and non-HFS paths. "No lines matched your selection" would be
  more honest than a file the user has to unpack to discover is empty.
