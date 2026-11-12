#!/usr/bin/env python3
"""
detect_protocol.py — Guess which CDD protocol an upload file belongs to from its
column headers (a "column signature"), using config/config.yaml as the registry.

Scoring per protocol block (blocks with no `readouts` — extraction-only — are
skipped, they can't be signature-detected):
  * recall    = fraction of the block's readouts present in the file header
  * precision = fraction of the file header explained by the block
  * identifier_ok = the block's identifier column is present (or reconcilable
    from WuXi's 'Molecule-Batch ID' to MDR1's 'Batch Molecule-Batch ID')
A block is *viable* if identifier_ok and recall >= MIN_RECALL. The best viable
block (by recall, then precision) wins.

Microsomal human and mouse share identical readouts, so they tie on columns
alone. When the top viable blocks tie and each carries a `species:` field, the
result is a SPECIES GROUP: the caller splits the file by its Species column and
routes each species' rows to the matching PID.

Header/values: this module only ever looks at column NAMES. It never reads or
returns data values.
"""

import re
from os.path import commonprefix

from convert_upload import _ID_PREFIXED, _ID_UNPREFIXED, _norm

MIN_RECALL = 0.6      # a block must explain >=60% of its readouts to be viable
TIE_EPS = 1e-9        # recall/precision within this count as tied (species group)
# block field that splits tied protocols by a column VALUE; the file column is the
# field name with "_" read as a space (cell_line -> "Cell line"); first match wins
SPLIT_KEYS = ("species", "route", "cell_line")


def _group_name(tied, by="species"):
    """Name a species or route group from the prefix its protocol names share.

    'Microsomal stability species dog' + '... human' -> 'Microsomal stability
    (species split)'; 'Mouse PK Cassette IV' + '... PO' with by='route' ->
    'Mouse PK Cassette (route split)'. Keeps the label honest once more than one
    assay splits — hepatocyte files used to be labelled 'Microsomal stability'.
    """
    common = commonprefix([c["name"] or "" for c in tied])
    common = common[: common.rfind(" ")] if " " in common else ""   # keep whole words only
    common = re.sub(r"[\s(\-_]*(species)?[\s(\-_]*$", "", common, flags=re.I).strip()
    return f"{common} ({by} split)" if len(common) >= 3 else f"{by.capitalize()} split"


def _identifier_ok(header_norms, block):
    """True if the block's identifier column is present or reconcilable."""
    idents = list((block.get("identifiers") or {}).values())
    for v in idents:
        if _norm(v) in header_norms:
            return True
        if v == _ID_PREFIXED and _norm(_ID_UNPREFIXED) in header_norms:
            return True
    return False


def _score_block(header_norms, block):
    """Score one protocol block against a header. None if not signature-detectable."""
    readouts = block.get("readouts") or {}
    if not readouts:
        return None
    matched = sum(1 for k in readouts if _norm(k) in header_norms)
    importable = {_norm(k) for k in readouts} | {
        _norm(v) for v in (block.get("identifiers") or {}).values()
    }
    explained = sum(1 for h in header_norms if h in importable)
    return {
        "matched": matched,
        "total": len(readouts),
        "recall": matched / len(readouts),
        "precision": explained / len(header_norms) if header_norms else 0.0,
        "identifier_ok": _identifier_ok(header_norms, block),
        **{k: block.get(k) for k in SPLIT_KEYS},
    }


def score_protocols(header, config):
    """Return every signature-detectable block scored, sorted best-first."""
    header_norms = {_norm(h) for h in header}
    protos = config.get("protocols") or {}
    cands = []
    for pid, block in protos.items():
        sc = _score_block(header_norms, block)
        if sc is None:
            continue
        cands.append({"pid": int(pid), "name": block.get("name"), **sc})
    cands.sort(key=lambda c: (c["recall"], c["precision"]), reverse=True)
    return cands


def detect(header, config, min_recall=MIN_RECALL):
    """Classify a header into one protocol (or a species group / unknown).

    Returns a dict:
      status: 'single' | '<key>_group' for a key in SPLIT_KEYS | 'ambiguous' | 'unknown'
      pid, name        : the chosen protocol (None unless 'single')
      confidence       : best viable recall (0..1)
      <key>_pids       : {column value: pid} for each key in SPLIT_KEYS (filled
                         only for its own '<key>_group'), e.g. species_pids
      candidates       : top scored blocks (for display / manual override)
    """
    cands = score_protocols(header, config)
    viable = [c for c in cands if c["identifier_ok"] and c["recall"] >= min_recall]
    result = {
        "status": "unknown",
        "pid": None,
        "name": None,
        "confidence": round(cands[0]["recall"], 3) if cands else 0.0,
        **{f"{k}_pids": {} for k in SPLIT_KEYS},
        "candidates": [
            {k: c[k] for k in ("pid", "name", "recall", "precision", "identifier_ok")}
            for c in cands[:4]
        ],
    }
    if not viable:
        return result

    best = viable[0]
    result["confidence"] = round(best["recall"], 3)
    tied = [
        c for c in viable
        if abs(c["recall"] - best["recall"]) <= TIE_EPS
        and abs(c["precision"] - best["precision"]) <= TIE_EPS
    ]
    for key in SPLIT_KEYS:
        group = [c for c in tied if c[key]]
        if len(group) >= 2:
            result["status"] = f"{key}_group"
            result["name"] = _group_name(group, by=key.replace("_", " "))
            result[f"{key}_pids"] = {c[key]: c["pid"] for c in group}
            return result
    if len(tied) == 1:
        result["status"] = "single"
        result["pid"] = best["pid"]
        result["name"] = best["name"]
    else:
        result["status"] = "ambiguous"  # >1 tied, no split field to disambiguate
    return result


def main():
    """Smoke-test detection on a file's header (column names only, no values)."""
    import argparse
    import yaml

    from convert_upload import read_upload_sheet

    ap = argparse.ArgumentParser(description="Detect the CDD protocol for an upload file.")
    ap.add_argument("file", help="Raw .xlsx workbook with an 'Upload' tab.")
    ap.add_argument("--config", default="config/config.yaml")
    args = ap.parse_args()

    config = yaml.safe_load(open(args.config))
    header, _ = read_upload_sheet(args.file)
    d = detect(header, config)
    print(f"status={d['status']} pid={d['pid']} name={d['name']!r} conf={d['confidence']}")
    for k in SPLIT_KEYS:
        if d[f"{k}_pids"]:
            print(f"{k}_pids={d[f'{k}_pids']}")
    for c in d["candidates"]:
        print(f"  cand pid={c['pid']} recall={c['recall']:.2f} prec={c['precision']:.2f} "
              f"id_ok={c['identifier_ok']} name={c['name']!r}")


if __name__ == "__main__":
    main()
