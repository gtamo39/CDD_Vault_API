# CDD Vault API extraction

Export CDD Vault collections to a CSV locally. Two ways to drive it:
the Python CLI ([python/get_library.py](python/get_library.py)) or the
Jupyter walkthrough ([vignettes/Sample_library_download.ipynb](vignettes/Sample_library_download.ipynb)).

Chemistry data (SMILES, InChI, structures, compound names) stays on this
machine — see [CLAUDE.md](CLAUDE.md) for the policy.

## Setup

```bash
# Virtual env (named "cdd" by convention)
python3 -m venv cdd
source cdd/bin/activate

# Dependencies
pip install -r requirements.txt

# (Optional, for the notebook) register the venv as a Jupyter kernel
python -m ipykernel install --user --name cdd --display-name "Python (CDD)"

# CDD Vault API token — one line, never commit
# Must have READ/WRITE access (read-only tokens can export but NOT import —
# the Slurps import returns 401 "Token has insufficient access to modify data").
echo 'YOUR_TOKEN_HERE' > ~/.cdd_token
chmod 600 ~/.cdd_token
```

Get the token from CDD Vault → your name (top-right) → **My Account** →
**API Tokens** → **Generate New Token**. For importing data
([import_to_protocol.py](python/import_to_protocol.py)), the token's account
needs **Read & Write** permission in the vault — a read-only token can export
collections but will be rejected on import.

## Quick start

Three equivalent entry points — pick whichever fits your workflow.

**CLI** — writes a CSV:

```bash
python3 python/get_library.py \
  --vault 7108 \
  --collections AJ,AK \
  --output ./library.csv
```

**Python** — returns a `pandas.DataFrame`:

```python
import sys; sys.path.insert(0, 'python')
from get_library import get_df

df = get_df(
    vault=7108,
    collections=['AJ', 'AK'],
    columns=['collection', 'name', 'smiles', 'Subseries', 'Lib ID'],
)
```

**Notebook** — open [vignettes/Sample_library_download.ipynb](vignettes/Sample_library_download.ipynb)
and pick the **Python (CDD)** kernel.

See **[docs/documentation.md](docs/documentation.md)** for the full reference
— every CLI flag, the `get_df` signature, the column-resolution chain, the
UDF field catalog, and end-to-end examples.

## Import protocol (assay) data into CDD Vault

Bulk-import a data file into a CDD **protocol** via the Slurps API, using
[python/import_to_protocol.py](python/import_to_protocol.py). The file is
streamed to CDD as a binary upload — the script never reads or prints your row
values; stdout is metadata only. Full reference:
**[docs/import_to_protocol.md](docs/import_to_protocol.md)**.

Step-by-step:

**1. Find the protocol id (PID).** Lists every protocol; writes nothing:

```bash
python3 python/import_to_protocol.py --vault 7108 --list-protocols
```

**2. Inspect its readouts.** Prints each readout's id, name, and type — these
are what your columns map to:

```bash
python3 python/import_to_protocol.py --vault 7108 --describe-protocol <PID>
```

**3. Record the mapping in config.** Add a block under the PID in
[config/config.yaml](config/config.yaml) — `identifiers` (the batch key),
`ignore` (columns present but not imported), and `readouts` (file column →
readout id). The MDR1 protocol (PID 131399) is already filled in as a template.

**4. Validate offline.** Checks your file's header against the config — no
token, no network, header column names only (never data). Flags `UNMAPPED`
columns (typos/strays) and `MISSING` readouts:

```bash
python3 python/import_to_protocol.py \
  --file data/uploads/<your_file>.csv --protocol <PID> --check-mapping
```

**5. Create a mapping template in the web UI** (one time). CDD's *Import Data*
wizard → map columns → **save as a named template**. This is the committed
import path (the inline-from-config route isn't publicly documented yet).

**6. Dry-run.** Builds and prints the exact payload; posts nothing. Optionally
attach run metadata with `--run-date` / `--run-place` / `--run-person` /
`--run-conditions`:

```bash
python3 python/import_to_protocol.py --vault 7108 \
  --file data/uploads/<your_file>.csv --protocol <PID> \
  --project "<PROJECT>" --mapping-template "<TEMPLATE>" \
  --run-date 2026-06-09 --dry-run
```

**7. Import.** Drop `--dry-run` to submit + poll until `committed`. The header
pre-flight (step 4) runs automatically and aborts before upload if anything is
unmapped. `autoreject` is ON by default — a file with errors is rejected
wholesale rather than partly committed.

```bash
python3 python/import_to_protocol.py --vault 7108 \
  --file data/uploads/<your_file>.csv --protocol <PID> \
  --project "<PROJECT>" --mapping-template "<TEMPLATE>" \
  --run-date 2026-06-09
```

**Undo an import.** To remove a run created by an import, delete it from the
protocol's **Runs** tab in the web UI — see CDD's guide:
[How do I delete a run of a protocol?](https://support.collaborativedrug.com/hc/en-us/articles/214358663-How-do-I-delete-a-run-of-a-protocol)

## Extract protocol (assay) data

Pull each protocol's readout data back out of CDD, joined with molecule SMILES,
into one wide table (one row per compound, latest run per compound). Assays are
picked by the short `alias` set in [config/config.yaml](config/config.yaml)
(`mdck`, `logd`, `hlm`, `mlm`, `rlm`, `solubility`, `ppb`, `caco2`). Full
reference: **[docs/get_protocol_data.md](docs/get_protocol_data.md)**.

**CLI** — writes a wide CSV:

```bash
python3 python/get_protocol_data.py --experiments solubility,logd --output ./assays.csv
python3 python/get_protocol_data.py --list      # show the alias -> protocol map
python3 python/get_protocol_data.py             # all aliased protocols
```

**Python** — returns a `pandas.DataFrame`:

```python
import sys; sys.path.insert(0, 'python')
from get_protocol_data import get_data

df = get_data(experiments=['solubility', 'logd', 'ppb'])   # omit for all
```

**Notebook** — [vignettes/sample_protocol_download.ipynb](vignettes/sample_protocol_download.ipynb)
walks through it step by step.

## Repository layout

| Path | Purpose |
|---|---|
| [python/get_library.py](python/get_library.py) | Export CDD collections to CSV / SDF / `pandas.DataFrame` |
| [python/download_cdd_structures.py](python/download_cdd_structures.py) | Fetch compound structure PNGs for a saved search — see [docs/download_cdd_structures.md](docs/download_cdd_structures.md) |
| [python/import_to_protocol.py](python/import_to_protocol.py) | Bulk-import a data file into a CDD protocol (Slurps API); `--list-protocols` finds the PID — see [docs/import_to_protocol.md](docs/import_to_protocol.md) |
| [python/get_protocol_data.py](python/get_protocol_data.py) | Extract protocol assay data + SMILES into one wide table (latest per compound); pick assays by alias (`--experiments logd,ppb`) — see [docs/get_protocol_data.md](docs/get_protocol_data.md) |
| [python/split_species.py](python/split_species.py) | Split an `_upl.csv` into one CSV per species (e.g. MMS → `_Human_upl.csv` / `_Mouse_upl.csv`); Python port of the ADME HTML tool's species split |
| [python/convert_dataset.py](python/convert_dataset.py) | `convert_to_target_format` — long→wide pivot for MDR1-style data |
| [vignettes/](vignettes/) | Jupyter walkthroughs — `Sample_library_download.ipynb`, `convert_dataset.ipynb`, `sample_protocol_download.ipynb` |
| [tests/](tests/) | `unittest` suite (run with `python -m unittest discover tests`) |
| [docs/documentation.md](docs/documentation.md) | Full CLI + field reference |
| [CLAUDE.md](CLAUDE.md) | Collaboration rules + local-only data policy |
| `data/`, `output/` | Local-only (gitignored) — never commit |

## Tests

Hermetic `unittest` suite — synthetic `TEST-XXXX` compound IDs only, no real
chemistry, no network calls. Run from the repo root with the project venv
activated:

```bash
# All tests
python -m unittest discover tests -v

# Just one file
python -m unittest tests.test_convert_dataset -v
```

Current coverage:

| Module under test | Test file |
|---|---|
| [python/convert_dataset.py](python/convert_dataset.py) | [tests/test_convert_dataset.py](tests/test_convert_dataset.py) — 13 tests covering the long→wide pivot, unit-suffix handling, base/inhibitor splits, outer-merge behavior, type coercion, and input-validation guards |
