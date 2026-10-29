"""Unit tests for ``convert_to_target_format`` in ``python/convert_dataset.py``.

Mirrors the synthetic round-trip in
``vignettes/convert_dataset.ipynb`` (cells 12 / 14) but as ``unittest``
TestCases so they're runnable headlessly.

All compound IDs are synthetic (``TEST-XXXX`` — never the real
``SRB-XXXXXXX`` pattern) so no chemistry data crosses the wire. The
fake ``col_ori`` / ``col_target`` cover the four interesting cases:
- a column WITH a unit (``Mean Papp A to B``) — exercises the unit-rename step
- a Number column WITHOUT a unit (``Efflux ratio``)
- a Text column (``Permeability Class``)
- an identifier column (``Study number``)

Run from the repo root:

    python -m unittest tests.test_convert_dataset

or::

    python tests/test_convert_dataset.py
"""
import contextlib
import io
import sys
import unittest
from pathlib import Path

import pandas as pd

# python/convert_dataset.py lives next to vignettes/. Add ../python to the path.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'python'))

from convert_upload import (apply_corrections, apply_global_ignores,
                            normalize_species, species_alias_map, split_species)
from import_to_protocol import evaluate_mapping
from convert_dataset import (
    check_columns_match,
    convert_to_target_format,
    normalize_ori_columns,
    read_xlsx_preserving_qualifiers,
    validate_pivoted_output,
)


# ---------- synthetic fixtures (no real chemistry) ----------

def _make_fake_col_ori():
    """Minimal ``col_ori`` covering unit-bearing + unitless rows."""
    return pd.DataFrame({
        'Name':      ['Study number', 'Mean Papp A to B', 'Efflux ratio', 'Permeability Class'],
        'Data Type': ['Text',         'Number',           'Number',       'Text'],
        # Unit only set for Mean Papp A to B — drives the step 1b rename
        'Unit':      [None,           '(10-6 cm/s)',      None,           None],
        'Required':  [None,           None,               None,           None],
        'Condition': [None,           None,               None,           None],
    })


def _make_fake_col_target():
    """Minimal ``col_target`` — each base name paired with its ``- PgP inhibitor`` variant.

    Capitalization mirrors the real conversion xlsx: 'Mean Papp A to B - PgP Inhibitor'
    uses capital I, the others use lowercase 'inhibitor'. The regex in
    ``convert_to_target_format`` handles both spellings.
    """
    return pd.DataFrame({
        'Name': [
            'Study number',
            'Study number - PgP inhibitor',
            'Mean Papp A to B',
            'Mean Papp A to B - PgP Inhibitor',   # capital I — intentional
            'Efflux ratio',
            'Efflux ratio - PgP inhibitor',
            'Permeability Class',
            'Permeability Class - PgP inhibitor',
        ],
        'Data Type': ['Text', 'Text', 'Number', 'Number', 'Number', 'Number', 'Text', 'Text'],
        'Unit':      [None] * 8,
        'Required':  [None] * 8,
        'Condition': [None] * 8,
    })


def _make_fake_ori():
    """Build a 5-row synthetic long-form DataFrame across 3 test compounds.

    - TEST-0001 and TEST-0002: both no-inhibitor and with-inhibitor rows
      (distinct values so a value-swap bug is detectable).
    - TEST-0003: no-inhibitor only — exercises the outer-merge for a missing
      condition (inhibitor cells should come out NaN).

    The result column 'Mean Papp A to B (10-6 cm/s)' includes its unit suffix
    on purpose — that's how CDD's CSV export names it, and step 1b of the
    conversion has to recognize and rename it.
    """
    rows = []
    scenarios = [
        ('TEST-0001', [('no_inhibitor',   '10.5', '1.9', 'low'),
                       ('with_inhibitor', '25.0', '0.6', 'high')]),
        ('TEST-0002', [('no_inhibitor',   '5.2',  '5.8', 'low'),
                       ('with_inhibitor', '12.1', '1.7', 'medium')]),
        ('TEST-0003', [('no_inhibitor',   '8.0',  '3.1', 'medium')]),
    ]
    for compound, conds in scenarios:
        for cond, papp_ab, efflux, perm in conds:
            rows.append({
                'Molecule Name': compound,
                'Batch Molecule-Batch ID': f'{compound}-001',
                'MDR1-MDCK II: Run Conditions': cond,
                'MDR1-MDCK II: Study number': 'TEST-STUDY',
                # Unit baked into the name — exercises step 1b rename
                'MDR1-MDCK II: Mean Papp A to B (10-6 cm/s)': papp_ab,
                'MDR1-MDCK II: Efflux ratio': efflux,
                'MDR1-MDCK II: Permeability Class': perm,
            })
    return pd.DataFrame(rows)


# ---------- happy-path: full conversion ----------

class ConvertToTargetFormatTests(unittest.TestCase):
    """End-to-end pivot on synthetic long-form data."""

    def setUp(self):
        self.col_ori = _make_fake_col_ori()
        self.col_target = _make_fake_col_target()
        self.ori = _make_fake_ori()
        self.mask = self.ori['MDR1-MDCK II: Run Conditions'] == 'with_inhibitor'
        self.target = convert_to_target_format(
            self.ori, self.col_ori, self.col_target, self.mask,
        )

    def test_returns_dataframe(self):
        """
        Input    : synthetic ori + col_ori + col_target + mask.
        Expected : output is a pandas DataFrame.
        Rationale: surface-level contract.
        """
        # function must return a DataFrame, not a dict / records / Styler
        self.assertIsInstance(self.target, pd.DataFrame)

    def test_row_count_equals_unique_compounds(self):
        """
        Input    : 5 input rows across 3 unique compounds.
        Expected : output has 3 rows.
        Rationale: the long→wide pivot collapses one row per (compound, condition) to one per compound.
        """
        # 3 compounds → 3 rows
        self.assertEqual(len(self.target), 3)

    def test_column_order_matches_target_schema(self):
        """
        Input    : col_target with 8 names + 2 id columns.
        Expected : output columns = id_cols followed by col_target['Name'] in order.
        Rationale: downstream consumers depend on a stable canonical column order.
        """
        expected = ['Molecule Name', 'Batch Molecule-Batch ID'] + list(self.col_target['Name'])
        # full column order must match the target schema exactly
        self.assertEqual(list(self.target.columns), expected)

    def test_unit_bearing_column_survives_filter(self):
        """
        Input    : ori has 'MDR1-MDCK II: Mean Papp A to B (10-6 cm/s)'; col_ori has
                   Name='Mean Papp A to B' + Unit='(10-6 cm/s)'.
        Expected : output contains BOTH 'Mean Papp A to B' and 'Mean Papp A to B - PgP Inhibitor'.
        Rationale: regression test for step 1b — without the unit rename the col_ori
                   filter silently drops these 4 columns.
        """
        # base and inhibitor variants both present
        self.assertIn('Mean Papp A to B', self.target.columns)
        self.assertIn('Mean Papp A to B - PgP Inhibitor', self.target.columns)

    def test_number_columns_are_numeric_dtype(self):
        """
        Input    : col_target rows with Data Type 'Number' (Mean Papp A to B + variant, Efflux ratio + variant).
        Expected : those columns in the output are numeric dtype.
        Rationale: pd.to_numeric(errors='coerce') should fire on every Number column.
        """
        # base column coerced
        self.assertTrue(pd.api.types.is_numeric_dtype(self.target['Mean Papp A to B']))
        # inhibitor variant coerced
        self.assertTrue(pd.api.types.is_numeric_dtype(self.target['Mean Papp A to B - PgP Inhibitor']))

    def test_text_columns_stay_non_numeric(self):
        """
        Input    : Text-typed col_target row 'Permeability Class'.
        Expected : output column is non-numeric.
        Rationale: Text columns must not be silently coerced — would turn 'low' into NaN.
        """
        # Permeability Class is Text — must NOT have been coerced
        self.assertFalse(pd.api.types.is_numeric_dtype(self.target['Permeability Class']))

    def test_base_value_lands_in_base_column(self):
        """
        Input    : TEST-0001 no-inhibitor row has Mean Papp A to B = '10.5'.
        Expected : output row for TEST-0001 has 10.5 in 'Mean Papp A to B'.
        Rationale: locks the mask-direction (no-inhibitor rows → base columns).
        """
        t1 = self.target.loc[self.target['Molecule Name'] == 'TEST-0001'].iloc[0]
        # no-inhibitor value lands in base column after numeric coercion
        self.assertEqual(t1['Mean Papp A to B'], 10.5)

    def test_inhibitor_value_lands_in_inhibitor_column(self):
        """
        Input    : TEST-0001 with-inhibitor row has Mean Papp A to B = '25.0'.
        Expected : output row for TEST-0001 has 25.0 in 'Mean Papp A to B - PgP Inhibitor'.
        Rationale: locks the mask-direction (with-inhibitor rows → inhibitor columns).
        """
        t1 = self.target.loc[self.target['Molecule Name'] == 'TEST-0001'].iloc[0]
        # with-inhibitor value lands in inhibitor column
        self.assertEqual(t1['Mean Papp A to B - PgP Inhibitor'], 25.0)

    def test_values_not_swapped_between_compounds(self):
        """
        Input    : TEST-0002 has distinct values (5.2 base, 12.1 inhibitor).
        Expected : TEST-0002's values appear under TEST-0002, not TEST-0001.
        Rationale: catches merge-key bugs where rows get joined to the wrong compound.
        """
        t2 = self.target.loc[self.target['Molecule Name'] == 'TEST-0002'].iloc[0]
        # base value specifically for TEST-0002
        self.assertEqual(t2['Mean Papp A to B'], 5.2)
        # inhibitor value specifically for TEST-0002
        self.assertEqual(t2['Mean Papp A to B - PgP Inhibitor'], 12.1)

    def test_missing_inhibitor_row_yields_nan(self):
        """
        Input    : TEST-0003 has only a no-inhibitor row.
        Expected : base value populated, inhibitor cells NaN.
        Rationale: outer-merge must preserve compounds measured in only one condition.
        """
        t3 = self.target.loc[self.target['Molecule Name'] == 'TEST-0003'].iloc[0]
        # base value present
        self.assertEqual(t3['Mean Papp A to B'], 8.0)
        # missing inhibitor partner → NaN
        self.assertTrue(pd.isna(t3['Mean Papp A to B - PgP Inhibitor']))

    def test_text_values_flow_into_correct_cells(self):
        """
        Input    : TEST-0001 no-inh Permeability Class = 'low', with-inh = 'high'.
        Expected : 'low' in base column, 'high' in inhibitor column.
        Rationale: confirms Text columns honor the same base/inhibitor split as Number columns.
        """
        t1 = self.target.loc[self.target['Molecule Name'] == 'TEST-0001'].iloc[0]
        # base text value
        self.assertEqual(t1['Permeability Class'], 'low')
        # inhibitor text value (note lowercase 'inhibitor' suffix here)
        self.assertEqual(t1['Permeability Class - PgP inhibitor'], 'high')


# ---------- stable output schema (even when source columns are missing) ----------

class StableOutputSchemaTests(unittest.TestCase):
    """Output columns always equal id_cols + col_target['Name'], regardless of source."""

    def test_missing_source_columns_emit_nan(self):
        """
        Input    : col_ori is missing 'Permeability Class' entirely (source ori
                   doesn't carry that column).
        Expected : output still has 'Permeability Class' and 'Permeability Class - PgP inhibitor';
                   both are all-NaN.
        Rationale: stable schema — two runs with the same col_target always
                   produce CSVs with the same headers, so downstream code
                   can rely on column names existing.
        """
        col_target = _make_fake_col_target()
        # col_ori that intentionally omits 'Permeability Class'
        col_ori_partial = pd.DataFrame({
            'Name':      ['Study number', 'Mean Papp A to B', 'Efflux ratio'],
            'Data Type': ['Text',         'Number',           'Number'],
            'Unit':      [None,           '(10-6 cm/s)',      None],
            'Required':  [None] * 3,
            'Condition': [None] * 3,
        })
        ori = _make_fake_ori()
        mask = ori['MDR1-MDCK II: Run Conditions'] == 'with_inhibitor'

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            target = convert_to_target_format(ori, col_ori_partial, col_target, mask)

        # full col_target schema present (independent of what col_ori covered)
        expected = ['Molecule Name', 'Batch Molecule-Batch ID'] + list(col_target['Name'])
        self.assertEqual(list(target.columns), expected)

        # the dropped pair is all-NaN
        self.assertTrue(target['Permeability Class'].isna().all())
        self.assertTrue(target['Permeability Class - PgP inhibitor'].isna().all())


# ---------- input-validation guards ----------

class InputValidationTests(unittest.TestCase):
    """Errors raised on malformed input."""

    def test_mask_length_mismatch_raises_value_error(self):
        """
        Input    : inhibitor_mask shorter than ori_df.
        Expected : ValueError.
        Rationale: catches a class of caller bugs where the mask was built from a filtered subset.
        """
        ori = _make_fake_ori()
        # mask is one row short — silent truncation would be worse than a hard error
        bad_mask = pd.Series([False] * (len(ori) - 1))
        with self.assertRaises(ValueError):
            convert_to_target_format(
                ori, _make_fake_col_ori(), _make_fake_col_target(), bad_mask,
            )

    def test_missing_id_col_raises_value_error(self):
        """
        Input    : id_cols references a column that doesn't exist in ori (after prefix strip).
        Expected : ValueError naming the missing column.
        Rationale: fail fast rather than producing an empty wide DataFrame.
        """
        ori = _make_fake_ori()
        mask = ori['MDR1-MDCK II: Run Conditions'] == 'with_inhibitor'
        with self.assertRaises(ValueError):
            convert_to_target_format(
                ori, _make_fake_col_ori(), _make_fake_col_target(), mask,
                id_cols=('Molecule Name', 'Nonexistent ID'),
            )


# ---------- shared helper: normalize_ori_columns ----------

class NormalizeOriColumnsTests(unittest.TestCase):
    """Prefix strip + unit-suffix rename — shared by the CLI conversion and the notebook viz."""

    def test_prefix_is_stripped(self):
        """
        Input    : DataFrame with columns prefixed by 'MDR1-MDCK II: '.
        Expected : every column has the prefix removed.
        Rationale: identity-preserving rename — every consumer relies on it.
        """
        ori = pd.DataFrame({
            'Molecule Name': ['X'],
            'MDR1-MDCK II: Efflux ratio': ['1.0'],
            'MDR1-MDCK II: Permeability Class': ['low'],
        })
        col_ori = _make_fake_col_ori()
        out = normalize_ori_columns(ori, col_ori)
        # prefix should be gone on every prefixed column
        self.assertIn('Efflux ratio', out.columns)
        self.assertIn('Permeability Class', out.columns)
        # non-prefixed columns left alone
        self.assertIn('Molecule Name', out.columns)

    def test_unit_suffix_rename(self):
        """
        Input    : column 'MDR1-MDCK II: Mean Papp A to B (10-6 cm/s)';
                   col_ori row Name='Mean Papp A to B', Unit='(10-6 cm/s)'.
        Expected : output column is the bare 'Mean Papp A to B'.
        Rationale: locks the unit-rename that lets unit-bearing CDD columns
                   line up with col_ori's separate Name/Unit schema.
        """
        ori = pd.DataFrame({'MDR1-MDCK II: Mean Papp A to B (10-6 cm/s)': ['10.5']})
        col_ori = _make_fake_col_ori()
        out = normalize_ori_columns(ori, col_ori)
        # bare canonical name is the survivor
        self.assertIn('Mean Papp A to B', out.columns)
        # the unit-bearing variant should NOT remain in the output
        self.assertNotIn('Mean Papp A to B (10-6 cm/s)', out.columns)

    def test_does_not_mutate_input(self):
        """
        Input    : ori with prefixed column.
        Expected : input DataFrame's columns unchanged after the call.
        Rationale: callers (incl. visualize_conversion) keep referring to the
                   original ori_df after normalization; mutation would surprise them.
        """
        ori = pd.DataFrame({'MDR1-MDCK II: Efflux ratio': ['1.0']})
        before = list(ori.columns)
        _ = normalize_ori_columns(ori, _make_fake_col_ori())
        # original columns preserved exactly
        self.assertEqual(list(ori.columns), before)


# ---------- numeric coercion preserves lab notations ----------

class NumericCoercionTests(unittest.TestCase):
    """Number columns: parseable → float, unparseable strings preserved, empties → NaN."""

    def _convert_with_papp_value(self, papp_ab_str):
        """Run the pivot on a 1-compound ori where Mean Papp A to B = the given string.

        Suppresses stdout to silence the all-False mask warning — these tests
        only care about the base-column coercion, not the inhibitor side.
        """
        ori = pd.DataFrame([
            {
                'Molecule Name': 'TEST-0001',
                'Batch Molecule-Batch ID': 'TEST-0001-001',
                'MDR1-MDCK II: Run Conditions': 'no_inhibitor',
                'MDR1-MDCK II: Study number': 'S1',
                'MDR1-MDCK II: Mean Papp A to B (10-6 cm/s)': papp_ab_str,
                'MDR1-MDCK II: Efflux ratio': '1.5',
                'MDR1-MDCK II: Permeability Class': 'low',
            }
        ])
        mask = ori['MDR1-MDCK II: Run Conditions'] == 'with_inhibitor'  # all False
        with contextlib.redirect_stdout(io.StringIO()):
            return convert_to_target_format(
                ori, _make_fake_col_ori(), _make_fake_col_target(), mask,
            )

    def test_below_detection_limit_string_is_preserved(self):
        """
        Input    : Mean Papp A to B = '<0.38' (a below-detection-limit notation).
        Expected : output cell holds the literal string '<0.38', not NaN.
        Rationale: regression for the bug where pd.to_numeric(errors='coerce')
                   silently lost qualified lab values.
        """
        target = self._convert_with_papp_value('<0.38')
        v = target.loc[0, 'Mean Papp A to B']
        # value must be the original string, not NaN / not parsed
        self.assertEqual(v, '<0.38')
        # and the column dtype falls back to object to accommodate the string
        self.assertEqual(target['Mean Papp A to B'].dtype, object)

    def test_all_parseable_column_stays_numeric_dtype(self):
        """
        Input    : Mean Papp A to B = '10.5' (parseable as float).
        Expected : output column dtype is numeric and the value is 10.5.
        Rationale: don't regress the all-parseable case — preserves downstream math.
        """
        target = self._convert_with_papp_value('10.5')
        # parseable value becomes a float
        self.assertEqual(target.loc[0, 'Mean Papp A to B'], 10.5)
        # dtype stays numeric (no fallback to object needed)
        self.assertTrue(pd.api.types.is_numeric_dtype(target['Mean Papp A to B']))

    def test_empty_string_becomes_nan(self):
        """
        Input    : Mean Papp A to B = '' (empty cell, not a qualified value).
        Expected : output cell is NaN.
        Rationale: empties are missing data, not lab notations — they shouldn't
                   be preserved as the literal empty string.
        """
        target = self._convert_with_papp_value('')
        # truly empty cell → NaN, not ''
        self.assertTrue(pd.isna(target.loc[0, 'Mean Papp A to B']))


# ---------- mask distribution warnings ----------

class MaskDistributionWarningTests(unittest.TestCase):
    """Warn loudly when the inhibitor mask is degenerate (all True or all False).

    A typo like 'inhinitor' vs 'inhibitor' in the caller's `.str.contains(...)`
    expression silently yields an all-False mask; without these warnings, the
    only symptom is NaN-filled inhibitor columns far downstream.
    """

    def _run_and_capture(self, mask):
        """Call the conversion and return whatever it printed to stdout."""
        ori = _make_fake_ori()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            convert_to_target_format(
                ori, _make_fake_col_ori(), _make_fake_col_target(), mask,
            )
        return buf.getvalue()

    def test_all_false_mask_emits_warning(self):
        """
        Input    : inhibitor_mask = Series of all False (mimics 'inhinitor' typo).
        Expected : stdout contains 'all False' and the True/total counts.
        Rationale: classic silent-failure mode — needs a loud warning at the source.
        """
        mask = pd.Series([False] * len(_make_fake_ori()))
        out = self._run_and_capture(mask)
        # warning text must mention the all-False state
        self.assertIn('all False', out)
        # and the mask counts must appear so the user can confirm the diagnosis
        self.assertIn('mask True=0', out)

    def test_all_true_mask_emits_warning(self):
        """
        Input    : inhibitor_mask = Series of all True.
        Expected : stdout contains 'all True' and the True/total counts.
        Rationale: symmetric guard — wrong column or inverted predicate.
        """
        mask = pd.Series([True] * len(_make_fake_ori()))
        out = self._run_and_capture(mask)
        # warning text must mention the all-True state
        self.assertIn('all True', out)
        # and the counts (5 True out of 5 total in the fake fixture)
        self.assertIn(f'mask True={len(_make_fake_ori())}', out)

    def test_balanced_mask_emits_no_distribution_warning(self):
        """
        Input    : mixed mask (some True, some False) from the standard fixture.
        Expected : neither 'all False' nor 'all True' appears in stdout.
        Rationale: don't cry wolf — the warning must stay silent in the normal case.
        """
        ori = _make_fake_ori()
        mask = ori['MDR1-MDCK II: Run Conditions'] == 'with_inhibitor'
        out = self._run_and_capture(mask)
        # neither degenerate-mask warning fires
        self.assertNotIn('all False', out)
        self.assertNotIn('all True', out)


# ---------- column-equivalence guard ----------

class CheckColumnsMatchTests(unittest.TestCase):
    """check_columns_match — strict name+order equality between two column lists."""

    def _capture(self, fn, *a, **kw):
        """Run fn under stdout capture and return (result, captured_text)."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = fn(*a, **kw)
        return result, buf.getvalue()

    def test_identical_lists_return_true_and_no_diff(self):
        """
        Input    : two identical column lists.
        Expected : returns True; stdout reports identical_and_in_order=True
                   and prints no diff lines.
        Rationale: happy-path baseline.
        """
        cols = ['a', 'b', 'c']
        result, out = self._capture(check_columns_match, cols, cols)
        # match returns True
        self.assertTrue(result)
        # success line includes the True marker
        self.assertIn('identical_and_in_order: True', out)
        # no diff sections were printed
        self.assertNotIn('only in OLD', out)
        self.assertNotIn('only in NEW', out)

    def test_extra_column_in_new_returns_false_and_lists_it(self):
        """
        Input    : new_cols has one column the old doesn't.
        Expected : returns False; diff names the extra column on the NEW side.
        Rationale: catches the most common drift — a new schema gained a field.
        """
        old = ['a', 'b']
        new = ['a', 'b', 'c']
        result, out = self._capture(check_columns_match, old, new)
        # match is False
        self.assertFalse(result)
        # diff identifies the new-only column
        self.assertIn("only in NEW (1): ['c']", out)
        self.assertIn('only in OLD (0):', out)

    def test_missing_column_in_new_returns_false_and_lists_it(self):
        """
        Input    : new_cols dropped one column the old has.
        Expected : returns False; diff names the missing column on the OLD side.
        Rationale: catches data loss between runs.
        """
        old = ['a', 'b', 'c']
        new = ['a', 'b']
        result, out = self._capture(check_columns_match, old, new)
        # match is False
        self.assertFalse(result)
        # diff identifies the old-only column
        self.assertIn("only in OLD (1): ['c']", out)

    def test_reordered_shared_columns_returns_false_with_order_note(self):
        """
        Input    : same column set, different order.
        Expected : returns False; diff says 'order of shared columns differs'.
        Rationale: catches reordering bugs that would break positional concat.
        """
        old = ['a', 'b', 'c']
        new = ['c', 'b', 'a']
        result, out = self._capture(check_columns_match, old, new)
        # match is False
        self.assertFalse(result)
        # order-mismatch hint is surfaced
        self.assertIn('order of shared columns differs', out)


# ---------- read_xlsx_preserving_qualifiers ----------

class ReadXlsxPreservingQualifiersTests(unittest.TestCase):
    """Recover '<x' / '>x' display-formatted values from xlsx cells."""

    def _make_xlsx(self, dirpath):
        """Build a tiny xlsx with one '\\< 0.000' formatted cell, one plain float,
        and one plain text cell. Returns the file path."""
        import openpyxl
        from openpyxl.styles import NamedStyle  # noqa: F401 — not used but imports openpyxl side-effects
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Upload'
        ws.append(['Compound', 'Mean Papp'])
        # Row 2: qualifier-formatted cell
        ws.append(['TEST-0001', 0.384])
        ws.cell(row=2, column=2).number_format = r'\<\ 0.000'
        # Row 3: plain numeric cell
        ws.append(['TEST-0002', 5.5])
        # Row 4: plain text cell
        ws.append(['TEST-0003', 'NQ'])
        path = f'{dirpath}/qualifiers.xlsx'
        wb.save(path)
        return path

    def test_qualifier_cell_becomes_string(self):
        """
        Input    : xlsx cell value=0.384, format='\\< 0.000'.
        Expected : column value is the string '< 0.384'.
        Rationale: regression test for the exact bug in the Wuxi xlsx
                   where '<' was hidden in Excel format and lost on read.
        """
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            path = self._make_xlsx(d)
            df = read_xlsx_preserving_qualifiers(path, 'Upload')
        # qualifier-formatted cell came back as the displayed string
        self.assertEqual(df.iloc[0]['Mean Papp'], '< 0.384')

    def test_plain_numeric_cell_unchanged(self):
        """
        Input    : xlsx cell value=5.5, format='General'.
        Expected : column value is the float 5.5.
        Rationale: don't disturb cells that didn't have a qualifier format.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = self._make_xlsx(d)
            df = read_xlsx_preserving_qualifiers(path, 'Upload')
        # plain numeric cell passes through unchanged
        self.assertEqual(df.iloc[1]['Mean Papp'], 5.5)

    def test_plain_text_cell_unchanged(self):
        """
        Input    : xlsx cell value='NQ' (text), no special format.
        Expected : column value is the string 'NQ'.
        Rationale: existing text cells must not be re-interpreted.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = self._make_xlsx(d)
            df = read_xlsx_preserving_qualifiers(path, 'Upload')
        # text cell passes through unchanged
        self.assertEqual(df.iloc[2]['Mean Papp'], 'NQ')


# ---------- validate_pivoted_output ----------

class ValidatePivotedOutputTests(unittest.TestCase):
    """Run the validate_pivoted_output check suite on synthetic small DataFrames."""

    def _good_df(self):
        """A small wide-format DataFrame that should pass every check."""
        return pd.DataFrame({
            'Molecule Name':                    ['SRB-0000001', 'SRB-0000002'],
            'Batch Molecule-Batch ID':          ['SRB-0000001-001', 'SRB-0000002-001'],
            'Mean Papp A to B':                 [10.0, 5.0],
            'Mean Papp B to A':                 [20.0, 10.0],
            'Efflux ratio':                     [2.0, 2.0],
            'Mean Papp A to B - PgP Inhibitor': [4.0, 2.0],
            'Mean Papp B to A - PgP inhibitor': [4.0, 2.0],
            'Efflux ratio - PgP inhibitor':     [1.0, 1.0],
        })

    def _capture(self, fn, *a, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = fn(*a, **kw)
        return r, buf.getvalue()

    def test_passes_on_clean_input(self):
        """
        Input    : well-formed ids, batch ids prefixed correctly, consistent efflux.
        Expected : returns True; final line says 'PASS'.
        Rationale: confirms no check spuriously fires on good data.
        """
        result, out = self._capture(validate_pivoted_output, self._good_df())
        # all checks pass
        self.assertTrue(result)
        self.assertIn('PASS', out)

    def test_flags_bad_molecule_name(self):
        """
        Input    : Molecule Name 'NOT-AN-ID' (doesn't match SRB-\\d{7}).
        Expected : returns False; the relevant check shows 'FAIL'.
        Rationale: id-format regression — exactly the class of bug the user
                   asked to catch.
        """
        df = self._good_df()
        df.loc[0, 'Molecule Name'] = 'NOT-AN-ID'
        result, out = self._capture(validate_pivoted_output, df)
        # whole suite fails
        self.assertFalse(result)
        # specifically the molecule-name check fired
        self.assertIn('Molecule Name matches', out)
        self.assertIn('FAIL', out)

    def test_flags_batch_id_prefix_mismatch(self):
        """
        Input    : Batch ID 'SRB-9999999-001' for compound 'SRB-0000001'.
        Expected : returns False; the prefix-check fails.
        Rationale: direct analog of the Wuxi oversight — batch id detached
                   from the compound.
        """
        df = self._good_df()
        df.loc[0, 'Batch Molecule-Batch ID'] = 'SRB-9999999-001'
        result, out = self._capture(validate_pivoted_output, df)
        # whole suite fails
        self.assertFalse(result)
        # specifically the prefix check fired
        self.assertIn('Batch Molecule-Batch ID starts with', out)

    def test_flags_efflux_ratio_inconsistency(self):
        """
        Input    : Efflux ratio is 99 (wildly wrong vs Papp B→A / Papp A→B = 2).
        Expected : returns False; the efflux-consistency check fails.
        Rationale: catches transposed columns or formula errors.
        """
        df = self._good_df()
        df.loc[0, 'Efflux ratio'] = 99.0
        result, out = self._capture(validate_pivoted_output, df)
        # whole suite fails
        self.assertFalse(result)
        # specifically the efflux ratio check fired
        self.assertIn('Efflux ratio', out)

    def test_flags_duplicate_compound(self):
        """
        Input    : same (Molecule Name, Batch Molecule-Batch ID) on two rows.
        Expected : returns False; uniqueness check fails.
        Rationale: catches accidental wide-format dupes.
        """
        df = self._good_df()
        df.loc[1, 'Molecule Name'] = df.loc[0, 'Molecule Name']
        df.loc[1, 'Batch Molecule-Batch ID'] = df.loc[0, 'Batch Molecule-Batch ID']
        result, out = self._capture(validate_pivoted_output, df)
        # whole suite fails
        self.assertFalse(result)
        # specifically the uniqueness check fired
        self.assertIn('Unique', out)


class NormalizeSpeciesTests(unittest.TestCase):
    """convert_upload.normalize_species — map raw WuXi Species cells (e.g. 'FBS')
    onto the `species:` value the protocol blocks route on (e.g. '10%FBS').
    Synthetic ids only."""

    HEADER = ['Molecule-Batch ID', 'Study number', 'species', '%Unbound']
    ALIASES = {'FBS': '10%FBS'}

    def _rows(self, *species):
        return [['TEST-0000001-001', 'STU-1', sp, 1.0] for sp in species]

    def test_renames_fbs(self):
        """'FBS' becomes '10%FBS' so it routes to the 10% FBS protocol."""
        out = normalize_species(self.HEADER, self._rows('FBS'), self.ALIASES)
        # the species cell now carries the routing value
        self.assertEqual(out[0][2], '10%FBS')

    def test_match_ignores_case_and_spaces(self):
        """' fbs ' still matches; WuXi spacing/case is not reliable."""
        out = normalize_species(self.HEADER, self._rows(' fbs '), self.ALIASES)
        # normalised despite the stray spaces and lower case
        self.assertEqual(out[0][2], '10%FBS')

    def test_leaves_other_species_alone(self):
        """Mouse/Human/Rat are already correct and must not change."""
        out = normalize_species(self.HEADER, self._rows('Mouse', 'Human', 'Rat'), self.ALIASES)
        # every non-aliased species passes through untouched
        self.assertEqual([r[2] for r in out], ['Mouse', 'Human', 'Rat'])

    def test_already_correct_value_is_kept(self):
        """A file that already says '10%FBS' stays '10%FBS'."""
        out = normalize_species(self.HEADER, self._rows('10%FBS'), self.ALIASES)
        # idempotent
        self.assertEqual(out[0][2], '10%FBS')

    def test_does_not_mutate_caller_rows(self):
        """The caller's list must survive unchanged."""
        rows = self._rows('FBS')
        normalize_species(self.HEADER, rows, self.ALIASES)
        # original row still holds the raw value
        self.assertEqual(rows[0][2], 'FBS')

    def test_no_species_column_is_a_no_op(self):
        """A LogD file has no species column; rows return untouched."""
        header = ['Molecule-Batch ID', 'Study number']
        rows = [['TEST-0000001-001', 'STU-1']]
        # same object back, nothing to do
        self.assertIs(normalize_species(header, rows, self.ALIASES), rows)

    def test_empty_alias_map_is_a_no_op(self):
        """No `species_aliases:` in config must not break the import."""
        rows = self._rows('FBS')
        # returns the rows unchanged when the map is empty or None
        self.assertIs(normalize_species(self.HEADER, rows, None), rows)

    def test_split_routes_renamed_rows(self):
        """After the rename, split_species yields a '10%FBS' unit, not 'FBS'."""
        rows = normalize_species(self.HEADER, self._rows('FBS', 'Mouse'), self.ALIASES)
        units = dict(split_species(self.HEADER, rows))
        # the unit key now matches the config `species:` value
        self.assertEqual(sorted(units), ['10%FBS', 'Mouse'])



class SpeciesAliasMapTests(unittest.TestCase):
    """convert_upload.species_alias_map — fold each protocol's own `species:` value
    and the `species_aliases:` short forms into one lookup, so case differences and
    CRO spellings collapse onto one canonical value per protocol."""

    CONFIG = {
        'protocols': {
            125963: {'species': 'Mouse'},
            135299: {'species': 'Human'},
            135303: {'species': '10%FBS'},
            85979: {},                      # no species (LogD) — must be skipped
        },
        'species_aliases': {'FBS': '10%FBS'},
    }
    HEADER = ['Molecule-Batch ID', 'species', '%Unbound']

    def _rows(self, *species):
        return [['TEST-0000001-001', sp, 1.0] for sp in species]

    def test_own_species_values_are_canonical(self):
        """Each block's `species:` maps to itself, keyed lowercase."""
        m = species_alias_map(self.CONFIG)
        # the config value is both the key (lowercased) and the canonical result
        self.assertEqual(m['mouse'], 'Mouse')

    def test_blocks_without_species_are_skipped(self):
        """LogD has no `species:`, so it contributes no entry."""
        m = species_alias_map(self.CONFIG)
        # only the three species-bearing blocks plus the one alias
        self.assertEqual(sorted(m), ['10%fbs', 'fbs', 'human', 'mouse'])

    def test_alias_layers_on_top(self):
        """`species_aliases:` adds the CRO short form."""
        m = species_alias_map(self.CONFIG)
        # FBS resolves to the canonical 10%FBS
        self.assertEqual(m['fbs'], '10%FBS')

    def test_lowercase_species_merges_with_canonical(self):
        """A file writing both 'mouse' and 'Mouse' must yield ONE unit."""
        rows = normalize_species(self.HEADER, self._rows('mouse', 'Mouse'),
                                 species_alias_map(self.CONFIG))
        # both spellings collapse to the canonical 'Mouse'
        self.assertEqual([r[1] for r in rows], ['Mouse', 'Mouse'])

    def test_fbs_and_10pct_fbs_merge_into_one_unit(self):
        """'FBS' and '10%FBS' must land in a single split unit."""
        rows = normalize_species(self.HEADER, self._rows('FBS', '10%FBS'),
                                 species_alias_map(self.CONFIG))
        units = dict(split_species(self.HEADER, rows))
        # one key, holding both rows
        self.assertEqual(list(units), ['10%FBS'])
        self.assertEqual(len(units['10%FBS']), 2)

    def test_inner_spaces_are_ignored(self):
        """'10% FBS' (the CDD protocol's own spelling) must match '10%FBS'."""
        rows = normalize_species(self.HEADER, self._rows('10% FBS', '10 % fbs'),
                                 species_alias_map(self.CONFIG))
        # inner spacing and case are both irrelevant to routing
        self.assertEqual([r[1] for r in rows], ['10%FBS', '10%FBS'])

    def test_unknown_species_passes_through(self):
        """An unrecognised species is left alone, so it shows up for manual review."""
        rows = normalize_species(self.HEADER, self._rows('Hamster'),
                                 species_alias_map(self.CONFIG))
        # untouched, rather than silently dropped or mis-routed
        self.assertEqual(rows[0][1], 'Hamster')



class ApplyCorrectionsTests(unittest.TestCase):
    """convert_upload.apply_corrections — config-driven NA fills + decimal rounding, so a
    RAW CRO file imports the same as a hand-corrected one. Synthetic ids only."""

    BLOCK = {'corrections': {'fill_na': {'%Unbound': 0, '%Bound': 100},
                             'round_to': {1: ['%Unbound', '%Bound', '%Remaining']}}}
    HEADER = ['Molecule-Batch ID', '%Unbound', '%Bound', '%Remaining', 'Comment']

    def _rows(self, *vals):
        return [['TEST-0000001-001', *v] for v in vals]

    def test_fills_na_unbound_with_zero(self):
        """An empty %Unbound becomes 0, per config."""
        out, _ = apply_corrections(self.HEADER, self._rows(['', 50.0, 90.0, '']), self.BLOCK)
        # blank cell replaced by the configured fill
        self.assertEqual(out[0][1], 0)

    def test_fills_na_bound_with_hundred(self):
        """A literal 'NA' in %Bound becomes 100."""
        out, _ = apply_corrections(self.HEADER, self._rows([1.0, 'NA', 90.0, '']), self.BLOCK)
        # 'NA' counts as not-available, not as text to keep
        self.assertEqual(out[0][2], 100)

    def test_rounds_to_one_decimal(self):
        """Values are rounded to the configured number of decimals."""
        out, _ = apply_corrections(self.HEADER, self._rows([1.2345, 98.7654, 95.4321, '']), self.BLOCK)
        # all three configured columns rounded to 1 dp
        self.assertEqual([out[0][1], out[0][2], out[0][3]], [1.2, 98.8, 95.4])

    def test_keeps_qualifier_when_rounding(self):
        """A '<'/'>' qualified value keeps its qualifier."""
        out, _ = apply_corrections(self.HEADER, self._rows(['<0.567', 99.0, 90.0, '']), self.BLOCK)
        # qualifier preserved, number rounded
        self.assertEqual(out[0][1], '< 0.6')

    def test_remaining_na_is_left_alone(self):
        """%Remaining has no fill rule, so an NA stays NA (only rounding applies)."""
        out, _ = apply_corrections(self.HEADER, self._rows([1.0, 99.0, 'NA', '']), self.BLOCK)
        # not filled, because config lists no fill_na for %Remaining
        self.assertEqual(out[0][3], 'NA')

    def test_text_columns_untouched(self):
        """Columns outside the rules are never modified."""
        out, _ = apply_corrections(self.HEADER, self._rows([1.0, 99.0, 90.0, 'keep me']), self.BLOCK)
        # the Comment column passes through verbatim
        self.assertEqual(out[0][4], 'keep me')

    def test_does_not_mutate_caller_rows(self):
        """The caller's rows must survive unchanged."""
        rows = self._rows(['', 50.0, 90.0, ''])
        apply_corrections(self.HEADER, rows, self.BLOCK)
        # original still blank
        self.assertEqual(rows[0][1], '')

    def test_no_corrections_block_is_a_no_op(self):
        """A protocol without `corrections:` (e.g. LogD) is returned untouched."""
        rows = self._rows(['', 50.0, 90.0, ''])
        out, summary = apply_corrections(self.HEADER, rows, {})
        # same object back, empty summary
        self.assertIs(out, rows)
        self.assertEqual(summary, {})

    def test_missing_column_is_skipped(self):
        """A rule naming a column the file lacks must not raise."""
        header = ['Molecule-Batch ID', '%Unbound']
        out, _ = apply_corrections(header, [['TEST-0000001-001', '']], self.BLOCK)
        # %Bound/%Remaining absent -> skipped silently, %Unbound still filled
        self.assertEqual(out[0][1], 0)

    def test_summary_counts_what_changed(self):
        """The summary reports per-column counts for logging."""
        _, summary = apply_corrections(self.HEADER,
                                       self._rows(['', 'NA', 1.234, ''], ['', 50.0, 2.0, '']),
                                       self.BLOCK)
        # two blank %Unbound cells were filled
        self.assertEqual(summary.get('%Unbound: NA->0'), 2)



class GraphNotificationTests(unittest.TestCase):
    """notify._send_graph — Microsoft Graph app-only send. M365 disables SMTP basic
    auth, so this is the supported transport. Fully stubbed: no network, no secrets."""

    N = {'transport': 'graph', 'recipient': 'a@example.com', 'sender': 'auto@example.com',
         'tenant_id': 'TENANT', 'client_id': 'CLIENT', 'client_secret_file': '/nonexistent'}

    def _stub(self, token_ok=True, send_status=202):
        """Fake `requests` capturing what would be sent."""
        calls = {}
        class R:
            def __init__(s, ok, status, payload=None):
                s.ok, s.status_code, s._p, s.text = ok, status, payload or {}, ''
            def json(s): return s._p
        class Fake:
            @staticmethod
            def post(url, data=None, headers=None, json=None, timeout=None):
                calls.setdefault('urls', []).append(url)
                if 'login.microsoftonline.com' in url:
                    calls['token_data'] = data
                    return R(token_ok, 200 if token_ok else 401, {'access_token': 'TOK'})
                calls['headers'], calls['payload'] = headers, json
                return R(send_status == 202, send_status)
        return Fake, calls

    def _run(self, subject='S', body='B', html=None, **kw):
        import notify
        fake, calls = self._stub(**kw)
        real = sys.modules.get('requests')
        sys.modules['requests'] = fake
        notify._secret = lambda *a, **k: 'SECRET'          # stub the on-disk secret
        try:
            return notify._send_graph(self.N, subject, body, html), calls
        finally:
            if real is not None: sys.modules['requests'] = real
            else: sys.modules.pop('requests', None)

    def test_returns_true_on_202(self):
        """Graph answers 202 Accepted on a successful sendMail."""
        ok, _ = self._run()
        # 202 is success for sendMail (no body returned)
        self.assertTrue(ok)

    def test_posts_to_the_sender_mailbox(self):
        """The send URL addresses the configured sender mailbox."""
        _, calls = self._run()
        # app-only sends AS a named mailbox
        self.assertIn('https://graph.microsoft.com/v1.0/users/auto@example.com/sendMail', calls['urls'])

    def test_uses_client_credentials_grant(self):
        """Token request is the app-only client-credentials flow, not a user login."""
        _, calls = self._run()
        # no user sign-in, hence no MFA prompt
        self.assertEqual(calls['token_data']['grant_type'], 'client_credentials')
        self.assertEqual(calls['token_data']['scope'], 'https://graph.microsoft.com/.default')

    def test_sends_bearer_token(self):
        """The access token is presented as a Bearer header."""
        _, calls = self._run()
        # token from the first call is reused on the send
        self.assertEqual(calls['headers']['Authorization'], 'Bearer TOK')

    def test_prefers_html_body(self):
        """Graph carries one body part, so the HTML rendering wins when present."""
        _, calls = self._run(html='<b>x</b>')
        b = calls['payload']['message']['body']
        # HTML chosen over the plain-text alternative
        self.assertEqual(b['contentType'], 'HTML')
        self.assertEqual(b['content'], '<b>x</b>')

    def test_falls_back_to_text(self):
        """Without HTML the plain body is sent as Text."""
        _, calls = self._run()
        # plain text when no HTML rendering was built
        self.assertEqual(calls['payload']['message']['body']['contentType'], 'Text')

    def test_splits_multiple_recipients(self):
        """A comma-separated recipient list becomes several toRecipients entries."""
        import notify
        fake, calls = self._stub()
        real = sys.modules.get('requests'); sys.modules['requests'] = fake
        notify._secret = lambda *a, **k: 'SECRET'
        try:
            notify._send_graph({**self.N, 'recipient': 'a@x.com, b@x.com'}, 'S', 'B')
        finally:
            if real is not None: sys.modules['requests'] = real
            else: sys.modules.pop('requests', None)
        got = [r['emailAddress']['address'] for r in calls['payload']['message']['toRecipients']]
        # both addresses, trimmed
        self.assertEqual(got, ['a@x.com', 'b@x.com'])

    def test_returns_false_on_send_error(self):
        """A non-202 response fails softly — notifications never break an import."""
        ok, _ = self._run(send_status=403)
        # False, not an exception
        self.assertFalse(ok)

    def test_returns_false_when_token_fails(self):
        """A rejected token request fails softly too."""
        ok, _ = self._run(token_ok=False)
        # e.g. admin consent not granted yet
        self.assertFalse(ok)



class ApplyGlobalIgnoresTests(unittest.TestCase):
    """convert_upload.apply_global_ignores — the top-level `ignore_columns:` list is
    folded into every protocol's own `ignore:` at config load, so a column the CRO
    adds to every report stops blocking validation without being copied per block."""

    def test_global_column_is_added_to_every_block(self):
        """Each protocol gains the global column alongside its own ignores."""
        cfg = {
            "ignore_columns": ["Compound ID"],
            "protocols": {1: {"ignore": ["Run Lab"]}, 2: {"ignore": ["Molecule Name"]}},
        }
        apply_global_ignores(cfg)
        # every block keeps its own entries and gains the global one
        self.assertEqual(cfg["protocols"][1]["ignore"], ["Run Lab", "Compound ID"])
        self.assertEqual(cfg["protocols"][2]["ignore"], ["Molecule Name", "Compound ID"])

    def test_block_without_ignore_key_gets_one(self):
        """A block with no `ignore:` still receives the global columns."""
        cfg = {"ignore_columns": ["Compound ID"], "protocols": {1: {"readouts": {}}}}
        apply_global_ignores(cfg)
        # the key is created rather than skipped
        self.assertEqual(cfg["protocols"][1]["ignore"], ["Compound ID"])

    def test_no_duplicate_when_block_already_lists_it(self):
        """A column named both globally and per block appears exactly once."""
        cfg = {"ignore_columns": ["Compound ID"], "protocols": {1: {"ignore": ["Compound  ID"]}}}
        apply_global_ignores(cfg)
        # whitespace-insensitive match, so the block's own spelling is kept alone
        self.assertEqual(cfg["protocols"][1]["ignore"], ["Compound  ID"])

    def test_missing_or_empty_global_list_is_a_noop(self):
        """Config without `ignore_columns:` is returned untouched."""
        cfg = {"protocols": {1: {"ignore": ["Run Lab"]}}}
        apply_global_ignores(cfg)
        # nothing added when there is no global list
        self.assertEqual(cfg["protocols"][1]["ignore"], ["Run Lab"])

    def test_global_ignore_unblocks_mapping(self):
        """The folded column counts as ignored, not unmapped, so `ok` becomes True."""
        cfg = {
            "ignore_columns": ["Compound ID"],
            "protocols": {1: {"identifiers": {"batch_id": "Molecule-Batch ID"},
                              "readouts": {"LogD": 1}}},
        }
        block = cfg["protocols"][1]
        header = ["Compound ID", "Molecule-Batch ID", "LogD"]
        # before folding, the extra column blocks the gate
        self.assertFalse(evaluate_mapping(header, block)["ok"])
        apply_global_ignores(cfg)
        res = evaluate_mapping(header, block)
        # after folding it is reported as ignored and the gate opens
        self.assertTrue(res["ok"])
        self.assertEqual(res["ignored"], ["Compound ID"])
        self.assertEqual(res["unmapped"], [])


if __name__ == '__main__':
    unittest.main()
