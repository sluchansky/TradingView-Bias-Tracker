"""Phase 7K-A.2 — Native Journal read API tests.

Tests: 15 backend contract tests.
All DB calls are mocked — these are unit tests for route behaviour.
"""
import importlib
import json
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Minimal stub so app.py can be imported without a real DB / env
# ---------------------------------------------------------------------------
def _load_app():
    import app as _app
    return _app


APP = _load_app()


def _make_conn(rows=None, count=0):
    """Return a mock psycopg2 connection whose cursor returns `rows`."""
    cur = MagicMock()
    if rows is None:
        rows = []
    # fetchone used for COUNT(*) → return (count,) on first call, None after
    cur.fetchone.side_effect = [(count,)] + [None] * 20
    cur.fetchall.return_value = rows
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = lambda s: cur
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cur
    return conn, cur


# ---------------------------------------------------------------------------
# Helper: call a Flask route under the test client
# ---------------------------------------------------------------------------
def _client():
    APP.app.config["TESTING"] = True
    return APP.app.test_client()


AUTH = {"Authorization": "Basic YWRtaW46dGVzdA=="}  # admin:test (any password works in tests)


# ===========================================================================
# 1. List endpoint requires authentication  (Express enforces; Flask allows
#    through — test that 503 returned when NJ_DB_READY=False, not 401)
# ===========================================================================
class TestNJTradesListAuth(unittest.TestCase):

    def test_db_not_ready_returns_503(self):
        with patch.object(APP, "NJ_DB_READY", False):
            r = _client().get("/journal/native-trades")
        self.assertEqual(r.status_code, 503)
        d = json.loads(r.data)
        self.assertFalse(d["ok"])
        self.assertFalse(d["db_ready"])
        self.assertIn("UNAVAILABLE", d["error"].upper())

    def test_db_not_ready_detail_returns_503(self):
        with patch.object(APP, "NJ_DB_READY", False):
            r = _client().get("/journal/native-trades/00000000-0000-0000-0000-000000000001")
        self.assertEqual(r.status_code, 503)
        d = json.loads(r.data)
        self.assertFalse(d["ok"])
        self.assertFalse(d["db_ready"])


# ===========================================================================
# 2 & 3. Limit is bounded and offset works
# ===========================================================================
class TestNJTradesListPagination(unittest.TestCase):

    def _get(self, qs=""):
        conn, cur = _make_conn(rows=[], count=0)
        with patch.object(APP, "NJ_DB_READY", True), \
             patch.object(APP, "_learning_conn", return_value=conn):
            return _client().get(f"/journal/native-trades{qs}")

    def test_default_limit_50(self):
        r = self._get()
        d = json.loads(r.data)
        self.assertTrue(d["ok"])
        self.assertEqual(d["limit"], 50)
        self.assertEqual(d["offset"], 0)

    def test_limit_clamped_to_200(self):
        r = self._get("?limit=9999")
        d = json.loads(r.data)
        self.assertEqual(d["limit"], 200)

    def test_limit_minimum_1(self):
        r = self._get("?limit=0")
        d = json.loads(r.data)
        self.assertEqual(d["limit"], 1)

    def test_offset_honoured(self):
        r = self._get("?offset=25")
        d = json.loads(r.data)
        self.assertEqual(d["offset"], 25)

    def test_bad_limit_falls_back_to_default(self):
        r = self._get("?limit=abc")
        d = json.loads(r.data)
        self.assertEqual(d["limit"], 50)


# ===========================================================================
# 4-10. Filters: instrument, direction, lifecycle, source, review, date, search
# ===========================================================================
class TestNJTradesListFilters(unittest.TestCase):

    def _get_with_spy(self, qs=""):
        conn, cur = _make_conn(rows=[], count=0)
        with patch.object(APP, "NJ_DB_READY", True), \
             patch.object(APP, "_learning_conn", return_value=conn):
            r = _client().get(f"/journal/native-trades{qs}")
        return json.loads(r.data), cur

    def test_instrument_filter(self):
        d, _ = self._get_with_spy("?instrument=MGC")
        self.assertTrue(d["ok"])
        self.assertEqual(d["filters"].get("instrument"), "MGC")

    def test_direction_filter(self):
        d, _ = self._get_with_spy("?direction=long")
        self.assertTrue(d["ok"])
        self.assertEqual(d["filters"].get("direction"), "long")

    def test_lifecycle_filter(self):
        d, _ = self._get_with_spy("?lifecycle_status=CLOSED")
        self.assertTrue(d["ok"])
        self.assertEqual(d["filters"].get("lifecycle_status"), "CLOSED")

    def test_source_filter(self):
        d, _ = self._get_with_spy("?source_label=PAPER")
        self.assertTrue(d["ok"])
        self.assertEqual(d["filters"].get("source_label"), "PAPER")

    def test_review_filter(self):
        d, _ = self._get_with_spy("?review_status=REVIEWED")
        self.assertTrue(d["ok"])
        self.assertEqual(d["filters"].get("review_status"), "REVIEWED")

    def test_date_range_filter(self):
        d, _ = self._get_with_spy("?date_from=2026-01-01&date_to=2026-01-31")
        self.assertTrue(d["ok"])
        self.assertEqual(d["filters"].get("date_from"), "2026-01-01")
        self.assertEqual(d["filters"].get("date_to"), "2026-01-31")

    def test_search_filter(self):
        d, _ = self._get_with_spy("?search=MGC")
        self.assertTrue(d["ok"])
        self.assertEqual(d["filters"].get("search"), "MGC")

    def test_empty_filters_not_in_response(self):
        """Filters with empty values should not appear in the filters dict."""
        d, _ = self._get_with_spy("?instrument=&direction=")
        self.assertTrue(d["ok"])
        self.assertNotIn("instrument", d["filters"])
        self.assertNotIn("direction", d["filters"])


# ===========================================================================
# 11. Missing trade returns 404
# ===========================================================================
class TestNJTradeDetail(unittest.TestCase):

    def test_missing_trade_returns_404(self):
        conn, cur = _make_conn(rows=None, count=0)
        cur.fetchone.side_effect = [None]  # no row
        with patch.object(APP, "NJ_DB_READY", True), \
             patch.object(APP, "_learning_conn", return_value=conn):
            r = _client().get(
                "/journal/native-trades/00000000-0000-0000-0000-000000000099"
            )
        self.assertEqual(r.status_code, 404)
        d = json.loads(r.data)
        self.assertFalse(d["ok"])
        self.assertIn("not found", d["error"])

    def test_found_trade_returns_200_with_ok_true(self):
        import datetime, uuid
        fake_uuid = uuid.uuid4()
        now = datetime.datetime.utcnow()
        # Build a row matching the SELECT columns in nj_trade_detail
        row = (
            fake_uuid, None, now, now,
            "MGC", None, None, None, None,
            None, None,
            None, None,
            None, None, None,
            75.0, "A", "READY",
            None, None, None, None,
            2000.0, 1990.0, None,
            10.0, 1, 3.0, None,
            None, None,
            "SUBMITTED", "PAPER",
            None, None, None,
            None, None, None,
            "UNREVIEWED", None,
            False, None,
            None, None,
        )
        conn, cur = _make_conn(rows=None, count=0)
        cur.fetchone.side_effect = [row]
        with patch.object(APP, "NJ_DB_READY", True), \
             patch.object(APP, "_learning_conn", return_value=conn):
            r = _client().get(f"/journal/native-trades/{fake_uuid}")
        self.assertEqual(r.status_code, 200)
        d = json.loads(r.data)
        self.assertTrue(d["ok"])
        self.assertIn("trade", d)
        self.assertEqual(d["trade"]["instrument"], "MGC")
        self.assertEqual(d["trade"]["source_label"], "PAPER")
        # UUID must be serialised as string, not object
        self.assertIsInstance(d["trade"]["id"], str)


# ===========================================================================
# 12. No secrets exposed
# ===========================================================================
class TestNJNoSecrets(unittest.TestCase):

    def test_list_response_has_no_password_key(self):
        conn, cur = _make_conn(rows=[], count=0)
        with patch.object(APP, "NJ_DB_READY", True), \
             patch.object(APP, "_learning_conn", return_value=conn):
            r = _client().get("/journal/native-trades")
        raw = r.data.decode()
        for bad in ("password", "DASHBOARD_PASSWORD", "DATABASE_URL", "PGPASSWORD"):
            self.assertNotIn(bad, raw)

    def test_detail_503_has_no_password_key(self):
        with patch.object(APP, "NJ_DB_READY", False):
            r = _client().get("/journal/native-trades/00000000-0000-0000-0000-000000000001")
        raw = r.data.decode()
        for bad in ("password", "DASHBOARD_PASSWORD", "DATABASE_URL"):
            self.assertNotIn(bad, raw)


# ===========================================================================
# 13. Empty result is safe
# ===========================================================================
class TestNJEmptyResult(unittest.TestCase):

    def test_empty_trades_list_is_valid(self):
        conn, cur = _make_conn(rows=[], count=0)
        with patch.object(APP, "NJ_DB_READY", True), \
             patch.object(APP, "_learning_conn", return_value=conn):
            r = _client().get("/journal/native-trades")
        self.assertEqual(r.status_code, 200)
        d = json.loads(r.data)
        self.assertTrue(d["ok"])
        self.assertEqual(d["trades"], [])
        self.assertEqual(d["total"], 0)


# ===========================================================================
# 14. NJ_DB_READY false returns explicit unavailable state
# ===========================================================================
class TestNJDbReadyFlag(unittest.TestCase):

    def test_list_when_not_ready(self):
        with patch.object(APP, "NJ_DB_READY", False):
            r = _client().get("/journal/native-trades")
        self.assertEqual(r.status_code, 503)
        d = json.loads(r.data)
        self.assertFalse(d["ok"])
        self.assertFalse(d["db_ready"])

    def test_detail_when_not_ready(self):
        with patch.object(APP, "NJ_DB_READY", False):
            r = _client().get("/journal/native-trades/00000000-0000-0000-0000-000000000001")
        self.assertEqual(r.status_code, 503)
        d = json.loads(r.data)
        self.assertFalse(d["ok"])
        self.assertFalse(d["db_ready"])

    def test_counts_when_not_ready_returns_zeros(self):
        with patch.object(APP, "NJ_DB_READY", False):
            r = _client().get("/journal/native-counts")
        self.assertEqual(r.status_code, 200)
        d = json.loads(r.data)
        self.assertTrue(d["ok"])
        self.assertFalse(d["db_ready"])
        self.assertEqual(d["native"], 0)
        self.assertEqual(d["tradzella"], 0)
        self.assertEqual(d["legacy"], 0)


# ===========================================================================
# 15. Counts endpoint: returns three source counts
# ===========================================================================
class TestNJSourceCounts(unittest.TestCase):

    def test_counts_returned_when_ready(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.side_effect = [(7,), (3,), (12,)]
        cur.__enter__ = lambda s: s
        cur.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value.__enter__ = lambda s: cur
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value = cur
        with patch.object(APP, "NJ_DB_READY", True), \
             patch.object(APP, "_learning_conn", return_value=conn):
            r = _client().get("/journal/native-counts")
        self.assertEqual(r.status_code, 200)
        d = json.loads(r.data)
        self.assertTrue(d["ok"])
        self.assertTrue(d["db_ready"])
        self.assertEqual(d["native"],   7)
        self.assertEqual(d["tradzella"], 3)
        self.assertEqual(d["legacy"],   12)

    def test_counts_fail_open_returns_zeros(self):
        conn = MagicMock()
        conn.cursor.side_effect = Exception("db exploded")
        with patch.object(APP, "NJ_DB_READY", True), \
             patch.object(APP, "_learning_conn", return_value=conn):
            r = _client().get("/journal/native-counts")
        self.assertEqual(r.status_code, 200)
        d = json.loads(r.data)
        self.assertTrue(d["ok"])
        # fail-open: returns 0s but does not 500
        self.assertEqual(d["native"], 0)


if __name__ == "__main__":
    unittest.main()
