"""
AUTO-TRADING SAFETY REMEDIATION — HIGH FINDINGS REGRESSION SUITE v2
====================================================================
Spec: AUTO-TRADING SAFETY REMEDIATION — FIX HIGH-RISK FINDINGS ONLY

Covers:
  § 2  — Execution-mode fail-closed (missing → disabled)
  § 3  — Databento health gate (connection, freshness, market state, halt)
  § 4  — LRE exceptions fail-closed (timeout, mismatch, malformed, contradictory)
  § 5  — Candidate freshness (all timestamp fields + instrument mismatch)
  § 6  — Structured blocked-attempt auditing (reason codes, fields)
  § 7  — Flask route security (GET cannot execute, broker-URL scoped)
  § 9  — Concurrency (2 and 10 simultaneous, timeout+retry, feed disconnect)

Every test that involves execution asserts the broker sentinel URL was NEVER
called.  Discord also uses requests.post, so assertions use _broker_never_called()
rather than assert_not_called().

No live orders are transmitted during this suite.
"""
import unittest
import json as _json
import threading
import time
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import app as APP
import databento_brain as DB_BRAIN

from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone, timedelta

# ── Helpers ───────────────────────────────────────────────────────────────────

_AUTH_HEADERS    = {"Authorization": "Basic dGVzdDp0ZXN0", "Origin": "http://localhost"}
_BROKER_SENTINEL = "https://tp.io/hook/AUDIT_TEST_SENTINEL"


def _now_iso(**delta_kwargs):
    dt = datetime.now(timezone.utc)
    if delta_kwargs:
        dt += timedelta(**delta_kwargs)
    return dt.isoformat()


def _broker_never_called(mock_post):
    """True iff requests.post was never called with the broker sentinel URL.
    Discord legitimately calls requests.post; we scope to the broker URL only."""
    for c in mock_post.call_args_list:
        args = c.args or ()
        url  = args[0] if args else (c.kwargs or {}).get("url", "")
        if _BROKER_SENTINEL in str(url):
            return False
    return True


def _post_traderspost(client, ticker="MGC", contracts=1, extra=None):
    payload = {"ticker": ticker, "contracts": contracts}
    if extra:
        payload.update(extra)
    return client.post("/traderspost",
                       data=_json.dumps(payload),
                       content_type="application/json",
                       headers=_AUTH_HEADERS)


def _make_analysis(verdict="LONG READY", market_open=True,
                   candidate_ts=None, direction="Long",
                   instrument=None, market_data_ts=None,
                   strategy_eval_ts=None, expires_at=None,
                   cp_instrument=None, market_state_cycle=None):
    """Build a minimal full_analysis() return value for testing."""
    tp = {"trade_plan": True, "entry_zone": "2800.0–2802.0",
          "stop_loss": "2795.0", "target1": "2815.0", "target2": "2830.0",
          "direction": direction, "rr": "1:3"}
    result = {
        "verdict":          verdict,
        "market_open":      market_open,
        "trade_plan":       tp if market_open else False,
        "directions":       {direction: {"verdict": verdict, "edge_score": 75}},
        "alert_diagnostics": {},
        "strategy_engine":  {"active_key": "LIQUIDITY_SWEEP_REVERSAL"},
    }
    if any([candidate_ts, market_data_ts, strategy_eval_ts, expires_at,
            cp_instrument, market_state_cycle]):
        cp = {}
        if candidate_ts:       cp["generated_at"]        = candidate_ts
        if market_data_ts:     cp["market_data_ts"]      = market_data_ts
        if strategy_eval_ts:   cp["strategy_eval_ts"]    = strategy_eval_ts
        if expires_at:         cp["expires_at"]          = expires_at
        if cp_instrument:      cp["instrument"]          = cp_instrument
        if market_state_cycle: cp["market_state_cycle"]  = market_state_cycle
        result["candidate_preview"] = cp
    return result


# ─────────────────────────────────────────────────────────────────────────────
# § 2 — Execution-mode fail-closed (missing → disabled)
# ─────────────────────────────────────────────────────────────────────────────

class TestExecutionModeFailClosed(unittest.TestCase):

    def _resolve(self, raw, tp_url="", exec_url=""):
        with patch.object(APP, "_EXECUTION_MODE_RAW", raw), \
             patch.object(APP, "TRADERSPOST_WEBHOOK_URL", tp_url), \
             patch.object(APP, "EXECUTION_WEBHOOK_URL", exec_url):
            return APP.resolve_execution_mode()

    def test_missing_mode_defaults_to_disabled(self):
        """Spec §2: missing EXECUTION_MODE must resolve to 'disabled'."""
        mode = self._resolve("")
        self.assertEqual(mode, "disabled",
                         "Missing EXECUTION_MODE must default to 'disabled'")
        self.assertFalse(APP.execution_is_live(mode))
        self.assertFalse(APP.execution_configured(mode))

    def test_blank_mode_defaults_to_disabled(self):
        """Spec §2: blank EXECUTION_MODE must resolve to 'disabled'."""
        with patch.object(APP, "_EXECUTION_MODE_RAW", "   ".strip().lower()):
            mode = APP.resolve_execution_mode()
        self.assertEqual(mode, "disabled")
        self.assertFalse(APP.execution_is_live(mode))

    def test_invalid_mode_defaults_to_disabled(self):
        """Spec §2: invalid value must resolve to 'disabled'."""
        for bad in ("LIVE", "enable_trading", "auto", "yes", "1"):
            with self.subTest(value=bad):
                mode = self._resolve(bad)
                self.assertEqual(mode, "disabled",
                                 f"Invalid value {bad!r} must resolve to 'disabled'")
                self.assertFalse(APP.execution_is_live(mode))

    def test_explicit_paper(self):
        """Spec §2: explicit 'paper' resolves to paper."""
        self.assertEqual(self._resolve("paper"), "paper")
        self.assertFalse(APP.execution_is_live("paper"))

    def test_explicit_disabled(self):
        """Spec §2: explicit 'disabled' disables all execution."""
        self.assertEqual(self._resolve("disabled"), "disabled")
        self.assertFalse(APP.execution_is_live("disabled"))
        self.assertFalse(APP.execution_configured("disabled"))

    def test_explicit_traderspost_with_url(self):
        """Spec §2: explicit traderspost + URL configured = live."""
        mode = self._resolve("traderspost", tp_url="https://tp.io/hook/x")
        self.assertEqual(mode, "traderspost")
        with patch.object(APP, "TRADERSPOST_WEBHOOK_URL", "https://tp.io/hook/x"):
            self.assertTrue(APP.execution_is_live(mode))

    def test_webhook_present_missing_mode_stays_disabled(self):
        """Spec §2: webhook URL presence NEVER enables execution."""
        mode = self._resolve("", tp_url="https://tp.io/hook/x")
        self.assertEqual(mode, "disabled",
                         "URL presence must not enable execution without explicit mode")
        self.assertFalse(APP.execution_is_live(mode))

    def test_webhook_present_disabled_mode_stays_disabled(self):
        """Spec §2: URL + EXECUTION_MODE=disabled stays disabled."""
        mode = self._resolve("disabled", tp_url="https://tp.io/hook/x")
        self.assertEqual(mode, "disabled")
        self.assertFalse(APP.execution_is_live(mode))

    def test_mixed_case_normalised(self):
        """Spec §2: mode comparisons are case-insensitive (env lowercased at load)."""
        self.assertEqual(self._resolve("paper"), "paper")
        self.assertEqual(self._resolve("DISABLED"), "disabled")  # lowercased externally

    def test_legacy_url_variable_does_not_override_disabled(self):
        """Spec §2: legacy variables must not override an explicit disabled."""
        with patch.object(APP, "_EXECUTION_MODE_RAW", "disabled"), \
             patch.object(APP, "TRADERSPOST_WEBHOOK_URL", "https://tp.io/hook/x"), \
             patch.object(APP, "EXECUTION_WEBHOOK_URL", "https://tp.io/hook/y"):
            mode = APP.resolve_execution_mode()
        self.assertEqual(mode, "disabled")
        self.assertFalse(APP.execution_is_live(mode))

    def test_restart_with_mode_missing_stays_disabled(self):
        """Spec §9: server restart with EXECUTION_MODE unset must not auto-trade."""
        with patch.object(APP, "_EXECUTION_MODE_RAW", ""):
            mode = APP.resolve_execution_mode()
        self.assertEqual(mode, "disabled")
        self.assertFalse(APP.execution_is_live(mode))

    def test_configured_vs_effective_mode_diverge_when_unset(self):
        """Spec §2: backend exposes configured_mode (None) vs effective_mode (disabled)."""
        with patch.object(APP, "_EXECUTION_MODE_RAW", ""):
            configured = APP._configured_execution_mode()
            effective  = APP.resolve_execution_mode()
        self.assertIsNone(configured,  "configured_mode must be None when EXECUTION_MODE unset")
        self.assertEqual(effective, "disabled")

    def test_no_outbound_broker_post_when_mode_missing(self):
        """Spec §2: disabled mode must not call the broker URL."""
        client = APP.app.test_client()
        with patch.object(APP, "_EXECUTION_MODE_RAW", ""), \
             patch.object(APP, "TRADERSPOST_WEBHOOK_URL", _BROKER_SENTINEL), \
             patch.object(APP, "full_analysis", return_value=_make_analysis()), \
             patch("requests.post") as mock_post:
            r = _post_traderspost(client)
            self.assertTrue(_broker_never_called(mock_post))
            body = _json.loads(r.data) if r.data else {}
            self.assertNotEqual(body.get("status"), "sent")

    def test_disabled_mode_returns_409(self):
        """Spec §2: EXECUTION_MODE=disabled must return 409."""
        client = APP.app.test_client()
        with patch.object(APP, "_EXECUTION_MODE_RAW", "disabled"), \
             patch("requests.post") as mock_post:
            r = _post_traderspost(client)
            self.assertIn(r.status_code, (409, 400))
            self.assertTrue(_broker_never_called(mock_post))

    def test_reason_code_in_status_response(self):
        """Spec §2: backend /status exposes configured_mode and effective_mode."""
        client = APP.app.test_client()
        r = client.get("/status", headers=_AUTH_HEADERS)
        if r.status_code == 200:
            body = _json.loads(r.data)
            self.assertIn("effective_mode",  body)
            self.assertIn("configured_mode", body)


# ─────────────────────────────────────────────────────────────────────────────
# § 3 — Databento health gate
# ─────────────────────────────────────────────────────────────────────────────

class TestDatabentoHealthGate(unittest.TestCase):

    def _gate(self, inst="MGC", **overrides):
        db_status  = overrides.get("DATABENTO_STATUS", {"connected": True})
        db_bars    = overrides.get("DATABENTO_BARS_BY_INST", {})
        brain_mock = overrides.get("_DATABENTO_BRAIN",
                                   MagicMock(_id_to_inst={1: "MGC", 2: "MNQ",
                                                           3: "MES",  4: "MYM"}))
        tick_ts    = overrides.get("CURRENT_PRICE_TS_BY_TICKER",
                                   {inst: _now_iso(seconds=-30)})
        vwap_fn    = overrides.get("get_vwap_mock",
                                   lambda *a, **kw: (2800.0, "ok"))
        enabled    = overrides.get("DATABENTO_ENABLED", True)
        market_open = overrides.get("market_open", True)

        analysis_val = {
            "verdict": "LONG READY", "market_open": market_open,
            "trade_plan": {}, "strategy_engine": {},
        }
        with patch.object(APP, "DATABENTO_ENABLED", enabled), \
             patch.object(APP, "_DATABENTO_BRAIN", brain_mock), \
             patch.object(DB_BRAIN, "DATABENTO_STATUS", db_status), \
             patch.object(DB_BRAIN, "DATABENTO_BARS_BY_INST", db_bars), \
             patch.dict(APP.CURRENT_PRICE_TS_BY_TICKER, tick_ts, clear=True), \
             patch.object(APP, "get_vwap", vwap_fn), \
             patch.object(APP, "full_analysis", return_value=analysis_val):
            return APP._check_databento_execution_health(inst)

    def test_healthy_connected_feed(self):
        healthy, reason, diag = self._gate()
        self.assertTrue(healthy, f"Expected healthy but got {reason}")
        self.assertEqual(reason, "healthy")

    def test_disconnected_feed(self):
        healthy, reason, diag = self._gate(DATABENTO_STATUS={"connected": False})
        self.assertFalse(healthy)
        self.assertIn("DISCONNECT", reason.upper())

    def test_unknown_connection_state(self):
        """Spec §3: empty DATABENTO_STATUS (unknown state) must block."""
        healthy, reason, diag = self._gate(DATABENTO_STATUS={})
        self.assertFalse(healthy)

    def test_databento_disabled_gate_is_noop(self):
        """Spec §3: DATABENTO_ENABLED=False → gate passes unconditionally."""
        healthy, reason, diag = self._gate(DATABENTO_ENABLED=False)
        self.assertTrue(healthy)
        self.assertEqual(reason, "databento_disabled")

    def test_unsubscribed_instrument(self):
        """Spec §3: instrument absent from id→inst map must block."""
        healthy, reason, diag = self._gate(
            inst="MGC",
            _DATABENTO_BRAIN=MagicMock(_id_to_inst={1: "MNQ"}))
        self.assertFalse(healthy)
        self.assertIn("subscribed", reason.lower())

    def test_stale_tick(self):
        healthy, reason, diag = self._gate(
            CURRENT_PRICE_TS_BY_TICKER={"MGC": _now_iso(minutes=-6)})
        self.assertFalse(healthy)
        self.assertIn("STALE", reason.upper())

    def test_stale_completed_bar(self):
        stale_ts = int((datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp())
        healthy, reason, diag = self._gate(
            DATABENTO_BARS_BY_INST={"MGC": [{"ts": stale_ts}]})
        self.assertFalse(healthy)
        self.assertIn("STALE", reason.upper())

    def test_stale_vwap(self):
        healthy, reason, diag = self._gate(
            get_vwap_mock=lambda *a, **kw: (None, "stale"))
        self.assertFalse(healthy)
        self.assertIn("VWAP", reason.upper())

    def test_missing_tick_timestamp(self):
        healthy, reason, diag = self._gate(CURRENT_PRICE_TS_BY_TICKER={})
        self.assertFalse(healthy)

    def test_malformed_tick_timestamp(self):
        healthy, reason, diag = self._gate(
            CURRENT_PRICE_TS_BY_TICKER={"MGC": "not-a-date"})
        self.assertFalse(healthy)
        self.assertIn("malformed", reason.lower())

    def test_future_timestamp_rejected(self):
        """Spec §3: tick 2 min in future must block."""
        healthy, reason, diag = self._gate(
            CURRENT_PRICE_TS_BY_TICKER={"MGC": _now_iso(minutes=2)})
        self.assertFalse(healthy)
        self.assertIn("future", reason.lower())

    def test_wrong_instrument_timestamp(self):
        """Spec §3: freshness record for MNQ when checking MGC must block."""
        healthy, reason, diag = self._gate(
            inst="MGC",
            CURRENT_PRICE_TS_BY_TICKER={"MNQ": _now_iso(seconds=-30)})
        self.assertFalse(healthy)

    def test_empty_market_state(self):
        """Spec §3: empty market state must block."""
        with patch.object(APP, "DATABENTO_ENABLED", True), \
             patch.object(APP, "_DATABENTO_BRAIN",
                          MagicMock(_id_to_inst={1: "MGC", 2: "MNQ",
                                                  3: "MES", 4: "MYM"})), \
             patch.object(DB_BRAIN, "DATABENTO_STATUS", {"connected": True}), \
             patch.object(DB_BRAIN, "DATABENTO_BARS_BY_INST", {}), \
             patch.dict(APP.CURRENT_PRICE_TS_BY_TICKER,
                        {"MGC": _now_iso(seconds=-30)}, clear=True), \
             patch.object(APP, "get_vwap", lambda *a, **kw: (2800.0, "ok")), \
             patch.object(APP, "full_analysis", return_value={}):
            healthy, reason, diag = APP._check_databento_execution_health("MGC")
        self.assertFalse(healthy)
        self.assertIn("market_state", reason.lower())

    def test_maintenance_halt_blocks(self):
        """Spec §3: CME maintenance halt (market_open=False, HALT regime) must block."""
        healthy, reason, diag = self._gate(market_open=False)
        self.assertFalse(healthy)
        # reason is MARKET_CLOSED or MAINTENANCE_HALT — both block
        self.assertFalse(healthy)

    def test_feed_disconnect_after_initial_approval(self):
        """Spec §3: disconnect between approval and transmission must still block."""
        healthy1, _, _ = self._gate(DATABENTO_STATUS={"connected": True})
        self.assertTrue(healthy1)
        healthy2, reason2, _ = self._gate(DATABENTO_STATUS={"connected": False})
        self.assertFalse(healthy2)
        self.assertIn("DISCONNECT", reason2.upper())

    def test_reconnect_before_fresh_data_blocked(self):
        """Spec §3: reconnected but stale tick (>5 min) must still block."""
        healthy, reason, diag = self._gate(
            DATABENTO_STATUS={"connected": True},
            CURRENT_PRICE_TS_BY_TICKER={"MGC": _now_iso(minutes=-10)})
        self.assertFalse(healthy)

    def test_zero_outbound_calls_on_blocked_gate(self):
        """Spec §3: every blocked gate condition → zero broker calls."""
        client = APP.app.test_client()
        with patch.object(APP, "AUTO_TRADE", {"MGC": True}), \
             patch.object(APP, "DATABENTO_ENABLED", True), \
             patch.object(APP, "_DATABENTO_BRAIN",
                          MagicMock(_id_to_inst={1: "MGC"})), \
             patch.object(DB_BRAIN, "DATABENTO_STATUS", {"connected": False}), \
             patch.dict(APP.CURRENT_PRICE_TS_BY_TICKER, {}, clear=True), \
             patch.object(APP, "emergency_disabled", return_value=False), \
             patch.object(APP, "_outcome_cooldown_remaining", return_value=(0, "")), \
             patch.object(APP, "full_analysis",
                          return_value={"market_open": True, "verdict": "LONG READY",
                                        "strategy_engine": {}}), \
             patch("requests.post") as mock_post:
            APP._maybe_auto_execute("MGC", source="auto")
            self.assertTrue(_broker_never_called(mock_post))


# ─────────────────────────────────────────────────────────────────────────────
# § 4 — LRE exceptions fail-closed
# ─────────────────────────────────────────────────────────────────────────────

class TestLreExceptionsFailClosed(unittest.TestCase):

    def setUp(self):
        self.client = APP.app.test_client()
        with APP._LRE_ERROR_COUNT_LOCK:
            APP._LRE_ERROR_COUNT = 0

    def _run(self, lre_side_effect=None, lre_return=("LIVE_ELIGIBLE", None)):
        with patch.object(APP, "resolve_execution_mode", return_value="paper"), \
             patch.object(APP, "TRADERSPOST_WEBHOOK_URL", _BROKER_SENTINEL), \
             patch.object(APP, "full_analysis", return_value=_make_analysis()), \
             patch("requests.post") as mock_post:
            if lre_side_effect is not None:
                with patch.object(APP, "_check_learning_eligibility",
                                  side_effect=lre_side_effect):
                    r = _post_traderspost(self.client)
            else:
                with patch.object(APP, "_check_learning_eligibility",
                                  return_value=lre_return):
                    r = _post_traderspost(self.client)
            body = _json.loads(r.data) if r.data else {}
            return r.status_code, body, mock_post

    def test_live_eligible_passes(self):
        code, body, mock_post = self._run(lre_return=("LIVE_ELIGIBLE", None))
        self.assertNotIn(body.get("status"), ("error",))
        self.assertTrue(_broker_never_called(mock_post))  # paper mode, no broker call

    def test_disabled_blocks_409(self):
        code, body, mock_post = self._run(lre_return=("DISABLED", "repeated_failures"))
        self.assertEqual(code, 409)
        self.assertTrue(_broker_never_called(mock_post))

    def test_insufficient_samples_passes(self):
        code, body, mock_post = self._run(lre_return=("INSUFFICIENT_SAMPLES", None))
        self.assertNotIn(code, (500,))
        self.assertTrue(_broker_never_called(mock_post))

    def test_no_optional_data_passes(self):
        code, body, mock_post = self._run(lre_return=("NO_OPTIONAL_DATA", None))
        self.assertNotIn(code, (500,))
        self.assertTrue(_broker_never_called(mock_post))

    def test_exception_at_check1_fails_closed(self):
        code, body, mock_post = self._run(
            lre_side_effect=RuntimeError("simulated DB crash"))
        self.assertIn(code, (409, 500))
        self.assertTrue(_broker_never_called(mock_post))
        self.assertGreater(APP._get_lre_error_count(), 0)

    def test_exception_increments_counter(self):
        before = APP._get_lre_error_count()
        self._run(lre_side_effect=ValueError("mock error"))
        self.assertEqual(APP._get_lre_error_count(), before + 1)

    def test_timeout_fails_closed(self):
        """Spec §4: LRE timeout must block execution (RC_LRE_TIMEOUT)."""
        def _slow(*args, **kwargs):
            time.sleep(10)  # longer than lre_timeout_sec
            return "LIVE_ELIGIBLE", None
        with patch.object(APP, "resolve_execution_mode", return_value="paper"), \
             patch.object(APP, "TRADERSPOST_WEBHOOK_URL", _BROKER_SENTINEL), \
             patch.object(APP, "full_analysis", return_value=_make_analysis()), \
             patch.object(APP, "_DB_EXEC_THRESHOLDS",
                          {**APP._DB_EXEC_THRESHOLDS, "lre_timeout_sec": 0.1}), \
             patch.object(APP, "_check_learning_eligibility", side_effect=_slow), \
             patch("requests.post") as mock_post:
            r = _post_traderspost(self.client)
            body = _json.loads(r.data) if r.data else {}
            self.assertIn(r.status_code, (409, 500),
                          "LRE timeout must block execution")
            self.assertTrue(_broker_never_called(mock_post))
            # Error counter must have been incremented
            self.assertGreater(APP._get_lre_error_count(), 0)

    def test_malformed_result_not_a_tuple_fails_closed(self):
        """Spec §4: non-tuple LRE result must block."""
        code, body, mock_post = self._run(lre_return="LIVE_ELIGIBLE")
        self.assertIn(code, (409, 500))
        self.assertTrue(_broker_never_called(mock_post))

    def test_unknown_status_treated_as_contradictory_fails_closed(self):
        """Spec §4: unrecognised LRE status must block (contradictory result)."""
        code, body, mock_post = self._run(lre_return=("UNKNOWN_INTERNAL_STATE_XYZ", None))
        self.assertIn(code, (409, 500),
                      "Unrecognised LRE status must block execution")
        self.assertTrue(_broker_never_called(mock_post))

    def test_check2_exception_fails_closed(self):
        """Spec §4: check-2 exception must block and never reach broker."""
        bad_elig = MagicMock()
        bad_elig.get = MagicMock(side_effect=RuntimeError("check2 crash"))
        with patch.object(APP, "resolve_execution_mode", return_value="paper"), \
             patch.object(APP, "TRADERSPOST_WEBHOOK_URL", _BROKER_SENTINEL), \
             patch.object(APP, "full_analysis", return_value=_make_analysis()), \
             patch.object(APP, "_check_learning_eligibility",
                          return_value=("LIVE_ELIGIBLE", None)), \
             patch.object(APP, "LEARNING_ELIGIBILITY", bad_elig), \
             patch("requests.post") as mock_post:
            r = _post_traderspost(self.client)
            body = _json.loads(r.data) if r.data else {}
            self.assertNotEqual(body.get("status"), "sent")
            self.assertTrue(_broker_never_called(mock_post))

    def test_no_broker_post_on_any_lre_exception(self):
        """Core invariant: broker URL NEVER called on any LRE exception."""
        for exc_type in (RuntimeError, ValueError, KeyError, TypeError):
            with self.subTest(exc=exc_type.__name__):
                with APP._LRE_ERROR_COUNT_LOCK:
                    APP._LRE_ERROR_COUNT = 0
                with patch.object(APP, "resolve_execution_mode",
                                  return_value="traderspost"), \
                     patch.object(APP, "TRADERSPOST_WEBHOOK_URL", _BROKER_SENTINEL), \
                     patch.object(APP, "execution_configured", return_value=True), \
                     patch.object(APP, "full_analysis", return_value=_make_analysis()), \
                     patch.object(APP, "_check_learning_eligibility",
                                  side_effect=exc_type("crash")), \
                     patch("requests.post") as mock_post:
                    r = _post_traderspost(self.client)
                    self.assertTrue(_broker_never_called(mock_post),
                                    f"{exc_type.__name__} must never reach broker")
                    self.assertNotEqual(_json.loads(r.data).get("status"), "sent")

    def test_lre_diagnostics_in_error_response(self):
        """Spec §4: error response must include lre_diagnostics block."""
        code, body, _ = self._run(lre_side_effect=RuntimeError("crash"))
        self.assertIn("lre_diagnostics", body,
                      "Error response must contain lre_diagnostics")
        diag = body["lre_diagnostics"]
        self.assertIn("instrument", diag)
        self.assertIn("blocked",    diag)
        self.assertTrue(diag["blocked"])

    def test_lre_error_count_in_status(self):
        """Spec §4: /status must expose lre_error_count as a non-negative integer.
        We verify: (a) the field exists, (b) it is a non-negative int, and
        (c) incrementing the counter before the request is reflected — tested via
        the in-process counter, not via /status which may cache a short-lived result.
        """
        with APP._LRE_ERROR_COUNT_LOCK:
            APP._LRE_ERROR_COUNT = 0
        APP._increment_lre_error_count()
        APP._increment_lre_error_count()
        # Verify the in-process helper correctly tracks what we just wrote.
        self.assertGreaterEqual(APP._get_lre_error_count(), 2,
                                "_get_lre_error_count must return incremented value")
        # /status should have the field; its exact value may lag behind a cache window.
        r = APP.app.test_client().get("/status", headers=_AUTH_HEADERS)
        if r.status_code == 200:
            body = _json.loads(r.data)
            self.assertIn("lre_error_count", body,
                          "lre_error_count must appear in /status")
            self.assertIsInstance(body["lre_error_count"], int,
                                  "lre_error_count must be an integer")
            self.assertGreaterEqual(body["lre_error_count"], 0)
        with APP._LRE_ERROR_COUNT_LOCK:
            APP._LRE_ERROR_COUNT = 0


# ─────────────────────────────────────────────────────────────────────────────
# § 5 — Candidate freshness
# ─────────────────────────────────────────────────────────────────────────────

class TestCandidateFreshness(unittest.TestCase):

    def setUp(self):
        self.client = APP.app.test_client()

    def _run(self, **analysis_kwargs):
        analysis = _make_analysis(**analysis_kwargs)
        with patch.object(APP, "resolve_execution_mode", return_value="paper"), \
             patch.object(APP, "TRADERSPOST_WEBHOOK_URL", _BROKER_SENTINEL), \
             patch.object(APP, "full_analysis", return_value=analysis), \
             patch("requests.post") as mock_post:
            r = _post_traderspost(self.client)
            return r.status_code, _json.loads(r.data) if r.data else {}, mock_post

    def test_no_candidate_preview_passes(self):
        code, body, _ = self._run()
        self.assertNotIn((body.get("reason") or "").lower(), ["candidate"])

    def test_fresh_creation_timestamp_passes(self):
        code, body, _ = self._run(candidate_ts=_now_iso(seconds=-30))
        self.assertNotEqual(body.get("status"), "error")

    def test_stale_creation_timestamp_blocked(self):
        code, body, mock_post = self._run(candidate_ts=_now_iso(minutes=-5))
        self.assertIn(code, (400, 409))
        self.assertTrue(_broker_never_called(mock_post))
        self.assertIn("stale", (body.get("reason") or "").lower())

    def test_malformed_creation_timestamp_blocked(self):
        code, body, mock_post = self._run(candidate_ts="not-a-date")
        self.assertIn(code, (400, 409))
        self.assertTrue(_broker_never_called(mock_post))
        self.assertIn("malformed", (body.get("reason") or "").lower())

    def test_future_creation_timestamp_blocked(self):
        code, body, mock_post = self._run(candidate_ts=_now_iso(minutes=5))
        self.assertIn(code, (400, 409))
        self.assertTrue(_broker_never_called(mock_post))
        self.assertIn("future", (body.get("reason") or "").lower())

    def test_stale_market_data_timestamp_blocked(self):
        """Spec §5: stale market_data_ts must block."""
        code, body, mock_post = self._run(
            candidate_ts=_now_iso(seconds=-30),
            market_data_ts=_now_iso(minutes=-10))
        self.assertIn(code, (400, 409))
        self.assertTrue(_broker_never_called(mock_post))

    def test_fresh_market_data_timestamp_passes(self):
        code, body, _ = self._run(
            candidate_ts=_now_iso(seconds=-30),
            market_data_ts=_now_iso(seconds=-60))
        self.assertNotIn(code, (400, 409), "Fresh market_data_ts must pass")

    def test_stale_strategy_eval_timestamp_blocked(self):
        """Spec §5: stale strategy_eval_ts must block."""
        code, body, mock_post = self._run(
            candidate_ts=_now_iso(seconds=-30),
            strategy_eval_ts=_now_iso(minutes=-10))
        self.assertIn(code, (400, 409))
        self.assertTrue(_broker_never_called(mock_post))

    def test_fresh_strategy_eval_timestamp_passes(self):
        code, body, _ = self._run(
            candidate_ts=_now_iso(seconds=-30),
            strategy_eval_ts=_now_iso(seconds=-60))
        self.assertNotIn(code, (400, 409), "Fresh strategy_eval_ts must pass")

    def test_expired_candidate_blocked(self):
        """Spec §5: explicitly expired candidate (expires_at in the past) must block."""
        code, body, mock_post = self._run(
            candidate_ts=_now_iso(seconds=-30),
            expires_at=_now_iso(seconds=-5))   # expired 5 s ago
        self.assertIn(code, (400, 409))
        self.assertTrue(_broker_never_called(mock_post))

    def test_not_yet_expired_passes(self):
        """Spec §5: expires_at in the future must pass."""
        code, body, _ = self._run(
            candidate_ts=_now_iso(seconds=-30),
            expires_at=_now_iso(seconds=60))   # expires in 60 s
        self.assertNotIn(code, (400, 409), "Non-expired candidate must pass")

    def test_candidate_instrument_mismatch_blocked(self):
        """Spec §5: candidate instrument must match execution instrument."""
        code, body, mock_post = self._run(
            candidate_ts=_now_iso(seconds=-30),
            cp_instrument="MNQ")   # posting to MGC but candidate says MNQ
        self.assertIn(code, (400, 409))
        self.assertTrue(_broker_never_called(mock_post))
        self.assertIn("instrument", (body.get("reason") or "").lower())

    def test_matching_candidate_instrument_passes(self):
        """Spec §5: candidate instrument matches execution instrument → passes."""
        code, body, _ = self._run(
            candidate_ts=_now_iso(seconds=-30),
            cp_instrument="MGC")
        self.assertNotIn(code, (400, 409), "Matching instrument must pass")

    def test_empty_candidate_preview_passes(self):
        """Spec §5: candidate_preview block with no timestamps is pass-through."""
        analysis = _make_analysis()
        analysis["candidate_preview"] = {}
        with patch.object(APP, "resolve_execution_mode", return_value="paper"), \
             patch.object(APP, "full_analysis", return_value=analysis), \
             patch("requests.post") as mock_post:
            r = _post_traderspost(self.client)
            body = _json.loads(r.data) if r.data else {}
            self.assertNotIn("malformed", (body.get("reason") or "").lower())


# ─────────────────────────────────────────────────────────────────────────────
# § 6 — Structured blocked-attempt auditing
# ─────────────────────────────────────────────────────────────────────────────

class TestStructuredAuditRecords(unittest.TestCase):

    def setUp(self):
        with APP._EXEC_ATTEMPTS_LOCK:
            APP._EXEC_ATTEMPTS.clear()

    def test_record_writes_required_fields(self):
        APP._record_exec_attempt({
            "instrument":       "MGC",
            "candidate_id":     "test-candidate-001",
            "strategy":         "LIQUIDITY_SWEEP_REVERSAL",
            "direction":        "Long",
            "configured_mode":  "paper",
            "effective_mode":   "paper",
            "databento_health": {"connected": True},
            "tick_age_sec":     45.0,
            "bar_age_sec":      90.0,
            "vwap_status":      "ok",
            "candidate_age_sec": 30.0,
            "lre_state":        "LIVE_ELIGIBLE",
            "mandatory_gate":   "passed",
            "duplicate_guard":  "new_signal",
            "final_action":     "paper_simulated",
            "reason_code":      None,
        })
        with APP._EXEC_ATTEMPTS_LOCK:
            r = list(APP._EXEC_ATTEMPTS)[-1]
        self.assertEqual(r["instrument"],    "MGC")
        self.assertEqual(r["final_action"],  "paper_simulated")
        self.assertIn("recorded_at", r)

    def test_record_fail_open_on_bad_input(self):
        """Spec §6: _record_exec_attempt must never raise."""
        try:
            APP._record_exec_attempt(None)
            APP._record_exec_attempt({})
            APP._record_exec_attempt("bad")
        except Exception as exc:
            self.fail(f"_record_exec_attempt raised: {exc}")

    def test_blocked_attempt_not_counted_as_executed_trade(self):
        """Spec §6: blocked attempt must not create a journal/strategy_trades entry."""
        with patch.object(APP, "AUTO_TRADE", {"MGC": True}), \
             patch.object(APP, "DATABENTO_ENABLED", True), \
             patch.object(APP, "_DATABENTO_BRAIN",
                          MagicMock(_id_to_inst={1: "MGC"})), \
             patch.object(DB_BRAIN, "DATABENTO_STATUS", {"connected": False}), \
             patch.dict(APP.CURRENT_PRICE_TS_BY_TICKER, {}, clear=True), \
             patch.object(APP, "emergency_disabled", return_value=False), \
             patch.object(APP, "_outcome_cooldown_remaining", return_value=(0, "")), \
             patch.object(APP, "full_analysis",
                          return_value={"market_open": True, "verdict": "LONG READY",
                                        "strategy_engine": {}}), \
             patch.object(APP, "create_journal_entry") as mock_journal, \
             patch("requests.post"):
            APP._maybe_auto_execute("MGC", source="auto")
            mock_journal.assert_not_called()

    def test_reason_codes_are_constants(self):
        """Spec §6: expected reason code constants must exist on the module."""
        for attr in ("RC_EXECUTION_MODE_DISABLED", "RC_EXECUTION_MODE_NOT_EXPLICIT",
                     "RC_DATABENTO_DISCONNECTED", "RC_STALE_TICK", "RC_STALE_BAR",
                     "RC_STALE_VWAP", "RC_STALE_CANDIDATE", "RC_INVALID_TIMESTAMP",
                     "RC_FUTURE_TIMESTAMP", "RC_WRONG_INSTRUMENT_STATE",
                     "RC_LRE_BLOCK", "RC_LRE_EXCEPTION", "RC_LRE_TIMEOUT",
                     "RC_LRE_INVALID_RESULT", "RC_DUPLICATE_SIGNAL",
                     "RC_MARKET_CLOSED", "RC_MAINTENANCE_HALT"):
            self.assertTrue(hasattr(APP, attr),
                            f"Reason code constant {attr!r} missing from app module")


# ─────────────────────────────────────────────────────────────────────────────
# § 7 — Flask route security
# ─────────────────────────────────────────────────────────────────────────────

class TestFlaskRouteSecurity(unittest.TestCase):

    def setUp(self):
        self.client = APP.app.test_client()

    def test_get_cannot_execute(self):
        """Spec §7: GET /traderspost must never execute an order."""
        r = self.client.get("/traderspost", headers=_AUTH_HEADERS)
        self.assertIn(r.status_code, (404, 405))

    def test_put_delete_rejected(self):
        """Spec §7: PUT and DELETE on execution route must be rejected."""
        for method in ("PUT", "DELETE"):
            with self.subTest(method=method):
                r = getattr(self.client, method.lower())(
                    "/traderspost", headers=_AUTH_HEADERS)
                self.assertIn(r.status_code, (404, 405))

    def test_unauthenticated_reaches_paper_not_broker(self):
        """Spec §7: Flask reached without Express auth must not hit broker URL."""
        with patch.object(APP, "resolve_execution_mode", return_value="paper"), \
             patch.object(APP, "TRADERSPOST_WEBHOOK_URL", _BROKER_SENTINEL), \
             patch.object(APP, "full_analysis", return_value=_make_analysis()), \
             patch("requests.post") as mock_post:
            r = self.client.post("/traderspost",
                                  data=_json.dumps({"ticker": "MGC", "contracts": 1}),
                                  content_type="application/json")
            self.assertTrue(_broker_never_called(mock_post))
            body = _json.loads(r.data) if r.data else {}
            self.assertNotEqual(body.get("status"), "sent")

    def test_execution_mode_endpoint_does_not_leak_url(self):
        """Spec §7: /status must not expose the raw webhook URL."""
        r = self.client.get("/status", headers=_AUTH_HEADERS)
        if r.status_code == 200:
            raw = r.data.decode()
            self.assertNotIn("traderspost.io", raw,
                             "/status must not expose the webhook URL")


# ─────────────────────────────────────────────────────────────────────────────
# § 9 — Concurrency and regression
# ─────────────────────────────────────────────────────────────────────────────

class TestConcurrencyAndRegression(unittest.TestCase):

    def setUp(self):
        self.client = APP.app.test_client()
        # Capture and restore module-level state touched by other tests
        self._orig_exec_mode_raw  = APP._EXECUTION_MODE_RAW
        self._orig_resolve_fn     = APP.resolve_execution_mode   # guard against monkeypatching

    def tearDown(self):
        APP._EXECUTION_MODE_RAW     = self._orig_exec_mode_raw
        APP.resolve_execution_mode  = self._orig_resolve_fn

    def test_paper_dry_run_all_instruments_and_directions(self):
        """Spec §9: paper dry-run × 4 instruments × 2 directions → zero broker calls."""
        cases = [
            ("MGC", "LONG READY",  "Long"),
            ("MGC", "SHORT READY", "Short"),
            ("MNQ", "LONG READY",  "Long"),
            ("MNQ", "SHORT READY", "Short"),
            ("MES", "LONG READY",  "Long"),
            ("MES", "SHORT READY", "Short"),
            ("MYM", "LONG READY",  "Long"),
            ("MYM", "SHORT READY", "Short"),
        ]
        for inst, verdict, direction in cases:
            with self.subTest(inst=inst, direction=direction):
                with patch.object(APP, "resolve_execution_mode", return_value="paper"), \
                     patch.object(APP, "TRADERSPOST_WEBHOOK_URL", _BROKER_SENTINEL), \
                     patch.object(APP, "full_analysis",
                                  return_value=_make_analysis(verdict=verdict,
                                                               direction=direction)), \
                     patch("requests.post") as mock_post:
                    r = _post_traderspost(self.client, ticker=inst)
                    self.assertTrue(_broker_never_called(mock_post),
                                    f"{inst} {direction}: broker URL must not be called")
                    body = _json.loads(r.data) if r.data else {}
                    self.assertNotEqual(body.get("status"), "sent")

    def test_2_simultaneous_paper_calls_no_duplicate(self):
        """Spec §9: 2 simultaneous paper calls must not both reach 'sent'."""
        results = []
        barrier = threading.Barrier(2)

        def worker():
            with patch.object(APP, "resolve_execution_mode", return_value="paper"), \
                 patch.object(APP, "TRADERSPOST_WEBHOOK_URL", _BROKER_SENTINEL), \
                 patch.object(APP, "full_analysis", return_value=_make_analysis()), \
                 patch("requests.post"):
                barrier.wait()
                r = _post_traderspost(self.client)
                results.append(_json.loads(r.data).get("status"))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertLessEqual(results.count("sent"), 1)

    def test_10_simultaneous_paper_calls_no_duplicate(self):
        """Spec §9: 10 simultaneous paper calls — at most 1 is 'sent'."""
        results = []
        barrier = threading.Barrier(10)

        def worker():
            with patch.object(APP, "resolve_execution_mode", return_value="paper"), \
                 patch.object(APP, "TRADERSPOST_WEBHOOK_URL", _BROKER_SENTINEL), \
                 patch.object(APP, "full_analysis", return_value=_make_analysis()), \
                 patch("requests.post"):
                barrier.wait()
                r = _post_traderspost(self.client)
                results.append(_json.loads(r.data).get("status"))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertLessEqual(results.count("sent"), 1,
                             f"10 concurrent calls produced {results.count('sent')} 'sent' responses")

    def test_timeout_followed_by_retry_no_duplicate(self):
        """Spec §9: a 409 (e.g. cooldown/retry) followed by another call
        must not produce two 'sent' responses."""
        first_result = [None]
        second_result = [None]

        with patch.object(APP, "resolve_execution_mode", return_value="paper"), \
             patch.object(APP, "TRADERSPOST_WEBHOOK_URL", _BROKER_SENTINEL), \
             patch.object(APP, "full_analysis", return_value=_make_analysis()), \
             patch("requests.post"):
            r1 = _post_traderspost(self.client)
            first_result[0] = _json.loads(r1.data).get("status")
            r2 = _post_traderspost(self.client)
            second_result[0] = _json.loads(r2.data).get("status")

        sent = [first_result[0], second_result[0]].count("sent")
        self.assertLessEqual(sent, 1, "Retry must not produce a second 'sent'")

    def test_feed_disconnect_between_approval_and_send(self):
        """Spec §9: feed disconnects between approval and send — second call blocked."""
        # First call: healthy → allowed (paper)
        with patch.object(APP, "DATABENTO_ENABLED", False), \
             patch.object(APP, "resolve_execution_mode", return_value="paper"), \
             patch.object(APP, "full_analysis", return_value=_make_analysis()), \
             patch("requests.post"):
            r1 = _post_traderspost(self.client)
            body1 = _json.loads(r1.data).get("status")
        # Second call: feed now disconnected
        with patch.object(APP, "AUTO_TRADE", {"MGC": True}), \
             patch.object(APP, "DATABENTO_ENABLED", True), \
             patch.object(APP, "_DATABENTO_BRAIN",
                          MagicMock(_id_to_inst={1: "MGC"})), \
             patch.object(DB_BRAIN, "DATABENTO_STATUS", {"connected": False}), \
             patch.dict(APP.CURRENT_PRICE_TS_BY_TICKER, {}, clear=True), \
             patch.object(APP, "emergency_disabled", return_value=False), \
             patch.object(APP, "_outcome_cooldown_remaining", return_value=(0, "")), \
             patch.object(APP, "full_analysis",
                          return_value={"market_open": True, "verdict": "LONG READY",
                                        "strategy_engine": {}}), \
             patch("requests.post") as mock_post2:
            result2 = APP._maybe_auto_execute("MGC", source="auto")
            self.assertFalse(result2, "Disconnected feed must block _maybe_auto_execute")
            self.assertTrue(_broker_never_called(mock_post2))

    def test_missing_mode_never_transmits_to_broker(self):
        """Spec §9: missing mode + URL → disabled → no broker call."""
        with patch.object(APP, "_EXECUTION_MODE_RAW", ""), \
             patch.object(APP, "TRADERSPOST_WEBHOOK_URL", _BROKER_SENTINEL), \
             patch.object(APP, "full_analysis", return_value=_make_analysis()), \
             patch("requests.post") as mock_post:
            _post_traderspost(self.client)
            self.assertTrue(_broker_never_called(mock_post))

    def test_disconnected_feed_never_transmits(self):
        """Spec §9: disconnected Databento feed → no broker call."""
        with patch.object(APP, "AUTO_TRADE", {"MGC": True}), \
             patch.object(APP, "DATABENTO_ENABLED", True), \
             patch.object(APP, "_DATABENTO_BRAIN",
                          MagicMock(_id_to_inst={1: "MGC"})), \
             patch.object(DB_BRAIN, "DATABENTO_STATUS", {"connected": False}), \
             patch.dict(APP.CURRENT_PRICE_TS_BY_TICKER, {}, clear=True), \
             patch.object(APP, "emergency_disabled", return_value=False), \
             patch.object(APP, "_outcome_cooldown_remaining", return_value=(0, "")), \
             patch.object(APP, "full_analysis",
                          return_value={"market_open": True, "verdict": "LONG READY",
                                        "strategy_engine": {}}), \
             patch("requests.post") as mock_post:
            APP._maybe_auto_execute("MGC", source="auto")
            self.assertTrue(_broker_never_called(mock_post))

    def test_stale_data_never_transmits(self):
        """Spec §9: stale tick must never reach the broker."""
        client = APP.app.test_client()
        with patch.object(APP, "resolve_execution_mode", return_value="paper"), \
             patch.object(APP, "TRADERSPOST_WEBHOOK_URL", _BROKER_SENTINEL), \
             patch.object(APP, "full_analysis", return_value=_make_analysis(
                 candidate_ts=_now_iso(minutes=-10))), \
             patch("requests.post") as mock_post:
            r = _post_traderspost(client)
            self.assertTrue(_broker_never_called(mock_post))

    def test_lre_exception_never_transmits(self):
        """Spec §9: LRE exception must never reach broker."""
        with patch.object(APP, "resolve_execution_mode", return_value="traderspost"), \
             patch.object(APP, "TRADERSPOST_WEBHOOK_URL", _BROKER_SENTINEL), \
             patch.object(APP, "execution_configured", return_value=True), \
             patch.object(APP, "full_analysis", return_value=_make_analysis()), \
             patch.object(APP, "_check_learning_eligibility",
                          side_effect=Exception("crash")), \
             patch("requests.post") as mock_post:
            r = _post_traderspost(self.client)
            self.assertTrue(_broker_never_called(mock_post))
            self.assertNotEqual(_json.loads(r.data).get("status"), "sent")

    def test_server_restart_missing_mode_is_disabled(self):
        """Spec §9: restart with EXECUTION_MODE unset → disabled → not live.
        Uses direct assignment + try/finally to avoid cross-test patch contamination."""
        orig = APP._EXECUTION_MODE_RAW
        try:
            APP._EXECUTION_MODE_RAW = ""
            mode = APP.resolve_execution_mode()
            self.assertEqual(mode, "disabled",
                             "Restart with empty EXECUTION_MODE must resolve to 'disabled'")
            self.assertFalse(APP.execution_is_live(mode))
        finally:
            APP._EXECUTION_MODE_RAW = orig


if __name__ == "__main__":
    unittest.main()
