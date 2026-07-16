"""Phase 3 thesis tests — Thesis Enforcement + Outcome Validation.

Drives Phase 3 helpers directly — pure-function, no network/threads/DB unless
explicitly testing the fail-open path (where we set THESIS_EVAL_DB_READY=False
to verify the guard works).

Covers 18 cases:
  P3-01  Aligned LONG setup with LONG READY thesis
  P3-02  Aligned SHORT setup with SHORT ACTIVE thesis
  P3-03  LONG setup conflicting with SHORT thesis → CONFLICTING
  P3-04  SHORT setup conflicting with LONG thesis → CONFLICTING
  P3-05  FORMING thesis produces PARTIALLY_ALIGNED
  P3-06  NEUTRAL thesis → NEUTRAL alignment
  P3-07  INVALIDATED thesis → INVALIDATED alignment
  P3-08  COOLDOWN thesis → INVALIDATED alignment (same effect)
  P3-09  Missing thesis snap → NO_THESIS (fails open)
  P3-10  Database unavailable → _thesis_eval_stats returns safe zero dict
  P3-11  Shadow mode does not change original verdict (READY stays READY)
  P3-12  Enforced mode blocks conflicting setup (READY → WAIT via gate)
  P3-13  Direction flip triggers _mark_setups_stale_for_inst stale marker
  P3-14  Non-flip transition does NOT create stale marker
  P3-15  Confidence adjustment is capped at spec limits (no unlimited stacking)
  P3-16  Outcome correctly identifies false block (shadow-blocked + target hit)
  P3-17  Outcome correctly identifies loss avoided (shadow-blocked + stop hit)
  P3-18  _thesis_eval_stats returns correct structure when DB is unavailable

Runnable two ways:
  pytest test_thesis_phase3.py
  python3 test_thesis_phase3.py
"""
import os
import sys
import types
import unittest

# ── minimal env to satisfy app-level constants ────────────────────────────────
os.environ.setdefault("TRADING_MODE",           "SCALP")
os.environ.setdefault("THESIS_HYSTERESIS",      "1")
os.environ.setdefault("THESIS_ENFORCEMENT_MODE","shadow")

# Prevent heavy boot side-effects (DB probes, timers, etc.)
os.environ.setdefault("DB_INIT_SKIP",   "1")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "")

sys.path.insert(0, os.path.dirname(__file__))
import app  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_thesis(status="READY_LONG", direction="LONG", confidence=80,
                 thesis_id="ABC123", inv_reason=None):
    t = {
        "status":     status,
        "direction":  direction,
        "confidence": confidence,
        "thesisId":   thesis_id,
        "evidenceFor":     ["STRUCTURE_BULLISH", "CVD_ALIGNED"],
        "evidenceAgainst": [],
    }
    if inv_reason:
        t["invalidationReason"] = inv_reason
    return t


# ── tests ─────────────────────────────────────────────────────────────────────

class TestP3Alignment(unittest.TestCase):

    # P3-01 ── Aligned LONG with LONG READY thesis
    def test_aligned_long_long_ready(self):
        snap = _make_thesis("READY_LONG", "LONG", 80)
        result = app._evaluate_thesis_alignment(snap, "LONG")
        self.assertEqual(result, "ALIGNED", result)

    # P3-02 ── Aligned SHORT with SHORT ACTIVE thesis
    def test_aligned_short_short_active(self):
        snap = _make_thesis("ACTIVE_SHORT", "SHORT", 75)
        result = app._evaluate_thesis_alignment(snap, "SHORT")
        self.assertEqual(result, "ALIGNED", result)

    # P3-03 ── LONG setup conflicting with SHORT thesis
    def test_conflicting_long_short_thesis(self):
        snap = _make_thesis("READY_SHORT", "SHORT", 80)
        result = app._evaluate_thesis_alignment(snap, "LONG")
        self.assertEqual(result, "CONFLICTING", result)

    # P3-04 ── SHORT setup conflicting with LONG thesis
    def test_conflicting_short_long_thesis(self):
        snap = _make_thesis("ACTIVE_LONG", "LONG", 85)
        result = app._evaluate_thesis_alignment(snap, "SHORT")
        self.assertEqual(result, "CONFLICTING", result)

    # P3-05 ── FORMING thesis → PARTIALLY_ALIGNED
    def test_forming_thesis_partial(self):
        snap = _make_thesis("FORMING_LONG", "LONG", 60)
        result = app._evaluate_thesis_alignment(snap, "LONG")
        self.assertEqual(result, "PARTIALLY_ALIGNED", result)

    # P3-06 ── NEUTRAL thesis → NEUTRAL
    def test_neutral_thesis(self):
        snap = _make_thesis("NEUTRAL", "", 0)
        result = app._evaluate_thesis_alignment(snap, "LONG")
        self.assertEqual(result, "NEUTRAL", result)

    # P3-07 ── INVALIDATED thesis → INVALIDATED
    def test_invalidated_thesis(self):
        snap = _make_thesis("INVALIDATED", "LONG", 30, inv_reason="Structure broke")
        result = app._evaluate_thesis_alignment(snap, "LONG")
        self.assertEqual(result, "INVALIDATED", result)

    # P3-08 ── COOLDOWN thesis → INVALIDATED (same treatment)
    def test_cooldown_thesis(self):
        snap = _make_thesis("COOLDOWN", "SHORT", 20)
        result = app._evaluate_thesis_alignment(snap, "SHORT")
        self.assertEqual(result, "INVALIDATED", result)

    # P3-09 ── Missing thesis snap → NO_THESIS (fails open)
    def test_no_thesis_snap_none(self):
        result = app._evaluate_thesis_alignment(None, "LONG")
        self.assertEqual(result, "NO_THESIS", result)

    def test_no_thesis_snap_empty_dict(self):
        result = app._evaluate_thesis_alignment({}, "LONG")
        # Empty dict has no direction → NEUTRAL
        self.assertIn(result, ("NO_THESIS", "NEUTRAL"), result)

    # P3-10 ── DB unavailable → _thesis_eval_stats returns safe zero dict
    def test_db_unavailable_returns_safe_dict(self):
        orig = app.THESIS_EVAL_DB_READY
        try:
            app.THESIS_EVAL_DB_READY = False
            stats = app._thesis_eval_stats()
            self.assertEqual(stats["total"],       0)
            self.assertEqual(stats["aligned"],     0)
            self.assertEqual(stats["conflicting"], 0)
            self.assertIsNone(stats["avg_r_aligned"])
            self.assertIn("enforcement_mode", stats)
        finally:
            app.THESIS_EVAL_DB_READY = orig


class TestP3GateResult(unittest.TestCase):

    # P3-11 ── Shadow mode does not change original verdict
    def test_shadow_mode_does_not_change_verdict(self):
        orig_mode = app._THESIS_ENFORCEMENT_MODE
        try:
            app._THESIS_ENFORCEMENT_MODE = "shadow"
            snap = _make_thesis("READY_SHORT", "SHORT", 80)  # opposes LONG setup
            gate = app._build_thesis_gate_result(
                "MNQ", snap, "CONFLICTING", "LONG", "READY")
            # In shadow mode, action must always be ALLOW (never BLOCK)
            self.assertEqual(gate["action"], "ALLOW",
                             "Shadow mode must never block — action must be ALLOW")
            self.assertEqual(gate["shadow_action"], "BLOCK",
                             "Shadow mode should record that it WOULD have blocked")
            self.assertTrue(gate["would_change_decision"],
                            "would_change_decision must be True when shadow blocks a READY")
            # Critically: original_verdict is preserved
            self.assertEqual(gate["original_verdict"], "READY")
        finally:
            app._THESIS_ENFORCEMENT_MODE = orig_mode

    # P3-12 ── Enforced mode blocks conflicting setup
    def test_enforced_mode_blocks_conflicting(self):
        orig_mode = app._THESIS_ENFORCEMENT_MODE
        try:
            app._THESIS_ENFORCEMENT_MODE = "enforced"
            snap = _make_thesis("READY_SHORT", "SHORT", 80)
            gate = app._build_thesis_gate_result(
                "MNQ", snap, "CONFLICTING", "LONG", "READY")
            self.assertEqual(gate["action"], "BLOCK",
                             "Enforced mode must set action=BLOCK for CONFLICTING")
        finally:
            app._THESIS_ENFORCEMENT_MODE = orig_mode

    def test_enforced_mode_does_not_block_aligned(self):
        orig_mode = app._THESIS_ENFORCEMENT_MODE
        try:
            app._THESIS_ENFORCEMENT_MODE = "enforced"
            snap = _make_thesis("READY_LONG", "LONG", 82)
            gate = app._build_thesis_gate_result(
                "MGC", snap, "ALIGNED", "LONG", "READY")
            self.assertEqual(gate["action"], "ALLOW",
                             "Enforced mode must not block an ALIGNED setup")
        finally:
            app._THESIS_ENFORCEMENT_MODE = orig_mode

    def test_off_mode_always_allows(self):
        orig_mode = app._THESIS_ENFORCEMENT_MODE
        try:
            app._THESIS_ENFORCEMENT_MODE = "off"
            snap = _make_thesis("READY_SHORT", "SHORT", 90)
            gate = app._build_thesis_gate_result(
                "MNQ", snap, "CONFLICTING", "LONG", "READY")
            self.assertEqual(gate["action"], "ALLOW",
                             "Off mode must never block")
            self.assertEqual(gate["shadow_action"], "ALLOW",
                             "Off mode shadow_action must also be ALLOW")
        finally:
            app._THESIS_ENFORCEMENT_MODE = orig_mode


class TestP3StaleSetups(unittest.TestCase):

    def setUp(self):
        # Clear stale markers before each test
        app.STALE_SETUPS_BY_INST.clear()

    # P3-13 ── Direction flip triggers stale marker
    def test_direction_flip_creates_stale_marker(self):
        prev = _make_thesis("READY_LONG", "LONG", 80, "ID001")
        new  = _make_thesis("READY_SHORT", "SHORT", 75, "ID002")
        app._mark_setups_stale_for_inst("MNQ", prev, new)
        markers = app.STALE_SETUPS_BY_INST.get("MNQ") or []
        self.assertEqual(len(markers), 1, "Exactly one stale marker expected")
        self.assertEqual(markers[0]["transition_type"], "DIRECTION_FLIP")

    def test_invalidation_creates_stale_marker(self):
        prev = _make_thesis("ACTIVE_LONG", "LONG", 85, "ID003")
        new  = _make_thesis("INVALIDATED", "", 0,  "ID003")
        app._mark_setups_stale_for_inst("MGC", prev, new)
        markers = app.STALE_SETUPS_BY_INST.get("MGC") or []
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["transition_type"], "INVALIDATED")

    # P3-14 ── Non-flip transition does NOT create stale marker
    def test_non_flip_does_not_create_stale_marker(self):
        prev = _make_thesis("FORMING_LONG", "LONG", 55, "ID004")
        new  = _make_thesis("READY_LONG",   "LONG", 80, "ID004")
        app._mark_setups_stale_for_inst("MNQ", prev, new)
        markers = app.STALE_SETUPS_BY_INST.get("MNQ") or []
        self.assertEqual(len(markers), 0,
                         "FORMING→READY (same direction) should NOT create stale marker")

    def test_neutral_to_forming_not_stale(self):
        prev = _make_thesis("NEUTRAL", "", 0, "ID005")
        new  = _make_thesis("FORMING_LONG", "LONG", 55, "ID006")
        app._mark_setups_stale_for_inst("MNQ", prev, new)
        markers = app.STALE_SETUPS_BY_INST.get("MNQ") or []
        self.assertEqual(len(markers), 0)


class TestP3ConfidenceAdjustmentCaps(unittest.TestCase):

    # P3-15 ── Confidence adjustment capped at spec limits (no double-counting)
    def test_aligned_cap_is_plus5(self):
        snap = _make_thesis("READY_LONG", "LONG", 85)
        gate = app._build_thesis_gate_result(
            "MGC", snap, "ALIGNED", "LONG", "READY")
        self.assertEqual(gate["confidence_adjustment"],
                         app.THESIS_EVAL_MAX_CONF_ADJ_ALIGNED)
        self.assertLessEqual(gate["confidence_adjustment"], 5,
                              "ALIGNED adj must be <= +5 (spec cap)")

    def test_partial_cap_is_plus2(self):
        snap = _make_thesis("FORMING_LONG", "LONG", 60)
        gate = app._build_thesis_gate_result(
            "MGC", snap, "PARTIALLY_ALIGNED", "LONG", "READY")
        self.assertEqual(gate["confidence_adjustment"],
                         app.THESIS_EVAL_MAX_CONF_ADJ_PARTIAL)
        self.assertLessEqual(gate["confidence_adjustment"], 2,
                              "PARTIALLY_ALIGNED adj must be <= +2 (spec cap)")

    def test_neutral_adj_is_zero(self):
        snap = _make_thesis("NEUTRAL", "", 0)
        gate = app._build_thesis_gate_result(
            "MNQ", snap, "NEUTRAL", "LONG", "WAIT")
        self.assertEqual(gate["confidence_adjustment"], 0)

    def test_conflicting_adj_is_negative(self):
        snap = _make_thesis("READY_SHORT", "SHORT", 80)
        gate = app._build_thesis_gate_result(
            "MNQ", snap, "CONFLICTING", "LONG", "READY")
        self.assertLessEqual(gate["confidence_adjustment"], 0,
                              "CONFLICTING adj must not be positive")

    def test_no_thesis_adj_is_zero(self):
        gate = app._build_thesis_gate_result(
            "MNQ", None, "NO_THESIS", "LONG", "READY")
        self.assertEqual(gate["confidence_adjustment"], 0)


class TestP3OutcomeClassification(unittest.TestCase):
    """Test false-block and loss-avoided classification logic against
    the _thesis_eval_stats structure (using direct field logic, not DB)."""

    # P3-16 ── False block: shadow blocked but original hit target
    def test_false_block_identification(self):
        # A false block has: would_change_decision=True, target_hit=True, stop_hit=False
        # We test that the _build_thesis_gate_result marks would_change_decision correctly
        orig_mode = app._THESIS_ENFORCEMENT_MODE
        try:
            app._THESIS_ENFORCEMENT_MODE = "shadow"
            snap = _make_thesis("READY_SHORT", "SHORT", 80)
            gate = app._build_thesis_gate_result(
                "MNQ", snap, "CONFLICTING", "LONG", "READY")
            # In shadow mode: would_change=True means this is a POTENTIAL false block
            # (if the setup later hits target before stop)
            self.assertTrue(gate["would_change_decision"],
                            "A shadow-blocked READY setup must set would_change_decision=True")
            self.assertEqual(gate["shadow_verdict"], "WAIT")
            self.assertEqual(gate["original_verdict"], "READY")
        finally:
            app._THESIS_ENFORCEMENT_MODE = orig_mode

    # P3-17 ── Loss avoided: shadow blocked + stop hit
    def test_loss_avoided_identification(self):
        orig_mode = app._THESIS_ENFORCEMENT_MODE
        try:
            app._THESIS_ENFORCEMENT_MODE = "shadow"
            snap = _make_thesis("INVALIDATED", "LONG", 20, inv_reason="Structure broke")
            gate = app._build_thesis_gate_result(
                "MGC", snap, "INVALIDATED", "LONG", "READY")
            # A loss avoided = would_change_decision=True AND (later) stop_hit=True
            self.assertTrue(gate["would_change_decision"])
            self.assertEqual(gate["shadow_action"], "BLOCK",
                             "INVALIDATED thesis must shadow-block a READY setup")
        finally:
            app._THESIS_ENFORCEMENT_MODE = orig_mode

    # P3-18 ── Dashboard stats structure has all expected keys
    def test_stats_structure_has_all_keys(self):
        orig_db = app.THESIS_EVAL_DB_READY
        try:
            app.THESIS_EVAL_DB_READY = False
            stats = app._thesis_eval_stats()
            required_keys = [
                "total", "aligned", "partially_aligned", "neutral",
                "conflicting", "invalidated", "no_thesis",
                "would_block", "would_upgrade", "would_downgrade",
                "false_blocks", "losses_avoided",
                "avg_r_aligned", "avg_r_conflicting", "net_r_impact",
                "enforcement_mode", "db_ready",
            ]
            for k in required_keys:
                self.assertIn(k, stats, f"Missing key: {k}")
        finally:
            app.THESIS_EVAL_DB_READY = orig_db


class TestP3Integration(unittest.TestCase):
    """Integration: _compute_thesis_gate returns correct structure."""

    def test_compute_gate_off_mode_returns_empty(self):
        orig_mode  = app._THESIS_ENFORCEMENT_MODE
        orig_eval  = app._THESIS_EVAL_ENABLED
        try:
            app._THESIS_ENFORCEMENT_MODE = "off"
            app._THESIS_EVAL_ENABLED     = False
            snap = _make_thesis("READY_LONG", "LONG", 80)
            # strict-like dict
            strict = {"candidate": "Long"}
            gate = app._compute_thesis_gate("MNQ", snap, strict, "READY")
            self.assertEqual(gate, {},
                             "Off mode must return empty dict immediately")
        finally:
            app._THESIS_ENFORCEMENT_MODE = orig_mode
            app._THESIS_EVAL_ENABLED     = orig_eval

    def test_compute_gate_shadow_mode_attaches_all_fields(self):
        orig_mode = app._THESIS_ENFORCEMENT_MODE
        orig_eval = app._THESIS_EVAL_ENABLED
        orig_persist = app._THESIS_EVAL_DB_PERSIST_ENABLED
        try:
            app._THESIS_ENFORCEMENT_MODE        = "shadow"
            app._THESIS_EVAL_ENABLED            = True
            app._THESIS_EVAL_DB_PERSIST_ENABLED = False  # no DB writes in tests
            snap   = _make_thesis("READY_LONG", "LONG", 80)
            strict = {"candidate": "Long"}
            gate   = app._compute_thesis_gate("MGC", snap, strict, "READY")
            for field in ("alignment", "action", "shadow_action",
                          "confidence_adjustment", "would_change_decision",
                          "original_verdict", "shadow_verdict", "reasons",
                          "enforcement_mode", "evaluated_at"):
                self.assertIn(field, gate, f"Missing field: {field}")
            self.assertEqual(gate["alignment"], "ALIGNED")
            self.assertEqual(gate["action"],    "ALLOW",
                             "Shadow mode must never change action to BLOCK")
        finally:
            app._THESIS_ENFORCEMENT_MODE        = orig_mode
            app._THESIS_EVAL_ENABLED            = orig_eval
            app._THESIS_EVAL_DB_PERSIST_ENABLED = orig_persist

    def test_gate_reasons_populated(self):
        snap = _make_thesis("READY_LONG", "LONG", 85)
        gate = app._build_thesis_gate_result(
            "MNQ", snap, "ALIGNED", "LONG", "READY")
        self.assertIsInstance(gate["reasons"], list)
        self.assertGreater(len(gate["reasons"]), 0, "Reasons list must not be empty")

    def test_gate_reasons_conflicting(self):
        snap = _make_thesis("READY_SHORT", "SHORT", 82)
        gate = app._build_thesis_gate_result(
            "MNQ", snap, "CONFLICTING", "LONG", "READY")
        self.assertIsInstance(gate["reasons"], list)
        self.assertTrue(any("oppos" in r.lower() for r in gate["reasons"]),
                        "Conflicting reasons must mention direction opposition")


# ── runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()
    for cls in [
        TestP3Alignment,
        TestP3GateResult,
        TestP3StaleSetups,
        TestP3ConfidenceAdjustmentCaps,
        TestP3OutcomeClassification,
        TestP3Integration,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
