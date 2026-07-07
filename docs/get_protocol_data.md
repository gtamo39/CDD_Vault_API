# `get_protocol_data.py` — Extract Protocol (Assay) Data with SMILES

Pulls each protocol's readout data back out of CDD Vault and joins it with
molecule SMILES into **one wide table** — one row per compound, the **latest**
run per compound per assay. The read counterpart to
[import_to_protocol.py](../python/import_to_protocol.py).

Script location: [python/get_protocol_data.py](../python/get_protocol_data.py)

Protocols are selected by the short **`alias`** set in
[config/config.yaml](../config/config.yaml). With no `--experiments`, every
aliased protocol is pulled.

As with the rest of this repo, chemistry stays local: SMILES and values land
only in the CSV / returned DataFrame — never in stdout (which shows counts,
ids, and column names only).

---

## Alias map

Run `--list` to see it (from config):

```bash
python3 python/get_protocol_data.py --list
```

| Alias | Protocol | PID |
|---|---|---|
| `mdck` | MDR1-MDCK II Inhibitor | 131399 |
| `logd` | LogD | 85979 |
| `hlm` | Microsomal stability species human | 88861 |
| `mlm` | Microsomal stability species mouse | 88862 |
| `rlm` | Microsomal stability species rat | 111161 |
| `solubility` | Thermodynamic Solubility | 86015 |
| `ppb` | Plasma protein binding (UC) | 125963 |
| `caco2` | Caco-2 permeability | 85977 |

Add or change an alias by editing the `alias:` field in that protocol's block
in `config.yaml`.

---

## Quick start

**CLI** — writes a wide CSV:

```bash
# Specific assays
python3 python/get_protocol_data.py --experiments solubility,logd --output ./assays.csv

# All aliased protocols
python3 python/get_protocol_data.py --output ./all_assays.csv
```

**Python / notebook** — returns a `pandas.DataFrame`:

```python
import sys; sys.path.insert(0, 'python')
from get_protocol_data import get_data

df = get_data(experiments=['solubility', 'logd', 'ppb'])   # omit for all
```

**Walkthrough notebook:** [vignettes/sample_protocol_download.ipynb](../vignettes/sample_protocol_download.ipynb)
— step-by-step (list aliases → one assay → several merged → all → save CSV →
checks). From the notebook, pass `config_path='../config/config.yaml'` since it
runs from `vignettes/`.

---

## Output shape

One **merged wide** DataFrame, one row per compound (joined on molecule id):

- `molecule` — CDD internal molecule id (join key)
- `name`, `smiles` — molecule columns (configurable, see `--mol-columns`)
- per requested assay, prefixed by its alias:
  - `<alias>_run_date` — date of the run the values came from
  - `<alias>_<readout>` — one column per readout (e.g. `logd_LogD7.4`,
    `ppb_%Unbound`, `mdck_Mean Papp A to B`)

Because each assay's columns are alias-prefixed, assays with different readouts
sit side by side without collisions. A compound missing from an assay has NaN
in that assay's columns.

**Latest per compound:** if a compound has several runs in an assay, only the
most recent (by `run_date`, tie-break run id) is kept — a current-state SAR
snapshot. Use the raw endpoints if you need full history.

---

## CLI arguments

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--vault N` | int | `7108` | CDD Vault id. |
| `--experiments "a,b"` | str | all aliased | Comma-separated aliases (e.g. `logd,ppb`). Omit for every aliased protocol. |
| `--config PATH` | path | `config/config.yaml` | Config holding the `alias:` → protocol map. |
| `--token-file PATH` | path | `~/.cdd_token` | One-line API token file. |
| `--mol-columns "a,b"` | str | `name,smiles` | Molecule fields to include (any molecule top-level field, e.g. `name,smiles,inchi_key,molecular_weight`). |
| `--output PATH` | path | `./protocol_data.csv` | CSV to write. |
| `--page-size N` | int | `1000` | CDD pagination size. |
| `--list` | flag | off | Print the alias → protocol map, then exit. |

`get_data(...)` takes the same options as keyword args (`experiments`,
`vault`, `token`/`token_file`, `config_path`, `mol_columns`, `page_size`,
`verbose`) and returns the DataFrame instead of writing a file.

---

## How it works (CDD endpoints)

All GET, paginated (`{count, objects, offset, page_size}`):

1. `GET /vaults/{v}/protocols/{id}` → readout definitions (`id → name`).
2. `GET /vaults/{v}/protocols/{id}/data` → rows
   `{id, molecule, batch, run, readouts:{<rid>:{value, outlier?}}}`.
3. `GET /vaults/{v}/runs?protocols={id}` → `run_date` per run (for "latest").
4. `GET /vaults/{v}/molecules?molecules=<ids>` → `name`, `smiles`, … for just
   the compounds that have data (the `?molecules=` id filter *is* honored,
   unlike the collection filter — see the get_library quirks).

Per assay: map readout ids → names, keep the latest row per molecule, prefix
columns with the alias. Then all assays are merged on `molecule`, with molecule
columns joined once.

---

## Examples

**All ADME assays for a full SAR table:**

```bash
python3 python/get_protocol_data.py --output ./adme_all.csv
```

**Add molecular descriptors** to the molecule columns:

```bash
python3 python/get_protocol_data.py --experiments logd,solubility \
  --mol-columns "name,smiles,inchi_key,molecular_weight,log_p" \
  --output ./logd_sol.csv
```

**In a notebook**, then filter/plot directly:

```python
from get_protocol_data import get_data
df = get_data(experiments=['hlm', 'mlm', 'rlm'])   # microsomal stability, 3 species
df[['name', 'hlm_CLint (raw)', 'mlm_CLint (raw)', 'rlm_CLint (raw)']].dropna()
```

---

## Privacy

Local-only, same rules as the rest of the repo (see [CLAUDE.md](../CLAUDE.md)):
SMILES and readout values appear only in the returned DataFrame / output CSV.
stdout is metadata only — experiment names, row/compound/readout counts, column
names, the output path. Safe to paste for debugging.

## When to use this vs the other scripts

| Use case | Tool |
|---|---|
| **Extract** assay data (readouts) + SMILES into a table | **this script** |
| **Import** assay data into a protocol | [import_to_protocol.py](../python/import_to_protocol.py) |
| Export collection membership / molecule fields to CSV / SDF | [get_library.py](../python/get_library.py) |
| Download compound structure PNGs | [download_cdd_structures.py](../python/download_cdd_structures.py) |
