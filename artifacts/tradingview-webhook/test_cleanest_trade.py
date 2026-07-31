"""test_cleanest_trade.py — Cleanest Trade canonical ranking tests (Cases A–L)

Python port of rankCandidates() / getVerdictFromRecord() / getEdgeFromRecord() /
getDirectionFromRecord() from src/lib/cleanestTrade.ts.

Algorithm invariants verified:
  - Actionable (READY/EARLY READY) > non-actionable (WAIT) regardless of edge
  - Tie on act: higher edge score wins
  - Tie on act + edge: first in iteration order (stable, no flipping)
  - Null records (failed fetches) are silently skipped
  - Empty input → None
  - POTENTIAL candidates qualify (act=0) for the manual button
  - Plan extraction: READY → trade_plan; POTENTIAL → directions[dir].potential_plan
"""

import math
import unittest

# ── Python port of the canonical ranking functions ────────────────────────────

ACTIONABLE_VERDICTS = {
    'LONG READY', 'SHORT READY',
    'LONG EARLY READY', 'SHORT EARLY READY',
}


def is_actionable_verdict(v):
    return str(v or '') in ACTIONABLE_VERDICTS


def get_verdict_from_record(d):
    if not d:
        return 'WAIT'
    brain = d.get('brain') or {}
    decision = brain.get('decision') or {}
    v = decision.get('verdict')
    if v is not None:
        return str(v)
    return str(d.get('verdict') or 'WAIT')


def get_edge_from_record(d):
    if not d:
        return 0.0
    brain = d.get('brain') or {}
    score = brain.get('score') or {}
    v = score.get('value')
    if v is None:
        v = d.get('edge_score')
    if v is None:
        return 0.0
    try:
        n = float(v)
        return n if math.isfinite(n) else 0.0
    except (TypeError, ValueError):
        return 0.0


def get_direction_from_record(d):
    if not d:
        return 'Long'
    v = get_verdict_from_record(d)
    if is_actionable_verdict(v):
        brain = d.get('brain') or {}
        decision = brain.get('decision') or {}
        bdir = decision.get('direction')
        if bdir == 'Short':
            return 'Short'
        if bdir == 'Long':
            return 'Long'
        if 'LONG' in v:
            return 'Long'
        if 'SHORT' in v:
            return 'Short'
    ls = float(d.get('long_score') or 0)
    ss = float(d.get('short_score') or 0)
    return 'Short' if ss > ls else 'Long'


def rank_candidates(inputs):
    """Exact port of rankCandidates():
    inputs = [{'instrument': str, 'mode': str, 'record': dict|None}, ...]
    Returns the winning candidate dict or None.
    """
    best = None
    for inp in inputs:
        record = inp.get('record')
        if not record:
            continue
        v    = get_verdict_from_record(record)
        act  = 1 if is_actionable_verdict(v) else 0
        edge = get_edge_from_record(record)
        cand = {
            'instrument': inp['instrument'],
            'mode':       inp['mode'],
            'act':        act,
            'edge':       edge,
            'verdict':    v,
            'direction':  get_direction_from_record(record),
            'record':     record,
        }
        if best is None:
            best = cand
            continue
        # Identical comparison to the TypeScript: act first, then edge
        if cand['act'] != best['act']:
            if cand['act'] > best['act']:
                best = cand
        elif cand['edge'] > best['edge']:
            best = cand
    return best


def get_plan_from_record(record, direction):
    """Port of getPlanFromRecord()."""
    tp = record.get('trade_plan') or {}
    if tp.get('trade_plan') is True:
        return tp
    dirs = record.get('directions') or {}
    blk = dirs.get(direction) or {}
    pp = blk.get('potential_plan') or {}
    if pp.get('trade_plan') is True:
        return pp
    return None


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _rec(verdict='WAIT', edge=50, long_score=None, short_score=None,
         brain_dir=None, trade_plan=None, directions=None):
    """Build a minimal /status record."""
    rec = {
        'verdict':    verdict,
        'edge_score': edge,
    }
    if long_score is not None:
        rec['long_score'] = long_score
    if short_score is not None:
        rec['short_score'] = short_score
    # Wire into brain contract
    v = verdict
    bdir = brain_dir or (
        ('Long' if 'LONG' in v else 'Short' if 'SHORT' in v else None)
        if is_actionable_verdict(v) else None
    )
    rec['brain'] = {
        'decision': {'verdict': v, 'direction': bdir},
        'score':    {'value': edge},
    }
    if trade_plan is not None:
        rec['trade_plan'] = trade_plan
    if directions is not None:
        rec['directions'] = directions
    return rec


def _inp(inst, mode, record):
    return {'instrument': inst, 'mode': mode, 'record': record}


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestIsActionableVerdict(unittest.TestCase):

    def test_long_ready(self):
        self.assertTrue(is_actionable_verdict('LONG READY'))

    def test_short_ready(self):
        self.assertTrue(is_actionable_verdict('SHORT READY'))

    def test_long_early_ready(self):
        self.assertTrue(is_actionable_verdict('LONG EARLY READY'))

    def test_short_early_ready(self):
        self.assertTrue(is_actionable_verdict('SHORT EARLY READY'))

    def test_wait_not_actionable(self):
        self.assertFalse(is_actionable_verdict('WAIT'))

    def test_empty_not_actionable(self):
        self.assertFalse(is_actionable_verdict(''))

    def test_none_not_actionable(self):
        self.assertFalse(is_actionable_verdict(None))

    def test_partial_string_not_actionable(self):
        # Substring matches must not trigger
        self.assertFalse(is_actionable_verdict('LONG'))
        self.assertFalse(is_actionable_verdict('READY'))


class TestRankCandidates(unittest.TestCase):

    # ── Case A: one qualifying READY trade ────────────────────────────────────
    def test_case_a_single_ready_selected(self):
        inputs = [
            _inp('MGC', 'SCALP', _rec('LONG READY', edge=75)),
        ]
        result = rank_candidates(inputs)
        self.assertIsNotNone(result)
        self.assertEqual(result['instrument'], 'MGC')
        self.assertEqual(result['mode'],       'SCALP')
        self.assertEqual(result['act'],        1)
        self.assertEqual(result['direction'],  'Long')

    # ── Case B: multiple qualifying — highest act then highest edge ────────────
    def test_case_b_ready_beats_wait(self):
        inputs = [
            _inp('MNQ', 'SCALP', _rec('WAIT',       edge=90)),
            _inp('MGC', 'SWING', _rec('SHORT READY', edge=60)),
        ]
        result = rank_candidates(inputs)
        # READY (act=1) must win even with lower edge
        self.assertEqual(result['instrument'], 'MGC')
        self.assertEqual(result['act'],        1)
        self.assertEqual(result['direction'],  'Short')

    def test_case_b_higher_edge_wins_same_act(self):
        inputs = [
            _inp('MGC', 'SCALP', _rec('LONG READY', edge=65)),
            _inp('MNQ', 'SWING', _rec('LONG READY', edge=82)),
        ]
        result = rank_candidates(inputs)
        self.assertEqual(result['instrument'], 'MNQ')
        self.assertEqual(result['edge'],       82)

    # ── Case C: tie — first in iteration order wins (stable) ─────────────────
    def test_case_c_tie_first_wins(self):
        inputs = [
            _inp('MGC', 'SCALP', _rec('LONG READY', edge=70)),
            _inp('MNQ', 'SCALP', _rec('LONG READY', edge=70)),
        ]
        result = rank_candidates(inputs)
        # MGC_SCALP is first → should win
        self.assertEqual(result['instrument'], 'MGC')
        self.assertEqual(result['mode'],       'SCALP')

    def test_case_c_tie_mode_order(self):
        inputs = [
            _inp('MGC', 'SCALP', _rec('LONG READY', edge=70)),
            _inp('MGC', 'SWING', _rec('LONG READY', edge=70)),
        ]
        result = rank_candidates(inputs)
        self.assertEqual(result['mode'], 'SCALP')  # SCALP first

    # ── Case D: only POTENTIAL (WAIT) candidates — best selected ─────────────
    def test_case_d_potential_only_best_edge(self):
        inputs = [
            _inp('MGC', 'SCALP', _rec('WAIT', edge=40)),
            _inp('MNQ', 'SWING', _rec('WAIT', edge=62)),
            _inp('MES', 'SCALP', _rec('WAIT', edge=35)),
        ]
        result = rank_candidates(inputs)
        # All WAIT — highest edge wins
        self.assertIsNotNone(result)
        self.assertEqual(result['instrument'], 'MNQ')
        self.assertEqual(result['act'],        0)
        self.assertEqual(result['edge'],       62)

    # ── Case E: all null records (all fetches failed) ─────────────────────────
    def test_case_e_all_blocked_null_records(self):
        inputs = [
            _inp('MGC', 'SCALP', None),
            _inp('MNQ', 'SWING', None),
        ]
        result = rank_candidates(inputs)
        self.assertIsNone(result)

    # ── Case F: no candidates (empty input) ──────────────────────────────────
    def test_case_f_empty_input(self):
        result = rank_candidates([])
        self.assertIsNone(result)

    # ── Case G: actionable vs non-actionable (edge doesn't matter) ────────────
    def test_case_g_actionable_wins_regardless_of_edge(self):
        inputs = [
            _inp('MNQ', 'SCALP', _rec('WAIT',       edge=110)),  # max edge, but WAIT
            _inp('MGC', 'SWING', _rec('LONG READY',  edge=1)),   # minimal edge, but READY
        ]
        result = rank_candidates(inputs)
        self.assertEqual(result['act'],        1)
        self.assertEqual(result['instrument'], 'MGC')

    # ── Case H: repeated calls return identical result (pure, no state) ────────
    def test_case_h_repeated_calls_stable(self):
        inputs = [
            _inp('MGC', 'SCALP', _rec('LONG READY', edge=75)),
            _inp('MNQ', 'SWING', _rec('WAIT',       edge=50)),
        ]
        r1 = rank_candidates(inputs)
        r2 = rank_candidates(inputs)
        self.assertEqual(r1['instrument'], r2['instrument'])
        self.assertEqual(r1['mode'],       r2['mode'])
        self.assertEqual(r1['act'],        r2['act'])
        self.assertEqual(r1['edge'],       r2['edge'])

    # ── Case I: same fixture → same result (deterministic) ────────────────────
    def test_case_i_deterministic_same_fixture(self):
        # Simulate both old-home and Main Brain receiving identical data
        shared_record = _rec('SHORT READY', edge=80)
        inputs_a = [_inp('MGC', 'SCALP', shared_record)]
        inputs_b = [_inp('MGC', 'SCALP', shared_record)]
        ra = rank_candidates(inputs_a)
        rb = rank_candidates(inputs_b)
        self.assertEqual(ra['instrument'], rb['instrument'])
        self.assertEqual(ra['direction'],  rb['direction'])
        self.assertEqual(ra['act'],        rb['act'])
        self.assertEqual(ra['edge'],       rb['edge'])

    # ── Case J: plan extraction matches canonical preview ─────────────────────
    def test_case_j_ready_plan_extracted(self):
        plan = {
            'trade_plan': True, 'entry_zone': '4100.00–4108.00',
            'stop_loss': '4090.00', 'target1': '4118.00',
            'rr': '1:1', 'risk_points': 10.0,
            'risk_dollars_per_contract': 100.0,
            'atr_pts': 5.0, 'stop_distance_ticks': 10,
        }
        record = _rec('LONG READY', edge=75, trade_plan=plan)
        extracted = get_plan_from_record(record, 'Long')
        self.assertIsNotNone(extracted)
        self.assertEqual(extracted['entry_zone'], '4100.00–4108.00')
        self.assertEqual(extracted['stop_loss'],  '4090.00')
        self.assertEqual(extracted['target1'],    '4118.00')
        self.assertEqual(extracted['rr'],         '1:1')

    def test_case_j_potential_plan_extracted(self):
        pp = {
            'trade_plan': True, 'entry_zone': '4200.00–4208.00',
            'stop_loss': '4190.00', 'target1': '4218.00',
            'rr': '1:1', 'risk_points': 10.0,
        }
        record = _rec('WAIT', edge=55, directions={
            'Long': {'potential_plan': pp}
        })
        extracted = get_plan_from_record(record, 'Long')
        self.assertIsNotNone(extracted)
        self.assertEqual(extracted['entry_zone'], '4200.00–4208.00')

    def test_case_j_no_plan_returns_none(self):
        record = _rec('WAIT', edge=40)  # no trade_plan, no directions
        extracted = get_plan_from_record(record, 'Long')
        self.assertIsNone(extracted)

    # ── Case K: active trade present — pure ranking, no side effects ──────────
    def test_case_k_active_trade_no_mutation(self):
        # The ranking function is pure — it does not mutate any input
        record = _rec('LONG READY', edge=75)
        original_record = dict(record)
        original_brain  = dict(record.get('brain', {}))
        inputs = [_inp('MGC', 'SCALP', record)]
        rank_candidates(inputs)
        # Confirm record was not mutated
        self.assertEqual(record.get('verdict'),    original_record.get('verdict'))
        self.assertEqual(record.get('edge_score'), original_record.get('edge_score'))

    # ── Case L: stale/unavailable data (None record) gracefully skipped ────────
    def test_case_l_stale_record_skipped(self):
        inputs = [
            _inp('MGC', 'SCALP', None),              # stale / fetch failed
            _inp('MNQ', 'SWING', _rec('WAIT', edge=45)),
        ]
        result = rank_candidates(inputs)
        # Should return MNQ even though MGC failed
        self.assertIsNotNone(result)
        self.assertEqual(result['instrument'], 'MNQ')

    # ── Edge case: mixed null and valid records ────────────────────────────────
    def test_mixed_null_and_valid(self):
        inputs = [
            _inp('MGC', 'SCALP', None),
            _inp('MNQ', 'SCALP', None),
            _inp('MES', 'SWING', _rec('SHORT READY', edge=55)),
            _inp('MYM', 'SWING', None),
        ]
        result = rank_candidates(inputs)
        self.assertEqual(result['instrument'], 'MES')
        self.assertEqual(result['act'],        1)

    # ── Regression: brain contract takes priority over top-level verdict ───────
    def test_brain_contract_wins_over_top_level(self):
        record = {
            'verdict':    'WAIT',            # stale top-level
            'edge_score': 40,
            'brain': {
                'decision': {'verdict': 'LONG READY', 'direction': 'Long'},
                'score':    {'value': 80},
            },
        }
        self.assertEqual(get_verdict_from_record(record), 'LONG READY')
        self.assertAlmostEqual(get_edge_from_record(record), 80.0)
        self.assertTrue(is_actionable_verdict(get_verdict_from_record(record)))

    # ── Regression: direction from verdict string when brain.direction missing ─
    def test_direction_fallback_to_verdict_string(self):
        record = {
            'verdict': 'SHORT READY',
            'edge_score': 70,
            'brain': {
                'decision': {'verdict': 'SHORT READY', 'direction': None},
                'score':    {'value': 70},
            },
        }
        self.assertEqual(get_direction_from_record(record), 'Short')

    # ── Regression: WAIT direction uses long_score vs short_score comparison ──
    def test_wait_direction_from_scores(self):
        record_long  = _rec('WAIT', long_score=60, short_score=40)
        record_short = _rec('WAIT', long_score=40, short_score=60)
        self.assertEqual(get_direction_from_record(record_long),  'Long')
        self.assertEqual(get_direction_from_record(record_short), 'Short')

    def test_wait_direction_equal_scores_defaults_long(self):
        record = _rec('WAIT', long_score=50, short_score=50)
        self.assertEqual(get_direction_from_record(record), 'Long')


if __name__ == '__main__':
    unittest.main(verbosity=2)
