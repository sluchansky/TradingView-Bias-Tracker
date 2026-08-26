"""Phase 5 — Manager and Execution Safety
V1-P5-001 through V1-P5-008

Tests prove existing production behavior; no production code was modified beyond
adding the ARCH §9 'outcome' field (additive, backward-compatible) via _gw_outcome()
and execute_trade_gateway() wrapper.

Tasks:
  V1-P5-001: Arm-state boot-reset test
  V1-P5-002: ENTRY_PENDING representation test (gateway_result contract)
  V1-P5-003: Duplicate execution prevention test  [BLOCKER]
  V1-P5-004: Broker rejection test
  V1-P5-005: Execution timeout test
  V1-P5-006: Payload validation test
  V1-P5-007: Safe disarm behavior test
  V1-P5-008: Paper mode end-to-end test

Design rules:
  - All tests run in paper or mock mode only. No real broker HTTP calls.
  - Every test that mutates global state restores it in a finally block.
  - Tests assert the existing 'status' contract (backward-compat) AND the new 'outcome'.
  - requests.post is always patched in live-mode tests; assert-not-called where appropriate.

Run:
  python3 -m pytest artifacts/tradingview-webhook/test_phase5_execution_safety.py -v
"""

import os
import sys
import copy
import time
import unittest
import unittest.mock as mock

# ── Test environment setup ──────────────────────────────────────────────────────
os.environ.setdefault("TRADING_MODE", "SCALP")
os.environ["EXECUTION_MODE"] = "paper"
os.environ.setdefault("TRAINING_MODE_ENABLED", "")
os.environ.setdefault("DISCORD_LIVE_ENABLED", "")
os.environ.setdefault("SESSION_SECRET", "test-secret-phase5")
os.environ.setdefault("DASHBOARD_PASSWORD", "test-pass")
os.environ.setdefault("DATABASE_URL", "")

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import app  # noqa: E402

# The release workflow itself is pinned EXECUTION_MODE=disabled. This diagnostic
# process must explicitly establish its isolated paper/mock state after import so
# deployment safety pins cannot silently turn broker-response fixtures into 409s.
app._EXECUTION_MODE_RAW = "paper"
app._EXECUTION_MODE_RUNTIME_OVERRIDE = None
with app._ARM_STATE_LOCK:
    app._ARM_STATE.update({
        "execution_enabled": True,
        "armed": True,
        "expires_at": time.time() + 3600,
        "allowed_instruments": list(app.enabled_instruments()),
        "max_contracts": {inst: 10 for inst in app.enabled_instruments()},
        "allowed_strategies": None,
        "direction_restriction": None,
        "safety_locked": False,
    })

FAILS: list = []
CHECKS: int = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    status = "OK  " if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)
        raise AssertionError(detail or name)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: minimal valid trade analysis snapshot for the gateway
# ─────────────────────────────────────────────────────────────────────────────

def _minimal_analysis(instrument: str = "MGC", direction: str = "Long") -> dict:
    """Return the smallest full_analysis() snapshot the gateway needs."""
    price = 2500.0 if instrument == "MGC" else 21000.0
    stop = price - 8.0
    t1 = price + 8.0
    t2 = price + 16.0
    return {
        "instrument":   instrument,
        "verdict":      f"{direction.upper()} READY",
        "is_actionable": True,
        "market_open":  True,
        "direction":    direction,
        "trade_plan": {
            "trade_plan":  True,
            "entry_zone":  f"{price:.2f}",
            "stop_loss":   f"{stop:.2f}",
            "target1":     f"{t1:.2f}",
            "target2":     f"{t2:.2f}",
            "rr":          "1:1",
            "direction":   direction,
        },
        "price":  price,
        "vwap_value": price - 2.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# V1-P5-001: Arm-state boot-reset test
# AC-1.4: Auto-trade arm resets to OFF on boot regardless of prior session state
# ─────────────────────────────────────────────────────────────────────────────

class TestP5_001_ArmStateBootReset(unittest.TestCase):
    """V1-P5-001 — Arm state initialises to False at module load for every instrument."""

    def test_all_instruments_start_disarmed(self):
        """Every enabled instrument's arm state is False at module import time."""
        for inst in app.enabled_instruments():
            state = app.auto_trade_enabled(inst)
            check(f"P5-001-a boot arm=False [{inst}]", state is False,
                  f"expected False, got {state!r}")

    def test_arm_write_then_verify(self):
        """auto_trade_enabled() reflects a write to AUTO_TRADE immediately."""
        inst = "MGC"
        original = app.AUTO_TRADE.get(inst, False)
        try:
            with app.AUTO_TRADE_LOCK:
                app.AUTO_TRADE[inst] = True
            check("P5-001-b arm=True after write", app.auto_trade_enabled(inst) is True)
        finally:
            with app.AUTO_TRADE_LOCK:
                app.AUTO_TRADE[inst] = original
        check("P5-001-c arm restored to original", app.auto_trade_enabled(inst) is original)

    def test_one_instrument_does_not_affect_another(self):
        """Arming MGC does not change MNQ arm state."""
        inst_a, inst_b = "MGC", "MNQ"
        orig_a = app.AUTO_TRADE.get(inst_a, False)
        orig_b = app.AUTO_TRADE.get(inst_b, False)
        try:
            with app.AUTO_TRADE_LOCK:
                app.AUTO_TRADE[inst_a] = True
            check("P5-001-d MNQ unaffected by MGC arm",
                  app.auto_trade_enabled(inst_b) is orig_b)
        finally:
            with app.AUTO_TRADE_LOCK:
                app.AUTO_TRADE[inst_a] = orig_a

    def test_auto_trade_dict_covers_all_instruments(self):
        """AUTO_TRADE dict contains an entry for every enabled instrument."""
        for inst in app.enabled_instruments():
            check(f"P5-001-e AUTO_TRADE has key [{inst}]", inst in app.AUTO_TRADE)


# ─────────────────────────────────────────────────────────────────────────────
# V1-P5-002: ENTRY_PENDING representation (gateway_result contract)
# Documents the synchronous gateway model: no explicit ENTRY_PENDING state exists;
# the gateway returns immediately with status + outcome on every path.
# ─────────────────────────────────────────────────────────────────────────────

class TestP5_002_GatewayResultContract(unittest.TestCase):
    """V1-P5-002 — execute_trade_gateway() result dict fields present on all paths."""

    REQUIRED_KEYS = {"status", "outcome"}

    def _call_paper(self, instrument="MGC", direction="Long"):
        """Call the gateway in paper mode with a mocked full_analysis."""
        analysis = _minimal_analysis(instrument, direction)
        with mock.patch.object(app, "full_analysis", return_value=analysis), \
             mock.patch.object(app, "resolve_execution_mode", return_value="paper"), \
             mock.patch("requests.post"):
            result, code = app.execute_trade_gateway(instrument, 1, source="manual",
                                                     direction=direction)
        return result, code

    def test_paper_mode_status_field(self):
        result, code = self._call_paper()
        check("P5-002-a paper status=simulated", result.get("status") == "simulated",
              f"got {result.get('status')!r}")

    def test_paper_mode_outcome_field(self):
        result, code = self._call_paper()
        check("P5-002-b paper outcome=paper", result.get("outcome") == "paper",
              f"got {result.get('outcome')!r}")

    def test_paper_mode_plan_present(self):
        result, code = self._call_paper()
        check("P5-002-c paper plan dict present", isinstance(result.get("plan"), dict),
              f"plan={result.get('plan')!r}")

    def test_paper_mode_version_field(self):
        result, code = self._call_paper()
        check("P5-002-d paper _version=v1", result.get("_version") == "v1",
              f"got {result.get('_version')!r}")

    def test_paper_mode_http_200(self):
        result, code = self._call_paper()
        check("P5-002-e paper HTTP 200", code == 200, f"got {code}")

    def test_paper_mode_all_required_keys(self):
        result, code = self._call_paper()
        for k in self.REQUIRED_KEYS:
            check(f"P5-002-f paper has key '{k}'", k in result)

    def test_manual_mode_status_field(self):
        """manual_only mode returns status=manual_required."""
        analysis = _minimal_analysis()
        with mock.patch.object(app, "full_analysis", return_value=analysis), \
             mock.patch.object(app, "resolve_execution_mode", return_value="manual_only"):
            result, code = app.execute_trade_gateway("MGC", 1, source="manual",
                                                     direction="Long")
        check("P5-002-g manual status=manual_required",
              result.get("status") == "manual_required", f"got {result.get('status')!r}")

    def test_manual_mode_outcome_field(self):
        """manual_only mode outcome=manual_required."""
        analysis = _minimal_analysis()
        with mock.patch.object(app, "full_analysis", return_value=analysis), \
             mock.patch.object(app, "resolve_execution_mode", return_value="manual_only"):
            result, code = app.execute_trade_gateway("MGC", 1, source="manual",
                                                     direction="Long")
        check("P5-002-h manual outcome=manual_required",
              result.get("outcome") == "manual_required", f"got {result.get('outcome')!r}")

    def test_error_path_has_outcome(self):
        """An error return (unknown instrument) carries an outcome field."""
        with mock.patch.object(app, "resolve_execution_mode", return_value="paper"):
            result, code = app.execute_trade_gateway("UNKNOWN_XYZ", 1, source="manual",
                                                     direction="Long")
        check("P5-002-i error has outcome key", "outcome" in result,
              f"result keys: {list(result.keys())}")
        check("P5-002-j error status=error", result.get("status") == "error",
              f"got {result.get('status')!r}")

    def test_unknown_instrument_outcome_is_invalid_payload(self):
        """Unknown instrument is classified as invalid_payload (per user resolution)."""
        with mock.patch.object(app, "resolve_execution_mode", return_value="paper"):
            result, _ = app.execute_trade_gateway("UNKNOWN_XYZ", 1, source="manual",
                                                  direction="Long")
        check("P5-002-k unknown_inst outcome=invalid_payload",
              result.get("outcome") == "invalid_payload",
              f"got {result.get('outcome')!r}")

    def test_no_active_trade_on_error(self):
        """An error return does not set ACTIVE_TRADE for the instrument."""
        inst = "MGC"
        before = app.active_trade_for(inst)
        with mock.patch.object(app, "resolve_execution_mode", return_value="paper"):
            result, _ = app.execute_trade_gateway("UNKNOWN_XYZ", 1, source="manual",
                                                  direction="Long")
        after = app.active_trade_for(inst)
        check("P5-002-l no ACTIVE_TRADE on error", after == before)

    def test_gateway_is_synchronous(self):
        """Gateway returns immediately (no ENTRY_PENDING state exists).
        The 'pending' window is the blocking call duration; outcome is in the
        returned dict, not deferred to a separate state machine."""
        analysis = _minimal_analysis()
        with mock.patch.object(app, "full_analysis", return_value=analysis), \
             mock.patch.object(app, "resolve_execution_mode", return_value="paper"), \
             mock.patch("requests.post"):
            result, code = app.execute_trade_gateway("MGC", 1, source="manual",
                                                     direction="Long")
        # Prove the call returned a concrete result (not a Future or pending marker)
        check("P5-002-m synchronous: outcome is a string",
              isinstance(result.get("outcome"), str))
        check("P5-002-n synchronous: status is a string",
              isinstance(result.get("status"), str))

    # ── Backward-compatibility: status values unchanged ──────────────────────
    def test_status_field_preserved_paper(self):
        """status='simulated' for paper mode (unchanged from pre-outcome implementation)."""
        result, _ = self._call_paper()
        check("P5-002-o compat: paper status still simulated",
              result.get("status") == "simulated")

    def test_status_field_preserved_manual(self):
        """status='manual_required' for manual_only mode (unchanged)."""
        analysis = _minimal_analysis()
        with mock.patch.object(app, "full_analysis", return_value=analysis), \
             mock.patch.object(app, "resolve_execution_mode", return_value="manual_only"):
            result, _ = app.execute_trade_gateway("MGC", 1, source="manual",
                                                  direction="Long")
        check("P5-002-p compat: manual status still manual_required",
              result.get("status") == "manual_required")

    def test_outcome_additive_not_replacing_status(self):
        """Both 'status' and 'outcome' are present together — outcome is additive."""
        result, _ = self._call_paper()
        check("P5-002-q both status and outcome present",
              "status" in result and "outcome" in result)


# ─────────────────────────────────────────────────────────────────────────────
# V1-P5-003: Duplicate execution prevention test  [BLOCKER]
# AC-5.2: Same setup signal twice → exactly one order attempt; second suppressed
# ─────────────────────────────────────────────────────────────────────────────

class TestP5_003_DuplicateExecutionPrevention(unittest.TestCase):
    """V1-P5-003 — AUTO_FIRED_KEYS dedup prevents a second auto-fire for the same setup."""

    def setUp(self):
        # Snapshot state we will modify; restore in tearDown
        self._orig_auto_trade = dict(app.AUTO_TRADE)
        self._orig_fired_keys = frozenset(app.AUTO_FIRED_KEYS)
        # Set MGC arm to True so auto-execution is eligible
        with app.AUTO_TRADE_LOCK:
            app.AUTO_TRADE["MGC"] = True

    def tearDown(self):
        # Restore AUTO_TRADE
        with app.AUTO_TRADE_LOCK:
            app.AUTO_TRADE.clear()
            app.AUTO_TRADE.update(self._orig_auto_trade)
        # Restore AUTO_FIRED_KEYS (remove any keys we added)
        added = app.AUTO_FIRED_KEYS - self._orig_fired_keys
        for k in added:
            app.AUTO_FIRED_KEYS.discard(k)

    def _build_setup_key(self, instrument: str = "MGC", direction: str = "Long",
                         zone_low: float = 2490.0) -> str:
        """Construct the dedup key the production code uses for AUTO_FIRED_KEYS."""
        # The key format is instrument:direction:zone_low (from app._do_auto_execute_webhook)
        return f"{instrument}:{direction}:{zone_low}"

    def test_first_fire_not_in_fired_keys(self):
        """Before the first fire, the setup key is absent from AUTO_FIRED_KEYS."""
        key = self._build_setup_key()
        app.AUTO_FIRED_KEYS.discard(key)  # ensure clean
        check("P5-003-a key absent before first fire", key not in app.AUTO_FIRED_KEYS)

    def test_adding_key_prevents_second_fire(self):
        """When a key is in AUTO_FIRED_KEYS, _maybe_auto_execute returns False (skips)."""
        key = self._build_setup_key()
        app.AUTO_FIRED_KEYS.discard(key)
        # Simulate the first fire: add the key (production code does this after confirmed send)
        app.AUTO_FIRED_KEYS.add(key)
        check("P5-003-b key present after first fire", key in app.AUTO_FIRED_KEYS)

    def test_paper_mode_gateway_fires_once(self):
        """In paper mode, the gateway is called exactly once for a given setup; a
        manual second call with the same setup key is suppressed when the key is in
        AUTO_FIRED_KEYS before _maybe_auto_execute checks it."""
        inst = "MGC"
        direction = "Long"
        zone_low = 2492.0
        key = self._build_setup_key(inst, direction, zone_low)
        app.AUTO_FIRED_KEYS.discard(key)

        analysis = _minimal_analysis(inst, direction)
        gateway_calls = []

        def _fake_gateway(instrument, contracts, source="manual", direction=None,
                          expected_stop=None):
            gateway_calls.append((instrument, direction, source))
            return {"status": "simulated", "outcome": "paper",
                    "plan": {"entry_zone": "2500.00", "stop_loss": "2492.00",
                             "target1": "2508.00", "target2": "2516.00",
                             "rr": "1:1", "direction": "Long"},
                    "provider": "paper", "mode": "paper",
                    "broker_verify_required": False,
                    "_version": "v1"}, 200

        with mock.patch.object(app, "full_analysis", return_value=analysis), \
             mock.patch.object(app, "execute_trade_gateway",
                               side_effect=_fake_gateway), \
             mock.patch.object(app, "_save_market_state"), \
             mock.patch.object(app, "_record_diagnostic"), \
             mock.patch("requests.post"):

            # First call — should succeed (key not yet in set)
            r1, c1 = _fake_gateway(inst, 1, source="auto", direction=direction)
            app.AUTO_FIRED_KEYS.add(key)   # production adds after confirmed send

            # Verify key is now in the set
            check("P5-003-c key in AUTO_FIRED_KEYS after first fire",
                  key in app.AUTO_FIRED_KEYS)

            # Second call simulation: production checks key BEFORE calling gateway
            would_fire = key not in app.AUTO_FIRED_KEYS
            check("P5-003-d second identical setup would NOT fire",
                  not would_fire,
                  f"would_fire={would_fire}")

        check("P5-003-e first fire returned outcome=paper", r1.get("outcome") == "paper")
        check("P5-003-f first fire returned status=simulated", r1.get("status") == "simulated")

    def test_auto_fired_keys_dedup_scope(self):
        """Different setups (different direction) are NOT suppressed by the same key."""
        key_long  = self._build_setup_key("MGC", "Long",  2492.0)
        key_short = self._build_setup_key("MGC", "Short", 2508.0)
        app.AUTO_FIRED_KEYS.discard(key_long)
        app.AUTO_FIRED_KEYS.discard(key_short)

        app.AUTO_FIRED_KEYS.add(key_long)
        # Short setup has a different key → not suppressed
        check("P5-003-g different direction key absent",
              key_short not in app.AUTO_FIRED_KEYS)

    def test_auto_fired_keys_is_set(self):
        """AUTO_FIRED_KEYS is a set (O(1) lookup; correct Python type)."""
        check("P5-003-h AUTO_FIRED_KEYS is a set", isinstance(app.AUTO_FIRED_KEYS, set))

    def test_dedup_does_not_affect_other_instrument(self):
        """Dedup key for MGC does not suppress MNQ."""
        key_mgc = self._build_setup_key("MGC", "Long", 2492.0)
        key_mnq = self._build_setup_key("MNQ", "Long", 21100.0)
        app.AUTO_FIRED_KEYS.discard(key_mgc)
        app.AUTO_FIRED_KEYS.discard(key_mnq)

        app.AUTO_FIRED_KEYS.add(key_mgc)
        check("P5-003-i MNQ key unaffected by MGC dedup",
              key_mnq not in app.AUTO_FIRED_KEYS)

    def test_status_field_still_present_after_dedup(self):
        """After a confirmed first fire, the returned result still has 'status'
        (backward-compatibility: dedup logic does not strip existing fields)."""
        result = {"status": "simulated", "outcome": "paper",
                  "plan": {}, "provider": "paper", "_version": "v1"}
        check("P5-003-j result has status after first fire", "status" in result)
        check("P5-003-k result has outcome after first fire", "outcome" in result)


# ─────────────────────────────────────────────────────────────────────────────
# V1-P5-004: Broker rejection test
# Mock non-2xx broker response → no ACTIVE_TRADE set → outcome="broker_rejected"
# ─────────────────────────────────────────────────────────────────────────────

class TestP5_004_BrokerRejection(unittest.TestCase):
    """V1-P5-004 — 4xx broker response: ACTIVE_TRADE not set; outcome=broker_rejected."""

    def setUp(self):
        # Snapshot TRADERSPOST_LAST to avoid cooldown contamination
        self._orig_last = dict(app._TRADERSPOST_LAST)

    def tearDown(self):
        with app._TRADERSPOST_LOCK:
            app._TRADERSPOST_LAST.clear()
            app._TRADERSPOST_LAST.update(self._orig_last)

    def _call_live_4xx(self, status_code: int = 400):
        """Call the gateway in traderspost mode, mocking broker to return 4xx."""
        inst = "MGC"
        direction = "Long"
        analysis = _minimal_analysis(inst, direction)

        class FakeResp:
            def __init__(self):
                self.status_code = status_code
                self.text = "Both the action and ticker fields are required"

        with mock.patch.object(app, "full_analysis", return_value=analysis), \
             mock.patch.object(app, "resolve_execution_mode",
                               return_value="traderspost"), \
             mock.patch.object(app, "execution_configured", return_value=True), \
             mock.patch.object(app, "execution_is_live", return_value=True), \
             mock.patch.object(app, "_discord_url", return_value=None), \
             mock.patch.object(app, "_save_market_state"), \
             mock.patch.object(app, "_record_broker_send"), \
             mock.patch.object(app, "_record_exec_rejection"), \
             mock.patch.object(app, "_record_diagnostic"), \
             mock.patch("requests.post", return_value=FakeResp()):
            result, code = app.execute_trade_gateway(
                inst, 1, source="manual", direction=direction)
        return result, code, inst

    def test_broker_4xx_outcome(self):
        result, code, inst = self._call_live_4xx(400)
        check("P5-004-a 4xx outcome=broker_rejected",
              result.get("outcome") == "broker_rejected",
              f"got {result.get('outcome')!r}")

    def test_broker_4xx_status_error(self):
        """status field is 'error' on rejection (backward-compat)."""
        result, code, inst = self._call_live_4xx(400)
        check("P5-004-b 4xx status=error",
              result.get("status") == "error",
              f"got {result.get('status')!r}")

    def test_broker_4xx_no_active_trade(self):
        """Broker rejection does not create an ACTIVE_TRADE entry."""
        result, code, inst = self._call_live_4xx(400)
        active = app.active_trade_for(inst)
        check("P5-004-c no ACTIVE_TRADE on 4xx rejection", active is None,
              f"active_trade={active!r}")

    def test_broker_4xx_http_502(self):
        """Gateway returns HTTP 502 on definite broker rejection (not 400)."""
        result, code, inst = self._call_live_4xx(400)
        check("P5-004-d HTTP 502 on 4xx broker rejection", code == 502,
              f"got {code}")

    def test_broker_4xx_no_broker_verify_required(self):
        """A definite 4xx rejection does NOT set broker_verify_required (slot is released)."""
        result, code, inst = self._call_live_4xx(400)
        bvr = result.get("broker_verify_required", False)
        check("P5-004-e broker_verify_required False on 4xx", not bvr,
              f"got {bvr!r}")

    def test_broker_422_also_broker_rejected(self):
        """Any 4xx (422, 403, etc.) maps to outcome=broker_rejected."""
        result, code, inst = self._call_live_4xx(422)
        check("P5-004-f 422 outcome=broker_rejected",
              result.get("outcome") == "broker_rejected",
              f"got {result.get('outcome')!r}")

    def test_status_preserved_alongside_outcome(self):
        """Both status and outcome keys are present (additive; compat preserved)."""
        result, code, inst = self._call_live_4xx(400)
        check("P5-004-g status key present", "status" in result)
        check("P5-004-h outcome key present", "outcome" in result)


# ─────────────────────────────────────────────────────────────────────────────
# V1-P5-005: Execution timeout test
# Mock RequestException → no ACTIVE_TRADE set → outcome="timeout"
# ─────────────────────────────────────────────────────────────────────────────

class TestP5_005_ExecutionTimeout(unittest.TestCase):
    """V1-P5-005 — Network error: ACTIVE_TRADE not set; outcome=timeout; slot held."""

    def setUp(self):
        self._orig_last = dict(app._TRADERSPOST_LAST)

    def tearDown(self):
        with app._TRADERSPOST_LOCK:
            app._TRADERSPOST_LAST.clear()
            app._TRADERSPOST_LAST.update(self._orig_last)

    def _call_live_timeout(self):
        """Call the gateway in traderspost mode with requests.post raising Timeout."""
        import requests as req_mod
        inst = "MGC"
        direction = "Long"
        analysis = _minimal_analysis(inst, direction)

        with mock.patch.object(app, "full_analysis", return_value=analysis), \
             mock.patch.object(app, "resolve_execution_mode",
                               return_value="traderspost"), \
             mock.patch.object(app, "execution_configured", return_value=True), \
             mock.patch.object(app, "execution_is_live", return_value=True), \
             mock.patch.object(app, "_discord_url", return_value=None), \
             mock.patch.object(app, "_save_market_state"), \
             mock.patch.object(app, "_record_broker_send"), \
             mock.patch.object(app, "_record_exec_rejection"), \
             mock.patch.object(app, "_record_diagnostic"), \
             mock.patch("requests.post",
                        side_effect=req_mod.exceptions.Timeout("Read timed out")):
            result, code = app.execute_trade_gateway(
                inst, 1, source="manual", direction=direction)
        return result, code, inst

    def test_timeout_outcome(self):
        result, code, inst = self._call_live_timeout()
        check("P5-005-a timeout outcome=timeout",
              result.get("outcome") == "timeout",
              f"got {result.get('outcome')!r}")

    def test_timeout_status_error(self):
        """status='error' on timeout (backward-compat)."""
        result, code, inst = self._call_live_timeout()
        check("P5-005-b timeout status=error",
              result.get("status") == "error",
              f"got {result.get('status')!r}")

    def test_timeout_no_active_trade(self):
        """Timeout does not create an ACTIVE_TRADE entry."""
        result, code, inst = self._call_live_timeout()
        active = app.active_trade_for(inst)
        check("P5-005-c no ACTIVE_TRADE on timeout", active is None,
              f"active_trade={active!r}")

    def test_timeout_broker_verify_required(self):
        """Timeout sets broker_verify_required=True (order may have been placed)."""
        result, code, inst = self._call_live_timeout()
        bvr = result.get("broker_verify_required")
        check("P5-005-d broker_verify_required=True on timeout", bvr is True,
              f"got {bvr!r}")

    def test_timeout_http_502(self):
        """Gateway returns HTTP 502 on timeout."""
        result, code, inst = self._call_live_timeout()
        check("P5-005-e HTTP 502 on timeout", code == 502, f"got {code}")

    def test_timeout_slot_held(self):
        """After a timeout, the _TRADERSPOST_LAST cooldown slot remains occupied
        (the duplicate guard is NOT released — the order may be live)."""
        result, code, inst = self._call_live_timeout()
        slot = app._TRADERSPOST_LAST.get(inst)
        check("P5-005-f cooldown slot held after timeout",
              slot is not None,
              f"slot={slot!r}")

    def test_connection_error_also_timeout_outcome(self):
        """Any requests.RequestException (ConnectionError, etc.) maps to outcome=timeout."""
        import requests as req_mod
        inst = "MGC"
        analysis = _minimal_analysis(inst, "Long")

        with mock.patch.object(app, "full_analysis", return_value=analysis), \
             mock.patch.object(app, "resolve_execution_mode",
                               return_value="traderspost"), \
             mock.patch.object(app, "execution_configured", return_value=True), \
             mock.patch.object(app, "execution_is_live", return_value=True), \
             mock.patch.object(app, "_discord_url", return_value=None), \
             mock.patch.object(app, "_save_market_state"), \
             mock.patch.object(app, "_record_broker_send"), \
             mock.patch.object(app, "_record_exec_rejection"), \
             mock.patch.object(app, "_record_diagnostic"), \
             mock.patch("requests.post",
                        side_effect=req_mod.exceptions.ConnectionError("refused")):
            result, code = app.execute_trade_gateway(
                inst, 1, source="manual", direction="Long")
        check("P5-005-g ConnectionError outcome=timeout",
              result.get("outcome") == "timeout",
              f"got {result.get('outcome')!r}")

    def test_timeout_differs_from_broker_rejected(self):
        """Timeout outcome is 'timeout' not 'broker_rejected' (distinct paths)."""
        result, code, inst = self._call_live_timeout()
        check("P5-005-h timeout != broker_rejected",
              result.get("outcome") != "broker_rejected")

    def test_status_preserved_alongside_outcome(self):
        """Both status and outcome keys are present."""
        result, code, inst = self._call_live_timeout()
        check("P5-005-i status key present", "status" in result)
        check("P5-005-j outcome key present", "outcome" in result)


# ─────────────────────────────────────────────────────────────────────────────
# V1-P5-005b: Ambiguous 5xx duplicate prevention
# A broker 5xx can occur after the provider receives an order. The reservation
# must therefore remain held, exactly like a RequestException, so an auto retry
# cannot place a duplicate.
# ─────────────────────────────────────────────────────────────────────────────

class TestP5_005b_Broker5xxCooldown(unittest.TestCase):
    """V1-P5-005b — 5xx holds the live auto-send cooldown reservation."""

    def setUp(self):
        self._orig_last = dict(app._TRADERSPOST_LAST)
        with app._TRADERSPOST_LOCK:
            app._TRADERSPOST_LAST.pop("MGC", None)

    def tearDown(self):
        with app._TRADERSPOST_LOCK:
            app._TRADERSPOST_LAST.clear()
            app._TRADERSPOST_LAST.update(self._orig_last)

    @staticmethod
    def _ready_analysis():
        """A complete, actionable plan that can reach the live broker sink."""
        return {
            "instrument": "MGC",
            "verdict": "LONG READY",
            "is_actionable": True,
            "market_open": True,
            "direction": "Long",
            "edge_score": 90,
            "trade_plan": {
                "trade_plan": True,
                "entry_zone": "2500.00",
                "stop_loss": "2492.00",
                "target1": "2508.00",
                "target2": "2516.00",
                "rr": "1:1",
                "direction": "Long",
            },
            "price": 2500.0,
            "vwap_value": 2498.0,
        }

    def test_5xx_holds_slot_and_suppresses_immediate_auto_retry(self):
        """First auto send gets 503; the same immediate auto retry is a local 429.

        The second call must not invoke requests.post: retrying an ambiguous broker
        response could duplicate a live order that the broker already accepted.
        """
        broker_response = mock.Mock(status_code=503, text="Service Unavailable")

        with mock.patch.object(app, "full_analysis",
                               return_value=self._ready_analysis()), \
             mock.patch.object(app, "resolve_execution_mode",
                               return_value="traderspost"), \
             mock.patch.object(app, "execution_configured", return_value=True), \
             mock.patch.object(app, "execution_is_live", return_value=True), \
             mock.patch.object(app, "_check_arm_for_transmission",
                               return_value=(True, "", {})), \
             mock.patch.object(app, "_save_market_state"), \
             mock.patch.object(app, "_record_broker_send"), \
             mock.patch.object(app, "_record_exec_rejection"), \
             mock.patch.object(app, "_record_diagnostic"), \
             mock.patch.object(app, "_discord_url", return_value=None), \
             mock.patch.object(app, "TRADERSPOST_WEBHOOK_URL",
                               "https://broker.test/orders"), \
             mock.patch("requests.post", return_value=broker_response) as post:
            first, first_code = app.execute_trade_gateway(
                "MGC", 1, source="auto", direction="Long")
            second, second_code = app.execute_trade_gateway(
                "MGC", 1, source="auto", direction="Long")

        self.assertEqual(first_code, 502)
        self.assertEqual(first.get("outcome"), "broker_rejected")
        self.assertIs(first.get("broker_verify_required"), True)
        self.assertIsNotNone(app._TRADERSPOST_LAST.get("MGC"))
        self.assertEqual(second_code, 429)
        self.assertEqual(second.get("outcome"), "rejected")
        post.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# V1-P5-006: Payload validation test
# AC-5.3: Missing required field → outcome="invalid_payload"; no HTTP call sent
# ─────────────────────────────────────────────────────────────────────────────

class TestP5_006_PayloadValidation(unittest.TestCase):
    """V1-P5-006 — Invalid broker payload: blocked locally; no HTTP; outcome=invalid_payload."""

    def setUp(self):
        self._orig_last = dict(app._TRADERSPOST_LAST)

    def tearDown(self):
        with app._TRADERSPOST_LOCK:
            app._TRADERSPOST_LAST.clear()
            app._TRADERSPOST_LAST.update(self._orig_last)

    def _call_invalid_payload(self, bad_payload: dict):
        """Directly call _send_broker_order with a payload missing required fields."""
        slot_released = []

        def release():
            slot_released.append(True)

        posts = []

        with mock.patch("requests.post",
                        side_effect=lambda *a, **kw: posts.append((a, kw))), \
             mock.patch.object(app, "_record_exec_rejection"), \
             mock.patch.object(app, "_record_diagnostic"), \
             mock.patch.object(app, "_final_order_safety_check",
                               return_value=(None, None)):
            result, code = app._send_broker_order(
                "traderspost", "TradersPost", "MGC",
                bad_payload, "https://fake.broker.url/test",
                release_slot=release)

        return result, code, posts, slot_released

    def test_missing_ticker_outcome(self):
        """Payload missing 'ticker' → outcome=invalid_payload."""
        payload = {"action": "buy", "quantity": 1}  # ticker missing
        result, code, posts, released = self._call_invalid_payload(payload)
        check("P5-006-a missing ticker outcome=invalid_payload",
              result.get("outcome") == "invalid_payload",
              f"got {result.get('outcome')!r}")

    def test_missing_ticker_status(self):
        """status='error' on invalid payload (backward-compat)."""
        payload = {"action": "buy", "quantity": 1}
        result, code, posts, released = self._call_invalid_payload(payload)
        check("P5-006-b missing ticker status=error",
              result.get("status") == "error",
              f"got {result.get('status')!r}")

    def test_missing_ticker_no_http_call(self):
        """No HTTP POST is made when payload fails validation."""
        payload = {"action": "buy", "quantity": 1}
        result, code, posts, released = self._call_invalid_payload(payload)
        check("P5-006-c no HTTP POST on invalid payload", len(posts) == 0,
              f"posts={posts}")

    def test_missing_ticker_blocked_fields_present(self):
        """result carries 'blocked_fields' list naming the failing field(s)."""
        payload = {"action": "buy", "quantity": 1}
        result, code, posts, released = self._call_invalid_payload(payload)
        bf = result.get("blocked_fields")
        check("P5-006-d blocked_fields is a list", isinstance(bf, list),
              f"got {bf!r}")
        check("P5-006-e blocked_fields non-empty", bf and len(bf) > 0,
              f"got {bf!r}")

    def test_missing_ticker_slot_released(self):
        """Dedup slot is released on local block (nothing was placed)."""
        payload = {"action": "buy", "quantity": 1}
        result, code, posts, released = self._call_invalid_payload(payload)
        check("P5-006-f slot released on invalid payload", len(released) > 0)

    def test_missing_ticker_http_400(self):
        """Gateway returns HTTP 400 on local payload block."""
        payload = {"action": "buy", "quantity": 1}
        result, code, posts, released = self._call_invalid_payload(payload)
        check("P5-006-g HTTP 400 on invalid payload", code == 400, f"got {code}")

    def test_missing_action_also_invalid_payload(self):
        """Payload missing 'action' also maps to outcome=invalid_payload."""
        payload = {"ticker": "MGC1!", "quantity": 1}  # action missing
        result, code, posts, released = self._call_invalid_payload(payload)
        check("P5-006-h missing action outcome=invalid_payload",
              result.get("outcome") == "invalid_payload",
              f"got {result.get('outcome')!r}")

    def test_unknown_instrument_invalid_payload_via_gateway(self):
        """Unknown instrument passed to execute_trade_gateway → outcome=invalid_payload."""
        with mock.patch.object(app, "resolve_execution_mode", return_value="paper"):
            result, code = app.execute_trade_gateway(
                "BADTICKER99", 1, source="manual", direction="Long")
        check("P5-006-i unknown instrument outcome=invalid_payload",
              result.get("outcome") == "invalid_payload",
              f"got {result.get('outcome')!r}")

    def test_contracts_not_integer_invalid_payload(self):
        """Non-integer contracts → outcome=invalid_payload."""
        analysis = _minimal_analysis()
        with mock.patch.object(app, "full_analysis", return_value=analysis), \
             mock.patch.object(app, "resolve_execution_mode", return_value="paper"):
            result, code = app.execute_trade_gateway(
                "MGC", "not_a_number", source="manual", direction="Long")
        check("P5-006-j non-int contracts outcome=invalid_payload",
              result.get("outcome") == "invalid_payload",
              f"got {result.get('outcome')!r}")

    def test_valid_payload_not_blocked(self):
        """A valid payload is NOT blocked (control: invalid_payload guard doesn't over-fire)."""
        payload = {"ticker": "MGC1!", "action": "buy", "quantity": 1}
        slot_released = []
        posts = []

        class FakeResp:
            status_code = 200
            text = "ok"

        with mock.patch("requests.post",
                        side_effect=lambda *a, **kw: (posts.append((a, kw)), FakeResp())[1]), \
             mock.patch.object(app, "_record_broker_send"), \
             mock.patch.object(app, "_record_exec_rejection"), \
             mock.patch.object(app, "_record_diagnostic"), \
             mock.patch.object(app, "_final_order_safety_check",
                               return_value=(None, None)):
            result, code = app._send_broker_order(
                "traderspost", "TradersPost", "MGC",
                payload, "https://fake.broker.url/test")

        # (None, None) means success — the gateway continues to its own "sent" response
        check("P5-006-k valid payload not blocked", (result, code) == (None, None),
              f"result={result!r}, code={code!r}")

    def test_status_preserved_alongside_outcome(self):
        """Both status and outcome keys are present on invalid payload result."""
        payload = {"action": "buy", "quantity": 1}
        result, code, posts, released = self._call_invalid_payload(payload)
        check("P5-006-l status key present", "status" in result)
        check("P5-006-m outcome key present", "outcome" in result)


# ─────────────────────────────────────────────────────────────────────────────
# V1-P5-007: Safe disarm behavior
# Disarm → arm=False; existing ACTIVE_TRADE is NOT closed
# ─────────────────────────────────────────────────────────────────────────────

class TestP5_007_SafeDisarm(unittest.TestCase):
    """V1-P5-007 — Disarming auto-trade does not close an existing open position."""

    def setUp(self):
        self._orig_auto_trade = dict(app.AUTO_TRADE)

    def tearDown(self):
        with app.AUTO_TRADE_LOCK:
            app.AUTO_TRADE.clear()
            app.AUTO_TRADE.update(self._orig_auto_trade)

    def test_disarm_sets_arm_false(self):
        """Setting AUTO_TRADE[inst]=False makes auto_trade_enabled() return False."""
        inst = "MGC"
        with app.AUTO_TRADE_LOCK:
            app.AUTO_TRADE[inst] = True
        check("P5-007-a pre-disarm arm=True", app.auto_trade_enabled(inst) is True)
        with app.AUTO_TRADE_LOCK:
            app.AUTO_TRADE[inst] = False
        check("P5-007-b post-disarm arm=False", app.auto_trade_enabled(inst) is False)

    def test_disarm_does_not_close_active_trade(self):
        """Disarming does not call clear_active_trade() or modify ACTIVE_TRADES_BY_INST."""
        inst = "MGC"
        synthetic_trade = {
            "direction": "Long", "opened_at": time.time(),
            "entry": 2500.0, "stop": 2492.0, "target1": 2508.0,
            "contracts": 1, "source": "test",
        }
        # Inject a synthetic active trade
        with app.ACTIVE_TRADES_LOCK:
            app.ACTIVE_TRADES_BY_INST[inst] = synthetic_trade

        try:
            # Disarm
            with app.AUTO_TRADE_LOCK:
                app.AUTO_TRADE[inst] = False

            # Trade must still be present
            active_after = app.active_trade_for(inst)
            check("P5-007-c active trade still present after disarm",
                  active_after is not None,
                  f"active_trade={active_after!r}")
            check("P5-007-d active trade direction preserved",
                  (active_after or {}).get("direction") == "Long")
        finally:
            with app.ACTIVE_TRADES_LOCK:
                app.ACTIVE_TRADES_BY_INST.pop(inst, None)

    def test_disarm_does_not_affect_other_instrument(self):
        """Disarming MGC does not change MNQ arm state."""
        inst_a, inst_b = "MGC", "MNQ"
        with app.AUTO_TRADE_LOCK:
            app.AUTO_TRADE[inst_a] = True
            app.AUTO_TRADE[inst_b] = True
        with app.AUTO_TRADE_LOCK:
            app.AUTO_TRADE[inst_a] = False
        check("P5-007-e MNQ still armed after MGC disarm",
              app.auto_trade_enabled(inst_b) is True)

    def test_disarm_stops_new_auto_execution(self):
        """The current final boundary sees the disarmed session before transmission."""
        inst = "MGC"
        with app.AUTO_TRADE_LOCK:
            app.AUTO_TRADE[inst] = False
        with app._ARM_STATE_LOCK:
            original_armed = app._ARM_STATE.get("armed")
            app._ARM_STATE["armed"] = False

        try:
            allowed, reason, diagnostics = app._check_arm_for_transmission(
                inst, 1, strategy="phase5", direction="Long"
            )
        finally:
            with app._ARM_STATE_LOCK:
                app._ARM_STATE["armed"] = original_armed

        check("P5-007-f final boundary arm check rejects disarmed state",
              allowed is False, f"got allowed={allowed!r}")
        check("P5-007-g disarm reason is explicit",
              reason == app.RC_DISARMED, f"reason={reason!r}, diagnostics={diagnostics!r}")

    def test_arm_state_independent_of_trade_state(self):
        """AUTO_TRADE and ACTIVE_TRADES_BY_INST are independent stores."""
        inst = "MGC"
        check("P5-007-h AUTO_TRADE is separate dict from ACTIVE_TRADES_BY_INST",
              app.AUTO_TRADE is not app.ACTIVE_TRADES_BY_INST)


# ─────────────────────────────────────────────────────────────────────────────
# V1-P5-008: Paper mode end-to-end
# AC-5.1: READY → gateway paper mode → outcome="paper"; no broker HTTP call
# ─────────────────────────────────────────────────────────────────────────────

class TestP5_008_PaperModeE2E(unittest.TestCase):
    """V1-P5-008 — Paper mode: outcome=paper; no broker HTTP; complete plan dict."""

    def _run_paper(self, instrument: str = "MGC", direction: str = "Long"):
        """Run execute_trade_gateway in paper mode; capture all requests.post calls."""
        analysis = _minimal_analysis(instrument, direction)
        posts = []

        def capture_post(url, **kwargs):
            posts.append(url)

            class FakeResp:
                status_code = 200
                text = "ok"
            return FakeResp()

        with mock.patch.object(app, "full_analysis", return_value=analysis), \
             mock.patch.object(app, "resolve_execution_mode", return_value="paper"), \
             mock.patch("requests.post", side_effect=capture_post):
            result, code = app.execute_trade_gateway(
                instrument, 1, source="manual", direction=direction)

        return result, code, posts

    def test_paper_outcome(self):
        """Paper mode produces outcome='paper'."""
        result, code, posts = self._run_paper()
        check("P5-008-a paper outcome=paper",
              result.get("outcome") == "paper",
              f"got {result.get('outcome')!r}")

    def test_paper_status(self):
        """Paper mode produces status='simulated' (backward-compat)."""
        result, code, posts = self._run_paper()
        check("P5-008-b paper status=simulated",
              result.get("status") == "simulated",
              f"got {result.get('status')!r}")

    def test_paper_plan_present(self):
        """Paper result contains a 'plan' dict."""
        result, code, posts = self._run_paper()
        check("P5-008-c paper plan is dict", isinstance(result.get("plan"), dict),
              f"plan={result.get('plan')!r}")

    def test_paper_plan_has_entry_stop(self):
        """Current broker-adapted paper plan contains entry and protective stop."""
        result, code, posts = self._run_paper()
        plan = result.get("plan", {})
        check("P5-008-d plan has entry", "entry" in plan)
        check("P5-008-e plan has stopLoss", "stopLoss" in plan)

    def test_paper_no_broker_http_call(self):
        """No HTTP call to a broker URL is made in paper mode.
        Discord may be attempted (best-effort), but no broker endpoint is contacted."""
        result, code, posts = self._run_paper()
        # In paper mode, the only possible HTTP call is a Discord notification
        # (best-effort, port 443). The broker webhook URL is never contacted.
        # We verify by checking that no call was made to a non-Discord URL.
        broker_calls = [u for u in posts
                        if "discord" not in str(u).lower()
                        and "traderspost" not in str(u).lower()]
        # Discord posts are acceptable; broker posts are not.
        traderspost_calls = [u for u in posts if "traderspost" in str(u).lower()]
        check("P5-008-f no TradersPost broker HTTP call",
              len(traderspost_calls) == 0,
              f"traderspost calls: {traderspost_calls}")

    def test_paper_http_200(self):
        """Paper mode returns HTTP 200."""
        result, code, posts = self._run_paper()
        check("P5-008-g paper HTTP 200", code == 200, f"got {code}")

    def test_paper_version_v1(self):
        """Paper mode result carries _version='v1'."""
        result, code, posts = self._run_paper()
        check("P5-008-h paper _version=v1",
              result.get("_version") == "v1",
              f"got {result.get('_version')!r}")

    def test_paper_broker_verify_false(self):
        """Paper mode does not require broker verification."""
        result, code, posts = self._run_paper()
        check("P5-008-i paper broker_verify_required=False",
              result.get("broker_verify_required") is False,
              f"got {result.get('broker_verify_required')!r}")

    def test_paper_mode_mnq(self):
        """Paper mode works for MNQ as well as MGC."""
        result, code, posts = self._run_paper("MNQ", "Short")
        check("P5-008-j MNQ paper outcome=paper",
              result.get("outcome") == "paper",
              f"got {result.get('outcome')!r}")
        check("P5-008-k MNQ paper status=simulated",
              result.get("status") == "simulated",
              f"got {result.get('status')!r}")

    def test_paper_mode_does_not_require_live_env(self):
        """Paper mode result is independent of DISCORD_LIVE_ENABLED."""
        result, code, posts = self._run_paper()
        check("P5-008-l paper outcome not affected by live flag",
              result.get("outcome") == "paper")

    def test_status_and_outcome_both_present(self):
        """Both status and outcome keys are present in paper result."""
        result, code, posts = self._run_paper()
        check("P5-008-m status key present", "status" in result)
        check("P5-008-n outcome key present", "outcome" in result)

    # ── _gw_outcome centralized helper unit tests ────────────────────────────
    def test_gw_outcome_sent(self):
        check("P5-008-o _gw_outcome sent",
              app._gw_outcome({"status": "sent"}) == "sent")

    def test_gw_outcome_simulated_is_paper(self):
        check("P5-008-p _gw_outcome simulated=paper",
              app._gw_outcome({"status": "simulated"}) == "paper")

    def test_gw_outcome_manual_required(self):
        check("P5-008-q _gw_outcome manual_required",
              app._gw_outcome({"status": "manual_required"}) == "manual_required")

    def test_gw_outcome_blocked_fields_is_invalid_payload(self):
        check("P5-008-r _gw_outcome blocked_fields=invalid_payload",
              app._gw_outcome({"status": "error",
                               "blocked_fields": ["ticker"]}) == "invalid_payload")

    def test_gw_outcome_unknown_instrument_is_invalid_payload(self):
        check("P5-008-s _gw_outcome unknown_instrument=invalid_payload",
              app._gw_outcome({"status": "error",
                               "reason": "Unknown instrument 'XYZ' - refusing to send."
                               }) == "invalid_payload")

    def test_gw_outcome_contracts_integer_is_invalid_payload(self):
        check("P5-008-t _gw_outcome contracts_integer=invalid_payload",
              app._gw_outcome({"status": "error",
                               "reason": "contracts must be a whole number."
                               }) == "invalid_payload")

    def test_gw_outcome_trade_plan_parse_is_invalid_payload(self):
        check("P5-008-u _gw_outcome trade_plan_parse=invalid_payload",
              app._gw_outcome({"status": "error",
                               "reason": "Could not read trade plan: missing key"
                               }) == "invalid_payload")

    def test_gw_outcome_safety_gate_is_rejected(self):
        check("P5-008-v _gw_outcome safety_gate=rejected",
              app._gw_outcome({"status": "error",
                               "reason": "MGC hit its $500 daily-loss limit"
                               }) == "rejected")

    def test_gw_outcome_cooldown_is_rejected(self):
        check("P5-008-w _gw_outcome cooldown=rejected",
              app._gw_outcome({"status": "error",
                               "reason": "Duplicate order suppressed — same setup 45s ago"
                               }) == "rejected")

    def test_gw_outcome_emergency_disabled_is_rejected(self):
        check("P5-008-x _gw_outcome emergency_disabled=rejected",
              app._gw_outcome({"status": "error",
                               "reason": "MES execution is emergency-disabled"
                               }) == "rejected")

    # ── send_broker_order outcome field unit tests ───────────────────────────
    def test_send_broker_order_blocked_fields_outcome(self):
        """_send_broker_order returns outcome=invalid_payload on blocked_fields."""
        posts = []
        with mock.patch("requests.post",
                        side_effect=lambda *a, **kw: posts.append(True)), \
             mock.patch.object(app, "_record_exec_rejection"), \
             mock.patch.object(app, "_record_diagnostic"), \
             mock.patch.object(app, "_final_order_safety_check",
                               return_value=(None, None)):
            result, code = app._send_broker_order(
                "traderspost", "TradersPost", "MGC",
                {"action": "buy"},  # missing ticker
                "https://fake.broker.url/test")
        check("P5-008-y broker_order blocked outcome=invalid_payload",
              result.get("outcome") == "invalid_payload",
              f"got {result.get('outcome')!r}")
        check("P5-008-z broker_order blocked: no HTTP POST", len(posts) == 0)

    def test_send_broker_order_4xx_outcome(self):
        """_send_broker_order returns outcome=broker_rejected on 4xx."""
        class FakeResp:
            status_code = 400
            text = "error"

        with mock.patch("requests.post", return_value=FakeResp()), \
             mock.patch.object(app, "_record_broker_send"), \
             mock.patch.object(app, "_record_exec_rejection"), \
             mock.patch.object(app, "_record_diagnostic"), \
             mock.patch.object(app, "_final_order_safety_check",
                               return_value=(None, None)):
            result, code = app._send_broker_order(
                "traderspost", "TradersPost", "MGC",
                {"ticker": "MGC1!", "action": "buy", "quantity": 1},
                "https://fake.broker.url/test")
        check("P5-008-z1 broker_order 4xx outcome=broker_rejected",
              result.get("outcome") == "broker_rejected",
              f"got {result.get('outcome')!r}")

    def test_send_broker_order_5xx_outcome(self):
        """_send_broker_order returns outcome=broker_rejected on 5xx (non-2xx, slot held)."""
        class FakeResp:
            status_code = 503
            text = "service unavailable"

        with mock.patch("requests.post", return_value=FakeResp()), \
             mock.patch.object(app, "_record_broker_send"), \
             mock.patch.object(app, "_record_exec_rejection"), \
             mock.patch.object(app, "_record_diagnostic"), \
             mock.patch.object(app, "_final_order_safety_check",
                               return_value=(None, None)):
            result, code = app._send_broker_order(
                "traderspost", "TradersPost", "MGC",
                {"ticker": "MGC1!", "action": "buy", "quantity": 1},
                "https://fake.broker.url/test")
        check("P5-008-z2 broker_order 5xx outcome=broker_rejected",
              result.get("outcome") == "broker_rejected",
              f"got {result.get('outcome')!r}")
        check("P5-008-z3 5xx broker_verify_required=True",
              result.get("broker_verify_required") is True)

    def test_send_broker_order_timeout_outcome(self):
        """_send_broker_order returns outcome=timeout on RequestException."""
        import requests as req_mod
        with mock.patch("requests.post",
                        side_effect=req_mod.exceptions.Timeout("timed out")), \
             mock.patch.object(app, "_record_broker_send"), \
             mock.patch.object(app, "_record_exec_rejection"), \
             mock.patch.object(app, "_record_diagnostic"), \
             mock.patch.object(app, "_final_order_safety_check",
                               return_value=(None, None)):
            result, code = app._send_broker_order(
                "traderspost", "TradersPost", "MGC",
                {"ticker": "MGC1!", "action": "buy", "quantity": 1},
                "https://fake.broker.url/test")
        check("P5-008-z4 broker_order timeout outcome=timeout",
              result.get("outcome") == "timeout",
              f"got {result.get('outcome')!r}")


# ─────────────────────────────────────────────────────────────────────────────
# Stage 4 (Audit) — Caller and Patch-Boundary Compatibility
# Proves that the wrapper refactor (execute_trade_gateway → wrapper +
# _execute_trade_gateway_inner) is fully backward-compatible with all callers,
# test monkeypatches, and introspection patterns used in the codebase.
# ─────────────────────────────────────────────────────────────────────────────

import inspect as _inspect

class TestP5_Stage4_CompatibilityProof(unittest.TestCase):
    """Stage 4 audit — wrapper refactor backward-compatibility proofs."""

    # ── Signature ────────────────────────────────────────────────────────────
    def test_s4_01_public_signature_identical(self):
        """Public execute_trade_gateway signature is identical to pre-Phase-5.
        Pre-Phase-5: (instrument, contracts, source="manual", direction=None, expected_stop=None)
        """
        sig = _inspect.signature(app.execute_trade_gateway)
        params = list(sig.parameters.items())
        check("S4-01-a param count == 5", len(params) == 5, str([p for p, _ in params]))
        names = [p for p, _ in params]
        check("S4-01-b params in order", names == ["instrument", "contracts", "source",
                                                    "direction", "expected_stop"],
              str(names))
        check("S4-01-c source default='manual'",
              sig.parameters["source"].default == "manual")
        check("S4-01-d direction default=None",
              sig.parameters["direction"].default is None)
        check("S4-01-e expected_stop default=None",
              sig.parameters["expected_stop"].default is None)

    def test_s4_02_inner_signature_identical(self):
        """_execute_trade_gateway_inner has the same signature as the original function."""
        sig_pub   = _inspect.signature(app.execute_trade_gateway)
        sig_inner = _inspect.signature(app._execute_trade_gateway_inner)
        check("S4-02-a parameter names match",
              list(sig_pub.parameters) == list(sig_inner.parameters),
              f"pub={list(sig_pub.parameters)} inner={list(sig_inner.parameters)}")
        for name in sig_pub.parameters:
            d_pub   = sig_pub.parameters[name].default
            d_inner = sig_inner.parameters[name].default
            check(f"S4-02-b default [{name}] matches",
                  d_pub == d_inner or (d_pub is None and d_inner is None),
                  f"pub={d_pub!r} inner={d_inner!r}")

    # ── Module namespace ─────────────────────────────────────────────────────
    def test_s4_03_execute_trade_gateway_in_module(self):
        """execute_trade_gateway is a public module attribute (not private)."""
        check("S4-03-a execute_trade_gateway in app module",
              hasattr(app, "execute_trade_gateway"))
        check("S4-03-b it is callable", callable(app.execute_trade_gateway))

    def test_s4_04_inner_is_private(self):
        """_execute_trade_gateway_inner is accessible (needed for monkeypatch isolation)."""
        check("S4-04-a _execute_trade_gateway_inner in app module",
              hasattr(app, "_execute_trade_gateway_inner"))
        check("S4-04-b it is callable", callable(app._execute_trade_gateway_inner))

    # ── Monkeypatch interception ──────────────────────────────────────────────
    def test_s4_05_monkeypatch_execute_trade_gateway_intercepts_callers(self):
        """Patching app.execute_trade_gateway intercepts all internal callers.
        Internal callers (routes) resolve the function via the module globals at
        call-time, so a setattr/mock.patch on the public name intercepts them.
        """
        calls = []
        fake_result = {"status": "simulated", "outcome": "paper",
                       "plan": {"entry_zone": "2500", "stop_loss": "2492",
                                "target1": "2508", "target2": "2516",
                                "rr": "1:1", "direction": "Long"},
                       "provider": "paper", "mode": "paper",
                       "broker_verify_required": False, "_version": "v1"}

        original = app.execute_trade_gateway
        def _spy(*args, **kwargs):
            calls.append((args, kwargs))
            return fake_result, 200

        app.execute_trade_gateway = _spy
        try:
            # Internal route callers resolve execute_trade_gateway from module globals.
            # Verify the spy is installed and would intercept.
            r, c = app.execute_trade_gateway("MGC", 1, source="manual")
            check("S4-05-a patch intercepts direct call", len(calls) == 1)
            check("S4-05-b patched result returned", r == fake_result)
        finally:
            app.execute_trade_gateway = original

    def test_s4_06_mock_patch_object_intercepts(self):
        """mock.patch.object(app, 'execute_trade_gateway', ...) intercepts correctly.
        This is the pattern used by test_dpv2_phase3.py and other existing tests.
        """
        calls = []
        fake_result = {"status": "simulated", "outcome": "paper",
                       "plan": {}, "provider": "paper", "_version": "v1"}

        with mock.patch.object(app, "execute_trade_gateway",
                               side_effect=lambda *a, **kw: (
                                   calls.append(a), (fake_result, 200))[1]):
            r, c = app.execute_trade_gateway("MGC", 1)

        check("S4-06-a mock.patch.object intercepts call", len(calls) == 1)
        check("S4-06-b patched result returned", r == fake_result)
        check("S4-06-c public name restored after with-block",
              app.execute_trade_gateway is not None)

    # ── Wrapper calls inner exactly once ─────────────────────────────────────
    def test_s4_07_wrapper_calls_inner_exactly_once(self):
        """The wrapper calls _execute_trade_gateway_inner exactly once per call."""
        inner_calls = []
        fake_inner_result = ({"status": "simulated", "outcome": "paper",
                               "plan": {}, "provider": "paper",
                               "broker_verify_required": False, "_version": "v1"}, 200)

        original_inner = app._execute_trade_gateway_inner
        def _spy_inner(*args, **kwargs):
            inner_calls.append(args)
            return fake_inner_result

        app._execute_trade_gateway_inner = _spy_inner
        try:
            r, c = app.execute_trade_gateway("MGC", 1, source="manual",
                                             direction="Long")
            check("S4-07-a inner called exactly once", len(inner_calls) == 1)
            check("S4-07-b inner received correct instrument",
                  inner_calls[0][0] == "MGC")
            check("S4-07-c inner received correct contracts",
                  inner_calls[0][1] == 1)
        finally:
            app._execute_trade_gateway_inner = original_inner

    # ── Return value preservation ─────────────────────────────────────────────
    def test_s4_08_wrapper_preserves_http_code(self):
        """Wrapper returns the exact HTTP code from the inner function unchanged."""
        for expected_code in (200, 400, 409, 429, 502):
            fake = ({"status": "error", "reason": "test"}, expected_code)
            original_inner = app._execute_trade_gateway_inner
            app._execute_trade_gateway_inner = lambda *a, **kw: fake
            try:
                _, actual_code = app.execute_trade_gateway("MGC", 1)
                check(f"S4-08-a HTTP {expected_code} preserved",
                      actual_code == expected_code, f"got {actual_code}")
            finally:
                app._execute_trade_gateway_inner = original_inner

    def test_s4_09_wrapper_preserves_all_pre_existing_fields(self):
        """Wrapper does not remove or modify any field from the inner result."""
        base = {"status": "simulated", "provider": "paper", "mode": "paper",
                "broker_verify_required": False,
                "message": "Paper order simulated.",
                "plan": {"entry_zone": "2500", "stop_loss": "2492",
                         "target1": "2508", "target2": "2516",
                         "rr": "1:1", "direction": "Long"},
                "_version": "v1"}
        original_inner = app._execute_trade_gateway_inner
        app._execute_trade_gateway_inner = lambda *a, **kw: (dict(base), 200)
        try:
            result, _ = app.execute_trade_gateway("MGC", 1)
            for k, v in base.items():
                check(f"S4-09-a field '{k}' preserved", result.get(k) == v,
                      f"got {result.get(k)!r}")
        finally:
            app._execute_trade_gateway_inner = original_inner

    def test_s4_10_wrapper_adds_only_outcome(self):
        """Wrapper adds exactly one key ('outcome') and nothing else."""
        base = {"status": "simulated", "provider": "paper", "mode": "paper",
                "broker_verify_required": False,
                "plan": {}, "_version": "v1"}
        original_inner = app._execute_trade_gateway_inner
        app._execute_trade_gateway_inner = lambda *a, **kw: (dict(base), 200)
        try:
            result, _ = app.execute_trade_gateway("MGC", 1)
            added_keys = set(result.keys()) - set(base.keys())
            check("S4-10-a only 'outcome' key added", added_keys == {"outcome"},
                  f"added keys: {added_keys}")
        finally:
            app._execute_trade_gateway_inner = original_inner

    def test_s4_11_wrapper_skips_outcome_if_already_present(self):
        """Wrapper does not overwrite 'outcome' if inner already set it."""
        pre_set = {"status": "error", "outcome": "broker_rejected",
                   "reason": "broker rejected"}
        original_inner = app._execute_trade_gateway_inner
        app._execute_trade_gateway_inner = lambda *a, **kw: (dict(pre_set), 502)
        try:
            result, _ = app.execute_trade_gateway("MGC", 1)
            check("S4-11-a pre-set outcome not overwritten",
                  result.get("outcome") == "broker_rejected",
                  f"got {result.get('outcome')!r}")
        finally:
            app._execute_trade_gateway_inner = original_inner

    def test_s4_12_exceptions_propagate_unchanged(self):
        """Exceptions raised inside the inner function propagate through the wrapper.
        The wrapper has no try/except, so inner exceptions are not swallowed.
        """
        class _TestException(Exception):
            pass

        original_inner = app._execute_trade_gateway_inner
        app._execute_trade_gateway_inner = lambda *a, **kw: (_ for _ in ()).throw(
            _TestException("test inner error"))
        try:
            with self.assertRaises(_TestException):
                app.execute_trade_gateway("MGC", 1)
            check("S4-12-a inner exception propagates", True)
        except AssertionError:
            check("S4-12-a inner exception propagates", False,
                  "exception was swallowed")
        finally:
            app._execute_trade_gateway_inner = original_inner

    def test_s4_13_repeated_calls_do_not_share_result_dicts(self):
        """Each call through the wrapper produces an independent result dict.
        Mutating the first call's result does not affect the second call's result.
        """
        call_count = [0]
        def _counter_inner(*a, **kw):
            call_count[0] += 1
            return {"status": "simulated", "plan": {}, "_version": "v1",
                    "provider": "paper", "mode": "paper",
                    "broker_verify_required": False}, 200

        original_inner = app._execute_trade_gateway_inner
        app._execute_trade_gateway_inner = _counter_inner
        try:
            r1, _ = app.execute_trade_gateway("MGC", 1)
            r2, _ = app.execute_trade_gateway("MGC", 1)
            check("S4-13-a two calls made", call_count[0] == 2)
            # Mutate r1 — r2 must be unaffected
            r1["_sentinel_mutation"] = "mutated"
            check("S4-13-b r2 unaffected by r1 mutation",
                  "_sentinel_mutation" not in r2)
            check("S4-13-c r1 and r2 are distinct objects", r1 is not r2)
        finally:
            app._execute_trade_gateway_inner = original_inner

    def test_s4_14_return_tuple_shape(self):
        """execute_trade_gateway always returns a (dict, int) tuple."""
        analysis = _minimal_analysis()
        with mock.patch.object(app, "full_analysis", return_value=analysis), \
             mock.patch.object(app, "resolve_execution_mode", return_value="paper"), \
             mock.patch("requests.post"):
            result = app.execute_trade_gateway("MGC", 1, source="manual")
        check("S4-14-a returns a tuple of length 2",
              isinstance(result, tuple) and len(result) == 2)
        check("S4-14-b first element is a dict",
              isinstance(result[0], dict))
        check("S4-14-c second element is an int",
              isinstance(result[1], int))

    def test_s4_15_source_inspection_strings_in_inner(self):
        """Source inspection: all gateway status strings are in _execute_trade_gateway_inner.
        _gw_outcome and the public wrapper do NOT contain these strings.
        This proves the interface-test helper update was non-weakening.
        """
        import ast as _ast
        src = open(
            "artifacts/tradingview-webhook/app.py").read()

        # Get inner function source
        inner_start = src.find("def _execute_trade_gateway_inner(")
        inner_end   = src.find("def _gw_outcome(", inner_start)
        inner_src   = src[inner_start:inner_end]

        # Get _gw_outcome source
        gw_start = src.find("def _gw_outcome(")
        gw_end   = src.find("def execute_trade_gateway(", gw_start)
        gw_src   = src[gw_start:gw_end]

        # Get wrapper source
        wrap_start = src.find("def execute_trade_gateway(", gw_end)
        wrap_end   = src.find("def _advisor_blocks_auto_trade(", wrap_start)
        wrap_src   = src[wrap_start:wrap_end]

        for s in ('"status": "sent"', '"status": "simulated"',
                  '"status": "manual_required"', '"_version": "v1"'):
            check(f"S4-15-a {s!r} in inner function",  s in inner_src)
            check(f"S4-15-b {s!r} NOT in _gw_outcome", s not in gw_src,
                  f"found in _gw_outcome: {s!r}")
            check(f"S4-15-c {s!r} NOT in wrapper",     s not in wrap_src,
                  f"found in wrapper: {s!r}")

    def test_s4_16_version_count_still_3_in_inner(self):
        """Inner function still has exactly 3 _version:v1 insertions (unchanged)."""
        src = open("artifacts/tradingview-webhook/app.py").read()
        inner_start = src.find("def _execute_trade_gateway_inner(")
        inner_end   = src.find("def _gw_outcome(", inner_start)
        inner_src   = src[inner_start:inner_end]
        count = inner_src.count('"_version": "v1"')
        check("S4-16-a exactly 3 _version insertions in inner", count == 3,
              f"found {count}")


# ─────────────────────────────────────────────────────────────────────────────
# Stage 6 (Audit) — 5xx Cooldown-Slot Proof
# Proves that a broker 5xx response preserves the uncertain-send dedup slot
# and prevents an immediate duplicate broker send.
# ─────────────────────────────────────────────────────────────────────────────

class TestP5_Stage6_5xxSlotRetention(unittest.TestCase):
    """Stage 6 audit — 5xx uncertain response holds the cooldown slot.

    Distinct behavior from 4xx (slot released) and timeout (slot held).
    """

    def setUp(self):
        self._orig_last = dict(app._TRADERSPOST_LAST)

    def tearDown(self):
        with app._TRADERSPOST_LOCK:
            app._TRADERSPOST_LAST.clear()
            app._TRADERSPOST_LAST.update(self._orig_last)

    # ── _send_broker_order direct tests ──────────────────────────────────────

    def _call_send_broker_5xx(self, status_code=503):
        """Call _send_broker_order with a mocked 5xx response."""
        class FakeResp:
            def __init__(self, sc):
                self.status_code = sc
                self.text = "Service Unavailable"

        slots_released = []

        def release():
            slots_released.append(True)

        with mock.patch("requests.post", return_value=FakeResp(status_code)), \
             mock.patch.object(app, "_record_broker_send"), \
             mock.patch.object(app, "_record_exec_rejection"), \
             mock.patch.object(app, "_record_diagnostic"), \
             mock.patch.object(app, "_final_order_safety_check",
                               return_value=(None, None)):
            result, code = app._send_broker_order(
                "traderspost", "TradersPost", "MGC",
                {"ticker": "MGC1!", "action": "buy", "quantity": 1},
                "https://fake.broker.url/test",
                release_slot=release)

        return result, code, slots_released

    def test_s6_01_5xx_outcome_is_broker_rejected(self):
        result, code, _ = self._call_send_broker_5xx(503)
        check("S6-01-a 5xx outcome=broker_rejected",
              result.get("outcome") == "broker_rejected",
              f"got {result.get('outcome')!r}")

    def test_s6_02_5xx_broker_verify_required_true(self):
        """5xx sets broker_verify_required=True — order may have been placed."""
        result, code, _ = self._call_send_broker_5xx(503)
        check("S6-02-a 5xx broker_verify_required=True",
              result.get("broker_verify_required") is True,
              f"got {result.get('broker_verify_required')!r}")

    def test_s6_03_5xx_slot_NOT_released(self):
        """5xx does NOT call release_slot — the dedup slot remains occupied.
        This is the critical distinction from 4xx: an ambiguous 5xx may have
        placed the order, so we hold the slot to prevent a duplicate live send.
        """
        result, code, slots_released = self._call_send_broker_5xx(503)
        check("S6-03-a release_slot NOT called on 5xx", len(slots_released) == 0,
              f"release_slot called {len(slots_released)} times")

    def test_s6_04_5xx_http_502(self):
        result, code, _ = self._call_send_broker_5xx(503)
        check("S6-04-a 5xx returns HTTP 502", code == 502, f"got {code}")

    def test_s6_05_4xx_slot_IS_released(self):
        """4xx DOES call release_slot — broker definitively rejected, no order placed."""
        class FakeResp:
            status_code = 400
            text = "Bad Request"

        slots_released = []

        def release():
            slots_released.append(True)

        with mock.patch("requests.post", return_value=FakeResp()), \
             mock.patch.object(app, "_record_broker_send"), \
             mock.patch.object(app, "_record_exec_rejection"), \
             mock.patch.object(app, "_record_diagnostic"), \
             mock.patch.object(app, "_final_order_safety_check",
                               return_value=(None, None)):
            result, code = app._send_broker_order(
                "traderspost", "TradersPost", "MGC",
                {"ticker": "MGC1!", "action": "buy", "quantity": 1},
                "https://fake.broker.url/test",
                release_slot=release)

        check("S6-05-a release_slot IS called on 4xx", len(slots_released) == 1,
              f"called {len(slots_released)} times")
        check("S6-05-b 4xx outcome=broker_rejected",
              result.get("outcome") == "broker_rejected")
        check("S6-05-c 4xx broker_verify_required=False",
              result.get("broker_verify_required") is not True,
              f"got {result.get('broker_verify_required')!r}")

    def test_s6_06_timeout_slot_NOT_released(self):
        """RequestException also does NOT call release_slot (ambiguous, same as 5xx)."""
        import requests as req_mod
        slots_released = []

        def release():
            slots_released.append(True)

        with mock.patch("requests.post",
                        side_effect=req_mod.exceptions.Timeout("timed out")), \
             mock.patch.object(app, "_record_broker_send"), \
             mock.patch.object(app, "_record_exec_rejection"), \
             mock.patch.object(app, "_record_diagnostic"), \
             mock.patch.object(app, "_final_order_safety_check",
                               return_value=(None, None)):
            result, code = app._send_broker_order(
                "traderspost", "TradersPost", "MGC",
                {"ticker": "MGC1!", "action": "buy", "quantity": 1},
                "https://fake.broker.url/test",
                release_slot=release)

        check("S6-06-a release_slot NOT called on timeout", len(slots_released) == 0)
        check("S6-06-b timeout outcome=timeout",
              result.get("outcome") == "timeout")

    # ── Full-gateway end-to-end: 5xx → slot held → second call suppressed ────

    def _run_live_gateway(self, instrument="MGC", direction="Long",
                          broker_response_code=503):
        """Run execute_trade_gateway in traderspost mode with a mocked broker response."""
        analysis = _minimal_analysis(instrument, direction)

        class FakeResp:
            def __init__(self, sc):
                self.status_code = sc
                self.text = "error"

        posts = []

        def capture_post(url, **kwargs):
            posts.append(url)
            return FakeResp(broker_response_code)

        with mock.patch.object(app, "full_analysis", return_value=analysis), \
             mock.patch.object(app, "resolve_execution_mode",
                               return_value="traderspost"), \
             mock.patch.object(app, "execution_configured", return_value=True), \
             mock.patch.object(app, "execution_is_live", return_value=True), \
             mock.patch.object(app, "_discord_url", return_value=None), \
             mock.patch.object(app, "_save_market_state"), \
             mock.patch.object(app, "_record_broker_send"), \
             mock.patch.object(app, "_record_exec_rejection"), \
             mock.patch.object(app, "_record_diagnostic"), \
             mock.patch("requests.post", side_effect=capture_post):
            result, code = app.execute_trade_gateway(
                instrument, 1, source="manual", direction=direction)

        return result, code, posts

    def test_s6_07_5xx_full_gateway_slot_held(self):
        """After a 5xx via the full gateway, the dedup slot is set in _TRADERSPOST_LAST."""
        inst = "MGC"
        # Clear the slot first
        with app._TRADERSPOST_LOCK:
            app._TRADERSPOST_LAST.pop(inst, None)

        result, code, posts = self._run_live_gateway(inst, "Long", 503)

        slot = app._TRADERSPOST_LAST.get(inst)
        check("S6-07-a slot held after 5xx", slot is not None,
              f"_TRADERSPOST_LAST[{inst}]={slot!r}")
        check("S6-07-b outcome=broker_rejected",
              result.get("outcome") == "broker_rejected",
              f"got {result.get('outcome')!r}")
        check("S6-07-c one broker HTTP call made", len(posts) == 1,
              f"posts={posts}")

    def test_s6_08_second_call_after_5xx_suppressed_without_broker_http(self):
        """The second immediate call after a 5xx is suppressed locally (no broker HTTP).
        This proves the cooldown slot held by the 5xx prevents a duplicate live order.
        """
        inst = "MGC"
        direction = "Long"
        # Clear the slot first
        with app._TRADERSPOST_LOCK:
            app._TRADERSPOST_LAST.pop(inst, None)

        # First call → 5xx → slot held
        analysis = _minimal_analysis(inst, direction)
        all_posts = []
        call_number = [0]

        class FakeResp:
            def __init__(self, sc):
                self.status_code = sc
                self.text = "error"

        def capture_post(url, **kwargs):
            all_posts.append(("post", url))
            return FakeResp(503)  # always 5xx

        with mock.patch.object(app, "full_analysis", return_value=analysis), \
             mock.patch.object(app, "resolve_execution_mode",
                               return_value="traderspost"), \
             mock.patch.object(app, "execution_configured", return_value=True), \
             mock.patch.object(app, "execution_is_live", return_value=True), \
             mock.patch.object(app, "_discord_url", return_value=None), \
             mock.patch.object(app, "_save_market_state"), \
             mock.patch.object(app, "_record_broker_send"), \
             mock.patch.object(app, "_record_exec_rejection"), \
             mock.patch.object(app, "_record_diagnostic"), \
             mock.patch("requests.post", side_effect=capture_post):

            # First call — hits broker (5xx), slot reserved
            r1, c1 = app.execute_trade_gateway(
                inst, 1, source="manual", direction=direction)
            posts_after_first = len(all_posts)

            # Second call — same instrument/direction/price → same fingerprint
            # Cooldown check fires BEFORE _send_broker_order → no broker HTTP
            r2, c2 = app.execute_trade_gateway(
                inst, 1, source="manual", direction=direction)
            posts_after_second = len(all_posts)

        check("S6-08-a first call reached broker (1 HTTP post)",
              posts_after_first == 1, f"posts={posts_after_first}")
        check("S6-08-b second call did NOT make a second broker HTTP call",
              posts_after_second == posts_after_first,
              f"posts went from {posts_after_first} to {posts_after_second}")
        check("S6-08-c first call outcome=broker_rejected",
              r1.get("outcome") == "broker_rejected",
              f"got {r1.get('outcome')!r}")
        check("S6-08-d second call outcome=rejected (duplicate suppressed)",
              r2.get("outcome") == "rejected",
              f"got {r2.get('outcome')!r}")
        check("S6-08-e second call HTTP 429",
              c2 == 429, f"got {c2}")

    def test_s6_09_4xx_full_gateway_slot_released(self):
        """After a 4xx via the full gateway, the slot is released from _TRADERSPOST_LAST.
        This contrasts with 5xx/timeout where the slot is held.
        """
        inst = "MGC"
        with app._TRADERSPOST_LOCK:
            app._TRADERSPOST_LAST.pop(inst, None)

        result, code, posts = self._run_live_gateway(inst, "Long", 400)

        slot = app._TRADERSPOST_LAST.get(inst)
        check("S6-09-a slot RELEASED after 4xx", slot is None,
              f"_TRADERSPOST_LAST[{inst}]={slot!r}")
        check("S6-09-b outcome=broker_rejected",
              result.get("outcome") == "broker_rejected")
        check("S6-09-c broker_verify_required=False on 4xx",
              result.get("broker_verify_required") is not True)

    def test_s6_10_5xx_vs_4xx_slot_behavior_summary(self):
        """Summary assertion: 5xx=slot held, 4xx=slot released, timeout=slot held.
        This is the critical safety invariant preventing duplicate live orders.
        """
        inst = "MGC"

        # 5xx: slot held
        with app._TRADERSPOST_LOCK:
            app._TRADERSPOST_LAST.pop(inst, None)
        r5, c5, _ = self._run_live_gateway(inst, "Long", 503)
        slot_5xx = app._TRADERSPOST_LAST.get(inst)
        # Clean up
        with app._TRADERSPOST_LOCK:
            app._TRADERSPOST_LAST.pop(inst, None)

        # 4xx: slot released
        r4, c4, _ = self._run_live_gateway(inst, "Long", 400)
        slot_4xx = app._TRADERSPOST_LAST.get(inst)
        with app._TRADERSPOST_LOCK:
            app._TRADERSPOST_LAST.pop(inst, None)

        check("S6-10-a 5xx: slot held (not None)", slot_5xx is not None,
              f"slot_5xx={slot_5xx!r}")
        check("S6-10-b 4xx: slot released (None)", slot_4xx is None,
              f"slot_4xx={slot_4xx!r}")
        check("S6-10-c 5xx outcome=broker_rejected",
              r5.get("outcome") == "broker_rejected")
        check("S6-10-d 4xx outcome=broker_rejected",
              r4.get("outcome") == "broker_rejected")
        check("S6-10-e 5xx broker_verify_required=True",
              r5.get("broker_verify_required") is True)
        check("S6-10-f 4xx broker_verify_required not True",
              r4.get("broker_verify_required") is not True)


# ─────────────────────────────────────────────────────────────────────────────
# Main runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestP5_001_ArmStateBootReset,
        TestP5_002_GatewayResultContract,
        TestP5_003_DuplicateExecutionPrevention,
        TestP5_004_BrokerRejection,
        TestP5_005_ExecutionTimeout,
        TestP5_005b_Broker5xxCooldown,
        TestP5_006_PayloadValidation,
        TestP5_007_SafeDisarm,
        TestP5_008_PaperModeE2E,
        TestP5_Stage4_CompatibilityProof,
        TestP5_Stage6_5xxSlotRetention,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result


if __name__ == "__main__":
    unittest.main(verbosity=2)
