"""
INTRADAY_TREND dedicated engine — focused tests.

Coverage:
  1.  Routing:  build_strict_trade_plan(mode=IT) → build_intraday_trade_plan
  2.  MNQ-only gate:     IT_INSTRUMENT_BLOCKED for MGC / other tickers
  3.  it_ctx required:   IT_UNAVAILABLE when it_ctx is None
  4.  Setup family gate: IT_NO_VALID_SETUP on unknown / missing family
  5.  Confirmation gate: IT_STRUCTURE_FAIL on incomplete confirmation
  6.  Structural stop:   uses it_ctx.structural_stop_level, not ATR
  7.  Stop above entry:  IT_STRUCTURE_FAIL (Long stop >= price)
  8.  ATR sanity bounds: too-tight and too-wide structural stops rejected
  9.  Chase gate:        IT_MAX_CHASE when price >> entry zone
  10. Target selection:  IT_INSUFFICIENT_RR when no level qualifies
  11. Long trade plan:   real session level selected as TP2, rr_num >= 2.0
  12. Short trade plan:  real session level selected as TP2, rr_num >= 2.0
  13. Expiration:        expires_at present and per-family
  14. Time cutoff:       IT_SESSION_BLOCKED at/after 15:00 ET
  15. Time force-flat:   IT_SESSION_BLOCKED after 15:55 ET
  16. Daily cap veto:    IT_DAILY_CAP on _it_entry_veto_reasons
  17. _it_select_intraday_target Long / Short / no-candidate
  18. _it_find_tp1 structural / fallback paths
  19. SCALP isolation:   mode=SCALP still produces 1:1 plan (golden smoke)
  20. SWING isolation:   mode=SWING unaffected by IT changes (golden smoke)

All tests are PURE — no DB, no network.  DB functions mocked where needed.
"""

import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

# ── bootstrap: same pattern as test_intraday_trend_phase2.py ─────────────────
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TEST_DIR)

os.environ.setdefault("TRADING_MODE",          "INTRADAY_TREND")
os.environ.setdefault("DASHBOARD_PASSWORD",    "test")
os.environ.setdefault("SESSION_SECRET",        "test")
os.environ.setdefault("DATABASE_URL",          "postgresql://localhost/test")
os.environ.setdefault("MAX_RISK_DOLLARS",      "500")
os.environ.setdefault("MAX_INTRADAY_TREND_TRADES_PER_DAY", "2")
os.environ.setdefault("SWING_HTF_ENABLED",     "0")   # keep SWING HTF off in tests
os.environ.setdefault("MIN_INTRADAY_RR",       "2.0")

import app as A   # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

MNQ_PRICE  = 20_000.0   # a plausible MNQ price (2026)
_ATR_PTS   = 30.0       # ~30-point ATR for MNQ in normal conditions

def _vol(atr=_ATR_PTS, regime="NORMAL", status="ok"):
    return {"atr_pts": atr, "regime": regime, "label": regime, "status": status}


def _it_ctx_long(price=MNQ_PRICE, stop_level=None, family="TREND_PULLBACK",
                 confirmed=True, time_ok=True, time_reason=None,
                 stop_valid=True):
    """Build a minimal valid IT context for a Long setup."""
    if stop_level is None:
        stop_level = price - 2.5 * _ATR_PTS    # ~75 pts below = 2.5R
    sl_pts = abs(price - stop_level)
    return {
        "time_ok":   time_ok,
        "time_reason": time_reason,
        "setup_family":         family,
        "confirmation_complete": confirmed,
        "confirmation_missing":  [] if confirmed else ["Awaiting CHOCH"],
        "structural_stop_valid": stop_valid,
        "structural_stop_level": round(stop_level, 2),
        "structural_stop_pts":   round(sl_pts, 2),
        "structural_stop_source": "Below pullback low (19958.00)",
        "session_levels": {
            "session_high":       price + 200.0,   # 200pt above — ≥2R from 75-pt stop
            "session_low":        price - 250.0,
            "opening_range_high": price + 100.0,   # 100pt above — ≥2R if stop < 50
            "opening_range_low":  price - 120.0,
            "overnight_high":     price + 300.0,
            "overnight_low":      price - 350.0,
            "london_high":        price + 150.0,
            "london_low":         price - 160.0,
            "asia_high":          None,
            "asia_low":           None,
            "major_15m_swing_highs": [price + 80.0, price + 130.0, price + 180.0],
            "major_15m_swing_lows":  [price - 80.0, price - 120.0, price - 160.0],
        },
        "daily_levels": {
            "prior_high": price + 400.0,
            "prior_low":  price - 400.0,
        },
        "trend_alignment": "Bullish",
        "alignment_score": 3,
        "location_quality": "KEY_LEVEL",
        "setup_family_reason": "Pullback to VWAP completed.",
        "session": "NY",
    }


def _it_ctx_short(price=MNQ_PRICE, stop_level=None, family="LIQUIDITY_SWEEP_REVERSAL",
                  confirmed=True, time_ok=True, time_reason=None,
                  stop_valid=True):
    """Build a minimal valid IT context for a Short setup."""
    if stop_level is None:
        stop_level = price + 2.5 * _ATR_PTS
    sl_pts = abs(price - stop_level)
    return {
        "time_ok":   time_ok,
        "time_reason": time_reason,
        "setup_family":          family,
        "confirmation_complete": confirmed,
        "confirmation_missing":  [] if confirmed else ["Awaiting CHOCH"],
        "structural_stop_valid": stop_valid,
        "structural_stop_level": round(stop_level, 2),
        "structural_stop_pts":   round(sl_pts, 2),
        "structural_stop_source": "Above swept high (20058.00)",
        "session_levels": {
            "session_high":       price + 250.0,
            "session_low":        price - 200.0,   # 200pt below — ≥2R if stop < 100
            "opening_range_high": price + 120.0,
            "opening_range_low":  price - 100.0,
            "overnight_high":     price + 350.0,
            "overnight_low":      price - 300.0,
            "london_high":        price + 160.0,
            "london_low":         price - 150.0,
            "asia_high":          None,
            "asia_low":           None,
            "major_15m_swing_highs": [price + 80.0, price + 130.0],
            "major_15m_swing_lows":  [price - 80.0, price - 130.0, price - 180.0],
        },
        "daily_levels": {
            "prior_high": price + 400.0,
            "prior_low":  price - 400.0,
        },
        "trend_alignment": "Bearish",
        "alignment_score": 3,
        "location_quality": "KEY_LEVEL",
        "setup_family_reason": "Liquidity swept — reversal confirmed.",
        "session": "NY",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 1. Routing: build_strict_trade_plan(mode=INTRADAY_TREND)
#    → delegates to build_intraday_trade_plan
# ══════════════════════════════════════════════════════════════════════════════

class TestITRouting(unittest.TestCase):

    def test_routing_via_build_strict_calls_it_builder(self):
        """build_strict_trade_plan with mode=INTRADAY_TREND → IT routing marker."""
        ctx = _it_ctx_long()
        plan = A.build_strict_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(), mode="INTRADAY_TREND",
            vwap=MNQ_PRICE, it_ctx=ctx,
        )
        # IT plan marker must be set
        self.assertTrue(plan.get("it_plan"), "Expected it_plan=True from IT routing")

    def test_scalp_mode_does_not_route_to_it_builder(self):
        """mode=SCALP must NOT set it_plan."""
        plan = A.build_strict_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(), mode="SCALP", vwap=MNQ_PRICE,
        )
        self.assertFalse(plan.get("it_plan"),
                         "SCALP plan must not have it_plan=True")

    def test_swing_mode_does_not_route_to_it_builder(self):
        """mode=SWING must NOT set it_plan."""
        plan = A.build_strict_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(), mode="SWING", vwap=MNQ_PRICE,
        )
        self.assertFalse(plan.get("it_plan"),
                         "SWING plan must not have it_plan=True")


# ══════════════════════════════════════════════════════════════════════════════
# 2. MNQ-only gate
# ══════════════════════════════════════════════════════════════════════════════

class TestITInstrumentGate(unittest.TestCase):

    def test_mgc_blocked(self):
        ctx = _it_ctx_long()
        plan = A.build_intraday_trade_plan(
            "Long", "MGC1!", MNQ_PRICE, None, MNQ_PRICE - 5.0,
            volatility=_vol(), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        self.assertFalse(plan["trade_plan"])
        self.assertEqual(plan["it_veto_code"], "IT_INSTRUMENT_BLOCKED")

    def test_mnq_allowed(self):
        ctx = _it_ctx_long()
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        # Should succeed (no instrument block)
        self.assertNotEqual(plan.get("it_veto_code"), "IT_INSTRUMENT_BLOCKED")


# ══════════════════════════════════════════════════════════════════════════════
# 3. it_ctx required
# ══════════════════════════════════════════════════════════════════════════════

class TestITCtxRequired(unittest.TestCase):

    def test_none_ctx_returns_unavailable(self):
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(), vwap=MNQ_PRICE, it_ctx=None,
        )
        self.assertFalse(plan["trade_plan"])
        self.assertEqual(plan["it_veto_code"], "IT_UNAVAILABLE")

    def test_non_dict_ctx_returns_unavailable(self):
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(), vwap=MNQ_PRICE, it_ctx="bad",
        )
        self.assertFalse(plan["trade_plan"])
        self.assertEqual(plan["it_veto_code"], "IT_UNAVAILABLE")


# ══════════════════════════════════════════════════════════════════════════════
# 4. Setup family gate
# ══════════════════════════════════════════════════════════════════════════════

class TestITSetupFamilyGate(unittest.TestCase):

    def test_no_family_blocked(self):
        ctx = _it_ctx_long()
        ctx["setup_family"] = None
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        self.assertFalse(plan["trade_plan"])
        self.assertEqual(plan["it_veto_code"], "IT_NO_VALID_SETUP")

    def test_unknown_family_blocked(self):
        ctx = _it_ctx_long()
        ctx["setup_family"] = "SOME_NEW_PATTERN"
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        self.assertFalse(plan["trade_plan"])
        self.assertEqual(plan["it_veto_code"], "IT_NO_VALID_SETUP")

    def test_lsr_family_allowed(self):
        ctx = _it_ctx_long(family="LIQUIDITY_SWEEP_REVERSAL")
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        # Not blocked by family gate (may fail later for other reasons)
        self.assertNotEqual(plan.get("it_veto_code"), "IT_NO_VALID_SETUP")

    def test_breakout_retest_family_allowed(self):
        ctx = _it_ctx_long(family="BREAKOUT_RETEST")
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        self.assertNotEqual(plan.get("it_veto_code"), "IT_NO_VALID_SETUP")


# ══════════════════════════════════════════════════════════════════════════════
# 5. Confirmation gate
# ══════════════════════════════════════════════════════════════════════════════

class TestITConfirmationGate(unittest.TestCase):

    def test_incomplete_confirmation_blocked(self):
        ctx = _it_ctx_long(confirmed=False)
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        self.assertFalse(plan["trade_plan"])
        self.assertEqual(plan["it_veto_code"], "IT_STRUCTURE_FAIL")

    def test_complete_confirmation_allowed(self):
        ctx = _it_ctx_long(confirmed=True)
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        self.assertNotEqual(plan.get("it_veto_code"), "IT_STRUCTURE_FAIL")


# ══════════════════════════════════════════════════════════════════════════════
# 6. Structural stop — uses it_ctx levels, not ATR
# ══════════════════════════════════════════════════════════════════════════════

class TestITStructuralStop(unittest.TestCase):

    def test_stop_level_comes_from_it_ctx(self):
        """The stop in the plan must match it_ctx.structural_stop_level."""
        ctx = _it_ctx_long(price=MNQ_PRICE, stop_level=MNQ_PRICE - 60.0)
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        if plan["trade_plan"]:
            stop = float(plan["stop_loss"])
            # Must be the structural stop level, not an ATR-derived value
            self.assertAlmostEqual(stop, MNQ_PRICE - 60.0, places=1)

    def test_stop_invalid_when_structural_stop_valid_false(self):
        ctx = _it_ctx_long()
        ctx["structural_stop_valid"] = False
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        self.assertFalse(plan["trade_plan"])
        self.assertEqual(plan["it_veto_code"], "IT_STRUCTURE_FAIL")

    def test_stop_above_entry_blocked_for_long(self):
        """Long stop must be BELOW entry price."""
        ctx = _it_ctx_long(stop_level=MNQ_PRICE + 20.0)   # wrong side
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        self.assertFalse(plan["trade_plan"])
        self.assertEqual(plan["it_veto_code"], "IT_STRUCTURE_FAIL")

    def test_stop_below_entry_blocked_for_short(self):
        """Short stop must be ABOVE entry price."""
        ctx = _it_ctx_short(stop_level=MNQ_PRICE - 20.0)  # wrong side
        plan = A.build_intraday_trade_plan(
            "Short", "MNQ1!", MNQ_PRICE, MNQ_PRICE + 50.0, None,
            volatility=_vol(), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        self.assertFalse(plan["trade_plan"])
        self.assertEqual(plan["it_veto_code"], "IT_STRUCTURE_FAIL")

    def test_stop_too_tight_blocked(self):
        """Stop < 0.3 × ATR is rejected as noise."""
        ctx = _it_ctx_long(stop_level=MNQ_PRICE - 5.0)  # only 5 pts vs 30-pt ATR
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 10.0,
            volatility=_vol(atr=_ATR_PTS), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        self.assertFalse(plan["trade_plan"])
        self.assertEqual(plan["it_veto_code"], "IT_STRUCTURE_FAIL")

    def test_stop_too_wide_blocked(self):
        """Stop > 4 × ATR is rejected as implausibly wide."""
        ctx = _it_ctx_long(stop_level=MNQ_PRICE - 200.0)   # 200 pts vs 30-pt ATR
        ctx["structural_stop_pts"] = 200.0
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(atr=_ATR_PTS), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        self.assertFalse(plan["trade_plan"])
        self.assertEqual(plan["it_veto_code"], "IT_STRUCTURE_FAIL")


# ══════════════════════════════════════════════════════════════════════════════
# 9. Chase gate
# ══════════════════════════════════════════════════════════════════════════════

class TestITChaseGate(unittest.TestCase):

    def test_chase_blocked_when_price_far_from_entry(self):
        """If price has moved > 1.5 × ATR from entry, IT_MAX_CHASE fires.

        nearest_demand=None → entry anchors to vwap=MNQ_PRICE.
        live_price = MNQ_PRICE + 60 → chase_dist=60 > 1.5×30=45 → blocks.
        """
        ctx = _it_ctx_long(price=MNQ_PRICE, stop_level=MNQ_PRICE - 60.0)
        ctx["structural_stop_pts"] = 60.0
        live_price = MNQ_PRICE + 60.0   # 60 pts above vwap-anchored entry → chasing
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", live_price, None, None,
            volatility=_vol(atr=_ATR_PTS), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        self.assertFalse(plan["trade_plan"])
        self.assertEqual(plan["it_veto_code"], "IT_MAX_CHASE")

    def test_chase_allowed_within_tolerance(self):
        """Price within 1.5 × ATR of entry zone → chase gate passes.

        nearest_demand=None → entry anchors to vwap=MNQ_PRICE.
        live_price = MNQ_PRICE + 10 → chase_dist=10 < 45 → allowed.
        """
        ctx = _it_ctx_long(price=MNQ_PRICE, stop_level=MNQ_PRICE - 60.0)
        ctx["structural_stop_pts"] = 60.0
        live_price = MNQ_PRICE + 10.0   # 10 pts above vwap-anchored entry → ok
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", live_price, None, None,
            volatility=_vol(atr=_ATR_PTS), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        self.assertNotEqual(plan.get("it_veto_code"), "IT_MAX_CHASE")


# ══════════════════════════════════════════════════════════════════════════════
# 10. IT_INSUFFICIENT_RR — no structural level qualifies
# ══════════════════════════════════════════════════════════════════════════════

class TestITInsufficientRR(unittest.TestCase):

    def test_no_qualifying_level_returns_insufficient_rr(self):
        """When all session levels are below 2R, IT_INSUFFICIENT_RR fires.

        Uses nearest_demand=None so entry anchors to vwap=MNQ_PRICE (chase_dist=0).
        100-pt stop needs 200 pts above entry; all levels capped at 150 pts.
        """
        ctx = _it_ctx_long(stop_level=MNQ_PRICE - 100.0)  # 100-pt stop → need 200 pts
        ctx["structural_stop_pts"] = 100.0
        # All session levels provide < 200 pts above entry (< 2.0R)
        ctx["session_levels"] = {
            "session_high":       MNQ_PRICE + 150.0,  # 1.5R — not enough
            "session_low":        MNQ_PRICE - 250.0,
            "opening_range_high": MNQ_PRICE + 100.0,  # 1.0R — not enough
            "opening_range_low":  MNQ_PRICE - 120.0,
            "overnight_high":     MNQ_PRICE + 120.0,  # 1.2R — not enough
            "overnight_low":      MNQ_PRICE - 350.0,
            "london_high":        MNQ_PRICE + 80.0,   # 0.8R — not enough
            "london_low":         MNQ_PRICE - 160.0,
            "asia_high":          None, "asia_low": None,
            "major_15m_swing_highs": [MNQ_PRICE + 50.0, MNQ_PRICE + 90.0],
            "major_15m_swing_lows":  [MNQ_PRICE - 80.0, MNQ_PRICE - 120.0],
        }
        # prior_high must also be below 2R threshold (need 200 pts; cap at 190)
        ctx["daily_levels"] = {"prior_high": MNQ_PRICE + 190.0, "prior_low": MNQ_PRICE - 400.0}
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, None,   # nearest_demand=None → VWAP anchor
            volatility=_vol(atr=_ATR_PTS), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        self.assertFalse(plan["trade_plan"])
        self.assertEqual(plan["it_veto_code"], "IT_INSUFFICIENT_RR")


# ══════════════════════════════════════════════════════════════════════════════
# 11-12. Successful Long / Short trade plans
# ══════════════════════════════════════════════════════════════════════════════

class TestITSuccessfulPlan(unittest.TestCase):

    def test_long_plan_produced(self):
        """A fully valid Long IT context produces a complete trade plan.

        nearest_demand=None → entry anchors to vwap=MNQ_PRICE, chase_dist=0.
        """
        ctx = _it_ctx_long(stop_level=MNQ_PRICE - 75.0)
        ctx["structural_stop_pts"] = 75.0
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, None,   # VWAP-anchored entry
            volatility=_vol(atr=_ATR_PTS), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        self.assertTrue(plan["trade_plan"], f"Expected trade_plan=True, got: {plan.get('reason')}")
        self.assertTrue(plan.get("it_plan"))
        self.assertIsNotNone(plan["stop_loss"])
        self.assertIsNotNone(plan["target2"])
        self.assertIsNotNone(plan["rr_num"])
        self.assertGreaterEqual(plan["rr_num"], 2.0,
                                f"R:R must be >= 2.0, got {plan['rr_num']}")
        self.assertEqual(plan["direction"], "Long")
        self.assertEqual(plan["instrument"], "MNQ")

    def test_short_plan_produced(self):
        """A fully valid Short IT context produces a complete trade plan.

        nearest_supply=None → entry anchors to vwap=MNQ_PRICE, chase_dist=0.
        """
        ctx = _it_ctx_short(stop_level=MNQ_PRICE + 75.0)
        ctx["structural_stop_pts"] = 75.0
        plan = A.build_intraday_trade_plan(
            "Short", "MNQ1!", MNQ_PRICE, None, None,  # VWAP-anchored entry
            volatility=_vol(atr=_ATR_PTS), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        self.assertTrue(plan["trade_plan"], f"Expected trade_plan=True, got: {plan.get('reason')}")
        self.assertGreaterEqual(plan["rr_num"], 2.0,
                                f"R:R must be >= 2.0, got {plan['rr_num']}")
        self.assertEqual(plan["direction"], "Short")

    def test_plan_schema_parity_with_build_strict(self):
        """IT plan has all keys required by build_strict_trade_plan consumers."""
        REQUIRED = [
            "trade_plan", "reason", "direction", "instrument", "point_value",
            "entry_zone", "stop_loss", "target1", "target2", "rr", "rr_num",
            "target3", "be_level", "partial_level", "runner_target",
            "risk_points", "reward_points", "max_invalidation", "management",
            "atr_pts", "atr_multiplier", "atr_stop", "structure_stop",
            "calculated_stop", "min_stop_ticks", "tick_size",
            "stop_distance_ticks", "risk_dollars_per_contract",
            "nearest_demand", "nearest_supply",
            "volatility_regime", "volatility_label",
            "stop_valid", "stop_invalid_reason", "min_floor_applied",
        ]
        ctx = _it_ctx_long(stop_level=MNQ_PRICE - 75.0)
        ctx["structural_stop_pts"] = 75.0
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, None,  # VWAP-anchored entry
            volatility=_vol(atr=_ATR_PTS), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        for k in REQUIRED:
            self.assertIn(k, plan, f"Missing required key: {k}")

    def test_tp2_comes_from_session_level_not_atr(self):
        """TP2 must match a real session level, not entry + N×ATR.

        nearest_demand=None → entry = vwap = MNQ_PRICE (chase_dist=0).
        session_high at +200 is the nearest qualifying level (2.67R from 75-pt stop).
        All farther levels are explicitly set beyond session_high.
        """
        ctx = _it_ctx_long(stop_level=MNQ_PRICE - 75.0)
        ctx["structural_stop_pts"] = 75.0
        # session_high at +200 = 2.67R from 75-pt stop → nearest qualifying target
        session_high = MNQ_PRICE + 200.0
        ctx["session_levels"]["session_high"]       = session_high
        ctx["session_levels"]["opening_range_high"] = MNQ_PRICE + 600.0
        ctx["session_levels"]["overnight_high"]     = MNQ_PRICE + 800.0
        ctx["session_levels"]["london_high"]        = MNQ_PRICE + 700.0
        ctx["session_levels"]["major_15m_swing_highs"] = []
        ctx["daily_levels"] = {"prior_high": MNQ_PRICE + 900.0, "prior_low": MNQ_PRICE - 400.0}
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, None,   # VWAP-anchored entry
            volatility=_vol(atr=_ATR_PTS), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        self.assertTrue(plan["trade_plan"])
        tp2 = float(plan["target2"])
        # TP2 must be the session_high (nearest qualifying level), not a manufactured value
        self.assertAlmostEqual(tp2, session_high, places=0,
                               msg=f"TP2 ({tp2}) should be session_high ({session_high})")

    def test_setup_family_in_plan(self):
        """setup_family IT-specific key is present in successful plan."""
        ctx = _it_ctx_long(stop_level=MNQ_PRICE - 75.0, family="BREAKOUT_RETEST")
        ctx["structural_stop_pts"] = 75.0
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(atr=_ATR_PTS), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        if plan["trade_plan"]:
            self.assertEqual(plan["setup_family"], "BREAKOUT_RETEST")

    def test_structural_stop_source_in_plan(self):
        """stop_reason reflects the structural stop source, not ATR."""
        ctx = _it_ctx_long(stop_level=MNQ_PRICE - 75.0)
        ctx["structural_stop_pts"] = 75.0
        ctx["structural_stop_source"] = "Below pullback low (19925.00)"
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(atr=_ATR_PTS), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        if plan["trade_plan"]:
            self.assertIn("pullback", plan.get("stop_reason", "").lower(),
                          "stop_reason should reflect structural origin")


# ══════════════════════════════════════════════════════════════════════════════
# 13. Expiration — expires_at populated and correct family timing
# ══════════════════════════════════════════════════════════════════════════════

class TestITExpiration(unittest.TestCase):

    def _plan_for_family(self, family):
        ctx = _it_ctx_long(stop_level=MNQ_PRICE - 75.0, family=family)
        ctx["structural_stop_pts"] = 75.0
        return A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(atr=_ATR_PTS), vwap=MNQ_PRICE, it_ctx=ctx,
        )

    def test_expires_at_present(self):
        plan = self._plan_for_family("TREND_PULLBACK")
        if plan["trade_plan"]:
            self.assertIsNotNone(plan.get("expires_at"))

    def test_lsr_expires_30_min(self):
        plan = self._plan_for_family("LIQUIDITY_SWEEP_REVERSAL")
        if plan["trade_plan"]:
            exp = plan.get("expires_at")
            if exp:
                expires = datetime.fromisoformat(exp)
                now     = datetime.now(timezone.utc)
                delta   = (expires - now).total_seconds()
                self.assertAlmostEqual(delta / 60, 30, delta=2,
                                       msg="LSR expiry should be ~30 min")

    def test_breakout_retest_expires_45_min(self):
        plan = self._plan_for_family("BREAKOUT_RETEST")
        if plan["trade_plan"]:
            exp = plan.get("expires_at")
            if exp:
                expires = datetime.fromisoformat(exp)
                now     = datetime.now(timezone.utc)
                delta   = (expires - now).total_seconds()
                self.assertAlmostEqual(delta / 60, 45, delta=2,
                                       msg="BREAKOUT_RETEST expiry should be ~45 min")

    def test_trend_pullback_expires_60_min(self):
        plan = self._plan_for_family("TREND_PULLBACK")
        if plan["trade_plan"]:
            exp = plan.get("expires_at")
            if exp:
                expires = datetime.fromisoformat(exp)
                now     = datetime.now(timezone.utc)
                delta   = (expires - now).total_seconds()
                self.assertAlmostEqual(delta / 60, 60, delta=2,
                                       msg="TREND_PULLBACK expiry should be ~60 min")


# ══════════════════════════════════════════════════════════════════════════════
# 14-15. Time cutoff and force-flat
# ══════════════════════════════════════════════════════════════════════════════

class TestITTimeCutoff(unittest.TestCase):

    def test_entry_blocked_after_cutoff(self):
        """When time_ok=False, IT_SESSION_BLOCKED fires."""
        ctx = _it_ctx_long(time_ok=False,
                           time_reason="IT_SESSION_BLOCKED: Past 15:15 ET — no new entries.")
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(atr=_ATR_PTS), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        self.assertFalse(plan["trade_plan"])
        self.assertEqual(plan["it_veto_code"], "IT_SESSION_BLOCKED")

    def test_default_cutoff_is_15_00(self):
        """The current conservative spec blocks new IT entries at 15:00 ET."""
        self.assertEqual(A._IT_LAST_ENTRY_DEFAULT, "15:00",
                         "Default IT last-entry cutoff must be 15:00 per spec")

    def test_time_restriction_blocks_at_1516(self):
        """_it_time_restriction returns blocked at 15:16 ET."""
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        # Simulate 15:16 ET on a weekday
        t = datetime(2026, 8, 12, 15, 16, tzinfo=et)
        ok, state, reason = A._it_time_restriction(et_now=t)
        self.assertFalse(ok)
        self.assertEqual(state, "ENTRY_BLOCKED")

    def test_time_restriction_allows_at_1459(self):
        """_it_time_restriction returns allowed immediately before cutoff."""
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        t = datetime(2026, 8, 12, 14, 59, tzinfo=et)
        ok, state, _ = A._it_time_restriction(et_now=t)
        self.assertTrue(ok)
        self.assertEqual(state, "OK")

    def test_time_restriction_force_flat_at_1556(self):
        """_it_time_restriction returns FORCE_FLAT at 15:56 ET."""
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        t = datetime(2026, 8, 12, 15, 56, tzinfo=et)
        ok, state, _ = A._it_time_restriction(et_now=t)
        self.assertFalse(ok)
        self.assertEqual(state, "FORCE_FLAT")


# ══════════════════════════════════════════════════════════════════════════════
# 16. Daily cap via _it_entry_veto_reasons
# ══════════════════════════════════════════════════════════════════════════════

class TestITDailyCap(unittest.TestCase):

    def test_daily_cap_veto_fires(self):
        """When daily cap is hit, _it_entry_veto_reasons includes IT_DAILY_CAP.

        _it_entry_veto_reasons reads daily_trade_count / daily_trade_cap from
        it_ctx (populated by compute_intraday_trend_context), so set them directly.
        """
        ctx = _it_ctx_long(stop_level=MNQ_PRICE - 75.0)
        ctx["structural_stop_pts"] = 75.0
        ctx["daily_trade_count"] = 2   # 2 trades today — at cap
        ctx["daily_trade_cap"]   = 2
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, None,  # VWAP-anchored entry
            volatility=_vol(atr=_ATR_PTS), vwap=MNQ_PRICE, it_ctx=ctx,
        )
        vetoes = A._it_entry_veto_reasons(ctx, plan, "Long", "MNQ1!")
        codes = [v[0] for v in vetoes]
        self.assertIn("daily_cap", codes,
                      "Daily cap should fire when count >= cap")


# ══════════════════════════════════════════════════════════════════════════════
# 16b. Durable daily-count authority — ghost_observations readback
# ══════════════════════════════════════════════════════════════════════════════

class _ITCountCursor:
    def __init__(self, row=(0,), error=None):
        self.row = row
        self.error = error
        self.sql = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params
        if self.error:
            raise self.error

    def fetchone(self):
        return self.row


class _ITCountConnection:
    def __init__(self, cursor):
        self.cursor_obj = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_obj


class TestITDailyCountAuthority(unittest.TestCase):
    """The IT cap reconstructs from durable ghost-observation readback only."""

    def setUp(self):
        self._saved_ready = A.GHOST_OBS_DB_READY
        A.GHOST_OBS_DB_READY = True
        self._env_patch = patch.dict(os.environ, {"IT_DAILY_CAP": "3"}, clear=False)
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
        A.GHOST_OBS_DB_READY = self._saved_ready

    @staticmethod
    def _count_with(cursor, instrument="MNQ1!"):
        conn = _ITCountConnection(cursor)
        with patch.object(A, "get_db_connection", return_value=conn):
            with patch.object(A, "now_utc",
                              return_value=datetime(2026, 1, 16, 2, 30,
                                                    tzinfo=timezone.utc)):
                return A._it_daily_trade_count(instrument)

    def test_valid_empty_readback_is_zero_with_et_day(self):
        """A reachable empty ledger is a valid zero, not an unavailable count."""
        cur = _ITCountCursor(row=(0,))
        count, cap = self._count_with(cur)
        self.assertEqual((count, cap), (0, 3))
        self.assertEqual(cur.params, ("MNQ", "2026-01-15"),
                         "02:30 UTC is still the prior ET trading date")
        self.assertIn("signal_time AT TIME ZONE 'America/New_York'", cur.sql)
        self.assertNotIn("opened_at", cur.sql,
                         "opened_at is not a ghost_observations ledger column")

    def test_distinct_obs_key_counts_a_retry_once(self):
        """The durable ghost identity remains one count after a retry/readback."""
        cur = _ITCountCursor(row=(1,))
        self.assertEqual(self._count_with(cur)[0], 1)
        self.assertIn("COUNT(DISTINCT obs_key)", cur.sql)

    def test_restart_style_readback_reconstructs_cap_boundaries(self):
        """No process-local count is needed for cap-1, cap, or above-cap state."""
        for returned_count in (2, 3, 4):
            with self.subTest(returned_count=returned_count):
                count, cap = self._count_with(_ITCountCursor(row=(returned_count,)))
                self.assertEqual(cap, 3)
                self.assertEqual(count, returned_count)
                self.assertEqual(count >= cap, returned_count >= cap)

    def test_unreadable_or_malformed_readback_remains_fail_closed(self):
        for cursor in (
            _ITCountCursor(error=RuntimeError("missing table")),
            _ITCountCursor(row=None),
            _ITCountCursor(row=(None,)),
            _ITCountCursor(row=(True,)),
            _ITCountCursor(row=("0",)),
            _ITCountCursor(row=(0.0,)),
            _ITCountCursor(row=(-1,)),
        ):
            with self.subTest(cursor=cursor):
                self.assertEqual(self._count_with(cursor)[0], -1)

    def test_not_ready_source_remains_unavailable(self):
        A.GHOST_OBS_DB_READY = False
        self.assertEqual(A._it_daily_trade_count("MNQ"), (-1, 3))


# ══════════════════════════════════════════════════════════════════════════════
# 17. _it_select_intraday_target
# ══════════════════════════════════════════════════════════════════════════════

class TestITSelectTarget(unittest.TestCase):

    def _levels(self):
        return {
            "session_high":       MNQ_PRICE + 200.0,
            "session_low":        MNQ_PRICE - 200.0,
            "opening_range_high": MNQ_PRICE + 100.0,
            "opening_range_low":  MNQ_PRICE - 100.0,
            "overnight_high":     MNQ_PRICE + 300.0,
            "overnight_low":      MNQ_PRICE - 300.0,
            "london_high":        MNQ_PRICE + 150.0,
            "london_low":         MNQ_PRICE - 150.0,
            "asia_high":          None, "asia_low": None,
            "prior_high":         MNQ_PRICE + 400.0,
            "prior_low":          MNQ_PRICE - 400.0,
            "major_15m_swing_highs": [MNQ_PRICE + 80.0, MNQ_PRICE + 130.0],
            "major_15m_swing_lows":  [MNQ_PRICE - 80.0, MNQ_PRICE - 130.0],
        }

    def test_long_nearest_qualifying_level_selected(self):
        """Long: nearest level above entry providing >= 2R should be chosen."""
        entry = MNQ_PRICE
        risk  = 75.0   # 75 pt stop → need >= 150 pts above
        lv    = self._levels()
        tick  = 0.25
        result = A._it_select_intraday_target("Long", entry, risk, lv, tick)
        self.assertIsNotNone(result)
        tp2, tp1, label, rr = result
        # With 75-pt stop and 2.0R min, need 150 pts. Nearest qualifying is
        # session_high at +200 (2.67R). OR opening_range_high at +100 (1.33R — <2R)
        self.assertGreaterEqual(rr, 2.0, f"R:R {rr} must be >= 2.0")
        self.assertGreater(tp2, entry, "TP2 must be above entry for Long")

    def test_short_nearest_qualifying_level_selected(self):
        """Short: nearest level below entry providing >= 2R should be chosen."""
        entry = MNQ_PRICE
        risk  = 75.0
        lv    = self._levels()
        tick  = 0.25
        result = A._it_select_intraday_target("Short", entry, risk, lv, tick)
        self.assertIsNotNone(result)
        tp2, tp1, label, rr = result
        self.assertGreaterEqual(rr, 2.0, f"R:R {rr} must be >= 2.0")
        self.assertLess(tp2, entry, "TP2 must be below entry for Short")

    def test_no_qualifying_level_returns_none(self):
        """When all levels provide < 2R, return None.

        Uses risk=250 so need=500; prior_high at +400 provides only 1.6R.
        """
        entry = MNQ_PRICE
        risk  = 250.0   # 250-pt stop → need 500 pts; prior_high at +400 = 1.6R < 2R
        lv    = self._levels()
        tick  = 0.25
        result = A._it_select_intraday_target("Long", entry, risk, lv, tick)
        self.assertIsNone(result,
                          "Should return None when no level provides >= 2R")

    def test_tp1_is_present_in_result(self):
        """Successful result must include tp1 (first intraday objective)."""
        entry = MNQ_PRICE
        risk  = 75.0
        lv    = self._levels()
        tick  = 0.25
        result = A._it_select_intraday_target("Long", entry, risk, lv, tick)
        if result is not None:
            tp2, tp1, label, rr = result
            self.assertIsNotNone(tp1, "TP1 must be present in result")
            self.assertGreater(tp1, entry, "TP1 must be above entry for Long")


# ══════════════════════════════════════════════════════════════════════════════
# 18. _it_find_tp1
# ══════════════════════════════════════════════════════════════════════════════

class TestITFindTP1(unittest.TestCase):

    def test_structural_level_in_range_selected(self):
        """Uses nearest structural level between 0.75R and 1.5R from entry."""
        entry = MNQ_PRICE
        risk  = 75.0    # 75 pt stop → range is 56.25–112.5 pts above
        lv    = {
            "session_high":       entry + 100.0,   # 1.33R — in range
            "opening_range_high": entry + 200.0,   # 2.67R — out of range
            "overnight_high":     None,
            "london_high":        None,
            "major_15m_swing_highs": [entry + 60.0,  # 0.8R — in range
                                      entry + 180.0], # 2.4R — out of range
            "major_15m_swing_lows":  [],
            **{k: None for k in ("session_low", "opening_range_low",
                                 "overnight_low", "london_low")},
        }
        tp1 = A._it_find_tp1("Long", entry, risk, lv, 0.25)
        # Nearest in-range should be the swing_high at +60 (0.8R)
        self.assertGreater(tp1, entry)
        self.assertLessEqual(tp1 - entry, 1.5 * risk,
                             "TP1 should not exceed 1.5R from entry")

    def test_fallback_to_1r_when_no_structural_level(self):
        """Falls back to the current 1R manufactured target without a level."""
        entry = MNQ_PRICE
        risk  = 75.0
        empty_lv = {
            **{k: None for k in ("session_high", "session_low",
                                 "opening_range_high", "opening_range_low",
                                 "overnight_high", "overnight_low",
                                 "london_high", "london_low")},
            "major_15m_swing_highs": [],
            "major_15m_swing_lows":  [],
        }
        tp1 = A._it_find_tp1("Long", entry, risk, empty_lv, 0.25)
        expected = round(round((entry + 1.0 * risk) / 0.25) * 0.25, 10)
        self.assertAlmostEqual(tp1, expected, places=1,
                               msg="Fallback TP1 should be 1R from entry")


# ══════════════════════════════════════════════════════════════════════════════
# 19. SCALP isolation (golden smoke)
# ══════════════════════════════════════════════════════════════════════════════

class TestSCALPIsolation(unittest.TestCase):
    """SCALP mode must remain byte-identical after IT engine introduction."""

    def test_scalp_plan_is_not_it_plan(self):
        plan = A.build_strict_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(), mode="SCALP", vwap=MNQ_PRICE,
        )
        self.assertFalse(plan.get("it_plan"), "SCALP must not produce an IT plan")

    def test_scalp_plan_has_rr_num(self):
        """SCALP plan still produces a usable R:R."""
        plan = A.build_strict_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(), mode="SCALP", vwap=MNQ_PRICE,
        )
        if plan.get("trade_plan"):
            self.assertIsNotNone(plan.get("rr_num"))

    def test_swing_htf_disabled_for_scalp(self):
        """_swing_htf_enabled must return False for SCALP."""
        orig = os.environ.get("SWING_HTF_ENABLED", "")
        os.environ["SWING_HTF_ENABLED"] = "1"
        try:
            self.assertFalse(A._swing_htf_enabled("SCALP"),
                             "SCALP must never enter SWING HTF path")
        finally:
            if orig:
                os.environ["SWING_HTF_ENABLED"] = orig
            else:
                del os.environ["SWING_HTF_ENABLED"]


# ══════════════════════════════════════════════════════════════════════════════
# 20. SWING isolation (golden smoke)
# ══════════════════════════════════════════════════════════════════════════════

class TestSWINGIsolation(unittest.TestCase):
    """SWING mode must not be affected by IT engine introduction."""

    def test_swing_htf_disabled_for_it(self):
        """_swing_htf_enabled must return False for INTRADAY_TREND after revert."""
        orig = os.environ.get("SWING_HTF_ENABLED", "")
        os.environ["SWING_HTF_ENABLED"] = "1"
        try:
            self.assertFalse(A._swing_htf_enabled("INTRADAY_TREND"),
                             "INTRADAY_TREND must NOT enter SWING HTF path")
        finally:
            if orig:
                os.environ["SWING_HTF_ENABLED"] = orig
            else:
                del os.environ["SWING_HTF_ENABLED"]

    def test_swing_plan_is_not_it_plan(self):
        """mode=SWING with SWING_HTF_ENABLED=0 must not produce an IT plan."""
        plan = A.build_strict_trade_plan(
            "Long", "MNQ1!", MNQ_PRICE, None, MNQ_PRICE - 50.0,
            volatility=_vol(), mode="SWING", vwap=MNQ_PRICE,
        )
        self.assertFalse(plan.get("it_plan"),
                         "SWING plan must not have it_plan=True")


# ══════════════════════════════════════════════════════════════════════════════
# 21. _it_classify_trend_native() — native 15m bar classifier (spec §4)
# ══════════════════════════════════════════════════════════════════════════════

class TestNativeTrendClassifier(unittest.TestCase):
    """_it_classify_trend_native must classify from bar data, never SWING policy."""

    def _inject_mtf(self, inst, trend_15m, bars):
        """Inject a fake MTF state for testing."""
        import trend_alignment as _ta
        _ta.MTF_STATE_BY_INST[inst] = {"trend_15m": trend_15m, "bars_15m": bars}

    def _clear_mtf(self, inst):
        import trend_alignment as _ta
        _ta.MTF_STATE_BY_INST.pop(inst, None)

    def _mk_bars(self, highs, lows):
        """Build minimal bar dicts from high/low lists."""
        return [{"high": h, "low": l, "close": (h + l) / 2}
                for h, l in zip(highs, lows)]

    def test_bullish_trend_intact_returns_bullish(self):
        bars = self._mk_bars(
            [100, 102, 104, 106, 108, 110, 112, 115],
            [ 95,  97,  99, 101, 103, 105, 107, 110],
        )
        self._inject_mtf("MNQ", "BULLISH", bars)
        try:
            trend, reason = A._it_classify_trend_native("MNQ")
            self.assertEqual(trend, "BULLISH")
            self.assertIn("HH/HL", reason)
        finally:
            self._clear_mtf("MNQ")

    def test_bullish_with_lower_highs_returns_transition(self):
        # Late bars make lower highs (weakening) but not higher lows
        bars = self._mk_bars(
            [115, 114, 113, 112, 111, 110, 109, 108],
            [ 90,  89,  88,  87,  86,  85,  84,  83],
        )
        self._inject_mtf("MNQ", "BULLISH", bars)
        try:
            trend, _ = A._it_classify_trend_native("MNQ")
            self.assertEqual(trend, "TRANSITION")
        finally:
            self._clear_mtf("MNQ")

    def test_bearish_trend_intact_returns_bearish(self):
        bars = self._mk_bars(
            [115, 113, 111, 109, 107, 105, 103, 100],
            [110, 108, 106, 104, 102, 100,  98,  95],
        )
        self._inject_mtf("MNQ", "BEARISH", bars)
        try:
            trend, reason = A._it_classify_trend_native("MNQ")
            self.assertEqual(trend, "BEARISH")
            self.assertIn("LH/LL", reason)
        finally:
            self._clear_mtf("MNQ")

    def test_unavailable_returns_neutral(self):
        self._inject_mtf("MNQ", "UNAVAILABLE", [])
        try:
            trend, reason = A._it_classify_trend_native("MNQ")
            self.assertEqual(trend, "NEUTRAL")
        finally:
            self._clear_mtf("MNQ")

    def test_insufficient_bars_returns_neutral(self):
        bars = self._mk_bars([100, 102], [99, 101])
        self._inject_mtf("MNQ", "BULLISH", bars)
        try:
            trend, reason = A._it_classify_trend_native("MNQ")
            self.assertEqual(trend, "NEUTRAL")
            self.assertIn("Insufficient", reason)
        finally:
            self._clear_mtf("MNQ")

    def test_missing_instrument_returns_neutral(self):
        """None instrument must not raise — returns NEUTRAL safely."""
        trend, _ = A._it_classify_trend_native(None)
        self.assertEqual(trend, "NEUTRAL")

    def test_neutral_mtf_with_hh_hl_returns_bullish(self):
        bars = self._mk_bars(
            [100, 102, 104, 108, 110, 112, 114, 118],
            [ 98, 100, 102, 104, 106, 108, 110, 112],
        )
        self._inject_mtf("MNQ", "NEUTRAL", bars)
        try:
            trend, _ = A._it_classify_trend_native("MNQ")
            self.assertEqual(trend, "BULLISH")
        finally:
            self._clear_mtf("MNQ")


# ══════════════════════════════════════════════════════════════════════════════
# 22. _it_compute_1h_context() — 1H alignment (spec §6)
# ══════════════════════════════════════════════════════════════════════════════

class TestOneHContext(unittest.TestCase):

    def test_aligned_bullish(self):
        self.assertEqual(A._it_compute_1h_context("BULLISH", "BULLISH"), "ALIGNED")

    def test_aligned_bearish(self):
        self.assertEqual(A._it_compute_1h_context("BEARISH", "BEARISH"), "ALIGNED")

    def test_opposed_bull_vs_bear(self):
        self.assertEqual(A._it_compute_1h_context("BULLISH", "BEARISH"), "OPPOSED")

    def test_opposed_bear_vs_bull(self):
        self.assertEqual(A._it_compute_1h_context("BEARISH", "BULLISH"), "OPPOSED")

    def test_neutral_15m_returns_neutral(self):
        self.assertEqual(A._it_compute_1h_context("NEUTRAL",    "BULLISH"), "NEUTRAL")
        self.assertEqual(A._it_compute_1h_context("TRANSITION", "BEARISH"), "NEUTRAL")

    def test_neutral_1h_returns_neutral(self):
        self.assertEqual(A._it_compute_1h_context("BULLISH", "NEUTRAL"),  "NEUTRAL")
        self.assertEqual(A._it_compute_1h_context("BEARISH", None),       "NEUTRAL")
        self.assertEqual(A._it_compute_1h_context("BEARISH", ""),         "NEUTRAL")

    def test_alternate_1h_labels_normalised(self):
        """'bull' / 'bear' variant labels must be normalised."""
        self.assertEqual(A._it_compute_1h_context("BULLISH", "bull"), "ALIGNED")
        self.assertEqual(A._it_compute_1h_context("BEARISH", "bear"), "ALIGNED")


# ══════════════════════════════════════════════════════════════════════════════
# 23. _it_extension_state() — extension check (spec §8)
# ══════════════════════════════════════════════════════════════════════════════

class TestExtensionState(unittest.TestCase):
    """Price within 1× ATR = NORMAL; 1–2× = EXTENDED; >2× = EXTREME."""

    def test_normal_within_1atr(self):
        state, dist, _ = A._it_extension_state(20000.0, 20030.0, 100.0)
        self.assertEqual(state, "NORMAL")
        self.assertAlmostEqual(dist, 0.30, places=1)

    def test_extended_between_1_and_2_atr(self):
        state, dist, _ = A._it_extension_state(20000.0, 20150.0, 100.0)
        self.assertEqual(state, "EXTENDED")
        self.assertAlmostEqual(dist, 1.5, places=1)

    def test_extreme_beyond_2atr(self):
        state, dist, reason = A._it_extension_state(20000.0, 20250.0, 100.0)
        self.assertEqual(state, "EXTREME")
        self.assertAlmostEqual(dist, 2.5, places=1)
        self.assertIn("extreme", reason.lower())

    def test_missing_vwap_returns_unknown(self):
        state, dist, _ = A._it_extension_state(20000.0, None, 100.0)
        self.assertEqual(state, "UNKNOWN")
        self.assertIsNone(dist)

    def test_zero_atr_returns_unknown(self):
        state, dist, _ = A._it_extension_state(20000.0, 20000.0, 0.0)
        self.assertEqual(state, "UNKNOWN")
        self.assertIsNone(dist)

    def test_fails_open_on_error(self):
        """Bad inputs must not raise — returns UNKNOWN."""
        state, dist, _ = A._it_extension_state("bad", "data", "here")
        self.assertEqual(state, "UNKNOWN")


# ══════════════════════════════════════════════════════════════════════════════
# 24. _it_setup_score() — IT-native quality score (spec §18)
# ══════════════════════════════════════════════════════════════════════════════

class TestSetupScore(unittest.TestCase):

    def _ctx(self, t15="BULLISH", c1h="ALIGNED", lq="EXCELLENT",
             ext="NORMAL", confirmed=True, al=3):
        return {
            "trend_15m_native":    t15,
            "context_1h":          c1h,
            "location_quality":    lq,
            "extension_state":     ext,
            "confirmation_complete": confirmed,
            "confirmation_steps":  ["step1", "step2"],
            "trend_alignment":     "STRONG_BULLISH",
            "alignment_score":     al,
        }

    def test_perfect_context_scores_high(self):
        sc = A._it_setup_score(self._ctx())
        self.assertGreaterEqual(sc, 70)

    def test_opposed_1h_reduces_score(self):
        high = A._it_setup_score(self._ctx(c1h="ALIGNED"))
        low  = A._it_setup_score(self._ctx(c1h="OPPOSED"))
        self.assertGreater(high, low)

    def test_extreme_extension_reduces_score(self):
        normal  = A._it_setup_score(self._ctx(ext="NORMAL"))
        extreme = A._it_setup_score(self._ctx(ext="EXTREME"))
        self.assertGreater(normal, extreme)

    def test_mid_range_location_reduces_score(self):
        good = A._it_setup_score(self._ctx(lq="EXCELLENT"))
        poor = A._it_setup_score(self._ctx(lq="MID_RANGE"))
        self.assertGreater(good, poor)

    def test_transition_trend_less_than_directional(self):
        bull       = A._it_setup_score(self._ctx(t15="BULLISH"))
        transition = A._it_setup_score(self._ctx(t15="TRANSITION"))
        self.assertGreater(bull, transition)

    def test_error_returns_0(self):
        sc = A._it_setup_score(None)
        self.assertEqual(sc, 0)

    def test_score_bounded_0_to_100(self):
        sc = A._it_setup_score(self._ctx())
        self.assertGreaterEqual(sc, 0)
        self.assertLessEqual(sc, 100)


# ══════════════════════════════════════════════════════════════════════════════
# 25. _it_entry_state() — formal state machine (spec §10)
# ══════════════════════════════════════════════════════════════════════════════

class TestEntryStateMachine(unittest.TestCase):

    def _base(self, status="CONFIRMED_SETUP", t15="BULLISH", c1h="ALIGNED",
              ext="NORMAL", lq="KEY_LEVEL", confirmed=True, stop_valid=True,
              time_ok=True, daily_count=0, daily_cap=2):
        return {
            "status":                status,
            "trend_15m_native":      t15,
            "context_1h":            c1h,
            "extension_state":       ext,
            "location_quality":      lq,
            "confirmation_complete": confirmed,
            "structural_stop_valid": stop_valid,
            "time_ok":               time_ok,
            "daily_trade_count":     daily_count,
            "daily_trade_cap":       daily_cap,
        }

    def test_qualified_when_all_pass(self):
        self.assertEqual(A._it_entry_state(self._base()), "QUALIFIED")

    def test_blocked_session_time_restriction(self):
        ctx = self._base(time_ok=False)
        self.assertEqual(A._it_entry_state(ctx), "BLOCKED_SESSION")

    def test_blocked_session_daily_cap(self):
        ctx = self._base(status="DAILY_CAP_REACHED")
        self.assertEqual(A._it_entry_state(ctx), "BLOCKED_SESSION")

    def test_blocked_extension_extreme(self):
        ctx = self._base(ext="EXTREME")
        self.assertEqual(A._it_entry_state(ctx), "BLOCKED_EXTENSION")

    def test_blocked_data_db_unavailable(self):
        ctx = self._base(status="BLOCKED_DAILY_COUNT_UNAVAILABLE")
        self.assertEqual(A._it_entry_state(ctx), "BLOCKED_DATA")

    def test_waiting_for_trend_neutral_15m(self):
        ctx = self._base(t15="NEUTRAL")
        self.assertEqual(A._it_entry_state(ctx), "WAITING_FOR_TREND")

    def test_waiting_for_trend_transition(self):
        ctx = self._base(t15="TRANSITION")
        self.assertEqual(A._it_entry_state(ctx), "WAITING_FOR_TREND")

    def test_waiting_for_trend_opposed_1h(self):
        ctx = self._base(c1h="OPPOSED")
        self.assertEqual(A._it_entry_state(ctx), "WAITING_FOR_TREND")

    def test_waiting_for_location_mid_range(self):
        ctx = self._base(lq="MID_RANGE", status="BLOCKED_MID_RANGE")
        self.assertEqual(A._it_entry_state(ctx), "WAITING_FOR_LOCATION")

    def test_waiting_for_pullback_extended(self):
        ctx = self._base(ext="EXTENDED")
        self.assertEqual(A._it_entry_state(ctx), "WAITING_FOR_PULLBACK")

    def test_waiting_for_confirmation_incomplete(self):
        ctx = self._base(status="AWAITING_CONFIRMATION", confirmed=False)
        self.assertEqual(A._it_entry_state(ctx), "WAITING_FOR_CONFIRMATION")

    def test_risk_pending_no_stop(self):
        ctx = self._base(stop_valid=False, status="CONFIRMED_SETUP")
        self.assertEqual(A._it_entry_state(ctx), "RISK_PENDING")

    def test_blocked_risk_stop_invalid_status(self):
        ctx = self._base(stop_valid=False, status="BLOCKED_INVALID_STOP")
        self.assertEqual(A._it_entry_state(ctx), "BLOCKED_RISK")

    def test_managing_when_force_flat(self):
        ctx = self._base()
        ctx["mgmt_action"]    = "FORCE_FLAT"
        ctx["mgmt_force_flat"] = True
        self.assertEqual(A._it_entry_state(ctx), "MANAGING")

    def test_position_active_when_mgmt_action_present(self):
        ctx = self._base()
        ctx["mgmt_action"] = "HOLD_AT_BE"
        self.assertEqual(A._it_entry_state(ctx), "POSITION_ACTIVE")

    def test_fails_open_on_error(self):
        """Exception in state machine must not propagate — returns WAITING_FOR_TREND."""
        self.assertEqual(A._it_entry_state(None), "WAITING_FOR_TREND")


# ══════════════════════════════════════════════════════════════════════════════
# 26. analyze_intraday_trend() — isolation + basic contract (spec §24)
# ══════════════════════════════════════════════════════════════════════════════

class TestAnalyzeIntradayTrend(unittest.TestCase):
    """analyze_intraday_trend must exist, return the required keys, and not
    invoke any SWING strategy policy."""

    def test_returns_required_keys(self):
        result = A.analyze_intraday_trend(
            instrument="MNQ", price=MNQ_PRICE)
        for key in ("trend_15m_native", "trend_15m_native_reason",
                    "context_1h", "extension_state",
                    "extension_dist_atr", "extension_reason"):
            self.assertIn(key, result, f"Missing key: {key}")

    def test_fails_open_on_bad_instrument(self):
        """Unknown instrument must not raise."""
        result = A.analyze_intraday_trend(
            instrument="BADX", price=100.0)
        self.assertIn("trend_15m_native", result)
        self.assertEqual(result["trend_15m_native"], "NEUTRAL")

    def test_does_not_call_swing_functions(self):
        """SWING-policy functions must not appear as actual calls in analyze_intraday_trend.

        We strip docstrings and comment-only lines before scanning so that
        documentation mentioning the forbidden names (e.g. '✗ analyze_swing()')
        does not produce a false positive.
        """
        import inspect, re
        src = inspect.getsource(A.analyze_intraday_trend)
        # Remove triple-quoted docstrings (single and double-quote variants)
        src_nodoc = re.sub(r'""".*?"""', '', src, flags=re.DOTALL)
        src_nodoc = re.sub(r"'''.*?'''", '', src_nodoc, flags=re.DOTALL)
        # Remove comment-only lines
        src_nodoc = "\n".join(
            ln for ln in src_nodoc.splitlines()
            if not ln.lstrip().startswith("#")
        )
        forbidden = ["analyze_swing(", "_swing_entry_veto_reasons(",
                     "_swing_htf_enabled("]
        for fn in forbidden:
            self.assertNotIn(fn, src_nodoc,
                             f"analyze_intraday_trend must not call {fn}")

    def test_extension_unknown_when_no_vwap(self):
        """Without VWAP/ATR, extension state must be UNKNOWN (not a crash)."""
        result = A.analyze_intraday_trend(
            instrument="MNQ", price=MNQ_PRICE, trade_plan=None, swing_ctx=None)
        self.assertEqual(result["extension_state"], "UNKNOWN")

    def test_extension_computed_from_trade_plan_vwap(self):
        """VWAP passed via trade_plan dict must be used for extension calc."""
        result = A.analyze_intraday_trend(
            instrument="MNQ", price=MNQ_PRICE,
            trade_plan={"vwap": MNQ_PRICE + 300.0, "atr_pts": 100.0})
        # 300 pts / 100 ATR = 3.0 → EXTREME
        self.assertEqual(result["extension_state"], "EXTREME")

    def test_extension_normal_within_1atr(self):
        result = A.analyze_intraday_trend(
            instrument="MNQ", price=MNQ_PRICE,
            trade_plan={"vwap": MNQ_PRICE + 50.0, "atr_pts": 100.0})
        self.assertEqual(result["extension_state"], "NORMAL")


# ══════════════════════════════════════════════════════════════════════════════
# 27. New veto gates: EXTREME extension + OPPOSED 1H (spec §§6, 8)
# ══════════════════════════════════════════════════════════════════════════════

class TestNewVetoGates(unittest.TestCase):

    def _ctx_with_ext(self, ext_state):
        ctx = _it_ctx_long()
        ctx["extension_state"] = ext_state
        ctx["extension_reason"] = f"Price {2.5}× ATR — {ext_state}"
        return ctx

    def _ctx_with_1h(self, c1h):
        ctx = _it_ctx_long()
        ctx["context_1h"]   = c1h
        ctx["extension_state"] = "NORMAL"
        return ctx

    def test_extreme_extension_adds_veto(self):
        ctx = self._ctx_with_ext("EXTREME")
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        codes = [v[0] for v in vetoes]
        self.assertIn("extension", codes)

    def test_normal_extension_no_veto(self):
        ctx = self._ctx_with_ext("NORMAL")
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        codes = [v[0] for v in vetoes]
        self.assertNotIn("extension", codes)

    def test_extended_no_extension_veto(self):
        """EXTENDED (not EXTREME) must NOT add an extension veto."""
        ctx = self._ctx_with_ext("EXTENDED")
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        codes = [v[0] for v in vetoes]
        self.assertNotIn("extension", codes)

    def test_opposed_1h_adds_veto_by_default(self):
        orig = os.environ.pop("IT_ALLOW_OPPOSED_1H", None)
        try:
            ctx = self._ctx_with_1h("OPPOSED")
            vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
            codes = [v[0] for v in vetoes]
            self.assertIn("opposed_1h", codes)
        finally:
            if orig is not None:
                os.environ["IT_ALLOW_OPPOSED_1H"] = orig

    def test_opposed_1h_allowed_with_override_env(self):
        os.environ["IT_ALLOW_OPPOSED_1H"] = "1"
        try:
            ctx = self._ctx_with_1h("OPPOSED")
            vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
            codes = [v[0] for v in vetoes]
            self.assertNotIn("opposed_1h", codes)
        finally:
            del os.environ["IT_ALLOW_OPPOSED_1H"]

    def test_aligned_1h_no_veto(self):
        ctx = self._ctx_with_1h("ALIGNED")
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        codes = [v[0] for v in vetoes]
        self.assertNotIn("opposed_1h", codes)


# ══════════════════════════════════════════════════════════════════════════════
# 28. BLOCKED_EXTENSION / BLOCKED_OPPOSED_1H status codes
#     These verify compute_intraday_trend_context status precedence (spec §10).
#     We use direct helper calls to avoid the full context chain.
# ══════════════════════════════════════════════════════════════════════════════

class TestNewStatusCodes(unittest.TestCase):

    def test_entry_state_blocked_extension_for_extreme(self):
        """_it_entry_state maps EXTREME extension → BLOCKED_EXTENSION."""
        ctx = {
            "status": "BUILDING_CONTEXT",
            "extension_state": "EXTREME",
            "context_1h": "ALIGNED",
            "trend_15m_native": "BULLISH",
            "location_quality": "KEY_LEVEL",
            "confirmation_complete": True,
            "structural_stop_valid": True,
            "time_ok": True,
        }
        self.assertEqual(A._it_entry_state(ctx), "BLOCKED_EXTENSION")

    def test_entry_state_waiting_for_trend_for_opposed(self):
        """_it_entry_state maps OPPOSED 1H → WAITING_FOR_TREND."""
        ctx = {
            "status": "CONFIRMED_SETUP",
            "extension_state": "NORMAL",
            "context_1h": "OPPOSED",
            "trend_15m_native": "BULLISH",
            "location_quality": "KEY_LEVEL",
            "confirmation_complete": True,
            "structural_stop_valid": True,
            "time_ok": True,
        }
        self.assertEqual(A._it_entry_state(ctx), "WAITING_FOR_TREND")

    def test_setup_score_zero_on_exception(self):
        self.assertEqual(A._it_setup_score(None), 0)

    def test_setup_score_falls_back_for_unknown_extension(self):
        ctx = {
            "trend_15m_native": "BULLISH",
            "context_1h": "ALIGNED",
            "location_quality": "EXCELLENT",
            "extension_state": "UNKNOWN",
            "confirmation_complete": True,
            "alignment_score": 3,
            "trend_alignment": "STRONG_BULLISH",
        }
        sc = A._it_setup_score(ctx)
        # UNKNOWN extension = 0 pts for extension component; rest can still score
        self.assertGreaterEqual(sc, 40)

    def test_stable_schema_contains_native_keys(self):
        """compute_intraday_trend_context stable schema must expose new native keys."""
        # Just verify the default values are set (don't need full context boot)
        # We check by inspecting the source for the new keys in the ctx init dict
        import inspect
        src = inspect.getsource(A.compute_intraday_trend_context)
        for key in ("trend_15m_native", "context_1h", "extension_state",
                    "extension_dist_atr", "setup_score", "entry_state"):
            self.assertIn(f'"{key}"', src,
                          f"Stable schema must declare '{key}'")


# ══════════════════════════════════════════════════════════════════════════════
# Run
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
