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

`-std=legacy` cannot simply be dropped: `presformat5.f` has 9 output `FORMAT`
statements with a missing comma between an `A14` descriptor and a following
quote literal (`...1X,A14''''`), a legacy extension that requires the flag.

## Fix

1. Add the standard-required comma in those 9 formats: `1X,A14''''` becomes
   `1X,A14,''''`. This removes the only dependence on `-std=legacy`.
2. Build `presformat5` without `-std=legacy` (everything else can keep it).
   Target-specific override in `SOURCE/SELECT/Makefile`:

   ```make
   presformat5: F77FLAG := $(filter-out -std=legacy,$(F77FLAG))
   ```

Only `presformat5` needs rebuilding -- `post_hfs_format5` reads its *reformatted*
output, not the raw comment, so the bug is isolated there.

Verified on gfortran 13.4.0: HFS extract-all that previously failed now returns
correct output; non-HFS and stellar paths unchanged.
