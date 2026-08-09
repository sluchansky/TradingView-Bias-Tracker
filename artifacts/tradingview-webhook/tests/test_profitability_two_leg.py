"""
Profitability Engine — Two-Leg Exit Tests (Phase 1 follow-up #142)
===================================================================
Tests for two-leg (SCALP runner) exit tracking:
  - TP1 hit on two-target trade → CLOSE_TP1_PARTIAL, stay open
  - TP2 hit after TP1 → proper weighted gross_r
  - Runner stopped after TP1 → weighted gross_r from stop
  - Runner expired after TP1
  - Ambiguous TP1+stop same bar → stop-first (still closes immediately)
  - compute_two_leg_gross_r weighted formula
  - exit_model constants

Guarantees:
  - No app.py imports — pure computation functions only
  - No DB, no network, no gateway calls
"""

import sys
import pathlib
import unittest

_here = pathlib.Path(__file__).parent.parent.resolve()
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import profitability_engine as pe


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestTwoLegConstants(unittest.TestCase):
    def test_close_tp1_partial_constant_exists(self):
        self.assertEqual(pe.CLOSE_TP1_PARTIAL, "tp1_partial")

    def test_exit_model_constants_exist(self):
        self.assertEqual(pe.EXIT_MODEL_SINGLE,  "single_leg")
        self.assertEqual(pe.EXIT_MODEL_TWO_LEG, "two_leg_scalp")

    def test_two_leg_weights_sum_to_one(self):
        self.assertAlmostEqual(pe.TWO_LEG_WEIGHT_L1 + pe.TWO_LEG_WEIGHT_L2, 1.0)

    def test_tp1_partial_distinct_from_tp1(self):
        self.assertNotEqual(pe.CLOSE_TP1_PARTIAL, pe.CLOSE_TP1)


# ---------------------------------------------------------------------------
# TestTwoLegTP1Hit — TP1 hit on two-target trade → stays open
# ---------------------------------------------------------------------------

class TestTwoLegTP1Hit(unittest.TestCase):
    """When a two-leg trade touches TP1 cleanly, the engine must return
    status=None (still open) + CLOSE_TP1_PARTIAL so the caller keeps
    the observation open and marks tp1_hit=True."""

    def _long_setup(self):
        return dict(
            direction="Long",
            entry=2000.0, stop=1996.0,  # risk = 4 pts
            target1=2008.0,             # 2R
            target2=2012.0,             # 3R
        )

    def test_long_tp1_hit_returns_partial(self):
        s = self._long_setup()
        status, reason, exit_px, gross_r = pe.resolve_bar_outcome(
            s["direction"],
            bar_high=2009.0, bar_low=1998.0,   # TP1 touched, not TP2, not stop
            entry=s["entry"], stop=s["stop"],
            target1=s["target1"], target2=s["target2"],
            tp1_hit=False, bars_held=3,
        )
        self.assertIsNone(status, "status must be None — observation stays open")
        self.assertEqual(reason, pe.CLOSE_TP1_PARTIAL)
        self.assertEqual(exit_px, s["target1"])
        self.assertAlmostEqual(gross_r, 2.0)   # (2008-2000)/4 = 2.0R

    def test_short_tp1_hit_returns_partial(self):
        # Short: entry=2000, stop=2004, TP1=1992, TP2=1988  (risk=4pt)
        # bar_high=2003.9 (below stop@2004 — stop NOT touched)
        # bar_low=1991.0  (below TP1@1992 — TP1 touched cleanly)
        status, reason, exit_px, gross_r = pe.resolve_bar_outcome(
            direction="Short",
            bar_high=2003.9, bar_low=1991.0,
            entry=2000.0, stop=2004.0,
            target1=1992.0, target2=1988.0,
            tp1_hit=False, bars_held=2,
        )
        self.assertIsNone(status)
        self.assertEqual(reason, pe.CLOSE_TP1_PARTIAL)
        self.assertEqual(exit_px, 1992.0)
        self.assertAlmostEqual(gross_r, 2.0)  # (2000-1992)/4 = 2.0R

    def test_tp1_partial_only_when_target2_exists(self):
        """Single-target trade: TP1 should fully close, NOT partial."""
        status, reason, exit_px, gross_r = pe.resolve_bar_outcome(
            direction="Long",
            bar_high=2009.0, bar_low=1998.0,
            entry=2000.0, stop=1996.0,
            target1=2008.0, target2=None,   # ← single leg
            tp1_hit=False, bars_held=3,
        )
        self.assertEqual(status, pe.STATUS_CLOSED)
        self.assertEqual(reason, pe.CLOSE_TP1)          # full close
        self.assertNotEqual(reason, pe.CLOSE_TP1_PARTIAL)

    def test_tp1_ambiguous_with_stop_closes_stop_immediately(self):
        """Same bar touches stop AND TP1 on a two-leg trade → stop-first (still closes)."""
        status, reason, exit_px, gross_r = pe.resolve_bar_outcome(
            direction="Long",
            bar_high=2009.0, bar_low=1995.0,  # bar touches BOTH stop@1996 and TP1@2008
            entry=2000.0, stop=1996.0,
            target1=2008.0, target2=2012.0,
            tp1_hit=False, bars_held=3,
        )
        # Stop-first conservative: observation closes immediately as ambiguous
        self.assertEqual(status, pe.STATUS_CLOSED)
        self.assertEqual(reason, pe.CLOSE_AMBIGUOUS)
        self.assertAlmostEqual(gross_r, -1.0)   # exit at stop = -1R

    def test_tp1_not_yet_hit_keeps_open(self):
        """Bar doesn't reach TP1 — observation must stay open."""
        status, reason, exit_px, gross_r = pe.resolve_bar_outcome(
            direction="Long",
            bar_high=2006.0, bar_low=1997.0,  # below TP1@2008, above stop@1996
            entry=2000.0, stop=1996.0,
            target1=2008.0, target2=2012.0,
            tp1_hit=False, bars_held=5,
        )
        self.assertIsNone(status)
        self.assertIsNone(reason)


# ---------------------------------------------------------------------------
# TestTwoLegRunnerClose — leg 2 resolution after TP1 is already hit
# ---------------------------------------------------------------------------

class TestTwoLegRunnerClose(unittest.TestCase):
    """After tp1_hit=True the engine tracks leg 2 only (watching for TP2 or stop)."""

    def test_tp2_hit_after_tp1_closes(self):
        status, reason, exit_px, gross_r = pe.resolve_bar_outcome(
            direction="Long",
            bar_high=2013.0, bar_low=2002.0,  # TP2@2012 touched
            entry=2000.0, stop=1996.0,
            target1=2008.0, target2=2012.0,
            tp1_hit=True, bars_held=8,
        )
        self.assertEqual(status, pe.STATUS_CLOSED)
        self.assertEqual(reason, pe.CLOSE_TP2)
        self.assertEqual(exit_px, 2012.0)
        self.assertAlmostEqual(gross_r, 3.0)  # (2012-2000)/4 = 3R (leg2 only gross)

    def test_runner_stop_hit_after_tp1(self):
        """After TP1 hit, bar touches stop (runner stopped out)."""
        status, reason, exit_px, gross_r = pe.resolve_bar_outcome(
            direction="Long",
            bar_high=2007.0, bar_low=1995.0,  # stop@1996 touched
            entry=2000.0, stop=1996.0,
            target1=2008.0, target2=2012.0,
            tp1_hit=True, bars_held=12,
        )
        self.assertEqual(status, pe.STATUS_CLOSED)
        self.assertEqual(reason, pe.CLOSE_STOP)
        self.assertAlmostEqual(exit_px, 1996.0)
        self.assertAlmostEqual(gross_r, -1.0)  # exit at stop

    def test_runner_expires_after_tp1(self):
        status, reason, exit_px, gross_r = pe.resolve_bar_outcome(
            direction="Long",
            bar_high=2005.0, bar_low=1997.0,
            entry=2000.0, stop=1996.0,
            target1=2008.0, target2=2012.0,
            tp1_hit=True, bars_held=pe.GHOST_MAX_HOLD_BARS,  # expired
        )
        self.assertEqual(status, pe.STATUS_EXPIRED)
        self.assertEqual(reason, pe.CLOSE_EXPIRED)

    def test_tp2_ambiguous_with_stop_after_tp1(self):
        """After TP1, bar touches TP2 AND stop → conservative stop-first."""
        status, reason, exit_px, gross_r = pe.resolve_bar_outcome(
            direction="Long",
            bar_high=2013.0, bar_low=1995.0,  # both TP2@2012 and stop@1996
            entry=2000.0, stop=1996.0,
            target1=2008.0, target2=2012.0,
            tp1_hit=True, bars_held=6,
        )
        self.assertEqual(status, pe.STATUS_CLOSED)
        self.assertEqual(reason, pe.CLOSE_AMBIGUOUS)
        self.assertAlmostEqual(exit_px, 1996.0)

    def test_runner_still_open_between_tp1_and_tp2(self):
        """After TP1 hit, bar between stop and TP2 → still open."""
        status, reason, exit_px, gross_r = pe.resolve_bar_outcome(
            direction="Long",
            bar_high=2011.0, bar_low=2001.0,  # between TP1 and TP2, above stop
            entry=2000.0, stop=1996.0,
            target1=2008.0, target2=2012.0,
            tp1_hit=True, bars_held=10,
        )
        self.assertIsNone(status)
        self.assertIsNone(reason)


# ---------------------------------------------------------------------------
# TestComputeTwoLegGrossR — weighted R formula
# ---------------------------------------------------------------------------

class TestComputeTwoLegGrossR(unittest.TestCase):
    def test_both_legs_win(self):
        # TP1 at 2R leg-1, TP2 at 3R leg-2 → weighted 50/50 = 2.5R
        result = pe.compute_two_leg_gross_r(2.0, 3.0)
        self.assertAlmostEqual(result, 2.5)

    def test_tp1_win_runner_breakeven(self):
        # TP1 at 2R leg-1, runner stopped at BE (0R) → weighted = 1.0R
        result = pe.compute_two_leg_gross_r(2.0, 0.0)
        self.assertAlmostEqual(result, 1.0)

    def test_tp1_win_runner_stopped(self):
        # TP1 at 2R, runner stopped at -1R → weighted = 0.5R
        result = pe.compute_two_leg_gross_r(2.0, -1.0)
        self.assertAlmostEqual(result, 0.5)

    def test_full_stop_both_legs(self):
        # Both stopped at -1R → -1R total (same as single leg)
        result = pe.compute_two_leg_gross_r(-1.0, -1.0)
        self.assertAlmostEqual(result, -1.0)

    def test_custom_weights(self):
        # 60/40 split
        result = pe.compute_two_leg_gross_r(2.0, 1.0, leg1_weight=0.6, leg2_weight=0.4)
        self.assertAlmostEqual(result, 1.6)

    def test_result_is_rounded(self):
        result = pe.compute_two_leg_gross_r(1.333333, 2.666667)
        # Should be rounded to 4 dp
        self.assertEqual(result, round(0.5*1.333333 + 0.5*2.666667, 4))

    def test_symmetric_weights_commutative(self):
        # 50/50: swapping legs gives same result
        self.assertAlmostEqual(
            pe.compute_two_leg_gross_r(2.0, 1.0),
            pe.compute_two_leg_gross_r(1.0, 2.0),
        )


# ---------------------------------------------------------------------------
# TestTwoLegEndToEnd — full R accounting with costs
# ---------------------------------------------------------------------------

class TestTwoLegEndToEnd(unittest.TestCase):
    """Verify that weighted gross_r − cost_r gives correct net_r for SCALP."""

    SPECS = {
        "MNQ": {"point_value": 2.0, "tick_size": 0.25},
    }

    def test_mnq_tp1_then_be_runner(self):
        """MNQ: entry 19000, stop 18990 (10pt risk, $20 risk).
        TP1 at 2R (19020), runner stopped at BE (19000, 0R).
        Weighted gross = 0.5*2R + 0.5*0R = 1.0R.
        Commission: ($1.24 + $0.50) / $20 ≈ 0.087R each side."""
        tp1_gross = pe.compute_gross_r("Long", 19000, 19020, 18990)
        runner_gross = pe.compute_gross_r("Long", 19000, 19000, 18990)
        weighted = pe.compute_two_leg_gross_r(tp1_gross, runner_gross)
        cost_r   = pe.compute_commission_r("MNQ", 19000, 18990, self.SPECS)
        self.assertAlmostEqual(tp1_gross,    2.0)
        self.assertAlmostEqual(runner_gross, 0.0)
        self.assertAlmostEqual(weighted,     1.0)
        self.assertIsNotNone(cost_r)
        net_r = weighted - cost_r
        # Net R should be below 1.0 (costs subtracted).
        # MNQ 10-pt stop: cost_$ = $1.24 comm + $1.00 slip = $2.24; risk_$ = $20 → cost_R ≈ 0.112R
        # So net_r ≈ 0.888R — must be in (0.8, 1.0)
        self.assertLess(net_r, 1.0)
        self.assertGreater(net_r, 0.8)

    def test_mnq_tp2_clean_both_legs(self):
        """TP1 at 2R, TP2 at 3R. Weighted = 2.5R."""
        tp1_gross = pe.compute_gross_r("Long", 19000, 19020, 18990)
        tp2_gross = pe.compute_gross_r("Long", 19000, 19030, 18990)
        weighted = pe.compute_two_leg_gross_r(tp1_gross, tp2_gross)
        self.assertAlmostEqual(weighted, 2.5)


# ---------------------------------------------------------------------------
# TestTwoLegIsolation — two-leg code must not affect single-leg
# ---------------------------------------------------------------------------

class TestTwoLegIsolation(unittest.TestCase):
    """Two-leg path must be byte-identical to existing single-leg when target2=None."""

    def test_single_leg_tp1_unchanged(self):
        status, reason, exit_px, gross_r = pe.resolve_bar_outcome(
            direction="Long",
            bar_high=2010.0, bar_low=1998.0,
            entry=2000.0, stop=1996.0,
            target1=2008.0, target2=None,
            tp1_hit=False, bars_held=2,
        )
        self.assertEqual(status, pe.STATUS_CLOSED)
        self.assertEqual(reason, pe.CLOSE_TP1)    # NOT partial
        self.assertEqual(exit_px, 2008.0)

    def test_tp1_already_true_single_leg_no_tp2(self):
        """tp1_hit=True but target2=None → should immediately expire or close on stop.
        (Shouldn't happen in practice but must not crash.)"""
        status, reason, exit_px, gross_r = pe.resolve_bar_outcome(
            direction="Long",
            bar_high=2005.0, bar_low=1997.0,
            entry=2000.0, stop=1996.0,
            target1=2008.0, target2=None,  # no TP2
            tp1_hit=True, bars_held=5,
        )
        # No TP2, not stopped, not expired → still open
        self.assertIsNone(status)

    def test_no_money_path_symbols_in_two_leg(self):
        src = pathlib.Path(__file__).parent.parent / "profitability_engine.py"
        code = src.read_text()
        forbidden = [
            "_maybe_auto_execute", "ACTIVE_TRADES", "traderspost",
            "ARM_STATE", "EXECUTION_MODE", "send_order", "place_order",
        ]
        for sym in forbidden:
            self.assertNotIn(sym, code, f"profitability_engine.py must not reference {sym!r}")


if __name__ == "__main__":
    unittest.main()
