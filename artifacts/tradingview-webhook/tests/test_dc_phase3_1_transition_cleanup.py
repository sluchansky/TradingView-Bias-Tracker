"""
Decision Contract Phase 3.1 — Transition Cleanup Tests
=======================================================
Targeted regression for the OBSERVING → WAIT gap fix.

Spec requirements:
  1. WAIT → OBSERVING = valid
  2. OBSERVING → WAIT = valid  (THE FIX)
  3. OBSERVING → WAIT preserves reason code
  4. WAIT → WAIT does not create duplicate transition
  5. OBSERVING → OBSERVING does not create duplicate transition
  6. Illegal genuinely invalid transition still rejected
  7. Four-instrument isolation
  8. Restart persistence
  9. Parity behavior unchanged
  + Additional cycle / sequence tests
"""

from __future__ import annotations

import sys
import pathlib
import types
import unittest
from unittest.mock import MagicMock

_here = pathlib.Path(__file__).parent.parent.resolve()
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from decision_contract import (
    DecisionState as DS,
    DecisionRegistry,
    DecisionRecord,
    ReasonCode,
    LEGAL_TRANSITIONS,
    validate_transition,
    _map_strict_reason,
    map_full_analysis_to_canonical,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

INSTRUMENTS = ["MGC", "MNQ", "MES", "MYM"]


def _make_mock_db():
    mock_db = MagicMock()
    mock_db.cursor.return_value = MagicMock()
    return mock_db


def _make_registry(instruments=None):
    registry = DecisionRegistry(
        get_db_fn=lambda: _make_mock_db(),
        re_event_fn=lambda *a, **kw: None,
        instruments=instruments or INSTRUMENTS,
        shadow_mode=True,
    )
    DecisionRegistry.DC_DB_READY = False
    return registry


# arm_state for READY (execution enabled, not armed)
_ARM_READY = {
    "armed": False,
    "execution_enabled": True,
    "configured_mode": "traderspost",
    "safety_locked": False,
}

# arm_state that intentionally leaves execution disabled (WAIT path)
_ARM_DISABLED = {
    "armed": False,
    "execution_enabled": False,
    "configured_mode": "disabled",
    "safety_locked": False,
}


def _fa(inst="MGC", verdict="WAIT", edge=30.0,
        strict_reason="vwap_not_confirmed", gate_debug=None):
    return {
        "instrument": inst,
        "verdict": verdict,
        "direction": "",
        "edge_score": edge,
        "strict_reason": strict_reason,
        "gate_debug": gate_debug or {"vwap_confirmed": False},
        "is_actionable": False,
    }


def _fa_ready(inst="MGC", edge=75.0):
    return {
        "instrument": inst,
        "verdict": "LONG READY",
        "direction": "Long",
        "edge_score": edge,
        "strict_reason": "OK",
        "is_actionable": True,
    }


def _drive_to_observing(registry, inst="MGC"):
    """Simulate ORB SESSION_CLOSED → DC transitions to OBSERVING."""
    registry._simple_transition(
        inst, DS.OBSERVING, ReasonCode.SESSION_CLOSED, "Session closed", "orb_engine"
    )


def _drive_to_wait(registry, inst="MGC", reason="vwap_not_confirmed"):
    """Drive from any starting state to WAIT via full_analysis(verdict=WAIT)."""
    fa = _fa(inst=inst, verdict="WAIT", strict_reason=reason,
              gate_debug={"vwap_confirmed": False})
    return registry.observe_full_analysis(inst, fa, None)


def _history_len(registry, inst):
    return len(registry.get_history(inst))


# ── 1. WAIT → OBSERVING = valid ───────────────────────────────────────────────

class TestWaitToObservingValid(unittest.TestCase):

    def test_01_wait_to_observing_is_legal(self):
        """WAIT → OBSERVING must be in LEGAL_TRANSITIONS (pre-existing)."""
        ok, err = validate_transition(DS.WAIT, DS.OBSERVING, "test-id")
        self.assertTrue(ok, f"WAIT → OBSERVING should be legal: {err}")

    def test_02_wait_to_observing_via_simple_transition(self):
        """Registry can actually make the WAIT → OBSERVING transition."""
        registry = _make_registry(["MGC"])
        _drive_to_wait(registry, "MGC")
        self.assertEqual(registry.get_record("MGC").state, DS.WAIT)
        _drive_to_observing(registry, "MGC")
        self.assertEqual(registry.get_record("MGC").state, DS.OBSERVING)

    def test_03_wait_to_observing_via_orb_session_close(self):
        """Real production path: WAIT then ORB SESSION_CLOSED → OBSERVING."""
        registry = _make_registry(["MGC"])
        # Boot state: WAIT (full_analysis returned WAIT)
        _drive_to_wait(registry, "MGC")
        h_before = _history_len(registry, "MGC")
        # ORB fires session close
        _drive_to_observing(registry, "MGC")
        h_after = _history_len(registry, "MGC")
        self.assertEqual(registry.get_record("MGC").state, DS.OBSERVING)
        self.assertGreater(h_after, h_before, "Transition must be recorded in history")


# ── 2. OBSERVING → WAIT = valid (THE FIX) ────────────────────────────────────

class TestObservingToWaitValid(unittest.TestCase):

    def test_04_observing_to_wait_is_now_legal(self):
        """OBSERVING → WAIT must be in LEGAL_TRANSITIONS after Phase 3.1 fix."""
        ok, err = validate_transition(DS.OBSERVING, DS.WAIT, "test-id")
        self.assertTrue(ok, f"OBSERVING → WAIT should be legal: {err}")

    def test_05_observing_to_wait_via_full_analysis(self):
        """After ORB sets OBSERVING, full_analysis returning WAIT must succeed."""
        registry = _make_registry(["MGC"])
        # Simulate production sequence: WAIT → OBSERVING → WAIT
        _drive_to_wait(registry, "MGC")                    # → WAIT
        _drive_to_observing(registry, "MGC")               # → OBSERVING (ORB)
        self.assertEqual(registry.get_record("MGC").state, DS.OBSERVING)
        # Now full_analysis returns WAIT — this was the ILLEGAL TRANSITION
        _drive_to_wait(registry, "MGC")
        self.assertEqual(registry.get_record("MGC").state, DS.WAIT)

    def test_06_observing_to_wait_via_simple_transition(self):
        """Direct _simple_transition OBSERVING → WAIT is accepted."""
        registry = _make_registry(["MGC"])
        _drive_to_observing(registry, "MGC")
        registry._simple_transition(
            "MGC", DS.WAIT, ReasonCode.VWAP_CONFLICT, "vwap not confirmed", "full_analysis"
        )
        self.assertEqual(registry.get_record("MGC").state, DS.WAIT)

    def test_07_repeated_cycle_observing_wait_observing_wait(self):
        """Full production cycle: OBSERVING→WAIT→OBSERVING→WAIT — all transitions legal."""
        registry = _make_registry(["MGC"])
        _drive_to_observing(registry, "MGC")
        _drive_to_wait(registry, "MGC")
        self.assertEqual(registry.get_record("MGC").state, DS.WAIT)
        _drive_to_observing(registry, "MGC")
        self.assertEqual(registry.get_record("MGC").state, DS.OBSERVING)
        _drive_to_wait(registry, "MGC")
        self.assertEqual(registry.get_record("MGC").state, DS.WAIT)

    def test_08_all_transitions_in_cycle_are_persisted(self):
        """OBSERVING→WAIT→OBSERVING→WAIT creates 4 transition history rows."""
        registry = _make_registry(["MGC"])
        h0 = _history_len(registry, "MGC")
        _drive_to_observing(registry, "MGC")          # +1
        _drive_to_wait(registry, "MGC")               # +1
        _drive_to_observing(registry, "MGC")          # +1
        _drive_to_wait(registry, "MGC")               # +1
        h1 = _history_len(registry, "MGC")
        self.assertEqual(h1 - h0, 4, "All 4 state changes must create history rows")


# ── 3. OBSERVING → WAIT preserves reason code ─────────────────────────────────

class TestObservingToWaitPreservesReasonCode(unittest.TestCase):

    def test_09_vwap_conflict_reason_code_preserved(self):
        """VWAP gate failure produces VWAP_CONFLICT reason on WAIT record."""
        registry = _make_registry(["MGC"])
        _drive_to_observing(registry, "MGC")
        fa = _fa(strict_reason="vwap_not_confirmed",
                  gate_debug={"vwap_confirmed": False})
        registry.observe_full_analysis("MGC", fa, None)
        rec = registry.get_record("MGC")
        self.assertEqual(rec.state, DS.WAIT)
        self.assertEqual(rec.reason_code, ReasonCode.VWAP_CONFLICT)

    def test_10_no_structure_reason_code_preserved(self):
        """Structure gate failure produces NO_STRUCTURE reason on WAIT record."""
        registry = _make_registry(["MGC"])
        _drive_to_observing(registry, "MGC")
        fa = _fa(strict_reason="no structure signal",
                  gate_debug={"vwap_confirmed": True, "structure_confirmed": False})
        registry.observe_full_analysis("MGC", fa, None)
        rec = registry.get_record("MGC")
        self.assertEqual(rec.state, DS.WAIT)
        self.assertEqual(rec.reason_code, ReasonCode.NO_STRUCTURE)

    def test_11_no_setup_reason_code_default(self):
        """When strict_reason is empty, reason_code defaults to NO_SETUP on WAIT."""
        registry = _make_registry(["MGC"])
        _drive_to_observing(registry, "MGC")
        fa = _fa(strict_reason="",
                  gate_debug={"vwap_confirmed": True, "structure_confirmed": True})
        registry.observe_full_analysis("MGC", fa, None)
        rec = registry.get_record("MGC")
        self.assertEqual(rec.state, DS.WAIT)
        self.assertEqual(rec.reason_code, ReasonCode.NO_SETUP)

    def test_12_reason_code_not_replaced_with_unknown(self):
        """OBSERVING → WAIT must never overwrite a real reason_code with UNKNOWN."""
        registry = _make_registry(["MGC"])
        _drive_to_observing(registry, "MGC")
        # CVD failure
        fa = _fa(strict_reason="cvd_conflict",
                  gate_debug={"vwap_confirmed": True, "structure_confirmed": True})
        registry.observe_full_analysis("MGC", fa, None)
        rec = registry.get_record("MGC")
        self.assertNotEqual(rec.reason_code, ReasonCode.UNKNOWN,
                            "A real reason code must not be replaced with UNKNOWN")

    def test_13_map_strict_reason_vwap_returns_wait_vwap_conflict(self):
        """_map_strict_reason("vwap_not_confirmed") → (WAIT, VWAP_CONFLICT)."""
        state, rc = _map_strict_reason("vwap_not_confirmed", {})
        self.assertEqual(state, DS.WAIT)
        self.assertEqual(rc, ReasonCode.VWAP_CONFLICT)

    def test_14_map_full_analysis_wait_verdict_returns_wait(self):
        """map_full_analysis_to_canonical with verdict=WAIT returns a WAIT family state."""
        fa = {"strict_reason": "vwap_not_confirmed", "gate_debug": {"vwap_confirmed": False}}
        state, rc, text = map_full_analysis_to_canonical("WAIT", fa, arm_state=None)
        self.assertEqual(state, DS.WAIT)
        self.assertEqual(rc, ReasonCode.VWAP_CONFLICT)


# ── 4 & 5. Same-state repeated observations must not create duplicate transitions

class TestSameStateDedup(unittest.TestCase):

    def test_15_wait_to_wait_no_duplicate_transition(self):
        """Repeated WAIT evaluation (same state) creates NO new transition row."""
        registry = _make_registry(["MGC"])
        _drive_to_wait(registry, "MGC")
        h_before = _history_len(registry, "MGC")
        # Second full_analysis also returns WAIT — must be a no-op transition
        _drive_to_wait(registry, "MGC")
        h_after = _history_len(registry, "MGC")
        self.assertEqual(h_before, h_after,
                         "WAIT→WAIT (same state) must not add a new transition row")
        self.assertEqual(registry.get_record("MGC").state, DS.WAIT)

    def test_16_observing_to_observing_no_duplicate_transition(self):
        """Repeated OBSERVING observation creates NO new transition row."""
        registry = _make_registry(["MGC"])
        _drive_to_observing(registry, "MGC")
        h_before = _history_len(registry, "MGC")
        _drive_to_observing(registry, "MGC")
        h_after = _history_len(registry, "MGC")
        self.assertEqual(h_before, h_after,
                         "OBSERVING→OBSERVING (same state) must not add a transition row")

    def test_17_wait_verdict_repeated_three_times_still_one_transition(self):
        """Three consecutive WAIT evaluations from OBSERVING: 1 transition, not 3."""
        registry = _make_registry(["MGC"])
        _drive_to_observing(registry, "MGC")
        h_before = _history_len(registry, "MGC")
        for _ in range(3):
            _drive_to_wait(registry, "MGC")
        h_after = _history_len(registry, "MGC")
        self.assertEqual(h_after - h_before, 1,
                         "Three identical WAIT evals: only first creates a transition row")


# ── 6. Illegal transition still rejected ─────────────────────────────────────

class TestIllegalTransitionStillRejected(unittest.TestCase):

    def test_18_completed_to_wait_is_illegal(self):
        """COMPLETED → WAIT is not a legal transition."""
        ok, _ = validate_transition(DS.COMPLETED, DS.WAIT, "test")
        self.assertFalse(ok)

    def test_19_completed_to_observing_is_illegal(self):
        """COMPLETED → OBSERVING is not a legal transition."""
        ok, _ = validate_transition(DS.COMPLETED, DS.OBSERVING, "test")
        self.assertFalse(ok)

    def test_20_entry_requested_to_wait_is_illegal(self):
        """ENTRY_REQUESTED → WAIT (skipping order resolution) is not legal."""
        ok, _ = validate_transition(DS.ENTRY_REQUESTED, DS.WAIT, "test")
        self.assertFalse(ok)

    def test_21_registry_rejects_illegal_without_crash(self):
        """Registry silently drops an illegal transition — does not raise."""
        registry = _make_registry(["MGC"])
        # Drive to COMPLETED
        _ARM_EXEC = {**_ARM_READY, "armed": True}
        fa = _fa_ready("MGC")
        registry.observe_full_analysis("MGC", fa, _ARM_EXEC)
        registry.observe_entry_requested("MGC")
        registry.observe_order_accepted("MGC")
        registry.observe_position_active("MGC")
        registry.observe_completed("MGC")
        self.assertEqual(registry.get_record("MGC").state, DS.COMPLETED)
        # Try illegal COMPLETED → WAIT — must be silently ignored, no raise
        registry._simple_transition("MGC", DS.WAIT, ReasonCode.NO_SETUP, "", "test")
        self.assertEqual(registry.get_record("MGC").state, DS.COMPLETED,
                         "State must remain COMPLETED after illegal transition attempt")


# ── 7. Four-instrument isolation ─────────────────────────────────────────────

class TestFourInstrumentIsolation(unittest.TestCase):

    def test_22_observing_to_wait_on_mgc_does_not_affect_mnq(self):
        """OBSERVING → WAIT on MGC leaves MNQ state unchanged."""
        registry = _make_registry(INSTRUMENTS)
        for inst in INSTRUMENTS:
            _drive_to_observing(registry, inst)
        state_mnq_before = registry.get_record("MNQ").state
        _drive_to_wait(registry, "MGC")
        self.assertEqual(registry.get_record("MGC").state, DS.WAIT)
        self.assertEqual(registry.get_record("MNQ").state, state_mnq_before)

    def test_23_all_four_can_independently_cycle_observing_wait(self):
        """All four instruments can independently do OBSERVING→WAIT→OBSERVING→WAIT."""
        registry = _make_registry(INSTRUMENTS)
        for inst in INSTRUMENTS:
            _drive_to_observing(registry, inst)
            _drive_to_wait(registry, inst)
            _drive_to_observing(registry, inst)
            _drive_to_wait(registry, inst)
            self.assertEqual(registry.get_record(inst).state, DS.WAIT)

    def test_24_independent_reason_codes_per_instrument(self):
        """Each instrument's WAIT reason code is independent."""
        registry = _make_registry(INSTRUMENTS)
        for inst in INSTRUMENTS:
            _drive_to_observing(registry, inst)
        # MGC: VWAP failure
        registry.observe_full_analysis("MGC", _fa(
            inst="MGC", strict_reason="vwap_not_confirmed",
            gate_debug={"vwap_confirmed": False}), None)
        # MNQ: structure failure
        registry.observe_full_analysis("MNQ", _fa(
            inst="MNQ", strict_reason="no structure signal",
            gate_debug={"vwap_confirmed": True, "structure_confirmed": False}), None)
        self.assertEqual(registry.get_record("MGC").reason_code, ReasonCode.VWAP_CONFLICT)
        self.assertEqual(registry.get_record("MNQ").reason_code, ReasonCode.NO_STRUCTURE)


# ── 8. Restart persistence ────────────────────────────────────────────────────

class TestRestartPersistence(unittest.TestCase):

    def test_25_record_accessible_after_observing_to_wait(self):
        """After OBSERVING→WAIT the record is retrievable and has correct state."""
        registry = _make_registry(["MGC"])
        _drive_to_observing(registry, "MGC")
        _drive_to_wait(registry, "MGC")
        rec = registry.get_record("MGC")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.state, DS.WAIT)
        self.assertEqual(rec.instrument, "MGC")

    def test_26_transition_history_persisted_across_cycle(self):
        """Transition history contains all steps of OBSERVING→WAIT→OBSERVING→WAIT."""
        registry = _make_registry(["MGC"])
        n0 = _history_len(registry, "MGC")
        _drive_to_observing(registry, "MGC")
        _drive_to_wait(registry, "MGC")
        _drive_to_observing(registry, "MGC")
        _drive_to_wait(registry, "MGC")
        n1 = _history_len(registry, "MGC")
        self.assertEqual(n1 - n0, 4)
        hist = registry.get_history("MGC")
        # get_history returns a list of dicts (serialised DecisionTransition rows)
        def _to_state(t):
            return t.get("to_state") if isinstance(t, dict) else t.to_state
        states = [_to_state(t) for t in hist[-4:]]
        self.assertEqual(states,
                         [DS.OBSERVING, DS.WAIT, DS.OBSERVING, DS.WAIT])


# ── 9. Parity behavior unchanged ─────────────────────────────────────────────

class TestParityUnchanged(unittest.TestCase):

    def test_27_wait_verdict_from_observing_parity_agree_true(self):
        """WAIT verdict: legacy=not-actionable; canonical=WAIT (not EXECUTABLE) → parity agrees."""
        registry = _make_registry(["MGC"])
        _drive_to_observing(registry, "MGC")
        fa = _fa(verdict="WAIT", strict_reason="vwap_not_confirmed",
                  gate_debug={"vwap_confirmed": False})
        registry.observe_full_analysis("MGC", fa, None)
        rec = registry.get_record("MGC")
        # Both legacy and canonical agree this is not executable
        self.assertTrue(rec.parity_agree,
                        "WAIT state: legacy and canonical both non-executable → parity_agree=True")

    def test_28_ready_verdict_parity_still_works(self):
        """READY verdict still evaluates parity correctly — Phase 3.1 does not regress this."""
        registry = _make_registry(["MGC"])
        fa = _fa_ready("MGC")
        registry.observe_full_analysis("MGC", fa, _ARM_READY)
        rec = registry.get_record("MGC")
        self.assertEqual(rec.state, DS.READY)
        # Ready + not armed: legacy=actionable, canonical=READY (not EXECUTABLE) → parity mismatch
        # (READY_VERDICTS mark legacy as actionable but DC is not yet EXECUTABLE without arm)
        self.assertIsNotNone(rec.parity_agree)  # must be computed, not None

    def test_29_legal_transitions_still_contain_wait_to_observing(self):
        """WAIT → OBSERVING must still be in LEGAL_TRANSITIONS (regression guard)."""
        ok, err = validate_transition(DS.WAIT, DS.OBSERVING, "test")
        self.assertTrue(ok, f"WAIT → OBSERVING must remain legal: {err}")

    def test_30_observing_to_wait_now_in_legal_transitions(self):
        """OBSERVING → WAIT must be in LEGAL_TRANSITIONS after Phase 3.1."""
        self.assertIn((DS.OBSERVING, DS.WAIT), LEGAL_TRANSITIONS)


# ── Additional: map_full_analysis_to_canonical edge cases ────────────────────

class TestMapFullAnalysisCanonicalization(unittest.TestCase):

    def test_31_market_closed_verdict_maps_to_observing(self):
        """'MARKET CLOSED' verdict maps to OBSERVING (not WAIT)."""
        state, rc, _ = map_full_analysis_to_canonical(
            "MARKET CLOSED", {}, arm_state=None)
        self.assertEqual(state, DS.OBSERVING)
        self.assertEqual(rc, ReasonCode.SESSION_CLOSED)

    def test_32_session_closed_verdict_maps_to_observing(self):
        """'SESSION CLOSED' verdict maps to OBSERVING."""
        state, rc, _ = map_full_analysis_to_canonical(
            "SESSION CLOSED", {}, arm_state=None)
        self.assertEqual(state, DS.OBSERVING)

    def test_33_setup_building_verdict_maps_to_setup_forming(self):
        """'SETUP BUILDING' verdict maps to SETUP_FORMING."""
        state, rc, _ = map_full_analysis_to_canonical(
            "SETUP BUILDING", {}, arm_state=None)
        self.assertEqual(state, DS.SETUP_FORMING)

    def test_34_observing_to_setup_forming_is_legal(self):
        """OBSERVING → SETUP_FORMING (existing path) still legal."""
        ok, _ = validate_transition(DS.OBSERVING, DS.SETUP_FORMING, "test")
        self.assertTrue(ok)


class TestBlockedStateRecovery(unittest.TestCase):

    def test_35_wait_to_ready_is_legal_when_intermediate_tick_is_skipped(self):
        """A fresh full analysis can promote WAIT directly to READY."""
        ok, err = validate_transition(DS.WAIT, DS.READY, "test")
        self.assertTrue(ok, f"WAIT → READY must be legal: {err}")

    def test_36_blocked_data_recovers_to_each_full_analysis_signal_state(self):
        """Fresh data must not leave the display-only state machine stuck."""
        for state in (DS.WAIT, DS.SETUP_FORMING, DS.EARLY, DS.READY,
                      DS.QUALIFIED, DS.RISK_PENDING, DS.RISK_APPROVED,
                      DS.EXECUTABLE):
            ok, err = validate_transition(DS.BLOCKED_DATA, state, "test")
            self.assertTrue(ok, f"BLOCKED_DATA → {state} must be legal: {err}")

    def test_37_full_analysis_promotes_blocked_data_to_ready(self):
        """Observed live recovery: data arrives and the next cycle is READY."""
        registry = _make_registry(["MNQ"])
        registry.observe_full_analysis(
            "MNQ",
            _fa(
                inst="MNQ", strict_reason="data unavailable",
                gate_debug={"data_available": False},
            ),
            None,
        )
        self.assertEqual(registry.get_record("MNQ").state, DS.BLOCKED_DATA)

        registry.observe_full_analysis("MNQ", _fa_ready("MNQ"), _ARM_READY)
        self.assertEqual(registry.get_record("MNQ").state, DS.READY)


if __name__ == "__main__":
    unittest.main(verbosity=2)
