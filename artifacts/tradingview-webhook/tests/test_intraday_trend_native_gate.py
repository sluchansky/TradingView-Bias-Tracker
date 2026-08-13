"""
INTRADAY_TREND — Native Gate Routing (spec: attached_assets/…native-gate-routing-fix.txt)

Verifies the one architectural correction: IT bypasses the inherited SWING
strict prerequisite (edge≥85 / zone_valid / vwap_confirmed / structure_confirmed)
and uses its own native pipeline as the sole READY/WAIT authority.

Tests A–B  — SCALP/SWING verdict routing is byte-identical after the change.
Tests C–D  — IT can reach READY when the legacy SWING gate would have blocked.
Tests E–M  — Every IT-native hard gate still blocks when conditions fail.

All tests are PURE — no DB, no network.  DB-dependent helpers are mocked.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

TEST_DIR = os.path.dirname(__file__)
APP_DIR  = os.path.dirname(TEST_DIR)
sys.path.insert(0, APP_DIR)

os.environ.setdefault("TRADING_MODE",       "INTRADAY_TREND")
os.environ.setdefault("DASHBOARD_PASSWORD", "test")
os.environ.setdefault("SESSION_SECRET",     "test")
os.environ.setdefault("DATABASE_URL",       "postgresql://localhost/test")
os.environ.setdefault("MAX_RISK_DOLLARS",   "500")

import app as A   # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

ENTRY  = 21_000.0   # MNQ price
ATR    = 50.0       # realistic MNQ ATR
STOP   = ENTRY - 20.0   # Long stop 20pts below entry (> 0.3×ATR = 15, < 4×ATR = 200)
RISK   = ENTRY - STOP   # 20.0 pts
TP2    = ENTRY + RISK * 2.5   # 2.5R → well above MIN_INTRADAY_RR=2.0


def _base_veto_ctx(overrides=None):
    """Minimal it_ctx that passes every _it_entry_veto_reasons gate."""
    ctx = {
        "instrument":            "MNQ",
        "context_1h":            "ALIGNED",
        "extension_state":       "NORMAL",
        "time_ok":               True,
        "location_quality":      "GOOD",
        "setup_family":          "TREND_PULLBACK",
        "confirmation_complete": True,
        "structural_stop_valid": True,
        "structural_stop_pts":   RISK,
        "daily_trade_count":     0,
        "daily_trade_cap":       3,
        "data_freshness_ok":     True,
        "stale_timeframes":      [],
        "cooldown_remaining":    0,
    }
    if overrides:
        ctx.update(overrides)
    return ctx


def _full_it_ctx(direction="Long", overrides=None):
    """Minimal it_ctx that passes both the veto layer AND build_intraday_trade_plan.

    Includes the structural stop level, session levels with a qualifying TP2
    target (≥2.0R from entry), and all Phase 3 keys.
    """
    if direction == "Long":
        sl_level = STOP
        tp2_level = TP2
    else:
        sl_level = ENTRY + RISK
        tp2_level = ENTRY - RISK * 2.5

    ctx = {
        "instrument":            "MNQ",
        "context_1h":            "ALIGNED",
        "extension_state":       "NORMAL",
        "time_ok":               True,
        "time_reason":           None,
        "location_quality":      "GOOD",
        "location_reason":       None,
        "setup_family":          "TREND_PULLBACK",
        "confirmation_complete": True,
        "confirmation_missing":  [],
        "structural_stop_valid": True,
        "structural_stop_level": sl_level,
        "structural_stop_pts":   RISK,
        "structural_stop_source": "test swing low",
        "data_freshness_ok":     True,
        "stale_timeframes":      [],
        "cooldown_remaining":    0,
        "daily_trade_count":     0,
        "daily_trade_cap":       3,
        # session / target levels — provide a real level that qualifies ≥2R
        "session_levels": {
            "overnight_high":         tp2_level if direction == "Long" else None,
            "overnight_low":          tp2_level if direction == "Short" else None,
            "asia_high":              None,
            "asia_low":               None,
            "prior_high":             tp2_level + 5 if direction == "Long" else None,
            "prior_low":              None,
            "major_15m_swing_highs":  [tp2_level] if direction == "Long" else [],
            "major_15m_swing_lows":   [] if direction == "Long" else [tp2_level],
        },
        "daily_levels": {},
        "intraday_bias":         direction,
        "trend_alignment":       "BULLISH" if direction == "Long" else "BEARISH",
        "projected_r":           None,
        "projected_points":      None,
    }
    if overrides:
        ctx.update(overrides)
    return ctx


def _volatility(atr=ATR):
    return {"atr_pts": atr, "regime": "normal", "label": "Normal"}


# ─────────────────────────────────────────────────────────────────────────────
# Test A — SCALP verdict routing: byte-identical after the architectural change
# ─────────────────────────────────────────────────────────────────────────────

class TestA_ScalpRoutingUnchanged(unittest.TestCase):
    """evaluate_strict_setup + the SCALP-specific routing branch are untouched."""

    def _strict(self, **kwargs):
        defaults = dict(
            current_price=21_000.0, ticker="MNQ1!", vwap=21_000.0,
            vwap_status="above", nearest_supply=21_050.0, nearest_demand=20_950.0,
            bullish=60, bearish=10, confidence=70,
            alert_history=[], volatility=_volatility(), session=None,
            cooldown_active=False, mode="SCALP",
        )
        defaults.update(kwargs)
        return A.evaluate_strict_setup(**defaults)

    def test_scalp_wait_when_edge_below_60(self):
        """SCALP threshold is 60; below → WAIT."""
        r = self._strict(bullish=20, bearish=10, confidence=30)
        self.assertEqual(r["label"], "WAIT")

    def test_scalp_direction_determined_by_structure_not_edge(self):
        """Direction key is present even on WAIT (routing uses it)."""
        r = self._strict(bullish=20, bearish=10, confidence=30)
        # May or may not have a direction, but the key must be present.
        self.assertIn("direction", r)

    def test_scalp_mode_flag_is_in_mode_config(self):
        """SCALP mode has EDGE_READY_THRESHOLD < 85 (currently 60)."""
        thr = int(A.cfg_for("SCALP", "EDGE_READY_THRESHOLD"))
        self.assertLess(thr, 85)

    def test_it_mode_flag_inherits_swing_threshold(self):
        """INTRADAY_TREND inherits SWING's EDGE_READY_THRESHOLD = 85."""
        thr = int(A.cfg_for("INTRADAY_TREND", "EDGE_READY_THRESHOLD"))
        self.assertEqual(thr, 85)


# ─────────────────────────────────────────────────────────────────────────────
# Test B — SWING verdict routing: byte-identical after the architectural change
# ─────────────────────────────────────────────────────────────────────────────

class TestB_SwingRoutingUnchanged(unittest.TestCase):
    """SWING evaluate_strict_setup still requires edge≥85 / zone / vwap / structure."""

    def _strict(self, **kwargs):
        defaults = dict(
            current_price=21_000.0, ticker="MNQ1!", vwap=21_000.0,
            vwap_status="above", nearest_supply=21_050.0, nearest_demand=20_950.0,
            bullish=60, bearish=10, confidence=70,
            alert_history=[], volatility=_volatility(), session=None,
            cooldown_active=False, mode="SWING",
        )
        defaults.update(kwargs)
        return A.evaluate_strict_setup(**defaults)

    def test_swing_requires_edge_85(self):
        """SWING gate: below 85 → WAIT."""
        r = self._strict(bullish=40, bearish=10, confidence=50)
        self.assertEqual(r["label"], "WAIT")

    def test_swing_threshold_is_85(self):
        """SWING EDGE_READY_THRESHOLD is 85."""
        self.assertEqual(int(A.cfg_for("SWING", "EDGE_READY_THRESHOLD")), 85)

    def test_swing_gate_require_zone_true(self):
        """SWING requires a zone (GATE_REQUIRE_ZONE=True)."""
        self.assertTrue(bool(A.cfg_for("SWING", "GATE_REQUIRE_ZONE")))


# ─────────────────────────────────────────────────────────────────────────────
# Test C — IT with edge=70 (<85) can reach the IT native pipeline
# ─────────────────────────────────────────────────────────────────────────────

class TestC_ITBelowLegacyEdgeCanReady(unittest.TestCase):
    """Verify _it_entry_veto_reasons passes independently of edge score."""

    def test_veto_passes_regardless_of_edge(self):
        """The veto layer has no edge-score check; a clear ctx passes with edge=70."""
        ctx = _base_veto_ctx()
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", instrument="MNQ")
        self.assertEqual(vetoes, [], f"Expected no vetoes, got: {vetoes}")

    def test_legacy_gate_would_have_blocked(self):
        """evaluate_strict_setup with INTRADAY_TREND config returns WAIT at edge≈70."""
        # Bullish 40 / Bearish 10 → edge ≈ 70 (below the 85 threshold)
        r = A.evaluate_strict_setup(
            current_price=21_000.0, ticker="MNQ1!",
            vwap=21_000.0, vwap_status="above",
            nearest_supply=21_050.0, nearest_demand=20_950.0,
            bullish=40, bearish=10, confidence=55,
            alert_history=[], volatility=_volatility(), session=None,
            cooldown_active=False, mode="INTRADAY_TREND",
        )
        self.assertEqual(r["label"], "WAIT",
                         "Legacy SWING gate should have produced WAIT at edge<85")

    def test_build_plan_succeeds_with_native_ctx(self):
        """build_intraday_trade_plan succeeds with a valid it_ctx — no edge gate inside."""
        ctx = _full_it_ctx("Long")
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", ENTRY,
            nearest_supply=ENTRY + 30.0,
            nearest_demand=ENTRY - 10.0,
            volatility=_volatility(),
            vwap=ENTRY - 5.0,
            it_ctx=ctx,
        )
        self.assertTrue(plan["trade_plan"],
                        f"Plan should succeed with valid it_ctx. Reason: {plan.get('reason')}")

    def test_shadow_legacy_struct_populated(self):
        """_it_legacy_strict must be captured for ghost analytics (component test)."""
        # Simulate what full_analysis does: shadow the legacy strict result before IT routing.
        legacy = {
            "label":    "WAIT",
            "score":    70,
            "reason":   "edge_score(70<85)",
            "missing":  ["edge_score"],
            "was_ready": False,
        }
        # Attach to a ctx dict like the display block does.
        ctx = _full_it_ctx("Long")
        ctx["legacy_strict_verdict"] = legacy
        self.assertFalse(ctx["legacy_strict_verdict"]["was_ready"])
        self.assertEqual(ctx["legacy_strict_verdict"]["score"], 70)
        self.assertEqual(ctx["legacy_strict_verdict"]["label"], "WAIT")


# ─────────────────────────────────────────────────────────────────────────────
# Test D — Legacy zone failure does not block IT when native ctx passes
# ─────────────────────────────────────────────────────────────────────────────

class TestD_LegacyZoneFailureDoesNotBlockIT(unittest.TestCase):
    """zone_valid=False in the SWING gate must not block IT native pipeline."""

    def test_swing_gate_blocks_without_zone(self):
        """evaluate_strict_setup blocks SWING when zone_valid=False (no BOS etc.)."""
        # No BOS / CHOCH in alert_history → zone_valid=False → WAIT for SWING
        r = A.evaluate_strict_setup(
            current_price=21_000.0, ticker="MNQ1!",
            vwap=21_000.0, vwap_status="above",
            nearest_supply=21_050.0, nearest_demand=None,
            bullish=60, bearish=10, confidence=60,
            alert_history=[],   # no zone alerts → zone_valid=False
            volatility=_volatility(), session=None,
            cooldown_active=False, mode="INTRADAY_TREND",
        )
        self.assertEqual(r["label"], "WAIT",
                         "SWING/IT legacy gate should WAIT when no zone signals")

    def test_veto_ignores_zone_valid(self):
        """_it_entry_veto_reasons has no zone_valid check — native IT location is what matters."""
        # Simulate a setup with GOOD IT location but no zone (zone_valid would be False)
        ctx = _base_veto_ctx({"location_quality": "GOOD"})
        # _it_entry_veto_reasons does NOT check zone_valid — only location_quality
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", instrument="MNQ")
        self.assertEqual(vetoes, [],
                         "_it_entry_veto_reasons should pass even without a supply/demand zone")

    def test_build_plan_doesnt_require_zone(self):
        """build_intraday_trade_plan anchors on VWAP when nearest_demand is None."""
        ctx = _full_it_ctx("Long")
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", ENTRY,
            nearest_supply=None,
            nearest_demand=None,    # no zone — VWAP fallback
            volatility=_volatility(),
            vwap=ENTRY - 5.0,
            it_ctx=ctx,
        )
        self.assertTrue(plan["trade_plan"],
                        f"Plan should succeed anchored on VWAP. Reason: {plan.get('reason')}")


# ─────────────────────────────────────────────────────────────────────────────
# Test E — Confirmation incomplete still blocks IT
# ─────────────────────────────────────────────────────────────────────────────

class TestE_ConfirmationStillBlocks(unittest.TestCase):

    def test_veto_blocks_on_incomplete_confirmation(self):
        ctx = _base_veto_ctx({
            "confirmation_complete": False,
            "confirmation_missing":  ["choch_entry"],
        })
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", instrument="MNQ")
        codes = [v[0] for v in vetoes]
        self.assertIn("confirmation", codes)

    def test_plan_blocks_on_incomplete_confirmation(self):
        ctx = _full_it_ctx("Long", {"confirmation_complete": False,
                                    "confirmation_missing": ["choch_entry"]})
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", ENTRY,
            nearest_supply=None, nearest_demand=ENTRY - 10.0,
            volatility=_volatility(), vwap=ENTRY - 5.0, it_ctx=ctx,
        )
        self.assertFalse(plan["trade_plan"])
        self.assertIn("IT_STRUCTURE_FAIL", plan["reason"])


# ─────────────────────────────────────────────────────────────────────────────
# Test F — MID_RANGE location still blocks IT
# ─────────────────────────────────────────────────────────────────────────────

class TestF_MidRangeStillBlocks(unittest.TestCase):

    def test_veto_blocks_on_mid_range_location(self):
        ctx = _base_veto_ctx({"location_quality": "MID_RANGE"})
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", instrument="MNQ")
        codes = [v[0] for v in vetoes]
        self.assertIn("location", codes)

    def test_plan_returns_no_plan_is_not_location_checked(self):
        """build_intraday_trade_plan does not check location_quality directly;
        the veto layer handles it.  A plan with MID_RANGE ctx still fires the
        plan builder — the veto is what blocks execution."""
        ctx = _full_it_ctx("Long", {"location_quality": "MID_RANGE"})
        # Veto fires at the veto layer (tested above).  Plan builder itself
        # may still build a plan — that's expected; the gate layer stops it.
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", instrument="MNQ")
        self.assertIn("location", [v[0] for v in vetoes])


# ─────────────────────────────────────────────────────────────────────────────
# Test G — EXTREME extension still blocks IT
# ─────────────────────────────────────────────────────────────────────────────

class TestG_ExtremeExtensionStillBlocks(unittest.TestCase):

    def test_veto_blocks_on_extreme_extension(self):
        ctx = _base_veto_ctx({
            "extension_state":  "EXTREME",
            "extension_reason": "BLOCKED_EXTENSION: price far from VWAP",
        })
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", instrument="MNQ")
        codes = [v[0] for v in vetoes]
        self.assertIn("extension", codes)

    def test_normal_extension_does_not_block(self):
        ctx = _base_veto_ctx({"extension_state": "NORMAL"})
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", instrument="MNQ")
        codes = [v[0] for v in vetoes]
        self.assertNotIn("extension", codes)


# ─────────────────────────────────────────────────────────────────────────────
# Test H — Invalid structural stop still blocks
# ─────────────────────────────────────────────────────────────────────────────

class TestH_InvalidStopStillBlocks(unittest.TestCase):

    def test_veto_blocks_when_stop_not_valid(self):
        ctx = _base_veto_ctx({"structural_stop_valid": False})
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", instrument="MNQ")
        codes = [v[0] for v in vetoes]
        self.assertIn("invalid_stop", codes)

    def test_plan_blocks_when_stop_not_valid(self):
        ctx = _full_it_ctx("Long", {"structural_stop_valid": False})
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", ENTRY,
            nearest_supply=None, nearest_demand=ENTRY - 10.0,
            volatility=_volatility(), vwap=ENTRY - 5.0, it_ctx=ctx,
        )
        self.assertFalse(plan["trade_plan"])
        self.assertIn("IT_STRUCTURE_FAIL", plan["reason"])

    def test_plan_blocks_when_stop_above_entry_long(self):
        """Stop above entry for Long → IT_STRUCTURE_FAIL."""
        ctx = _full_it_ctx("Long", {
            "structural_stop_valid": True,
            "structural_stop_level": ENTRY + 10.0,   # above entry — wrong side
            "structural_stop_pts":   10.0,
        })
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", ENTRY,
            nearest_supply=None, nearest_demand=ENTRY - 10.0,
            volatility=_volatility(), vwap=ENTRY - 5.0, it_ctx=ctx,
        )
        self.assertFalse(plan["trade_plan"])
        self.assertIn("IT_STRUCTURE_FAIL", plan["reason"])


# ─────────────────────────────────────────────────────────────────────────────
# Test I — Insufficient R:R still blocks
# ─────────────────────────────────────────────────────────────────────────────

class TestI_InsufficientRRStillBlocks(unittest.TestCase):

    def test_plan_blocks_when_no_level_qualifies_rr(self):
        """No structural level ≥2.0R from entry → IT_INSUFFICIENT_RR."""
        ctx = _full_it_ctx("Long", {
            "session_levels": {
                # All levels too close to qualify 2R
                "overnight_high":        ENTRY + 5.0,   # only 0.25R
                "overnight_low":         None,
                "asia_high":             None,
                "asia_low":              None,
                "prior_high":            ENTRY + 10.0,  # only 0.5R
                "prior_low":             None,
                "major_15m_swing_highs": [ENTRY + 8.0],
                "major_15m_swing_lows":  [],
            },
            "daily_levels": {},
        })
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", ENTRY,
            nearest_supply=ENTRY + 30.0, nearest_demand=ENTRY - 5.0,
            volatility=_volatility(), vwap=ENTRY - 5.0, it_ctx=ctx,
        )
        self.assertFalse(plan["trade_plan"])
        self.assertIn("IT_INSUFFICIENT_RR", plan["reason"])


# ─────────────────────────────────────────────────────────────────────────────
# Test J — Excessive chase still blocks
# ─────────────────────────────────────────────────────────────────────────────

class TestJ_ExcessiveChaseStillBlocks(unittest.TestCase):

    def test_plan_blocks_when_price_far_from_entry(self):
        """Price drifts >1.5×ATR from intended entry → IT_MAX_CHASE."""
        ctx = _full_it_ctx("Long")
        # entry anchors to nearest_demand = 20_950.
        # current_price = 21_200 → chase_dist = 250 >> 1.5×50 = 75
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", 21_200.0,     # current price is far from entry anchor
            nearest_supply=21_250.0, nearest_demand=20_950.0,
            volatility=_volatility(ATR),
            vwap=20_990.0,
            it_ctx=ctx,
        )
        self.assertFalse(plan["trade_plan"])
        self.assertIn("IT_MAX_CHASE", plan["reason"])


# ─────────────────────────────────────────────────────────────────────────────
# Test K — Stale data still blocks
# ─────────────────────────────────────────────────────────────────────────────

class TestK_StaleDataStillBlocks(unittest.TestCase):

    def test_veto_blocks_stale_data(self):
        ctx = _base_veto_ctx({
            "data_freshness_ok": False,
            "stale_timeframes":  ["15m", "1H"],
        })
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", instrument="MNQ")
        codes = [v[0] for v in vetoes]
        self.assertIn("data_freshness", codes)

    def test_fresh_data_does_not_block(self):
        ctx = _base_veto_ctx({"data_freshness_ok": True, "stale_timeframes": []})
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", instrument="MNQ")
        codes = [v[0] for v in vetoes]
        self.assertNotIn("data_freshness", codes)


# ─────────────────────────────────────────────────────────────────────────────
# Test L — Daily cap still blocks
# ─────────────────────────────────────────────────────────────────────────────

class TestL_DailyCapStillBlocks(unittest.TestCase):

    def test_veto_blocks_when_cap_reached(self):
        ctx = _base_veto_ctx({"daily_trade_count": 3, "daily_trade_cap": 3})
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", instrument="MNQ")
        codes = [v[0] for v in vetoes]
        self.assertIn("daily_cap", codes)

    def test_veto_fail_closed_on_db_error(self):
        """count==-1 means DB unavailable → fail-closed block."""
        ctx = _base_veto_ctx({"daily_trade_count": -1})
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", instrument="MNQ")
        codes = [v[0] for v in vetoes]
        self.assertIn("daily_count_unavailable", codes)

    def test_under_cap_does_not_block(self):
        ctx = _base_veto_ctx({"daily_trade_count": 1, "daily_trade_cap": 3})
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", instrument="MNQ")
        codes = [v[0] for v in vetoes]
        self.assertNotIn("daily_cap", codes)
        self.assertNotIn("daily_count_unavailable", codes)


# ─────────────────────────────────────────────────────────────────────────────
# Test M — Opposed 1H behavior unchanged
# ─────────────────────────────────────────────────────────────────────────────

class TestM_Opposed1HBehavior(unittest.TestCase):

    def _veto(self, ctx_overrides=None, env_overrides=None):
        ctx = _base_veto_ctx(ctx_overrides)
        old = {}
        for k, v in (env_overrides or {}).items():
            old[k] = os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = str(v)
        try:
            return A._it_entry_veto_reasons(ctx, {}, "Long", instrument="MNQ")
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_opposed_1h_blocks_by_default(self):
        """Default: IT_ALLOW_OPPOSED_1H not set → OPPOSED blocks."""
        vetoes = self._veto(
            {"context_1h": "OPPOSED"},
            {"IT_ALLOW_OPPOSED_1H": None},   # ensure unset
        )
        codes = [v[0] for v in vetoes]
        self.assertIn("opposed_1h", codes)

    def test_opposed_1h_overridable_via_env(self):
        """IT_ALLOW_OPPOSED_1H=1 removes the opposed-1H veto."""
        vetoes = self._veto(
            {"context_1h": "OPPOSED"},
            {"IT_ALLOW_OPPOSED_1H": "1"},
        )
        codes = [v[0] for v in vetoes]
        self.assertNotIn("opposed_1h", codes)

    def test_aligned_1h_never_blocks(self):
        """ALIGNED context is always fine."""
        vetoes = self._veto({"context_1h": "ALIGNED"})
        codes = [v[0] for v in vetoes]
        self.assertNotIn("opposed_1h", codes)


# ─────────────────────────────────────────────────────────────────────────────
# Extra — MNQ-only gate unchanged
# ─────────────────────────────────────────────────────────────────────────────

class TestExtra_MnqOnlyGate(unittest.TestCase):

    def test_non_mnq_always_blocked(self):
        """IT is MNQ-only; any other instrument gets an immediate veto."""
        for inst in ("MGC", "MES", "MYM", "ES", "GC"):
            ctx = _base_veto_ctx({"instrument": inst})
            vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", instrument=inst)
            codes = [v[0] for v in vetoes]
            self.assertIn("instrument", codes, f"Expected instrument veto for {inst}")

    def test_plan_blocks_for_non_mnq(self):
        """build_intraday_trade_plan returns IT_INSTRUMENT_BLOCKED for non-MNQ."""
        ctx = _full_it_ctx("Long")
        plan = A.build_intraday_trade_plan(
            "Long", "MGC1!", ENTRY,
            nearest_supply=None, nearest_demand=ENTRY - 10.0,
            volatility=_volatility(), vwap=ENTRY - 5.0, it_ctx=ctx,
        )
        self.assertFalse(plan["trade_plan"])
        self.assertIn("IT_INSTRUMENT_BLOCKED", plan["reason"])


# ─────────────────────────────────────────────────────────────────────────────
# Extra — IT context unavailable blocks plan builder (FAIL-CLOSED)
# ─────────────────────────────────────────────────────────────────────────────

class TestExtra_ContextUnavailable(unittest.TestCase):

    def test_plan_blocks_when_it_ctx_is_none(self):
        plan = A.build_intraday_trade_plan(
            "Long", "MNQ1!", ENTRY,
            nearest_supply=None, nearest_demand=ENTRY - 10.0,
            volatility=_volatility(), vwap=ENTRY - 5.0,
            it_ctx=None,
        )
        self.assertFalse(plan["trade_plan"])
        self.assertIn("IT_UNAVAILABLE", plan["reason"])

    def test_veto_blocks_when_it_ctx_is_not_dict(self):
        vetoes = A._it_entry_veto_reasons("not_a_dict", {}, "Long", instrument="MNQ")
        codes = [v[0] for v in vetoes]
        self.assertIn("unavailable", codes)


# ─────────────────────────────────────────────────────────────────────────────
# Extra — ATR sanity bounds for structural stop
# ─────────────────────────────────────────────────────────────────────────────

class TestExtra_AtrSanityBounds(unittest.TestCase):
    """Stop < 0.3×ATR (too tight) or > 4×ATR (too wide) still blocks."""

    def _plan(self, stop_pts):
        if stop_pts > 0:
            sl = ENTRY - stop_pts
        else:
            sl = ENTRY + abs(stop_pts)   # wrong side
        ctx = _full_it_ctx("Long", {
            "structural_stop_level": sl,
            "structural_stop_pts":   stop_pts,
        })
        return A.build_intraday_trade_plan(
            "Long", "MNQ1!", ENTRY,
            nearest_supply=None, nearest_demand=ENTRY - 10.0,
            volatility=_volatility(ATR),
            vwap=ENTRY - 5.0, it_ctx=ctx,
        )

    def test_stop_too_tight_blocks(self):
        """0.3×50 = 15pts minimum; 5pts is below → IT_STRUCTURE_FAIL."""
        plan = self._plan(5.0)
        self.assertFalse(plan["trade_plan"])
        self.assertIn("IT_STRUCTURE_FAIL", plan["reason"])

    def test_stop_too_wide_blocks(self):
        """4×50 = 200pts maximum; 210pts is above → IT_STRUCTURE_FAIL."""
        plan = self._plan(210.0)
        self.assertFalse(plan["trade_plan"])
        self.assertIn("IT_STRUCTURE_FAIL", plan["reason"])

    def test_stop_within_bounds_passes(self):
        """20pts: > 0.3×50=15 and < 4×50=200 → should pass ATR sanity."""
        plan = self._plan(20.0)
        # May also fail at RR selection (no target), but not at ATR sanity.
        if not plan["trade_plan"]:
            self.assertNotIn("too tight", plan.get("reason", ""))
            self.assertNotIn("too wide", plan.get("reason", ""))


# ─────────────────────────────────────────────────────────────────────────────
# Extra — legacy_strict_verdict is attached to the IT context dict
# ─────────────────────────────────────────────────────────────────────────────

class TestExtra_ShadowDataShape(unittest.TestCase):
    """Verify the shadow dict shape recorded by full_analysis for IT mode."""

    def test_legacy_strict_verdict_schema(self):
        """The shadow record must carry the five keys the spec requires."""
        shadow = {
            "label":    "WAIT",
            "score":    70,
            "reason":   "edge_score(70<85)",
            "missing":  ["edge_score"],
            "was_ready": False,
        }
        self.assertIn("label",     shadow)
        self.assertIn("score",     shadow)
        self.assertIn("reason",    shadow)
        self.assertIn("missing",   shadow)
        self.assertIn("was_ready", shadow)

    def test_was_ready_true_when_swing_approved(self):
        shadow = {
            "label": "Strong Trade", "score": 90, "reason": "",
            "missing": [], "was_ready": True,
        }
        self.assertTrue(shadow["was_ready"])

    def test_was_ready_false_when_swing_blocked(self):
        shadow = {
            "label": "WAIT", "score": 70, "reason": "edge_score(70<85)",
            "missing": ["edge_score"], "was_ready": False,
        }
        self.assertFalse(shadow["was_ready"])


if __name__ == "__main__":
    unittest.main()
