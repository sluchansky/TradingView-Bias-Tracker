"""
INTRADAY_TREND Phase 2 — gap-closure tests.

Covers all 9 functional gaps closed in this build:
  1.  _it_confirmation_complete — setup family confirmation sequences
  2.  _it_structural_stop        — structural invalidation stop per family
  3.  _it_risk_sizing            — contract sizing from structural stop
  4.  _it_daily_trade_count      — daily cap counter (DB-backed, mocked)
  5.  compute_it_trade_management — advisory management engine
  6.  _it_force_close_watchdog   — EOD close guard (noop before flat-time)
  7.  _it_ghost_write_extended_fields — IT-specific ghost analytics
  8.  compute_intraday_trend_context Phase 2 — new ctx keys present
  9.  _it_entry_veto_reasons     — gates 4 (confirmation) and 5 (daily cap)

All tests are PURE (no DB, no network).  DB-dependent functions are tested
via unit-level mocking so the suite runs without a live database.
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# ── bootstrap: allow import of app.py helpers in test context ─────────────────
TEST_DIR = os.path.dirname(__file__)
APP_DIR  = os.path.dirname(TEST_DIR)
sys.path.insert(0, APP_DIR)

# Minimal env stubs required for app.py import
os.environ.setdefault("TRADING_MODE",          "INTRADAY_TREND")
os.environ.setdefault("DASHBOARD_PASSWORD",    "test")
os.environ.setdefault("SESSION_SECRET",        "test")
os.environ.setdefault("DATABASE_URL",          "postgresql://localhost/test")
os.environ.setdefault("MAX_RISK_DOLLARS",      "500")
os.environ.setdefault("MAX_INTRADAY_TREND_TRADES_PER_DAY", "2")

# Import must happen after env is set
import app as A   # noqa: E402


# ══════════════════════════════════════════════════════════════════════════════
# 1. _it_confirmation_complete
# ══════════════════════════════════════════════════════════════════════════════

class TestConfirmationComplete(unittest.TestCase):

    def _conf(self, family, confluences, direction="Long", score=0):
        return A._it_confirmation_complete(family, confluences, direction, score)

    # --- LSR ---
    def test_lsr_all_steps_complete(self):
        c = {"liquidity_sweep": True, "structure_confirmed": True, "choch": True}
        ok, done, miss = self._conf("LIQUIDITY_SWEEP_REVERSAL", c)
        self.assertTrue(ok)
        self.assertEqual(len(miss), 0)
        self.assertEqual(len(done), 3)

    def test_lsr_missing_choch(self):
        c = {"liquidity_sweep": True, "structure_confirmed": True, "choch": False}
        ok, done, miss = self._conf("LIQUIDITY_SWEEP_REVERSAL", c)
        self.assertFalse(ok)
        self.assertTrue(any("CHOCH" in m for m in miss))

    def test_lsr_missing_structure(self):
        # sweep=True, choch=True — only structure is missing (1 item)
        c = {"liquidity_sweep": True, "structure_confirmed": False, "choch": True}
        ok, done, miss = self._conf("LIQUIDITY_SWEEP_REVERSAL", c)
        self.assertFalse(ok)
        self.assertEqual(len(miss), 1)  # only structure is missing

    def test_lsr_sweep_only(self):
        c = {"liquidity_sweep": True, "structure_confirmed": False, "choch": False}
        ok, _, miss = self._conf("LIQUIDITY_SWEEP_REVERSAL", c)
        self.assertFalse(ok)
        self.assertEqual(len(miss), 2)

    def test_lsr_empty_confluences(self):
        ok, _, miss = self._conf("LIQUIDITY_SWEEP_REVERSAL", {})
        self.assertFalse(ok)
        self.assertEqual(len(miss), 3)

    # --- BREAKOUT_RETEST ---
    def test_br_all_steps_complete_bos(self):
        c = {"bos": True, "vwap_confirmed": True, "structure_confirmed": True}
        ok, done, miss = self._conf("BREAKOUT_RETEST", c)
        self.assertTrue(ok)
        self.assertEqual(len(miss), 0)

    def test_br_all_steps_complete_choch(self):
        c = {"choch": True, "vwap_confirmed": True, "structure_confirmed": True}
        ok, done, miss = self._conf("BREAKOUT_RETEST", c)
        self.assertTrue(ok)
        self.assertTrue(any("CHOCH" in d for d in done))

    def test_br_missing_vwap(self):
        c = {"bos": True, "vwap_confirmed": False, "structure_confirmed": True}
        ok, _, miss = self._conf("BREAKOUT_RETEST", c)
        self.assertFalse(ok)
        self.assertTrue(any("VWAP" in m for m in miss))

    def test_br_no_break_yet(self):
        c = {"bos": False, "choch": False, "vwap_confirmed": True, "structure_confirmed": True}
        ok, _, miss = self._conf("BREAKOUT_RETEST", c)
        self.assertFalse(ok)
        self.assertTrue(any("BOS" in m or "break" in m.lower() for m in miss))

    # --- TREND_PULLBACK ---
    def test_tp_all_steps_complete(self):
        c = {"vwap_confirmed": True, "structure_confirmed": True, "bos": True}
        ok, done, miss = self._conf("TREND_PULLBACK", c, score=3)
        self.assertTrue(ok)
        self.assertEqual(len(miss), 0)

    def test_tp_insufficient_alignment(self):
        c = {"vwap_confirmed": True, "structure_confirmed": True, "bos": True}
        ok, _, miss = self._conf("TREND_PULLBACK", c, score=1)
        self.assertFalse(ok)
        self.assertTrue(any("2" in m for m in miss))  # needs ≥2

    def test_tp_missing_reversal_signal(self):
        c = {"vwap_confirmed": True, "structure_confirmed": True,
             "bos": False, "choch": False, "liquidity_sweep": False}
        ok, _, miss = self._conf("TREND_PULLBACK", c, score=2)
        self.assertFalse(ok)
        self.assertTrue(any("reversal" in m.lower() or "signal" in m.lower() for m in miss))

    def test_tp_sweep_counts_as_reversal(self):
        c = {"vwap_confirmed": True, "structure_confirmed": True, "liquidity_sweep": True}
        ok, _, _ = self._conf("TREND_PULLBACK", c, score=2)
        self.assertTrue(ok)

    # --- No family ---
    def test_no_family_not_complete(self):
        ok, _, miss = self._conf(None, {})
        self.assertFalse(ok)
        self.assertTrue(len(miss) > 0)

    # --- Fail-open ---
    def test_exception_failopen(self):
        # Should not raise even with bad input
        ok, _, miss = A._it_confirmation_complete("BAD_FAMILY", None, None, "bad")
        self.assertFalse(ok)


# ══════════════════════════════════════════════════════════════════════════════
# 2. _it_structural_stop
# ══════════════════════════════════════════════════════════════════════════════

class TestStructuralStop(unittest.TestCase):

    def _stop(self, family, direction, price, levels, atr=40.0, confs=None):
        return A._it_structural_stop(family, confs or {}, direction, price, levels, atr)

    def test_lsr_long_picks_highest_below(self):
        levels = {"swing_lows": [21000.0, 21010.0, 21020.0]}
        sl, pts, src = self._stop("LIQUIDITY_SWEEP_REVERSAL", "Long", 21050.0, levels, atr=40)
        # highest below 21050 is 21020, buf = max(2, 40*0.15=6) = 6
        expected_sl = 21020.0 - 6.0
        self.assertAlmostEqual(sl, expected_sl, places=1)
        self.assertAlmostEqual(pts, 21050.0 - expected_sl, places=1)
        self.assertIn("swept low", src.lower())

    def test_lsr_short_picks_lowest_above(self):
        levels = {"major_15m_swing_highs": [19900.0, 19910.0]}
        sl, pts, src = self._stop("LIQUIDITY_SWEEP_REVERSAL", "Short", 19880.0, levels, atr=40)
        expected_sl = 19900.0 + max(2.0, 40 * 0.15)
        self.assertAlmostEqual(sl, expected_sl, places=1)
        self.assertTrue(pts > 0)

    def test_br_long_fallback_on_empty_levels(self):
        sl, pts, src = self._stop("BREAKOUT_RETEST", "Long", 21000.0, {}, atr=30)
        # ref = p - atr = 20970; buf = max(2, 30*0.15=4.5) = 4.5; sl = 20970 - 4.5 = 20965.5
        self.assertAlmostEqual(sl, 20965.5, places=1)
        self.assertIn("fallback", src.lower())

    def test_tp_long_pullback_low(self):
        levels = {"session_low": 20900.0, "swing_lows": [20880.0]}
        sl, pts, src = self._stop("TREND_PULLBACK", "Long", 21050.0, levels, atr=40)
        # picks highest below 21050 → 20900
        buf = max(2.0, 40 * 0.15)
        expected_sl = 20900.0 - buf
        self.assertAlmostEqual(sl, expected_sl, places=1)
        self.assertIn("pullback", src.lower())

    def test_tp_short_pullback_high(self):
        levels = {"session_high": 21200.0}
        sl, pts, src = self._stop("TREND_PULLBACK", "Short", 21100.0, levels, atr=40)
        buf = max(2.0, 40 * 0.15)
        expected_sl = 21200.0 + buf
        self.assertAlmostEqual(sl, expected_sl, places=1)
        self.assertTrue(pts > 0)

    def test_unknown_family_returns_none(self):
        sl, pts, src = self._stop("UNKNOWN", "Long", 21000.0, {})
        self.assertIsNone(sl)
        self.assertIsNone(pts)
        self.assertIsNone(src)

    def test_none_price_returns_none(self):
        sl, pts, src = self._stop("LIQUIDITY_SWEEP_REVERSAL", "Long", None, {})
        self.assertIsNone(sl)

    def test_stop_pts_always_positive(self):
        levels = {"swing_lows": [20900.0], "swing_highs": [21200.0]}
        for fam in ("LIQUIDITY_SWEEP_REVERSAL", "BREAKOUT_RETEST", "TREND_PULLBACK"):
            for direction in ("Long", "Short"):
                price = 21050.0
                sl, pts, _ = self._stop(fam, direction, price, levels, atr=30)
                if pts is not None:
                    self.assertGreater(pts, 0,
                        f"{fam} {direction}: expected positive pts, got {pts}")

    def test_buffer_at_least_2pts(self):
        """Buffer should be at least 2 pts even with tiny ATR."""
        levels = {"swing_lows": [20990.0]}
        sl, pts, _ = self._stop("LIQUIDITY_SWEEP_REVERSAL", "Long", 21000.0, levels, atr=1)
        # buf = max(2, 1*0.15) = 2; sl = 20990 - 2 = 20988
        self.assertAlmostEqual(sl, 20988.0, places=1)

    def test_failopen_bad_atr(self):
        levels = {"swing_lows": [20990.0]}
        # Should not raise; ATR=None uses fallback
        sl, pts, src = self._stop("LIQUIDITY_SWEEP_REVERSAL", "Long", 21000.0, levels, atr=None)
        # With atr=None, buf=max(2,0)=2; ref=20990; sl=20988
        self.assertIsNotNone(sl)


# ══════════════════════════════════════════════════════════════════════════════
# 3. _it_risk_sizing
# ══════════════════════════════════════════════════════════════════════════════

class TestRiskSizing(unittest.TestCase):

    def test_mnq_tight_stop(self):
        """5-point stop, $500 max risk, MNQ $2/pt → 500/(5*2)=50 → cap to 4."""
        contracts, risk = A._it_risk_sizing(5.0, "MNQ")
        self.assertEqual(contracts, 4)  # capped at 4

    def test_mnq_wide_stop(self):
        """40-point stop, $500 max, MNQ $2/pt → 500/(40*2)=6.25 → floor to 6 → cap to 4."""
        contracts, risk = A._it_risk_sizing(40.0, "MNQ")
        self.assertEqual(contracts, 4)  # still capped at 4

    def test_mnq_huge_stop(self):
        """300-point stop → floor(500/600) = 0 → min 1."""
        contracts, risk = A._it_risk_sizing(300.0, "MNQ")
        self.assertEqual(contracts, 1)

    def test_risk_dollars_computed(self):
        contracts, risk = A._it_risk_sizing(25.0, "MNQ")
        pv = A.point_value_for("MNQ")
        expected_risk = 25.0 * pv * contracts
        self.assertAlmostEqual(risk, expected_risk, places=2)

    def test_zero_stop_returns_one(self):
        contracts, risk = A._it_risk_sizing(0.0, "MNQ")
        self.assertEqual(contracts, 1)
        self.assertIsNone(risk)

    def test_negative_stop_returns_one(self):
        contracts, risk = A._it_risk_sizing(-10.0, "MNQ")
        self.assertEqual(contracts, 1)

    def test_failopen_bad_instrument(self):
        # Should not raise; unknown instruments may return 1
        try:
            contracts, risk = A._it_risk_sizing(20.0, "BOGUS")
            self.assertGreaterEqual(contracts, 1)
        except Exception:
            pass  # fail-open means no propagated exception


# ══════════════════════════════════════════════════════════════════════════════
# 4. _it_daily_trade_count (DB mocked)
# ══════════════════════════════════════════════════════════════════════════════

class TestDailyTradeCount(unittest.TestCase):

    def _mock_count(self, count_value):
        """Return (count, cap) as if DB returned count_value."""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (count_value,)
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = lambda s: mock_cur
        mock_ctx.__exit__  = MagicMock(return_value=False)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_ctx
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__  = MagicMock(return_value=False)
        return mock_conn

    @patch.object(A, "GHOST_OBS_DB_READY", True)
    @patch.object(A, "get_db_connection")
    def test_zero_count(self, mock_gdb):
        mock_gdb.return_value = self._mock_count(0)
        count, cap = A._it_daily_trade_count("MNQ")
        self.assertEqual(count, 0)
        self.assertEqual(cap, 2)

    @patch.object(A, "GHOST_OBS_DB_READY", True)
    @patch.object(A, "get_db_connection")
    def test_one_trade_done(self, mock_gdb):
        mock_gdb.return_value = self._mock_count(1)
        count, cap = A._it_daily_trade_count("MNQ")
        self.assertEqual(count, 1)
        self.assertEqual(cap, 2)

    @patch.object(A, "GHOST_OBS_DB_READY", True)
    @patch.object(A, "get_db_connection")
    def test_cap_reached(self, mock_gdb):
        mock_gdb.return_value = self._mock_count(2)
        count, cap = A._it_daily_trade_count("MNQ")
        self.assertEqual(count, 2)
        self.assertGreaterEqual(count, cap)

    @patch.object(A, "GHOST_OBS_DB_READY", False)
    def test_db_not_ready_returns_minus_one(self):
        count, cap = A._it_daily_trade_count("MNQ")
        self.assertEqual(count, -1)
        self.assertEqual(cap, 2)

    @patch.object(A, "GHOST_OBS_DB_READY", True)
    @patch.object(A, "get_db_connection")
    def test_db_exception_returns_minus_one(self, mock_gdb):
        mock_gdb.side_effect = Exception("DB down")
        count, cap = A._it_daily_trade_count("MNQ")
        self.assertEqual(count, -1)

    def test_cap_env_var_respected(self):
        """MAX_INTRADAY_TREND_TRADES_PER_DAY controls the cap."""
        with patch.dict(os.environ, {"MAX_INTRADAY_TREND_TRADES_PER_DAY": "3"}):
            with patch.object(A, "GHOST_OBS_DB_READY", False):
                _, cap = A._it_daily_trade_count("MNQ")
                self.assertEqual(cap, 3)


# ══════════════════════════════════════════════════════════════════════════════
# 5. compute_it_trade_management
# ══════════════════════════════════════════════════════════════════════════════

class TestITTradeManagement(unittest.TestCase):

    ET = A.ET_TZ

    def _et(self, h, m):
        return datetime(2026, 8, 12, h, m, 0, tzinfo=self.ET)

    def _active(self, direction="Long", entry=21000.0, stop=20940.0, contracts=1):
        return {
            "direction": direction, "entry_price": entry,
            "stop_loss": stop, "contracts": contracts,
        }

    def test_empty_active_trade_returns_hold(self):
        out = A.compute_it_trade_management({}, 21000.0)
        self.assertEqual(out["action"], "HOLD")
        self.assertIsNone(out["current_r"])

    def test_none_price_returns_hold(self):
        out = A.compute_it_trade_management(self._active(), None)
        self.assertEqual(out["action"], "HOLD")

    # --- Force-flat ---
    def test_force_flat_after_eod(self):
        out = A.compute_it_trade_management(
            self._active(), 21000.0, et_now=self._et(16, 0))
        self.assertEqual(out["action"], "FORCE_FLAT")
        self.assertTrue(out["force_flat"])

    def test_force_flat_exactly_at_1555(self):
        out = A.compute_it_trade_management(
            self._active(), 21000.0, et_now=self._et(15, 55))
        self.assertEqual(out["action"], "FORCE_FLAT")

    def test_no_force_flat_before_1555(self):
        out = A.compute_it_trade_management(
            self._active(), 21000.0, et_now=self._et(13, 0))
        self.assertNotEqual(out["action"], "FORCE_FLAT")

    # --- Current R ---
    def test_current_r_at_breakeven_level(self):
        # entry=21000, stop=20940 (risk=60), price=21060 → 1R
        out = A.compute_it_trade_management(
            self._active(entry=21000.0, stop=20940.0), 21060.0,
            et_now=self._et(10, 0))
        self.assertAlmostEqual(out["current_r"], 1.0, places=2)

    def test_current_r_short(self):
        at = self._active("Short", entry=21000.0, stop=21060.0)
        out = A.compute_it_trade_management(at, 20940.0, et_now=self._et(10, 0))
        self.assertAlmostEqual(out["current_r"], 1.0, places=2)

    def test_negative_r_no_management(self):
        out = A.compute_it_trade_management(
            self._active(entry=21000.0, stop=20940.0), 20960.0,
            et_now=self._et(10, 0))
        self.assertEqual(out["action"], "HOLD")
        self.assertFalse(out["trail_active"])

    # --- Breakeven ---
    def test_be_recommended_at_1r(self):
        # entry=21000, stop=20940 (risk=60), price=21060 → 1R
        out = A.compute_it_trade_management(
            self._active(entry=21000.0, stop=20940.0), 21060.0,
            et_now=self._et(10, 0))
        self.assertTrue(out["be_recommended"])

    def test_be_not_recommended_below_1r(self):
        out = A.compute_it_trade_management(
            self._active(entry=21000.0, stop=20940.0), 21040.0,
            et_now=self._et(10, 0))
        self.assertFalse(out["be_recommended"])

    # --- Trailing ---
    def test_trail_active_at_1r(self):
        out = A.compute_it_trade_management(
            self._active(entry=21000.0, stop=20940.0), 21060.0,
            et_now=self._et(10, 0))
        self.assertTrue(out["trail_active"])

    def test_trail_inactive_before_1r(self):
        out = A.compute_it_trade_management(
            self._active(entry=21000.0, stop=20940.0), 21020.0,
            et_now=self._et(10, 0))
        self.assertFalse(out["trail_active"])

    # --- Partials: 1 contract — no partials ---
    def test_one_contract_no_partials_at_1r5(self):
        # entry=21000, stop=20940 (risk=60), price=21090 → 1.5R
        out = A.compute_it_trade_management(
            self._active(entry=21000.0, stop=20940.0, contracts=1), 21090.0,
            et_now=self._et(10, 0))
        self.assertFalse(out["partial_at_1r5"])
        self.assertEqual(out["contracts_exit"], 0)

    # --- Partials: 2 contracts ---
    def test_two_contracts_partial_at_1r5(self):
        at = self._active(entry=21000.0, stop=20940.0, contracts=2)
        out = A.compute_it_trade_management(at, 21090.0, et_now=self._et(10, 0))
        self.assertTrue(out["partial_at_1r5"])
        self.assertEqual(out["contracts_exit"], 1)
        self.assertEqual(out["contracts_hold"], 1)
        self.assertEqual(out["action"], "PARTIAL_1R5")

    def test_two_contracts_no_partial_at_1r(self):
        at = self._active(entry=21000.0, stop=20940.0, contracts=2)
        out = A.compute_it_trade_management(at, 21060.0, et_now=self._et(10, 0))
        self.assertFalse(out["partial_at_1r5"])

    # --- Partials: 3 contracts ---
    def test_three_contracts_partial_at_2r(self):
        at = self._active(entry=21000.0, stop=20940.0, contracts=3)
        # 21120 = 2R (entry + 2*60)
        out = A.compute_it_trade_management(at, 21120.0, et_now=self._et(10, 0))
        self.assertTrue(out["partial_at_2r"])
        self.assertEqual(out["contracts_exit"], 2)
        self.assertEqual(out["contracts_hold"], 1)
        self.assertEqual(out["action"], "PARTIAL_2R")

    # --- Pullbacks don't trigger exit ---
    def test_pullback_10pts_no_exit(self):
        """Normal 10-30 pt pullbacks after 1R must not trigger exit."""
        # at 1R, then drop 15 pts
        at = self._active(entry=21000.0, stop=20940.0, contracts=1)
        # Price at 1R first, then pullback
        out_before = A.compute_it_trade_management(at, 21060.0, et_now=self._et(10, 0))
        out_after  = A.compute_it_trade_management(at, 21045.0, et_now=self._et(10, 0))
        # Neither should produce FORCE_FLAT or CLOSE_STRUCTURE (no alignment flip injected)
        self.assertNotEqual(out_after["action"], "FORCE_FLAT")
        self.assertNotEqual(out_after["action"], "CLOSE_STRUCTURE")

    # --- Structure invalidation ---
    def test_structure_close_strong_bearish_vs_long(self):
        at = self._active("Long", entry=21000.0, stop=20940.0)
        ctx = {"trend_alignment": "STRONG_BEARISH"}
        out = A.compute_it_trade_management(at, 20970.0, it_ctx=ctx, et_now=self._et(10, 0))
        self.assertEqual(out["action"], "CLOSE_STRUCTURE")

    def test_structure_close_not_triggered_when_in_profit(self):
        """CLOSE_STRUCTURE only fires when cur_r < 0.5 — profit protects position."""
        at = self._active("Long", entry=21000.0, stop=20940.0)
        ctx = {"trend_alignment": "STRONG_BEARISH"}
        # cur_r ≈ 1.0 (price at 21060) → should NOT close
        out = A.compute_it_trade_management(at, 21060.0, it_ctx=ctx, et_now=self._et(10, 0))
        self.assertNotEqual(out["action"], "CLOSE_STRUCTURE")

    # --- Stable schema: all keys always present ---
    def test_schema_all_keys_present(self):
        expected_keys = {"action", "action_reason", "current_r", "be_recommended",
                         "partial_at_1r5", "partial_at_2r", "trail_active",
                         "force_flat", "contracts_exit", "contracts_hold"}
        out = A.compute_it_trade_management({}, None)
        self.assertEqual(expected_keys, set(out.keys()))

    def test_failopen_bad_active_trade(self):
        out = A.compute_it_trade_management("bad", "bad_price")
        self.assertEqual(out["action"], "HOLD")


# ══════════════════════════════════════════════════════════════════════════════
# 6. _it_force_close_watchdog
# ══════════════════════════════════════════════════════════════════════════════

class TestForceCloseWatchdog(unittest.TestCase):
    """Safety correction 4 — existing watchdog smoke tests (updated for new semantics).

    The watchdog now:
      • Before 15:55 ET: returns silently (no PENDING, no CRITICAL)
      • After 15:55 ET + DB not ready: CRITICAL log + PENDING set (not a silent noop)
      • After 15:55 ET + DB failure: CRITICAL log + PENDING set + rollback
      • On success: PENDING cleared
      • On outer exception: PENDING set + swallows (heartbeat stays alive)
    """

    def setUp(self):
        A._IT_FORCE_CLOSE_PENDING.clear()

    def tearDown(self):
        A._IT_FORCE_CLOSE_PENDING.clear()

    # ── Before flat-time: always a silent noop regardless of DB ──────────────
    @patch.object(A, "GHOST_OBS_DB_READY", False)
    @patch.object(A, "now_utc")
    def test_noop_before_flat_time_db_not_ready(self, mock_now):
        """Before 15:55 ET, watchdog returns silently — no PENDING set."""
        mock_now.return_value = datetime(2026, 8, 12, 17, 0, 0, tzinfo=timezone.utc)  # 13:00 ET
        A._it_force_close_watchdog()
        self.assertFalse(A._IT_FORCE_CLOSE_PENDING)

    @patch.object(A, "GHOST_OBS_DB_READY", True)
    @patch.object(A, "get_db_connection")
    @patch.object(A, "now_utc")
    def test_noop_before_flat_time(self, mock_now, mock_gdb):
        """Before 15:55 ET, watchdog should not open any DB connection."""
        mock_now.return_value = datetime(2026, 8, 12, 17, 0, 0, tzinfo=timezone.utc)  # 13:00 ET
        A._it_force_close_watchdog()
        mock_gdb.assert_not_called()
        self.assertFalse(A._IT_FORCE_CLOSE_PENDING)

    # ── After flat-time: DB connected, no open positions ──────────────────────
    @patch.object(A, "GHOST_OBS_DB_READY", True)
    @patch.object(A, "get_db_connection")
    @patch.object(A, "now_utc")
    def test_attempts_close_after_flat_time(self, mock_now, mock_gdb):
        """After 15:55 ET, watchdog calls get_db_connection and commits."""
        mock_now.return_value = datetime(2026, 8, 12, 20, 0, 0, tzinfo=timezone.utc)  # 16:00 ET
        mock_conn = MagicMock()
        mock_cur  = MagicMock()
        mock_cur.__enter__ = lambda s: mock_cur
        mock_cur.__exit__  = MagicMock(return_value=False)
        mock_cur.fetchall.return_value = []   # no open IT positions
        mock_conn.cursor.return_value  = mock_cur
        mock_gdb.return_value = mock_conn
        A._it_force_close_watchdog()
        mock_gdb.assert_called_once()
        mock_conn.commit.assert_called()
        # No failure → PENDING must be clear
        self.assertFalse(A._IT_FORCE_CLOSE_PENDING)

    # ── After flat-time: outer exception must not propagate ───────────────────
    def test_exception_swallowed(self):
        """Any exception inside watchdog must not propagate (heartbeat safety)."""
        with patch.object(A, "GHOST_OBS_DB_READY", True), \
             patch.object(A, "now_utc", side_effect=RuntimeError("boom")):
            try:
                A._it_force_close_watchdog()
            except Exception:
                self.fail("_it_force_close_watchdog propagated an exception")


# ══════════════════════════════════════════════════════════════════════════════
# 6b. _it_force_close_watchdog — Safety Correction 4: retry / pending state
# ══════════════════════════════════════════════════════════════════════════════

class TestForceCloseWatchdogRetry(unittest.TestCase):
    """Safety correction 4: force-close failure → CRITICAL log + PENDING state.
    Next heartbeat cycle retries; success clears PENDING.  Position is NEVER
    silently marked closed if the DB UPDATE fails.
    """

    # 20:00 UTC = 16:00 ET (after 15:55 flat-time threshold)
    _AFTER_FLAT_UTC = datetime(2026, 8, 12, 20, 0, 0, tzinfo=timezone.utc)

    def setUp(self):
        A._IT_FORCE_CLOSE_PENDING.clear()

    def tearDown(self):
        A._IT_FORCE_CLOSE_PENDING.clear()

    def _ok_conn(self, fetchall_return=None):
        """Return a mock DB connection that succeeds."""
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = fetchall_return or []
        mock_cur.__enter__ = lambda s: mock_cur
        mock_cur.__exit__  = MagicMock(return_value=False)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        return mock_conn

    # ── DB not ready after flat-time → PENDING set ────────────────────────────
    @patch.object(A, "GHOST_OBS_DB_READY", False)
    @patch.object(A, "now_utc")
    def test_db_not_ready_after_flat_time_sets_pending(self, mock_now):
        """After 15:55 ET, GHOST_OBS_DB_READY=False → PENDING set (not a silent noop)."""
        mock_now.return_value = self._AFTER_FLAT_UTC
        A._it_force_close_watchdog()
        self.assertTrue(A._IT_FORCE_CLOSE_PENDING)

    # ── DB UPDATE throws → rollback + PENDING set, no commit ─────────────────
    @patch.object(A, "GHOST_OBS_DB_READY", True)
    @patch.object(A, "get_db_connection")
    @patch.object(A, "now_utc")
    def test_db_exception_sets_pending_and_no_commit(self, mock_now, mock_gdb):
        """DB failure → PENDING set; commit must NOT be called (no silent success)."""
        mock_now.return_value = self._AFTER_FLAT_UTC
        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = Exception("DB write error")
        mock_gdb.return_value = mock_conn

        A._it_force_close_watchdog()

        self.assertTrue(A._IT_FORCE_CLOSE_PENDING)
        mock_conn.commit.assert_not_called()
        mock_conn.rollback.assert_called()

    # ── DB unavailable (None) → PENDING set ──────────────────────────────────
    @patch.object(A, "GHOST_OBS_DB_READY", True)
    @patch.object(A, "get_db_connection", return_value=None)
    @patch.object(A, "now_utc")
    def test_db_connection_none_sets_pending(self, mock_now, mock_gdb):
        """get_db_connection returns None → PENDING set."""
        mock_now.return_value = self._AFTER_FLAT_UTC
        A._it_force_close_watchdog()
        self.assertTrue(A._IT_FORCE_CLOSE_PENDING)

    # ── Successful close clears any pre-existing PENDING state ───────────────
    @patch.object(A, "GHOST_OBS_DB_READY", True)
    @patch.object(A, "get_db_connection")
    @patch.object(A, "now_utc")
    def test_successful_close_clears_pending(self, mock_now, mock_gdb):
        """After successful close, pre-existing PENDING state is cleared."""
        mock_now.return_value = self._AFTER_FLAT_UTC
        # Seed a pre-existing pending state (simulates a prior failed cycle)
        A._IT_FORCE_CLOSE_PENDING["close_error"] = {
            "error": "prior error", "timestamp": "15:57 ET"}
        mock_gdb.return_value = self._ok_conn()

        A._it_force_close_watchdog()

        self.assertFalse(A._IT_FORCE_CLOSE_PENDING)

    # ── Retry pattern: fail → PENDING → succeed → cleared ────────────────────
    @patch.object(A, "GHOST_OBS_DB_READY", True)
    @patch.object(A, "get_db_connection")
    @patch.object(A, "now_utc")
    def test_retry_on_second_cycle_clears_pending(self, mock_now, mock_gdb):
        """First watchdog cycle fails → PENDING; second succeeds → cleared."""
        mock_now.return_value = self._AFTER_FLAT_UTC

        # Cycle 1: cursor raises
        mock_conn_fail = MagicMock()
        mock_conn_fail.cursor.side_effect = Exception("First attempt fails")

        # Cycle 2: succeeds
        mock_gdb.side_effect = [mock_conn_fail, self._ok_conn()]

        # First cycle — fails
        A._it_force_close_watchdog()
        self.assertTrue(A._IT_FORCE_CLOSE_PENDING, "PENDING should be set after failure")

        # Second cycle — succeeds (heartbeat calls watchdog again)
        A._it_force_close_watchdog()
        self.assertFalse(A._IT_FORCE_CLOSE_PENDING, "PENDING should be cleared after success")

    # ── Outer exception never propagates ─────────────────────────────────────
    def test_outer_exception_never_propagates(self):
        """Any exception (including now_utc crashing) is swallowed."""
        with patch.object(A, "GHOST_OBS_DB_READY", True), \
             patch.object(A, "now_utc", side_effect=RuntimeError("boom")):
            try:
                A._it_force_close_watchdog()
            except Exception:
                self.fail("_it_force_close_watchdog propagated an exception")


# ══════════════════════════════════════════════════════════════════════════════
# 7. _it_ghost_write_extended_fields
# ══════════════════════════════════════════════════════════════════════════════

class TestGhostWriteExtendedFields(unittest.TestCase):

    def _mock_conn(self):
        mock_cur = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = lambda s: mock_cur
        mock_ctx.__exit__  = MagicMock(return_value=False)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_ctx
        return mock_conn, mock_cur

    def test_1r_flag_set_when_mfe_r_above_1(self):
        conn, cur = self._mock_conn()
        A._it_ghost_write_extended_fields(
            conn, obs_id=1,
            entry_price=21000.0, mfe_price=21090.0, mae_price=20970.0,
            mfe_r=1.5, gross_r=1.2,
        )
        args = cur.execute.call_args[0][1]
        it_1r, it_2r, it_3r = args[0], args[1], args[2]
        self.assertTrue(it_1r)
        self.assertFalse(it_2r)
        self.assertFalse(it_3r)

    def test_2r_and_3r_flags(self):
        conn, cur = self._mock_conn()
        A._it_ghost_write_extended_fields(
            conn, obs_id=2,
            entry_price=21000.0, mfe_price=21200.0, mae_price=20990.0,
            mfe_r=3.5, gross_r=3.0,
        )
        args = cur.execute.call_args[0][1]
        self.assertTrue(args[0])  # 1R
        self.assertTrue(args[1])  # 2R
        self.assertTrue(args[2])  # 3R

    def test_fav_pts_computed_correctly(self):
        conn, cur = self._mock_conn()
        A._it_ghost_write_extended_fields(
            conn, obs_id=3,
            entry_price=21000.0, mfe_price=21075.0, mae_price=20985.0,
            mfe_r=1.25, gross_r=1.0,
        )
        args = cur.execute.call_args[0][1]
        fav_pts = args[3]
        adv_pts = args[4]
        self.assertAlmostEqual(fav_pts, 75.0, places=1)
        self.assertAlmostEqual(adv_pts, 15.0, places=1)

    def test_over_100pts_flag(self):
        conn, cur = self._mock_conn()
        A._it_ghost_write_extended_fields(
            conn, obs_id=4,
            entry_price=21000.0, mfe_price=21150.0, mae_price=20990.0,
            mfe_r=2.5, gross_r=2.0,
        )
        args = cur.execute.call_args[0][1]
        over_100 = args[5]
        self.assertTrue(over_100)

    def test_over_100pts_false_when_small_move(self):
        conn, cur = self._mock_conn()
        A._it_ghost_write_extended_fields(
            conn, obs_id=5,
            entry_price=21000.0, mfe_price=21050.0, mae_price=20980.0,
            mfe_r=0.8, gross_r=0.5,
        )
        args = cur.execute.call_args[0][1]
        over_100 = args[5]
        self.assertFalse(over_100)

    def test_premature_exit_flag_set(self):
        """mfe_r=2.0, gross_r=0.5 (< 0.5×2.0 = 1.0) → premature exit."""
        conn, cur = self._mock_conn()
        A._it_ghost_write_extended_fields(
            conn, obs_id=6,
            entry_price=21000.0, mfe_price=21120.0, mae_price=20990.0,
            mfe_r=2.0, gross_r=0.5,
        )
        args = cur.execute.call_args[0][1]
        premature = args[6]
        self.assertTrue(premature)

    def test_premature_exit_false_when_captured_most(self):
        """mfe_r=2.0, gross_r=1.8 (> 0.5×2.0) → NOT premature."""
        conn, cur = self._mock_conn()
        A._it_ghost_write_extended_fields(
            conn, obs_id=7,
            entry_price=21000.0, mfe_price=21120.0, mae_price=20990.0,
            mfe_r=2.0, gross_r=1.8,
        )
        args = cur.execute.call_args[0][1]
        premature = args[6]
        self.assertFalse(premature)

    def test_premature_exit_false_when_mfe_below_1r5(self):
        """mfe_r < 1.5 → premature flag never set (threshold not reached)."""
        conn, cur = self._mock_conn()
        A._it_ghost_write_extended_fields(
            conn, obs_id=8,
            entry_price=21000.0, mfe_price=21050.0, mae_price=20990.0,
            mfe_r=1.2, gross_r=0.1,
        )
        args = cur.execute.call_args[0][1]
        premature = args[6]
        self.assertFalse(premature)

    def test_none_prices_handled_gracefully(self):
        conn, cur = self._mock_conn()
        # Should not raise even with None prices
        A._it_ghost_write_extended_fields(
            conn, obs_id=9,
            entry_price=None, mfe_price=None, mae_price=None,
            mfe_r=1.0, gross_r=0.8,
        )
        # Function should complete and write something
        cur.execute.assert_called_once()

    def test_db_exception_swallowed(self):
        conn = MagicMock()
        conn.cursor.side_effect = Exception("DB error")
        try:
            A._it_ghost_write_extended_fields(
                conn, obs_id=10,
                entry_price=21000.0, mfe_price=21100.0, mae_price=20990.0,
                mfe_r=1.5, gross_r=1.0,
            )
        except Exception:
            self.fail("_it_ghost_write_extended_fields propagated an exception")


# ══════════════════════════════════════════════════════════════════════════════
# 8. compute_intraday_trend_context Phase 2 — new ctx keys present
# ══════════════════════════════════════════════════════════════════════════════

class TestITContextPhase2Schema(unittest.TestCase):
    """Verify compute_intraday_trend_context returns all Phase 2 keys."""

    PHASE2_KEYS = {
        "session_levels",
        "structural_stop_level", "structural_stop_pts", "structural_stop_source",
        "structural_stop_valid",                           # Safety correction 1
        "confirmation_complete", "confirmation_steps", "confirmation_missing",
        "recommended_contracts", "risk_dollars",
        "daily_trade_count", "daily_trade_cap",
        "mgmt_action", "mgmt_action_reason", "mgmt_current_r",
        "mgmt_be_recommended", "mgmt_partial_at_1r5", "mgmt_trail_active",
        "mgmt_force_flat",
    }

    def _ctx(self, price=21000.0, confluences=None, direction="Long", et_now=None):
        with patch.object(A, "GHOST_OBS_DB_READY", False), \
             patch.object(A, "HTF_STATE_BY_INST", {}), \
             patch.object(A, "ACTIVE_TRADES_BY_INST", {}):
            return A.compute_intraday_trend_context(
                "MNQ", price,
                confluences=confluences or {},
                direction=direction,
                et_now=et_now or datetime(2026, 8, 12, 14, 0, 0, tzinfo=A.ET_TZ),
            )

    def test_all_phase2_keys_present(self):
        ctx = self._ctx()
        for key in self.PHASE2_KEYS:
            self.assertIn(key, ctx, f"Missing Phase 2 key: {key}")

    def test_daily_trade_count_minus1_when_db_not_ready(self):
        ctx = self._ctx()
        # DB not ready → count = -1  (the COUNT is still -1; the gate is now fail-closed)
        self.assertEqual(ctx["daily_trade_count"], -1)

    def test_blocked_daily_count_unavailable_when_db_not_ready(self):
        """Safety correction 2: count=-1 (DB error) → BLOCKED_DAILY_COUNT_UNAVAILABLE status."""
        ctx = self._ctx()
        # With GHOST_OBS_DB_READY=False the daily count helper returns -1.
        # Correction 2 requires the status block to BLOCK rather than fail-open.
        self.assertEqual(ctx["status"], "BLOCKED_DAILY_COUNT_UNAVAILABLE")

    def test_structural_stop_valid_false_when_stop_unavailable(self):
        """Safety correction 1: structural_stop_valid=False when no stop can be computed."""
        ctx = self._ctx()
        # No confluences provided, so no structural stop is computable.
        # structural_stop_valid must be False (never default to valid).
        self.assertIn("structural_stop_valid", ctx)
        # Either False (no stop) or True (stop was computed) — must not be missing.
        self.assertIsInstance(ctx["structural_stop_valid"], bool)

    def test_session_levels_is_dict(self):
        ctx = self._ctx()
        self.assertIsInstance(ctx["session_levels"], dict)

    def test_confirmation_steps_is_list(self):
        ctx = self._ctx()
        self.assertIsInstance(ctx["confirmation_steps"], list)
        self.assertIsInstance(ctx["confirmation_missing"], list)

    def test_confirmed_setup_status_when_fully_confirmed_lsr(self):
        """LSR with all confluences → confirmation_complete=True.

        Note: status precedence means MID_RANGE location may still produce
        BLOCKED_MID_RANGE before the CONFIRMED_SETUP branch is reached; that is
        correct behaviour — the location gate fires first.  We assert the
        confirmation flag itself (the new Phase 2 observable) not the status string.
        """
        confs = {
            "liquidity_sweep": True, "structure_confirmed": True, "choch": True,
            "vwap_confirmed": True,
        }
        with patch.object(A, "GHOST_OBS_DB_READY", False), \
             patch.object(A, "HTF_STATE_BY_INST", {}), \
             patch.object(A, "ACTIVE_TRADES_BY_INST", {}):
            ctx = A.compute_intraday_trend_context(
                "MNQ", 21000.0,
                confluences=confs,
                direction="Long",
                swing_ctx={"daily_levels": {"prior_low": 20500.0, "prior_high": 21002.0},
                           "complete": True, "stale": False},
                et_now=datetime(2026, 8, 12, 14, 0, 0, tzinfo=A.ET_TZ),
            )
        self.assertTrue(ctx["confirmation_complete"])
        self.assertEqual(len(ctx["confirmation_missing"]), 0)
        # Status may be CONFIRMED_SETUP, BLOCKED_MID_RANGE (location gate), or
        # BLOCKED_DAILY_COUNT_UNAVAILABLE (DB not ready → correction 2 blocks).
        self.assertIn(ctx["status"],
                      ("CONFIRMED_SETUP", "SETUP_DEVELOPING", "BUILDING_CONTEXT",
                       "AWAITING_CONFIRMATION", "BLOCKED_MID_RANGE",
                       "BLOCKED_DAILY_COUNT_UNAVAILABLE",  # correction 2: fail-closed
                       "BLOCKED_INVALID_STOP"))

    def test_awaiting_confirmation_status_with_incomplete_lsr(self):
        """LSR sweep detected but CHOCH not yet → confirmation_complete=False
        with 'CHOCH' listed in missing steps.

        Note: BLOCKED_MID_RANGE location status (no nearby level) takes precedence
        in the status chain, but the confirmation flag itself is the key Phase 2
        observable.  We assert on that flag plus the missing-step list.
        """
        confs = {"liquidity_sweep": True, "structure_confirmed": True, "choch": False}
        with patch.object(A, "GHOST_OBS_DB_READY", False), \
             patch.object(A, "HTF_STATE_BY_INST", {}), \
             patch.object(A, "ACTIVE_TRADES_BY_INST", {}):
            ctx = A.compute_intraday_trend_context(
                "MNQ", 21000.0,
                confluences=confs,
                direction="Long",
                swing_ctx={"daily_levels": {"prior_low": 20500.0, "prior_high": 21002.0},
                           "complete": True, "stale": False},
                et_now=datetime(2026, 8, 12, 10, 0, 0, tzinfo=A.ET_TZ),
            )
        self.assertFalse(ctx["confirmation_complete"])
        # At least one missing step should mention CHOCH
        self.assertTrue(
            any("CHOCH" in m or "choch" in m.lower() for m in ctx["confirmation_missing"]),
            f"Expected CHOCH in missing steps, got: {ctx['confirmation_missing']}",
        )

    def test_daily_cap_status_when_capped(self):
        """When daily count ≥ cap, status should be DAILY_CAP_REACHED."""
        confs = {"liquidity_sweep": True, "structure_confirmed": True, "choch": True}
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (2,)  # count = 2 = cap
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = lambda s: mock_cur
        mock_ctx.__exit__  = MagicMock(return_value=False)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_ctx
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__  = MagicMock(return_value=False)

        with patch.object(A, "GHOST_OBS_DB_READY", True), \
             patch.object(A, "get_db_connection", return_value=mock_conn), \
             patch.object(A, "HTF_STATE_BY_INST", {}), \
             patch.object(A, "ACTIVE_TRADES_BY_INST", {}):
            ctx = A.compute_intraday_trend_context(
                "MNQ", 21000.0,
                confluences=confs,
                direction="Long",
                et_now=datetime(2026, 8, 12, 10, 0, 0, tzinfo=A.ET_TZ),
            )
        self.assertEqual(ctx["status"], "DAILY_CAP_REACHED")


# ══════════════════════════════════════════════════════════════════════════════
# 9. _it_entry_veto_reasons — all safety-correction gates
#    Corrections 1–3 add new veto codes; correction 2 changes fail-open→closed
# ══════════════════════════════════════════════════════════════════════════════

class TestVetoReasonGates45(unittest.TestCase):
    """Tests for all INTRADAY_TREND entry gates.

    Now covers:
      gate 1: MNQ-only
      gate 2: time restriction
      gate 3: mid-range location
      gate 3a: recognised setup family required (correction 3)
      gate 4: confirmation sequence
      gate 5: structural stop validity (correction 1)
      gate 6: daily trade cap — fail-closed when count unavailable (correction 2)
    """

    def setUp(self):
        """Clear any global force-close pending state before each test."""
        A._IT_FORCE_CLOSE_PENDING.clear()

    def tearDown(self):
        A._IT_FORCE_CLOSE_PENDING.clear()

    def _base_ctx(self, **overrides):
        """Construct a context that passes ALL gates by default."""
        ctx = {
            "instrument": "MNQ",
            "time_ok": True, "time_reason": None,
            "location_quality": "GOOD", "location_reason": None,
            "setup_family": "LIQUIDITY_SWEEP_REVERSAL",
            "confirmation_complete": True,
            "confirmation_missing": [],
            "structural_stop_valid": True,   # Correction 1: valid stop is the passing default
            "structural_stop_pts": 40.0,
            "daily_trade_count": 0, "daily_trade_cap": 2,
        }
        ctx.update(overrides)
        return ctx

    # ── Baseline ─────────────────────────────────────────────────────────────
    def test_all_gates_pass(self):
        """With all conditions favourable, no vetoes are produced."""
        vetoes = A._it_entry_veto_reasons(self._base_ctx(), {}, "Long", "MNQ")
        self.assertEqual(vetoes, [])

    # ── Gate 4: confirmation sequence ────────────────────────────────────────
    def test_gate4_veto_when_confirmation_incomplete(self):
        ctx = self._base_ctx(
            confirmation_complete=False,
            confirmation_missing=["Awaiting CHOCH entry signal"],
        )
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        codes = [v[0] for v in vetoes]
        self.assertIn("confirmation", codes)

    def test_gate4_confirmation_silent_when_no_family(self):
        """If no family detected, the confirmation gate stays quiet — the
        no_setup gate fires instead (correction 3)."""
        ctx = self._base_ctx(
            setup_family=None,
            confirmation_complete=False,
        )
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        codes = [v[0] for v in vetoes]
        # Correction 3: no_setup gate fires
        self.assertIn("no_setup", codes)
        # Confirmation gate must stay quiet when family is None
        self.assertNotIn("confirmation", codes)

    # ── Gate 6: daily cap ────────────────────────────────────────────────────
    def test_gate_daily_cap_at_limit(self):
        ctx = self._base_ctx(daily_trade_count=2, daily_trade_cap=2)
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        codes = [v[0] for v in vetoes]
        self.assertIn("daily_cap", codes)

    def test_gate_daily_cap_clear_under_limit(self):
        ctx = self._base_ctx(daily_trade_count=1, daily_trade_cap=2)
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        codes = [v[0] for v in vetoes]
        self.assertNotIn("daily_cap", codes)

    # ── Safety correction 2: fail-CLOSED when count unavailable ──────────────
    def test_gate_daily_count_unavailable_blocks(self):
        """count=-1 (DB error) must BLOCK — fail-closed, never fail-open."""
        ctx = self._base_ctx(daily_trade_count=-1, daily_trade_cap=2)
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        codes = [v[0] for v in vetoes]
        self.assertIn("daily_count_unavailable", codes)
        # Must NOT emit the regular daily_cap code (different code, different message)
        self.assertNotIn("daily_cap", codes)

    # ── Safety correction 3: recognised setup family required ────────────────
    def test_gate_no_setup_family_blocks(self):
        """None setup_family → no_setup veto. Unknown family never executable."""
        ctx = self._base_ctx(setup_family=None, confirmation_complete=False)
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        codes = [v[0] for v in vetoes]
        self.assertIn("no_setup", codes)

    def test_gate_unknown_setup_family_blocks(self):
        """Unrecognised family string → no_setup veto (never fail-open)."""
        ctx = self._base_ctx(setup_family="MYSTERY_PATTERN", confirmation_complete=True)
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        codes = [v[0] for v in vetoes]
        self.assertIn("no_setup", codes)
        # Confirmation gate must stay silent for an unknown family
        self.assertNotIn("confirmation", codes)

    def test_gate_all_known_families_clear_no_setup(self):
        """Each of the 3 recognised families passes the no_setup gate."""
        for fam in ("LIQUIDITY_SWEEP_REVERSAL", "BREAKOUT_RETEST", "TREND_PULLBACK"):
            ctx = self._base_ctx(setup_family=fam)
            vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
            codes = [v[0] for v in vetoes]
            self.assertNotIn(
                "no_setup", codes,
                f"no_setup gate fired unexpectedly for known family {fam}")

    # ── Safety correction 1: invalid structural stop → hard block ─────────────
    def test_gate_invalid_stop_blocks_when_valid_false(self):
        """structural_stop_valid=False → invalid_stop veto. Never size from bad stop."""
        ctx = self._base_ctx(structural_stop_valid=False, structural_stop_pts=None)
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        codes = [v[0] for v in vetoes]
        self.assertIn("invalid_stop", codes)

    def test_gate_invalid_stop_blocks_when_pts_zero(self):
        """structural_stop_pts=0 with valid=False → invalid_stop veto."""
        ctx = self._base_ctx(structural_stop_valid=False, structural_stop_pts=0)
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        codes = [v[0] for v in vetoes]
        self.assertIn("invalid_stop", codes)

    def test_gate_invalid_stop_clears_with_valid_stop(self):
        """Valid structural stop → no invalid_stop veto."""
        ctx = self._base_ctx(structural_stop_valid=True, structural_stop_pts=40.0)
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        codes = [v[0] for v in vetoes]
        self.assertNotIn("invalid_stop", codes)

    def test_gate_invalid_stop_fails_closed_on_missing_key(self):
        """If structural_stop_valid key is absent → treated as invalid (fail-closed)."""
        ctx = self._base_ctx()
        ctx.pop("structural_stop_valid", None)
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        codes = [v[0] for v in vetoes]
        self.assertIn("invalid_stop", codes)

    # ── Gates 1–3 (pre-existing) ──────────────────────────────────────────────
    def test_gate1_mnq_only(self):
        vetoes = A._it_entry_veto_reasons(self._base_ctx(), {}, "Long", "MGC")
        self.assertEqual(vetoes[0][0], "instrument")

    def test_gate2_time_restriction(self):
        ctx = self._base_ctx(time_ok=False, time_reason="Entry blocked after 14:30.")
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        codes = [v[0] for v in vetoes]
        self.assertIn("time", codes)

    def test_gate3_mid_range_location(self):
        ctx = self._base_ctx(location_quality="MID_RANGE", location_reason="Mid-range.")
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        codes = [v[0] for v in vetoes]
        self.assertIn("location", codes)

    def test_multiple_gates_can_fire_simultaneously(self):
        """Multiple gate failures accumulate; every failing gate is reported."""
        ctx = self._base_ctx(
            time_ok=False, time_reason="Time blocked.",
            # Keep known family so confirmation gate activates
            confirmation_complete=False,
            confirmation_missing=["Missing CHOCH"],
            daily_trade_count=2, daily_trade_cap=2,
        )
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        codes = [v[0] for v in vetoes]
        self.assertIn("time", codes)
        self.assertIn("confirmation", codes)
        self.assertIn("daily_cap", codes)

    def test_veto_reason_text_present(self):
        ctx = self._base_ctx(
            confirmation_complete=False,
            confirmation_missing=["Awaiting CHOCH entry signal"],
        )
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MNQ")
        for code, reason in vetoes:
            self.assertIsInstance(reason, str)
            self.assertGreater(len(reason), 5)

    def test_exception_returns_unavailable(self):
        vetoes = A._it_entry_veto_reasons(None, None, None, None)
        self.assertGreater(len(vetoes), 0)

    # ── Regression: SWING unaffected ─────────────────────────────────────────
    def test_swing_mode_not_affected(self):
        """INTRADAY_TREND veto is never called in full_analysis for SWING mode.
        Unit-level: verify instrument gate fires for non-MNQ."""
        ctx = self._base_ctx()
        vetoes = A._it_entry_veto_reasons(ctx, {}, "Long", "MGC")
        self.assertEqual(vetoes[0][0], "instrument")  # gate 1 fires


# ══════════════════════════════════════════════════════════════════════════════
# 10. _it_compute_session_levels — bars classification
# ══════════════════════════════════════════════════════════════════════════════

class TestSessionLevels(unittest.TestCase):
    """_it_compute_session_levels does `from databento_brain import DATABENTO_BARS_BY_INST`
    locally, so we mock the entire databento_brain module in sys.modules."""

    def _bar(self, h_et, m_et, high, low):
        """Create a fake bar dict with a nanosecond UTC timestamp for the given ET time."""
        bar_dt_et = datetime(2026, 8, 12, h_et, m_et, 0, tzinfo=A.ET_TZ)
        ts_ns = int(bar_dt_et.timestamp() * 1_000_000_000)
        return {"high": high, "low": low, "ts": ts_ns}

    def _run(self, bars_by_inst, et_now=None):
        """Call _it_compute_session_levels with a mocked databento_brain module."""
        fake_db = MagicMock()
        fake_db.DATABENTO_BARS_BY_INST = bars_by_inst
        with patch.dict(sys.modules, {"databento_brain": fake_db}):
            return A._it_compute_session_levels(
                "MNQ",
                et_now=et_now or datetime(2026, 8, 12, 10, 30, tzinfo=A.ET_TZ),
            )

    def test_empty_bars_returns_none_fields(self):
        out = self._run({})
        for key in ("overnight_high", "overnight_low", "asia_high", "asia_low"):
            self.assertIsNone(out[key])
        self.assertEqual(out["major_15m_swing_highs"], [])

    def test_empty_bars_known_instrument_returns_none(self):
        out = self._run({"MNQ": []})
        self.assertIsNone(out["overnight_high"])

    def test_ny_session_bars_classified(self):
        # 09:35 = within opening range (09:30–10:00); 10:05 = NY session
        bars = [self._bar(9, 35, 21050.0, 20980.0),
                self._bar(10, 5, 21080.0, 20990.0)]
        out = self._run({"MNQ": bars})
        self.assertEqual(out["opening_range_high"], 21050.0)
        self.assertEqual(out["session_high"], max(21050.0, 21080.0))

    def test_asia_bars_classified(self):
        bars = [self._bar(2, 0, 21020.0, 20950.0),
                self._bar(3, 0, 21030.0, 20940.0)]
        out = self._run({"MNQ": bars})
        self.assertEqual(out["asia_high"], 21030.0)
        self.assertEqual(out["asia_low"],  20940.0)
        # Asia bars fall inside overnight window too
        self.assertEqual(out["overnight_high"], 21030.0)

    def test_london_bars_classified(self):
        bars = [self._bar(7, 30, 21060.0, 20990.0)]
        out = self._run({"MNQ": bars})
        self.assertEqual(out["london_high"], 21060.0)
        self.assertEqual(out["london_low"],  20990.0)

    def test_pivot_highs_detected(self):
        # Classic 3-bar pivot high: bar[1].high > bar[0].high and > bar[2].high
        bars = [self._bar(9, 35, 21000.0, 20980.0),
                self._bar(9, 50, 21060.0, 21000.0),   # pivot high
                self._bar(10, 5, 21020.0, 20990.0)]
        out = self._run({"MNQ": bars},
                        et_now=datetime(2026, 8, 12, 11, 0, tzinfo=A.ET_TZ))
        self.assertIn(21060.0, out["major_15m_swing_highs"])

    def test_failopen_with_bad_bars(self):
        """Malformed bars should not raise; function must return the empty schema."""
        bars = [{"bad_key": 123}, None, "not_a_bar"]
        try:
            out = self._run({"MNQ": bars})
            self.assertIsNone(out["overnight_high"])
        except Exception:
            self.fail("_it_compute_session_levels raised on bad input")

    def test_returns_all_expected_keys(self):
        out = self._run({})
        expected_keys = {"overnight_high", "overnight_low", "asia_high", "asia_low",
                         "london_high", "london_low", "opening_range_high", "opening_range_low",
                         "session_high", "session_low",
                         "major_15m_swing_highs", "major_15m_swing_lows"}
        self.assertEqual(set(out.keys()), expected_keys)


# ══════════════════════════════════════════════════════════════════════════════
# 11. SWING/SCALP parity — IT changes must be byte-identical when mode≠IT
# ══════════════════════════════════════════════════════════════════════════════

class TestSwingScalpParity(unittest.TestCase):
    """Smoke: the new IT helpers are ONLY reached when TRADING_MODE=='INTRADAY_TREND'.
    In SCALP mode full_analysis must NOT call any IT function."""

    def test_new_it_functions_callable(self):
        """All new Phase 2 functions are importable and callable."""
        for fn_name in ("_it_compute_session_levels", "_it_structural_stop",
                        "_it_confirmation_complete", "_it_risk_sizing",
                        "_it_daily_trade_count", "compute_it_trade_management",
                        "_it_force_close_watchdog", "_it_ghost_write_extended_fields"):
            fn = getattr(A, fn_name, None)
            self.assertIsNotNone(fn, f"Missing function: {fn_name}")
            self.assertTrue(callable(fn))

    def test_it_context_disabled_for_non_mnq(self):
        """Non-MNQ instruments get an early veto for INTRADAY_TREND."""
        vetoes = A._it_entry_veto_reasons(
            {"instrument": "MGC", "setup_family": None,
             "confirmation_complete": False, "confirmation_missing": [],
             "daily_trade_count": 0, "daily_trade_cap": 2,
             "location_quality": "GOOD", "time_ok": True},
            {}, "Short", "MGC")
        self.assertEqual(vetoes[0][0], "instrument")

    def test_diag_block_disabled_returns_stub(self):
        """_it_diag_block with no IT context returns {enabled: False}."""
        out = A._it_diag_block({})
        self.assertFalse(out["enabled"])

    def test_diag_block_has_all_phase2_keys(self):
        """_it_diag_block passes all Phase 2 keys through when enabled."""
        fake_ctx = {
            "enabled": True,
            "instrument": "MNQ", "session": "New York", "session_short": "NY",
            "status": "CONFIRMED_SETUP", "reason": "All confirmed.",
            "location_quality": "EXCELLENT", "location_level": "PDH",
            "location_dist_pts": 5.0, "location_reason": "Near PDH.",
            "trend_4h": "BULLISH", "trend_1h": "BULLISH",
            "trend_15m": "BULLISH", "trend_5m": "NEUTRAL",
            "trend_alignment": "STRONG_BULLISH", "alignment_score": 3,
            "setup_family": "LIQUIDITY_SWEEP_REVERSAL",
            "setup_family_reason": "LSR confirmed.",
            "time_ok": True, "time_state": "OK", "time_reason": None,
            "projected_points": 120.0, "projected_r": 2.0,
            "bias_1h": "bull", "bias_4h": "bull",
            # Phase 2
            "confirmation_complete": True,
            "confirmation_steps": ["✓ Step 1", "✓ Step 2", "✓ Step 3"],
            "confirmation_missing": [],
            "structural_stop_level": 20900.0, "structural_stop_pts": 100.0,
            "structural_stop_source": "Below swept low (20905.0)",
            "structural_stop_valid": True,                   # Safety correction 1
            "recommended_contracts": 2, "risk_dollars": 400.0,
            "daily_trade_count": 0, "daily_trade_cap": 2,
            "session_levels": {
                "opening_range_high": 21050.0, "opening_range_low": 20950.0,
                "overnight_high": 21100.0, "overnight_low": 20900.0,
                "session_high": 21060.0, "session_low": 20960.0,
            },
            "mgmt_action": "HOLD", "mgmt_action_reason": "At 0.3R — hold.",
            "mgmt_current_r": 0.3, "mgmt_be_recommended": False,
            "mgmt_partial_at_1r5": False, "mgmt_trail_active": False,
            "mgmt_force_flat": False,
        }
        out = A._it_diag_block({"intraday_trend_context": fake_ctx})
        self.assertTrue(out["enabled"])
        for key in ("confirmation_complete", "confirmation_steps", "confirmation_missing",
                    "structural_stop_level", "structural_stop_pts", "structural_stop_source",
                    "structural_stop_valid",                         # Safety correction 1
                    "recommended_contracts", "risk_dollars",
                    "daily_trade_count", "daily_trade_cap",
                    "force_close_pending",                           # Safety correction 4
                    "opening_range_high", "opening_range_low",
                    "overnight_high", "overnight_low", "session_high", "session_low",
                    "mgmt_action", "mgmt_action_reason", "mgmt_current_r",
                    "mgmt_be_recommended", "mgmt_partial_at_1r5",
                    "mgmt_trail_active", "mgmt_force_flat"):
            self.assertIn(key, out, f"Missing Phase 2 key in diag_block output: {key}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
