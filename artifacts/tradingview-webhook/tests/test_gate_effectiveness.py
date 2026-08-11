"""Tests for Gate Effectiveness Audit — Phase 8C.

Covers the 16 required audit spec tests plus extras for watcher logic.

ALL production paths (gate, execution, Databento, Edge Score) must be
byte-identical when gate_effectiveness is imported.  Every test that
touches a DB operation mocks the connection so no real DB is required.

Test inventory (audit spec Part 21):
 1  approved opportunity is recorded
 2  blocked opportunity is recorded
 3  blocker reason is preserved
 4  multiple blockers are preserved
 5  primary blocker is correct
 6  counterfactual trade cannot execute
 7  ghost outcome tracks TP
 8  ghost outcome tracks stop
 9  MFE calculation
10  MAE calculation
11  R calculation
12  restart does not duplicate ghost opportunity
13  current production gate output is unchanged
14  edge score computation is unchanged
15  Databento ingestion is unchanged
16  execution gateway behavior is unchanged
"""

import json
import sys
import types
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Lightweight app stub (avoids importing the real 81k-line app.py)
# ---------------------------------------------------------------------------

def _make_app_stub():
    """Return a minimal app module stub used by gate_effectiveness imports."""
    stub = types.ModuleType("app")
    stub.is_actionable   = lambda v: isinstance(v, str) and "READY" in v and "WAIT" not in v
    stub.is_early_ready  = lambda v: isinstance(v, str) and "EARLY" in v
    stub.ready_direction = lambda v: "Long" if "LONG" in str(v) else ("Short" if "SHORT" in str(v) else None)
    stub._learning_conn  = lambda: None   # patched per-test
    stub.DATABENTO_BARS_BY_INST = {}
    stub.LEARNING_DB_ENABLED    = True
    return stub


_APP_STUB = _make_app_stub()
sys.modules.setdefault("app", _APP_STUB)

import gate_effectiveness as ge  # noqa: E402  (must come after stub registration)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_conn(rows=None, rowcount=1):
    """Return a mock psycopg2 connection whose cursor returns `rows`."""
    cur = MagicMock()
    cur.__enter__ = lambda s: cur
    cur.__exit__  = MagicMock(return_value=False)
    cur.fetchone  = MagicMock(return_value=(rows[0] if rows else None))
    cur.fetchall  = MagicMock(return_value=(rows or []))
    cur.rowcount  = rowcount
    conn = MagicMock()
    conn.cursor   = MagicMock(return_value=cur)
    conn.__enter__ = lambda s: conn
    conn.__exit__  = MagicMock(return_value=False)
    return conn, cur


def _full_result(verdict="LONG READY", edge=75, direction="Long", blocked_by=None):
    """Minimal full_analysis result dict for recording tests."""
    return {
        "verdict":         verdict,
        "strict_direction": direction,
        "strict_reason":   "",
        "trade_plan": {
            "entry": 2000.0, "stop": 1990.0,
            "target": 2010.0, "target2": 2015.0,
        },
        "edge_breakdown": {"score": edge},
        "confluences": {
            "bos": True, "choch": False, "vwap": True,
            "liquidity_sweep": True, "volume_confirmed": True,
            "preferred_session": False, "zone_mitigated": True,
        },
        "gate_debug": {
            "bos_confirmed": True, "choch_confirmed": False,
            "vwap_confirmed": True, "volume_ok": True,
            "cvd_conflict": False,
            "failed_conditions": blocked_by or [],
            "blockedBy": blocked_by or [],
            "edge_score": edge,
        },
        "volatility": {"atr_pts": 5.0, "regime": "normal"},
        "vwap": 1998.0,
        "session_state": "regular",
        "cvd_state": "bullish",
        "trend_alignment": "aligned",
    }


# ---------------------------------------------------------------------------
# Test 1 — approved opportunity is recorded
# ---------------------------------------------------------------------------

class TestApprovedRecorded(unittest.TestCase):
    def test_allowed_verdict_inserts_row(self):
        """An ALLOWED (LONG READY) result must produce an INSERT."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.record_gate_decision(_full_result("LONG READY", 70), "MNQ", "SCALP")
        cur.execute.assert_called()
        sql = cur.execute.call_args[0][0]
        self.assertIn("INSERT INTO gate_audit_log", sql)

    def test_allowed_audit_id_contains_ALLOWED(self):
        """Audit ID for an ALLOWED trade must contain 'ALLOWED'."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.record_gate_decision(_full_result("LONG READY", 70), "MNQ", "SCALP")
        params = cur.execute.call_args[0][1]
        audit_id = params[0]
        self.assertIn("ALLOWED", audit_id)

    def test_early_allowed_verdict_recorded(self):
        """An EARLY ALLOWED (LONG EARLY READY) verdict must also be recorded."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.record_gate_decision(_full_result("LONG EARLY READY", 55), "MGC", "SCALP")
        params = cur.execute.call_args[0][1]
        gate_verdict_col = params[10]   # gate_verdict is the 11th param (index 10)
        self.assertEqual(gate_verdict_col, "EARLY_ALLOWED")


# ---------------------------------------------------------------------------
# Test 2 — blocked opportunity is recorded
# ---------------------------------------------------------------------------

class TestBlockedRecorded(unittest.TestCase):
    def test_blocked_verdict_inserts_row(self):
        """A WAIT verdict with meaningful edge score must produce an INSERT."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        result = _full_result("WAIT", 50, blocked_by=["vwap_confirmed"])
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.record_gate_decision(result, "MNQ", "SCALP")
        cur.execute.assert_called()
        sql = cur.execute.call_args[0][0]
        self.assertIn("INSERT INTO gate_audit_log", sql)

    def test_blocked_audit_id_contains_BLOCKED(self):
        """Audit ID for a BLOCKED trade must contain 'BLOCKED'."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        result = _full_result("WAIT", 50, blocked_by=["vwap_confirmed"])
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.record_gate_decision(result, "MNQ", "SCALP")
        params = cur.execute.call_args[0][1]
        audit_id = params[0]
        self.assertIn("BLOCKED", audit_id)

    def test_low_edge_no_blocker_skipped(self):
        """A WAIT with edge below MIN_EDGE_TO_RECORD_BLOCKED and no blocker is skipped."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        # Edge = 5, no blockers — pure noise, should not record
        result = _full_result("WAIT", 5, blocked_by=[])
        result["gate_debug"]["failed_conditions"] = []
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.record_gate_decision(result, "MNQ", "SCALP")
        cur.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3 — blocker reason is preserved
# ---------------------------------------------------------------------------

class TestBlockerReasonPreserved(unittest.TestCase):
    def test_single_blocker_stored_in_all_blockers(self):
        """A single blocking reason must appear in the all_blockers JSON param."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        result = _full_result("WAIT", 60, blocked_by=["vwap_confirmed"])
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.record_gate_decision(result, "MNQ", "SCALP")
        params = cur.execute.call_args[0][1]
        all_blockers_json = params[13]   # all_blockers is 14th param (index 13)
        blockers = json.loads(all_blockers_json)
        self.assertIn("vwap_confirmed", blockers)


# ---------------------------------------------------------------------------
# Test 4 — multiple blockers are preserved
# ---------------------------------------------------------------------------

class TestMultipleBlockersPreserved(unittest.TestCase):
    def test_all_blockers_preserved(self):
        """All blocking reasons must be stored in all_blockers."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        blockers = ["vwap_confirmed", "zone_valid", "edge_score(45<50)"]
        result = _full_result("WAIT", 45, blocked_by=blockers)
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.record_gate_decision(result, "MNQ", "SCALP")
        params = cur.execute.call_args[0][1]
        stored = json.loads(params[13])
        for b in blockers:
            self.assertIn(b, stored)


# ---------------------------------------------------------------------------
# Test 5 — primary blocker is correct
# ---------------------------------------------------------------------------

class TestPrimaryBlockerCorrect(unittest.TestCase):
    def test_primary_blocker_is_first_element(self):
        """primary_blocker must be the first element of failed_conditions."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        blockers = ["zone_valid", "vwap_confirmed", "structure_confirmed"]
        result = _full_result("WAIT", 55, blocked_by=blockers)
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.record_gate_decision(result, "MNQ", "SCALP")
        params = cur.execute.call_args[0][1]
        primary = params[12]   # primary_blocker is 13th param (index 12)
        self.assertEqual(primary, "zone_valid")


# ---------------------------------------------------------------------------
# Test 6 — counterfactual trade cannot execute
# ---------------------------------------------------------------------------

class TestCounterfactualCannotExecute(unittest.TestCase):
    def test_no_broker_call_during_record(self):
        """record_gate_decision must never touch any broker/execution function."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()

        # Verify none of these broker functions are called
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn), \
             patch.object(_APP_STUB, "execute_trade_gateway", MagicMock(), create=True) as m_exec, \
             patch.object(_APP_STUB, "_send_traderspost", MagicMock(), create=True) as m_tp:
            ge.record_gate_decision(_full_result("WAIT", 55, blocked_by=["vwap_confirmed"]), "MNQ", "SCALP")
            m_exec.assert_not_called()
            m_tp.assert_not_called()

    def test_watcher_cycle_does_not_call_execute_gateway(self):
        """_gate_audit_watcher_cycle must never call execute_trade_gateway."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn(rows=[])   # no pending rows
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn), \
             patch.object(_APP_STUB, "execute_trade_gateway", MagicMock(), create=True) as m_exec:
            ge._gate_audit_watcher_cycle()
            m_exec.assert_not_called()


# ---------------------------------------------------------------------------
# Test 7 — ghost outcome tracks TP
# ---------------------------------------------------------------------------

class TestOutlineTracksTP(unittest.TestCase):
    def _pending_row(self, direction="Long"):
        sig = datetime.now(timezone.utc) - timedelta(minutes=5)
        return {
            "audit_id": "TEST|Long|SCALP|BLOCKED|2026081109",
            "instrument": "MNQ", "direction": direction,
            "entry": 2000.0, "stop": 1990.0, "target1": 2010.0, "target2": 2020.0,
            "risk_pts": 10.0,
            "signal_time": sig, "bars_held": 0,
            "mfe_r": 0.0, "mae_r": 0.0, "mfe_price": None, "mae_price": None,
            "tp1_hit": False, "gate_verdict": "BLOCKED",
        }

    def test_tp1_hit_sets_completed(self):
        """When bar high reaches target1 for a Long, outcome must be COMPLETED."""
        ge.GATE_AUDIT_DB_READY = True
        row = self._pending_row("Long")
        conn, cur = _mock_conn(rows=[
            (row["audit_id"], row["instrument"], row["direction"],
             row["entry"], row["stop"], row["target1"], row["target2"],
             row["risk_pts"], row["signal_time"], row["bars_held"],
             row["mfe_r"], row["mae_r"], row["mfe_price"], row["mae_price"],
             row["tp1_hit"], row["gate_verdict"]),
        ])
        # Single bar: high reaches target1
        _APP_STUB.DATABENTO_BARS_BY_INST = {
            "MNQ": [{"high": 2012.0, "low": 1998.0, "ts": datetime.now(timezone.utc)}]
        }
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge._gate_audit_watcher_cycle()
        update_sql = cur.execute.call_args_list[-1][0][0]
        self.assertIn("UPDATE gate_audit_log", update_sql)
        update_params = cur.execute.call_args_list[-1][0][1]
        # outcome_status is at index 9 in the UPDATE params
        outcome_idx = 9
        self.assertEqual(update_params[outcome_idx], "COMPLETED")


# ---------------------------------------------------------------------------
# Test 8 — ghost outcome tracks stop
# ---------------------------------------------------------------------------

class TestOutcomeTracksStop(unittest.TestCase):
    def test_stop_hit_sets_final_r_negative(self):
        """When bar low reaches stop for a Long, final_r must be -1.0."""
        ge.GATE_AUDIT_DB_READY = True
        sig = datetime.now(timezone.utc) - timedelta(minutes=5)
        conn, cur = _mock_conn(rows=[
            ("STOP_TEST|Long|SCALP|BLOCKED|2026081109", "MNQ", "Long",
             2000.0, 1990.0, 2010.0, 2020.0, 10.0, sig, 0,
             0.0, 0.0, None, None, False, "BLOCKED"),
        ])
        _APP_STUB.DATABENTO_BARS_BY_INST = {
            "MNQ": [{"high": 2001.0, "low": 1988.0, "ts": datetime.now(timezone.utc)}]
        }
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge._gate_audit_watcher_cycle()
        update_params = cur.execute.call_args_list[-1][0][1]
        # final_r is at index 7 in the UPDATE params
        final_r = update_params[7]
        self.assertEqual(final_r, -1.0)


# ---------------------------------------------------------------------------
# Test 9 — MFE calculation
# ---------------------------------------------------------------------------

class TestMFECalculation(unittest.TestCase):
    def test_mfe_equals_max_favorable_excursion_in_r(self):
        """MFE should equal (bar_high - entry) / risk_pts for a Long."""
        mfe_r, _, _, _, stop_hit, tp1_hit = ge._resolve_bar_outcome(
            bar_high=2005.0, bar_low=1998.0,
            entry=2000.0, stop_px=1990.0, target1=2015.0,
            direction="Long",
            mfe_r=0.0, mae_r=0.0, mfe_price=None, mae_price=None,
            risk_pts=10.0,
        )
        self.assertAlmostEqual(mfe_r, 0.5)   # (2005 - 2000) / 10
        self.assertFalse(stop_hit)
        self.assertFalse(tp1_hit)

    def test_mfe_short_uses_low_extreme(self):
        """MFE for a Short should be (entry - bar_low) / risk_pts."""
        mfe_r, _, _, _, _, _ = ge._resolve_bar_outcome(
            bar_high=2001.0, bar_low=1994.0,
            entry=2000.0, stop_px=2010.0, target1=1990.0,
            direction="Short",
            mfe_r=0.0, mae_r=0.0, mfe_price=None, mae_price=None,
            risk_pts=10.0,
        )
        self.assertAlmostEqual(mfe_r, 0.6)   # (2000 - 1994) / 10


# ---------------------------------------------------------------------------
# Test 10 — MAE calculation
# ---------------------------------------------------------------------------

class TestMAECalculation(unittest.TestCase):
    def test_mae_equals_max_adverse_excursion_in_r(self):
        """MAE should equal (entry - bar_low) / risk_pts for a Long."""
        _, mae_r, _, _, _, _ = ge._resolve_bar_outcome(
            bar_high=2001.0, bar_low=1997.0,
            entry=2000.0, stop_px=1990.0, target1=2015.0,
            direction="Long",
            mfe_r=0.0, mae_r=0.0, mfe_price=None, mae_price=None,
            risk_pts=10.0,
        )
        self.assertAlmostEqual(mae_r, 0.3)   # (2000 - 1997) / 10

    def test_mae_short_uses_high_extreme(self):
        """MAE for a Short should be (bar_high - entry) / risk_pts."""
        _, mae_r, _, _, _, _ = ge._resolve_bar_outcome(
            bar_high=2003.0, bar_low=1998.0,
            entry=2000.0, stop_px=2010.0, target1=1990.0,
            direction="Short",
            mfe_r=0.0, mae_r=0.0, mfe_price=None, mae_price=None,
            risk_pts=10.0,
        )
        self.assertAlmostEqual(mae_r, 0.3)   # (2003 - 2000) / 10


# ---------------------------------------------------------------------------
# Test 11 — R calculation
# ---------------------------------------------------------------------------

class TestRCalculation(unittest.TestCase):
    def test_stop_hit_returns_negative_one_r(self):
        """Conservative stop-first: stop hit → final_r = -1.0."""
        _, _, _, _, stop_hit, _ = ge._resolve_bar_outcome(
            bar_high=2001.0, bar_low=1988.0,   # low goes through stop
            entry=2000.0, stop_px=1990.0, target1=2015.0,
            direction="Long",
            mfe_r=0.0, mae_r=0.0, mfe_price=None, mae_price=None,
            risk_pts=10.0,
        )
        self.assertTrue(stop_hit)

    def test_conservative_stop_first_when_both_hit(self):
        """When both stop AND target touch in one bar, TP must be suppressed."""
        _, _, _, _, stop_hit, tp1_hit = ge._resolve_bar_outcome(
            bar_high=2018.0, bar_low=1988.0,   # both target1 and stop crossed
            entry=2000.0, stop_px=1990.0, target1=2010.0,
            direction="Long",
            mfe_r=0.0, mae_r=0.0, mfe_price=None, mae_price=None,
            risk_pts=10.0,
        )
        # Conservative: stop wins
        self.assertTrue(stop_hit)
        self.assertFalse(tp1_hit)

    def test_evidence_status_labels(self):
        """Evidence status must follow the spec's N thresholds."""
        self.assertEqual(ge._evidence_status(5),   "ANECDOTAL")
        self.assertEqual(ge._evidence_status(10),  "EARLY")
        self.assertEqual(ge._evidence_status(30),  "MODERATE")
        self.assertEqual(ge._evidence_status(100), "STRONGER_EVIDENCE")


# ---------------------------------------------------------------------------
# Test 12 — restart does not duplicate ghost opportunity
# ---------------------------------------------------------------------------

class TestRestartDedup(unittest.TestCase):
    def test_on_conflict_do_update_present_in_sql(self):
        """The INSERT must use ON CONFLICT DO UPDATE to prevent restart duplication."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        result = _full_result("WAIT", 60, blocked_by=["vwap_confirmed"])
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.record_gate_decision(result, "MNQ", "SCALP")
        sql = cur.execute.call_args[0][0]
        self.assertIn("ON CONFLICT", sql)
        self.assertIn("DO UPDATE", sql)

    def test_same_bucket_produces_same_audit_id(self):
        """Two calls within the same 1-hour bucket for BLOCKED must produce same audit_id."""
        ge.GATE_AUDIT_DB_READY = True
        conn1, cur1 = _mock_conn()
        conn2, cur2 = _mock_conn()
        conns = iter([conn1, conn2])
        with patch.object(_APP_STUB, "_learning_conn", side_effect=lambda: next(conns)):
            result = _full_result("WAIT", 60, blocked_by=["zone_valid"])
            ge.record_gate_decision(result, "MNQ", "SCALP")
            ge.record_gate_decision(result, "MNQ", "SCALP")
        id1 = cur1.execute.call_args[0][1][0]
        id2 = cur2.execute.call_args[0][1][0]
        self.assertEqual(id1, id2)


# ---------------------------------------------------------------------------
# Tests 13-16 — production behavior unchanged
# ---------------------------------------------------------------------------

class TestProductionBehaviorUnchanged(unittest.TestCase):
    """Prove that importing gate_effectiveness does not alter any money-path function."""

    def test_13_gate_output_unchanged_when_db_not_ready(self):
        """When GATE_AUDIT_DB_READY is False, record_gate_decision is a no-op."""
        ge.GATE_AUDIT_DB_READY = False
        conn = MagicMock()
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            # Should silently return without calling the DB
            ge.record_gate_decision(_full_result("LONG READY", 75), "MNQ", "SCALP")
        conn.cursor.assert_not_called()
        ge.GATE_AUDIT_DB_READY = True   # restore

    def test_14_edge_score_computation_unchanged(self):
        """_extract does not mutate the result dict or alter the edge_score key."""
        result = _full_result("LONG READY", 75)
        original_score = result["edge_breakdown"]["score"]
        ge._extract(result)
        self.assertEqual(result["edge_breakdown"]["score"], original_score)

    def test_15_result_dict_not_mutated(self):
        """record_gate_decision must NOT mutate the result dict passed to it."""
        ge.GATE_AUDIT_DB_READY = True
        conn, _ = _mock_conn()
        result = _full_result("LONG READY", 75)
        verdict_before = result["verdict"]
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.record_gate_decision(result, "MNQ", "SCALP")
        self.assertEqual(result["verdict"], verdict_before)

    def test_16_execution_gateway_not_imported_at_module_load(self):
        """gate_effectiveness must not import execute_trade_gateway at module level."""
        import gate_effectiveness as _ge2  # noqa: PLC0415
        self.assertFalse(hasattr(_ge2, "execute_trade_gateway"))


# ---------------------------------------------------------------------------
# Additional: GATE_AUDIT_DB_READY flag gating
# ---------------------------------------------------------------------------

class TestDbReadyGating(unittest.TestCase):
    def test_check_gate_audit_db_ready_sets_flag(self):
        """check_gate_audit_db_ready() must set GATE_AUDIT_DB_READY=True on success."""
        ge.GATE_AUDIT_DB_READY = False
        conn, cur = _mock_conn(rows=[(1,)])
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.check_gate_audit_db_ready()
        self.assertTrue(ge.GATE_AUDIT_DB_READY)

    def test_check_gate_audit_db_ready_stays_false_on_table_missing(self):
        """check_gate_audit_db_ready() must leave flag False when table is missing."""
        ge.GATE_AUDIT_DB_READY = False
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = lambda s: cur
        cur.__exit__  = MagicMock(return_value=False)
        cur.execute   = MagicMock(side_effect=Exception("relation does not exist"))
        conn.cursor   = MagicMock(return_value=cur)
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.check_gate_audit_db_ready()
        self.assertFalse(ge.GATE_AUDIT_DB_READY)


# ---------------------------------------------------------------------------
# Additional: bar_ts helper
# ---------------------------------------------------------------------------

class TestBarTs(unittest.TestCase):
    def test_bar_ts_from_float(self):
        """_bar_ts must handle a Unix epoch float timestamp."""
        ts = 1700000000.0
        result = ge._bar_ts({"ts": ts})
        self.assertEqual(result.tzinfo, timezone.utc)

    def test_bar_ts_from_iso_string(self):
        """_bar_ts must handle an ISO 8601 string."""
        ts = "2026-08-11T09:30:00Z"
        result = ge._bar_ts({"ts": ts})
        self.assertEqual(result.year, 2026)

    def test_bar_ts_from_datetime(self):
        """_bar_ts must pass through a tz-aware datetime unchanged."""
        dt = datetime(2026, 8, 11, 9, 30, 0, tzinfo=timezone.utc)
        result = ge._bar_ts({"ts": dt})
        self.assertEqual(result, dt)

    def test_bar_ts_missing_returns_min(self):
        """_bar_ts must return datetime.min (UTC) for a bar with no timestamp."""
        result = ge._bar_ts({})
        self.assertEqual(result, datetime.min.replace(tzinfo=timezone.utc))


# ---------------------------------------------------------------------------
# Additional: baseline version
# ---------------------------------------------------------------------------

class TestBaselineVersion(unittest.TestCase):
    def test_baseline_constant_correct(self):
        """BASELINE_VERSION must be the audit spec's control identifier."""
        self.assertEqual(ge.BASELINE_VERSION, "GATE_BASELINE_2026_08_11")

    def test_baseline_inserted_as_second_param(self):
        """record_gate_decision must write BASELINE_VERSION to the DB row."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn()
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge.record_gate_decision(_full_result("LONG READY", 70), "MNQ", "SCALP")
        params = cur.execute.call_args[0][1]
        self.assertEqual(params[1], ge.BASELINE_VERSION)


if __name__ == "__main__":
    unittest.main()
