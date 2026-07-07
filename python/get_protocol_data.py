#!/usr/bin/env python3
"""
get_protocol_data.py — Extract protocol (assay) readout data from CDD Vault,
joined with molecule SMILES, into one wide DataFrame / CSV.

The read counterpart to import_to_protocol.py. Protocols are selected by the
short `alias` set in config/config.yaml (mdck, logd, hlm, mlm, solubility, ppb,
caco2, rlm). With no --experiments, every aliased protocol is pulled.

Output (per the agreed design):
  * ONE merged wide table — one row per compound (molecule), joined across all
    requested assays on molecule id.
  * LATEST run per compound per assay (most recent run_date; tie-break run id).
  * readout columns are prefixed by alias, e.g. `logd_LogD7.4`, `ppb_%Unbound`,
    plus `<alias>_run_date`. Molecule columns (name, smiles, ...) appear once.

HARD RULE (same as the rest of this repo): chemistry values never hit stdout —
only counts, ids, status, column names. SMILES/values land only in the CSV /
returned DataFrame.

CDD endpoints used (all GET, paginated {count, objects, offset, page_size}):
  /vaults/{v}/protocols/{id}            -> readout_definitions (id -> name)
  /vaults/{v}/protocols/{id}/data       -> rows {id, molecule, batch, run,
                                            readouts:{<rid>:{value, outlier?}}}
  /vaults/{v}/runs?protocols={id}       -> runs {id, run_date, ...}
  /vaults/{v}/molecules?molecules=a,b   -> molecule {id, name, smiles, ...}
                                            (the ?molecules= id filter IS honored)

CLI:
  python get_protocol_data.py --experiments solubility,logd --output ./assays.csv
  python get_protocol_data.py                      # all aliased protocols
  python get_protocol_data.py --list               # show alias -> protocol map
"""

import argparse
import sys
from pathlib import Path

# Reuse auth + pagination primitives from the exporter.
from get_library import API_BASE, get_with_retry, load_token, make_session

DEFAULT_MOL_COLUMNS = ("name", "smiles")


# ---------- config ----------

def load_config(config_path):
    import yaml
    p = Path(config_path)
    if not p.exists():
        sys.exit(f"ERR: config not found: {p}")
    return yaml.safe_load(p.read_text()) or {}


def alias_map(config):
    """Return {alias: (pid, name)} for every protocol block that has an alias."""
    out = {}
    for pid, block in (config.get("protocols") or {}).items():
        alias = (block or {}).get("alias")
        if alias:
            out[alias] = (int(pid), block.get("name"))
    return out


def resolve_experiments(config, experiments):
    """Map requested aliases to [(alias, pid, name)]; None/empty -> all aliased."""
    amap = alias_map(config)
    if not amap:
        sys.exit("ERR: no protocols with an `alias:` in config")
    if not experiments:
        chosen = sorted(amap)
    else:
        chosen, unknown = [], []
        for a in experiments:
            (chosen if a in amap else unknown).append(a)
        if unknown:
            sys.exit(f"ERR: unknown experiment(s) {unknown}; "
                     f"known aliases: {sorted(amap)}")
    return [(a, amap[a][0], amap[a][1]) for a in chosen]


# ---------- CDD paginated GET ----------

def _paginate(session, url, params=None, page_size=1000):
    """Yield objects from a paginated CDD endpoint."""
    params = dict(params or {})
    offset = 0
    while True:
        params.update({"offset": offset, "page_size": page_size})
        data = get_with_retry(session, url, params=params).json()
        objs = data.get("objects", []) if isinstance(data, dict) else []
        if not objs:
            break
        for o in objs:
            yield o
        offset += len(objs)
        total = data.get("count") if isinstance(data, dict) else None
        if (total is not None and offset >= total) or len(objs) < page_size:
            break


def readout_names(session, vault, pid):
    """{readout_definition_id: name} for a protocol."""
    p = get_with_retry(session, f"{API_BASE}/vaults/{vault}/protocols/{pid}").json()
    rds = p.get("readout_definitions") or []
    return {rd["id"]: rd["name"] for rd in rds if "id" in rd and "name" in rd}


def run_dates(session, vault, pid, page_size=1000):
    """{run_id: run_date} for a protocol (run_date is an ISO string, sorts fine)."""
    out = {}
    for r in _paginate(session, f"{API_BASE}/vaults/{vault}/runs",
                       params={"protocols": pid}, page_size=page_size):
        if "id" in r:
            out[r["id"]] = r.get("run_date") or ""
    return out


def protocol_rows(session, vault, pid, page_size=1000):
    """All data rows for a protocol."""
    return list(_paginate(session, f"{API_BASE}/vaults/{vault}/protocols/{pid}/data",
                          page_size=page_size))


def fetch_molecules(session, vault, mol_ids, columns, page_size=200):
    """{mol_id: {col: value}} for the given ids, via the ?molecules= filter."""
    out = {}
    ids = sorted({int(m) for m in mol_ids})
    for i in range(0, len(ids), page_size):
        chunk = ids[i:i + page_size]
        for o in _paginate(
            session, f"{API_BASE}/vaults/{vault}/molecules",
            params={"molecules": ",".join(map(str, chunk))}, page_size=page_size,
        ):
            mid = o.get("id")
            if mid is not None:
                out[mid] = {c: o.get(c) for c in columns}
    return out


# ---------- assembly ----------

def _latest_per_molecule(rows, rdates):
    """Keep the most recent row per molecule (max run_date, tie-break run id)."""
    best = {}
    for r in rows:
        mid = r.get("molecule")
        if mid is None:
            continue
        key = (rdates.get(r.get("run"), ""), r.get("run") or 0)
        if mid not in best or key > best[mid][0]:
            best[mid] = (key, r)
    return {mid: r for mid, (_, r) in best.items()}


def _assay_frame(alias, rows, rdates, rnames):
    """Build a per-molecule dict-of-columns for one assay (latest row each)."""
    import pandas as pd
    latest = _latest_per_molecule(rows, rdates)
    records = []
    for mid, r in latest.items():
        rec = {"molecule": mid,
               f"{alias}_run_date": rdates.get(r.get("run"), "")}
        readouts = r.get("readouts") or {}
        for rid, name in rnames.items():
            cell = readouts.get(rid) or readouts.get(str(rid))
            rec[f"{alias}_{name}"] = cell.get("value") if isinstance(cell, dict) else None
        records.append(rec)
    return pd.DataFrame.from_records(records)


def get_data(
    experiments=None,
    vault=7108,
    token=None,
    token_file="~/.cdd_token",
    config_path="config/config.yaml",
    mol_columns=DEFAULT_MOL_COLUMNS,
    page_size=1000,
    verbose=True,
):
    """Extract latest assay data for the given experiments as one wide DataFrame.

    Args:
        experiments: aliases (list[str] or 'logd,ppb'); None/empty -> all aliased.
        vault: CDD Vault id.
        token / token_file: auth (token wins; else read token_file).
        config_path: config.yaml with the per-protocol `alias:` entries.
        mol_columns: molecule fields to include (default name + smiles).
        page_size: CDD pagination size.
        verbose: print progress (metadata only).

    Returns:
        pandas.DataFrame — one row per compound, `molecule` + mol_columns, then
        `<alias>_run_date` and `<alias>_<readout>` for each requested assay.
    """
    import pandas as pd

    if isinstance(experiments, str):
        experiments = [e.strip() for e in experiments.split(",") if e.strip()]
    config = load_config(config_path)
    chosen = resolve_experiments(config, experiments)
    if token is None:
        token = load_token(token_file)
    session = make_session(token)
    mol_columns = list(mol_columns)

    if verbose:
        print(f"experiments={[a for a, _, _ in chosen]}")

    frames, all_mol_ids = [], set()
    for alias, pid, name in chosen:
        rnames = readout_names(session, vault, pid)
        rdates = run_dates(session, vault, pid, page_size=page_size)
        rows = protocol_rows(session, vault, pid, page_size=page_size)
        frame = _assay_frame(alias, rows, rdates, rnames)
        if verbose:
            print(f"  {alias} (pid={pid}): rows={len(rows)} compounds={len(frame)} "
                  f"readouts={len(rnames)}")
        all_mol_ids.update(frame["molecule"].tolist())
        frames.append(frame)

    if not all_mol_ids:
        if verbose:
            print("no data rows found for the requested experiments")
        return pd.DataFrame(columns=["molecule", *mol_columns])

    mols = fetch_molecules(session, vault, all_mol_ids, mol_columns)
    base = pd.DataFrame(
        [{"molecule": mid, **{c: mols.get(mid, {}).get(c) for c in mol_columns}}
         for mid in sorted(all_mol_ids)]
    )

    out = base
    for frame in frames:
        out = out.merge(frame, on="molecule", how="left")
    if verbose:
        print(f"compounds={len(out)} columns={len(out.columns)}")
    return out


# ---------- CLI ----------

def main():
    ap = argparse.ArgumentParser(
        description="Extract CDD protocol assay data (latest per compound) joined "
                    "with SMILES into one wide CSV. Chemistry stays local; stdout "
                    "is metadata only.",
    )
    ap.add_argument("--vault", type=int, default=7108)
    ap.add_argument("--experiments",
                    help="Comma-separated aliases (e.g. logd,ppb). Omit for all.")
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--token-file", default="~/.cdd_token")
    ap.add_argument("--mol-columns", default=",".join(DEFAULT_MOL_COLUMNS),
                    help="Molecule fields to include (default: name,smiles).")
    ap.add_argument("--output", default="./protocol_data.csv")
    ap.add_argument("--page-size", type=int, default=1000)
    ap.add_argument("--list", action="store_true",
                    help="Print the alias -> protocol map from config, then exit.")
    args = ap.parse_args()

    if args.list:
        for alias, (pid, name) in sorted(alias_map(load_config(args.config)).items()):
            print(f"  {alias:12s} -> pid={pid}  {name}")
        return

    experiments = ([e.strip() for e in args.experiments.split(",") if e.strip()]
                   if args.experiments else None)
    df = get_data(
        experiments=experiments, vault=args.vault, token_file=args.token_file,
        config_path=args.config,
        mol_columns=[c.strip() for c in args.mol_columns.split(",") if c.strip()],
        page_size=args.page_size, verbose=True,
    )
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"output_file={out}  rows={len(df)}  columns={len(df.columns)}")


if __name__ == "__main__":
    main()
