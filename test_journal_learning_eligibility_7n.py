"""
Phase 7N Batch C — Learning Eligibility & Review Analytics tests.

Tests the two new DISPLAY-ONLY routes:
  GET /journal/learning-eligibility
    - ELIGIBLE path (all criteria met + REVIEWED)
    - REVIEW_REQUIRED (not yet reviewed)
    - MISSING_RISK (planned stop not recorded, system only)
    - MISSING_STRATEGY (strategy_key null, system only)
    - INVALID_OUTCOME (null or unrecognised result)
    - EXCLUDED_BY_OPERATOR (review_status = EXCLUDED)
    - Tradezella trades: ELIGIBLE, REVIEW_REQUIRED, INVALID_OUTCOME
    - Completion of review does NOT call any learning weight update

  GET /journal/review-analytics
    - Returns all expected keys
    - Coaching summary surfaces top-3 mistakes and top-3 positives correctly
    - Works when no reviewed trades exist (empty data)
    - Route never touches LEARNING_ELIGIBILITY or any learning formula

All tests are DISPLAY-ONLY — no gate/scoring/sizing/broker paths are touched.
No weight update is called by any code under test.
"""

import json
import types
import unittest
from unittest.mock import MagicMock, patch, PropertyMock, call
import sys
import os

# ---------------------------------------------------------------------------
# Minimal Flask test-client bootstrap (mirrors the Phase 7N Batch A pattern)
# ---------------------------------------------------------------------------

sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                "artifacts", "tradingview-webhook"))

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

_app.TRADEZELLA_DB_READY  = True
_app.TRADEZELLA_AVAILABLE = True
_app.LEARNING_DB_ENABLED  = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

AUTH_HEADER = {"Authorization": "Bearer test-owner-token"}


def _is_owner_true(*_a, **_kw):
    return True


def _make_cur_multi(fetchall_side_effects, fetchone_side_effects=None,
                    description_side_effects=None):
    """Cursor mock with per-call side effects for fetchall, fetchone, description."""
    from unittest.mock import PropertyMock

    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__  = MagicMock(return_value=False)

    if fetchall_side_effects is not None:
        cur.fetchall = MagicMock(side_effect=fetchall_side_effects)
    else:
        cur.fetchall = MagicMock(return_value=[])

    if fetchone_side_effects is not None:
        cur.fetchone = MagicMock(side_effect=fetchone_side_effects)
    else:
        cur.fetchone = MagicMock(return_value=(0,))

    if description_side_effects is not None:
        type(cur).description = PropertyMock(side_effect=description_side_effects)
    else:
        type(cur).description = PropertyMock(return_value=[])

    return cur


def _make_conn(cur):
    conn = MagicMock()
    conn.__enter__ = lambda s: s
    conn.__exit__  = MagicMock(return_value=False)
    conn.cursor    = MagicMock(return_value=cur)
    conn.close     = MagicMock()
    return conn


# ---------------------------------------------------------------------------
# Base test class
# ---------------------------------------------------------------------------

class EligibilityTestBase(unittest.TestCase):

    def setUp(self):
        self.client = flask_app.test_client()
        self._patcher_owner = patch.object(_app, "_is_owner", _is_owner_true,
                                           create=True)
        try:
            self._patcher_owner.start()
        except AttributeError:
            pass
        self._patcher_conn = patch.object(_app, "_learning_conn",
                                          return_value=None)
        self._patcher_conn.start()

    def tearDown(self):
        try:
            self._patcher_owner.stop()
        except Exception:
            pass
        self._patcher_conn.stop()

    def _get(self, url):
        return self.client.get(url, headers=AUTH_HEADER)


# ---------------------------------------------------------------------------
# Column descriptor helpers
# ---------------------------------------------------------------------------

# Columns returned by the eligibility route for system trades
_SYS_DESC = [("id",), ("result",), ("strategy_key",), ("stop",), ("review_status",)]
# Columns returned by the eligibility route for tradezella trades
_TZ_DESC  = [("id",), ("result",), ("review_status",)]


def _elig_conn(sys_rows, tz_rows):
    """Build a mocked connection that returns sys_rows then tz_rows."""
    cur = _make_cur_multi(
        fetchall_side_effects=[sys_rows, tz_rows],
        description_side_effects=[_SYS_DESC, _TZ_DESC],
    )
    return _make_conn(cur)


# ---------------------------------------------------------------------------
# Tests: /journal/learning-eligibility
# ---------------------------------------------------------------------------

class TestLearningEligibilityRoute(EligibilityTestBase):
    """Tests for GET /journal/learning-eligibility."""

    def _get_elig(self, sys_rows, tz_rows=()):
        conn = _elig_conn(sys_rows, list(tz_rows))
        with patch.object(_app, "_learning_conn", return_value=conn):
            return self._get("/journal/learning-eligibility")

    # ── System trade paths ──────────────────────────────────────────────────

    def test_eligible_system_trade(self):
        """All criteria met → ELIGIBLE."""
        r = self._get_elig(
            sys_rows=[(1, "win", "CHOCH_DEMAND", 100.0, "REVIEWED")],
        )
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d["ok"])
        records = {(rec["source"], rec["trade_id"]): rec for rec in d["records"]}
        rec = records[("system", 1)]
        self.assertEqual(rec["status"], "ELIGIBLE")
        self.assertEqual(rec["reason"], "")

    def test_review_required_system_trade(self):
        """Trade not yet reviewed → REVIEW_REQUIRED."""
        r = self._get_elig(
            sys_rows=[(2, "loss", "CHOCH_DEMAND", 99.5, "UNREVIEWED")],
        )
        d = r.get_json()
        self.assertTrue(d["ok"])
        rec = d["records"][0]
        self.assertEqual(rec["status"], "REVIEW_REQUIRED")
        self.assertIn("UNREVIEWED", rec["reason"])

    def test_review_required_in_progress(self):
        """IN_PROGRESS (draft) → REVIEW_REQUIRED."""
        r = self._get_elig(
            sys_rows=[(3, "win", "CHOCH_DEMAND", 100.0, "IN_PROGRESS")],
        )
        d = r.get_json()
        rec = d["records"][0]
        self.assertEqual(rec["status"], "REVIEW_REQUIRED")
        self.assertIn("IN_PROGRESS", rec["reason"])

    def test_missing_risk_system_trade(self):
        """Stop (planned risk) is None → MISSING_RISK."""
        r = self._get_elig(
            sys_rows=[(4, "win", "CHOCH_DEMAND", None, "REVIEWED")],
        )
        d = r.get_json()
        rec = d["records"][0]
        self.assertEqual(rec["status"], "MISSING_RISK")
        self.assertIn("stop", rec["reason"].lower())

    def test_missing_strategy_system_trade(self):
        """strategy_key is None → MISSING_STRATEGY."""
        r = self._get_elig(
            sys_rows=[(5, "win", None, 100.0, "REVIEWED")],
        )
        d = r.get_json()
        rec = d["records"][0]
        self.assertEqual(rec["status"], "MISSING_STRATEGY")
        self.assertIn("strategy", rec["reason"].lower())

    def test_missing_strategy_empty_string(self):
        """strategy_key is empty string → MISSING_STRATEGY."""
        r = self._get_elig(
            sys_rows=[(6, "win", "", 100.0, "REVIEWED")],
        )
        d = r.get_json()
        rec = d["records"][0]
        self.assertEqual(rec["status"], "MISSING_STRATEGY")

    def test_invalid_outcome_null_result(self):
        """result is None → INVALID_OUTCOME."""
        r = self._get_elig(
            sys_rows=[(7, None, "CHOCH_DEMAND", 100.0, "REVIEWED")],
        )
        d = r.get_json()
        rec = d["records"][0]
        self.assertEqual(rec["status"], "INVALID_OUTCOME")

    def test_invalid_outcome_unrecognised_result(self):
        """result is an unknown value → INVALID_OUTCOME."""
        r = self._get_elig(
            sys_rows=[(8, "PARTIAL", "CHOCH_DEMAND", 100.0, "REVIEWED")],
        )
        d = r.get_json()
        rec = d["records"][0]
        self.assertEqual(rec["status"], "INVALID_OUTCOME")

    def test_excluded_by_operator(self):
        """review_status = EXCLUDED → EXCLUDED_BY_OPERATOR."""
        r = self._get_elig(
            sys_rows=[(9, "win", "CHOCH_DEMAND", 100.0, "EXCLUDED")],
        )
        d = r.get_json()
        rec = d["records"][0]
        self.assertEqual(rec["status"], "EXCLUDED_BY_OPERATOR")
        self.assertIn("excluded", rec["reason"].lower())

    def test_excluded_takes_priority_over_missing_data(self):
        """EXCLUDED is checked before INVALID_OUTCOME and MISSING_STRATEGY."""
        r = self._get_elig(
            sys_rows=[(10, None, None, None, "EXCLUDED")],
        )
        d = r.get_json()
        rec = d["records"][0]
        self.assertEqual(rec["status"], "EXCLUDED_BY_OPERATOR")

    # ── Tradezella paths ────────────────────────────────────────────────────

    def test_eligible_tradzella_trade(self):
        """Tradezella: all criteria met → ELIGIBLE."""
        r = self._get_elig(
            sys_rows=[],
            tz_rows=[(1, "win", "REVIEWED")],
        )
        d = r.get_json()
        self.assertTrue(d["ok"])
        recs = {(rec["source"], rec["trade_id"]): rec for rec in d["records"]}
        rec = recs[("tradzella", 1)]
        self.assertEqual(rec["status"], "ELIGIBLE")

    def test_review_required_tradzella(self):
        """Tradezella: UNREVIEWED → REVIEW_REQUIRED."""
        r = self._get_elig(sys_rows=[], tz_rows=[(2, "loss", "UNREVIEWED")])
        d = r.get_json()
        rec = d["records"][0]
        self.assertEqual(rec["status"], "REVIEW_REQUIRED")

    def test_invalid_outcome_tradzella(self):
        """Tradezella: null outcome → INVALID_OUTCOME."""
        r = self._get_elig(sys_rows=[], tz_rows=[(3, None, "REVIEWED")])
        d = r.get_json()
        rec = d["records"][0]
        self.assertEqual(rec["status"], "INVALID_OUTCOME")

    def test_excluded_tradzella(self):
        """Tradezella: EXCLUDED → EXCLUDED_BY_OPERATOR."""
        r = self._get_elig(sys_rows=[], tz_rows=[(4, "win", "EXCLUDED")])
        d = r.get_json()
        rec = d["records"][0]
        self.assertEqual(rec["status"], "EXCLUDED_BY_OPERATOR")

    # ── Response shape ──────────────────────────────────────────────────────

    def test_response_shape(self):
        """Route returns ok, records list, total, and counts dict."""
        r = self._get_elig(
            sys_rows=[(1, "win", "STRAT", 100.0, "REVIEWED"),
                      (2, "win", "STRAT", 100.0, "UNREVIEWED")],
        )
        d = r.get_json()
        self.assertIn("ok", d)
        self.assertIn("records", d)
        self.assertIn("total", d)
        self.assertIn("counts", d)
        self.assertEqual(d["total"], 2)
        self.assertEqual(d["counts"]["ELIGIBLE"], 1)
        self.assertEqual(d["counts"]["REVIEW_REQUIRED"], 1)

    def test_empty_db_returns_empty_list(self):
        """No trades → ok=True, records=[], total=0."""
        r = self._get_elig(sys_rows=[], tz_rows=[])
        d = r.get_json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["records"], [])
        self.assertEqual(d["total"], 0)

    def test_trade_id_is_int(self):
        """trade_id in response is always an integer."""
        r = self._get_elig(sys_rows=[(42, "win", "STRAT", 100.0, "REVIEWED")])
        d = r.get_json()
        self.assertIsInstance(d["records"][0]["trade_id"], int)

    def test_valid_outcome_case_insensitive(self):
        """'WIN' in uppercase also passes outcome check."""
        r = self._get_elig(
            sys_rows=[(11, "WIN", "STRAT", 100.0, "REVIEWED")],
        )
        d = r.get_json()
        rec = d["records"][0]
        self.assertEqual(rec["status"], "ELIGIBLE")

    def test_be_outcome_is_valid(self):
        """'be' (break-even) is a valid outcome."""
        r = self._get_elig(sys_rows=[(12, "be", "STRAT", 100.0, "REVIEWED")])
        d = r.get_json()
        self.assertEqual(d["records"][0]["status"], "ELIGIBLE")

    def test_scratch_outcome_is_valid(self):
        """'scratch' is a valid outcome."""
        r = self._get_elig(sys_rows=[(13, "scratch", "STRAT", 100.0, "REVIEWED")])
        d = r.get_json()
        self.assertEqual(d["records"][0]["status"], "ELIGIBLE")

    def test_db_unavailable_returns_503(self):
        """When _learning_conn returns None the route returns 503."""
        # _patcher_conn already mocks _learning_conn → None
        r = self._get("/journal/learning-eligibility")
        self.assertEqual(r.status_code, 503)

    # ── Learning isolation ──────────────────────────────────────────────────

    def test_no_learning_weight_update_on_eligible(self):
        """Completing a review does NOT call _update_learning_weights or equivalent."""
        # Patch any weight-update function to detect an accidental call
        weight_update = MagicMock()
        with patch.dict(vars(_app), {
            "_update_learning_weights": weight_update,
            "update_learning_weights":  weight_update,
        }, clear=False):
            self._get_elig(sys_rows=[(1, "win", "STRAT", 100.0, "REVIEWED")])
        weight_update.assert_not_called()

    def test_learning_eligibility_cache_not_modified(self):
        """Route never writes to LEARNING_ELIGIBILITY in-memory cache."""
        before = dict(_app.LEARNING_ELIGIBILITY)  # snapshot
        self._get_elig(
            sys_rows=[(1, "win", "STRAT", 100.0, "REVIEWED"),
                      (2, None, None, None, "UNREVIEWED")],
        )
        # Cache must be unchanged
        self.assertEqual(_app.LEARNING_ELIGIBILITY, before)


# ---------------------------------------------------------------------------
# Tests: /journal/review-analytics
# ---------------------------------------------------------------------------

# Column descriptors for the analytics route queries (8 queries total)
_ANALYTICS_DESCRIPTIONS = [
    [("tag",), ("n",)],           # mistake_tag_counts
    [("tag",), ("n",)],           # positive_tag_counts
    [("followed_plan",), ("n",)], # followed_plan_distribution
    [("setup_quality",), ("n",)], # rating: setup
    [("execution_quality",), ("n",)],
    [("discipline_quality",), ("n",)],
    [("overall_quality",), ("n",)],
    [("day",), ("emotion",), ("n",)],  # emotion_by_day
    [("discipline_quality",), ("n",), ("wins",)],  # win_rate_by_discipline
]


def _analytics_conn(
    mistake_rows=None,
    positive_rows=None,
    followed_rows=None,
    rating_rows=None,        # list of 4 lists (one per rating field)
    emotion_rows=None,
    discipline_rows=None,
    reviewed_count=0,
):
    """Build a mocked conn that satisfies all 8+1 queries in review_analytics."""
    mistake_rows   = mistake_rows  or []
    positive_rows  = positive_rows or []
    followed_rows  = followed_rows or []
    rating_rows    = rating_rows   or [[], [], [], []]  # 4 fields
    emotion_rows   = emotion_rows  or []
    discipline_rows = discipline_rows or []

    from unittest.mock import PropertyMock

    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__  = MagicMock(return_value=False)

    # fetchall: mistake, positive, followed, 4x rating, emotion, discipline = 9
    cur.fetchall = MagicMock(side_effect=[
        mistake_rows, positive_rows, followed_rows,
        *rating_rows,
        emotion_rows, discipline_rows,
    ])
    # fetchone: reviewed count
    cur.fetchone = MagicMock(return_value=(reviewed_count,))

    conn = MagicMock()
    conn.__enter__ = lambda s: s
    conn.__exit__  = MagicMock(return_value=False)
    conn.cursor    = MagicMock(return_value=cur)
    conn.close     = MagicMock()
    return conn


class TestReviewAnalyticsRoute(EligibilityTestBase):
    """Tests for GET /journal/review-analytics."""

    def _get_analytics(self, **kwargs):
        conn = _analytics_conn(**kwargs)
        with patch.object(_app, "_learning_conn", return_value=conn):
            return self._get("/journal/review-analytics")

    def test_response_keys_present(self):
        """Route returns all required keys."""
        r = self._get_analytics(reviewed_count=0)
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d["ok"])
        for key in (
            "mistake_tag_counts", "positive_tag_counts",
            "followed_plan_distribution", "rating_distributions",
            "emotion_by_day", "win_rate_by_discipline_quality",
            "coaching_summary",
        ):
            self.assertIn(key, d, f"Key '{key}' missing from response")

    def test_empty_db_returns_ok(self):
        """No reviewed trades → ok=True, all lists empty."""
        r = self._get_analytics(reviewed_count=0)
        d = r.get_json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["mistake_tag_counts"], [])
        self.assertEqual(d["positive_tag_counts"], [])
        self.assertEqual(d["followed_plan_distribution"], {})
        self.assertEqual(d["emotion_by_day"], [])

    def test_coaching_summary_top_mistakes(self):
        """coaching_summary.top_mistakes contains at most 3 entries, highest first."""
        mistake_rows = [
            ("chased_entry",  8),
            ("fear",          5),
            ("oversized",     3),
            ("held_too_long", 1),
        ]
        r = self._get_analytics(mistake_rows=mistake_rows, reviewed_count=10)
        d = r.get_json()
        top = d["coaching_summary"]["top_mistakes"]
        self.assertEqual(len(top), 3)
        self.assertEqual(top[0]["tag"], "chased_entry")
        self.assertEqual(top[0]["count"], 8)
        self.assertEqual(top[1]["tag"], "fear")
        self.assertEqual(top[2]["tag"], "oversized")

    def test_coaching_summary_top_positives(self):
        """coaching_summary.top_positives contains at most 3 entries, highest first."""
        positive_rows = [
            ("patient_entry",       6),
            ("respected_stop",      4),
            ("good_execution",      2),
            ("managed_calmly",      1),
        ]
        r = self._get_analytics(positive_rows=positive_rows, reviewed_count=5)
        d = r.get_json()
        top = d["coaching_summary"]["top_positives"]
        self.assertEqual(len(top), 3)
        self.assertEqual(top[0]["tag"], "patient_entry")
        self.assertEqual(top[1]["tag"], "respected_stop")
        self.assertEqual(top[2]["tag"], "good_execution")

    def test_coaching_summary_reviewed_count(self):
        """coaching_summary.reviewed_count reflects the DB count."""
        r = self._get_analytics(reviewed_count=42)
        d = r.get_json()
        self.assertEqual(d["coaching_summary"]["reviewed_count"], 42)

    def test_followed_plan_distribution(self):
        """followed_plan_distribution maps option → count correctly."""
        followed_rows = [("YES", 10), ("PARTIALLY", 5), ("NO", 3)]
        r = self._get_analytics(followed_rows=followed_rows, reviewed_count=18)
        d = r.get_json()
        dist = d["followed_plan_distribution"]
        self.assertEqual(dist["YES"], 10)
        self.assertEqual(dist["PARTIALLY"], 5)
        self.assertEqual(dist["NO"], 3)

    def test_rating_distributions_all_fields(self):
        """rating_distributions contains all four quality fields."""
        r = self._get_analytics(
            rating_rows=[
                [(3, 5), (4, 3)],   # setup_quality
                [(2, 4), (5, 2)],   # execution_quality
                [(4, 6)],           # discipline_quality
                [(5, 10)],          # overall_quality
            ],
            reviewed_count=10,
        )
        d = r.get_json()
        dist = d["rating_distributions"]
        for field in ("setup_quality", "execution_quality",
                      "discipline_quality", "overall_quality"):
            self.assertIn(field, dist)
        # JSON serialises integer keys to strings
        sq = dist["setup_quality"]
        self.assertEqual(sq.get("3") or sq.get(3), 5)
        self.assertEqual(sq.get("4") or sq.get(4), 3)

    def test_win_rate_by_discipline_quality(self):
        """win_rate_by_discipline_quality computes correct percentages."""
        discipline_rows = [
            (3, 10, 5),   # quality=3, n=10, wins=5 → 50%
            (4, 8,  6),   # quality=4, n=8,  wins=6 → 75%
            (5, 4,  4),   # quality=5, n=4,  wins=4 → 100%
        ]
        r = self._get_analytics(discipline_rows=discipline_rows, reviewed_count=22)
        d = r.get_json()
        wr = d["win_rate_by_discipline_quality"]
        # Keys are ints in Python but JSON coerces to strings; accept both
        wr_int = {int(k): v for k, v in wr.items()}
        self.assertAlmostEqual(wr_int[3], 50.0, places=0)
        self.assertAlmostEqual(wr_int[4], 75.0, places=0)
        self.assertAlmostEqual(wr_int[5], 100.0, places=0)

    def test_coaching_summary_fewer_than_three_tags(self):
        """Fewer than 3 mistake tags → top_mistakes returns only what's there."""
        r = self._get_analytics(
            mistake_rows=[("fear", 3)],
            reviewed_count=3,
        )
        d = r.get_json()
        top = d["coaching_summary"]["top_mistakes"]
        self.assertEqual(len(top), 1)
        self.assertEqual(top[0]["tag"], "fear")

    def test_db_unavailable_returns_503(self):
        """When _learning_conn returns None the route returns 503."""
        # _patcher_conn mocks → None
        r = self._get("/journal/review-analytics")
        self.assertEqual(r.status_code, 503)

    def test_no_learning_weight_update_called(self):
        """review-analytics route never calls any learning weight update."""
        weight_update = MagicMock()
        with patch.dict(vars(_app), {
            "_update_learning_weights": weight_update,
            "update_learning_weights":  weight_update,
        }, clear=False):
            self._get_analytics(reviewed_count=5)
        weight_update.assert_not_called()

    def test_learning_eligibility_cache_unchanged(self):
        """review-analytics never writes to the in-memory LEARNING_ELIGIBILITY."""
        before = dict(_app.LEARNING_ELIGIBILITY)
        self._get_analytics(reviewed_count=3)
        self.assertEqual(_app.LEARNING_ELIGIBILITY, before)

    def test_mistake_tag_list_sorted_descending(self):
        """mistake_tag_counts is ordered highest count first."""
        # DB returns already sorted (route trusts ORDER BY n DESC)
        mistake_rows = [("chased_entry", 9), ("fear", 4), ("oversized", 1)]
        r = self._get_analytics(mistake_rows=mistake_rows, reviewed_count=5)
        d = r.get_json()
        counts = [x["count"] for x in d["mistake_tag_counts"]]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_emotion_by_day_list(self):
        """emotion_by_day returns day/emotion/count triples."""
        emotion_rows = [("2026-07-01", "fear", 2), ("2026-07-02", "calm", 3)]
        r = self._get_analytics(emotion_rows=emotion_rows, reviewed_count=5)
        d = r.get_json()
        ebd = d["emotion_by_day"]
        self.assertEqual(len(ebd), 2)
        self.assertEqual(ebd[0]["day"], "2026-07-01")
        self.assertEqual(ebd[0]["emotion"], "fear")
        self.assertEqual(ebd[1]["count"], 3)


# ---------------------------------------------------------------------------
# Integration: eligibility route whitelist
# ---------------------------------------------------------------------------

class TestEligibilityRouteWhitelisted(EligibilityTestBase):
    """Check the routes are registered (not 404 from proxy whitelist logic)."""

    def test_learning_eligibility_route_exists(self):
        """GET /journal/learning-eligibility should not 404 (returns 503 without DB)."""
        r = self._get("/journal/learning-eligibility")
        self.assertNotEqual(r.status_code, 404)

    def test_review_analytics_route_exists(self):
        """GET /journal/review-analytics should not 404 (returns 503 without DB)."""
        r = self._get("/journal/review-analytics")
        self.assertNotEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
