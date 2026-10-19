#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""Keep only the lines of a VALD3 extraction whose van der Waals constant
comes from a given reference (default: BPM, Barklem, Piskunov & O'Mara).

Works on Extract All / Extract Element / Extract Stellar output, short or
long format, plain or gzipped. Everything that is not a line record (header,
model atmosphere block of Extract Stellar, reference list) is copied through
unchanged; the "lines selected" count in the Extract Stellar header is
updated.

    python3 filter_vdw_ref.py Cis.123456.gz > bpm_only.txt
    python3 filter_vdw_ref.py --key ABO Cis.123456.gz -o abo_only.txt

Request the extraction with the default linelist configuration, with
references included (the filter has nothing to go on otherwise), and
preferably with "have van der Waals damping" ticked to keep the file small.
Either short or long format works.

--key is the reference key as printed in the file (BPM, K14, ...), not a
number: the numbers in front of the keys index the reference list at the end
of that particular file, so the same source gets a different number in every
extraction, and neither matches the VALD_ref_number of the BibTeX file.

A summary of which references supplied the vdW constants goes to stderr.
The reference list at the end of the file is not pruned, so it still lists
sources that only the dropped lines used.
"""
import argparse
import collections
import gzip
import re
import sys

# A line record starts with the quoted species followed by the wavelength;
# this excludes the model name and abundance lines of Extract Stellar output.
RECORD_START = re.compile(r"^'[^']*',\s+-?\d+\.\d*,")
# "  12 K14" style reference entries. The key must be preceded by exactly one
# space, which keeps out e.g. "Kurucz Fe I 2014   7" in the long-format
# comment field.
REF_PAIR = re.compile(r"(?<!\S)(\d+) (\S+?)(?='|\s|$)")
STELLAR_HEADER = re.compile(r"^(\s*[-\d.]+,\s*[-\d.]+,\s*)(\d+)(,.*)$", re.S)

# Reference order as written by presformat5:
#   short: wl, E_low, gf, Lande, rad, Stark, vdW
#   long:  wl, gf, E_low, E_up, Lande, rad, Stark, vdW, term
LAYOUT = {"short": (7, 6), "long": (9, 7)}


def open_text(path):
    if path == "-":
        return sys.stdin
    with open(path, "rb") as f:
        gz = f.read(2) == b"\x1f\x8b"
    return gzip.open(path, "rt") if gz else open(path)


def vdw_key(ref_line, fmt):
    nref, idx = LAYOUT[fmt]
    pairs = REF_PAIR.findall(ref_line)
    if len(pairs) < nref:
        return None
    return pairs[-nref:][idx][1]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("infile", help="VALD output file (.gz or plain, '-' for stdin)")
    ap.add_argument("-k", "--key", default="BPM",
                    help="reference key to keep, as printed in the file (default: BPM)")
    ap.add_argument("-o", "--output", help="output file (default: stdout)")
    args = ap.parse_args()

    with open_text(args.infile) as f:
        lines = f.readlines()

    head = "".join(lines[:5])
    fmt = "long" if "J lo" in head else "short"
    if fmt == "short" and "Reference" not in head:
        sys.exit("No references in this file: request it with references "
                 "included, otherwise there is nothing to filter on.")
    reclen = 4 if fmt == "long" else 1

    out, stats = [], collections.Counter()
    n_in = n_kept = n_bad = 0
    i = 0
    while i < len(lines):
        if not RECORD_START.match(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        rec = lines[i:i + reclen]
        i += reclen
        n_in += 1
        key = vdw_key(rec[-1], fmt)
        if key is None:
            n_bad += 1
            continue
        stats[key] += 1
        if key == args.key:
            out.extend(rec)
            n_kept += 1

    m = STELLAR_HEADER.match(out[0]) if out else None
    if m:
        out[0] = f"{m.group(1)}{n_kept}{m.group(3)}"

    dst = open(args.output, "w") if args.output else sys.stdout
    dst.writelines(out)
    if args.output:
        dst.close()

    print(f"{fmt} format: {n_kept} of {n_in} lines kept "
          f"(vdW reference '{args.key}')", file=sys.stderr)
    if n_bad:
        print(f"WARNING: {n_bad} records with unparsable references dropped",
              file=sys.stderr)
    print("vdW references in input:", file=sys.stderr)
    for k, n in stats.most_common():
        print(f"  {k:10s} {n:7d}", file=sys.stderr)


if __name__ == "__main__":
    main()
