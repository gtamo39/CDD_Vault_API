# `import_to_protocol.py` — Bulk Import Data into a CDD Vault Protocol

Uploads a data file (CSV / SDF / ZIP / XLSX) into a CDD Vault **protocol** via
the Slurps API — the programmatic equivalent of the web UI's *Import Data*.

Script location: [python/import_to_protocol.py](../python/import_to_protocol.py)

The file is **streamed** to CDD as a binary upload; this script never reads or
prints your row values. Everything on stdout is metadata only — slurp id,
state, protocol/readout ids and names, status codes, and error/warning
**counts** (never the rows themselves).

---

## Finding the protocol id (PID) — start here

Most import work needs the numeric **protocol id**. There is no need to dig
through the web UI — the script lists every protocol in the vault for you:

```bash
python3 python/import_to_protocol.py --vault 7108 --list-protocols
```

Output is one line per protocol (metadata only, writes nothing):

```
=== protocols in vault 7108: 42 ===
  id=12345  name='MDR1 Permeability'  readouts=14
  id=12346  name='Kinetic Solubility'  readouts=6
  ...
```

Find your protocol by name and copy its `id` — that's the PID you pass to
`--describe-protocol` and that your mapping template targets.

> **Alternative — from the web UI URL:** open the protocol in CDD Vault and
> read the number out of the address bar:
> `https://app.collaborativedrug.com/vaults/7108/protocols/`**`12345`**`…`

---

## Quick start

```bash
# 1. Find the protocol id
python3 python/import_to_protocol.py --vault 7108 --list-protocols

# 2. Inspect that protocol's readout definitions (what your columns map to)
python3 python/import_to_protocol.py --vault 7108 --describe-protocol 12345

# 2b. Offline: check your file's columns line up with the config mapping
python3 python/import_to_protocol.py \
  --file data/uploads/my_upload.csv --protocol 12345 --check-mapping

# 3. Dry-run: validate inputs + print the payload, post NOTHING
python3 python/import_to_protocol.py --vault 7108 \
  --file data/uploads/my_upload.csv \
  --project "My Project" \
  --mapping-template "MDR1 upload" \
  --dry-run

# 4. Drop --dry-run to actually submit + poll until committed
python3 python/import_to_protocol.py --vault 7108 \
  --file data/uploads/my_upload.csv \
  --project "My Project" \
  --mapping-template "MDR1 upload"
```

---

## CLI arguments

| Flag                     | Type | Default        | Description                                                                                     |
| ------------------------ | ---- | -------------- | ----------------------------------------------------------------------------------------------- |
| `--vault N`              | int  | `7108`         | CDD Vault numeric ID.                                                                            |
| `--token-file PATH`      | path | `~/.cdd_token` | File containing the API token on one line.                                                       |
| `--list-protocols`       | flag | off            | List every protocol (id, name, readout count), then exit. **Use this to find the PID.**         |
| `--describe-protocol PID`| int  | _optional_     | Print one protocol's readout definitions (id, name, type), then exit.                            |
| `--config PATH`          | path | `config/config.yaml` | YAML of per-protocol mappings (column → readout id).                                       |
| `--protocol PID`         | int  | _optional_     | Protocol id; looks up its mapping block in `--config`. Triggers a pre-flight header check on import. |
| `--check-mapping`        | flag | off            | Offline: validate `--file` header against the `--protocol` mapping in `--config`, then exit. No token/network. |
| `--file PATH`            | path | _import only_  | Data file to import (`.csv` / `.sdf` / `.zip` / `.xlsx`).                                         |
| `--project NAME`         | str  | _import only_  | CDD project name. Required for an import.                                                         |
| `--mapping-template NAME`| str  | _one required_ | Name of an existing mapping template (created once in the web UI). Reliable path.                |
| `--mapping-json PATH`    | path | _one required_ | JSON file merged verbatim into the payload (inline mappings — schema is your responsibility).    |
| `--no-autoreject`        | flag | off            | Allow commit despite suspicious events / errors. Default keeps autoreject **ON** (safer).        |
| `--drop-empty-rows`      | flag | off            | Drop rows whose identifier columns are all blank (junk rows the WuXi prep tool stamps with `Run Lab`/`Provider Name`) before upload. Writes a cleaned temp copy; original untouched. Needs `--protocol`; CSV only. |
| `--run-date`             | str  | _optional_     | Run metadata → `runs.run_date`.                                                                   |
| `--run-place`            | str  | _optional_     | Run metadata → `runs.place`.                                                                      |
| `--run-person`           | str  | _optional_     | Run metadata → `runs.person`.                                                                     |
| `--run-conditions`       | str  | _optional_     | Run metadata → `runs.conditions`.                                                                 |
| `--poll-interval SEC`    | float| `5.0`          | Seconds between slurp-status polls.                                                               |
| `--poll-timeout SEC`     | float| `600.0`        | Give up polling after this long (the import may still finish server-side).                        |
| `--dry-run`              | flag | off            | Validate inputs and print the payload; post nothing.                                              |

For an import you must supply `--file`, `--project`, and **one** of
`--mapping-template` / `--mapping-json`.

---

## Modes

### `--list-protocols` (discovery)

Lists every protocol in the vault with its id, name, and readout count. This
is the canonical way to find the PID. Writes nothing.

### `--describe-protocol PID` (discovery)

Prints a single protocol's `readout_definitions` — each readout's `id`, `name`,
and data type. These are the targets your file's columns must map to. Use it to
line up your upload columns against the protocol before building a mapping. If
the protocol's shape differs from the expected `readout_definitions` list, the
script dumps the top-level keys instead so you can see the real structure.

### Import (default)

Submits the file and polls until the slurp reaches a terminal state:

- `committed` — success (exit code 0)
- `rejected` / `invalid` / `canceled` — failure (exit code 1)

---

## How an import works (CDD Slurps flow)

The Slurps endpoint is asynchronous:

1. **Submit** — `POST /vaults/{v}/slurps` as `multipart/form-data` with a
   `file` part (your data file) and a `json` part holding
   `{project, mapping_template, runs, autoreject}`. Response includes the new
   `slurp` id.
2. **Poll** — `GET /vaults/{v}/slurps/{id}` until `state` is terminal. A clean
   import commits immediately; polling mainly catches errors and warnings.
3. **Report** — the script prints the final state plus counts of any `errors`,
   `warnings`, and `suspicious_events`.

---

## Mapping file columns to readouts

CDD needs to know which file column feeds which protocol readout. Two ways:

### `--mapping-template` (recommended)

Create the mapping **once** in the web UI (*Import Data* → map columns → save
as a named template), then reference that name on every subsequent run. This is
the confirmed, reliable path and the one the official SDK exposes.

### `--mapping-json` (inline, advanced)

Pass a JSON file whose contents are merged verbatim into the slurp payload.
This supports CDD's inline field-header mappings, but **the inline-mapping JSON
schema is not publicly documented** — you are responsible for getting the keys
right. Treat it as a power-user escape hatch; prefer a saved template.

---

## Config-driven mapping (`config/config.yaml`)

[config/config.yaml](../config/config.yaml) is the single source of truth for
*which upload-file column feeds which CDD readout*, per protocol. Each protocol
is keyed by its PID:

```yaml
vault: 7108
protocols:
  131399:
    name: MDR1-MDCK II Inhibitor
    project: FBXO31                    # CDD "Project" dropdown value
    mapping_template: "MDR1 upload"    # saved web-UI template name ("" if none yet)
    identifiers:                       # batch key — NOT a readout
      batch_id: "Batch Molecule-Batch ID"
    ignore:                            # present in file, intentionally not imported
      - "Molecule Name"
    readouts:                          # file column  ->  CDD readout id
      "Study number": 1883900
      "Mean Papp A to B": 1883902
      # ...
```

When `--protocol` is set, `--project` and `--mapping-template` fall back to the
block's `project` / `mapping_template` if not passed on the CLI (CLI wins). So
a fully-configured protocol imports with just `--protocol` + `--file`.

The three column groups the validator recognises:

- **`identifiers`** — the batch key CDD matches rows on. For MDR1 that's just
  `Batch Molecule-Batch ID` (the `SRB-XXXXXXX-NNN` value embeds the molecule,
  so `Molecule Name` is redundant as a key).
- **`ignore`** — columns that exist in the upload but shouldn't import (e.g. the
  redundant `Molecule Name`). Listed so they don't show up as errors.
- **`readouts`** — everything that maps to a protocol readout id.

Populate (or refresh) a block straight from the vault:

```bash
python3 python/import_to_protocol.py --vault 7108 --describe-protocol 131399
```

then copy each readout's `name` and `id` into the `readouts:` map. The keys
must match the upload file header **exactly** — including the inconsistent
`PgP`/`Pgp` and `inhibitor`/`Inhibitor` suffix spellings, which are real.

### `--check-mapping` — offline pre-flight

Validate an upload file against the config **before** touching the network. It
reads only the **header row** (column names — never data values) and reports
columns that won't import and readouts that will be left blank:

```bash
python3 python/import_to_protocol.py \
  --file data/uploads/my_upload.csv \
  --protocol 131399 --check-mapping
```

```
=== mapping check: protocol=131399 name='MDR1-MDCK II Inhibitor' ===
file=my_upload.csv  file_columns=30  config_readouts=26
mapped=27
ignored (1) — present but intentionally not imported:
  - 'Molecule Name'
UNMAPPED file columns (1) — no readout/identifier in config, will not import:
  - 'Run Lab'
mapping_ok=False  (resolve UNMAPPED columns first)
```

- **mapped** — file columns that match a readout or the batch identifier.
- **ignored** — file columns in the config's `ignore` list (present on purpose,
  not imported).
- **UNMAPPED** — a file column with no home in the config (a typo, a stray
  export column, or a readout missing from the config). These won't import and
  fail the check.
- **MISSING** — a config readout absent from the file (imported as blank).
- **EMPTY-IDENTIFIER rows** — data rows where every identifier column (Molecule
  Name / Batch ID) is blank. CDD rejects these outright ("Molecule Names
  Required"), so the check fails until they're removed. The count is read from
  the identifier columns only; values are never printed.

When `--protocol` is supplied on a real import, this same check runs
automatically as a pre-flight and **aborts before upload** if any column is
unmapped — so a mismatched file never reaches CDD.

---

## `autoreject` — why imports get rejected wholesale

`autoreject` defaults to **ON** (matching CDD's own API default). If the file
produces *Suspicious Events* or *Errors*, the entire slurp is rejected rather
than partially committed. This is the safe behaviour — you fix the file and
re-run, instead of ending up with half an import. Pass `--no-autoreject` only
when you deliberately want to commit despite warnings.

---

## Privacy notes

- The data file is uploaded directly to `app.collaborativedrug.com` (the vault
  service) as a binary stream. This script never opens, reads, parses, or
  prints its contents.
- stdout is metadata-only by design: ids, names, states, status codes, and
  error/warning **counts** — never row values. Safe to paste into chat for
  debugging.
- Per-row CDD error detail can contain data values, so it is **not** printed.
  Inspect the slurp in the web UI for that level of detail.

See [CLAUDE.md](../CLAUDE.md) for the full local-only data policy.

---

## Troubleshooting

| Symptom | What it means | Fix |
|---|---|---|
| `ERR list-protocols status=401/403` | Token rejected or no access to that vault | Regenerate token in CDD; check the vault id |
| `ERR submit status=401 ... "Token has insufficient access to modify data"` | Token is **read-only** — reads work but it can't import | Get a token from an account with Read & Write / import permission in the vault (ask a vault admin), regenerate it, overwrite `~/.cdd_token` |
| `ERR: supply --mapping-template NAME or --mapping-json PATH` | No mapping given | Add one of the two mapping flags |
| `final_state=rejected` + `errors=N` | Slurp hit errors and autoreject dropped it | Inspect the slurp in the web UI, fix the file, re-run |
| `final_state=invalid` | The file/mapping didn't validate | Re-check the mapping template matches the file columns |
| `WARN: poll timeout` | Import outran `--poll-timeout` | Raise `--poll-timeout`; the import may still finish server-side — re-check in the UI |
| `ERR submit: response had no 'id'` | POST succeeded but response shape was unexpected | Re-run; if persistent the API shape may have changed |

---

## When to use this vs the other scripts

| Use case | Tool |
|---|---|
| **Import** assay/readout data into a protocol | **this script** |
| **Export** collections to CSV / SDF / DataFrame | [get_library.py](../python/get_library.py) |
| Download compound structure PNGs | [download_cdd_structures.py](../python/download_cdd_structures.py) |

All three share the same auth (`X-CDD-Token`, `~/.cdd_token` default) — see
[docs/documentation.md](documentation.md) for token generation and venv setup.
