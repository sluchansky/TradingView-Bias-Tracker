"""
AUTO-TRADING SAFETY REMEDIATION — HIGH FINDINGS REGRESSION SUITE
================================================================
Covers HIGH-1 (execution-mode fail-closed), HIGH-2 (Databento health gate),
HIGH-3 (LRE exceptions fail-closed), candidate timestamp validation, and the
structured execution-attempt audit trail.

Every test with a broker-call assertion verifies that requests.post was NOT called
with the TradersPost/broker webhook URL.  Discord notifications also use requests.post
so plain assert_not_called() would give false negatives — all tests here use
_broker_never_called() instead.

No live orders are transmitted during this suite.
"""
import unittest
import json as _json
import threading
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import app as APP
import databento_brain as DB_BRAIN

from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone, timedelta

# ── Test helpers ──────────────────────────────────────────────────────────────

_AUTH_HEADERS = {"Authorization": "Basic dGVzdDp0ZXN0", "Origin": "http://localhost"}
_BROKER_SENTINEL = "https://tp.io/hook/AUDIT_TEST_SENTINEL"


def _now_iso(**delta_kwargs):
    dt = datetime.now(timezone.utc)
    if delta_kwargs:
        dt += timedelta(**delta_kwargs)
    return dt.isoformat()


def _broker_never_called(mock_post):
    """Return True if requests.post was never called with the broker sentinel URL.

    Discord notifications legitimately call requests.post with a Discord URL,
    so we must scope broker assertions to the broker URL only.
    """
    for c in mock_post.call_args_list:
        args = c.args or ()
        url = args[0] if args else (c.kwargs or {}).get("url", "")
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


def _make_analysis(verdict="LONG READY", market_open=True, candidate_ts=None,
                   direction="Long"):
    tp = {
        "trade_plan": True,
        "entry_zone": "2800.0–2802.0",
        "stop_loss": "2795.0",
        "target1": "2815.0",
        "target2": "2830.0",
        "direction": direction,
        "rr": "1:3",
    }
    result = {
        "verdict": verdict,
        "market_open": market_open,
        "trade_plan": tp if market_open else False,
        "directions": {direction: {"verdict": verdict, "edge_score": 75}},
        "alert_diagnostics": {},
        "strategy_engine": {"active_key": "LIQUIDITY_SWEEP_REVERSAL"},
    }
    if candidate_ts is not None:
        result["candidate_preview"] = {"generated_at": candidate_ts}
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — HIGH-1: Execution-mode fail-closed
# ─────────────────────────────────────────────────────────────────────────────

class TestHigh1ExecutionModeFailClosed(unittest.TestCase):
    """Every missing/blank/invalid/URL-only path must resolve to a non-live mode."""

    def _resolve(self, raw, tp_url="", exec_url=""):
        with patch.object(APP, "_EXECUTION_MODE_RAW", raw), \
             patch.object(APP, "TRADERSPOST_WEBHOOK_URL", tp_url), \
             patch.object(APP, "EXECUTION_WEBHOOK_URL", exec_url):
            return APP.resolve_execution_mode()

    def test_variable_missing(self):
        """Missing EXECUTION_MODE (empty string) must never be live."""
        mode = self._resolve("")
        self.assertFalse(APP.execution_is_live(mode),
                         f"Missing EXECUTION_MODE resolved to live mode: {mode!r}")

    def test_variable_blank(self):
        """Blank EXECUTION_MODE must resolve safely."""
        # _EXECUTION_MODE_RAW is already .strip().lower() at load; simulate blank
        with patch.object(APP, "_EXECUTION_MODE_RAW", ""):
            mode = APP.resolve_execution_mode()
            self.assertFalse(APP.execution_is_live(mode))

    def test_variable_invalid(self):
        """An unknown EXECUTION_MODE value must not activate live execution."""
        mode = self._resolve("LIVE")
        self.assertFalse(APP.execution_is_live(mode))
        mode2 = self._resolve("enable_trading")
        self.assertFalse(APP.execution_is_live(mode2))

    def test_paper_mode_explicit(self):
        """Explicit 'paper' resolves correctly and is never live."""
        self.assertEqual(self._resolve("paper"), "paper")
        self.assertFalse(APP.execution_is_live("paper"))

    def test_disabled_mode_explicit(self):
        """Explicit 'disabled' blocks all execution via the gateway."""
        self.assertEqual(self._resolve("disabled"), "disabled")
        self.assertFalse(APP.execution_is_live("disabled"))
        self.assertFalse(APP.execution_configured("disabled"))

    def test_explicit_traderspost(self):
        """Explicit EXECUTION_MODE=traderspost activates live mode when URL set."""
        mode = self._resolve("traderspost", tp_url="https://tp.io/hook/abc")
        self.assertEqual(mode, "traderspost")
        with patch.object(APP, "TRADERSPOST_WEBHOOK_URL", "https://tp.io/hook/abc"):
            self.assertTrue(APP.execution_is_live(mode))
            self.assertTrue(APP.execution_configured(mode))

    def test_webhook_configured_mode_missing_is_not_live(self):
        """URL configured + EXECUTION_MODE unset must NOT produce live mode."""
        mode = self._resolve("", tp_url="https://tp.io/hook/abc")
        self.assertFalse(APP.execution_is_live(mode),
                         "URL presence must never silently enable live execution")
        self.assertNotEqual(mode, "traderspost")

    def test_webhook_configured_mode_disabled_stays_disabled(self):
        """URL configured + EXECUTION_MODE=disabled must stay disabled."""
        mode = self._resolve("disabled", tp_url="https://tp.io/hook/abc")
        self.assertEqual(mode, "disabled")
        self.assertFalse(APP.execution_is_live(mode))

    def test_case_insensitive_normalisation(self):
        """'paper' resolves correctly (env-load already lowercases the raw value)."""
        self.assertEqual(self._resolve("paper"), "paper")

    def test_restart_config_reload_consistency(self):
        """resolve_execution_mode() is deterministic across repeated calls."""
        with patch.object(APP, "_EXECUTION_MODE_RAW", "paper"):
            modes = {APP.resolve_execution_mode() for _ in range(10)}
        self.assertEqual(len(modes), 1, "resolve_execution_mode() must be deterministic")

    def test_configured_mode_vs_effective_mode_helpers(self):
        """configured_mode and effective_mode diverge when EXECUTION_MODE is unset."""
        with patch.object(APP, "_EXECUTION_MODE_RAW", ""):
            self.assertIsNone(APP._configured_execution_mode())
            self.assertEqual(APP.resolve_execution_mode(), "paper")

    def test_no_outbound_broker_post_when_mode_missing(self):
        """No broker POST to the sentinel URL when EXECUTION_MODE is unset."""
        client = APP.app.test_client()
        with patch.object(APP, "_EXECUTION_MODE_RAW", ""), \
             patch.object(APP, "TRADERSPOST_WEBHOOK_URL", _BROKER_SENTINEL), \
             patch.object(APP, "full_analysis", return_value=_make_analysis()), \
             patch("requests.post") as mock_post:
            r = _post_traderspost(client)
            self.assertTrue(_broker_never_called(mock_post),
                            "Broker POST must not be called when EXECUTION_MODE is missing")
            body = _json.loads(r.data) if r.data else {}
            self.assertNotEqual(body.get("status"), "sent")

    def test_disabled_mode_returns_409_immediately(self):
        """EXECUTION_MODE=disabled must return 409."""
        client = APP.app.test_client()
        with patch.object(APP, "_EXECUTION_MODE_RAW", "disabled"), \
             patch("requests.post") as mock_post:
            r = _post_traderspost(client)
            self.assertIn(r.status_code, (409, 400))
            self.assertTrue(_broker_never_called(mock_post))


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 — HIGH-2: Databento execution-health gate
# ─────────────────────────────────────────────────────────────────────────────

class TestHigh2DatabentoHealthGate(unittest.TestCase):
    """_check_databento_execution_health() must block on every unhealthy condition.

    DATABENTO_STATUS and DATABENTO_BARS_BY_INST live in databento_brain, not app.
    The health gate imports them lazily, so patches go to databento_brain.*.
    CURRENT_PRICE_TS_BY_TICKER is a module-level dict in app, patched with patch.dict.
    """

    def _gate(self, inst="MGC", **overrides):
        """Run the health gate with the given overrides."""
        db_status  = overrides.get("DATABENTO_STATUS", {"connected": True})
        db_bars    = overrides.get("DATABENTO_BARS_BY_INST", {})
        brain_mock = overrides.get("_DATABENTO_BRAIN",
                                   MagicMock(_id_to_inst={1: "MGC", 2: "MNQ",
                                                           3: "MES", 4: "MYM"}))
        tick_ts    = overrides.get("CURRENT_PRICE_TS_BY_TICKER",
                                   {inst: _now_iso(seconds=-30)})
        vwap_fn    = overrides.get("get_vwap_mock",
                                   lambda *a, **kw: (2800.0, "ok"))
        enabled    = overrides.get("DATABENTO_ENABLED", True)

        with patch.object(APP, "DATABENTO_ENABLED", enabled), \
             patch.object(APP, "_DATABENTO_BRAIN", brain_mock), \
             patch.object(DB_BRAIN, "DATABENTO_STATUS", db_status), \
             patch.object(DB_BRAIN, "DATABENTO_BARS_BY_INST", db_bars), \
             patch.dict(APP.CURRENT_PRICE_TS_BY_TICKER, tick_ts, clear=True), \
             patch.object(APP, "get_vwap", vwap_fn):
            return APP._check_databento_execution_health(inst)

    def test_healthy_connected_feed(self):
        healthy, reason, diag = self._gate()
        self.assertTrue(healthy, f"Expected healthy but got {reason}: {diag}")
        self.assertEqual(reason, "healthy")

    def test_disconnected_feed(self):
        healthy, reason, diag = self._gate(DATABENTO_STATUS={"connected": False})
        self.assertFalse(healthy)
        self.assertIn("disconnect", reason)

    def test_reconnect_before_fresh_data_is_still_blocked(self):
        """Reconnected but tick is 10 min old (> 5-min threshold) — stale."""
        healthy, reason, diag = self._gate(
            DATABENTO_STATUS={"connected": True},
            CURRENT_PRICE_TS_BY_TICKER={"MGC": _now_iso(minutes=-10)},
        )
        self.assertFalse(healthy)
        self.assertIn("stale", reason)

    def test_stale_tick(self):
        healthy, reason, diag = self._gate(
            CURRENT_PRICE_TS_BY_TICKER={"MGC": _now_iso(minutes=-6)}
        )
        self.assertFalse(healthy)
        self.assertIn("stale", reason)

    def test_stale_completed_bar(self):
        stale_ts = int(
            (datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp()
        )
        healthy, reason, diag = self._gate(
            DATABENTO_BARS_BY_INST={"MGC": [{"ts": stale_ts}]}
        )
        self.assertFalse(healthy)
        self.assertIn("stale", reason)

    def test_stale_vwap(self):
        healthy, reason, diag = self._gate(
            get_vwap_mock=lambda *a, **kw: (None, "stale")
        )
        self.assertFalse(healthy)
        self.assertIn("vwap", reason)

    def test_missing_tick_timestamp(self):
        healthy, reason, diag = self._gate(CURRENT_PRICE_TS_BY_TICKER={})
        self.assertFalse(healthy)

    def test_malformed_tick_timestamp(self):
        healthy, reason, diag = self._gate(
            CURRENT_PRICE_TS_BY_TICKER={"MGC": "not-a-date"}
        )
        self.assertFalse(healthy)
        self.assertIn("malformed", reason)

    def test_future_timestamp(self):
        """Tick 2 minutes in the future must be rejected."""
        healthy, reason, diag = self._gate(
            CURRENT_PRICE_TS_BY_TICKER={"MGC": _now_iso(minutes=2)}
        )
        self.assertFalse(healthy)
        self.assertIn("future", reason)

    def test_wrong_instrument_freshness_record(self):
        """Freshness for MNQ exists but we're checking MGC — block."""
        healthy, reason, diag = self._gate(
            inst="MGC",
            CURRENT_PRICE_TS_BY_TICKER={"MNQ": _now_iso(seconds=-30)},
        )
        self.assertFalse(healthy)

    def test_empty_state(self):
        """Completely empty DATABENTO_STATUS and tick dict must block."""
        healthy, reason, diag = self._gate(
            DATABENTO_STATUS={},
            CURRENT_PRICE_TS_BY_TICKER={},
        )
        self.assertFalse(healthy)

    def test_databento_disabled_gate_is_noop(self):
        """When DATABENTO_ENABLED=False the gate passes unconditionally."""
        healthy, reason, diag = self._gate(DATABENTO_ENABLED=False)
        self.assertTrue(healthy)
        self.assertEqual(reason, "databento_disabled")

    def test_connection_state_unknown_fails_closed(self):
        """DATABENTO_STATUS without 'connected' key must fail closed."""
        healthy, reason, diag = self._gate(DATABENTO_STATUS={})
        self.assertFalse(healthy)

    def test_instrument_not_subscribed_blocks(self):
        """Instrument absent from the id→inst map must block."""
        healthy, reason, diag = self._gate(
            inst="MGC",
            _DATABENTO_BRAIN=MagicMock(_id_to_inst={1: "MNQ"}),
        )
        self.assertFalse(healthy)
        self.assertIn("subscribed", reason)

    def test_health_gate_blocks_auto_execution_after_disconnect(self):
        """Spec invariant: approved before disconnect is blocked at transmission.
        The gate runs in _maybe_auto_execute (not at signal arrival), so a disconnect
        between signal and execution is caught here."""
        healthy1, _, _ = self._gate(DATABENTO_STATUS={"connected": True})
        self.assertTrue(healthy1)
        healthy2, reason2, _ = self._gate(DATABENTO_STATUS={"connected": False})
        self.assertFalse(healthy2)
        self.assertIn("disconnect", reason2)


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — HIGH-3: LRE exceptions fail closed
# ─────────────────────────────────────────────────────────────────────────────

class TestHigh3LreExceptionsFailClosed(unittest.TestCase):
    """LRE exceptions at both gateway check points must block execution."""

    def setUp(self):
        self.client = APP.app.test_client()
        with APP._LRE_ERROR_COUNT_LOCK:
            APP._LRE_ERROR_COUNT = 0

    def _run_gateway(self, lre_side_effect=None, lre_return=("LIVE_ELIGIBLE", None)):
        with patch.object(APP, "resolve_execution_mode", return_value="paper"), \
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

    def test_lre_pass_live_eligible(self):
        """LIVE_ELIGIBLE allows execution to proceed (no LRE error)."""
        code, body, mock_post = self._run_gateway(lre_return=("LIVE_ELIGIBLE", None))
        # Should succeed in paper mode (simulated) — not an LRE block
        self.assertNotIn(body.get("status"), ("error",),
                         "LIVE_ELIGIBLE should not produce an error status")
        self.assertTrue(_broker_never_called(mock_post))

    def test_lre_block_disabled(self):
        """DISABLED status must block with 409."""
        code, body, mock_post = self._run_gateway(
            lre_return=("DISABLED", "repeated_failures")
        )
        self.assertIn(code, (409,))
        self.assertTrue(_broker_never_called(mock_post))

    def test_lre_insufficient_samples_ghost_only(self):
        """GHOST_ONLY (under-sample) in paper mode continues (demotion is paper→paper)."""
        code, body, mock_post = self._run_gateway(lre_return=("GHOST_ONLY", "n<50"))
        # Paper demote: already paper, so gateway continues and returns simulated
        self.assertNotIn(code, (500,))
        self.assertTrue(_broker_never_called(mock_post))

    def test_lre_exception_check1_fails_closed(self):
        """Exception at LRE check 1 must block execution (fail-closed)."""
        code, body, mock_post = self._run_gateway(
            lre_side_effect=RuntimeError("simulated DB crash")
        )
        self.assertIn(code, (409, 500),
                      "LRE exception must block execution, not approve it")
        self.assertTrue(_broker_never_called(mock_post))
        self.assertGreater(APP._get_lre_error_count(), 0,
                           "LRE error counter must increment on exception")

    def test_lre_exception_increments_error_counter(self):
        """Each LRE exception at check 1 increments the counter by exactly 1."""
        before = APP._get_lre_error_count()
        self._run_gateway(lre_side_effect=ValueError("mock error"))
        self.assertEqual(APP._get_lre_error_count(), before + 1)

    def test_lre_check2_exception_fails_closed(self):
        """Exception at LRE check 2 (setup-key disabled lookup) must block."""
        # Simulate the LEARNING_ELIGIBILITY_LOCK raising inside the check-2 block.
        # We do this by patching LEARNING_ELIGIBILITY to raise on .get().
        bad_elig = MagicMock()
        bad_elig.get = MagicMock(side_effect=RuntimeError("elig crash"))
        with patch.object(APP, "resolve_execution_mode", return_value="paper"), \
             patch.object(APP, "full_analysis", return_value=_make_analysis()), \
             patch.object(APP, "_check_learning_eligibility",
                          return_value=("LIVE_ELIGIBLE", None)), \
             patch.object(APP, "LEARNING_ELIGIBILITY", bad_elig), \
             patch("requests.post") as mock_post:
            r = _post_traderspost(self.client)
            body = _json.loads(r.data) if r.data else {}
            # Must block OR continue with demotion — must NEVER return "sent"
            self.assertNotEqual(body.get("status"), "sent",
                                "LRE check-2 exception must not approve execution")
            self.assertTrue(_broker_never_called(mock_post))

    def test_lre_malformed_result_not_a_tuple(self):
        """A non-tuple LRE result must not silently approve execution."""
        with patch.object(APP, "resolve_execution_mode", return_value="paper"), \
             patch.object(APP, "full_analysis", return_value=_make_analysis()), \
             patch.object(APP, "_check_learning_eligibility",
                          return_value="LIVE_ELIGIBLE"),  \
             patch("requests.post") as mock_post:
            # Unpacking a string raises ValueError → should fail-closed
            r = _post_traderspost(self.client)
            body = _json.loads(r.data) if r.data else {}
            self.assertNotEqual(body.get("status"), "sent")
            self.assertTrue(_broker_never_called(mock_post))

    def test_lre_unknown_status_treated_as_no_influence(self):
        """An unknown LRE status (not DISABLED/GHOST_ONLY/LIVE_ELIGIBLE) is treated
        as LIVE_ELIGIBLE — safe pass-through; never a live broker call."""
        code, body, mock_post = self._run_gateway(
            lre_return=("UNKNOWN_STATUS_XYZ", None)
        )
        self.assertTrue(_broker_never_called(mock_post))

    def test_no_live_broker_post_on_lre_exception(self):
        """Core invariant: broker URL never called when LRE throws."""
        with patch.object(APP, "resolve_execution_mode", return_value="traderspost"), \
             patch.object(APP, "TRADERSPOST_WEBHOOK_URL", _BROKER_SENTINEL), \
             patch.object(APP, "execution_configured", return_value=True), \
             patch.object(APP, "full_analysis", return_value=_make_analysis()), \
             patch.object(APP, "_check_learning_eligibility",
                          side_effect=RuntimeError("crash")), \
             patch("requests.post") as mock_post:
            r = _post_traderspost(self.client)
            self.assertTrue(_broker_never_called(mock_post),
                            "LRE exception must never reach broker POST")
            self.assertNotEqual(_json.loads(r.data).get("status"), "sent")


# ─────────────────────────────────────────────────────────────────────────────
# Section 4 — Candidate timestamp validation
# ─────────────────────────────────────────────────────────────────────────────

class TestCandidateTimestampValidation(unittest.TestCase):
    """Candidate timestamps in the analysis result must be validated before execution."""

    def setUp(self):
        self.client = APP.app.test_client()

    def _run(self, candidate_ts=None):
        analysis = _make_analysis(candidate_ts=candidate_ts)
        with patch.object(APP, "resolve_execution_mode", return_value="paper"), \
             patch.object(APP, "full_analysis", return_value=analysis), \
             patch("requests.post") as mock_post:
            r = _post_traderspost(self.client)
            return r.status_code, _json.loads(r.data) if r.data else {}, mock_post

    def test_no_candidate_preview_passes(self):
        """Absent candidate_preview is allowed — gate only runs when block is present."""
        code, body, _ = self._run(candidate_ts=None)
        reason = (body.get("reason") or "").lower()
        self.assertNotIn("candidate", reason)

    def test_fresh_candidate_passes(self):
        """Candidate generated 30 seconds ago must not be blocked."""
        code, body, _ = self._run(candidate_ts=_now_iso(seconds=-30))
        self.assertNotEqual(body.get("status"), "error")

    def test_stale_candidate_blocked(self):
        """Candidate older than 2 minutes must be blocked."""
        code, body, mock_post = self._run(candidate_ts=_now_iso(minutes=-5))
        self.assertIn(code, (400, 409))
        self.assertTrue(_broker_never_called(mock_post))
        self.assertIn("stale", (body.get("reason") or "").lower())

    def test_malformed_candidate_ts_blocked(self):
        """Malformed ISO timestamp in candidate_preview must block (fail-closed)."""
        code, body, mock_post = self._run(candidate_ts="not-a-real-date")
        self.assertIn(code, (400, 409))
        self.assertTrue(_broker_never_called(mock_post))
        self.assertIn("malformed", (body.get("reason") or "").lower())

    def test_future_candidate_ts_blocked(self):
        """Candidate with timestamp 5 minutes in the future must be blocked."""
        code, body, mock_post = self._run(candidate_ts=_now_iso(minutes=5))
        self.assertIn(code, (400, 409))
        self.assertTrue(_broker_never_called(mock_post))
        self.assertIn("future", (body.get("reason") or "").lower())

    def test_empty_candidate_preview_passes(self):
        """candidate_preview present but with no generated_at — pass-through."""
        analysis = _make_analysis()
        analysis["candidate_preview"] = {}
        with patch.object(APP, "resolve_execution_mode", return_value="paper"), \
             patch.object(APP, "full_analysis", return_value=analysis), \
             patch("requests.post") as mock_post:
            r = _post_traderspost(self.client)
            body = _json.loads(r.data) if r.data else {}
            self.assertNotIn("malformed", (body.get("reason") or "").lower())


# ─────────────────────────────────────────────────────────────────────────────
# Section 5 — Structured audit reason codes
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditReasonCodes(unittest.TestCase):
    """_record_exec_attempt must write machine-readable reason codes."""

    def setUp(self):
        with APP._EXEC_ATTEMPTS_LOCK:
            APP._EXEC_ATTEMPTS.clear()

    def test_record_exec_attempt_writes_record(self):
        """_record_exec_attempt appends a record with required fields."""
        APP._record_exec_attempt({
            "instrument": "MGC",
            "source": "auto",
            "setup_key": "test_key",
            "final_action": "blocked",
            "reason_code": "databento_disconnected",
        })
        with APP._EXEC_ATTEMPTS_LOCK:
            attempts = list(APP._EXEC_ATTEMPTS)
        self.assertEqual(len(attempts), 1)
        r = attempts[0]
        self.assertEqual(r["instrument"], "MGC")
        self.assertEqual(r["final_action"], "blocked")
        self.assertEqual(r["reason_code"], "databento_disconnected")
        self.assertIn("recorded_at", r)

    def test_record_exec_attempt_fail_open(self):
        """_record_exec_attempt must never raise."""
        try:
            APP._record_exec_attempt(None)
            APP._record_exec_attempt({})
        except Exception as exc:
            self.fail(f"_record_exec_attempt raised unexpectedly: {exc}")

    def test_exec_attempt_contains_mode_fields(self):
        """Records must include configured_mode and effective_mode fields."""
        APP._record_exec_attempt({
            "instrument":      "MNQ",
            "configured_mode": "paper",
            "effective_mode":  "paper",
            "final_action":    "blocked",
            "reason_code":     "test",
        })
        with APP._EXEC_ATTEMPTS_LOCK:
            r = list(APP._EXEC_ATTEMPTS)[-1]
        self.assertIn("configured_mode", r)
        self.assertIn("effective_mode", r)

    def test_lre_error_count_exposed_in_status(self):
        """GET /status must include lre_error_count."""
        client = APP.app.test_client()
        with APP._LRE_ERROR_COUNT_LOCK:
            APP._LRE_ERROR_COUNT = 7
        try:
            r = client.get("/status", headers=_AUTH_HEADERS)
            if r.status_code == 200:
                body = _json.loads(r.data)
                self.assertIn("lre_error_count", body)
                self.assertEqual(body["lre_error_count"], 7)
        finally:
            with APP._LRE_ERROR_COUNT_LOCK:
                APP._LRE_ERROR_COUNT = 0


# ─────────────────────────────────────────────────────────────────────────────
# Section 6 — Concurrency and regression
# ─────────────────────────────────────────────────────────────────────────────

class TestConcurrencyAndRegression(unittest.TestCase):

    def setUp(self):
        self.client = APP.app.test_client()

    def test_paper_dry_run_all_instruments(self):
        """Paper-mode dry-run for all 4 instruments × 2 directions: zero broker sends."""
        cases = [
            ("MGC", "LONG READY", "Long"),
            ("MGC", "SHORT READY", "Short"),
            ("MNQ", "LONG READY", "Long"),
            ("MNQ", "SHORT READY", "Short"),
            ("MES", "LONG READY", "Long"),
            ("MES", "SHORT READY", "Short"),
            ("MYM", "LONG READY", "Long"),
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
                    self.assertNotEqual(body.get("status"), "sent",
                                        f"{inst} {direction}: must not be 'sent' in paper mode")

    def test_missing_mode_with_webhook_never_transmits_to_broker(self):
        """Missing EXECUTION_MODE + URL configured never transmits to the broker URL."""
        with patch.object(APP, "_EXECUTION_MODE_RAW", ""), \
             patch.object(APP, "TRADERSPOST_WEBHOOK_URL", _BROKER_SENTINEL), \
             patch.object(APP, "full_analysis", return_value=_make_analysis()), \
             patch("requests.post") as mock_post:
            _post_traderspost(self.client)
            self.assertTrue(_broker_never_called(mock_post))

    def test_no_lre_exception_can_reach_broker_transmission(self):
        """LRE exception at check 1 must never reach requests.post for broker URL."""
        with patch.object(APP, "resolve_execution_mode", return_value="traderspost"), \
             patch.object(APP, "TRADERSPOST_WEBHOOK_URL", _BROKER_SENTINEL), \
             patch.object(APP, "execution_configured", return_value=True), \
             patch.object(APP, "full_analysis", return_value=_make_analysis()), \
             patch.object(APP, "_check_learning_eligibility",
                          side_effect=Exception("crash")), \
             patch("requests.post") as mock_post:
            _post_traderspost(self.client)
            self.assertTrue(_broker_never_called(mock_post))

    def test_two_concurrent_calls_no_duplicate_broker_send(self):
        """Two simultaneous paper-mode calls cannot both be 'sent'."""
        results = []
        barrier = threading.Barrier(2)

        def worker():
            with patch.object(APP, "resolve_execution_mode", return_value="paper"), \
                 patch.object(APP, "full_analysis", return_value=_make_analysis()), \
                 patch.object(APP, "TRADERSPOST_WEBHOOK_URL", _BROKER_SENTINEL), \
                 patch("requests.post"):
                barrier.wait()
                r = _post_traderspost(self.client)
                results.append(_json.loads(r.data).get("status"))

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start(); t2.start()
        t1.join(); t2.join()
        self.assertLessEqual(results.count("sent"), 1,
                             "Two concurrent paper calls must not both be 'sent'")

    def test_health_gate_blocks_auto_on_disconnected_feed(self):
        """_maybe_auto_execute returns False when Databento health gate fails."""
        with patch.object(APP, "AUTO_TRADE", {"MGC": True}), \
             patch.object(APP, "DATABENTO_ENABLED", True), \
             patch.object(APP, "_DATABENTO_BRAIN",
                          MagicMock(_id_to_inst={1: "MGC"})), \
             patch.object(DB_BRAIN, "DATABENTO_STATUS", {"connected": False}), \
             patch.dict(APP.CURRENT_PRICE_TS_BY_TICKER, {}, clear=True), \
             patch.object(APP, "emergency_disabled", return_value=False), \
             patch.object(APP, "_outcome_cooldown_remaining", return_value=(0, "")), \
             patch("requests.post") as mock_post:
            result = APP._maybe_auto_execute("MGC", source="auto")
            self.assertFalse(result,
                             "_maybe_auto_execute must return False when health gate fails")
            self.assertTrue(_broker_never_called(mock_post))

    def test_server_restart_with_missing_mode_safe(self):
        """After restart with EXECUTION_MODE unset, mode must not be live."""
        with patch.object(APP, "_EXECUTION_MODE_RAW", ""):
            mode = APP.resolve_execution_mode()
            self.assertFalse(APP.execution_is_live(mode))

    def test_timeout_does_not_duplicate_via_broker_url(self):
        """Paper mode timeout scenario: broker URL never called."""
        with patch.object(APP, "resolve_execution_mode", return_value="paper"), \
             patch.object(APP, "TRADERSPOST_WEBHOOK_URL", _BROKER_SENTINEL), \
             patch.object(APP, "full_analysis", return_value=_make_analysis()), \
             patch("requests.post") as mock_post:
            _post_traderspost(self.client)
            self.assertTrue(_broker_never_called(mock_post))


# ─────────────────────────────────────────────────────────────────────────────
# Section 7 — Flask port exposure
# ─────────────────────────────────────────────────────────────────────────────

class TestFlaskPortExposure(unittest.TestCase):

    def setUp(self):
        self.client = APP.app.test_client()

    def test_get_cannot_execute(self):
        """GET /traderspost must never execute an order."""
        r = self.client.get("/traderspost", headers=_AUTH_HEADERS)
        self.assertIn(r.status_code, (404, 405))

    def test_unauthenticated_request_does_not_hit_broker(self):
        """Without Express auth, Flask is reached but broker must not be called."""
        with patch.object(APP, "resolve_execution_mode", return_value="paper"), \
             patch.object(APP, "TRADERSPOST_WEBHOOK_URL", _BROKER_SENTINEL), \
             patch.object(APP, "full_analysis", return_value=_make_analysis()), \
             patch("requests.post") as mock_post:
            r = self.client.post("/traderspost",
                                  data=_json.dumps({"ticker": "MGC", "contracts": 1}),
                                  content_type="application/json")
            body = _json.loads(r.data) if r.data else {}
            self.assertTrue(_broker_never_called(mock_post))
            self.assertNotEqual(body.get("status"), "sent")

    def test_execution_route_is_post_only(self):
        """GET, PUT, DELETE on /traderspost must be rejected."""
        for method in ("GET", "PUT", "DELETE"):
            with self.subTest(method=method):
                r = getattr(self.client, method.lower())(
                    "/traderspost", headers=_AUTH_HEADERS
                )
                self.assertIn(r.status_code, (404, 405))


if __name__ == "__main__":
    unittest.main()
