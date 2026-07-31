"""test_cleanest_trade_parity.py — Direct legacy-versus-new algorithm parity tests.

Part 1 of Phase 7J.1 hardening: runs BOTH the exact legacy pickCleanestSetup()
comparison loop (from app.py lines 63699-63755) AND the new rankCandidates()
from cleanestTrade.ts against every shared fixture, then directly compares:
  - selected instrument
  - selected mode
  - act (actionable flag)
  - edge score
  - direction

DO NOT merely duplicate expected values in two unrelated tests.
Both implementations are exercised against the SAME fixture dict and their
outputs are compared with assertEqual / assertIs — never against a separate
hard-coded expected value.

Fixtures cover:
  ✓ READY and POTENTIAL candidates
  ✓ higher-edge WAIT vs lower-edge READY
  ✓ ties (same act + same edge)
  ✓ missing / null records
  ✓ malformed records (None values, missing keys)
  ✓ Long and Short directions
  ✓ SCALP and SWING modes
  ✓ all four instruments: MGC, MNQ, MES, MYM
"""

import math
import unittest

# ── Python port of the LEGACY pickCleanestSetup comparison loop ───────────────
#
# Source: app.py lines 63699-63755.
# Only the comparison / ranking logic is ported — URL fetching, DOM mutations,
# and the userPickedSetup / actionableOnly guards are UI concerns not present
# in unit-testable ranking.

ACTIONABLE_VERDICTS_LEGACY = {
    'LONG READY', 'SHORT READY',
    'LONG EARLY READY', 'SHORT EARLY READY',
}


def _legacy_js_is_actionable(v):
    """Exact port of jsIsActionable() from the dashboard JS."""
    return str(v or '') in ACTIONABLE_VERDICTS_LEGACY


def _legacy_js_ready_dir(v):
    """Exact port of jsReadyDir()."""
    if not _legacy_js_is_actionable(v):
        return None
    if 'LONG' in str(v):
        return 'Long'
    if 'SHORT' in str(v):
        return 'Short'
    return None


def _legacy_build_fallback(d):
    """Exact port of buildLegacyFallback() — only the fields used by ranking."""
    v  = (d.get('verdict') or 'WAIT') if d else 'WAIT'
    es = float(d.get('edge_score') or 0) if d else 0.0
    rd = _legacy_js_ready_dir(v)
    return {
        'decision': {'verdict': v, 'direction': rd},
        'score':    {'value': es},
    }


def _legacy_get_brain(d):
    """Exact port of getBrain(d) — d.brain if present, else buildLegacyFallback."""
    if d and d.get('brain'):
        return d['brain']
    return _legacy_build_fallback(d)


def _legacy_num(x):
    """Exact port of the num() helper used in the legacy selector."""
    try:
        n = float(x)
        return n if math.isfinite(n) else 0.0
    except (TypeError, ValueError):
        return 0.0


def legacy_pick_cleanest(candidates):
    """Port of the comparison loop inside pickCleanestSetup().

    candidates: list of {'instrument': str, 'mode': str, 'record': dict | None}
    Returns: {'instrument', 'mode', 'act', 'edge', 'direction'} | None
    """
    best = None
    for c in candidates:
        d = c.get('record')
        if d is None:
            continue
        bk = _legacy_get_brain(d)
        verdict = (bk.get('decision') or {}).get('verdict') or 'WAIT'
        edge    = _legacy_num((bk.get('score') or {}).get('value'))
        act     = 1 if _legacy_js_is_actionable(verdict) else 0
        cand = {
            'instrument': c['instrument'],
            'mode':       c['mode'],
            'act':        act,
            'edge':       edge,
            'record':     d,
        }
        if best is None:
            best = cand
            continue
        if cand['act'] != best['act']:
            if cand['act'] > best['act']:
                best = cand
        elif cand['edge'] > best['edge']:
            best = cand

    if best is None:
        return None

    bk   = _legacy_get_brain(best['record'])
    bdir = (bk.get('decision') or {}).get('direction')
    if not bdir:
        ss = _legacy_num(best['record'].get('short_score'))
        ls = _legacy_num(best['record'].get('long_score'))
        bdir = 'Short' if ss > ls else 'Long'

    return {
        'instrument': best['instrument'],
        'mode':       best['mode'],
        'act':        best['act'],
        'edge':       best['edge'],
        'direction':  bdir,
    }


# ── Python port of the NEW rankCandidates() from cleanestTrade.ts ────────────
# (identical to the version in test_cleanest_trade.py — reproduced here for
# isolation so the two implementations stay independent of each other's imports)

ACTIONABLE_VERDICTS_NEW = {
    'LONG READY', 'SHORT READY',
    'LONG EARLY READY', 'SHORT EARLY READY',
}


def _new_is_actionable(v):
    return str(v or '') in ACTIONABLE_VERDICTS_NEW


def _new_get_verdict(d):
    if not d:
        return 'WAIT'
    brain = d.get('brain') or {}
    decision = brain.get('decision') or {}
    v = decision.get('verdict')
    if v is not None:
        return str(v)
    return str(d.get('verdict') or 'WAIT')


def _new_get_edge(d):
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


def _new_get_direction(d):
    if not d:
        return 'Long'
    v = _new_get_verdict(d)
    if _new_is_actionable(v):
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


def new_rank_candidates(candidates):
    """Port of rankCandidates() from cleanestTrade.ts.

    candidates: list of {'instrument': str, 'mode': str, 'record': dict | None}
    Returns: {'instrument', 'mode', 'act', 'edge', 'direction'} | None
    """
    best = None
    for c in candidates:
        d = c.get('record')
        if d is None:
            continue
        v    = _new_get_verdict(d)
        act  = 1 if _new_is_actionable(v) else 0
        edge = _new_get_edge(d)
        cand = {
            'instrument': c['instrument'],
            'mode':       c['mode'],
            'act':        act,
            'edge':       edge,
            'direction':  _new_get_direction(d),
        }
        if best is None:
            best = cand
            continue
        if cand['act'] != best['act']:
            if cand['act'] > best['act']:
                best = cand
        elif cand['edge'] > best['edge']:
            best = cand

    return best


# ── Shared fixture builders ────────────────────────────────────────────────────

def _rec(verdict='WAIT', edge=50, long_score=None, short_score=None, brain_dir=None):
    """Build a minimal /status record with both legacy (top-level) and new (brain
    contract) fields so both algorithms read the same authoritative values."""
    is_act = verdict in ACTIONABLE_VERDICTS_LEGACY
    bdir = brain_dir or (
        ('Long' if 'LONG' in verdict else 'Short' if 'SHORT' in verdict else None)
        if is_act else None
    )
    rec = {
        'verdict':    verdict,
        'edge_score': edge,
        'brain': {
            'decision': {'verdict': verdict, 'direction': bdir},
            'score':    {'value': edge},
        },
    }
    if long_score  is not None: rec['long_score']  = long_score
    if short_score is not None: rec['short_score'] = short_score
    return rec


def _inp(instrument, mode, record):
    return {'instrument': instrument, 'mode': mode, 'record': record}


def _compare(legacy, new_):
    """Assert that both algorithms selected the same candidate — fail with detail."""
    if legacy is None and new_ is None:
        return  # both agree: no candidate
    assert legacy is not None and new_ is not None, (
        f'Null-agreement mismatch: legacy={legacy!r} new={new_!r}'
    )
    assert legacy['instrument'] == new_['instrument'], (
        f'Instrument mismatch: legacy={legacy["instrument"]} new={new_["instrument"]}'
    )
    assert legacy['mode'] == new_['mode'], (
        f'Mode mismatch: legacy={legacy["mode"]} new={new_["mode"]}'
    )
    assert legacy['act'] == new_['act'], (
        f'Act mismatch: legacy={legacy["act"]} new={new_["act"]}'
    )
    assert abs(legacy['edge'] - new_['edge']) < 0.001, (
        f'Edge mismatch: legacy={legacy["edge"]} new={new_["edge"]}'
    )
    assert legacy['direction'] == new_['direction'], (
        f'Direction mismatch: legacy={legacy["direction"]} new={new_["direction"]}'
    )


# ── Parity test cases ─────────────────────────────────────────────────────────

class TestLegacyVsNewParity(unittest.TestCase):

    # FX-1: Single READY — both must select it
    def test_fx1_single_ready(self):
        cands = [_inp('MGC', 'SCALP', _rec('LONG READY', edge=75))]
        _compare(legacy_pick_cleanest(cands), new_rank_candidates(cands))

    # FX-2: READY beats higher-edge WAIT
    def test_fx2_ready_beats_higher_edge_wait(self):
        cands = [
            _inp('MNQ', 'SCALP', _rec('WAIT',       edge=110)),
            _inp('MGC', 'SWING', _rec('SHORT READY', edge=1)),
        ]
        _compare(legacy_pick_cleanest(cands), new_rank_candidates(cands))

    # FX-3: Higher edge wins when both READY
    def test_fx3_higher_edge_wins_among_ready(self):
        cands = [
            _inp('MGC', 'SCALP', _rec('LONG READY',  edge=65)),
            _inp('MNQ', 'SWING', _rec('SHORT READY', edge=82)),
        ]
        _compare(legacy_pick_cleanest(cands), new_rank_candidates(cands))

    # FX-4: Higher edge wins when both WAIT
    def test_fx4_higher_edge_wins_among_wait(self):
        cands = [
            _inp('MES', 'SCALP', _rec('WAIT', edge=40)),
            _inp('MNQ', 'SWING', _rec('WAIT', edge=62)),
            _inp('MYM', 'SCALP', _rec('WAIT', edge=35)),
        ]
        _compare(legacy_pick_cleanest(cands), new_rank_candidates(cands))

    # FX-5: Tie (same act, same edge) — first in iteration order wins
    def test_fx5_tie_first_wins(self):
        cands = [
            _inp('MGC', 'SCALP', _rec('LONG READY', edge=70)),
            _inp('MNQ', 'SCALP', _rec('LONG READY', edge=70)),
        ]
        legacy = legacy_pick_cleanest(cands)
        new_   = new_rank_candidates(cands)
        _compare(legacy, new_)
        # Confirm both select MGC (first in list)
        self.assertEqual(legacy['instrument'], 'MGC')

    # FX-6: Tie on mode — SCALP before SWING
    def test_fx6_tie_scalp_before_swing(self):
        cands = [
            _inp('MGC', 'SCALP', _rec('LONG READY', edge=70)),
            _inp('MGC', 'SWING', _rec('LONG READY', edge=70)),
        ]
        _compare(legacy_pick_cleanest(cands), new_rank_candidates(cands))

    # FX-7: All null — both return None
    def test_fx7_all_null(self):
        cands = [
            _inp('MGC', 'SCALP', None),
            _inp('MNQ', 'SWING', None),
        ]
        _compare(legacy_pick_cleanest(cands), new_rank_candidates(cands))

    # FX-8: Empty list — both return None
    def test_fx8_empty_input(self):
        _compare(legacy_pick_cleanest([]), new_rank_candidates([]))

    # FX-9: Mixed null and valid — both skip null
    def test_fx9_mixed_null_and_valid(self):
        cands = [
            _inp('MGC', 'SCALP', None),
            _inp('MNQ', 'SCALP', None),
            _inp('MES', 'SWING', _rec('SHORT READY', edge=55)),
            _inp('MYM', 'SWING', None),
        ]
        _compare(legacy_pick_cleanest(cands), new_rank_candidates(cands))

    # FX-10: Long direction (actionable)
    def test_fx10_long_direction_actionable(self):
        cands = [_inp('MGC', 'SCALP', _rec('LONG READY', edge=75))]
        legacy = legacy_pick_cleanest(cands)
        new_   = new_rank_candidates(cands)
        _compare(legacy, new_)
        self.assertEqual(legacy['direction'], 'Long')

    # FX-11: Short direction (actionable)
    def test_fx11_short_direction_actionable(self):
        cands = [_inp('MNQ', 'SWING', _rec('SHORT READY', edge=80))]
        legacy = legacy_pick_cleanest(cands)
        new_   = new_rank_candidates(cands)
        _compare(legacy, new_)
        self.assertEqual(legacy['direction'], 'Short')

    # FX-12: WAIT direction from short_score > long_score
    def test_fx12_wait_direction_from_scores_short_wins(self):
        cands = [_inp('MES', 'SCALP', _rec('WAIT', edge=50,
                                           long_score=40, short_score=60))]
        legacy = legacy_pick_cleanest(cands)
        new_   = new_rank_candidates(cands)
        _compare(legacy, new_)
        self.assertEqual(legacy['direction'], 'Short')

    # FX-13: WAIT direction from long_score > short_score
    def test_fx13_wait_direction_from_scores_long_wins(self):
        cands = [_inp('MYM', 'SWING', _rec('WAIT', edge=50,
                                           long_score=70, short_score=30))]
        legacy = legacy_pick_cleanest(cands)
        new_   = new_rank_candidates(cands)
        _compare(legacy, new_)
        self.assertEqual(legacy['direction'], 'Long')

    # FX-14: EARLY READY is actionable (act=1)
    def test_fx14_early_ready_is_actionable(self):
        cands = [
            _inp('MNQ', 'SCALP', _rec('WAIT',             edge=90)),
            _inp('MGC', 'SWING', _rec('LONG EARLY READY', edge=50)),
        ]
        legacy = legacy_pick_cleanest(cands)
        new_   = new_rank_candidates(cands)
        _compare(legacy, new_)
        self.assertEqual(legacy['act'], 1)
        self.assertEqual(legacy['instrument'], 'MGC')

    # FX-15: SHORT EARLY READY direction
    def test_fx15_short_early_ready_direction(self):
        cands = [_inp('MES', 'SWING', _rec('SHORT EARLY READY', edge=60))]
        legacy = legacy_pick_cleanest(cands)
        new_   = new_rank_candidates(cands)
        _compare(legacy, new_)
        self.assertEqual(legacy['direction'], 'Short')

    # FX-16: All four instruments present — same winner
    def test_fx16_all_four_instruments(self):
        cands = [
            _inp('MGC', 'SCALP', _rec('WAIT',       edge=45)),
            _inp('MNQ', 'SCALP', _rec('WAIT',       edge=50)),
            _inp('MES', 'SWING', _rec('LONG READY', edge=70)),
            _inp('MYM', 'SWING', _rec('WAIT',       edge=80)),
        ]
        _compare(legacy_pick_cleanest(cands), new_rank_candidates(cands))

    # FX-17: SCALP mode candidates dominate over SWING
    def test_fx17_scalp_and_swing_modes(self):
        cands = [
            _inp('MGC', 'SCALP', _rec('LONG READY', edge=65)),
            _inp('MGC', 'SWING', _rec('WAIT',       edge=90)),
        ]
        legacy = legacy_pick_cleanest(cands)
        new_   = new_rank_candidates(cands)
        _compare(legacy, new_)
        self.assertEqual(legacy['mode'], 'SCALP')  # READY wins regardless of mode

    # FX-18: Record without 'brain' key — legacy falls back to top-level fields
    def test_fx18_malformed_no_brain_key(self):
        """Record lacking a brain contract; both algorithms must handle gracefully."""
        d = {'verdict': 'LONG READY', 'edge_score': 70}
        cands = [_inp('MGC', 'SCALP', d)]
        legacy = legacy_pick_cleanest(cands)
        new_   = new_rank_candidates(cands)
        _compare(legacy, new_)
        self.assertEqual(legacy['act'], 1)

    # FX-19: Record with None values in score fields
    def test_fx19_malformed_none_score_values(self):
        d = {
            'verdict': 'WAIT',
            'edge_score': None,
            'brain': {
                'decision': {'verdict': 'WAIT', 'direction': None},
                'score':    {'value': None},
            },
        }
        cands = [_inp('MNQ', 'SCALP', d)]
        legacy = legacy_pick_cleanest(cands)
        new_   = new_rank_candidates(cands)
        _compare(legacy, new_)
        self.assertAlmostEqual(legacy['edge'], 0.0)

    # FX-20: brain.decision.verdict overrides top-level verdict
    def test_fx20_brain_verdict_overrides_top_level(self):
        d = {
            'verdict':    'WAIT',       # stale top-level
            'edge_score': 40,
            'brain': {
                'decision': {'verdict': 'LONG READY', 'direction': 'Long'},
                'score':    {'value': 80},
            },
        }
        cands = [_inp('MES', 'SWING', d)]
        legacy = legacy_pick_cleanest(cands)
        new_   = new_rank_candidates(cands)
        _compare(legacy, new_)
        self.assertEqual(legacy['act'], 1)
        self.assertAlmostEqual(legacy['edge'], 80.0)

    # FX-21: brain.score.value overrides top-level edge_score
    def test_fx21_brain_edge_overrides_top_level(self):
        d = {
            'verdict':    'WAIT',
            'edge_score': 30,     # lower
            'brain': {
                'decision': {'verdict': 'WAIT', 'direction': None},
                'score':    {'value': 85},  # higher — both impls must use this
            },
        }
        cands = [
            _inp('MGC', 'SCALP', d),
            _inp('MNQ', 'SWING', _rec('WAIT', edge=60)),  # lower brain score
        ]
        legacy = legacy_pick_cleanest(cands)
        new_   = new_rank_candidates(cands)
        _compare(legacy, new_)
        self.assertEqual(legacy['instrument'], 'MGC')

    # FX-22: Repeated calls — both algorithms are pure/stateless
    def test_fx22_repeated_calls_stable(self):
        cands = [
            _inp('MGC', 'SCALP', _rec('LONG READY', edge=75)),
            _inp('MNQ', 'SWING', _rec('WAIT',       edge=50)),
        ]
        legacy1 = legacy_pick_cleanest(cands)
        legacy2 = legacy_pick_cleanest(cands)
        new1    = new_rank_candidates(cands)
        new2    = new_rank_candidates(cands)
        _compare(legacy1, new1)
        _compare(legacy2, new2)
        self.assertEqual(legacy1['instrument'], legacy2['instrument'])
        self.assertEqual(new1['instrument'],    new2['instrument'])

    # FX-23: Full 8-combination scan fixture (all 4 instruments × 2 modes)
    def test_fx23_full_eight_combination_scan(self):
        """Simulates a complete 8-request scan with one winner."""
        cands = [
            _inp('MGC', 'SCALP', _rec('WAIT',        edge=40)),
            _inp('MGC', 'SWING', _rec('WAIT',        edge=35)),
            _inp('MNQ', 'SCALP', _rec('WAIT',        edge=55)),
            _inp('MNQ', 'SWING', _rec('LONG READY',  edge=72)),  # ← winner
            _inp('MES', 'SCALP', _rec('WAIT',        edge=30)),
            _inp('MES', 'SWING', _rec('WAIT',        edge=48)),
            _inp('MYM', 'SCALP', _rec('WAIT',        edge=60)),
            _inp('MYM', 'SWING', _rec('WAIT',        edge=65)),
        ]
        _compare(legacy_pick_cleanest(cands), new_rank_candidates(cands))

    # FX-24: Full scan with some null (partial failure)
    def test_fx24_partial_scan_with_nulls(self):
        cands = [
            _inp('MGC', 'SCALP', None),                          # failed
            _inp('MGC', 'SWING', _rec('WAIT',       edge=50)),
            _inp('MNQ', 'SCALP', _rec('SHORT READY', edge=68)),  # ← winner
            _inp('MNQ', 'SWING', _rec('WAIT',       edge=40)),
            _inp('MES', 'SCALP', None),                          # failed
            _inp('MES', 'SWING', _rec('WAIT',       edge=30)),
            _inp('MYM', 'SCALP', _rec('WAIT',       edge=45)),
            _inp('MYM', 'SWING', None),                          # failed
        ]
        _compare(legacy_pick_cleanest(cands), new_rank_candidates(cands))

    # FX-25: Worst-case — all WAIT, exact same edge
    def test_fx25_all_wait_same_edge_stable_order(self):
        edge = 55
        cands = [
            _inp('MGC', 'SCALP', _rec('WAIT', edge=edge)),
            _inp('MGC', 'SWING', _rec('WAIT', edge=edge)),
            _inp('MNQ', 'SCALP', _rec('WAIT', edge=edge)),
            _inp('MNQ', 'SWING', _rec('WAIT', edge=edge)),
            _inp('MES', 'SCALP', _rec('WAIT', edge=edge)),
            _inp('MES', 'SWING', _rec('WAIT', edge=edge)),
            _inp('MYM', 'SCALP', _rec('WAIT', edge=edge)),
            _inp('MYM', 'SWING', _rec('WAIT', edge=edge)),
        ]
        legacy = legacy_pick_cleanest(cands)
        new_   = new_rank_candidates(cands)
        _compare(legacy, new_)
        # First candidate in list wins tie: MGC SCALP
        self.assertEqual(legacy['instrument'], 'MGC')
        self.assertEqual(legacy['mode'],       'SCALP')


if __name__ == '__main__':
    unittest.main(verbosity=2)
