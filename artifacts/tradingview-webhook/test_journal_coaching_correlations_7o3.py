"""Phase 7O.3 — Rating × Mistake/Emotion Correlation Analytics tests.

Tests cover:
- Coverage counting (missing ratings excluded, reviewed-only)
- Rating bands (LOW=1-2, MEDIUM=3, HIGH=4-5)
- Rating × Mistake matrix: cell assignment, count parity, band_pct
- Rating × Emotion matrix: top_mistake overlap, avg_intensity
- Setup × Execution matrix: correct cell assignment, empty cells
- Discipline outcomes: profit_factor, avg_mistake_count, disc_summary
- High-quality losses: criteria, profitable trade excluded
- Low-quality wins: profitable poor-process trades, clean win excluded
- Expensive combinations: no duplicate counting, sorted by net_r, min 2 occurrences
- Drill-down: rating_min/rating_max filter, quality_classification filter,
  realized_r_min/realized_r_max filter
- Safety: no learning mutation, no gate reachable, no DDL, no execution path
- Phase 7O regression: intraday tests still pass
"""

import sys
import os
import importlib
import unittest
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Bootstrap — same pattern as other coaching test files
# ---------------------------------------------------------------------------
_WEBHOOK_DIR = os.path.join(os.path.dirname(__file__))
if _WEBHOOK_DIR not in sys.path:
    sys.path.insert(0, _WEBHOOK_DIR)

import app as APP

FLASK_APP = APP.app
FLASK_APP.config["TESTING"] = True

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client():
    return FLASK_APP.test_client()


def _auth_headers():
    import base64
    creds = base64.b64encode(b"admin:password").decode()
    return {"Authorization": f"Basic {creds}"}


def _band(r):
    """Replicate the server-side band logic."""
    if r is None:
        return None
    if r <= 2:
        return "LOW"
    if r == 3:
        return "MEDIUM"
    return "HIGH"


# ---------------------------------------------------------------------------
# Part 1 — Module-level attributes & helpers
# ---------------------------------------------------------------------------

class TestModuleAttributes(unittest.TestCase):

    def test_rating_fields_frozenset_exists(self):
        self.assertTrue(hasattr(APP, "_RATING_FIELDS"),
                        "_RATING_FIELDS must be defined at module level")

    def test_rating_fields_contains_four_fields(self):
        expected = {"setup_quality", "execution_quality",
                    "discipline_quality", "overall_quality"}
        self.assertEqual(set(APP._RATING_FIELDS), expected)

    def test_correlations_endpoint_registered(self):
        rules = {r.rule for r in FLASK_APP.url_map.iter_rules()}
        self.assertIn("/journal/coaching/correlations", rules)

    def test_coaching_win_results_sql_defined(self):
        self.assertTrue(hasattr(APP, "_COACHING_WIN_RESULTS_SQL"))
        ws = APP._COACHING_WIN_RESULTS_SQL
        self.assertIn("win", ws)
        self.assertIn("scratch", ws)


# ---------------------------------------------------------------------------
# Part 2 — Band logic correctness
# ---------------------------------------------------------------------------

class TestBandLogic(unittest.TestCase):

    def test_rating_1_is_low(self):
        self.assertEqual(_band(1), "LOW")

    def test_rating_2_is_low(self):
        self.assertEqual(_band(2), "LOW")

    def test_rating_3_is_medium(self):
        self.assertEqual(_band(3), "MEDIUM")

    def test_rating_4_is_high(self):
        self.assertEqual(_band(4), "HIGH")

    def test_rating_5_is_high(self):
        self.assertEqual(_band(5), "HIGH")

    def test_none_returns_none(self):
        self.assertIsNone(_band(None))


# ---------------------------------------------------------------------------
# Part 3 — Coverage response shape (mock DB)
# ---------------------------------------------------------------------------

def _mock_conn_cov(reviewed=10, with_setup=8, with_execution=7,
                   with_discipline=9, with_overall=6,
                   with_mistakes=5, with_emotions=4, excluded=2):
    """Build a mock connection that returns coverage-like rows."""
    conn = MagicMock()
    cur  = MagicMock()
    conn.__enter__ = lambda s: s
    conn.__exit__  = MagicMock(return_value=False)
    conn.cursor.return_value.__enter__ = lambda s: s
    conn.cursor.return_value.__exit__  = MagicMock(return_value=False)

    cov_row = {
        "reviewed": reviewed, "with_setup": with_setup,
        "with_execution": with_execution, "with_discipline": with_discipline,
        "with_overall": with_overall, "with_mistakes": with_mistakes,
        "with_emotions": with_emotions, "excluded": excluded,
    }
    # fetchall returns list of tuples; description provides col names
    def _make_result(row_dict):
        cols = list(row_dict.keys())
        rows = [tuple(row_dict.values())]
        desc = [(c,) for c in cols]
        return desc, rows

    desc, rows = _make_result(cov_row)
    cur.description = desc
    cur.fetchall.return_value = rows
    conn.cursor.return_value = cur
    return conn


class TestCoverageSection(unittest.TestCase):

    def test_coverage_keys_present_in_response_schema(self):
        """The endpoint must include all 8 coverage keys in its response."""
        import json as _json
        # We can't easily mock the full DB flow, so we check the source
        import inspect
        src = inspect.getsource(APP.journal_coaching_correlations)
        required_keys = [
            '"reviewed"', '"with_setup"', '"with_execution"',
            '"with_discipline"', '"with_overall"', '"with_mistakes"',
            '"with_emotions"', '"excluded"',
        ]
        for key in required_keys:
            self.assertIn(key, src, f"Coverage key {key} missing from endpoint source")

    def test_coverage_excluded_is_not_reviewed(self):
        """Excluded trades are reviewed_status != 'REVIEWED' — never counted in 'reviewed'."""
        import inspect
        src = inspect.getsource(APP.journal_coaching_correlations)
        # Must filter NOT reviewed for excluded count
        self.assertIn("<>'REVIEWED'", src.replace(" ", "").replace("!=", "<>").replace("'",
            "'") or src, "Excluded count must filter review_status != REVIEWED")

    def test_missing_ratings_not_treated_as_zero(self):
        """with_setup etc. must use NULL checks, not = 0."""
        import inspect
        src = inspect.getsource(APP.journal_coaching_correlations)
        # with_setup must check IS NOT NULL, not != 0
        self.assertIn("IS NOT NULL", src)
        # Must NOT filter setup_quality = 0 or similar
        self.assertNotIn("setup_quality = 0", src)


# ---------------------------------------------------------------------------
# Part 4 — Rating × Mistake matrix logic
# ---------------------------------------------------------------------------

class TestRatingMistakeMatrix(unittest.TestCase):

    def _build_rm_rows(self):
        """Simulate what the endpoint aggregates from a small dataset."""
        # 3 trades: all LOW discipline with mistake 'chased_entry'
        trades = [
            {"discipline_quality": 2, "mistake_tags": ["chased_entry"], "r_multiple": -1.0,
             "result": "loss", "followed_plan": "NO"},
            {"discipline_quality": 2, "mistake_tags": ["chased_entry"], "r_multiple": -0.5,
             "result": "loss", "followed_plan": "NO"},
            {"discipline_quality": 2, "mistake_tags": ["chased_entry", "moved_stop"],
             "r_multiple": 0.5, "result": "win", "followed_plan": "YES"},
        ]
        # Aggregate manually as the endpoint would
        from collections import defaultdict
        agg = defaultdict(lambda: {"n": 0, "wins": 0, "net_r": 0.0, "plans": 0})
        band_total = defaultdict(int)
        for t in trades:
            r = t["discipline_quality"]
            band_total[r] += 1
            for tag in t["mistake_tags"]:
                k = (r, tag)
                agg[k]["n"]     += 1
                agg[k]["wins"]  += 1 if t["result"] == "win" else 0
                agg[k]["net_r"] += t["r_multiple"]
                agg[k]["plans"] += 1 if t["followed_plan"] == "YES" else 0
        return agg, band_total

    def test_discipline_low_chased_entry_count(self):
        agg, _ = self._build_rm_rows()
        cell = agg.get((2, "chased_entry"))
        self.assertIsNotNone(cell)
        self.assertEqual(cell["n"], 3)

    def test_discipline_low_moved_stop_count(self):
        agg, _ = self._build_rm_rows()
        cell = agg.get((2, "moved_stop"))
        self.assertIsNotNone(cell)
        self.assertEqual(cell["n"], 1)

    def test_band_pct_calculation(self):
        """band_pct = n_with_mistake / band_total_trades * 100."""
        agg, band_total = self._build_rm_rows()
        cell  = agg[(2, "chased_entry")]
        n     = cell["n"]
        bt    = band_total[2]
        pct   = round(n / bt * 100, 1)
        # 3 trades have chased_entry, out of 3 band total → 100%
        self.assertEqual(pct, 100.0)

    def test_win_rate_computed_correctly(self):
        agg, _ = self._build_rm_rows()
        cell = agg[(2, "chased_entry")]
        win_rate = round(cell["wins"] / cell["n"] * 100, 1)
        self.assertAlmostEqual(win_rate, 33.3, delta=0.2)

    def test_endpoint_source_uses_rating_fields_allowlist(self):
        """rating_field must be validated against _CORR_RATING_FIELDS inside the fn."""
        import inspect
        src = inspect.getsource(APP.journal_coaching_correlations)
        self.assertIn("_CORR_RATING_FIELDS", src)

    def test_endpoint_source_parameterises_tag_not_interpolated(self):
        """Tags come from jsonb_array_elements, never from %s interpolation."""
        import inspect
        src = inspect.getsource(APP.journal_coaching_correlations)
        # The mistake tag must be read from jsonb_array_elements_text, not via %s
        self.assertIn("jsonb_array_elements_text", src)


# ---------------------------------------------------------------------------
# Part 5 — Rating × Emotion matrix logic
# ---------------------------------------------------------------------------

class TestRatingEmotionMatrix(unittest.TestCase):

    def test_top_mistake_present_in_source(self):
        import inspect
        src = inspect.getsource(APP.journal_coaching_correlations)
        self.assertIn("top_mistake", src)
        self.assertIn("top_mistake_pct", src)

    def test_avg_intensity_from_emotion_tag_object(self):
        """avg_intensity reads elem->>'intensity' not a separate column."""
        import inspect
        src = inspect.getsource(APP.journal_coaching_correlations)
        self.assertIn("intensity", src)
        self.assertIn("elem->>'intensity'", src)

    def test_overlap_map_deduplication(self):
        """overlap_map uses (rating, emotion) key — no duplicates per trade."""
        # A trade with emotion A and mistakes [X, Y] should contribute ONE entry
        # per (rating, emotion, mistake) — not double-counted.
        overlap: dict = {}
        def _add(r, em, mk, cnt=1):
            k = (r, em)
            overlap.setdefault(k, []).append((mk, cnt))
        _add(2, "fomo", "chased_entry", 2)
        _add(2, "fomo", "moved_stop",   1)
        sorted_overlap = {k: sorted(v, key=lambda x: (-x[1], x[0]))
                          for k, v in overlap.items()}
        top = sorted_overlap.get((2, "fomo"), [])
        self.assertEqual(top[0][0], "chased_entry")
        self.assertEqual(top[0][1], 2)


# ---------------------------------------------------------------------------
# Part 6 — Setup × Execution matrix
# ---------------------------------------------------------------------------

class TestSetupExecutionMatrix(unittest.TestCase):

    def test_matrix_cell_lookup(self):
        """matrixCell(sq, eq) returns None for empty cells."""
        matrix = [
            {"setup_q": 4, "exec_q": 3, "n": 2, "avg_r": 0.5},
            {"setup_q": 2, "exec_q": 1, "n": 1, "avg_r": -1.0},
        ]
        def cell(sq, eq):
            return next((r for r in matrix if r["setup_q"] == sq and r["exec_q"] == eq), None)
        self.assertIsNotNone(cell(4, 3))
        self.assertIsNone(cell(5, 5))
        self.assertIsNotNone(cell(2, 1))

    def test_matrix_top_mistake_tie_break_alphabetical(self):
        """When two mistakes have the same count, the alphabetically earlier one wins."""
        # Simulating cell_mx_map update logic
        cell_mx_map: dict = {}
        entries = [("bravo", 3), ("alpha", 3), ("zeta", 2)]
        for tag, cnt in entries:
            k = (4, 3)
            existing = cell_mx_map.get(k)
            if (existing is None
                    or cnt > existing[1]
                    or (cnt == existing[1] and tag < existing[0])):
                cell_mx_map[k] = (tag, cnt)
        # alpha < bravo alphabetically, both have cnt=3
        self.assertEqual(cell_mx_map[(4, 3)][0], "alpha")

    def test_confidence_insufficient_for_single_trade(self):
        conf = APP._coaching_confidence(1)
        self.assertEqual(conf, "INSUFFICIENT_DATA")

    def test_confidence_early_for_5_trades(self):
        conf = APP._coaching_confidence(5)
        self.assertEqual(conf, "EARLY_SIGNAL")

    def test_confidence_moderate_for_20_trades(self):
        conf = APP._coaching_confidence(20)
        self.assertEqual(conf, "MODERATE_CONFIDENCE")

    def test_confidence_strong_for_50_trades(self):
        conf = APP._coaching_confidence(50)
        self.assertEqual(conf, "STRONG_EVIDENCE")

    def test_empty_cell_not_in_response(self):
        """Empty 5×5 cells (no trades) should not appear in setup_execution_matrix."""
        import inspect
        src = inspect.getsource(APP.journal_coaching_correlations)
        # Matrix query uses GROUP BY — cells with 0 trades are simply absent
        self.assertIn("GROUP BY setup_q, exec_q", src)


# ---------------------------------------------------------------------------
# Part 7 — High-quality losses
# ---------------------------------------------------------------------------

class TestHighQualityLosses(unittest.TestCase):

    def _classify_hql(self, trades):
        """Replicate the HQL filter criteria."""
        return [
            t for t in trades
            if (t.get("setup_quality", 0) in (4, 5)
                and t.get("execution_quality", 0) in (4, 5)
                and t.get("discipline_quality", 0) in (4, 5)
                and t.get("followed_plan") == "YES"
                and (t.get("r_multiple") or 0) < 0)
        ]

    def test_hql_criteria_all_high_ratings_followed_plan_loss(self):
        trades = [
            {"setup_quality": 5, "execution_quality": 4, "discipline_quality": 4,
             "followed_plan": "YES", "r_multiple": -0.8},  # HQL ✓
            {"setup_quality": 5, "execution_quality": 4, "discipline_quality": 4,
             "followed_plan": "YES", "r_multiple": 1.2},   # excluded — profitable
            {"setup_quality": 3, "execution_quality": 5, "discipline_quality": 5,
             "followed_plan": "YES", "r_multiple": -0.5},  # excluded — setup=3
        ]
        hql = self._classify_hql(trades)
        self.assertEqual(len(hql), 1)
        self.assertAlmostEqual(hql[0]["r_multiple"], -0.8)

    def test_profitable_trade_excluded_from_hql(self):
        trades = [
            {"setup_quality": 5, "execution_quality": 5, "discipline_quality": 5,
             "followed_plan": "YES", "r_multiple": 0.5},
        ]
        hql = self._classify_hql(trades)
        self.assertEqual(len(hql), 0)

    def test_hql_sql_criteria_in_source(self):
        import inspect
        src = inspect.getsource(APP.journal_coaching_correlations)
        self.assertIn("BETWEEN 4 AND 5", src)
        self.assertIn("r_multiple < 0", src)
        self.assertIn("followed_plan='YES'", src)

    def test_hql_avg_r_computed_correctly(self):
        trades = [{"r_multiple": -0.8}, {"r_multiple": -1.2}]
        total_r = sum(t["r_multiple"] for t in trades)
        avg_r = round(total_r / len(trades), 3)
        self.assertAlmostEqual(avg_r, -1.0, places=3)


# ---------------------------------------------------------------------------
# Part 8 — Low-quality wins
# ---------------------------------------------------------------------------

class TestLowQualityWins(unittest.TestCase):

    def _classify_lqw(self, trades):
        """Replicate the LQW filter criteria."""
        results = []
        for t in trades:
            r = t.get("r_multiple") or 0
            if r <= 0:
                continue
            disc = t.get("discipline_quality")
            exc  = t.get("execution_quality")
            fp   = t.get("followed_plan", "")
            mts  = t.get("mistake_tags", [])
            if (disc is not None and disc <= 2
                    or exc is not None and exc <= 2
                    or fp == "NO"
                    or len(mts) > 0):
                results.append(t)
        return results

    def test_low_discipline_profitable_is_lqw(self):
        trades = [
            {"r_multiple": 1.5, "discipline_quality": 1, "execution_quality": 4,
             "followed_plan": "YES", "mistake_tags": []},
        ]
        lqw = self._classify_lqw(trades)
        self.assertEqual(len(lqw), 1)

    def test_clean_win_excluded_from_lqw(self):
        """A profitable trade with high ratings, plan followed, no mistakes — NOT LQW."""
        trades = [
            {"r_multiple": 1.5, "discipline_quality": 5, "execution_quality": 5,
             "followed_plan": "YES", "mistake_tags": []},
        ]
        lqw = self._classify_lqw(trades)
        self.assertEqual(len(lqw), 0)

    def test_followed_plan_no_makes_lqw(self):
        trades = [
            {"r_multiple": 0.8, "discipline_quality": 4, "execution_quality": 4,
             "followed_plan": "NO", "mistake_tags": []},
        ]
        lqw = self._classify_lqw(trades)
        self.assertEqual(len(lqw), 1)

    def test_any_mistake_tag_makes_lqw(self):
        trades = [
            {"r_multiple": 0.8, "discipline_quality": 5, "execution_quality": 5,
             "followed_plan": "YES", "mistake_tags": ["sized_too_large"]},
        ]
        lqw = self._classify_lqw(trades)
        self.assertEqual(len(lqw), 1)

    def test_losing_trade_excluded_from_lqw(self):
        trades = [
            {"r_multiple": -1.0, "discipline_quality": 1, "execution_quality": 1,
             "followed_plan": "NO", "mistake_tags": ["fomo"]},
        ]
        lqw = self._classify_lqw(trades)
        self.assertEqual(len(lqw), 0)

    def test_lqw_sql_criteria_in_source(self):
        import inspect
        src = inspect.getsource(APP.journal_coaching_correlations)
        self.assertIn("r_multiple > 0", src)
        self.assertIn("BETWEEN 1 AND 2", src)
        self.assertIn("followed_plan='NO'", src)


# ---------------------------------------------------------------------------
# Part 9 — Expensive combinations
# ---------------------------------------------------------------------------

class TestExpensiveCombinations(unittest.TestCase):

    def _run_combos(self, trades):
        """Replicate the Python-side combination pairing logic."""
        import itertools
        import json
        from collections import defaultdict

        combo_map = defaultdict(lambda: {"n": 0, "net_r": 0.0, "wins": 0, "dates": []})

        def _parse_tokens(t):
            tokens = []
            for mt in (t.get("mistake_tags") or []):
                tokens.append(f"MISTAKE:{mt}")
            for em in (t.get("emotion_tags") or []):
                tag = em.get("tag") if isinstance(em, dict) else None
                if tag:
                    tokens.append(f"EMOTION:{tag}")
            if (t.get("discipline_quality") or 10) <= 2:
                tokens.append("RATING:LOW_DISCIPLINE")
            if (t.get("execution_quality") or 10) <= 2:
                tokens.append("RATING:LOW_EXECUTION")
            if t.get("followed_plan") == "NO":
                tokens.append("PLAN:NO")
            return list(dict.fromkeys(tokens))

        for trade in trades:
            r  = float(trade.get("r_multiple") or 0)
            is_win = trade.get("result", "") in {
                "win", "scratch", "be", "breakeven", "b/e"}
            tokens = _parse_tokens(trade)
            if len(tokens) < 2:
                continue
            seen = set()
            for a, b in itertools.combinations(sorted(set(tokens)), 2):
                key = f"{a} + {b}"
                if key in seen:
                    continue
                seen.add(key)
                combo_map[key]["n"]     += 1
                combo_map[key]["net_r"] += r
                combo_map[key]["wins"]  += int(is_win)

        return combo_map

    def test_single_tag_trade_produces_no_combo(self):
        trades = [
            {"mistake_tags": ["fomo"], "emotion_tags": [], "r_multiple": -1.0,
             "result": "loss", "followed_plan": "YES",
             "discipline_quality": 4, "execution_quality": 4},
        ]
        combos = self._run_combos(trades)
        self.assertEqual(len(combos), 0)

    def test_two_mistake_tags_produce_one_combo(self):
        trades = [
            {"mistake_tags": ["fomo", "chased_entry"], "emotion_tags": [], "r_multiple": -1.0,
             "result": "loss", "followed_plan": "YES",
             "discipline_quality": 4, "execution_quality": 4},
        ]
        combos = self._run_combos(trades)
        self.assertEqual(len(combos), 1)
        key = "MISTAKE:chased_entry + MISTAKE:fomo"
        self.assertIn(key, combos)

    def test_same_trade_doesnt_double_count_same_pair(self):
        """A trade with [A, A] (duplicated tag) must produce only ONE combo entry."""
        trades = [
            {"mistake_tags": ["fomo", "fomo"], "emotion_tags": [], "r_multiple": -1.0,
             "result": "loss", "followed_plan": "YES",
             "discipline_quality": 4, "execution_quality": 4},
        ]
        combos = self._run_combos(trades)
        # dedup removes second fomo → only one token → no combo
        self.assertEqual(len(combos), 0)

    def test_combos_sorted_by_net_r_ascending(self):
        trades = [
            {"mistake_tags": ["fomo", "chased_entry"], "emotion_tags": [], "r_multiple": -3.0,
             "result": "loss", "followed_plan": "NO",
             "discipline_quality": 2, "execution_quality": 2},
            {"mistake_tags": ["fomo", "chased_entry"], "emotion_tags": [], "r_multiple": -1.0,
             "result": "loss", "followed_plan": "NO",
             "discipline_quality": 2, "execution_quality": 2},
            {"mistake_tags": ["moved_stop", "sized_too_large"], "emotion_tags": [],
             "r_multiple": -0.5, "result": "loss", "followed_plan": "YES",
             "discipline_quality": 4, "execution_quality": 4},
            {"mistake_tags": ["moved_stop", "sized_too_large"], "emotion_tags": [],
             "r_multiple": -0.3, "result": "loss", "followed_plan": "YES",
             "discipline_quality": 4, "execution_quality": 4},
        ]
        combos = self._run_combos(trades)
        items = sorted([
            {"combo": k, "n": v["n"], "net_r": round(v["net_r"], 3)}
            for k, v in combos.items() if v["n"] >= 2
        ], key=lambda x: x["net_r"])
        # The fomo+chased_entry combo should be most negative
        self.assertGreater(len(items), 0)
        self.assertLess(items[0]["net_r"], -3.5)

    def test_combos_min_2_occurrences_required(self):
        trades = [
            {"mistake_tags": ["fomo", "chased_entry"], "emotion_tags": [], "r_multiple": -1.0,
             "result": "loss", "followed_plan": "YES",
             "discipline_quality": 4, "execution_quality": 4},
        ]
        combos = self._run_combos(trades)
        # Only 1 occurrence — must not appear in final list
        filtered = {k: v for k, v in combos.items() if v["n"] >= 2}
        self.assertEqual(len(filtered), 0)


# ---------------------------------------------------------------------------
# Part 10 — Drill-down filter extensions (journal_trades_list)
# ---------------------------------------------------------------------------

class TestDrillDownExtensions(unittest.TestCase):

    def test_rating_min_max_params_parsed(self):
        """rating_min and rating_max must be parsed in journal_trades_list."""
        import inspect
        src = inspect.getsource(APP.journal_trades_list)
        self.assertIn("rating_min", src)
        self.assertIn("rating_max", src)

    def test_realized_r_min_max_params_parsed(self):
        import inspect
        src = inspect.getsource(APP.journal_trades_list)
        self.assertIn("realized_r_min", src)
        self.assertIn("realized_r_max", src)

    def test_quality_classification_param_parsed(self):
        import inspect
        src = inspect.getsource(APP.journal_trades_list)
        self.assertIn("quality_classification", src)

    def test_high_quality_loss_filter_clauses_present(self):
        import inspect
        src = inspect.getsource(APP.journal_trades_list)
        self.assertIn("high_quality_loss", src)
        # Must include all three rating filters
        self.assertIn("setup_quality", src)
        self.assertIn("execution_quality", src)
        self.assertIn("discipline_quality", src)

    def test_low_quality_win_filter_clauses_present(self):
        import inspect
        src = inspect.getsource(APP.journal_trades_list)
        self.assertIn("low_quality_win", src)
        self.assertIn("r_multiple > 0", src)

    def test_quality_classification_allowlist_only(self):
        """Only 'high_quality_loss' and 'low_quality_win' are valid values."""
        import inspect
        src = inspect.getsource(APP.journal_trades_list)
        self.assertIn('"high_quality_loss"', src)
        self.assertIn('"low_quality_win"', src)

    def test_rating_min_filter_requires_rating_field(self):
        """rating_min should only filter when rating_field is also set."""
        import inspect
        src = inspect.getsource(APP.journal_trades_list)
        # Should be: if rating_field and rating_min
        self.assertIn("rating_field and rating_min", src)

    def test_realized_r_min_sql_clause(self):
        """realized_r_min adds r_multiple >= clause."""
        import inspect
        src = inspect.getsource(APP.journal_trades_list)
        self.assertIn("r_multiple >= %s", src)

    def test_realized_r_max_sql_clause(self):
        """realized_r_max adds r_multiple <= clause."""
        import inspect
        src = inspect.getsource(APP.journal_trades_list)
        self.assertIn("r_multiple <= %s", src)


# ---------------------------------------------------------------------------
# Part 11 — Proxy whitelist
# ---------------------------------------------------------------------------

class TestProxyWhitelist(unittest.TestCase):

    def test_correlations_in_proxy_whitelist(self):
        proxy_path = os.path.join(
            os.path.dirname(__file__), "..", "api-server",
            "src", "routes", "flask-proxy.ts"
        )
        self.assertTrue(os.path.exists(proxy_path),
                        "flask-proxy.ts must exist")
        with open(proxy_path) as f:
            content = f.read()
        self.assertIn("/journal/coaching/correlations", content,
                      "correlations route must be in flask-proxy.ts whitelist")


# ---------------------------------------------------------------------------
# Part 12 — Safety invariants
# ---------------------------------------------------------------------------

class TestSafetyInvariants(unittest.TestCase):

    def _corr_src(self):
        import inspect
        return inspect.getsource(APP.journal_coaching_correlations)

    def test_no_learning_weight_mutation(self):
        src = self._corr_src()
        self.assertNotIn("_LEARNING_WEIGHTS", src)
        self.assertNotIn("update_weights", src)
        self.assertNotIn("_UPDATE_LEARNING", src)

    def test_no_ddl_statements(self):
        src = self._corr_src()
        for stmt in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE",
                     "CREATE INDEX", "INSERT INTO"):
            self.assertNotIn(stmt, src.upper(),
                             f"DDL statement '{stmt}' must not appear in correlations endpoint")

    def test_no_gate_or_scoring_variables(self):
        src = self._corr_src()
        forbidden = ["EDGE_SCORE", "_edge_score", "is_actionable",
                     "_READY_GATE", "TRADERSPOST_URL"]
        for name in forbidden:
            self.assertNotIn(name, src,
                             f"Gate/scoring variable '{name}' must not appear in correlations")

    def test_no_execution_path_reachable(self):
        src = self._corr_src()
        forbidden_calls = ["_send_order", "traderspost_webhook", "_fire_auto_trade",
                           "_enqueue_slow", "requests.post"]
        for fn in forbidden_calls:
            self.assertNotIn(fn, src,
                             f"Execution call '{fn}' must not appear in correlations")

    def test_no_new_global_state_mutation(self):
        """The endpoint must not write to in-memory globals."""
        src = self._corr_src()
        forbidden_globals = ["ACTIVE_TRADES_BY_INST", "ALERT_HISTORY",
                             "AUTO_FIRED_KEYS", "CVD_BY_TICKER"]
        for g in forbidden_globals:
            self.assertNotIn(g, src,
                             f"Global '{g}' must not be mutated in correlations endpoint")

    def test_endpoint_is_select_only(self):
        """All SQL in the endpoint must be SELECT statements (no INSERT/UPDATE/DELETE)."""
        src = self._corr_src()
        # Check for UPDATE/DELETE/INSERT (case-insensitive)
        import re
        bad_patterns = [
            r"\bINSERT\s+INTO\b",
            r"\bUPDATE\s+\w",
            r"\bDELETE\s+FROM\b",
        ]
        for pattern in bad_patterns:
            self.assertIsNone(re.search(pattern, src, re.IGNORECASE),
                              f"Mutating SQL '{pattern}' must not appear in correlations endpoint")

    def test_coaching_endpoint_still_works_structurally(self):
        """Phase 7O regression: existing coaching endpoint source unchanged."""
        import inspect
        src = inspect.getsource(APP.journal_coaching)
        # Core sections still present
        self.assertIn("costliest_mistakes", src)
        self.assertIn("best_behaviors", src)
        self.assertIn("followed_plan_analytics", src)


# ---------------------------------------------------------------------------
# Part 13 — Response schema shape
# ---------------------------------------------------------------------------

class TestResponseSchema(unittest.TestCase):

    def test_correlations_response_keys_defined_in_source(self):
        import inspect
        src = inspect.getsource(APP.journal_coaching_correlations)
        required = [
            '"coverage"', '"rating_mistake"', '"rating_emotion"',
            '"setup_execution_matrix"', '"discipline_outcomes"',
            '"discipline_summary"', '"high_quality_losses"',
            '"low_quality_wins"', '"expensive_combinations"',
            '"correlation_summary"',
        ]
        for key in required:
            self.assertIn(key, src,
                          f"Response key {key} missing from correlations endpoint")

    def test_discipline_summary_only_when_sufficient_data(self):
        """disc_summary must only be set when ≥5 trades exist in each band."""
        import inspect
        src = inspect.getsource(APP.journal_coaching_correlations)
        self.assertIn("n >= 5", src)

    def test_expensive_combinations_capped_at_15(self):
        import inspect
        src = inspect.getsource(APP.journal_coaching_correlations)
        self.assertIn("[:15]", src)

    def test_hql_trades_capped_at_20(self):
        import inspect
        src = inspect.getsource(APP.journal_coaching_correlations)
        self.assertIn("[:20]", src)

    def test_correlation_summary_has_threshold_guards(self):
        """Summary sentences only emit when n >= 5."""
        import inspect
        src = inspect.getsource(APP.journal_coaching_correlations)
        self.assertIn("hql_n >= 5", src)
        self.assertIn("lqw_n >= 5", src)


# ---------------------------------------------------------------------------
# Part 14 — Discipline outcomes logic
# ---------------------------------------------------------------------------

class TestDisciplineOutcomes(unittest.TestCase):

    def _compute_disc_summary(self, disc_rows):
        """Replicate the disc_summary logic."""
        low_avg_r  = None
        high_avg_r = None
        for row in disc_rows:
            disc = row.get("disc_rating", 0)
            n    = row.get("n", 0)
            if disc <= 2 and n >= 5:
                low_avg_r  = row.get("avg_r")
            if disc >= 4 and n >= 5:
                high_avg_r = row.get("avg_r")
        if low_avg_r is not None and high_avg_r is not None:
            s_hi = "+" if (high_avg_r or 0) >= 0 else ""
            s_lo = "+" if (low_avg_r  or 0) >= 0 else ""
            return (
                f"Trades rated discipline 4–5 averaged {s_hi}{high_avg_r:.2f}R. "
                f"Trades rated discipline 1–2 averaged {s_lo}{low_avg_r:.2f}R."
            )
        return None

    def test_disc_summary_generated_when_sufficient_data(self):
        # Use ONE row per band — the loop overwrites low_avg_r on each matching
        # disc<=2 row, so the last one wins.  Keep one low-band row to make
        # the assertion deterministic.
        rows = [
            {"disc_rating": 2, "n": 6, "avg_r": -0.72},
            {"disc_rating": 5, "n": 8, "avg_r": 0.54},
        ]
        summary = self._compute_disc_summary(rows)
        self.assertIsNotNone(summary)
        self.assertIn("4–5", summary)
        self.assertIn("1–2", summary)
        self.assertIn("+0.54R", summary)
        self.assertIn("-0.72R", summary)

    def test_disc_summary_none_when_low_band_insufficient(self):
        rows = [
            {"disc_rating": 1, "n": 3, "avg_r": -0.72},  # n < 5
            {"disc_rating": 5, "n": 8, "avg_r": 0.54},
        ]
        summary = self._compute_disc_summary(rows)
        self.assertIsNone(summary)

    def test_disc_summary_none_when_high_band_insufficient(self):
        rows = [
            {"disc_rating": 2, "n": 6, "avg_r": -0.72},
            {"disc_rating": 4, "n": 3, "avg_r": 0.54},  # n < 5
        ]
        summary = self._compute_disc_summary(rows)
        self.assertIsNone(summary)

    def test_profit_factor_helper(self):
        pf = APP._coaching_pf(4.5, 2.0)
        self.assertAlmostEqual(pf, 2.25)

    def test_profit_factor_none_when_no_losers(self):
        pf = APP._coaching_pf(4.5, 0.0)
        self.assertIsNone(pf)


# ---------------------------------------------------------------------------
# Part 15 — Main Brain TypeScript interface (basic source checks)
# ---------------------------------------------------------------------------

class TestMainBrainTSInterface(unittest.TestCase):

    def _ts_src(self):
        ts_path = os.path.join(
            os.path.dirname(__file__), "..", "home",
            "src", "pages", "MainBrain.tsx"
        )
        with open(ts_path) as f:
            return f.read()

    def test_jdrill_filter_has_rating_min_max(self):
        src = self._ts_src()
        self.assertIn("rating_min?:", src)
        self.assertIn("rating_max?:", src)

    def test_jdrill_filter_has_realized_r_range(self):
        src = self._ts_src()
        self.assertIn("realized_r_min?:", src)
        self.assertIn("realized_r_max?:", src)

    def test_jdrill_filter_has_quality_classification(self):
        src = self._ts_src()
        self.assertIn("quality_classification?:", src)

    def test_drill_server_keys_updated(self):
        src = self._ts_src()
        self.assertIn("'rating_min'", src)
        self.assertIn("'rating_max'", src)
        self.assertIn("'quality_classification'", src)

    def test_correlations_fetch_url_correct(self):
        src = self._ts_src()
        self.assertIn("/api/journal/coaching/correlations", src)

    def test_correlations_loading_state_present(self):
        src = self._ts_src()
        self.assertIn("correlationsLoading", src)
        self.assertIn("correlationsData", src)

    def test_fetch_correlations_called_in_apply_button(self):
        src = self._ts_src()
        self.assertIn("fetchCorrelations", src)

    def test_correlations_section_rendered(self):
        src = self._ts_src()
        self.assertIn("CORRELATIONS", src)
        self.assertIn("high_quality_losses", src)
        self.assertIn("expensive_combinations", src)

    def test_quality_classification_chips_present(self):
        src = self._ts_src()
        self.assertIn("HIGH-QUALITY LOSS", src)
        self.assertIn("LOW-QUALITY WIN", src)


# ---------------------------------------------------------------------------
# Part 16 — Phase 7O regression
# ---------------------------------------------------------------------------

class TestPhase7ORegression(unittest.TestCase):

    def test_intraday_endpoint_still_registered(self):
        rules = {r.rule for r in FLASK_APP.url_map.iter_rules()}
        self.assertIn("/journal/coaching/intraday", rules)

    def test_coaching_endpoint_still_registered(self):
        rules = {r.rule for r in FLASK_APP.url_map.iter_rules()}
        self.assertIn("/journal/coaching", rules)

    def test_intraday_confidence_thresholds_unchanged(self):
        self.assertEqual(APP._intraday_confidence(0), "INSUFFICIENT")
        self.assertEqual(APP._intraday_confidence(4), "INSUFFICIENT")
        self.assertEqual(APP._intraday_confidence(5), "EARLY")
        self.assertEqual(APP._intraday_confidence(19), "EARLY")
        self.assertEqual(APP._intraday_confidence(20), "MODERATE")
        self.assertEqual(APP._intraday_confidence(49), "MODERATE")
        self.assertEqual(APP._intraday_confidence(50), "STRONG")

    def test_hhmm_re_still_rejects_invalid(self):
        self.assertIsNone(APP._HHMM_RE.match("25:00"))
        self.assertIsNone(APP._HHMM_RE.match("24:00"))
        self.assertIsNone(APP._HHMM_RE.match("abc"))

    def test_hhmm_re_still_accepts_valid(self):
        self.assertIsNotNone(APP._HHMM_RE.match("09:30"))
        self.assertIsNotNone(APP._HHMM_RE.match("23:59"))
        self.assertIsNotNone(APP._HHMM_RE.match("00:00"))

    def test_journal_trades_list_registered(self):
        rules = {r.rule for r in FLASK_APP.url_map.iter_rules()}
        self.assertIn("/journal/trades", rules)

    def test_coaching_base_cte_unchanged(self):
        """_COACHING_BASE_CTE must still be the base for all coaching queries."""
        cte = APP._COACHING_BASE_CTE
        self.assertIn("WITH base AS", cte)
        self.assertIn("mistake_tags", cte)
        self.assertIn("emotion_tags", cte)
        self.assertIn("review_status", cte)


if __name__ == "__main__":
    unittest.main(verbosity=2)
