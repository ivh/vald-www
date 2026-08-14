# hfs_pres: build fails under gfortran 15+ on the name `split`

## Symptom

`make` in `SOURCE/SELECT` fails to compile `hfs_pres.f`. This is a **build**
failure, not a runtime one -- everything else in the tree builds.

```
hfs_pres.f:215:72:
  215 |      *              rref1,rref2,refID1,refID2)
      |                                                                        1
Error: Too many arguments in call to 'split' at (1)
hfs_pres.f:84:72:
  ... same at the other call site ...
Fatal Error: Cannot open module file 'reallocate.mod' for reading at (1)
```

The `reallocate.mod` line is a knock-on, not a second problem: `MODULE
reallocate` occupies lines 5-338 of the same file, the two errors are inside it,
so no `.mod` is written and the `use reallocate` at line 348 then has nothing to
open. Fix `split` and it goes away.

Seen with Homebrew GCC 16.1.0. Line numbers are as of SVN r3754.

## Cause

`hfs_pres.f` calls `split` at lines 83 and 214 (in `LINES_FORMAT` and
`LINES_SELECT`) with ten arguments. That `split` is the external subroutine
defined in `hfs_vald.f` -- confirmed by `nm hfs_vald.o`, which exports `T
split_`, and by the old binaries, which contain the same symbol.

**Fortran 2023 added an intrinsic subroutine `SPLIT`** (a string tokeniser,
`SPLIT(STRING, SET, POS [, BACK])`). gfortran 15 and later implement it. The
calls have no interface and no `EXTERNAL` declaration, so the compiler is free
to bind them to the intrinsic, which takes at most four arguments -- hence "Too
many arguments". Older gfortran had no such intrinsic and resolved the name to
the external, which is why the source has been fine until now.

`-std=` is not a lever here. gfortran exposes the full intrinsic set regardless,
and a one-line probe calling `split` with the intrinsic's own signature compiles
clean under `-std=legacy`, `-std=gnu` and `-std=f2018` alike. Adding
`-std=legacy` to the `hfs_pres.o` rule does **not** suppress the error. (Note in
passing that this rule uses `${LFLAG}` rather than `${F77FLAG}`, so `hfs_pres.f`
is compiled without `-std=legacy` in any case.)

## Fix

Declare the name as external in the two procedures that call it, so the
compiler cannot reach for the intrinsic:

```fortran
          INTEGER CODE, refID1(3), refID2(3), LEN1, IREF(9), ihfs, I
!
! SPLIT became an intrinsic subroutine in Fortran 2023, so without this
! gfortran 15+ binds the call below to the intrinsic and rejects it as
! "Too many arguments". Ours is the external in hfs_vald.f.
!
          EXTERNAL split
```

and the same in `LINES_SELECT`. That is the whole change: two declarations, no
Makefile change, no change to `hfs_vald.f` or to any call site.

`EXTERNAL` is standard Fortran and has always meant exactly this, so the fix is
a no-op on the older compilers currently in use at the mirrors -- it can go in
without waiting for anyone to upgrade. Every mirror will need it as soon as it
reaches gcc 15.

The alternative is to rename `split` to something unlikely to collide (say
`hfs_split`) in `hfs_vald.f` and both call sites. That is cleaner long-term and
immune to the next intrinsic that lands on a common name, at the cost of a larger
diff to shared source. Either is fine; the `EXTERNAL` version is the minimal one.

## Verification

Rebuilt the whole tree on macOS/arm64 with gfortran 16.1.0:

```bash
cd $VALD_HOME/SOURCE
VALD_HOME=$VALD_HOME make clean && VALD_HOME=$VALD_HOME make all && \
VALD_HOME=$VALD_HOME make install
```

Clean build. Ran a stored HFS extract-element job (Cr 2, 4438.914-4440.914 A,
`preselect5 | presformat5 | hfs_pres | post_hfs_format5`) through the rebuilt
binaries: output is **byte-identical** to the result the same request produced
before the rebuild. Non-HFS extract and stellar paths unchanged.

## Not the same as `presformat_fix.md`

`notes/presformat_fix.md` describes a *runtime* failure in the same HFS pipeline
(`FORMAT ERROR IN LINE #` out of `presformat5`, from comma-termination of `A16`
character fields under `-std=legacy` on gfortran >= 13). The two are independent:
one stops the build, the other corrupts a read at run time on particular data.

Worth noting that the `presformat5` fix described in that note does **not**
appear to have been applied to the source -- an HFS extract-all over 5000-5002 A
still fails exactly as documented there, and 28 of 75 HFS requests in our test
database failed with that error and its `post_hfs_format5` follow-on. Both fixes
are needed for a working HFS pipeline on a current toolchain.
