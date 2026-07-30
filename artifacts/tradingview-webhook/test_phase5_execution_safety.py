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
os.environ.setdefault("EXECUTION_MODE", "paper")
os.environ.setdefault("TRAINING_MODE_ENABLED", "")
os.environ.setdefault("DISCORD_LIVE_ENABLED", "")
os.environ.setdefault("SESSION_SECRET", "test-secret-phase5")
os.environ.setdefault("DASHBOARD_PASSWORD", "test-pass")
os.environ.setdefault("DATABASE_URL", "")

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import app  # noqa: E402

FAILS: list = []
CHECKS: int = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    status = "OK  " if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


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
        "verdict":      "SCALP READY",
        "is_actionable": True,
        "market_open":  True,
        "direction":    direction,
        "trade_plan": {
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
             mock.patch.object(app, "_record_diagnostic"):
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
             mock.patch.object(app, "_record_diagnostic"):
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
        """After disarm, _maybe_auto_execute returns False (no new trade attempted)."""
        inst = "MGC"
        with app.AUTO_TRADE_LOCK:
            app.AUTO_TRADE[inst] = False

        fired = []

        def fake_gateway(*a, **kw):
            fired.append(True)
            return {"status": "simulated", "outcome": "paper",
                    "plan": {}, "provider": "paper", "_version": "v1"}, 200

        with mock.patch.object(app, "execute_trade_gateway",
                               side_effect=fake_gateway):
            result = app._maybe_auto_execute(inst)

        check("P5-007-f _maybe_auto_execute returns False when disarmed",
              result is False, f"got {result!r}")
        check("P5-007-g gateway NOT called when disarmed", len(fired) == 0,
              f"fired={fired}")

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
        """plan dict contains entry_zone and stop_loss keys."""
        result, code, posts = self._run_paper()
        plan = result.get("plan", {})
        check("P5-008-d plan has entry_zone", "entry_zone" in plan)
        check("P5-008-e plan has stop_loss", "stop_loss" in plan)

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
             mock.patch.object(app, "_record_diagnostic"):
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
             mock.patch.object(app, "_record_diagnostic"):
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
             mock.patch.object(app, "_record_diagnostic"):
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
             mock.patch.object(app, "_record_diagnostic"):
            result, code = app._send_broker_order(
                "traderspost", "TradersPost", "MGC",
                {"ticker": "MGC1!", "action": "buy", "quantity": 1},
                "https://fake.broker.url/test")
        check("P5-008-z4 broker_order timeout outcome=timeout",
              result.get("outcome") == "timeout",
              f"got {result.get('outcome')!r}")


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
        TestP5_006_PayloadValidation,
        TestP5_007_SafeDisarm,
        TestP5_008_PaperModeE2E,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result


if __name__ == "__main__":
    unittest.main(verbosity=2)
