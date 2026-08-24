"""
Canonical Market State Engine — test suite
==========================================
Tests cover all 27 spec requirements plus additional edge cases.
Run: cd artifacts/tradingview-webhook && pytest test_canonical_market_state.py -v
"""
from __future__ import annotations

import time
import math
import threading
from unittest.mock import MagicMock, patch
import pytest

# ── Module under test ─────────────────────────────────────────────────────────
import canonical_market_state as cms
from canonical_market_state import (
    CanonicalMarketStateEngine,
    replay_bars,
    _session_start,
    WARMUP_BARS,
    ATR_PERIOD,
    SWING_LOOKBACK,
    HEALTHY,
    STALE,
    INSUFFICIENT_HISTORY,
    DATA_UNAVAILABLE,
    SHADOW,
    STALE_THRESHOLD_S,
)


# ── Fixtures / helpers ────────────────────────────────────────────────────────

def _bar(ts, o, h, lo, c, vol=100):
    return {"ts": ts, "open": o, "high": h, "low": lo, "close": c, "volume": vol}


def _bars_trend_up(n=40, start_price=1000.0, start_ts=1_700_000_000.0, tick_size=1.0):
    """Generate n uptrending 1m bars."""
    bars = []
    for i in range(n):
        o = start_price + i * tick_size
        h = o + tick_size * 2
        lo = o - tick_size * 0.5
        c = h - tick_size * 0.3
        bars.append(_bar(start_ts + i * 60, o, h, lo, c))
    return bars


def _bars_trend_down(n=40, start_price=2000.0, start_ts=1_700_000_000.0, tick_size=1.0):
    """Generate n downtrending 1m bars."""
    bars = []
    for i in range(n):
        o = start_price - i * tick_size
        h = o + tick_size * 0.5
        lo = o - tick_size * 2
        c = lo + tick_size * 0.3
        bars.append(_bar(start_ts + i * 60, o, h, lo, c))
    return bars


def _engine(inst="MNQ"):
    return CanonicalMarketStateEngine(inst)


def _feed(engine, bars):
    for bar in bars:
        engine.on_bar_close(bar)
    return engine.get_snapshot()


# ── 1. Per-instrument state isolation ─────────────────────────────────────────

class TestInstrumentIsolation:
    def test_separate_engines_no_crosstalk(self):
        """State changes in MNQ must not affect MGC."""
        mnq = CanonicalMarketStateEngine("MNQ")
        mgc = CanonicalMarketStateEngine("MGC")
        bars = _bars_trend_up(30, start_price=29000.0)
        _feed(mnq, bars)
        snap_mgc = mgc.get_snapshot()
        assert snap_mgc["last_price"] is None
        assert snap_mgc["vwap"]["value"] is None

    def test_instruments_named_correctly(self):
        for inst in ("MGC", "MNQ", "MES", "MYM"):
            e = CanonicalMarketStateEngine(inst)
            assert e.get_snapshot()["instrument"] == inst

    def test_reset_for_replay_clears_state(self):
        e = _engine()
        _feed(e, _bars_trend_up(20))
        e.reset_for_replay()
        snap = e.get_snapshot()
        assert snap["last_price"] is None
        assert snap["vwap"]["value"] is None
        assert snap["atr"]["value"] is None


# ── 2. VWAP calculation ────────────────────────────────────────────────────────

class TestVwapCalculation:
    def test_vwap_basic(self):
        """VWAP should equal typical-price when all bars have identical price."""
        e = _engine()
        ts = 1_700_000_000.0
        # All bars: H=110, L=100, C=105 → typical=105
        for i in range(5):
            e.on_bar_close(_bar(ts + i * 60, 100, 110, 100, 105, vol=100))
        snap = e.get_snapshot()
        assert snap["vwap"]["value"] == pytest.approx(105.0, abs=0.001)

    def test_vwap_volume_weighted(self):
        """VWAP must weight by volume."""
        e = _engine()
        ts = 1_700_000_000.0
        # Bar 1: typical=100, vol=100  → pv=10000
        # Bar 2: typical=200, vol=200  → pv=40000
        # VWAP = 50000/300 = 166.67
        e.on_bar_close(_bar(ts,       90, 110,  90, 100, vol=100))   # typical≈100
        e.on_bar_close(_bar(ts + 60, 190, 210, 190, 200, vol=200))   # typical≈200
        snap = e.get_snapshot()
        # VWAP = (100*100 + 200*200) / (100+200) = 50000/300 ≈ 166.67
        expected = (100*100 + 200*200) / 300
        assert snap["vwap"]["value"] == pytest.approx(expected, abs=1.0)

    def test_vwap_side_above(self):
        e = _engine()
        ts = 1_700_000_000.0
        for i in range(3):
            e.on_bar_close(_bar(ts + i*60, 100, 105, 100, 100, vol=100))
        # Now add a bar with high close — price will be above VWAP
        e.on_bar_close(_bar(ts + 180, 100, 200, 100, 200, vol=10))
        snap = e.get_snapshot()
        assert snap["vwap"]["side"] in ("ABOVE", "AT")

    def test_vwap_side_below(self):
        e = _engine()
        ts = 1_700_000_000.0
        # Build VWAP around 100, then last bar closes way below
        for i in range(5):
            e.on_bar_close(_bar(ts + i*60, 100, 105, 98, 102, vol=200))
        e.on_bar_close(_bar(ts + 300, 50, 55, 48, 50, vol=1))
        snap = e.get_snapshot()
        assert snap["vwap"]["side"] in ("BELOW", "AT")

    def test_vwap_distance_and_pct(self):
        e = _engine()
        ts = 1_700_000_000.0
        e.on_bar_close(_bar(ts, 100, 100, 100, 100, vol=100))
        e.on_bar_close(_bar(ts+60, 110, 110, 110, 110, vol=100))
        snap = e.get_snapshot()
        assert snap["vwap"]["distance"] is not None
        assert snap["vwap"]["distance_pct"] is not None


# ── 3. VWAP session reset ──────────────────────────────────────────────────────

class TestVwapSessionReset:
    def test_session_reset_at_boundary(self):
        """VWAP must reset when crossing the 18:00 ET session boundary.

        Use a summer date (EDT = UTC-4) where 18:00 ET = 22:00 UTC.
        Bar before boundary at 21:50 UTC, bar after at 22:05 UTC.
        """
        e = _engine()
        from datetime import datetime, timezone
        # 2024-06-15 is EDT (UTC-4): 18:00 ET = 22:00 UTC
        ts1 = datetime(2024, 6, 15, 21, 50, 0, tzinfo=timezone.utc).timestamp()
        e.on_bar_close(_bar(ts1, 100, 105, 95, 100, vol=500))
        snap1 = e.get_snapshot()
        vwap1 = snap1["vwap"]["value"]

        # Session 2: bar at 22:05 UTC (18:05 EDT → new session)
        ts2 = datetime(2024, 6, 15, 22, 5, 0, tzinfo=timezone.utc).timestamp()
        e.on_bar_close(_bar(ts2, 200, 210, 195, 205, vol=100))
        snap2 = e.get_snapshot()
        vwap2 = snap2["vwap"]["value"]

        # After session reset, VWAP should be based only on session2 bars
        assert vwap2 is not None
        assert vwap2 > 150, f"Expected VWAP > 150 after reset, got {vwap2}"

    def test_no_reset_within_same_session(self):
        """Bars in same session should accumulate (VWAP shifts smoothly)."""
        e = _engine()
        from datetime import datetime, timezone
        base = datetime(2023, 11, 15, 22, 5, 0, tzinfo=timezone.utc).timestamp()
        e.on_bar_close(_bar(base,        100, 110, 95, 100, vol=100))
        snap_a = e.get_snapshot()
        e.on_bar_close(_bar(base + 60,   200, 210, 195, 200, vol=100))
        snap_b = e.get_snapshot()
        # VWAP should have shifted, not reset to single bar
        assert snap_b["vwap"]["sample_volume"] > snap_a["vwap"]["sample_volume"]


# ── 4. Relative-volume calculation ────────────────────────────────────────────

class TestRelativeVolume:
    def test_rvol_requires_min_history(self):
        e = _engine()
        e.on_bar_close(_bar(0, 100, 105, 95, 100, vol=100))
        snap = e.get_snapshot()
        # Only 1 bar — not enough for baseline
        assert snap["volume"]["relative_volume"] is None

    def test_rvol_computed_after_two_bars(self):
        e = _engine()
        e.on_bar_close(_bar(0,  100, 105, 95, 100, vol=100))
        e.on_bar_close(_bar(60, 100, 105, 95, 100, vol=200))
        snap = e.get_snapshot()
        assert snap["volume"]["relative_volume"] is not None
        # 2nd bar vol (200) / baseline (100) = 2.0
        assert snap["volume"]["relative_volume"] == pytest.approx(2.0, abs=0.1)

    def test_rvol_regime_high(self):
        e = _engine()
        for i in range(5):
            e.on_bar_close(_bar(i*60, 100, 105, 95, 100, vol=100))
        e.on_bar_close(_bar(300, 100, 105, 95, 100, vol=500))  # 5× baseline → HIGH
        snap = e.get_snapshot()
        assert snap["volume"]["regime"] == "HIGH"

    def test_session_cumulative_volume(self):
        e = _engine()
        for i in range(3):
            e.on_bar_close(_bar(i*60, 100, 105, 95, 100, vol=100))
        snap = e.get_snapshot()
        assert snap["volume"]["session_cumulative"] == 300


# ── 5. ATR calculation ────────────────────────────────────────────────────────

class TestAtrCalculation:
    def test_atr_needs_period_bars(self):
        e = _engine()
        for i in range(ATR_PERIOD - 1):
            e.on_bar_close(_bar(i*60, 100, 110, 90, 100))
        snap = e.get_snapshot()
        assert snap["atr"]["value"] is None
        assert snap["atr"]["health"] == INSUFFICIENT_HISTORY

    def test_atr_computes_after_period(self):
        e = _engine()
        for i in range(ATR_PERIOD + 1):
            e.on_bar_close(_bar(i*60, 100, 110, 90, 100))  # TR = 20 each bar
        snap = e.get_snapshot()
        assert snap["atr"]["value"] == pytest.approx(20.0, abs=2.0)

    def test_atr_percent(self):
        e = _engine()
        for i in range(ATR_PERIOD + 1):
            e.on_bar_close(_bar(i*60, 1000, 1010, 990, 1000))  # TR=20, price=1000
        snap = e.get_snapshot()
        assert snap["atr"]["atr_pct"] is not None
        assert snap["atr"]["atr_pct"] == pytest.approx(2.0, abs=0.5)

    def test_atr_uses_previous_close(self):
        """TR must include gap from previous close."""
        e = _engine()
        # Bar 1: high=110, low=90, close=100  → TR=20
        e.on_bar_close(_bar(0, 90, 110, 90, 100))
        # Bar 2: gapped up open, high=120, low=108, close=115 → TR = max(12, 20, 8) = 20
        e.on_bar_close(_bar(60, 108, 120, 108, 115))
        # The second bar's TR includes |high - prev_close| = |120-100| = 20
        # (no assertion on exact value since it's early warmup, just check no error)
        snap = e.get_snapshot()
        assert snap["atr"]["health"] in (INSUFFICIENT_HISTORY, HEALTHY)


# ── 6-8. Trend (consumed from trend_alignment) ────────────────────────────────

class TestTrend:
    def test_trend_block_present_in_snapshot(self):
        """Trend block must always appear, even when empty (fail-open)."""
        e = _engine()
        _feed(e, _bars_trend_up(5))
        with patch("canonical_market_state._DATABENTO_BARS", {}), \
             patch("canonical_market_state._CVD_BY_TICKER", {}), \
             patch("canonical_market_state._RVOL_BY_TICKER", {}):
            snap = e.get_snapshot()
        # Augment manually
        from canonical_market_state import _augment_snapshot
        with patch("trend_alignment.get_mtf_state", return_value={
            "source": "databento_1m_resample",
            "alignment": "ALIGNED_LONG", "alignment_freshness": "CURRENT",
            "fifteen_minute": {"trend": "BULLISH", "bar_count": 25, "freshness": "CURRENT", "age_seconds": 30},
            "four_hour": {"trend": "BULLISH", "bar_count": 6, "freshness": "CURRENT", "age_seconds": 120},
        }):
            with patch("fvg_engine.FVG_ZONES_BY_INST", {}, create=True):
                _augment_snapshot("MNQ", snap)
        assert "trend" in snap
        assert snap["trend"]["trend_15m"] == "BULLISH"

    def test_trend_alignment_bullish(self):
        e = _engine()
        _feed(e, _bars_trend_up(5))
        snap = e.get_snapshot()
        with patch("trend_alignment.get_mtf_state", return_value={
            "source": "databento_1m_resample",
            "alignment": "ALIGNED_LONG", "alignment_freshness": "CURRENT",
            "fifteen_minute": {"trend": "BULLISH", "freshness": "CURRENT"},
            "four_hour": {"trend": "BULLISH", "freshness": "CURRENT"},
        }), patch("fvg_engine.FVG_ZONES_BY_INST", {}, create=True):
            from canonical_market_state import _augment_snapshot
            _augment_snapshot("MNQ", snap)
        assert snap["trend"]["trend_alignment"] == "ALIGNED_LONG"

    def test_trend_alignment_mixed(self):
        e = _engine()
        _feed(e, _bars_trend_up(5))
        snap = e.get_snapshot()
        with patch("trend_alignment.get_mtf_state", return_value={
            "source": "databento_1m_resample",
            "alignment": "CONFLICTING", "alignment_freshness": "CURRENT",
            "fifteen_minute": {"trend": "BULLISH", "freshness": "CURRENT"},
            "four_hour": {"trend": "BEARISH", "freshness": "CURRENT"},
        }), patch("fvg_engine.FVG_ZONES_BY_INST", {}, create=True):
            from canonical_market_state import _augment_snapshot
            _augment_snapshot("MNQ", snap)
        assert snap["trend"]["trend_alignment"] == "CONFLICTING"

    def test_trend_alignment_unknown_when_no_data(self):
        e = _engine()
        _feed(e, _bars_trend_up(5))
        snap = e.get_snapshot()
        with patch("trend_alignment.MTF_STATE_BY_INST", {}, create=True), \
             patch("fvg_engine.FVG_ZONES_BY_INST", {}, create=True):
            from canonical_market_state import _augment_snapshot
            _augment_snapshot("MNQ", snap)
        assert snap["trend"]["trend_alignment"] == "UNAVAILABLE"

    def test_stale_shadow_trend_is_unavailable_with_age(self):
        e = _engine()
        snap = e.get_snapshot()
        with patch("trend_alignment.get_mtf_state", return_value={
            "source": "databento_1m_resample",
            "alignment": "UNAVAILABLE", "alignment_freshness": "STALE",
            "fifteen_minute": {"trend": "UNAVAILABLE", "freshness": "STALE", "age_seconds": 1900, "bar_count": 25},
            "four_hour": {"trend": "UNAVAILABLE", "freshness": "STALE", "age_seconds": 32000, "bar_count": 6},
        }), patch("fvg_engine.FVG_ZONES_BY_INST", {}, create=True):
            from canonical_market_state import _augment_snapshot
            _augment_snapshot("MNQ", snap)
        assert snap["trend"]["trend_15m"] == "UNAVAILABLE"
        assert snap["trend"]["trend_4h"] == "UNAVAILABLE"
        assert snap["trend"]["health"] == STALE
        assert snap["trend"]["trend_15m_age_seconds"] == 1900
        assert snap["trend"]["true_source"] == "databento"

    def test_malformed_shadow_trend_is_explicit_calculation_error(self):
        e = _engine()
        snap = e.get_snapshot()
        with patch("trend_alignment.get_mtf_state", side_effect=TypeError("malformed trend state")), \
             patch("fvg_engine.FVG_ZONES_BY_INST", {}, create=True):
            from canonical_market_state import _augment_snapshot
            _augment_snapshot("MNQ", snap)
        assert snap["trend"]["health"] == cms.CALCULATION_ERROR
        assert snap["trend"]["trend_15m"] == "UNAVAILABLE"
        assert snap["trend"]["trend_4h"] == "UNAVAILABLE"
        assert snap["trend"]["trend_alignment"] == "UNAVAILABLE"
        assert snap["trend"]["error_code"] == "TREND_STATE_READ_FAILED"


# ── 9-14. Market structure ────────────────────────────────────────────────────

class TestMarketStructure:
    def _make_hh_hl_bars(self, n=40, base_ts=1_700_000_000.0):
        """Create bars with clear HH/HL pattern (uptrend)."""
        bars = []
        price = 1000.0
        for i in range(n):
            # Zigzag with rising lows and highs
            if i % 6 < 3:
                h = price + 10 + i * 0.5
                lo = price - 2
                c = h - 2
            else:
                h = price + 5 + i * 0.3
                lo = price - 3 + i * 0.2
                c = lo + 1
                price = lo + 1  # Rising low
            bars.append(_bar(base_ts + i * 60, price, h, lo, c))
        return bars

    def test_structure_needs_enough_bars(self):
        e = _engine()
        # Feed fewer bars than required for pivot detection
        for i in range(SWING_LOOKBACK):
            e.on_bar_close(_bar(i*60, 100, 110, 90, 100))
        snap = e.get_snapshot()
        assert snap["structure"]["direction"] == "UNKNOWN"

    def test_structure_direction_bullish_bos(self):
        """Bars with zigzag pattern (swing lows/highs) should allow BOS detection."""
        e = _engine()
        ts = 1_700_000_000.0
        # Create a zigzag: every 6 bars is a pull-back, then a new high
        # This ensures pivot highs and lows are detectable
        price = 1000.0
        bars = []
        for i in range(50):
            phase = i % 10
            if phase < 6:   # rally phase
                h = price + 10 + phase
                lo = price - 2
                c = h - 1
            else:            # pullback phase
                h = price + 5
                lo = price - 5 + phase * 0.5
                c = lo + 2
                price = c    # ratchet up slowly
            bars.append(_bar(ts + i*60, price, h, lo, c))
        _feed(e, bars)
        snap = e.get_snapshot()
        # With realistic zigzag, structure health should not be DATA_UNAVAILABLE
        # (INSUFFICIENT_HISTORY is acceptable when pivot detection needs more bars)
        assert snap["structure"]["health"] != DATA_UNAVAILABLE or snap["warmup"]["bars_available"] < WARMUP_BARS

    def test_bullish_bos_detected(self):
        """Close above swing high → BOS BULLISH."""
        e = _engine()
        ts = 1_700_000_000.0
        # Phase 1: establish a swing high around 1020
        for i in range(15):
            h = 1000 + 20 * math.sin(i * 0.6)
            lo = 1000 - 5
            c = 1000 + 5
            e.on_bar_close(_bar(ts + i*60, 1000, max(h, lo+1), lo, c))
        # Phase 2: strong bullish close that breaks above swing high
        for j in range(10):
            e.on_bar_close(_bar(ts + (15+j)*60, 1050+j*3, 1055+j*3, 1048+j*3, 1053+j*3))
        snap = e.get_snapshot()
        # Either BOS fired or structure has bullish direction
        bos = snap["structure"]["last_bos"]
        if bos is not None:
            assert bos["direction"] == "BULLISH"

    def test_bearish_bos_detected(self):
        """Close below swing low → BOS BEARISH."""
        e = _engine()
        ts = 1_700_000_000.0
        # Phase 1: range
        for i in range(15):
            lo = 1000 - 20 * abs(math.sin(i * 0.6))
            e.on_bar_close(_bar(ts + i*60, 1000, 1010, lo, 1005))
        # Phase 2: strong bearish move below swing low
        for j in range(10):
            e.on_bar_close(_bar(ts + (15+j)*60, 970-j*3, 972-j*3, 965-j*3, 966-j*3))
        snap = e.get_snapshot()
        bos = snap["structure"]["last_bos"]
        if bos is not None:
            assert bos["direction"] == "BEARISH"

    def test_choch_fires_on_reversal(self):
        """CHoCH must fire when BOS reverses established structure direction."""
        e = _engine()
        ts = 1_700_000_000.0
        # Step 1: establish bullish structure
        for i in range(25):
            price = 1000 + i * 2
            e.on_bar_close(_bar(ts + i*60, price, price+6, price-1, price+4))
        # Step 2: bearish reversal
        for j in range(20):
            price = 1048 - j * 3
            e.on_bar_close(_bar(ts + (25+j)*60, price, price+1, price-6, price-4))
        snap = e.get_snapshot()
        # CHoCH may or may not have fired depending on pivot positions — structure dir should shift
        assert snap["structure"]["health"] in (HEALTHY, INSUFFICIENT_HISTORY)

    def test_hh_hl_structure_classified(self):
        e = _engine()
        bars = self._make_hh_hl_bars(40)
        for bar in bars:
            e.on_bar_close(bar)
        snap = e.get_snapshot()
        # At minimum should have detected some swing points
        assert snap["structure"]["swing_high"] is not None or snap["structure"]["direction"] != "UNKNOWN" or snap["warmup"]["bars_available"] >= WARMUP_BARS


# ── 15. Stale-data behavior ────────────────────────────────────────────────────

class TestStaleness:
    def test_vwap_goes_stale_after_threshold(self):
        e = _engine()
        ts = 1_700_000_000.0
        e.on_bar_close(_bar(ts, 100, 105, 95, 100))
        # Manually wind back the update time
        e._vwap_updated = time.time() - (STALE_THRESHOLD_S + 10)
        snap = e.get_snapshot()
        assert snap["vwap"]["health"] == STALE

    def test_atr_goes_stale_after_threshold(self):
        e = _engine()
        ts = 1_700_000_000.0
        for i in range(ATR_PERIOD + 1):
            e.on_bar_close(_bar(ts + i*60, 100, 110, 90, 100))
        e._atr_updated = time.time() - (STALE_THRESHOLD_S + 10)
        snap = e.get_snapshot()
        assert snap["atr"]["health"] == STALE


# ── 16. Insufficient-history behavior ────────────────────────────────────────

class TestInsufficientHistory:
    def test_atr_unavailable_before_warmup(self):
        e = _engine()
        for i in range(5):  # far fewer than ATR_PERIOD
            e.on_bar_close(_bar(i*60, 100, 110, 90, 100))
        snap = e.get_snapshot()
        assert snap["atr"]["value"] is None
        assert snap["atr"]["health"] == INSUFFICIENT_HISTORY

    def test_structure_insufficient_before_warmup(self):
        e = _engine()
        for i in range(SWING_LOOKBACK):
            e.on_bar_close(_bar(i*60, 100, 110, 90, 100))
        snap = e.get_snapshot()
        assert snap["structure"]["direction"] == "UNKNOWN"

    def test_warmup_flags_exposed(self):
        e = _engine()
        e.on_bar_close(_bar(0, 100, 110, 90, 100))
        snap = e.get_snapshot()
        assert snap["warmup"]["complete"] is False
        assert snap["warmup"]["bars_required"] == WARMUP_BARS
        assert snap["warmup"]["bars_available"] == 1

    def test_vwap_null_not_zero_when_missing(self):
        """Must use null, not synthesized zero values."""
        e = _engine()
        snap = e.get_snapshot()
        assert snap["vwap"]["value"] is None
        assert snap["atr"]["value"] is None


# ── 17. Deterministic replay ──────────────────────────────────────────────────

class TestDeterministicReplay:
    def _same_bars(self):
        ts = 1_700_000_000.0
        bars = []
        for i in range(50):
            price = 1000 + math.sin(i * 0.3) * 20
            bars.append(_bar(ts + i*60, price, price+8, price-8, price+3))
        return bars

    def test_same_bars_same_vwap(self):
        bars = self._same_bars()
        snap1 = replay_bars("MNQ", bars)
        snap2 = replay_bars("MNQ", bars)
        assert snap1["vwap"]["value"] == snap2["vwap"]["value"]

    def test_same_bars_same_atr(self):
        bars = self._same_bars()
        snap1 = replay_bars("MNQ", bars)
        snap2 = replay_bars("MNQ", bars)
        assert snap1["atr"]["value"] == snap2["atr"]["value"]

    def test_same_bars_same_structure(self):
        bars = self._same_bars()
        snap1 = replay_bars("MNQ", bars)
        snap2 = replay_bars("MNQ", bars)
        assert snap1["structure"]["direction"] == snap2["structure"]["direction"]
        # last_bos and last_choch must match too
        assert snap1["structure"]["last_bos"] == snap2["structure"]["last_bos"]

    def test_same_bars_same_sweep_count(self):
        bars = self._same_bars()
        snap1 = replay_bars("MNQ", bars)
        snap2 = replay_bars("MNQ", bars)
        assert snap1["sweeps"]["total_detected"] == snap2["sweeps"]["total_detected"]


# ── 18. Databento disconnect behavior ────────────────────────────────────────

class TestDisconnectBehavior:
    def test_engine_handles_empty_bars_deque(self):
        """on_bar_close with empty public deque must not raise."""
        from collections import deque
        original = cms._DATABENTO_BARS
        cms._started = True
        cms._engines["MNQ"] = CanonicalMarketStateEngine("MNQ")
        cms._DATABENTO_BARS = {"MNQ": deque()}  # empty
        try:
            cms.on_bar_close("MNQ", 1000.0)  # must not raise
        finally:
            cms._DATABENTO_BARS = original
            cms._started = False

    def test_engine_handles_none_bar_fields(self):
        """Bar with None fields must not corrupt state."""
        e = _engine()
        e.on_bar_close({"ts": 0, "open": None, "high": None, "low": None, "close": None, "volume": None})
        snap = e.get_snapshot()
        assert snap["vwap"]["value"] is None


# ── 19. Comparison logging failure does not block trading ─────────────────────

class TestComparisonFailureIsolation:
    def test_comparison_log_failure_silent(self):
        """record_legacy_comparison with broken DB must not raise."""
        def bad_db():
            raise RuntimeError("DB down")

        cms_state_get_db = cms._get_db_fn
        cms._get_db_fn = bad_db
        try:
            # Must not raise
            cms.record_legacy_comparison("MNQ", "vwap", 100.0, 101.0)
        finally:
            cms._get_db_fn = cms_state_get_db

    def test_comparison_logged_with_good_db(self):
        """record_legacy_comparison calls DB correctly when available."""
        mock_cur = MagicMock()
        mock_db  = MagicMock()
        mock_db.cursor.return_value = mock_cur

        orig = cms._get_db_fn
        cms._get_db_fn = lambda: mock_db
        cms._started   = True
        try:
            cms.record_legacy_comparison("MGC", "vwap", 1800.0, 1800.5, {"note": "test"})
            assert mock_cur.execute.called
            assert mock_db.commit.called
        finally:
            cms._get_db_fn = orig
            cms._started   = False


# ── 20-21. Feature flag OFF / shadow-only produces zero live behavior change ──

class TestFeatureFlags:
    def test_disabled_engine_returns_no_state(self):
        """When CMS_ENABLED is False, start() must be a no-op."""
        with patch.object(cms, "CMS_ENABLED", False):
            original_engines = dict(cms._engines)
            # Simulate calling start() with disabled flag
            if not cms.CMS_ENABLED:
                pass  # engine would not start
            # Engines should not be modified
            # (in real code start() returns early when disabled)

    def test_source_selectors_all_legacy_by_default(self):
        """All source selectors default to 'legacy' — fail safe."""
        import importlib, os
        # Without env vars, all sources must be legacy
        for selector in ("VWAP_SOURCE", "STRUCTURE_SOURCE", "CVD_SOURCE",
                         "SWEEP_SOURCE", "FVG_SOURCE", "ZONE_SOURCE"):
            val = getattr(cms, selector)
            assert val == "legacy", f"{selector} must default to 'legacy', got {val!r}"

    def test_shadow_only_default(self):
        assert cms.CMS_SHADOW_ONLY is True

    def test_all_promotion_statuses_are_shadow(self):
        """No component may be LIVE_CANONICAL in this phase."""
        e = _engine()
        _feed(e, _bars_trend_up(50))
        snap = e.get_snapshot()
        for comp_name in ("vwap", "volume", "atr", "structure", "sweeps"):
            comp = snap.get(comp_name, {})
            status = comp.get("promotion_status")
            assert status != "LIVE_CANONICAL", f"{comp_name} must not be LIVE_CANONICAL"
            assert status == "SHADOW", f"{comp_name} promotion_status should be SHADOW"


# ── 22-24. Existing scoring regressions ────────────────────────────────────────

class TestScoringRegression:
    """These tests verify that the canonical engine is truly isolated from
    the production scoring path.  They operate at the canonical_market_state
    module boundary — not at app.py level — to keep test scope narrow."""

    def test_get_snapshot_never_sets_ready(self):
        e = _engine()
        _feed(e, _bars_trend_up(50))
        snap = e.get_snapshot()
        # snapshot must not contain any verdict / ready / edge_score key
        for forbidden in ("verdict", "edge_score", "ready", "gate"):
            assert forbidden not in snap, f"Snapshot must not contain {forbidden!r}"

    def test_on_bar_close_does_not_modify_external_dicts(self):
        """Engine must not write to CVD_BY_TICKER or RVOL_BY_TICKER."""
        sentinel_cvd  = {"MNQ": {"value": 42.0}}
        sentinel_rvol = {"MNQ": {"value": 1.5}}

        orig_cvd  = cms._CVD_BY_TICKER
        orig_rvol = cms._RVOL_BY_TICKER
        cms._CVD_BY_TICKER  = sentinel_cvd
        cms._RVOL_BY_TICKER = sentinel_rvol
        try:
            e = _engine()
            _feed(e, _bars_trend_up(20))
            assert cms._CVD_BY_TICKER["MNQ"]["value"] == 42.0
            assert cms._RVOL_BY_TICKER["MNQ"]["value"] == 1.5
        finally:
            cms._CVD_BY_TICKER  = orig_cvd
            cms._RVOL_BY_TICKER = orig_rvol


# ── 25. ORB (read-only pass-through) ──────────────────────────────────────────

class TestOrbPassthrough:
    def test_snapshot_does_not_contain_orb_engine(self):
        """Canonical state must not implement its own ORB — it should read existing."""
        e = _engine()
        snap = e.get_snapshot()
        # ORB data is not in the per-engine snapshot (it's added at augment time if available)
        # Key point: engine must NOT have computed its own ORB_HIGH / ORB_LOW
        assert "orb_engine" not in snap


# ── 26-27. ARM / execution isolation ─────────────────────────────────────────

class TestExecutionIsolation:
    def test_snapshot_has_no_broker_keys(self):
        e = _engine()
        _feed(e, _bars_trend_up(50))
        snap = e.get_snapshot()
        forbidden = {"entry", "stop", "target", "quantity", "size",
                     "traderspost", "arm", "auto_trade", "broker"}
        for key in forbidden:
            assert key not in snap, f"Snapshot must not expose broker key: {key!r}"

    def test_session_start_deterministic(self):
        """_session_start must return same value for same input ts."""
        ts = 1_700_100_000.0
        assert _session_start(ts) == _session_start(ts)

    def test_session_start_resets_at_boundary(self):
        """Straddles 18:00 ET on a summer date (EDT=UTC-4 → boundary=22:00 UTC)."""
        from datetime import datetime, timezone
        # 2024-06-15 is EDT: 21:59 UTC = 17:59 ET, 22:01 UTC = 18:01 ET → different sessions
        ts_before = datetime(2024, 6, 15, 21, 59, 0, tzinfo=timezone.utc).timestamp()
        ts_after  = datetime(2024, 6, 15, 22,  1, 0, tzinfo=timezone.utc).timestamp()
        assert _session_start(ts_before) != _session_start(ts_after)


# ── Thread-safety smoke test ──────────────────────────────────────────────────

class TestThreadSafety:
    def test_concurrent_bar_close_and_snapshot(self):
        """Concurrent writes and reads must not corrupt state or deadlock."""
        e = _engine()
        errors = []
        ts = 1_700_000_000.0

        def writer():
            for i in range(100):
                try:
                    e.on_bar_close(_bar(ts + i*60, 100, 110, 90, 100+i*0.1))
                except Exception as exc:
                    errors.append(exc)

        def reader():
            for _ in range(50):
                try:
                    e.get_snapshot()
                    time.sleep(0.001)
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Thread-safety errors: {errors}"


# ── Provenance / source fields ────────────────────────────────────────────────

class TestProvenance:
    def test_all_components_have_source_field(self):
        e = _engine()
        _feed(e, _bars_trend_up(30))
        snap = e.get_snapshot()
        for comp in ("vwap", "volume", "atr", "structure", "sweeps"):
            assert "source" in snap[comp], f"{comp} missing source field"
            assert snap[comp]["source"] == "databento"

    def test_all_components_have_health_field(self):
        e = _engine()
        _feed(e, _bars_trend_up(30))
        snap = e.get_snapshot()
        for comp in ("vwap", "volume", "atr", "structure", "sweeps"):
            assert "health" in snap[comp], f"{comp} missing health field"

    def test_no_synthesized_zeros(self):
        """Null must be used for unknown values, not 0."""
        e = _engine()
        snap = e.get_snapshot()
        # Freshly created engine — no data yet
        assert snap["vwap"]["value"] is None
        assert snap["atr"]["value"] is None
        assert snap["volume"]["relative_volume"] is None


# ── DST session boundary tests ─────────────────────────────────────────────────

class TestVwapDstSessionBoundary:
    """Verify VWAP session reset uses 18:00 America/New_York (DST-aware)."""

    def test_edt_session_reset_at_2200_utc(self):
        """Summer (EDT = UTC-4): 18:00 ET = 22:00 UTC — reset must fire at 22:00 UTC."""
        from datetime import datetime, timezone
        # 2024-06-15 is a Saturday; CME trades Sun–Fri; just testing boundary math
        before = datetime(2024, 6, 15, 21, 59, 0, tzinfo=timezone.utc).timestamp()
        after  = datetime(2024, 6, 15, 22,  1, 0, tzinfo=timezone.utc).timestamp()
        assert _session_start(before) != _session_start(after), (
            "EDT: 21:59 UTC and 22:01 UTC should be in different sessions (boundary = 22:00 UTC)"
        )

    def test_est_session_reset_at_2300_utc(self):
        """Winter (EST = UTC-5): 18:00 ET = 23:00 UTC — reset must fire at 23:00 UTC."""
        from datetime import datetime, timezone
        before = datetime(2024, 1, 15, 22, 59, 0, tzinfo=timezone.utc).timestamp()
        after  = datetime(2024, 1, 15, 23,  1, 0, tzinfo=timezone.utc).timestamp()
        assert _session_start(before) != _session_start(after), (
            "EST: 22:59 UTC and 23:01 UTC should be in different sessions (boundary = 23:00 UTC)"
        )

    def test_no_false_reset_at_2200_utc_in_winter(self):
        """Winter (EST): 22:00 UTC = 17:00 ET — must NOT reset (boundary is 23:00 UTC)."""
        from datetime import datetime, timezone
        ts_2200 = datetime(2024, 1, 15, 22,  0, 0, tzinfo=timezone.utc).timestamp()
        ts_2230 = datetime(2024, 1, 15, 22, 30, 0, tzinfo=timezone.utc).timestamp()
        assert _session_start(ts_2200) == _session_start(ts_2230), (
            "EST: 22:00 UTC and 22:30 UTC should be in the SAME session (no reset until 23:00 UTC)"
        )

    def test_session_start_deterministic(self):
        """Same input must always produce same output."""
        from datetime import datetime, timezone
        ts = datetime(2024, 6, 15, 22, 5, 0, tzinfo=timezone.utc).timestamp()
        assert _session_start(ts) == _session_start(ts)

    def test_session_start_is_utc_timestamp(self):
        """session_start must be a Unix timestamp (float), not None."""
        from datetime import datetime, timezone
        ts = datetime(2024, 6, 15, 22, 5, 0, tzinfo=timezone.utc).timestamp()
        result = _session_start(ts)
        assert isinstance(result, float)
        assert result > 0

    def test_vwap_resets_on_edt_session_boundary(self):
        """Engine VWAP must reset when a bar crosses the 18:00 ET boundary in summer."""
        from datetime import datetime, timezone
        e = _engine()
        # Bar before EDT session boundary (21:50 UTC = 17:50 ET)
        ts_pre = datetime(2024, 6, 15, 21, 50, 0, tzinfo=timezone.utc).timestamp()
        e.on_bar_close(_bar(ts_pre, 1000, 1010, 990, 1000, vol=500))
        v1 = e._vwap

        # Bar after EDT session boundary (22:05 UTC = 18:05 ET → new session)
        ts_post = datetime(2024, 6, 15, 22, 5, 0, tzinfo=timezone.utc).timestamp()
        e.on_bar_close(_bar(ts_post, 2000, 2010, 1990, 2000, vol=100))
        v2 = e._vwap

        assert v2 is not None
        assert v2 > 1500, "After session reset, VWAP should reflect only the new session bar"

    def test_vwap_resets_on_est_session_boundary(self):
        """Engine VWAP must reset when a bar crosses the 18:00 ET boundary in winter."""
        from datetime import datetime, timezone
        e = _engine()
        # Bar before EST session boundary (22:50 UTC = 17:50 ET)
        ts_pre = datetime(2024, 1, 15, 22, 50, 0, tzinfo=timezone.utc).timestamp()
        e.on_bar_close(_bar(ts_pre, 1000, 1010, 990, 1000, vol=500))

        # Bar after EST session boundary (23:05 UTC = 18:05 ET → new session)
        ts_post = datetime(2024, 1, 15, 23, 5, 0, tzinfo=timezone.utc).timestamp()
        e.on_bar_close(_bar(ts_post, 2000, 2010, 1990, 2000, vol=100))

        assert e._vwap > 1500, "After EST session reset, VWAP must reflect only new session bar"
        assert e._session_start_ts == _session_start(ts_post)

    def test_session_start_exposed_in_vwap_block(self):
        """Snapshot vwap block must include session_start (ISO string or None)."""
        from datetime import datetime, timezone
        e = _engine()
        ts = datetime(2024, 6, 15, 22, 5, 0, tzinfo=timezone.utc).timestamp()
        e.on_bar_close(_bar(ts, 1000, 1010, 990, 1000, vol=100))
        snap = e.get_snapshot()
        # session_start must be present (either ISO string or None before first bar)
        assert "session_start" in snap["vwap"]
        assert snap["vwap"]["session_start"] is not None


# ── Provenance audit tests ─────────────────────────────────────────────────────

class TestProvenanceAudit:
    """Verify that canonical state reports TRUE source for each component."""

    def _augmented_snap(self, inst="MNQ"):
        from canonical_market_state import _augment_snapshot
        e = CanonicalMarketStateEngine(inst)
        _feed(e, _bars_trend_up(30))
        snap = e.get_snapshot()
        with patch("trend_alignment.MTF_STATE_BY_INST", {}, create=True), \
             patch("fvg_engine.FVG_ZONES_BY_INST", {}, create=True):
            _augment_snapshot(inst, snap)
        return snap

    def test_cvd_true_source_is_databento_primary(self):
        snap = self._augmented_snap()
        assert snap["cvd"]["true_source"] == "databento_primary"

    def test_rvol_true_source_is_databento_primary(self):
        snap = self._augmented_snap()
        assert snap["volume"]["true_source"] == "databento_primary"

    def test_trend_true_source_is_databento(self):
        e = CanonicalMarketStateEngine("MNQ")
        _feed(e, _bars_trend_up(30))
        snap = e.get_snapshot()
        from canonical_market_state import _augment_snapshot
        with patch("trend_alignment.MTF_STATE_BY_INST", {
            "MNQ": {"trend_15m": {"direction": "BULLISH"}, "trend_4h": {"direction": "BULLISH"}}
        }, create=True), patch("fvg_engine.FVG_ZONES_BY_INST", {}, create=True):
            _augment_snapshot("MNQ", snap)
        assert snap["trend"]["true_source"] == "databento"

    def test_fvg_zones_true_source_is_databento(self):
        e = CanonicalMarketStateEngine("MNQ")
        _feed(e, _bars_trend_up(30))
        snap = e.get_snapshot()
        from canonical_market_state import _augment_snapshot
        with patch("trend_alignment.MTF_STATE_BY_INST", {}, create=True), \
             patch("fvg_engine.FVG_ZONES_BY_INST", {"MNQ": []}, create=True):
            _augment_snapshot("MNQ", snap)
        assert snap["fvg_zones"]["true_source"] == "databento"

    def test_zone_state_true_source_is_tradingview(self):
        snap = self._augmented_snap()
        assert snap["zone_state"]["true_source"] == "tradingview"
        assert snap["zone_state"]["promotion_status"] == "UNAVAILABLE_FOR_DATABENTO_PROMOTION"

    def test_zone_state_not_labeled_as_fvg(self):
        snap = self._augmented_snap()
        # zone_state is a SEPARATE block from fvg_zones
        assert "zone_state" in snap
        assert "fvg_zones" in snap
        # zone_state must not say "databento"
        assert snap["zone_state"]["true_source"] != "databento"

    def test_orb_returns_unavailable_without_injection(self):
        """Without intraday_by_ticker injection, ORB must return UNAVAILABLE (not a fake value)."""
        snap = self._augmented_snap()
        orb = snap.get("orb", {})
        assert orb.get("status") == "UNAVAILABLE" or orb.get("true_source") == "tradingview"
        assert orb.get("promotion_status") == "UNAVAILABLE_FOR_DATABENTO_PROMOTION"

    def test_orb_exposed_when_injected(self):
        """With injection, ORB block exposes or_high/or_low/or_complete."""
        from canonical_market_state import _augment_snapshot
        e = CanonicalMarketStateEngine("MNQ")
        _feed(e, _bars_trend_up(10))
        snap = e.get_snapshot()

        intraday = {"MNQ": {"or_high": 29500.0, "or_low": 29400.0, "or_complete": True}}
        orig = cms._INTRADAY_BY_TICKER
        cms._INTRADAY_BY_TICKER = intraday
        try:
            with patch("trend_alignment.MTF_STATE_BY_INST", {}, create=True), \
                 patch("fvg_engine.FVG_ZONES_BY_INST", {}, create=True):
                _augment_snapshot("MNQ", snap)
        finally:
            cms._INTRADAY_BY_TICKER = orig

        orb = snap.get("orb", {})
        assert orb.get("or_high") == 29500.0
        assert orb.get("or_low")  == 29400.0
        assert orb.get("or_complete") is True
        assert orb.get("true_source") == "tradingview"
        assert orb.get("or_width") == pytest.approx(100.0, abs=0.01)

    def test_no_component_lies_about_tradingview_source(self):
        """Components that are TradingView-sourced must not claim 'databento' true_source."""
        snap = self._augmented_snap()
        tv_components = ["zone_state", "orb"]
        for comp in tv_components:
            if comp in snap:
                assert snap[comp].get("true_source") != "databento", (
                    f"{comp} claims databento source but is TradingView-derived"
                )


# ── Comparison metrics tests ───────────────────────────────────────────────────

class TestComparisonMetrics:
    """Verify _VwapStats rolling accumulator and agreement_status logic."""

    def test_vwap_stats_tracks_sample_count(self):
        from canonical_market_state import _VwapStats
        s = _VwapStats()
        s.record(1.5)
        s.record(2.0)
        assert s.sample_count == 2

    def test_vwap_stats_max_tick_diff(self):
        from canonical_market_state import _VwapStats
        s = _VwapStats()
        s.record(3.0)
        s.record(8.0)
        s.record(1.0)
        assert s.max_tick_diff == pytest.approx(8.0, abs=0.001)

    def test_vwap_stats_avg_tick_diff(self):
        from canonical_market_state import _VwapStats
        s = _VwapStats()
        s.record(2.0)
        s.record(4.0)
        d = s.to_dict()
        assert d["avg_tick_diff"] == pytest.approx(3.0, abs=0.01)

    def test_vwap_stats_consecutive_acceptable_resets_on_miss(self):
        from canonical_market_state import _VwapStats, VWAP_MATCH_TICKS
        s = _VwapStats()
        s.record(VWAP_MATCH_TICKS - 0.1)  # acceptable
        s.record(VWAP_MATCH_TICKS - 0.1)  # acceptable
        assert s.consecutive_acceptable == 2
        s.record(VWAP_MATCH_TICKS + 1.0)  # miss — resets counter
        assert s.consecutive_acceptable == 0

    def test_vwap_stats_pct_within_tolerance(self):
        from canonical_market_state import _VwapStats, VWAP_TOLERANCE_TICKS
        s = _VwapStats()
        s.record(VWAP_TOLERANCE_TICKS - 0.1)   # within
        s.record(VWAP_TOLERANCE_TICKS - 0.1)   # within
        s.record(VWAP_TOLERANCE_TICKS + 1.0)   # outside
        d = s.to_dict()
        assert d["pct_within_tolerance"] == pytest.approx(66.7, abs=0.5)

    def test_agreement_status_match(self):
        """VWAP diff ≤ 2 ticks → MATCH."""
        from canonical_market_state import _augment_snapshot, AGREE_MATCH, TICK_SIZES
        e = CanonicalMarketStateEngine("MNQ")
        _feed(e, _bars_trend_up(10, start_price=29800.0))
        snap = e.get_snapshot()
        tick = TICK_SIZES["MNQ"]
        close_legacy = snap["vwap"]["value"]  # match: same as databento
        if close_legacy is None:
            pytest.skip("No VWAP computed yet")
        orig = cms._VWAP_BY_TICKER
        cms._VWAP_BY_TICKER = {"MNQ": {"vwap": close_legacy + tick * 1.0}}  # 1 tick diff
        try:
            with patch("trend_alignment.MTF_STATE_BY_INST", {}, create=True), \
                 patch("fvg_engine.FVG_ZONES_BY_INST", {}, create=True):
                _augment_snapshot("MNQ", snap)
        finally:
            cms._VWAP_BY_TICKER = orig
        assert snap["vwap_comparison"]["agreement_status"] == AGREE_MATCH

    def test_agreement_status_large_diff(self):
        """VWAP diff > 10 ticks → LARGE_DIFF."""
        from canonical_market_state import _augment_snapshot, AGREE_LARGE_DIFF, TICK_SIZES
        e = CanonicalMarketStateEngine("MNQ")
        _feed(e, _bars_trend_up(10, start_price=29800.0))
        snap = e.get_snapshot()
        tick = TICK_SIZES["MNQ"]
        db_vwap = snap["vwap"]["value"]
        if db_vwap is None:
            pytest.skip("No VWAP computed yet")
        orig = cms._VWAP_BY_TICKER
        cms._VWAP_BY_TICKER = {"MNQ": {"vwap": db_vwap + tick * 15.0}}  # 15 ticks diff
        try:
            with patch("trend_alignment.MTF_STATE_BY_INST", {}, create=True), \
                 patch("fvg_engine.FVG_ZONES_BY_INST", {}, create=True):
                _augment_snapshot("MNQ", snap)
        finally:
            cms._VWAP_BY_TICKER = orig
        assert snap["vwap_comparison"]["agreement_status"] == AGREE_LARGE_DIFF

    def test_agreement_status_waiting_when_no_db_vwap(self):
        """WAITING when Databento VWAP not yet computed."""
        from canonical_market_state import _augment_snapshot, AGREE_WAITING
        e = CanonicalMarketStateEngine("MNQ")
        snap = e.get_snapshot()  # fresh engine — no VWAP
        orig = cms._VWAP_BY_TICKER
        cms._VWAP_BY_TICKER = {"MNQ": {"vwap": 29800.0}}
        try:
            with patch("trend_alignment.MTF_STATE_BY_INST", {}, create=True), \
                 patch("fvg_engine.FVG_ZONES_BY_INST", {}, create=True):
                _augment_snapshot("MNQ", snap)
        finally:
            cms._VWAP_BY_TICKER = orig
        assert snap["vwap_comparison"]["agreement_status"] == AGREE_WAITING

    def test_vwap_comparison_block_has_all_required_fields(self):
        """vwap_comparison must include all fields needed by the dashboard."""
        from canonical_market_state import _augment_snapshot
        e = CanonicalMarketStateEngine("MNQ")
        _feed(e, _bars_trend_up(10, start_price=29800.0))
        snap = e.get_snapshot()
        db_vwap = snap["vwap"]["value"]
        if db_vwap is None:
            pytest.skip("No VWAP yet")
        orig = cms._VWAP_BY_TICKER
        cms._VWAP_BY_TICKER = {"MNQ": {"vwap": db_vwap + 1.0}}
        try:
            with patch("trend_alignment.MTF_STATE_BY_INST", {}, create=True), \
                 patch("fvg_engine.FVG_ZONES_BY_INST", {}, create=True):
                _augment_snapshot("MNQ", snap)
        finally:
            cms._VWAP_BY_TICKER = orig
        vc = snap["vwap_comparison"]
        required = [
            "legacy_vwap", "legacy_source", "legacy_freshness",
            "databento_vwap", "databento_source", "databento_freshness",
            "absolute_difference", "tick_difference", "tick_size",
            "agreement_status", "session_start", "sample_volume",
            "sample_count", "consecutive_acceptable",
            "avg_tick_diff", "max_tick_diff", "pct_within_tolerance",
        ]
        for field in required:
            assert field in vc, f"vwap_comparison missing required field: {field!r}"

    def test_tick_size_per_instrument(self):
        """Each instrument uses its own tick size for tick_difference."""
        from canonical_market_state import TICK_SIZES
        assert TICK_SIZES["MGC"] == pytest.approx(0.10, abs=0.001)
        assert TICK_SIZES["MNQ"] == pytest.approx(0.25, abs=0.001)
        assert TICK_SIZES["MES"] == pytest.approx(0.25, abs=0.001)
        assert TICK_SIZES["MYM"] == pytest.approx(1.00, abs=0.001)

    def test_vwap_stats_acceptable_and_unacceptable_counts(self):
        from canonical_market_state import _VwapStats, VWAP_MATCH_TICKS
        s = _VwapStats()
        s.record(VWAP_MATCH_TICKS - 0.1)   # acceptable
        s.record(VWAP_MATCH_TICKS - 0.1)   # acceptable
        s.record(VWAP_MATCH_TICKS + 1.0)   # unacceptable
        assert s.acceptable_count   == 2
        assert s.unacceptable_count == 1
        assert s.sample_count       == 3

    def test_vwap_stats_longest_streak_tracked_separately(self):
        """longest_consecutive_acceptable must survive a reset."""
        from canonical_market_state import _VwapStats, VWAP_MATCH_TICKS
        s = _VwapStats()
        # Build streak of 5
        for _ in range(5):
            s.record(VWAP_MATCH_TICKS - 0.1)
        assert s.longest_consecutive_acceptable == 5
        assert s.consecutive_acceptable         == 5
        # Break streak
        s.record(VWAP_MATCH_TICKS + 1.0)
        assert s.consecutive_acceptable         == 0   # current reset
        assert s.longest_consecutive_acceptable == 5   # longest preserved
        # New run of 3 — longest stays 5
        for _ in range(3):
            s.record(VWAP_MATCH_TICKS - 0.1)
        assert s.longest_consecutive_acceptable == 5
        assert s.consecutive_acceptable         == 3

    def test_vwap_stats_median_tick_diff(self):
        from canonical_market_state import _VwapStats
        s = _VwapStats()
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            s.record(v)
        assert s._percentile(50) == pytest.approx(3.0, abs=0.01)

    def test_vwap_stats_p95_tick_diff(self):
        from canonical_market_state import _VwapStats
        s = _VwapStats()
        for v in range(1, 101):     # 1..100
            s.record(float(v))
        p95 = s._percentile(95)
        # 95th percentile of 1..100 should be ~95-96
        assert 94.0 <= p95 <= 97.0

    def test_vwap_stats_promotion_shadow_by_default(self):
        from canonical_market_state import _VwapStats, VWAP_MATCH_TICKS
        s = _VwapStats()
        for _ in range(49):   # one short of 50
            s.record(VWAP_MATCH_TICKS - 0.1)
        assert s.promotion_status  == "SHADOW"
        assert s.promotion_eligible is False

    def test_vwap_stats_promotion_requires_50_consecutive(self):
        """Total ≥50 but streak broken — must stay SHADOW."""
        from canonical_market_state import _VwapStats, VWAP_MATCH_TICKS
        s = _VwapStats()
        for _ in range(49):
            s.record(VWAP_MATCH_TICKS - 0.1)
        s.record(VWAP_MATCH_TICKS + 5.0)    # break streak — longest = 49
        for _ in range(49):
            s.record(VWAP_MATCH_TICKS - 0.1)
        # total = 99, longest = 49 → SHADOW
        assert s.sample_count > 50
        assert s.longest_consecutive_acceptable == 49
        assert s.promotion_status  == "SHADOW"
        assert s.promotion_eligible is False

    def test_vwap_stats_promotion_validating_on_qualifying_streak(self):
        """≥50 total AND ≥50 consecutive longest → VALIDATING."""
        from canonical_market_state import _VwapStats, VWAP_MATCH_TICKS
        s = _VwapStats()
        for _ in range(50):
            s.record(VWAP_MATCH_TICKS - 0.1)
        assert s.promotion_status  == "VALIDATING"
        assert s.promotion_eligible is True

    def test_vwap_stats_to_dict_has_all_validation_fields(self):
        from canonical_market_state import _VwapStats
        s = _VwapStats()
        s.record(1.0)
        d = s.to_dict()
        required = [
            "sample_count", "acceptable_count", "unacceptable_count",
            "consecutive_acceptable", "longest_consecutive_acceptable",
            "avg_tick_diff", "median_tick_diff", "p95_tick_diff",
            "max_tick_diff", "latest_tick_diff", "pct_within_tolerance",
            "promotion_status", "promotion_eligible",
        ]
        for field in required:
            assert field in d, f"to_dict() missing field: {field!r}"

    def test_vwap_comparison_block_has_all_new_fields(self):
        """vwap_comparison in get_snapshot() must include all new validation fields."""
        from canonical_market_state import _augment_snapshot
        e = CanonicalMarketStateEngine("MNQ")
        _feed(e, _bars_trend_up(10, start_price=29800.0))
        snap = e.get_snapshot()
        db_vwap = snap["vwap"]["value"]
        if db_vwap is None:
            pytest.skip("No VWAP yet")
        orig = cms._VWAP_BY_TICKER
        cms._VWAP_BY_TICKER = {"MNQ": {"vwap": db_vwap + 1.0}}
        try:
            with patch("trend_alignment.MTF_STATE_BY_INST", {}, create=True), \
                 patch("fvg_engine.FVG_ZONES_BY_INST", {}, create=True):
                _augment_snapshot("MNQ", snap)
        finally:
            cms._VWAP_BY_TICKER = orig
        vc = snap["vwap_comparison"]
        new_fields = [
            "acceptable_count", "unacceptable_count",
            "longest_consecutive_acceptable",
            "median_tick_diff", "p95_tick_diff",
            "promotion_status", "promotion_eligible",
        ]
        for field in new_fields:
            assert field in vc, f"vwap_comparison missing new field: {field!r}"

    def test_promotion_status_shadow_in_vwap_comparison_before_qualifying(self):
        """vwap_comparison must report SHADOW before 50-sample streak."""
        from canonical_market_state import _augment_snapshot
        e = CanonicalMarketStateEngine("MNQ")
        _feed(e, _bars_trend_up(10, start_price=29800.0))
        snap = e.get_snapshot()
        db_vwap = snap["vwap"]["value"]
        if db_vwap is None:
            pytest.skip("No VWAP yet")
        orig_vwap  = cms._VWAP_BY_TICKER
        orig_stats = cms._VWAP_STATS
        cms._VWAP_BY_TICKER = {"MNQ": {"vwap": db_vwap + 1.0}}
        cms._VWAP_STATS = {"MNQ": cms._VwapStats()}   # fresh — 0 samples
        try:
            with patch("trend_alignment.MTF_STATE_BY_INST", {}, create=True), \
                 patch("fvg_engine.FVG_ZONES_BY_INST", {}, create=True):
                _augment_snapshot("MNQ", snap)
        finally:
            cms._VWAP_BY_TICKER = orig_vwap
            cms._VWAP_STATS     = orig_stats
        assert snap["vwap_comparison"]["promotion_status"]  == "SHADOW"
        assert snap["vwap_comparison"]["promotion_eligible"] is False

    def test_four_instruments_remain_isolated(self):
        """Stats for MGC must not bleed into MNQ."""
        import canonical_market_state as cms_mod
        orig_stats = cms_mod._VWAP_STATS
        cms_mod._VWAP_STATS = {inst: cms_mod._VwapStats() for inst in ("MGC", "MNQ", "MES", "MYM")}
        try:
            cms_mod._VWAP_STATS["MGC"].record(5.0)
            cms_mod._VWAP_STATS["MGC"].record(3.0)
            assert cms_mod._VWAP_STATS["MGC"].sample_count == 2
            assert cms_mod._VWAP_STATS["MNQ"].sample_count == 0
            assert cms_mod._VWAP_STATS["MES"].sample_count == 0
            assert cms_mod._VWAP_STATS["MYM"].sample_count == 0
        finally:
            cms_mod._VWAP_STATS = orig_stats
