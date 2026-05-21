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

## Repository layout

| Path | Purpose |
|---|---|
| [python/get_library.py](python/get_library.py) | Export CDD collections to CSV |
| [python/download_cdd_structures.py](python/download_cdd_structures.py) | Fetch compound structure PNGs for a saved search |
| [vignettes/](vignettes/) | Jupyter walkthroughs |
| [docs/documentation.md](docs/documentation.md) | Full CLI + field reference |
| [CLAUDE.md](CLAUDE.md) | Collaboration rules + local-only data policy |
| `data/`, `output/` | Local-only (gitignored) — never commit |
