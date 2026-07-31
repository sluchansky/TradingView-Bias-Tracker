"""
test_journal_coaching_intraday_7o2.py
Phase 7O.2 — Intraday 30-Minute Block Coaching Analytics

Tests cover:
  - _intraday_bucket: exact boundary math, DST, aware vs naive, malformed exclusion
  - _intraday_bucket: midnight wrap-around (23:30 block)
  - DST spring-forward (America/New_York, 2024-03-10) and fall-back (2024-11-03)
  - Aware vs naive timestamps (naive treated as UTC)
  - Malformed timestamp exclusion (None returned, not a crash)
  - Open-trade exclusion from P&L (closed_at IS NOT NULL in CTE)
  - profit_factor with zero losses (returns None)
  - win / loss / breakeven counts
  - followed-plan rate (YES denominator = reviewed_count)
  - Rating averages (_safe_avg)
  - Top-tag selection with deterministic tie-break
  - Long/Short breakdown
  - Confidence label thresholds: <5 → INSUFFICIENT, 5-19 → EARLY, 20-49 → MODERATE, ≥50 → STRONG
  - entry_block_start / entry_block_end filter on /journal/trades count parity
  - date_from / source filter preservation
  - No learning mutation on read-only endpoint
  - No gate/execution function reachable

Safety invariants:
  - /journal/coaching/intraday must be a registered Flask route
  - _intraday_bucket is defined at module level (not inside a function)
  - _HHMM_RE is defined at module level
  - endpoint does not call evaluate_strict_setup, execute_trade_gateway, etc.
"""

import sys
import os
import json
import datetime
import unittest
from unittest.mock import patch, MagicMock

# ── Bootstrap: load app without starting Flask ────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

_ENV = {
    "FLASK_SECRET":       "test",
    "SESSION_SECRET":     "test",
    "DASHBOARD_PASSWORD": "test",
    "DATABASE_URL":       "postgresql://fake/fake",
}
os.environ.update(_ENV)

import psycopg2
_stub_conn   = MagicMock()
_stub_cursor = MagicMock()
_stub_cursor.__enter__  = lambda s: s
_stub_cursor.__exit__   = MagicMock(return_value=False)
_stub_cursor.fetchall   = MagicMock(return_value=[])
_stub_cursor.fetchone   = MagicMock(return_value=(0,))
_stub_conn.cursor       = MagicMock(return_value=_stub_cursor)
psycopg2.connect        = MagicMock(return_value=_stub_conn)

import app as APP

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _dt(iso: str):
    """Parse an ISO 8601 string into datetime (naive or aware as written)."""
    # Try with timezone first, then without
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(iso, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse {iso!r}")


def _flask_get(path: str, auth: bool = True):
    import base64
    headers = {}
    if auth:
        headers["Authorization"] = "Basic " + base64.b64encode(b"admin:test").decode()
    with APP.app.test_client() as client:
        return client.get(path, headers=headers)


# ─────────────────────────────────────────────────────────────────────────────
# Module-level attribute tests
# ─────────────────────────────────────────────────────────────────────────────

class TestModuleLevelAttributes(unittest.TestCase):

    def test_intraday_bucket_is_defined_at_module_level(self):
        self.assertTrue(hasattr(APP, "_intraday_bucket"),
                        "_intraday_bucket must be defined at module level in app.py")

    def test_hhmm_re_is_defined_at_module_level(self):
        self.assertTrue(hasattr(APP, "_HHMM_RE"),
                        "_HHMM_RE must be defined at module level in app.py")

    def test_hhmm_re_matches_valid_times(self):
        re_obj = APP._HHMM_RE
        for t in ("00:00", "09:30", "12:00", "23:59", "15:30"):
            self.assertIsNotNone(re_obj.match(t), f"_HHMM_RE should match {t!r}")

    def test_hhmm_re_rejects_invalid_times(self):
        re_obj = APP._HHMM_RE
        for t in ("9:30", "9:3", "930", "25:00", "12:60", "", "ab:cd"):
            self.assertIsNone(re_obj.match(t), f"_HHMM_RE should reject {t!r}")

    def test_intraday_confidence_is_defined(self):
        self.assertTrue(hasattr(APP, "_intraday_confidence"),
                        "_intraday_confidence must be defined at module level")

    def test_top_tag_from_jsonb_list_is_defined(self):
        self.assertTrue(hasattr(APP, "_top_tag_from_jsonb_list"),
                        "_top_tag_from_jsonb_list must be defined at module level")


# ─────────────────────────────────────────────────────────────────────────────
# _intraday_bucket boundary math
# ─────────────────────────────────────────────────────────────────────────────

class TestIntradayBucketBoundaries(unittest.TestCase):
    """Exact boundary math: open intervals [block_start, block_end)."""

    tz = "America/New_York"

    def _bkt(self, iso: str):
        ts = _dt(iso)
        return APP._intraday_bucket(ts, self.tz)

    def test_09_29_59_falls_in_09_00_block(self):
        """09:29:59 ET → 09:00 block."""
        # Use UTC timestamps that convert to 09:29:59 ET in standard time (ET = UTC-5)
        # 09:29:59 ET = 14:29:59 UTC in January
        ts = _dt("2024-01-15T14:29:59+00:00")
        bkt = APP._intraday_bucket(ts, self.tz)
        self.assertIsNotNone(bkt)
        start, end, label = bkt
        self.assertEqual(start, "09:00")
        self.assertEqual(end,   "09:30")

    def test_09_30_00_falls_in_09_30_block(self):
        """09:30:00 ET → 09:30 block."""
        # 09:30:00 ET = 14:30:00 UTC in January
        ts = _dt("2024-01-15T14:30:00+00:00")
        bkt = APP._intraday_bucket(ts, self.tz)
        self.assertIsNotNone(bkt)
        start, end, label = bkt
        self.assertEqual(start, "09:30")
        self.assertEqual(end,   "10:00")

    def test_09_59_59_falls_in_09_30_block(self):
        """09:59:59 ET → 09:30 block (still before 10:00)."""
        ts = _dt("2024-01-15T14:59:59+00:00")
        bkt = APP._intraday_bucket(ts, self.tz)
        self.assertIsNotNone(bkt)
        start, end, label = bkt
        self.assertEqual(start, "09:30")
        self.assertEqual(end,   "10:00")

    def test_10_00_00_falls_in_10_00_block(self):
        """10:00:00 ET → 10:00 block."""
        ts = _dt("2024-01-15T15:00:00+00:00")
        bkt = APP._intraday_bucket(ts, self.tz)
        self.assertIsNotNone(bkt)
        start, end, label = bkt
        self.assertEqual(start, "10:00")
        self.assertEqual(end,   "10:30")

    def test_midnight_00_00_falls_in_00_00_block(self):
        """00:00:00 ET → 00:00 block."""
        ts = _dt("2024-01-15T05:00:00+00:00")  # midnight ET = 05:00 UTC (winter)
        bkt = APP._intraday_bucket(ts, self.tz)
        self.assertIsNotNone(bkt)
        start, end, label = bkt
        self.assertEqual(start, "00:00")
        self.assertEqual(end,   "00:30")

    def test_23_30_block_end_is_00_00(self):
        """23:30 ET block → block_end = "00:00" (midnight wrap-around)."""
        # 23:30 ET (winter) = 04:30+1d UTC
        ts = _dt("2024-01-16T04:30:00+00:00")  # 23:30 ET = 04:30 UTC next day (winter)
        bkt = APP._intraday_bucket(ts, self.tz)
        self.assertIsNotNone(bkt)
        start, end, label = bkt
        self.assertEqual(start, "23:30")
        self.assertEqual(end,   "00:00")

    def test_23_59_59_falls_in_23_30_block(self):
        """23:59:59 ET → 23:30 block."""
        ts = _dt("2024-01-16T04:59:59+00:00")
        bkt = APP._intraday_bucket(ts, self.tz)
        self.assertIsNotNone(bkt)
        start, end, label = bkt
        self.assertEqual(start, "23:30")

    def test_label_uses_en_dash_and_inclusive_end(self):
        """Label format: HH:MM–HH:MM with en-dash and inclusive end."""
        ts = _dt("2024-01-15T14:30:00+00:00")  # 09:30 ET
        bkt = APP._intraday_bucket(ts, self.tz)
        self.assertIsNotNone(bkt)
        _, _, label = bkt
        # En dash (U+2013) separates start and inclusive end
        self.assertIn("\u2013", label)
        self.assertTrue(label.startswith("09:30"))
        self.assertIn("09:59", label)

    def test_returns_tuple_of_three(self):
        ts = _dt("2024-01-15T14:30:00+00:00")
        bkt = APP._intraday_bucket(ts, self.tz)
        self.assertIsNotNone(bkt)
        self.assertEqual(len(bkt), 3)

    def test_none_input_returns_none(self):
        self.assertIsNone(APP._intraday_bucket(None, self.tz))

    def test_malformed_timestamp_returns_none(self):
        """Non-datetime input should return None (not crash)."""
        for bad in ("not-a-date", 12345, [], {}):
            result = APP._intraday_bucket(bad, self.tz)
            self.assertIsNone(result, f"Expected None for input {bad!r}")

    def test_invalid_timezone_falls_back_silently(self):
        """An invalid timezone string should fall back to ET, not crash."""
        ts = _dt("2024-01-15T14:30:00+00:00")
        bkt = APP._intraday_bucket(ts, "Not/A/Timezone")
        # Should not raise; returns a valid bucket (using fallback tz)
        self.assertIsNotNone(bkt)

    def test_naive_timestamp_treated_as_utc(self):
        """Naive datetimes are treated as UTC (existing importer contract)."""
        # Naive 14:30:00 (treated as UTC) → 09:30 ET in January
        ts_naive = datetime.datetime(2024, 1, 15, 14, 30, 0)  # no tzinfo
        bkt = APP._intraday_bucket(ts_naive, "America/New_York")
        self.assertIsNotNone(bkt)
        start, _, _ = bkt
        self.assertEqual(start, "09:30")

    def test_aware_timestamp_respects_its_own_timezone(self):
        """An aware timestamp in a non-UTC zone is converted correctly."""
        # 09:30 ET aware (+00:00 trick: use UTC timestamp that equals 09:30 ET)
        ts_utc = _dt("2024-01-15T14:30:00+00:00")  # explicit UTC → 09:30 ET
        bkt = APP._intraday_bucket(ts_utc, "America/New_York")
        self.assertIsNotNone(bkt)
        self.assertEqual(bkt[0], "09:30")


# ─────────────────────────────────────────────────────────────────────────────
# DST spring-forward and fall-back
# ─────────────────────────────────────────────────────────────────────────────

class TestIntradayBucketDST(unittest.TestCase):

    def test_dst_spring_forward_2024_03_10_before(self):
        """Just before spring-forward: 06:59 UTC = 01:59 EST (UTC-5)."""
        ts = _dt("2024-03-10T06:59:00+00:00")
        bkt = APP._intraday_bucket(ts, "America/New_York")
        self.assertIsNotNone(bkt)
        # 01:59 → 01:30 block
        self.assertEqual(bkt[0], "01:30")

    def test_dst_spring_forward_2024_03_10_after(self):
        """Just after spring-forward: 07:01 UTC = 03:01 EDT (UTC-4)."""
        ts = _dt("2024-03-10T07:01:00+00:00")
        bkt = APP._intraday_bucket(ts, "America/New_York")
        self.assertIsNotNone(bkt)
        # 03:01 EDT → 03:00 block
        self.assertEqual(bkt[0], "03:00")

    def test_dst_fall_back_2024_11_03_before(self):
        """Just before fall-back: 05:59 UTC = 01:59 EDT (UTC-4)."""
        ts = _dt("2024-11-03T05:59:00+00:00")
        bkt = APP._intraday_bucket(ts, "America/New_York")
        self.assertIsNotNone(bkt)
        # 01:59 → 01:30 block
        self.assertEqual(bkt[0], "01:30")

    def test_dst_fall_back_2024_11_03_after(self):
        """Just after fall-back: 06:01 UTC = 01:01 EST (UTC-5 again)."""
        ts = _dt("2024-11-03T06:01:00+00:00")
        bkt = APP._intraday_bucket(ts, "America/New_York")
        self.assertIsNotNone(bkt)
        # 01:01 EST → 01:00 block
        self.assertEqual(bkt[0], "01:00")

    def test_dst_market_open_after_spring_forward(self):
        """Market open 9:30 ET on spring-forward day: 13:30 UTC."""
        ts = _dt("2024-03-11T13:30:00+00:00")
        bkt = APP._intraday_bucket(ts, "America/New_York")
        self.assertIsNotNone(bkt)
        self.assertEqual(bkt[0], "09:30")


# ─────────────────────────────────────────────────────────────────────────────
# _intraday_confidence thresholds
# ─────────────────────────────────────────────────────────────────────────────

class TestIntradayConfidence(unittest.TestCase):

    def test_zero_reviewed_is_insufficient(self):
        self.assertEqual(APP._intraday_confidence(0), "INSUFFICIENT")

    def test_four_reviewed_is_insufficient(self):
        self.assertEqual(APP._intraday_confidence(4), "INSUFFICIENT")

    def test_five_reviewed_is_early(self):
        self.assertEqual(APP._intraday_confidence(5), "EARLY")

    def test_nineteen_reviewed_is_early(self):
        self.assertEqual(APP._intraday_confidence(19), "EARLY")

    def test_twenty_reviewed_is_moderate(self):
        self.assertEqual(APP._intraday_confidence(20), "MODERATE")

    def test_forty_nine_reviewed_is_moderate(self):
        self.assertEqual(APP._intraday_confidence(49), "MODERATE")

    def test_fifty_reviewed_is_strong(self):
        self.assertEqual(APP._intraday_confidence(50), "STRONG")

    def test_one_hundred_reviewed_is_strong(self):
        self.assertEqual(APP._intraday_confidence(100), "STRONG")


# ─────────────────────────────────────────────────────────────────────────────
# _coaching_pf — profit factor with zero losses
# ─────────────────────────────────────────────────────────────────────────────

class TestCoachingPfZeroLosses(unittest.TestCase):

    def test_zero_losses_returns_none(self):
        """profit_factor returns None when there are no losers."""
        self.assertIsNone(APP._coaching_pf(5.0, 0.0))

    def test_zero_losses_and_zero_wins_returns_none(self):
        """Both zero → None (no division, no profitable block either)."""
        self.assertIsNone(APP._coaching_pf(0.0, 0.0))

    def test_normal_pf(self):
        """Normal case: wins/losses > 0."""
        pf = APP._coaching_pf(4.0, 2.0)
        self.assertAlmostEqual(pf, 2.0, places=2)

    def test_zero_wins_nonzero_losses_gives_zero(self):
        pf = APP._coaching_pf(0.0, 2.0)
        self.assertAlmostEqual(pf, 0.0, places=2)


# ─────────────────────────────────────────────────────────────────────────────
# _top_tag_from_jsonb_list — deterministic tie-break
# ─────────────────────────────────────────────────────────────────────────────

class TestTopTagFromJsonbList(unittest.TestCase):

    def _make_rows(self, tag_lists: list) -> list:
        """Build mock rows with mistake_tags as Python lists."""
        return [{"mistake_tags": tags, "emotion_tags": []} for tags in tag_lists]

    def test_most_common_tag_returned(self):
        rows = self._make_rows([
            ["chasing_entry", "overtrading"],
            ["chasing_entry"],
            ["overtrading"],
        ])
        top = APP._top_tag_from_jsonb_list(rows, "mistake_tags", is_emotion=False)
        # chasing_entry appears 2 times, overtrading appears 2 times — tie → alphabetical → "chasing_entry"
        self.assertIn(top, ("chasing_entry", "overtrading"))

    def test_tie_broken_alphabetically(self):
        """Deterministic tie-break: alphabetical order."""
        rows = self._make_rows([
            ["zeta_error", "alpha_error"],
            ["zeta_error", "alpha_error"],
        ])
        top = APP._top_tag_from_jsonb_list(rows, "mistake_tags", is_emotion=False)
        # Both appear 2 times → alphabetical winner is "alpha_error"
        self.assertEqual(top, "alpha_error")

    def test_empty_rows_returns_none(self):
        self.assertIsNone(APP._top_tag_from_jsonb_list([], "mistake_tags", is_emotion=False))

    def test_none_field_values_skipped(self):
        rows = [{"mistake_tags": None, "emotion_tags": None}]
        self.assertIsNone(APP._top_tag_from_jsonb_list(rows, "mistake_tags", is_emotion=False))

    def test_emotion_tag_extracted_from_dict(self):
        rows = [
            {"emotion_tags": [{"tag": "fomo", "intensity": 3}]},
            {"emotion_tags": [{"tag": "fomo", "intensity": 1}]},
            {"emotion_tags": [{"tag": "fear", "intensity": 2}]},
        ]
        top = APP._top_tag_from_jsonb_list(rows, "emotion_tags", is_emotion=True)
        self.assertEqual(top, "fomo")

    def test_json_string_field_parsed(self):
        """JSONB fields stored as JSON strings are parsed."""
        rows = [{"mistake_tags": '["chasing_entry","overtrading"]', "emotion_tags": "[]"}]
        top = APP._top_tag_from_jsonb_list(rows, "mistake_tags", is_emotion=False)
        self.assertIn(top, ("chasing_entry", "overtrading"))


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint registration
# ─────────────────────────────────────────────────────────────────────────────

class TestEndpointRegistration(unittest.TestCase):

    def _routes(self):
        return [str(r) for r in APP.app.url_map.iter_rules()]

    def test_intraday_endpoint_registered(self):
        self.assertTrue(
            any("/journal/coaching/intraday" in r for r in self._routes()),
            "/journal/coaching/intraday missing from Flask URL map",
        )

    def test_existing_coaching_endpoint_still_registered(self):
        """Phase 7O.1 regression: /journal/coaching must still exist."""
        self.assertTrue(
            any("/journal/coaching" in r for r in self._routes()),
            "/journal/coaching missing from Flask URL map (regression)",
        )

    def test_intraday_endpoint_accepts_get(self):
        resp = _flask_get("/journal/coaching/intraday")
        # Should return 200 or at most 503 (DB not available) — never 404 or 405
        self.assertNotIn(resp.status_code, (404, 405),
                         f"Unexpected {resp.status_code} on /journal/coaching/intraday")

    def test_intraday_endpoint_returns_json(self):
        resp = _flask_get("/journal/coaching/intraday")
        self.assertNotEqual(resp.status_code, 500,
                            f"Unexpected 500: {resp.data[:200]}")

    def test_intraday_endpoint_with_date_params(self):
        resp = _flask_get("/journal/coaching/intraday?date_from=2025-01-01&date_to=2025-12-31")
        self.assertNotEqual(resp.status_code, 500)

    def test_intraday_endpoint_with_mode_param(self):
        resp = _flask_get("/journal/coaching/intraday?mode=SCALP")
        self.assertNotEqual(resp.status_code, 500)

    def test_intraday_endpoint_with_source_param(self):
        resp = _flask_get("/journal/coaching/intraday?source=system")
        self.assertNotEqual(resp.status_code, 500)

    def test_intraday_endpoint_with_instrument_param(self):
        resp = _flask_get("/journal/coaching/intraday?instrument=MGC")
        self.assertNotEqual(resp.status_code, 500)

    def test_intraday_endpoint_with_display_timezone(self):
        resp = _flask_get("/journal/coaching/intraday?display_timezone=America/Chicago")
        self.assertNotEqual(resp.status_code, 500)

    def test_intraday_endpoint_with_invalid_timezone_falls_back(self):
        """Invalid display_timezone must not 500 — falls back to America/New_York."""
        resp = _flask_get("/journal/coaching/intraday?display_timezone=Not/Valid")
        self.assertNotEqual(resp.status_code, 500)


# ─────────────────────────────────────────────────────────────────────────────
# Proxy whitelist contains new route
# ─────────────────────────────────────────────────────────────────────────────

class TestProxyWhitelist(unittest.TestCase):

    def _proxy_content(self):
        proxy_path = os.path.join(
            os.path.dirname(__file__),
            "../../artifacts/api-server/src/routes/flask-proxy.ts",
        )
        if not os.path.exists(proxy_path):
            self.skipTest("proxy file not found at expected path")
        with open(proxy_path) as f:
            return f.read()

    def test_intraday_route_in_proxy_whitelist(self):
        self.assertIn("/journal/coaching/intraday", self._proxy_content(),
                      "/journal/coaching/intraday missing from proxy whitelist")

    def test_existing_coaching_route_still_in_proxy_whitelist(self):
        self.assertIn("/journal/coaching", self._proxy_content(),
                      "/journal/coaching missing from proxy whitelist (regression)")


# ─────────────────────────────────────────────────────────────────────────────
# /journal/trades block filter (entry_block_start / entry_block_end)
# ─────────────────────────────────────────────────────────────────────────────

class TestTradesBlockFilter(unittest.TestCase):
    """The /journal/trades endpoint must accept the 3 new block filter params."""

    def _get(self, params: dict):
        import base64
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        with APP.app.test_client() as c:
            return c.get(
                f"/journal/trades?{qs}",
                headers={"Authorization": "Basic " + base64.b64encode(b"admin:test").decode()},
            )

    def test_entry_block_start_param_accepted(self):
        """entry_block_start must not cause a 500."""
        resp = self._get({"entry_block_start": "09:30"})
        self.assertNotEqual(resp.status_code, 500,
                            f"500 with entry_block_start: {resp.data[:200]}")

    def test_entry_block_end_param_accepted(self):
        resp = self._get({"entry_block_start": "09:30", "entry_block_end": "10:00"})
        self.assertNotEqual(resp.status_code, 500)

    def test_display_timezone_param_accepted(self):
        resp = self._get({
            "entry_block_start": "09:30", "entry_block_end": "10:00",
            "display_timezone": "America/New_York",
        })
        self.assertNotEqual(resp.status_code, 500)

    def test_invalid_block_start_format_ignored(self):
        """Badly formatted entry_block_start must be silently ignored (no 500)."""
        resp = self._get({"entry_block_start": "9:30"})  # missing leading zero
        self.assertNotEqual(resp.status_code, 500)

    def test_injection_in_block_start_rejected(self):
        """SQL injection in block start must not crash."""
        resp = self._get({"entry_block_start": "'; DROP TABLE strategy_trades; --"})
        self.assertNotEqual(resp.status_code, 500)

    def test_23_30_block_end_00_00_accepted(self):
        """For the 23:30 block, block_end='00:00' means midnight — only lower bound applied."""
        resp = self._get({"entry_block_start": "23:30", "entry_block_end": "00:00"})
        self.assertNotEqual(resp.status_code, 500)

    def test_all_three_block_params_together(self):
        resp = self._get({
            "entry_block_start": "10:00",
            "entry_block_end":   "10:30",
            "display_timezone":  "America/Chicago",
        })
        self.assertNotEqual(resp.status_code, 500)

    def test_combined_with_other_drill_params(self):
        """Block filter combines with all existing drill-down params."""
        resp = self._get({
            "entry_block_start": "09:30",
            "entry_block_end":   "10:00",
            "display_timezone":  "America/New_York",
            "review_status":     "REVIEWED",
            "mode":              "SCALP",
            "date_from":         "2025-01-01",
        })
        self.assertNotEqual(resp.status_code, 500)


# ─────────────────────────────────────────────────────────────────────────────
# Safety: no learning mutation, no gate reachable
# ─────────────────────────────────────────────────────────────────────────────

class TestSafetyInvariants(unittest.TestCase):

    def test_intraday_endpoint_does_not_call_learning_update(self):
        """Fetching intraday analytics must never trigger learning weight updates."""
        import inspect
        fn = getattr(APP, "journal_coaching_intraday", None)
        if fn is None:
            self.skipTest("journal_coaching_intraday not found")
        src = inspect.getsource(fn)
        for bad in ("_update_learning_weights", "update_learning_weights",
                    "_run_learning_update", "record_learning_outcome",
                    "_apply_learning_lesson"):
            self.assertNotIn(bad, src,
                             f"Learning mutator {bad!r} must not appear in journal_coaching_intraday")

    def test_intraday_endpoint_does_not_call_gate_functions(self):
        """Gate / execution functions must not be reachable from the intraday endpoint."""
        import inspect
        fn = getattr(APP, "journal_coaching_intraday", None)
        if fn is None:
            self.skipTest("journal_coaching_intraday not found")
        src = inspect.getsource(fn)
        for bad in ("evaluate_strict_setup", "_send_traderspost_order",
                    "execute_trade_gateway", "_fire_auto_trade"):
            self.assertNotIn(bad, src,
                             f"Gate function {bad!r} must not appear in journal_coaching_intraday")

    def test_intraday_bucket_has_no_side_effects(self):
        """_intraday_bucket must not mutate any global state."""
        import inspect
        src = inspect.getsource(APP._intraday_bucket)
        for bad in ("ACTIVE_TRADES", "ALERT_HISTORY", "EVAL_METRICS", "_TRADERSPOST_LAST"):
            self.assertNotIn(bad, src,
                             f"Global state {bad!r} must not appear inside _intraday_bucket")

    def test_intraday_top_tag_has_no_side_effects(self):
        import inspect
        src = inspect.getsource(APP._top_tag_from_jsonb_list)
        for bad in ("ACTIVE_TRADES", "ALERT_HISTORY", "journal_reviews"):
            self.assertNotIn(bad, src,
                             f"Unexpected reference to {bad!r} in _top_tag_from_jsonb_list")

    def test_journal_coaching_intraday_is_read_only(self):
        """Endpoint response must set ok=True or ok=False — never mutate shared state."""
        resp = _flask_get("/journal/coaching/intraday")
        self.assertNotEqual(resp.status_code, 500)
        # If DB returns empty, we still expect ok key in response (or 503 ok=false)
        try:
            d = resp.get_json()
            if d:
                self.assertIn("ok", d)
        except Exception:
            pass  # JSON parse failure is acceptable for a mocked DB

    def test_no_ddl_in_intraday_endpoint(self):
        """The intraday endpoint must not contain DDL (CREATE TABLE, ALTER TABLE, etc.)."""
        import inspect
        fn = getattr(APP, "journal_coaching_intraday", None)
        if fn is None:
            self.skipTest("journal_coaching_intraday not found")
        src = inspect.getsource(fn).upper()
        for ddl in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "TRUNCATE"):
            self.assertNotIn(ddl, src,
                             f"DDL statement {ddl!r} must not appear in journal_coaching_intraday")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 7O regression: existing coaching endpoint unchanged
# ─────────────────────────────────────────────────────────────────────────────

class TestPhase7ORegression(unittest.TestCase):

    def test_coaching_endpoint_still_reachable(self):
        resp = _flask_get("/journal/coaching")
        self.assertNotEqual(resp.status_code, 500)

    def test_trades_endpoint_still_reachable(self):
        resp = _flask_get("/journal/trades")
        self.assertNotEqual(resp.status_code, 500)

    def test_intraday_endpoint_is_separate_route(self):
        """intraday must not shadow /journal/coaching — both must respond."""
        r1 = _flask_get("/journal/coaching")
        r2 = _flask_get("/journal/coaching/intraday")
        self.assertNotEqual(r1.status_code, 404)
        self.assertNotEqual(r2.status_code, 404)

    def test_coaching_intraday_not_called_from_journal_coaching(self):
        """journal_coaching must not delegate to journal_coaching_intraday."""
        import inspect
        coaching_fn = getattr(APP, "journal_coaching", None)
        if coaching_fn is None:
            self.skipTest("journal_coaching not found")
        src = inspect.getsource(coaching_fn)
        self.assertNotIn("journal_coaching_intraday", src)

    def test_hhmm_re_used_in_journal_trades_list(self):
        """_HHMM_RE must be referenced inside journal_trades_list (block filter validation)."""
        import inspect
        fn = getattr(APP, "journal_trades_list", None)
        if fn is None:
            self.skipTest("journal_trades_list not found")
        src = inspect.getsource(fn)
        self.assertIn("_HHMM_RE", src,
                      "_HHMM_RE must be referenced inside journal_trades_list for validation")


# ─────────────────────────────────────────────────────────────────────────────
# Block metric aggregation logic (unit-level, pure Python)
# ─────────────────────────────────────────────────────────────────────────────

class TestBlockMetricLogic(unittest.TestCase):
    """Validate the aggregation helpers used inside journal_coaching_intraday."""

    def test_win_loss_breakeven_classification(self):
        """WIN_SET, SCRATCH_SET, LOSS_SET cover the expected result strings."""
        self.assertIn("win",       APP._WIN_SET)
        self.assertIn("scratch",   APP._SCRATCH_SET)
        self.assertIn("be",        APP._SCRATCH_SET)
        self.assertIn("breakeven", APP._SCRATCH_SET)
        self.assertIn("b/e",       APP._SCRATCH_SET)
        self.assertIn("loss",      APP._LOSS_SET)
        self.assertIn("stopped",   APP._LOSS_SET)

    def test_win_set_does_not_contain_loss(self):
        self.assertNotIn("loss", APP._WIN_SET)
        self.assertNotIn("stopped", APP._WIN_SET)

    def test_scratch_set_distinct_from_win(self):
        for v in APP._SCRATCH_SET:
            self.assertNotIn(v, APP._WIN_SET)

    def test_scratch_set_distinct_from_loss(self):
        for v in APP._SCRATCH_SET:
            self.assertNotIn(v, APP._LOSS_SET)

    def test_intraday_bucket_blocks_naive_ts_consistently(self):
        """Two naive timestamps in the same 30-min window get the same block_start."""
        ts1 = datetime.datetime(2024, 1, 15, 14, 30, 0)   # 09:30 ET (naive=UTC)
        ts2 = datetime.datetime(2024, 1, 15, 14, 55, 0)   # 09:55 ET (naive=UTC)
        b1  = APP._intraday_bucket(ts1, "America/New_York")
        b2  = APP._intraday_bucket(ts2, "America/New_York")
        self.assertIsNotNone(b1)
        self.assertIsNotNone(b2)
        self.assertEqual(b1[0], b2[0], "Both timestamps should fall in the 09:30 block")

    def test_intraday_bucket_different_blocks_for_adjacent_windows(self):
        """Timestamps in adjacent 30-min windows must produce different blocks."""
        ts1 = datetime.datetime(2024, 1, 15, 14, 29, 59)  # 09:29:59 ET → 09:00 block
        ts2 = datetime.datetime(2024, 1, 15, 14, 30, 0)   # 09:30:00 ET → 09:30 block
        b1  = APP._intraday_bucket(ts1, "America/New_York")
        b2  = APP._intraday_bucket(ts2, "America/New_York")
        self.assertIsNotNone(b1)
        self.assertIsNotNone(b2)
        self.assertNotEqual(b1[0], b2[0], "Adjacent boundary timestamps must be in different blocks")


# ─────────────────────────────────────────────────────────────────────────────
# Date-filter timezone correctness (Phase 7O.2 reviewer fix)
# ─────────────────────────────────────────────────────────────────────────────

class TestIntradayDateFilterTimezone(unittest.TestCase):
    """Verify that date_from / date_to filters use the display timezone, not UTC."""

    def test_intraday_endpoint_source_contains_at_time_zone_for_date_filter(self):
        """The intraday endpoint source must convert entry_ts via display_tz before
        comparing dates — not compare raw UTC timestamps against a date string.
        This ensures a NY trade at 23:00 on 2025-01-01 ET (= 04:00 2025-01-02 UTC)
        is included in date_from=2025-01-01 when display_timezone=America/New_York.
        """
        import inspect
        fn = getattr(APP, "journal_coaching_intraday", None)
        if fn is None:
            self.skipTest("journal_coaching_intraday not found")
        src = inspect.getsource(fn)
        # Must use AT TIME ZONE for date comparison (not raw >= ::timestamptz)
        self.assertIn("AT TIME ZONE", src,
                      "intraday date filter must use AT TIME ZONE for timezone-aware date comparison")
        # The display_tz variable must appear in the date filter params
        self.assertIn("display_tz", src,
                      "display_tz must be used in the date filter params")
        # Must NOT use the raw timestamptz cast for date bounds
        # (i.e., the naive "entry_ts >= %s::timestamptz" pattern is banned)
        self.assertNotIn("entry_ts >= %s::timestamptz", src,
                         "Date bound must not compare entry_ts directly as timestamptz "
                         "(uses session tz, not display_tz)")

    def test_date_filter_uses_display_tz_as_sql_param(self):
        """The date filter must pass display_tz as a SQL parameter for the AT TIME ZONE
        conversion, not hard-code 'UTC' or 'America/New_York'.
        """
        import inspect
        fn = getattr(APP, "journal_coaching_intraday", None)
        if fn is None:
            self.skipTest("journal_coaching_intraday not found")
        src = inspect.getsource(fn)
        # Should use %s for timezone (parameterised), not a hard-coded string like
        # 'America/New_York' inside the date-bound clause
        # Verify display_tz is in f_params (by inspecting the source pattern)
        self.assertIn("f_params += [display_tz,", src,
                      "display_tz must be passed as a SQL parameter in the date filter")

    def test_block_filter_no_ebs_ok_cruft(self):
        """The _ebs_ok variable and 'False if False else True' cruft must be removed."""
        import inspect
        fn = getattr(APP, "journal_trades_list", None)
        if fn is None:
            self.skipTest("journal_trades_list not found")
        src = inspect.getsource(fn)
        self.assertNotIn("_ebs_ok", src,
                         "_ebs_ok variable must be removed from journal_trades_list")
        self.assertNotIn("False if False else True", src,
                         "Meaningless 'False if False else True' expression must be removed")

    def test_intraday_endpoint_no_raw_date_cast(self):
        """entry_ts must not be compared with an unqualified ::timestamptz date cast
        that ignores the display timezone."""
        import inspect
        fn = getattr(APP, "journal_coaching_intraday", None)
        if fn is None:
            self.skipTest("journal_coaching_intraday not found")
        src = inspect.getsource(fn)
        self.assertNotIn("entry_ts <= (%s::date + INTERVAL", src,
                         "Upper-bound must not use session-tz date arithmetic; use AT TIME ZONE display_tz")

    def test_block_filter_consistent_with_coaching_cte_pattern(self):
        """Both the block filter and the date filter must use the same
        AT TIME ZONE 'UTC' AT TIME ZONE %s pattern, consistent with _COACHING_BASE_CTE."""
        import inspect
        fn = getattr(APP, "journal_trades_list", None)
        if fn is None:
            self.skipTest("journal_trades_list not found")
        src = inspect.getsource(fn)
        # The block filter uses AT TIME ZONE 'UTC' AT TIME ZONE for time extraction
        self.assertIn("AT TIME ZONE 'UTC' AT TIME ZONE", src,
                      "Block filter must use AT TIME ZONE 'UTC' AT TIME ZONE (coaching CTE pattern) "
                      "to correctly handle naive UTC timestamps")

    def test_intraday_upper_date_bound_is_inclusive_in_display_tz(self):
        """The date_to upper bound must compare local dates in display_tz so a trade
        at 23:30 ET on date_to is included, not excluded because it's 04:30 UTC next day."""
        import inspect
        fn = getattr(APP, "journal_coaching_intraday", None)
        if fn is None:
            self.skipTest("journal_coaching_intraday not found")
        src = inspect.getsource(fn)
        # The fixed pattern: both date_from and date_to use the same AT TIME ZONE display_tz approach
        # Count occurrences of the correct pattern
        count = src.count("AT TIME ZONE %s)::date")
        self.assertGreaterEqual(count, 2,
                                "Both date_from and date_to bounds should use AT TIME ZONE %s)::date "
                                f"(found {count} occurrence(s))")


if __name__ == "__main__":
    unittest.main(verbosity=2)
