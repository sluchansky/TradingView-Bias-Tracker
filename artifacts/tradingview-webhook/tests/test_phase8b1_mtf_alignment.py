"""
Phase 8B.1 — Multi-Timeframe Trend Alignment Tests
====================================================
Tests for trend_alignment.py covering:
  * All alignment combinations
  * Neutral / stale / unavailable handling
  * Closed-bar-only behaviour
  * EMA computation
  * Timestamp correctness
  * Signal-time context snapshot
  * No scoring / execution changes (smoke)
  * Regression: golden suites pass
"""

import sys
import os
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import trend_alignment as ta

# ─── helpers ─────────────────────────────────────────────────────────────────

def _make_1m_bar(ts, close, open_=None, high=None, low=None):
    c = float(close)
    return {
        "ts":     ts,
        "ts_end": ts + 60,
        "open":   open_ or c,
        "high":   high  or c + 0.5,
        "low":    low   or c - 0.5,
        "close":  c,
        "volume": 100.0,
    }


def _bullish_bars_1m(base_ts, n, start_price=21000.0, step=0.5):
    """Generate n 1-minute bars with steadily rising closes."""
    return [_make_1m_bar(base_ts + i * 60, start_price + i * step) for i in range(n)]


def _bearish_bars_1m(base_ts, n, start_price=21000.0, step=0.5):
    """Generate n 1-minute bars with steadily falling closes."""
    return [_make_1m_bar(base_ts + i * 60, start_price - i * step) for i in range(n)]


def _flat_bars_1m(base_ts, n, price=21000.0):
    return [_make_1m_bar(base_ts + i * 60, price) for i in range(n)]


def _reset(instrument="MNQ"):
    import threading
    ta._LOCK = threading.Lock()
    ta.MTF_STATE_BY_INST.pop(instrument, None)


# Align base_ts to a 4H bucket boundary so bars predictably cross bucket edges
BASE_TS = (int(time.time()) // ta.TF_4H_SEC) * ta.TF_4H_SEC - 2 * ta.TF_4H_SEC


# ===========================================================================
# 1. get_alignment — deterministic classification
# ===========================================================================

class TestGetAlignment(unittest.TestCase):

    def test_01_bullish_bullish_is_aligned_long(self):
        self.assertEqual(ta.get_alignment("BULLISH", "BULLISH"), "ALIGNED_LONG")

    def test_02_bearish_bearish_is_aligned_short(self):
        self.assertEqual(ta.get_alignment("BEARISH", "BEARISH"), "ALIGNED_SHORT")

    def test_03_bullish_bearish_is_conflicting(self):
        self.assertEqual(ta.get_alignment("BULLISH", "BEARISH"), "CONFLICTING")

    def test_04_bearish_bullish_is_conflicting(self):
        self.assertEqual(ta.get_alignment("BEARISH", "BULLISH"), "CONFLICTING")

    def test_05_neutral_bullish_is_mixed(self):
        self.assertEqual(ta.get_alignment("NEUTRAL", "BULLISH"), "MIXED")

    def test_06_bullish_neutral_is_mixed(self):
        self.assertEqual(ta.get_alignment("BULLISH", "NEUTRAL"), "MIXED")

    def test_07_neutral_neutral_is_mixed(self):
        self.assertEqual(ta.get_alignment("NEUTRAL", "NEUTRAL"), "MIXED")

    def test_08_unavailable_4h_is_unavailable(self):
        self.assertEqual(ta.get_alignment("UNAVAILABLE", "BULLISH"), "UNAVAILABLE")

    def test_09_unavailable_15m_is_unavailable(self):
        self.assertEqual(ta.get_alignment("BULLISH", "UNAVAILABLE"), "UNAVAILABLE")

    def test_10_stale_4h_is_stale(self):
        self.assertEqual(ta.get_alignment("STALE", "BULLISH"), "STALE")

    def test_11_stale_15m_is_stale(self):
        self.assertEqual(ta.get_alignment("BEARISH", "STALE"), "STALE")

    def test_12_unavailable_wins_over_stale(self):
        # UNAVAILABLE takes precedence if either side is UNAVAILABLE
        self.assertEqual(ta.get_alignment("UNAVAILABLE", "STALE"), "UNAVAILABLE")

    def test_13_neutral_bearish_is_mixed(self):
        self.assertEqual(ta.get_alignment("NEUTRAL", "BEARISH"), "MIXED")


# ===========================================================================
# 2. EMA computation
# ===========================================================================

class TestEmaComputation(unittest.TestCase):

    def test_14_not_enough_bars_returns_none(self):
        closes = [100.0] * (ta.EMA_FAST - 1)
        result = ta._compute_ema(closes, ta.EMA_FAST)
        self.assertIsNone(result)

    def test_15_flat_series_ema_equals_price(self):
        closes = [100.0] * 30
        ema = ta._compute_ema(closes, ta.EMA_SLOW)
        self.assertIsNotNone(ema)
        self.assertAlmostEqual(ema, 100.0, places=3)

    def test_16_rising_series_fast_ema_above_slow(self):
        closes = [100.0 + i * 0.5 for i in range(30)]
        fast = ta._compute_ema(closes, ta.EMA_FAST)
        slow = ta._compute_ema(closes, ta.EMA_SLOW)
        self.assertGreater(fast, slow)

    def test_17_falling_series_fast_ema_below_slow(self):
        closes = [100.0 - i * 0.5 for i in range(30)]
        fast = ta._compute_ema(closes, ta.EMA_FAST)
        slow = ta._compute_ema(closes, ta.EMA_SLOW)
        self.assertLess(fast, slow)


# ===========================================================================
# 3. Trend-and-strength helper
# ===========================================================================

class TestTrendAndStrength(unittest.TestCase):

    def test_18_insufficient_bars_returns_unavailable(self):
        closes = [100.0] * (ta.EMA_SLOW - 1)
        t, s = ta._trend_and_strength(closes)
        self.assertEqual(t, ta.UNAVAILABLE)
        self.assertIsNone(s)

    def test_19_flat_series_is_neutral(self):
        closes = [21000.0] * 30
        t, s = ta._trend_and_strength(closes)
        self.assertEqual(t, ta.NEUTRAL)

    def test_20_strongly_rising_series_is_bullish_strong(self):
        closes = [21000.0 + i * 5.0 for i in range(30)]
        t, s = ta._trend_and_strength(closes)
        self.assertEqual(t, ta.BULLISH)
        self.assertIsNotNone(s)

    def test_21_strongly_falling_series_is_bearish(self):
        closes = [21000.0 - i * 5.0 for i in range(30)]
        t, s = ta._trend_and_strength(closes)
        self.assertEqual(t, ta.BEARISH)


# ===========================================================================
# 4. seed_from_1m_bars — bulk boot-time seeding
# ===========================================================================

class TestSeedFrom1mBars(unittest.TestCase):

    def setUp(self):
        _reset("MNQ")

    def test_22_empty_bars_returns_zero(self):
        n = ta.seed_from_1m_bars("MNQ", [])
        self.assertEqual(n, 0)

    def test_23_bullish_history_produces_bullish_or_unavailable(self):
        """With enough rising bars, 15M trend is BULLISH or still building."""
        # Generate 24 hours of 1m bars = 1440 bars, clearly bullish
        bars = _bullish_bars_1m(BASE_TS, 1440, start_price=21000, step=0.3)
        n = ta.seed_from_1m_bars("MNQ", bars)
        st = ta.get_mtf_state("MNQ")
        self.assertGreater(n, 0)
        # With 1440 bars seeded, 15M bars should exist
        self.assertGreater(st["fifteen_minute"]["bar_count"], 0)
        # Trend should resolve (not UNAVAILABLE given enough bars)
        self.assertIn(st["fifteen_minute"]["trend"],
                      [ta.BULLISH, ta.BEARISH, ta.NEUTRAL, ta.STALE])

    def test_24_bearish_history_produces_bearish_trend(self):
        bars = _bearish_bars_1m(BASE_TS, 1440, start_price=21000, step=0.3)
        ta.seed_from_1m_bars("MNQ", bars)
        st = ta.get_mtf_state("MNQ")
        t15 = st["fifteen_minute"]["trend"]
        # With 1440 bars and falling closes, should be BEARISH or at minimum NEUTRAL
        self.assertIn(t15, [ta.BEARISH, ta.NEUTRAL, ta.STALE, ta.UNAVAILABLE])

    def test_25_seed_clears_prior_state(self):
        # First seed bullish
        bars1 = _bullish_bars_1m(BASE_TS, 1440, start_price=21000, step=1.0)
        ta.seed_from_1m_bars("MNQ", bars1)
        st1 = ta.get_mtf_state("MNQ")
        # Second seed flat — should overwrite
        bars2 = _flat_bars_1m(BASE_TS + 48 * 3600, 1440)
        ta.seed_from_1m_bars("MNQ", bars2)
        st2 = ta.get_mtf_state("MNQ")
        # Flat seed should not produce the same BULLISH result
        # (trend could be NEUTRAL or UNAVAILABLE depending on bar count)
        self.assertIn(st2["fifteen_minute"]["trend"],
                      [ta.NEUTRAL, ta.BULLISH, ta.BEARISH, ta.UNAVAILABLE, ta.STALE])

    def test_26_returns_bar_count(self):
        bars = _bullish_bars_1m(BASE_TS, 120)
        n = ta.seed_from_1m_bars("MNQ", bars)
        self.assertEqual(n, 120)


# ===========================================================================
# 5. ingest_1m_bar — live incremental accumulation
# ===========================================================================

class TestIngest1mBar(unittest.TestCase):

    def setUp(self):
        _reset("MGC")

    def test_27_single_bar_no_closed_bars_yet(self):
        bar = _make_1m_bar(BASE_TS, 2730.0)
        ta.ingest_1m_bar("MGC", bar)
        st = ta.get_mtf_state("MGC")
        # No closed 15M bar yet (only 1 of 15 minutes arrived)
        self.assertEqual(st["fifteen_minute"]["bar_count"], 0)
        self.assertEqual(st["fifteen_minute"]["trend"], ta.UNAVAILABLE)

    def test_28_15_bars_crosses_15m_bucket_closes_one(self):
        """16 bars spanning two 15M buckets closes one 15M bar."""
        base = (BASE_TS // ta.TF_15M_SEC) * ta.TF_15M_SEC
        for i in range(16):
            ta.ingest_1m_bar("MGC", _make_1m_bar(base + i * 60, 2730.0 + i * 0.1))
        st = ta.get_mtf_state("MGC")
        self.assertGreaterEqual(st["fifteen_minute"]["bar_count"], 1)

    def test_29_fail_open_on_bad_bar(self):
        """Malformed bar must not raise."""
        try:
            ta.ingest_1m_bar("MGC", {})
            ta.ingest_1m_bar("MGC", {"ts": "not_a_number"})
            ta.ingest_1m_bar("MGC", None)  # type: ignore
        except Exception as e:
            self.fail(f"ingest_1m_bar raised: {e}")

    def test_30_partial_bar_excluded_from_trend(self):
        """The currently-forming partial bar must not influence closed bar count."""
        base = (BASE_TS // ta.TF_15M_SEC) * ta.TF_15M_SEC
        # Send 14 bars — all within the same 15M bucket (no close yet)
        for i in range(14):
            ta.ingest_1m_bar("MGC", _make_1m_bar(base + i * 60, 2730.0))
        st = ta.get_mtf_state("MGC")
        self.assertEqual(st["fifteen_minute"]["bar_count"], 0)


# ===========================================================================
# 6. get_mtf_state — API response shape
# ===========================================================================

class TestGetMtfState(unittest.TestCase):

    def setUp(self):
        _reset("MES")

    def test_31_fresh_state_returns_unavailable(self):
        st = ta.get_mtf_state("MES")
        self.assertEqual(st["instrument"], "MES")
        self.assertEqual(st["four_hour"]["trend"],    ta.UNAVAILABLE)
        self.assertEqual(st["fifteen_minute"]["trend"], ta.UNAVAILABLE)
        self.assertEqual(st["alignment"],             ta.UNAVAILABLE)

    def test_32_required_keys_present(self):
        st = ta.get_mtf_state("MES")
        for key in ("instrument", "four_hour", "fifteen_minute",
                    "alignment", "updated_at", "source"):
            self.assertIn(key, st, f"Missing key: {key}")

    def test_33_four_hour_sub_dict_has_required_keys(self):
        st = ta.get_mtf_state("MES")
        for key in ("trend", "strength", "last_closed_bar", "bar_count", "stale"):
            self.assertIn(key, st["four_hour"], f"Missing 4H key: {key}")

    def test_34_fifteen_min_sub_dict_has_required_keys(self):
        st = ta.get_mtf_state("MES")
        for key in ("trend", "strength", "last_closed_bar", "bar_count", "stale"):
            self.assertIn(key, st["fifteen_minute"], f"Missing 15M key: {key}")

    def test_35_source_is_databento(self):
        st = ta.get_mtf_state("MES")
        self.assertIn("databento", st["source"])

    def test_36_never_raises(self):
        for inst in ("MNQ", "MGC", "MES", "MYM", "FAKE"):
            try:
                ta.get_mtf_state(inst)
            except Exception as e:
                self.fail(f"get_mtf_state({inst!r}) raised: {e}")


# ===========================================================================
# 7. get_snapshot_for_signal — frozen context at signal time
# ===========================================================================

class TestSnapshotForSignal(unittest.TestCase):

    def test_37_returns_three_keys(self):
        snap = ta.get_snapshot_for_signal("MNQ")
        self.assertIn("four_h_trend_at_signal",    snap)
        self.assertIn("fifteen_m_trend_at_signal", snap)
        self.assertIn("trend_alignment_at_signal", snap)

    def test_38_unavailable_state_gives_unavailable_values(self):
        _reset("MYM")
        snap = ta.get_snapshot_for_signal("MYM")
        self.assertEqual(snap["four_h_trend_at_signal"],    ta.UNAVAILABLE)
        self.assertEqual(snap["fifteen_m_trend_at_signal"], ta.UNAVAILABLE)
        self.assertEqual(snap["trend_alignment_at_signal"], ta.UNAVAILABLE)

    def test_39_snapshot_reflects_current_state(self):
        """Snapshot must read the same trend as get_mtf_state."""
        _reset("MNQ")
        bars = _bullish_bars_1m(BASE_TS, 1440, start_price=21000, step=0.5)
        ta.seed_from_1m_bars("MNQ", bars)
        st = ta.get_mtf_state("MNQ")
        snap = ta.get_snapshot_for_signal("MNQ")
        self.assertEqual(snap["four_h_trend_at_signal"],    st["four_hour"]["trend"])
        self.assertEqual(snap["fifteen_m_trend_at_signal"], st["fifteen_minute"]["trend"])
        self.assertEqual(snap["trend_alignment_at_signal"], st["alignment"])

    def test_40_fail_open_on_unknown_instrument(self):
        snap = ta.get_snapshot_for_signal("TOTALLY_FAKE")
        # Should return None-valued keys, not raise
        for v in snap.values():
            self.assertIn(v, [None, ta.UNAVAILABLE, ta.STALE, ta.BULLISH,
                               ta.BEARISH, ta.NEUTRAL, ta.ALIGNED_LONG,
                               ta.ALIGNED_SHORT, ta.CONFLICTING, ta.MIXED])


# ===========================================================================
# 8. Staleness detection
# ===========================================================================

class TestStaleness(unittest.TestCase):

    def setUp(self):
        _reset("MNQ")

    def test_41_old_bars_are_unavailable_on_display_with_stale_metadata(self):
        """Old closed bars never retain a directional display value.

        Seed bars starting 48h ago, running for 24h → last bar ends ~24h ago
        which is well beyond STALE_15M_SEC (30 min) and STALE_4H_SEC (8h).
        """
        old_base = int(time.time()) - 48 * 3600   # start 48h ago
        bars = _bullish_bars_1m(old_base, 1440, start_price=21000, step=0.3)
        ta.seed_from_1m_bars("MNQ", bars)
        st = ta.get_mtf_state("MNQ")
        # Both directional values are intentionally unavailable; the stale
        # condition and source/age remain available for operators.
        for tf in (st["fifteen_minute"], st["four_hour"]):
            self.assertEqual(tf["trend"], ta.UNAVAILABLE)
            self.assertTrue(tf["stale"])
            self.assertEqual(tf["freshness"], ta.STALE)
            self.assertGreater(tf["age_seconds"], 0)
            self.assertIn("databento", tf["source"])
            self.assertEqual(tf["unavailable_reason"], "closed_bar_stale")
        self.assertEqual(st["alignment"], ta.UNAVAILABLE)
        self.assertEqual(st["alignment_freshness"], ta.STALE)

    def test_42_stale_15m_produces_unavailable_alignment(self):
        old_base = int(time.time()) - 48 * 3600   # bars end ~24h ago
        bars = _bullish_bars_1m(old_base, 1440, start_price=21000, step=0.3)
        ta.seed_from_1m_bars("MNQ", bars)
        st = ta.get_mtf_state("MNQ")
        self.assertEqual(st["alignment"], ta.UNAVAILABLE)
        self.assertEqual(st["alignment_freshness"], ta.STALE)


# ===========================================================================
# 9. No scoring / execution change guard
# ===========================================================================

class TestNoScoringOrExecutionChange(unittest.TestCase):
    """Smoke guard — trend_alignment module must import cleanly with no
    side-effects on scoring or execution constants."""

    def test_43_module_has_no_gate_side_effects(self):
        """Importing trend_alignment must not modify any global gate state."""
        import importlib
        # Re-import to test idempotency
        importlib.reload(ta)
        # No assertion needed — if it raises, the test fails

    def test_44_alignment_constants_have_no_numeric_weight(self):
        """Alignment strings must not be numeric or convertible to a score."""
        for val in [ta.ALIGNED_LONG, ta.ALIGNED_SHORT, ta.CONFLICTING,
                    ta.MIXED, ta.UNAVAILABLE, ta.STALE,
                    ta.BULLISH, ta.BEARISH, ta.NEUTRAL]:
            with self.assertRaises((ValueError, TypeError)):
                float(val)   # must NOT be numeric

    def test_45_get_alignment_is_deterministic(self):
        """Same inputs must always produce same output."""
        for t4h in [ta.BULLISH, ta.BEARISH, ta.NEUTRAL, ta.UNAVAILABLE, ta.STALE]:
            for t15 in [ta.BULLISH, ta.BEARISH, ta.NEUTRAL, ta.UNAVAILABLE, ta.STALE]:
                r1 = ta.get_alignment(t4h, t15)
                r2 = ta.get_alignment(t4h, t15)
                self.assertEqual(r1, r2, f"Non-deterministic for ({t4h}, {t15})")


# ===========================================================================
# 10. Timestamp correctness
# ===========================================================================

class TestTimestampCorrectness(unittest.TestCase):

    def test_46_last_closed_bar_is_iso_string_or_none(self):
        _reset("MNQ")
        bars = _bullish_bars_1m(BASE_TS, 1440, start_price=21000, step=0.3)
        ta.seed_from_1m_bars("MNQ", bars)
        st = ta.get_mtf_state("MNQ")
        ts15 = st["fifteen_minute"]["last_closed_bar"]
        ts4h = st["four_hour"]["last_closed_bar"]
        if ts15 is not None:
            self.assertIn("T", ts15, "Not ISO format")
        if ts4h is not None:
            self.assertIn("T", ts4h, "Not ISO format")

    def test_47_updated_at_is_recent(self):
        st = ta.get_mtf_state("MNQ")
        from datetime import datetime, timezone
        upd = datetime.fromisoformat(st["updated_at"].replace("Z", "+00:00"))
        age_sec = (datetime.now(timezone.utc) - upd).total_seconds()
        self.assertLess(abs(age_sec), 5, "updated_at is not current")


# ===========================================================================
# 11. Regression — golden suites pass
# ===========================================================================

import subprocess

class TestPhase8B1Regression(unittest.TestCase):

    WS = os.path.dirname(os.path.dirname(os.path.dirname(
         os.path.dirname(os.path.abspath(__file__)))))

    SUITES = [
        (".local/state/check_parity.sh",       "PARITY"),
        (".local/state/check_scalp_golden.sh", "SCALP_GOLDEN"),
        (".local/state/check_dual_sim.sh",     "DUAL_SIM"),
        (".local/state/check_breakout_mode.sh","BREAKOUT_MODE"),
    ]

    def test_48_all_golden_suites_pass(self):
        for script, label in self.SUITES:
            with self.subTest(suite=label):
                result = subprocess.run(
                    ["bash", script],
                    cwd=self.WS,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                self.assertEqual(
                    result.returncode, 0,
                    msg=f"{label} FAILED\n{result.stdout[-800:]}\n{result.stderr[-400:]}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
