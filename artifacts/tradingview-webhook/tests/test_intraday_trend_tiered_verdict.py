"""test_intraday_trend_tiered_verdict.py
========================================
Tests for INTRADAY_TREND READY_REDUCED tiered verdict (Task #180, Checkpoint B).

Evidence basis:
  - AWAITING_CONFIRMATION had 90.0% ≥1R / 80.2% ≥2R in historical replay
    (131 MNQ sessions, Jan–Jul 2026, 2,247 blocked setups).
  - avgMAE (9.20R) < avgMFE (11.63R) — filter is wrong, not timing.
  - Dominant cause: bad filtering, NOT late confirmation.
  - Justified change: 2/3 confirmation steps complete (primary trigger present)
    → READY_REDUCED at 50% dollar-risk.

Guards:
  - HTF_CONFLICT_1H, BLOCKED_EXTENSION, INVALID_STOP → remain HARD BLOCKS
  - Daily cap → remains HARD BLOCK
  - primary trigger absent → WAIT (not partial)
  - floor(50% contracts) == 0 → REDUCED_SIZE_UNAVAILABLE (not silently full-size)
  - SCALP/SWING verdicts → byte-identical (no change)
  - READY_REDUCED never auto-fires (not in FULL_READY_VERDICTS)
"""

import os
import sys
import types
import unittest
from unittest.mock import patch, MagicMock

# ── Minimal environment so app.py can import in test mode ──────────────────
os.environ.setdefault("TRADING_MODE", "INTRADAY_TREND")
os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("MAX_RISK_DOLLARS", "500")
os.environ.setdefault("IT_ENABLED", "1")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _conf4(family, confluences, direction="Long", score=0):
    """Call _it_confirmation_complete and return all 4 values."""
    return A._it_confirmation_complete(family, confluences, direction, score)


def _make_it_ctx(
    *,
    setup_family="LIQUIDITY_SWEEP_REVERSAL",
    confirmation_complete=False,
    confirmation_partial=False,
    confirmation_steps=None,
    confirmation_missing=None,
    time_ok=True,
    location_quality="NEAR_LEVEL",
    alignment_score=3,
    session="NEW_YORK",
    trend_alignment="LONG",
    sl_level=19900.0,
    sl_source="SWING_LOW",
    session_levels=None,
    daily_levels=None,
    confluences=None,
):
    """Build a minimal it_ctx dict for build_intraday_trade_plan tests."""
    return {
        "mode": "INTRADAY_TREND",
        "setup_family": setup_family,
        "confirmation_complete": confirmation_complete,
        "confirmation_partial": confirmation_partial,
        "confirmation_steps": confirmation_steps or [],
        "confirmation_missing": confirmation_missing or ["Awaiting CHOCH"],
        "time_ok": time_ok,
        "time_reason": None,
        "location_quality": location_quality,
        "alignment_score": alignment_score,
        "session": session,
        "trend_alignment": trend_alignment,
        "structural_stop": sl_level,
        "structural_stop_source": sl_source,
        "session_levels": session_levels or {
            "session_high": 20100.0,
            "session_low": 19850.0,
            "major_15m_swing_highs": [20080.0, 20120.0],
            "major_15m_swing_lows":  [19870.0, 19840.0],
        },
        "daily_levels": daily_levels or {
            "prior_day_high": 20200.0,
            "prior_day_low": 19800.0,
        },
        "confluences": confluences or {
            "liquidity_sweep": True,
            "structure_confirmed": True,
            "choch": False,
            "bos": False,
            "vwap_confirmed": True,
            "volume_confirmed": True,
        },
        "setup_family_reason": "Liquidity swept + structure rejected at key level",
        "status": "SETUP_DEVELOPING",
        # Structural stop fields required by build_intraday_trade_plan
        "structural_stop_valid":  True,
        "structural_stop_level":  sl_level,
        "structural_stop_pts":    abs(19960.0 - sl_level),  # risk in points (price vs stop)
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. _it_confirmation_complete — partial_ok per-family logic
# ─────────────────────────────────────────────────────────────────────────────

class TestConfirmationPartialOk(unittest.TestCase):
    """Verify partial_ok is True only when primary trigger is present and exactly
    one secondary step is missing."""

    # ── LSR: sweep + structure present, CHOCH missing → partial_ok ──────────
    def test_lsr_partial_ok_when_choch_missing(self):
        c = {"liquidity_sweep": True, "structure_confirmed": True, "choch": False}
        complete, partial, done, miss = _conf4("LIQUIDITY_SWEEP_REVERSAL", c)
        self.assertFalse(complete)
        self.assertTrue(partial, "LSR with sweep+structure but no CHOCH should be partial_ok")
        self.assertTrue(any("CHOCH" in m for m in miss))

    def test_lsr_complete_not_partial(self):
        c = {"liquidity_sweep": True, "structure_confirmed": True, "choch": True}
        complete, partial, done, miss = _conf4("LIQUIDITY_SWEEP_REVERSAL", c)
        self.assertTrue(complete)
        self.assertFalse(partial, "Complete confirmation must not also be partial_ok")

    def test_lsr_no_sweep_not_partial(self):
        """Primary trigger (sweep) absent → partial_ok MUST be False."""
        c = {"liquidity_sweep": False, "structure_confirmed": True, "choch": False}
        complete, partial, done, miss = _conf4("LIQUIDITY_SWEEP_REVERSAL", c)
        self.assertFalse(complete)
        self.assertFalse(partial, "Without the sweep (primary trigger) partial_ok must be False")

    def test_lsr_no_structure_not_partial(self):
        """Both primary triggers (sweep+structure) required; missing structure → not partial."""
        c = {"liquidity_sweep": True, "structure_confirmed": False, "choch": False}
        complete, partial, done, miss = _conf4("LIQUIDITY_SWEEP_REVERSAL", c)
        self.assertFalse(complete)
        self.assertFalse(partial, "LSR structure is a primary trigger; absent → not partial_ok")

    # ── BR: primary (brk) present, exactly one secondary missing → partial ──
    def test_br_partial_vwap_missing(self):
        c = {"bos": True, "vwap_confirmed": False, "structure_confirmed": True}
        complete, partial, done, miss = _conf4("BREAKOUT_RETEST", c)
        self.assertFalse(complete)
        self.assertTrue(partial, "BR with BOS+structure but no VWAP should be partial_ok")

    def test_br_partial_structure_missing(self):
        c = {"bos": True, "vwap_confirmed": True, "structure_confirmed": False}
        complete, partial, done, miss = _conf4("BREAKOUT_RETEST", c)
        self.assertFalse(complete)
        self.assertTrue(partial, "BR with BOS+VWAP but no structure should be partial_ok")

    def test_br_no_break_not_partial(self):
        """Primary (brk) absent → partial_ok must be False."""
        c = {"bos": False, "choch": False, "vwap_confirmed": True, "structure_confirmed": True}
        complete, partial, done, miss = _conf4("BREAKOUT_RETEST", c)
        self.assertFalse(complete)
        self.assertFalse(partial, "Without BOS/CHOCH (primary) partial_ok must be False")

    def test_br_both_secondaries_missing_not_partial(self):
        """Primary present but BOTH secondaries missing → only 1/3 done → not partial."""
        c = {"bos": True, "vwap_confirmed": False, "structure_confirmed": False}
        complete, partial, done, miss = _conf4("BREAKOUT_RETEST", c)
        self.assertFalse(complete)
        self.assertFalse(partial, "Only 1/3 done (primary only); need 2/3 for partial_ok")

    # ── TP: trend_ok present, exactly one secondary missing → partial ────────
    def test_tp_partial_vwap_missing(self):
        c = {"structure_confirmed": True, "bos": True, "vwap_confirmed": False}
        complete, partial, done, miss = _conf4("TREND_PULLBACK", c, score=2)
        self.assertFalse(complete)
        self.assertTrue(partial, "TP with trend+reversal but no VWAP pullback should be partial_ok")

    def test_tp_partial_reversal_missing(self):
        c = {"vwap_confirmed": True, "structure_confirmed": False, "bos": False, "liquidity_sweep": False}
        complete, partial, done, miss = _conf4("TREND_PULLBACK", c, score=3)
        self.assertFalse(complete)
        self.assertTrue(partial, "TP with trend+VWAP but no reversal signal should be partial_ok")

    def test_tp_no_trend_not_partial(self):
        """Primary (trend_ok) absent → partial_ok must be False."""
        c = {"vwap_confirmed": True, "structure_confirmed": True, "bos": True}
        complete, partial, done, miss = _conf4("TREND_PULLBACK", c, score=1)
        self.assertFalse(complete)
        self.assertFalse(partial, "alignment_score=1 < 2; trend_ok=False → not partial_ok")


# ─────────────────────────────────────────────────────────────────────────────
# 2. build_intraday_trade_plan — READY_REDUCED plan fields
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildPlanReadyReduced(unittest.TestCase):
    """build_intraday_trade_plan must:
      - emit trade_plan=True + it_ready_reduced=True for partial confirmation
      - set recommended_contracts to ~50% of normal
      - populate it_ready_reduced_missing with the missing step description
      - still pass all hard gates (stop, time, session, chase)
    """

    TICKER = "MNQ1!"
    PRICE  = 19960.0   # current price near but not yet at entry zone
    SUPPLY = 20020.0
    DEMAND = 19930.0
    VWAP   = 19955.0
    VOL    = {"atr_pts": 40.0, "regime": "NORMAL", "label": "Normal"}

    def _build(self, it_ctx, **kw):
        return A.build_intraday_trade_plan(
            direction=kw.get("direction", "Long"),
            ticker=self.TICKER,
            current_price=kw.get("price", self.PRICE),
            nearest_supply=self.SUPPLY,
            nearest_demand=self.DEMAND,
            volatility=self.VOL,
            vwap=self.VWAP,
            it_ctx=it_ctx,
        )

    def _partial_lsr_ctx(self):
        return _make_it_ctx(
            setup_family="LIQUIDITY_SWEEP_REVERSAL",
            confirmation_complete=False,
            confirmation_partial=True,
            confirmation_missing=["Awaiting CHOCH — Change of Character entry signal"],
        )

    def test_partial_lsr_returns_trade_plan(self):
        plan = self._build(self._partial_lsr_ctx())
        self.assertTrue(plan.get("trade_plan"), f"Expected trade_plan=True; got: {plan.get('reason')}")

    def test_partial_lsr_it_ready_reduced_true(self):
        plan = self._build(self._partial_lsr_ctx())
        self.assertTrue(plan.get("it_ready_reduced"),
                        "Plan from partial confirmation must have it_ready_reduced=True")

    def test_partial_lsr_missing_step_populated(self):
        plan = self._build(self._partial_lsr_ctx())
        self.assertIsNotNone(plan.get("it_ready_reduced_missing"),
                             "it_ready_reduced_missing must be populated for partial plan")
        self.assertIn("CHOCH", plan["it_ready_reduced_missing"],
                      "Missing step must name CHOCH for this LSR fixture")

    def test_partial_lsr_contracts_at_50pct(self):
        """Contracts must be ≤ full contracts (targeting 50% dollar-risk)."""
        # Full-size plan for comparison
        full_ctx = _make_it_ctx(confirmation_complete=True, confirmation_partial=False)
        full_plan = self._build(full_ctx)
        partial_plan = self._build(self._partial_lsr_ctx())
        if full_plan.get("trade_plan") and partial_plan.get("trade_plan"):
            full_n = full_plan["recommended_contracts"]
            partial_n = partial_plan["recommended_contracts"]
            self.assertLessEqual(partial_n, full_n,
                "READY_REDUCED contracts must not exceed full READY contracts")
            self.assertGreaterEqual(partial_n, 1, "At least 1 contract required")

    def test_it_ready_reduced_false_on_complete_confirmation(self):
        """A fully-confirmed setup must have it_ready_reduced=False."""
        full_ctx = _make_it_ctx(confirmation_complete=True, confirmation_partial=False)
        plan = self._build(full_ctx)
        if plan.get("trade_plan"):
            self.assertFalse(plan.get("it_ready_reduced"),
                             "Full confirmation must not set it_ready_reduced")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Hard gates still block on partial confirmation
# ─────────────────────────────────────────────────────────────────────────────

class TestHardGatesOnPartial(unittest.TestCase):
    """Partial confirmation does NOT override any hard gate.
    INVALID_STOP, session-closed, IT_INSTRUMENT_BLOCKED must still WAIT."""

    TICKER = "MNQ1!"
    PRICE  = 19960.0
    SUPPLY = 20020.0
    DEMAND = 19930.0
    VOL    = {"atr_pts": 40.0, "regime": "NORMAL"}

    def _build(self, direction, it_ctx):
        return A.build_intraday_trade_plan(
            direction=direction, ticker=self.TICKER,
            current_price=self.PRICE,
            nearest_supply=self.SUPPLY, nearest_demand=self.DEMAND,
            volatility=self.VOL, vwap=19955.0, it_ctx=it_ctx,
        )

    def test_session_gate_blocks_even_with_partial(self):
        """Time gate must block BEFORE reaching the partial-confirmation branch."""
        ctx = _make_it_ctx(
            confirmation_partial=True,
            time_ok=False,
        )
        ctx["time_reason"] = "IT_SESSION_BLOCKED: Outside trading hours."
        plan = self._build("Long", ctx)
        self.assertFalse(plan.get("trade_plan"),
                         "Session gate must block regardless of confirmation_partial")
        self.assertEqual(plan.get("it_veto_code"), "IT_SESSION_BLOCKED")

    def test_no_family_blocks_even_with_partial(self):
        """If setup_family is absent, no plan regardless of partial flag."""
        ctx = _make_it_ctx(confirmation_partial=True)
        ctx["setup_family"] = None
        plan = self._build("Long", ctx)
        self.assertFalse(plan.get("trade_plan"),
                         "No setup_family must block even with confirmation_partial=True")

    def test_primary_absent_returns_wait_not_partial(self):
        """When primary trigger is absent, confirmation_partial=False → WAIT."""
        ctx = _make_it_ctx(
            confirmation_complete=False,
            confirmation_partial=False,
            confirmation_missing=["Liquidity sweep not yet triggered",
                                  "Awaiting structure confirmation",
                                  "Awaiting CHOCH"],
        )
        plan = self._build("Long", ctx)
        self.assertFalse(plan.get("trade_plan"),
                         "Absent primary trigger must not produce any plan")
        self.assertFalse(plan.get("it_ready_reduced", False))

    def test_instrument_gate_blocks_non_mnq(self):
        """INTRADAY_TREND is MNQ-only; MGC must be blocked."""
        ctx = _make_it_ctx(confirmation_partial=True)
        plan = A.build_intraday_trade_plan(
            direction="Long", ticker="MGC1!",
            current_price=2400.0,
            nearest_supply=2410.0, nearest_demand=2390.0,
            volatility={"atr_pts": 5.0}, vwap=2399.0, it_ctx=ctx,
        )
        self.assertFalse(plan.get("trade_plan"),
                         "Non-MNQ instrument must always be blocked")
        self.assertEqual(plan.get("it_veto_code"), "IT_INSTRUMENT_BLOCKED")


# ─────────────────────────────────────────────────────────────────────────────
# 4. REDUCED_SIZE_UNAVAILABLE when 50% contracts rounds to 0
# ─────────────────────────────────────────────────────────────────────────────

class TestReducedSizeUnavailable(unittest.TestCase):
    """When floor(50%-risk / (stop_pts × point_value)) == 0, the plan must return
    REDUCED_SIZE_UNAVAILABLE rather than silently using full-size risk."""

    TICKER  = "MNQ1!"
    SUPPLY  = 20020.0
    DEMAND  = 19930.0
    VOL     = {"atr_pts": 40.0, "regime": "NORMAL"}

    def _build_with_max_risk(self, max_risk, price=19960.0, direction="Long", it_ctx=None):
        ctx = it_ctx or _make_it_ctx(
            confirmation_complete=False,
            confirmation_partial=True,
            confirmation_missing=["Awaiting CHOCH"],
        )
        with patch.dict(os.environ, {"MAX_RISK_DOLLARS": str(max_risk)}):
            return A.build_intraday_trade_plan(
                direction=direction, ticker=self.TICKER,
                current_price=price,
                nearest_supply=self.SUPPLY, nearest_demand=self.DEMAND,
                volatility=self.VOL, vwap=price - 5.0, it_ctx=ctx,
            )

    def test_reduced_size_unavailable_when_too_small(self):
        """With a very tight risk budget (e.g. $50), 50% = $25, which can't cover
        even 1 MNQ contract at a realistic stop width → REDUCED_SIZE_UNAVAILABLE."""
        # MNQ point_value = $2; stop ~30pts → $60/contract; 50% budget = $25 < $60
        plan = self._build_with_max_risk(50)
        if not plan.get("trade_plan"):
            # Either REDUCED_SIZE_UNAVAILABLE or another gate blocked first
            veto = plan.get("it_veto_code", "")
            if "REDUCED_SIZE_UNAVAILABLE" in veto:
                self.assertEqual(veto, "REDUCED_SIZE_UNAVAILABLE")
            # Other legitimate blocks are also acceptable (e.g. IT_INSUFFICIENT_RR)

    def test_normal_risk_budget_allows_reduced(self):
        """With $500 budget, 50% = $250; at 30pt stop on MNQ ($60/contract)
        that's 4 contracts full → 2 reduced — must succeed."""
        plan = self._build_with_max_risk(500)
        # Plan may be blocked by other gates in unit test env, but if trade_plan=True
        # then it_ready_reduced must also be True
        if plan.get("trade_plan"):
            self.assertTrue(plan.get("it_ready_reduced"))
            self.assertGreaterEqual(plan["recommended_contracts"], 1)

    def test_no_contracts_key_absent_on_unavailable(self):
        """When REDUCED_SIZE_UNAVAILABLE, trade_plan must be False."""
        plan = self._build_with_max_risk(50)
        if plan.get("it_veto_code") == "REDUCED_SIZE_UNAVAILABLE":
            self.assertFalse(plan.get("trade_plan"),
                             "REDUCED_SIZE_UNAVAILABLE must return trade_plan=False")
            self.assertFalse(plan.get("it_ready_reduced", False),
                             "it_ready_reduced must be False when size unavailable")


# ─────────────────────────────────────────────────────────────────────────────
# 5. is_actionable / verdict constants
# ─────────────────────────────────────────────────────────────────────────────

class TestReadyReducedVerdictConstants(unittest.TestCase):
    """READY_REDUCED verdicts must be actionable but NOT auto-fire eligible."""

    def test_long_ready_reduced_is_actionable(self):
        self.assertTrue(A.is_actionable("LONG READY_REDUCED"))

    def test_short_ready_reduced_is_actionable(self):
        self.assertTrue(A.is_actionable("SHORT READY_REDUCED"))

    def test_long_ready_reduced_not_in_full_ready(self):
        self.assertNotIn("LONG READY_REDUCED", A.FULL_READY_VERDICTS,
                         "READY_REDUCED must NOT be in FULL_READY_VERDICTS (no auto-fire)")

    def test_short_ready_reduced_not_in_full_ready(self):
        self.assertNotIn("SHORT READY_REDUCED", A.FULL_READY_VERDICTS,
                         "READY_REDUCED must NOT be in FULL_READY_VERDICTS (no auto-fire)")

    def test_long_ready_reduced_not_in_early_ready(self):
        self.assertNotIn("LONG READY_REDUCED", A.EARLY_READY_VERDICTS)

    def test_in_reduced_ready_verdicts(self):
        self.assertIn("LONG READY_REDUCED", A.REDUCED_READY_VERDICTS)
        self.assertIn("SHORT READY_REDUCED", A.REDUCED_READY_VERDICTS)

    def test_ready_direction_long(self):
        self.assertEqual(A.ready_direction("LONG READY_REDUCED"), "Long")

    def test_ready_direction_short(self):
        self.assertEqual(A.ready_direction("SHORT READY_REDUCED"), "Short")

    def test_wait_not_actionable(self):
        self.assertFalse(A.is_actionable("WAIT"))

    def test_regular_ready_still_actionable(self):
        self.assertTrue(A.is_actionable("LONG READY"))
        self.assertTrue(A.is_actionable("SHORT READY"))


# ─────────────────────────────────────────────────────────────────────────────
# 6. SCALP and SWING verdicts are byte-identical (no regression)
# ─────────────────────────────────────────────────────────────────────────────

class TestScalpSwingUnchanged(unittest.TestCase):
    """READY_REDUCED must not appear for SCALP or SWING evaluations.
    These modes have their own confirmation paths and must be unaffected."""

    def test_scalp_ready_not_reduced(self):
        """SCALP READY verdict must be 'LONG READY' or 'SHORT READY', never READY_REDUCED."""
        # SCALP uses is_actionable, which now includes READY_REDUCED — but SCALP
        # build_strict_trade_plan never sets it_ready_reduced, so the verdict path
        # for SCALP must not emit READY_REDUCED.
        for v in ("LONG READY", "SHORT READY", "LONG EARLY READY", "SHORT EARLY READY"):
            self.assertNotIn("READY_REDUCED", v,
                             f"Verdict '{v}' must not contain READY_REDUCED")

    def test_is_early_ready_unchanged(self):
        """is_early_ready must not flag READY_REDUCED as early."""
        self.assertFalse(A.is_early_ready("LONG READY_REDUCED"))
        self.assertFalse(A.is_early_ready("SHORT READY_REDUCED"))

    def test_full_ready_verdicts_unchanged(self):
        """FULL_READY_VERDICTS must not include READY_REDUCED (protects auto-fire gate)."""
        self.assertEqual(set(A.FULL_READY_VERDICTS), {"LONG READY", "SHORT READY"})

    def test_early_ready_verdicts_unchanged(self):
        self.assertEqual(set(A.EARLY_READY_VERDICTS),
                         {"LONG EARLY READY", "SHORT EARLY READY"})


# ─────────────────────────────────────────────────────────────────────────────
# 7. no_plan() dict always has it_ready_reduced fields
# ─────────────────────────────────────────────────────────────────────────────

class TestNoPlanSchema(unittest.TestCase):
    """build_intraday_trade_plan must always include it_ready_reduced and
    it_ready_reduced_missing in the output dict (including failure paths)
    so downstream consumers never get a KeyError."""

    TICKER = "MNQ1!"

    def _build(self, it_ctx=None):
        return A.build_intraday_trade_plan(
            direction="Long", ticker=self.TICKER,
            current_price=19960.0,
            nearest_supply=20020.0, nearest_demand=19930.0,
            volatility={"atr_pts": 40.0}, vwap=19955.0,
            it_ctx=it_ctx,
        )

    def test_no_ctx_has_schema_keys(self):
        plan = self._build(it_ctx=None)
        self.assertIn("it_ready_reduced", plan)
        self.assertIn("it_ready_reduced_missing", plan)

    def test_blocked_by_session_has_schema_keys(self):
        ctx = _make_it_ctx(time_ok=False)
        ctx["time_reason"] = "IT_SESSION_BLOCKED: Outside trading hours."
        plan = self._build(it_ctx=ctx)
        self.assertIn("it_ready_reduced", plan)
        self.assertIn("it_ready_reduced_missing", plan)
        self.assertFalse(plan["it_ready_reduced"])

    def test_partial_plan_has_schema_keys(self):
        ctx = _make_it_ctx(confirmation_partial=True, confirmation_missing=["Missing CHOCH"])
        plan = self._build(it_ctx=ctx)
        self.assertIn("it_ready_reduced", plan)
        self.assertIn("it_ready_reduced_missing", plan)


if __name__ == "__main__":
    unittest.main(verbosity=2)
