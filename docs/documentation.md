# `get_library.py` — CDD Vault Collection Exporter

Exports one or more CDD Vault Collections to a single CSV file. All chemistry
stays on the local machine; the script's stdout shows only counts, status
codes, field names, and numeric IDs (never SMILES, InChI, structures, or
compound names like `SRB-XXXXXXX`).

Script location: [python/get_library.py](../python/get_library.py)

---

## Quick start

```bash
# Save your CDD Vault API token (one line, no whitespace)
echo 'YOUR_TOKEN_HERE' > ~/.cdd_token
chmod 600 ~/.cdd_token

# Install the only dependency (use a project-local venv, not base conda)
pip install requests

# Default: export collections AJ and AK from vault 7108 to ./library.csv
python3 python/get_library.py --vault 7108 --collections AJ,AK
```

The default output has three columns (`collection,name,smiles`) and one row
per (molecule, batch).

Or use the Python API to get a DataFrame directly:

```python
import sys; sys.path.insert(0, 'python')
from get_library import get_df

df = get_df(vault=7108, collections=['AJ', 'AK'])
```

See [Programmatic API (`get_df`)](#programmatic-api-get_df) below for the
full signature.

---

## CLI arguments

| Flag                 | Type        | Default                       | Description                                                                                                |
| -------------------- | ----------- | ----------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `--vault N`          | int         | `7108`                        | CDD Vault numeric ID.                                                                                      |
| `--collections "A,B"`| str         | _one of these three required_ | Comma-separated collection **names** to export. Mutually exclusive with `--collection-ids` / `--all-collections`. |
| `--collection-ids "I,J"` | str     | _one of these three required_ | Comma-separated numeric **collection IDs**. Useful when names are ambiguous.                              |
| `--all-collections`  | flag        | off                           | Export **every** collection in the vault. Mutually exclusive with `--collections` / `--collection-ids`.    |
| `--token-file PATH`  | path        | `~/.cdd_token`                | File containing the API token on one line.                                                                  |
| `--output PATH`      | path        | `./library.csv`               | Where to write the file. Parent directory created if missing. UTF-8. Extension drives the default format (`.sdf` → SDF, anything else → CSV) unless `--format` overrides. |
| `--columns "a,b,c"`  | str         | `collection,name,smiles`      | Comma-separated column list. Names are resolved across five namespaces (see [Column resolution](#column-resolution)). Names may contain spaces if shell-quoted (e.g. `"Lib ID"`). In SDF mode, each column becomes a property tag. |
| `--format csv\|sdf`  | str         | inferred from `--output`      | Output format. Set explicitly to override the extension-based default. CSV writes one row per (molecule, batch); SDF writes one record per (molecule, batch), structure from `molfile`, columns as property tags. |
| `--limit N`          | int         | _no limit_                    | Cap rows **per collection**. Useful for smoke tests. Counts rows (one per batch), not molecules.            |
| `--page-size N`      | int         | `1000`                        | CDD API page size for the molecule listing. Rarely needs tuning.                                            |
| `--discover`         | flag        | off                           | Probe one collection: dump field counts, smiles-present check, and meta-endpoint comparison. Writes no CSV. |
| `--list-fields`      | flag        | off                           | List every available column name per namespace for the requested collection(s), with coverage counts. Writes no CSV. |

> **Note on `--discover` vs `--list-fields`:** `--discover` shows the top-level
> listing fields and the meta-endpoint comparison for the **first page only**;
> it's useful for a quick sanity check that the API is responding. `--list-fields`
> scans the **entire collection** and lists all three namespaces (top-level,
> `molecule_fields`, `batch_fields`) with full coverage counts; it's the
> reference you'd consult before picking columns.

---

## Modes

### Export mode (CSV or SDF)

Writes one row/record per `(molecule, batch)` pair. Molecules with multiple
batches produce multiple rows (molecule-level columns repeat); molecules
with no batches produce one row with batch-level cells empty.

**Format selection.** By default the script picks CSV or SDF from the
`--output` file extension (`.sdf` → SDF, anything else → CSV). Pass
`--format csv|sdf` to override.

**CSV vs SDF — when to use which:**

|                            | CSV                                                                | SDF                                                                                 |
| -------------------------- | ------------------------------------------------------------------ | ----------------------------------------------------------------------------------- |
| **Primary use**            | Spreadsheets, pandas, ML pipelines, joining with non-chem data     | Chemistry software (RDKit, ChemDraw, KNIME, OpenBabel) that needs 2D/3D structures  |
| **How structure appears**  | Only if you request `smiles` / `molfile` / `inchi` in `--columns` (as text cells)  | Always present — the `molfile` block is the structure, independent of `--columns`   |
| **Columns / properties**   | Header row + tabular rows; one cell per column, empty values kept as empty cells | Per record: `>  <Name>` then value, blank line as separator; empty values omitted   |
| **Record terminator**      | Newline                                                            | `$$$$`                                                                              |
| **Multi-line values**      | Awkward (need quoting); we don't currently quote-escape internal newlines | Native — each line in a value just adds a line under the property tag               |
| **Typical size**           | ~1 MB for AJ+AK (~8K rows, 3 columns)                              | ~10–30 MB for the same data (every record carries a full molfile block, ~1–3 KB)    |
| **Skipped molecules**      | None — every (molecule, batch) row is written                      | Records with an empty `molfile` are skipped and reported as `records_skipped_no_molfile` |
| **Required deps**          | `requests` (stdlib `csv`)                                          | `requests` only — molfile comes verbatim from CDD's listing, no RDKit needed        |

**Rule of thumb:** if you'll open it in Excel/pandas/Jupyter, use CSV. If
you'll open it in a chemistry tool that wants to render structures, use SDF.
Generate both if you need both — they're independent runs and the column
list can differ.

**CSV output:**

```bash
python3 python/get_library.py \
  --vault 7108 \
  --collections AJ,AK \
  --columns "collection,name,smiles,Subseries,Lib ID,Px_anywhere,Px_screened_anywhere" \
  --output ./library.csv
```

**SDF output:** the requested columns become SDF property tags (`>  <Column>`),
and the structure block is each molecule's `molfile` field — which CDD's
listing returns directly, so there's no extra fetch and no RDKit dependency.
Molecules with an empty `molfile` are skipped and reported.

```bash
python3 python/get_library.py \
  --vault 7108 \
  --collections AJ,AK \
  --columns "collection,name,Subseries,Lib ID,Px_anywhere" \
  --output ./library.sdf
```

At the end of an export run the script prints:

- `total_rows_written=N` (CSV) or `total_records_written=N` (SDF)
- `records_skipped_no_molfile=N` (SDF only, if any)
- `multi_batch_molecules=N` — molecules that emitted >1 row/record
- `zero_batch_molecules=N` — molecules with no batches (rare)
- `WARN: column 'X' never matched any namespace …` — typo guard (full exports only)

When `--limit` is set, the typo guard is suppressed because a sparse-but-real
field (e.g. one present on 20% of molecules) can be falsely flagged from a
small sample.

### `--discover` mode

One-line-per-field probe of the first page of one collection. Compares the
listing fields against the per-molecule meta endpoint. Use this to confirm
the API is responding and the endpoint shape hasn't changed.

```bash
python3 python/get_library.py --vault 7108 --collections AJ --discover
```

### `--list-fields` mode

Walks every molecule in the requested collection(s) and tallies what columns
are available across three namespaces:

- **Top-level listing fields** (denominator = molecules) — fields returned
  directly on each molecule object
- **Molecule UDFs** (`molecule_fields`, denominator = molecules) — user-defined
  fields at the molecule level
- **Batch UDFs** (`batch_fields`, denominator = **batches**) — user-defined
  fields at the batch level

Use this to discover what column names exist before picking `--columns`.

```bash
python3 python/get_library.py --vault 7108 --collections AJ,AK --list-fields
```

---

## Programmatic API (`get_df`)

For notebook or scripting use, `get_df()` returns a `pandas.DataFrame` from the
same primitives the CLI uses — one row per (molecule, batch), same five-step
column resolution. `pandas` is lazily imported, so the CLI path stays
dependency-light.

```python
from get_library import get_df

df = get_df(
    vault: int,
    collections=None,           # list[str] or 'AJ,AK' or None
    collection_ids=None,        # list[int|str] or '931034,931035' or None
    columns=None,               # list[str] or 'a,b,c'; default ['collection','name','smiles']
    token=None,                 # raw token string; falls back to token_file
    token_file='~/.cdd_token',  # one-line token file
    limit=None,                 # cap rows per collection (smoke test)
    page_size=1000,             # CDD API page size
    verbose=True,               # print progress to stdout
)
```

Each argument maps one-to-one to a CLI flag — see the [CLI arguments](#cli-arguments)
table for the same defaults and semantics. The only differences:

- `collections` accepts either a list (`['AJ', 'AK']`) or comma string (`'AJ,AK'`).
  Pass an **empty list** (`collections=[]`) to fetch every collection in the
  vault — the equivalent of the CLI's `--all-collections`. `collections=None`
  (the default) still requires one of `collections` / `collection_ids`.
- `collection_ids` likewise
- `columns` likewise; default is `['collection', 'name', 'smiles']`
- `verbose=False` suppresses the per-collection progress prints
- **SDF output is CLI-only.** `get_df()` always returns a DataFrame. If you
  need SDF from Python, call `export_sdf()` directly (same arguments as
  `export_csv`) or shell out to the CLI with `--output ./library.sdf`.

### Notebook example

The companion notebook is [vignettes/Sample_library_download.ipynb](../vignettes/Sample_library_download.ipynb).
The setup cell uses `%autoreload 2` so edits to `python/get_library.py`
propagate without restarting the kernel.

```python
%load_ext autoreload
%autoreload 2

import os, sys
sys.path.insert(0, os.path.abspath('../python'))

import pandas as pd
from get_library import get_df

df = get_df(
    vault=7108,
    collections=['AJ', 'AK'],
    columns=[
        'collection', 'name', 'molecule_batch_identifier',
        'smiles', 'molecular_weight', 'log_p',
        'Subseries', 'Px_anywhere',
        'Lib ID', 'Plate ID', 'Px_screened_anywhere',
    ],
)
```

> **Privacy reminder:** `df.head()` in a notebook will render SMILES and
> compound names into cell output. Clear all outputs before committing the
> notebook (`jupyter nbconvert --clear-output --inplace <notebook.ipynb>`).
> The Python API is otherwise local-only by the same rules as the CLI.

---

## Column resolution

Each name in `--columns` is looked up in this order; the **first hit wins**:

| Order | Namespace                  | Lookup                                  | Examples                                              |
| ----- | -------------------------- | --------------------------------------- | ----------------------------------------------------- |
| 1     | Special                    | resolved collection name                | `collection`                                          |
| 2     | Molecule top-level         | `obj[col]`                              | `name`, `smiles`, `inchi_key`, `molecular_weight`     |
| 3     | Batch top-level            | `batch[col]` (when batch is not `None`) | `molecule_batch_identifier`, `formula_weight`, `salt_name` |
| 4     | Molecule UDF               | `obj["molecule_fields"][col]`           | `Subseries`, `Px_anywhere`, `docking_score`           |
| 5     | Batch UDF                  | `batch["batch_fields"][col]`            | `Lib ID`, `Plate ID`, `Px_screened_anywhere`          |

Unknown names produce empty cells and a single stderr WARN per name at the
end of a full export. Nested dict/list values (e.g. `docking_pose`) are
JSON-encoded into the cell.

---

## Row model

- **One row per `(molecule, batch)` pair.** Multi-batch molecules emit one
  row per batch; molecule-level columns (e.g. `smiles`, `Subseries`) repeat
  identically across those rows; batch-level columns vary.
- **Molecules with zero batches** emit one row with batch-level cells empty.
- **`--limit N`** caps rows per collection, not molecules — useful for smoke
  testing on small samples.
- The `collection` column always reflects the **requested** collection (the
  one named in `--collections`). A molecule that's a member of both AJ and AK
  appears in both export passes if both are requested, with different
  `collection` values.

Multi-batch is rare in practice. Example coverage for AJ from a recent run:
7,754 molecules with 1 batch, 100 with 2 batches, 5 with 3 batches, 1 with 4
batches (≈1.3% are multi-batch).

---

## Privacy and data-handling

This script is **local-only by design**. Per the project's [CLAUDE.md](../CLAUDE.md):

- Chemistry data (SMILES, InChI, MOL, structures, descriptors, compound names
  like `SRB-XXXXXXX`) **never appears in stdout**. Only counts, status codes,
  field names, and numeric IDs are printed.
- The CSV on disk is the only place chemistry values land.
- Token file is read from `~/.cdd_token` by default; never logged.

If you re-run with `--discover` or `--list-fields`, the output is metadata
only (field names, counts) — safe to paste into chat or share for debugging.

---

## Field reference

The full set of column names available for `--columns`, with coverage counts
from a recent run on **vault 7108** (collections AJ and AK). Use this as
a starting point; UDFs evolve over time, so re-run `--list-fields` if a
column suddenly stops resolving.

### Collection AK (25 molecules, 25 batches)

**Top-level listing fields** (denominator = 25 molecules)

```
id                              25/25
class                           25/25
created_at                      25/25
modified_at                     25/25
name                            25/25
synonyms                        25/25
registration_type               25/25
registration_form               25/25
projects                        25/25
collections                     25/25
owner                           25/25
smiles                          25/25
cxsmiles                        25/25
inchi                           25/25
inchi_key                       25/25
iupac_name                      25/25
molfile                         25/25
molecular_weight                25/25
log_p                           25/25
log_d                           25/25
log_s                           25/25
num_aromatic_rings              25/25
num_h_bond_donors               25/25
num_h_bond_acceptors            25/25
num_rule_of_5_violations        25/25
formula                         25/25
isotope_formula                 25/25
p_k_a                           25/25
p_k_a_type                      25/25
p_k_a_acidic                    25/25
exact_mass                      25/25
heavy_atom_count                25/25
composition                     25/25
isotope_composition             25/25
topological_polar_surface_area  25/25
num_rotatable_bonds             25/25
cns_mpo_score                   25/25
bbb2_score                      25/25
fsp3                            25/25
batches                         25/25
source_files                    25/25
molecule_fields                 25/25
udfs                            25/25
p_k_a_basic                      7/25
```

**Molecule UDFs** (`molecule_fields`, denominator = 25 molecules)

```
Subseries           25/25
Series              25/25
Stereo_Descriptor   25/25
Type                25/25
docking_pose        23/25
docking_score       23/25
Px_anywhere         13/25
Flag                 5/25
HasFlag              5/25
Stereo_Comment       1/25
IUPAC Name           1/25
```

**Batch UDFs** (`batch_fields`, denominator = 25 batches)

```
Initial Amount            25/25
Current Amount            25/25
Location                  25/25
Vendor_Batch_ID           25/25
Batch_Created_Date        25/25
Purity_Percent            25/25
Vendor-CRO                25/25
Stock last update         25/25
Lib Flavor                25/25
temp_Concentration        24/25
Tube position             24/25
Tube ID                   24/25
Plate ID                  24/25
Sol (uL@10mM)_WuXi        24/25
Sol (uL@10mM)_vial        24/25
CRO_Molecule_ID           22/25
Solid (mg)_WuXi           20/25
Solid (mg)_WuXi_vial      20/25
Largest_Vector            19/25
Largest_Vector_Lenght     19/25
MaSIF Score               16/25
MaSIF Score Apo           16/25
MaSIF delta Score         16/25
Px_screened_anywhere      13/25
Px_screened_source        13/25
Px_screened_library       13/25
Px_screened_date          13/25
Px internally              9/25
Prot_Deg_DMSO              6/25
Prot_Deg_bin_DMSO          6/25
ADME training set          4/25
Px at BMS                  4/25
temp_Volume                3/25
SFC_comments               1/25
```

### Collection AJ (7,860 molecules, 7,973 batches)

**Top-level listing fields** (denominator = 7,860 molecules)

```
id                              7860/7860
class                           7860/7860
created_at                      7860/7860
modified_at                     7860/7860
name                            7860/7860
synonyms                        7860/7860
registration_type               7860/7860
registration_form               7860/7860
projects                        7860/7860
collections                     7860/7860
owner                           7860/7860
smiles                          7860/7860
cxsmiles                        7860/7860
inchi                           7860/7860
inchi_key                       7860/7860
molfile                         7860/7860
molecular_weight                7860/7860
log_p                           7860/7860
log_d                           7860/7860
log_s                           7860/7860
num_aromatic_rings              7860/7860
num_h_bond_donors               7860/7860
num_h_bond_acceptors            7860/7860
num_rule_of_5_violations        7860/7860
formula                         7860/7860
isotope_formula                 7860/7860
exact_mass                      7860/7860
heavy_atom_count                7860/7860
composition                     7860/7860
isotope_composition             7860/7860
topological_polar_surface_area  7860/7860
num_rotatable_bonds             7860/7860
cns_mpo_score                   7860/7860
bbb2_score                      7860/7860
fsp3                            7860/7860
batches                         7860/7860
source_files                    7860/7860
molecule_fields                 7860/7860
udfs                            7860/7860
iupac_name                      7851/7860
p_k_a                           7818/7860
p_k_a_type                      7818/7860
p_k_a_acidic                    7780/7860
p_k_a_basic                     4929/7860
```

**Molecule UDFs** (`molecule_fields`, denominator = 7,860 molecules)

```
Subseries              7860/7860
Series                 7853/7860
Type                   7853/7860
Stereo_Descriptor      7851/7860
Stereo_Comment         2568/7860
Px_anywhere            1915/7860
IUPAC Name             1363/7860
docking_pose           1013/7860
docking_score          1013/7860
in_Patent               525/7860
num_in_Patent           525/7860
Flag                    117/7860
HasFlag                 117/7860
Parent_of               111/7860
In_Patent #5 temp        74/7860
num_in_Patent #5 temp    74/7860
Evotec Profile           65/7860
Evotec Vector            65/7860
Comments                 16/7860
Control Type              7/7860
PROTAC Target             6/7860
Name                      4/7860
```

**Batch UDFs** (`batch_fields`, denominator = 7,973 batches)

```
Batch_Created_Date         7973/7973
Vendor_Batch_ID            7973/7973
Initial Amount             7973/7973
Current Amount             7973/7973
Stock last update          7973/7973
Location                   7966/7973
Purity_Percent             7965/7973
CRO_Molecule_ID            7958/7973
Vendor-CRO                 7928/7973
Lib Flavor                 7674/7973
Largest_Vector             7633/7973
Largest_Vector_Lenght      7625/7973
Tube position              7476/7973
Tube ID                    7476/7973
Plate ID                   7476/7973
Lib ID                     6975/7973
Lib Number                 6571/7973
Px_screened_anywhere       5591/7973
Px_screened_source         5591/7973
Px_screened_library        5591/7973
Px_screened_date           5591/7973
Batch Mol-Batch ID         2509/7973
SFC_comments               2406/7973
Sol (uL@10mM)_vial         2307/7973
Sol (uL@10mM)_WuXi         2307/7973
MaSIF Score                2170/7973
MaSIF Score Apo            2170/7973
MaSIF delta Score          2170/7973
Solid (mg)_WuXi_vial       2013/7973
Solid (mg)_WuXi            2013/7973
Prot_Deg_DMSO              1517/7973
Prot_Deg_bin_DMSO          1517/7973
Px internally              1027/7973
Px at BMS                  1016/7973
temp_Concentration          764/7973
Salt_Equivalent             400/7973
temp_Volume                 278/7973
Lib Vendor                  268/7973
temp_STAT6_DMax10uM BD      230/7973
ADME training set           110/7973
temp_STAT6_DMax10uM          70/7973
Px Evotec                    66/7973
Px_screened_order            56/7973
Note                         19/7973
Internal QC LC/MS            12/7973
Place                         4/7973
```

> **Snapshot date:** 2026-05-20. UDFs in CDD evolve as the chemistry team
> adds/retires fields. Re-run `--list-fields` to refresh.

---

## Examples

**Smoke test before a big run.** Cap to 3 rows per collection, mixed namespaces:

```bash
python3 python/get_library.py \
  --vault 7108 --collections AJ \
  --columns "collection,name,smiles,Subseries,Lib ID,Px_anywhere" \
  --limit 3 \
  --output /tmp/smoke.csv
```

**Just see what columns exist** for a collection you haven't worked with yet:

```bash
python3 python/get_library.py --vault 7108 --collections AJ --list-fields
```

**Export by numeric collection ID** when names collide or you need them
unambiguous:

```bash
python3 python/get_library.py \
  --vault 7108 --collection-ids 931034,931035 \
  --output ./library.csv
```

**Export the entire vault** — every collection, no need to list names:

```bash
python3 python/get_library.py \
  --vault 7108 --all-collections \
  --output ./library_all.csv
```

The Python equivalent is `get_df(vault=7108, collections=[])`. Note a molecule
that belongs to several collections is emitted once per collection (with the
matching `collection` value), so the full-vault export can contain duplicate
structures across `collection` groups.

**Wide export** with batch identifiers and screening metadata:

```bash
python3 python/get_library.py \
  --vault 7108 --collections AJ,AK \
  --columns "collection,name,molecule_batch_identifier,smiles,inchi_key,molecular_weight,Subseries,Lib ID,Plate ID,Tube ID,Px_anywhere,Px_screened_anywhere,Px_screened_date" \
  --output ./library_wide.csv
```

**SDF export** for downstream RDKit / OpenBabel / ChemDraw workflows. Each
record's structure block is the molecule's CDD `molfile`; the requested
columns become SDF property tags. No RDKit needed on this side — the script
just emits text around the molfile.

```bash
python3 python/get_library.py \
  --vault 7108 --collections AJ,AK \
  --columns "collection,name,molecule_batch_identifier,Subseries,Lib ID,Px_anywhere,Px_screened_anywhere" \
  --output ./library.sdf
```

(Format is auto-detected from the `.sdf` extension. To force a specific
format regardless of extension, add `--format sdf` or `--format csv`.)

---

## Known CDD API quirks

- The endpoint `/vaults/{v}/collections/{cid}/molecules` returns the full vault
  listing **regardless of `{cid}`** — the path segment is ignored. The script
  filters client-side via each molecule's `collections` field.
- `GET /vaults/{v}/molecules?collections={cid}` returns **404** under every
  parameter spelling tried (`collection`, `collections`, `collection_id`,
  `collection_ids`).
- `POST /vaults/{v}/searches` and `POST /vaults/{v}/molecules/search` return
  **404** — saved searches can't be created via the API.

Practical consequence: every export walks the full vault listing once per
requested collection (~10s per collection for a 10K-molecule vault), then
filters in Python.
