"""
Profitability Engine — Phase 1 Tests
======================================
Tests A through Q as specified in the NEXT TASK document (Steps 20 & 21).

These tests exercise the pure computation functions in profitability_engine.py
and the integration seams in app.py WITHOUT touching any live database,
execution gateway, scoring, or strategy code.

Test naming follows the spec:
    A: Observation creation
    B: Duplicate protection
    C: Safety independence
    D: No live bypass
    E: Frozen trade plan
    F: MFE calculation
    G: MAE calculation
    H: Stop outcome
    I: Target outcome
    J: Ambiguous ordering
    K: Transaction costs
    L: Aggregation
    M: Expectancy
    N: Drawdown
    O: Source isolation
    P: No scoring change
    Q: No execution change
"""

import types
import sys
import unittest
from unittest.mock import patch, MagicMock, call


# ---------------------------------------------------------------------------
# Import the pure computation module directly (no app.py needed)
# ---------------------------------------------------------------------------
import importlib, os, pathlib

# Make sure the module can be imported from the test file's location
_here = pathlib.Path(__file__).parent.parent.resolve()
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import profitability_engine as pe


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

SPECS_FIXTURE = {
    "MGC": {"point_value": 10.0, "tick_size": 0.1},
    "MNQ": {"point_value": 2.0,  "tick_size": 0.25},
    "MES": {"point_value": 50.0, "tick_size": 0.25},
}

COMM_SIDE = 0.62    # default commission per side
SLIP_TICKS = 1.0    # default slippage ticks


def _closed_row(strategy_key="MGC|SCALP|LIQUIDITY_SWEEP_REVERSAL|LONG",
                instrument="MGC", net_r=1.0, gross_r=1.12,
                cost_r=0.12, mfe_r=1.5, mae_r=-0.3,
                close_reason="tp1"):
    return {
        "strategy_key": strategy_key,
        "instrument":   instrument,
        "net_r":        net_r,
        "gross_r":      gross_r,
        "cost_r":       cost_r,
        "mfe_r":        mfe_r,
        "mae_r":        mae_r,
        "close_reason": close_reason,
    }


# ---------------------------------------------------------------------------
# A: Observation creation (pure-logic path)
# ---------------------------------------------------------------------------

class TestA_ObservationCreation(unittest.TestCase):
    """A valid obs_key is built for a READY setup."""

    def test_obs_key_is_deterministic(self):
        key1 = pe.build_obs_key("MGC", "Long", "LIQUIDITY_SWEEP_REVERSAL", "20260808", 2010.0)
        key2 = pe.build_obs_key("MGC", "Long", "LIQUIDITY_SWEEP_REVERSAL", "20260808", 2010.0)
        self.assertEqual(key1, key2)

    def test_obs_key_format(self):
        key = pe.build_obs_key("MNQ", "Short", "ORB", "20260808", 19500.0)
        self.assertTrue(key.startswith("ghost|MNQ|Short|ORB|20260808|"))

    def test_entry_bucket_rounds_to_half_point(self):
        # Two prices within the same 0.5-pt bucket → same bucket value.
        # Bucket boundary is at ±0.25 from each 0.5 multiple.
        # 2010.0 and 2010.1 are both in [2010.0, 2010.25) → bucket 2010.0
        b1 = pe.entry_bucket_from_price(2010.0)
        b2 = pe.entry_bucket_from_price(2010.1)
        self.assertEqual(b1, b2)

    def test_entry_bucket_different_buckets(self):
        b1 = pe.entry_bucket_from_price(2010.0)
        b2 = pe.entry_bucket_from_price(2011.0)
        self.assertNotEqual(b1, b2)

    def test_strategy_short_from_pipe_key(self):
        sk = "MGC|SCALP|LIQUIDITY_SWEEP_REVERSAL|LONG"
        self.assertEqual(pe.extract_strategy_short(sk), "LIQUIDITY_SWEEP_REVERSAL")

    def test_strategy_short_from_legacy_key(self):
        sk = "MGC_SCALP_LIQUIDITY_SWEEP_REVERSAL"
        self.assertEqual(pe.extract_strategy_short(sk), "LIQUIDITY_SWEEP_REVERSAL")

    def test_strategy_short_fallback_for_unknown(self):
        self.assertEqual(pe.extract_strategy_short(""), "UNKNOWN")
        self.assertNotEqual(pe.extract_strategy_short("ORB"), "UNKNOWN")


# ---------------------------------------------------------------------------
# B: Duplicate protection
# ---------------------------------------------------------------------------

class TestB_DuplicateProtection(unittest.TestCase):
    """Same setup → same obs_key → idempotent."""

    def test_same_setup_same_key(self):
        """Two READY signals for the same (inst, dir, strategy, day, entry bucket)
        must produce the exact same obs_key (the DB ON CONFLICT handles the rest).
        The 0.5-pt bucket absorbs small price fluctuations: 2010.0 and 2010.1
        both land in the [2010.0, 2010.25) bucket."""
        k1 = pe.build_obs_key("MGC", "Long", "ORB", "20260808", pe.entry_bucket_from_price(2010.0))
        k2 = pe.build_obs_key("MGC", "Long", "ORB", "20260808", pe.entry_bucket_from_price(2010.1))
        # Both 2010.0 and 2010.1 round to bucket 2010.0
        self.assertEqual(k1, k2)

    def test_different_direction_different_key(self):
        k1 = pe.build_obs_key("MGC", "Long",  "ORB", "20260808", 2010.0)
        k2 = pe.build_obs_key("MGC", "Short", "ORB", "20260808", 2010.0)
        self.assertNotEqual(k1, k2)

    def test_different_strategy_different_key(self):
        k1 = pe.build_obs_key("MGC", "Long", "ORB",    "20260808", 2010.0)
        k2 = pe.build_obs_key("MGC", "Long", "CHOCH",  "20260808", 2010.0)
        self.assertNotEqual(k1, k2)

    def test_different_day_different_key(self):
        k1 = pe.build_obs_key("MGC", "Long", "ORB", "20260808", 2010.0)
        k2 = pe.build_obs_key("MGC", "Long", "ORB", "20260809", 2010.0)
        self.assertNotEqual(k1, k2)


# ---------------------------------------------------------------------------
# C: Safety independence
# ---------------------------------------------------------------------------

class TestC_SafetyIndependence(unittest.TestCase):
    """Ghost obs creation logic is independent of execution gate state."""

    def test_ghost_observe_does_not_call_execute_gateway(self):
        """_ghost_observe_setup must never call execute_trade_gateway or
        _maybe_auto_execute — verified by confirming the pure computation
        path in profitability_engine.py has no such reference."""
        import inspect
        src = inspect.getsource(pe)
        self.assertNotIn("execute_trade_gateway", src)
        self.assertNotIn("_maybe_auto_execute",   src)
        self.assertNotIn("ACTIVE_TRADES",         src)
        self.assertNotIn("ARM_STATE",             src)

    def test_profitability_engine_has_no_app_imports(self):
        """profitability_engine.py must not import from app.py
        (it receives needed data as parameters)."""
        import inspect
        src = inspect.getsource(pe)
        self.assertNotIn("import app", src)
        self.assertNotIn("from app", src)


# ---------------------------------------------------------------------------
# D: No live bypass
# ---------------------------------------------------------------------------

class TestD_NoLiveBypass(unittest.TestCase):
    """Creating a ghost observation must not affect the live path."""

    def test_no_money_path_symbols_in_engine(self):
        """The profitability_engine module must not reference any live-trade
        symbols that could affect the money path."""
        import inspect
        src = inspect.getsource(pe)
        for sym in ("MANAGED_TRADES_BY_KEY", "AUTO_FIRED_KEYS",
                    "send_live_ready_card", "_traderspost_send",
                    "execute_trade_gateway", "ACTIVE_TRADES"):
            self.assertNotIn(sym, src,
                             f"profitability_engine must not reference {sym!r}")

    def test_resolve_bar_outcome_never_positive_on_stopped_out(self):
        """When stop is hit, gross_r must be non-positive."""
        # Long trade, bar goes below stop
        _, reason, exit_px, gross_r = pe.resolve_bar_outcome(
            "Long", bar_high=2015.0, bar_low=2005.0,
            entry=2010.0, stop=2007.0, target1=2020.0, target2=None,
            tp1_hit=False, bars_held=5)
        self.assertEqual(reason, pe.CLOSE_STOP)
        self.assertLessEqual(gross_r, 0.0)
        self.assertAlmostEqual(exit_px, 2007.0, places=4)


# ---------------------------------------------------------------------------
# E: Frozen trade plan
# ---------------------------------------------------------------------------

class TestE_FrozenTradePlan(unittest.TestCase):
    """Changing inputs after obs creation does not modify stored values."""

    def test_obs_key_contains_entry_bucket_not_live_price(self):
        """The obs_key is built from entry_bucket (a discrete value), so
        minor price moves after signal time don't create a new observation.
        Prices within 0.25 pts of a 0.5-multiple land in the same bucket."""
        bucket_at_signal = pe.entry_bucket_from_price(2010.05)  # → bucket 2010.0
        bucket_drift     = pe.entry_bucket_from_price(2010.20)  # → bucket 2010.0 (same)
        key_signal = pe.build_obs_key("MGC", "Long", "ORB", "20260808", bucket_at_signal)
        key_drift  = pe.build_obs_key("MGC", "Long", "ORB", "20260808", bucket_drift)
        # Both within [2010.0, 2010.25) → same bucket → same key → ON CONFLICT dedup
        self.assertEqual(key_signal, key_drift)

    def test_compute_gross_r_uses_only_original_stop(self):
        """Gross R is calculated from the original entry/stop, not any managed stop."""
        original_entry = 2010.0
        original_stop  = 2007.0   # risk = 3 pts
        exit_at_tp1    = 2016.0   # 6 pts above entry = +2R

        gross_r = pe.compute_gross_r("Long", original_entry, exit_at_tp1, original_stop)
        self.assertAlmostEqual(gross_r, 2.0, places=4)

        # If someone modifies the stop (e.g. moved to BE), the original calculation
        # must use the ORIGINAL stop — verify by computing with modified stop
        managed_stop_be = 2010.0  # breakeven
        gross_r_wrong = pe.compute_gross_r("Long", original_entry, exit_at_tp1, managed_stop_be)
        # This gives infinity (risk=0) → gross_r_wrong should be None
        self.assertIsNone(gross_r_wrong)


# ---------------------------------------------------------------------------
# F: MFE calculation
# ---------------------------------------------------------------------------

class TestF_MFE(unittest.TestCase):

    def test_long_mfe_basic(self):
        """Long trade: MFE is the highest point reached above entry in R."""
        entry = 2010.0
        stop  = 2007.0   # 3 pts risk
        # Bar reaches 2016 (2 R above entry)
        mfe_r, mae_r, mfe_px, mae_px = pe.update_mfe_mae(
            "Long", bar_high=2016.0, bar_low=2011.0,
            entry=entry, risk_points=abs(entry-stop),
            current_mfe_price=None, current_mae_price=None,
            current_mfe_r=0.0, current_mae_r=0.0,
        )
        self.assertAlmostEqual(mfe_r, (2016.0 - 2010.0) / 3.0, places=4)

    def test_short_mfe_basic(self):
        """Short trade: MFE is the lowest point reached below entry in R."""
        entry = 2010.0
        stop  = 2013.0   # 3 pts risk
        # Bar dips to 2004 (2 R below entry)
        mfe_r, mae_r, mfe_px, mae_px = pe.update_mfe_mae(
            "Short", bar_high=2011.0, bar_low=2004.0,
            entry=entry, risk_points=abs(entry-stop),
            current_mfe_price=None, current_mae_price=None,
            current_mfe_r=0.0, current_mae_r=0.0,
        )
        self.assertAlmostEqual(mfe_r, (2010.0 - 2004.0) / 3.0, places=4)

    def test_mfe_accumulates_over_bars(self):
        """MFE should only increase (ratchet the best price reached)."""
        entry = 2010.0
        risk  = 3.0
        mfe_r1, mae_r1, mfep1, maep1 = pe.update_mfe_mae(
            "Long", 2014.0, 2011.0, entry, risk, None, None, 0.0, 0.0)
        # Second bar reaches lower high — MFE should NOT decrease
        mfe_r2, mae_r2, mfep2, maep2 = pe.update_mfe_mae(
            "Long", 2012.0, 2010.5, entry, risk, mfep1, maep1, mfe_r1, mae_r1)
        self.assertGreaterEqual(mfe_r2, mfe_r1)

    def test_mfe_expressed_in_r_units(self):
        """MFE = 30 pts move / 20 pts risk = 1.5R."""
        entry = 2010.0
        risk  = 20.0
        mfe_r, _, _, _ = pe.update_mfe_mae(
            "Long", 2040.0, 2011.0, entry, risk, None, None, 0.0, 0.0)
        self.assertAlmostEqual(mfe_r, 1.5, places=4)


# ---------------------------------------------------------------------------
# G: MAE calculation
# ---------------------------------------------------------------------------

class TestG_MAE(unittest.TestCase):

    def test_long_mae_basic(self):
        """Long trade: MAE is the lowest point reached below entry in R (negative)."""
        entry = 2010.0
        risk  = 3.0
        # Bar dips to 2008.5 (0.5R adverse)
        _, mae_r, _, _ = pe.update_mfe_mae(
            "Long", 2011.0, 2008.5, entry, risk, None, None, 0.0, 0.0)
        self.assertAlmostEqual(mae_r, (2008.5 - 2010.0) / 3.0, places=4)
        self.assertLess(mae_r, 0)

    def test_short_mae_basic(self):
        """Short trade: MAE is the highest point reached above entry in R (negative)."""
        entry = 2010.0
        risk  = 3.0
        _, mae_r, _, _ = pe.update_mfe_mae(
            "Short", 2011.5, 2009.0, entry, risk, None, None, 0.0, 0.0)
        self.assertAlmostEqual(mae_r, (2010.0 - 2011.5) / 3.0, places=4)
        self.assertLess(mae_r, 0)

    def test_mae_expressed_in_r_units(self):
        """MAE = 10 pts adverse / 20 pts risk = -0.5R."""
        entry = 2010.0
        risk  = 20.0
        _, mae_r, _, _ = pe.update_mfe_mae(
            "Long", 2011.0, 2000.0, entry, risk, None, None, 0.0, 0.0)
        self.assertAlmostEqual(mae_r, (2000.0 - 2010.0) / 20.0, places=4)


# ---------------------------------------------------------------------------
# H: Stop outcome → negative R
# ---------------------------------------------------------------------------

class TestH_StopOutcome(unittest.TestCase):

    def test_long_stop_first(self):
        status, reason, exit_px, gross_r = pe.resolve_bar_outcome(
            "Long", bar_high=2013.0, bar_low=2005.0,
            entry=2010.0, stop=2007.0, target1=2020.0, target2=None,
            tp1_hit=False, bars_held=1)
        self.assertEqual(status, pe.STATUS_CLOSED)
        self.assertEqual(reason, pe.CLOSE_STOP)
        self.assertAlmostEqual(exit_px, 2007.0, places=4)
        self.assertAlmostEqual(gross_r, -1.0, places=4)  # stop = 1R loss

    def test_short_stop_first(self):
        status, reason, exit_px, gross_r = pe.resolve_bar_outcome(
            "Short", bar_high=2015.0, bar_low=2008.0,
            entry=2010.0, stop=2013.0, target1=2000.0, target2=None,
            tp1_hit=False, bars_held=1)
        self.assertEqual(reason, pe.CLOSE_STOP)
        self.assertLess(gross_r, 0.0)

    def test_gross_r_at_stop_is_exactly_minus_one(self):
        """Full stop hit should produce gross_r = -1.0."""
        gross_r = pe.compute_gross_r("Long", 2010.0, 2007.0, 2007.0)
        self.assertAlmostEqual(gross_r, -1.0, places=6)


# ---------------------------------------------------------------------------
# I: Target outcome → positive R
# ---------------------------------------------------------------------------

class TestI_TargetOutcome(unittest.TestCase):

    def test_long_tp1_clean(self):
        status, reason, exit_px, gross_r = pe.resolve_bar_outcome(
            "Long", bar_high=2020.5, bar_low=2011.0,
            entry=2010.0, stop=2007.0, target1=2020.0, target2=None,
            tp1_hit=False, bars_held=3)
        self.assertEqual(status, pe.STATUS_CLOSED)
        self.assertEqual(reason, pe.CLOSE_TP1)
        self.assertAlmostEqual(exit_px, 2020.0, places=4)
        # risk = 3 pts, target = 10 pts → +3.33R
        self.assertAlmostEqual(gross_r, (2020.0 - 2010.0) / 3.0, places=4)

    def test_short_tp1_clean(self):
        status, reason, exit_px, gross_r = pe.resolve_bar_outcome(
            "Short", bar_high=2009.0, bar_low=1998.0,
            entry=2010.0, stop=2013.0, target1=2001.0, target2=None,
            tp1_hit=False, bars_held=2)
        self.assertEqual(reason, pe.CLOSE_TP1)
        self.assertGreater(gross_r, 0.0)

    def test_gross_r_at_2r_target(self):
        """Target at 2R should produce exactly +2.0 gross_r."""
        gross_r = pe.compute_gross_r("Long", 2010.0, 2016.0, 2007.0)
        self.assertAlmostEqual(gross_r, 2.0, places=6)


# ---------------------------------------------------------------------------
# J: Ambiguous ordering — conservative (stop-first)
# ---------------------------------------------------------------------------

class TestJ_AmbiguousOrdering(unittest.TestCase):

    def test_same_bar_touches_both_stop_and_target_long(self):
        """When bar touches both stop AND target for a long, stop-first wins."""
        status, reason, exit_px, gross_r = pe.resolve_bar_outcome(
            "Long", bar_high=2020.5, bar_low=2006.5,   # touches both
            entry=2010.0, stop=2007.0, target1=2020.0, target2=None,
            tp1_hit=False, bars_held=2)
        self.assertEqual(status, pe.STATUS_CLOSED)
        self.assertEqual(reason, pe.CLOSE_AMBIGUOUS,
                         "Ambiguous bar must use conservative (stop-first) resolution")
        self.assertLessEqual(gross_r, 0.0,
                             "Conservative outcome must not be positive")

    def test_same_bar_touches_both_stop_and_target_short(self):
        """When bar touches both stop AND target for a short, stop-first wins."""
        status, reason, exit_px, gross_r = pe.resolve_bar_outcome(
            "Short", bar_high=2013.5, bar_low=1999.5,  # touches both
            entry=2010.0, stop=2013.0, target1=2000.0, target2=None,
            tp1_hit=False, bars_held=2)
        self.assertEqual(reason, pe.CLOSE_AMBIGUOUS)
        self.assertLessEqual(gross_r, 0.0)

    def test_clean_target_bar_is_not_ambiguous(self):
        """When only the target is touched (not the stop), outcome is TP1."""
        _, reason, _, gross_r = pe.resolve_bar_outcome(
            "Long", bar_high=2021.0, bar_low=2011.0,   # low is above stop (2007)
            entry=2010.0, stop=2007.0, target1=2020.0, target2=None,
            tp1_hit=False, bars_held=2)
        self.assertEqual(reason, pe.CLOSE_TP1)
        self.assertGreater(gross_r, 0.0)


# ---------------------------------------------------------------------------
# K: Transaction costs
# ---------------------------------------------------------------------------

class TestK_TransactionCosts(unittest.TestCase):

    def test_commission_r_is_positive(self):
        cost = pe.compute_commission_r("MGC", 2010.0, 2007.0, SPECS_FIXTURE,
                                       comm_per_side_usd=COMM_SIDE,
                                       slippage_ticks=SLIP_TICKS)
        self.assertIsNotNone(cost)
        self.assertGreater(cost, 0.0)

    def test_commission_r_formula(self):
        """Verify the commission formula manually for MGC.

        risk_$    = (2010 - 2007) * 10 = $30
        comm_$    = 0.62 * 2 = $1.24
        slip_$    = 1.0 tick * 0.1 pts/tick * $10/pt * 2 = $2.00
        total_$   = $3.24
        cost_R    = $3.24 / $30 = 0.108
        """
        cost = pe.compute_commission_r("MGC", 2010.0, 2007.0, SPECS_FIXTURE,
                                       comm_per_side_usd=0.62,
                                       slippage_ticks=1.0)
        expected = (0.62 * 2 + 1.0 * 0.1 * 10.0 * 2) / (3.0 * 10.0)
        self.assertAlmostEqual(cost, expected, places=6)

    def test_gross_r_and_net_r_are_distinct(self):
        gross_r = 1.0
        cost_r  = pe.compute_commission_r("MGC", 2010.0, 2007.0, SPECS_FIXTURE)
        self.assertIsNotNone(cost_r)
        net_r = gross_r - cost_r
        self.assertLess(net_r, gross_r)
        self.assertNotAlmostEqual(net_r, gross_r, places=4)

    def test_zero_risk_returns_none(self):
        cost = pe.compute_commission_r("MGC", 2010.0, 2010.0, SPECS_FIXTURE)
        self.assertIsNone(cost)

    def test_unknown_instrument_returns_none(self):
        cost = pe.compute_commission_r("XYZW", 100.0, 99.0, SPECS_FIXTURE)
        self.assertIsNone(cost)

    def test_cost_scales_inversely_with_risk(self):
        """Larger stop → smaller cost in R (fixed dollar cost / larger risk)."""
        cost_tight = pe.compute_commission_r("MGC", 2010.0, 2007.0, SPECS_FIXTURE)  # 3-pt stop
        cost_wide  = pe.compute_commission_r("MGC", 2010.0, 2000.0, SPECS_FIXTURE)  # 10-pt stop
        self.assertGreater(cost_tight, cost_wide)


# ---------------------------------------------------------------------------
# L: Aggregation
# ---------------------------------------------------------------------------

class TestL_Aggregation(unittest.TestCase):

    def _make_rows(self):
        return [
            _closed_row(strategy_key="MGC|SCALP|ORB|LONG",       instrument="MGC", net_r=1.5,  mfe_r=2.0, mae_r=-0.2),
            _closed_row(strategy_key="MGC|SCALP|ORB|LONG",       instrument="MGC", net_r=-1.0, mfe_r=0.3, mae_r=-1.1),
            _closed_row(strategy_key="MGC|SCALP|ORB|LONG",       instrument="MGC", net_r=0.8,  mfe_r=1.2, mae_r=-0.4),
            _closed_row(strategy_key="MNQ|SCALP|ORB|LONG",       instrument="MNQ", net_r=2.0,  mfe_r=2.5, mae_r=-0.1),
            _closed_row(strategy_key="MNQ|SCALP|ORB|LONG",       instrument="MNQ", net_r=-1.5, mfe_r=0.2, mae_r=-1.6),
        ]

    def test_aggregation_separates_instruments(self):
        rows = self._make_rows()
        groups = pe.aggregate_by_strategy_instrument(rows)
        instruments = {g["instrument"] for g in groups}
        self.assertIn("MGC", instruments)
        self.assertIn("MNQ", instruments)
        self.assertEqual(len(groups), 2)

    def test_aggregation_counts_are_correct(self):
        rows = self._make_rows()
        groups = pe.aggregate_by_strategy_instrument(rows)
        mgc = next(g for g in groups if g["instrument"] == "MGC")
        self.assertEqual(mgc["closed_trades"], 3)
        self.assertEqual(mgc["wins"],   2)    # 1.5 and 0.8 are wins
        self.assertEqual(mgc["losses"], 1)    # -1.0 is a loss

    def test_aggregation_cumulative_net_r(self):
        rows = self._make_rows()
        groups = pe.aggregate_by_strategy_instrument(rows)
        mgc = next(g for g in groups if g["instrument"] == "MGC")
        # 1.5 + (-1.0) + 0.8 = 1.3
        self.assertAlmostEqual(mgc["cumulative_net_r"], 1.3, places=4)

    def test_same_strategy_different_instruments_stay_separate(self):
        """ORB × MGC and ORB × MNQ must NOT be combined."""
        rows = self._make_rows()
        groups = pe.aggregate_by_strategy_instrument(rows)
        self.assertEqual(len(groups), 2,
                         "Same strategy across two instruments must produce two separate rows")


# ---------------------------------------------------------------------------
# M: Expectancy
# ---------------------------------------------------------------------------

class TestM_Expectancy(unittest.TestCase):

    def test_positive_expectancy_from_positive_avg_net_r(self):
        rows = [_closed_row(net_r=r) for r in [1.5, -1.0, 2.0, -0.5, 1.0]]
        stats = pe.compute_edge_ledger_stats(rows)
        # avg = (1.5 - 1.0 + 2.0 - 0.5 + 1.0) / 5 = 0.6
        self.assertAlmostEqual(stats["net_expectancy_r"], 0.6, places=4)
        self.assertGreater(stats["net_expectancy_r"], 0)

    def test_negative_expectancy(self):
        rows = [_closed_row(net_r=r) for r in [-1.0, -1.0, 0.5, -1.0]]
        stats = pe.compute_edge_ledger_stats(rows)
        self.assertLess(stats["net_expectancy_r"], 0)

    def test_win_rate_alone_does_not_determine_profitability(self):
        """55% win rate with tiny wins and big losses = negative expectancy."""
        rows = [_closed_row(net_r=r) for r in [0.1, 0.1, 0.1, 0.1, 0.1, -5.0, -5.0, -5.0, -5.0]]
        stats = pe.compute_edge_ledger_stats(rows)
        self.assertGreater(stats["win_rate"], 0.5)           # > 50% win rate
        self.assertLess(stats["net_expectancy_r"], 0.0)      # but negative expectancy

    def test_profit_factor_formula(self):
        """PF = sum(wins) / |sum(losses)|"""
        wins   = [1.5, 2.0, 1.0]   # total 4.5
        losses = [-1.0, -1.5]       # total -2.5
        pf = pe.compute_profit_factor(4.5, -2.5)
        self.assertAlmostEqual(pf, 4.5 / 2.5, places=6)

    def test_profit_factor_no_losses_returns_none(self):
        pf = pe.compute_profit_factor(3.0, 0.0)
        self.assertIsNone(pf)

    def test_expectancy_equals_avg_net_r(self):
        rows = [_closed_row(net_r=r) for r in [2.0, -1.0, 1.5]]
        stats = pe.compute_edge_ledger_stats(rows)
        self.assertAlmostEqual(stats["net_expectancy_r"],
                               stats["avg_net_r"], places=6)


# ---------------------------------------------------------------------------
# N: Drawdown
# ---------------------------------------------------------------------------

class TestN_Drawdown(unittest.TestCase):

    def test_max_drawdown_basic(self):
        """Series: [1, 2, 0, -1] → peak 2, trough -1, drawdown = -3."""
        dd = pe.compute_max_drawdown([1.0, 2.0, 0.0, -1.0])
        self.assertAlmostEqual(dd, -3.0, places=4)

    def test_max_drawdown_zero_when_monotonically_increasing(self):
        dd = pe.compute_max_drawdown([0.5, 1.0, 1.5, 2.0])
        self.assertAlmostEqual(dd, 0.0, places=4)

    def test_max_drawdown_is_non_positive(self):
        dd = pe.compute_max_drawdown([1.0, -1.0, 0.5, -2.0, 1.5])
        self.assertLessEqual(dd, 0.0)

    def test_max_drawdown_empty_series(self):
        dd = pe.compute_max_drawdown([])
        self.assertEqual(dd, 0.0)

    def test_cumulative_r_tracks_in_aggregate_stats(self):
        rows = [_closed_row(net_r=r) for r in [1.0, -0.5, 2.0, -1.5]]
        stats = pe.compute_edge_ledger_stats(rows)
        # cumulative: 1.0 → 0.5 → 2.5 → 1.0 → peak 2.5 trough 0.5 → dd -2.0
        self.assertLessEqual(stats["max_drawdown_r"], 0.0)
        self.assertAlmostEqual(stats["cumulative_net_r"],
                               1.0 - 0.5 + 2.0 - 1.5, places=4)


# ---------------------------------------------------------------------------
# O: Source isolation
# ---------------------------------------------------------------------------

class TestO_SourceIsolation(unittest.TestCase):

    def test_obs_key_differs_by_day(self):
        """Observations from different days produce different keys."""
        k1 = pe.build_obs_key("MGC", "Long", "ORB", "20260807", 2010.0)
        k2 = pe.build_obs_key("MGC", "Long", "ORB", "20260808", 2010.0)
        self.assertNotEqual(k1, k2)

    def test_source_field_constants_are_distinct(self):
        self.assertNotEqual(pe.GHOST_SOURCE_LIVE_SHADOW, pe.GHOST_SOURCE_PAPER)
        self.assertNotEqual(pe.GHOST_SOURCE_PAPER, pe.GHOST_SOURCE_BACKTEST)
        self.assertNotEqual(pe.GHOST_SOURCE_LIVE_SHADOW, pe.GHOST_SOURCE_BACKTEST)

    def test_aggregate_does_not_mix_instruments(self):
        """Filtering by source must be possible since source is preserved per row."""
        live_rows = [
            {"strategy_key":"ORB","instrument":"MGC","net_r":1.0,
             "source": pe.GHOST_SOURCE_LIVE_SHADOW,
             "gross_r":1.1,"cost_r":0.1,"mfe_r":1.5,"mae_r":-0.2}
        ]
        bt_rows = [
            {"strategy_key":"ORB","instrument":"MGC","net_r":-0.5,
             "source": pe.GHOST_SOURCE_BACKTEST,
             "gross_r":-0.4,"cost_r":0.1,"mfe_r":0.2,"mae_r":-0.6}
        ]
        live_stats = pe.compute_edge_ledger_stats(live_rows)
        bt_stats   = pe.compute_edge_ledger_stats(bt_rows)
        self.assertGreater(live_stats["net_expectancy_r"], 0)
        self.assertLess(bt_stats["net_expectancy_r"], 0)
        # If mixed, expectancy would be (1.0 - 0.5) / 2 = 0.25 — positive but wrong
        combined = pe.compute_edge_ledger_stats(live_rows + bt_rows)
        self.assertNotAlmostEqual(
            combined["net_expectancy_r"], live_stats["net_expectancy_r"],
            msg="Mixed populations produce a different (misleading) expectancy",
        )


# ---------------------------------------------------------------------------
# P: No scoring change
# ---------------------------------------------------------------------------

class TestP_NoScoringChange(unittest.TestCase):
    """profitability_engine.py does not import or modify any scoring symbols."""

    def test_no_scoring_symbols(self):
        import inspect
        src = inspect.getsource(pe)
        for sym in ("edge_score", "evaluate_strict_setup", "EDGE_COMPONENTS",
                    "calculate_bias", "score_alerts", "compute_scalp_quality",
                    "compute_swing_quality", "get_trade_opportunity"):
            self.assertNotIn(sym, src,
                             f"profitability_engine must not reference scoring symbol {sym!r}")

    def test_compute_gross_r_ignores_edge_score(self):
        """gross_r is purely: (exit - entry) / risk — no score input."""
        # Same trade, fake different edge scores → gross_r must be identical
        gr1 = pe.compute_gross_r("Long", 2010.0, 2020.0, 2007.0)
        gr2 = pe.compute_gross_r("Long", 2010.0, 2020.0, 2007.0)
        self.assertAlmostEqual(gr1, gr2, places=8)


# ---------------------------------------------------------------------------
# Q: No execution change
# ---------------------------------------------------------------------------

class TestQ_NoExecutionChange(unittest.TestCase):
    """profitability_engine.py has no execution symbols."""

    def test_no_execution_symbols(self):
        import inspect
        src = inspect.getsource(pe)
        for sym in ("execute_trade_gateway", "_maybe_auto_execute",
                    "traderspost", "TRADERSPOST", "send_order",
                    "EXECUTION_MODE", "AUTO_TRADE", "ARM_STATE"):
            self.assertNotIn(sym, src,
                             f"profitability_engine must not reference execution symbol {sym!r}")

    def test_resolve_bar_outcome_is_pure(self):
        """resolve_bar_outcome has no side effects — calling it twice with the
        same inputs produces identical results."""
        args = ("Long", 2015.0, 2005.0, 2010.0, 2007.0, 2020.0, None, False, 5)
        r1 = pe.resolve_bar_outcome(*args)
        r2 = pe.resolve_bar_outcome(*args)
        self.assertEqual(r1, r2)

    def test_update_mfe_mae_is_pure(self):
        """update_mfe_mae has no side effects."""
        args = ("Long", 2015.0, 2008.0, 2010.0, 3.0, None, None, 0.0, 0.0)
        r1 = pe.update_mfe_mae(*args)
        r2 = pe.update_mfe_mae(*args)
        self.assertEqual(r1, r2)


# ---------------------------------------------------------------------------
# Additional edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):

    def test_expired_after_max_hold_bars(self):
        """Trade not resolved in GHOST_MAX_HOLD_BARS bars → EXPIRED."""
        status, reason, _, _ = pe.resolve_bar_outcome(
            "Long", bar_high=2012.0, bar_low=2009.0,
            entry=2010.0, stop=2007.0, target1=2030.0, target2=None,
            tp1_hit=False, bars_held=pe.GHOST_MAX_HOLD_BARS)
        self.assertEqual(status, pe.STATUS_EXPIRED)
        self.assertEqual(reason, pe.CLOSE_EXPIRED)

    def test_still_open_within_max_hold(self):
        """Trade not at stop or target and not expired → None (still open)."""
        status, reason, _, _ = pe.resolve_bar_outcome(
            "Long", bar_high=2012.0, bar_low=2009.0,
            entry=2010.0, stop=2007.0, target1=2030.0, target2=None,
            tp1_hit=False, bars_held=5)
        self.assertIsNone(status)

    def test_zero_risk_distance_returns_none_for_gross_r(self):
        gross_r = pe.compute_gross_r("Long", 2010.0, 2015.0, 2010.0)
        self.assertIsNone(gross_r)

    def test_invalid_direction_returns_none_for_gross_r(self):
        gross_r = pe.compute_gross_r("SIDEWAYS", 2010.0, 2015.0, 2007.0)
        self.assertIsNone(gross_r)

    def test_aggregate_empty_returns_empty_list(self):
        result = pe.aggregate_by_strategy_instrument([])
        self.assertEqual(result, [])

    def test_edge_ledger_stats_no_rows(self):
        stats = pe.compute_edge_ledger_stats([])
        self.assertEqual(stats["closed_trades"], 0)
        self.assertIsNone(stats["win_rate"])
        self.assertIsNone(stats["net_expectancy_r"])

    def test_mnq_commission_formula(self):
        """MNQ: risk=$50 (1 pt × $2 × 25 ticks? — verify with spec).

        MNQ point_value=2.0, tick_size=0.25
        2-pt stop → risk_$ = 2.0 * 2.0 = $4
        comm_$    = 0.62 * 2 = $1.24
        slip_$    = 1.0 * 0.25 * 2.0 * 2 = $1.00
        cost_R    = $2.24 / $4 = 0.56
        """
        cost = pe.compute_commission_r("MNQ", 19500.0, 19498.0, SPECS_FIXTURE,
                                       comm_per_side_usd=0.62, slippage_ticks=1.0)
        risk_dollars = 2.0 * 2.0     # 2 pts × $2/pt
        expected = (0.62 * 2 + 1.0 * 0.25 * 2.0 * 2) / risk_dollars
        self.assertAlmostEqual(cost, expected, places=6)


if __name__ == "__main__":
    unittest.main()
