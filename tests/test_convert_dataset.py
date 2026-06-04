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

from convert_dataset import convert_to_target_format, normalize_ori_columns


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


if __name__ == '__main__':
    unittest.main()
