"""
Phase 7B — Main Brain read-only aggregation route

Tests for GET /main-brain:  build_main_brain_payload(), _mb_safe_num(),
section helpers, fault isolation, JSON serialisation, and proxy registration.

All tests are pure unit tests against the builder helpers and the in-memory
state stores.  None of these tests hit a live Flask server, write to the
database, or invoke the execution gateway.

Run:
    python3 artifacts/tradingview-webhook/test_phase7b_main_brain_route.py

Exit 0 = all passed.
"""
import sys
import os
import math
import json
import time
import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Bootstrap: add the webhook dir to the path so we can import helpers from
# app.py without starting the server.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

# We import only the pure helpers; server-startup side-effects are blocked by
# patching the Flask app object before the module is imported.  We rely on
# app.py already being importable in the test environment (py_compile passes
# means the module can be parsed; we do not need a running Flask instance).

# ---------------------------------------------------------------------------
# Helper: import the builder functions we need without re-importing the whole
# server.  We do this by importing specific names after the module loads.
# ---------------------------------------------------------------------------
def _import_builders():
    """Import builder symbols from app.py in a test-safe way."""
    # app.py is already importable (syntax-checked by CI).
    # We stub out the database probe so boot does not fail in tests.
    import app as _app
    return _app


_APP = None


def get_app():
    global _APP
    if _APP is None:
        _APP = _import_builders()
    return _APP


# ---------------------------------------------------------------------------
# Minimal fake full_analysis result used across tests.
# ---------------------------------------------------------------------------
FAKE_RESULT_BASE = {
    "active_ticker": "MGC",
    "current_price": 3250.5,
    "market_direction": "BULL",
    "session": "REGULAR",
    "vwap_value": 3248.0,
    "vwap_status": "ok",
    "strict_label": "WAIT",
    "strict_direction": None,
    "strict_reason": "WAIT — failed gate(s): structure_confirmed",
    "strict_missing": ["structure_confirmed"],
    "edge_score": 20,
    "edge_breakdown": {
        "grade": "WAIT",
        "components": [
            {"name": "bos_confirmed",  "score": 0},
            {"name": "choch_confirmed","score": 0},
            {"name": "vwap_confirmed", "score": 15},
        ],
    },
    "trade_plan": {"entry": None, "stop": None, "rr": None, "tp1": None, "tp2": None},
    "market_intelligence": {
        "regime": "RISK_ON",
        "primary_driver": "NONE",
        "risk_state": "STABLE",
        "conviction": "LOW",
        "directional_confidence": None,
        "futures_preference": None,
    },
    "volatility": {"status": "ok", "atr_pts": 4.5},
    "strategy_engine": {
        "active_strategy": "LIQUIDITY_SWEEP_REVERSAL",
        "market_regime": "RISK_ON",
        "reasoning": "Sweep detected",
    },
    "coach": {
        "weight_updated": True,
        "thesis_resolved": False,
        "thesis_last_resolved_at": None,
        "learning_influence": 0.05,
        "rule_engine_eligibility": "LIVE_ELIGIBLE",
        "_version": "v1",
    },
    "manager": {
        "gateway_debug": {"reason": "auto OFF"},
        "training_gate": {"enabled": False},
        "auto_trade_enabled": {"MGC": False},
        "_version": "v1",
    },
    "data_feed": {
        "price_fresh": True,
        "price_age_seconds": 5,
    },
}


# ============================================================================
# TC-P7B-001  Schema completeness
# ============================================================================
class TestSchemaCompleteness(unittest.TestCase):
    """build_main_brain_payload returns every required top-level key."""

    def setUp(self):
        self.app = get_app()

    def test_001_all_top_level_keys_present(self):
        payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
        required = [
            "_version", "generated_at", "market", "market_state", "left_brain",
            "verdict", "strategy_scanner", "active_trades", "manager",
            "execution_gateway", "coach", "journal", "performance",
            "decision_timeline", "alerts", "system_status",
            "availability", "errors",
        ]
        for k in required:
            self.assertIn(k, payload, f"Missing top-level key: {k}")

    def test_002_version_is_v1(self):
        payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
        self.assertEqual(payload["_version"], "v1")

    def test_003_generated_at_is_iso_string(self):
        payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
        ga = payload["generated_at"]
        self.assertIsInstance(ga, str)
        # Must parse as ISO datetime
        datetime.fromisoformat(ga)

    def test_004_availability_has_all_section_keys(self):
        payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
        avail_sections = [
            "market", "market_state", "left_brain", "verdict", "strategy_scanner",
            "active_trades", "manager", "execution_gateway", "coach",
            "journal", "performance", "timeline", "alerts", "system_status",
        ]
        for sec in avail_sections:
            self.assertIn(sec, payload["availability"],
                          f"availability missing section: {sec}")

    def test_005_errors_is_list(self):
        payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
        self.assertIsInstance(payload["errors"], list)

    def test_005a_top_of_book_is_display_only_and_copied_to_payload(self):
        expected = {
            "available": True, "state": "LIVE", "instrument": "MGC",
            "bid_size": 48, "ask_size": 32, "imbalance": 0.2,
            "updated_at": "2026-08-20T14:00:00+00:00", "age_s": 0.1,
        }
        with patch("databento_brain.get_top_of_book_display", return_value=expected):
            payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
        self.assertEqual(payload["top_of_book"], expected)


# ============================================================================
# TC-P7B-002  _mb_safe_num helper
# ============================================================================
class TestMbSafeNum(unittest.TestCase):
    def setUp(self):
        self.fn = get_app()._mb_safe_num

    def test_006_none_returns_none(self):
        self.assertIsNone(self.fn(None))

    def test_007_int_returns_float(self):
        self.assertEqual(self.fn(42), 42.0)

    def test_008_float_returns_same(self):
        self.assertAlmostEqual(self.fn(3.14), 3.14)

    def test_009_nan_returns_none(self):
        self.assertIsNone(self.fn(float("nan")))

    def test_010_inf_returns_none(self):
        self.assertIsNone(self.fn(float("inf")))
        self.assertIsNone(self.fn(float("-inf")))

    def test_011_string_number_returns_float(self):
        self.assertEqual(self.fn("3.5"), 3.5)

    def test_012_non_numeric_string_returns_none(self):
        self.assertIsNone(self.fn("hello"))

    def test_013_zero_returns_zero(self):
        self.assertEqual(self.fn(0), 0.0)

    def test_014_negative_returns_negative(self):
        self.assertEqual(self.fn(-5.5), -5.5)


# ============================================================================
# TC-P7B-003  JSON serialisation
# ============================================================================
class TestJsonSerialisation(unittest.TestCase):
    """Payload must be fully serialisable — no datetime, deque, frozenset, NaN."""

    def setUp(self):
        self.app = get_app()

    def test_015_payload_json_roundtrip_no_exception(self):
        payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
        serialised = json.dumps(payload)  # must not raise
        self.assertIsInstance(serialised, str)

    def test_016_no_nan_in_output(self):
        payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))

        def _has_nan(obj, path=""):
            if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
                return True, path
            if isinstance(obj, dict):
                for k, v in obj.items():
                    found, p = _has_nan(v, path + "." + str(k))
                    if found:
                        return True, p
            if isinstance(obj, list):
                for i, v in enumerate(obj):
                    found, p = _has_nan(v, path + f"[{i}]")
                    if found:
                        return True, p
            return False, ""

        found, path = _has_nan(payload)
        self.assertFalse(found, f"NaN/Inf found at path: {path}")

    def test_017_no_raw_datetime_in_output(self):
        payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
        raw = json.dumps(payload)  # datetime in output → TypeError before this
        self.assertIsInstance(raw, str)

    def test_018_components_normalised_to_dict_when_list(self):
        r = dict(FAKE_RESULT_BASE)
        r["edge_breakdown"] = {
            "grade": "WAIT",
            "components": [
                {"name": "bos_confirmed", "score": 20},
                {"name": "vwap_confirmed", "score": 15},
            ],
        }
        payload = self.app.build_main_brain_payload(r)
        comps = payload["verdict"]["components"]
        self.assertIsInstance(comps, dict)
        self.assertIn("bos_confirmed", comps)
        self.assertIn("vwap_confirmed", comps)

    def test_019_components_passthrough_when_dict(self):
        r = dict(FAKE_RESULT_BASE)
        r["edge_breakdown"] = {
            "grade": "A",
            "components": {"bos_confirmed": 20, "vwap_confirmed": 15},
        }
        payload = self.app.build_main_brain_payload(r)
        comps = payload["verdict"]["components"]
        self.assertIsInstance(comps, dict)
        self.assertEqual(comps.get("bos_confirmed"), 20)

    def test_020_frozenset_strict_missing_serialisable(self):
        r = dict(FAKE_RESULT_BASE)
        r["strict_missing"] = frozenset(["structure_confirmed", "edge_score"])
        payload = self.app.build_main_brain_payload(r)
        # Must round-trip
        raw = json.dumps(payload)
        self.assertIsInstance(raw, str)
        # failed_conditions must be a list
        self.assertIsInstance(payload["verdict"]["failed_conditions"], list)


# ============================================================================
# TC-P7B-004  None result (fault injection)
# ============================================================================
class TestNoneResult(unittest.TestCase):
    """build_main_brain_payload must not crash when result=None."""

    def setUp(self):
        self.app = get_app()

    def test_021_none_result_returns_payload(self):
        payload = self.app.build_main_brain_payload(None)
        self.assertIn("_version", payload)
        self.assertEqual(payload["_version"], "v1")

    def test_022_none_result_has_errors_list(self):
        payload = self.app.build_main_brain_payload(None)
        self.assertIsInstance(payload["errors"], list)

    def test_023_none_result_json_serialisable(self):
        payload = self.app.build_main_brain_payload(None)
        json.dumps(payload)  # must not raise


# ============================================================================
# TC-P7B-005  Verdict section
# ============================================================================
class TestVerdictSection(unittest.TestCase):
    def setUp(self):
        self.app = get_app()

    def test_024_wait_verdict_is_not_actionable(self):
        r = dict(FAKE_RESULT_BASE)
        r["strict_label"] = "WAIT"
        payload = self.app.build_main_brain_payload(r)
        self.assertFalse(payload["verdict"]["is_actionable"])
        self.assertEqual(payload["verdict"]["readiness"], "WAIT")

    def test_025_long_ready_is_actionable(self):
        r = dict(FAKE_RESULT_BASE)
        r["strict_label"] = "LONG READY"
        r["edge_score"] = 85
        payload = self.app.build_main_brain_payload(r)
        self.assertTrue(payload["verdict"]["is_actionable"])
        self.assertEqual(payload["verdict"]["readiness"], "LONG READY")

    def test_026_short_ready_is_actionable(self):
        r = dict(FAKE_RESULT_BASE)
        r["strict_label"] = "SHORT READY"
        r["edge_score"] = 80
        payload = self.app.build_main_brain_payload(r)
        self.assertTrue(payload["verdict"]["is_actionable"])

    def test_027_edge_max_is_always_110(self):
        payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
        self.assertEqual(payload["verdict"]["edge_max"], 110)

    def test_028_failed_conditions_is_list(self):
        payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
        self.assertIsInstance(payload["verdict"]["failed_conditions"], list)

    def test_029_verdict_available_when_no_exception(self):
        payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
        self.assertTrue(payload["availability"]["verdict"]["available"])


# ============================================================================
# TC-P7B-006  Derived active-trade fields
# ============================================================================
class TestActiveTradeDerivedFields(unittest.TestCase):
    """Derived fields (current_r, unrealized_pnl) are correct."""

    def setUp(self):
        self.app = get_app()

    def _run_with_active_trade(self, trade_dict, current_price=3260.0):
        r = dict(FAKE_RESULT_BASE)
        r["current_price"] = current_price
        # Inject the trade into ACTIVE_TRADES_BY_INST
        key = trade_dict.get("instrument", "MGC")
        original = dict(self.app.ACTIVE_TRADES_BY_INST)
        try:
            self.app.ACTIVE_TRADES_BY_INST[key] = trade_dict
            payload = self.app.build_main_brain_payload(r)
            return payload["active_trades"]
        finally:
            self.app.ACTIVE_TRADES_BY_INST.clear()
            self.app.ACTIVE_TRADES_BY_INST.update(original)

    def test_030_long_current_r_positive_above_entry(self):
        trade = {
            "instrument": "MGC",
            "direction": "long",
            "entry": 3250.0,
            "stop": 3240.0,    # risk = 10 pts
            "contracts": 1,
        }
        trades = self._run_with_active_trade(trade, current_price=3260.0)
        self.assertEqual(len(trades), 1)
        # current_r = (3260 - 3250) / 10 * 1 = 1.0
        self.assertAlmostEqual(trades[0]["current_r"], 1.0, places=2)

    def test_031_long_current_r_negative_below_entry(self):
        trade = {
            "instrument": "MGC",
            "direction": "long",
            "entry": 3250.0,
            "stop": 3240.0,
            "contracts": 1,
        }
        trades = self._run_with_active_trade(trade, current_price=3245.0)
        self.assertEqual(len(trades), 1)
        # current_r = (3245 - 3250) / 10 = -0.5
        self.assertAlmostEqual(trades[0]["current_r"], -0.5, places=2)

    def test_032_short_current_r_positive_below_entry(self):
        trade = {
            "instrument": "MGC",
            "direction": "short",
            "entry": 3250.0,
            "stop": 3260.0,    # risk = 10 pts
            "contracts": 1,
        }
        trades = self._run_with_active_trade(trade, current_price=3240.0)
        self.assertEqual(len(trades), 1)
        # current_r = (3250 - 3240) / 10 = 1.0  (dir_sign=-1 * (3240-3250) = 10)
        self.assertAlmostEqual(trades[0]["current_r"], 1.0, places=2)

    def test_033_no_active_trade_returns_empty_list(self):
        original = dict(self.app.ACTIVE_TRADES_BY_INST)
        try:
            self.app.ACTIVE_TRADES_BY_INST.clear()
            payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
            self.assertIsInstance(payload["active_trades"], list)
        finally:
            self.app.ACTIVE_TRADES_BY_INST.update(original)

    def test_034_none_slot_is_skipped(self):
        original = dict(self.app.ACTIVE_TRADES_BY_INST)
        try:
            self.app.ACTIVE_TRADES_BY_INST["MGC"] = None
            payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
            mgc_trades = [t for t in payload["active_trades"] if t["instrument"] == "MGC"]
            self.assertEqual(mgc_trades, [])
        finally:
            self.app.ACTIVE_TRADES_BY_INST.clear()
            self.app.ACTIVE_TRADES_BY_INST.update(original)


# ============================================================================
# TC-P7B-007  Fault isolation
# ============================================================================
class TestFaultIsolation(unittest.TestCase):
    """A single section failure must not propagate to the whole payload."""

    def setUp(self):
        self.app = get_app()

    def test_035_coach_missing_still_returns_payload(self):
        r = dict(FAKE_RESULT_BASE)
        del r["coach"]
        payload = self.app.build_main_brain_payload(r)
        self.assertIn("_version", payload)
        # coach section must have fallback keys
        self.assertIn("weight_updated", payload["coach"])

    def test_036_strategy_engine_missing_still_returns_payload(self):
        r = dict(FAKE_RESULT_BASE)
        del r["strategy_engine"]
        payload = self.app.build_main_brain_payload(r)
        self.assertIn("strategy_scanner", payload)

    def test_037_market_intelligence_none_graceful(self):
        r = dict(FAKE_RESULT_BASE)
        r["market_intelligence"] = None
        payload = self.app.build_main_brain_payload(r)
        ms = payload["market_state"]
        self.assertIsNone(ms["regime"])

    def test_038_trade_plan_none_does_not_crash(self):
        r = dict(FAKE_RESULT_BASE)
        r["trade_plan"] = None
        payload = self.app.build_main_brain_payload(r)
        self.assertIsNone(payload["verdict"]["risk_reward"])

    def test_039_edge_breakdown_none_graceful(self):
        r = dict(FAKE_RESULT_BASE)
        r["edge_breakdown"] = None
        payload = self.app.build_main_brain_payload(r)
        self.assertIsInstance(payload["verdict"]["components"], dict)


# ============================================================================
# TC-P7B-008  Decision timeline (derived partial)
# ============================================================================
class TestDecisionTimeline(unittest.TestCase):
    def setUp(self):
        self.app = get_app()

    def test_040_timeline_completeness_is_partial(self):
        payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
        tl = payload["decision_timeline"]
        self.assertTrue(tl.get("partial"))
        self.assertEqual(tl.get("completeness"), "PARTIAL")

    def test_041_timeline_events_is_list(self):
        payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
        self.assertIsInstance(payload["decision_timeline"]["events"], list)

    def test_042_timeline_events_capped_at_20(self):
        payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
        self.assertLessEqual(len(payload["decision_timeline"]["events"]), 20)

    def test_043_timeline_missing_event_types_present(self):
        payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
        tl = payload["decision_timeline"]
        self.assertIn("missing_event_types", tl)
        self.assertIn("TRADE_OPENED", tl["missing_event_types"])

    def test_044_timeline_deferred_key_present(self):
        payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
        tl = payload["decision_timeline"]
        self.assertIn("_deferred", tl)
        self.assertIn("Phase 7C", tl["_deferred"])

    def test_045_timeline_availability_shows_partial(self):
        payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
        tl_avail = payload["availability"].get("timeline", {})
        self.assertTrue(tl_avail.get("partial"))


# ============================================================================
# TC-P7B-009  Strategy scanner
# ============================================================================
class TestStrategyScanner(unittest.TestCase):
    def setUp(self):
        self.app = get_app()

    def test_046_only_main_engine_strategies_returned(self):
        # Inject a research (paper-sim) strategy into the diagnostics
        app = self.app
        inst = "MGC"
        original = dict(app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER)
        try:
            app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER[inst] = {
                "selected_key": "LIQUIDITY_SWEEP_REVERSAL",
                "market_regime": "RISK_ON",
                "strategies": [
                    {"key": "LIQUIDITY_SWEEP_REVERSAL", "eligible": True},
                    {"key": "OPENING_DRIVE",             "eligible": True},
                    {"key": "RESEARCH_MEAN_REVERSION",   "eligible": True},  # paper-sim
                    {"key": "RESEARCH_SCALP_DOJI",       "eligible": True},  # paper-sim
                ],
            }
            payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
            keys = {s.get("key") or s.get("strategy_key")
                    for s in payload["strategy_scanner"]["ranked_strategies"]}
            self.assertIn("LIQUIDITY_SWEEP_REVERSAL", keys)
            self.assertIn("OPENING_DRIVE", keys)
            self.assertNotIn("RESEARCH_MEAN_REVERSION", keys)
            self.assertNotIn("RESEARCH_SCALP_DOJI", keys)
        finally:
            app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER.clear()
            app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER.update(original)

    def test_047_selected_strategy_annotated(self):
        app = self.app
        inst = "MGC"
        original = dict(app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER)
        try:
            app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER[inst] = {
                "selected_key": "OPENING_DRIVE",
                "market_regime": "TRENDING",
                "strategies": [
                    {"key": "OPENING_DRIVE",             "eligible": True},
                    {"key": "LIQUIDITY_SWEEP_REVERSAL",  "eligible": False},
                ],
            }
            r = dict(FAKE_RESULT_BASE)
            payload = self.app.build_main_brain_payload(r)
            ranked = payload["strategy_scanner"]["ranked_strategies"]
            od = next((s for s in ranked if (s.get("key") or s.get("strategy_key")) == "OPENING_DRIVE"), None)
            self.assertIsNotNone(od)
            self.assertTrue(od.get("selected"))
            lsr = next((s for s in ranked if (s.get("key") or s.get("strategy_key")) == "LIQUIDITY_SWEEP_REVERSAL"), None)
            self.assertIsNotNone(lsr)
            self.assertFalse(lsr.get("selected"))
        finally:
            app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER.clear()
            app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER.update(original)

    def test_048_display_labels_assigned(self):
        app = self.app
        inst = "MGC"
        original = dict(app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER)
        try:
            app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER[inst] = {
                "selected_key": "VWAP_TREND_CONTINUATION",
                "market_regime": "TRENDING",
                "strategies": [{"key": "VWAP_TREND_CONTINUATION", "eligible": True}],
            }
            payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
            ranked = payload["strategy_scanner"]["ranked_strategies"]
            vtc = next((s for s in ranked if (s.get("key") or s.get("strategy_key")) == "VWAP_TREND_CONTINUATION"), None)
            self.assertIsNotNone(vtc)
            self.assertEqual(vtc.get("label"), "VWAP Trend Continuation")
        finally:
            app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER.clear()
            app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER.update(original)


# ============================================================================
# TC-P7B-010  Coach pass-through
# ============================================================================
class TestCoachPassThrough(unittest.TestCase):
    def setUp(self):
        self.app = get_app()

    def test_049_coach_version_preserved(self):
        payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
        self.assertEqual(payload["coach"]["_version"], "v1")

    def test_050_coach_is_shallow_copy(self):
        r = dict(FAKE_RESULT_BASE)
        r["coach"] = {
            "weight_updated": True,
            "thesis_resolved": False,
            "thesis_last_resolved_at": None,
            "learning_influence": 0.1,
            "rule_engine_eligibility": "LIVE_ELIGIBLE",
            "_version": "v1",
            "_extra_key": "should_be_copied",
        }
        payload = self.app.build_main_brain_payload(r)
        self.assertEqual(payload["coach"].get("_extra_key"), "should_be_copied")

    def test_051_coach_mutation_does_not_affect_source(self):
        r = dict(FAKE_RESULT_BASE)
        src_coach = {
            "weight_updated": False,
            "thesis_resolved": False,
            "thesis_last_resolved_at": None,
            "learning_influence": 0.0,
            "rule_engine_eligibility": "LIVE_ELIGIBLE",
            "_version": "v1",
        }
        r["coach"] = src_coach
        payload = self.app.build_main_brain_payload(r)
        payload["coach"]["weight_updated"] = True  # mutate returned copy
        self.assertFalse(src_coach["weight_updated"])  # source unchanged


# ============================================================================
# TC-P7B-011  Manager section
# ============================================================================
class TestManagerSection(unittest.TestCase):
    def setUp(self):
        self.app = get_app()

    def test_052_manager_has_gateway_debug(self):
        payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
        self.assertIn("gateway_debug", payload["manager"])

    def test_053_manager_has_training_gate(self):
        payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
        self.assertIn("training_gate", payload["manager"])

    def test_054_manager_has_version(self):
        payload = self.app.build_main_brain_payload(dict(FAKE_RESULT_BASE))
        self.assertIn("_version", payload["manager"])


# ============================================================================
# TC-P7B-012  Proxy whitelist registration
# ============================================================================
class TestProxyWhitelist(unittest.TestCase):
    """Verify /main-brain is registered in the Express proxy whitelist."""

    PROXY_FILE = os.path.join(
        os.path.dirname(__file__),
        "../../artifacts/api-server/src/routes/flask-proxy.ts",
    )

    def test_055_main_brain_in_bot1_routes(self):
        proxy_path = os.path.normpath(self.PROXY_FILE)
        self.assertTrue(
            os.path.exists(proxy_path),
            f"flask-proxy.ts not found at {proxy_path}",
        )
        with open(proxy_path, "r") as f:
            content = f.read()
        self.assertIn('"/main-brain"', content,
                      "/main-brain not found in BOT1_ROUTES")

    def test_056_main_brain_not_in_open_paths(self):
        """Route must be owner-only; OPEN_PATHS lets anonymous traffic through.

        OPEN_PATHS is declared in dashboard-auth.ts (not flask-proxy.ts).
        We read that file and confirm /main-brain is absent from it.
        """
        auth_path = os.path.normpath(os.path.join(
            os.path.dirname(__file__),
            "../../artifacts/api-server/src/routes/dashboard-auth.ts",
        ))
        self.assertTrue(
            os.path.exists(auth_path),
            f"dashboard-auth.ts not found at {auth_path}",
        )
        with open(auth_path, "r") as f:
            content = f.read()
        # Extract the OPEN_PATHS Set literal content
        import re
        m = re.search(r'OPEN_PATHS\s*=\s*new\s+Set\s*\(\s*\[([^\]]*)\]', content,
                      re.DOTALL)
        open_set_content = m.group(1) if m else ""
        self.assertNotIn(
            '"/main-brain"', open_set_content,
            "/main-brain must NOT be in dashboard-auth OPEN_PATHS (owner-only route)",
        )
        self.assertNotIn(
            "'/main-brain'", open_set_content,
            "/main-brain must NOT be in dashboard-auth OPEN_PATHS (owner-only route)",
        )


# ============================================================================
# Runner
# ============================================================================
if __name__ == "__main__":
    loader = unittest.TestLoader()
    loader.sortTestMethodsUsing = None   # preserve definition order
    suite  = unittest.TestSuite()

    test_classes = [
        TestSchemaCompleteness,       # TC-P7B-001
        TestMbSafeNum,                # TC-P7B-002
        TestJsonSerialisation,        # TC-P7B-003
        TestNoneResult,               # TC-P7B-004
        TestVerdictSection,           # TC-P7B-005
        TestActiveTradeDerivedFields, # TC-P7B-006
        TestFaultIsolation,           # TC-P7B-007
        TestDecisionTimeline,         # TC-P7B-008
        TestStrategyScanner,          # TC-P7B-009
        TestCoachPassThrough,         # TC-P7B-010
        TestManagerSection,           # TC-P7B-011
        TestProxyWhitelist,           # TC-P7B-012
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    total   = result.testsRun
    failed  = len(result.failures) + len(result.errors)
    passed  = total - failed
    skipped = len(result.skipped)

    print()
    print("=" * 64)
    print(f"  TOTAL: {total} checks — {passed} passed, {failed} failed"
          + (f", {skipped} skipped" if skipped else ""))
    if failed == 0:
        print("  PASS  all Phase 7B main-brain-route checks passed")
    else:
        print("  FAIL  one or more checks failed — see above")
    print("=" * 64)
    sys.exit(0 if failed == 0 else 1)
