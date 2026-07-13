#!/usr/bin/env python3
"""
app.py — Local-only FastAPI server for drag-drop WuXi -> CDD Vault imports.

LOCALHOST ONLY. This app handles SMILES / compound IDs / assay values, so it
MUST NOT be exposed off this machine — it binds to 127.0.0.1 and the CDD token
is read server-side (from ~/.cdd_token) and never sent to the browser.

Pipeline (reusing the tested CLI modules):
  upload  -> read Upload tab (convert_upload) -> detect protocol (detect_protocol)
          -> per submission unit: reconcile identifier + drop blank-id rows +
             stage a CSV in a temp dir + evaluate mapping (import_to_protocol)
  submit  -> build payload + submit_slurp per unit (raises SlurpError on 422)
  status  -> slurp_status per slurp id (frontend polls)

Only column NAMES and counts cross to the browser — never cell values. Staged
CSVs live in a temp dir (nothing written into the repo) and are cleared on exit.
"""

import atexit
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

# The CLI modules live in <repo>/python/; this file is <repo>/webapp/app.py,
# so the repo root is one level up.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import yaml
from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import convert_upload as cu
import detect_protocol as dp
import import_to_protocol as itp
import notify
from get_library import API_BASE, make_session  # noqa: F401  (API_BASE via itp)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config" / "config.yaml"
TOKEN_FILE = Path("~/.cdd_token").expanduser()
STATIC_DIR = Path(__file__).resolve().parent / "static"

# Temp staging for converted CSVs — cleared on process exit; never in the repo.
STAGE_DIR = Path(tempfile.mkdtemp(prefix="cdd_webapp_"))
atexit.register(lambda: shutil.rmtree(STAGE_DIR, ignore_errors=True))

CONFIG = yaml.safe_load(CONFIG_PATH.read_text())
VAULT = CONFIG.get("vault", 7108)

# file_id -> staged unit (raw header/rows kept so a protocol change can re-convert).
STAGE = {}
_SESSION = None  # lazily built read/write CDD session


# ---------- helpers ----------

def _block(pid):
    protos = CONFIG.get("protocols") or {}
    return protos.get(pid) or protos.get(str(pid))


def _importable_protocols():
    """Config blocks that can actually be imported to (have a mapping template)."""
    out = []
    for pid, b in (CONFIG.get("protocols") or {}).items():
        if b.get("mapping_template") and (b.get("readouts") or b.get("species")):
            out.append({"pid": int(pid), "name": b.get("name"), "alias": b.get("alias")})
    return sorted(out, key=lambda x: x["name"] or "")


def _session():
    global _SESSION
    if _SESSION is None:
        if not TOKEN_FILE.exists():
            raise HTTPException(400, f"CDD token file not found: {TOKEN_FILE} "
                                     "(a read/write token is required to submit)")
        tok = TOKEN_FILE.read_text().strip()
        if not tok:
            raise HTTPException(400, f"CDD token file is empty: {TOKEN_FILE}")
        _SESSION = make_session(tok)
    return _SESSION


def _unit_view(fid):
    """The browser-safe view of a staged unit — names + counts only, no values."""
    e = STAGE[fid]
    return {
        "file_id": fid,
        "parent": e["parent"],
        "species": e.get("species"),
        "protocol_pid": e.get("pid"),
        "protocol_name": e.get("name"),
        "rows": e.get("kept", len(e.get("raw_rows", []))),
        "dropped_blank_id": e.get("dropped", 0),
        "renamed_identifier": bool(e.get("renames")),
        "mapping_ok": e.get("mapping_ok", False),
        "unmapped": e.get("unmapped", []),   # column names — schema, safe
        "missing": e.get("missing", []),     # column names — schema, safe
        "status": e.get("status", "needs-selection"),
        "candidates": e.get("candidates", []),
    }


def _stage_unit(fid, parent, species, header, rows, pid, candidates=None):
    """Convert (if a pid is known) + evaluate + store a submission unit."""
    e = STAGE.setdefault(fid, {})
    e.update(parent=parent, species=species, raw_header=header, raw_rows=rows,
             pid=pid, candidates=candidates or [])
    if pid is None:
        e.update(csv_path=None, name=None, mapping_ok=False, unmapped=[], missing=[],
                 kept=len(rows), dropped=0, renames={}, status="needs-selection")
        return _unit_view(fid)

    block = _block(pid)
    if block is None:
        e.update(status="error", name=f"pid {pid} not in config", mapping_ok=False)
        return _unit_view(fid)
    h2, renames = cu.reconcile_identifier(header, block)
    r2, dropped = cu.drop_blank_identifier_rows(h2, rows, block)
    csv_path = STAGE_DIR / f"{fid}.csv"
    cu.write_csv(csv_path, h2, r2)
    res = itp.evaluate_mapping(h2, block, missing_id_rows=0)
    ready = res["ok"] and bool(block.get("mapping_template")) and len(r2) > 0
    e.update(csv_path=str(csv_path), header=h2, conv_rows=r2, name=block.get("name"),
             project=block.get("project"), mapping_template=block.get("mapping_template"),
             mapping_ok=res["ok"], unmapped=res["unmapped"], missing=res["missing"],
             kept=len(r2), dropped=dropped, renames=renames,
             status="ready" if ready else "needs-selection")
    return _unit_view(fid)


def _summary_rows():
    """Per-compound rollup across staged units: batch id -> the assay labels it
    appears in, both in first-seen order.

    Returns [{"batch_id": ..., "assays": [label, ...]}, ...]. Extracts ONLY the
    identifier (batch id) column + each unit's label — never readout values. By
    design this carries compound batch ids (shown in the local browser and, per
    explicit owner authorization, the team email); it deliberately stops short of
    the measured values.
    """
    order, assays, dates = [], {}, {}
    for e in STAGE.values():
        pid, header, rows = e.get("pid"), e.get("header"), e.get("conv_rows")
        if not (pid and header and rows):
            continue
        block = _block(pid) or {}
        label = block.get("label") or block.get("name") or f"PID {pid}"
        hdr_norm = [cu._norm(h) for h in header]
        idents = list((block.get("identifiers") or {}).values())
        idx = next((hdr_norm.index(cu._norm(n)) for n in idents if cu._norm(n) in hdr_norm), None)
        if idx is None:
            continue
        date_idx = next((i for i, h in enumerate(hdr_norm) if h.lower() in ("date", "run date")), None)
        for r in rows:
            bid = str(r[idx]).strip() if idx < len(r) and r[idx] is not None else ""
            if not bid:
                continue
            if bid not in assays:
                assays[bid], dates[bid] = [], []
                order.append(bid)
            if label not in assays[bid]:
                assays[bid].append(label)
            if date_idx is not None and date_idx < len(r):
                dv = str(r[date_idx]).strip()
                if dv and dv not in dates[bid]:
                    dates[bid].append(dv)
    return [{"batch_id": b, "run_date": ", ".join(dates[b]), "assays": assays[b]} for b in order]


# ---------- app ----------

app = FastAPI(title="WuXi → CDD import")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/protocols")
def protocols():
    return {"protocols": _importable_protocols()}


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)):
    """Accept one or more .xlsx; return a flat list of submission units."""
    units = []
    for uf in files:
        raw = STAGE_DIR / f"raw_{uuid.uuid4().hex}.xlsx"
        raw.write_bytes(await uf.read())
        try:
            header, rows = cu.read_upload_sheet(raw)
        except cu.UploadSheetError as ex:
            fid = uuid.uuid4().hex
            STAGE[fid] = {"parent": uf.filename, "species": None, "pid": None,
                          "name": str(ex), "status": "error", "raw_rows": [],
                          "kept": 0, "mapping_ok": False}
            units.append(_unit_view(fid))
            continue
        finally:
            raw.unlink(missing_ok=True)

        det = dp.detect(header, CONFIG)
        cands = det.get("candidates", [])
        if det["status"] == "species_group":
            for species, srows in (cu.split_species(header, rows) or []):
                pid = det["species_pids"].get(species) or det["species_pids"].get(
                    species.title()) or det["species_pids"].get(species.capitalize())
                units.append(_stage_unit(uuid.uuid4().hex, uf.filename, species,
                                         header, srows, pid, cands))
        elif det["status"] == "single":
            units.append(_stage_unit(uuid.uuid4().hex, uf.filename, None,
                                     header, rows, det["pid"], cands))
        else:  # unknown / ambiguous — user must pick from the dropdown
            units.append(_stage_unit(uuid.uuid4().hex, uf.filename, None,
                                     header, rows, None, cands))
    return {"units": units}


@app.post("/api/recheck")
def recheck(payload: dict = Body(...)):
    """Re-stage a unit against a user-chosen protocol; returns the updated view."""
    fid, pid = payload.get("file_id"), payload.get("pid")
    e = STAGE.get(fid)
    if e is None:
        raise HTTPException(404, "unknown file_id")
    return _stage_unit(fid, e["parent"], e.get("species"),
                       e["raw_header"], e["raw_rows"], int(pid) if pid else None,
                       e.get("candidates"))


@app.post("/api/submit")
def submit(payload: dict = Body(...)):
    """Submit every listed ready unit to CDD. Returns per-unit slurp id or error."""
    session = _session()
    out = []
    for item in payload.get("units", []):
        fid = item.get("file_id")
        e = STAGE.get(fid)
        if e is None or not e.get("csv_path") or e.get("status") != "ready":
            out.append({"file_id": fid, "error": "not ready to submit"})
            continue
        slurp_payload = itp.build_payload(
            project=e["project"], mapping_template=e["mapping_template"], autoreject=True)
        try:
            sid = itp.submit_slurp(session, VAULT, e["csv_path"], slurp_payload, verbose=False)
            out.append({"file_id": fid, "slurp_id": sid})
        except itp.SlurpError as ex:
            out.append({"file_id": fid, "error": str(ex), "status": ex.status,
                        "web_url": ex.web_url})
    return {"results": out}


@app.get("/api/status")
def status(ids: str = ""):
    """Poll one or more slurp ids; return state + committed/total counts."""
    session = _session()
    out = []
    for sid in [int(s) for s in ids.split(",") if s.strip()]:
        obj = itp.slurp_status(session, VAULT, sid)
        out.append({
            "slurp_id": sid,
            "state": obj.get("state", "unknown"),
            "records_committed": obj.get("records_committed"),
            "total_records": obj.get("total_records"),
        })
    return {"statuses": out}


@app.get("/api/summary")
def summary():
    """Per-compound → assays rollup for the staged batch. NOTE: unlike the other
    endpoints, this intentionally returns compound batch ids to the LOCAL browser
    (localhost) — the per-compound view the operator asked for."""
    return {"compounds": _summary_rows()}


@app.post("/api/email-summary")
def email_summary(payload: dict = Body(...)):
    """Email the per-compound summary table to the configured recipient. The body
    carries compound batch ids by explicit owner authorization (see wiki)."""
    rows = _summary_rows()
    sent = notify.notify_compound_summary(
        CONFIG, rows, subject=payload.get("subject") or "[CDD] upload summary")
    return {"sent": bool(sent), "compounds": len(rows)}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    # 127.0.0.1 ONLY — see module docstring (chemistry data must not leave the box).
    uvicorn.run(app, host="127.0.0.1", port=8000)
