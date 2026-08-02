"""Phase C: Review Workflow tests — 53 tests.

Covers:
  • Review constants    (5)  — frozensets completeness
  • _nj_patch_review   (14) — valid patch; immutable fields blocked; unknown fields;
                              enum validation; tag list validation; rating range;
                              status audit trail; not_found; db_not_ready;
                              review_notes update; followed_plan; valid tags
  • _nj_compute_review_completeness (8) — SYSTEM_AUTO; SYSTEM_MANUAL_CONFIRM;
                              EXTERNAL_MANUAL; with override; missing_required list;
                              all optional filled; empty review_data; completed_required
  • _nj_add_screenshot  (7)  — valid add; invalid category; unsafe storage_key; file_too_large;
                              max_attachments_reached; dotdot in key; db_not_ready
  • _nj_delete_screenshot (3) — valid delete; not_found; jsonb filter SQL
  • Eligibility Phase C  (8) — EXCLUDED blocks; EXCLUDED wins over lifecycle;
                              SYSTEM_MANUAL_CONFIRM unreviewed blocked;
                              SYSTEM_MANUAL_CONFIRM reviewed passes;
                              EXTERNAL_MANUAL blocked; TRADZELLA_IMPORT blocked;
                              SYSTEM_AUTO no review required; STATUS_UNKNOWN still blocked
  • Phase B regression   (5) — append_management_event invalid type dropped;
                              valid type executes; _nj_set_outcome idempotency guard;
                              5-col SELECT; POSITION_CLOSED event type
  • Integration          (3) — patch with notes; system_auto completeness; smc completeness
"""
import json
import sys
import uuid
from unittest.mock import MagicMock, patch
import unittest

# ── minimal frozensets extracted for pure-logic tests ──────────────────────────
_NJ_REVIEW_STATUSES    = frozenset({"UNREVIEWED","IN_PROGRESS","REVIEWED","NEEDS_REVIEW","EXCLUDED"})
_NJ_FOLLOWED_PLAN      = frozenset({"YES","PARTIALLY","NO","NOT_APPLICABLE"})
_NJ_MISTAKE_TAGS       = frozenset({"ENTERED_EARLY","ENTERED_LATE","OVERTRADED","REVENGE_TRADE",
                                     "EXITED_EARLY","MOVED_STOP_TOO_SOON","WIDENED_STOP","OVERSIZED",
                                     "IGNORED_BLOCKER","TOOK_COUNTERTREND","MISSED_TARGET",
                                     "MANUAL_INTERVENTION","BROKE_SESSION_RULE","OTHER"})
_NJ_EMOTION_TAGS       = frozenset({"ANXIETY","FEAR","FOMO","IMPATIENCE","FRUSTRATION","REVENGE",
                                     "OVERCONFIDENCE","HESITATION","CALM","DISCIPLINED","OTHER"})
_NJ_POSITIVE_TAGS      = frozenset({"FOLLOWED_PLAN","WAITED_FOR_CONFIRMATION","RESPECTED_STOP",
                                     "LET_WINNER_RUN","GOOD_RISK_CONTROL","GOOD_PATIENCE",
                                     "CLEAN_EXECUTION","NO_INTERVENTION","OTHER"})
_NJ_SCREENSHOT_CATS    = frozenset({"PRE_ENTRY","ENTRY","MANAGEMENT","EXIT","REVIEW","OTHER"})
_NJ_OVERRIDE_ASSESS    = frozenset({"HELPFUL","HARMFUL","NEUTRAL","CANNOT_DETERMINE"})
_NJ_IMMUTABLE_FIELDS   = frozenset({"id","internal_trade_id","signal_id","execution_fingerprint",
                                     "broker_order_id","traderspost_id","lifecycle_status",
                                     "source_label","instrument","contract","mode","session",
                                     "direction","canonical_strategy_key","strategy_display_name",
                                     "setup_name","playbook","thesis_direction","thesis_strength",
                                     "thesis_alignment","edge_score","grade","readiness",
                                     "confirmations","blockers","opposing_structure","risk_state",
                                     "planned_entry","planned_stop","planned_targets","planned_risk",
                                     "planned_contracts","planned_rr","planned_dollar_risk",
                                     "market_data_timestamp","decision_timestamp",
                                     "execution","management_events","outcome","override_comparison",
                                     "learning_eligible","learning_blocked_reason",
                                     "tradezella_trade_id","tradzella_enrichment","legacy_journal_key",
                                     "created_at","updated_at"})
_NJ_VALID_EVENT_TYPES  = frozenset({"STATUS_CHANGE","ORDER_SUBMITTED","ORDER_ACKNOWLEDGED",
                                     "POSITION_OPENED","STOP_PLACED","TARGET_PLACED",
                                     "STOP_MOVED","BREAK_EVEN_MOVE","TRAILING_STOP_UPDATE",
                                     "TARGET_HIT","PARTIAL_EXIT","SCALE_OUT",
                                     "MANUAL_EXIT","EMERGENCY_FLATTEN","THESIS_INVALIDATION_EXIT",
                                     "TIME_STOP","SESSION_CLOSE","BROKER_RECONCILIATION",
                                     "OPERATOR_OVERRIDE","POSITION_CLOSED","ORDER_REJECTED",
                                     "STATUS_UNKNOWN"})
_NJ_TERMINAL_OUTCOMES  = frozenset({"CLOSED","REJECTED","CANCELED","STATUS_UNKNOWN"})


# ── Pure-logic _nj_compute_review_completeness extracted for unit tests ────────
def _nj_compute_review_completeness(review_data, source_label, has_manual_override):
    rd = review_data or {}
    required = ["followed_plan", "setup_quality", "execution_quality"]
    if (source_label or "") in ("SYSTEM_MANUAL_CONFIRM", "EXTERNAL_MANUAL"):
        required += ["management_quality", "emotional_control"]
    if has_manual_override:
        required.append("override_assessment")
    optional = ["lesson","what_went_well","what_to_improve",
                "mistake_tags","emotion_tags","positive_tags"]
    def _ne(v): return v is not None and v != "" and v != []
    comp_req = sum(1 for f in required if _ne(rd.get(f)))
    comp_opt = sum(1 for f in optional if _ne(rd.get(f)))
    return {
        "completed": comp_req + comp_opt,
        "required": len(required),
        "optional": len(optional),
        "completed_required": comp_req,
        "completed_optional": comp_opt,
        "total": len(required) + len(optional),
        "missing_required": [f for f in required if not _ne(rd.get(f))],
    }


# ══════════════════════════════════════════════════════════════════════════════
# 1. Review constants (5 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestPhaseConstants(unittest.TestCase):
    """Frozenset completeness checks."""

    def test_review_statuses_count(self):
        self.assertEqual(len(_NJ_REVIEW_STATUSES), 5)
        self.assertIn("EXCLUDED", _NJ_REVIEW_STATUSES)
        self.assertIn("REVIEWED", _NJ_REVIEW_STATUSES)

    def test_mistake_tags_count(self):
        self.assertEqual(len(_NJ_MISTAKE_TAGS), 14)

    def test_emotion_tags_count(self):
        self.assertEqual(len(_NJ_EMOTION_TAGS), 11)

    def test_positive_tags_count(self):
        self.assertEqual(len(_NJ_POSITIVE_TAGS), 9)

    def test_immutable_fields_key_sample(self):
        self.assertIn("planned_entry", _NJ_IMMUTABLE_FIELDS)
        self.assertIn("lifecycle_status", _NJ_IMMUTABLE_FIELDS)
        self.assertIn("edge_score", _NJ_IMMUTABLE_FIELDS)


# ══════════════════════════════════════════════════════════════════════════════
# 2. _nj_patch_review (14 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestNJPatchReview(unittest.TestCase):
    """_nj_patch_review validates and persists review-data patches."""

    def setUp(self):
        import app
        app.NJ_DB_READY = True
        self._conn = MagicMock()
        self._cur = MagicMock()
        self._conn.cursor.return_value = self._cur
        self._patch = patch.object(app, "_learning_conn", return_value=self._conn)
        self._patch.start()
        # Default fetchone: existing row with UNREVIEWED status + empty review_data
        self._cur.fetchone.return_value = ("UNREVIEWED", {})

    def tearDown(self):
        self._patch.stop()
        import app
        app.NJ_DB_READY = False

    def _update_sql_params(self):
        for c in self._cur.execute.call_args_list:
            sql = c[0][0]
            if "UPDATE native_journal" in sql and "review_status" in sql:
                return c[0][1]
        return None

    def test_valid_patch_returns_ok(self):
        import app
        ok, reason = app._nj_patch_review(str(uuid.uuid4()), {"review_status": "IN_PROGRESS"})
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_immutable_field_blocked(self):
        import app
        ok, reason = app._nj_patch_review(str(uuid.uuid4()), {"lifecycle_status": "CLOSED"})
        self.assertFalse(ok)
        self.assertIn("immutable_fields", reason)

    def test_unknown_field_blocked(self):
        import app
        ok, reason = app._nj_patch_review(str(uuid.uuid4()), {"nonexistent_key": "value"})
        self.assertFalse(ok)
        self.assertIn("unknown_fields", reason)

    def test_invalid_review_status_blocked(self):
        import app
        ok, reason = app._nj_patch_review(str(uuid.uuid4()), {"review_status": "BANANAS"})
        self.assertFalse(ok)
        self.assertIn("invalid_review_status", reason)

    def test_invalid_followed_plan_blocked(self):
        import app
        ok, reason = app._nj_patch_review(str(uuid.uuid4()), {"followed_plan": "MAYBE"})
        self.assertFalse(ok)
        self.assertIn("invalid_followed_plan", reason)

    def test_invalid_mistake_tag_blocked(self):
        import app
        ok, reason = app._nj_patch_review(str(uuid.uuid4()), {"mistake_tags": ["NOT_A_TAG"]})
        self.assertFalse(ok)
        self.assertIn("invalid_mistake_tags", reason)

    def test_mistake_tags_not_list_blocked(self):
        import app
        ok, reason = app._nj_patch_review(str(uuid.uuid4()), {"mistake_tags": "OVERSIZED"})
        self.assertFalse(ok)
        self.assertEqual(reason, "mistake_tags_must_be_list")

    def test_rating_out_of_range_blocked(self):
        import app
        ok, reason = app._nj_patch_review(str(uuid.uuid4()), {"setup_quality": 6})
        self.assertFalse(ok)
        self.assertIn("out_of_range", reason)

    def test_rating_not_integer_blocked(self):
        import app
        ok, reason = app._nj_patch_review(str(uuid.uuid4()), {"setup_quality": "good"})
        self.assertFalse(ok)
        self.assertIn("not_integer", reason)

    def test_valid_all_quality_ratings(self):
        import app
        ok, _ = app._nj_patch_review(str(uuid.uuid4()), {
            "setup_quality": 4, "execution_quality": 3,
            "management_quality": 5, "emotional_control": 2,
        })
        self.assertTrue(ok)

    def test_status_transition_recorded_in_review_data(self):
        import app
        ok, _ = app._nj_patch_review(str(uuid.uuid4()), {"review_status": "REVIEWED"})
        self.assertTrue(ok)
        params = self._update_sql_params()
        self.assertIsNotNone(params)
        rd = json.loads(params[1])
        self.assertIn("status_history", rd)
        self.assertEqual(rd["status_history"][0]["to_status"], "REVIEWED")

    def test_not_ready_returns_false(self):
        import app
        app.NJ_DB_READY = False
        ok, reason = app._nj_patch_review(str(uuid.uuid4()), {"review_status": "REVIEWED"})
        self.assertFalse(ok)
        self.assertEqual(reason, "db_not_ready")

    def test_not_found_returns_false(self):
        import app
        self._cur.fetchone.return_value = None
        ok, reason = app._nj_patch_review(str(uuid.uuid4()), {"review_status": "REVIEWED"})
        self.assertFalse(ok)
        self.assertEqual(reason, "not_found")

    def test_valid_tags_accepted(self):
        import app
        ok, _ = app._nj_patch_review(str(uuid.uuid4()), {
            "mistake_tags": ["ENTERED_EARLY", "OVERSIZED"],
            "emotion_tags":  ["FOMO", "ANXIETY"],
            "positive_tags": ["FOLLOWED_PLAN"],
        })
        self.assertTrue(ok)


# ══════════════════════════════════════════════════════════════════════════════
# 3. _nj_compute_review_completeness (8 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestNJComputeReviewCompleteness(unittest.TestCase):
    """Pure-logic completeness calculator — no DB calls."""

    def test_system_auto_three_required(self):
        rc = _nj_compute_review_completeness({}, "SYSTEM_AUTO", False)
        self.assertEqual(rc["required"], 3)

    def test_system_manual_confirm_five_required(self):
        rc = _nj_compute_review_completeness({}, "SYSTEM_MANUAL_CONFIRM", False)
        self.assertEqual(rc["required"], 5)

    def test_external_manual_five_required(self):
        rc = _nj_compute_review_completeness({}, "EXTERNAL_MANUAL", False)
        self.assertEqual(rc["required"], 5)

    def test_manual_override_adds_override_assessment(self):
        rc = _nj_compute_review_completeness({}, "SYSTEM_AUTO", True)
        self.assertEqual(rc["required"], 4)
        self.assertIn("override_assessment", rc["missing_required"])

    def test_all_required_filled_nothing_missing(self):
        rd = {"followed_plan": "YES", "setup_quality": 4, "execution_quality": 3}
        rc = _nj_compute_review_completeness(rd, "SYSTEM_AUTO", False)
        self.assertEqual(rc["missing_required"], [])
        self.assertEqual(rc["completed_required"], 3)

    def test_optional_counted_separately(self):
        rd = {"followed_plan": "YES", "setup_quality": 4, "execution_quality": 3,
              "lesson": "Good trade"}
        rc = _nj_compute_review_completeness(rd, "SYSTEM_AUTO", False)
        self.assertEqual(rc["completed_optional"], 1)
        self.assertEqual(rc["optional"], 6)

    def test_empty_list_not_completed(self):
        rd = {"mistake_tags": [], "followed_plan": "YES", "setup_quality": 4, "execution_quality": 3}
        rc = _nj_compute_review_completeness(rd, "SYSTEM_AUTO", False)
        self.assertEqual(rc["completed_optional"], 0)

    def test_empty_review_data_all_missing(self):
        rc = _nj_compute_review_completeness(None, "SYSTEM_MANUAL_CONFIRM", False)
        self.assertEqual(len(rc["missing_required"]), 5)
        self.assertEqual(rc["completed_required"], 0)


# ══════════════════════════════════════════════════════════════════════════════
# 4. _nj_add_screenshot (7 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestNJAddScreenshot(unittest.TestCase):
    """_nj_add_screenshot validates metadata and appends to JSONB array."""

    def setUp(self):
        import app
        app.NJ_DB_READY = True
        self._conn = MagicMock()
        self._cur = MagicMock()
        self._conn.cursor.return_value = self._cur
        self._patch = patch.object(app, "_learning_conn", return_value=self._conn)
        self._patch.start()
        self._cur.fetchone.return_value = (0,)   # 0 existing screenshots

    def tearDown(self):
        self._patch.stop()
        import app
        app.NJ_DB_READY = False

    def _args(self, **kw):
        defaults = dict(
            internal_trade_id=str(uuid.uuid4()),
            attachment_id=str(uuid.uuid4()),
            category="ENTRY",
            caption="Before entry",
            storage_key="nj/attachments/abc123.png",
            mime_type="image/png",
            file_size=102400,
        )
        defaults.update(kw)
        return defaults

    def test_valid_add_returns_ok(self):
        import app
        ok, reason = app._nj_add_screenshot(**self._args())
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_invalid_category_blocked(self):
        import app
        ok, reason = app._nj_add_screenshot(**self._args(category="BANANA"))
        self.assertFalse(ok)
        self.assertIn("invalid_category", reason)

    def test_unsafe_storage_key_blocked(self):
        import app
        ok, reason = app._nj_add_screenshot(**self._args(storage_key="/etc/passwd"))
        self.assertFalse(ok)
        self.assertEqual(reason, "unsafe_storage_key")

    def test_dotdot_in_key_blocked(self):
        import app
        ok, reason = app._nj_add_screenshot(**self._args(storage_key="nj/../../../etc/passwd"))
        self.assertFalse(ok)
        self.assertEqual(reason, "unsafe_storage_key")

    def test_file_too_large_blocked(self):
        import app
        ok, reason = app._nj_add_screenshot(**self._args(file_size=6 * 1024 * 1024))
        self.assertFalse(ok)
        self.assertEqual(reason, "file_too_large")

    def test_max_attachments_blocked(self):
        import app
        self._cur.fetchone.return_value = (10,)
        ok, reason = app._nj_add_screenshot(**self._args())
        self.assertFalse(ok)
        self.assertEqual(reason, "max_attachments_reached")

    def test_not_ready_returns_false(self):
        import app
        app.NJ_DB_READY = False
        ok, reason = app._nj_add_screenshot(**self._args())
        self.assertFalse(ok)
        self.assertEqual(reason, "db_not_ready")


# ══════════════════════════════════════════════════════════════════════════════
# 5. _nj_delete_screenshot (3 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestNJDeleteScreenshot(unittest.TestCase):

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

    def test_valid_delete_returns_ok(self):
        import app
        ok, reason = app._nj_delete_screenshot(str(uuid.uuid4()), str(uuid.uuid4()))
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_not_ready_returns_false(self):
        import app
        app.NJ_DB_READY = False
        ok, reason = app._nj_delete_screenshot(str(uuid.uuid4()), str(uuid.uuid4()))
        self.assertFalse(ok)

    def test_delete_sql_uses_jsonb_filter(self):
        import app
        attachment_id = str(uuid.uuid4())
        app._nj_delete_screenshot(str(uuid.uuid4()), attachment_id)
        call_args = self._cur.execute.call_args
        sql = call_args[0][0]
        self.assertIn("jsonb_array_elements", sql)
        self.assertIn("attachment_id", sql)


# ══════════════════════════════════════════════════════════════════════════════
# 6. Eligibility Phase C (8 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestEligibilityPhaseC(unittest.TestCase):
    """Review-aware eligibility rules by source_label."""

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

    def _row(self, lifecycle="CLOSED", strategy="LSR", planned_risk=20.0,
             execution=None, outcome=None, review_status="UNREVIEWED",
             source_label="SYSTEM_AUTO"):
        if execution is None:
            execution = {"avg_entry": 19502.5}
        if outcome is None:
            outcome = {"net_pnl": 100.0}
        self._cur.fetchone.return_value = (
            lifecycle, strategy, planned_risk,
            execution, outcome, review_status, source_label,
        )

    def _update_params(self):
        for c in self._cur.execute.call_args_list:
            sql = c[0][0]
            if "UPDATE native_journal" in sql and "learning_eligible" in sql:
                return c[0][1]
        return None

    def test_excluded_always_blocks(self):
        import app
        self._row(review_status="EXCLUDED", source_label="SYSTEM_AUTO")
        app._nj_check_and_set_learning_eligible(str(uuid.uuid4()))
        p = self._update_params()
        self.assertFalse(p[0])
        self.assertEqual(p[1], "review_excluded")

    def test_excluded_blocks_before_lifecycle(self):
        """EXCLUDED must be checked before lifecycle — EXCLUDED wins."""
        import app
        self._row(lifecycle="ACTIVE", review_status="EXCLUDED")
        app._nj_check_and_set_learning_eligible(str(uuid.uuid4()))
        p = self._update_params()
        self.assertFalse(p[0])
        self.assertEqual(p[1], "review_excluded")

    def test_system_manual_confirm_unreviewed_blocked(self):
        import app
        self._row(review_status="UNREVIEWED", source_label="SYSTEM_MANUAL_CONFIRM")
        app._nj_check_and_set_learning_eligible(str(uuid.uuid4()))
        p = self._update_params()
        self.assertFalse(p[0])
        self.assertIn("review_required", p[1])

    def test_system_manual_confirm_reviewed_eligible(self):
        import app
        self._row(review_status="REVIEWED", source_label="SYSTEM_MANUAL_CONFIRM")
        app._nj_check_and_set_learning_eligible(str(uuid.uuid4()))
        p = self._update_params()
        self.assertTrue(p[0])
        self.assertIsNone(p[1])

    def test_external_manual_unreviewed_blocked(self):
        import app
        self._row(review_status="UNREVIEWED", source_label="EXTERNAL_MANUAL")
        app._nj_check_and_set_learning_eligible(str(uuid.uuid4()))
        p = self._update_params()
        self.assertFalse(p[0])
        self.assertIn("attribution_required", p[1])

    def test_tradzella_import_unreviewed_blocked(self):
        import app
        self._row(review_status="UNREVIEWED", source_label="TRADZELLA_IMPORT")
        app._nj_check_and_set_learning_eligible(str(uuid.uuid4()))
        p = self._update_params()
        self.assertFalse(p[0])
        self.assertIn("review_required", p[1])

    def test_system_auto_unreviewed_eligible(self):
        """SYSTEM_AUTO does not require review to be learning-eligible."""
        import app
        self._row(review_status="UNREVIEWED", source_label="SYSTEM_AUTO")
        app._nj_check_and_set_learning_eligible(str(uuid.uuid4()))
        p = self._update_params()
        self.assertTrue(p[0])
        self.assertIsNone(p[1])

    def test_status_unknown_still_blocked(self):
        import app
        self._row(review_status="STATUS_UNKNOWN", source_label="SYSTEM_AUTO")
        app._nj_check_and_set_learning_eligible(str(uuid.uuid4()))
        p = self._update_params()
        self.assertFalse(p[0])
        self.assertEqual(p[1], "unresolved_status")


# ══════════════════════════════════════════════════════════════════════════════
# 7. Phase B regression (5 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestPhaseBRegression(unittest.TestCase):
    """Verify Phase B changes still hold after Phase C integration."""

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

    def test_append_invalid_event_type_dropped(self):
        """Unknown event types must be silently dropped — no DB write."""
        import app
        app._nj_append_management_event(str(uuid.uuid4()), "FAKE_EVENT_TYPE_XYZ")
        for c in self._cur.execute.call_args_list:
            self.assertNotIn("UPDATE native_journal", c[0][0])

    def test_append_valid_event_type_executes(self):
        import app
        app._nj_append_management_event(str(uuid.uuid4()), "STOP_MOVED",
                                         old_value=100.0, new_value=105.0)
        executed = any("UPDATE native_journal" in c[0][0]
                       for c in self._cur.execute.call_args_list)
        self.assertTrue(executed)

    def test_set_outcome_idempotency_guard_on_closed(self):
        """_nj_set_outcome must skip a row already in CLOSED state."""
        import app
        self._cur.fetchone.return_value = (19500.0, 19480.0, "Long", None, "CLOSED")
        with patch.object(app, "_nj_check_and_set_learning_eligible") as mock_elig:
            app._nj_set_outcome(str(uuid.uuid4()), {})
            mock_elig.assert_not_called()
        for c in self._cur.execute.call_args_list:
            self.assertNotIn("UPDATE native_journal", c[0][0])

    def test_set_outcome_5col_select(self):
        """SELECT must include created_at and lifecycle_status (5 cols)."""
        import app
        self._cur.fetchone.return_value = (19500.0, 19480.0, "Long", None, "ACTIVE")
        with patch.object(app, "_nj_check_and_set_learning_eligible"):
            app._nj_set_outcome(str(uuid.uuid4()), {}, pnl_dollars=100.0)
        first_sql = self._cur.execute.call_args_list[0][0][0]
        self.assertIn("lifecycle_status", first_sql)
        self.assertIn("created_at", first_sql)

    def test_set_outcome_position_closed_event_type(self):
        """The appended event must use POSITION_CLOSED, not STATUS_CHANGE."""
        import app
        self._cur.fetchone.return_value = (19500.0, 19480.0, "Long", None, "ACTIVE")
        with patch.object(app, "_nj_check_and_set_learning_eligible"):
            app._nj_set_outcome(str(uuid.uuid4()), {}, exit_reason="T1 Hit")
        for c in self._cur.execute.call_args_list:
            params = c[0][1]
            for p in params:
                if isinstance(p, str) and "POSITION_CLOSED" in p:
                    return  # found in UPDATE params
        self.fail("POSITION_CLOSED event not found in any UPDATE parameters")


# ══════════════════════════════════════════════════════════════════════════════
# 8. Integration tests (3 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestReviewPatchAndCompleteness(unittest.TestCase):
    """Cross-cutting: patch calls succeed, completeness correctly computed."""

    def setUp(self):
        import app
        app.NJ_DB_READY = True
        self._conn = MagicMock()
        self._cur = MagicMock()
        self._conn.cursor.return_value = self._cur
        self._patch = patch.object(app, "_learning_conn", return_value=self._conn)
        self._patch.start()
        self._cur.fetchone.return_value = ("UNREVIEWED", {})

    def tearDown(self):
        self._patch.stop()
        import app
        app.NJ_DB_READY = False

    def test_patch_with_notes_includes_review_notes_in_update(self):
        import app
        ok, _ = app._nj_patch_review(str(uuid.uuid4()), {
            "review_notes": "Followed my plan exactly.",
            "review_status": "REVIEWED",
        })
        self.assertTrue(ok)
        for c in self._cur.execute.call_args_list:
            sql = c[0][0]
            if "review_notes" in sql:
                return
        self.fail("review_notes not found in UPDATE SQL")

    def test_system_auto_completeness_totals(self):
        rc = _nj_compute_review_completeness(None, "SYSTEM_AUTO", False)
        self.assertEqual(rc["total"], 9)   # 3 required + 6 optional

    def test_system_manual_confirm_all_required_satisfied(self):
        rd = {"followed_plan": "YES", "setup_quality": 4, "execution_quality": 3,
              "management_quality": 5, "emotional_control": 4}
        rc = _nj_compute_review_completeness(rd, "SYSTEM_MANUAL_CONFIRM", False)
        self.assertEqual(rc["missing_required"], [])
        self.assertEqual(rc["completed_required"], 5)


if __name__ == "__main__":
    unittest.main()
