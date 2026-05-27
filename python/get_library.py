#!/usr/bin/env python3
"""
get_library.py — Export specific CDD Vault collections to a CSV file.

HARD RULE: chemistry data (SMILES, InChI, structures, descriptors, etc.) is
NEVER printed to stdout. Only counts, status codes, field names, IDs (numeric),
collection names, and progress are printed. The CSV file on disk is the only
place where chemistry values appear.

Row model: one row per (molecule, batch). Multi-batch molecules produce one
row per batch (molecule-level columns repeat across those rows). Molecules
with no batches still produce one row (batch-level columns empty).

Column resolution for --columns: each requested name is looked up in order:
  1. 'collection'                  -> resolved collection name (special)
  2. molecule top-level            -> obj[col]                  (e.g. name, smiles, id)
  3. batch top-level               -> batch[col]                (e.g. molecule_batch_identifier)
  4. molecule UDF                  -> obj['molecule_fields'][col]   (e.g. Subseries, Px_anywhere)
  5. batch UDF                     -> batch['batch_fields'][col]    (e.g. 'Lib ID', Plate ID)
Unknown columns produce empty cells and a single WARN per name on stderr.
Nested dict/list values are JSON-encoded into the cell.

CLI:
  python get_library.py
    --vault 7108
    --collections "AJ,AK"                # or --collection-ids 931034,931035
    --token-file ~/.cdd_token
    --output ./library_AJ_AK.csv
    --columns "collection,name,smiles,Subseries,Lib ID"   # default: collection,name,smiles
    [--limit N]   [--page-size 1000]   [--discover]   [--list-fields]
"""

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import requests
from tqdm import tqdm

API_BASE = "https://app.collaborativedrug.com/api/v1"


def load_token(token_file: str) -> str:
    p = Path(os.path.expanduser(token_file))
    if not p.exists():
        sys.exit(f"ERR: token file not found: {p}")
    tok = p.read_text().strip()
    if not tok:
        sys.exit(f"ERR: token file empty: {p}")
    return tok


def make_session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "X-CDD-Token": token,
        "Accept": "application/json",
    })
    return s


def get_with_retry(session, url, params=None, max_retries=6, base_sleep=1.0):
    """GET with exponential backoff on 429 / 5xx. Other errors abort with a
    metadata-only message (no body printed)."""
    last_status = None
    for attempt in range(max_retries):
        r = session.get(url, params=params, timeout=60)
        last_status = r.status_code
        if r.status_code == 200:
            return r
        if r.status_code in (429, 500, 502, 503, 504):
            sleep = base_sleep * (2 ** attempt)
            print(
                f"  retry status={r.status_code} sleep={sleep:.1f}s "
                f"attempt={attempt + 1}/{max_retries}",
                file=sys.stderr,
            )
            time.sleep(sleep)
            continue
        # Anything else — bail with status info only.
        sys.exit(
            f"ERR: status={r.status_code} url={url} "
            f"params_keys={list(params.keys()) if params else []} "
            f"body_len={len(r.content)}"
        )
    sys.exit(f"ERR: max retries exhausted last_status={last_status} url={url}")


# ---------- collection resolution ----------

def list_collections(session, vault):
    r = get_with_retry(session, f"{API_BASE}/vaults/{vault}/collections")
    data = r.json()
    if isinstance(data, dict) and "objects" in data:
        return data["objects"]
    return data


def resolve_collections(session, vault, names=None, ids=None):
    """Return list of (name, id) tuples. Always look up the canonical name
    from the API so the CSV 'collection' column is meaningful regardless of
    whether the user passed names or IDs."""
    cols = list_collections(session, vault)
    by_name = {}
    by_id = {}
    for c in cols:
        nm = c.get("name")
        cid = c.get("id")
        if nm is not None and cid is not None:
            by_name[nm] = cid
            by_id[str(cid)] = nm

    resolved = []
    if names:
        for nm in names:
            nm = nm.strip()
            if nm not in by_name:
                sys.exit(
                    f"ERR: collection name '{nm}' not found in vault {vault}. "
                    f"vault_collection_count={len(by_name)}"
                )
            resolved.append((nm, by_name[nm]))
    else:
        for i in ids:
            i = str(i).strip()
            if i not in by_id:
                sys.exit(
                    f"ERR: collection id {i} not found in vault {vault}. "
                    f"vault_collection_count={len(by_id)}"
                )
            resolved.append((by_id[i], int(i)))
    return resolved


# ---------- pagination ----------

def _in_collection(obj, collection_id):
    """True if the listing object belongs to the given collection.

    CDD's /vaults/{v}/collections/{cid}/molecules endpoint does NOT actually
    filter by {cid} — it returns the full vault listing regardless. The real
    membership signal is the molecule's own `collections` field:
    [{"id": <int>, "name": <str>}, ...]  or  None.
    """
    colls = obj.get("collections") or []
    return any(
        isinstance(c, dict) and c.get("id") == collection_id for c in colls
    )


def paginate_molecules(session, vault, collection_id, page_size=1000,
                       limit=None):
    offset = 0
    yielded = 0
    while True:
        params = {
            "offset": offset,
            "page_size": page_size,
        }
        r = get_with_retry(
            session,
            f"{API_BASE}/vaults/{vault}/collections/{collection_id}/molecules",
            params=params,
        )
        data = r.json()
        objs = data.get("objects", []) if isinstance(data, dict) else []
        total = data.get("count") if isinstance(data, dict) else None
        if not objs:
            break
        for o in objs:
            if not _in_collection(o, collection_id):
                continue
            yield o
            yielded += 1
            if limit is not None and yielded >= limit:
                return
        offset += len(objs)
        if total is not None and offset >= total:
            break
        if len(objs) < page_size:
            break


# ---------- discover ----------

def discover(session, vault, resolved, page_size=1000):
    print("=== --discover mode (no CSV written) ===")
    print(f"vault={vault}")
    for name, cid in resolved:
        print(f"\n--- collection name={name} id={cid} ---")
        r = get_with_retry(
            session,
            f"{API_BASE}/vaults/{vault}/collections/{cid}/molecules",
            params={"offset": 0, "page_size": page_size},
        )
        data = r.json()
        total = data.get("count") if isinstance(data, dict) else None
        objs = data.get("objects", []) if isinstance(data, dict) else []
        # CDD's endpoint ignores {cid} — apply the client-side filter and
        # report both raw and filtered counts so listing/filter divergence
        # is visible at a glance.
        objs_in_cid = [o for o in objs if _in_collection(o, cid)]
        print(
            f"total_count_reported={total}  first_page_objects={len(objs)}  "
            f"first_page_in_target_collection={len(objs_in_cid)}"
        )
        if total and objs and len(objs_in_cid) == len(objs):
            print(
                "  WARN: every listing row matched — likely the endpoint "
                "started filtering, or you're discovering the only collection "
                "in the vault."
            )
        if total and objs and len(objs_in_cid) == 0:
            print(
                "  WARN: zero filtered hits on the first page; either this "
                "collection is sparse (large vault, few members) or the "
                "molecule-level `collections` field stopped populating."
            )
        objs = objs_in_cid
        if not objs:
            continue
        keys = {}
        for o in objs:
            for k, v in o.items():
                if k not in keys:
                    keys[k] = {"present": 0, "non_null": 0}
                keys[k]["present"] += 1
                if v not in (None, "", [], {}):
                    keys[k]["non_null"] += 1
        print(f"listing_field_count={len(keys)}")
        print("listing_fields:")
        for k in sorted(keys.keys()):
            print(
                f"  {k}  present={keys[k]['present']}  "
                f"non_null={keys[k]['non_null']}"
            )
        has_smiles = "smiles" in keys and keys["smiles"]["non_null"] > 0
        print(f"listing_includes_smiles_nonnull={has_smiles}")

        # Probe the per-molecule endpoint with the first id to see the full
        # field set available there. We print only key names + count.
        first_id = objs[0].get("id")
        if first_id is not None:
            rm = get_with_retry(
                session,
                f"{API_BASE}/vaults/{vault}/molecules/{first_id}",
            )
            meta = rm.json()
            if isinstance(meta, dict):
                mkeys = sorted(meta.keys())
                print(f"meta_field_count={len(mkeys)}")
                print(f"meta_fields: {mkeys}")
            else:
                print(f"meta_response_type={type(meta).__name__}")


# ---------- column resolution ----------

def _to_cell(v):
    """Coerce a value to a CSV-safe string. Nested dicts/lists become JSON."""
    if v is None:
        return ""
    if isinstance(v, (str, int, float, bool)):
        return str(v)
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False)


class ColumnResolver:
    """Looks up requested column names in the layered CDD namespace.

    Resolution chain (first hit wins):
      1. 'collection'  -> the resolved collection name
      2. molecule[col]
      3. batch[col]                    (when batch is not None)
      4. molecule['molecule_fields'][col]
      5. batch['batch_fields'][col]    (when batch is not None)

    Tracks columns that matched at least once so unknowns can be reported
    after the export completes.
    """

    def __init__(self, columns):
        self.columns = columns
        self._matched = set()

    def resolve_row(self, mol_obj, batch_obj, collection_name):
        out = []
        for col in self.columns:
            v, found = self._lookup(col, mol_obj, batch_obj, collection_name)
            if found:
                self._matched.add(col)
            out.append(_to_cell(v) if found else "")
        return out

    def _lookup(self, col, mol_obj, batch_obj, collection_name):
        if col == "collection":
            return collection_name, True
        if col in mol_obj:
            return mol_obj[col], True
        if batch_obj is not None and col in batch_obj:
            return batch_obj[col], True
        mf = mol_obj.get("molecule_fields")
        if isinstance(mf, dict) and col in mf:
            return mf[col], True
        if batch_obj is not None:
            bf = batch_obj.get("batch_fields")
            if isinstance(bf, dict) and col in bf:
                return bf[col], True
        return None, False

    def warn_unmatched(self):
        unmatched = [c for c in self.columns if c not in self._matched]
        for c in unmatched:
            print(
                f"WARN: column {c!r} never matched any namespace "
                f"(top-level / molecule_fields / batch_fields) "
                f"— cells left empty. Try --list-fields to see what's available.",
                file=sys.stderr,
            )


# ---------- --list-fields ----------

def list_fields(session, vault, resolved, page_size=1000):
    """Print all column names available in each namespace, with coverage counts.

    Three namespaces, scoped to the molecules in each requested collection:
      - top-level listing fields  (counted per molecule)
      - molecule UDFs (`molecule_fields`)  (counted per molecule)
      - batch UDFs (`batch_fields`)        (counted per batch)
    """
    print("=== --list-fields (no CSV written) ===")
    for name, cid in resolved:
        print(f"\n--- collection name={name} id={cid} ---")
        top = Counter()
        mf = Counter()
        bf = Counter()
        n_mols = 0
        n_batches = 0
        for mol in paginate_molecules(session, vault, cid,
                                      page_size=page_size, limit=None):
            n_mols += 1
            for k, v in mol.items():
                if v not in (None, "", [], {}):
                    top[k] += 1
            mf_d = mol.get("molecule_fields")
            if isinstance(mf_d, dict):
                for k, v in mf_d.items():
                    if v not in (None, "", [], {}):
                        mf[k] += 1
            for b in (mol.get("batches") or []):
                n_batches += 1
                bf_d = b.get("batch_fields")
                if isinstance(bf_d, dict):
                    for k, v in bf_d.items():
                        if v not in (None, "", [], {}):
                            bf[k] += 1
        print(f"molecules={n_mols}  batches={n_batches}")

        def _dump(label, counter, denom):
            print(f"\n{label} (denominator={denom}):")
            for k, n in counter.most_common():
                print(f"  {k!r}: {n}/{denom}")

        _dump("Top-level listing fields", top, n_mols)
        _dump("Molecule UDFs (molecule_fields)", mf, n_mols)
        _dump("Batch UDFs (batch_fields)", bf, n_batches)


# ---------- row collection (shared by export_csv and get_df) ----------

def collect_rows(session, vault, resolved, columns, limit=None,
                 page_size=1000, verbose=True):
    """Walk the resolved collections and produce all rows in memory.

    Returns (rows, resolver, stats) where:
      - rows: list[list[str]] — each inner list is one row in `columns` order
      - resolver: the ColumnResolver instance (call .warn_unmatched() to log typos)
      - stats: dict with multi_batch_mols, zero_batch_mols, rows_per_collection

    Used by both export_csv (writes to disk) and get_df (builds a DataFrame).
    """
    resolver = ColumnResolver(columns)
    multi_batch_mols = 0
    zero_batch_mols = 0
    rows_per_coll = {}
    all_rows = []

    for name, cid in resolved:
        if verbose:
            print(f"\n--- collection name={name} id={cid} ---")
        coll_rows = 0
        # Row count isn't known up front (paginated), so the bar shows a live
        # count + rate rather than a percentage. Disabled when not verbose.
        bar = tqdm(desc=f"  {name}", unit="row", disable=not verbose, leave=True)
        for mol in paginate_molecules(session, vault, cid,
                                      page_size=page_size, limit=None):
            batches = mol.get("batches") or []
            if not batches:
                zero_batch_mols += 1
                iter_batches = [None]
            else:
                if len(batches) > 1:
                    multi_batch_mols += 1
                iter_batches = batches
            for batch in iter_batches:
                all_rows.append(resolver.resolve_row(mol, batch, name))
                coll_rows += 1
                bar.update(1)
                if limit is not None and coll_rows >= limit:
                    break
            if limit is not None and coll_rows >= limit:
                break
        bar.close()
        rows_per_coll[name] = coll_rows
        if verbose:
            print(f"  collection_rows={coll_rows}")

    return all_rows, resolver, {
        "multi_batch_mols": multi_batch_mols,
        "zero_batch_mols": zero_batch_mols,
        "rows_per_collection": rows_per_coll,
    }


def _print_run_stats(rows, stats, limit, resolver, output=None):
    """Emit the post-run summary used by both export_csv and get_df."""
    print(f"\ntotal_rows={len(rows)}")
    if stats["multi_batch_mols"]:
        print(
            f"multi_batch_molecules={stats['multi_batch_mols']} "
            f"(each emitted >1 row — one per batch)"
        )
    if stats["zero_batch_mols"]:
        print(
            f"zero_batch_molecules={stats['zero_batch_mols']} "
            f"(emitted 1 row with empty batch-level cells)"
        )
    if output is not None:
        print(f"output_file={output}")
    if limit is None:
        resolver.warn_unmatched()
    else:
        print("note: limit set → unmatched-column WARN skipped "
              "(false positives likely). Re-run without limit to validate "
              "columns, or use --list-fields.")


# ---------- export ----------

def export_csv(session, vault, resolved, output, columns, limit,
               page_size=1000):
    print("=== export ===")
    print(
        f"vault={vault} output={output} columns={columns} "
        f"limit={limit} page_size={page_size}"
    )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    rows, resolver, stats = collect_rows(
        session, vault, resolved, columns,
        limit=limit, page_size=page_size, verbose=True,
    )

    with open(output, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(columns)
        w.writerows(rows)

    _print_run_stats(rows, stats, limit, resolver, output=output)


# ---------- notebook / programmatic API ----------

def _normalize_arg(arg):
    """Accept a list, a comma-separated string, or None."""
    if arg is None:
        return None
    if isinstance(arg, str):
        return [x.strip() for x in arg.split(",") if x.strip()]
    return [str(x).strip() for x in arg if str(x).strip()]


def get_df(
    vault,
    collections=None,
    collection_ids=None,
    columns=None,
    token=None,
    token_file="~/.cdd_token",
    limit=None,
    page_size=1000,
    verbose=True,
):
    """Fetch CDD Vault collections and return as a pandas DataFrame.

    Mirrors the CLI behavior: one row per (molecule, batch), layered column
    resolution across `top-level / molecule_fields / batch_fields`. See
    docs/documentation.md for the full reference.

    Args:
        vault (int): CDD Vault numeric ID.
        collections: collection names — list[str] or 'AJ,AK'. Mutually
            exclusive with collection_ids.
        collection_ids: numeric collection IDs — list[int|str] or '931034,931035'.
            Mutually exclusive with collections.
        columns: requested columns — list[str] or comma-separated string.
            Defaults to ['collection', 'name', 'smiles'].
        token: API token string. If None, falls back to token_file.
        token_file: path to a one-line token file (default '~/.cdd_token').
        limit: cap rows per collection (smoke testing).
        page_size: CDD API page size.
        verbose: print progress to stdout.

    Returns:
        pandas.DataFrame with the requested columns.
    """
    import pandas as pd  # lazy so the CLI doesn't require pandas

    names = _normalize_arg(collections)
    ids = _normalize_arg(collection_ids)
    if not names and not ids:
        raise ValueError(
            "must supply collections=['AJ',...] or collection_ids=[931034,...]"
        )
    if names and ids:
        raise ValueError(
            "supply only one of collections / collection_ids, not both"
        )

    if token is None:
        token = load_token(token_file)
    session = make_session(token)

    if names:
        resolved = resolve_collections(session, vault, names=names)
    else:
        resolved = resolve_collections(session, vault, ids=ids)
    if verbose:
        print(f"resolved_collections={[(n, int(c)) for n, c in resolved]}")

    if columns is None:
        cols = ["collection", "name", "smiles"]
    elif isinstance(columns, str):
        cols = [c.strip() for c in columns.split(",") if c.strip()]
    else:
        cols = list(columns)
    if not cols:
        raise ValueError("columns parsed to empty list")
    if verbose:
        print(f"requested_columns={cols}")

    rows, resolver, stats = collect_rows(
        session, vault, resolved, cols,
        limit=limit, page_size=page_size, verbose=verbose,
    )

    df = pd.DataFrame(rows, columns=cols)

    if verbose:
        _print_run_stats(rows, stats, limit, resolver, output=None)

    return df


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(
        description="Export CDD Vault collections to CSV "
                    "(chemistry stays local; stdout shows metadata only).",
    )
    ap.add_argument("--vault", type=int, default=7108)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--collections", help="Comma-separated names, e.g. AJ,AK")
    g.add_argument("--collection-ids",
                   help="Comma-separated numeric IDs")
    ap.add_argument("--token-file", default="~/.cdd_token")
    ap.add_argument("--output", default="./library.csv")
    ap.add_argument("--columns", default="collection,name,smiles")
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap rows per collection (smoke test).")
    ap.add_argument("--page-size", type=int, default=1000)
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--list-fields", action="store_true",
                    help="List all available column names per namespace "
                         "(top-level / molecule_fields / batch_fields) "
                         "with coverage counts, then exit.")
    args = ap.parse_args()

    if not args.collections and not args.collection_ids:
        sys.exit("ERR: must supply --collections or --collection-ids")

    token = load_token(args.token_file)
    session = make_session(token)

    if args.collections:
        names = [n.strip() for n in args.collections.split(",") if n.strip()]
        resolved = resolve_collections(session, args.vault, names=names)
    else:
        ids = [i.strip() for i in args.collection_ids.split(",") if i.strip()]
        resolved = resolve_collections(session, args.vault, ids=ids)

    print(
        "resolved_collections=" +
        str([(n, int(c)) for n, c in resolved])
    )

    if args.discover:
        discover(session, args.vault, resolved, page_size=args.page_size)
        return

    if args.list_fields:
        list_fields(session, args.vault, resolved, page_size=args.page_size)
        return

    columns = [c.strip() for c in args.columns.split(",") if c.strip()]
    if not columns:
        sys.exit("ERR: --columns parsed to empty list")
    print(f"requested_columns={columns}")
    export_csv(
        session, args.vault, resolved, args.output, columns,
        args.limit, page_size=args.page_size,
    )


if __name__ == "__main__":
    main()
