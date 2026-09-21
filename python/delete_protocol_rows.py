#!/usr/bin/env python3
"""
delete_protocol_rows.py — Delete protocol readout rows from a CDD protocol,
selected by their stored Species value.

Use it to clean rows that were imported into the wrong protocol (e.g. 10%FBS
records sitting in the mouse PPB protocol before the FBS protocol existed).

SAFETY (this destroys production data and CDD has no undo):
  * DRY-RUN by default — nothing is deleted without --execute.
  * A backup CSV of every selected row is written BEFORE the first delete, and
    --execute aborts if the backup cannot be written.
  * Selection is by the Species READOUT VALUE, never by compound identity — a
    compound can hold rows for several species, and only the matching rows go.
  * Mouse/unmatched rows are never touched.

HARD RULE (same as the rest of the repo): stdout stays metadata-only — counts,
run ids, row ids. Compound identifiers and readout values go to the backup file
on disk, never to the terminal.

Examples:
  python python/delete_protocol_rows.py --protocol 125963 --species 10%FBS
  python python/delete_protocol_rows.py --protocol 125963 --species 10%FBS --execute
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_cdd_commit as ccc
import convert_upload as cu
from get_library import API_BASE, load_token, make_session
from get_protocol_data import protocol_rows, run_dates


def select_rows(rows, species_rid, wanted, aliases, exclude_runs=()):
    """Rows whose canonical Species value equals `wanted` (case/alias-insensitive).

    `exclude_runs` protects whole runs from selection — use it to drop the OLD
    rows while keeping a freshly uploaded run.
    """
    skip = {int(x) for x in exclude_runs or ()}
    out = []
    for r in rows:
        if r.get("run") in skip:
            continue
        cell = (r.get("readouts") or {}).get(str(species_rid)) or {}
        raw = str(cell.get("value", "")).strip()
        if aliases.get(cu.species_key(raw)) == wanted:
            out.append(r)
    return out


def write_backup(path, block, rows, mbid):
    """Dump the selected rows (identifiers + values) to a local CSV before deleting."""
    readouts = block["readouts"]
    header = ["readout_row_id", "run", "Molecule-Batch ID", *readouts]
    out = []
    for r in rows:
        ro = r.get("readouts") or {}
        out.append([r.get("id"), r.get("run"), mbid.get(r.get("batch"), "")]
                   + [ccc.cell_value(ro.get(str(rid))) for rid in readouts.values()])
    cu.write_csv(path, header, out)


def delete_row(session, vault, row_id):
    """DELETE one readout row. Returns (ok, message)."""
    r = session.delete(f"{API_BASE}/vaults/{vault}/readout_rows/{row_id}")
    return r.ok, (r.json().get("message") if r.ok else f"HTTP {r.status_code}: {r.text[:120]}")


def main():
    ap = argparse.ArgumentParser(description="Delete protocol rows by Species value (dry-run by default).")
    ap.add_argument("--protocol", type=int, required=True)
    ap.add_argument("--species", required=True, help="canonical species, e.g. '10%%FBS'")
    ap.add_argument("--vault", type=int, default=None)
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--backup-dir", default="data/uploads")
    ap.add_argument("--exclude-run", default="", help="comma-separated run ids to KEEP (never selected)")
    ap.add_argument("--execute", action="store_true", help="actually delete (default: dry-run)")
    args = ap.parse_args()

    config = yaml.safe_load(open(args.config))
    vault = args.vault or config["vault"]
    block = (config.get("protocols") or {}).get(args.protocol)
    if not block or not block.get("readouts"):
        sys.exit(f"ERR: protocol {args.protocol} has no readouts in {args.config}")
    species_rid = block["readouts"].get("Species") or block["readouts"].get("species")
    if not species_rid:
        sys.exit(f"ERR: protocol {args.protocol} has no Species readout")

    session = make_session(load_token("~/.cdd_token"))
    rows = protocol_rows(session, vault, args.protocol)
    keep_runs = [x.strip() for x in args.exclude_run.split(",") if x.strip()]
    picked = select_rows(rows, species_rid, args.species, cu.species_alias_map(config), keep_runs)
    print(f"protocol {args.protocol} ({block.get('name')}): {len(rows)} rows total")
    print(f"selected Species == {args.species!r}: {len(picked)} rows"
          + (f"  (protecting run(s) {', '.join(keep_runs)})" if keep_runs else ""))
    if not picked:
        return
    dates = run_dates(session, vault, args.protocol)
    for run, n in sorted(Counter(r.get("run") for r in picked).items()):
        total = sum(1 for r in rows if r.get("run") == run)
        print(f"   run={run} {dates.get(run,'?')}  deleting {n} of {total} rows in that run"
              f"{'  (whole run becomes empty)' if n == total else ''}")

    if not args.execute:
        print("\nDRY RUN — nothing deleted. Re-run with --execute to apply.")
        return

    mbid = ccc.fetch_batch_identifiers(session, vault, {r["batch"] for r in picked if r.get("batch")})
    backup = Path(args.backup_dir) / f"deleted_rows_{args.protocol}_{args.species.replace('%','pct')}.csv"
    backup.parent.mkdir(parents=True, exist_ok=True)
    write_backup(backup, block, picked, mbid)
    print(f"\nbackup written: {backup}  ({len(picked)} rows)")

    ok = 0
    for r in picked:
        good, msg = delete_row(session, vault, r["id"])
        ok += good
        if not good:
            print(f"   FAILED row_id={r['id']}: {msg}")
    print(f"deleted {ok}/{len(picked)} rows")
    if ok != len(picked):
        sys.exit(1)


if __name__ == "__main__":
    main()
