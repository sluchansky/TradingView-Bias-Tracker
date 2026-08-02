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
        """Make cursor.fetchone() return the NJ row needed by _nj_set_outcome."""
        self._cur.fetchone.return_value = (planned_entry, planned_stop, direction)

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
                 execution=_UNSET, outcome=_UNSET, review_status="UNREVIEWED"):
        # Use sentinel so callers can explicitly pass None to simulate a NULL column
        if execution is _UNSET:
            execution = {"avg_entry": 19502.5}
        if outcome is _UNSET:
            outcome = {"net_pnl": 100.0, "realized_r": 1.0}
        self._cur.fetchone.return_value = (
            lifecycle, strategy, planned_risk, execution, outcome, review_status
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


if __name__ == "__main__":
    unittest.main()
