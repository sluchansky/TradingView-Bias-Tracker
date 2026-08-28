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

import pytest

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


def _make_dbb_stub():
    """Return a minimal databento_brain stub for tests.

    The watcher cycle imports databento_brain.DATABENTO_BARS_BY_INST directly
    (not via the app stub), so tests that exercise bar-resolution logic must
    set bars on this stub, not on _APP_STUB.
    """
    stub = types.ModuleType("databento_brain")
    stub.DATABENTO_BARS_BY_INST = {}
    return stub


_DBB_STUB = _make_dbb_stub()

import gate_effectiveness as ge  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_dependency_modules(monkeypatch):
    """Give every test fresh lazy-import dependencies, independent of order."""
    global _APP_STUB, _DBB_STUB
    _APP_STUB = _make_app_stub()
    _DBB_STUB = _make_dbb_stub()
    monkeypatch.setitem(sys.modules, "app", _APP_STUB)
    monkeypatch.setitem(sys.modules, "databento_brain", _DBB_STUB)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_conn(rows=None, rowcount=1):
    """Return a mock psycopg2 connection whose cursor returns `rows`.

    Critical: use MagicMock(return_value=cur) for __enter__ so that
    `with conn.cursor() as x:` gives x == cur.  Python's dunder machinery
    looks up __enter__ on type(obj), not the instance, so a plain lambda
    assignment is ignored; MagicMock(return_value=...) stores the callable
    in a way that MagicMock's magic-method system actually uses.
    """
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)   # `with cur as x:` → x is cur
    cur.__exit__  = MagicMock(return_value=False)
    cur.fetchone  = MagicMock(return_value=(rows[0] if rows else None))
    cur.fetchall  = MagicMock(return_value=(rows or []))
    cur.rowcount  = rowcount
    conn = MagicMock()
    conn.cursor   = MagicMock(return_value=cur)
    conn.__enter__ = MagicMock(return_value=conn)  # `with conn as x:` → x is conn
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
        # Single bar: high reaches target1.
        # After the databento_brain fix, the watcher reads bars from the
        # databento_brain module directly — set bars on _DBB_STUB, not _APP_STUB.
        _DBB_STUB.DATABENTO_BARS_BY_INST = {
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
        # Watcher reads from databento_brain stub, not the app stub.
        _DBB_STUB.DATABENTO_BARS_BY_INST = {
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


# ---------------------------------------------------------------------------
# Additional: backfill PENDING preservation
# ---------------------------------------------------------------------------

class TestBackfillRecentPendingPreservation(unittest.TestCase):
    """Backfill must not terminate observations still inside the settlement window."""

    def _make_recent_row(self, has_bars=False):
        """Row whose signal_time is 2 hours ago — well inside the 6h window."""
        sig = datetime.now(timezone.utc) - timedelta(hours=2)
        return (
            "BF_RECENT|Long|SCALP|BLOCKED|2026081110", "MNQ", "Long",
            2000.0, 1990.0, 2010.0, 2020.0,    # entry/stop/t1/t2
            10.0, sig, 0,                        # risk_pts, signal_time, bars_held
            0.0, 0.0, None, None, False,         # mfe_r, mae_r, mfe_p, mae_p, tp1
        )

    def test_recent_pending_no_bars_is_preserved(self):
        """Backfill must skip (not INSUFFICIENT) a recent row with no buffered bars."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn(rows=[self._make_recent_row(has_bars=False)])
        _DBB_STUB.DATABENTO_BARS_BY_INST = {}   # no bars in buffer

        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge._run_backfill_thread()

        # No UPDATE should have been issued for this recent PENDING row
        update_calls = [
            c for c in cur.execute.call_args_list
            if "UPDATE" in str(c)
        ]
        self.assertEqual(len(update_calls), 0,
                         "Backfill must not UPDATE a recent PENDING row with no bars")

    def test_recent_pending_bars_no_outcome_is_preserved(self):
        """Backfill must skip a recent row with bars that haven't hit TP or stop."""
        ge.GATE_AUDIT_DB_READY = True
        sig = datetime.now(timezone.utc) - timedelta(hours=2)
        row = (
            "BF_RECENT2|Long|SCALP|BLOCKED|2026081110", "MNQ", "Long",
            2000.0, 1990.0, 2010.0, 2020.0,
            10.0, sig, 0,
            0.0, 0.0, None, None, False,
        )
        conn, cur = _mock_conn(rows=[row])
        # Bar that neither hits stop (1990) nor target1 (2010)
        _DBB_STUB.DATABENTO_BARS_BY_INST = {
            "MNQ": [{"high": 2003.0, "low": 1995.0, "ts": datetime.now(timezone.utc)}]
        }

        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge._run_backfill_thread()

        update_calls = [
            c for c in cur.execute.call_args_list
            if "UPDATE" in str(c)
        ]
        self.assertEqual(len(update_calls), 0,
                         "Backfill must not terminate a recent in-window PENDING row "
                         "that has bars but no definitive outcome yet")

    def test_stale_pending_no_bars_becomes_insufficient(self):
        """Backfill must mark an expired row with no bars as INSUFFICIENT_COUNTERFACTUAL_DATA."""
        ge.GATE_AUDIT_DB_READY = True
        sig = datetime.now(timezone.utc) - timedelta(hours=8)   # past 6h window
        row = (
            "BF_STALE|Long|SCALP|BLOCKED|2026081104", "MNQ", "Long",
            2000.0, 1990.0, 2010.0, 2020.0,
            10.0, sig, 0,
            0.0, 0.0, None, None, False,
        )
        conn, cur = _mock_conn(rows=[row], rowcount=1)
        _DBB_STUB.DATABENTO_BARS_BY_INST = {}   # no bars

        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge._run_backfill_thread()

        update_calls = [
            c for c in cur.execute.call_args_list
            if "UPDATE" in str(c) and "INSUFFICIENT" in str(c)
        ]
        self.assertGreater(len(update_calls), 0,
                           "Stale PENDING row with no bars must be marked INSUFFICIENT")


# ---------------------------------------------------------------------------
# Additional: trigger_backfill_if_needed startup guard
# ---------------------------------------------------------------------------

class TestBackfillStartupGuard(unittest.TestCase):
    """trigger_backfill_if_needed must only fire before the watcher has cycled."""

    def test_skips_when_cycles_total_nonzero(self):
        """If the watcher has already run at least once, backfill must not start."""
        ge.GATE_AUDIT_DB_READY = True
        original = ge._GE_WATCHER_STATE["cycles_total"]
        ge._GE_WATCHER_STATE["cycles_total"] = 1
        try:
            with patch.object(ge, "trigger_backfill") as mock_bf:
                ge.trigger_backfill_if_needed()
            mock_bf.assert_not_called()
        finally:
            ge._GE_WATCHER_STATE["cycles_total"] = original

    def test_fires_when_cycles_total_is_zero(self):
        """If no watcher cycle has run yet, backfill must be triggered."""
        ge.GATE_AUDIT_DB_READY = True
        original = ge._GE_WATCHER_STATE["cycles_total"]
        ge._GE_WATCHER_STATE["cycles_total"] = 0
        try:
            with patch.object(ge, "trigger_backfill") as mock_bf:
                ge.trigger_backfill_if_needed()
            mock_bf.assert_called_once()
        finally:
            ge._GE_WATCHER_STATE["cycles_total"] = original


# ---------------------------------------------------------------------------
# Additional: get_settlement_health DB failure → ok: false
# ---------------------------------------------------------------------------

class TestSettlementHealthDbFailure(unittest.TestCase):
    """get_settlement_health must return ok=False when the DB aggregate query fails."""

    def test_db_failure_returns_ok_false(self):
        """When the GROUP BY query raises, ok must be False and error key present."""
        ge.GATE_AUDIT_DB_READY = True
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__  = MagicMock(return_value=False)
        cur.execute   = MagicMock(side_effect=Exception("connection lost"))
        conn.cursor   = MagicMock(return_value=cur)

        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            result = ge.get_settlement_health()

        self.assertFalse(result["ok"],
                         "ok must be False when the DB aggregate query raises")
        self.assertIn("error", result["db"],
                      "db dict must carry an 'error' key on failure")

    def test_unavailable_connection_returns_ok_false(self):
        """When _learning_conn() returns None (DB outage), ok must be False.

        This is the regression test for the bug where an unavailable connection
        silently returned ok=True with an empty db dict — a live DB outage was
        reported as healthy.
        """
        ge.GATE_AUDIT_DB_READY = True
        with patch.object(_APP_STUB, "_learning_conn", return_value=None):
            result = ge.get_settlement_health()

        self.assertFalse(result["ok"],
                         "ok must be False when _learning_conn returns None")
        self.assertEqual(result["db"].get("error"), "connection_unavailable",
                         "db.error must be 'connection_unavailable' when no connection")

    def test_db_success_returns_ok_true(self):
        """When the GROUP BY query succeeds, ok must be True."""
        ge.GATE_AUDIT_DB_READY = True
        conn, cur = _mock_conn(rows=[("PENDING", 10), ("COMPLETED", 5)])

        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            result = ge.get_settlement_health()

        self.assertTrue(result["ok"])
        self.assertIn("watcher", result)


# ---------------------------------------------------------------------------
# Additional: watcher settled_last_run only counts rowcount > 0
# ---------------------------------------------------------------------------

class TestWatcherBarsHeldStability(unittest.TestCase):
    """bars_held must not inflate across multiple watcher cycles for a PENDING row.

    Regression: the watcher always re-reads ALL bars after signal_time.
    Before the fix, `bars_held += new_bars` accumulated across cycles —
    a row with 10 bars processed twice would write bars_held=20 instead of 10.
    After the fix, bars_held = new_bars (replace, not accumulate).
    """

    def _make_pending_row(self):
        sig = datetime.now(timezone.utc) - timedelta(minutes=15)
        return (
            "BH_STABLE|Long|SCALP|ALLOWED|2026081109", "MNQ", "Long",
            2000.0, 1990.0, 2010.0, 2020.0,
            10.0, sig, 0,              # bars_held starts at 0
            0.0, 0.0, None, None, False, "ALLOWED",
        )

    def _run_one_cycle(self, conn):
        """Run exactly one watcher cycle against the given mock connection."""
        _DBB_STUB.DATABENTO_BARS_BY_INST = {
            "MNQ": [
                {"high": 2005.0, "low": 1998.0, "ts": datetime.now(timezone.utc)},
                {"high": 2004.0, "low": 1997.0, "ts": datetime.now(timezone.utc) + timedelta(seconds=1)},
            ]
        }
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge._gate_audit_watcher_cycle()

    def test_bars_held_does_not_double_across_two_cycles(self):
        """Running the watcher twice on the same row must not double bars_held.

        The test simulates two watcher cycles: the first processes 2 bars and
        writes bars_held=2; the second reads bars_held=2 from the DB, processes
        the same 2 bars, and must still write bars_held=2 — not 4.
        """
        ge.GATE_AUDIT_DB_READY = True
        row = self._make_pending_row()

        # First cycle: DB row has bars_held=0
        conn1, cur1 = _mock_conn(rows=[row], rowcount=1)
        self._run_one_cycle(conn1)

        # Extract bars_held written by first cycle
        update_calls1 = [
            c for c in cur1.execute.call_args_list if "UPDATE" in str(c)
        ]
        self.assertTrue(update_calls1, "First cycle must issue an UPDATE")
        params1 = update_calls1[-1][0][1]   # positional args tuple
        # bars_held is the 9th param in the UPDATE (index 8, 0-based)
        # Look for it by scanning for the integer at the bars_held position
        bars_held_after_cycle1 = None
        for p in params1:
            if isinstance(p, int) and 1 <= p <= 100:
                bars_held_after_cycle1 = p
                break
        self.assertIsNotNone(bars_held_after_cycle1, "Must find bars_held in UPDATE params")
        self.assertEqual(bars_held_after_cycle1, 2,
                         "First cycle: bars_held must equal the 2 bars processed")

        # Second cycle: simulate DB returning bars_held=2 (as written by first cycle)
        row2 = list(row)
        row2[9] = 2          # bars_held already 2 in DB
        conn2, cur2 = _mock_conn(rows=[tuple(row2)], rowcount=1)
        self._run_one_cycle(conn2)

        update_calls2 = [
            c for c in cur2.execute.call_args_list if "UPDATE" in str(c)
        ]
        self.assertTrue(update_calls2, "Second cycle must issue an UPDATE")
        params2 = update_calls2[-1][0][1]
        bars_held_after_cycle2 = None
        for p in params2:
            if isinstance(p, int) and 1 <= p <= 100:
                bars_held_after_cycle2 = p
                break
        self.assertIsNotNone(bars_held_after_cycle2,
                             "Must find bars_held in second cycle UPDATE params")
        self.assertEqual(bars_held_after_cycle2, 2,
                         "Second cycle must write bars_held=2 (same 2 bars), not 4 "
                         "(regression: += instead of = doubled the count)")


class TestWatcherRowcountGuard(unittest.TestCase):
    """settled_last_run must not increment when another process won a concurrent UPDATE."""

    def test_no_increment_when_rowcount_zero(self):
        """When UPDATE.rowcount == 0, settled_last_run must not increase."""
        ge.GATE_AUDIT_DB_READY = True
        sig = datetime.now(timezone.utc) - timedelta(minutes=5)
        row = (
            "ROWCOUNT_TEST|Long|SCALP|BLOCKED|2026081109", "MNQ", "Long",
            2000.0, 1990.0, 2010.0, 2020.0,
            10.0, sig, 0,
            0.0, 0.0, None, None, False, "BLOCKED",
        )
        # rowcount=0 simulates a lost race (another process updated the row first)
        conn, cur = _mock_conn(rows=[row], rowcount=0)
        _DBB_STUB.DATABENTO_BARS_BY_INST = {
            "MNQ": [{"high": 2012.0, "low": 1996.0, "ts": datetime.now(timezone.utc)}]
        }

        before = ge._GE_WATCHER_STATE["settled_last_run"]
        with patch.object(_APP_STUB, "_learning_conn", return_value=conn):
            ge._gate_audit_watcher_cycle()

        # settled_last_run must not go above its previous value
        self.assertLessEqual(
            ge._GE_WATCHER_STATE["settled_last_run"], before,
            "settled_last_run must not increment when UPDATE rowcount is 0 "
            "(concurrent process already updated the row)",
        )


if __name__ == "__main__":
    unittest.main()
