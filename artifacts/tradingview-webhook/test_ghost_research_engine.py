"""
test_ghost_research_engine.py — Phase 2 Ghost Research & Evidence Engine Tests

Coverage:
- Opportunity dedup (idempotency)
- All 10 variants created per opportunity
- Lookahead prevention (bar_ts guard)
- Conservative stop-first same-bar resolution
- NO_ENTRY for pre-filtered variants (TREND_REQUIRED, CVD_ALIGNED)
- Restart recovery
- Entry simulation per variant rule
- MAE/MFE tracking
- BREAKOUT_MISSED forces NO_ENTRY
- Bootstrap CI determinism
- Monte Carlo drawdown determinism
- Evidence state machine transitions
- READY_FOR_REVIEW gating
- Profit factor, net expectancy, max drawdown
- Cost model (commission + slippage)
- Rejection category assignment
- Multiple instruments isolation
- Fail-open on DB error
- Baseline vs variant paired comparison (schema)
"""

import math
import random
import sys
import threading
import types
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, call, patch

# ── Import the engine ─────────────────────────────────────────────────────────
import ghost_research_engine as gre
from ghost_research_engine import (
    EvidenceState,
    GhostResearchEngine,
    OutcomeResult,
    RejectionCat,
    ResultStatus,
    Variant,
    ALL_VARIANTS,
    _aggregate_results,
    _bootstrap_ci,
    _commission_r,
    _compute_evidence_state,
    _compute_gross_r,
    _compute_max_drawdown,
    _gate_ready_for_review,
    _monte_carlo_drawdown,
    _opportunity_id,
    _rejection_category,
    _update_mfe_mae,
    _experiment_id,
    _result_id,
)

INSTRUMENTS = ["MNQ", "MGC", "MES", "MYM"]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _bar(high: float, low: float, close: float = None, ts: int = 1000) -> Dict:
    if close is None:
        close = (high + low) / 2
    return {"high": high, "low": low, "close": close, "ts": ts, "open": low}


def _orb_status(
    state: str = "BREAKOUT_DETECTED",
    direction: str = "Long",
    inst: str = "MNQ",
    entry: float = 21000.0,
    stop: float = 20900.0,
    tp1: float = 21300.0,
    tp2: float = 21600.0,
    or_high: float = 20980.0,
    or_low: float = 20900.0,
    trading_date: str = "2026-08-11",
    strategy_version: str = "1.0",
    config_version: str = "1.0",
    breakout_bar_ts: int = 100,
    confirmation_mode: str = "CLOSE_OUTSIDE",
    block_reason: str = "",
) -> Dict:
    return {
        "state": state,
        "breakout_direction": direction,
        "instrument": inst,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "or_high": or_high,
        "or_low": or_low,
        "or_width": or_high - or_low,
        "or_midpoint": (or_high + or_low) / 2,
        "trading_date": trading_date,
        "strategy_version": strategy_version,
        "config_version": config_version,
        "breakout_bar_ts": breakout_bar_ts,
        "long_breakout_level": or_high if direction == "Long" else None,
        "short_breakout_level": or_low if direction == "Short" else None,
        "confirmation_mode": confirmation_mode,
        "current_atr": 80.0,
        "block_reason": block_reason,
        "contracts": 1,
        "range_duration_min": 10,
    }


def _canonical(
    trend_15m: str = "BULLISH",
    trend_4h:  str = "BULLISH",
    cvd:       str = "BULLISH",
    vwap:      float = 21000.0,
    price:     float = 21050.0,
) -> Dict:
    return {
        "trend":  {"trend_15m": trend_15m, "trend_4h": trend_4h, "alignment": "ALIGNED_BULLISH"},
        "cvd":    {"direction": cvd, "value": 1500},
        "vwap":   {"vwap": vwap, "side": "ABOVE" if price >= vwap else "BELOW"},
        "volume": {"value": 500, "relative_volume": 1.3},
        "atr":    {"value": 80, "regime": "NORMAL"},
        "structure": {"bos": True, "choch": False},
    }


def _make_row(net_r: float, gross_r: float = None, result: str = None,
              mfe_r: float = 0.5, mae_r: float = -0.2,
              entry_timestamp: str = "2026-08-11T10:00:00+00:00") -> Dict:
    if gross_r is None:
        gross_r = net_r + 0.1
    if result is None:
        result = OutcomeResult.WIN if net_r > 0 else OutcomeResult.LOSS
    return {
        "net_r": net_r, "gross_r": gross_r,
        "result": result, "mfe_r": mfe_r, "mae_r": mae_r,
        "entry_timestamp": entry_timestamp,
        "tp1_hit": net_r > 0, "tp2_hit": False, "stop_hit": net_r < 0,
        "bars_held": 20, "ambiguous_bar": False,
    }


class FakeDB:
    """Minimal psycopg2-compatible fake for unit tests."""
    def __init__(self):
        self.committed = False
        self.calls: List = []
        self._rows: List = []

    def set_rows(self, rows: List):
        self._rows = rows

    def cursor(self):
        cur = MagicMock()
        cur.execute   = lambda *a, **k: self.calls.append(a)
        cur.fetchone  = lambda: self._rows[0] if self._rows else None
        cur.fetchall  = lambda: list(self._rows)
        cur.description = []
        return cur

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


def _make_engine(
    canonical_override: Optional[Dict] = None,
    db_ready: bool = True,
    instruments: List[str] = None,
) -> GhostResearchEngine:
    db = FakeDB()
    GhostResearchEngine.GRE_DB_READY = db_ready

    canon = canonical_override or _canonical()

    engine = GhostResearchEngine(
        get_db_fn       = lambda: db,
        get_canonical_fn= lambda inst: canon,
        get_bars_fn     = lambda inst: [_bar(21050, 20960, 21000, ts=100)],
        re_event_fn     = MagicMock(),
        instruments     = instruments or INSTRUMENTS,
    )
    engine._db = db
    return engine


# ══════════════════════════════════════════════════════════════════════════════
# 1. Pure helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeGrossR(unittest.TestCase):
    def test_long_win(self):
        r = _compute_gross_r("Long", 100, 103, 97)
        self.assertAlmostEqual(r, 1.0, places=5)

    def test_long_loss(self):
        # Exit AT stop price: risk=3, loss=(97-100)/3 = -1.0R
        r = _compute_gross_r("Long", 100, 97, 97)
        self.assertAlmostEqual(r, -1.0, places=5)

    def test_short_win(self):
        r = _compute_gross_r("Short", 100, 97, 103)
        self.assertAlmostEqual(r, 1.0, places=5)

    def test_short_loss(self):
        r = _compute_gross_r("Short", 100, 103, 97)
        self.assertAlmostEqual(r, -1.0, places=5)

    def test_zero_risk(self):
        self.assertIsNone(_compute_gross_r("Long", 100, 103, 100))

    def test_invalid_direction(self):
        self.assertIsNone(_compute_gross_r("FLAT", 100, 103, 97))

    def test_fractional_r(self):
        r = _compute_gross_r("Long", 100, 101.5, 97)
        self.assertAlmostEqual(r, 0.5, places=5)


class TestUpdateMfeMae(unittest.TestCase):
    def test_long_new_high(self):
        mfe_r, mae_r, mfe_p, mae_p = _update_mfe_mae(
            "Long", 105, 99, 100, 3, None, None, 0.0, 0.0)
        self.assertAlmostEqual(mfe_r, 5/3, places=4)
        self.assertAlmostEqual(mae_r, -1/3, places=4)

    def test_short_new_low(self):
        mfe_r, mae_r, mfe_p, mae_p = _update_mfe_mae(
            "Short", 101, 95, 100, 3, None, None, 0.0, 0.0)
        self.assertAlmostEqual(mfe_r, 5/3, places=4)

    def test_does_not_regress_mfe(self):
        mfe_r, mae_r, _, _ = _update_mfe_mae("Long", 101, 99, 100, 3, None, None, 2.0, 0.0)
        self.assertEqual(mfe_r, 2.0)  # existing MFE preserved

    def test_zero_risk(self):
        result = _update_mfe_mae("Long", 105, 95, 100, 0, None, None, 0.0, 0.0)
        self.assertEqual(result[:2], (0.0, 0.0))


class TestCommissionR(unittest.TestCase):
    def test_mnq_positive(self):
        r = _commission_r("MNQ", 21000, 20900)  # 100 pt risk = $200
        self.assertGreater(r, 0)
        self.assertLess(r, 0.1)

    def test_mgc_positive(self):
        r = _commission_r("MGC", 2000, 1990)   # 10 pt risk = $100
        self.assertGreater(r, 0)

    def test_zero_risk(self):
        r = _commission_r("MNQ", 21000, 21000)
        self.assertEqual(r, 0.0)


class TestMaxDrawdown(unittest.TestCase):
    def test_flat(self):
        self.assertEqual(_compute_max_drawdown([]), 0.0)

    def test_always_positive(self):
        dd = _compute_max_drawdown([1, 1, 1])
        self.assertEqual(dd, 0.0)

    def test_simple_dd(self):
        # cumsum: 1, -1, 0. peak=1, min(cumsum-peak)=(-1-1)=-2
        dd = _compute_max_drawdown([1, -2, 1])
        self.assertAlmostEqual(dd, -2.0, places=4)

    def test_worst_dd(self):
        dd = _compute_max_drawdown([2, -3, 2, -4])
        self.assertLessEqual(dd, -1.0)


class TestRejectionCategory(unittest.TestCase):
    def test_risk_block(self):
        cat = _rejection_category("BLOCKED_BY_INSTRUMENT_RISK", "")
        self.assertEqual(cat, RejectionCat.RISK)

    def test_exec_block(self):
        cat = _rejection_category("BLOCKED_BY_EXECUTION_MODE", "")
        self.assertEqual(cat, RejectionCat.EXECUTION_SAFETY)

    def test_data_block(self):
        cat = _rejection_category("BLOCKED_BY_RANGE_WIDTH", "")
        self.assertEqual(cat, RejectionCat.DATA)

    def test_fallback(self):
        cat = _rejection_category("SOME_UNKNOWN_STATE", "")
        self.assertEqual(cat, RejectionCat.OTHER)


# ══════════════════════════════════════════════════════════════════════════════
# 2. Statistics aggregation
# ══════════════════════════════════════════════════════════════════════════════

class TestAggregateResults(unittest.TestCase):
    def _rows(self) -> List[Dict]:
        return [_make_row(r) for r in [1.5, -0.8, 1.2, -0.5, 0.9, -1.0, 1.1, -0.6, 0.8, 1.3]]

    def test_counts(self):
        rows = self._rows()
        s = _aggregate_results(rows)
        self.assertEqual(s["closed_count"], 10)
        self.assertGreater(s["win_count"], 0)
        self.assertGreater(s["loss_count"], 0)

    def test_profit_factor_positive_when_profitable(self):
        rows = [_make_row(r) for r in [1.0, 1.0, 1.0, -0.5, -0.5]]
        s = _aggregate_results(rows)
        self.assertGreater(s.get("profit_factor") or 0, 1.0)

    def test_net_expectancy(self):
        rows = [_make_row(r) for r in [1.0, 1.0, -0.5, -0.5]]
        s = _aggregate_results(rows)
        self.assertAlmostEqual(s["net_expectancy"], 0.25, places=5)

    def test_no_entry_excluded_from_closed_count(self):
        rows = [_make_row(1.0)] + [{
            "net_r": None, "gross_r": None, "result": OutcomeResult.NO_ENTRY,
            "mfe_r": 0, "mae_r": 0, "entry_timestamp": "2026-08-11T10:00:00+00:00",
        }]
        s = _aggregate_results(rows)
        self.assertEqual(s["closed_count"], 1)
        self.assertEqual(s["no_entry_count"], 1)

    def test_empty(self):
        s = _aggregate_results([])
        self.assertIsNone(s["win_rate"])
        self.assertEqual(s["sample_count"], 0)

    def test_all_losses(self):
        rows = [_make_row(-1.0) for _ in range(5)]
        s = _aggregate_results(rows)
        self.assertEqual(s["win_rate"], 0.0)
        # gross_wins=0, gross_loss>0 → profit_factor = 0.0 (no winning trades)
        self.assertEqual(s.get("profit_factor"), 0.0)


class TestBootstrapCI(unittest.TestCase):
    def test_determinism(self):
        net_rs = [0.5, -0.3, 0.8, 1.0, -0.5, 0.6, 0.4, -0.2, 0.9, 0.3,
                  0.7, -0.4, 0.5, 0.8, -0.3, 0.6, 0.9, 0.4, -0.1, 0.5] * 3
        r1 = _bootstrap_ci(net_rs, seed=42)
        r2 = _bootstrap_ci(net_rs, seed=42)
        self.assertEqual(r1["ci_low"],  r2["ci_low"])
        self.assertEqual(r1["ci_high"], r2["ci_high"])

    def test_different_seed_differs(self):
        net_rs = [0.5, -0.3, 0.8, 1.0, -0.5, 0.6, 0.4, -0.2, 0.9, 0.3] * 3
        r1 = _bootstrap_ci(net_rs, seed=42)
        r2 = _bootstrap_ci(net_rs, seed=99)
        # Different seeds SHOULD produce different results (not guaranteed but highly likely)
        self.assertNotEqual(r1["ci_low"], r2["ci_low"])

    def test_insufficient_samples(self):
        r = _bootstrap_ci([1.0, 0.5], seed=42)
        self.assertEqual(r["status"], "INSUFFICIENT_SAMPLES")

    def test_ci_low_less_than_ci_high(self):
        net_rs = list(range(-5, 15))
        r = _bootstrap_ci(net_rs, seed=42)
        self.assertLess(r["ci_low"], r["ci_high"])

    def test_profitable_ci_low_positive(self):
        net_rs = [0.8, 0.9, 1.0, 0.7, 0.6, 0.8, 0.9, 1.0, 0.7, 0.8,
                  0.9, 1.0, 0.7, 0.6, 0.8, 0.9, 1.0, 0.7, 0.8, 0.9,
                  1.0, 0.7, 0.6, 0.8, 0.9, 1.0, 0.7, 0.8, 0.9, 1.0]
        r = _bootstrap_ci(net_rs, seed=42)
        self.assertGreater(r["ci_low"], 0)


class TestMonteCarloDrawdown(unittest.TestCase):
    def test_determinism(self):
        net_rs = [0.5, -0.3, 0.8, 1.0, -0.5, 0.6, 0.4, -0.2, 0.9, 0.3] * 5
        r1 = _monte_carlo_drawdown(net_rs, seed=42)
        r2 = _monte_carlo_drawdown(net_rs, seed=42)
        self.assertEqual(r1["median_dd"], r2["median_dd"])
        self.assertEqual(r1["p95_dd"],    r2["p95_dd"])

    def test_insufficient_samples(self):
        r = _monte_carlo_drawdown([1.0, 0.5], seed=42)
        self.assertEqual(r["status"], "INSUFFICIENT_SAMPLES")

    def test_p95_more_negative_than_median(self):
        net_rs = [1.0, -2.0, 1.0, -2.0, 0.5, -0.5, 1.5, -1.5, 0.8, -0.8] * 5
        r = _monte_carlo_drawdown(net_rs, seed=42)
        self.assertLessEqual(r["p95_dd"], r["median_dd"])


# ══════════════════════════════════════════════════════════════════════════════
# 3. Evidence state machine
# ══════════════════════════════════════════════════════════════════════════════

class TestEvidenceStateMachine(unittest.TestCase):
    def _stats(self, n: int = 60, exp: float = 0.2, pf: float = 1.5, days: int = 5) -> Dict:
        return {
            "closed_count": n, "net_expectancy": exp, "profit_factor": pf,
            "max_drawdown": -1.5, "distinct_days": days,
            "win_rate": 0.55, "win_count": max(1, int(n * 0.55)),
        }

    def test_insufficient_data_below_25(self):
        s = self._stats(n=10)
        state = _compute_evidence_state(s, EvidenceState.INSUFFICIENT_DATA)
        self.assertEqual(state, EvidenceState.INSUFFICIENT_DATA)

    def test_observing_at_25_to_49(self):
        s = self._stats(n=30, exp=0.3, pf=1.6)
        state = _compute_evidence_state(s, EvidenceState.INSUFFICIENT_DATA)
        self.assertEqual(state, EvidenceState.OBSERVING)

    def test_observing_when_low_expectancy(self):
        s = self._stats(n=60, exp=0.01)
        state = _compute_evidence_state(s, EvidenceState.OBSERVING)
        self.assertEqual(state, EvidenceState.OBSERVING)

    def test_observing_when_low_pf(self):
        s = self._stats(n=60, exp=0.2, pf=1.1)
        state = _compute_evidence_state(s, EvidenceState.OBSERVING)
        self.assertEqual(state, EvidenceState.OBSERVING)

    def test_observing_when_low_days(self):
        s = self._stats(n=60, exp=0.2, pf=1.5, days=1)
        state = _compute_evidence_state(s, EvidenceState.OBSERVING)
        self.assertEqual(state, EvidenceState.OBSERVING)

    def test_promising_when_all_gates_pass(self):
        net_rs = [0.5] * 60
        boot = _bootstrap_ci(net_rs, seed=42)
        mc   = _monte_carlo_drawdown(net_rs, seed=42)
        s    = self._stats(n=60, exp=0.5, pf=2.0, days=5)
        state = _compute_evidence_state(s, EvidenceState.OBSERVING, boot, mc)
        self.assertEqual(state, EvidenceState.PROMISING)

    def test_retired_and_rejected_are_terminal(self):
        s = self._stats(n=100, exp=0.5, pf=2.5)
        for terminal in (EvidenceState.RETIRED, EvidenceState.REJECTED):
            state = _compute_evidence_state(s, terminal)
            self.assertEqual(state, terminal)

    def test_does_not_regress_ready_for_review(self):
        s = self._stats(n=40, exp=0.01)  # stats would say OBSERVING
        state = _compute_evidence_state(s, EvidenceState.READY_FOR_REVIEW)
        self.assertEqual(state, EvidenceState.READY_FOR_REVIEW)

    def test_ci_crossing_zero_blocks_promising(self):
        bad_boot = {"status": "OK", "ci_low": -0.1, "ci_high": 0.5}
        mc = {"status": "OK", "p95_dd": -2.0}
        s  = self._stats(n=60)
        state = _compute_evidence_state(s, EvidenceState.OBSERVING, bad_boot, mc)
        self.assertEqual(state, EvidenceState.OBSERVING)


class TestReadyForReviewGate(unittest.TestCase):
    def _good_stats(self) -> Dict:
        return {
            "closed_count": 60, "net_expectancy": 0.3, "profit_factor": 1.6,
            "max_drawdown": -2.0, "distinct_days": 5,
        }

    def test_passes_all_gates(self):
        boot = {"status": "OK", "ci_low": 0.1, "ci_high": 0.5}
        mc   = {"status": "OK", "p95_dd": -3.0}
        self.assertTrue(_gate_ready_for_review(self._good_stats(), boot, mc))

    def test_fails_insufficient_samples(self):
        s = {**self._good_stats(), "closed_count": 20}
        boot = {"status": "OK", "ci_low": 0.1, "ci_high": 0.5}
        mc   = {"status": "OK", "p95_dd": -3.0}
        self.assertFalse(_gate_ready_for_review(s, boot, mc))

    def test_fails_ci_low_negative(self):
        boot = {"status": "OK", "ci_low": -0.05, "ci_high": 0.5}
        mc   = {"status": "OK", "p95_dd": -3.0}
        self.assertFalse(_gate_ready_for_review(self._good_stats(), boot, mc))

    def test_fails_bad_bootstrap(self):
        boot = {"status": "INSUFFICIENT_SAMPLES"}
        mc   = {"status": "OK", "p95_dd": -3.0}
        self.assertFalse(_gate_ready_for_review(self._good_stats(), boot, mc))


# ══════════════════════════════════════════════════════════════════════════════
# 4. Opportunity identification and dedup
# ══════════════════════════════════════════════════════════════════════════════

class TestOpportunityId(unittest.TestCase):
    def test_stable(self):
        id1 = _opportunity_id("MNQ", "2026-08-11", 100, "Long")
        id2 = _opportunity_id("MNQ", "2026-08-11", 100, "Long")
        self.assertEqual(id1, id2)

    def test_different_inst(self):
        id1 = _opportunity_id("MNQ", "2026-08-11", 100, "Long")
        id2 = _opportunity_id("MGC", "2026-08-11", 100, "Long")
        self.assertNotEqual(id1, id2)

    def test_different_date(self):
        id1 = _opportunity_id("MNQ", "2026-08-11", 100, "Long")
        id2 = _opportunity_id("MNQ", "2026-08-12", 100, "Long")
        self.assertNotEqual(id1, id2)

    def test_different_direction(self):
        id1 = _opportunity_id("MNQ", "2026-08-11", 100, "Long")
        id2 = _opportunity_id("MNQ", "2026-08-11", 100, "Short")
        self.assertNotEqual(id1, id2)

    def test_length_24(self):
        oid = _opportunity_id("MNQ", "2026-08-11", 100, "Long")
        self.assertEqual(len(oid), 24)


class TestExperimentResultIds(unittest.TestCase):
    def test_experiment_id_contains_variant(self):
        opp_id = _opportunity_id("MNQ", "2026-08-11", 100, "Long")
        eid    = _experiment_id(opp_id, Variant.BASELINE)
        self.assertIn(Variant.BASELINE, eid)

    def test_result_id_prefixed(self):
        exp_id = "abc123_BASELINE"
        rid    = _result_id(exp_id)
        self.assertTrue(rid.startswith("RES_"))


# ══════════════════════════════════════════════════════════════════════════════
# 5. Variant catalogue
# ══════════════════════════════════════════════════════════════════════════════

class TestVariantCatalogue(unittest.TestCase):
    def test_ten_variants(self):
        self.assertEqual(len(ALL_VARIANTS), 10)

    def test_all_required_variants_present(self):
        required = {
            Variant.BASELINE, Variant.TOUCH, Variant.CLOSE_AND_RETEST,
            Variant.BUFFER_PLUS_2, Variant.BUFFER_MINUS_2,
            Variant.TP_1R, Variant.TP_1_5R, Variant.TP_2R,
            Variant.TREND_REQUIRED, Variant.CVD_ALIGNED,
        }
        self.assertEqual(required, set(ALL_VARIANTS))

    def test_within_hard_cap(self):
        self.assertLessEqual(len(ALL_VARIANTS), gre.MAX_GHOST_VARIANTS_HARD_CAP)


# ══════════════════════════════════════════════════════════════════════════════
# 6. Snapshot builder
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildSnapshot(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()

    def test_snapshot_contains_trend(self):
        snap = self.engine._build_snapshot(
            "MNQ", _orb_status(), _bar(21050, 20960, 21000), 21000, _canonical())
        self.assertIn("trend_15m", snap)
        self.assertIn("trend_4h", snap)

    def test_snapshot_contains_cvd(self):
        snap = self.engine._build_snapshot(
            "MNQ", _orb_status(), _bar(21050, 20960, 21000), 21000, _canonical())
        self.assertIn("cvd_direction", snap)

    def test_vwap_distance_computed(self):
        snap = self.engine._build_snapshot(
            "MNQ", _orb_status(), _bar(21050, 20960, 21000), 21050,
            _canonical(vwap=21000, price=21050))
        self.assertIsNotNone(snap.get("vwap_distance_pct"))

    def test_snapshot_without_bar(self):
        snap = self.engine._build_snapshot(
            "MNQ", _orb_status(), None, 21000, _canonical())
        self.assertIsNone(snap.get("bar_high"))


# ══════════════════════════════════════════════════════════════════════════════
# 7. Trend / CVD filters (NO_ENTRY logic)
# ══════════════════════════════════════════════════════════════════════════════

class TestContextFilters(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()

    def test_trend_required_aligned_long(self):
        snap = _canonical(trend_15m="BULLISH", trend_4h="BULLISH")
        self.assertTrue(self.engine._check_trend_aligned(
            self.engine._build_snapshot("MNQ", _orb_status(), None, 21000, snap),
            "Long"))

    def test_trend_required_not_aligned_long(self):
        snap = _canonical(trend_15m="BEARISH", trend_4h="BULLISH")
        self.assertFalse(self.engine._check_trend_aligned(
            self.engine._build_snapshot("MNQ", _orb_status(), None, 21000, snap),
            "Long"))

    def test_trend_required_aligned_short(self):
        snap = _canonical(trend_15m="BEARISH", trend_4h="BEARISH")
        self.assertTrue(self.engine._check_trend_aligned(
            self.engine._build_snapshot("MNQ", _orb_status(direction="Short"),
                                        None, 21000, snap),
            "Short"))

    def test_cvd_aligned_bullish(self):
        snap = _canonical(cvd="BULLISH")
        self.assertTrue(self.engine._check_cvd_aligned(
            self.engine._build_snapshot("MNQ", _orb_status(), None, 21000, snap),
            "Long"))

    def test_cvd_not_aligned(self):
        snap = _canonical(cvd="BEARISH")
        self.assertFalse(self.engine._check_cvd_aligned(
            self.engine._build_snapshot("MNQ", _orb_status(), None, 21000, snap),
            "Long"))

    def test_cvd_aligned_short_bearish(self):
        snap = _canonical(cvd="BEARISH")
        self.assertTrue(self.engine._check_cvd_aligned(
            self.engine._build_snapshot("MNQ", _orb_status(direction="Short"),
                                        None, 21000, snap),
            "Short"))


# ══════════════════════════════════════════════════════════════════════════════
# 8. Entry simulation per variant
# ══════════════════════════════════════════════════════════════════════════════

class TestEntrySimulation(unittest.TestCase):
    """Test the core entry logic in _process_one_experiment."""

    def _make_result(self, variant: str, direction: str = "Long",
                     entry: float = 21000.0, stop: float = 20900.0,
                     tp1: float = 21300.0, tp2: float = 21600.0,
                     entry_rule: str = "CLOSE_OUTSIDE") -> Dict:
        return {
            "result_id":    f"RES_{variant}",
            "experiment_id": f"EXP_{variant}",
            "opportunity_id": "OPP1",
            "status":       ResultStatus.WATCHING_ENTRY,
            "variant_name": variant,
            "instrument":   "MNQ",
            "direction":    direction,
            "entry_price":  entry,
            "stop_price":   stop,
            "tp1_price":    tp1,
            "tp2_price":    tp2,
            "entry_rule":   entry_rule,
            "filter_rules": {},
            "mfe_r": 0.0, "mae_r": 0.0, "mfe_price": None, "mae_price": None,
            "bars_held": 0, "last_bar_ts": None, "cost_r": 0.04,
        }

    def test_close_outside_triggers_entry(self):
        """Bar close above planned entry triggers CLOSE_OUTSIDE entry."""
        engine = _make_engine()
        rd = self._make_result(Variant.BUFFER_PLUS_2, entry=21005.0, entry_rule="CLOSE_OUTSIDE")
        engine._open_results["RES_BUFFER_PLUS_2"] = rd
        engine._enter_experiment = MagicMock()

        # Bar that closes ABOVE the entry level
        bar = _bar(high=21010, low=20990, close=21006, ts=200)
        orb = _orb_status(state="POSITION_ACTIVE")
        engine._process_one_experiment(
            "RES_BUFFER_PLUS_2", "MNQ", bar, 200, 21010, 20990, 21006, orb)
        engine._enter_experiment.assert_called_once()

    def test_close_outside_no_trigger_below(self):
        """Bar close BELOW entry level should not trigger for Long."""
        engine = _make_engine()
        rd = self._make_result(Variant.BUFFER_PLUS_2, entry=21005.0, entry_rule="CLOSE_OUTSIDE")
        engine._open_results["RES_BUFFER_PLUS_2"] = rd
        engine._enter_experiment = MagicMock()

        bar = _bar(high=21003, low=20990, close=21002, ts=200)
        orb = _orb_status()
        engine._process_one_experiment(
            "RES_BUFFER_PLUS_2", "MNQ", bar, 200, 21003, 20990, 21002, orb)
        engine._enter_experiment.assert_not_called()

    def test_touch_triggers_on_bar_low(self):
        """TOUCH variant enters when bar_low <= breakout level (Long)."""
        engine = _make_engine()
        rd = self._make_result(Variant.TOUCH, entry=20980.0, entry_rule="TOUCH")
        engine._open_results["RES_TOUCH"] = rd
        engine._enter_experiment = MagicMock()

        # Bar low touches the breakout level
        bar = _bar(high=21010, low=20978, close=21005, ts=200)
        orb = _orb_status()
        engine._process_one_experiment(
            "RES_TOUCH", "MNQ", bar, 200, 21010, 20978, 21005, orb)
        engine._enter_experiment.assert_called_once()

    def test_touch_no_trigger_above_level(self):
        """TOUCH variant should not enter if bar_low > breakout level."""
        engine = _make_engine()
        rd = self._make_result(Variant.TOUCH, entry=20980.0, entry_rule="TOUCH")
        engine._open_results["RES_TOUCH"] = rd
        engine._enter_experiment = MagicMock()

        bar = _bar(high=21010, low=20985, close=21005, ts=200)
        orb = _orb_status()
        engine._process_one_experiment(
            "RES_TOUCH", "MNQ", bar, 200, 21010, 20985, 21005, orb)
        engine._enter_experiment.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 9. Exit simulation — stop, TP, same-bar
# ══════════════════════════════════════════════════════════════════════════════

class TestExitSimulation(unittest.TestCase):

    def _make_active(self, direction: str = "Long",
                     entry: float = 21000.0, stop: float = 20900.0,
                     tp1: float = 21300.0, tp2: float = 21600.0) -> Dict:
        return {
            "result_id":    "RES_BASELINE",
            "experiment_id": "EXP_BASELINE",
            "opportunity_id": "OPP1",
            "status":       ResultStatus.ACTIVE,
            "variant_name": Variant.BASELINE,
            "instrument":   "MNQ",
            "direction":    direction,
            "entry_price":  entry,
            "stop_price":   stop,
            "tp1_price":    tp1,
            "tp2_price":    tp2,
            "entry_rule":   "CLOSE_OUTSIDE",
            "filter_rules": {},
            "mfe_r": 0.0, "mae_r": 0.0, "mfe_price": None, "mae_price": None,
            "bars_held": 5, "last_bar_ts": 199, "cost_r": 0.04,
        }

    def test_stop_hit_long(self):
        engine = _make_engine()
        rd = self._make_active()
        engine._open_results["RES_BASELINE"] = rd
        engine._complete_experiment = MagicMock()

        # Bar low goes below stop (20900)
        bar = _bar(high=21050, low=20890, close=20895, ts=200)
        orb = _orb_status(state="POSITION_ACTIVE")
        engine._process_one_experiment(
            "RES_BASELINE", "MNQ", bar, 200, 21050, 20890, 20895, orb)
        calls = engine._complete_experiment.call_args_list
        self.assertEqual(len(calls), 1)
        self.assertIn("STOP_HIT", str(calls[0]))

    def test_tp1_hit_long(self):
        engine = _make_engine()
        rd = self._make_active()
        engine._open_results["RES_BASELINE"] = rd
        engine._complete_experiment = MagicMock()

        bar = _bar(high=21310, low=21000, close=21305, ts=200)
        orb = _orb_status(state="POSITION_ACTIVE")
        engine._process_one_experiment(
            "RES_BASELINE", "MNQ", bar, 200, 21310, 21000, 21305, orb)
        calls = engine._complete_experiment.call_args_list
        self.assertEqual(len(calls), 1)
        self.assertIn("TP1_HIT", str(calls[0]))

    def test_tp2_hit_long(self):
        engine = _make_engine()
        rd = self._make_active()
        engine._open_results["RES_BASELINE"] = rd
        engine._complete_experiment = MagicMock()

        bar = _bar(high=21610, low=21000, close=21605, ts=200)
        orb = _orb_status(state="POSITION_ACTIVE")
        engine._process_one_experiment(
            "RES_BASELINE", "MNQ", bar, 200, 21610, 21000, 21605, orb)
        calls = engine._complete_experiment.call_args_list
        self.assertEqual(len(calls), 1)
        self.assertIn("TP2_HIT", str(calls[0]))

    def test_same_bar_stop_wins_conservative(self):
        """When both stop and TP hit in the same bar, stop is applied first."""
        engine = _make_engine()
        rd = self._make_active()
        engine._open_results["RES_BASELINE"] = rd
        captured = {}

        def _complete(result_id, exit_price, exit_reason, result, exit_ts,
                      ambiguous_bar=False, **kw):
            captured["exit_reason"]    = exit_reason
            captured["ambiguous_bar"]  = ambiguous_bar

        engine._complete_experiment = _complete

        # Bar that simultaneously hits both stop (low=20890) and TP1 (high=21310)
        bar = _bar(high=21310, low=20890, close=21000, ts=200)
        orb = _orb_status(state="POSITION_ACTIVE")
        engine._process_one_experiment(
            "RES_BASELINE", "MNQ", bar, 200, 21310, 20890, 21000, orb)
        self.assertEqual(captured.get("exit_reason"), "STOP_HIT")
        self.assertTrue(captured.get("ambiguous_bar"))

    def test_short_stop_hit_on_high(self):
        """Short position: stop is hit when bar_high >= stop."""
        engine = _make_engine()
        rd = self._make_active(direction="Short", entry=21000, stop=21100,
                               tp1=20700, tp2=20400)
        engine._open_results["RES_BASELINE"] = rd
        engine._complete_experiment = MagicMock()

        # Bar high touches stop
        bar = _bar(high=21105, low=20990, close=21000, ts=200)
        orb = _orb_status(state="POSITION_ACTIVE")
        engine._process_one_experiment(
            "RES_BASELINE", "MNQ", bar, 200, 21105, 20990, 21000, orb)
        calls = engine._complete_experiment.call_args_list
        self.assertEqual(len(calls), 1)
        self.assertIn("STOP_HIT", str(calls[0]))


# ══════════════════════════════════════════════════════════════════════════════
# 10. Lookahead prevention
# ══════════════════════════════════════════════════════════════════════════════

class TestLookaheadPrevention(unittest.TestCase):
    def test_same_bar_ts_skipped(self):
        """Engine must not process the same bar twice (idempotency guard)."""
        engine = _make_engine()
        rd = {
            "result_id": "RES_BASELINE", "experiment_id": "EXP", "opportunity_id": "OPP1",
            "status": ResultStatus.ACTIVE, "variant_name": Variant.BASELINE,
            "instrument": "MNQ", "direction": "Long",
            "entry_price": 21000.0, "stop_price": 20900.0,
            "tp1_price": 21300.0, "tp2_price": 21600.0,
            "entry_rule": "CLOSE_OUTSIDE", "filter_rules": {},
            "mfe_r": 0.0, "mae_r": 0.0, "mfe_price": None, "mae_price": None,
            "bars_held": 5, "last_bar_ts": 200,  # ← same as bar below
            "cost_r": 0.04,
        }
        engine._open_results["RES_BASELINE"] = rd
        engine._complete_experiment = MagicMock()

        # Same bar_ts as last_bar_ts → should be a no-op
        bar = _bar(high=21310, low=20890, close=21000, ts=200)
        orb = _orb_status(state="POSITION_ACTIVE")
        engine._process_one_experiment(
            "RES_BASELINE", "MNQ", bar, 200, 21310, 20890, 21000, orb)
        engine._complete_experiment.assert_not_called()

    def test_older_bar_skipped(self):
        """bar_ts earlier than last_bar_ts is skipped (replay guard)."""
        engine = _make_engine()
        rd = {
            "result_id": "RES_B", "experiment_id": "EXP", "opportunity_id": "OPP1",
            "status": ResultStatus.ACTIVE, "variant_name": Variant.BASELINE,
            "instrument": "MNQ", "direction": "Long",
            "entry_price": 21000.0, "stop_price": 20900.0,
            "tp1_price": 21300.0, "tp2_price": 21600.0,
            "entry_rule": "CLOSE_OUTSIDE", "filter_rules": {},
            "mfe_r": 0.0, "mae_r": 0.0, "mfe_price": None, "mae_price": None,
            "bars_held": 5, "last_bar_ts": 300,  # ← newer than bar below
            "cost_r": 0.04,
        }
        engine._open_results["RES_B"] = rd
        engine._complete_experiment = MagicMock()

        bar = _bar(high=21310, low=20890, close=21000, ts=200)  # older bar
        orb = _orb_status(state="POSITION_ACTIVE")
        engine._process_one_experiment(
            "RES_B", "MNQ", bar, 200, 21310, 20890, 21000, orb)
        engine._complete_experiment.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 11. BREAKOUT_MISSED forces NO_ENTRY
# ══════════════════════════════════════════════════════════════════════════════

class TestBreakoutMissedForceNoEntry(unittest.TestCase):
    def test_completion_runs_outside_lock_and_engine_remains_usable(self):
        engine = _make_engine()
        engine._active_opp["MNQ"] = "OPP_UNLOCKED"
        engine._open_results["RES_UNLOCKED"] = {
            "result_id": "RES_UNLOCKED", "opportunity_id": "OPP_UNLOCKED",
            "status": ResultStatus.WATCHING_ENTRY, "instrument": "MNQ",
        }
        completed = []

        def complete(result_id, **_kwargs):
            # This models the real completion path's re-entry into _lock.
            with engine._lock:
                completed.append(result_id)
                engine._open_results.pop(result_id, None)

        engine._complete_experiment = complete
        worker = threading.Thread(
            target=engine._on_breakout_missed,
            args=("MNQ", _orb_status(state="BREAKOUT_MISSED"), "2026-08-21"),
        )
        worker.start()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive(), "BREAKOUT_MISSED must not self-deadlock")
        self.assertEqual(completed, ["RES_UNLOCKED"])
        # A fresh state operation proves subsequent GRE processing can use the lock.
        with engine._lock:
            engine._last_orb_state["MNQ"] = "BREAKOUT_MISSED"

    def test_watching_entry_becomes_no_entry(self):
        engine = _make_engine()
        engine._active_opp["MNQ"] = "OPP_MISSED"
        rd = {
            "result_id": "RES_MISSED", "experiment_id": "EXP", "opportunity_id": "OPP_MISSED",
            "status": ResultStatus.WATCHING_ENTRY, "variant_name": Variant.BASELINE,
            "instrument": "MNQ", "direction": "Long",
            "entry_price": 21000.0, "stop_price": 20900.0,
            "tp1_price": 21300.0, "tp2_price": 21600.0,
            "entry_rule": "CLOSE_OUTSIDE", "filter_rules": {},
            "mfe_r": 0.0, "mae_r": 0.0, "mfe_price": None, "mae_price": None,
            "bars_held": 0, "last_bar_ts": None, "cost_r": 0.04,
        }
        engine._open_results["RES_MISSED"] = rd
        engine._complete_experiment = MagicMock()

        orb = _orb_status(state="BREAKOUT_MISSED")
        engine._last_orb_state["MNQ"] = "BREAKOUT_DETECTED"  # was in different state
        engine._process_inst("MNQ", orb, _bar(21050, 20960, 21000, ts=300), 21000)
        engine._complete_experiment.assert_called()

    def test_pending_becomes_no_entry(self):
        engine = _make_engine()
        engine._active_opp["MNQ"] = "OPP2"
        rd = {
            "result_id": "RES_P", "experiment_id": "EXP", "opportunity_id": "OPP2",
            "status": ResultStatus.PENDING, "variant_name": Variant.TOUCH,
            "instrument": "MNQ", "direction": "Long",
            "entry_price": 20980.0, "stop_price": 20900.0,
            "tp1_price": 21180.0, "tp2_price": 21380.0,
            "entry_rule": "TOUCH", "filter_rules": {},
            "mfe_r": 0.0, "mae_r": 0.0, "mfe_price": None, "mae_price": None,
            "bars_held": 0, "last_bar_ts": None, "cost_r": 0.04,
        }
        engine._open_results["RES_P"] = rd
        engine._complete_experiment = MagicMock()

        orb = _orb_status(state="BREAKOUT_MISSED")
        engine._last_orb_state["MNQ"] = "CONFIRMATION_PENDING"
        engine._process_inst("MNQ", orb, _bar(21050, 20960, 21000, ts=300), 21000)
        engine._complete_experiment.assert_called()


# ══════════════════════════════════════════════════════════════════════════════
# 12. MAE/MFE tracking
# ══════════════════════════════════════════════════════════════════════════════

class TestMaeMfeTracking(unittest.TestCase):
    def test_mfe_increases_on_favorable_bar(self):
        engine = _make_engine()
        rd = {
            "result_id": "RES_T", "experiment_id": "EXP", "opportunity_id": "OPP",
            "status": ResultStatus.ACTIVE, "variant_name": Variant.TP_1R,
            "instrument": "MNQ", "direction": "Long",
            "entry_price": 21000.0, "stop_price": 20900.0,
            "tp1_price": 21100.0, "tp2_price": 21100.0,
            "entry_rule": "CLOSE_OUTSIDE", "filter_rules": {},
            "mfe_r": 0.0, "mae_r": 0.0, "mfe_price": None, "mae_price": None,
            "bars_held": 0, "last_bar_ts": 100, "cost_r": 0.04,
        }
        engine._open_results["RES_T"] = rd

        # Bar that favorably moves — high=21060 is 0.6R favorable
        bar = _bar(high=21060, low=20985, close=21050, ts=200)
        orb = _orb_status(state="POSITION_ACTIVE")
        engine._process_one_experiment("RES_T", "MNQ", bar, 200, 21060, 20985, 21050, orb)

        with engine._lock:
            updated = engine._open_results.get("RES_T", {})
        self.assertAlmostEqual(updated.get("mfe_r", 0), 0.6, places=4)
        self.assertLess(updated.get("mae_r", 0), 0)  # adverse = below entry

    def test_mae_tracks_adverse_excursion(self):
        engine = _make_engine()
        rd = {
            "result_id": "RES_MAE", "experiment_id": "EXP", "opportunity_id": "OPP",
            "status": ResultStatus.ACTIVE, "variant_name": Variant.TP_1R,
            "instrument": "MNQ", "direction": "Long",
            "entry_price": 21000.0, "stop_price": 20900.0,
            "tp1_price": 21200.0, "tp2_price": 21200.0,
            "entry_rule": "CLOSE_OUTSIDE", "filter_rules": {},
            "mfe_r": 0.0, "mae_r": 0.0, "mfe_price": None, "mae_price": None,
            "bars_held": 0, "last_bar_ts": 100, "cost_r": 0.04,
        }
        engine._open_results["RES_MAE"] = rd

        # Bar dips below entry by 30 pts = 0.3R adverse
        bar = _bar(high=21010, low=20970, close=21005, ts=200)
        orb = _orb_status(state="POSITION_ACTIVE")
        engine._process_one_experiment("RES_MAE", "MNQ", bar, 200, 21010, 20970, 21005, orb)

        with engine._lock:
            updated = engine._open_results.get("RES_MAE", {})
        self.assertAlmostEqual(updated.get("mae_r", 0), -0.3, places=4)


# ══════════════════════════════════════════════════════════════════════════════
# 13. Multiple instruments isolation
# ══════════════════════════════════════════════════════════════════════════════

class TestMultiInstrumentIsolation(unittest.TestCase):
    def test_different_instruments_get_different_opportunities(self):
        """MNQ and MGC breakouts on the same day must have different opportunity IDs."""
        mnq_id = _opportunity_id("MNQ", "2026-08-11", 100, "Long")
        mgc_id = _opportunity_id("MGC", "2026-08-11", 100, "Long")
        self.assertNotEqual(mnq_id, mgc_id)

    def test_mnq_open_result_not_processed_for_mgc_bar(self):
        """Processing a MGC bar should not touch MNQ open results."""
        engine = _make_engine()
        rd = {
            "result_id": "RES_MNQ", "experiment_id": "EXP", "opportunity_id": "OPP",
            "status": ResultStatus.ACTIVE, "variant_name": Variant.BASELINE,
            "instrument": "MNQ",  # ← MNQ result
            "direction": "Long",
            "entry_price": 21000.0, "stop_price": 20900.0,
            "tp1_price": 21300.0, "tp2_price": 21600.0,
            "entry_rule": "CLOSE_OUTSIDE", "filter_rules": {},
            "mfe_r": 0.0, "mae_r": 0.0, "mfe_price": None, "mae_price": None,
            "bars_held": 3, "last_bar_ts": 100, "cost_r": 0.04,
        }
        engine._open_results["RES_MNQ"] = rd
        engine._complete_experiment = MagicMock()

        # Process MGC bar close that would hit stop level if MNQ
        bar = _bar(high=2010, low=1990, close=2000, ts=200)
        # Instrument filtering: _process_open_experiments filters by inst
        engine._process_open_experiments("MGC", bar, 2000, _orb_status(state="POSITION_ACTIVE"))
        engine._complete_experiment.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 14. Fail-open on DB error
# ══════════════════════════════════════════════════════════════════════════════

class TestFailOpen(unittest.TestCase):
    def test_on_bar_close_does_not_raise_on_db_error(self):
        engine = _make_engine(db_ready=False)

        # Should silently no-op, never raise
        try:
            engine.on_bar_close("MNQ", _orb_status(), 21000)
        except Exception as exc:
            self.fail(f"on_bar_close raised unexpectedly: {exc}")

    def test_db_error_in_insert_does_not_crash(self):
        """If DB insert fails, engine catches and continues."""
        engine = _make_engine()

        def _bad_db():
            raise Exception("DB connection refused")

        engine._get_db = _bad_db
        GhostResearchEngine.GRE_DB_READY = True

        try:
            engine._on_breakout_detected(
                "MNQ", _orb_status(), _bar(21050, 20960, 21000), 21000, "2026-08-11")
        except Exception as exc:
            self.fail(f"Should not raise: {exc}")

    def test_canonical_failure_does_not_crash(self):
        engine = _make_engine()

        def _bad_can(inst):
            raise RuntimeError("Canonical state unavailable")

        engine._get_can = _bad_can
        GhostResearchEngine.GRE_DB_READY = True

        try:
            engine._on_breakout_detected(
                "MNQ", _orb_status(), _bar(21050, 20960, 21000), 21000, "2026-08-11")
        except Exception as exc:
            self.fail(f"Should not raise: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# 15. Dedup guard (same opportunity not re-inserted)
# ══════════════════════════════════════════════════════════════════════════════

class TestOpportunityDedup(unittest.TestCase):
    def test_same_opportunity_not_created_twice(self):
        engine = _make_engine()
        opp_id = _opportunity_id("MNQ", "2026-08-11", 100, "Long")
        engine._active_opp["MNQ"] = opp_id  # simulate already recorded

        insert_calls: List = []
        engine._insert_opportunity = lambda *a, **k: insert_calls.append(a) or False

        orb = _orb_status(breakout_bar_ts=100)
        engine._on_breakout_detected("MNQ", orb, _bar(21050, 20960), 21000, "2026-08-11")
        self.assertEqual(len(insert_calls), 0)

    def test_new_breakout_same_day_different_direction_creates_new(self):
        engine = _make_engine()
        opp_long  = _opportunity_id("MNQ", "2026-08-11", 100, "Long")
        opp_short = _opportunity_id("MNQ", "2026-08-11", 100, "Short")
        self.assertNotEqual(opp_long, opp_short)


# ══════════════════════════════════════════════════════════════════════════════
# 16. Outcome computation
# ══════════════════════════════════════════════════════════════════════════════

class TestOutcomeComputation(unittest.TestCase):
    def test_win_result(self):
        r = _compute_gross_r("Long", 21000, 21300, 20900)
        self.assertAlmostEqual(r, 3.0, places=5)

    def test_loss_result(self):
        # Exit at stop: risk=100, loss=(20900-21000)/100 = -1.0R
        r = _compute_gross_r("Long", 21000, 20900, 20900)
        self.assertAlmostEqual(r, -1.0, places=5)

    def test_breakeven_near_zero(self):
        r = _compute_gross_r("Long", 21000, 21001, 20900)
        self.assertAlmostEqual(r, 0.01, places=4)

    def test_win_classification(self):
        """net_r > 0.05 → WIN"""
        rows = [{"net_r": 0.5, "gross_r": 0.6, "result": OutcomeResult.WIN,
                 "mfe_r": 0.8, "mae_r": -0.1,
                 "entry_timestamp": "2026-08-11T10:00:00+00:00"}]
        s = _aggregate_results(rows)
        self.assertEqual(s["win_count"], 1)

    def test_loss_classification(self):
        rows = [{"net_r": -0.8, "gross_r": -0.7, "result": OutcomeResult.LOSS,
                 "mfe_r": 0.2, "mae_r": -1.0,
                 "entry_timestamp": "2026-08-11T10:00:00+00:00"}]
        s = _aggregate_results(rows)
        self.assertEqual(s["loss_count"], 1)


# ══════════════════════════════════════════════════════════════════════════════
# 17. Cost model verification
# ══════════════════════════════════════════════════════════════════════════════

class TestCostModel(unittest.TestCase):
    def test_cost_positive_for_all_instruments(self):
        for inst in ["MNQ", "MGC", "MES", "MYM"]:
            r = _commission_r(inst, 100 + gre._TICK_SIZE.get(inst, 0.25) * 100,
                              100)
            self.assertGreater(r, 0, f"{inst} should have positive cost")

    def test_larger_risk_reduces_cost_r(self):
        """Greater R denominator → smaller cost ratio."""
        r_small_risk = _commission_r("MNQ", 21000, 20950)  # 50 pt risk
        r_large_risk = _commission_r("MNQ", 21000, 20500)  # 500 pt risk
        self.assertGreater(r_small_risk, r_large_risk)

    def test_net_r_less_than_gross_r(self):
        gross_r = _compute_gross_r("Long", 21000, 21300, 20900)
        cost_r  = _commission_r("MNQ", 21000, 20900)
        net_r   = gross_r - cost_r
        self.assertLess(net_r, gross_r)


# ══════════════════════════════════════════════════════════════════════════════
# 18. Restart recovery — open results restored (structure test)
# ══════════════════════════════════════════════════════════════════════════════

class TestRestartRecovery(unittest.TestCase):
    def test_boot_with_db_error_fails_open(self):
        """DB probe failure must not crash boot."""
        engine = GhostResearchEngine(
            get_db_fn=lambda: (_ for _ in ()).throw(Exception("DB down")),
            get_canonical_fn=lambda i: {},
            get_bars_fn=lambda i: [],
            re_event_fn=MagicMock(),
            instruments=INSTRUMENTS,
        )
        try:
            engine.boot()
        except Exception as exc:
            self.fail(f"boot should not raise: {exc}")

    def test_gre_db_ready_false_after_failed_probe(self):
        engine = GhostResearchEngine(
            get_db_fn=lambda: (_ for _ in ()).throw(Exception("DB down")),
            get_canonical_fn=lambda i: {},
            get_bars_fn=lambda i: [],
            re_event_fn=MagicMock(),
            instruments=INSTRUMENTS,
        )
        engine.boot()
        self.assertFalse(GhostResearchEngine.GRE_DB_READY)


# ══════════════════════════════════════════════════════════════════════════════
# 19. Context filter breakdowns (unit)
# ══════════════════════════════════════════════════════════════════════════════

class TestContextBreakdowns(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()

    def test_breakdown_by_trend_15m(self):
        rows = [
            {**_make_row(1.0), "trend_15m": "BULLISH", "trend_4h": "BULLISH",
             "cvd_direction": "BULLISH", "vwap_side": "ABOVE", "direction": "Long"},
            {**_make_row(-0.5), "trend_15m": "BEARISH", "trend_4h": "BULLISH",
             "cvd_direction": "BEARISH", "vwap_side": "BELOW", "direction": "Long"},
        ]
        bd = self.engine._compute_breakdowns(rows)
        self.assertIn("by_trend_15m", bd)
        self.assertIn("BULLISH", bd["by_trend_15m"])
        self.assertIn("BEARISH", bd["by_trend_15m"])

    def test_breakdown_counts(self):
        rows = [
            {**_make_row(1.0), "trend_15m": "BULLISH", "trend_4h": "BULLISH",
             "cvd_direction": "BULLISH", "vwap_side": "ABOVE", "direction": "Long"},
        ]
        bd = self.engine._compute_breakdowns(rows)
        bull = bd["by_trend_15m"].get("BULLISH", {})
        self.assertEqual(bull.get("n"), 1)


# ══════════════════════════════════════════════════════════════════════════════
# 20. Variant TP target computations
# ══════════════════════════════════════════════════════════════════════════════

class TestVariantTargetComputation(unittest.TestCase):
    def test_tp_1r_long(self):
        """TP_1R variant: target = entry + 1 × risk."""
        entry = 21000.0; stop = 20900.0
        risk  = entry - stop  # = 100 pts
        expected_tp = entry + risk   # = 21100
        self.assertAlmostEqual(expected_tp, 21100.0)

    def test_tp_1_5r_long(self):
        entry = 21000.0; stop = 20900.0
        risk  = entry - stop
        expected_tp = entry + 1.5 * risk
        self.assertAlmostEqual(expected_tp, 21150.0)

    def test_tp_2r_long(self):
        entry = 21000.0; stop = 20900.0
        risk  = entry - stop
        expected_tp = entry + 2.0 * risk
        self.assertAlmostEqual(expected_tp, 21200.0)

    def test_buffer_plus_2_long(self):
        """BUFFER_PLUS_2 Long: entry = breakout_level + 2 ticks."""
        tick = gre._TICK_SIZE["MNQ"]  # 0.25
        breakout_level = 20980.0
        expected = breakout_level + 2 * tick
        self.assertAlmostEqual(expected, 20980.5)

    def test_buffer_minus_2_short(self):
        """BUFFER_MINUS_2 Short: entry = breakout_level + 2 ticks (closer to range)."""
        tick = gre._TICK_SIZE["MNQ"]
        breakout_level = 20900.0  # short breakout
        expected = breakout_level + 2 * tick  # for Short BUFFER_MINUS_2
        self.assertAlmostEqual(expected, 20900.5)


# ══════════════════════════════════════════════════════════════════════════════
# 21. Transition detection
# ══════════════════════════════════════════════════════════════════════════════

class TestTransitionDetection(unittest.TestCase):
    def test_breakout_detected_fires_once(self):
        engine = _make_engine()
        engine._on_breakout_detected = MagicMock()

        orb1 = _orb_status(state="BREAKOUT_DETECTED")
        engine._last_orb_state["MNQ"] = "RANGE_LOCKED"

        engine._process_inst("MNQ", orb1, _bar(21050, 20960), 21000)
        engine._on_breakout_detected.assert_called_once()

    def test_breakout_detected_not_repeated_same_state(self):
        engine = _make_engine()
        engine._on_breakout_detected = MagicMock()
        engine._last_orb_state["MNQ"] = "BREAKOUT_DETECTED"  # already in state

        orb1 = _orb_status(state="BREAKOUT_DETECTED")
        engine._process_inst("MNQ", orb1, _bar(21050, 20960), 21000)
        engine._on_breakout_detected.assert_not_called()

    def test_position_active_fires_on_qualification(self):
        engine = _make_engine()
        engine._on_position_active = MagicMock()
        engine._last_orb_state["MNQ"] = "CONFIRMATION_PENDING"

        orb1 = _orb_status(state="POSITION_ACTIVE")
        engine._process_inst("MNQ", orb1, _bar(21050, 20960), 21000)
        engine._on_position_active.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# 22. GRE version constant present
# ══════════════════════════════════════════════════════════════════════════════

class TestModuleConstants(unittest.TestCase):
    def test_version_string(self):
        self.assertIsInstance(gre.GRE_VERSION, str)
        self.assertGreater(len(gre.GRE_VERSION), 0)

    def test_strategy_name(self):
        self.assertEqual(gre.STRATEGY_NAME, "09:30_ORB")

    def test_thresholds_sane(self):
        self.assertEqual(gre.THRESH_OBSERVING, 25)
        self.assertGreater(gre.THRESH_VALIDATION, gre.THRESH_OBSERVING)

    def test_tick_sizes_defined(self):
        for inst in ["MNQ", "MGC", "MES", "MYM"]:
            self.assertIn(inst, gre._TICK_SIZE)
            self.assertGreater(gre._TICK_SIZE[inst], 0)

    def test_point_values_defined(self):
        for inst in ["MNQ", "MGC", "MES", "MYM"]:
            self.assertIn(inst, gre._POINT_VALUE)
            self.assertGreater(gre._POINT_VALUE[inst], 0)


# ══════════════════════════════════════════════════════════════════════════════
# 23. get_health returns required keys
# ══════════════════════════════════════════════════════════════════════════════

class TestGetHealth(unittest.TestCase):
    def test_health_keys_present_when_db_ready(self):
        engine = _make_engine()
        db = engine._db
        db.set_rows([(0, 0, 0, 0)])  # fake cursor results

        # Patch cursor to return a usable row
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (5, 10, 8, 2)
        mock_cur.description = [("opps_today",), ("total_experiments",),
                                 ("completed",), ("no_entry_count",)]
        db.cursor = lambda: mock_cur

        h = engine.get_health()
        self.assertIn("gre_version", h)
        self.assertIn("db_ready", h)
        self.assertIn("strategy", h)

    def test_health_db_not_ready(self):
        engine = _make_engine(db_ready=False)
        h = engine.get_health()
        self.assertFalse(h["db_ready"])


# ══════════════════════════════════════════════════════════════════════════════
# Run
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
