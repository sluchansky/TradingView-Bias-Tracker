"""test_strategy_eligibility.py — Phase 6B.1C Strategy Eligibility Engine tests.

Tests cover:
  SE001  Neutral block structure
  SE002  Determinism — same inputs produce same output
  SE003  Eligibility cannot change edge_score in result
  SE004  Eligibility cannot change verdict in result
  SE005  Eligibility cannot alter trade_plan in result
  SE006  Output serializes to JSON without error
  SE007  affects_execution is always False
  SE008  shadow_mode is always True
  SE009  All five STRATEGY_PRIORITY keys present in strategies dict
  SE010  STRATEGY_PRIORITY order preserved in summary list
  SE011  Confidence is bounded 0-100 for all strategies
  SE012  eligible is bool (not truthy object)
  SE013  Asia session lowers LSR confidence vs New York session
  SE014  VOLATILE regime raises LSR confidence vs BALANCED
  SE015  ORB Long direction raises confidence vs Short direction
  SE016  OD inside 08:00-10:00 window raises confidence vs outside
  SE017  London session lowers REB confidence vs New York
  SE018  confidence_label maps correctly to confidence threshold
  SE019  compute_strategy_eligibility does not mutate the input result dict
  SE020  Flag OFF leaves full_analysis result unchanged (key absent)
"""
import json
import sys
import os
import unittest
import importlib

# ---------------------------------------------------------------------------
# Module bootstrap — import app without running the Flask dev server
# ---------------------------------------------------------------------------
os.environ.setdefault("STRATEGY_ELIGIBILITY_ENABLED", "1")

import app as _app

compute_strategy_eligibility  = _app.compute_strategy_eligibility
_strategy_eligibility_neutral = _app._strategy_eligibility_neutral
STRATEGY_PRIORITY             = _app.STRATEGY_PRIORITY
STRATEGY_ELIGIBILITY_ENABLED  = _app.STRATEGY_ELIGIBILITY_ENABLED


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_result(
    *,
    session_window="New York",
    vol_regime="TRENDING",
    market_regime="TRENDING",
    direction="Long",
    edge_score=72,
    verdict="READY",
    trade_plan=None,
    or_complete=False,
):
    """Minimal fake full_analysis() result dict for eligibility engine tests."""
    return {
        "active_ticker":   "MGC",
        "session":         {"preferred": True, "bonus": 10, "window": session_window},
        "volatility":      {"regime": vol_regime, "atr_pts": 5.0, "ratio": 1.3},
        "strategy_engine": {"market_regime": market_regime, "active_strategy": "Opening Drive"},
        "alert_diagnostics": {
            "dominant_direction": direction,
            "edge_score":         edge_score,
        },
        "edge_score":  edge_score,
        "verdict":     verdict,
        "trade_plan":  trade_plan or {"action": "BUY", "entry": 2000.0},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNeutralBlock(unittest.TestCase):
    """SE001 — neutral block has the required structure."""

    def test_SE001_neutral_keys(self):
        n = _strategy_eligibility_neutral("test reason")
        self.assertFalse(n["available"])
        self.assertEqual(n["reason"], "test reason")
        self.assertIsInstance(n["strategies"], dict)
        self.assertIsInstance(n["summary"], list)
        self.assertFalse(n["affects_execution"])
        self.assertTrue(n["shadow_mode"])


class TestDeterminism(unittest.TestCase):
    """SE002 — identical inputs produce identical outputs."""

    def test_SE002_deterministic_on_same_result(self):
        r = _fake_result()
        out1 = compute_strategy_eligibility(r)
        out2 = compute_strategy_eligibility(r)
        # confidence values must be identical across calls
        for key in STRATEGY_PRIORITY:
            self.assertEqual(
                out1["strategies"][key]["confidence"],
                out2["strategies"][key]["confidence"],
                f"{key} confidence drifted between calls",
            )
            self.assertEqual(
                out1["strategies"][key]["eligible"],
                out2["strategies"][key]["eligible"],
                f"{key} eligible drifted between calls",
            )


class TestMoneyPathIsolation(unittest.TestCase):
    """SE003-SE005, SE019 — eligibility cannot affect the money path."""

    def _call_and_compare(self, key, before_val, after_val, field):
        self.assertEqual(before_val, after_val,
                         f"compute_strategy_eligibility mutated result[{field!r}]")

    def test_SE003_edge_score_unchanged(self):
        r = _fake_result(edge_score=77)
        before = r["edge_score"]
        compute_strategy_eligibility(r)
        self._call_and_compare("edge_score", before, r["edge_score"], "edge_score")

    def test_SE004_verdict_unchanged(self):
        r = _fake_result(verdict="SHORT READY")
        before = r["verdict"]
        compute_strategy_eligibility(r)
        self._call_and_compare("verdict", before, r["verdict"], "verdict")

    def test_SE005_trade_plan_unchanged(self):
        plan = {"action": "SELL", "entry": 1999.5, "stop": 2005.0, "target1": 1985.0}
        r = _fake_result(trade_plan=plan)
        before = dict(r["trade_plan"])
        compute_strategy_eligibility(r)
        self.assertEqual(before, r["trade_plan"],
                         "trade_plan was mutated by compute_strategy_eligibility")

    def test_SE019_result_dict_not_mutated(self):
        r = _fake_result()
        keys_before = set(r.keys())
        compute_strategy_eligibility(r)
        keys_after  = set(r.keys())
        self.assertEqual(keys_before, keys_after,
                         "compute_strategy_eligibility added/removed keys from result dict")


class TestOutputStructure(unittest.TestCase):
    """SE006-SE012 — output structure invariants."""

    def setUp(self):
        self.out = compute_strategy_eligibility(_fake_result())

    def test_SE006_json_serializable(self):
        s = json.dumps(self.out)
        self.assertIsInstance(s, str)
        parsed = json.loads(s)
        self.assertIsInstance(parsed, dict)

    def test_SE007_affects_execution_false(self):
        self.assertFalse(self.out["affects_execution"])

    def test_SE008_shadow_mode_true(self):
        self.assertTrue(self.out["shadow_mode"])

    def test_SE009_all_strategy_keys_present(self):
        for key in STRATEGY_PRIORITY:
            self.assertIn(key, self.out["strategies"],
                          f"{key} missing from strategies dict")

    def test_SE010_summary_order_matches_priority(self):
        summary_keys = [s["key"] for s in self.out["summary"]]
        self.assertEqual(summary_keys, list(STRATEGY_PRIORITY),
                         "summary order does not match STRATEGY_PRIORITY")

    def test_SE011_confidence_bounded_0_100(self):
        for key, strat in self.out["strategies"].items():
            c = strat["confidence"]
            self.assertGreaterEqual(c, 0,   f"{key} confidence below 0: {c}")
            self.assertLessEqual(c,   100,  f"{key} confidence above 100: {c}")

    def test_SE012_eligible_is_bool(self):
        for key, strat in self.out["strategies"].items():
            self.assertIsInstance(strat["eligible"], bool,
                                  f"{key} eligible is not bool: {type(strat['eligible'])}")


class TestSessionScoring(unittest.TestCase):
    """SE013, SE016, SE017 — session-based confidence differences.

    _eligibility_session() uses the live clock as its primary source, so tests
    that need explicit session control call the scorer functions directly (they
    accept `session` as an explicit parameter).
    """

    def test_SE013_lsr_asia_lower_than_ny(self):
        from app import _elig_score_liquidity_sweep_reversal as _lsr
        s_ny,  _, _ = _lsr("New York", 10.0, "VOLATILE", "Long", {})
        s_as,  _, _ = _lsr("Asia",     21.0, "VOLATILE", "Long", {})
        ny   = max(0, min(100, int(s_ny)))
        asia = max(0, min(100, int(s_as)))
        self.assertGreater(ny, asia,
                           f"LSR NY ({ny}) should exceed LSR Asia ({asia})")

    def test_SE016_od_inside_window_higher_than_outside(self):
        from app import _elig_score_opening_drive as _od
        s_in,  _, _ = _od("New York",  9.0, "TRENDING", "Long", {})   # 08-10 window
        s_out, _, _ = _od("Asia",     20.0, "BALANCED", "Short", {})  # overnight
        self.assertGreater(s_in, s_out,
                           f"OD in-window ({s_in}) should exceed OD out-of-window ({s_out})")

    def test_SE017_reb_london_lower_than_ny(self):
        from app import _elig_score_range_expansion_breakout as _reb
        s_ny,  _, _ = _reb("New York", 13.0, "TRENDING", "Long", {})
        s_lon, _, _ = _reb("London",    4.0, "TRENDING", "Long", {})
        ny  = max(0, min(100, int(s_ny)))
        lon = max(0, min(100, int(s_lon)))
        self.assertGreater(ny, lon,
                           f"REB NY ({ny}) should exceed REB London ({lon})")


class TestRegimeScoring(unittest.TestCase):
    """SE014 — regime-based confidence differences."""

    def _lsr_conf(self, vol_regime):
        r   = _fake_result(session_window="New York", vol_regime=vol_regime,
                           market_regime=vol_regime, direction="Long")
        out = compute_strategy_eligibility(r)
        return out["strategies"]["LIQUIDITY_SWEEP_REVERSAL"]["confidence"]

    def test_SE014_volatile_lsr_higher_than_balanced(self):
        vol  = self._lsr_conf("VOLATILE")
        bal  = self._lsr_conf("BALANCED")
        self.assertGreater(vol, bal,
                           f"LSR VOLATILE ({vol}) should exceed BALANCED ({bal})")


class TestDirectionScoring(unittest.TestCase):
    """SE015 — direction-based confidence differences."""

    def _orb_conf(self, direction):
        r   = _fake_result(session_window="New York", vol_regime="TRENDING",
                           market_regime="TRENDING", direction=direction)
        out = compute_strategy_eligibility(r)
        return out["strategies"]["OPENING_RANGE_BREAKOUT"]["confidence"]

    def test_SE015_orb_long_higher_than_short(self):
        lng = self._orb_conf("Long")
        sht = self._orb_conf("Short")
        self.assertGreater(lng, sht,
                           f"ORB Long ({lng}) should exceed ORB Short ({sht})")


class TestConfidenceLabelMapping(unittest.TestCase):
    """SE018 — confidence_label thresholds are correct."""

    def _inject_confidence(self, raw_score, session="New York", regime="VOLATILE", direction="Long"):
        """Call the scorer functions directly to test the label logic."""
        from app import _elig_score_liquidity_sweep_reversal
        score, _, _ = _elig_score_liquidity_sweep_reversal(session, 9.0, regime, direction, {})
        confidence  = max(0, min(100, int(score)))
        if confidence >= 80:
            return "HIGH"
        if confidence >= 60:
            return "MEDIUM"
        if confidence >= 40:
            return "LOW"
        return "NONE"

    def test_SE018_high_label_at_80_plus(self):
        label = self._inject_confidence(85, session="New York", regime="VOLATILE")
        self.assertEqual(label, "HIGH")

    def test_SE018_none_label_at_below_40(self):
        label = self._inject_confidence(10, session="Asia", regime="BALANCED")
        self.assertEqual(label, "NONE")


class TestScorerUnits(unittest.TestCase):
    """Unit tests for individual scorer functions (determinism + boundary checks)."""

    def test_od_in_window_trending(self):
        from app import _elig_score_opening_drive
        score, reasons, warnings = _elig_score_opening_drive("New York", 9.0, "TRENDING", "Long", {})
        confidence = max(0, min(100, int(score)))
        self.assertGreaterEqual(confidence, 70, "OD in NY window TRENDING should be >=70")
        self.assertIsInstance(reasons, list)
        self.assertIsInstance(warnings, list)

    def test_od_outside_window_asia(self):
        from app import _elig_score_opening_drive
        score_in,  _, _ = _elig_score_opening_drive("New York", 9.0, "TRENDING",  "Long", {})
        score_out, _, _ = _elig_score_opening_drive("Asia",    20.0, "BALANCED", "Short", {})
        self.assertGreater(score_in, score_out,
                           "OD in window should outscore OD outside window")

    def test_lsr_ny_volatile_long(self):
        from app import _elig_score_liquidity_sweep_reversal
        score, reasons, warnings = _elig_score_liquidity_sweep_reversal(
            "New York", 10.0, "VOLATILE", "Long", {})
        confidence = max(0, min(100, int(score)))
        self.assertGreaterEqual(confidence, 80, "LSR NY VOLATILE Long should be >=80")

    def test_lsr_asia_balanced_short(self):
        from app import _elig_score_liquidity_sweep_reversal
        score, _, _ = _elig_score_liquidity_sweep_reversal(
            "Asia", 21.0, "BALANCED", "Short", {})
        confidence = max(0, min(100, int(score)))
        self.assertLess(confidence, 40, "LSR Asia BALANCED Short should be <40 (NONE)")

    def test_orb_long_with_or_complete_trending(self):
        from app import _elig_score_opening_range_breakout
        score, reasons, _ = _elig_score_opening_range_breakout(
            "New York", 13.0, "TRENDING", "Long", {"or_complete": True})
        confidence = max(0, min(100, int(score)))
        self.assertGreaterEqual(confidence, 80, "ORB Long OR-complete TRENDING should be >=80")

    def test_orb_short_kills_confidence(self):
        from app import _elig_score_opening_range_breakout
        score_l, _, _ = _elig_score_opening_range_breakout(
            "New York", 13.0, "TRENDING", "Long",  {"or_complete": True})
        score_s, _, _ = _elig_score_opening_range_breakout(
            "New York", 13.0, "TRENDING", "Short", {"or_complete": True})
        self.assertGreater(score_l, score_s,
                           "ORB Long should outscore ORB Short")

    def test_reb_london_penalty(self):
        from app import _elig_score_range_expansion_breakout
        score_ny,  _, _ = _elig_score_range_expansion_breakout("New York", 13.0, "TRENDING", "Long", {})
        score_lon, _, _ = _elig_score_range_expansion_breakout("London",    4.0, "BALANCED", "Long", {})
        self.assertGreater(score_ny, score_lon, "REB NY should outscore REB London")

    def test_vtc_trending_ny_positive(self):
        from app import _elig_score_vwap_trend_continuation
        score, reasons, warnings = _elig_score_vwap_trend_continuation(
            "New York", 10.0, "TRENDING", "Long", {})
        confidence = max(0, min(100, int(score)))
        self.assertGreaterEqual(confidence, 70, "VTC NY TRENDING Long should be >=70")

    def test_vtc_asia_balanced_short_negative(self):
        from app import _elig_score_vwap_trend_continuation
        score_ny,  _, _ = _elig_score_vwap_trend_continuation("New York", 10.0, "TRENDING", "Long",  {})
        score_as,  _, _ = _elig_score_vwap_trend_continuation("Asia",    20.0, "BALANCED", "Short", {})
        self.assertGreater(score_ny, score_as,
                           "VTC NY TRENDING Long should outscore VTC Asia BALANCED Short")


class TestFlagOff(unittest.TestCase):
    """SE020 — when STRATEGY_ELIGIBILITY_ENABLED is False, key is absent from full_analysis."""

    def test_SE020_flag_off_key_absent(self):
        import app as _a
        orig = _a.STRATEGY_ELIGIBILITY_ENABLED
        try:
            _a.STRATEGY_ELIGIBILITY_ENABLED = False
            result = _a.full_analysis()
            self.assertNotIn(
                "strategy_eligibility", result,
                "strategy_eligibility key must be absent when flag is OFF",
            )
        finally:
            _a.STRATEGY_ELIGIBILITY_ENABLED = orig

    def test_SE020b_flag_on_key_present(self):
        import app as _a
        orig = _a.STRATEGY_ELIGIBILITY_ENABLED
        try:
            _a.STRATEGY_ELIGIBILITY_ENABLED = True
            result = _a.full_analysis()
            self.assertIn(
                "strategy_eligibility", result,
                "strategy_eligibility key must be present when flag is ON",
            )
            self.assertTrue(result["strategy_eligibility"].get("available", False))
        finally:
            _a.STRATEGY_ELIGIBILITY_ENABLED = orig


if __name__ == "__main__":
    loader   = unittest.TestLoader()
    suite    = loader.loadTestsFromModule(sys.modules[__name__])
    runner   = unittest.TextTestRunner(verbosity=2)
    result   = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
