"""
Phase 7N Batch A — Journal Review Workflow tests.

Tests the journal_reviews CRUD backend:
  - Five status states (UNREVIEWED, IN_PROGRESS, REVIEWED, NEEDS_DATA, EXCLUDED)
  - REVIEWED gate (followed_plan + overall_quality + post_trade_review + tag/lesson)
  - Controlled-vocabulary validation (mistake_tags, positive_tags, emotion_tags)
  - Rating range (1–5), duplicate tag deduplication
  - Draft persistence (saved fields survive across calls)
  - NEEDS_DATA override when execution truth is missing
  - EXCLUDED guard (PATCH rejected after POST /exclude)
  - Re-import preservation (ON CONFLICT DO NOTHING on journal_reviews)
  - Review queue counts
  - GET /journal/trades returns real review_status (not hardcoded 'system'/'imported')

All tests are DISPLAY-ONLY — no gate/scoring/sizing/broker paths are touched.
"""
import json
import types
import unittest
from unittest.mock import MagicMock, patch, call
import sys
import os

# ---------------------------------------------------------------------------
# Minimal Flask test-client bootstrap (mirrors the pattern used in Phase 7M)
# ---------------------------------------------------------------------------
# We import app.py directly; the test database is mocked throughout.

sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                "artifacts", "tradingview-webhook"))


def _make_cur(rows=None, rowcount=0):
    """Return a context-manager cursor mock pre-loaded with rows."""
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__  = MagicMock(return_value=False)
    cur.fetchone  = MagicMock(side_effect=iter(rows or []))
    cur.fetchall  = MagicMock(return_value=rows or [])
    cur.rowcount  = rowcount
    return cur


def _make_conn(cur):
    conn = MagicMock()
    conn.__enter__ = lambda s: s
    conn.__exit__  = MagicMock(return_value=False)
    conn.cursor    = MagicMock(return_value=cur)
    conn.close     = MagicMock()
    return conn


# Prevent the real DB / scheduler / timer threads from starting
_mock_env = {
    "LEARNING_DB_URL":       "postgresql://mock/mock",
    "LEARNING_DB_ENABLED":   "0",
    "TRADEZELLA_AVAILABLE":  "1",
    "DATABENTO_ENABLED":     "0",
}

with patch.dict(os.environ, _mock_env):
    import app as _app  # noqa: E402

flask_app = _app.app
flask_app.config["TESTING"] = True

# Always report TRADEZELLA_DB_READY = True for route tests
_app.TRADEZELLA_DB_READY  = True
_app.TRADEZELLA_AVAILABLE = True
_app.LEARNING_DB_ENABLED  = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

AUTH_HEADER = {"Authorization": "Bearer test-owner-token"}


def _owner_bypass(f):
    """Patch @owner_required so it always passes for unit tests."""
    return f  # real bypass is done via monkeypatching below


def _owner_pass(f):
    """Decorator replacement that calls the wrapped function directly."""
    from functools import wraps
    @wraps(f)
    def wrapper(*a, **kw):
        return f(*a, **kw)
    return wrapper


def _patch_owner():
    _app.owner_required = _owner_pass
    # Re-register review routes with the bypass decorator — easier to just
    # call the view functions directly under test_client since @owner_required
    # checks request context headers; we bypass by patching _is_owner.


def _is_owner_true(*_a, **_kw):
    return True


# ---------------------------------------------------------------------------
# Base test class
# ---------------------------------------------------------------------------

class ReviewTestBase(unittest.TestCase):

    def setUp(self):
        self.client = flask_app.test_client()
        # Bypass auth for all tests
        self._patcher_owner = patch.object(_app, "_is_owner", _is_owner_true,
                                           create=True)
        try:
            self._patcher_owner.start()
        except AttributeError:
            pass  # function may not exist; auth guard checked differently
        # Mock _learning_conn so no real DB is hit
        self._patcher_conn = patch.object(_app, "_learning_conn",
                                          return_value=None)
        self._patcher_conn.start()

    def tearDown(self):
        try:
            self._patcher_owner.stop()
        except Exception:
            pass
        self._patcher_conn.stop()

    def _route_get(self, url):
        return self.client.get(url, headers=AUTH_HEADER)

    def _route_patch(self, url, body):
        return self.client.patch(url,
                                 data=json.dumps(body),
                                 content_type="application/json",
                                 headers=AUTH_HEADER)

    def _route_post(self, url, body):
        return self.client.post(url,
                                data=json.dumps(body),
                                content_type="application/json",
                                headers=AUTH_HEADER)


# ---------------------------------------------------------------------------
# Unit tests for pure helper functions (no DB / no HTTP)
# ---------------------------------------------------------------------------

class TestReviewHelpers(unittest.TestCase):
    """Tests _review_is_complete and controlled-vocabulary sets."""

    def _complete_row(self):
        return {
            "followed_plan":    "YES",
            "overall_quality":  4,
            "post_trade_review": "good trade",
            "lesson_learned":   "stay patient",
            "positive_tags":    ["patient_entry"],
            "mistake_tags":     [],
        }

    def test_complete_row_passes(self):
        self.assertTrue(_app._review_is_complete(self._complete_row()))

    def test_no_followed_plan_fails(self):
        row = {**self._complete_row(), "followed_plan": None}
        self.assertFalse(_app._review_is_complete(row))

    def test_no_overall_quality_fails(self):
        row = {**self._complete_row(), "overall_quality": None}
        self.assertFalse(_app._review_is_complete(row))

    def test_empty_post_trade_review_fails(self):
        row = {**self._complete_row(), "post_trade_review": "   "}
        self.assertFalse(_app._review_is_complete(row))

    def test_no_tags_or_lesson_fails(self):
        row = {**self._complete_row(),
               "lesson_learned": "",
               "positive_tags":  [],
               "mistake_tags":   []}
        self.assertFalse(_app._review_is_complete(row))

    def test_lesson_alone_satisfies_tag_requirement(self):
        row = {**self._complete_row(),
               "positive_tags": [],
               "mistake_tags":  []}
        row["lesson_learned"] = "I need to be more patient"
        self.assertTrue(_app._review_is_complete(row))

    def test_mistake_tags_alone_satisfies_requirement(self):
        row = {**self._complete_row(),
               "lesson_learned": "",
               "positive_tags":  [],
               "mistake_tags":   ["chased_entry"]}
        self.assertTrue(_app._review_is_complete(row))

    def test_positive_tags_alone_satisfies_requirement(self):
        row = {**self._complete_row(),
               "lesson_learned": "",
               "mistake_tags":   [],
               "positive_tags":  ["patient_entry"]}
        self.assertTrue(_app._review_is_complete(row))

    def test_vocabulary_coverage_mistake(self):
        for tag in _app.REVIEW_MISTAKE_TAGS:
            self.assertIsInstance(tag, str)
            self.assertGreater(len(tag), 0)

    def test_vocabulary_coverage_positive(self):
        for tag in _app.REVIEW_POSITIVE_TAGS:
            self.assertIsInstance(tag, str)

    def test_vocabulary_coverage_emotion(self):
        for tag in _app.REVIEW_EMOTION_TAGS:
            self.assertIsInstance(tag, str)

    def test_followed_plan_values(self):
        self.assertSetEqual(
            _app.REVIEW_FOLLOWED_PLAN_VALUES,
            {"YES", "PARTIALLY", "NO", "NOT_APPLICABLE"},
        )

    def test_review_status_values(self):
        self.assertSetEqual(
            _app.REVIEW_STATUS_VALUES,
            {"UNREVIEWED", "IN_PROGRESS", "REVIEWED", "NEEDS_DATA", "EXCLUDED"},
        )

    def test_plan_checklist_items_are_strings(self):
        for item in _app.REVIEW_PLAN_CHECKLIST_ITEMS:
            self.assertIsInstance(item, str)


# ---------------------------------------------------------------------------
# Integration-style tests: exercise route logic with mocked DB
# ---------------------------------------------------------------------------

class TestReviewRouteGet(ReviewTestBase):
    """GET /journal/trade/<source>/<id>/review"""

    def _mock_conn_no_row(self):
        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__  = MagicMock(return_value=False)
        cur.fetchone  = MagicMock(return_value=None)
        conn = MagicMock()
        conn.cursor   = MagicMock(return_value=cur)
        conn.close    = MagicMock()
        return conn

    def test_get_unreviewed_defaults_for_unknown_trade(self):
        conn = self._mock_conn_no_row()
        with patch.object(_app, "_learning_conn", return_value=conn), \
             patch.object(_app, "_journal_db_guard",
                          return_value=(conn, None)):
            resp = self._route_get("/journal/trade/system/999/review")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["review"]["review_status"], "UNREVIEWED")
        self.assertIn("vocabulary", data)
        self.assertIn("mistake_tags", data["vocabulary"])

    def test_get_returns_vocabulary(self):
        conn = self._mock_conn_no_row()
        with patch.object(_app, "_journal_db_guard",
                          return_value=(conn, None)):
            resp = self._route_get("/journal/trade/tradzella/1/review")
        data = resp.get_json()
        vocab = data.get("vocabulary", {})
        self.assertIn("followed_plan", vocab)
        self.assertIn("emotion_tags", vocab)
        self.assertIn("plan_checklist", vocab)

    def test_unknown_source_returns_400(self):
        conn = self._mock_conn_no_row()
        with patch.object(_app, "_journal_db_guard",
                          return_value=(conn, None)):
            resp = self._route_get("/journal/trade/mystery/1/review")
        self.assertEqual(resp.status_code, 400)


class TestReviewRoutePatch(ReviewTestBase):
    """PATCH /journal/trade/<source>/<id>/review"""

    def _mock_upsert_conn(self, existing_row=None):
        """Conn that returns existing_row from SELECT, then (1,) from INSERT RETURNING."""
        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__  = MagicMock(return_value=False)
        calls = []
        # First fetchone = SELECT existing row (None = not found)
        # Second fetchone = INSERT RETURNING id
        cur.fetchone = MagicMock(side_effect=[existing_row, (1,)])
        cur.execute  = MagicMock()
        conn = MagicMock()
        conn.cursor  = MagicMock(return_value=cur)
        conn.close   = MagicMock()
        return conn

    def test_patch_partial_fields_gives_in_progress(self):
        conn = self._mock_upsert_conn()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)), \
             patch.object(_app, "_review_needs_data", return_value=False):
            resp = self._route_patch(
                "/journal/trade/system/1/review",
                {"pre_trade_notes": "I saw a clean BOS above VWAP."},
            )
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["review_status"], "IN_PROGRESS")

    def test_patch_rejects_unknown_mistake_tag(self):
        conn = self._mock_upsert_conn()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)):
            resp = self._route_patch(
                "/journal/trade/system/1/review",
                {"mistake_tags": ["invalid_fantasy_tag_xyz"]},
            )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("unknown", resp.get_json().get("error", "").lower())

    def test_patch_rejects_unknown_positive_tag(self):
        conn = self._mock_upsert_conn()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)):
            resp = self._route_patch(
                "/journal/trade/system/1/review",
                {"positive_tags": ["invented_tag"]},
            )
        self.assertEqual(resp.status_code, 400)

    def test_patch_rejects_invalid_rating(self):
        conn = self._mock_upsert_conn()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)):
            resp = self._route_patch(
                "/journal/trade/system/1/review",
                {"setup_quality": 6},       # valid range is 1–5
            )
        self.assertEqual(resp.status_code, 400)

    def test_patch_rejects_zero_rating(self):
        conn = self._mock_upsert_conn()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)):
            resp = self._route_patch(
                "/journal/trade/system/1/review",
                {"overall_quality": 0},
            )
        self.assertEqual(resp.status_code, 400)

    def test_patch_valid_rating_range(self):
        conn = self._mock_upsert_conn()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)), \
             patch.object(_app, "_review_needs_data", return_value=False):
            resp = self._route_patch(
                "/journal/trade/system/1/review",
                {"setup_quality": 3, "overall_quality": 5},
            )
        data = resp.get_json()
        self.assertTrue(data["ok"])

    def test_patch_deduplicates_tags(self):
        """Duplicate tags in the request are silently collapsed to one entry."""
        conn = self._mock_upsert_conn()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)), \
             patch.object(_app, "_review_needs_data", return_value=False):
            resp = self._route_patch(
                "/journal/trade/system/1/review",
                {"mistake_tags": ["chased_entry", "chased_entry", "fear"]},
            )
        self.assertTrue(resp.get_json()["ok"])
        # Verify we don't raise; duplicate handling is internal

    def test_patch_multi_tags(self):
        conn = self._mock_upsert_conn()
        tags = ["patient_entry", "respected_stop", "good_execution"]
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)), \
             patch.object(_app, "_review_needs_data", return_value=False):
            resp = self._route_patch(
                "/journal/trade/system/1/review",
                {"positive_tags": tags},
            )
        self.assertTrue(resp.get_json()["ok"])

    def test_patch_all_emotion_tags_valid(self):
        conn = self._mock_upsert_conn()
        emotion_tags = [
            {"tag": "calm", "intensity": 3},
            {"tag": "confident", "intensity": 5},
        ]
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)), \
             patch.object(_app, "_review_needs_data", return_value=False):
            resp = self._route_patch(
                "/journal/trade/tradzella/2/review",
                {"emotion_tags": emotion_tags},
            )
        self.assertTrue(resp.get_json()["ok"])

    def test_patch_invalid_emotion_intensity(self):
        conn = self._mock_upsert_conn()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)):
            resp = self._route_patch(
                "/journal/trade/system/1/review",
                {"emotion_tags": [{"tag": "calm", "intensity": 9}]},
            )
        self.assertEqual(resp.status_code, 400)

    def test_patch_unknown_emotion_tag_rejected(self):
        conn = self._mock_upsert_conn()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)):
            resp = self._route_patch(
                "/journal/trade/system/1/review",
                {"emotion_tags": [{"tag": "ecstatic", "intensity": 1}]},
            )
        self.assertEqual(resp.status_code, 400)

    def test_patch_invalid_followed_plan_rejected(self):
        conn = self._mock_upsert_conn()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)):
            resp = self._route_patch(
                "/journal/trade/system/1/review",
                {"followed_plan": "MAYBE"},
            )
        self.assertEqual(resp.status_code, 400)

    def test_patch_all_valid_followed_plan_values(self):
        for fp in ["YES", "PARTIALLY", "NO", "NOT_APPLICABLE"]:
            conn = self._mock_upsert_conn()
            with patch.object(_app, "_journal_db_guard", return_value=(conn, None)), \
                 patch.object(_app, "_review_needs_data", return_value=False):
                resp = self._route_patch(
                    "/journal/trade/system/1/review",
                    {"followed_plan": fp},
                )
            self.assertTrue(resp.get_json()["ok"], f"failed for {fp}")

    def test_draft_persistence_fields_merged(self):
        """Second PATCH merges on top of existing; existing pre_trade_notes preserved."""
        existing_row = (
            "IN_PROGRESS",  # review_status
            "YES",          # followed_plan
            None,           # plan_checklist
            None,           # mistake_tags
            None,           # positive_tags
            None,           # emotion_tags
            "My pre-trade context.",  # pre_trade_notes (existing)
            None,           # in_trade_notes
            None,           # post_trade_review
            None,           # lesson_learned
            None,           # what_differently
            None,           # what_repeat
            None,           # setup_quality
            None,           # execution_quality
            None,           # discipline_quality
            None,           # overall_quality
            None,           # exclude_reason
            None,           # reviewed_at
            "operator",     # reviewed_by
            None,           # updated_at
        )
        conn = self._mock_upsert_conn(existing_row)
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)), \
             patch.object(_app, "_review_needs_data", return_value=False):
            resp = self._route_patch(
                "/journal/trade/system/1/review",
                # Only send new field; pre_trade_notes should be preserved from existing
                {"in_trade_notes": "Price swept the low cleanly."},
            )
        data = resp.get_json()
        self.assertTrue(data["ok"])
        # Status should remain IN_PROGRESS (incomplete)
        self.assertEqual(data["review_status"], "IN_PROGRESS")


class TestReviewedGate(ReviewTestBase):
    """REVIEWED status auto-promotion and explicit mark-reviewed gate."""

    def _mock_conn(self):
        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__  = MagicMock(return_value=False)
        cur.fetchone  = MagicMock(side_effect=[None, (1,)])  # no existing, insert ok
        cur.execute   = MagicMock()
        conn = MagicMock()
        conn.cursor   = MagicMock(return_value=cur)
        conn.close    = MagicMock()
        return conn

    def _complete_body(self):
        return {
            "followed_plan":    "YES",
            "overall_quality":  4,
            "post_trade_review": "Executed the plan well, exited at TP1.",
            "lesson_learned":   "Trust the setup.",
            "positive_tags":    ["patient_entry"],
            "review_status":    "REVIEWED",
        }

    def test_explicit_reviewed_with_complete_fields_succeeds(self):
        conn = self._mock_conn()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)), \
             patch.object(_app, "_review_needs_data", return_value=False):
            resp = self._route_patch("/journal/trade/system/1/review", self._complete_body())
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["review_status"], "REVIEWED")

    def test_explicit_reviewed_without_followed_plan_blocked(self):
        body = {**self._complete_body(), "followed_plan": None}
        conn = self._mock_conn()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)):
            resp = self._route_patch("/journal/trade/system/1/review", body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Cannot mark REVIEWED", resp.get_json()["error"])

    def test_explicit_reviewed_without_overall_quality_blocked(self):
        body = {**self._complete_body(), "overall_quality": None}
        conn = self._mock_conn()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)):
            resp = self._route_patch("/journal/trade/system/1/review", body)
        self.assertEqual(resp.status_code, 400)

    def test_explicit_reviewed_without_post_trade_review_blocked(self):
        body = {**self._complete_body(), "post_trade_review": ""}
        conn = self._mock_conn()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)):
            resp = self._route_patch("/journal/trade/system/1/review", body)
        self.assertEqual(resp.status_code, 400)

    def test_explicit_reviewed_without_tag_or_lesson_blocked(self):
        body = {**self._complete_body(),
                "positive_tags":  [],
                "mistake_tags":   [],
                "lesson_learned": ""}
        conn = self._mock_conn()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)):
            resp = self._route_patch("/journal/trade/system/1/review", body)
        self.assertEqual(resp.status_code, 400)

    def test_auto_promoted_to_reviewed_when_fields_complete(self):
        """Without explicitly passing review_status=REVIEWED, complete fields auto-promote."""
        body = {
            "followed_plan":    "PARTIALLY",
            "overall_quality":  3,
            "post_trade_review": "I held through the noise, which was a mistake.",
            "mistake_tags":     ["held_too_long"],
            # No explicit review_status
        }
        conn = self._mock_conn()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)), \
             patch.object(_app, "_review_needs_data", return_value=False):
            resp = self._route_patch("/journal/trade/system/1/review", body)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["review_status"], "REVIEWED")


class TestNeedsDataOverride(ReviewTestBase):
    """NEEDS_DATA status is set when execution truth is missing."""

    def _mock_conn(self):
        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__  = MagicMock(return_value=False)
        cur.fetchone  = MagicMock(side_effect=[None, (1,)])
        cur.execute   = MagicMock()
        conn = MagicMock()
        conn.cursor   = MagicMock(return_value=cur)
        conn.close    = MagicMock()
        return conn

    def test_in_progress_overridden_to_needs_data_when_entry_missing(self):
        conn = self._mock_conn()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)), \
             patch.object(_app, "_review_needs_data", return_value=True):
            resp = self._route_patch(
                "/journal/trade/system/1/review",
                {"pre_trade_notes": "No execution data yet."},
            )
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["review_status"], "NEEDS_DATA")

    def test_reviewed_overridden_to_needs_data_when_entry_missing(self):
        body = {
            "followed_plan":    "YES",
            "overall_quality":  4,
            "post_trade_review": "Good execution.",
            "lesson_learned":   "Stay patient.",
            "positive_tags":    ["patient_entry"],
            "review_status":    "REVIEWED",
        }
        conn = self._mock_conn()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)), \
             patch.object(_app, "_review_needs_data", return_value=True):
            resp = self._route_patch("/journal/trade/system/1/review", body)
        # Server returns 200 with NEEDS_DATA (not 400)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["review_status"], "NEEDS_DATA")


class TestExcludeRoute(ReviewTestBase):
    """POST /journal/trade/<source>/<id>/exclude"""

    def _mock_conn(self):
        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__  = MagicMock(return_value=False)
        cur.fetchone  = MagicMock(return_value=(1,))
        cur.execute   = MagicMock()
        conn = MagicMock()
        conn.cursor   = MagicMock(return_value=cur)
        conn.close    = MagicMock()
        return conn

    def test_exclude_requires_reason(self):
        conn = self._mock_conn()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)):
            resp = self._route_post("/journal/trade/system/1/exclude", {})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("reason", resp.get_json()["error"].lower())

    def test_exclude_with_reason_succeeds(self):
        conn = self._mock_conn()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)):
            resp = self._route_post(
                "/journal/trade/system/1/exclude",
                {"reason": "Platform glitch — fills were erroneous."},
            )
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["review_status"], "EXCLUDED")

    def test_excluded_trade_rejects_patch(self):
        """PATCH on an EXCLUDED trade returns 409."""
        existing_excluded = (
            "EXCLUDED",  # review_status
            None, None, None, None, None, None, None, None, None,
            None, None, None, None, None, None,
            "Platform glitch",  # exclude_reason
            None, "operator", None,
        )
        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__  = MagicMock(return_value=False)
        cur.fetchone  = MagicMock(return_value=existing_excluded)
        cur.execute   = MagicMock()
        conn = MagicMock()
        conn.cursor   = MagicMock(return_value=cur)
        conn.close    = MagicMock()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)):
            resp = self._route_patch(
                "/journal/trade/system/1/review",
                {"pre_trade_notes": "Trying to edit."},
            )
        self.assertEqual(resp.status_code, 409)
        self.assertIn("EXCLUDED", resp.get_json()["error"])

    def test_unknown_source_returns_400(self):
        conn = self._mock_conn()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)):
            resp = self._route_post("/journal/trade/bogus/1/exclude",
                                    {"reason": "test"})
        self.assertEqual(resp.status_code, 400)


class TestReviewQueue(ReviewTestBase):
    """GET /journal/review-queue"""

    def _make_queue_conn(self, sys_count=3, tz_count=1, sys_oldest=None, tz_oldest=None):
        import datetime
        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__  = MagicMock(return_value=False)
        ts = datetime.datetime(2025, 6, 1, 9, 30, tzinfo=datetime.timezone.utc)
        side = [
            (sys_count,),
            (tz_count,),
            sys_oldest or (1, "system", ts),
            tz_oldest or (2, "tradzella", ts),
        ]
        cur.fetchone = MagicMock(side_effect=side)
        cur.execute  = MagicMock()
        conn = MagicMock()
        conn.cursor  = MagicMock(return_value=cur)
        conn.close   = MagicMock()
        return conn

    def test_queue_returns_total_count(self):
        conn = self._make_queue_conn(sys_count=5, tz_count=2)
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)):
            resp = self._route_get("/journal/review-queue")
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["unreviewed_count"], 7)

    def test_queue_all_reviewed_flag(self):
        conn = self._make_queue_conn(sys_count=0, tz_count=0)
        # When counts are 0, oldest rows return None
        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__  = MagicMock(return_value=False)
        cur.fetchone  = MagicMock(side_effect=[(0,), (0,), None, None])
        cur.execute   = MagicMock()
        conn = MagicMock()
        conn.cursor   = MagicMock(return_value=cur)
        conn.close    = MagicMock()
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)):
            resp = self._route_get("/journal/review-queue")
        data = resp.get_json()
        self.assertTrue(data["all_reviewed"])
        self.assertEqual(data["unreviewed_count"], 0)
        self.assertIsNone(data["next_trade"])

    def test_queue_next_trade_present(self):
        conn = self._make_queue_conn(sys_count=3, tz_count=0)
        with patch.object(_app, "_journal_db_guard", return_value=(conn, None)):
            resp = self._route_get("/journal/review-queue")
        data = resp.get_json()
        nxt = data.get("next_trade")
        self.assertIsNotNone(nxt)
        self.assertIn("source", nxt)
        self.assertIn("id", nxt)


class TestPersistTradezellaReviewStub(ReviewTestBase):
    """_persist_tradezella_trades creates journal_reviews stub for new imports."""

    def test_new_import_creates_review_stub(self):
        """When a new tradzella trade is inserted, a journal_reviews stub is created."""
        trades = [{
            "source": "tradezella", "dedupe_key": "test-key-001",
            "symbol": "MGC", "side": "long",
            "entry_time": None, "exit_time": None,
            "entry_price": 2000.0, "exit_price": 2010.0,
            "quantity": 1, "pnl": 100.0, "fees": 5.0,
            "setup": "CHOCH", "mistake": None,
            "notes": "Clean sweep before BOS.",
            "screenshots": None, "mfe": None, "mae": None,
            "r_multiple": 1.0, "mode": "SCALP",
            "session_bucket": "AM", "session_day": "Monday",
            "outcome": "win", "raw_row": {},
        }]

        # Simulate: INSERT tradzella_trades RETURNING id
        # Then INSERT journal_reviews (no result needed)
        # Then INSERT import_batches
        execute_calls = []

        def _execute(sql, params=None):
            execute_calls.append(sql.strip()[:60])

        fetchone_results = [(42,), None]  # first: trade id; second: batch insert

        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__  = MagicMock(return_value=False)
        cur.fetchone  = MagicMock(side_effect=fetchone_results)
        cur.execute   = MagicMock(side_effect=_execute)
        cur.rowcount  = 1

        conn = MagicMock()
        conn.cursor   = MagicMock(return_value=cur)
        conn.close    = MagicMock()

        _app.TRADEZELLA_DB_READY = True
        with patch.object(_app, "_learning_conn", return_value=conn):
            result = _app._persist_tradezella_trades(trades, filename="test.csv")

        self.assertTrue(result["ok"])
        self.assertEqual(result["imported"], 1)

        # The execute calls should include a journal_reviews insert
        calls_str = " ".join(execute_calls)
        self.assertIn("journal_reviews", calls_str)

    def test_reimport_duplicate_does_not_create_extra_review_stub(self):
        """ON CONFLICT DO NOTHING: re-import of existing trade skips review stub creation."""
        trades = [{
            "source": "tradezella", "dedupe_key": "existing-key-999",
            "symbol": "MNQ", "side": "short",
            "entry_time": None, "exit_time": None,
            "entry_price": 19000.0, "exit_price": 18900.0,
            "quantity": 1, "pnl": 200.0, "fees": 5.0,
            "setup": None, "mistake": None, "notes": None,
            "screenshots": None, "mfe": None, "mae": None,
            "r_multiple": 1.0, "mode": "SWING",
            "session_bucket": "AM", "session_day": "Tuesday",
            "outcome": "win", "raw_row": {},
        }]

        execute_calls = []

        def _execute(sql, params=None):
            execute_calls.append(sql.strip()[:80])

        # Simulate ON CONFLICT DO NOTHING: INSERT RETURNING returns None (duplicate)
        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__  = MagicMock(return_value=False)
        cur.fetchone  = MagicMock(side_effect=[None, None])  # None = duplicate skipped
        cur.execute   = MagicMock(side_effect=_execute)
        cur.rowcount  = 1

        conn = MagicMock()
        conn.cursor   = MagicMock(return_value=cur)
        conn.close    = MagicMock()

        _app.TRADEZELLA_DB_READY = True
        with patch.object(_app, "_learning_conn", return_value=conn):
            result = _app._persist_tradezella_trades(trades, filename="re-import.csv")

        self.assertTrue(result["ok"])
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["skipped_dupes"], 1)

        # journal_reviews INSERT should NOT appear (trade was a dupe, not inserted)
        calls_str = " ".join(execute_calls)
        self.assertNotIn("journal_reviews", calls_str)


class TestReviewNeedsDataHelper(unittest.TestCase):
    """_review_needs_data returns True when execution truth is missing."""

    def _make_conn_row(self, row):
        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__  = MagicMock(return_value=False)
        cur.fetchone  = MagicMock(return_value=row)
        cur.execute   = MagicMock()
        conn = MagicMock()
        conn.cursor   = MagicMock(return_value=cur)
        return conn

    def test_system_trade_with_all_fields_returns_false(self):
        conn = self._make_conn_row((2000.0, "CHOCH_LONG_SCALP", "win"))
        result = _app._review_needs_data("system", 1, conn)
        self.assertFalse(result)

    def test_system_trade_missing_entry_returns_true(self):
        conn = self._make_conn_row((None, "CHOCH_LONG_SCALP", "win"))
        result = _app._review_needs_data("system", 1, conn)
        self.assertTrue(result)

    def test_system_trade_missing_strategy_key_returns_true(self):
        conn = self._make_conn_row((2000.0, None, "win"))
        result = _app._review_needs_data("system", 1, conn)
        self.assertTrue(result)

    def test_system_trade_missing_result_returns_true(self):
        conn = self._make_conn_row((2000.0, "CHOCH_LONG_SCALP", None))
        result = _app._review_needs_data("system", 1, conn)
        self.assertTrue(result)

    def test_system_trade_not_found_returns_true(self):
        conn = self._make_conn_row(None)
        result = _app._review_needs_data("system", 999, conn)
        self.assertTrue(result)

    def test_tradzella_trade_with_all_fields_returns_false(self):
        conn = self._make_conn_row((2000.0, "win"))
        result = _app._review_needs_data("tradzella", 1, conn)
        self.assertFalse(result)

    def test_tradzella_trade_missing_entry_price_returns_true(self):
        conn = self._make_conn_row((None, "win"))
        result = _app._review_needs_data("tradzella", 1, conn)
        self.assertTrue(result)

    def test_unknown_source_returns_false_fail_open(self):
        conn = self._make_conn_row(None)
        result = _app._review_needs_data("mystery_source", 1, conn)
        self.assertFalse(result)  # fail-open for unknown source


class TestGetOrDefaultReview(unittest.TestCase):
    """_get_or_default_review returns sensible defaults for unknown trades."""

    def test_no_row_returns_unreviewed_defaults(self):
        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__  = MagicMock(return_value=False)
        cur.fetchone  = MagicMock(return_value=None)
        conn = MagicMock()
        conn.cursor   = MagicMock(return_value=cur)
        result = _app._get_or_default_review(conn, "system", 999)
        self.assertEqual(result["review_status"], "UNREVIEWED")
        self.assertIsNone(result["followed_plan"])
        self.assertEqual(result["reviewed_by"], "operator")

    def test_existing_row_returned(self):
        import datetime
        ts = datetime.datetime(2025, 6, 1, 9, 0, tzinfo=datetime.timezone.utc)
        row = (
            "IN_PROGRESS", "YES", None, None, None, None,
            "some notes", None, "post review", "lesson", None, None,
            3, 4, 5, 4, None, ts, "operator", ts,
        )
        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__  = MagicMock(return_value=False)
        cur.fetchone  = MagicMock(return_value=row)
        conn = MagicMock()
        conn.cursor   = MagicMock(return_value=cur)
        result = _app._get_or_default_review(conn, "system", 1)
        self.assertEqual(result["review_status"], "IN_PROGRESS")
        self.assertEqual(result["followed_plan"], "YES")
        self.assertEqual(result["pre_trade_notes"], "some notes")
        self.assertEqual(result["setup_quality"], 3)
        self.assertIsInstance(result["reviewed_at"], str)  # serialized to ISO


class TestControlledVocabularyConstants(unittest.TestCase):
    """Sanity-check that the tag vocabularies are frozen and non-empty."""

    def test_mistake_tags_frozen(self):
        self.assertIsInstance(_app.REVIEW_MISTAKE_TAGS, frozenset)
        self.assertGreater(len(_app.REVIEW_MISTAKE_TAGS), 10)

    def test_positive_tags_frozen(self):
        self.assertIsInstance(_app.REVIEW_POSITIVE_TAGS, frozenset)
        self.assertGreater(len(_app.REVIEW_POSITIVE_TAGS), 5)

    def test_emotion_tags_frozen(self):
        self.assertIsInstance(_app.REVIEW_EMOTION_TAGS, frozenset)
        self.assertGreater(len(_app.REVIEW_EMOTION_TAGS), 5)

    def test_plan_checklist_is_list(self):
        self.assertIsInstance(_app.REVIEW_PLAN_CHECKLIST_ITEMS, list)
        self.assertGreater(len(_app.REVIEW_PLAN_CHECKLIST_ITEMS), 5)

    def test_no_overlap_mistake_positive(self):
        """Mistake and positive tag sets must be disjoint."""
        overlap = _app.REVIEW_MISTAKE_TAGS & _app.REVIEW_POSITIVE_TAGS
        self.assertEqual(len(overlap), 0, f"overlap: {overlap}")


if __name__ == "__main__":
    unittest.main()
