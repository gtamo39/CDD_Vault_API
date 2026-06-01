"""MDR1 long → wide DataFrame conversion.

Canonical home for `convert_to_target_format`. The companion notebook
[vignettes/convert_dataset.ipynb](../vignettes/convert_dataset.ipynb)
imports this module (with `%autoreload 2`) and unit tests live in
[tests/test_convert_dataset.py](../tests/test_convert_dataset.py).

Background: a CDD CSV export gives one row per (compound, assay condition).
The downstream schema in `mdr1_conversion.xlsx` (sheet 'target') wants one
row per compound with each result column duplicated as `<X>` and
`<X> - PgP inhibitor`. This module pivots accordingly.
"""
import re

import pandas as pd


def normalize_ori_columns(
    ori_df: pd.DataFrame,
    col_ori: pd.DataFrame,
    ori_prefix: str = 'MDR1-MDCK II: ',
) -> pd.DataFrame:
    """Strip the CDD source prefix and apply col_ori's unit-suffix rename.

    Shared between `convert_to_target_format` (step 1 + 1b) and notebook
    visualizers so the two stay in sync — anywhere the conversion logic
    drops a column, the visualization drops it too.

    Steps:
      1. For every column starting with `ori_prefix`, remove the prefix.
      2. If `col_ori` has a `Unit` column, find rows whose Unit is a non-empty
         string. For each, the CDD export typically names the column as
         '<Name> <Unit>' (e.g. 'Mean Papp A to B (10-6 cm/s)'); rename it back
         to the canonical bare Name so downstream filters that key off
         `col_ori['Name']` find a match.

    Args:
        ori_df: long-form DataFrame from a CDD CSV export.
        col_ori: schema DataFrame whose `Name` column lists the canonical
            (bare) column names, optionally with a `Unit` column.
        ori_prefix: string stripped from each column name (default
            'MDR1-MDCK II: ').

    Returns:
        A copy of `ori_df` with renamed columns. Original `ori_df` is unchanged.
    """
    def _strip(c):
        return c[len(ori_prefix):] if c.startswith(ori_prefix) else c
    out = ori_df.copy()
    out.columns = [_strip(c) for c in out.columns]
    if 'Unit' in col_ori.columns:
        unit_renames = {
            f"{row['Name']} {row['Unit']}": row['Name']
            for _, row in col_ori.iterrows()
            if isinstance(row.get('Unit'), str) and row['Unit'].strip()
        }
        if unit_renames:
            out = out.rename(columns=unit_renames)
    return out


def convert_to_target_format(
    ori_df: pd.DataFrame,
    col_ori: pd.DataFrame,
    col_target: pd.DataFrame,
    inhibitor_mask: pd.Series,
    id_cols=('Molecule Name', 'Batch Molecule-Batch ID'),
    ori_prefix: str = 'MDR1-MDCK II: ',
    inhibitor_suffix_re: str = r'\s*-\s*Pg[Pp]\s+[Ii]nhibitor\s*$',
) -> pd.DataFrame:
    """Pivot `ori_df` from long (row per condition) to wide (row per compound).

    Pipeline:
      1. Strip `ori_prefix` from every column name in `ori_df`.
      1b. Use `col_ori['Unit']` to rename unit-bearing ori columns
          ('Mean Papp A to B (10-6 cm/s)') back to their canonical bare names
          ('Mean Papp A to B') so step 2's filter matches.
      2. Keep only `id_cols + col_ori['Name']`; drop everything else.
      3. Use `inhibitor_mask` to split into no-inhibitor / with-inhibitor rows.
      4. Rename inhibitor-row data columns to their `'<X> - PgP inhibitor'`
         names from `col_target` (regex handles PgP/Pgp + inhibitor/Inhibitor variants).
      5. Outer-merge on `id_cols`.
      6. Coerce Number-typed target columns via `pd.to_numeric(errors='coerce')`.
      7. Reorder to `id_cols + col_target['Name']`.

    Args:
        ori_df: long-form input (one row per compound × condition).
        col_ori: schema from sheet 'original' — its `Name` column selects which
            (prefix-stripped) columns of `ori_df` to keep before pivoting. If a
            `Unit` column is present, unit-bearing ori columns are recognized
            and renamed (step 1b).
        col_target: schema from sheet 'target' — defines the wide column layout
            and the per-column `Data Type` for coercion.
        inhibitor_mask: bool Series aligned to `ori_df.index`. True = with-inhibitor.
            Compute it BEFORE calling — the mask can reference any column of the
            original `ori_df` (including ones dropped by the col_ori filter).
        id_cols: columns identifying a unique compound across the two rows.
        ori_prefix: prefix stripped from `ori_df.columns` so they match `col_ori['Name']`.
        inhibitor_suffix_re: regex matching the inhibitor suffix in `col_target['Name']`.

    Returns:
        DataFrame with columns = list(id_cols) + list(col_target['Name']),
        one row per compound.
    """
    if len(inhibitor_mask) != len(ori_df):
        raise ValueError(
            f'inhibitor_mask length ({len(inhibitor_mask)}) does not match '
            f'ori_df length ({len(ori_df)})'
        )

    # Degenerate masks silently produce empty halves and leave one side of the
    # output entirely NaN. The most common cause is a typo or wrong column in
    # the caller's `ori[...].str.contains(...)` expression. Surface it loudly.
    n_inh = int(inhibitor_mask.sum())
    total = len(inhibitor_mask)
    if n_inh == 0:
        print(
            f'WARN: inhibitor_mask is all False — no rows will go into the '
            f'with-inhibitor columns (they will all be NaN). Check the source '
            f'column and the search string for typos. '
            f'(mask True={n_inh}/{total})'
        )
    elif n_inh == total:
        print(
            f'WARN: inhibitor_mask is all True — no rows will go into the base '
            f'columns (they will all be NaN). Check the source column and the '
            f'search string. (mask True={n_inh}/{total})'
        )

    id_cols = list(id_cols)

    # 1. Strip the CDD source prefix and apply col_ori's unit-suffix rename
    #    in one shot. Shared with notebook visualizers via the same helper so
    #    the renames stay in sync across all consumers.
    ori = normalize_ori_columns(ori_df, col_ori, ori_prefix)

    # 2. Validate id_cols, then narrow to id_cols + col_ori['Name'].
    missing_id = [c for c in id_cols if c not in ori.columns]
    if missing_id:
        raise ValueError(f'id_cols not in ori (after prefix strip): {missing_id}')
    ori_keep = list(col_ori['Name'])
    missing_ori = [c for c in ori_keep if c not in ori.columns]
    if missing_ori:
        print(f'WARN: {len(missing_ori)} col_ori name(s) not found in ori '
              f'(after prefix strip): {missing_ori}')
        ori_keep = [c for c in ori_keep if c in ori.columns]
    ori = ori[id_cols + ori_keep].copy()

    # 3. Pair base + inhibitor target names via the suffix regex.
    suf = re.compile(inhibitor_suffix_re)
    base_targets, inh_targets = [], {}    # inh_targets: inhibitor_name -> base_name
    for name in col_target['Name']:
        if suf.search(name):
            inh_targets[name] = suf.sub('', name)
        else:
            base_targets.append(name)

    # 4. Split rows by inhibitor_mask.
    base_df = ori.loc[~inhibitor_mask.values, :].copy()
    inh_df = ori.loc[inhibitor_mask.values, :].copy()

    # 5. Rename inhibitor sub-frame's data columns to their inhibitor target names.
    rename = {base: inh for inh, base in inh_targets.items() if base in inh_df.columns}
    inh_df = inh_df.rename(columns=rename)

    # 6. Deduplicate + outer-merge on id_cols.
    if base_df.duplicated(subset=id_cols).any():
        print('WARN: duplicate id_cols in no-inhibitor rows — keeping first')
        base_df = base_df.drop_duplicates(subset=id_cols, keep='first')
    if inh_df.duplicated(subset=id_cols).any():
        print('WARN: duplicate id_cols in with-inhibitor rows — keeping first')
        inh_df = inh_df.drop_duplicates(subset=id_cols, keep='first')
    out = base_df.merge(inh_df, on=id_cols, how='outer')

    # 7. Type coercion for Number columns.
    dtypes = dict(zip(col_target['Name'], col_target['Data Type']))
    for name, dt in dtypes.items():
        if dt == 'Number' and name in out.columns:
            out[name] = pd.to_numeric(out[name], errors='coerce')

    # 8. Final column order: id_cols first, then target schema order.
    target_order = [n for n in col_target['Name'] if n in out.columns]
    return out[id_cols + target_order].reset_index(drop=True)
