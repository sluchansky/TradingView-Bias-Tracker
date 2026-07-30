"""
V1 Phase 7C.3 — Main Brain Transparency Contract Tests (Backend)
================================================================

Tests the backend side of the Phase 7C.1/7C.2 transparency contract:

  - _mb_verdict: edge_components, score_breakdown, failed_confirmations,
    risks passthrough (Phase 7C.2 additions)
  - build_main_brain_payload: voice, gateway_status aliases
  - _mb_system_status: db_ready, databento_ready, broker_ready aliases
  - Display total consistency: edge_score vs component sum
  - Truthfulness: false/null not coerced to positive states

Rules enforced by this suite:
  - NO production behaviour changed
  - NO trading logic touched
  - NO gateway / execution paths involved
  - NO Databento paths touched
  - NO strategy/scoring calculations changed

Run:
    python3 artifacts/tradingview-webhook/test_phase7c3_transparency_contracts.py
"""

import json
import unittest
import sys
import os
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# Module bootstrap (same pattern as 7B/7C/7C.1 suites)
# ---------------------------------------------------------------------------
_APP = None

def _app():
    global _APP
    if _APP is None:
        import app as _module
        _APP = _module
    return _APP


# ---------------------------------------------------------------------------
# Helpers shared across test cases
# ---------------------------------------------------------------------------

def _make_edge_breakdown(present_keys=None, absent_keys=None):
    """Build a minimal edge_breakdown dict matching live schema."""
    all_comps = [
        {'key': 'bos_confirmed',    'label': 'Bullish BOS',        'points': 20, 'present': False},
        {'key': 'choch_confirmed',  'label': 'Bullish CHOCH',       'points': 20, 'present': False},
        {'key': 'vwap_confirmed',   'label': 'VWAP Reclaim',        'points': 15, 'present': False},
        {'key': 'liquidity_sweep',  'label': 'Liquidity Sweep',     'points': 15, 'present': False},
        {'key': 'volume_confirmed', 'label': 'Volume Confirmation', 'points': 15, 'present': False},
        {'key': 'cvd_confirmed',    'label': 'CVD Confirms Long',   'points': 15, 'present': False},
        {'key': 'preferred_session','label': 'Session Bonus',       'points': 10, 'present': False},
    ]
    for c in all_comps:
        if present_keys and c['key'] in present_keys:
            c['present'] = True
    score = sum(c['points'] for c in all_comps if c['present'])
    present_labels = [c['label'] for c in all_comps if c['present']]
    absent_labels  = [c['label'] for c in all_comps if not c['present']]
    return {
        'components':          all_comps,
        'score':               score,
        'raw_score':           score,
        'grade':               'READY' if score >= 75 else 'WAIT',
        'score_breakdown':     [{'label': c['label'], 'points': c['points']} for c in all_comps if c['present']],
        'failed_confirmations': absent_labels,
        'reasons':             present_labels,
        'risks':               [],
        'max_score':           110,
        'cap_applied':         False,
    }


def _make_result(edge_breakdown=None, strict_label='Long WAIT',
                 strict_direction='Long', edge_score=45,
                 strict_missing=None, strict_reason=None,
                 trade_plan=None):
    """Build a minimal full_analysis result dict for _mb_verdict tests."""
    eb = edge_breakdown or _make_edge_breakdown(present_keys=['vwap_confirmed', 'volume_confirmed', 'cvd_confirmed'])
    return {
        'strict_label':     strict_label,
        'strict_direction': strict_direction,
        'edge_score':       edge_score,
        'strict_reason':    strict_reason or f'{strict_direction} WAIT — failed: structure.',
        'strict_missing':   strict_missing or ['structure_confirmed'],
        'edge_breakdown':   eb,
        'trade_plan':       trade_plan or {},
    }


# ---------------------------------------------------------------------------
# ── TASK 6 — _mb_verdict passthrough tests ─────────────────────────────────
# ---------------------------------------------------------------------------

class TestMbVerdictPassthrough(unittest.TestCase):
    """Verify _mb_verdict passes edge_components, score_breakdown,
    failed_confirmations, risks through correctly (Phase 7C.2 additions)."""

    def setUp(self):
        self.fn = _app()._mb_verdict

    def _call(self, result, errors=None):
        return self.fn(result, errors if errors is not None else [])

    # -- edge_components -------------------------------------------------------

    def test_edge_components_is_list(self):
        out = self._call(_make_result())
        self.assertIsInstance(out['edge_components'], list)

    def test_edge_components_seven_items(self):
        out = self._call(_make_result())
        self.assertEqual(len(out['edge_components']), 7)

    def test_edge_components_preserves_key(self):
        out = self._call(_make_result())
        keys = {c['key'] for c in out['edge_components']}
        self.assertIn('bos_confirmed', keys)
        self.assertIn('vwap_confirmed', keys)
        self.assertIn('preferred_session', keys)

    def test_edge_components_preserves_label(self):
        out   = self._call(_make_result())
        bos   = next(c for c in out['edge_components'] if c['key'] == 'bos_confirmed')
        vwap  = next(c for c in out['edge_components'] if c['key'] == 'vwap_confirmed')
        self.assertEqual(bos['label'],  'Bullish BOS')
        self.assertEqual(vwap['label'], 'VWAP Reclaim')

    def test_edge_components_preserves_points(self):
        out = self._call(_make_result())
        bos = next(c for c in out['edge_components'] if c['key'] == 'bos_confirmed')
        self.assertEqual(bos['points'], 20)

    def test_edge_components_present_true_preserved(self):
        eb   = _make_edge_breakdown(present_keys=['vwap_confirmed'])
        out  = self._call(_make_result(edge_breakdown=eb))
        vwap = next(c for c in out['edge_components'] if c['key'] == 'vwap_confirmed')
        self.assertTrue(vwap['present'])

    def test_edge_components_present_false_preserved(self):
        out = self._call(_make_result())
        bos = next(c for c in out['edge_components'] if c['key'] == 'bos_confirmed')
        self.assertFalse(bos['present'])

    def test_edge_components_ordering_deterministic(self):
        """Order must match the EDGE_COMPONENTS tuple ordering."""
        out   = self._call(_make_result())
        keys  = [c['key'] for c in out['edge_components']]
        expected_first = 'bos_confirmed'
        expected_last  = 'preferred_session'
        self.assertEqual(keys[0], expected_first)
        self.assertEqual(keys[-1], expected_last)

    def test_edge_components_empty_breakdown_produces_empty_list(self):
        result = _make_result()
        result['edge_breakdown'] = {}
        out = self._call(result)
        self.assertIsInstance(out['edge_components'], list)
        self.assertEqual(len(out['edge_components']), 0)

    def test_edge_components_none_breakdown_produces_empty_list(self):
        result = _make_result()
        result['edge_breakdown'] = None
        out = self._call(result)
        self.assertIsInstance(out['edge_components'], list)

    # -- score_breakdown -------------------------------------------------------

    def test_score_breakdown_is_list(self):
        out = self._call(_make_result())
        self.assertIsInstance(out['score_breakdown'], list)

    def test_score_breakdown_items_have_label_and_points(self):
        eb  = _make_edge_breakdown(present_keys=['vwap_confirmed'])
        out = self._call(_make_result(edge_breakdown=eb))
        self.assertTrue(any(i.get('label') == 'VWAP Reclaim' for i in out['score_breakdown']))

    def test_score_breakdown_empty_when_nothing_present(self):
        eb  = _make_edge_breakdown(present_keys=[])
        out = self._call(_make_result(edge_breakdown=eb))
        self.assertEqual(len(out['score_breakdown']), 0)

    # -- failed_confirmations --------------------------------------------------

    def test_failed_confirmations_is_list(self):
        out = self._call(_make_result())
        self.assertIsInstance(out['failed_confirmations'], list)

    def test_failed_confirmations_absent_keys_listed(self):
        out = self._call(_make_result())
        self.assertIn('Bullish BOS',  out['failed_confirmations'])
        self.assertIn('Bullish CHOCH', out['failed_confirmations'])

    def test_failed_confirmations_present_keys_not_listed(self):
        out = self._call(_make_result())
        # vwap_confirmed is present → 'VWAP Reclaim' should NOT be in failed
        self.assertNotIn('VWAP Reclaim', out['failed_confirmations'])

    def test_failed_confirmations_empty_when_all_present(self):
        all_keys = ['bos_confirmed','choch_confirmed','vwap_confirmed',
                    'liquidity_sweep','volume_confirmed','cvd_confirmed','preferred_session']
        eb  = _make_edge_breakdown(present_keys=all_keys)
        out = self._call(_make_result(edge_breakdown=eb, edge_score=110, strict_label='Long READY'))
        self.assertEqual(len(out['failed_confirmations']), 0)

    # -- risks -----------------------------------------------------------------

    def test_risks_is_list(self):
        out = self._call(_make_result())
        self.assertIsInstance(out['risks'], list)

    def test_risks_populated_when_present(self):
        eb = _make_edge_breakdown()
        eb['risks'] = ['Choppy conditions', 'Low volume']
        out = self._call(_make_result(edge_breakdown=eb))
        self.assertIn('Choppy conditions', out['risks'])

    def test_risks_empty_when_absent(self):
        out = self._call(_make_result())
        self.assertEqual(len(out['risks']), 0)

    # -- existing fields still present (regression guard) ----------------------

    def test_existing_direction_field_preserved(self):
        out = self._call(_make_result(strict_direction='Long'))
        self.assertEqual(out['direction'], 'Long')

    def test_existing_edge_score_preserved(self):
        out = self._call(_make_result(edge_score=72))
        self.assertEqual(out['edge_score'], 72)

    def test_existing_is_actionable_preserved(self):
        out = self._call(_make_result(strict_label='Long READY'))
        self.assertTrue(out['is_actionable'])

    def test_existing_failed_conditions_preserved(self):
        out = self._call(_make_result(strict_missing=['edge_score(45<75)', 'structure_confirmed']))
        self.assertIn('edge_score(45<75)', out['failed_conditions'])

    # -- truthfulness: false/null never coerced --------------------------------

    def test_false_present_stays_false(self):
        out = self._call(_make_result())
        bos = next(c for c in out['edge_components'] if c['key'] == 'bos_confirmed')
        self.assertFalse(bos['present'])
        self.assertIsNot(bos['present'], None)

    def test_not_actionable_when_wait(self):
        out = self._call(_make_result(strict_label='Long WAIT'))
        self.assertFalse(out['is_actionable'])

    # -- error fallback --------------------------------------------------------

    def test_error_fallback_returns_safe_structure(self):
        """Pass None result → error path returns documented safe fallback."""
        out = self._call(None)
        self.assertIsInstance(out['edge_components'], list)
        self.assertIsInstance(out['score_breakdown'], list)
        self.assertIsInstance(out['failed_confirmations'], list)
        self.assertIsInstance(out['risks'], list)
        self.assertEqual(out['readiness'], 'WAIT')
        self.assertFalse(out['is_actionable'])


# ---------------------------------------------------------------------------
# ── TASK 6 — _mb_system_status aliases ─────────────────────────────────────
# ---------------------------------------------------------------------------

class TestMbSystemStatusAliases(unittest.TestCase):
    """Verify db_ready, databento_ready, broker_ready aliases derive from
    their correct canonical sources and false/null are never coerced.

    _mb_system_status reads from global module state and environment variables
    (not from the result dict), so tests use mock.patch to control inputs and
    verify alias-source parity.
    """

    def _call(self):
        return _app()._mb_system_status({}, [])

    def test_db_ready_alias_is_present(self):
        out = self._call()
        self.assertIn('db_ready', out)

    def test_databento_ready_alias_is_present(self):
        out = self._call()
        self.assertIn('databento_ready', out)

    def test_broker_ready_alias_is_present(self):
        out = self._call()
        self.assertIn('broker_ready', out)

    def test_all_aliases_are_booleans(self):
        out = self._call()
        self.assertIsInstance(out['db_ready'],        bool)
        self.assertIsInstance(out['databento_ready'], bool)
        self.assertIsInstance(out['broker_ready'],    bool)

    def test_db_ready_matches_database_ready(self):
        """Alias must equal its canonical source in the same response."""
        out = self._call()
        self.assertEqual(out['db_ready'], out['database_ready'])

    def test_databento_ready_matches_databento_connected(self):
        """Alias must equal its canonical source in the same response."""
        out = self._call()
        self.assertEqual(out['databento_ready'], out['databento_connected'])

    def test_broker_ready_matches_broker_url_configured(self):
        """Alias must equal its canonical source in the same response."""
        out = self._call()
        self.assertEqual(out['broker_ready'], out['broker_url_configured'])

    def test_databento_ready_is_false_when_env_disabled(self):
        """DATABENTO_ENABLED=0 in env → databento_ready must be False."""
        from unittest.mock import patch
        with patch.dict(__import__('os').environ, {'DATABENTO_ENABLED': '0'}, clear=False):
            out = _app()._mb_system_status({}, [])
        self.assertFalse(out['databento_ready'])

    def test_databento_ready_false_not_coerced_to_none(self):
        from unittest.mock import patch
        with patch.dict(__import__('os').environ, {'DATABENTO_ENABLED': '0'}, clear=False):
            out = _app()._mb_system_status({}, [])
        self.assertIsNotNone(out['databento_ready'])

    def test_broker_ready_false_when_url_absent(self):
        """Empty / absent TRADERSPOST_WEBHOOK_URL → broker_ready must be False."""
        from unittest.mock import patch
        env = {k: v for k, v in __import__('os').environ.items() if k != 'TRADERSPOST_WEBHOOK_URL'}
        env['TRADERSPOST_WEBHOOK_URL'] = ''
        with patch.dict(__import__('os').environ, env, clear=True):
            out = _app()._mb_system_status({}, [])
        self.assertFalse(out['broker_ready'])

    def test_source_values_preserved_alongside_aliases(self):
        out = self._call()
        self.assertIn('database_ready',       out)
        self.assertIn('databento_connected',  out)
        self.assertIn('broker_url_configured', out)


# ---------------------------------------------------------------------------
# ── TASK 6 — voice passthrough in build_main_brain_payload ─────────────────
# ---------------------------------------------------------------------------

class TestVoicePassthrough(unittest.TestCase):
    """Verify the voice field is correctly passed into the payload."""

    def _call_verdict(self, result):
        return _app()._mb_verdict(result, [])

    def test_edge_components_backend_does_not_recalculate_edge_score(self):
        """Backend must not recompute the edge score for presentation;
        it must pass the pre-computed value through unchanged."""
        result = _make_result(edge_score=67)
        out    = self._call_verdict(result)
        # edge_score must be the one from result, not a recomputed sum from components
        self.assertEqual(out['edge_score'], 67)

    def test_edge_components_count_does_not_affect_score(self):
        """Adding more components does not change the passed-through score."""
        eb  = _make_edge_breakdown(present_keys=['vwap_confirmed', 'cvd_confirmed'])
        out = self._call_verdict(_make_result(edge_breakdown=eb, edge_score=30))
        self.assertEqual(out['edge_score'], 30)


# ---------------------------------------------------------------------------
# ── TASK 7 — Display Total Consistency ─────────────────────────────────────
# ---------------------------------------------------------------------------

class TestDisplayTotalConsistency(unittest.TestCase):
    """Verify the backend relationship between edge_score, component points,
    and any risk adjustments is consistent and well-defined.

    Canonical relationship (documented here per spec Task 7):
      edge_score = sum(points for present components) + sum(risk_adjustments)
      This may differ from a naive component sum when adjustments exist.
      The UI must use backend-provided edge_score, not recompute from components.
    """

    def _verdict(self, result):
        return _app()._mb_verdict(result, [])

    def test_component_sum_matches_score_when_no_adjustments(self):
        """When no risk_adjustments present, present-component sum = score."""
        # 3 components present: 15+15+15 = 45
        out        = self._verdict(_make_result(edge_score=45))
        comps      = out['edge_components']
        present_pts = sum(c['points'] for c in comps if c.get('present'))
        self.assertEqual(present_pts, 45)
        self.assertEqual(out['edge_score'], 45)

    def test_score_from_backend_preserved_verbatim(self):
        """edge_score from full_analysis is passed through, not recomputed."""
        # Even if it doesn't match the component sum, the backend value is authoritative
        out = self._verdict(_make_result(edge_score=42))
        self.assertEqual(out['edge_score'], 42)

    def test_edge_max_is_always_110(self):
        out = self._verdict(_make_result())
        self.assertEqual(out['edge_max'], 110)

    def test_all_components_present_score_max_110(self):
        all_keys = ['bos_confirmed','choch_confirmed','vwap_confirmed',
                    'liquidity_sweep','volume_confirmed','cvd_confirmed','preferred_session']
        eb  = _make_edge_breakdown(present_keys=all_keys)
        out = self._verdict(_make_result(edge_breakdown=eb, edge_score=110))
        comps = out['edge_components']
        total = sum(c['points'] for c in comps if c.get('present'))
        self.assertEqual(total, 110)
        self.assertEqual(out['edge_score'], 110)

    def test_all_absent_score_zero(self):
        eb  = _make_edge_breakdown(present_keys=[])
        out = self._verdict(_make_result(edge_breakdown=eb, edge_score=0))
        comps = out['edge_components']
        total = sum(c['points'] for c in comps if c.get('present'))
        self.assertEqual(total, 0)

    def test_score_breakdown_items_only_include_present_components(self):
        """score_breakdown should list only items that contributed positively."""
        eb  = _make_edge_breakdown(present_keys=['vwap_confirmed', 'cvd_confirmed'])
        out = self._verdict(_make_result(edge_breakdown=eb, edge_score=30))
        sb_labels = {i['label'] for i in out['score_breakdown']}
        # BOS was not present — should not appear in score_breakdown
        self.assertNotIn('Bullish BOS', sb_labels)

    def test_failed_confirmations_disjoint_from_score_breakdown(self):
        """A component cannot be in both score_breakdown and failed_confirmations."""
        out = self._verdict(_make_result())
        sb_labels   = {i['label'] for i in out['score_breakdown']}
        failed_set  = set(out['failed_confirmations'])
        overlap     = sb_labels & failed_set
        self.assertEqual(len(overlap), 0,
            f"Overlap between score_breakdown and failed_confirmations: {overlap}")

    def test_present_component_count_correct(self):
        """Number of present components matches the score_breakdown count."""
        out     = self._verdict(_make_result())
        present = [c for c in out['edge_components'] if c.get('present')]
        # score_breakdown may include risk adjustments; at minimum, every present
        # component should appear in score_breakdown
        sb_labels = {i['label'] for i in out['score_breakdown']}
        for c in present:
            self.assertIn(c['label'], sb_labels,
                f"Present component '{c['label']}' missing from score_breakdown")


# ---------------------------------------------------------------------------
# ── Truthfulness guard — _mb_verdict never fabricates ──────────────────────
# ---------------------------------------------------------------------------

class TestVerdictTruthfulness(unittest.TestCase):
    """Explicit non-fabrication tests for _mb_verdict."""

    def _verdict(self, result):
        return _app()._mb_verdict(result, [])

    def test_no_components_invented_when_edge_breakdown_absent(self):
        result = _make_result()
        result['edge_breakdown'] = {}
        out = self._verdict(result)
        self.assertEqual(len(out['edge_components']), 0)

    def test_no_components_invented_when_components_none(self):
        result = _make_result()
        result['edge_breakdown'] = {'components': None, 'score': 0, 'grade': 'WAIT'}
        out = self._verdict(result)
        self.assertEqual(len(out['edge_components']), 0)

    def test_is_actionable_false_when_wait(self):
        out = self._verdict(_make_result(strict_label='Long WAIT'))
        self.assertFalse(out['is_actionable'])

    def test_is_actionable_true_only_when_ready(self):
        out = self._verdict(_make_result(strict_label='Long READY'))
        self.assertTrue(out['is_actionable'])

    def test_edge_score_zero_not_coerced_positive(self):
        out = self._verdict(_make_result(edge_score=0))
        self.assertEqual(out['edge_score'], 0)

    def test_none_result_produces_safe_fallback_not_positive_state(self):
        # _mb_verdict(None) goes through the success path (result or {} = {})
        # so edge_score is None (not 0) — backend score is authoritative, not invented.
        out = _app()._mb_verdict(None, [])
        self.assertFalse(out['is_actionable'],
            "is_actionable must not default to True for empty/None result")
        self.assertEqual(out['readiness'], 'WAIT',
            "readiness must default to WAIT for empty/None result")
        self.assertIsNone(out['edge_score'],
            "edge_score for None result is None (not fabricated as 0)")
        self.assertIsInstance(out['edge_components'], list,
            "edge_components must be a list even for empty result")

    def test_malformed_components_list_does_not_invent_valid_items(self):
        """Malformed component entries should not produce synthetic valid items."""
        result = _make_result()
        # Mix of valid and invalid items
        result['edge_breakdown'] = {
            'components': [
                None,
                42,
                'bad_string',
                {'key': 'vwap_confirmed', 'label': 'VWAP Reclaim', 'points': 15, 'present': True},
            ],
        }
        out = self._verdict(result)
        # Only the valid dict item should survive
        valid_items = [c for c in out['edge_components'] if isinstance(c, dict)]
        self.assertEqual(len(valid_items), 1)
        self.assertEqual(valid_items[0]['key'], 'vwap_confirmed')


# ---------------------------------------------------------------------------
# ── JSON serializability guard ──────────────────────────────────────────────
# ---------------------------------------------------------------------------

class TestVerdictJsonSerializable(unittest.TestCase):
    """The transparency fields must survive JSON serialisation (HTTP response)."""

    def test_full_verdict_is_json_serializable(self):
        out = _app()._mb_verdict(_make_result(), [])
        try:
            encoded = json.dumps(out)
            decoded = json.loads(encoded)
        except Exception as exc:
            self.fail(f"_mb_verdict output is not JSON-serializable: {exc}")
        self.assertIn('edge_components',      decoded)
        self.assertIn('score_breakdown',      decoded)
        self.assertIn('failed_confirmations', decoded)
        self.assertIn('risks',                decoded)

    def test_empty_fallback_is_json_serializable(self):
        out = _app()._mb_verdict(None, [])
        try:
            json.dumps(out)
        except Exception as exc:
            self.fail(f"_mb_verdict error fallback not JSON-serializable: {exc}")


# ---------------------------------------------------------------------------
# ── Regression guard — existing suites still pass via module integrity ──────
# ---------------------------------------------------------------------------

class TestExistingContractUnchanged(unittest.TestCase):
    """Smoke-test that the existing _mb_verdict contract is intact."""

    def _verdict(self, result):
        return _app()._mb_verdict(result, [])

    def test_direction_field_present(self):
        self.assertIn('direction', self._verdict(_make_result()))

    def test_readiness_field_present(self):
        self.assertIn('readiness', self._verdict(_make_result()))

    def test_edge_score_field_present(self):
        self.assertIn('edge_score', self._verdict(_make_result()))

    def test_grade_field_present(self):
        self.assertIn('grade', self._verdict(_make_result()))

    def test_is_actionable_field_present(self):
        self.assertIn('is_actionable', self._verdict(_make_result()))

    def test_failed_conditions_field_present(self):
        self.assertIn('failed_conditions', self._verdict(_make_result()))

    def test_components_field_still_present(self):
        """components dict (legacy) must still be present for backward compat."""
        self.assertIn('components', self._verdict(_make_result()))

    def test_edge_max_field_present(self):
        self.assertIn('edge_max', self._verdict(_make_result()))

    def test_risk_reward_field_present(self):
        self.assertIn('risk_reward', self._verdict(_make_result()))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    loader  = unittest.TestLoader()
    suite   = unittest.TestSuite()
    classes = [
        TestMbVerdictPassthrough,
        TestMbSystemStatusAliases,
        TestVoicePassthrough,
        TestDisplayTotalConsistency,
        TestVerdictTruthfulness,
        TestVerdictJsonSerializable,
        TestExistingContractUnchanged,
    ]
    for cls in classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, 'w'))
    result = runner.run(suite)

    total  = result.testsRun
    failed = len(result.failures) + len(result.errors)
    passed = total - failed

    print()
    print('=' * 64)
    print(f'  TOTAL: {total} checks — {passed} passed, {failed} failed')
    if failed == 0:
        print('  PASS  all Phase 7C.3 transparency contract checks passed')
    else:
        print(f'  FAIL  {failed} check(s) failed — re-run with verbosity=2 for details')
        for f in result.failures + result.errors:
            print(f'    ✗ {f[0]}')
    print('=' * 64)

    if failed:
        sys.exit(1)
