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
echo 'YOUR_TOKEN_HERE' > ~/.cdd_token
chmod 600 ~/.cdd_token
```

Get the token from CDD Vault → your name (top-right) → **My Account** →
**API Tokens** → **Generate New Token**.

## Quick start

CLI:

```bash
python3 python/get_library.py \
  --vault 7108 \
  --collections AJ,AK \
  --output ./library.csv
```

Or open [vignettes/Sample_library_download.ipynb](vignettes/Sample_library_download.ipynb)
and pick the **Python (CDD)** kernel.

See **[docs/documentation.md](docs/documentation.md)** for the full CLI
reference (every flag, the column-resolution chain, the UDF field catalog,
and end-to-end examples).

## Repository layout

| Path | Purpose |
|---|---|
| [python/get_library.py](python/get_library.py) | Export CDD collections to CSV |
| [python/download_cdd_structures.py](python/download_cdd_structures.py) | Fetch compound structure PNGs for a saved search |
| [vignettes/](vignettes/) | Jupyter walkthroughs |
| [docs/documentation.md](docs/documentation.md) | Full CLI + field reference |
| [CLAUDE.md](CLAUDE.md) | Collaboration rules + local-only data policy |
| `data/`, `output/` | Local-only (gitignored) — never commit |
