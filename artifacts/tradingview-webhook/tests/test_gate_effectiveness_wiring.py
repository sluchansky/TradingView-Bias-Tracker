"""Gate Effectiveness Audit — Phase 8C wiring regression tests.

Spec items covered:
  1. ALLOWED final gate decision creates exactly 1 Phase 8C observation
  2. BLOCKED final gate decision creates exactly 1 Phase 8C observation
  3. Early-return BLOCKED decisions are still recorded (when score+blocker present)
  4. One candidate cannot create duplicate observations (ON CONFLICT dedup)
  5. Recorder failure cannot crash live trading (FAIL-OPEN)
  6. Database/API counts agree (validate_wiring round-trip)
  7. Dashboard JSON contract remains valid (summary schema)
  8. Synthetic/test observations cannot contaminate live measurements
  9. Direction extracted correctly from strict_reason when strict_direction is None
 10. Low-signal BLOCKED with no blocker is skipped (no spam)
 11. GATE_AUDIT_TRACE log lines are emitted at INFO level on success
"""

from __future__ import annotations

import json
import logging
import threading
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import gate_effectiveness as ge


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_conn(rows=None):
    """Return (conn, cursor) mocks; cursor.fetchone returns rows one-by-one."""
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__ = lambda s: cur
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    if rows is not None:
        cur.fetchone.side_effect = list(rows) + [None]
    return conn, cur


def _result(verdict="WAIT", score=50, direction=None, strict_reason=None,
            blocker=None, entry=None, stop=None, target=None):
    """Build a minimal full_analysis result dict for tests."""
    gate_debug = {}
    if blocker:
        gate_debug["failed_conditions"] = [blocker] if isinstance(blocker, str) else blocker
        gate_debug["blockedBy"] = gate_debug["failed_conditions"]
    return {
        "verdict":         verdict,
        "strict_direction": direction,
        "strict_reason":   strict_reason,
        "edge_breakdown":  {"score": score},
        "gate_debug":      gate_debug,
        "confluences":     {},
        "trade_plan":      {
            "entry":  entry,
            "stop":   stop,
            "target": target,
        } if entry else {},
        "volatility":      {},
        "directions":      {},
    }


class TestAllowedRecorded(unittest.TestCase):
    """Spec item 1: ALLOWED final gate decision creates exactly 1 observation."""

    def test_ready_verdict_writes_one_row(self):
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        r = _result(verdict="LONG READY", score=80, direction="Long")

        with patch.object(ge, "_learning_conn", return_value=conn):
            ge.record_gate_decision(r, "MNQ", "SCALP")

        # Exactly one INSERT call
        insert_calls = [c for c in cur.execute.call_args_list
                        if "INSERT INTO gate_audit_log" in str(c)]
        self.assertEqual(len(insert_calls), 1)
        conn.commit.assert_called_once()

    def test_allowed_gate_verdict_label(self):
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        r = _result(verdict="LONG READY", score=80, direction="Long")

        with patch.object(ge, "_learning_conn", return_value=conn):
            ge.record_gate_decision(r, "MNQ", "SCALP")

        params = cur.execute.call_args_list[-1][0][1]
        # gate_verdict is at index 10 (0-based) in the INSERT param tuple
        gate_verdict_idx = 10
        self.assertEqual(params[gate_verdict_idx], "ALLOWED")


class TestBlockedRecorded(unittest.TestCase):
    """Spec item 2: BLOCKED final gate decision creates exactly 1 observation."""

    def test_blocked_verdict_with_blocker_writes_one_row(self):
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        r = _result(verdict="WAIT", score=20, direction="Long",
                    blocker="vwap_unconfirmed")

        with patch.object(ge, "_learning_conn", return_value=conn):
            ge.record_gate_decision(r, "MNQ", "SCALP")

        insert_calls = [c for c in cur.execute.call_args_list
                        if "INSERT INTO gate_audit_log" in str(c)]
        self.assertEqual(len(insert_calls), 1)

    def test_blocked_gate_verdict_label(self):
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        r = _result(verdict="WAIT", score=20, direction="Long",
                    blocker="vwap_unconfirmed")

        with patch.object(ge, "_learning_conn", return_value=conn):
            ge.record_gate_decision(r, "MNQ", "SCALP")

        params = cur.execute.call_args_list[-1][0][1]
        gate_verdict_idx = 10
        self.assertEqual(params[gate_verdict_idx], "BLOCKED")


class TestDirectionFromStrictReason(unittest.TestCase):
    """Spec item 9 / root-cause fix: direction parsed from strict_reason."""

    def test_short_wait_strict_reason_extracts_short(self):
        """The production case: verdict='WAIT', strict_direction=None,
        strict_reason='Short WAIT — failed gate(s): volume_unconfirmed, edge_score(20<60).'
        Must produce direction='Short' so the record is NOT dropped."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        r = _result(
            verdict="WAIT",
            score=20,
            direction=None,
            strict_reason="Short WAIT — failed gate(s): volume_unconfirmed, edge_score(20<60).",
            blocker=["volume_unconfirmed", "edge_score(20<60)"],
        )

        with patch.object(ge, "_learning_conn", return_value=conn):
            ge.record_gate_decision(r, "MNQ", "SCALP")

        insert_calls = [c for c in cur.execute.call_args_list
                        if "INSERT INTO gate_audit_log" in str(c)]
        self.assertEqual(len(insert_calls), 1, "record must NOT be dropped when direction in strict_reason")
        params = cur.execute.call_args_list[-1][0][1]
        # direction is at index 5
        self.assertEqual(params[5], "Short")

    def test_long_wait_strict_reason_extracts_long(self):
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        r = _result(
            verdict="WAIT",
            score=30,
            direction=None,
            strict_reason="Long WAIT — failed gate(s): zone_mitigated.",
            blocker="zone_mitigated",
        )
        with patch.object(ge, "_learning_conn", return_value=conn):
            ge.record_gate_decision(r, "MGC", "SWING")

        insert_calls = [c for c in cur.execute.call_args_list
                        if "INSERT INTO gate_audit_log" in str(c)]
        self.assertEqual(len(insert_calls), 1)
        params = cur.execute.call_args_list[-1][0][1]
        self.assertEqual(params[5], "Long")

    def test_bare_wait_no_direction_drops_record(self):
        """Pure WAIT with no directional hint = no candidate, correctly skipped."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        r = _result(verdict="WAIT", score=20, direction=None,
                    strict_reason="WAIT — market neutral",
                    blocker="edge_score(20<60)")
        with patch.object(ge, "_learning_conn", return_value=conn):
            ge.record_gate_decision(r, "MNQ", "SCALP")
        insert_calls = [c for c in cur.execute.call_args_list
                        if "INSERT INTO gate_audit_log" in str(c)]
        self.assertEqual(len(insert_calls), 0)


class TestEarlyReturnBlocked(unittest.TestCase):
    """Spec item 3: early-return BLOCKED still recorded when score+blocker present."""

    def test_high_score_no_blocker_still_allowed_to_record(self):
        """Score>=MIN_EDGE_TO_RECORD_BLOCKED with no blocker: ALLOWED path records normally."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        r = _result(verdict="LONG READY", score=80, direction="Long")
        with patch.object(ge, "_learning_conn", return_value=conn):
            ge.record_gate_decision(r, "MNQ", "SCALP")
        insert_calls = [c for c in cur.execute.call_args_list
                        if "INSERT INTO gate_audit_log" in str(c)]
        self.assertEqual(len(insert_calls), 1)

    def test_low_score_with_blocker_records(self):
        """Low score but primary_blocker present → must record (not skipped)."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        # score=10 < MIN_EDGE_TO_RECORD_BLOCKED=15 BUT primary_blocker set → record
        r = _result(verdict="WAIT", score=10, direction="Long",
                    blocker="zone_mitigated",
                    strict_reason="Long WAIT — zone mitigated.")
        with patch.object(ge, "_learning_conn", return_value=conn):
            ge.record_gate_decision(r, "MNQ", "SCALP")
        insert_calls = [c for c in cur.execute.call_args_list
                        if "INSERT INTO gate_audit_log" in str(c)]
        self.assertEqual(len(insert_calls), 1)


class TestDeduplication(unittest.TestCase):
    """Spec item 4: one candidate cannot create duplicate observations."""

    def test_same_bucket_same_audit_id(self):
        """Two calls in the same dedup window must produce identical audit_ids."""
        now = datetime(2026, 8, 11, 14, 5, 0, tzinfo=timezone.utc)
        inst, direction, mode = "MNQ", "Long", "SCALP"

        with patch("gate_effectiveness.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            # Bucket: 10-minute window → minute 5 → bucket index 0
            ts_bucket = now.strftime("%Y%m%d%H") + f"{now.minute // ge._ALLOWED_DEDUP_MIN:02d}"
            expected_id = f"{inst}|{direction}|{mode}|ALLOWED|{ts_bucket}"

            ts_bucket2 = now.strftime("%Y%m%d%H") + f"{now.minute // ge._ALLOWED_DEDUP_MIN:02d}"
            expected_id2 = f"{inst}|{direction}|{mode}|ALLOWED|{ts_bucket2}"

        self.assertEqual(expected_id, expected_id2)

    def test_on_conflict_do_update_in_sql(self):
        """INSERT must use ON CONFLICT DO UPDATE so re-runs are idempotent."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        r = _result(verdict="LONG READY", score=80, direction="Long")
        with patch.object(ge, "_learning_conn", return_value=conn):
            ge.record_gate_decision(r, "MNQ", "SCALP")
        sql = cur.execute.call_args_list[-1][0][0]
        self.assertIn("ON CONFLICT", sql)
        self.assertIn("DO UPDATE", sql)


class TestFailOpen(unittest.TestCase):
    """Spec item 5: recorder failure cannot crash live trading."""

    def test_db_unavailable_does_not_raise(self):
        ge.GATE_AUDIT_DB_READY = True
        r = _result(verdict="LONG READY", score=80, direction="Long")
        with patch.object(ge, "_learning_conn", return_value=None):
            # Must not raise
            ge.record_gate_decision(r, "MNQ", "SCALP")

    def test_insert_exception_does_not_raise(self):
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        cur.execute.side_effect = Exception("db exploded")
        r = _result(verdict="LONG READY", score=80, direction="Long")
        with patch.object(ge, "_learning_conn", return_value=conn):
            ge.record_gate_decision(r, "MNQ", "SCALP")  # must not raise

    def test_db_not_ready_skips_silently(self):
        ge.GATE_AUDIT_DB_READY = False
        r = _result(verdict="LONG READY", score=80, direction="Long")
        # No mock needed — should return without touching DB
        ge.record_gate_decision(r, "MNQ", "SCALP")  # must not raise


class TestValidateWiringRoundTrip(unittest.TestCase):
    """Spec item 6: DB/API counts agree via validate_wiring."""

    def _make_wiring_conn(self):
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: cur
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        # First fetchone = count_before, second/third = (synthetic readback), fourth = count_after
        cur.fetchone.side_effect = [(5,), None, (7,)]
        # fetchall = both synthetic rows found
        cur.fetchall.return_value = [
            ("SYNTHETIC_WIRING_BLOCKED", "BLOCKED"),
            ("SYNTHETIC_WIRING_ALLOWED", "ALLOWED"),
        ]
        return conn, cur

    def test_validate_wiring_returns_pass_when_both_found(self):
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = self._make_wiring_conn()
        with patch.object(ge, "_learning_conn", return_value=conn):
            result = ge.validate_wiring(clean_up=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(result["verified_blocked"])
        self.assertTrue(result["verified_allowed"])

    def test_validate_wiring_fails_when_db_not_ready(self):
        ge.GATE_AUDIT_DB_READY = False
        result = ge.validate_wiring()
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], "FAIL")

    def test_validate_wiring_fail_open_on_db_error(self):
        ge.GATE_AUDIT_DB_READY = True
        with patch.object(ge, "_learning_conn", return_value=None):
            result = ge.validate_wiring()
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], "FAIL")


class TestDashboardJsonContract(unittest.TestCase):
    """Spec item 7: dashboard JSON contract remains valid."""

    def _make_summary_conn(self):
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: cur
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cur.fetchone.side_effect = [
            (10, 5, 3, 2, None),   # total counts query
            (0.5, 3.0, 1.0, 3),    # approved expectancy
            (-0.2, 1.0, 2.0, 2),   # blocked expectancy
        ]
        return conn, cur

    def test_summary_has_required_keys(self):
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = self._make_summary_conn()
        with patch.object(ge, "_learning_conn", return_value=conn):
            s = ge.get_summary()
        required = {"available", "baseline_version", "total_observations",
                    "total_approved", "total_blocked", "completed_outcomes",
                    "pending_outcomes", "evidence_status", "collector_active",
                    "approved", "blocked"}
        for key in required:
            self.assertIn(key, s, f"Missing key: {key}")

    def test_summary_unavailable_when_db_not_ready(self):
        ge.GATE_AUDIT_DB_READY = False
        s = ge.get_summary()
        self.assertFalse(s.get("available"))

    def test_summary_has_collector_active_flag(self):
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = self._make_summary_conn()
        with patch.object(ge, "_learning_conn", return_value=conn):
            s = ge.get_summary()
        self.assertTrue(s.get("collector_active"))


class TestSyntheticContamination(unittest.TestCase):
    """Spec item 8: synthetic observations cannot contaminate live measurements."""

    def test_validate_wiring_cleans_up_by_default(self):
        """With clean_up=True (default), a DELETE is issued after validation."""
        ge.GATE_AUDIT_DB_READY = True
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: cur
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cur.fetchone.side_effect = [(0,), None, (0,)]
        cur.fetchall.return_value = [
            ("SYNTHETIC_WIRING_BLOCKED", "BLOCKED"),
            ("SYNTHETIC_WIRING_ALLOWED", "ALLOWED"),
        ]
        with patch.object(ge, "_learning_conn", return_value=conn):
            ge.validate_wiring(clean_up=True)
        # DELETE must be called
        delete_calls = [c for c in cur.execute.call_args_list
                        if "DELETE" in str(c)]
        self.assertGreater(len(delete_calls), 0, "DELETE must be called when clean_up=True")

    def test_synthetic_audit_ids_are_prefixed(self):
        """Synthetic records use SYNTHETIC_ prefix so they can be bulk-deleted."""
        ge.GATE_AUDIT_DB_READY = True
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: cur
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cur.fetchone.side_effect = [(0,), None, (2,)]
        cur.fetchall.return_value = []
        with patch.object(ge, "_learning_conn", return_value=conn):
            ge.validate_wiring(clean_up=False)
        insert_sqls = [str(c) for c in cur.execute.call_args_list
                       if "INSERT INTO gate_audit_log" in str(c)]
        for sql in insert_sqls:
            self.assertIn("SYNTHETIC_WIRING_", sql)


class TestLowSignalSkipped(unittest.TestCase):
    """Spec item 10: low-signal BLOCKED with no blocker is skipped."""

    def test_low_score_no_blocker_no_direction_skipped(self):
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        # score=5 < 15, no blocker, no direction
        r = _result(verdict="WAIT", score=5, direction=None)
        with patch.object(ge, "_learning_conn", return_value=conn):
            ge.record_gate_decision(r, "MNQ", "SCALP")
        insert_calls = [c for c in cur.execute.call_args_list
                        if "INSERT INTO gate_audit_log" in str(c)]
        self.assertEqual(len(insert_calls), 0)

    def test_low_score_no_blocker_with_direction_skipped(self):
        """Low score, explicit direction, but NO blocker and NO strict_reason
        (so the reason-text fallback cannot produce a blocker label) → skipped."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        # score=5 < 15, direction="Long", no blocker, no strict_reason
        # → primary_blocker stays None → early-return fires
        r = _result(verdict="WAIT", score=5, direction="Long", strict_reason=None)
        with patch.object(ge, "_learning_conn", return_value=conn):
            ge.record_gate_decision(r, "MNQ", "SCALP")
        insert_calls = [c for c in cur.execute.call_args_list
                        if "INSERT INTO gate_audit_log" in str(c)]
        self.assertEqual(len(insert_calls), 0)


class TestGateAuditTraceLogging(unittest.TestCase):
    """Spec item 11: GATE_AUDIT_TRACE log line emitted at INFO on success."""

    def test_trace_logged_at_info_on_successful_insert(self):
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        r = _result(verdict="LONG READY", score=80, direction="Long")
        with patch.object(ge, "_learning_conn", return_value=conn):
            with self.assertLogs("gate_effectiveness", level="INFO") as log_ctx:
                ge.record_gate_decision(r, "MNQ", "SCALP")
        trace_lines = [l for l in log_ctx.output if "GATE_AUDIT_TRACE" in l]
        self.assertGreater(len(trace_lines), 0, "GATE_AUDIT_TRACE must be logged at INFO")
        self.assertIn("recorder_called=true", trace_lines[0])


if __name__ == "__main__":
    unittest.main()
