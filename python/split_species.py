#!/usr/bin/env python3
"""
split_species.py — Split an "_upl.csv" file into one CSV per species.

A faithful Python port of the species-split in the "From XLS to CDD upload
(ADME)" HTML tool, for files that are already in `_upl.csv` form (e.g. an MMS
export containing both Human and Mouse rows) and need to be split after the
fact — one file per protocol/species.

Matches the HTML tool exactly:
  * species column = the header cell whose trimmed, lower-cased text is "species"
  * species order  = distinct non-empty values in first-seen order (blank rows
                     skipped)
  * per species    = header row + every non-blank row whose species cell equals
                     that species (trimmed). Rows with no species (e.g. the junk
                     rows the prep tool stamps with Run Lab/Provider Name) match
                     nothing and are dropped.
  * output name    = <base>_<species>_upl.csv, where <base> is the input name
                     with a trailing "_upl.csv" / ".csv" removed and <species>
                     sanitised (remove \\ / : * ? " < > | , spaces -> "_")
  * a UTF-8 BOM is written (as the HTML tool does)

Values are copied verbatim and never printed; stdout shows only species names
and row counts.

CLI:
  python split_species.py data/uploads/..._MMS_..._upl.csv
  python split_species.py <file> --output-dir data/uploads --dry-run
"""

import argparse
import csv
import sys
from pathlib import Path

# stamped onto blank rows by the prep tool — same list the HTML uses.
SPECIES_HEADER = "species"


def _blank_row(row):
    """True if every cell is empty / None / whitespace (HTML tool's blankRow)."""
    return all(c is None or str(c).strip() == "" for c in row) if row else True


def _safe(name):
    """Sanitise a species value for a filename (HTML tool's regex)."""
    for ch in '\\/:*?"<>|':
        name = name.replace(ch, "")
    return "_".join(name.split())


def _base(file_name):
    """Input name with a trailing '_upl.csv' or '.csv' removed."""
    if file_name.endswith("_upl.csv"):
        return file_name[: -len("_upl.csv")]
    if file_name.endswith(".csv"):
        return file_name[: -len(".csv")]
    return file_name


def split_species(file_path, output_dir=None, dry_run=False):
    """Split file_path into one <base>_<species>_upl.csv per species.

    Returns a list of (species, out_path, data_rows). Writes files unless dry_run.
    """
    src = Path(file_path)
    rows = list(csv.reader(open(src, newline="", encoding="utf-8-sig")))
    if not rows:
        sys.exit(f"ERR: empty file: {src}")
    header = rows[0]
    hdr_norm = [str(h).strip().lower() if h is not None else "" for h in header]
    if SPECIES_HEADER not in hdr_norm:
        sys.exit(f"ERR: no 'species' column in {src.name}; "
                 f"header={[str(h) for h in header]}")
    sc = hdr_norm.index(SPECIES_HEADER)

    def species_of(row):
        return str(row[sc]).strip() if sc < len(row) and row[sc] is not None else ""

    data = [r for r in rows[1:] if not _blank_row(r)]
    order, seen = [], set()
    for r in data:
        sp = species_of(r)
        if sp and sp not in seen:
            seen.add(sp)
            order.append(sp)
    if not order:
        sys.exit(f"ERR: 'species' column present but no non-empty values in {src.name}")

    out_dir = Path(output_dir) if output_dir else src.parent
    base = _base(src.name)
    results = []
    for sp in order:
        subset = [header] + [r for r in data if species_of(r) == sp]
        out_path = out_dir / f"{base}_{_safe(sp)}_upl.csv"
        if not dry_run:
            with open(out_path, "w", newline="", encoding="utf-8-sig") as fh:
                csv.writer(fh).writerows(subset)
        results.append((sp, out_path, len(subset) - 1))
    return results


def main():
    ap = argparse.ArgumentParser(
        description="Split an _upl.csv into one CSV per species "
                    "(port of the ADME HTML tool's species split).",
    )
    ap.add_argument("file", help="Input _upl.csv with a 'species' column.")
    ap.add_argument("--output-dir", default=None,
                    help="Where to write the split files (default: input's dir).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report the split without writing files.")
    args = ap.parse_args()

    results = split_species(args.file, args.output_dir, args.dry_run)
    tag = "would write" if args.dry_run else "wrote"
    print(f"species found: {[sp for sp, _, _ in results]}")
    for sp, out_path, n in results:
        print(f"  {tag}: {out_path.name}  ({n} rows)")


if __name__ == "__main__":
    main()
