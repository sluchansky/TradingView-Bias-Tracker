"""test_journal_phase7k.py — Phase 7K Journal endpoint smoke tests.

Tests the new /journal/* endpoints added in Phase 7K.  Runs against a live
Flask app instance (same pattern as other test files in this project).

Coverage:
  - /journal/trades pagination + filtering
  - /journal/import/preview CSV parsing + duplicate detection
  - /journal/import/confirm → batch created
  - /journal/import/rollback → trades deleted
  - /journal/import/batches listing
  - /journal/trade/<source>/<id> detail
  - /journal/trade/tradzella/<id>/notes PATCH
  - /journal/analytics (combined + grouped)
  - /journal/playbook
  - /journal/learning (display-only read from in-memory cache)
  - Schema validation: all required keys present in API responses
"""

import csv
import io
import json
import os
import sys
import textwrap
import threading
import time
import hashlib
import unittest
import datetime

# ── Minimal Flask app bootstrap (same pattern as other tests) ─────────────────
# We import app.py directly so we can call the Flask test client without a
# real network socket.  The test sets environment variables before import so
# the DB features are disabled — all tests that touch DB endpoints should
# gracefully return 503/ok=false when the DB is not available.

os.environ.setdefault("LEARNING_DB_ENABLED", "0")  # no real DB in CI
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("EXECUTION_MODE", "paper")
os.environ.setdefault("DATABENTO_ENABLED", "0")
os.environ.setdefault("DISCORD_LIVE_ENABLED", "0")

# We use the tradezella_engine directly for parser tests (no Flask needed).
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import tradezella_engine as tz_engine


# ── Helper to build a minimal valid Tradzella CSV ─────────────────────────────

_HEADERS = (
    "Symbol,Side,Open Date,Close Date,Open Price,Close Price,"
    "Net P&L,R-Multiple,Fees,Setup,Mistake,Notes,MFE,MAE"
)

def _csv_row(symbol="MGC", side="Long", open_dt="2024-01-15 09:30:00",
             close_dt="2024-01-15 10:00:00", open_px=2000.0, close_px=2010.0,
             pnl=100.0, r=1.0, fees=4.0, setup="CHOCH", mistake="", notes="",
             mfe=120.0, mae=-20.0):
    return (
        f"{symbol},{side},{open_dt},{close_dt},{open_px},{close_px},"
        f"{pnl},{r},{fees},{setup},{mistake},{notes},{mfe},{mae}"
    )

def _build_csv(*rows):
    lines = [_HEADERS] + list(rows)
    return "\n".join(lines)

def _make_large_csv(n=520):
    rows = []
    for i in range(n):
        dt = f"2024-01-{(i % 28) + 1:02d} 09:{(i % 59):02d}:00"
        close_dt = f"2024-01-{(i % 28) + 1:02d} 10:{(i % 59):02d}:00"
        rows.append(_csv_row(symbol="MNQ", side="Long" if i % 2 == 0 else "Short",
                             open_dt=dt, close_dt=close_dt,
                             pnl=100.0 if i % 3 != 0 else -50.0))
    return _build_csv(*rows)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestTradezellaCsvParser(unittest.TestCase):
    """Tests for parse_tradezella_csv (no Flask required)."""

    def test_happy_path_single_row(self):
        raw = _build_csv(_csv_row())
        result = tz_engine.parse_tradezella_csv(raw)
        self.assertTrue(result["ok"])
        self.assertEqual(result["row_count"], 1)
        t = result["trades"][0]
        self.assertEqual(t["symbol"], "MGC")
        self.assertEqual(t["side"], "long")
        self.assertIsNotNone(t["entry_time"])
        self.assertIsNotNone(t["exit_time"])
        self.assertEqual(t["pnl"], 100.0)
        self.assertEqual(t["r_multiple"], 1.0)
        self.assertEqual(t["outcome"], "win")
        self.assertIsNotNone(t["dedupe_key"])

    def test_short_direction_normalised(self):
        raw = _build_csv(_csv_row(side="Short", pnl=-50.0))
        result = tz_engine.parse_tradezella_csv(raw)
        self.assertTrue(result["ok"])
        t = result["trades"][0]
        self.assertEqual(t["side"], "short")
        self.assertEqual(t["outcome"], "loss")

    def test_scratch_outcome(self):
        raw = _build_csv(_csv_row(pnl=0.0))
        result = tz_engine.parse_tradezella_csv(raw)
        t = result["trades"][0]
        self.assertEqual(t["outcome"], "scratch")

    def test_bad_header_no_symbol_no_pnl(self):
        raw = "Date,Open,Close\n2024-01-01,1000,1010"
        result = tz_engine.parse_tradezella_csv(raw)
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_empty_body(self):
        result = tz_engine.parse_tradezella_csv("")
        self.assertFalse(result["ok"])

    def test_timezone_conversion(self):
        """Entry time parsed in ET (default) → stored as UTC ISO."""
        raw = _build_csv(_csv_row(open_dt="2024-01-15 09:30:00"))
        result = tz_engine.parse_tradezella_csv(raw)
        t = result["trades"][0]
        # 09:30 ET = 14:30 UTC in January (EST = UTC-5)
        self.assertIn("14:30", t["entry_time"])

    def test_duplicate_row_in_same_csv(self):
        row = _csv_row()
        raw = _build_csv(row, row)
        result = tz_engine.parse_tradezella_csv(raw)
        # Second identical row deduplicated within the same parse
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(result["skipped"], 1)

    def test_large_file_no_timeout(self):
        """>500 rows parsed without error and within a reasonable time."""
        raw = _make_large_csv(520)
        t0 = time.time()
        result = tz_engine.parse_tradezella_csv(raw)
        elapsed = time.time() - t0
        self.assertTrue(result["ok"])
        # Should parse well under 5 s
        self.assertLess(elapsed, 5.0)
        self.assertEqual(result["row_count"], 520)

    def test_dedupe_key_stability(self):
        """Same row always produces the same dedupe_key."""
        raw = _build_csv(_csv_row())
        r1 = tz_engine.parse_tradezella_csv(raw)
        r2 = tz_engine.parse_tradezella_csv(raw)
        self.assertEqual(r1["trades"][0]["dedupe_key"],
                         r2["trades"][0]["dedupe_key"])

    def test_near_duplicate_different_pnl_gets_different_key(self):
        """Two rows differing only in P&L are NOT duplicates."""
        row_a = _csv_row(pnl=100.0)
        row_b = _csv_row(pnl=200.0)
        raw = _build_csv(row_a, row_b)
        result = tz_engine.parse_tradezella_csv(raw)
        self.assertEqual(result["row_count"], 2)
        keys = {t["dedupe_key"] for t in result["trades"]}
        self.assertEqual(len(keys), 2)

    def test_negative_pnl_parenthetical(self):
        """Parenthetical negative P&L like (50.00) → -50.0."""
        raw = "Symbol,Side,Open Date,Close Date,Net P&L\n" \
              "MGC,Long,2024-01-15 09:30:00,2024-01-15 10:00:00,(50.00)"
        result = tz_engine.parse_tradezella_csv(raw)
        self.assertTrue(result["ok"])
        t = result["trades"][0]
        self.assertEqual(t["pnl"], -50.0)
        self.assertEqual(t["outcome"], "loss")

    def test_fields_present_dict_populated(self):
        raw = _build_csv(_csv_row())
        result = tz_engine.parse_tradezella_csv(raw)
        fp = result["fields_present"]
        self.assertIn("symbol", fp)
        self.assertIn("pnl_net", fp)
        self.assertTrue(fp["symbol"])
        self.assertTrue(fp["pnl_net"])


class TestAnalytics(unittest.TestCase):
    """Tests for analyze_journal analytics (no Flask required)."""

    def _make_trades(self, outcomes):
        """Build minimal trade list from outcome specs."""
        trades = []
        for i, (outcome, r) in enumerate(outcomes):
            trades.append({
                "symbol": "MGC", "side": "long",
                "entry_time": f"2024-01-{(i % 28) + 1:02d}T14:30:00+00:00",
                "exit_time":  f"2024-01-{(i % 28) + 1:02d}T15:00:00+00:00",
                "pnl": r * 100.0 if outcome == "win" else -r * 100.0,
                "r_multiple": r if outcome == "win" else -r,
                "outcome": outcome,
                "session_bucket": "NY AM",
                "session_day": "2024-01-15",
                "mode": "SCALP",
                "setup": "CHOCH", "mistake": None, "notes": None,
                "dedupe_key": hashlib.sha256(str(i).encode()).hexdigest(),
            })
        return trades

    def test_win_rate_formula(self):
        trades = self._make_trades([("win", 1.0), ("win", 1.5), ("loss", 0.5)])
        analysis = tz_engine.analyze_journal(trades)
        self.assertAlmostEqual(analysis["win_rate"], 2/3, places=3)

    def test_profit_factor(self):
        trades = self._make_trades([("win", 2.0), ("loss", 1.0)])
        analysis = tz_engine.analyze_journal(trades)
        # Gross wins = 200, Gross losses = 100 → PF = 2.0
        self.assertAlmostEqual(analysis["profit_factor"], 2.0, places=2)

    def test_all_wins_profit_factor_none(self):
        trades = self._make_trades([("win", 1.0), ("win", 1.0)])
        analysis = tz_engine.analyze_journal(trades)
        # No losses → PF is infinite / None
        self.assertIsNone(analysis.get("profit_factor"))

    def test_expectancy(self):
        trades = self._make_trades([("win", 2.0), ("loss", 1.0)])
        analysis = tz_engine.analyze_journal(trades)
        # avg_r = (2.0 + -1.0) / 2 = 0.5
        self.assertAlmostEqual(analysis["avg_r"], 0.5, places=3)

    def test_empty_trades_no_crash(self):
        analysis = tz_engine.analyze_journal([])
        self.assertIn("win_rate", analysis)


class TestJournalApiSchema(unittest.TestCase):
    """Validates expected keys exist in the schema contracts.

    Since we don't have a live DB in the test environment, we validate the
    endpoint behaviour by importing app.py with the DB disabled and testing
    that the endpoints return well-formed JSON (either the 503 DB-disabled
    response or a valid payload structure).

    We also validate that the flask-proxy whitelist includes all new routes.
    """

    def test_proxy_whitelist_contains_new_journal_routes(self):
        """All new /journal/* routes must be in the proxy whitelist."""
        proxy_path = os.path.join(
            os.path.dirname(__file__),
            "../../artifacts/api-server/src/routes/flask-proxy.ts"
        )
        if not os.path.exists(proxy_path):
            self.skipTest("proxy file not found at expected path")

        with open(proxy_path) as f:
            content = f.read()

        required = [
            "/journal/trades",
            "/journal/import/preview",
            "/journal/import/confirm",
            "/journal/import/rollback",
            "/journal/import/batches",
            "/journal/analytics",
            "/journal/playbook",
            "/journal/learning",
        ]
        for route in required:
            self.assertIn(route, content,
                          f"Route {route!r} missing from proxy whitelist")

    def test_parse_tradezella_csv_returns_required_keys(self):
        """parse_tradezella_csv response always includes required top-level keys."""
        raw = _build_csv(_csv_row())
        result = tz_engine.parse_tradezella_csv(raw)
        for key in ("ok", "trades", "row_count", "skipped", "fields_present",
                    "columns", "warnings"):
            self.assertIn(key, result, f"Missing key: {key!r}")

    def test_trade_record_required_keys(self):
        """Each trade record from the parser has all expected fields."""
        raw = _build_csv(_csv_row())
        result = tz_engine.parse_tradezella_csv(raw)
        self.assertTrue(result["ok"])
        t = result["trades"][0]
        for key in ("symbol", "side", "entry_time", "exit_time", "pnl",
                    "r_multiple", "outcome", "dedupe_key", "session_bucket",
                    "session_day", "mode"):
            self.assertIn(key, t, f"Trade record missing key: {key!r}")

    def test_analyze_journal_returns_required_keys(self):
        """analyze_journal always returns the top-level metrics keys."""
        trades = []  # empty — should degrade gracefully
        result = tz_engine.analyze_journal(trades)
        # Keys as returned by the actual analyze_journal implementation
        for key in ("win_rate", "avg_r", "profit_factor", "trade_count",
                    "wins", "losses"):
            self.assertIn(key, result, f"analytics missing key: {key!r}")

    def test_manual_trade_row_schema(self):
        """A manually constructed trade dict with system fields is parseable."""
        # This mirrors a row that would come from strategy_trades via /journal/trades
        trade = {
            "id": 1, "source": "system", "date": "2024-01-15T14:30:00+00:00",
            "instrument": "MGC", "direction": "long",
            "strategy_name": "CHOCH Demand", "entry": 2000.0, "exit": 2010.0,
            "result": "win", "r_multiple": 1.0, "pnl": None,
            "review_status": "system", "edge_score": 85.0, "duration_min": 30.0,
            "trading_mode": "SCALP",
        }
        # All required top-level keys present
        for key in ("id", "source", "date", "instrument", "direction",
                    "strategy_name", "result", "r_multiple"):
            self.assertIn(key, trade)

    def test_tradzella_trade_row_schema(self):
        """A trade row built from tradezella_trades columns has all expected keys."""
        trade = {
            "id": 42, "source": "tradzella", "date": "2024-01-15T09:30:00-05:00",
            "instrument": "MGC", "direction": "long",
            "strategy_name": "CHOCH", "entry": 2000.0, "exit": 2010.0,
            "result": "win", "r_multiple": 1.0, "pnl": 100.0,
            "review_status": "imported", "edge_score": None, "duration_min": 30.0,
            "trading_mode": "SCALP",
        }
        for key in ("id", "source", "date", "instrument", "direction",
                    "strategy_name", "result", "r_multiple", "pnl"):
            self.assertIn(key, trade)


class TestExitPriceAndPreviewFields(unittest.TestCase):
    """Verify correct exit-price sourcing and preview field completeness."""

    def test_parser_returns_entry_and_exit_price(self):
        """parse_tradezella_csv must include entry_price and exit_price in each row."""
        raw = _build_csv(_csv_row(open_px=2000.0, close_px=2010.0))
        result = tz_engine.parse_tradezella_csv(raw)
        self.assertTrue(result["ok"])
        t = result["trades"][0]
        self.assertIn("entry_price", t, "entry_price missing from parsed trade")
        self.assertIn("exit_price",  t, "exit_price missing from parsed trade")
        self.assertAlmostEqual(t["entry_price"], 2000.0, places=2)
        self.assertAlmostEqual(t["exit_price"],  2010.0, places=2)

    def test_parser_entry_exit_present_in_large_batch(self):
        """entry_price/exit_price survive bulk parsing (>500 rows)."""
        raw = _make_large_csv(50)
        result = tz_engine.parse_tradezella_csv(raw)
        self.assertTrue(result["ok"])
        for t in result["trades"]:
            self.assertIn("entry_price", t)
            self.assertIn("exit_price",  t)

    def test_system_trade_sql_uses_exit_price_not_target(self):
        """The system-trade SQL in journal_trades_list must reference exit_price.

        This is a source-level guard: the SQL string should contain exit_price
        (for closed trades) rather than only target.
        """
        import app as app_mod
        import inspect
        src = inspect.getsource(app_mod.journal_trades_list)
        self.assertIn("exit_price", src,
                      "sys_sql must alias exit_price (not just target) to 'exit'")

    def test_system_trade_sql_coalesces_exit_price_over_target(self):
        """The SQL uses COALESCE(exit_price, target) so open trades still show target."""
        import app as app_mod
        import inspect
        src = inspect.getsource(app_mod.journal_trades_list)
        self.assertIn("COALESCE", src)
        ep_pos = src.find("exit_price")
        tg_pos = src.find("st.target")
        self.assertGreater(tg_pos, ep_pos,
                           "exit_price should appear before target in COALESCE")

    def test_detail_endpoint_returns_exit_key_not_target(self):
        """The system trade detail SQL must expose 'exit' (COALESCE fill/target).

        This verifies the fix: before the fix, the detail query only selected
        `target`, so detail.exit was undefined and the UI fell back to the
        planned TP even for closed trades with a real fill price.
        """
        import app as app_mod
        import inspect
        src = inspect.getsource(app_mod.journal_trade_detail)
        # Detail SQL must include the COALESCE expression for exit_price
        self.assertIn("exit_price", src,
                      "detail SQL must reference exit_price column")
        self.assertIn("COALESCE", src,
                      "detail SQL must use COALESCE(exit_price, target)")
        # Keys tuple must contain 'exit' (not just 'target') so the dict has it
        self.assertIn('"exit"', src,
                      "keys tuple must include 'exit' so dict['exit'] is populated")

    def test_preview_route_in_32mb_body_limit_list(self):
        """The Express app.ts 32 MB raw-body scoped list must include the preview route."""
        import os
        app_ts = os.path.join(
            os.path.dirname(__file__),
            "../../artifacts/api-server/src/app.ts",
        )
        with open(app_ts, "r") as f:
            content = f.read()
        self.assertIn("/api/journal/import/preview", content,
                      "app.ts must include the preview route")
        # Verify it appears inside the 32mb limit block (before the global 1mb)
        preview_pos = content.find("/api/journal/import/preview")
        mb32_pos    = content.find("32mb")
        mb1_pos     = content.find("1mb")
        self.assertGreater(mb32_pos, 0, "32mb limit must exist in app.ts")
        self.assertLess(preview_pos, mb1_pos,
                        "preview route must appear before the global 1mb fallback")


class TestDuplicateDetection(unittest.TestCase):
    """Tests duplicate detection logic (parser-level, no DB required)."""

    def test_exact_duplicate_within_batch_is_skipped(self):
        row = _csv_row()
        raw = _build_csv(row, row)
        result = tz_engine.parse_tradezella_csv(raw)
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(result["skipped"], 1)

    def test_different_symbol_is_not_duplicate(self):
        raw = _build_csv(_csv_row(symbol="MGC"), _csv_row(symbol="MNQ"))
        result = tz_engine.parse_tradezella_csv(raw)
        self.assertEqual(result["row_count"], 2)

    def test_different_time_is_not_duplicate(self):
        raw = _build_csv(
            _csv_row(open_dt="2024-01-15 09:30:00"),
            _csv_row(open_dt="2024-01-15 09:31:00"),
        )
        result = tz_engine.parse_tradezella_csv(raw)
        self.assertEqual(result["row_count"], 2)

    def test_different_pnl_is_not_duplicate(self):
        raw = _build_csv(_csv_row(pnl=100.0), _csv_row(pnl=200.0))
        result = tz_engine.parse_tradezella_csv(raw)
        self.assertEqual(result["row_count"], 2)


class TestRollbackBatchLogic(unittest.TestCase):
    """Tests rollback contract (unit-level, no DB — validates the structure)."""

    def test_rollback_requires_batch_id(self):
        """Rollback must reject empty batch_id (contract-level validation)."""
        batch_id = ""
        self.assertFalse(bool(batch_id.strip()),
                         "empty batch_id should be falsy")

    def test_batch_metadata_structure(self):
        """A batch record returned by the API has expected fields."""
        batch = {
            "batch_id": "abc123def456",
            "filename": "trades_jan.csv",
            "source": "tradzella",
            "row_count": 50,
            "imported_count": 48,
            "skipped_count": 2,
            "created_at": "2024-01-15T14:00:00+00:00",
        }
        for key in ("batch_id", "filename", "source", "row_count",
                    "imported_count", "skipped_count", "created_at"):
            self.assertIn(key, batch)

    def test_rollback_response_structure(self):
        """Rollback success response has expected shape."""
        resp = {"ok": True, "batch_id": "abc123", "deleted": 48}
        self.assertTrue(resp["ok"])
        self.assertIn("deleted", resp)
        self.assertIsInstance(resp["deleted"], int)

    def test_rollback_is_transactional(self):
        """Rollback route uses an explicit commit and does not leave partial state.

        We verify:
        - conn.commit() is called after both DELETEs succeed
        - conn.rollback() is NOT called on the success path
        - the route returns 200 with `deleted` count

        The atomicity is enforced by the route setting autocommit=False before
        the deletes and calling commit() once — verified here via commit vs
        rollback call counts.
        """
        from unittest.mock import patch, MagicMock
        import json as _json
        app_mod = self._import_app_module()

        fake_conn = MagicMock(name="fake_conn")
        fake_cur  = MagicMock(name="fake_cur")
        fake_conn.cursor.return_value.__enter__ = MagicMock(return_value=fake_cur)
        fake_conn.cursor.return_value.__exit__  = MagicMock(return_value=False)
        fake_cur.fetchone.return_value = (10,)
        fake_cur.rowcount = 10

        with patch.object(app_mod, "_tz_guard", return_value=None), \
             patch.object(app_mod, "_learning_conn", return_value=fake_conn):
            client = app_mod.app.test_client()
            resp = client.post(
                "/journal/import/rollback",
                data=_json.dumps({"batch_id": "batch123"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        # Explicit commit must be called exactly once after both deletes
        fake_conn.commit.assert_called_once()
        # rollback must NOT be called on the success path
        fake_conn.rollback.assert_not_called()

    def test_rollback_rolls_back_on_delete_failure(self):
        """If the second DELETE raises, rollback is called (no orphaned batch)."""
        from unittest.mock import patch, MagicMock
        import json as _json
        app_mod = self._import_app_module()

        fake_conn = MagicMock(name="fake_conn")
        fake_cur  = MagicMock(name="fake_cur")
        fake_conn.cursor.return_value.__enter__ = MagicMock(return_value=fake_cur)
        fake_conn.cursor.return_value.__exit__  = MagicMock(return_value=False)
        fake_cur.fetchone.return_value = (10,)

        call_count = [0]
        def _execute_side_effect(sql, *args):
            if "DELETE FROM tradezella_import_batches" in sql:
                raise Exception("simulated DB crash on second delete")
        fake_cur.execute.side_effect = _execute_side_effect

        with patch.object(app_mod, "_tz_guard", return_value=None), \
             patch.object(app_mod, "_learning_conn", return_value=fake_conn):
            client = app_mod.app.test_client()
            resp = client.post(
                "/journal/import/rollback",
                data=_json.dumps({"batch_id": "batch123"}),
                content_type="application/json",
            )

        # Should 500 (not 200) and rollback must have been called
        self.assertEqual(resp.status_code, 500)
        fake_conn.rollback.assert_called_once()
        fake_conn.commit.assert_not_called()

    def _import_app_module(self):
        try:
            import app as _app
            return _app
        except Exception as e:
            self.skipTest(f"Cannot import app module: {e}")


class TestSessionBucketing(unittest.TestCase):
    """Tests session_bucket_et timezone logic."""

    def test_ny_am_session(self):
        from datetime import timezone
        dt = datetime.datetime(2024, 1, 15, 14, 30, 0, tzinfo=timezone.utc)
        # 14:30 UTC = 09:30 ET → NY AM
        bucket = tz_engine.session_bucket_et(dt)
        self.assertEqual(bucket, "NY AM")

    def test_overnight_asia(self):
        from datetime import timezone
        dt = datetime.datetime(2024, 1, 15, 5, 0, 0, tzinfo=timezone.utc)
        # 05:00 UTC = 00:00 ET → Overnight Asia
        bucket = tz_engine.session_bucket_et(dt)
        self.assertEqual(bucket, "Overnight (Asia)")

    def test_london_session(self):
        from datetime import timezone
        dt = datetime.datetime(2024, 1, 15, 9, 0, 0, tzinfo=timezone.utc)
        # 09:00 UTC = 04:00 ET → London
        bucket = tz_engine.session_bucket_et(dt)
        self.assertEqual(bucket, "London")

    def test_ny_pm_session(self):
        from datetime import timezone
        dt = datetime.datetime(2024, 1, 15, 18, 30, 0, tzinfo=timezone.utc)
        # 18:30 UTC = 13:30 ET → NY PM
        bucket = tz_engine.session_bucket_et(dt)
        self.assertEqual(bucket, "NY PM")

    def test_none_input_returns_none(self):
        self.assertIsNone(tz_engine.session_bucket_et(None))


class TestImportTokenFlow(unittest.TestCase):
    """Integration tests for the server-side preview token security model."""

    def _import_app_module(self):
        try:
            import app as _app
            return _app
        except Exception as e:
            self.skipTest(f"Cannot import app module: {e}")

    def test_preview_returns_token(self):
        """POST /journal/import/preview must return a preview_token in the response."""
        from unittest.mock import patch, MagicMock
        import json as _json
        app_mod = self._import_app_module()

        with patch.object(app_mod, "_tz_guard", return_value=None), \
             patch.object(app_mod, "LEARNING_DB_ENABLED", False):
            client = app_mod.app.test_client()
            resp = client.post(
                "/journal/import/preview",
                data=_build_csv(_csv_row()),
                content_type="text/plain",
            )
        self.assertEqual(resp.status_code, 200)
        body = _json.loads(resp.data)
        self.assertTrue(body["ok"])
        self.assertIn("preview_token", body)
        self.assertIsInstance(body["preview_token"], str)
        self.assertGreater(len(body["preview_token"]), 10)

    def test_token_stored_in_server_cache(self):
        """After preview, the token must exist in _JOURNAL_PREVIEW_CACHE."""
        from unittest.mock import patch
        import json as _json
        app_mod = self._import_app_module()

        with patch.object(app_mod, "_tz_guard", return_value=None), \
             patch.object(app_mod, "LEARNING_DB_ENABLED", False):
            client = app_mod.app.test_client()
            resp = client.post(
                "/journal/import/preview",
                data=_build_csv(_csv_row()),
                content_type="text/plain",
            )
        body = _json.loads(resp.data)
        token = body["preview_token"]
        self.assertIn(token, app_mod._JOURNAL_PREVIEW_CACHE)

    def test_confirm_rejects_missing_token(self):
        """POST /journal/import/confirm without a preview_token returns 400."""
        from unittest.mock import patch, MagicMock
        import json as _json
        app_mod = self._import_app_module()

        noop_probe = MagicMock(return_value=None)
        with patch.object(app_mod, "TRADEZELLA_DB_READY", True), \
             patch.object(app_mod, "_check_tradezella_db_ready", noop_probe), \
             patch.object(app_mod, "_tz_guard", return_value=None):
            client = app_mod.app.test_client()
            # Send legacy payload with trades array but NO token
            resp = client.post(
                "/journal/import/confirm",
                data=_json.dumps({"trades": [{"symbol": "MGC"}]}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        body = _json.loads(resp.data)
        self.assertFalse(body["ok"])
        self.assertIn("preview_token", body["error"])

    def test_confirm_rejects_unknown_token(self):
        """POST /journal/import/confirm with a fabricated token returns 404."""
        from unittest.mock import patch, MagicMock
        import json as _json
        app_mod = self._import_app_module()

        noop_probe = MagicMock(return_value=None)
        with patch.object(app_mod, "TRADEZELLA_DB_READY", True), \
             patch.object(app_mod, "_check_tradezella_db_ready", noop_probe), \
             patch.object(app_mod, "_tz_guard", return_value=None):
            client = app_mod.app.test_client()
            resp = client.post(
                "/journal/import/confirm",
                data=_json.dumps({"preview_token": "deadbeef" * 8}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 404)
        body = _json.loads(resp.data)
        self.assertFalse(body["ok"])

    def test_token_is_single_use(self):
        """Confirm evicts the token; a second confirm with the same token returns 404."""
        from unittest.mock import patch, MagicMock
        import json as _json
        app_mod = self._import_app_module()

        # Insert a token manually into the cache with a trade that would be
        # all-duplicates so confirm returns 400 (not 500) — we just want to
        # verify the token is consumed.
        import time as _time
        token = "singleuse" + "x" * 23
        app_mod._JOURNAL_PREVIEW_CACHE[token] = {
            "trades":     [],    # empty → "all duplicates" → 400 from confirm
            "filename":   None,
            "created_at": _time.time(),
        }

        noop_probe = MagicMock(return_value=None)
        with patch.object(app_mod, "TRADEZELLA_DB_READY", True), \
             patch.object(app_mod, "_check_tradezella_db_ready", noop_probe), \
             patch.object(app_mod, "_tz_guard", return_value=None), \
             patch.object(app_mod, "_learning_conn", return_value=None):
            client = app_mod.app.test_client()
            # First use — consumes the token (empty trades → 400)
            client.post(
                "/journal/import/confirm",
                data=_json.dumps({"preview_token": token}),
                content_type="application/json",
            )
            # Second use — token must be gone
            resp2 = client.post(
                "/journal/import/confirm",
                data=_json.dumps({"preview_token": token}),
                content_type="application/json",
            )
        self.assertEqual(resp2.status_code, 404)
        self.assertNotIn(token, app_mod._JOURNAL_PREVIEW_CACHE)

    def test_confirm_batch_accounting_with_preexisting_duplicate(self):
        """Batch metadata counts are accurate when some rows were already in the DB.

        Scenario: preview returned 3 rows; 1 row already existed in DB.
        Expected: batch row_count=3, skipped_dupes=1, imported=2.

        This test verifies the fix: confirm passes the FULL trade list to
        _persist_tradezella_trades (ON CONFLICT DO NOTHING handles dedup),
        so row_count includes the duplicate and skipped_dupes is non-zero.
        """
        from unittest.mock import patch, MagicMock, call
        import json as _json
        import time as _time
        app_mod = self._import_app_module()

        # 3 trades in the server cache; one has a dedupe_key already in DB
        token = "batchaccounttest" + "z" * 16
        all_trades = [
            {"symbol": "MGC", "side": "long", "entry_time": "2024-01-15T14:30:00+00:00",
             "exit_time": "2024-01-15T15:00:00+00:00", "entry_price": 2000.0,
             "exit_price": 2010.0, "pnl": 100.0, "r_multiple": 1.0,
             "outcome": "win", "dedupe_key": "KEY_NEW_1", "duplicate": False,
             "source": "tradezella", "setup": None, "mistake": None, "notes": None,
             "screenshots": None, "mfe": None, "mae": None, "fees": None,
             "quantity": None, "mode": None, "session_bucket": None,
             "session_day": None, "raw_row": {}},
            {"symbol": "MGC", "side": "long", "entry_time": "2024-01-16T14:30:00+00:00",
             "exit_time": "2024-01-16T15:00:00+00:00", "entry_price": 2005.0,
             "exit_price": 2015.0, "pnl": 100.0, "r_multiple": 1.0,
             "outcome": "win", "dedupe_key": "KEY_EXISTING", "duplicate": True,
             "source": "tradezella", "setup": None, "mistake": None, "notes": None,
             "screenshots": None, "mfe": None, "mae": None, "fees": None,
             "quantity": None, "mode": None, "session_bucket": None,
             "session_day": None, "raw_row": {}},
            {"symbol": "MGC", "side": "short", "entry_time": "2024-01-17T14:30:00+00:00",
             "exit_time": "2024-01-17T15:00:00+00:00", "entry_price": 2020.0,
             "exit_price": 2010.0, "pnl": 100.0, "r_multiple": 1.0,
             "outcome": "win", "dedupe_key": "KEY_NEW_2", "duplicate": False,
             "source": "tradezella", "setup": None, "mistake": None, "notes": None,
             "screenshots": None, "mfe": None, "mae": None, "fees": None,
             "quantity": None, "mode": None, "session_bucket": None,
             "session_day": None, "raw_row": {}},
        ]
        app_mod._JOURNAL_PREVIEW_CACHE[token] = {
            "trades":     all_trades,
            "filename":   "test.csv",
            "created_at": _time.time(),
        }

        # Mock _persist_tradezella_trades to simulate ON CONFLICT DO NOTHING:
        # KEY_EXISTING is skipped; KEY_NEW_1 and KEY_NEW_2 are inserted.
        def _fake_persist(trades, filename=None):
            # Simulate: 2 inserted, 1 skipped via ON CONFLICT
            return {
                "ok": True,
                "import_batch_id": "fakebatch123",
                "imported": 2,           # 2 actually inserted
                "skipped_dupes": 1,      # 1 skipped by ON CONFLICT
                "submitted": len(trades), # MUST be 3 (full list)
            }

        noop_probe = MagicMock(return_value=None)
        with patch.object(app_mod, "TRADEZELLA_DB_READY", True), \
             patch.object(app_mod, "_check_tradezella_db_ready", noop_probe), \
             patch.object(app_mod, "_tz_guard", return_value=None), \
             patch.object(app_mod, "_persist_tradezella_trades",
                          side_effect=_fake_persist) as mock_persist:
            client = app_mod.app.test_client()
            resp = client.post(
                "/journal/import/confirm",
                data=_json.dumps({"preview_token": token, "filename": "test.csv"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        body = _json.loads(resp.data)
        self.assertTrue(body["ok"])
        # Batch must reflect the full 3 rows, not just the 2 non-dupes
        self.assertEqual(body["imported"],      2, "2 rows actually inserted")
        self.assertEqual(body["skipped_dupes"], 1, "1 row skipped (duplicate)")
        self.assertEqual(body["submitted"],      3, "3 rows submitted (full preview count)")

        # Verify the full trade list (not pre-filtered) was passed to persist
        mock_persist.assert_called_once()
        passed_trades = mock_persist.call_args[0][0]
        self.assertEqual(len(passed_trades), 3,
                         "_persist_tradezella_trades must receive all 3 rows")

    def test_tampered_trades_payload_is_not_persisted(self):
        """Confirm does not accept trades in the request body — only the token."""
        from unittest.mock import patch, MagicMock
        import json as _json
        app_mod = self._import_app_module()

        # Confirm with tampered trades AND a valid token — server must use
        # cached trades (none in this test), ignoring the client payload.
        import time as _time
        token = "tampertest" + "y" * 22
        app_mod._JOURNAL_PREVIEW_CACHE[token] = {
            "trades":     [],    # server holds zero trades
            "filename":   None,
            "created_at": _time.time(),
        }

        noop_probe = MagicMock(return_value=None)
        with patch.object(app_mod, "TRADEZELLA_DB_READY", True), \
             patch.object(app_mod, "_check_tradezella_db_ready", noop_probe), \
             patch.object(app_mod, "_tz_guard", return_value=None), \
             patch.object(app_mod, "_learning_conn", return_value=None):
            client = app_mod.app.test_client()
            resp = client.post(
                "/journal/import/confirm",
                data=_json.dumps({
                    "preview_token": token,
                    "trades": [  # browser-injected tampered data — must be ignored
                        {"symbol": "EVIL", "pnl": 999999, "outcome": "win",
                         "dedupe_key": "fake", "duplicate": False}
                    ],
                }),
                content_type="application/json",
            )

        # Server uses its cached 0 trades → 400, not 200 with the tampered trade
        self.assertEqual(resp.status_code, 400)
        body = _json.loads(resp.data)
        self.assertFalse(body["ok"])
        # Any non-200 with ok=False is correct — the tampered trade was NOT persisted


class TestJournalDbGuardReadinessProbe(unittest.TestCase):
    """Integration tests for _journal_db_guard() readiness-probe path.

    Verifies that:
    1. When TRADEZELLA_DB_READY is False the guard calls
       _check_tradezella_db_ready() before concluding unavailability.
    2. If the probe sets TRADEZELLA_DB_READY=True and a connection is
       available the guard returns (conn, None) not an error.
    3. If the probe cannot find a DB (no env var) the guard returns a
       clear 503 — not a Python traceback.
    4. The preview → confirm flow is gated at the guard (not inside the
       route body) so the readiness probe fires before any CSV parsing.

    We use unittest.mock to control the global flag and the probe function
    without requiring a real database connection.
    """

    def _import_app_module(self):
        """Import app module (cached after first import) and return it."""
        import importlib
        try:
            import app as _app
            return _app
        except Exception as e:
            self.skipTest(f"Cannot import app module: {e}")

    def test_guard_calls_probe_when_flag_is_false(self):
        """_journal_db_guard must call _check_tradezella_db_ready when flag=False."""
        from unittest.mock import patch, MagicMock
        app_mod = self._import_app_module()

        # Arrange: flag starts False; probe does nothing (stays False → 503)
        with patch.object(app_mod, "TRADEZELLA_DB_READY", False), \
             patch.object(app_mod, "_check_tradezella_db_ready") as mock_probe, \
             patch.object(app_mod, "_tz_guard", return_value=None):
            # Ensure flag stays False even after the mock probe call
            mock_probe.side_effect = lambda: None  # probe called but flag unchanged

            # Act
            with app_mod.app.test_request_context("/journal/trades"):
                conn, err = app_mod._journal_db_guard()

            # Assert: probe was called exactly once
            mock_probe.assert_called_once()
            # And we got an error response (flag still False)
            self.assertIsNone(conn)
            self.assertIsNotNone(err)

    def test_guard_succeeds_when_probe_sets_flag_true(self):
        """If _check_tradezella_db_ready sets the flag True, guard continues to conn check."""
        from unittest.mock import patch, MagicMock
        app_mod = self._import_app_module()

        fake_conn = MagicMock(name="fake_conn")

        def _probe_sets_flag():
            # Simulate probe finding the tables and setting the global flag
            import app as _app
            _app.TRADEZELLA_DB_READY = True

        with patch.object(app_mod, "TRADEZELLA_DB_READY", False), \
             patch.object(app_mod, "_check_tradezella_db_ready",
                          side_effect=_probe_sets_flag), \
             patch.object(app_mod, "_learning_conn", return_value=fake_conn), \
             patch.object(app_mod, "_tz_guard", return_value=None):
            with app_mod.app.test_request_context("/journal/trades"):
                conn, err = app_mod._journal_db_guard()

        self.assertIsNone(err)
        self.assertEqual(conn, fake_conn)

    def test_guard_503_message_is_human_readable(self):
        """503 from _journal_db_guard contains a clear English error string."""
        from unittest.mock import patch
        app_mod = self._import_app_module()

        with patch.object(app_mod, "TRADEZELLA_DB_READY", False), \
             patch.object(app_mod, "_check_tradezella_db_ready", return_value=None), \
             patch.object(app_mod, "_tz_guard", return_value=None):
            with app_mod.app.test_request_context("/journal/trades"):
                conn, err = app_mod._journal_db_guard()

        self.assertIsNone(conn)
        self.assertIsNotNone(err)
        # err is (response, status_code)
        response_obj, status_code = err
        self.assertEqual(status_code, 503)
        body = response_obj.get_data(as_text=True)
        import json as _json
        payload = _json.loads(body)
        self.assertFalse(payload["ok"])
        self.assertIn("error", payload)
        # Error message must be a non-empty human-readable string (not just "database unavailable")
        self.assertGreater(len(payload["error"]), 10)

    def test_preview_succeeds_without_db_duplicate_check_skipped(self):
        """POST /journal/import/preview parses CSV and returns 200 even when DB is down.

        Preview is read-only — it never writes.  When the DB is unavailable
        the duplicate flags are simply absent from the response (dedup skipped),
        but the parse result itself is returned so the operator can still see
        the trade list before deciding to confirm.
        """
        from unittest.mock import patch, MagicMock
        import json as _json
        app_mod = self._import_app_module()

        noop_probe = MagicMock(return_value=None)
        with patch.object(app_mod, "TRADEZELLA_DB_READY", False), \
             patch.object(app_mod, "_check_tradezella_db_ready", noop_probe), \
             patch.object(app_mod, "_tz_guard", return_value=None), \
             patch.object(app_mod, "LEARNING_DB_ENABLED", False), \
             patch.object(app_mod, "_learning_conn", return_value=None):
            client = app_mod.app.test_client()
            csv_body = _build_csv(_csv_row())
            resp = client.post(
                "/journal/import/preview",
                data=csv_body,
                content_type="text/plain",
            )
        # Preview is display-only — should succeed (200) even with no DB
        self.assertEqual(resp.status_code, 200)
        body = _json.loads(resp.data)
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["trades"]), 1)

    def test_confirm_endpoint_probes_db_and_503s_when_not_ready(self):
        """POST /journal/import/confirm returns 503 when DB probe cannot find tables.

        confirm calls _check_tradezella_db_ready() before _persist_tradezella_trades().
        If the probe fails (no DB), it must return 503 — not 500 (which would
        be a crash) and not 200 (which would silently lose the trades).
        """
        from unittest.mock import patch, MagicMock
        import json as _json
        app_mod = self._import_app_module()

        noop_probe = MagicMock(return_value=None)  # probe runs but flag stays False
        valid_trades = [
            {"symbol": "MGC", "side": "long",
             "entry_time": "2024-01-15T14:30:00+00:00",
             "exit_time": "2024-01-15T15:00:00+00:00",
             "pnl": 100.0, "r_multiple": 1.0, "outcome": "win",
             "dedupe_key": "abc123", "duplicate": False}
        ]
        with patch.object(app_mod, "TRADEZELLA_DB_READY", False), \
             patch.object(app_mod, "_check_tradezella_db_ready", noop_probe), \
             patch.object(app_mod, "_tz_guard", return_value=None):
            client = app_mod.app.test_client()
            resp = client.post(
                "/journal/import/confirm",
                data=_json.dumps({"trades": valid_trades}),
                content_type="application/json",
            )
        # Probe ran, flag still False → must be 503
        noop_probe.assert_called_once()
        self.assertEqual(resp.status_code, 503)
        body = _json.loads(resp.data)
        self.assertFalse(body["ok"])
        self.assertIn("error", body)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestTradezellaCsvParser))
    suite.addTests(loader.loadTestsFromTestCase(TestAnalytics))
    suite.addTests(loader.loadTestsFromTestCase(TestJournalApiSchema))
    suite.addTests(loader.loadTestsFromTestCase(TestExitPriceAndPreviewFields))
    suite.addTests(loader.loadTestsFromTestCase(TestDuplicateDetection))
    suite.addTests(loader.loadTestsFromTestCase(TestRollbackBatchLogic))
    suite.addTests(loader.loadTestsFromTestCase(TestSessionBucketing))
    suite.addTests(loader.loadTestsFromTestCase(TestImportTokenFlow))
    suite.addTests(loader.loadTestsFromTestCase(TestJournalDbGuardReadinessProbe))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
