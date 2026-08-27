"""
Native Journal — Phase A tests.

Covers:
  - journal record created from snapshot
  - send paths update the same record
  - lifecycle state transitions
  - management events appended correctly
  - outcome + R calculation from immutable planned columns
  - close by instrument (legacy path linkage)
  - learning eligibility gating
  - immutable planned context never overwritten
  - no duplicate rows on conflict
  - restart recovery (record survives in DB)
  - rejected / status_unknown paths
  - Tradzella enrichment does not overwrite planned context (Phase A stub)
"""

import unittest
import uuid
import json
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, call


# ─── helpers ────────────────────────────────────────────────────────────────

def _make_snapshot(**overrides):
    """Minimal valid snapshot dict."""
    base = {
        "internal_trade_id": str(uuid.uuid4()),
        "signal_id": f"sig-{uuid.uuid4().hex[:8]}",
        "execution_fingerprint": f"fp-{uuid.uuid4().hex[:8]}",
        "instrument": "MNQ",
        "contract": "MNQ1!",
        "mode": "SCALP",
        "direction": "Long",
        "source": "traderspost",
        "canonical_strategy_key": "LIQUIDITY_SWEEP_REVERSAL",
        "strategy_display_name": "Liquidity Sweep Reversal",
        "setup_name": "Sweep Entry",
        "playbook": "sweep_long",
        "thesis_direction": "LONG",
        "thesis_strength": "MODERATE",
        "thesis_alignment": "aligned",
        "edge_score": 72.0,
        "grade": "A",
        "readiness": "READY",
        "actionable": True,
        "confirmations": ["BOS", "VWAP"],
        "blockers": [],
        "opposing_structure": None,
        "risk_state": "normal",
        "planned_entry": 19500.0,
        "planned_stop": 19480.0,
        "planned_targets": [19520.0, 19540.0],
        "planned_risk": 20.0,
        "planned_contracts": 2,
        "broker_order_id": "ord-abc123",
        "broker_signal_id": "tp-xyz789",
        "broker_metadata": {"path": "live"},
        "created_at": "2026-08-02T16:00:00.000Z",
        "sent_at": "2026-08-02T16:00:00.100Z",
    }
    base.update(overrides)
    return base


def _make_paper_snapshot(**overrides):
    s = _make_snapshot(**overrides)
    s["source"] = "paper"
    return s


# ─── test classes ────────────────────────────────────────────────────────────

class TestNJBootProbe(unittest.TestCase):
    """_boot_native_journal_table sets NJ_DB_READY when table exists."""

    def setUp(self):
        import app
        app.NJ_DB_READY = False

    def tearDown(self):
        import app
        app.NJ_DB_READY = False

    def test_sets_ready_when_table_exists(self):
        import app
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        with patch.object(app, "LEARNING_DB_ENABLED", True), \
             patch.object(app, "_learning_conn", return_value=mock_conn):
            app._boot_native_journal_table()
        self.assertTrue(app.NJ_DB_READY)
        mock_cur.execute.assert_called_once_with("SELECT 1 FROM native_journal LIMIT 0")

    def test_stays_false_when_table_missing(self):
        import app
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.execute.side_effect = Exception("relation does not exist")
        mock_conn.cursor.return_value = mock_cur
        with patch.object(app, "LEARNING_DB_ENABLED", True), \
             patch.object(app, "_learning_conn", return_value=mock_conn):
            app._boot_native_journal_table()
        self.assertFalse(app.NJ_DB_READY)

    def test_no_op_when_learning_db_disabled(self):
        import app
        with patch.object(app, "LEARNING_DB_ENABLED", False), \
             patch.object(app, "_learning_conn") as mock_lc:
            app._boot_native_journal_table()
        mock_lc.assert_not_called()
        self.assertFalse(app.NJ_DB_READY)


class TestNJStaleSubmissionClassification(unittest.TestCase):
    """Submission-only rows become inert without invented fills or outcomes."""

    def setUp(self):
        import app
        self.app = app
        self.app.NJ_DB_READY = True

    def tearDown(self):
        self.app.NJ_DB_READY = False

    def test_marks_only_old_rows_without_fill_or_terminal_evidence(self):
        cursor = MagicMock()
        cursor.rowcount = 2
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        with patch.object(self.app, "_learning_conn", return_value=conn):
            self.assertEqual(self.app._nj_classify_stale_submitted_rows(), 2)

        sql = cursor.execute.call_args.args[0]
        params = cursor.execute.call_args.args[1]
        self.assertIn("lifecycle_status = 'STATUS_UNKNOWN'", sql)
        self.assertIn("review_status = 'NEEDS_REVIEW'", sql)
        self.assertIn("outcome IS NULL", sql)
        self.assertIn("? 'avg_entry'", sql)
        self.assertIn("'POSITION_OPENED'", sql)
        self.assertEqual(params[2], self.app._NJ_STALE_SUBMITTED_HOURS)
        self.assertNotIn("actual_exit", params[0])
        self.assertNotIn("net_pnl", params[0])
        self.assertNotIn("realized_r", params[0])
        conn.commit.assert_called_once()


class TestNJCreateFromSnapshot(unittest.TestCase):
    """_nj_create_from_snapshot inserts a SUBMITTED row correctly."""

    def setUp(self):
        import app
        app.NJ_DB_READY = True
        self._conn = MagicMock()
        self._cur = MagicMock()
        self._conn.cursor.return_value = self._cur
        self._patch = patch.object(app, "_learning_conn", return_value=self._conn)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        import app
        app.NJ_DB_READY = False

    def _get_insert_args(self):
        """Return (sql, params) from the cursor.execute INSERT call."""
        import app
        for c in self._cur.execute.call_args_list:
            sql = c[0][0]
            if "INSERT INTO native_journal" in sql:
                return sql, c[0][1]
        return None, None

    def test_creates_row_for_live_snapshot(self):
        import app
        snap = _make_snapshot()
        app._nj_create_from_snapshot(snap)
        sql, params = self._get_insert_args()
        self.assertIsNotNone(sql, "INSERT was not called")
        self.assertIn("ON CONFLICT (internal_trade_id) DO NOTHING", sql)
        # First param is internal_trade_id
        self.assertEqual(params[0], snap["internal_trade_id"])
        # lifecycle_status is SUBMITTED (index 5 after the 4 identity fields)
        self.assertEqual(params[5], "SUBMITTED")
        # source_label is SYSTEM_AUTO for live source
        self.assertEqual(params[6], "SYSTEM_AUTO")

    def test_creates_row_for_paper_snapshot(self):
        import app
        snap = _make_paper_snapshot()
        app._nj_create_from_snapshot(snap)
        _, params = self._get_insert_args()
        self.assertIsNotNone(params)
        self.assertEqual(params[6], "PAPER")

    def test_paper_execution_mode_is_labeled_paper_for_auto_origin(self):
        """`source=auto` identifies the initiator; `mode=paper` is the safety mode."""
        import app
        snap = _make_snapshot(mode="paper", source="auto",
                              broker_order_id=None, broker_signal_id=None)
        app._nj_create_from_snapshot(snap)
        _, params = self._get_insert_args()
        self.assertIsNotNone(params)
        self.assertEqual(params[6], "PAPER")

    def test_no_op_when_nj_db_not_ready(self):
        import app
        app.NJ_DB_READY = False
        snap = _make_snapshot()
        app._nj_create_from_snapshot(snap)
        self._cur.execute.assert_not_called()

    def test_no_op_when_no_internal_trade_id(self):
        import app
        snap = _make_snapshot()
        del snap["internal_trade_id"]
        app._nj_create_from_snapshot(snap)
        self._cur.execute.assert_not_called()

    def test_fail_open_on_db_error(self):
        import app
        self._cur.execute.side_effect = Exception("DB error")
        snap = _make_snapshot()
        # Must not raise
        app._nj_create_from_snapshot(snap)

    def test_idempotent_on_conflict_do_nothing(self):
        import app
        snap = _make_snapshot()
        app._nj_create_from_snapshot(snap)
        sql, _ = self._get_insert_args()
        self.assertIn("DO NOTHING", sql)

    def test_immutable_planned_fields_set(self):
        import app
        snap = _make_snapshot(
            canonical_strategy_key="ORB_BREAKOUT",
            edge_score=85.0,
            planned_entry=19500.0,
            planned_stop=19470.0,
            planned_risk=30.0,
        )
        app._nj_create_from_snapshot(snap)
        _, params = self._get_insert_args()
        self.assertIsNotNone(params)
        # canonical_strategy_key is at index 11
        self.assertEqual(params[11], "ORB_BREAKOUT")

    def test_execution_block_contains_submission_time(self):
        import app
        import psycopg2.extras
        snap = _make_snapshot(sent_at="2026-08-02T16:00:00.100Z")
        app._nj_create_from_snapshot(snap)
        _, params = self._get_insert_args()
        # last param is execution (psycopg2.extras.Json wrapper)
        exec_json = params[-1]
        if hasattr(exec_json, 'adapted'):
            exec_data = exec_json.adapted
        else:
            exec_data = exec_json
        if isinstance(exec_data, str):
            exec_data = json.loads(exec_data)
        self.assertIn("submission_time", exec_data)

    def test_conn_closed_on_success(self):
        import app
        app._nj_create_from_snapshot(_make_snapshot())
        self._conn.close.assert_called()

    def test_conn_closed_on_error(self):
        import app
        self._cur.execute.side_effect = Exception("fail")
        app._nj_create_from_snapshot(_make_snapshot())
        self._conn.close.assert_called()


class TestNJUpdateLifecycle(unittest.TestCase):
    """_nj_update_lifecycle updates status and appends a management event."""

    def setUp(self):
        import app
        app.NJ_DB_READY = True
        self._conn = MagicMock()
        self._cur = MagicMock()
        self._conn.cursor.return_value = self._cur
        self._patch = patch.object(app, "_learning_conn", return_value=self._conn)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        import app
        app.NJ_DB_READY = False

    def _update_sql_args(self):
        for c in self._cur.execute.call_args_list:
            sql = c[0][0]
            if "UPDATE native_journal" in sql and "lifecycle_status" in sql:
                return sql, c[0][1]
        return None, None

    def test_updates_status_to_active(self):
        import app
        iid = str(uuid.uuid4())
        app._nj_update_lifecycle(iid, "ACTIVE")
        sql, params = self._update_sql_args()
        self.assertIsNotNone(sql)
        self.assertEqual(params[0], "ACTIVE")

    def test_appends_management_event(self):
        import app
        iid = str(uuid.uuid4())
        app._nj_update_lifecycle(iid, "ACKNOWLEDGED")
        sql, params = self._update_sql_args()
        # params[1] is the JSON array of events
        events = json.loads(params[1])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["new_value"]["lifecycle_status"], "ACKNOWLEDGED")

    def test_ignores_unknown_status(self):
        import app
        iid = str(uuid.uuid4())
        app._nj_update_lifecycle(iid, "BOGUS_STATE")
        self._cur.execute.assert_not_called()

    def test_no_op_when_not_ready(self):
        import app
        app.NJ_DB_READY = False
        app._nj_update_lifecycle(str(uuid.uuid4()), "ACTIVE")
        self._cur.execute.assert_not_called()

    def test_fail_open_on_db_error(self):
        import app
        self._cur.execute.side_effect = Exception("DB error")
        app._nj_update_lifecycle(str(uuid.uuid4()), "ACTIVE")  # must not raise

    def test_event_includes_timestamp(self):
        import app
        iid = str(uuid.uuid4())
        app._nj_update_lifecycle(iid, "ACTIVE")
        _, params = self._update_sql_args()
        events = json.loads(params[1])
        self.assertIn("timestamp", events[0])
        self.assertTrue(events[0]["timestamp"].endswith("Z"))

    def test_automated_true_for_system_source(self):
        import app
        iid = str(uuid.uuid4())
        app._nj_update_lifecycle(iid, "ACTIVE", source="system_auto")
        _, params = self._update_sql_args()
        events = json.loads(params[1])
        self.assertTrue(events[0]["automated"])

    def test_custom_event_type_preserved(self):
        import app
        iid = str(uuid.uuid4())
        app._nj_update_lifecycle(iid, "PARTIALLY_CLOSED", event_type="PARTIAL_EXIT")
        _, params = self._update_sql_args()
        events = json.loads(params[1])
        self.assertEqual(events[0]["event_type"], "PARTIAL_EXIT")


class TestNJUpdateExecution(unittest.TestCase):
    """_nj_update_execution merges patch into the execution JSONB column."""

    def setUp(self):
        import app
        app.NJ_DB_READY = True
        self._conn = MagicMock()
        self._cur = MagicMock()
        self._conn.cursor.return_value = self._cur
        self._patch = patch.object(app, "_learning_conn", return_value=self._conn)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        import app
        app.NJ_DB_READY = False

    def test_merges_avg_entry(self):
        import app
        iid = str(uuid.uuid4())
        app._nj_update_execution(iid, {"avg_entry": 19502.5, "actual_qty": 2})
        sql, params = self._cur.execute.call_args[0]
        self.assertIn("COALESCE(execution", sql)
        patch_data = json.loads(params[0])
        self.assertEqual(patch_data["avg_entry"], 19502.5)

    def test_no_op_on_empty_patch(self):
        import app
        iid = str(uuid.uuid4())
        app._nj_update_execution(iid, {})
        # Still calls execute because {} is technically a valid merge
        # (no-op on the DB side; we allow it)

    def test_no_op_when_not_ready(self):
        import app
        app.NJ_DB_READY = False
        app._nj_update_execution(str(uuid.uuid4()), {"avg_entry": 100.0})
        self._cur.execute.assert_not_called()

    def test_fail_open_on_db_error(self):
        import app
        self._cur.execute.side_effect = Exception("DB error")
        app._nj_update_execution(str(uuid.uuid4()), {"avg_entry": 100.0})  # must not raise


class TestNJAppendManagementEvent(unittest.TestCase):
    """_nj_append_management_event appends one event to the array."""

    def setUp(self):
        import app
        app.NJ_DB_READY = True
        self._conn = MagicMock()
        self._cur = MagicMock()
        self._conn.cursor.return_value = self._cur
        self._patch = patch.object(app, "_learning_conn", return_value=self._conn)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        import app
        app.NJ_DB_READY = False

    def _event_params(self):
        for c in self._cur.execute.call_args_list:
            sql = c[0][0]
            if "UPDATE native_journal" in sql and "management_events" in sql:
                return c[0][1]
        return None

    def test_stop_moved_event(self):
        import app
        iid = str(uuid.uuid4())
        app._nj_append_management_event(
            iid, "STOP_MOVED",
            old_value={"stop": 19480.0},
            new_value={"stop": 19490.0},
            reason="break_even",
        )
        params = self._event_params()
        self.assertIsNotNone(params)
        events = json.loads(params[0])
        self.assertEqual(events[0]["event_type"], "STOP_MOVED")
        self.assertEqual(events[0]["old_value"]["stop"], 19480.0)
        self.assertEqual(events[0]["new_value"]["stop"], 19490.0)
        self.assertEqual(events[0]["reason"], "break_even")

    def test_manual_exit_event_with_operator(self):
        import app
        iid = str(uuid.uuid4())
        app._nj_append_management_event(
            iid, "MANUAL_EXIT",
            automated=False,
            operator_id="operator",
            reason="emergency",
        )
        params = self._event_params()
        events = json.loads(params[0])
        self.assertFalse(events[0]["automated"])
        self.assertEqual(events[0]["operator_id"], "operator")

    def test_no_op_when_not_ready(self):
        import app
        app.NJ_DB_READY = False
        app._nj_append_management_event(str(uuid.uuid4()), "STOP_MOVED")
        self._cur.execute.assert_not_called()

    def test_fail_open(self):
        import app
        self._cur.execute.side_effect = Exception("fail")
        app._nj_append_management_event(str(uuid.uuid4()), "TARGET_HIT")  # must not raise


class TestNJSetOutcome(unittest.TestCase):
    """_nj_set_outcome closes the row, sets outcome, and computes R."""

    def setUp(self):
        import app
        app.NJ_DB_READY = True
        self._conn = MagicMock()
        self._cur = MagicMock()
        self._conn.cursor.return_value = self._cur
        self._patch = patch.object(app, "_learning_conn", return_value=self._conn)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        import app
        app.NJ_DB_READY = False

    def _mock_db_row(self, planned_entry=19500.0, planned_stop=19480.0,
                     direction="Long"):
        """Make cursor.fetchone() return the NJ row needed by _nj_set_outcome.

        Phase B: SELECT now fetches 5 columns:
          planned_entry, planned_stop, direction, created_at, lifecycle_status
        created_at=None (skips duration calc), lifecycle_status="ACTIVE"
        (passes the idempotency guard).
        """
        self._cur.fetchone.return_value = (
            planned_entry, planned_stop, direction, None, "ACTIVE"
        )

    def _update_params(self):
        for c in self._cur.execute.call_args_list:
            sql = c[0][0]
            if "UPDATE native_journal" in sql and "lifecycle_status  = 'CLOSED'" in sql:
                return c[0][1]
        return None

    def test_sets_lifecycle_to_closed(self):
        import app
        self._mock_db_row()
        iid = str(uuid.uuid4())
        with patch.object(app, "_nj_check_and_set_learning_eligible"):
            app._nj_set_outcome(iid, {}, exit_reason="Win — T1 Hit ✅", pnl_dollars=125.0)
        params = self._update_params()
        self.assertIsNotNone(params)
        outcome = json.loads(params[0])
        self.assertEqual(outcome["exit_reason"], "Win — T1 Hit ✅")
        self.assertAlmostEqual(outcome["net_pnl"], 125.0)

    def test_computes_realized_r_from_immutable_planned_stop(self):
        """R must use planned_stop (19480), not any moved stop."""
        import app
        # planned_entry=19500, planned_stop=19480 → r_unit=20
        # direction=Long, actual_exit=19520 → R = (19520-19500)/20 = 1.0
        self._mock_db_row(planned_entry=19500.0, planned_stop=19480.0, direction="Long")
        iid = str(uuid.uuid4())
        with patch.object(app, "_nj_check_and_set_learning_eligible"):
            app._nj_set_outcome(iid, {"actual_exit": 19520.0}, pnl_dollars=200.0)
        params = self._update_params()
        outcome = json.loads(params[0])
        self.assertAlmostEqual(outcome["realized_r"], 1.0, places=3)

    def test_loss_r_is_negative(self):
        import app
        # planned_entry=19500, planned_stop=19480, actual_exit=19480 → R=-1.0
        self._mock_db_row(planned_entry=19500.0, planned_stop=19480.0, direction="Long")
        iid = str(uuid.uuid4())
        with patch.object(app, "_nj_check_and_set_learning_eligible"):
            app._nj_set_outcome(iid, {"actual_exit": 19480.0}, pnl_dollars=-200.0)
        params = self._update_params()
        outcome = json.loads(params[0])
        self.assertAlmostEqual(outcome["realized_r"], -1.0, places=3)

    def test_short_r_computed_correctly(self):
        import app
        # Short: planned_entry=2050, planned_stop=2060, actual_exit=2040
        # r_unit=10, dir_sign=-1, R = -1*(2040-2050)/10 = 1.0
        self._mock_db_row(planned_entry=2050.0, planned_stop=2060.0, direction="Short")
        iid = str(uuid.uuid4())
        with patch.object(app, "_nj_check_and_set_learning_eligible"):
            app._nj_set_outcome(iid, {"actual_exit": 2040.0}, pnl_dollars=100.0)
        params = self._update_params()
        outcome = json.loads(params[0])
        self.assertAlmostEqual(outcome["realized_r"], 1.0, places=3)

    def test_r_not_computed_without_actual_exit(self):
        import app
        self._mock_db_row()
        iid = str(uuid.uuid4())
        with patch.object(app, "_nj_check_and_set_learning_eligible"):
            app._nj_set_outcome(iid, {}, pnl_dollars=50.0)
        params = self._update_params()
        outcome = json.loads(params[0])
        self.assertNotIn("realized_r", outcome)

    def test_calls_eligibility_check_after_close(self):
        import app
        self._mock_db_row()
        iid = str(uuid.uuid4())
        with patch.object(app, "_nj_check_and_set_learning_eligible") as mock_elig:
            app._nj_set_outcome(iid, {}, pnl_dollars=0.0)
        mock_elig.assert_called_once_with(iid)

    def test_no_row_found_is_silent(self):
        import app
        self._cur.fetchone.return_value = None
        iid = str(uuid.uuid4())
        with patch.object(app, "_nj_check_and_set_learning_eligible"):
            app._nj_set_outcome(iid, {}, pnl_dollars=0.0)  # must not raise

    def test_no_op_when_not_ready(self):
        import app
        app.NJ_DB_READY = False
        with patch.object(app, "_nj_check_and_set_learning_eligible") as mock_elig:
            app._nj_set_outcome(str(uuid.uuid4()), {})
        self._cur.execute.assert_not_called()
        mock_elig.assert_not_called()

    def test_fail_open(self):
        import app
        self._cur.execute.side_effect = Exception("DB fail")
        with patch.object(app, "_nj_check_and_set_learning_eligible"):
            app._nj_set_outcome(str(uuid.uuid4()), {})  # must not raise


_UNSET = object()  # sentinel for _set_row to distinguish "pass None" from "use default"


class TestNJLearningEligibility(unittest.TestCase):
    """_nj_check_and_set_learning_eligible gates correctly."""

    def setUp(self):
        import app
        app.NJ_DB_READY = True
        self._conn = MagicMock()
        self._cur = MagicMock()
        self._conn.cursor.return_value = self._cur
        self._patch = patch.object(app, "_learning_conn", return_value=self._conn)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        import app
        app.NJ_DB_READY = False

    def _set_row(self, lifecycle="CLOSED", strategy="LSR", planned_risk=20.0,
                 execution=_UNSET, outcome=_UNSET, review_status="UNREVIEWED",
                 source_label="SYSTEM_AUTO"):
        # Use sentinel so callers can explicitly pass None to simulate a NULL column
        # Phase C: SELECT now fetches 7 cols (added source_label for review-aware eligibility).
        if execution is _UNSET:
            execution = {"avg_entry": 19502.5}
        if outcome is _UNSET:
            outcome = {"net_pnl": 100.0, "realized_r": 1.0}
        self._cur.fetchone.return_value = (
            lifecycle, strategy, planned_risk, execution, outcome,
            review_status, source_label,
        )

    def _update_params(self):
        for c in self._cur.execute.call_args_list:
            sql = c[0][0]
            if "UPDATE native_journal" in sql and "learning_eligible" in sql:
                return c[0][1]
        return None

    def test_eligible_when_all_criteria_pass(self):
        import app
        self._set_row()
        app._nj_check_and_set_learning_eligible(str(uuid.uuid4()))
        params = self._update_params()
        self.assertTrue(params[0])    # eligible = True
        self.assertIsNone(params[1])  # no blocked reason

    def test_blocked_when_not_closed(self):
        import app
        self._set_row(lifecycle="ACTIVE")
        app._nj_check_and_set_learning_eligible(str(uuid.uuid4()))
        params = self._update_params()
        self.assertFalse(params[0])
        self.assertIn("lifecycle=ACTIVE", params[1])

    def test_blocked_when_no_strategy(self):
        import app
        self._set_row(strategy=None)
        app._nj_check_and_set_learning_eligible(str(uuid.uuid4()))
        params = self._update_params()
        self.assertFalse(params[0])
        self.assertEqual(params[1], "missing_strategy")

    def test_blocked_when_no_planned_risk(self):
        import app
        self._set_row(planned_risk=None)
        app._nj_check_and_set_learning_eligible(str(uuid.uuid4()))
        params = self._update_params()
        self.assertFalse(params[0])
        self.assertEqual(params[1], "missing_planned_risk")

    def test_blocked_when_no_execution(self):
        import app
        self._set_row(execution=None)
        app._nj_check_and_set_learning_eligible(str(uuid.uuid4()))
        params = self._update_params()
        self.assertFalse(params[0])
        self.assertEqual(params[1], "missing_execution_data")

    def test_blocked_when_execution_missing_fill_data(self):
        import app
        self._set_row(execution={"source": "traderspost"})  # no avg_entry or fill_prices
        app._nj_check_and_set_learning_eligible(str(uuid.uuid4()))
        params = self._update_params()
        self.assertFalse(params[0])
        self.assertEqual(params[1], "missing_execution_data")

    def test_blocked_when_no_outcome(self):
        import app
        self._set_row(outcome=None)
        app._nj_check_and_set_learning_eligible(str(uuid.uuid4()))
        params = self._update_params()
        self.assertFalse(params[0])
        self.assertEqual(params[1], "missing_outcome")

    def test_blocked_when_status_unknown(self):
        import app
        self._set_row(review_status="STATUS_UNKNOWN")
        app._nj_check_and_set_learning_eligible(str(uuid.uuid4()))
        params = self._update_params()
        self.assertFalse(params[0])
        self.assertEqual(params[1], "unresolved_status")

    def test_eligible_with_fill_prices_instead_of_avg_entry(self):
        import app
        self._set_row(execution={"fill_prices": [19502.5, 19503.0]})
        app._nj_check_and_set_learning_eligible(str(uuid.uuid4()))
        params = self._update_params()
        self.assertTrue(params[0])

    def test_no_row_is_silent(self):
        import app
        self._cur.fetchone.return_value = None
        app._nj_check_and_set_learning_eligible(str(uuid.uuid4()))  # must not raise

    def test_fail_open(self):
        import app
        self._cur.execute.side_effect = Exception("DB error")
        app._nj_check_and_set_learning_eligible(str(uuid.uuid4()))  # must not raise


class TestNJFindOpenByInstrument(unittest.TestCase):
    """_nj_find_open_by_instrument looks up the most-recent open row."""

    def setUp(self):
        import app
        app.NJ_DB_READY = True
        self._conn = MagicMock()
        self._cur = MagicMock()
        self._conn.cursor.return_value = self._cur
        self._patch = patch.object(app, "_learning_conn", return_value=self._conn)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        import app
        app.NJ_DB_READY = False

    def test_returns_iid_when_found(self):
        import app
        iid = str(uuid.uuid4())
        self._cur.fetchone.return_value = (iid,)
        result = app._nj_find_open_by_instrument("MNQ", "Long")
        self.assertEqual(result, iid)

    def test_returns_none_when_not_found(self):
        import app
        self._cur.fetchone.return_value = None
        result = app._nj_find_open_by_instrument("MNQ", "Long")
        self.assertIsNone(result)

    def test_returns_none_when_not_ready(self):
        import app
        app.NJ_DB_READY = False
        result = app._nj_find_open_by_instrument("MNQ", "Long")
        self.assertIsNone(result)
        self._cur.execute.assert_not_called()

    def test_fail_open_returns_none(self):
        import app
        self._cur.execute.side_effect = Exception("DB error")
        result = app._nj_find_open_by_instrument("MNQ", "Long")
        self.assertIsNone(result)


class TestNJCloseByInstrument(unittest.TestCase):
    """_nj_close_by_instrument delegates to _nj_find_open + _nj_set_outcome."""

    def setUp(self):
        import app
        app.NJ_DB_READY = True

    def tearDown(self):
        import app
        app.NJ_DB_READY = False

    def test_calls_set_outcome_when_row_found(self):
        import app
        iid = str(uuid.uuid4())
        with patch.object(app, "_nj_find_open_by_instrument", return_value=iid) as mock_find, \
             patch.object(app, "_nj_set_outcome") as mock_close:
            app._nj_close_by_instrument("MNQ", "Long", "Win — T1 Hit ✅",
                                         pnl_dollars=200.0, actual_exit=19520.0)
        mock_find.assert_called_once_with("MNQ", "Long")
        mock_close.assert_called_once_with(
            iid,
            {"actual_exit": 19520.0},
            exit_reason="Win — T1 Hit ✅",
            pnl_dollars=200.0,
        )

    def test_no_op_when_no_row(self):
        import app
        with patch.object(app, "_nj_find_open_by_instrument", return_value=None), \
             patch.object(app, "_nj_set_outcome") as mock_close:
            app._nj_close_by_instrument("MNQ", "Long", "Loss")
        mock_close.assert_not_called()

    def test_no_op_when_not_ready(self):
        import app
        app.NJ_DB_READY = False
        with patch.object(app, "_nj_find_open_by_instrument") as mock_find, \
             patch.object(app, "_nj_set_outcome") as mock_close:
            app._nj_close_by_instrument("MNQ", "Long", "Loss")
        mock_find.assert_not_called()
        mock_close.assert_not_called()


class TestNJWiring(unittest.TestCase):
    """Integration: _capture_send_time_snapshot and _update_journal_outcome call NJ helpers."""

    def setUp(self):
        import app
        app.NJ_DB_READY = True

    def tearDown(self):
        import app
        app.NJ_DB_READY = False

    def test_capture_snapshot_calls_nj_create(self):
        """_capture_send_time_snapshot must call _nj_create_from_snapshot after persist."""
        import app
        snap = _make_snapshot()
        mock_ts = MagicMock()
        mock_ts.build_trade_snapshot.return_value = snap
        with patch.dict("sys.modules", {"trade_snapshot": mock_ts}), \
             patch.object(app, "_persist_trade_snapshot") as mock_persist, \
             patch.object(app, "_nj_create_from_snapshot") as mock_nj:
            app._capture_send_time_snapshot(
                a={}, instrument="MNQ", mode="SCALP", source="traderspost",
                contracts=2, direction="Long",
                entry=19500.0, stop=19480.0, t1=19520.0, t2=19540.0,
            )
        mock_persist.assert_called_once_with(snap)
        mock_nj.assert_called_once_with(snap)

    def test_capture_snapshot_nj_exception_does_not_propagate(self):
        """If _nj_create_from_snapshot raises, _capture_send_time_snapshot must not raise."""
        import app
        snap = _make_snapshot()
        mock_ts = MagicMock()
        mock_ts.build_trade_snapshot.return_value = snap
        with patch.dict("sys.modules", {"trade_snapshot": mock_ts}), \
             patch.object(app, "_persist_trade_snapshot"), \
             patch.object(app, "_nj_create_from_snapshot", side_effect=Exception("NJ fail")):
            # Should not raise — the whole function is wrapped in try/except
            app._capture_send_time_snapshot(
                a={}, instrument="MNQ", mode="SCALP", source="traderspost",
                contracts=2, direction="Long",
                entry=19500.0, stop=19480.0, t1=19520.0, t2=19540.0,
            )

    def test_failed_gateway_paper_insert_does_not_link_missing_journal_row(self):
        """A failed gateway INSERT leaves its managed trade retryable, not linked."""
        import app
        snap = _make_snapshot()
        snap.update({
            "internal_trade_id": str(uuid.uuid4()),
            "instrument": "MNQ",
            "mode": "paper",
            "direction": "Long",
            "planned_entry": 19500.0,
        })
        mt = {
            "key": ("MNQ", "Long", 19500.0, "gateway-failure"),
            "instrument": "MNQ",
            "symbol": "MNQ1!",
            "direction": "Long",
            "entry": 19500.0,
            "initial_stop": 19480.0,
            "stop": 19480.0,
            "tp1": 19520.0,
            "risk_points": 20.0,
        }
        original_managed = dict(app.MANAGED_TRADES_BY_KEY)
        app.MANAGED_TRADES_BY_KEY.clear()
        app.MANAGED_TRADES_BY_KEY[mt["key"]] = mt
        mock_ts = MagicMock()
        mock_ts.build_trade_snapshot.return_value = snap
        try:
            with patch.dict("sys.modules", {"trade_snapshot": mock_ts}), \
                 patch.object(app, "_persist_trade_snapshot"), \
                 patch.object(app, "_nj_create_from_snapshot", return_value=False), \
                 patch.object(app, "_link_gateway_snapshot_to_managed_trade") as link:
                app._capture_send_time_snapshot(
                    a={}, instrument="MNQ", mode="paper", source="auto",
                    contracts=1, direction="Long",
                    entry=19500.0, stop=19480.0, t1=19520.0, t2=19540.0,
                )
            link.assert_not_called()
            self.assertNotIn("native_journal_internal_trade_id", mt)
            with patch.object(app, "_nj_create_from_snapshot", return_value=True):
                app._ensure_managed_paper_journal(mt)
            self.assertEqual(mt["native_journal_internal_trade_id"],
                             app._managed_paper_journal_id(mt))
        finally:
            app.MANAGED_TRADES_BY_KEY.clear()
            app.MANAGED_TRADES_BY_KEY.update(original_managed)

    def test_update_journal_outcome_calls_nj_close_on_terminal(self):
        """_update_journal_outcome must call _nj_close_by_instrument on a terminal outcome."""
        import app
        # Inject a Pending journal entry for MNQ
        entry = {
            "id": 9901,
            "symbol": "MNQ1!",
            "direction": "Long",
            "outcome": "Pending",
            "analytics_posted": False,
        }
        original_journal = list(app.JOURNAL)
        app.JOURNAL.clear()
        app.JOURNAL.append(entry)
        try:
            with patch.object(app, "_persist_journal_entry"), \
                 patch.object(app, "post_performance_stats"), \
                 patch.object(app, "_nj_close_by_instrument") as mock_nj_close, \
                 patch.object(app, "_outcome_state", return_value="win"):
                app._update_journal_outcome(
                    "Win — T1 Hit ✅",
                    pnl_dollars=200.0,
                    symbol="MNQ",
                )
            mock_nj_close.assert_called_once()
            call_kwargs = mock_nj_close.call_args
            self.assertEqual(call_kwargs[1]["exit_reason"], "Win — T1 Hit ✅")
            self.assertAlmostEqual(call_kwargs[1]["pnl_dollars"], 200.0)
        finally:
            app.JOURNAL.clear()
            for e in original_journal:
                app.JOURNAL.append(e)

    def test_update_journal_outcome_no_nj_close_on_intermediate(self):
        """_update_journal_outcome must NOT call _nj_close_by_instrument for T1 Hit (non-terminal)."""
        import app
        entry = {
            "id": 9902,
            "symbol": "MNQ1!",
            "direction": "Long",
            "outcome": "Pending",
            "analytics_posted": False,
        }
        original_journal = list(app.JOURNAL)
        app.JOURNAL.clear()
        app.JOURNAL.append(entry)
        try:
            with patch.object(app, "_persist_journal_entry"), \
                 patch.object(app, "_nj_close_by_instrument") as mock_nj_close, \
                 patch.object(app, "_outcome_state", return_value="t1_hit"):  # not win/loss/breakeven
                app._update_journal_outcome("T1 Hit ⚡", symbol="MNQ")
            mock_nj_close.assert_not_called()
        finally:
            app.JOURNAL.clear()
            for e in original_journal:
                app.JOURNAL.append(e)


class TestNJNoDuplicates(unittest.TestCase):
    """ON CONFLICT (internal_trade_id) DO NOTHING prevents duplicate rows."""

    def setUp(self):
        import app
        app.NJ_DB_READY = True
        self._conn = MagicMock()
        self._cur = MagicMock()
        self._conn.cursor.return_value = self._cur
        self._patch = patch.object(app, "_learning_conn", return_value=self._conn)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        import app
        app.NJ_DB_READY = False

    def test_double_create_does_not_duplicate(self):
        import app
        snap = _make_snapshot()
        app._nj_create_from_snapshot(snap)
        app._nj_create_from_snapshot(snap)
        insert_calls = [
            c for c in self._cur.execute.call_args_list
            if "INSERT INTO native_journal" in c[0][0]
        ]
        # Both calls fire the SQL — dedup is enforced by ON CONFLICT at DB level
        for c in insert_calls:
            self.assertIn("DO NOTHING", c[0][0])


class TestManagedPaperJournalBridge(unittest.TestCase):
    """Four-instrument paper journal coverage for managed display trades."""

    def setUp(self):
        import app
        self.app = app
        self.original_ready = app.NJ_DB_READY
        self.original_managed = dict(app.MANAGED_TRADES_BY_KEY)
        app.NJ_DB_READY = True
        app.MANAGED_TRADES_BY_KEY.clear()

    def tearDown(self):
        self.app.NJ_DB_READY = self.original_ready
        self.app.MANAGED_TRADES_BY_KEY.clear()
        self.app.MANAGED_TRADES_BY_KEY.update(self.original_managed)

    @staticmethod
    def _managed_trade(inst, direction="Long", entry=100.0):
        key = (inst, direction, round(entry, 0), "2026-08-20")
        return {
            "key": key,
            "instrument": inst,
            "symbol": inst + "1!",
            "direction": direction,
            "entry": entry,
            "initial_stop": entry - 10.0,
            "stop": entry - 10.0,
            "tp1": entry + 10.0,
            "risk_points": 10.0,
            "point_value": 1.0,
            "registered_at": "2026-08-20T14:00:00+00:00",
            "learning_ctx": {
                "strategy_key": "VWAP_PULLBACK_CONTINUATION",
                "strategy": "VWAP Pullback",
                "edge_score": 80,
                "grade": "A",
            },
        }

    def test_creates_explicit_paper_rows_for_all_four_instruments(self):
        """Every canonical contract gets the same paper row shape, never a broker row."""
        for index, inst in enumerate(("MGC", "MNQ", "MES", "MYM")):
            mt = self._managed_trade(inst, entry=100.0 + index)
            with self.subTest(instrument=inst), \
                 patch.object(self.app, "_nj_create_from_snapshot") as create, \
                 patch.object(self.app, "_nj_update_lifecycle") as lifecycle, \
                 patch.object(self.app, "_nj_update_execution", return_value=True) as save_state:
                self.app._ensure_managed_paper_journal(mt)
                create.assert_called_once()
                snapshot = create.call_args.args[0]
                self.assertEqual(snapshot["instrument"], inst)
                self.assertEqual(snapshot["mode"], "paper")
                self.assertEqual(snapshot["source"], "paper")
                self.assertIsNone(snapshot["broker_order_id"])
                self.assertIsNone(snapshot["broker_signal_id"])
                self.assertEqual(create.call_args.kwargs["link_edge_ledger"], False)
                lifecycle.assert_called_once()
                save_state.assert_called_once()
                self.assertEqual(mt["native_journal_source"], "paper")

    def test_repeated_watcher_pass_keeps_one_stable_paper_identity(self):
        mt = self._managed_trade("MNQ")
        with patch.object(self.app, "_nj_create_from_snapshot") as create, \
             patch.object(self.app, "_nj_update_lifecycle"):
            self.app._ensure_managed_paper_journal(mt)
            first_id = mt["native_journal_internal_trade_id"]
            self.app._ensure_managed_paper_journal(mt)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(mt["native_journal_internal_trade_id"], first_id)

    def test_gateway_paper_snapshot_links_then_prevents_display_duplicate(self):
        mt = self._managed_trade("MES", direction="Short", entry=200.0)
        self.app.MANAGED_TRADES_BY_KEY[mt["key"]] = mt
        gateway_iid = str(uuid.uuid4())
        snapshot = _make_snapshot(
            internal_trade_id=gateway_iid,
            instrument="MES",
            direction="Short",
            planned_entry=200.0,
            planned_stop=210.0,
            mode="paper",
            source="auto",
        )
        self.app._attach_managed_paper_state_to_gateway_snapshot(snapshot)
        self.assertIn("managed_paper_state", snapshot)
        self.app._link_gateway_snapshot_to_managed_trade(snapshot)
        self.assertEqual(mt["native_journal_internal_trade_id"], gateway_iid)
        self.assertEqual(mt["native_journal_source"], "paper")
        with patch.object(self.app, "_nj_create_from_snapshot") as create, \
             patch.object(self.app, "_nj_update_lifecycle"), \
             patch.object(self.app, "_nj_update_execution", return_value=True):
            self.app._ensure_managed_paper_journal(mt)
        create.assert_not_called()

        # If the service restarts before the watcher, the state already present
        # in the gateway row still rehydrates the attached managed trade.
        self.app.MANAGED_TRADES_BY_KEY.clear()
        cursor = MagicMock()
        cursor.fetchall.return_value = [(
            gateway_iid, {"managed_paper_state": snapshot["managed_paper_state"]}
        )]
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        with patch.object(self.app, "_learning_conn", return_value=conn):
            self.app._load_managed_paper_trades_from_db()
        restored = self.app.MANAGED_TRADES_BY_KEY[mt["key"]]
        self.assertEqual(restored["native_journal_internal_trade_id"], gateway_iid)

    def test_paper_close_persists_outcome_by_exact_internal_id(self):
        mt = self._managed_trade("MYM")
        mt.update({
            "native_journal_internal_trade_id": str(uuid.uuid4()),
            "native_journal_source": "paper",
            "outcome": "Win",
            "result_label": "Win (TP1)",
            "exit_price": 110.0,
            "pnl_dollars": 10.0,
            "r_multiple": 1.0,
        })
        with patch.object(self.app, "_nj_set_outcome") as set_outcome:
            self.app._close_managed_paper_journal(mt)
        set_outcome.assert_called_once()
        self.assertEqual(set_outcome.call_args.args[0], mt["native_journal_internal_trade_id"])
        self.assertEqual(set_outcome.call_args.kwargs["actual_exit"], 110.0)
        self.assertEqual(set_outcome.call_args.kwargs["pnl_dollars"], 10.0)
        self.assertTrue(set_outcome.call_args.args[1]["paper_tracking"])

    def test_paper_managed_close_skips_broad_instrument_fallback(self):
        """Exact-ID paper closes must not claim a different same-side paper row."""
        mt = self._managed_trade("MGC")
        mt.update({
            "native_journal_internal_trade_id": str(uuid.uuid4()),
            "native_journal_source": "paper",
            "outcome": "Win",
            "result_label": "Win (TP1)",
            "pnl_dollars": 10.0,
        })
        original_journal = list(self.app.JOURNAL)
        self.app.JOURNAL.clear()
        try:
            with patch.object(self.app, "_nj_close_by_instrument") as close_by_instrument:
                self.app._apply_outcome_to_journal(mt)
            close_by_instrument.assert_not_called()
        finally:
            self.app.JOURNAL.clear()
            self.app.JOURNAL.extend(original_journal)

    def test_all_four_paper_trades_rehydrate_and_close_exact_rows_after_restart(self):
        """A restart retains one broker-free paper row and exact close target per instrument."""
        rows = []
        for index, inst in enumerate(("MGC", "MNQ", "MES", "MYM")):
            mt = self._managed_trade(inst, entry=100.0 + index)
            with patch.object(self.app, "_nj_create_from_snapshot"), \
                 patch.object(self.app, "_nj_update_lifecycle"), \
                 patch.object(self.app, "_nj_update_execution") as save_state:
                self.app._ensure_managed_paper_journal(mt)
            # This is the state committed in the initial INSERT, before the
            # follow-up ACTIVE transition or any later execution JSONB update.
            state = self.app._managed_paper_snapshot(
                mt, mt["native_journal_internal_trade_id"]
            )["managed_paper_state"]
            rows.append((
                mt["native_journal_internal_trade_id"],
                {"managed_paper_state": state},
            ))

        # Simulate a new webhook-server process with only its active journal rows.
        self.app.MANAGED_TRADES_BY_KEY.clear()
        cursor = MagicMock()
        cursor.fetchall.return_value = rows
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        with patch.object(self.app, "_learning_conn", return_value=conn):
            self.app._load_managed_paper_trades_from_db()

        self.assertEqual(len(self.app.MANAGED_TRADES_BY_KEY), 4)
        self.assertIn("FROM native_journal", cursor.execute.call_args.args[0])
        restored = list(self.app.MANAGED_TRADES_BY_KEY.values())
        self.assertEqual({mt["instrument"] for mt in restored},
                         {"MGC", "MNQ", "MES", "MYM"})

        with patch.object(self.app, "_nj_set_outcome") as set_outcome, \
             patch.object(self.app.requests, "post") as broker_post:
            for mt in restored:
                mt.update({
                    "outcome": "Win",
                    "result_label": "Win (TP1)",
                    "exit_price": float(mt["entry"]) + 10.0,
                    "pnl_dollars": 10.0,
                    "r_multiple": 1.0,
                })
                self.app._close_managed_paper_journal(mt)

        self.assertEqual(set_outcome.call_count, 4)
        self.assertEqual(
            {call.args[0] for call in set_outcome.call_args_list},
            {internal_trade_id for internal_trade_id, _ in rows},
        )
        broker_post.assert_not_called()

    def test_conditional_runner_watermarks_preserve_the_exit_after_restart(self):
        """A restarted runner must retain its pre-restart high-water trailing anchor."""
        mt = self._managed_trade("MNQ")
        mt.update({"remaining_pct": 1.0, "entry_epoch": self.app.time.time()})
        bar = {"high": 110.0, "low": 101.0, "close": 108.0}
        neutral_market = {"open": True}

        # First bar reaches TP1 and observes a strong runner high-water mark.
        with patch.object(self.app, "current_price_for", return_value=160.0), \
             patch.object(self.app, "market_session_status", return_value=neutral_market), \
             patch.object(self.app, "get_volatility", return_value={"atr_pts": 5.0}), \
             patch.object(self.app, "VWAP_BY_TICKER", {}), \
             patch.object(self.app, "CVD_BY_TICKER", {}), \
             patch.object(self.app, "_send_management_update"), \
             patch.object(self.app, "_maybe_move_be_to_entry"):
            self.app._evaluate_conditional_paper_runner(mt, bar)
        self.assertTrue(mt["tp1_hit"])
        self.assertEqual(mt["cp_peak"], 160.0)

        state = self.app._managed_paper_state_snapshot(mt)
        internal_trade_id = str(uuid.uuid4())
        restored = self.app._restore_managed_paper_state(state, internal_trade_id)
        self.assertEqual(restored["cp_peak"], 160.0)
        self.assertEqual(restored["cp_trough"], mt["cp_trough"])

        # The same trailing pullback must produce the same runner decision on
        # both sides of the restart boundary.
        with patch.object(self.app, "current_price_for", return_value=120.0), \
             patch.object(self.app, "market_session_status", return_value=neutral_market), \
             patch.object(self.app, "get_volatility", return_value={"atr_pts": 5.0}), \
             patch.object(self.app, "VWAP_BY_TICKER", {}), \
             patch.object(self.app, "CVD_BY_TICKER", {}):
            before_restart = self.app._runner_exit_signal(self.app._mt_to_runner_rec(mt))
            after_restart = self.app._runner_exit_signal(self.app._mt_to_runner_rec(restored))
        self.assertEqual(before_restart, after_restart)
        self.assertEqual(before_restart, (True, "ATR trail"))

    def test_stop_managing_is_terminal_after_restart_without_broker_order(self):
        """An operator stop must not restore the paper lifecycle on the next boot."""
        mt = self._managed_trade("MGC")
        mt.update({
            "native_journal_internal_trade_id": str(uuid.uuid4()),
            "native_journal_source": "paper",
        })
        self.app.MANAGED_TRADES_BY_KEY[mt["key"]] = mt
        with self.app.app.test_request_context(
            "/stop-managing", method="POST", json={"ticker": "MGC"}
        ), patch.object(self.app, "_nj_update_execution", return_value=True) as save_state, \
             patch.object(self.app, "_nj_update_lifecycle") as lifecycle, \
             patch.object(self.app.requests, "post") as broker_post:
            response, status = self.app.stop_managing()

        self.assertEqual(status, 200)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(mt["closed"])
        state = save_state.call_args.args[1]["managed_paper_state"]
        self.assertTrue(state["closed"])
        lifecycle.assert_called_once()
        self.assertEqual(lifecycle.call_args.args[1], "CANCELED")
        broker_post.assert_not_called()

        # Even if a stale pre-cancel lifecycle query returns the row, its closed
        # recovery payload must prevent it from returning to the watcher.
        self.app.MANAGED_TRADES_BY_KEY.clear()
        cursor = MagicMock()
        cursor.fetchall.return_value = [(
            mt["native_journal_internal_trade_id"], {"managed_paper_state": state}
        )]
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        with patch.object(self.app, "_learning_conn", return_value=conn):
            self.app._load_managed_paper_trades_from_db()
        self.assertNotIn(mt["key"], self.app.MANAGED_TRADES_BY_KEY)

    def test_stop_fence_blocks_recovery_after_native_cancel_outage(self):
        """An independently durable stop intent fences an old open row at boot."""
        mt = self._managed_trade("MNQ")
        mt.update({
            "native_journal_internal_trade_id": str(uuid.uuid4()),
            "native_journal_source": "paper",
        })
        open_state = self.app._managed_paper_state_snapshot(mt)
        self.app.MANAGED_TRADES_BY_KEY[mt["key"]] = mt
        with self.app.app.test_request_context(
            "/stop-managing", method="POST", json={"ticker": "MNQ"}
        ), patch.object(self.app, "_save_managed_paper_stop_intent", return_value=True), \
             patch.object(self.app, "_nj_update_execution", return_value=False), \
             patch.object(self.app, "_nj_update_lifecycle", return_value=False):
            _response, status = self.app.stop_managing()
        self.assertEqual(status, 200)
        self.assertTrue(mt["closed"])
        stopped_state = self.app._managed_paper_state_snapshot(mt)

        # Simulate restart after the native row remained ACTIVE with its old
        # snapshot. Once DB access recovers, the durable stop fence wins and the
        # old row is canceled rather than restored into the watcher.
        self.app.MANAGED_TRADES_BY_KEY.clear()
        cursor = MagicMock()
        cursor.fetchall.return_value = [(
            mt["native_journal_internal_trade_id"],
            {"managed_paper_state": open_state},
        )]
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        with patch.object(
            self.app, "_load_managed_paper_stop_intent",
            return_value={"managed_paper_state": stopped_state},
        ), patch.object(self.app, "_save_managed_paper_stop_intent", return_value=True), \
             patch.object(self.app, "_nj_update_execution", return_value=True), \
             patch.object(self.app, "_nj_update_lifecycle", return_value=True), \
             patch.object(self.app, "_learning_conn", return_value=conn):
            self.app._load_managed_paper_trades_from_db()
        self.assertNotIn(mt["key"], self.app.MANAGED_TRADES_BY_KEY)

    def test_stop_fence_retries_native_cancel_while_process_stays_up(self):
        """A fenced stop remains inert and retries until native state is terminal."""
        mt = self._managed_trade("MYM")
        mt.update({
            "native_journal_internal_trade_id": str(uuid.uuid4()),
            "native_journal_source": "paper",
            "closed": True,
        })
        self.app.MANAGED_TRADES_BY_KEY[mt["key"]] = mt
        with patch.object(self.app, "_save_managed_paper_stop_intent", return_value=True), \
             patch.object(self.app, "_nj_update_execution", return_value=False), \
             patch.object(self.app, "_nj_update_lifecycle", return_value=False):
            self.assertTrue(self.app._cancel_managed_paper_journal(mt))
        self.assertTrue(mt["_paper_cancel_pending"])
        self.assertTrue(mt["_paper_stop_fenced"])

        with patch.object(self.app, "_fetch_latest_bar", return_value=None), \
             patch.object(self.app, "_save_managed_paper_stop_intent", return_value=True), \
             patch.object(self.app, "_nj_update_execution", return_value=True) as update_state, \
             patch.object(self.app, "_nj_update_lifecycle", return_value=True) as cancel_row:
            self.app._watch_managed_trades()
        update_state.assert_called_once()
        self.assertEqual(cancel_row.call_args.args[1], "CANCELED")
        self.assertTrue(mt["closed"])
        self.assertNotIn("_paper_cancel_pending", mt)
        self.assertNotIn("_paper_stop_fenced", mt)

    def test_terminal_fence_blocks_recovery_after_outcome_write_outage(self):
        """A failed exact-row close must not restore or double-finalize on boot."""
        mt = self._managed_trade("MES")
        mt.update({
            "native_journal_internal_trade_id": str(uuid.uuid4()),
            "native_journal_source": "paper",
            "closed": True,
            "outcome": "Win",
            "result_label": "Win (TP1)",
            "exit_price": 110.0,
            "pnl_dollars": 10.0,
            "r_multiple": 1.0,
        })
        open_state = self.app._managed_paper_state_snapshot({
            **mt, "closed": False, "outcome": None,
        })
        with patch.object(self.app, "_save_managed_paper_terminal_intent", return_value=True), \
             patch.object(self.app, "_nj_set_outcome", return_value=False):
            self.assertFalse(self.app._close_managed_paper_journal(mt))
        terminal_state = self.app._managed_paper_state_snapshot(mt)
        terminal_payload = self.app._managed_paper_terminal_payload(mt)

        self.app.MANAGED_TRADES_BY_KEY.clear()
        cursor = MagicMock()
        cursor.fetchall.return_value = [(
            mt["native_journal_internal_trade_id"],
            {"managed_paper_state": open_state},
        )]
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        with patch.object(
            self.app, "_load_managed_paper_terminal_intent",
            return_value={
                "managed_paper_state": terminal_state,
                "terminal_outcome": terminal_payload,
            },
        ), patch.object(self.app, "_save_managed_paper_terminal_intent", return_value=True), \
             patch.object(self.app, "_nj_set_outcome", return_value=True) as set_outcome, \
             patch.object(self.app, "_learning_conn", return_value=conn):
            self.app._load_managed_paper_trades_from_db()
        self.assertNotIn(mt["key"], self.app.MANAGED_TRADES_BY_KEY)
        self.assertEqual(set_outcome.call_args.args[0], mt["native_journal_internal_trade_id"])
        self.assertEqual(set_outcome.call_args.args[1]["managed_result"], "Win")
        self.assertEqual(set_outcome.call_args.kwargs["exit_reason"], "Win (TP1)")
        self.assertEqual(set_outcome.call_args.kwargs["actual_exit"], 110.0)
        self.assertEqual(set_outcome.call_args.kwargs["pnl_dollars"], 10.0)
        self.assertEqual(set_outcome.call_args.args[1]["managed_r_multiple"], 1.0)

    def test_terminal_outcome_write_retries_while_fenced_and_inert(self):
        """A fenced but unconfirmed close stays out of evaluation until retried."""
        mt = self._managed_trade("MGC")
        mt.update({
            "native_journal_internal_trade_id": str(uuid.uuid4()),
            "native_journal_source": "paper",
            "closed": True,
            "_paper_terminal_pending": True,
            "outcome": "Loss",
        })
        self.app.MANAGED_TRADES_BY_KEY[mt["key"]] = mt
        with patch.object(self.app, "_fetch_latest_bar", return_value=None), \
             patch.object(self.app, "_close_managed_paper_journal", return_value=True) as retry_close:
            self.app._watch_managed_trades()
        retry_close.assert_called_once_with(mt)
        self.assertNotIn("_paper_terminal_pending", mt)

    def test_boot_terminal_retry_stays_inert_until_native_row_confirms(self):
        """A boot-time close outage remains retryable without another restart."""
        mt = self._managed_trade("MGC")
        mt.update({
            "native_journal_internal_trade_id": str(uuid.uuid4()),
            "native_journal_source": "paper",
            "closed": True,
            "outcome": "Win",
        })
        cursor = MagicMock()
        cursor.fetchall.return_value = [(
            mt["native_journal_internal_trade_id"],
            {"managed_paper_state": self.app._managed_paper_state_snapshot({**mt, "closed": False})},
        )]
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        intent = {
            "managed_paper_state": self.app._managed_paper_state_snapshot(mt),
            "terminal_outcome": self.app._managed_paper_terminal_payload(mt),
        }
        with patch.object(self.app, "_load_managed_paper_terminal_intent", return_value=intent), \
             patch.object(self.app, "_close_managed_paper_journal", return_value=False), \
             patch.object(self.app, "_learning_conn", return_value=conn):
            self.app._load_managed_paper_trades_from_db()
        recovered = self.app.MANAGED_TRADES_BY_KEY[mt["key"]]
        self.assertTrue(recovered["closed"])
        self.assertTrue(recovered["_paper_terminal_pending"])
        with patch.object(self.app, "_fetch_latest_bar", return_value=None), \
             patch.object(self.app, "_close_managed_paper_journal", return_value=True), \
             patch.object(self.app, "_send_outcome_update") as notify, \
             patch.object(self.app, "_apply_outcome_to_journal") as legacy, \
             patch.object(self.app, "_record_strategy_trade") as record:
            self.app._watch_managed_trades()
        self.assertNotIn("_paper_terminal_pending", recovered)
        notify.assert_called_once()
        legacy.assert_called_once()
        record.assert_called_once()

    def test_boot_stop_retry_stays_inert_until_native_row_confirms(self):
        """A boot-time cancel outage remains retryable without another restart."""
        mt = self._managed_trade("MNQ")
        mt.update({
            "native_journal_internal_trade_id": str(uuid.uuid4()),
            "native_journal_source": "paper",
            "closed": True,
        })
        cursor = MagicMock()
        cursor.fetchall.return_value = [(
            mt["native_journal_internal_trade_id"],
            {"managed_paper_state": self.app._managed_paper_state_snapshot({**mt, "closed": False})},
        )]
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        intent = {"managed_paper_state": self.app._managed_paper_state_snapshot(mt)}
        with patch.object(self.app, "_load_managed_paper_stop_intent", return_value=intent), \
             patch.object(self.app, "_cancel_managed_paper_journal", return_value=False), \
             patch.object(self.app, "_learning_conn", return_value=conn):
            self.app._load_managed_paper_trades_from_db()
        recovered = self.app.MANAGED_TRADES_BY_KEY[mt["key"]]
        self.assertTrue(recovered["closed"])
        self.assertTrue(recovered["_paper_cancel_pending"])
        with patch.object(self.app, "_fetch_latest_bar", return_value=None), \
             patch.object(self.app, "_cancel_managed_paper_journal", side_effect=lambda trade: trade.pop("_paper_cancel_pending", None)):
            self.app._watch_managed_trades()
        self.assertNotIn("_paper_cancel_pending", recovered)

    def test_unfenced_terminal_close_rolls_back_before_outcome_side_effects(self):
        """No fence plus no exact close leaves the paper trade open and unbooked."""
        mt = self._managed_trade("MES")
        mt.update({
            "native_journal_internal_trade_id": str(uuid.uuid4()),
            "native_journal_source": "paper",
            "mfe": 0.0,
            "mae": 0.0,
        })
        with patch.object(self.app, "_save_managed_paper_terminal_intent", return_value=False), \
             patch.object(self.app, "_nj_set_outcome", return_value=False), \
             patch.object(self.app, "_send_outcome_update") as notify, \
             patch.object(self.app, "_record_strategy_trade") as record:
            self.assertFalse(self.app._close_managed_trade(mt, "Win", "Win (TP1)", 110.0))
        self.assertFalse(mt.get("closed"))
        self.assertNotIn("outcome", mt)
        notify.assert_not_called()
        record.assert_not_called()

    def test_fenced_terminal_intent_also_waits_for_exact_native_outcome(self):
        """An intent fence alone cannot release legacy or learning side effects."""
        mt = self._managed_trade("MNQ")
        mt.update({
            "native_journal_internal_trade_id": str(uuid.uuid4()),
            "native_journal_source": "paper",
            "mfe": 0.0,
            "mae": 0.0,
        })

        def failed_exact_close(trade):
            trade["_paper_terminal_fenced"] = True
            return False

        with patch.object(
            self.app, "_close_managed_paper_journal",
            side_effect=failed_exact_close,
        ), patch.object(self.app, "_send_outcome_update") as notify, \
             patch.object(self.app, "_apply_outcome_to_journal") as legacy, \
             patch.object(self.app, "_record_strategy_trade") as record:
            self.assertFalse(
                self.app._close_managed_trade(mt, "Win", "Win (TP1)", 110.0)
            )

        self.assertFalse(mt.get("closed"))
        self.assertTrue(mt.get("_paper_terminal_pending"))
        self.assertEqual(mt["_paper_terminal_retry"], {
            "outcome": "Win",
            "result_label": "Win (TP1)",
            "exit_price": 110.0,
        })
        self.assertNotIn("outcome", mt)
        notify.assert_not_called()
        legacy.assert_not_called()
        record.assert_not_called()

    def test_fenced_terminal_retry_replays_original_close_then_side_effects(self):
        mt = self._managed_trade("MNQ")
        mt.update({
            "native_journal_internal_trade_id": str(uuid.uuid4()),
            "native_journal_source": "paper",
            "mfe": 0.0,
            "mae": 0.0,
        })
        outcomes = iter((False, True))
        with patch.object(
            self.app, "_nj_set_outcome", side_effect=lambda *a, **k: next(outcomes)
        ) as set_outcome, patch.object(
            self.app, "_save_managed_paper_terminal_intent", return_value=True
        ), patch.object(self.app, "_send_outcome_update") as notify, \
             patch.object(self.app, "_apply_outcome_to_journal") as legacy, \
             patch.object(self.app, "_record_strategy_trade") as record, \
             patch.object(self.app, "_fetch_latest_bar", return_value=None):
            self.assertFalse(
                self.app._close_managed_trade(mt, "Win", "Win (TP1)", 110.0)
            )
            self.app.MANAGED_TRADES_BY_KEY[mt["key"]] = mt
            self.app._watch_managed_trades()

        self.assertEqual(set_outcome.call_count, 2)
        self.assertEqual(set_outcome.call_args.args[1]["managed_result"], "Win")
        self.assertEqual(set_outcome.call_args.kwargs["actual_exit"], 110.0)
        self.assertTrue(mt["closed"])
        self.assertEqual(mt["outcome"], "Win")
        self.assertEqual(mt["result_label"], "Win (TP1)")
        self.assertEqual(mt["exit_price"], 110.0)
        self.assertNotIn("_paper_terminal_pending", mt)
        self.assertNotIn("_paper_terminal_retry", mt)
        notify.assert_called_once()
        legacy.assert_called_once()
        record.assert_called_once()


    def test_swing_paper_state_restores_its_thesis_lifecycle(self):
        """A rehydrated SWING keeps the dispatcher and once-only thesis flags."""
        mt = self._managed_trade("MYM")
        mt.update({
            "is_swing": True,
            "thesis_key": "swing-restart-test",
            "swing_thesis": {
                "status": "WEAKENING",
                "next_review_at": "2026-08-20T15:00:00+00:00",
                "last_review_decision": "REDUCE",
            },
            "swing_reduced": True,
            "swing_stop_moved": True,
            "swing_exit_advised": True,
        })
        restored = self.app._restore_managed_paper_state(
            self.app._managed_paper_state_snapshot(mt), str(uuid.uuid4())
        )
        self.assertTrue(restored["is_swing"])
        self.assertEqual(restored["thesis_key"], mt["thesis_key"])
        self.assertEqual(restored["swing_thesis"], mt["swing_thesis"])
        self.assertTrue(restored["swing_reduced"])
        self.assertTrue(restored["swing_stop_moved"])
        self.assertTrue(restored["swing_exit_advised"])
        with patch.object(self.app, "_swing_htf_enabled", return_value=True):
            self.assertTrue(self.app._swing_lifecycle_enabled(restored))

    def test_paper_recovery_merges_exact_id_into_swing_restored_first(self):
        """Boot order must not make a SWING close fall back to instrument matching."""
        mt = self._managed_trade("MES")
        mt.update({
            "is_swing": True,
            "thesis_key": "swing-paper-merge-test",
            "swing_thesis": {"status": "VALID", "last_review_decision": "HOLD"},
            "swing_stop_moved": True,
        })
        paper_id = str(uuid.uuid4())
        paper_state = self.app._managed_paper_state_snapshot(mt)
        swing_first = self.app._restore_swing_mt_snapshot(
            self.app._swing_mt_snapshot(mt)
        )
        self.assertNotIn("native_journal_internal_trade_id", swing_first)
        self.app.MANAGED_TRADES_BY_KEY[mt["key"]] = swing_first

        cursor = MagicMock()
        cursor.fetchall.return_value = [(paper_id, {"managed_paper_state": paper_state})]
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        with patch.object(self.app, "_learning_conn", return_value=conn):
            self.app._load_managed_paper_trades_from_db()

        recovered = self.app.MANAGED_TRADES_BY_KEY[mt["key"]]
        self.assertTrue(recovered["is_swing"])
        self.assertEqual(recovered["thesis_key"], mt["thesis_key"])
        self.assertTrue(recovered["swing_stop_moved"])
        self.assertEqual(recovered["native_journal_internal_trade_id"], paper_id)
        self.assertEqual(recovered["native_journal_source"], "paper")
        with patch.object(self.app, "_nj_set_outcome") as set_outcome, \
             patch.object(self.app, "requests") as requests_mock:
            recovered.update({"outcome": "Win", "result_label": "Win (target hit)"})
            self.app._close_managed_paper_journal(recovered)
        self.assertEqual(set_outcome.call_args.args[0], paper_id)
        requests_mock.post.assert_not_called()

    def test_failed_state_write_is_retried_before_restart(self):
        """A failed JSONB update must not mark paper state as safely persisted."""
        mt = self._managed_trade("MNQ")
        mt.update({
            "native_journal_internal_trade_id": str(uuid.uuid4()),
            "native_journal_source": "paper",
        })
        with patch.object(self.app, "_nj_update_execution", return_value=False) as save_state:
            self.app._persist_managed_paper_state(mt)
        self.assertNotIn("_paper_state_persisted", mt)
        save_state.assert_called_once()

        with patch.object(self.app, "_nj_update_execution", return_value=True) as save_state:
            self.app._persist_managed_paper_state(mt)
        self.assertIn("_paper_state_persisted", mt)
        save_state.assert_called_once()

    def test_failed_initial_create_retries_without_claiming_a_journal_id(self):
        """A transient INSERT failure leaves the managed trade retryable on the next pass."""
        mt = self._managed_trade("MES")
        with patch.object(self.app, "_nj_create_from_snapshot", return_value=False) as create:
            self.app._ensure_managed_paper_journal(mt)
        create.assert_called_once()
        self.assertNotIn("native_journal_internal_trade_id", mt)

        with patch.object(self.app, "_nj_create_from_snapshot", return_value=True) as create, \
             patch.object(self.app, "_nj_update_lifecycle"), \
             patch.object(self.app, "_nj_update_execution", return_value=True):
            self.app._ensure_managed_paper_journal(mt)
        create.assert_called_once()
        self.assertEqual(
            mt["native_journal_internal_trade_id"],
            self.app._managed_paper_journal_id(mt),
        )

    def test_watcher_pauses_until_initial_paper_insert_is_durable(self):
        """A failed insert cannot let a paper trade advance only in memory."""
        mt = self._managed_trade("MGC")
        mt["entry_epoch"] = 1
        self.app.MANAGED_TRADES_BY_KEY[mt["key"]] = mt
        bar = {"high": 120.0, "low": 80.0, "start": 2}
        with patch.object(self.app, "_fetch_latest_bar", return_value=bar), \
             patch.object(self.app, "_nj_create_from_snapshot", return_value=False), \
             patch.object(self.app, "_evaluate_managed_trade_levels") as evaluate:
            self.app._watch_managed_trades()
        evaluate.assert_not_called()
        self.assertNotIn("native_journal_internal_trade_id", mt)

        with patch.object(self.app, "_fetch_latest_bar", return_value=bar), \
             patch.object(self.app, "_nj_create_from_snapshot", return_value=True), \
             patch.object(self.app, "_nj_update_execution", return_value=True), \
             patch.object(self.app, "_nj_update_lifecycle", return_value=True), \
             patch.object(self.app, "_evaluate_managed_trade_levels") as evaluate:
            self.app._watch_managed_trades()
        evaluate.assert_called_once_with(mt, bar)

    def test_watcher_pauses_attached_paper_until_state_update_is_durable(self):
        """A gateway-linked PAPER row cannot advance before its recovery state saves."""
        mt = self._managed_trade("MNQ")
        mt.update({
            "entry_epoch": 1,
            "native_journal_internal_trade_id": str(uuid.uuid4()),
            "native_journal_source": "paper",
        })
        self.app.MANAGED_TRADES_BY_KEY[mt["key"]] = mt
        bar = {"high": 120.0, "low": 80.0, "start": 2}
        with patch.object(self.app, "_fetch_latest_bar", return_value=bar), \
             patch.object(self.app, "_nj_update_execution", return_value=False), \
             patch.object(self.app, "_evaluate_managed_trade_levels") as evaluate:
            self.app._watch_managed_trades()
        evaluate.assert_not_called()

        with patch.object(self.app, "_fetch_latest_bar", return_value=bar), \
             patch.object(self.app, "_nj_update_execution", return_value=True), \
             patch.object(self.app, "_nj_update_lifecycle", return_value=True), \
             patch.object(self.app, "_evaluate_managed_trade_levels") as evaluate:
            self.app._watch_managed_trades()
        evaluate.assert_called_once_with(mt, bar)

    def test_watcher_reverts_nonterminal_change_when_state_update_fails(self):
        """A non-terminal lifecycle mutation stays absent until it is recoverable."""
        mt = self._managed_trade("MES")
        mt.update({
            "entry_epoch": 1,
            "native_journal_internal_trade_id": str(uuid.uuid4()),
            "native_journal_source": "paper",
            "paper_journal_activated": True,
        })
        mt["_paper_state_persisted"] = self.app._managed_paper_state_snapshot(mt)
        self.app.MANAGED_TRADES_BY_KEY[mt["key"]] = mt
        bar = {"high": 120.0, "low": 80.0, "start": 2}

        def advance_runner(trade, _bar):
            trade["tp1_hit"] = True
            trade["runner_active"] = True

        with patch.object(self.app, "_fetch_latest_bar", return_value=bar), \
             patch.object(self.app, "_nj_update_execution", return_value=False), \
             patch.object(self.app, "_evaluate_managed_trade_levels", side_effect=advance_runner):
            self.app._watch_managed_trades()
        self.assertNotIn("tp1_hit", mt)
        self.assertNotIn("runner_active", mt)

        with patch.object(self.app, "_fetch_latest_bar", return_value=bar), \
             patch.object(self.app, "_nj_update_execution", return_value=True), \
             patch.object(self.app, "_evaluate_managed_trade_levels", side_effect=advance_runner):
            self.app._watch_managed_trades()
        self.assertTrue(mt["tp1_hit"])
        self.assertTrue(mt["runner_active"])

    def test_tp1_break_even_effects_wait_for_durable_paper_state(self):
        """A failed TP1 state write cannot notify or advance the active-trade mirror."""
        mt = self._managed_trade("MGC")
        mt.update({
            "entry_epoch": 1,
            "native_journal_internal_trade_id": str(uuid.uuid4()),
            "native_journal_source": "paper",
            "paper_journal_activated": True,
            "mfe": 0.0,
            "mae": 0.0,
            "remaining_pct": 1.0,
            "tp1_pct": 0.5,
            "tp2_pct": 0.5,
            "tp2": 120.0,
            "runner": None,
        })
        mt["_paper_state_persisted"] = self.app._managed_paper_state_snapshot(mt)
        self.app.MANAGED_TRADES_BY_KEY[mt["key"]] = mt
        self.app.ACTIVE_TRADES_BY_INST["MGC"] = {
            "status": "open", "direction": "Long", "stop_loss": 90.0,
        }
        bar = {"high": 111.0, "low": 95.0, "close": 111.0, "start": 2}
        with patch.object(self.app, "_fetch_latest_bar", return_value=bar), \
             patch.object(self.app, "_scalp_dynamic_lifecycle_enabled", return_value=True), \
             patch.object(self.app, "_nj_update_execution", return_value=False), \
             patch.object(self.app, "_post_management_update") as post:
            self.app._watch_managed_trades()
        self.assertFalse(mt.get("tp1_hit"))
        self.assertFalse(mt.get("be_moved"))
        self.assertEqual(self.app.ACTIVE_TRADES_BY_INST["MGC"]["stop_loss"], 90.0)
        post.assert_not_called()

        with patch.object(self.app, "_fetch_latest_bar", return_value=bar), \
             patch.object(self.app, "_scalp_dynamic_lifecycle_enabled", return_value=True), \
             patch.object(self.app, "_nj_update_execution", return_value=True), \
             patch.object(self.app, "_post_management_update") as post:
            self.app._watch_managed_trades()
        self.assertTrue(mt["tp1_hit"])
        self.assertTrue(mt["be_moved"])
        self.assertEqual(self.app.ACTIVE_TRADES_BY_INST["MGC"]["stop_loss"], 100.0)
        self.assertEqual(post.call_count, 2)

        with patch.object(self.app, "_fetch_latest_bar", return_value=bar), \
             patch.object(self.app, "_scalp_dynamic_lifecycle_enabled", return_value=True), \
             patch.object(self.app, "_nj_update_execution", return_value=True), \
             patch.object(self.app, "_post_management_update") as retry_post:
            self.app._watch_managed_trades()
        retry_post.assert_not_called()


class TestCanonicalAnalyticsSessions(unittest.TestCase):
    def test_learning_and_trade_management_share_dst_aware_et_buckets(self):
        import app
        winter = datetime(2026, 1, 15, 14, 45, tzinfo=timezone.utc)
        summer = datetime(2026, 7, 15, 13, 45, tzinfo=timezone.utc)
        self.assertEqual(app._learning_session_name(winter), "09:30-10:30")
        self.assertEqual(app._learning_session_name(summer), "09:30-10:30")
        self.assertEqual(
            app._trade_mgmt_session_name(winter),
            app._learning_session_name(winter),
        )


if __name__ == "__main__":
    unittest.main()
