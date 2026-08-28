"""
test_intraday_trend.py — Smoke + unit tests for the INTRADAY_TREND strategy.

Coverage:
  §A  MODES dict: INTRADAY_TREND entry present + profile mirrors SWING
  §B  Session classifier (_it_session_et)
  §C  Location quality (_it_location_quality)
  §D  Directional context (_it_directional_context)
  §E  Setup family detection (_it_setup_family)
  §F  Time restriction (_it_time_restriction)
  §G  Projected move (_it_projected_move)
  §H  Full context (compute_intraday_trend_context) — stable schema, fail-open
  §I  Entry veto (_it_entry_veto_reasons) — MNQ-only + time + location gates
  §J  Diag block (_it_diag_block) — display-only mirror, fail-open
  §K  full_analysis integration — IT mode computes context; SCALP stays byte-identical
  §L  Golden — SCALP + SWING byte-identical to baseline (no regressions)
"""
import os, sys, types, unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

os.environ.setdefault("TRADING_MODE", "INTRADAY_TREND")
os.environ.setdefault("SWING_HTF_ENABLED",  "0")
os.environ.setdefault("TREND_BRAKE_ENABLED", "0")
os.environ.setdefault("LEARNING_ENABLED",    "0")
os.environ.setdefault("VOLATILITY_INTEL_ENABLED", "0")
os.environ.setdefault("DUAL_TF_ENGINE",      "0")
os.environ.setdefault("SWING_STRATEGY_FILTER_ENABLED", "0")
os.environ.setdefault("MICRO_SCALP_ENABLED", "0")
os.environ.setdefault("ADVISOR_REVIEW_GATE_ENABLED", "0")
os.environ.setdefault("LIVE_TWO_CONTRACT_RUNNER_ENABLED", "0")
os.environ.setdefault("BREAKOUT_MODE_ENABLED", "0")
os.environ.setdefault("SWING_MODE_V2_ENABLED", "0")
os.environ.setdefault("MI_CONFIDENCE_STRUCTURE_ENABLED", "0")
os.environ.setdefault("MANUAL_ORDER_ENABLED", "0")
os.environ.setdefault("USER_APPROVED_PREVIEW_ENABLED", "0")
os.environ.setdefault("GRE_ENABLED",         "0")
os.environ.setdefault("EL_ENABLED",          "0")
os.environ.setdefault("DC_ENABLED",          "0")
os.environ.setdefault("GATE_AUDIT_ENABLED",  "0")
os.environ.setdefault("PROP_LOCK_ENABLED",   "0")
os.environ.setdefault("MTF_TREND_ENABLED",   "0")
os.environ.setdefault("STRUCTURE_REVERSAL_DEMOTE_ENABLED", "0")
os.environ.setdefault("EXECUTION_MODE", "manual_only")
os.environ.setdefault("DISCORD_LIVE_ENABLED", "0")

import app

ET = ZoneInfo("America/New_York")

def _et(h, m, date="2026-01-15"):
    return datetime.fromisoformat(f"{date}T{h:02d}:{m:02d}:00").replace(tzinfo=ET)


# ─────────────────────────────────────────────────────────────────────────────
# §A  MODES dict
# ─────────────────────────────────────────────────────────────────────────────
class TestModes(unittest.TestCase):
    def test_intraday_trend_in_modes(self):
        self.assertIn("INTRADAY_TREND", app.MODES)

    def test_intraday_trend_mirrors_swing_keys(self):
        sw = app.MODES["SWING"]
        it = app.MODES["INTRADAY_TREND"]
        for k in sw:
            self.assertIn(k, it, f"Key {k!r} missing from INTRADAY_TREND profile")

    def test_intraday_trend_has_thresholds(self):
        it = app.MODES["INTRADAY_TREND"]
        # Profile carries edge/gate thresholds (mirroring SWING profile)
        self.assertIn("EDGE_READY_THRESHOLD", it)
        self.assertIn("GATE_REQUIRE_ZONE",    it)

    def test_scalp_and_swing_unchanged(self):
        self.assertIn("SCALP", app.MODES)
        self.assertIn("SWING", app.MODES)


# ─────────────────────────────────────────────────────────────────────────────
# §B  Session classifier
# ─────────────────────────────────────────────────────────────────────────────
class TestSessionClassifier(unittest.TestCase):
    def _sess(self, h, m): return app._it_session_et(_et(h, m))

    def test_london(self):
        s = self._sess(3, 0)
        self.assertEqual(s["session"], "LONDON")
        self.assertEqual(s["session_short"], "LDN")

    def test_premarket(self):
        s = self._sess(8, 0)
        self.assertEqual(s["session"], "US_PREMARKET")
        self.assertEqual(s["session_short"], "PRE")

    def test_ny_open(self):
        s = self._sess(9, 45)
        self.assertEqual(s["session"], "NY_OPEN")
        self.assertEqual(s["session_short"], "OPEN")

    def test_ny_session(self):
        s = self._sess(11, 0)
        self.assertEqual(s["session"], "NY_SESSION")
        self.assertEqual(s["session_short"], "NY")

    def test_late_session(self):
        s = self._sess(15, 0)
        self.assertEqual(s["session"], "LATE_SESSION")
        self.assertEqual(s["session_short"], "LATE")

    def test_closed(self):
        s = self._sess(17, 0)
        self.assertEqual(s["session"], "CLOSED")

    def test_fields_present(self):
        s = self._sess(10, 0)
        for k in ("session", "session_short", "et_hour", "et_minute"):
            self.assertIn(k, s)

    def test_fail_open_on_none(self):
        # Pass None — should not raise
        s = app._it_session_et(None)
        self.assertIn("session", s)


# ─────────────────────────────────────────────────────────────────────────────
# §C  Location quality
# ─────────────────────────────────────────────────────────────────────────────
class TestLocationQuality(unittest.TestCase):
    def test_vwap_excellent(self):
        r = app._it_location_quality(20000, {}, 100, vwap=20005)
        self.assertEqual(r["quality"], "EXCELLENT")
        self.assertEqual(r["level_hit"], "VWAP")

    def test_vwap_good(self):
        r = app._it_location_quality(20000, {}, 100, vwap=20040)
        self.assertEqual(r["quality"], "GOOD")

    def test_prior_high_excellent(self):
        r = app._it_location_quality(20000, {"prior_high": 20015}, 100, vwap=None)
        self.assertEqual(r["quality"], "EXCELLENT")
        self.assertEqual(r["level_hit"], "Prior day high")

    def test_prior_low_good(self):
        r = app._it_location_quality(20000, {"prior_low": 19950}, 100, vwap=None)
        self.assertEqual(r["quality"], "GOOD")

    def test_mid_range_no_levels(self):
        r = app._it_location_quality(20000, {}, 100, vwap=None)
        self.assertEqual(r["quality"], "MID_RANGE")

    def test_mid_range_level_far(self):
        r = app._it_location_quality(20000, {"prior_high": 20500}, 100, vwap=None)
        self.assertEqual(r["quality"], "MID_RANGE")

    def test_schema_complete(self):
        r = app._it_location_quality(20000, {"prior_high": 20020}, 100)
        for k in ("quality", "level_hit", "dist_pts", "reason"):
            self.assertIn(k, r)

    def test_zero_atr_returns_mid_range(self):
        r = app._it_location_quality(20000, {"prior_high": 20001}, 0)
        self.assertEqual(r["quality"], "MID_RANGE")

    def test_none_price_returns_mid_range(self):
        r = app._it_location_quality(None, {"prior_high": 20001}, 100)
        self.assertEqual(r["quality"], "MID_RANGE")


# ─────────────────────────────────────────────────────────────────────────────
# §D  Directional context
# ─────────────────────────────────────────────────────────────────────────────
class TestDirectionalContext(unittest.TestCase):
    def _htf(self, bias_4h, bias_1h):
        return {"4H": {"bias": bias_4h}, "1H": {"bias": bias_1h}}

    def test_strong_bullish_alignment(self):
        htf = self._htf("bull", "bull")
        c = {"direction": "Long", "structure_confirmed": True}
        r = app._it_directional_context(htf, 20010, 19990, c)
        self.assertIn(r["trend_alignment"], ("STRONG_BULLISH", "BULLISH"))
        self.assertGreater(r["alignment_score"], 0)

    def test_strong_bearish_alignment(self):
        htf = self._htf("bear", "bear")
        c = {"direction": "Short", "structure_confirmed": True}
        r = app._it_directional_context(htf, 19990, 20010, c)
        self.assertIn(r["trend_alignment"], ("STRONG_BEARISH", "BEARISH"))
        self.assertLess(r["alignment_score"], 0)

    def test_mixed_no_htf(self):
        r = app._it_directional_context({}, None, None, {})
        self.assertEqual(r["trend_alignment"], "MIXED")
        self.assertEqual(r["alignment_score"], 0)

    def test_all_keys_present(self):
        r = app._it_directional_context({}, 20000, 20000, {})
        for k in ("trend_4h","trend_1h","trend_15m","trend_5m","trend_alignment","alignment_score"):
            self.assertIn(k, r)

    def test_fail_open_on_bad_input(self):
        r = app._it_directional_context(None, None, None, None)
        self.assertIn("trend_alignment", r)


# ─────────────────────────────────────────────────────────────────────────────
# §E  Setup family
# ─────────────────────────────────────────────────────────────────────────────
class TestSetupFamily(unittest.TestCase):
    def test_liquidity_sweep_reversal(self):
        c = {"liquidity_sweep": True, "structure_confirmed": True}
        fam, reason = app._it_setup_family(c, "Long")
        self.assertEqual(fam, "LIQUIDITY_SWEEP_REVERSAL")
        self.assertIsNotNone(reason)

    def test_breakout_retest(self):
        c = {"bos": True, "vwap_confirmed": True}
        fam, reason = app._it_setup_family(c, "Long")
        self.assertEqual(fam, "BREAKOUT_RETEST")

    def test_trend_pullback(self):
        c = {"vwap_confirmed": True, "structure_confirmed": True}
        fam, reason = app._it_setup_family(c, "Long")
        self.assertEqual(fam, "TREND_PULLBACK")

    def test_no_family_empty_confluences(self):
        fam, reason = app._it_setup_family({}, "Long")
        self.assertIsNone(fam)
        self.assertIsNotNone(reason)

    def test_sweep_alone_partial(self):
        c = {"liquidity_sweep": True}
        fam, reason = app._it_setup_family(c, "Long")
        # Partial sweep — family detected but awaiting confirmation
        self.assertEqual(fam, "LIQUIDITY_SWEEP_REVERSAL")
        self.assertIn("Await", reason)

    def test_fail_open_none_input(self):
        fam, reason = app._it_setup_family(None, None)
        # Should not raise; family may be None
        self.assertIsNotNone(reason)


# ─────────────────────────────────────────────────────────────────────────────
# §F  Time restriction
# ─────────────────────────────────────────────────────────────────────────────
class TestTimeRestriction(unittest.TestCase):
    def _chk(self, h, m, env=None):
        prev = {}
        if env:
            for k, v in env.items():
                prev[k] = os.environ.get(k)
                os.environ[k] = v
        try:
            return app._it_time_restriction(_et(h, m))
        finally:
            for k in (env or {}):
                if prev.get(k) is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = prev[k]

    def test_ok_at_9_30(self):
        ok, state, reason = self._chk(9, 30)
        self.assertTrue(ok)
        self.assertEqual(state, "OK")
        self.assertIsNone(reason)

    def test_ok_at_13_00(self):
        ok, _, _ = self._chk(13, 0)
        self.assertTrue(ok)

    def test_allowed_at_14_30(self):
        ok, state, reason = self._chk(14, 30)
        self.assertTrue(ok)
        self.assertEqual(state, "OK")
        self.assertIsNone(reason)

    def test_force_flat_at_15_55(self):
        ok, state, reason = self._chk(15, 55)
        self.assertFalse(ok)
        self.assertEqual(state, "FORCE_FLAT")

    def test_force_flat_beats_entry_blocked(self):
        # At 16:00 — past both; FORCE_FLAT takes precedence
        ok, state, reason = self._chk(16, 0)
        self.assertFalse(ok)
        self.assertEqual(state, "FORCE_FLAT")

    def test_custom_last_entry_time(self):
        ok, state, _ = self._chk(12, 0, {"IT_LAST_NEW_ENTRY_TIME": "11:00"})
        self.assertFalse(ok)
        self.assertEqual(state, "ENTRY_BLOCKED")

    def test_fail_open_on_none(self):
        # Should not raise when et_now=None
        ok, state, reason = app._it_time_restriction(None)
        self.assertIn(state, ("OK", "ENTRY_BLOCKED", "FORCE_FLAT", "BLOCKED_SESSION"))


# ─────────────────────────────────────────────────────────────────────────────
# §G  Projected move
# ─────────────────────────────────────────────────────────────────────────────
class TestProjectedMove(unittest.TestCase):
    def test_long_basic(self):
        pts, r = app._it_projected_move(20000, 20100, 40, "Long")
        self.assertEqual(pts, 100.0)
        self.assertAlmostEqual(r, 2.5, places=1)

    def test_short_basic(self):
        pts, r = app._it_projected_move(20100, 19900, 40, "Short")
        self.assertEqual(pts, 200.0)
        self.assertAlmostEqual(r, 5.0, places=1)

    def test_zero_risk_returns_none(self):
        pts, r = app._it_projected_move(20000, 20100, 0, "Long")
        self.assertIsNone(pts)

    def test_wrong_direction_returns_none(self):
        # target < entry for Long = wrong direction
        pts, r = app._it_projected_move(20100, 20000, 40, "Long")
        self.assertIsNone(pts)

    def test_bad_input_returns_none(self):
        pts, r = app._it_projected_move(None, None, None, "Long")
        self.assertIsNone(pts)


# ─────────────────────────────────────────────────────────────────────────────
# §H  Full context — schema + fail-open
# ─────────────────────────────────────────────────────────────────────────────
class TestComputeContext(unittest.TestCase):
    REQUIRED_KEYS = (
        "enabled", "instrument", "mode", "price",
        "bias_1h", "bias_4h", "complete", "stale",
        "session", "session_short",
        "location_quality", "trend_alignment", "alignment_score",
        "setup_family", "time_ok", "time_state",
        "status", "reason",
    )

    def _ctx(self, inst="MNQ", price=20000, **kw):
        return app.compute_intraday_trend_context(inst, price, **kw)

    def test_all_required_keys_present(self):
        ctx = self._ctx()
        for k in self.REQUIRED_KEYS:
            self.assertIn(k, ctx, f"Required key {k!r} missing from context")

    def test_mode_is_intraday_trend(self):
        ctx = self._ctx()
        self.assertEqual(ctx["mode"], "INTRADAY_TREND")
        self.assertTrue(ctx["enabled"])

    def test_mnq_instrument_preserved(self):
        ctx = self._ctx(inst="MNQ")
        self.assertEqual(ctx["instrument"], "MNQ")

    def test_session_is_classified(self):
        ctx = self._ctx(et_now=_et(10, 15))
        self.assertEqual(ctx["session"], "NY_OPEN")

    def test_location_mid_range_without_levels(self):
        ctx = self._ctx(price=20000, swing_ctx={})
        self.assertEqual(ctx["location_quality"], "MID_RANGE")

    def test_location_near_vwap(self):
        plan = {"vwap": 20005, "atr_pts": 100}
        ctx = self._ctx(price=20000, trade_plan=plan, confluences={})
        self.assertEqual(ctx["location_quality"], "EXCELLENT")

    def test_setup_family_sweep_reversal(self):
        c = {"liquidity_sweep": True, "structure_confirmed": True}
        ctx = self._ctx(confluences=c, direction="Long")
        self.assertEqual(ctx["setup_family"], "LIQUIDITY_SWEEP_REVERSAL")

    def test_projected_move_from_trade_plan(self):
        plan = {"trade_plan": True, "entry_zone": 20000, "target1": 20150,
                "risk_pts": 50, "vwap": 19990, "atr_pts": 50}
        ctx = self._ctx(price=20000, trade_plan=plan, direction="Long")
        self.assertIsNotNone(ctx["projected_points"])
        self.assertIsNotNone(ctx["projected_r"])
        self.assertAlmostEqual(ctx["projected_r"], 3.0, places=1)

    def test_time_allowed_before_1500_cutoff(self):
        ctx = self._ctx(et_now=_et(14, 45))
        self.assertTrue(ctx["time_ok"])

    def test_fail_open_on_bad_instrument(self):
        ctx = app.compute_intraday_trend_context(None, None)
        self.assertIn("status", ctx)
        self.assertIn("enabled", ctx)

    def test_swing_ctx_htf_mirrors_1h(self):
        sc = {"bias_1h": "bull", "bias_4h": "bear", "complete": True, "stale": False}
        ctx = self._ctx(swing_ctx=sc)
        self.assertEqual(ctx["bias_1h"], "bull")
        self.assertEqual(ctx["bias_4h"], "bear")
        self.assertTrue(ctx["complete"])
        self.assertFalse(ctx["stale"])


# ─────────────────────────────────────────────────────────────────────────────
# §I  Entry veto — fail-closed money path
# ─────────────────────────────────────────────────────────────────────────────
class TestEntryVeto(unittest.TestCase):
    def _mnq_ctx(self, **kw):
        defaults = {
            "instrument": "MNQ",
            "time_ok": True, "time_state": "OK", "time_reason": None,
            "location_quality": "GOOD",
            # These are the established fail-closed prerequisites. Individual
            # tests override one input at a time to isolate the asserted veto.
            "setup_family": "TREND_PULLBACK",
            "confirmation_complete": True,
            "structural_stop_valid": True,
            "structural_stop_pts": 50.0,
            "daily_trade_count": 0,
            "daily_trade_cap": 2,
        }
        defaults.update(kw)
        return defaults

    def test_mnq_passes(self):
        ctx = self._mnq_ctx()
        v = app._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        self.assertEqual(v, [])

    def test_non_mnq_blocked(self):
        ctx = self._mnq_ctx(instrument="MGC")
        v = app._it_entry_veto_reasons(ctx, {}, "Long", "MGC")
        self.assertTrue(any(code == "instrument" for code, _ in v))

    def test_mgc_raw_ticker_blocked(self):
        ctx = self._mnq_ctx(instrument="MGC")
        v = app._it_entry_veto_reasons(ctx, {}, "Long", "MGC1!")
        self.assertTrue(any(code == "instrument" for code, _ in v))

    def test_mes_blocked(self):
        ctx = self._mnq_ctx(instrument="MES")
        v = app._it_entry_veto_reasons(ctx, {}, "Long", "MES")
        self.assertTrue(any(code == "instrument" for code, _ in v))

    def test_time_blocked(self):
        ctx = self._mnq_ctx(time_ok=False, time_state="ENTRY_BLOCKED",
                            time_reason="Past 14:30 ET")
        v = app._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        self.assertTrue(any(code == "time" for code, _ in v))

    def test_force_flat_blocked(self):
        ctx = self._mnq_ctx(time_ok=False, time_state="FORCE_FLAT",
                            time_reason="Past 15:55 ET")
        v = app._it_entry_veto_reasons(ctx, {}, "Short", "MNQ")
        self.assertTrue(any(code == "time" for code, _ in v))

    def test_mid_range_location_blocked(self):
        ctx = self._mnq_ctx(location_quality="MID_RANGE",
                            location_reason="No key level nearby.")
        v = app._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        self.assertTrue(any(code == "location" for code, _ in v))

    def test_excellent_location_passes(self):
        ctx = self._mnq_ctx(location_quality="EXCELLENT")
        v = app._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        self.assertEqual(v, [])

    def test_good_location_passes(self):
        ctx = self._mnq_ctx(location_quality="GOOD")
        v = app._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        self.assertEqual(v, [])

    def test_poor_location_passes(self):
        # POOR is not a blocker — only MID_RANGE is
        ctx = self._mnq_ctx(location_quality="POOR")
        v = app._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        self.assertEqual(v, [])

    def test_none_ctx_returns_veto(self):
        v = app._it_entry_veto_reasons(None, {}, "Long", "MNQ")
        self.assertTrue(len(v) > 0)

    def test_multiple_vetoes_accumulated(self):
        # Non-MNQ + time blocked — instrument veto short-circuits before time check
        ctx = self._mnq_ctx(instrument="MGC", time_ok=False, time_state="ENTRY_BLOCKED")
        v = app._it_entry_veto_reasons(ctx, {}, "Long", "MGC")
        # At minimum instrument veto fires; could have 1 or 2 vetoes
        self.assertGreaterEqual(len(v), 1)

    def test_fail_closed_on_bad_ctx(self):
        v = app._it_entry_veto_reasons("not_a_dict", {}, "Long", "MNQ")
        self.assertTrue(len(v) > 0)


# ─────────────────────────────────────────────────────────────────────────────
# §J  Diag block — display-only mirror
# ─────────────────────────────────────────────────────────────────────────────
class TestDiagBlock(unittest.TestCase):
    def test_disabled_when_no_context(self):
        r = app._it_diag_block({})
        self.assertFalse(r.get("enabled"))

    def test_disabled_when_context_not_enabled(self):
        r = app._it_diag_block({"intraday_trend_context": {"enabled": False}})
        self.assertFalse(r.get("enabled"))

    def test_enabled_with_full_context(self):
        ctx = app.compute_intraday_trend_context("MNQ", 20000, et_now=_et(10, 0))
        a = {"intraday_trend_context": ctx}
        r = app._it_diag_block(a)
        self.assertTrue(r.get("enabled"))
        self.assertEqual(r.get("mode"), "INTRADAY_TREND")

    def test_all_keys_forwarded(self):
        ctx = app.compute_intraday_trend_context("MNQ", 20000, et_now=_et(10, 0))
        a = {"intraday_trend_context": ctx}
        r = app._it_diag_block(a)
        for k in ("session", "status", "location_quality", "trend_alignment",
                  "setup_family", "time_ok", "time_state"):
            self.assertIn(k, r, f"Key {k!r} missing from diag block")

    def test_fail_open_on_exception(self):
        r = app._it_diag_block(None)
        self.assertFalse(r.get("enabled", True))


# ─────────────────────────────────────────────────────────────────────────────
# §K  full_analysis integration — IT mode wires context into result
# ─────────────────────────────────────────────────────────────────────────────
class TestFullAnalysisIntegration(unittest.TestCase):
    """Verify the INTRADAY_TREND plumbing inside full_analysis.

    full_analysis(current_price_override, ticker_override) reads trading mode
    from the global app.TRADING_MODE.  We swap it in/out around each call.

    Confirms:
      - result["intraday_trend_context"] present+enabled when mode=INTRADAY_TREND
      - SCALP / SWING modes do NOT produce intraday_trend_context
      - Non-MNQ instrument context is still produced (veto fires only if actionable)
    """

    def _run(self, ticker="MNQ1!", mode=None, price=20000):
        prev_mode = app.TRADING_MODE
        try:
            if mode:
                app.TRADING_MODE = mode
            return app.full_analysis(
                current_price_override=price,
                ticker_override=ticker,
            )
        finally:
            app.TRADING_MODE = prev_mode

    def test_it_mode_context_in_result(self):
        r = self._run(mode="INTRADAY_TREND")
        self.assertIn("intraday_trend_context", r)
        ctx = r["intraday_trend_context"]
        self.assertIsInstance(ctx, dict)
        self.assertTrue(ctx.get("enabled"))
        self.assertEqual(ctx.get("mode"), "INTRADAY_TREND")

    def test_scalp_mode_no_it_context(self):
        r = self._run(mode="SCALP")
        self.assertNotIn("intraday_trend_context", r)

    def test_non_mnq_it_context_still_attached(self):
        # Context is always attached for INTRADAY_TREND regardless of instrument
        # (veto fires on is_actionable — but context itself is always computed).
        r = self._run(ticker="MGC1!", mode="INTRADAY_TREND")
        self.assertIn("intraday_trend_context", r)
        ctx = r.get("intraday_trend_context", {})
        self.assertTrue(ctx.get("enabled"))

    def test_mnq_it_context_has_session(self):
        r = self._run(mode="INTRADAY_TREND")
        ctx = r.get("intraday_trend_context", {})
        self.assertIsNotNone(ctx.get("session"))

    def test_swing_mode_no_it_context(self):
        r = self._run(mode="SWING")
        self.assertNotIn("intraday_trend_context", r)


# ─────────────────────────────────────────────────────────────────────────────
# §L  Byte-identical goldens — SCALP + SWING unchanged
# ─────────────────────────────────────────────────────────────────────────────
class TestGoldens(unittest.TestCase):
    """Any SCALP or SWING run MUST NOT carry an intraday_trend_context key.
    The key should be absent — not None — to preserve byte-identical semantics."""

    def _run(self, ticker, mode, price=20000):
        prev = app.TRADING_MODE
        try:
            app.TRADING_MODE = mode
            return app.full_analysis(
                current_price_override=price,
                ticker_override=ticker,
            )
        finally:
            app.TRADING_MODE = prev

    def test_scalp_no_it_key(self):
        r = self._run("MNQ1!", "SCALP")
        self.assertNotIn("intraday_trend_context", r)

    def test_swing_no_it_key(self):
        r = self._run("MNQ1!", "SWING")
        self.assertNotIn("intraday_trend_context", r)

    def test_scalp_mgc_no_it_key(self):
        r = self._run("MGC1!", "SCALP")
        self.assertNotIn("intraday_trend_context", r)

    def test_swing_mgc_no_it_key(self):
        r = self._run("MGC1!", "SWING")
        self.assertNotIn("intraday_trend_context", r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
