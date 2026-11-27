#!/usr/bin/env python3
"""
mpk_to_upload.py — Build a CDD upload CSV from a WuXi mouse-PK (MPK) report.

WuXi MPK reports carry no 'Upload' tab. This reads the 'PK Report' and 'Dose'
tabs and returns one row per (analyte, dosing group) in the column layout of
the Mouse PK Cassette block in config/config.yaml. Every label it looks for
lives in that block's `report:` section, so a changed report layout is a config
edit, not a code change.

Each value comes from:
  * study header (col B label, col C value): study number, in-life date, species
  * per-group header rows: route (group name minus its number), nominal dose,
    formulation (forward-filled across a merged group; `formulation_pattern` cuts a
    leading '<conc> mg/mL in', so the readout holds the vehicle only)
  * per-analyte rows: analyte molecule id -> batch id (the identifier)
  * each 'PK Parameters' table: the 'Mean' column, matched by row LABEL
  * the 'Dose' tab: nominal concentration per (group number, compound)
Numeric fields keep a number or a '<'/'>' qualified number; any other token
(ND, --, BQL, ...) is written blank, because CDD rejects text in a numeric
readout.

HARD RULE (same as import_to_protocol): cell VALUES are never printed. Stdout is
metadata only — file names, row counts, and the tokens blanked per column.
"""

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from convert_upload import _norm, write_csv

NUMERIC = re.compile(r"^[<>]?\s*[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?$")


def iso_date(v):
    """Render a date cell, or a 'YYYY/M/D' text cell, as ISO 'YYYY-MM-DD'."""
    if hasattr(v, "date"):
        return v.date().isoformat()
    m = re.match(r"(\d{4})\D(\d{1,2})\D(\d{1,2})", str(v))
    return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}" if m else _norm(v)


def header_rows(ws, title):
    """{label in col B: row} for the study header, i.e. every row above the first PK block."""
    rows = {}
    for r in range(1, ws.max_row + 1):
        lab = _norm(ws.cell(r, 2).value)
        if lab.startswith(title):
            break
        if lab:
            rows.setdefault(lab, r)
    return rows


def group_table(ws, rows, labels):
    """{(group, compound): {'dose', 'formulation'}} from the per-group header rows.

    Group columns run right from col C while the nominal-dose cell is a number;
    the PK-settings labels beside them stop the scan. A blank formulation cell
    inherits the one to its left in the same group (merged cells).
    """
    table, form = {}, {}
    for c in range(3, ws.max_column + 1):
        dose = ws.cell(rows[labels["dose"]], c).value
        if not NUMERIC.match(_norm(dose)):
            break
        g = _norm(ws.cell(rows[labels["group"]], c).value)
        form[g] = _norm(ws.cell(rows[labels["formulation"]], c).value) or form.get(g, "")
        table[(g, _norm(ws.cell(rows[labels["compound"]], c).value))] = {"dose": dose, "formulation": form[g]}
    return table


def report_kind(path, spec):
    """'cassette' if a dosing group of the report gives more than one compound, else 'single'.

    The CLI fills only the blocks whose `dosing:` equals this, so a single-compound report never
    gets the cassette column layout (and the reverse).
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[spec["sheet"]]
    groups = group_table(ws, header_rows(ws, spec["block_title"]), spec["group_labels"])
    wb.close()
    return "cassette" if max(Counter(g for g, _ in groups).values()) > 1 else "single"


def analyte_batches(ws, rows, labels):
    """{analyte molecule id: batch id}, reading columns right from col C until the batch cell is blank."""
    out = {}
    for c in range(3, ws.max_column + 1):
        batch = _norm(ws.cell(rows[labels["batch"]], c).value)
        if not batch:
            break
        out[_norm(ws.cell(rows[labels["analyte"]], c).value)] = batch
    return out


def pk_tables(ws, groups, spec):
    """[(analyte, group, {upload field: Mean cell})] for every 'PK Parameters' table.

    A table's group is the nearest group name above it in the same column; its
    analyte is the id on the row under the nearest block title above it. Rows
    are read until a blank label, a group name or another title.
    """
    params = {_norm(k): v for k, v in spec["parameters"].items()}
    title, ptitle = spec["block_title"], spec["params_title"]
    out, analyte = [], None
    for r in range(1, ws.max_row + 1):
        if _norm(ws.cell(r, 2).value).startswith(title):
            analyte = _norm(ws.cell(r + 1, 2).value)
        for c in range(1, ws.max_column + 1):
            if _norm(ws.cell(r, c).value) != ptitle:
                continue
            mean = next(m for m in range(c + 1, ws.max_column + 1) if _norm(ws.cell(r, m).value) == "Mean")
            group = next(_norm(ws.cell(u, c).value) for u in range(r - 1, 0, -1)
                         if _norm(ws.cell(u, c).value) in groups)
            vals, rr = {}, r + 1
            while (lab := _norm(ws.cell(rr, c).value)) and lab not in groups and lab != ptitle \
                    and not lab.startswith(title):
                if lab in params:
                    vals[params[lab]] = ws.cell(rr, mean).value
                rr += 1
            out.append((analyte, group, vals))
    return out


def dose_concentrations(ws, labels):
    """{(group number, compound): nominal concentration} from the Dose tab's repeated tables."""
    out, idx = {}, None
    for row in ws.iter_rows(values_only=True):
        cells = [_norm(v) for v in row]
        if labels["group"] in cells:
            idx = {k: cells.index(v) for k, v in labels.items()}
        elif idx and cells[idx["group"]]:
            out[(cells[idx["group"]], cells[idx["compound"]])] = row[idx["concentration"]]
    return out


def report_rows(path, block):
    """Read one MPK report into (header, rows, blanked, conflicts) for a block with a `report:` map.

    header    : identifier + the block's readout keys, in config order
    rows      : one list per (analyte, group), aligned to header; only the groups
                whose route equals the block's `route:` when it sets one
    blanked   : {field: Counter(token)} for numeric cells written blank
    conflicts : 1-based positions in `rows` whose formulation text names another
                concentration than the Dose tab (Concentration keeps the Dose-tab value)
    Raises ValueError if the species text does not match `species_pattern`.
    """
    import openpyxl

    spec = block["report"]
    fields = spec["fields"]
    ident = block["identifiers"]["batch_id"]
    header = [ident] + list(block["readouts"])
    numeric = set(spec["parameters"].values()) | {fields["dose"], fields["concentration"]}

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[spec["sheet"]]
    rows = header_rows(ws, spec["block_title"])
    study = {f: ws.cell(rows[lab], 3).value for lab, f in spec["header"].items()}
    m = re.match(spec["species_pattern"], _norm(ws.cell(rows[spec["species_label"]], 3).value))
    if not m:
        raise ValueError(f"{Path(path).name}: species text does not match report.species_pattern")
    groups = group_table(ws, rows, spec["group_labels"])
    batches = analyte_batches(ws, rows, spec["analyte_labels"])
    tables = pk_tables(ws, {g for g, _ in groups}, spec)
    conc = dose_concentrations(wb[spec["dose_sheet"]], spec["dose_tab"])
    wb.close()

    fpat = spec.get("formulation_pattern")
    out, blanked, conflicts = [], defaultdict(Counter), []
    for analyte, group, vals in tables:
        route = re.sub(r"\d+$", "", group).strip()
        if block.get("route") and route != block["route"]:
            continue
        rec = {**study, **{k: v for k, v in m.groupdict().items() if v}, **vals}
        rec[ident] = batches[analyte]
        rec[fields["date"]] = iso_date(rec[fields["date"]])
        rec[fields["route"]] = route
        rec[fields["dose"]] = groups[(group, analyte)]["dose"]
        rec[fields["formulation"]] = groups[(group, analyte)]["formulation"]
        rec[fields["concentration"]] = conc.get((re.search(r"(\d+)$", group)[1], analyte), "")
        rec[fields["analyte"]] = analyte
        for f in numeric & rec.keys():
            s = _norm(rec[f])
            if s and not NUMERIC.match(s):
                blanked[f][s] += 1
                rec[f] = ""
        fm = fpat and re.match(fpat, rec[fields["formulation"]])
        if fm:
            rec[fields["formulation"]] = fm["formulation"]
            c = _norm(rec[fields["concentration"]])
            if fm["concentration"] and (not c or float(c) != float(fm["concentration"])):
                conflicts.append(len(out) + 1)
        out.append([rec.get(h, "") for h in header])
    return header, out, blanked, conflicts


def sheet_xml(header, rows):
    """A minimal worksheet part holding header + rows: numbers as numbers, the rest as inline text."""
    from openpyxl.utils import get_column_letter
    from xml.sax.saxutils import escape

    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
    for r, vals in enumerate([header] + rows, 1):
        cells = []
        for c, v in enumerate(vals, 1):
            ref = f"{get_column_letter(c)}{r}"
            if isinstance(v, str) and re.fullmatch(r"[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?", v.strip()):
                v = float(v)
            if v is None or str(v).strip() == "":
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                cells.append(f'<c r="{ref}"><v>{v!r}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(v))}</t></is></c>')
        out.append(f'<row r="{r}">{"".join(cells)}</row>')
    return "".join(out) + "</sheetData></worksheet>"


def add_upload_sheet(src, dst, header, rows, name="Upload"):
    """Copy the workbook `src` to `dst` with one extra sheet `name` holding header + rows.

    Works on the zip parts, not through openpyxl: every original part (charts,
    drawings, images, embedded objects) is copied byte-for-byte, and only the
    workbook, its relationships and the content-type list are patched to register
    the new sheet. The sheet goes LAST, so sheet-position references such as a
    defined name's localSheetId still point where they did.
    Raises ValueError if `src` already has a sheet called `name`.
    """
    import zipfile

    with zipfile.ZipFile(src) as zin:
        parts = {i.filename: zin.read(i.filename) for i in zin.infolist()}
        infos = zin.infolist()
    wb = parts["xl/workbook.xml"].decode("utf8")
    if re.search(rf'<sheet\b[^>]*\bname="{re.escape(name)}"', wb):
        raise ValueError(f"{Path(src).name} already has a '{name}' sheet")
    rels = parts["xl/_rels/workbook.xml.rels"].decode("utf8")
    types = parts["[Content_Types].xml"].decode("utf8")

    # next free numbers for the part, the sheet and the relationship
    n = max(int(m) for m in re.findall(r"xl/worksheets/sheet(\d+)\.xml", " ".join(parts))) + 1
    sid = max(int(x) for x in re.findall(r'sheetId="(\d+)"', wb)) + 1
    rid = f"rId{max(int(x) for x in re.findall(r'Id=\"rId(\d+)\"', rels)) + 1}"

    # register the sheet in the three index parts
    patched = {
        "xl/workbook.xml": wb.replace("</sheets>", f'<sheet name="{name}" sheetId="{sid}" r:id="{rid}"/></sheets>'),
        "xl/_rels/workbook.xml.rels": rels.replace(
            "</Relationships>", f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/'
            f'2006/relationships/worksheet" Target="worksheets/sheet{n}.xml"/></Relationships>'),
        "[Content_Types].xml": types.replace(
            "</Types>", f'<Override PartName="/xl/worksheets/sheet{n}.xml" ContentType="application/'
            f'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'),
    }
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in infos:
            zout.writestr(info, patched[info.filename].encode("utf8") if info.filename in patched
                          else parts[info.filename])
        zout.writestr(f"xl/worksheets/sheet{n}.xml", sheet_xml(header, rows))


def main():
    """Convert MPK reports to one `<report>_<route>_upl.csv` per route block (stdout is metadata only).

    Without --protocol, each report fills the blocks whose `dosing:` matches its `report_kind`.
    """
    import argparse

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import import_to_protocol as itp
    from split_species import _safe

    ap = argparse.ArgumentParser(description="Build CDD upload CSVs from WuXi MPK reports.")
    ap.add_argument("files", nargs="+", help="MPK report .xlsx files (no Upload tab needed).")
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--protocol", type=int,
                    help="One block only; default: the blocks whose `dosing:` matches the report (cassette / single).")
    ap.add_argument("--dry-run", action="store_true", help="Report what would be written, write nothing.")
    ap.add_argument("--upload-tab", metavar="DIR",
                    help="Also write a copy of each report into DIR with an 'Upload' tab holding every route.")
    args = ap.parse_args()

    protos = itp.load_config(args.config)["protocols"]
    report_blocks = [b for b in protos.values() if b.get("report")]
    for f in args.files:
        kind = report_kind(f, report_blocks[0]["report"])
        blocks = [protos[args.protocol]] if args.protocol else [b for b in report_blocks if b.get("dosing") == kind]
        if not blocks:
            sys.exit(f"ERR {Path(f).name}: {kind} report, but no block has `dosing: {kind}`")
        print(f"{Path(f).name}: {kind} report")
        written = 0
        for block in blocks:
            header, rows, blanked, conflicts = report_rows(f, block)
            tag = f"_{_safe(block['route'])}" if block.get("route") else ""
            out = Path(f).with_name(f"{Path(f).stem}{tag}_upl.csv")
            if not args.dry_run:
                write_csv(out, header, rows)
            written += len(rows)
            blank = {k: dict(v) for k, v in blanked.items()}
            print(f"{Path(f).name} [{block.get('route') or 'all'}]: rows={len(rows)} cols={len(header)} "
                  f"blanked={blank or 'none'}{'' if args.dry_run else ' -> ' + out.name}")
            if conflicts:
                print(f"WARN {Path(f).name} [{block.get('route') or 'all'}]: row(s) {conflicts}: the formulation "
                      "names another concentration than the Dose tab; Concentration keeps the Dose-tab value")

        # rows whose route no block claims
        header, every, _, _ = report_rows(f, {k: v for k, v in blocks[0].items() if k != "route"})
        if not args.protocol and len(every) != written:
            print(f"WARN {Path(f).name}: {len(every) - written} row(s) match no block's route — not written")

        # copy of the report with an Upload tab holding every route
        if args.upload_tab and not args.dry_run:
            dst = Path(args.upload_tab) / Path(f).name
            dst.parent.mkdir(parents=True, exist_ok=True)
            add_upload_sheet(f, dst, header, every)
            print(f"{Path(f).name}: Upload tab rows={len(every)} -> {dst}")


if __name__ == "__main__":
    main()
