# CDD_Vault_API — project wiki
_Updated: 2026-10-29. Durable, aggregate memory of this repo — read at session start. Aggregate only: no SMILES / compound IDs / per-compound values._

## Where we are now
- **Purpose:** local-only tooling around the CDD Vault REST API (vault **7108**) — export collections, download structure PNGs, bulk-import assay data into protocols, and reshape CRO data for upload.
- **Current state (2026-10-29):** all WuXi protocols import end-to-end. **Microsomal stability is now a 3-species split** (human 88861 / mouse 88862 / **rat 111161, new 2026-10-29**) — rat went live 2026-10-29 with its own `RLM_GT` template, so all **12** importable protocols now have one. **PPB is now split across 6 species protocols** (mouse 125963 + Human/Monkey/Dog/Rat/10%FBS 135299-135303), each with its own `*_GT` mapping template, and value corrections (NA fills + 1-decimal rounding) now run AUTOMATICALLY from config on every upload. Drag-drop webapp is the primary import path. Email transport is now **Microsoft Graph** (code built + tested 2026-09-18); it stays **inactive** until IT supplies an app registration's tenant id / client id / client secret — `~/.auto_graph` does not exist yet. Env is conda `cdd` (Py 3.12).
- **MDR1 protocol import works end-to-end (2026-06-26):** slurp 3460913 → `committed`, 2/2 records. Full Slurps flow proven (POST→`id`, poll→`state`, `committed`).
- **Scripts** (those that hit CDD share auth: `X-CDD-Token` header, token from `~/.cdd_token`, base `https://app.collaborativedrug.com/api/v1`):
  - `python/get_library.py` — export collections → CSV / SDF / `pandas.DataFrame` (`get_df`).
  - `python/download_cdd_structures.py` — fetch compound structure PNGs for a saved search (async export-job flow).
  - `python/import_to_protocol.py` — bulk-import a data file into a protocol via the **Slurps API**. Library-safe core (`submit_slurp`→`SlurpError`, `slurp_status`, `evaluate_mapping`) reused by the webapp; CLI is a thin wrapper.
  - `python/get_protocol_data.py` — extract protocol assay data + SMILES into one wide table (`get_data(experiments=[...])` / CLI `--experiments`); the read counterpart to import.
  - `python/convert_dataset.py` — long→wide pivot for MDR1-style CRO data.
  - `python/split_species.py` — split an `_upl.csv` into one CSV per species (offline; port of the ADME HTML tool's species split).
  - `python/convert_upload.py` — raw WuXi `.xlsx` Upload tab → CDD-ready rows (qualifiers, ISO dates, blank-id drop, MDR1-only identifier rename, in-memory species split). Offline, values never printed.
  - `python/detect_protocol.py` — column-signature protocol detection from `config.yaml` (offline; single / species-group / unknown).
  - `python/notify.py` — optional email (stdlib SMTP): `notify_committed` (metadata-only, CLI) + `notify_compound_summary` (per-compound rollup table, includes batch ids by authorized override; webapp). Sending never raises.
  - `python/check_cdd_commit.py` — post-commit audit: fetch each protocol's data back from CDD and verify every source-file compound exists + its numeric readout values match. Reuses convert_upload/detect_protocol/get_protocol_data. SUCCESS/fail exit code; stdout metadata-only; per-compound detail → local `<file>.verify.txt`. `--compare` also writes `<file>.compare.csv` (per-field CDD-vs-WuXi side-by-side for compounds already in CDD).
  - `python/delete_protocol_rows.py` — delete protocol readout rows selected by Species value. DRY-RUN by default (`--execute` required), backs up every selected row to CSV first, `--exclude-run` protects a run from selection, stdout metadata-only.
  - `static/verify_file.html` — standalone offline format checker **we send to WuXi** (no server/CDN; parses xlsx in-browser). See its section below.
  - `webapp/` — local-only FastAPI drag-drop import UI reusing the modules above. Run: `conda activate cdd && python webapp/app.py` → http://127.0.0.1:8000. Shows the file table + a per-compound summary (Compound | Run date | Assays), submits the batch, then emails the summary to the team.
- **Open threads:**
  - MDR1 import config (PID 131399) is **fully populated** — project `FBXO31`, template `MDCK-MDR1`, identifier, ignores, 26 readouts. **Pipeline validated end-to-end (2026-06-26):** with a Read/Write token the POST succeeded (returned `slurp_id`), polling returned `state` (saw `queued_for_processing` → `rejected`) — both previously-unconfirmed Slurps bits now confirmed. **A read-only token gives `401 "Token has insufficient access to modify data"`** — import needs a Read & Write account token in vault 7108.
  - **First import rejected → fixed → committed (2026-06-26):** the upload had 3 trailing junk rows (all data cols blank, only `External - WuXi,WuXi`). CDD rejected exactly those 3 ("Molecule Names Required"); good rows link the molecule via Batch ID (Molecule Name mapping not required, CDD derives it). Removing the junk rows (1569→1416 B) → re-import → `committed`, 2/2. **Guard added:** `--check-mapping` counts EMPTY-IDENTIFIER rows (identifier cols all blank) and fails the pre-flight before upload. `Molecule Name` kept under `ignore` (user preference; identifier = batch id only).
  - **Slurp response fields confirmed:** `report_outcome` now reads the real keys — `records_committed`, `records_processed`, `total_records`, `import_errors`, `import_warnings` (earlier `errors`/`warnings`/`*_processed` guesses never matched).
  - **`--drop-empty-rows` added (2026-06-26):** the WuXi prep tool stamps `Run Lab`/`Provider Name` onto blank rows, so most ADME files carry junk rows (LogD had 959/960!). The flag drops rows whose identifier cols are all blank into a cleaned temp copy (original untouched), then validates + uploads that. CSV only, needs `--protocol`.
  - Inline-mapping JSON schema is still undocumented, so `config/config.yaml` validates but the **commit relies on the web-UI template** (`mapping_template`), not config-fed inline mapping.
  - Notebook outputs in `vignettes/convert_dataset.ipynb` should be cleared before any push (they can hold structure thumbnails / per-compound values).
  - **WuXi upload-format migration (2026-07-07):** new WuXi reports ship a single `Upload` tab keyed by **`Molecule-Batch ID`** (combined `SRB-XXXXXXX-NNN`) for ALL protocols — readout names already match our CDD convention. **Identifier match verified against the live templates:** ADME templates (LogD/PPB/Thermosl upload) map on `Molecule-Batch ID` as-is; **only MDR1's template wants `Batch Molecule-Batch ID`.** So the raw→processed step renames the identifier for MDR1 only; ADME files keep `Molecule-Batch ID` (I briefly renamed ADME too — wrong, reverted 2026-07-07). Remaining format gap is just the inconsistent tab name (`Upload`/`upload`/`Upload ` trailing space). Converted `data/uploads/20260707/raw/*.xlsx` → `processed/*.csv` (drop blank-id rows incl. 988 in LogD, preserve `<`/`>` qualifiers, ISO dates, drop stray empty-header col); see `data/uploads/20260707/report.md`. All 5 processed CSVs validate `mapping_ok=True`. A stale/mismatched CDD template surfaces as HTTP 422 `"Provided mapping template does not match file."` (import_to_protocol.py now writes 400/422 bodies to a local `<file>.slurp_error.txt`; the error JSON includes a `web_url` to the mapping editor). **DONE:** all 5 WuXi files committed — MDR1, plus the 3 ADME via rebuilt `*_GT` templates.
  - **Drag-drop import web UI — BUILT & TESTED (2026-07-08):** FastAPI local webapp (`webapp/`), localhost-only, reskin of the `From XLS to CDD upload (ADME).html` tool (same palette/fonts, drag-drop + dynamic list). Flow: drop WuXi `.xlsx` → backend extracts the `Upload` tab, converts (blank-id drop, ISO dates, `<`/`>` qualifier reconstruction via openpyxl number_format, MDR1-only identifier rename), auto-splits microsomal by `Species` → per-species submission units → **column-signature protocol detection** → `check_mapping` per unit. Table shows one row per submission unit (an MMS file → 2 rows) with an editable protocol dropdown; **Submit button grey→green once every row's match is confirmed** (`mapping_ok` + non-unknown pid). One batch Submit for all confirmed rows → `submit_slurp` each → progress div polls `state` (surfaces the 422 template-mismatch + `web_url` as a "fix mapping" link). **Decisions:** detection = column signature (not filename); microsomal split in-app; staging CSVs in scratch only (nothing written to the repo); token stays server-side; localhost bind only. A pure `file://` HTML→CDD `fetch()` was ruled out earlier (CORS: null origin + `X-CDD-Token` preflight CDD won't approve) — hence the local server. **New modules:** `python/convert_upload.py` (reusable converter, consolidates the ad-hoc raw→processed logic + `split_species`), `python/detect_protocol.py` (signature scoring), `webapp/app.py` (endpoints: `/api/protocols`, `/api/upload`, `/api/recheck`, `/api/submit`, `/api/status`); `import_to_protocol.py` core funcs refactored to raise/return instead of `sys.exit` (`SlurpError`, `slurp_status`, `evaluate_mapping`; CLI stays a thin wrapper). **Caveats:** microsomal templates (`Microsomal stability - human/mouse`) + identifier (`Compound ID`+`Batch No`) *were* the OLD format — RESOLVED, see next. **New-format microsomal migration DONE (2026-07-09):** a new WuXi MMS report (Human/Mouse, `Molecule-Batch ID`) had three repo-side blockers, all now fixed and the file imported to CDD end-to-end via the webapp. The blockers were: (a) **mis-detected as LogD** — 88861/88862 scored top recall (0.70) but `id_ok=False` because config identifier was still `Compound ID`+`Batch No` while the file ships `Molecule-Batch ID`; fixed by setting both blocks' `identifiers` → `{'batch_id': 'Molecule-Batch ID'}` (detection now `species_group`, conf 1.0). (b) **`_x000D_` header escaping** — new microsomal multi-line headers arrived as `CLint (raw)_x000D_\n(μL/min/mg)` / `Remaining_x000D_\n(...)`; openpyxl leaves the OOXML `_xHHHH_` escape literal and `_norm` only collapses real whitespace, so those 3 readouts missed. Fixed in `convert_upload.py`: new `_unescape_ooxml()` (regex `_x([0-9A-Fa-f]{4})_`→`chr`) applied to the header **and** string cell values; the header build now also `_norm`-collapses so processed CSVs get clean single-line headers (no embedded CR/LF; RFC-4180 valid but visually confusing before). Recall 0.70→1.0, `mapping_ok=True`. (c) **stale templates 422'd** — rebuilt by hand in the CDD UI → saved `HLM_GT` (88861) / `MLM_GT` (88862) → updated `mapping_template` in config (same one-time dance as the ADME three). Mappings verified column-for-column incl. `species`→Species, `Comment`→Serac Comment. **All 35 tests still pass.** Split CSVs live at `data/uploads/20260709/processed/*_Human.csv` / `*_Mouse.csv` (4 rows each). Webapp verified end-to-end: dropping the single MMS `.xlsx` splits into 2 ready units (Human→88861, Mouse→88862) and submits each as a separate slurp — both **committed 2026-07-09**. NOTE: the webapp loads config + modules once at startup — restart it after any config/code change. hlm/mlm can't be told apart by columns (identical readouts) — resolved by the `Species` value at split time. New deps: `fastapi`, `uvicorn[standard]`, `python-multipart` (into the `cdd` venv + requirements.txt, never base). **Run:** `cdd/bin/python webapp/app.py` (from repo root) → http://127.0.0.1:8000 (needs a read/write `~/.cdd_token` for submit). **Tested (2026-07-08):** page + all endpoints; LogD/MDR1 upload → correct detection (MDR1 `renamed_identifier`), recheck→wrong pid flips to not-ready, submit-guard blocks not-ready with no CDD call, synthetic microsomal xlsx splits Human→88861 / Mouse→88862 both ready. Live submit NOT re-fired at the already-committed 5 files (would create duplicate runs); the submit path reuses the same `submit_slurp` that committed them via CLI.

## CRO-facing format checker (static/verify_file.html, 2026-09-14)
- **Purpose:** stop bad WuXi reports at the source. A **standalone single-file HTML** we email to WuXi; they drop a report on it and it validates the workbook against our upload convention BEFORE sending. Shifts the format fight left — we stop discovering breakage at import time.
- **Standalone by design:** no server, no CDN, no Google Fonts, no external request of any kind. The xlsx is parsed **in the browser** with a hand-rolled minimal ZIP reader (central-directory walk + `DecompressionStream('deflate-raw')`) plus regex OOXML parsing (workbook.xml → rels → sharedStrings → sheet). This lets us promise WuXi "nothing leaves your machine", and it works offline. Needs a browser with `DecompressionStream` (Chrome/Edge/Firefox/Safari ≥ 2023); the page says so if absent.
- **Checks (blockers → red):** (1) a sheet named `Upload` exists (case/space-insensitive); (2) the columns match a known assay (recall ≥ 0.6, mirrors `detect_protocol`); (3) the identifier column is present — one `Molecule-Batch ID` (MDR1: `Batch Molecule-Batch ID`); (4) every expected endpoint column is present; (5) **numeric result columns contain numbers only**. **Warnings (amber):** unexpected extra columns; `Study number`/`Date`/identifier blank on some rows. Header normalisation mirrors `convert_upload` exactly (`_unescape_ooxml` `_xHHHH_` decode + whitespace collapse + BOM strip), so WuXi's multi-line `CLint (raw)\r\n(μL/min/mg)` headers match.
- **The conventions are duplicated from config, on purpose** — the page must run with no repo access. `PROTOCOLS` in the HTML carries 5 entries (MDR1, LogD, TSY, PPB, and microsomal **collapsed to one** entry since HLM/MLM share an identical column set and the file carries both in its `species` column). **If a protocol's columns change in `config/config.yaml`, update the HTML's `PROTOCOLS` block and re-send the file.** No readout ids are embedded (internal, and not needed).
- **Validated 2026-09-14 against the 9 real failures in `data/uploads/20260914/` + 9 synthetic workbooks** (TEST-xxxx ids only). The real files split into exactly 3 failure modes: **5 files have no `Upload` tab at all** (WuXi shipped only Signature/Summary/Materials/Initial Report/BA Data — the TSY and HMS ones too); **3 MMS files use the OLD identifier** `Compound ID` + `Batch No` instead of `Molecule-Batch ID` (their endpoint columns score recall 1.0, so the checker names the assay and tells them to merge the two columns); **1 MDR1 file is structurally perfect but carries a literal `NA`** in `Efflux ratio - PgP inhibitor`. Synthetic cases cover the pass path for all 5 assays, a misspelled column, a lowercase `upload` sheet, blank key fields, and `<`/`>` qualifiers.
- **`NA` in a numeric field is a real CDD reject (2026-09-14):** CDD's rejected-row export (`data/uploads/CDD - Ignore text value in numeric fields - *.csv`, `Event Description` column) says `REJECTED: Value 'NA' not a valid number: treating it as blank.` **Convention: leave the cell EMPTY, never write `NA`/`ND`.** A `<`/`>` qualifier (`<0.5`) is legitimate and is NOT flagged. This is why the MDR1 file passed every structural check yet still failed the import.
- **Parser gotcha found + fixed:** the cell regex `<c\b([^>]*)(?:\/>|>...)` was GREEDY, so on a self-closing empty cell (`<c r="B3" t="inlineStr" />` — openpyxl's form for a blank) the `[^>]*` swallowed the `/` and the body match then ate the FOLLOWING cell's value, silently shifting/blanking data. Blank cells are everywhere in real reports (comment columns), so this mattered. Fix: lazy `[^>]*?`. Caught by a synthetic row with one blank field showing a false "blank" on the neighbouring column.
- **De-branded before sending (2026-09-14):** on review, all "CDD" and "Serac" wording was stripped from the page — title/eyebrow/lede are now generic, and rejection messages say "the import rejects…" instead of naming CDD. The CRO does not need to know which vault product we use. **The two exceptions are `Serac Comment` and `Serac Comment - PgP inhibitor`, which are literal MDR1 COLUMN NAMES in the workbook** — renaming them would break validation, so they stay. Audit confirmed the file carries no token, vault id, PID/readout id, project, template name, email, host, path, or any chemistry, and has **zero network primitives** (no fetch/XHR/beacon/WebSocket/Worker/dynamic-import/img/iframe/form/src) and no storage — it only reads the file dropped on it and renders locally, with filenames + cell tokens HTML-escaped. Practical note: email gateways often strip `.html` attachments — zip it or share via a drive link. No telemetry by design, so we cannot tell whether WuXi actually runs it.
- **Not wired into the webapp** — it is a deliverable we hand out, not part of the import flow. The server-side webapp keeps its own (authoritative) detection via `detect_protocol`.

## A mapping template is BOUND TO ONE PROTOCOL — never reuse one (2026-09-17)

**Question that prompted this:** "would `PPB_GT` work for 135303 `ppb UC (10% FBS)`?" **Answer: NO, and
trying it is a silent data-integrity risk, not a clean failure.**

**Evidence (from the API, `GET /vaults/7108/mapping_templates/<id>`):** a template stores
`header_mappings`, and each maps a file column to a specific **ReadoutDefinition id**, which belongs to
exactly one protocol. `PPB_GT` (id 59178) maps `Study number`->1773615, `Date`->1773616, ... all tagged
`protocol_name: "Ppp UC (mouse)"` — i.e. **125963's** readouts (1773615-1773623). Protocol 135303 owns a
disjoint set (1964917-1964925). Identical column NAMES are a coincidence of naming; the binding is by id.

**Why it is dangerous, not just wrong:** `build_payload` sends `{project, mapping_template, autoreject,
runs}` — it **never sends a protocol id**. The template alone decides the destination. So
`--protocol 135303 --mapping-template PPB_GT` would NOT import into 135303; `--protocol` only selects the
config block for the offline header check. The rows would land in **125963 (mouse)**, with no error,
because the columns match perfectly.

**Consequence:** one template per protocol, always. This is also why `HLM_GT` and `MLM_GT` exist
separately despite human/mouse microsomal sharing an identical column set — same root cause, and it was
never written down until now. The five new PPB protocols each need their own template.

**Useful endpoints (read-only, metadata only):** `GET /vaults/<v>/mapping_templates` lists all (67 in this
vault; summary fields only), and `GET /vaults/<v>/mapping_templates/<id>` returns the full
`header_mappings` — the fastest way to check what an existing template actually targets.

## Recipe — build a CDD mapping template with a one-row CSV (2026-09-17)

**Why:** a protocol cannot be imported to until a saved mapping template exists, and the template can
only be built by hand in the CDD web UI. The webapp hides a protocol with an empty `mapping_template`
(`_importable_protocols()`, app.py:73) and refuses READY (app.py:149). A **one-row CSV** is the fastest
way through the wizard: it carries the real header, so every column can be mapped, and it commits one
record instead of hundreds.

**Steps:**

1. Cut one row out of a real report, through the SAME conversion path the webapp uses, so the CSV is
   genuinely import-ready:
   - `read_upload_sheet` -> `normalize_species` (canonical species) -> `split_species` -> pick the unit
   - `reconcile_identifier` + `drop_blank_identifier_rows` -> `write_csv` (UTF-8-BOM)
   - Pick the **most complete row** (fewest blank cells) so every column shows a value in the wizard.
2. Pre-flight it offline: `itp.evaluate_mapping(header, block)` must return `ok=True`, `unmapped=[]`,
   `missing=[]`. This proves the header matches the config block BEFORE touching CDD.
3. In the CDD UI: *Import Data* -> upload the CSV -> pick the protocol -> map each column -> **save as a
   named template** (convention `*_GT`).
4. Put the template name in `config/config.yaml` under that PID, then restart the webapp.

**First use (2026-09-17):** `data/uploads/20260917/2026_09_17_PPBUC_10pctFBS_template_1row.csv` — one
`10%FBS` row (10 of the 114 source rows were 10%FBS after normalisation) cut from
`data/uploads/20260831/2026_08_31_PPBUC_433706_all_corrected.xlsx`, for PID **135303** `ppb UC (10% FBS)`.
Validates `mapping_ok=True`, 10 columns, 8 of 10 populated (the two blanks are free-text comment columns).

**Screenshots of the wizard live in `wiki/img/` and MUST be redacted first.** `wiki/` is NOT gitignored
(only `data/` is), so an unredacted screenshot commits a compound id + its values into the repo, which the
aggregate-only wiki rule forbids. Workflow:

1. Save the raw screenshot under `data/uploads/<date>/` — gitignored, so the original never leaves the machine.
2. Black out the data row with ImageMagick (installed; no Pillow in the env):
   `convert raw.png -fill black -draw "rectangle <x1>,<y1> <x2>,<y2>" wiki/img/<name>.png`
   Keep the green header row, the mapping arrows and the blue panel — those carry no compound data and are
   the whole point of the picture.
3. Check the output by eye, then embed with a path relative to `wiki.md`: `![alt](img/<name>.png)`.

**CAUTION — the row commits as real data.** It is a genuine 10%FBS measurement, so it belongs in 135303,
but it will also appear in the full upload later. Either delete that run afterwards (protocol -> Runs tab)
or let the duplicate guard skip it. Check whether the wizard can save a template WITHOUT completing the
commit — if it can, abort after saving and nothing is written at all (unverified).

**Do NOT fabricate rows for the other species.** The 2026-08-31 file holds only Mouse and 10%FBS, so
Human/Monkey/Dog/Rat have no real data yet. Repeat this recipe per protocol when WuXi first sends that
species. Never invent a compound to make a template, and never feed a 10%FBS row to the Human protocol
just to map columns — it would commit wrong data. All six PPB protocols share ONE column set, so each
template is the same mapping, only built per protocol.

## PPB audit — 10%FBS data is MISFILED inside the mouse protocol (2026-09-17)

**Question:** is all of CDD's ppb-mouse data inside `data/uploads/20260831/2026_08_31_PPBUC_433706_all_corrected.xlsx`,
or is there newer data? **Answer: the file is complete and current — but CDD 125963 is holding data that
does not belong to it.**

- CDD **125963** holds **114 distinct compounds** across **27 runs** (142 protocol-data rows).
- The 2026-08-31 file holds **114** too: **104 `Mouse` + 10 `10%FBS`**.
- Set comparison: **in both 104 / only-in-CDD 10 / only-in-file 0**, and the 10 "only in CDD" are EXACTLY
  the file's 10 `10%FBS` compounds. `cdd_ids <= (mouse | fbs)` is **True** — the file covers CDD 100%.
- **Nothing is missing, and there is no new data:** the newest run carrying rows is **2026-08-28**, before
  the file was built. (Counts only; no compound ids left the machine.)

**The real finding: the 10 `10%FBS` compounds sit in protocol 125963 (mouse).** They were uploaded there
because 135303 did not exist yet. They should move to 135303 now that it has a template.

**CORRECTION (2026-09-17) — an earlier version of this entry was WRONG.** It claimed "26 FBS rows across 10
runs, 4 of them MIXED with mouse data". That came from selecting rows by **compound identity** (every row of
any compound in the FBS set), which over-counts: a compound can hold rows for several species. The correct
selector is the stored **Species readout value** (`1773617`). Redone properly:

**Rows in 125963 by stored Species value (142 total):** `Mouse` 117 + `mouse` 3 = **120 Mouse**;
`10%FBS` 10 + `FBS` 6 = **16 FBS**; **`Human` 1, `Monkey` 1, `Rat` 1, `Dog` 1**; 2 rows with a BLANK species.

**So 125963 is holding SIX species, not two.** And every FBS row sits in a run with **zero** Mouse rows:

| Run | Date | Non-mouse rows | Mouse rows |
|---|---|---|---|
| 2108013 | 2026-06-17 | 2 blank-species | 3 (mixed) |
| 2210918 | 2026-08-28 | 1 FBS + 1 Human + 1 Monkey + 1 Rat + 1 Dog | 0 |
| 2210919 / 2210923 / 2210924 / 2210929 | 2026-08-28 | 2 / 6 / 1 / 6 FBS | 0 |

**Deleting the 16 FBS rows touches NO mouse data** — the earlier "4 mixed runs, do not delete" warning was an
artefact of the bad selector. The only genuinely mixed run is 2108013, and it holds blank-species rows, not FBS.

**Human/Monkey/Rat/Dog DATA EXISTS after all (1 row each, run 2210918).** This corrects the earlier claim that
"no data exists for those species" — that was true of the FILES on disk, but CDD itself has one row per species.
**These rows can seed the four remaining mapping templates** via the one-row-CSV recipe, without waiting for WuXi.

**Species matching is now WHITESPACE-INSENSITIVE (2026-09-17).** New `convert_upload.species_key()` folds a
Species cell to lowercase with ALL spaces removed, and both `species_alias_map()` and `normalize_species()` use
it. This matters because the CDD protocol is named `ppb UC (10% FBS)` **with a space**, so `10% FBS` is a very
likely CRO spelling — and it previously did NOT route. Now `FBS`, `fbs`, ` FBS `, `10%FBS`, `10% FBS`,
`10 % FBS` and `10%  fbs` all canonicalise to `10%FBS`; `Mouse`/`mouse`/`MOUSE` all give `Mouse`. An unknown
species (e.g. `hamster`) still passes through untouched so it surfaces for manual review rather than being
mis-routed. This runs on EVERY uploaded file in `webapp/app.py` right after `read_upload_sheet`, so raw WuXi
files are covered, and the corrected value is what gets written to the CSV and stored in CDD.

**Extracted the misfiled non-mouse species back out of CDD (2026-09-17):**
`data/uploads/20260917/2026_09_17_PPBUC_other_species_all_corrected.xlsx` — an `Upload`-tab workbook holding
the **4 rows** for `Human`/`Monkey`/`Rat`/`Dog` pulled from 125963, with the standard PPB corrections applied
(`%Unbound` NA->0, `%Bound` NA->100, 1-decimal rounding; 0 NA fills were actually needed). Verified through the
real pipeline: detects `species_group` conf 1.0 and splits into 4 units, each routing to its own protocol with
`mapping_ok=True` — the only blocker is the missing template. Per-species one-row CSVs are in
`data/uploads/20260917/processed/`, so **each of the 4 remaining templates can be built right now** with the
one-row recipe; no need to wait for WuXi. **Still unresolved: the 2 blank-Species rows** (run 2108013,
2026-06-17) — `split_species` drops blank-species rows, so they route nowhere and need a manual decision.

**Backups taken before any delete (2026-09-17)** — `data/uploads/20260917/backup_125963_nonmouse_*.csv`, one file
per species (10%FBS 16, Human/Monkey/Rat/Dog 1 each, unknown 2 = 22 rows). Gitignored; written straight to disk,
contents never printed.

**Programmatic deletion is supported** (the web UI was failing for this): `DELETE /vaults/<v>/readout_rows/<id>`
removes ONE readout row, and `DELETE /vaults/<v>/runs/<id>` removes a whole run. Each protocol-data row's `id`
IS the readout-row id, so `protocol_rows()` already gives the handles. New tool **`python/delete_protocol_rows.py`**
selects by Species value, is **DRY-RUN by default** (`--execute` required), writes a backup CSV before the first
delete, and keeps stdout metadata-only.

**Also: run 2228508 (2026-09-17) has ZERO data rows** — an empty run, almost certainly created by the
mapping-template wizard ("Create a new run") while building `PPB_10%FBS_GT`. Harmless, but worth deleting.
Watch for this after every template build.

**Split CSVs produced (2026-09-17)** in `data/uploads/20260831/processed/` — the same pattern as the MMS
Human/Mouse split: `..._all_corrected_Mouse.csv` (104 rows -> 125963, `PPB_GT`) and
`..._all_corrected_10pctFBS.csv` (10 rows -> 135303, `PPB_10%FBS_GT`). Both `mapping_ok=True`, 0 dropped.
The webapp does this split in memory automatically; these files are for the CLI path.

## 132956 `Reworked - PPB (UC)` is WIDE per-species — it cannot be the all-species protocol (2026-09-17)

The plan was to point a species-agnostic "holds all PPB data" protocol at **132956**. Its actual shape
blocks that. `--describe-protocol 132956` returns **12 readouts in a WIDE, per-species layout**:

- `%Unbound - Mouse`, `%Bound - Mouse`, `%Remaining - Mouse`
- `%Unbound - Human`, `%Bound - Human`, `%Remaining - Human`
- `Study number`, `Date`, `Species`, `Provider Comment`, `Provider Name`, `Serac Comment`

**Three blockers:**

1. **It only knows Mouse and Human.** `Species` is a **required PickList** whose only values are `Human`
   and `Mouse`. There are no columns for Monkey, Dog, Rat or 10%FBS, so it structurally cannot hold "all
   PPB data irrespective of species", and a `10%FBS` value would be rejected by the picklist.
2. **A raw WuXi PPB file cannot map to it.** The file is LONG (one `%Unbound` column + a `Species` column);
   132956 is WIDE (one column per species). One file column cannot map to two readouts, so a
   **long->wide pivot** is required first (the `convert_dataset.convert_to_target_format` pattern used for
   MDR1) — new conversion code, not just a template.
3. **Its numeric readouts are `data_type: Number`** (2 decimal places), unlike 125963/135303 where every
   readout is `Text`. A Number field REJECTS text, so any `NA` triggers the
   "Ignore text value in numeric fields" rejection we already hit on MDR1.

**Conclusion:** a species-agnostic catch-all wants a LONG-format protocol with the SAME 9 readouts as the
per-species ones. Then the identical CSV uploads to it unchanged (no pivot), and it accepts any species
because `Species` is just a cell value. 132956 is a different thing — a Mouse-vs-Human comparison table.

**DECISION (2026-09-17): PARKED.** The all-species/catch-all idea is dropped for now — we stay with the
per-species protocols only (125963 mouse + 135303 10%FBS). **Config was NOT changed**, and 132956 is not
referenced by config. If it is revived, note the catch-all mechanism is sound but is NOT config-only: the
webapp emits exactly one unit per species today, so an extra all-rows unit needs a code change in
`webapp/app.py` and `check_cdd_commit.load_units` plus a flag on the catch-all block — and the target should
be a NEW long-format protocol (same 9 readouts), not 132956.

## Duplicate handling on re-upload — what actually happens (2026-09-17)

**CDD never overwrites.** A slurp always creates a **NEW RUN**; there is no upsert or merge. Uploading the same
compound twice leaves it with two runs, and the older one has to be deleted by hand.

**The webapp's guard is PER UNIT and ALL-OR-NOTHING.** `_already_uploaded()` (app.py) returns True only when
`checked > 0 and not missing and not mismatch` — i.e. EVERY compound in the unit is already in CDD **and**
every compared value matches. Then the row shows orange `already uploaded` and is skipped on submit.

**If even ONE row is new, or ONE value differs, the WHOLE unit uploads** — including the rows that were already
there, which then duplicate. It does **not** filter row-by-row. That is the main trap: a mostly-duplicate unit
with one changed value re-uploads everything.

**Observed live (2026-09-17)** on `2026_09_17_PPBUC_other_species_all_corrected.xlsx`, after the 4 templates
were built (building a template COMMITS its one-row CSV — expected, and that row is genuine data):

| Species | In target protocol | Status | Why |
|---|---|---|---|
| Human | 1 | ALREADY UPLOADED (skipped) | all values match |
| Monkey | 1 | ALREADY UPLOADED (skipped) | all values match |
| Rat | 0 | READY | compound absent — the Rat template build did not commit |
| Dog | 1 | **READY (will duplicate)** | `%Unbound` differs by a **rounding-level** gap (<0.05) |

**The Dog case is a lesson:** `data/uploads/20260917/` holds BOTH `backup_125963_nonmouse_*.csv` (raw,
full-precision, straight from CDD) and `processed/*_template_1row.csv` (corrected, rounded to 1 dp). The Dog row
in CDD appears to have come from the RAW backup, so it disagrees with the corrected file at the 2nd decimal and
the guard correctly refuses to call it a duplicate. **Always upload the `processed/` files, never the `backup_`
ones** — the backups exist only as a safety copy before deletion.

**THE 1% TOLERANCE MASKS ROUNDING — this defeats a "re-upload to round the values" plan (2026-09-17).**
The guard compares with `VERIFY_TOL = VERIFY_REL = 0.01`, so a RAW value and its 1-decimal rounding usually
count as EQUAL. Checked the 4 template rows both ways: at 1% tolerance Human/Monkey/Rat show **no** mismatch,
but at EXACT tolerance all four mismatch on `%Unbound`, `%Bound` and `%Remaining` — i.e. **CDD is holding the
RAW values for all four**, and only Dog's `%Unbound` gap is wide enough to exceed 1%.

Consequence: submitting the corrected file **cannot** convert those rows to 1-decimal. Human/Monkey/Rat are
skipped as "already uploaded" and KEEP their raw values; only Dog uploads, and it ADDS a 2nd run rather than
replacing the first. **To actually enforce the rounded values you must DELETE the existing rows first, then
upload** — `delete_protocol_rows.py --protocol <pid> --species <sp> --execute`, then submit the corrected file
so each species lands as a single clean run. (Building the 4 templates committed one row each into
135299-135302, runs 2228599/2228603/2228607/2228610, all dated 2026-09-17.)

**Diagnostic gotcha:** `check_unit()` mismatch entries use the key **`column`** (plus `batch_id`, `file`, `cdd`),
NOT `field`. Filtering on `field` silently yields an empty list and makes a real mismatch look clean.

## BUG in the 2026-08-31 extraction: "latest run per compound" dropped whole species (2026-09-17)

**Prompted by "can we also have latest-run-per-compound for the other species?"** For the other species it was
already effectively satisfied (10%FBS: 16 rows -> 10 distinct compounds, already deduped; Human/Monkey/Rat/Dog
hold 1 row each). But the question exposed a real defect in the original workflow.

**The defect:** the 2026-08-31 extraction keyed "latest run" on the **COMPOUND alone**. A compound measured in
several species therefore kept only its single most-recent run, and every other species' row for that compound
was silently discarded. **7 compounds are measured in more than one species** in 125963 — 6 as
`10%FBS`+`Mouse`, and 1 as `10%FBS`+`Dog`+`Human`+`Monkey`+`Rat`.

**Consequence:** the 2026-08-31 file was missing 10 (compound, species) pairs — the 6 MOUSE rows whose compound's
latest run happened to be an FBS run, plus 4 rows from the multi-species compound. This is the real explanation
for the earlier observation "6 mouse compounds in CDD are absent from the file's Mouse rows".

**The fix: key on `(compound, species)`, not `compound`.** Regenerated as
`data/uploads/20260917/2026_09_17_PPBUC_all_species_latest_corrected.xlsx` — latest run per (compound, species)
across the WHOLE of 125963, with the standard corrections (`%Unbound` NA->0 x11, `%Bound` NA->100 x11, 1-decimal
rounding).

| | old (2026-08-31) | new (2026-09-17) |
|---|---|---|
| (compound, species) pairs | 114 | **124** |
| recovered by the fix | — | **10** (nothing lost: old-minus-new = 0) |

142 CDD rows collapse to 124. Per species: Mouse 110, 10%FBS 10, Human/Monkey/Rat/Dog 1 each. Verified through
the real pipeline: `species_group` conf 1.0, all six units `mapping_ok=True` with a template each.

**The corrected xlsx is NOT a backup — do not treat it as one.** It differs from CDD 125963 two ways:
it is **deduped** (142 CDD rows -> 124: drops 10 Mouse + 6 10%FBS repeat measurements, and the 2 blank-species
rows route nowhere so they vanish) and it is **corrected** (NA fills + 1-decimal rounding), so even the 124 rows
it keeps no longer hold CDD's values.

**Cross-checked against a CDD-UI export (2026-09-17):** the user exported `20260917_PPB_backup.csv` from the
vault UI. Verified EQUIVALENT to our API backup **without either file's values being displayed** — compare by
canonicalising each cell (trim, collapse spaces so `< 0.5`==`<0.5`, numeric re-format so `37.90`==`37.9`,
NA/blank unified), hashing each row with SHA-256, and comparing the multisets. Result: **142 rows each, 111
distinct molecules each, and the full (molecule name + 9 readouts) multisets are IDENTICAL.** This hashing
recipe is the way to diff two chemistry files while honouring the local-only rule.

**Their UI export has two gaps — keep the API backup as the authoritative one for restore:** (1) `CDD Number` is
**blank on all 142 rows**, so it cannot be used as a join key (we matched on `Molecule Name` instead); (2) it
carries **no batch column**, only `Molecule Name`, while 3 molecules here hold more than one batch — those rows
are ambiguous in the UI export. Our API backup keeps `Molecule-Batch ID`, `batch`, `run`, `run_date` and
`readout_row_id`, so it alone supports a row-level restore. Column naming also differs: the UI prefixes each
readout with `Ppp UC (mouse): `, and its `Serac Comment` is our `Comment` (readout 1773622).

**True backup: `data/uploads/20260917/backup_125963_FULL_raw_20260917.csv`** — all **142** rows, RAW values, no
dedup, no corrections, blank-species rows included, and carrying `readout_row_id` + `run` + `run_date` +
`molecule`/`batch` so any row can be traced or restored. **Take a fresh one of these before any deletion.**

**Rule for any future PPB extraction: the dedup key is (compound, species).** Keying on compound alone loses data
whenever a compound is assayed in more than one matrix.

## Corrections are now AUTOMATIC and config-driven (2026-09-17)

**The gap:** the `%Unbound` NA->0 / `%Bound` NA->100 / 1-decimal rules existed only inside one-off scripts (the
2026-08-31 notebook cells and an ad-hoc extraction). The webapp pipeline applied NO value corrections at all, so
a RAW WuXi file would have imported `NA` as literal text with full decimals — silently, because the PPB readouts
are `Text` type and CDD accepts `NA` without an error.

**The fix:** new **`convert_upload.apply_corrections(header, rows, block)`**, driven by a per-protocol
`corrections:` block in config (config over code — add a rule there, never in Python):

```yaml
corrections:
  fill_na:  {"%Unbound": 0, "%Bound": 100}   # empty/NA cell -> this value
  round_to: {1: ["%Unbound", "%Bound", "%Remaining"]}
```

Behaviour: NA means empty/`NA`/`N/A`/`nan`/`none`/`ND`/`N.D.`; fills run BEFORE rounding; a `<`/`>` qualifier is
preserved (`<0.567` -> `< 0.6`); columns absent from the header are skipped silently; the caller's rows are never
mutated; it returns a `(rows, summary)` pair whose summary counts changes per column (counts only, no values).
**`%Remaining` deliberately has no fill rule** — an NA there stays NA, only rounding applies, matching the
2026-08-31 decision.

**Wired into BOTH paths, per unit (after the pid is known, since rules are per protocol):** `_stage_unit` in
`webapp/app.py` (before the CSV is written, and the counts are stored on the unit record) and `_unit` in
`check_cdd_commit.py` — the second is essential, or verification would compare a RAW file against corrected CDD
values and report false mismatches.

**Scope:** the `corrections:` block is on the 6 PPB protocols only. LogD / TSY / MDR1 / microsomal have none and
are provably untouched (a no-op returns the same list object).

**Verified end-to-end** on a synthetic raw file (`TEST-` ids): `NA`->0/100, `95.4321`->`95.4`, `<0.567`->`< 0.6`,
`%Remaining` NA left as NA, and species spellings (`mouse`, `FBS`) still canonicalised first. 10 new unit tests,
59 total, all pass.

## Upload the 5-SPECIES file, not the all-species one (2026-09-17)

**Deletions completed by the user:** 125963 is down to **122 rows (120 Mouse + 2 blank-species)** — the 20
non-mouse rows are gone — and 135299/135300/135301/135302/135303 are all at **0 rows**.

**Trap found in pre-flight:** the all-species file was expected to have its Mouse unit skipped as a duplicate.
It is NOT. `check_unit` on its 110 Mouse rows gives **matched 81, missing 0, and 29 mismatches on `%Unbound`**,
so the unit is READY and would push 110 rows into 125963 — duplicating data we deliberately agreed to preserve.

**Why:** the guard's tolerance is 1% RELATIVE. `%Unbound` values are frequently small (well under 1%), and
rounding a small number to 1 decimal moves it by far more than 1% of itself (0.567 -> 0.6 is ~6%). So the
rounding correction itself pushes ~29 of 110 mouse rows outside the duplicate tolerance. The NA->0 fills add to
this: CDD holds blank where the corrected file holds 0. Re-applying corrections changes nothing here — the
mismatch count is identical with and without them (the 24 "changed" rows are cosmetic, e.g. `0` -> `0.0`).

**Lesson: a relative tolerance is the wrong guard for a near-zero readout.** The duplicate check is reliable for
mid-range values and unreliable for small ones. Do not rely on it alone to protect a protocol from a re-upload.

**Fix: `data/uploads/20260917/2026_09_17_PPBUC_5species_latest_corrected.xlsx`** — the same corrected extract with
the Mouse rows removed: **14 rows** (10%FBS 10, Human/Monkey/Rat/Dog 1 each). Pre-flight confirms all 5 units
READY, `mapping_ok=True`, each target protocol empty, 14 rows sent, and **125963 untouched**. Use this file.
The all-species file stays as the complete corrected record, but must NOT be dropped into the webapp as-is.

## Upload done + what DIFF means in the comparison table (2026-09-17)

**The all-species file was uploaded as-is (user experiment).** Result matches the prediction exactly:

| protocol | rows before | rows after | new run |
|---|---|---|---|
| 125963 mouse | 122 | **232** | 2228663 |
| 135299 Human / 135300 Monkey / 135301 Dog / 135302 Rat | 0 | 1 each | 2228661 / 2228662 / 2228660 / 2228664 |
| 135303 10%FBS | 0 | 10 | 2228658 |

125963 now holds **230 Mouse rows but still only 110 distinct compounds** — so the UI grid is unchanged at 110
rows and now displays the NEW (corrected) values, because it shows one row per compound and the new run is the
latest. **The 120 original rows are still there, just invisible in that view.** To undo only this upload, delete
run **2228663** (the snapshot `runs_before_upload_*.json` is what identified it).

**CDD-UI vs API row counts:** the protocol-data grid collapses to one row per compound; the API returns every
readout row. That is why the UI showed 110 while the API reported 120.

**What DIFF means (`compare_value` in check_cdd_commit):**

| CDD cell | verdict | shown |
|---|---|---|
| **empty / missing** | `False` | **DIFF** |
| non-numeric text (`NA`, `N/A`) | `None` | blank — not comparable |
| numeric | `True`/`False` | `ok` / `DIFF` |

So a DIFF next to a numeric file value means CDD's cell is **EMPTY**, not that it holds `NA`. A literal `NA` in
CDD is skipped, because text cannot be compared with a number. In 125963 there are **2 empty** `%Unbound` cells
and **11 holding the text `NA`** — only the 2 empty ones can produce a DIFF. This matters because the PPB
readouts are `Text` type, so a raw import stores `NA` as text rather than leaving the cell blank.

## PPB migration COMPLETE — final state and how the mouse cleanup was done (2026-09-17)

**Final state of the PPB family:**

| PID | protocol | rows | compounds | template |
|---|---|---|---|---|
| 125963 | ppp UC (mouse) | 112 (110 Mouse + 2 blank-species) | 110 | `PPB_GT` |
| 135299 | ppb UC (Human) | 1 | 1 | `PPB_HUMAN_GT` |
| 135300 | ppb UC (Monkey) | 1 | 1 | `PPB_MONKEY_GT` |
| 135301 | ppb UC (Dog) | 1 | 1 | `PPB_DOG_GT` |
| 135302 | ppb UC (Rat) | 1 | 1 | `PPB_RAT_GT` |
| 135303 | ppb UC (10% FBS) | 10 | 10 | `PPB_10%FBS_GT` |

**How the mouse duplication was resolved — keep the new run, delete the old rows.** After the all-species upload
left 125963 at 232 rows (120 old + 110 new), the user verified all 110 new rows against the old ones. Rather than
wiping and re-uploading, we deleted only the OLD rows and KEPT the freshly uploaded run 2228663, using a new
**`--exclude-run`** flag on `delete_protocol_rows.py`. 120 rows across 21 runs went; 112 remain. **This pattern is
strictly better than delete-then-reupload:** one step, no window with no data, no second import to re-verify, and
the surviving rows are the exact ones already checked.

**What the repeat measurements actually were (corrects an earlier overstatement).** The 10 mouse compounds with
2 rows each were NOT 10 genuine repeats: **9 were two rows in the SAME run holding IDENTICAL values** (an import
that double-entered), and only **1** was a genuine re-measurement (2026-03-25 -> 2026-08-28). So the real cost of
the wipe was 1 historical data point plus the run structure, not 10 measurements. Each row keeps its experimental
date in the `Date` readout, so the dating survives as data even though the 21 runs do not.

**Leftover: 25 EMPTY runs in 125963** (and 1 in 135299) — run records whose rows were all deleted. Harmless, but
they clutter the Runs tab; delete them in the CDD UI if you want it tidy. Watch for these after any row-level
deletion, and after each template build (the wizard creates a run even when nothing commits).

**Still open: the 2 blank-Species rows in 125963** (run 2108013, 2026-06-17). They cannot be selected by species,
`split_species` drops them on upload, and they route nowhere. They need a human decision.

**Backups taken through the day, all in `data/uploads/20260917/` (gitignored):**
`backup_125963_FULL_raw_*.csv` (142 rows, pre-cleanup), `backup_125963_PREUPLOAD_*.csv` (122 rows),
`backup_125963_PREDELETE_*.csv` (232 rows, both old and new), `backup_125963_nonmouse_*.csv` (per species), plus
`runs_before_upload_*.json` — the run-id snapshot that let us identify exactly which run an upload had created.
**Taking a run-id snapshot before an upload is the reliable way to make that upload undoable.**

## Pre-flight of `reports_with_no_issues` — no duplication risk, one file unroutable (2026-09-17)

Checked the 9 corrected WuXi reports in `data/uploads/20260917/reports_with_no_issues/` (the fixed versions of
the 20260914 failures) for duplication risk. Method: full pipeline per unit — read -> normalise species ->
detect -> split -> reconcile identifier -> drop blank ids -> apply corrections -> `check_unit` against live CDD,
counting matched / missing / mismatched compounds.

**Result: NO duplication risk in any file.** Every unit is cleanly all-new or fully-already-present; **not one is
PARTIAL**, which is the only dangerous shape (the guard is all-or-nothing, so a partial unit re-uploads its
already-present rows).

| outcome | units |
|---|---|
| all new -> clean upload | 8 microsomal units (88861 Human / 88862 Mouse) + 1 MDR1 file |
| already uploaded -> skipped | 2 MDR1 files (4 and 2 compounds, all matching) |
| **no route** | 1 unit — see below |

**The exception: `2026_08_26_MMS_433706-2026080702-MMS_v1_Report_0 update.xlsx` is a RAT file.** Its single row
has `species = Rat`, and detection returns `species_pids = {Human: 88861, Mouse: 88862}` only, because those are
the only microsomal blocks carrying a `species:` field. So the row routes NOWHERE and is silently dropped — it
cannot duplicate anything, but it also cannot import.

**To enable RLM (PID 111161) the config block must be filled in** — today it is extraction-only (`name`, `alias:
rlm`, `project`). CDD already defines its 10 readouts, and the protocol already holds **39 rows / 39 compounds
across 5 runs, last touched 2025-11-14** (dormant since). The file's compound is **new** to it, so importing
would not duplicate. Needed: `species: Rat`, `identifiers: {batch_id: "Molecule-Batch ID"}`, `label`, the readout
map, and a `mapping_template` built with the one-row recipe.

**Name mismatches to handle in that mapping (file column -> CDD readout):** `T½ (min)` -> `T½` (1476624),
`CLint (raw) (μL/min/mg)` -> `CLint (raw)` (1476625), `species` -> `Species` (1476623), `Comment` ->
`Serac Comment` (1476629). **CAUTION: `T½`, `CLint (raw)` and both `Remaining` readouts are `data_type: Number`**
on 111161 (unlike the Text-typed PPB readouts), so any `NA` would be REJECTED rather than stored as text — a
`corrections:` block is advisable there. Its `Species` PickList is `required` and allows only `Rat`.

## 9 slurps AUTO-REJECTED on 2026-09-17 — one cause found, microsomal still open

**Symptom:** all 9 submitted units showed `VERIFY FAILED (0 MISMATCH - N MISSING)` in the webapp. CDD had created
a run for each (3 in 88861, 5 in 88862, 1 in 131399) but **every run holds 0 rows**. CDD emailed:
*"The import ... contains at least one error or suspicious event and was rejected automatically"*, linking
slurp 3651748 (`state=rejected`, `records_processed=2`, `records_committed=0`, `import_errors=2`).

**`autoreject=true` is why one bad row kills the whole file** — `build_payload` sets it, so a single error rejects
every record. CDD's email suggests `autoreject=false` to leave the slurp in accept/reject state for inspection.
Worth adding as an option when diagnosing.

**CONFIRMED cause (1 of 9): `433706-2026090401-MDR1_v3_report_1.xlsx` has `NA` in `Efflux ratio - PgP inhibitor`.**
131399's readouts are `Number`, so text is rejected — the identical failure as the 2026-09-14 MDR1 file. The
`static/verify_file.html` checker already flags exactly this, and WuXi shipped the file anyway.

**ROOT CAUSE FOUND (user spotted it): WuXi wrote the MOLECULE id, not the MOLECULE-BATCH id.** Every MMS
identifier is shaped `SRB-NNNNNNN` — **the trailing `-NNN` batch suffix is missing** (29/29 values). The MDR1
files are correct: `SRB-NNNNNNN-NNN` (10/10). CDD therefore reports *"Record rejected because your database does
not contain a molecule corresponding to SRB-XXXXXXX"*. Confirmed from both ends: **all 15 referenced molecules
EXIST in the vault, but 0 of 15 match an actual batch identifier.**

**METHOD WARNING — `GET /batches?molecule_batch_identifier=` is a PREFIX match, not exact.** An earlier check
here used `count > 0` and wrongly concluded "all ids registered". `?molecule_batch_identifier=SRB` returns 16760
rows. **Always compare the returned `molecule_batch_identifier` for equality**, never trust the count.

**Fixing it locally is only partly safe:** of the 15 molecules, **12 have exactly ONE registered batch**
(appending that suffix is unambiguous) but **2 have 2 batches and 1 has 3** — for those, guessing which batch was
assayed could attach data to the wrong batch. **Preferred fix: WuXi resends with correct Molecule-Batch IDs**,
since they hold the source of truth.

**This is exactly what `static/verify_file.html` should have caught.** The checker currently verifies the
identifier COLUMN exists but not the VALUE FORMAT. Adding an `SRB-XXXXXXX-NNN` shape check would have stopped
this file at WuXi's desk.

**Cleanup pending: 9 EMPTY runs** created by the rejected slurps (88861: 2228698/2228700/2228704, 88862:
2228699/2228701/2228702/2228705/2228706, 131399: 2228703).

## Two guards added against the truncated-batch-id failure (2026-09-17)

**1. CRO-side — `static/verify_file.html` now validates the identifier VALUE, not just the column.**
`BATCH_ID_RE` (`^[A-Za-z]+-\d+-\d+$`) must match every identifier; `MOLECULE_ONLY_RE` (`^[A-Za-z]+-\d+$`)
detects the specific mistake and names the fix per value ("SRB-XXXXXXX" should be "SRB-XXXXXXX-001"). It is a
BLOCKING check, and it reports up to 3 offending values. Verified: all 6 truncated MMS files now FAIL, both
correct MDR1 files PASS, and the `NA` MDR1 file still fails on its numeric check. Regression suites unchanged
(9 real 20260914 files + 9 synthetic). Send WuXi the updated file.

**2. Serac-side — the webapp refuses to submit a unit whose batch ids are not in CDD.**
New `check_cdd_commit.missing_batches(session, vault, mbids, cache)` compares the returned
`molecule_batch_identifier` for EQUALITY (the endpoint is a prefix match — see the warning above) and returns
the unregistered ids. `webapp/app.py` calls it via `_unregistered_batches()` in `_stage_unit`; any unknown id
forces `status="unknown-batches"`, which `updateGate()` treats as unresolved, so **Submit stays disabled**. The
row shows a red `unknown batch id` tag naming up to 3 offenders and the `-001` hint. A per-request `batch_cache`
means each id is looked up once. **Fail-safe: a missing token or network error returns [] and never blocks** —
the guard only blocks on a definite negative. app.js cache-buster bumped to `?v=2026-09-17-1`.

Verified on the real files through the actual staging path: the truncated MMS unit -> `unknown-batches` (2 ids,
submit blocked); the good MDR1 file -> `already-uploaded` (0 unknown). Together these two guards stop this class
of failure at the CRO's desk AND at ours, instead of at CDD after 9 wasted slurps.

## Email UNBLOCKED — Microsoft Graph OAuth2 transport built (2026-09-18)

**IT confirmed the diagnosis we reached on 2026-07-20:** *"Your python script is using SMTP ... Microsoft has
deactivated it per default on all M365 tenants. Microsoft does support modern authentication methods like OAuth2
... Microsoft Graph (mail.send) and App registration."* That is exactly option (1) recorded back then, so the
SMTP path is a dead end by tenant policy, not a bug.

**Built: `transport: graph` in `notify.py`.** `_send()` now dispatches — `graph` -> `_send_graph()`, anything
else -> the original SMTP path (kept, unchanged). New helpers: `_graph_token()` (OAuth2 **client-credentials**,
app-only, so no user sign-in and no MFA prompt) and `_secret()` (one-line chmod-600 file). **No new dependency** —
`requests` was already required.

- token: `POST https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token`, `grant_type=client_credentials`,
  `scope=https://graph.microsoft.com/.default`
- send: `POST https://graph.microsoft.com/v1.0/users/<sender>/sendMail`, `Authorization: Bearer <token>`;
  **success is HTTP 202** with no body
- **Graph carries ONE body part**, so the HTML rendering is sent when present, else plain text (SMTP still sends
  true multipart/alternative). Comma-separated recipients are split into several `toRecipients`.
- Same hard contract as before: **never raises**. A missing/blank credential, a rejected token, or a non-202
  response logs a WARN and returns False, so a mail problem can never break or roll back an import.

**Config (`notifications:`):** `transport: graph`, `tenant_id`, `client_id`, `client_secret_file:
~/.auto_graph`. **The secret lives in a chmod-600 file, never in config** — same rule as `~/.cdd_token`. The
legacy `smtp_host`/`smtp_port`/`password_file` keys stay for reference.

**Still TODO — waiting on IT for three values** (`tenant_id`, `client_id`, and the **client secret**, which IT's
email did not mention but the app-only flow requires), plus **admin consent** on the `Mail.Send` APPLICATION
permission. **Security note to pass to IT: `Mail.Send` application permission grants send-as for EVERY mailbox in
the tenant unless scoped.** Ask them to restrict it to `auto@seracbio.com` with an Application Access Policy
(`New-ApplicationAccessPolicy`). Until the values land, `--test-notification` exits 1 with a precise WARN naming
which of the three is missing.

**Tested with a fully stubbed `requests`** (9 tests, no network, no secrets): 202 -> True, correct sendMail URL
for the sender mailbox, client-credentials grant + `.default` scope, Bearer header, HTML preferred over text,
text fallback, multi-recipient split, and soft-fail on both a 403 send and a 401 token. 69 tests total.

## Rat microsomal stability (RLM, PID 111161) configured for import (2026-10-29)

**Trigger:** WuXi sent `data/uploads/20261029/2026_08_26_MMS_433706-2026080702-MMS_v1_Report_0 update.xlsx`
— 1 Upload row, `species` = **Rat**. 111161 was an **extraction-only** block (alias + PID only), so the
file had nowhere to go.

**Finding — rat and mouse are STRUCTURALLY IDENTICAL.** `--describe-protocol` on 111161 vs 88862 returns
the same 10 readouts, same names, same data types, in the same order. Only the readout **ids** differ:

| Readout (CDD name) | Type | mouse 88862 | rat 111161 |
|---|---|---|---|
| Study number | Text | 1113188 | 1476621 |
| Date | Text | 1113189 | 1476622 |
| Species | PickList | 1113190 | 1476623 |
| T½ | Number | 1113191 | 1476624 |
| CLint (raw) | Number | 1113192 | 1476625 |
| Remaining (T=60min) | Number | 1113193 | 1476626 |
| Remaining (No co factor=60min) | Number | 1113194 | 1476627 |
| Provider Comment | Text | 1113195 | 1476628 |
| Serac Comment | Text | 1113196 | 1476629 |
| Provider Name | Text | 1113197 | 1476630 |

The config block is therefore a straight copy of the mouse block with the rat ids, `alias: rlm`,
`label: RLM`, `species: Rat`. Config keys stay the **file's** column spellings (`species`, `T½ (min)`,
`CLint (raw) (μL/min/mg)`, `Comment`), exactly as in the mouse block — CDD's own names differ and that is
fine, the id is what binds. The file column set matches too: 11 columns, same names, no `Run Lab`.

**Species picklist on 111161 holds exactly one value, `Rat`** — so a mouse or human row sent there would
be rejected. The file's only species value is `Rat`. Good.

**Detection now returns a 3-WAY species group.** `detect_protocol.py` scores 88861 / 88862 / 111161 all at
recall 1.00 / precision 1.00 — identical readouts cannot be told apart by columns. The tie resolves on the
`species:` field exactly as the 2-way human/mouse case already did, with no code change:
`species_pids={'Human': 88861, 'Mouse': 88862, 'Rat': 111161}`.

**Pre-flight, all green (metadata only):** 4 Number columns carry 0 non-numeric tokens (no literal `NA`, so
**no `corrections:` block is needed** — unlike PPB); batch-id format `1/1`; `missing_batches` → `0` missing
in CDD; the real import path (`read_upload_sheet` → `normalize_species` → `split_species` →
`reconcile_identifier` → `drop_blank_identifier_rows` → `apply_corrections`) maps **11/11 columns**,
nothing unmapped, nothing missing.

**`RLM_GT` BUILT AND LIVE (2026-10-29).** The template did not exist (none of the vault's 75 was a rat
one), so `mapping_template:` was held commented out on purpose — naming a template that does not exist
makes the webapp show the unit READY and then fail at CDD with HTTP 422. The one-row-CSV recipe above was
used: `data/uploads/20261029/2026_10_29_MMS_Rat_template_1row.csv` (UTF-8-BOM, 11 columns, 9 of 11
populated — the 2 blanks are `Provider Comment` and `Comment`, free text, same as PPB). The operator
uploaded it and saved `RLM_GT`; config now carries `mapping_template: "RLM_GT"`. **111161 is fully
importable.** All 12 importable protocols now have a template. Restart the webapp to pick it up.

**No duplicate-commit CAUTION this time.** The source report holds exactly ONE row, so the template row
*is* the whole dataset — building the template commits the only measurement we want anyway. Nothing to
delete afterwards. **CORRECTED 2026-10-29:** this section first said 111161 held "5 runs, all with 0 rows"
and that "no rat data has ever been committed". **Both were wrong** — an artifact of reading `readout_rows`
off the runs endpoint, which never expands it (see the method CAUTION below). 111161 actually held **39
rows** already and has **no empty runs**. Re-checked after the upload: **40 rows across 40 distinct
batches — one row per compound, no duplicate.** The compound we uploaded was genuinely new to the
protocol, so the wrong claim caused no harm, but the method was unsound.

**The same filename already sits in `data/uploads/20260917/reports_with_no_issues/` — it is NOT the same
file.** Different md5. Diffed column-by-column: the ONLY difference is `Molecule-Batch ID`. The old copy
has the **truncated** id (format check 0/1, missing in CDD 1/1); the new copy is correct (1/1, 0 missing).
So the 2026-10-29 file is **WuXi's fix of the truncated-batch-id defect** from 2026-09-17, and it
supersedes the older copy. **Use the 20261029 copy; never upload the 20260917 one.**

**Bug fixed in `--check-mapping` (xlsx path).** `read_header()` read the workbook's **active sheet** and
skipped the OOXML unescape, so the diagnostic disagreed with the importer: it reported 3 false UNMAPPED
columns (`CLint (raw)_x000D_\n(μL/min/mg)` etc.) for the rat file, and read a 1-column `Summary Report`
tab for an older mouse file. It now calls `convert_upload.read_upload_sheet` — the same reader the import
uses — and falls back to the active sheet only when the workbook has no `Upload` tab. CSV path unchanged.
`--check-mapping --protocol 111161` now returns `mapped=11, mapping_ok=True`. All **69 tests still pass**.

## CRO checker needed NO logic change for rat — only a label (2026-10-29)

**Question asked:** does `static/verify_file.html` need updating now that rat MMS is importable?

**Answer: no logic change.** The checker validates **column names**, not species, and human / mouse / rat
MMS ship an identical column set. Run against the real rat report the unchanged checker already returned
**7/7 pass**: Upload sheet, assay detected at 100% of columns, identifier present, full batch ids,
all endpoint columns present, no extra columns, numeric columns numeric.

**Independent confirmation of the batch-id guard.** Run against the SUPERSEDED 2026-09-17 copy of the same
report, the checker **failed it** with the right message — "1 value(s) are a MOLECULE id with no batch
suffix — add the trailing -001". So the guard added on 2026-09-17 would have caught the defect at WuXi's
desk before the file was ever sent. First real-world confirmation that the guard works on a true positive.

**One cosmetic fix made.** The MMS entry read `name:"Microsomal stability (Human + Mouse)"`, so a rat file
passed but displayed as "Human + Mouse" — WuXi could read that as a mis-detection and report a false
problem. Renamed to **"Microsomal stability (Human / Mouse / Rat)"**. The in-page spec list renders from
`PROTOCOLS`, so it picked the new name up automatically. Nothing else changed; re-audited after the edit:
still **zero** "CDD", the only two "Serac" strings are the literal MDR1 column names `Serac Comment` /
`Serac Comment - PgP inhibitor` (renaming them would break validation), **zero** network primitives,
no storage, 23,779 bytes. Still safe to share. **Re-send this build to WuXi.**

**CAUTION — the scratch test harness goes stale silently.** `verify_core.mjs` is a COPY of the HTML's
script block (lines 115-329, the pure-logic part; 330+ is DOM). The copy on disk predated the numeric-column
and batch-id guards, so it would have "passed" a test of code that is not what we ship. **Always regenerate
it from the HTML before trusting a run:**
`{ sed -n '115,329p' static/verify_file.html; echo 'export { readXlsx, analyze, norm, scoreAll, PROTOCOLS };'; } > verify_core.mjs`
Then `node test_verify.mjs` (9 real files in `data/uploads/20260914`). **A stale EXPECT entry was also
found and corrected:** the MDR1 file was recorded as `verdict:"pass"` from before the numeric check
existed, but it carries the literal `NA` in `Efflux ratio - PgP inhibitor` that the check was added for —
the correct expectation is `fail`. After both fixes: **ALL 9 FILES MATCH EXPECTATIONS.**

## CANONICAL microsomal-stability readout spec — use it to create Monkey + Dog (2026-10-29)

**Trigger:** `data/uploads/20261029/2026_10_25_MMS_433706-2026100701-MMS_2_report_0.xlsx` — 11 columns,
2 rows, species **Dog** and **Monkey**. Neither protocol exists in the vault (checked all 50: only
`ppb UC (Monkey)` 135300 and `ppb UC (Dog)` 135301, which are PPB, not microsomal). They must be created.

**Verified: human 88861, mouse 88862 and rat 111161 are IDENTICAL** — not just the names and types, but
`unit_label`, `precision_type`, `precision_number` and `required` as well. Compared field-by-field via the
API. Only the readout **ids** and the **Species picklist** differ per protocol. **One cosmetic nuance:**
human lists the two `Remaining …` readouts at positions 9-10 instead of 6-7. Display order only — a
mapping template binds column → readout **id**, never position — so it does not affect import.

**The canonical spec (copy it exactly for Monkey and Dog):**

| # | Readout name | Type | Unit | Precision | Required |
|---|---|---|---|---|---|
| 1 | Study number | Text | — | — | yes |
| 2 | Date | Text | — | — | yes |
| 3 | Species | PickList | — | — | yes |
| 4 | T½ | Number | `min` | 3 | yes |
| 5 | CLint (raw) | Number | `μL/min/mg` | 3 | yes |
| 6 | Remaining (T=60min) | Number | `%` | 3 significant figures | yes |
| 7 | Remaining (No co factor=60min) | Number | `%` | 3 significant figures | yes |
| 8 | Provider Comment | Text | — | — | no |
| 9 | Serac Comment | Text | — | — | no |
| 10 | Provider Name | Text | — | — | yes |

**CAUTION — the Species picklist holds ONE value per protocol.** Rat's holds only `Rat`, mouse's only
`Mouse`. Give the Monkey protocol only `Monkey` and the Dog protocol only `Dog`. A row of the wrong
species sent there is rejected, which is the safety property that makes the species split trustworthy.

**CDD readout names are NOT the file's column names, and that is correct.** The file ships `T½ (min)` and
`CLint (raw) (μL/min/mg)` (unit in the header); CDD carries the unit in `unit_label`, so the readout is
just `T½`. The file's `Comment` maps to CDD's `Serac Comment`, and the file's `species` is lowercase.
Config keys are always the **file's** spelling; the readout **id** is what binds. Same pattern as the
other three.

**Pre-flight of the Dog/Monkey file, all green:** column names **byte-identical** to the rat file and to
all three config blocks; 4 Number columns hold 0 non-numeric tokens (no `corrections:` block needed);
batch-id format 2/2; `missing_batches` → 0; the only blank cells are `Provider Comment` and `Comment`,
both optional. The CRO checker passes it 7/7.

**Until the protocols exist the webapp is SAFE but cannot route them.** `detect_protocol` returns
`species_group` with `species_pids={'Human','Mouse','Rat'}`; Dog and Monkey find no pid (app.py:272), so
those units fall through to the dropdown as `needs-selection` and Submit stays disabled. No mis-routing is
possible — nothing to guard against, just nothing to pick yet.

**Split per species, ready to upload (2026-10-29):** the source report was split in memory through the
same path the webapp uses (`read_upload_sheet` -> `normalize_species` -> `reconcile_identifier` ->
`split_species` -> `drop_blank_identifier_rows` -> `write_csv`), giving
`..._MMS_2_report_0_Dog_upl.csv` and `..._MMS_2_report_0_Monkey_upl.csv` in `data/uploads/20261029/`
(`<base>_<species>_upl.csv`, the `split_species.py` convention). Each is UTF-8-BOM, 11 columns, 1 row,
9 of 11 populated, header identical to the source; 2 split rows account for 2 source rows with no loss.
No identifier rename was needed (MMS keeps `Molecule-Batch ID`; only MDR1 renames). The rat block stood in
for the conversion because the Dog/Monkey blocks do not exist yet — the script **asserts** all three MMS
blocks share one identifier, one column set and no `corrections:` before relying on that.
**Because each species has exactly 1 row, each CSV is both the template seed and the full dataset** — build
the template from it and nothing needs deleting afterwards, same as rat.

**Remaining steps once the two protocols exist:** read back their readout ids
(`--describe-protocol <pid>`), add two config blocks modelled on 111161 (`alias`/`label`, `species: Monkey`
/ `species: Dog`), build one mapping template **per protocol** (never reuse — a template is bound to one
protocol), then uncomment `mapping_template:` and restart the webapp. Suggested names `MKLM_GT` / `DLM_GT`,
following `HLM_GT` / `MLM_GT` / `RLM_GT`.

## GOTCHA — a COPIED protocol keeps the source's Species picklist (2026-10-29)

**Symptom:** the first import into the new dog protocol **137630** `Microsomal stability species dog` was
rejected with `Readout "Dog" rejected. Value must match readout definition pick list.`

**Cause:** 137630 was created by copying the rat protocol, and the copy **carried rat's picklist value
across**. Its `Species` readout held `['Rat']`, so the file's `Dog` matched nothing. Everything else was a
perfect copy — names, types, `unit_label`, `precision_type`, `precision_number`, `required` and even the
readout ORDER all match the canonical spec exactly. The picklist was the single defect.

**Fix:** in the CDD UI, edit the `Species` readout definition on the new protocol and replace `Rat` with
the right species. **This is safe** — picklist values are per-readout objects, not shared: rat's `Rat` is
value id 306208 on readout 1476623, dog's `Rat` was id **401123** on readout 2015336. Editing one cannot
reach the other. Verified via the API before recommending the edit.

**EXPECT THE SAME ON MONKEY.** If the monkey protocol is also created by copying an existing one, its
picklist will hold the source's species too. Set it to `Monkey` BEFORE the first import. Generally: after
copying any protocol, check the picklist first — `--describe-protocol <pid>` does not print picklist
values, so read it from the API (`GET /vaults/7108/protocols/<pid>` → `readout_definitions[].pick_list_values`).

**No cleanup needed from the rejection.** The reject was clean — no partial data landed. (The "1 run with
0 rows" reading came from the unreliable `readout_rows` field; after the picklist fix the SAME run 2268661
holds the 1 expected row, so CDD populated the existing run rather than making a second one.)

**Config block added for 137630** (`alias: dlm`, `label: DLM`, `species: Dog`, readout ids 2015334-2015343).
`mapping_template:` stays **commented out** until `DLM_GT` is built — no microsomal dog template exists
(the only dog/monkey templates in the vault are `PPB_DOG_GT` / `PPB_MONKEY_GT`, which are PPB). Detection
now returns a **4-way** species group `{'Human': 88861, 'Mouse': 88862, 'Rat': 111161, 'Dog': 137630}`;
`--check-mapping --protocol 137630` against the Dog CSV gives `mapped=11, mapping_ok=True`. 69 tests pass.

## Microsomal stability is now a FIVE-species split — all live (2026-10-29)

**Two new protocols, both importable:**

| PID | Protocol | alias | label | species | template |
|---|---|---|---|---|---|
| 88861 | Microsomal stability species human | hlm | HLM | Human | `HLM_GT` |
| 88862 | Microsomal stability species mouse | mlm | MLM | Mouse | `MLM_GT` |
| 111161 | Microsomal stability species rat | rlm | RLM | Rat | `RLM_GT` |
| **137630** | Microsomal stability species dog | **dlm** | DLM | Dog | **`DLM_GT`** (62108) |
| **137631** | Microsomal stability species Monkey | **clm** | CLM | Monkey | **`CLM_GT`** (62110) |

`clm` = cynomolgus, matching the operator's template name. Note 137631's CDD name capitalises
**M**onkey where the other four are lowercase — cosmetic only, `name:` carries the exact CDD string.
Detection now returns a **5-way** species group; 14 importable protocols, every one with a template,
no duplicate aliases. 69 tests pass.

**The Monkey picklist was correct on creation** (`[(401128, 'Monkey')]`) — the copied-picklist gotcha
above was avoided. 137631 also matches the canonical spec exactly (names, types, units, precision, required).

**VERIFIED: each template binds to its OWN protocol.** This is the check that matters most, because a
template is bound to one protocol and `build_payload` never sends a protocol id — a mis-bound template
silently writes to the wrong place. For all five: every readout id in the template belongs to that
protocol, and every `protocol_name` in `header_mappings` is that protocol's name. **No cross-binding.**

**Method note — `header_mappings` entries key on `definition`, not `object`.** A parser looking for
`object` returns 0 mappings for every template, which reads as "unbound" when the template is in fact
fine. The real shape is
`{"header": {"id","name","position"}, "definition": {"id","name","data_type_name","protocol_name","type"}, "run_grouping": <run id>}`.
Identifier columns carry `type: "InternalFieldDefinition::MoleculeBatchIdentifier"`; readouts carry
`type: "ReadoutDefinition"` plus `protocol_name`.

**Harmless difference found between templates:** `RLM_GT` stores the ORIGINAL multi-line header spellings
(`CLint (raw)\r\n(μL/min/mg)`), while `DLM_GT` stores the collapsed single-line ones — i.e. RLM_GT was
built from the raw xlsx and DLM_GT from the `_upl.csv`. The **readout id sets are identical**, and the rat
import succeeded, so CDD matches these whitespace-insensitively. Either source works for template-building.

**CRO checker label made species-agnostic.** `static/verify_file.html` now reads
`"Microsomal stability (any species)"` instead of naming the species, so adding a sixth species never needs
another edit to the file we ship to WuXi. Regression re-run after the change: **ALL 9 FILES MATCH
EXPECTATIONS**, and the Dog/Monkey report passes 7/7.

## Caco-2 (85977) configured — the LAST extraction-only block is gone (2026-10-29)

**File:** `data/uploads/20261029/2026_10_27_Caco2_433706-2026092501-Caco2_v2_report.xlsx` — 13 columns,
**24 rows**, no species column (single protocol, no split). 13 file columns = 12 readouts + 1 identifier.

**CACO2_GT VERIFIED (template id 62111):** 13 header mappings, all 12 readout mappings target
`Caco-2 permeability`, identifier on `Molecule-Batch ID`
(`InternalFieldDefinition::MoleculeBatchIdentifier`), and the template's readout id set is **identical to
the config block's**. Detection returns `single` pid=85977 at recall 1.00 / precision 1.00;
`--check-mapping` gives `mapped=13, mapping_ok=True`. 15 importable protocols, **every one templated**,
and `protocols:` now has **no extraction-only entries left**.

**CAUTION — three config keys reproduce the CRO file's spelling, not CDD's.** Config keys always match the
FILE; the readout **id** is what binds:

| config key (= file column) | CDD readout name | id |
|---|---|---|
| `Mean %Soulution Recovery A to B` | Mean %Solution Recovery A to B | 1073987 |
| `Efflux Ratio` | Efflux ratio | 1073986 |
| `Comment` | Serac Comment | 1073990 |

The first is a genuine **misspelling in WuXi's file** ("Soulution"). `CACO2_GT` carries the same
misspelling, so if WuXi ever corrects it, **the config key AND the template must change together** — fixing
one alone breaks the import. The old `Caco-2 template` (34168) has it too, so the typo is long-standing.
Noted in a comment above the block. The template also stores `Mean  Papp` with a double space where the
file has one; CDD matches whitespace-insensitively, so that one is harmless.

**Pre-flight, all green:** 5 Number columns hold 0 non-numeric tokens (**no `corrections:` block needed**);
batch-id format 24/24, all 24 unique, 0 missing in CDD; no readout is `required`, so the 24 blank
`Provider Comment` / `Comment` cells are fine.

**Upload CONFIRMED landed:** run **2268684** (2026-10-29) holds all **24 rows**; the protocol went 95 → 119
rows. See the timing CAUTION below — the first read of this run showed 0 rows and `source_files: []`.

## CAUTION — two ways to misread CDD state as "empty" (2026-10-29)

Both of these produced WRONG conclusions in this session. Fixed claims are corrected in place above.

**1. `readout_rows` is NEVER expanded on the runs endpoints.** `GET /runs?protocols=<pid>` and
`GET /runs/<id>` both return `readout_rows: []` whether or not the run holds data, so
`len(o["readout_rows"])` reads 0 for every run and makes a populated protocol look empty. **It made rat
111161 (39 rows) look like "5 empty runs, no data ever committed".** The reliable count is
`get_protocol_data.protocol_rows(session, vault, pid)` (`GET /protocols/<pid>/data`), grouped by each row's
`run` key — note the key is **`run`** (an int), not `run_id`. The row shape is
`{'id','run','batch','molecule','readouts'}`, where `batch` and `molecule` are **ints**, not objects. True
empty runs = run ids with no row pointing at them.

**2. An import is NOT instant — do not conclude "it did not land" from one read.** Caco-2 read 95 rows
with its new run at 0 rows and `source_files: []`, then 119 rows with that same run holding 24 a short
while later. A run created by the template wizard and a run whose import is still processing look
identical on a single read. **Re-query before reporting a failure**, and compare against a protocol known
to have committed.

**Helper for both (`make_session` takes the TOKEN STRING, not a path):**
`from get_library import make_session, load_token; s = make_session(load_token('~/.cdd_token'))`

## CRO checker gained a Caco-2 entry — 6 assays now (2026-10-29)

`static/verify_file.html` had no Caco-2 entry, so a Caco-2 report dropped on it detected nothing. Added
`key:"Caco2"`, `name:"Caco-2 permeability"`, identifier `Molecule-Batch ID`, 12 readouts and the 5 numeric
columns. `PROTOCOLS` is now MDR1, **Caco2**, LogD, TSY, PPB, MMS. The in-page spec list renders from
`PROTOCOLS` generically, so the new assay and its 12 column chips appear with no further edit.

**The checker reproduces WuXi's `Soulution` misspelling on purpose.** The checker states the contract the
import actually enforces, and both `CACO2_GT` and the config key carry that spelling. Spelling it
correctly here would tell WuXi to "fix" a column that our template would then reject. If the typo is ever
cleaned up it must change in **three** places together: WuXi's report, `CACO2_GT`, and `config.yaml`.

**Detection margins verified — MDR1 is NOT affected.** MDR1 and Caco-2 share 6 column names, so the risk
was a Caco-2 entry stealing MDR1 files. Measured on the real reports:

| file | winner | runner-up | margin |
|---|---|---|---|
| MDR1 report | **MDR1 1.00** | Caco2 0.50 | 0.50 |
| Caco-2 report | **Caco2 1.00** | LogD 0.67 | 0.33 |

`norm()` collapses whitespace but does **not** lowercase, so `Efflux Ratio` (Caco-2's file) and
`Efflux ratio` (MDR1's) count as different columns — part of why the two separate cleanly. Both winners
sit at recall 1.00 with the runner-up below the 0.6 `MIN_RECALL` floor or well clear of it.

**Verified after the edit:** the Caco-2 report passes **7/7**; the 9-file regression still reports
**ALL 9 FILES MATCH EXPECTATIONS** (MDR1 still detected correctly); still zero "CDD", the only two "Serac"
strings are the literal MDR1 column names, zero network primitives, 24,323 bytes. **Re-send to WuXi** —
this build now covers all six assays.

**Regenerating the scratch core: the line range MOVED.** The pure-logic block is now lines **115-335**
(`<script>` at 114, `async function inflate` at 336 starts the DOM half). Do not reuse the old
`115,329` — it truncates mid-function and the import fails with `Invalid or unexpected token`. Find the
bounds each time rather than hard-coding them: `grep -n "<script>\|^async function inflate" static/verify_file.html`.

## HMS = HEPATOCYTE stability — an assay we have no protocol for (2026-10-29)

**Symptom:** `data/uploads/20261029/2026_10_25_HMS_433706-2026100701-HMS_v4_report_0_corrected.xlsx`
errors in the webapp, complaining about **PPB** readouts.

**Root cause: HMS is hepatocyte stability, NOT microsomal stability**, and **no hepatocyte protocol exists
in vault 7108** (all 50 checked — `MAO stability` and `HepG2 …` are different assays). There is no config
block either, so the detector has nothing correct to match.

**Why it lands on PPB.** 6 of PPB's 9 readouts are generic admin columns this file also carries —
`Study number`, `Date`, `Species`, `Provider Comment`, `Provider Name`, `Comment` — giving recall
**0.67**, above the `MIN_RECALL` floor of **0.6**. PPB blocks carry `species:`, so the tie becomes a
species group and routes Dog → 135301 `ppb UC (Dog)`, Monkey → 135300 `ppb UC (Monkey)`. **Wrong assay
entirely.** The reported errors per unit are:
* UNMAPPED (would not import): `T½ (min)`, `Clint_hep (uL/min/10^6)`, `Clint_liver (mL/min/kg)`
* MISSING (left blank): `%Unbound`, `%Bound`, `%Remaining`

**The guard held — nothing could go wrong.** `mapping_ok=False` → status `needs-selection` → Submit stays
disabled. The failure mode is a *confusing message*, not a bad upload. The CRO checker shows the same
false match (`Detected assay — Plasma protein binding (UC) — 67%`), so WuXi would be misled the same way.

**CAUTION — this is the generic-column trap.** Any CRO file for an unconfigured assay will match whatever
protocol shares the most admin columns, because `Study number` / `Date` / `Species` / the three
comment-and-name columns are common to EVERY WuXi layout. A 0.67 "match" with 3 unmapped columns means
"no protocol for this assay", not "nearly right". When a file errors against a protocol that is obviously
the wrong assay, check whether the assay exists at all **before** touching the mapping.

**The file itself is clean** and ready once a protocol exists: 2 rows (1 Dog, 1 Monkey), 3 numeric columns
with 0 non-numeric tokens, batch-id format 2/2, 0 missing in CDD, only the optional
`Provider Comment` / `Comment` blank.

**Spec to create the hepatocyte protocol** (10 file columns = 9 readouts + identifier, from the file):

| # | Readout | Type | Unit |
|---|---|---|---|
| 1 | Study number | Text | — |
| 2 | Date | Text | — |
| 3 | Species | PickList | — |
| 4 | T½ | Number | `min` |
| 5 | Clint_hep | Number | `uL/min/10^6` |
| 6 | Clint_liver | Number | `mL/min/kg` |
| 7 | Provider Comment | Text | — |
| 8 | Serac Comment | Text | — |
| 9 | Provider Name | Text | — |

**NOTE — `Species` is capital-S in the HMS file**, where the MMS files ship lowercase `species`. Config
keys copy the FILE, so the hepatocyte block must use `"Species"`. (`split_species` lowercases the header
for its own lookup, so the split itself works either way.)

**Open decision: one protocol per species, or one protocol for all?** Every existing split in this vault
is per-species (5 microsomal, 6 PPB), and a per-species picklist is what makes a wrong-species row get
rejected. A single protocol with a multi-value picklist would also work for a brand-new assay with no
history. Not decided — the operator chooses before creation.

## TSY gained a redundant `Compound ID` column — fixed via `ignore:` (2026-10-29)

**Symptom:** `data/uploads/20261029/2026_10_09_433706-2026092501-TSY_Report_Oct-08-2026.xlsx` errors in
the webapp. **Operator's diagnosis was correct:** `Compound ID` is the single UNMAPPED column.

Detection was never wrong (`single` pid=86015, recall 1.00) and all 8 readouts + the identifier matched.
Only the extra column blocked the gate. **This is a WuXi format change** — all four earlier TSY reports
on disk are 9 columns starting at `Molecule-Batch ID`, with no `Compound ID`.

**Fix (generalised 2026-10-29): a top-level `ignore_columns:` list, folded into EVERY block.** First
done as a TSY-only `ignore:` entry, then lifted to the top level because WuXi adds this column across
reports, not just TSY → `mapped=9, ignored=['Compound ID'], mapping_ok=True`. Detection precision also
rose to 0.90. This is the SAME shape as MDR1, which has ignored a redundant `Molecule Name` beside its
batch id since the beginning — that one stays block-level, because only MDR1 ships it.

**CAUTION — `ignore:` is VALIDATION-ONLY. It does not drop the column.** Nothing in `convert_upload.py`
or `webapp/app.py` removes ignored columns; the staged CSV still carries `Compound ID`, and that file is
what goes to CDD. `ignore:` only moves a column from "unmapped" to "ignored" so `evaluate_mapping`
returns ok.

**Does CDD accept the extra column? The MDR1 precedent says a template CAN skip one.** `MDCK-MDR1`'s
header mappings run positions **1..27** — position 0 is absent, and position 0 in the MDR1 file is the
ignored `Molecule Name`. That file imports successfully, so an unmapped extra column is not fatal in
itself.

**But `TSY_GT` was built from a 9-column file**, so its positions run 0..8 with `Molecule-Batch ID` at 0,
while the new file has `Compound ID` at 0 and the identifier at 1. Whether CDD re-matches on header NAME
(works) or validates the stored layout (422) is **not established** — no TSY file with this column has
ever been imported. **If the upload returns HTTP 422 `"Provided mapping template does not match file."`,
rebuild `TSY_GT` from this 10-column file leaving `Compound ID` unmapped** — which is exactly how
`MDCK-MDR1` is built. The config change is needed either way.

**The file is otherwise clean:** 2 rows, both solubility columns 0 non-numeric, batch-id format 2/2,
0 missing in CDD, only the optional `Comment` blank.

**The CRO checker already handles it correctly — left unchanged.** It returns `warn` (not `fail`) and
names the column: *"Not recognised (will be ignored or rejected): Compound ID"*. That is the right signal:
we would rather WuXi drop the redundant column than have the checker bless it. Adding `Compound ID` to the
checker's TSY `ignore` would silence a warning we actually want.

## Global `ignore_columns:` — one list for columns no protocol imports (2026-10-29)

A column the CRO adds to every report should be declared once, not copied into 15 blocks. New top-level
key in `config.yaml`, beside `species_aliases:`:

```yaml
ignore_columns:
  - "Compound ID"     # redundant molecule id beside Molecule-Batch ID (2026-10-09)
```

**How it reaches everything: folded at CONFIG LOAD, not at each call site.** New
`convert_upload.apply_global_ignores(config)` appends the global entries to every protocol's own
`ignore:` (whitespace-insensitive, never duplicating one a block already lists) and is called at the
**two** load points — `import_to_protocol.load_config` (CLI) and `webapp/app.py`'s module-level `CONFIG`.
Everything downstream then reads a block whose `ignore:` is already complete, so **no function signature
changed** and `detect_protocol`, `evaluate_mapping`, `check_mapping` and the webapp all pick it up for
free. Block-level `ignore:` still works and is still right for a column only one assay ships — MDR1 keeps
`Molecule Name` that way.

**Detection is provably unaffected.** `_score_block` computes recall/precision from `readouts` and
`identifiers` only — it never reads `ignore` — so folding columns in cannot move a detection score.
Verified across all 15 real reports: every verdict unchanged.

**CRO checker given the matching rule.** `static/verify_file.html` gained
`const IGNORE_ALL = ["Run Lab", "Compound ID"];`, merged into the `allowed` set in `analyze`, and the
rule text now reads "`Run Lab`, `Compound ID` and `Molecule Name` are allowed but ignored". The TSY
report went `warn` → **pass**. This REVERSES the 2026-10-29 note above that argued for keeping the
warning so WuXi would drop the column — the operator decided the column should not block, and the
checker must state the same contract the import enforces.

**CAUTION unchanged: this does NOT drop the column from the file.** `ignore:` is still validation-only.
The staged CSV keeps `Compound ID` and CDD still receives it. **Do not "fix" that by dropping ignored
columns at conversion time — it would break MDR1**, whose `MDCK-MDR1` template is built from a file that
HAS the ignored `Molecule Name` at position 0 (its mappings run positions 1..27). Removing the column
would change the layout the template was built against.

**Verified:** 74 tests pass (5 new `ApplyGlobalIgnoresTests`); CLI `--check-mapping` and the webapp's own
`CONFIG` both report `ok=True ignored=['Compound ID']`; all 15 real reports re-swept with no regression
(only the HMS file still fails, for the unrelated missing-protocol reason); checker regression still
**ALL 9 FILES MATCH EXPECTATIONS**; HTML re-audited — zero "CDD", two "Serac" (the MDR1 column names),
zero network primitives, 24,509 bytes.

## Post-commit verification (check_cdd_commit.py, 2026-07-16)
- **Purpose:** automate the manual "did it really upload?" check. For each committed workbook: detect protocol (+ species split) → fetch CDD protocol data → per compound, confirm a committed row EXISTS and numeric readout VALUES match. Webapp runs it automatically after commit (`POST /api/verify`) and flips rows + the Submit button to **azure "SUCCESS"** (`--azure #2f7bb5`); CLI returns SUCCESS/fail exit code.
- **Match key (verified 2026-07-16):** file `Molecule-Batch ID` == CDD batch `molecule_batch_identifier`. Protocol-data rows carry `batch` as a **numeric id**, not the SRB string — map via `GET /vaults/{v}/batches?batches=id1,id2` → `molecule_batch_identifier`. (`molecule` and `run` are also numeric ids.)
- **CDD qualifier storage (verified):** a readout cell is `{value, outlier?, modifier?}`. The `<`/`>` qualifier lives in a separate **`modifier`** field with a numeric `value` (sometimes instead embedded in a string `value` like "< 1"). `check_cdd_commit.cell_value` reconstructs `modifier + value` — else every qualified cell false-mismatches.
- **Multiple runs per batch exist** → matching the "latest run" picks the wrong one. Fix: keep ALL runs per batch and a compound passes if ANY run matches every compared readout.
- **Numeric-only value rule:** compare a readout only when the FILE value is numeric/qualified; a numeric file value with an empty CDD cell = mismatch; a value CDD stores non-numerically (dates) is skipped so metadata never false-fails. Tolerance `--tol` (abs, dflt 0.01) / `--rel` (rel, dflt 0.01) absorbs rounding.
- **Field-kind comparison — critical fix (2026-08-28):** the numeric-only rule alone caused a FALSE duplicate: a compound re-measured in a NEW study (different `Study number`/`Study Date`/value) matched an OLD CDD run, so the webapp flagged it "already uploaded" and would skip real new data. Root causes: (a) `Study number` is TEXT → skipped entirely, so different studies looked identical; (b) `Study Date` (YYYYMMDD int) compared with the RELATIVE tolerance — 1% of ~2e7 is ~2e5, so different dates fell within tol → false `ok`. Fix = `compare_field(col_norm_lower, ...)` classifies by column name: **`study number` → EXACT string (the run identity — a different study is NOT a duplicate); date columns (`study date`/`date`/`run date`) → EXACT digits-only (format-insensitive, never a tolerance); everything else → numeric `compare_value`.** `check_unit` + `comparison_rows` now use `compare_field`. Effect: a run counts as a duplicate only if its study number (and dates) match exactly AND measurements are within tolerance. Verified: synthetic new-study → NOT duplicate (DIFF on Study number+Date), same-study → duplicate; 20260716 files still verify (their study numbers match CDD). Pure `check_cdd_commit` change — webapp picks it up automatically (no frontend/cache change).
- **Validated on data/uploads/20260716 (2026-07-16):** all committed new-format files verify SUCCESS — LogD, MDR1, MMS (Human+Mouse), 2×PPBUC, TSY (`_GT_corrected`). An OLD-format file with `Compound ID`+`Batch No` (not `Molecule-Batch ID`) correctly returns "no protocol detected" (unverifiable, not a false pass).
- **Overlap comparison export (`--compare`, 2026-08-11):** for every source-file compound already in CDD, `check_cdd_commit --compare` writes a local `<file>.compare.csv` — long format, one row per field: `batch_id, cdd_compound_name, experiment, cdd_value, wuxi_value, match`. Covers CDD compound name (from `batch.molecule.name` = `SRB-XXXXXXX`), study number, study date, and each experiment readout (e.g. `LogD7.4`), CDD value beside the WuXi file value, `match` = ok/DIFF/'' (blank = non-numeric, not value-compared). `build_comparison()` reuses the protocol-data fetch (same run shape as `cdd_lookup`, so verify+compare share one fetch); picks the best-matching run per compound. NOTE: `batch.molecule` is the FULL molecule dict incl. `smiles`/`molfile`/`inchi` — code reads ONLY `.name`, never structures (assistant must never read/print structures either).
- **Duplicate-upload guard in the webapp (2026-08-11):** at staging (`/api/upload`, `/api/recheck`), before a unit is flagged `ready`, `_already_uploaded()` runs the SAME check against CDD — if every compound already exists with matching values, the unit's status becomes **`already-uploaded`** (orange tag `already uploaded`) instead of `ready`. Such units are excluded from submit (frontend sends only `ready`; the submit gate treats `already-uploaded` as resolved-but-not-submitted). Best-effort: needs a token, and any read/network error falls back to normal `ready` (never blocks staging). CDD lookups are memoized per request (pid→lookup) to avoid refetching for multi-unit files. Verified 2026-08-11: re-dragging the already-committed 20260811 LogD file → `already-uploaded`; a synthetic not-in-CDD batch → `ready`. NOTE: dup check now makes staging hit CDD (token needed at upload, not just submit). Frontend adds `--amber #c9791f` tag + gate text "N ready · M already uploaded (skipped)".

## Findings (CDD API behavior)
- **Collection filter is a lie, filter client-side:** `/vaults/{v}/collections/{cid}/molecules` ignores `{cid}` and returns the full vault listing; `GET /vaults/{v}/molecules?collections={cid}` 404s under every param spelling. Real membership = the molecule's own `collections` field (`[{"id","name"}]`). So every export walks the whole vault once per requested collection, then filters in Python (`_in_collection`). _[get_library.py; memory cdd_api_collection_filter_quirk]_
- **Saved searches can't be created via API:** `POST /vaults/{v}/searches` and `/molecules/search` both 404. Use a search created in the web UI (its numeric id is the URL prefix).
- **Structure images are async:** `GET /molecules/{m}/image` returns a job; poll `GET /exports/{job}` until `Content-Type: image/png`. _[download_cdd_structures.py]_
- **Slurps (bulk import) is async + template-driven:** `POST /vaults/{v}/slurps` (multipart: `file` part + `json` part `{project, mapping_template, runs, autoreject}`) → poll `GET /vaults/{v}/slurps/{id}` until `state` ∈ {committed, rejected, invalid, canceled} (`started` = in-flight). `committed` = success. Endpoint shapes confirmed from the Workflow-Informatics CDD-Python-SDK source (KB is login-gated). _[import_to_protocol.py]_
- **`autoreject` defaults ON:** an import with Suspicious Events / Errors is rejected wholesale, not partly committed. Safer; override with `--no-autoreject`.
- **Protocol data read path (2026-06-26):** `GET /vaults/{v}/protocols/{id}/data` → paginated rows `{id, molecule, batch, run, readouts:{<readout_id>:{value, outlier?}}}`; `GET /vaults/{v}/runs?protocols={id}` → `run_date` per run (for latest-per-compound); `GET /vaults/{v}/protocols/{id}` → readout_definitions (`id→name`). **Unlike the collection filter, `GET /vaults/{v}/molecules?molecules=id1,id2` IS honored** — returns exactly those molecules with `name`/`smiles`, so join SMILES by id (no full-vault walk needed).

## Decisions & conventions
- **PPB correction + re-upload workflow (2026-08-31, in `vignettes/convert_dataset.ipynb` last 6 cells):** re-extract the whole PPB (UC) protocol (PID **125963**) from CDD, correct, repackage for re-upload. Backup = documented `get_protocol_data.get_data(experiments=['ppb'])` (molecule-keyed, SMILES) → CSV. Upload frame = **batch-keyed** extraction (get_data lacks the Molecule-Batch ID needed as the upload identifier): `protocol_rows` + `run_dates` + `check_cdd_commit.fetch_batch_identifiers`/`cell_value`, latest run per batch, mapped back to the config `readouts` columns. **Corrections:** `%Unbound` NA→0, `%Bound` NA→100; round `%Unbound`/`%Bound`/`%Remaining` to 1 decimal (`%Remaining` NA left as-is). Output = `Upload`-tab xlsx in `data/uploads/20260831/`. **PPB upload columns / readout ids (125963, template PPB_GT):** `Molecule-Batch ID`(identifier) · `Study number`=1773615 · `Date`=1773616 · `Species`=1773617 · `%Unbound`=1773618 · `%Bound`=1773619 · `%Remaining`=1773623 · `Provider Comment`=1773620 · `Provider Name`=1773621 · `Comment`=1773622. NOTE: re-upload APPENDS new runs (doesn't overwrite) — old + corrected runs coexist per compound until old ones are deleted. Also note: `Reworked - Plasma protein binding (UC)` PID 132956 exists in CDD but is NOT what config targets (config = 125963).
- **PPB UC split into species protocols (2026-09-16):** CDD now has five new PPB protocols — `ppb UC (Human)` **135299**, `(Monkey)` **135300**, `(Dog)` **135301**, `(Rat)` **135302**, `(10% FBS)` **135303**. All five carry the SAME 9 readouts (Study number, Date, Species, %Unbound, %Bound, Provider Comment, Provider Name, **Serac Comment**, %Remaining) and are now in `config.yaml`. Every readout id was verified live against the API (45/45 match), not transcribed by hand. **The file's `Comment` column maps to CDD's `Serac Comment` readout** — in these protocols that readout exists by name, so the old 125963 `Comment`→Serac inference is now explicit. Aliases must be UNIQUE (`get_protocol_data.alias_map` is keyed by alias, so a duplicate silently overwrites): `ppb_human`/`ppb_monkey`/`ppb_dog`/`ppb_rat`/`ppb_fbs`; **125963 keeps alias `ppb`**, so existing calls like `get_data(experiments=['ppb'])` and the 2026-08-31 correction workflow keep working. **Detection consequence — `species: Mouse` ADDED to 125963:** the six PPB blocks share one column set, so a PPB file ties on columns alone. `detect_protocol` only returns a species group when >=2 tied blocks carry a `species:` field, and it routes by that value; without a species on 125963 the Mouse rows would have routed NOWHERE. With it, detection returns `species_group` conf 1.0 and `{Mouse:125963, Human:135299, Monkey:135300, Dog:135301, Rat:135302, '10%FBS':135303}`. Verified on both real PPB files: Mouse→125963 (unchanged), 10%FBS→135303. **`10%FBS` has NO SPACE** — that is what WuXi writes in the Species cell, and the routing key must match the cell exactly (the CDD protocol NAME says `10% FBS`, with a space; do not copy that). **CDD has no `ppb UC (Mouse)` protocol**, so mouse data stays on 125963. **PARTLY DONE (2026-09-17) — `mapping_template` was empty `""` on all five**: the templates do not exist yet and must be built by hand in the CDD web UI (the usual one-time dance), then written into config. An empty value makes `import_to_protocol.py` exit with a clear error BEFORE any upload, so this fails loudly, never silently. `project: FBXO31` assumed from the other blocks — confirm. **135303 is now LIVE:** template **`PPB_10%FBS_GT`** (id 61032) was built 2026-09-17 from the one-row CSV and is set in config. Verified via `GET /mapping_templates/61032`: all 10 columns bind to `ppb UC (10% FBS)` readouts and match the config ids exactly. The real 2026-08-31 file now yields TWO READY units (`10%FBS` 10 rows -> 135303, `Mouse` 104 rows -> 125963). **Still open: Human/Monkey/Dog/Rat (135299-135302)** have no template and stay hidden from the webapp dropdown until real data for those species arrives. The WuXi checker (`static/verify_file.html`) needs NO change: the PPB column set is unchanged. 35 unit tests still pass; LogD/MDR1/TSY still detect `single` and MMS still splits Human/Mouse.
- **125963 still exists — CDD RENAMED it (2026-09-17):** `Plasma protein binding (UC)` is now called **`Ppp UC (mouse)`** in CDD. It was NOT deleted: it keeps **26 runs** (all historical mouse data) and **all 9 readout ids are still valid** (verified against the API). `Comment`→**`Serac Comment`** (1773622) is now CONFIRMED by name, so the old 2026-06 "inference" note is settled fact. **Search trap:** a grep for `plasma|protein|ppb|binding` MISSES this protocol — the new name contains none of those strings (`Ppp`, not `ppb`). Search protocol lists for `UC (` instead. `name:` in config synced to the CDD name. **`Ppp` looks like CDD's typo for `ppb`** — renaming it to `ppb UC (Mouse)` in the CDD UI would make the family consistent (`name:` is display-only, so a rename needs only a cosmetic config edit). `Reworked - Plasma protein binding (UC)` **132956** (12 readouts, 1 run) remains a separate protocol we do NOT target.
- **Species alias normalisation (2026-09-17):** WuXi writes **`FBS`** in the Species cell, but the routing value is **`10%FBS`**. New top-level config key **`species_aliases:`** (`FBS: "10%FBS"`) maps a raw cell value onto the `species:` value the protocol blocks route on. New helper **`convert_upload.normalize_species(header, rows, aliases)`** rewrites the cells; the match ignores case and outer spaces (` fbs ` works), it is idempotent, it returns NEW rows (never mutates the caller's list), and it is a no-op when the file has no Species column or the map is empty. **Wired into BOTH paths, and both are needed:** `webapp/app.py` right after `read_upload_sheet` (so the split, the routing AND the Species value stored in CDD all agree), and `check_cdd_commit.load_units` after `read_file` — without the second one, post-commit verification would compare the file's `FBS` against CDD's `10%FBS` and report a false mismatch. Config over code: add a new CRO spelling to `species_aliases:`, never to the Python. Verified end-to-end on a synthetic 7-row PPB file: Mouse/Human/Monkey/Dog/Rat pass through untouched, and `FBS` + ` fbs ` collapse into ONE `10%FBS` unit routed to 135303. 8 new unit tests (43 total, all pass); real-file regression unchanged (PPB→Mouse, PPB→10%FBS, MMS→Human+Mouse, LogD→single).
- **Species matching made CANONICAL (2026-09-17, after a webapp screenshot):** a real PPB file produced FOUR units — `FBS`(6), `mouse`(2), `Mouse`(102), `10%FBS`(4) — because the CRO mixes spellings AND casing in one file. New **`convert_upload.species_alias_map(config)`** folds two sources into one lookup: every protocol block's own `species:` value (keyed lowercase, mapping to itself) PLUS the `species_aliases:` short forms. Both call sites now pass this map instead of the raw alias dict. Result on that file: 4 units → **2** (`Mouse` 104 rows → 125963, `10%FBS` 10 rows → 135303). So `species_aliases:` only needs the ODD spellings; case variants are handled for free. An unknown species (e.g. 'Hamster') is deliberately left untouched so it surfaces for manual review instead of being mis-routed. 6 more tests (49 total). **CAUTION — merging changes dup-guard granularity:** the 2 `mouse` rows were previously their own unit and were flagged `already-uploaded`; inside the merged 104-row unit they will re-upload, because the guard works per unit, not per row. **Two things block a green 10%FBS row, both outside the code:** (1) the webapp loads config + modules ONCE at startup, so it MUST be restarted after any of this; (2) `_importable_protocols()` (app.py:73) and `ready` (app.py:149) both require a non-empty `mapping_template`, so the five new PPB protocols are correctly hidden from the dropdown and cannot go READY until their CDD templates exist. That guard is right — do not weaken it to make the row green.
- **Local-only data policy (hard rule):** chemistry (SMILES/InChI/structures/compound names like `SRB-XXXXXXX`) never leaves the machine and never hits script stdout. Scripts print metadata only — counts, status codes, field names, numeric ids. Files on disk are the only place chemistry lands. See CLAUDE.md. Assistant must not `Read` structure files or echo notebook outputs.
- **`get_df(collections=[])` = fetch ALL collections** (empty list, not `None`). CLI equivalent `--all-collections`. `collections=None` still errors. _[2026-06-26]_
- **Import file is streamed, never read:** `import_to_protocol.py` hands CDD a binary file handle; the script (and assistant) never parse data rows. `--check-mapping` reads the **header row only** (column names = schema = allowed), never values.
- **Per-protocol import mappings live in `config/config.yaml`** (config-over-code). Keyed by PID. Three column groups: `identifiers` (the batch key CDD matches on), `ignore` (present-but-not-imported), `readouts` (column → readout id). `--check-mapping` is an offline pre-flight; it also auto-runs before a real import when `--protocol` is set and aborts on any UNMAPPED column.
- **MDR1 protocol "MDR1-MDCK II Inhibitor" = PID 131399** (26 readouts, ids 1883900–1883930 non-contiguous). Identifier = **only** `Batch Molecule-Batch ID` (`SRB-XXXXXXX-NNN` embeds the molecule). Ignored columns: `Molecule Name`, `Run Lab`. Project = `FBXO31`, saved mapping template = `MDCK-MDR1`. With both in the config block, import is just `--protocol 131399 --file <path>`. _[2026-06-26; memory cdd_protocol_import_config]_
- **Three ADME protocols configured (2026-06-26):** `LogD` PID **85979** (template `LogD_GT`, rebuilt 2026-07-07 from the new WuXi layout — old `LogD` template was stale and 422'd), `Thermodynamic Solubility` PID **86015** (template `TSY_GT`, rebuilt 2026-07-07 from the new WuXi layout — old `Thermosl upload` was stale), `Plasma protein binding (UC)` PID **125963** (template `PPB_GT`, rebuilt 2026-07-07 from the new WuXi layout — old `PPB` was stale). All project `FBXO31`. **Identifier = `Molecule-Batch ID`** — verified against the live CDD templates 2026-07-07 (the raw WuXi name, unchanged; NOT `Batch Molecule-Batch ID`, which is MDR1-only — I briefly set the `Batch ` prefix here and reverted it same day); `Run Lab` ignored if present. (The not-yet-migrated microsomal split files — hlm/mlm/rlm/caco2 configs — still use `Compound ID`+`Batch No`.) Assumption to confirm vs each template: `Comment`→Serac comment. Kinetic Solubility (PID 124033) exists but is not one of ours. **Import status (2026-07-07): all three ADME protocols COMMITTED** — LogD (`LogD_GT`), Thermodynamic Solubility (`TSY_GT`), Plasma protein binding UC (`PPB_GT`). Each stale original template 422'd "does not match file"; fix each time was: open the 422 edit URL → re-map by hand → save as a new `*_GT` template → update `mapping_template` in config. MDR1 committed earlier. All five WuXi files now imported.
- **MMS (microsomal, human+mouse) split done (2026-06-26):** `python/split_species.py` (faithful port of the HTML tool's species split) — species col = header where trimmed.lower()=='species'; distinct species in first-seen order; per species = header + matching rows; junk/blank-species rows drop out; sanitised filename `<base>_<species>_upl.csv`; UTF-8 BOM. **Files created** in `data/uploads/`: `..._0+1+2_Human_upl.csv` + `..._0+1+2_Mouse_upl.csv` (5 rows each, species-pure). Standalone — no auth/network, reads header column names only, prints only species names + counts.
- **Two microsomal-stability protocols configured (2026-06-26; migrated 2026-07-09):** `Microsomal stability species human` PID **88861** (template **`HLM_GT`**), `...mouse` PID **88862** (template **`MLM_GT`**) — both templates rebuilt 2026-07-09 for the new WuXi layout (old `Microsomal stability - human/mouse` were stale). Both project `FBXO31`, identifier **`Molecule-Batch ID`** (was `Compound ID`+`Batch No` pre-migration), ignore `Run Lab`, 10 readouts each incl. `species`→Species (PickList), `T½ (min)`→T½, `CLint (raw) (μL/min/mg)`→CLint (raw), two `Remaining …` cols, `Comment`→Serac Comment. Feed the split files (Human→88861, Mouse→88862). Both validate `mapping_ok=True`; both **committed 2026-07-09** via the webapp. Also present but not ours: microsomal **rat** PID 111161, MAO stability 124489. `Comment`→Serac Comment assumption as usual.
- **Header matching is whitespace-insensitive** (`import_to_protocol._norm`): collapses whitespace runs incl. embedded CR/LF, so the MMS multi-line headers (`CLint (raw)\r\r\n(μL/min/mg)`, `Remaining\r\r\n(…)`) match clean single-line config keys. No-op for single-line headers (MDR1/LogD/etc. unaffected — regression-checked).
- **The `_upl.csv` files are produced by a local HTML tool** — `data/uploads/From XLS to CDD upload (ADME).html` ("Upload-Sheet → CSV"): reads each workbook's `Upload` sheet, rounds (LogD/Papp/Efflux/Solubility 2dp, CLint 1dp, keeps `<`/`>` modifiers), stamps `Run Lab=External - WuXi` + `Provider Name=WuXi` on **every** row (this creates the junk rows), and **splits by `Species`** into `_Human_upl.csv`/`_Mouse_upl.csv` (the MMS answer). Runs offline, no data leaves the machine.
- **MDR1 pivot quirks** (`convert_dataset.py`): output is schema-stable (always all target columns, NaN-filled). Suffix spellings are inconsistent on purpose (`- PgP inhibitor` / `- PgP Inhibitor` / `- Pgp inhibitor`). Excel encodes `<`/`>` qualifiers as cell *number formats*, not values — must read via openpyxl (`read_xlsx_preserving_qualifiers`). CRO typo `inhinitor` is handled (regex `inhi[bn]itor`). Round-trip test asserts wide→long is lossless.
- **Stdout success/exit codes:** `import_to_protocol.py` exits 1 on failed mapping check or non-`committed` slurp; 0 on success.
- **Email-on-commit notification (2026-07-09, opt-in):** `python/notify.py` sends a **metadata-only** email when a slurp reaches `committed` — body carries protocol name/PID, slurp id, file *basename*, record counts; NEVER SMILES/compound ids/row values (same hard rule). Config in the top-level `notifications:` block of `config.yaml` (`enabled`, `on_commit`, recipient/sender, `smtp_host`/`smtp_port`, `password_file`); the SMTP app password lives in `~/.cdd_smtp` (chmod 600, like `~/.cdd_token`), NOT in config. Stdlib `smtplib`+`ssl` (no new dep), STARTTLS to `smtp.office365.com:587`. Fires from both the CLI (after `report_outcome` when ok) and the webapp (`/api/status` on the committed transition, deduped via a `NOTIFIED` set; slurp→meta stored at submit in `SLURP_META`). **Sending never raises** — a bad mailbox/disabled SMTP logs a WARN and returns False, never breaks/rolls back the upload. Verify SMTP offline with `import_to_protocol.py --test-notification` (no token, no upload; ignores `on_commit`, still needs `enabled`+creds). Current settings: recipient=sender=`gtamo@seracbio.com`, commit-only (`on_failure` not implemented — only `on_commit`). **BLOCKED (2026-07-13):** `--test-notification` failed — `535 5.7.139 Authentication unsuccessful, SmtpClientAuthentication is disabled for the Tenant` (https://aka.ms/smtp_auth_disabled). seracbio's M365 tenant has SMTP AUTH (basic auth) disabled org-wide, so the app-password+SMTP path cannot work as-is. App password saved at `~/.MS_pwd.txt` (chmod 600, `password_file` points there). Unblock options: (a) IT enables SMTP AUTH for the one mailbox: `Set-CASMailbox -Identity gtamo@seracbio.com -SmtpClientAuthenticationDisabled $false` (per-mailbox override works even with org-level off) — zero code change; (b) IT provides an internal unauthenticated SMTP relay → point `smtp_host` at it, drop the login; (c) build the Microsoft Graph `sendMail` route (needs an Azure AD app registration + `Mail.Send` + admin consent — more setup). notify.py code + wiring are done and tested (graceful-fail confirmed); only the transport is blocked. **Update 2026-07-20:** IT created a dedicated service mailbox `auto@seracbio.com` (config now sends from it, recipient stays `gtamo@seracbio.com`, password in `~/.auto_smtp`). `--test-notification` STILL returns `535 5.7.139 … disabled for the Tenant` — because SMTP AUTH is off at the **org/tenant level**, so a new mailbox inherits "off". The missing step is the **per-mailbox override** on the service account: `Set-CASMailbox -Identity auto@seracbio.com -SmtpClientAuthenticationDisabled $false` (overrides the tenant default; allow up to ~60 min propagation). If Security Defaults/Conditional Access still block basic-auth SMTP after that, fall back to Graph. Error `5.7.139 disabled` = policy (not creds); `5.7.3 authentication failed` would = bad password/app-password needed. **Update 2 (2026-07-20):** after the per-mailbox override, the error MOVED to `535 5.7.139 … user is locked by your organization's security defaults policy` — i.e. SMTP AUTH is now enabled on `auto@` but **Security Defaults blocks all basic/legacy auth tenant-wide, and cannot exclude a single account**. So SMTP-with-password is a dead end on this tenant. Remaining paths: (1) **Microsoft Graph app-only** (OAuth2 client-credentials + `Mail.Send` application permission + admin consent; uses `requests`, no new dep; not blocked by Security Defaults — the recommended route, needs an Azure AD app registration: tenant id / client id / client secret from IT), or (2) an **internal unauthenticated SMTP relay** (point `smtp_host` at it, no login). **DECISION: waiting for IT to say which they can provide before building** (Graph sender not yet written).
- **Per-compound summary table + team email (2026-07-13):** the webapp now shows a **per-compound rollup** below the file table — one row per compound batch id listing every assay label it appears in across the dropped files (labels from each protocol's new `label:` field in config; `_summary_rows()` in `app.py` aggregates from staged units' `conv_rows`, extracting ONLY the identifier column + label, never readout values). New endpoints: `GET /api/summary` (returns the rollup to the LOCAL browser) and `POST /api/email-summary` (emails it). When the submitted batch finishes committing, the frontend auto-POSTs `/api/email-summary` once (`emailSummary()`, guarded by `summaryEmailed`) → `notify.notify_compound_summary()` sends a multipart plain+HTML table. **AUTHORIZED POLICY OVERRIDE (data owner, 2026-07-13):** this team email intentionally **includes compound batch ids** (`SRB-…`) — an explicit, informed exception to the local-only rule for internal team distribution; still limited to batch ids + assay names, never SMILES/structures/measured values. `notify_committed` stays metadata-only; the webapp now sends the summary email instead of the per-slurp commit email (CLI still uses `notify_committed`). Verified end-to-end with the MMS file (4 compounds, assays `('HLM','MLM')`) — display + endpoints work; email still `sent:false` pending SMTP transport. Logic unit-tested with synthetic `TEST-…` ids (no real compound ids to stdout). Config gained `label:` for the 6 importable protocols (MDR1=`MDCK-MDR1 II (with and without inh)`, 85979=`LogD`, 86015=`therm sol 6.4`, 125963=`mouse ppb`, 88861=`HLM`, 88862=`MLM`). **Run-date column added (2026-07-13):** `_summary_rows()` also pulls the file's `Date`/`Run date` column (distinct values per compound) into `run_date`; shown as a middle column in the webapp table + email (Compound | Run date | Assays). Displayed verbatim from the cell (WuXi stores it as an int `YYYYMMDD`, so it shows e.g. `20260703`, unformatted). **Study number in the BROWSER rollup + CDD-vs-file comparison table (2026-08-28):** `_summary_rows()` returns `study_number` (from the file's `Study number` col) alongside batch_id/run_date/assays — rollup columns: Compound | Run date | Study number | Assays. Measured values are NOT in the rollup and NOT emailed (email renderers use only batch_id/run_date/assays; verified with sentinels). **`GET /api/compare`** renders the real CDD-vs-file side-by-side (the operator's actual intent, not file-only values): reuses `ccc.build_comparison` + `ccc.comparison_rows` → long-format rows (batch_id, cdd_compound_name, experiment, cdd_value, wuxi_value, match) for compounds already in CDD; browser-only (carries CDD + file values), never emailed. A separate "CDD vs file comparison" table shows it, DIFF rows highlighted (red), `ok` colored green (`td.ok-val`), and a double rule (`tr.group`) between compounds. Free-text columns are hidden from the comparison (`_IGNORE_COMPARE_COLS` = provider comment/provider name/comment/serac comment) in `comparison_rows` (affects both the webapp table and the `--compare` CSV). app.js cache-buster `?v=2026-08-28-4`. **Stale-STAGE fix:** `STAGE` is a global server dict that a browser refresh doesn't reset, so summary/compare double-counted a re-staged file (symptom: endpoint string repeated ` | ` identically). Added `POST /api/clear` (empties STAGE), called by the frontend on page load AND on Clear, so views reflect only the current batch. app.js cache-buster now `?v=2026-08-28-2` (hard-refresh once).
- **Protocol data extraction (`get_protocol_data.py`, 2026-06-26):** selects assays by the config `alias:` field (mdck/logd/hlm/mlm/rlm/solubility/ppb/caco2); `--experiments` omitted → all aliased. Design (user-chosen): **one merged wide table**, one row per compound joined on molecule id, **latest run per compound** (max `run_date`, tie-break run id), readout columns prefixed by alias (`logd_LogD7.4`, `ppb_%Unbound`), molecule cols (`name`,`smiles`, configurable via `--mol-columns`) once. Aliases live in config; caco2 (85977) is the last **extraction-only block** (alias+PID+name, no import mapping); rlm (111161) became importable 2026-10-29. Chemistry only in the CSV/DataFrame; stdout = counts/columns.

## Where things live
- **Scripts:** `python/{get_library,download_cdd_structures,import_to_protocol,get_protocol_data,convert_dataset,split_species,convert_upload,detect_protocol,notify}.py`. Shared auth primitives (`load_token`, `make_session`, `API_BASE`, `get_with_retry`) live in `get_library.py` and are imported by `import_to_protocol.py` + `get_protocol_data.py`. `split_species.py`, `convert_upload.py`, `detect_protocol.py`, `notify.py` are offline (no CDD auth/network; `notify.py` does hit SMTP when enabled).
- **Webapp:** `webapp/app.py` (FastAPI, localhost-only) + `webapp/static/{index.html,app.js}`. Adds `python/` to `sys.path` to reuse the CLI modules; run from repo root: `conda activate cdd && python webapp/app.py`. Endpoints: `/api/protocols`, `/api/upload`, `/api/recheck`, `/api/submit`, `/api/status`, `/api/summary` (per-compound rollup), `/api/email-summary`. Loads config + modules ONCE at startup — restart after any config/code change. Frontend `app.js` is included with a `?v=YYYY-MM-DD` cache-buster in index.html — **bump it whenever app.js changes**, else browsers serve a stale cached app.js (symptom: a new server status like `already-uploaded` renders as the fallback "needs protocol" because old JS lacks the branch). Users pick up a version bump after one hard-refresh. Staging in a temp dir, cleared on exit. Deps in `requirements.txt`: fastapi, uvicorn[standard], python-multipart.
- **Config:** `config/config.yaml` (vault id + per-protocol Slurps mappings; microsomal blocks carry a `species:` hint for the webapp's species→PID split; each importable block has a `label:` for the summary table; top-level `notifications:` block for email). Protocol keys are **ints**.
- **Docs:** `docs/documentation.md` (get_library full reference), `docs/download_cdd_structures.md`, `docs/import_to_protocol.md`, `docs/get_protocol_data.md`. README links all.
- **Notebooks:** `vignettes/Sample_library_download.ipynb` (export walkthrough), `vignettes/convert_dataset.ipynb` (MDR1 pivot), `vignettes/sample_protocol_download.ipynb` (assay-data extraction walkthrough; kernel `cdd`, pass `config_path='../config/config.yaml'`).
- **Tests:** `tests/test_convert_dataset.py` (stdlib `unittest`, ~35 tests, hermetic — synthetic `TEST-XXXX`, public SMILES only). Run `python -m unittest discover tests -v`.
- **Deliverables for the CRO (Wuxi):** `output/Wuxi_mdr1_instructions.txt`, `output/Wuxi_mdr1_template.xlsx`.
- **Local-only (gitignored):** `data/`, `output/`.
- **Dependencies:** `requirements.txt` — pandas 3.0.3, requests, ipykernel, tqdm, openpyxl, PyYAML 6.0.3, fastapi, uvicorn[standard], python-multipart. Email uses only the stdlib (`smtplib`/`email`) — no new dep. **Environment (2026-07-13): now a conda env `cdd` (Python 3.12)**, not the old venv — `conda activate cdd` then `python …`. The stale `cdd/` venv dir may still sit in the repo root (unused; removal not yet authorized). A `cdd` env created via `conda create` must actually finish installing Python — a half-created (metadata-only) env silently falls back to system pip (PEP 668 externally-managed error); verify with `which python` → `.../envs/cdd/bin/python`.
- **Secrets (chmod 600, never committed):** `~/.cdd_token` (CDD API token, read/write needed for import), `~/.MS_pwd.txt` (Outlook SMTP app password; path set by `notifications.password_file`).

## Reusable functions (reach for these first)
- **Export:** `get_library.get_df(vault, collections, columns=…)` → DataFrame; `collections=[]` for all. `--list-fields` dumps every available column per namespace (top-level / molecule_fields / batch_fields) with coverage counts.
- **Discover protocols:** `import_to_protocol.py --list-protocols` (find a PID) / `--describe-protocol PID` (readout ids+names). `--check-mapping` validates a file against config offline.
- **Extract assay data:** `get_protocol_data.get_data(experiments=['logd','ppb',...])` → wide DataFrame (latest per compound, SMILES joined); CLI `--experiments`/`--list`.
- **Pivot:** `convert_dataset.convert_to_target_format`, `validate_pivoted_output`, `read_xlsx_preserving_qualifiers`, `test_pivot_round_trip`.
- **Species split:** `split_species.split_species(file, output_dir=None, dry_run=False)` → one `_<species>_upl.csv` per species; `--dry-run` reports without writing.
- **Email:** `notify.notify_committed(config, …)` metadata-only commit notice (CLI); `notify.notify_compound_summary(config, rows, subject=…)` per-compound table (webapp). `import_to_protocol.py --test-notification` sends one test email offline. Transport currently BLOCKED (tenant SMTP AUTH off — see open threads).
- **Per-compound rollup:** `app._summary_rows()` (webapp) → `[{batch_id, run_date, assays:[label,…]}]` across staged units; labels from config `label:`. Ids + assay names + run date only, never readout values.
