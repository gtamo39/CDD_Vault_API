#!/usr/bin/env python3
"""
check_cdd_commit.py — Verify that data committed to CDD matches the source WuXi
workbooks: for every compound in each file, confirm a committed row exists in the
right protocol and its numeric readout values match what CDD stored.

The read-side audit counterpart to import_to_protocol.py + the webapp submit. It
reuses convert_upload (read Upload tab / species split / identifier reconcile),
detect_protocol (pick the PID), get_protocol_data (CDD read primitives), and the
per-protocol `readouts` map in config/config.yaml (file column -> readout id).

Matching:
  * key         file `Molecule-Batch ID` == CDD batch `molecule_batch_identifier`
  * a compound  PASSES existence if a committed row exists for that batch id
  * a readout   is compared only when the FILE value is numeric ('12.3', '< 5',
                '>100'); a numeric file value with nothing in CDD is a mismatch,
                and a value CDD stores non-numerically (e.g. a date) is skipped
                (so metadata columns never false-fail). Numbers match within
                `--tol` (abs) or `--rel` (relative); qualifiers must be equal.

HARD RULE: chemistry never hits stdout. stdout is metadata only — per-unit
counts (checked/matched/missing/mismatch) + the verdict. Per-compound detail
(batch id + file/CDD values) goes to a local `<file>.verify.txt`, never stdout.

CLI:
  python check_cdd_commit.py data/uploads/20260716/*.xlsx
  python check_cdd_commit.py --protocol 85979 some_logd.csv
  python check_cdd_commit.py --tol 0.05 --rel 0.02 file.xlsx
"""

import argparse
import csv
import re
import sys
from pathlib import Path

import convert_upload as cu
import detect_protocol as dp
from get_library import make_session, load_token
from get_protocol_data import (
    API_BASE, _paginate, get_with_retry, load_config,
    protocol_rows, readout_names, run_dates,
)

_NUM = re.compile(r"^\s*([<>]=?)?\s*(-?\d+(?:\.\d+)?)\s*$")


# ---------- value parsing / comparison ----------

def parse_num(v):
    """('<'|'>'|'', float) for a numeric-or-qualified scalar, else None."""
    if v is None:
        return None
    m = _NUM.match(str(v))
    return (m.group(1) or "", float(m.group(2))) if m else None


def compare_value(file_val, cdd_val, tol, rel):
    """Verdict for one readout: None = skip (not a numeric result), True/False.

    Skips when the file value is not numeric (text/date). A numeric file value
    with an empty CDD cell is a mismatch (False); a CDD value stored
    non-numerically (e.g. a date string) is skipped so it can't false-fail.
    """
    fp = parse_num(file_val)
    if fp is None:
        return None
    if cdd_val is None or str(cdd_val).strip() == "":
        return False
    cp = parse_num(cdd_val)
    if cp is None:
        return None
    if fp[0] != cp[0]:
        return False
    return abs(fp[1] - cp[1]) <= max(tol, abs(fp[1]) * rel)


_DATE_COLS = ("study date", "date", "run date")
# free-text columns hidden from the CDD-vs-file comparison view
_IGNORE_COMPARE_COLS = ("provider comment", "provider name", "comment", "serac comment")


def _digits(v):
    """Digits only, so a date matches regardless of format (2026-03-30 == 20260330)."""
    return re.sub(r"\D", "", str(v)) if v is not None else ""


def compare_field(col_norm_lower, file_val, cdd_val, tol, rel):
    """Verdict for one readout, respecting the field's kind:
      * study number  -> EXACT string (the run identity; a different study is NOT
                         a duplicate). Skip (None) only when the file value is blank.
      * a date column -> EXACT (digits-only) — never a numeric tolerance.
      * otherwise      -> numeric compare_value (measurements, with tolerance).
    """
    if col_norm_lower == "study number":
        f = str(file_val).strip() if file_val is not None else ""
        return None if not f else f == (str(cdd_val).strip() if cdd_val is not None else "")
    if col_norm_lower in _DATE_COLS:
        f = _digits(file_val)
        return None if not f else f == _digits(cdd_val)
    return compare_value(file_val, cdd_val, tol, rel)


# ---------- CDD read ----------

def fetch_batch_identifiers(session, vault, batch_ids, page_size=200):
    """{numeric batch id -> molecule_batch_identifier ('SRB-XXXXXXX-NNN')}."""
    out = {}
    ids = sorted({int(b) for b in batch_ids})
    for i in range(0, len(ids), page_size):
        chunk = ids[i:i + page_size]
        for o in _paginate(session, f"{API_BASE}/vaults/{vault}/batches",
                           params={"batches": ",".join(map(str, chunk))}, page_size=page_size):
            bid, mbid = o.get("id"), o.get("molecule_batch_identifier")
            if bid is not None and mbid:
                out[bid] = mbid
    return out


def cell_value(c):
    """The effective scalar of a CDD readout cell, re-attaching a `<`/`>`
    `modifier` to its numeric `value` (CDD stores the qualifier separately)."""
    if not isinstance(c, dict):
        return c
    v, mod = c.get("value"), c.get("modifier")
    if mod and v is not None and str(v).strip() != "":
        return f"{mod} {v}"
    return v


def cdd_lookup(session, vault, pid, page_size=1000):
    """{molecule_batch_identifier -> [ {str(rid): value}, ... ]} — ALL runs per
    batch (a compound can have several); a file row matches if ANY run matches."""
    rows = protocol_rows(session, vault, pid, page_size=page_size)
    mbid_of = fetch_batch_identifiers(session, vault,
                                      {r["batch"] for r in rows if r.get("batch") is not None})
    out = {}
    for r in rows:
        mbid = mbid_of.get(r.get("batch"))
        if not mbid:
            continue
        ro = r.get("readouts") or {}
        out.setdefault(mbid, []).append({str(rid): cell_value(c) for rid, c in ro.items()})
    return out


def build_comparison(session, vault, pid, page_size=1000):
    """For --compare mode. Returns (runs_by, name_by, rnames):
      runs_by  {mbid -> [ {str(rid): value}, ... ]}   ALL runs (same shape as cdd_lookup)
      name_by  {mbid -> CDD molecule name (SRB-XXXXXXX)}
      rnames   {rid -> readout name}   (for the 'experiment' label, e.g. LogD7.4)
    """
    rows = protocol_rows(session, vault, pid, page_size=page_size)
    bmap = {}  # numeric batch id -> (molecule_batch_identifier, cdd molecule name)
    ids = sorted({r["batch"] for r in rows if r.get("batch") is not None})
    for i in range(0, len(ids), 200):
        for o in _paginate(session, f"{API_BASE}/vaults/{vault}/batches",
                           params={"batches": ",".join(map(str, ids[i:i + 200]))}, page_size=200):
            if o.get("id") is not None:
                mol = o.get("molecule")
                name = mol.get("name") if isinstance(mol, dict) else None
                bmap[o["id"]] = (o.get("molecule_batch_identifier"), name)
    runs_by, name_by = {}, {}
    for r in rows:
        mbid, name = bmap.get(r.get("batch"), (None, None))
        if not mbid:
            continue
        ro = r.get("readouts") or {}
        runs_by.setdefault(mbid, []).append({str(rid): cell_value(c) for rid, c in ro.items()})
        name_by[mbid] = name
    return runs_by, name_by, readout_names(session, vault, pid)


def comparison_rows(unit, runs_by, name_by, rnames, block, tol, rel):
    """Per readout, a row [batch_id, cdd_name, experiment, cdd_value, wuxi_value, match]
    for every file compound that EXISTS in CDD (best-matching run). For side-by-side
    comparison of the overlap — carries values, so callers write it to a local file."""
    header, rows = unit["header"], unit["rows"]
    hdr_norm = [cu._norm(h) for h in header]
    idents = list((block.get("identifiers") or {}).values())
    id_idx = next((hdr_norm.index(cu._norm(n)) for n in idents if cu._norm(n) in hdr_norm), None)
    readouts = block.get("readouts") or {}
    col_idx = {col: (rid, hdr_norm.index(cu._norm(col)))
               for col, rid in readouts.items() if cu._norm(col) in hdr_norm}
    out = []
    if id_idx is None:
        return out
    for r in rows:
        mbid = str(r[id_idx]).strip() if id_idx < len(r) and r[id_idx] is not None else ""
        runs = runs_by.get(mbid)
        if not mbid or not runs:
            continue  # only compounds already in CDD
        best, best_n = runs[0], None
        for rv in runs:
            n = sum(1 for col, (rid, j) in col_idx.items()
                    if j < len(r) and compare_field(cu._norm(col).lower(), r[j], rv.get(str(rid)), tol, rel) is False)
            if best_n is None or n < best_n:
                best, best_n = rv, n
        for col, (rid, j) in col_idx.items():
            if cu._norm(col).lower() in _IGNORE_COMPARE_COLS:
                continue  # free-text columns aren't useful in the overlap view
            fv = r[j] if j < len(r) else ""
            cv = best.get(str(rid))
            verdict = compare_field(cu._norm(col).lower(), fv, cv, tol, rel)
            out.append([mbid, name_by.get(mbid) or "", rnames.get(rid) or col,
                        "" if cv is None else cv, "" if fv is None else fv,
                        "ok" if verdict is True else ("DIFF" if verdict is False else "")])
    return out


# ---------- file -> submission units ----------

def read_file(path):
    """(header, rows) for a workbook Upload tab (.xlsx) or a processed .csv."""
    p = Path(path)
    if p.suffix.lower() in (".csv", ".txt"):
        with open(p, encoding="utf-8-sig", newline="") as fh:
            grid = list(csv.reader(fh))
        return (grid[0] if grid else []), grid[1:]
    return cu.read_upload_sheet(p)


def _unit(parent, species, pid, header, rows, config):
    """Reconcile identifier + drop blank-id rows for one (pid, rows) unit."""
    block = (config.get("protocols") or {}).get(pid) or {}
    h2, _ = cu.reconcile_identifier(header, block)
    r2, _ = cu.drop_blank_identifier_rows(h2, rows, block)
    return {"parent": parent, "species": species, "pid": pid, "header": h2, "rows": r2}


def load_units(path, config, force_pid=None):
    """Build submission units from a file — mirrors the webapp: detect the PID,
    split microsomal by species, reconcile identifier, drop blank-id rows."""
    name = Path(path).name
    header, rows = read_file(path)
    if force_pid is not None:
        return [_unit(name, None, force_pid, header, rows, config)]
    det = dp.detect(header, config)
    if det["status"] == "species_group":
        units = []
        for sp, srows in (cu.split_species(header, rows) or []):
            pid = (det["species_pids"].get(sp) or det["species_pids"].get(sp.title())
                   or det["species_pids"].get(sp.capitalize()))
            units.append(_unit(name, sp, pid, header, srows, config) if pid
                         else {"parent": name, "species": sp, "pid": None})
        return units
    if det["status"] == "single":
        return [_unit(name, None, det["pid"], header, rows, config)]
    return [{"parent": name, "species": None, "pid": None}]


# ---------- checking ----------

def check_unit(unit, lookup, block, tol, rel):
    """Compare one unit's rows to CDD. Returns counts + detail lists (detail holds
    values for the LOCAL report only — callers must not print it to stdout)."""
    header, rows = unit["header"], unit["rows"]
    hdr_norm = [cu._norm(h) for h in header]
    idents = list((block.get("identifiers") or {}).values())
    id_idx = next((hdr_norm.index(cu._norm(n)) for n in idents if cu._norm(n) in hdr_norm), None)
    readouts = block.get("readouts") or {}
    col_idx = {col: hdr_norm.index(cu._norm(col)) for col in readouts if cu._norm(col) in hdr_norm}
    res = {"checked": 0, "matched": 0, "missing": [], "mismatch": []}
    if id_idx is None:
        return res
    for r in rows:
        mbid = str(r[id_idx]).strip() if id_idx < len(r) and r[id_idx] is not None else ""
        if not mbid:
            continue
        res["checked"] += 1
        runs = lookup.get(mbid)
        if not runs:
            res["missing"].append(mbid)
            continue
        # A compound matches if ANY of its runs matches every compared readout.
        best_fail = None
        for run_vals in runs:
            fails = []
            for col, rid in readouts.items():
                j = col_idx.get(col)
                if j is None or j >= len(r):
                    continue
                if compare_field(cu._norm(col).lower(), r[j], run_vals.get(str(rid)), tol, rel) is False:
                    fails.append({"batch_id": mbid, "column": col,
                                  "file": r[j], "cdd": run_vals.get(str(rid))})
            if not fails:
                best_fail = None
                break
            if best_fail is None or len(fails) < len(best_fail):
                best_fail = fails
        if best_fail:
            res["mismatch"].extend(best_fail)
        else:
            res["matched"] += 1
    return res


def verify(paths, vault=7108, token_file="~/.cdd_token", config_path="config/config.yaml",
           force_pid=None, tol=0.01, rel=0.01, write_report=True, compare=False, verbose=True):
    """Verify each file against CDD. Returns (success, per_file_results).

    compare=True also writes a local `<file>.compare.csv` — for every compound
    already in CDD, CDD's values (compound name, study number/date, each
    experiment) side by side with the WuXi file row.
    """
    config = load_config(config_path)
    session = make_session(load_token(token_file))
    lookups, detail = {}, {}
    overall, out = True, []
    for path in paths:
        units, file_ok, report, comp = [], True, [], []
        for u in load_units(path, config, force_pid=force_pid):
            pid = u.get("pid")
            if not pid:
                file_ok = False
                units.append({"species": u.get("species"), "pid": None, "verdict": "NO-PROTOCOL"})
                if verbose:
                    print(f"  [{u.get('species') or '-'}] no protocol detected -> FAIL")
                continue
            if pid not in lookups:
                if compare:  # build_comparison returns the same run shape cdd_lookup needs
                    runs_by, name_by, rnames = build_comparison(session, vault, pid)
                    lookups[pid], detail[pid] = runs_by, (name_by, rnames)
                else:
                    lookups[pid] = cdd_lookup(session, vault, pid)
            block = (config.get("protocols") or {}).get(pid) or {}
            res = check_unit(u, lookups[pid], block, tol, rel)
            ok = not res["missing"] and not res["mismatch"]
            file_ok = file_ok and ok
            units.append({"species": u.get("species"), "pid": pid, "verdict": "PASS" if ok else "FAIL",
                          "checked": res["checked"], "matched": res["matched"],
                          "missing": len(res["missing"]), "mismatch": len(res["mismatch"])})
            if verbose:
                print(f"  [{u.get('species') or '-'}] pid={pid} checked={res['checked']} "
                      f"matched={res['matched']} missing={len(res['missing'])} "
                      f"mismatch={len(res['mismatch'])} -> {'PASS' if ok else 'FAIL'}")
            if not ok:
                report.append((pid, u.get("species"), res))
            if compare:
                name_by, rnames = detail[pid]
                comp += comparison_rows(u, lookups[pid], name_by, rnames, block, tol, rel)
        overall = overall and file_ok
        if compare and comp:
            cp = Path(path).with_suffix(Path(path).suffix + ".compare.csv")
            with open(cp, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow(["batch_id", "cdd_compound_name", "experiment", "cdd_value",
                            "wuxi_value", "match"])
                w.writerows(comp)
            if verbose:
                print(f"  comparison -> {cp} ({len(comp)} field rows; local, has values)")
        if write_report and report:
            rp = Path(path).with_suffix(Path(path).suffix + ".verify.txt")
            with open(rp, "w", encoding="utf-8") as fh:
                for pid, sp, res in report:
                    fh.write(f"# pid={pid} species={sp}\n")
                    for m in res["missing"]:
                        fh.write(f"MISSING\t{m}\n")
                    for d in res["mismatch"]:
                        fh.write(f"MISMATCH\t{d['batch_id']}\t{d['column']}\t"
                                 f"file={d['file']!r}\tcdd={d['cdd']!r}\n")
            if verbose:
                print(f"  report -> {rp} (local; contains compound values)")
        out.append({"file": Path(path).name, "ok": file_ok, "units": units})
        if verbose:
            print(f"file={Path(path).name} -> {'PASS' if file_ok else 'FAIL'}\n")
    if verbose:
        n_ok = sum(1 for f in out if f["ok"])
        print(f"OVERALL: {'SUCCESS' if overall else 'FAILURE'} ({n_ok}/{len(out)} files passed)")
    return overall, out


def main():
    ap = argparse.ArgumentParser(
        description="Verify committed CDD data matches the source WuXi files "
                    "(existence + numeric readout values). Chemistry stays local; "
                    "stdout is metadata only.")
    ap.add_argument("files", nargs="+", help="Workbook(s) (.xlsx) or processed .csv.")
    ap.add_argument("--vault", type=int, default=7108)
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--token-file", default="~/.cdd_token")
    ap.add_argument("--protocol", type=int, help="Force this PID (skip detection).")
    ap.add_argument("--tol", type=float, default=0.01, help="Absolute value tolerance.")
    ap.add_argument("--rel", type=float, default=0.01, help="Relative value tolerance.")
    ap.add_argument("--no-report", action="store_true", help="Don't write .verify.txt files.")
    ap.add_argument("--compare", action="store_true",
                    help="Also write a local <file>.compare.csv: for each compound already "
                         "in CDD, CDD's compound name / study number / study date / each "
                         "experiment side by side with the WuXi file row.")
    args = ap.parse_args()

    ok, _ = verify(args.files, vault=args.vault, token_file=args.token_file,
                   config_path=args.config, force_pid=args.protocol,
                   tol=args.tol, rel=args.rel, write_report=not args.no_report,
                   compare=args.compare)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
