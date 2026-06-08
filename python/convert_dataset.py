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

    # 8. Final column order: id_cols first, then the FULL col_target schema.
    #    Any col_target name not present in `out` (because the source ori had
    #    no such column) is added as an all-NaN column. This keeps the output
    #    schema stable across different source files — two runs with the same
    #    col_target always produce CSVs with the same headers.
    for name in col_target['Name']:
        if name not in out.columns:
            out[name] = pd.NA
    target_order = list(col_target['Name'])
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
            col = out[c]
            # For numeric-dtype columns, round before stringifying — otherwise
            # float-precision drift through CSV roundtrip (e.g. 0.12345678901234567
            # vs 0.123456789012345) flags every cell as a diff. 6 decimals is
            # below lab-measurement precision and above IEEE-754 noise.
            if pd.api.types.is_numeric_dtype(col):
                col = col.round(6)
            # pandas 3.x preserves NaN through .astype(str). fillna('') first
            # so every missing-value sentinel collapses to the same '' token.
            s = col.fillna('').astype(str).str.strip()
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


def read_xlsx_preserving_qualifiers(path: str, sheet_name: str) -> pd.DataFrame:
    """Read an Excel sheet, reconstructing '<x' / '>x' values from cell formats.

    Lab workflows sometimes encode below-detection-limit or above-range values
    as ordinary numbers with a custom Excel display format like ``\\< 0.000``
    (the ``<`` is just rendered, not stored). ``pd.read_excel`` reads the
    underlying float and silently loses the qualifier. This reader walks the
    sheet with openpyxl, and for each cell whose ``number_format`` starts with
    ``\\<`` or ``\\>``, returns the qualified string (e.g. ``'< 0.384'``).
    Other cells pass through unchanged.

    Args:
        path: xlsx file path.
        sheet_name: name of the sheet to read.

    Returns:
        DataFrame with the same shape as ``pd.read_excel`` would produce,
        but with qualifier-formatted cells re-rendered as their displayed text.
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet_name]
    rows = list(ws.iter_rows(values_only=False))
    if not rows:
        return pd.DataFrame()
    header = [c.value for c in rows[0]]

    records = []
    for row in rows[1:]:
        rec = {}
        for h, cell in zip(header, row):
            val = cell.value
            fmt = cell.number_format or ''
            if (
                isinstance(val, (int, float))
                and not isinstance(val, bool)
                and isinstance(fmt, str)
            ):
                m = re.match(r'^\\([<>])', fmt)
                if m:
                    qualifier = m.group(1)
                    dm = re.search(r'\.(0+)', fmt)
                    decimals = len(dm.group(1)) if dm else 0
                    rec[h] = f'{qualifier} {val:.{decimals}f}'
                    continue
            rec[h] = val
        records.append(rec)
    return pd.DataFrame(records)


def validate_pivoted_output(
    df: pd.DataFrame,
    *,
    molecule_id_re: str = r'^SRB-\d{7}$',
    batch_id_re: str = r'^SRB-\d{7}-\d{3}$',
    efflux_rtol: float = 0.05,
) -> bool:
    """Run a suite of value-format and consistency checks on a pivoted DataFrame.

    Mirrors the spirit of the Batch-Molecule-Batch-ID oversight: catch
    silently-malformed identifiers, transposed numeric columns, and accidental
    duplicates. Prints per-check pass/fail plus an offending-row count.
    Cell values are never printed.

    Checks (return True iff all pass):

      1. ``Molecule Name`` matches ``molecule_id_re`` (default ``SRB-XXXXXXX``).
      2. ``Batch Molecule-Batch ID`` matches ``batch_id_re`` (default
         ``SRB-XXXXXXX-NNN``).
      3. Each row's ``Batch Molecule-Batch ID`` starts with its
         ``Molecule Name`` followed by ``-``.
      4. ``Efflux ratio`` ≈ ``Mean Papp B to A`` / ``Mean Papp A to B`` within
         ``efflux_rtol`` (applied to base + inhibitor variants).
      7. ``(Molecule Name, Batch Molecule-Batch ID)`` pairs are unique.

    Args:
        df: wide-format DataFrame (output of ``convert_to_target_format``).
        molecule_id_re: regex compounds must match.
        batch_id_re: regex batch ids must match.
        efflux_rtol: relative tolerance for the efflux-ratio consistency check.

    Returns:
        True iff every check passed.
    """
    print('=== validate_pivoted_output ===')
    results = []

    def _check(name, ok, n_bad=0):
        status = 'PASS' if ok else 'FAIL'
        tail = '' if ok else f'   ({n_bad} offending row(s))'
        print(f'  {status}  {name}{tail}')
        results.append(bool(ok))

    # 1. Molecule Name format
    if 'Molecule Name' in df.columns:
        s = df['Molecule Name'].astype(str)
        bad = ~s.str.match(molecule_id_re, na=False)
        _check(f'Molecule Name matches {molecule_id_re!r}', not bad.any(), int(bad.sum()))

    # 2. Batch ID format
    if 'Batch Molecule-Batch ID' in df.columns:
        s = df['Batch Molecule-Batch ID'].astype(str)
        bad = ~s.str.match(batch_id_re, na=False)
        _check(f'Batch Molecule-Batch ID matches {batch_id_re!r}', not bad.any(), int(bad.sum()))

    # 3. Batch ID starts with Molecule Name + '-'  (per-row element-wise check;
    #    pandas 3.x .str.startswith doesn't accept a Series argument).
    if {'Molecule Name', 'Batch Molecule-Batch ID'}.issubset(df.columns):
        mn = df['Molecule Name'].astype(str)
        bid = df['Batch Molecule-Batch ID'].astype(str)
        ok_row = pd.Series(
            [b.startswith(m + '-') for m, b in zip(mn, bid)],
            index=df.index,
        )
        _check('Batch Molecule-Batch ID starts with <Molecule Name>-',
               ok_row.all(), int((~ok_row).sum()))

    # 4. Efflux ratio consistency, base + inhibitor variants
    for ab, ba, ratio in [
        ('Mean Papp A to B', 'Mean Papp B to A', 'Efflux ratio'),
        ('Mean Papp A to B - PgP Inhibitor',
         'Mean Papp B to A - PgP inhibitor',
         'Efflux ratio - PgP inhibitor'),
    ]:
        if not all(c in df.columns for c in (ab, ba, ratio)):
            continue
        # Coerce numerically — any string (e.g. '<0.38') becomes NaN and is
        # excluded from this comparison (we can't compute the ratio anyway).
        ab_v = pd.to_numeric(df[ab], errors='coerce')
        ba_v = pd.to_numeric(df[ba], errors='coerce')
        r_v = pd.to_numeric(df[ratio], errors='coerce')
        eligible = ab_v.notna() & ba_v.notna() & r_v.notna() & (ab_v != 0)
        computed = ba_v / ab_v.where(ab_v != 0)
        rel = (r_v - computed).abs() / r_v.abs().where(r_v != 0)
        bad = eligible & (rel > efflux_rtol)
        _check(f'{ratio!r} ≈ {ba!r} / {ab!r} (rtol={efflux_rtol})',
               not bad.any(), int(bad.sum()))

    # 7. Unique (Molecule Name, Batch ID)
    if {'Molecule Name', 'Batch Molecule-Batch ID'}.issubset(df.columns):
        dup = df.duplicated(subset=['Molecule Name', 'Batch Molecule-Batch ID'])
        _check('Unique (Molecule Name, Batch Molecule-Batch ID)',
               not dup.any(), int(dup.sum()))

    all_pass = all(results) if results else False
    print(f'\n{"PASS" if all_pass else "FAIL"}: {sum(results)} / {len(results)} check(s) passed')
    return all_pass


def check_columns_match(old_cols, new_cols) -> bool:
    """Strict comparison of two column lists (names AND order).

    Two pivoted CSVs are `pd.concat`-ready only when their headers match
    exactly. This helper catches drift: a renamed col_target, a missing
    column, a re-ordering — anything that would break a downstream concat.

    Args:
        old_cols, new_cols: iterables of column names (typically the
            ``columns`` of two DataFrames or the headers of two CSVs).

    Returns:
        True iff `list(old_cols) == list(new_cols)`. On mismatch, prints
        a diff: columns only in one side, plus a note if the order of
        shared columns differs.
    """
    old_list = list(old_cols)
    new_list = list(new_cols)
    match = old_list == new_list
    print(
        f'old columns: {len(old_list)}   new columns: {len(new_list)}   '
        f'identical_and_in_order: {match}'
    )
    if not match:
        only_old = [c for c in old_list if c not in new_list]
        only_new = [c for c in new_list if c not in old_list]
        print(f'only in OLD ({len(only_old)}): {only_old}')
        print(f'only in NEW ({len(only_new)}): {only_new}')
        shared = set(old_list) & set(new_list)
        old_shared = [c for c in old_list if c in shared]
        new_shared = [c for c in new_list if c in shared]
        if old_shared != new_shared:
            print('order of shared columns differs between the two CSVs')
    return match
