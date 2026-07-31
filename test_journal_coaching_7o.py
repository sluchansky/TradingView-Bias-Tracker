"""
Phase 7O — Journal Coaching Dashboard tests.

Tests the display-only GET /journal/coaching route covering:
  - Data coverage (no trades, partial, complete)
  - Costliest mistakes (R aggregation, exclusion of EXCLUDED trades)
  - Best behaviors (positive tags, sorted by avg_r)
  - Followed-plan comparison (YES/PARTIAL/NO)
  - Emotion analytics (JSONB, intensity)
  - Rating distributions + win-rate correlation
  - Strategy coaching (min-sample confidence)
  - Session analytics (session + DOW grouping)
  - Discipline trend (IMPROVING / STABLE / DECLINING / INSUFFICIENT_DATA)
  - Coaching summary (deterministic, biggest_leak / best_habit / next_focus)
  - Priority score ordering (|net_R|×2 + freq×1) × min(1, n/20))
  - Safety: no learning-weight update, no gate mutation

INVARIANT: No money-path, gate, learning formula, or weight is touched.
"""

import json
import types
import unittest
from unittest.mock import MagicMock, patch, PropertyMock
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                "artifacts", "tradingview-webhook"))

_mock_env = {
    "LEARNING_DB_URL":      "postgresql://mock/mock",
    "LEARNING_DB_ENABLED":  "0",
    "TRADEZELLA_AVAILABLE": "1",
    "DATABENTO_ENABLED":    "0",
}
with patch.dict(os.environ, _mock_env):
    import app as _app

flask_app = _app.app
flask_app.config["TESTING"] = True
_app.TRADEZELLA_DB_READY  = True
_app.TRADEZELLA_AVAILABLE = True
_app.LEARNING_DB_ENABLED  = True

AUTH = {"Authorization": "Bearer test-owner"}


# ---------------------------------------------------------------------------
# Cursor / connection factory
# ---------------------------------------------------------------------------

# Column descriptors for each of the 12 DB calls in journal_coaching:
# 1  coverage fetchone + description
# 2  mistake_tags
# 3  positive_tags
# 4  followed_plan
# 5  emotion_tags
# 6  setup_quality
# 7  execution_quality
# 8  discipline_quality
# 9  overall_quality
# 10 strategy_coaching
# 11 session_analytics
# 12 discipline_trend

_COV_DESC  = [("total",),("reviewed",),("excluded",),("incomplete",),
              ("unreviewed",),("instruments",),("sources",),("modes",)]
_MST_DESC  = [("tag",),("n",),("wins",),("losses",),("net_r",),("avg_r",),
              ("net_pnl",),("avg_loss_r",),("sum_pos_r",),("sum_neg_r",),
              ("instruments",),("sessions",)]
_BEH_DESC  = [("tag",),("n",),("wins",),("net_r",),("avg_r",),
              ("sum_pos_r",),("sum_neg_r",),("plan_follow_pct",)]
_PLN_DESC  = [("followed_plan",),("n",),("wins",),("net_r",),("avg_r",),
              ("sum_pos_r",),("sum_neg_r",),("avg_setup",),
              ("avg_execution",),("avg_discipline",)]
_EMO_DESC  = [("emotion",),("n",),("avg_intensity",),("wins",),
              ("net_r",),("avg_r",),("plan_follow_pct",),("sessions",)]
_RAT_DESC  = [("rating",),("n",),("wins",),("avg_r",),("net_r",)]
_STR_DESC  = [("strategy_name",),("n",),("wins",),("net_r",),("avg_r",),
              ("sum_pos_r",),("sum_neg_r",),("plan_follow_pct",),("sessions",)]
_SES_DESC  = [("session",),("dow",),("n",),("wins",),("net_r",),
              ("avg_r",),("plan_follow_pct",)]
_TRD_DESC  = [("week_start",),("n",),("avg_discipline",),("avg_execution",),
              ("avg_overall",),("net_r",),("followed_plan_pct",),
              ("mistake_rate_pct",)]


def _make_coaching_conn(
    cov_row=(0,0,0,0,0,0,0,0),
    mistake_rows=None,
    behavior_rows=None,
    plan_rows=None,
    emotion_rows=None,
    rating_rows=None,   # list of 4 lists (setup/exec/discipline/overall)
    strategy_rows=None,
    session_rows=None,
    trend_rows=None,
):
    mistake_rows  = mistake_rows  or []
    behavior_rows = behavior_rows or []
    plan_rows     = plan_rows     or []
    emotion_rows  = emotion_rows  or []
    rating_rows   = rating_rows   or [[], [], [], []]
    strategy_rows = strategy_rows or []
    session_rows  = session_rows  or []
    trend_rows    = trend_rows    or []

    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__  = MagicMock(return_value=False)

    # fetchone: coverage only
    cur.fetchone = MagicMock(return_value=cov_row)

    # fetchall: 11 calls (mistake, behavior, plan, emotion, 4 ratings, strategy, session, trend)
    cur.fetchall = MagicMock(side_effect=[
        mistake_rows,
        behavior_rows,
        plan_rows,
        emotion_rows,
        *rating_rows,   # 4 lists
        strategy_rows,
        session_rows,
        trend_rows,
    ])

    # description: 12 accesses (1 coverage + 11 fetchall)
    type(cur).description = PropertyMock(side_effect=[
        _COV_DESC,    # coverage
        _MST_DESC,    # mistake
        _BEH_DESC,    # behavior
        _PLN_DESC,    # plan
        _EMO_DESC,    # emotion
        _RAT_DESC,    # setup_quality
        _RAT_DESC,    # execution_quality
        _RAT_DESC,    # discipline_quality
        _RAT_DESC,    # overall_quality
        _STR_DESC,    # strategy
        _SES_DESC,    # session
        _TRD_DESC,    # trend
    ])

    conn = MagicMock()
    conn.__enter__ = lambda s: s
    conn.__exit__  = MagicMock(return_value=False)
    conn.cursor    = MagicMock(return_value=cur)
    conn.close     = MagicMock()
    return conn


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class CoachingBase(unittest.TestCase):

    def setUp(self):
        self.client = flask_app.test_client()
        self._patcher_owner = patch.object(_app, "_is_owner",
                                           lambda *a, **kw: True, create=True)
        try:
            self._patcher_owner.start()
        except Exception:
            pass
        # Default: _learning_conn → None (503) unless overridden per test
        self._patcher_conn = patch.object(_app, "_learning_conn",
                                          return_value=None)
        self._patcher_conn.start()

    def tearDown(self):
        try:
            self._patcher_owner.stop()
        except Exception:
            pass
        self._patcher_conn.stop()

    def _get(self, url="", params=""):
        return self.client.get(f"/journal/coaching{params}",
                               headers=AUTH)

    def _get_coaching(self, **kwargs):
        conn = _make_coaching_conn(**kwargs)
        with patch.object(_app, "_learning_conn", return_value=conn):
            return self.client.get("/journal/coaching", headers=AUTH)


# ---------------------------------------------------------------------------
# 1. Route registration & DB guard
# ---------------------------------------------------------------------------

class TestCoachingRoute(CoachingBase):

    def test_route_exists_not_404(self):
        """Route is registered — 404 would mean it's missing from Flask."""
        r = self._get()
        self.assertNotEqual(r.status_code, 404)

    def test_db_unavailable_returns_503(self):
        """When _learning_conn returns None → 503 (DB not ready)."""
        r = self._get()
        self.assertEqual(r.status_code, 503)

    def test_empty_db_returns_ok(self):
        """All-zero DB → ok=True, all analytics sections empty."""
        r = self._get_coaching()
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["costliest_mistakes"], [])
        self.assertEqual(d["best_behaviors"], [])
        self.assertEqual(d["followed_plan_analytics"], [])
        self.assertEqual(d["emotion_analytics"], [])
        self.assertEqual(d["strategy_coaching"], [])
        self.assertEqual(d["session_analytics"], [])
        self.assertEqual(d["discipline_trend"]["weekly"], [])

    def test_response_keys_present(self):
        """All required top-level keys must appear in the response."""
        r = self._get_coaching()
        d = r.get_json()
        for key in ("data_coverage", "costliest_mistakes", "best_behaviors",
                    "followed_plan_analytics", "emotion_analytics",
                    "rating_analytics", "strategy_coaching", "session_analytics",
                    "discipline_trend", "coaching_summary", "coaching_priority"):
            self.assertIn(key, d, f"Key '{key}' missing")


# ---------------------------------------------------------------------------
# 2. Data coverage
# ---------------------------------------------------------------------------

class TestDataCoverage(CoachingBase):

    def test_coverage_counts_correct(self):
        """Coverage numbers propagate from DB row correctly."""
        r = self._get_coaching(
            cov_row=(50, 30, 5, 3, 12, 2, 2, 1),
        )
        d = r.get_json()
        cov = d["data_coverage"]
        self.assertEqual(cov["total"],      50)
        self.assertEqual(cov["reviewed"],   30)
        self.assertEqual(cov["excluded"],    5)
        self.assertEqual(cov["incomplete"],  3)
        self.assertEqual(cov["unreviewed"], 12)

    def test_coverage_confidence_strong(self):
        """≥50 reviewed → STRONG_EVIDENCE."""
        r = self._get_coaching(cov_row=(60, 50, 0, 0, 10, 3, 2, 2))
        d = r.get_json()
        self.assertEqual(d["data_coverage"]["confidence"], "STRONG_EVIDENCE")

    def test_coverage_confidence_insufficient(self):
        """<5 reviewed → INSUFFICIENT_DATA."""
        r = self._get_coaching(cov_row=(10, 3, 0, 0, 7, 2, 1, 1))
        d = r.get_json()
        self.assertEqual(d["data_coverage"]["confidence"], "INSUFFICIENT_DATA")

    def test_coverage_confidence_early(self):
        """5–19 reviewed → EARLY_SIGNAL."""
        r = self._get_coaching(cov_row=(20, 8, 0, 0, 12, 1, 1, 1))
        d = r.get_json()
        self.assertEqual(d["data_coverage"]["confidence"], "EARLY_SIGNAL")


# ---------------------------------------------------------------------------
# 3. Costliest Mistakes
# ---------------------------------------------------------------------------

class TestCostliestMistakes(CoachingBase):

    def _mistake_row(self, tag, n, wins, losses, net_r, avg_r,
                     net_pnl=0.0, avg_loss_r=None,
                     sum_pos_r=0.0, sum_neg_r=0.0,
                     instruments="MGC", sessions="ny_open"):
        return (tag, n, wins, losses, net_r, avg_r, net_pnl, avg_loss_r,
                sum_pos_r, sum_neg_r, instruments, sessions)

    def test_mistake_sorted_by_net_r_asc(self):
        """Mistakes are returned worst-first (most negative net_r first)."""
        rows = [
            self._mistake_row("chased_entry",  3, 1, 2, -3.5, -1.17, sum_neg_r=3.5),
            self._mistake_row("fear",          2, 1, 1, -1.0, -0.50, sum_neg_r=1.0),
        ]
        # DB returns already sorted by net_r ASC (route trusts ORDER BY)
        r = self._get_coaching(mistake_rows=rows)
        d = r.get_json()
        mst = d["costliest_mistakes"]
        self.assertEqual(len(mst), 2)
        self.assertEqual(mst[0]["tag"], "chased_entry")
        self.assertLessEqual(mst[0]["net_r"], mst[1]["net_r"])

    def test_mistake_win_rate_computed(self):
        """win_rate = wins/n * 100."""
        rows = [self._mistake_row("fear", 4, 1, 3, -3.0, -0.75, sum_neg_r=3.0)]
        r = self._get_coaching(mistake_rows=rows)
        d = r.get_json()
        self.assertAlmostEqual(d["costliest_mistakes"][0]["win_rate"], 25.0)

    def test_mistake_profit_factor_none_when_no_losers(self):
        """profit_factor is None when sum_neg_r = 0."""
        rows = [self._mistake_row("early_exit", 3, 3, 0, 1.5, 0.5,
                                   sum_pos_r=1.5, sum_neg_r=0.0)]
        r = self._get_coaching(mistake_rows=rows)
        d = r.get_json()
        self.assertIsNone(d["costliest_mistakes"][0]["profit_factor"])

    def test_mistake_confidence_early(self):
        """5–19 occurrences → EARLY_SIGNAL confidence."""
        rows = [self._mistake_row("oversized", 7, 3, 4, -4.0, -0.57,
                                   sum_neg_r=4.0)]
        r = self._get_coaching(mistake_rows=rows)
        d = r.get_json()
        self.assertEqual(d["costliest_mistakes"][0]["confidence"], "EARLY_SIGNAL")

    def test_breakeven_result_counted(self):
        """Breakeven trades count correctly (not win, not loss)."""
        rows = [self._mistake_row("held_too_long", 5, 0, 0,
                                   0.0, 0.0)]
        r = self._get_coaching(mistake_rows=rows)
        d = r.get_json()
        self.assertEqual(d["costliest_mistakes"][0]["n"], 5)
        self.assertEqual(d["costliest_mistakes"][0]["win_rate"], 0.0)


# ---------------------------------------------------------------------------
# 4. Best Behaviors
# ---------------------------------------------------------------------------

class TestBestBehaviors(CoachingBase):

    def _beh_row(self, tag, n, wins, net_r, avg_r, plan_pct,
                 sum_pos_r=0.0, sum_neg_r=0.0):
        return (tag, n, wins, net_r, avg_r, sum_pos_r, sum_neg_r, plan_pct)

    def test_behaviors_sorted_by_avg_r_desc(self):
        """Best behaviors: DB returns highest avg_r first (ORDER BY avg_r DESC)."""
        rows = [
            self._beh_row("waited_confirm", 10, 7, 7.5, 0.75, 90.0),
            self._beh_row("sized_correctly",  6, 4, 2.4, 0.40, 80.0),
        ]
        r = self._get_coaching(behavior_rows=rows)
        d = r.get_json()
        beh = d["best_behaviors"]
        self.assertEqual(beh[0]["tag"], "waited_confirm")
        self.assertGreaterEqual(beh[0]["avg_r"], beh[1]["avg_r"])

    def test_behavior_win_rate_computed(self):
        """win_rate computed from wins/n."""
        rows = [self._beh_row("patient", 5, 4, 2.0, 0.40, 60.0)]
        r = self._get_coaching(behavior_rows=rows)
        d = r.get_json()
        self.assertAlmostEqual(d["best_behaviors"][0]["win_rate"], 80.0)

    def test_behavior_profit_factor_computed(self):
        """profit_factor = sum_pos / sum_neg."""
        rows = [self._beh_row("plan_followed", 6, 4, 2.0, 0.33, 100.0,
                               sum_pos_r=3.0, sum_neg_r=1.0)]
        r = self._get_coaching(behavior_rows=rows)
        d = r.get_json()
        self.assertAlmostEqual(d["best_behaviors"][0]["profit_factor"], 3.0)


# ---------------------------------------------------------------------------
# 5. Followed-Plan Analytics
# ---------------------------------------------------------------------------

class TestFollowedPlan(CoachingBase):

    def _plan_row(self, plan, n, wins, net_r, avg_r,
                  sum_pos=0.0, sum_neg=0.0,
                  avg_setup=None, avg_exec=None, avg_disc=None):
        return (plan, n, wins, net_r, avg_r,
                sum_pos, sum_neg, avg_setup, avg_exec, avg_disc)

    def test_plan_comparison_yes_vs_no(self):
        """YES and NO groups both present with correct avg_r."""
        rows = [
            self._plan_row("YES", 15, 10, 9.3,  0.62, avg_setup=4.0),
            self._plan_row("NO",   8,  2, -6.5, -0.81, avg_setup=2.5),
        ]
        r = self._get_coaching(plan_rows=rows)
        d = r.get_json()
        plans = {p["followed_plan"]: p for p in d["followed_plan_analytics"]}
        self.assertAlmostEqual(plans["YES"]["avg_r"],  0.62)
        self.assertAlmostEqual(plans["NO"]["avg_r"], -0.81)

    def test_not_applicable_handled(self):
        """NOT_APPLICABLE is included but doesn't affect YES/NO."""
        rows = [
            self._plan_row("YES", 5, 3, 2.0, 0.4),
            self._plan_row("NOT_APPLICABLE", 3, 2, 0.5, 0.17),
        ]
        r = self._get_coaching(plan_rows=rows)
        d = r.get_json()
        plans = {p["followed_plan"]: p for p in d["followed_plan_analytics"]}
        self.assertIn("YES", plans)
        self.assertIn("NOT_APPLICABLE", plans)

    def test_win_rate_computed_for_plan(self):
        """win_rate = wins/n for each plan group."""
        rows = [self._plan_row("PARTIALLY", 4, 3, 1.0, 0.25)]
        r = self._get_coaching(plan_rows=rows)
        d = r.get_json()
        self.assertAlmostEqual(
            d["followed_plan_analytics"][0]["win_rate"], 75.0
        )


# ---------------------------------------------------------------------------
# 6. Emotion Analytics
# ---------------------------------------------------------------------------

class TestEmotionAnalytics(CoachingBase):

    def _emo_row(self, emotion, n, avg_intensity, wins, net_r, avg_r,
                 plan_pct=None, sessions="ny_open"):
        return (emotion, n, avg_intensity, wins, net_r, avg_r, plan_pct, sessions)

    def test_emotion_avg_intensity(self):
        """avg_intensity is passed through from DB aggregation."""
        rows = [self._emo_row("fomo", 5, 3.4, 2, -2.5, -0.50)]
        r = self._get_coaching(emotion_rows=rows)
        d = r.get_json()
        self.assertAlmostEqual(
            d["emotion_analytics"][0]["avg_intensity"], 3.4
        )

    def test_emotion_missing_intensity(self):
        """None avg_intensity is handled gracefully."""
        rows = [self._emo_row("calm", 3, None, 2, 1.0, 0.33)]
        r = self._get_coaching(emotion_rows=rows)
        d = r.get_json()
        self.assertIsNone(d["emotion_analytics"][0]["avg_intensity"])

    def test_emotion_win_rate(self):
        """win_rate computed for each emotion."""
        rows = [self._emo_row("fear", 6, 2.5, 2, -4.0, -0.67)]
        r = self._get_coaching(emotion_rows=rows)
        d = r.get_json()
        wr = d["emotion_analytics"][0]["win_rate"]
        self.assertAlmostEqual(wr, 100 * 2 / 6, places=0)

    def test_emotion_overlap_with_plan(self):
        """plan_follow_pct is surfaced from the DB aggregation."""
        rows = [self._emo_row("fomo", 9, 3.0, 1, -4.1, -0.46, plan_pct=22.0)]
        r = self._get_coaching(emotion_rows=rows)
        d = r.get_json()
        self.assertAlmostEqual(
            d["emotion_analytics"][0]["plan_follow_pct"], 22.0
        )


# ---------------------------------------------------------------------------
# 7. Rating Analytics
# ---------------------------------------------------------------------------

class TestRatingAnalytics(CoachingBase):

    def _rat_row(self, rating, n, wins, avg_r, net_r):
        return (rating, n, wins, avg_r, net_r)

    def test_rating_fields_present(self):
        """All four quality fields appear in rating_analytics."""
        r = self._get_coaching(
            rating_rows=[
                [self._rat_row(4, 5, 3, 0.30, 1.5)],   # setup
                [self._rat_row(3, 4, 2, 0.20, 0.8)],   # execution
                [self._rat_row(5, 6, 4, 0.40, 2.4)],   # discipline
                [self._rat_row(4, 7, 5, 0.35, 2.45)],  # overall
            ]
        )
        d = r.get_json()
        for field in ("setup_quality", "execution_quality",
                      "discipline_quality", "overall_quality"):
            self.assertIn(field, d["rating_analytics"],
                          f"'{field}' missing from rating_analytics")

    def test_rating_win_rate_computed(self):
        """win_rate = wins/n for each rating row."""
        r = self._get_coaching(
            rating_rows=[
                [self._rat_row(5, 10, 8, 0.6, 6.0)],
                [], [], [],
            ]
        )
        d = r.get_json()
        rows = d["rating_analytics"]["setup_quality"]
        self.assertAlmostEqual(rows[0]["win_rate"], 80.0)

    def test_rating_quality_independent_of_profitability(self):
        """Losing trades with high quality rating are recorded correctly."""
        # A losing trade with discipline=5 (high discipline, bad luck)
        r = self._get_coaching(
            rating_rows=[
                [],
                [],
                [self._rat_row(5, 3, 0, -0.5, -1.5)],  # all losers
                [],
            ]
        )
        d = r.get_json()
        disc = d["rating_analytics"]["discipline_quality"]
        self.assertEqual(disc[0]["rating"], 5)
        self.assertAlmostEqual(disc[0]["win_rate"], 0.0)
        self.assertAlmostEqual(disc[0]["avg_r"], -0.5)


# ---------------------------------------------------------------------------
# 8. Strategy Coaching
# ---------------------------------------------------------------------------

class TestStrategyCoaching(CoachingBase):

    def _strat_row(self, name, n, wins, net_r, avg_r,
                   sum_pos=0.0, sum_neg=0.0, plan_pct=None, sessions="ny_open"):
        return (name, n, wins, net_r, avg_r, sum_pos, sum_neg, plan_pct, sessions)

    def test_min_sample_warning_via_confidence(self):
        """Strategy with n=3 gets INSUFFICIENT_DATA confidence label."""
        r = self._get_coaching(
            strategy_rows=[self._strat_row("CHOCH_DEMAND", 3, 2, 0.9, 0.3)]
        )
        d = r.get_json()
        self.assertEqual(d["strategy_coaching"][0]["confidence"],
                         "INSUFFICIENT_DATA")

    def test_strategy_expectancy_correct(self):
        """avg_r is passed through as expectancy."""
        r = self._get_coaching(
            strategy_rows=[self._strat_row("BOS_SUPPLY", 20, 12, 8.0, 0.4,
                                            sum_pos=10.0, sum_neg=2.0)]
        )
        d = r.get_json()
        self.assertAlmostEqual(d["strategy_coaching"][0]["avg_r"], 0.4)

    def test_strategy_profit_factor_correct(self):
        """profit_factor = sum_pos / sum_neg."""
        r = self._get_coaching(
            strategy_rows=[self._strat_row("ORB", 15, 9, 5.0, 0.33,
                                            sum_pos=8.0, sum_neg=3.0)]
        )
        d = r.get_json()
        self.assertAlmostEqual(d["strategy_coaching"][0]["profit_factor"],
                               8.0 / 3.0, places=1)


# ---------------------------------------------------------------------------
# 9. Session Analytics
# ---------------------------------------------------------------------------

class TestSessionAnalytics(CoachingBase):

    def _sess_row(self, session, dow, n, wins, net_r, avg_r, plan_pct=None):
        return (session, dow, n, wins, net_r, avg_r, plan_pct)

    def test_session_grouping(self):
        """Different sessions are returned as separate rows."""
        rows = [
            self._sess_row("ny_open", 1, 8, 5, 3.0, 0.38),
            self._sess_row("ny_main", 1, 5, 2, -1.5, -0.30),
        ]
        r = self._get_coaching(session_rows=rows)
        d = r.get_json()
        sessions = {s["session"]: s for s in d["session_analytics"]}
        self.assertIn("ny_open", sessions)
        self.assertIn("ny_main", sessions)

    def test_dow_preserved(self):
        """DOW integer is returned as-is for frontend mapping."""
        r = self._get_coaching(
            session_rows=[self._sess_row("ny_open", 3, 5, 3, 1.5, 0.3)]
        )
        d = r.get_json()
        self.assertEqual(d["session_analytics"][0]["dow"], 3)

    def test_session_win_rate(self):
        """win_rate = wins/n per session row."""
        r = self._get_coaching(
            session_rows=[self._sess_row("london", 1, 6, 4, 2.0, 0.33)]
        )
        d = r.get_json()
        self.assertAlmostEqual(
            d["session_analytics"][0]["win_rate"],
            100 * 4 / 6, places=0
        )


# ---------------------------------------------------------------------------
# 10. Discipline Trend
# ---------------------------------------------------------------------------

def _week(week_start, n, avg_discipline, avg_execution=None,
          avg_overall=None, net_r=0.0, plan_pct=None, mistake_pct=None):
    return (week_start, n, avg_discipline, avg_execution,
            avg_overall, net_r, plan_pct, mistake_pct)


class TestDisciplineTrend(CoachingBase):

    def test_insufficient_data_zero_weeks(self):
        """No trend weeks → INSUFFICIENT_DATA."""
        r = self._get_coaching(trend_rows=[])
        d = r.get_json()
        self.assertEqual(d["discipline_trend"]["label"], "INSUFFICIENT_DATA")

    def test_insufficient_data_three_weeks(self):
        """Fewer than 4 weeks → INSUFFICIENT_DATA."""
        rows = [
            _week("2026-07-07", 3, 3.5),
            _week("2026-07-14", 4, 3.8),
            _week("2026-07-21", 5, 4.0),
        ]
        r = self._get_coaching(trend_rows=rows)
        d = r.get_json()
        self.assertEqual(d["discipline_trend"]["label"], "INSUFFICIENT_DATA")

    def test_improving_trend(self):
        """Discipline clearly rising → IMPROVING."""
        rows = [
            _week("2026-06-01", 5, 2.5),
            _week("2026-06-08", 6, 2.8),
            _week("2026-06-15", 7, 3.8),
            _week("2026-06-22", 6, 4.2),
        ]
        r = self._get_coaching(trend_rows=rows)
        d = r.get_json()
        self.assertEqual(d["discipline_trend"]["label"], "IMPROVING")

    def test_declining_trend(self):
        """Discipline clearly falling → DECLINING."""
        rows = [
            _week("2026-06-01", 5, 4.5),
            _week("2026-06-08", 5, 4.2),
            _week("2026-06-15", 5, 3.0),
            _week("2026-06-22", 5, 2.5),
        ]
        r = self._get_coaching(trend_rows=rows)
        d = r.get_json()
        self.assertEqual(d["discipline_trend"]["label"], "DECLINING")

    def test_stable_trend(self):
        """Discipline within ±0.2 → STABLE."""
        rows = [
            _week("2026-06-01", 5, 3.5),
            _week("2026-06-08", 5, 3.6),
            _week("2026-06-15", 5, 3.5),
            _week("2026-06-22", 5, 3.7),
        ]
        r = self._get_coaching(trend_rows=rows)
        d = r.get_json()
        self.assertEqual(d["discipline_trend"]["label"], "STABLE")

    def test_weekly_list_returned(self):
        """discipline_trend.weekly is a list of week objects."""
        rows = [_week("2026-07-14", 4, 3.0)]
        r = self._get_coaching(trend_rows=rows)
        d = r.get_json()
        self.assertIsInstance(d["discipline_trend"]["weekly"], list)
        self.assertEqual(len(d["discipline_trend"]["weekly"]), 1)
        self.assertIn("week_start", d["discipline_trend"]["weekly"][0])


# ---------------------------------------------------------------------------
# 11. Coaching Summary
# ---------------------------------------------------------------------------

class TestCoachingSummary(CoachingBase):

    def _mistake_row(self, tag, n, net_r, avg_r):
        # (tag, n, wins, losses, net_r, avg_r, net_pnl, avg_loss_r, sum_pos, sum_neg, insts, sess)
        return (tag, n, 0, n, net_r, avg_r, net_r * 100, avg_r, 0.0, abs(net_r), "MGC", "ny_open")

    def _beh_row(self, tag, n, avg_r):
        # (tag, n, wins, net_r, avg_r, sum_pos, sum_neg, plan_pct)
        return (tag, n, n, avg_r * n, avg_r, avg_r * n, 0.0, 100.0)

    def test_biggest_leak_is_worst_mistake(self):
        """coaching_summary.biggest_leak = first (worst net_r) mistake."""
        rows = [
            self._mistake_row("chased_entry",  12, -5.8, -0.48),
            self._mistake_row("fear",           5, -1.2, -0.24),
        ]
        r = self._get_coaching(mistake_rows=rows)
        d = r.get_json()
        leak = d["coaching_summary"].get("biggest_leak")
        self.assertIsNotNone(leak)
        self.assertEqual(leak["tag"], "chased_entry")
        self.assertAlmostEqual(leak["net_r"], -5.8, places=1)

    def test_best_habit_is_top_behavior(self):
        """coaching_summary.best_habit = first (best avg_r) behavior."""
        rows = [self._beh_row("waited_confirm", 10, 0.74)]
        r = self._get_coaching(behavior_rows=rows)
        d = r.get_json()
        habit = d["coaching_summary"].get("best_habit")
        self.assertIsNotNone(habit)
        self.assertEqual(habit["tag"], "waited_confirm")
        self.assertAlmostEqual(habit["avg_r"], 0.74, places=2)

    def test_best_habit_text_not_empty(self):
        """best_habit.text is a non-empty human-readable string."""
        rows = [self._beh_row("respected_stop", 7, 0.55)]
        r = self._get_coaching(behavior_rows=rows)
        d = r.get_json()
        text = d["coaching_summary"].get("best_habit", {}).get("text", "")
        self.assertGreater(len(text), 5)

    def test_no_summary_without_data(self):
        """With zero reviewed trades, coaching_summary has only discipline_trend."""
        r = self._get_coaching()
        d = r.get_json()
        sm = d["coaching_summary"]
        self.assertNotIn("biggest_leak", sm)
        self.assertNotIn("best_habit", sm)
        self.assertIn("discipline_trend", sm)

    def test_discipline_trend_in_summary(self):
        """coaching_summary always contains discipline_trend key."""
        r = self._get_coaching()
        d = r.get_json()
        self.assertIn("discipline_trend", d["coaching_summary"])


# ---------------------------------------------------------------------------
# 12. Coaching Priority Score
# ---------------------------------------------------------------------------

class TestCoachingPriority(CoachingBase):

    def _mrow(self, tag, n, net_r):
        # (tag, n, wins, losses, net_r, avg_r, net_pnl, avg_loss_r, sum_pos, sum_neg, insts, sess)
        return (tag, n, 0, n, net_r, net_r / n if n else 0.0,
                net_r * 100, net_r / n if n else 0.0,
                0.0, abs(net_r), "MGC", "ny_open")

    def test_priority_sorted_descending(self):
        """coaching_priority is sorted highest score first."""
        rows = [
            self._mrow("fear",          5, -5.0),   # |net_r|*2=10 + freq=5 = 15 scaled
            self._mrow("oversized",     2, -1.0),   # |net_r|*2=2  + freq=2 = 4  scaled
        ]
        r = self._get_coaching(mistake_rows=rows)
        d = r.get_json()
        pri = d["coaching_priority"]
        self.assertGreater(len(pri), 0)
        if len(pri) >= 2:
            self.assertGreaterEqual(pri[0]["score"], pri[1]["score"])

    def test_priority_higher_net_r_ranks_higher(self):
        """Bigger |net_R| → higher priority score."""
        rows = [
            self._mrow("big_loss",   10, -10.0),
            self._mrow("small_loss",  5,  -1.0),
        ]
        r = self._get_coaching(mistake_rows=rows)
        d = r.get_json()
        pri = {p["tag"]: p["score"] for p in d["coaching_priority"]}
        self.assertGreater(pri["big_loss"], pri["small_loss"])

    def test_priority_sample_weight_scales_down_small_n(self):
        """n=1 sample is penalised vs n=20 (same net_r)."""
        rows = [
            self._mrow("rare",    1, -5.0),   # sample_weight=0.05
            self._mrow("common", 20, -5.0),   # sample_weight=1.0
        ]
        r = self._get_coaching(mistake_rows=rows)
        d = r.get_json()
        pri = {p["tag"]: p["score"] for p in d["coaching_priority"]}
        self.assertGreater(pri["common"], pri["rare"])

    def test_priority_includes_type_field(self):
        """Each priority item has a type field."""
        rows = [self._mrow("fear", 5, -3.0)]
        r = self._get_coaching(mistake_rows=rows)
        d = r.get_json()
        self.assertIn("type", d["coaching_priority"][0])


# ---------------------------------------------------------------------------
# 13. Safety: no learning/gate mutations
# ---------------------------------------------------------------------------

class TestCoachingSafety(CoachingBase):

    def _mistake_row(self, tag, n, net_r):
        return (tag, n, 0, n, net_r, net_r / max(n, 1), 0.0, None,
                0.0, abs(net_r), "MGC", "ny")

    def test_no_learning_weight_update(self):
        """journal_coaching never calls _update_learning_weights."""
        weight_fn = MagicMock()
        with patch.dict(vars(_app), {
            "_update_learning_weights": weight_fn,
            "update_learning_weights":  weight_fn,
        }, clear=False):
            self._get_coaching(
                mistake_rows=[self._mistake_row("fear", 5, -3.0)]
            )
        weight_fn.assert_not_called()

    def test_learning_eligibility_cache_unchanged(self):
        """LEARNING_ELIGIBILITY in-memory cache is not modified."""
        before = dict(_app.LEARNING_ELIGIBILITY)
        self._get_coaching(
            mistake_rows=[self._mistake_row("fear", 5, -3.0)]
        )
        self.assertEqual(_app.LEARNING_ELIGIBILITY, before)

    def test_excluded_trade_not_in_mistaken_analytics(self):
        """EXCLUDED trades are filtered out at the DB level.
        
        The query runs WHERE review_status='REVIEWED', so EXCLUDED rows
        never appear in mistake aggregations. We verify the route returns
        whatever the (mock) DB returns — i.e., EXCLUDED rows must not be
        injected by the route itself.
        """
        # Mock returns empty (DB already excluded them)
        r = self._get_coaching(mistake_rows=[])
        d = r.get_json()
        self.assertEqual(d["costliest_mistakes"], [])


if __name__ == "__main__":
    unittest.main()
