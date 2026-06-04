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
from pathlib import Path

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


def _coerce_numeric_keep_unparseable(s: pd.Series) -> pd.Series:
    """Coerce numeric-looking strings to floats; keep unparseable strings as-is.

    Mirrors ``pd.to_numeric(errors='coerce')`` for the parseable case but
    preserves common lab notations like ``'<0.38'`` (below detection limit),
    ``'>200'`` (above range), or ``'NQ'`` (not quantified) that would otherwise
    be silently lost to NaN. Empty strings and existing NaNs stay NaN.

    Returns a numeric-dtype Series when every cell is parseable (or NaN);
    falls back to object dtype only when at least one cell is non-numeric.
    """
    coerced = pd.to_numeric(s, errors='coerce')
    # cells that became NaN despite the original being a non-empty string =
    # truly unparseable values worth preserving (not just missing data)
    unparseable = (
        coerced.isna()
        & s.notna()
        & (s.astype(str).str.strip() != '')
    )
    if not unparseable.any():
        return coerced
    out = coerced.astype(object)
    out.loc[unparseable] = s.loc[unparseable]
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

    # 7. Type coercion for Number columns. Parseable values become floats;
    #    common lab notations ('<0.38', '>200', 'NQ', etc.) are preserved
    #    as their original string. Empty / NaN cells stay NaN.
    dtypes = dict(zip(col_target['Name'], col_target['Data Type']))
    for name, dt in dtypes.items():
        if dt == 'Number' and name in out.columns:
            out[name] = _coerce_numeric_keep_unparseable(out[name])

    # 8. Final column order: id_cols first, then target schema order.
    target_order = [n for n in col_target['Name'] if n in out.columns]
    return out[id_cols + target_order].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Notebook-oriented helpers
# ---------------------------------------------------------------------------

def visualize_conversion(
    ori_df: pd.DataFrame,
    target_df: pd.DataFrame,
    compound_names,  # str (single compound) or iterable of str
    col_ori: pd.DataFrame,
    col_target: pd.DataFrame,
    inhibitor_mask: pd.Series,
    id_cols=('Molecule Name', 'Batch Molecule-Batch ID'),
    ori_prefix: str = 'MDR1-MDCK II: ',
    inhibitor_suffix_re: str = r'\s*-\s*Pg[Pp]\s+[Ii]nhibitor\s*$',
    no_inh_color: str = '#cce5ff',   # light blue
    inh_color: str = '#f8d7da',      # light red / pink
    id_color: str = '#f0f0f0',       # light gray
):
    """Side-by-side HTML view of the long→wide conversion for one or more compounds.

    Use after running the conversion to spot-check that values flowed into
    the right cells. Mirrors the in-script pipeline (prefix strip → col_ori
    filter → split → merge) so the visualized ori matches what the function
    actually saw.

    Args:
        ori_df, target_df: long-form input and wide-form output of
            `convert_to_target_format`.
        compound_names: a single compound name (str) or an iterable of names.
        col_ori, col_target, inhibitor_mask: the same arguments used in the
            forward call.
        id_cols, ori_prefix, inhibitor_suffix_re: must match the forward call.
        no_inh_color, inh_color, id_color: CSS colors for the styled cells.

    Output: renders styled DataFrames inline (requires a Jupyter / IPython
    display backend). Returns None.
    """
    id_cols = list(id_cols)
    if isinstance(compound_names, str):
        compound_names = [compound_names]
    for compound_name in compound_names:
        _render_one(
            ori_df, target_df, compound_name, col_ori, col_target,
            inhibitor_mask, id_cols, ori_prefix, inhibitor_suffix_re,
            no_inh_color, inh_color, id_color,
        )


def _render_one(
    ori_df, target_df, compound_name, col_ori, col_target,
    inhibitor_mask, id_cols, ori_prefix, inhibitor_suffix_re,
    no_inh_color, inh_color, id_color,
):
    """Render the before/after view for a single compound."""
    # Lazy import — keep the module importable in environments without IPython.
    from IPython.display import display, Markdown

    ori = normalize_ori_columns(ori_df, col_ori, ori_prefix)
    ori_keep = [c for c in list(col_ori['Name']) if c in ori.columns]
    ori = ori[id_cols + ori_keep]

    compound_rows = ori.loc[ori['Molecule Name'] == compound_name]
    if compound_rows.empty:
        display(Markdown(f'**No rows found for compound `{compound_name}` in ori.**'))
        return

    # Align the mask to the filtered subset and order rows: no-inhibitor first.
    mask_sub = inhibitor_mask.loc[compound_rows.index]
    sort_idx = mask_sub.sort_values().index
    compound_rows = compound_rows.loc[sort_idx]
    mask_sub = mask_sub.loc[sort_idx]

    def style_ori_rows(row):
        is_inh = bool(mask_sub.loc[row.name])
        color = inh_color if is_inh else no_inh_color
        return [f'background-color: {color}; color: black'] * len(row)

    ori_styled = (
        compound_rows.style
        .apply(style_ori_rows, axis=1)
        .set_caption(
            f'ori (long-form, prefix stripped, filtered to col_ori): '
            f'rows for {compound_name} — blue = no inhibitor, red = with inhibitor'
        )
    )

    target_rows = target_df.loc[target_df['Molecule Name'] == compound_name]
    if target_rows.empty:
        display(Markdown(f'**No rows found for compound `{compound_name}` in target.**'))
        return

    suf = re.compile(inhibitor_suffix_re)

    def style_target_cols(s):
        col = s.name
        if col in id_cols:
            color = id_color
        elif suf.search(col):
            color = inh_color
        else:
            color = no_inh_color
        return [f'background-color: {color}; color: black'] * len(s)

    target_styled = (
        target_rows.style
        .apply(style_target_cols, axis=0)
        .set_caption(
            f'target (wide-form): row for {compound_name} — '
            f'blue = base, red = "- PgP inhibitor", gray = id'
        )
    )

    display(Markdown(f'### Conversion view for `{compound_name}`'))
    display(Markdown('**Before** (`ori`, one row per condition):'))
    display(ori_styled)
    display(Markdown('**After** (`target`, one row per compound):'))
    display(target_styled)


def test_pivot_round_trip(
    target_df: pd.DataFrame,
    ori_df: pd.DataFrame,
    col_ori: pd.DataFrame,
    col_target: pd.DataFrame,
    inhibitor_mask: pd.Series,
    id_cols=('Molecule Name', 'Batch Molecule-Batch ID'),
    ori_prefix: str = 'MDR1-MDCK II: ',
    inhibitor_suffix_re: str = r'\s*-\s*Pg[Pp]\s+[Ii]nhibitor\s*$',
    output_path: str = 'output/pivoted_mdr1.csv',
) -> bool:
    """Save target_df, reverse-pivot it, and compare to the original ori.

    Args:
        target_df: wide output of `convert_to_target_format`.
        ori_df: original long-form data (e.g. ori from `data/MDR1_ori.csv`).
        col_ori, col_target: schemas used in the forward pivot.
        inhibitor_mask: bool Series used in the forward pivot.
        id_cols, ori_prefix, inhibitor_suffix_re: must match the forward call.
        output_path: where to save the wide CSV.

    Returns:
        True if every (compound, condition, column) cell in ori is losslessly
        recoverable from target_df, False otherwise. Prints summary counts
        only — no compound values.

    Rationale: catches silent data loss in the forward pivot (a dropped
    column, a coercion to NaN, a mis-paired base/inhibitor).
    """
    id_cols = list(id_cols)

    # 1. Save target_df to disk, then re-read so we test the on-disk artifact.
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    target_df.to_csv(out_path, index=False)
    pivoted = pd.read_csv(out_path)
    print(f'saved: {out_path}  shape={pivoted.shape}')

    # 2. Reverse the pivot: each compound row becomes one no-inh row plus, if
    #    it has inhibitor data, one with-inh row.
    suf = re.compile(inhibitor_suffix_re)
    base_targets = [n for n in col_target['Name'] if not suf.search(n)]
    inh_renames = {n: suf.sub('', n) for n in col_target['Name'] if suf.search(n)}

    no_inh = pivoted[id_cols + [c for c in base_targets if c in pivoted.columns]].copy()
    no_inh['__condition'] = 'no_inhibitor'

    inh_cols_present = [c for c in inh_renames if c in pivoted.columns]
    with_inh = pivoted[id_cols + inh_cols_present].copy()
    with_inh = with_inh.rename(columns=inh_renames)
    with_inh['__condition'] = 'with_inhibitor'

    # Symmetric all-NaN filter: drop rows where every measurement is NaN.
    def _drop_all_nan(df):
        cols = [c for c in df.columns if c not in id_cols + ['__condition']]
        if not cols:
            return df
        return df[~df[cols].isna().all(axis=1)]
    no_inh = _drop_all_nan(no_inh)
    with_inh = _drop_all_nan(with_inh)

    recovered = pd.concat([no_inh, with_inh], ignore_index=True)

    # 3. Normalize ori the same way the forward pivot does and filter to
    #    comparable columns + tag each row with its condition from the mask.
    ori_norm = normalize_ori_columns(ori_df, col_ori, ori_prefix)
    keep = [c for c in list(col_ori['Name']) if c in ori_norm.columns]
    ori_norm = ori_norm[id_cols + keep].copy()
    ori_norm['__condition'] = inhibitor_mask.map(
        {True: 'with_inhibitor', False: 'no_inhibitor'}
    ).values

    # Mirror the forward pivot's dedup.
    n_before = len(ori_norm)
    ori_norm = ori_norm.drop_duplicates(subset=id_cols + ['__condition'], keep='first')
    n_after = len(ori_norm)
    if n_before != n_after:
        print(
            f'note: {n_before - n_after} duplicate (id, condition) row(s) in ori '
            f"were deduped to match the forward pivot. Inspect locally with: "
            f"ori.assign(_cond=inhibitor_mask).loc[lambda d: d.duplicated("
            f"subset={list(id_cols)} + ['_cond'], keep=False)]"
        )

    # 4. Align + compare.
    sort_keys = id_cols + ['__condition']
    ori_sorted = ori_norm.sort_values(sort_keys).reset_index(drop=True)
    rec_sorted = recovered.sort_values(sort_keys).reset_index(drop=True)
    print(f'ori rows (normalized + filtered): {len(ori_sorted)}')
    print(f'recovered rows (reverse-pivot):    {len(rec_sorted)}')

    if len(ori_sorted) != len(rec_sorted):
        ori_keys = set(map(tuple, ori_sorted[sort_keys].astype(str).values.tolist()))
        rec_keys = set(map(tuple, rec_sorted[sort_keys].astype(str).values.tolist()))
        print(
            f'FAIL: row counts differ. only_in_ori={len(ori_keys - rec_keys)} '
            f'only_in_recovered={len(rec_keys - ori_keys)}'
        )
        return False

    def _stringify(df):
        out = df.copy()
        for c in out.columns:
            # pandas 3.x preserves NaN through .astype(str). fillna('') first
            # so every missing-value sentinel collapses to the same '' token.
            s = out[c].fillna('').astype(str).str.strip()
            s = s.replace({'nan': '', 'None': '', 'NaT': '', '<NA>': ''})
            # collapse int-like floats: '5.0' / '5.000' → '5'
            s = s.str.replace(r'^(-?\d+)\.0+$', r'\1', regex=True)
            # strip trailing zeros after a non-int decimal: '99.500' → '99.5'
            s = s.str.replace(r'^(-?\d+\.\d*?)0+$', r'\1', regex=True)
            # if only a trailing '.' remains, drop it: '5.' → '5'
            s = s.str.replace(r'\.$', '', regex=True)
            out[c] = s
        return out

    common_cols = [c for c in ori_sorted.columns if c in rec_sorted.columns]
    diffs = _stringify(ori_sorted[common_cols]) != _stringify(rec_sorted[common_cols])

    total = diffs.size
    bad = int(diffs.sum().sum())
    print(f'columns compared:  {len(common_cols)}')
    print(f'total cells:       {total}')
    print(f'differing cells:   {bad}')
    print(f'match rate:        {(1 - bad / total) * 100:.4f}%')

    if bad == 0:
        print('\nPASS: pivoted_mdr1.csv reverses cleanly into the original ori data.')
        return True

    print('\nDiffering columns (count of mismatched cells; values not shown):')
    by_col = diffs.sum().sort_values(ascending=False)
    for col, n in by_col.items():
        if n > 0:
            print(f'  {col!r}: {n}')

    threshold = max(1, len(ori_sorted) // 2)
    high_diff_cols = [c for c, n in by_col.items() if n >= threshold]
    if high_diff_cols:
        print('\nDiagnostic for high-diff columns (no values shown):')
        for col in high_diff_cols:
            o, r = ori_sorted[col], rec_sorted[col]
            o_lens = sorted(set(o.astype(str).str.len()))[:8]
            r_lens = sorted(set(r.astype(str).str.len()))[:8]
            o_nan, r_nan = int(o.isna().sum()), int(r.isna().sum())
            print(f'  {col!r}:')
            print(f'    ori  dtype={o.dtype}  nan={o_nan}/{len(o)}  str_lengths={o_lens}')
            print(f'    rec  dtype={r.dtype}  nan={r_nan}/{len(r)}  str_lengths={r_lens}')

    print('\nFAIL: round-trip is lossy. Inspect the columns above.')
    return False
