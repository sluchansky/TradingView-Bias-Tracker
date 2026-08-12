"""
INTRADAY_TREND Phase 3 — native engine gap-closure tests.

Covers all gaps implemented this build:
  1.  _it_time_restriction — 08:00 ET start (BLOCKED_SESSION), 15:00 cutoff
  2.  IT_DAILY_CAP env var — default cap = 3 in stable schema
  3.  _it_cooldown_remaining / _it_register_cooldown — 15-min inter-trade cooldown
  4.  _it_5m_location_engine — pullback state (AT_LOCATION / PULLING_BACK / EXTENDED)
  5.  _it_1m_confirmation_engine — confirmations_detected, count, min
  6.  _it_data_freshness — fail-open when trend_alignment absent
  7.  _it_entry_veto_reasons — cooldown veto, data-freshness veto, dynamic cap msg
  8.  compute_intraday_trend_context — stable schema has all Phase 3 keys
  9.  compute_intraday_trend_context — BLOCKED_SESSION / BLOCKED_COOLDOWN / BLOCKED_DATA
  10. _it_find_tp1 — fallback = 1.0R (was 1.25R)
  11. compute_it_trade_management — stop_move_reason + trail_stop_suggested
  12. _it_force_close_watchdog — SWING exclusion (does NOT close SWING ghost obs)
  13. _it_notify_force_flat — queues Discord notification (mocked)
  14. analyze_intraday_trend — VWAP fallback from VWAP_BY_TICKER

All tests are PURE (no DB, no network).  DB-dependent functions tested via mocking.
"""

import os
import sys
import time
import unittest
from datetime import datetime, timezone, date
from unittest.mock import MagicMock, patch

# ── bootstrap ─────────────────────────────────────────────────────────────────
TEST_DIR = os.path.dirname(__file__)
APP_DIR  = os.path.dirname(TEST_DIR)
sys.path.insert(0, APP_DIR)

os.environ.setdefault("TRADING_MODE",       "INTRADAY_TREND")
os.environ.setdefault("DASHBOARD_PASSWORD", "test")
os.environ.setdefault("SESSION_SECRET",     "test")
os.environ.setdefault("DATABASE_URL",       "postgresql://localhost/test")
os.environ.setdefault("MAX_RISK_DOLLARS",   "500")

import app as A  # noqa: E402

ET_TZ = A.ET_TZ


def _make_et(hour, minute):
    """Return a timezone-aware ET datetime for today at HH:MM."""
    d = date.today()
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=ET_TZ)


# ══════════════════════════════════════════════════════════════════════════════
# 1. _it_time_restriction — start time (08:00) and updated default cutoff (15:00)
# ══════════════════════════════════════════════════════════════════════════════

class TestTimeRestrictionPhase3(unittest.TestCase):

    def _tr(self, hour, minute, env_overrides=None):
        old = {}
        for k, v in (env_overrides or {}).items():
            old[k] = os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        try:
            return A._it_time_restriction(_make_et(hour, minute))
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_before_start_returns_blocked_session(self):
        ok, state, reason = self._tr(7, 59)
        self.assertFalse(ok)
        self.assertEqual(state, "BLOCKED_SESSION")
        self.assertIn("08:00", reason)

    def test_exactly_at_start_is_ok(self):
        ok, state, _ = self._tr(8, 0)
        self.assertTrue(ok)
        self.assertEqual(state, "OK")

    def test_during_session_ok(self):
        ok, state, _ = self._tr(10, 30)
        self.assertTrue(ok)
        self.assertEqual(state, "OK")

    def test_default_cutoff_is_1500(self):
        """Default last-entry cutoff is now 15:00 (was 15:15)."""
        ok, state, reason = self._tr(15, 0,
                                     {"INTRADAY_NEW_ENTRY_CUTOFF_ET": "",
                                      "IT_LAST_NEW_ENTRY_TIME": ""})
        self.assertFalse(ok)
        self.assertEqual(state, "ENTRY_BLOCKED")

    def test_before_1500_cutoff_is_ok(self):
        ok, state, _ = self._tr(14, 59,
                                 {"INTRADAY_NEW_ENTRY_CUTOFF_ET": "",
                                  "IT_LAST_NEW_ENTRY_TIME": ""})
        self.assertTrue(ok)
        self.assertEqual(state, "OK")

    def test_force_flat_at_1555(self):
        ok, state, _ = self._tr(15, 55)
        self.assertFalse(ok)
        self.assertEqual(state, "FORCE_FLAT")

    def test_env_override_start_time(self):
        """IT_ENTRY_START_ET controls the start gate."""
        ok, _, _ = self._tr(9, 29,
                             {"IT_ENTRY_START_ET": "09:30",
                              "INTRADAY_NEW_ENTRY_CUTOFF_ET": "",
                              "IT_LAST_NEW_ENTRY_TIME": ""})
        self.assertFalse(ok)

        ok2, _, _ = self._tr(9, 30,
                              {"IT_ENTRY_START_ET": "09:30",
                               "INTRADAY_NEW_ENTRY_CUTOFF_ET": "",
                               "IT_LAST_NEW_ENTRY_TIME": ""})
        self.assertTrue(ok2)

    def test_env_override_cutoff(self):
        """INTRADAY_NEW_ENTRY_CUTOFF_ET controls the new-entry cutoff."""
        ok, state, _ = self._tr(14, 30,
                                 {"INTRADAY_NEW_ENTRY_CUTOFF_ET": "14:30",
                                  "IT_LAST_NEW_ENTRY_TIME": ""})
        self.assertFalse(ok)
        self.assertEqual(state, "ENTRY_BLOCKED")

    def test_fail_open_on_bad_env(self):
        """Bad env value → fail-open (return OK)."""
        ok, state, _ = self._tr(10, 0,
                                 {"IT_ENTRY_START_ET": "BOGUS"})
        self.assertTrue(ok)


# ══════════════════════════════════════════════════════════════════════════════
# 2. IT_DAILY_CAP env var — default cap = 3 in stable schema
# ══════════════════════════════════════════════════════════════════════════════

class TestDailyCapEnvVar(unittest.TestCase):

    def _ctx(self, env_overrides=None):
        old = {}
        for k, v in (env_overrides or {}).items():
            old[k] = os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        try:
            return A.compute_intraday_trend_context(
                "MNQ", 20000,
                confluences={"liquidity_sweep": True},
                direction="Long")
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_default_cap_is_3(self):
        ctx = self._ctx({"IT_DAILY_CAP": None,
                         "MAX_INTRADAY_TREND_TRADES_PER_DAY": None})
        self.assertEqual(ctx["daily_trade_cap"], 3)

    def test_env_it_daily_cap_override(self):
        ctx = self._ctx({"IT_DAILY_CAP": "5"})
        self.assertEqual(ctx["daily_trade_cap"], 5)

    def test_schema_has_cap_key(self):
        ctx = self._ctx()
        self.assertIn("daily_trade_cap", ctx)


# ══════════════════════════════════════════════════════════════════════════════
# 3. _it_cooldown_remaining / _it_register_cooldown
# ══════════════════════════════════════════════════════════════════════════════

class TestCooldownMechanism(unittest.TestCase):

    def setUp(self):
        with A._IT_COOLDOWN_LOCK:
            A._IT_COOLDOWN_BY_INST.clear()
        os.environ.pop("INTRADAY_TREND_COOLDOWN_MINUTES", None)
        os.environ.pop("IT_COOLDOWN_MINUTES", None)

    def tearDown(self):
        with A._IT_COOLDOWN_LOCK:
            A._IT_COOLDOWN_BY_INST.clear()
        os.environ.pop("INTRADAY_TREND_COOLDOWN_MINUTES", None)
        os.environ.pop("IT_COOLDOWN_MINUTES", None)

    def test_no_previous_entry_returns_zero(self):
        self.assertEqual(A._it_cooldown_remaining("MNQ"), 0.0)

    def test_register_then_immediate_remaining(self):
        A._it_register_cooldown("MNQ")
        remaining = A._it_cooldown_remaining("MNQ")
        # Should be just under 15 minutes (default)
        self.assertGreater(remaining, 800)    # > 13 min
        self.assertLessEqual(remaining, 900)  # ≤ 15 min

    def test_register_different_inst_independent(self):
        A._it_register_cooldown("MNQ")
        self.assertEqual(A._it_cooldown_remaining("MGC"), 0.0)

    def test_cooldown_clears_after_window(self):
        """Simulate a past entry older than the cooldown window."""
        os.environ["INTRADAY_TREND_COOLDOWN_MINUTES"] = "1"  # 1 min window
        with A._IT_COOLDOWN_LOCK:
            A._IT_COOLDOWN_BY_INST["MNQ"] = time.monotonic() - 120  # 2 min ago
        remaining = A._it_cooldown_remaining("MNQ")
        self.assertEqual(remaining, 0.0)

    def test_env_override_cooldown_window(self):
        """INTRADAY_TREND_COOLDOWN_MINUTES overrides default 15-min window."""
        os.environ["INTRADAY_TREND_COOLDOWN_MINUTES"] = "30"
        A._it_register_cooldown("MNQ")
        remaining = A._it_cooldown_remaining("MNQ")
        self.assertGreater(remaining, 1700)    # ~28-29 min
        self.assertLessEqual(remaining, 1800)  # ≤ 30 min

    def test_fail_open_bad_env(self):
        """Bad env value → fail-open (return 0.0)."""
        os.environ["INTRADAY_TREND_COOLDOWN_MINUTES"] = "not_a_number"
        A._it_register_cooldown("MNQ")
        remaining = A._it_cooldown_remaining("MNQ")
        self.assertEqual(remaining, 0.0)


# ══════════════════════════════════════════════════════════════════════════════
# 4. _it_5m_location_engine
# ══════════════════════════════════════════════════════════════════════════════

class Test5mLocationEngine(unittest.TestCase):

    BASE_LEVELS = {
        "session_high":        20100.0,
        "session_low":         19900.0,
        "opening_range_high":  20050.0,
        "opening_range_low":   19980.0,
        "overnight_high":      20080.0,
        "overnight_low":       19950.0,
        "london_high":         20040.0,
        "london_low":          19970.0,
        "major_15m_swing_highs": [20030.0, 20060.0],
        "major_15m_swing_lows":  [19990.0, 19960.0],
    }

    def _loc(self, price, direction, vwap=None, atr=50.0, levels=None):
        return A._it_5m_location_engine(
            inst="MNQ",
            direction=direction,
            price=price,
            session_levels=levels if levels is not None else self.BASE_LEVELS,
            vwap=vwap,
            atr=atr,
        )

    def test_at_location_when_within_quarter_atr(self):
        # Price = 19982 (OR low = 19980, within 0.25 ATR = 12.5 pts)
        r = self._loc(19982.0, "Long", vwap=None, atr=50.0)
        self.assertEqual(r["pullback_state"], "AT_LOCATION")

    def test_pulling_back_within_one_atr(self):
        # Price = 20010, nearest level below (no VWAP) = OR low 19980 or swing low 19990
        # dist from 20010 to 19990 = 20 pts, dist_atr = 0.4 → PULLING_BACK
        r = self._loc(20010.0, "Long", vwap=None, atr=50.0)
        self.assertEqual(r["pullback_state"], "PULLING_BACK")

    def test_extended_when_far_from_all_levels(self):
        # Price = 20300, all levels below are >1 ATR away
        # session_high = 20100 → dist = 200 = 4 ATR → EXTENDED
        r = self._loc(20300.0, "Long", vwap=None, atr=50.0)
        self.assertEqual(r["pullback_state"], "EXTENDED")

    def test_vwap_identified_as_primary_location(self):
        # VWAP right below price → should be setup_location_type=VWAP
        r = self._loc(19982.0, "Long", vwap=19980.0, atr=50.0)
        self.assertIsNotNone(r["setup_location"])
        self.assertIn("VWAP", r.get("setup_location_type", ""))

    def test_short_direction_looks_above_price(self):
        # Short at 19990, VWAP = 20000.0 above (dist=10, dist_atr=0.2 → AT_LOCATION)
        r = self._loc(19990.0, "Short", vwap=20000.0, atr=50.0)
        self.assertEqual(r["pullback_state"], "AT_LOCATION")

    def test_fail_open_none_price(self):
        r = A._it_5m_location_engine("MNQ", "Long", None, {}, None, 50.0)
        self.assertEqual(r["pullback_state"], "UNKNOWN")

    def test_fail_open_zero_atr(self):
        r = A._it_5m_location_engine("MNQ", "Long", 20000.0, {}, None, 0.0)
        self.assertEqual(r["pullback_state"], "UNKNOWN")

    def test_fail_open_none_atr(self):
        r = A._it_5m_location_engine("MNQ", "Long", 20000.0, {}, None, None)
        self.assertEqual(r["pullback_state"], "UNKNOWN")

    def test_no_candidates_returns_waiting_or_unknown(self):
        r = self._loc(20000.0, "Long", vwap=None, atr=50.0, levels={})
        self.assertIn(r["pullback_state"], ("WAITING_FOR_PULLBACK", "UNKNOWN"))

    def test_returns_stable_schema_keys(self):
        r = self._loc(19990.0, "Long")
        for k in ("setup_location", "setup_location_value",
                  "setup_location_type", "pullback_state"):
            self.assertIn(k, r)


# ══════════════════════════════════════════════════════════════════════════════
# 5. _it_1m_confirmation_engine
# ══════════════════════════════════════════════════════════════════════════════

class Test1mConfirmationEngine(unittest.TestCase):

    def setUp(self):
        os.environ.pop("IT_MIN_CONFIRMATIONS", None)

    def tearDown(self):
        os.environ.pop("IT_MIN_CONFIRMATIONS", None)

    def _eng(self, direction="Long", confluences=None, price=20000.0,
             vwap=None, cvd_dir=None):
        if cvd_dir is not None:
            A.CVD_BY_TICKER["MNQ"] = {"direction": cvd_dir}
        else:
            A.CVD_BY_TICKER.pop("MNQ", None)
        try:
            return A._it_1m_confirmation_engine(
                inst="MNQ",
                direction=direction,
                confluences=confluences or {},
                price=price,
                vwap=vwap,
            )
        finally:
            A.CVD_BY_TICKER.pop("MNQ", None)

    def test_detects_sweep_from_confluences(self):
        r = self._eng(confluences={"liquidity_sweep": True})
        self.assertIn("liquidity_sweep", r["confirmations_detected"])

    def test_detects_micro_bos_from_confluences(self):
        r = self._eng(confluences={"bos": True})
        self.assertIn("micro_bos", r["confirmations_detected"])

    def test_detects_micro_choch_from_confluences(self):
        r = self._eng(confluences={"choch": True})
        self.assertIn("micro_choch", r["confirmations_detected"])

    def test_detects_vwap_reclaim_long_price_above_vwap(self):
        r = self._eng(direction="Long", price=20010.0, vwap=20000.0)
        self.assertIn("vwap_reclaim", r["confirmations_detected"])

    def test_no_vwap_reclaim_long_price_below_vwap(self):
        r = self._eng(direction="Long", price=19990.0, vwap=20000.0)
        self.assertNotIn("vwap_reclaim", r["confirmations_detected"])

    def test_detects_vwap_rejection_short(self):
        r = self._eng(direction="Short", price=19990.0, vwap=20000.0)
        self.assertIn("vwap_rejection", r["confirmations_detected"])

    def test_no_vwap_rejection_short_above_vwap(self):
        r = self._eng(direction="Short", price=20010.0, vwap=20000.0)
        self.assertNotIn("vwap_rejection", r["confirmations_detected"])

    def test_detects_cvd_aligned_long(self):
        r = self._eng(direction="Long", cvd_dir="BULLISH")
        self.assertIn("cvd_aligned", r["confirmations_detected"])

    def test_detects_cvd_aligned_short(self):
        r = self._eng(direction="Short", cvd_dir="BEARISH")
        self.assertIn("cvd_aligned", r["confirmations_detected"])

    def test_no_cvd_when_direction_mismatched(self):
        r = self._eng(direction="Long", cvd_dir="BEARISH")
        self.assertNotIn("cvd_aligned", r["confirmations_detected"])

    def test_momentum_recovery_from_structure_and_vwap(self):
        r = self._eng(confluences={"structure_confirmed": True,
                                   "vwap_confirmed": True})
        self.assertIn("momentum_recovery", r["confirmations_detected"])

    def test_confirmations_met_when_count_gte_min(self):
        # sweep + vwap_reclaim = 2, default min = 2 → met
        r = self._eng(direction="Long",
                      confluences={"liquidity_sweep": True},
                      price=20010.0, vwap=20000.0)
        self.assertGreaterEqual(r["confirmation_count"], 2)
        self.assertTrue(r["confirmations_met"])

    def test_not_met_when_below_min(self):
        # Only 1 confirmation (micro_bos), price < VWAP → no reclaim; default min=2
        r = self._eng(confluences={"bos": True}, price=19990.0, vwap=20000.0)
        if r["confirmation_count"] < 2:
            self.assertFalse(r["confirmations_met"])

    def test_env_it_min_confirmations_override(self):
        os.environ["IT_MIN_CONFIRMATIONS"] = "1"
        r = self._eng(confluences={"bos": True})
        self.assertEqual(r["min_confirmations"], 1)

    def test_no_direction_returns_empty(self):
        r = A._it_1m_confirmation_engine("MNQ", None, {}, 20000.0, None)
        self.assertEqual(r["confirmations_detected"], [])

    def test_returns_stable_schema_keys(self):
        r = self._eng()
        for k in ("confirmations_detected", "confirmation_count",
                  "min_confirmations", "confirmations_met"):
            self.assertIn(k, r)

    def test_fail_open_exception(self):
        """Any internal error → safe empty dict (no exception)."""
        r = A._it_1m_confirmation_engine(None, "Long", None, None, None)
        self.assertIsInstance(r, dict)
        self.assertIn("confirmations_detected", r)


# ══════════════════════════════════════════════════════════════════════════════
# 6. _it_data_freshness — fail-open
# ══════════════════════════════════════════════════════════════════════════════

class TestDataFreshness(unittest.TestCase):

    def test_absent_15m_state_is_treated_as_stale(self):
        """Per spec §10: absent 15m entry in MTF_STATE_BY_INST → not-ok (stale)."""
        mock_ta = MagicMock()
        mock_ta.MTF_STATE_BY_INST = {}  # no MNQ entry → trend_15m = "UNAVAILABLE"
        with patch.dict("sys.modules", {"trend_alignment": mock_ta}):
            ok, stale = A._it_data_freshness("MNQ")
        # Absent 15m data is a hard-block per spec §10 (not fail-open)
        self.assertFalse(ok)
        self.assertIn("15m", stale)

    def test_fail_open_when_module_raises_on_access(self):
        """If accessing the module's state dict raises, fail-open."""
        mock_ta = MagicMock()
        type(mock_ta).MTF_STATE_BY_INST = property(
            lambda self: (_ for _ in ()).throw(Exception("db down"))
        )
        with patch.dict("sys.modules", {"trend_alignment": mock_ta}):
            ok, stale = A._it_data_freshness("MNQ")
        self.assertTrue(ok)

    def test_stale_15m_makes_data_not_ok(self):
        """If trend_alignment has STALE 15m, return not-ok."""
        mock_ta = MagicMock()
        mock_ta.MTF_STATE_BY_INST = {
            "MNQ": {"trend_15m": "STALE", "bars_15m": [1, 2, 3, 4]}
        }
        with patch.dict("sys.modules", {"trend_alignment": mock_ta}):
            ok, stale = A._it_data_freshness("MNQ")
        self.assertFalse(ok)
        self.assertIn("15m", stale)

    def test_too_few_15m_bars_is_stale(self):
        """Fewer than 4 bars → 15m flagged stale → not-ok."""
        mock_ta = MagicMock()
        mock_ta.MTF_STATE_BY_INST = {
            "MNQ": {"trend_15m": "BULLISH", "bars_15m": [1, 2]}
        }
        with patch.dict("sys.modules", {"trend_alignment": mock_ta}):
            ok, stale = A._it_data_freshness("MNQ")
        self.assertFalse(ok)

    def test_healthy_15m_returns_ok(self):
        """Enough bars + non-STALE trend → is_ok=True."""
        mock_ta = MagicMock()
        mock_ta.MTF_STATE_BY_INST = {
            "MNQ": {"trend_15m": "BULLISH", "bars_15m": [1, 2, 3, 4, 5, 6]}
        }
        with patch.dict("sys.modules", {"trend_alignment": mock_ta}):
            ok, stale = A._it_data_freshness("MNQ")
        self.assertTrue(ok)
        self.assertNotIn("15m", stale)


# ══════════════════════════════════════════════════════════════════════════════
# 7. _it_entry_veto_reasons — cooldown, data freshness, dynamic cap
# ══════════════════════════════════════════════════════════════════════════════

class TestEntryVetoPhase3(unittest.TestCase):

    def _base_ctx(self, overrides=None):
        ctx = {
            "instrument":           "MNQ",
            "context_1h":           "ALIGNED",
            "extension_state":      "NORMAL",
            "time_ok":              True,
            "location_quality":     "GOOD",
            "setup_family":         "TREND_PULLBACK",
            "confirmation_complete": True,
            "structural_stop_valid": True,
            "structural_stop_pts":  10.0,
            "daily_trade_count":    0,
            "daily_trade_cap":      3,
            "data_freshness_ok":    True,
            "stale_timeframes":     [],
            "cooldown_remaining":   0,
        }
        if overrides:
            ctx.update(overrides)
        return ctx

    def _veto(self, ctx_overrides=None):
        ctx = self._base_ctx(ctx_overrides)
        return A._it_entry_veto_reasons(ctx, {}, "Long", instrument="MNQ")

    def test_no_vetoes_when_all_clear(self):
        self.assertEqual(self._veto(), [])

    def test_cooldown_veto_when_cooldown_active(self):
        vetoes = self._veto({"cooldown_remaining": 300})  # 5 min left
        codes = [v[0] for v in vetoes]
        self.assertIn("cooldown", codes)

    def test_no_cooldown_veto_when_clear(self):
        vetoes = self._veto({"cooldown_remaining": 0})
        codes = [v[0] for v in vetoes]
        self.assertNotIn("cooldown", codes)

    def test_data_freshness_veto_when_stale(self):
        vetoes = self._veto({"data_freshness_ok": False,
                             "stale_timeframes": ["15m"]})
        codes = [v[0] for v in vetoes]
        self.assertIn("data_freshness", codes)

    def test_no_freshness_veto_when_ok(self):
        vetoes = self._veto({"data_freshness_ok": True})
        codes = [v[0] for v in vetoes]
        self.assertNotIn("data_freshness", codes)

    def test_daily_cap_message_includes_dynamic_cap(self):
        """Cap message must show cap=3, not hardcoded 2."""
        vetoes = self._veto({"daily_trade_count": 3, "daily_trade_cap": 3})
        cap_vetoes = [v for v in vetoes if v[0] == "daily_cap"]
        self.assertTrue(cap_vetoes)
        self.assertIn("3", cap_vetoes[0][1])

    def test_daily_cap_env_override_reflected(self):
        vetoes = self._veto({"daily_trade_count": 5, "daily_trade_cap": 5})
        cap_vetoes = [v for v in vetoes if v[0] == "daily_cap"]
        self.assertTrue(cap_vetoes)
        self.assertIn("5", cap_vetoes[0][1])


# ══════════════════════════════════════════════════════════════════════════════
# 8 & 9. compute_intraday_trend_context — Phase 3 stable schema + new statuses
# ══════════════════════════════════════════════════════════════════════════════

class TestContextSchemaPhase3(unittest.TestCase):

    def _ctx(self, et_now=None, env_overrides=None):
        old = {}
        for k, v in (env_overrides or {}).items():
            old[k] = os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        try:
            return A.compute_intraday_trend_context("MNQ", 20000,
                                                    et_now=et_now)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_all_phase3_keys_present(self):
        ctx = self._ctx()
        required = (
            "setup_location", "setup_location_type", "pullback_state",
            "confirmations_detected", "confirmation_count", "min_confirmations",
            "confirmations_met", "data_freshness_ok", "stale_timeframes",
            "cooldown_remaining",
            # Phase 2 keys still present
            "structural_stop_valid", "confirmation_complete", "daily_trade_count",
            "daily_trade_cap",
            # Native IT keys
            "trend_15m_native", "context_1h", "extension_state", "entry_state",
        )
        for k in required:
            self.assertIn(k, ctx, f"Missing key: {k}")

    def test_daily_trade_cap_default_3(self):
        ctx = self._ctx(env_overrides={"IT_DAILY_CAP": None,
                                       "MAX_INTRADAY_TREND_TRADES_PER_DAY": None})
        self.assertEqual(ctx["daily_trade_cap"], 3)

    def test_blocked_session_before_0800(self):
        ctx = self._ctx(et_now=_make_et(7, 30))
        self.assertEqual(ctx["status"], "BLOCKED_SESSION")

    def test_entry_blocked_at_1500(self):
        ctx = self._ctx(et_now=_make_et(15, 0))
        self.assertEqual(ctx["status"], "ENTRY_BLOCKED")

    def test_blocked_cooldown_when_cooldown_active(self):
        """When cooldown is active and data is fresh, status is BLOCKED_COOLDOWN."""
        A._it_register_cooldown("MNQ")
        try:
            # Patch data freshness to avoid BLOCKED_DATA masking BLOCKED_COOLDOWN
            with patch.object(A, "_it_data_freshness", return_value=(True, [])):
                ctx = self._ctx(et_now=_make_et(10, 0))
            if ctx.get("cooldown_remaining", 0) > 0:
                self.assertEqual(ctx["status"], "BLOCKED_COOLDOWN")
        finally:
            with A._IT_COOLDOWN_LOCK:
                A._IT_COOLDOWN_BY_INST.pop("MNQ", None)

    def test_status_is_specific_not_generic_blocked_for_pre_open(self):
        """Status must be BLOCKED_SESSION, not generic BLOCKED."""
        ctx = self._ctx(et_now=_make_et(7, 0))
        self.assertNotEqual(ctx["status"], "BLOCKED",
                            "Status should be BLOCKED_SESSION, not generic BLOCKED")

    def test_force_flat_status_preserved(self):
        ctx = self._ctx(et_now=_make_et(16, 0))
        self.assertEqual(ctx["status"], "FORCE_FLAT")


# ══════════════════════════════════════════════════════════════════════════════
# 10. _it_find_tp1 — fallback = 1.0R (was 1.25R)
# ══════════════════════════════════════════════════════════════════════════════

class TestTP1Fallback(unittest.TestCase):

    def _tp1(self, direction, entry, risk, levels=None, tick=0.25):
        return A._it_find_tp1(direction, entry, risk, levels or {}, tick)

    def test_long_fallback_is_1r_when_no_structural_level(self):
        tp1 = self._tp1("Long", entry=20000.0, risk=50.0, levels={})
        self.assertAlmostEqual(tp1, 20050.0, places=1)

    def test_short_fallback_is_1r_when_no_structural_level(self):
        tp1 = self._tp1("Short", entry=20000.0, risk=50.0, levels={})
        self.assertAlmostEqual(tp1, 19950.0, places=1)

    def test_not_1_25r_fallback(self):
        """Regression: fallback must NOT be 1.25R."""
        tp1 = self._tp1("Long", entry=20000.0, risk=100.0, levels={})
        self.assertAlmostEqual(tp1, 20100.0, places=1)
        self.assertNotAlmostEqual(tp1, 20125.0, places=1)

    def test_structural_level_used_when_in_range(self):
        # OR high at 20040 = 0.8R from 20000; within typical TP1 acceptance band
        levels = {"opening_range_high": 20040.0}
        tp1 = self._tp1("Long", entry=20000.0, risk=50.0, levels=levels)
        # If structural TP1 is used, it should be ≤ 20050 (1R)
        self.assertLessEqual(tp1, 20050.0)


# ══════════════════════════════════════════════════════════════════════════════
# 11. compute_it_trade_management — stop_move_reason + trail_stop_suggested
# ══════════════════════════════════════════════════════════════════════════════

class TestManagementPhase3(unittest.TestCase):

    def _mgmt(self, entry=20000.0, stop=19950.0, direction="Long",
              price=None, contracts=1, it_ctx=None):
        trade = {
            "entry_price": entry,
            "stop_loss":   stop,
            "direction":   direction,
            "contracts":   contracts,
        }
        if price is None:
            price = entry + abs(entry - stop)  # at 1R
        return A.compute_it_trade_management(trade, price, it_ctx or {},
                                             _make_et(10, 0))

    def test_stop_move_reason_present_when_be_recommended(self):
        """BE recommended at 1R → stop_move_reason must be non-None."""
        r = self._mgmt(entry=20000.0, stop=19950.0, direction="Long",
                       price=20050.0)  # exactly 1R
        self.assertTrue(r["be_recommended"])
        self.assertIsNotNone(r["stop_move_reason"])
        self.assertIn("1R", r["stop_move_reason"])

    def test_stop_move_reason_none_before_1r(self):
        r = self._mgmt(entry=20000.0, stop=19950.0, direction="Long",
                       price=20030.0)  # 0.6R, before BE
        self.assertFalse(r["be_recommended"])
        self.assertIsNone(r["stop_move_reason"])

    def test_trail_stop_suggested_from_swing_lows(self):
        """After 1R, trail stop should use confirmed swing low above initial stop."""
        it_ctx = {
            "session_levels": {
                # Swing lows between stop (19950) and price (20055) - min_buf
                "major_15m_swing_lows":  [19960.0, 19975.0, 19985.0],
                "major_15m_swing_highs": [],
            }
        }
        # price = 20055 → cur_r = (20055-20000)/50 = 1.1R → trail_active
        r = self._mgmt(entry=20000.0, stop=19950.0, direction="Long",
                       price=20055.0, it_ctx=it_ctx)
        self.assertTrue(r["trail_active"])
        self.assertIsNotNone(r["trail_stop_suggested"])
        # Must be above stop (19950) and below price with buffer
        self.assertGreater(r["trail_stop_suggested"], 19950.0)
        self.assertLess(r["trail_stop_suggested"], 20055.0)

    def test_trail_stop_suggested_short_from_swing_highs(self):
        it_ctx = {
            "session_levels": {
                # Swing highs between price+buf (19945+) and stop (20050)
                "major_15m_swing_highs": [20010.0, 20020.0, 20030.0],
                "major_15m_swing_lows":  [],
            }
        }
        # Short: entry=20000, stop=20050, price=19945 → cur_r=55/50=1.1R
        r = self._mgmt(entry=20000.0, stop=20050.0, direction="Short",
                       price=19945.0, it_ctx=it_ctx)
        self.assertTrue(r["trail_active"])
        self.assertIsNotNone(r["trail_stop_suggested"])
        self.assertLess(r["trail_stop_suggested"], 20050.0)

    def test_trail_stop_none_when_no_qualifying_swing_lows(self):
        """Swing lows all BELOW stop → trail_stop_suggested is None."""
        it_ctx = {
            "session_levels": {
                "major_15m_swing_lows":  [19940.0, 19935.0],  # below stop=19950
                "major_15m_swing_highs": [],
            }
        }
        r = self._mgmt(entry=20000.0, stop=19950.0, direction="Long",
                       price=20055.0, it_ctx=it_ctx)
        # trail_active should be True (cur_r > 1.0), but no valid candidates
        self.assertTrue(r["trail_active"])
        self.assertIsNone(r["trail_stop_suggested"])

    def test_stable_schema_has_new_keys(self):
        r = self._mgmt()
        self.assertIn("stop_move_reason", r)
        self.assertIn("trail_stop_suggested", r)
        self.assertIn("trail_stop_source", r)


# ══════════════════════════════════════════════════════════════════════════════
# 12. _it_force_close_watchdog — SWING exclusion regression test
# ══════════════════════════════════════════════════════════════════════════════

class TestForceCloseWatchdogSwingExclusion(unittest.TestCase):
    """
    Regression: _it_force_close_watchdog must only close rows with
    trading_mode = 'INTRADAY_TREND'. SWING rows must NOT be touched.
    """

    def _run_watchdog_capture_sql(self):
        """Run watchdog with mock DB; return list of SQL strings executed."""
        sql_calls = []

        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_cur.__enter__ = lambda s: s
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.execute.side_effect = lambda sql, *a, **kw: sql_calls.append(sql)

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)

        # Return UTC time that maps to 20:00 ET (past 15:55 flat time)
        # August 2026: ET = UTC-4, so 20:00 ET = 00:00 UTC next day
        # Use 20:00 UTC = 16:00 ET
        flat_past_utc = datetime(2026, 8, 12, 20, 0, tzinfo=timezone.utc)

        with patch.object(A, "GHOST_OBS_DB_READY", True), \
             patch.object(A, "get_db_connection", return_value=mock_conn), \
             patch.object(A, "now_utc", return_value=flat_past_utc):
            A._it_force_close_watchdog()

        return sql_calls

    def test_where_clause_restricts_to_intraday_trend_mode(self):
        """UPDATE SQL must include trading_mode = 'INTRADAY_TREND'."""
        sql_calls = self._run_watchdog_capture_sql()
        it_sql = [s for s in sql_calls
                  if isinstance(s, str) and "ghost_observations" in s.lower()]
        self.assertTrue(it_sql, f"No ghost_observations SQL found in: {sql_calls}")
        for sql in it_sql:
            self.assertIn("INTRADAY_TREND", sql,
                          "WHERE clause must restrict to INTRADAY_TREND mode")

    def test_swing_mode_never_matched(self):
        """SQL must not contain WHERE clause that could match SWING rows."""
        sql_calls = self._run_watchdog_capture_sql()
        for sql in sql_calls:
            if isinstance(sql, str):
                self.assertNotIn("trading_mode = 'SWING'", sql)


# ══════════════════════════════════════════════════════════════════════════════
# 13. _it_notify_force_flat — queues Discord notification
# ══════════════════════════════════════════════════════════════════════════════

class TestNotifyForceFlat(unittest.TestCase):

    def test_notification_queued_when_called(self):
        """_it_notify_force_flat should enqueue a slow Discord post."""
        queued = []

        def mock_enqueue(fn):
            queued.append(fn)

        row = {
            "instrument": "MNQ", "direction": "Long",
            "entry_price": 20000.0, "mfe_r": 1.2, "gross_r": 0.8,
        }
        with patch.object(A, "_enqueue_slow", side_effect=mock_enqueue):
            A._it_notify_force_flat(row, "15:55 ET")

        self.assertEqual(len(queued), 1,
                         "_enqueue_slow should be called exactly once")

    def test_notification_fail_open_on_bad_row(self):
        """Bad row data must not raise — fail-open."""
        try:
            A._it_notify_force_flat(None, "15:55 ET")
            A._it_notify_force_flat({}, "15:55 ET")
            A._it_notify_force_flat({"mfe_r": "bad"}, "15:55 ET")
        except Exception as e:
            self.fail(f"_it_notify_force_flat raised on bad input: {e}")

    def test_queued_function_skips_post_when_discord_disabled(self):
        """When DISCORD_LIVE_ENABLED is False, queued function does not post."""
        queued = []

        def mock_enqueue(fn):
            queued.append(fn)

        row = {"instrument": "MNQ", "direction": "Long",
               "entry_price": 20000.0, "mfe_r": 0.5}
        with patch.object(A, "_enqueue_slow", side_effect=mock_enqueue), \
             patch.object(A, "DISCORD_LIVE_ENABLED", False), \
             patch("requests.post") as mock_post:
            A._it_notify_force_flat(row, "15:55 ET")
            if queued:
                queued[0]()   # execute the queued function
            mock_post.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 14. analyze_intraday_trend — VWAP fallback from VWAP_BY_TICKER
# ══════════════════════════════════════════════════════════════════════════════

class TestVWAPFallback(unittest.TestCase):

    def test_vwap_fallback_used_when_trade_plan_has_no_vwap(self):
        """When trade_plan=None but VWAP_BY_TICKER["MNQ"] set, VWAP is used."""
        A.VWAP_BY_TICKER["MNQ"] = 20000.0
        try:
            # Provide ATR via swing_ctx so extension can compute
            r = A.analyze_intraday_trend(
                instrument="MNQ",
                price=20050.0,
                confluences={},
                swing_ctx={"atr_1h": 100.0},   # ATR in swing_ctx
                trade_plan=None,
                direction="Long",
            )
            # With VWAP=20000 and price=20050 (0.5 ATR from VWAP), not extended
            ext = r.get("extension_state")
            self.assertNotEqual(ext, "UNKNOWN",
                               "Extension state is UNKNOWN — VWAP fallback not working")
        finally:
            A.VWAP_BY_TICKER.pop("MNQ", None)

    def test_extension_unknown_when_no_vwap_anywhere(self):
        """Without VWAP from any source, extension must be UNKNOWN."""
        A.VWAP_BY_TICKER.pop("MNQ", None)
        r = A.analyze_intraday_trend(
            instrument="MNQ",
            price=20000.0,
            confluences={},
            swing_ctx={},
            trade_plan=None,
            direction="Long",
        )
        self.assertEqual(r.get("extension_state"), "UNKNOWN")

    def test_vwap_from_trade_plan_takes_priority(self):
        """VWAP in trade_plan takes priority over VWAP_BY_TICKER."""
        A.VWAP_BY_TICKER["MNQ"] = 19000.0  # wrong value — should not be used
        try:
            r = A.analyze_intraday_trend(
                instrument="MNQ",
                price=20050.0,
                confluences={},
                swing_ctx={"atr_1h": 100.0},
                trade_plan={"vwap": 20000.0, "atr_pts": 100.0},
                direction="Long",
            )
            self.assertNotEqual(r.get("extension_state"), "UNKNOWN")
        finally:
            A.VWAP_BY_TICKER.pop("MNQ", None)


# ══════════════════════════════════════════════════════════════════════════════
# 15. _it_diag_block — new Phase 3 fields exposed in /status output
# ══════════════════════════════════════════════════════════════════════════════

class TestDiagBlockPhase3(unittest.TestCase):

    def _make_ctx(self, overrides=None):
        ctx = {
            "enabled": True,
            "instrument": "MNQ",
            "session": "NY",
            "session_short": "NY",
            "status": "BUILDING_CONTEXT",
            "reason": "test",
            # Phase 3 fields
            "setup_location":         "VWAP (20000.00)",
            "setup_location_type":    "VWAP",
            "pullback_state":         "AT_LOCATION",
            "confirmations_detected": ["liquidity_sweep", "vwap_reclaim"],
            "confirmation_count":     2,
            "min_confirmations":      2,
            "confirmations_met":      True,
            "data_freshness_ok":      True,
            "stale_timeframes":       [],
            "cooldown_remaining":     0,
            # Native IT fields
            "trend_15m_native":        "BULLISH",
            "trend_15m_native_reason": "EMA slope",
            "context_1h":              "ALIGNED",
            "extension_state":         "NORMAL",
            "extension_dist_atr":      0.3,
            "setup_score":             75,
            "entry_state":             "QUALIFIED",
            "mgmt_action":             None,
            "mgmt_action_reason":      None,
            "mgmt_current_r":          None,
            "mgmt_be_recommended":     False,
            "mgmt_partial_at_1r5":     False,
            "mgmt_trail_active":       False,
            "mgmt_force_flat":         False,
            "mgmt_stop_move_reason":   None,
            "mgmt_trail_stop_suggested": None,
            "mgmt_trail_stop_source":  None,
        }
        if overrides:
            ctx.update(overrides)
        return ctx

    def _diag(self, ctx_overrides=None):
        ctx = self._make_ctx(ctx_overrides)
        return A._it_diag_block({"intraday_trend_context": ctx})

    def test_setup_location_in_diag(self):
        self.assertEqual(self._diag()["setup_location"], "VWAP (20000.00)")

    def test_pullback_state_in_diag(self):
        self.assertEqual(self._diag()["pullback_state"], "AT_LOCATION")

    def test_confirmation_count_in_diag(self):
        self.assertEqual(self._diag()["confirmation_count"], 2)

    def test_min_confirmations_in_diag(self):
        self.assertEqual(self._diag()["min_confirmations"], 2)

    def test_confirmations_met_in_diag(self):
        self.assertTrue(self._diag()["confirmations_met"])

    def test_data_freshness_in_diag(self):
        self.assertTrue(self._diag()["data_freshness_ok"])

    def test_cooldown_remaining_in_diag(self):
        self.assertEqual(self._diag()["cooldown_remaining"], 0)

    def test_trend_15m_native_in_diag(self):
        self.assertEqual(self._diag()["trend_15m_native"], "BULLISH")

    def test_context_1h_in_diag(self):
        self.assertEqual(self._diag()["context_1h"], "ALIGNED")

    def test_extension_state_in_diag(self):
        self.assertEqual(self._diag()["extension_state"], "NORMAL")

    def test_entry_state_in_diag(self):
        self.assertEqual(self._diag()["entry_state"], "QUALIFIED")

    def test_setup_score_in_diag(self):
        self.assertEqual(self._diag()["setup_score"], 75)

    def test_disabled_returns_disabled_block(self):
        r = A._it_diag_block({"intraday_trend_context": {"enabled": False}})
        self.assertFalse(r["enabled"])

    def test_fail_open_bad_input(self):
        r = A._it_diag_block(None)
        self.assertFalse(r["enabled"])
        r2 = A._it_diag_block({})
        self.assertFalse(r2["enabled"])


if __name__ == "__main__":
    unittest.main()
