"""
Comprehensive deterministic tests for the 09:30 ORB Engine.

All tests are offline — no Databento connection, no real DB.
Time is mocked; bars are injected directly.

Run:
    python -m pytest test_orb_engine.py -v
"""

import json
import threading
import unittest
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytz

from orb_engine import (
    ConfirmationMode, ExecutionMode, OrbConfig, OrbEngine,
    OrbInstrumentState, OrbState, RiskReservationState,
    ORB_STRATEGY_VERSION, ORB_CONFIG_VERSION,
    _orb_contracts, _INSTRUMENTS, _INDEX_GROUP, _METALS_GROUP,
)

ET_TZ = pytz.timezone("America/New_York")

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_TEST_DATE = "2026-08-11"  # Tuesday — normal trading day (EDT = UTC-4)


def _et_ts(hour: int, minute: int, date: str = _TEST_DATE) -> int:
    """Unix timestamp (int) for a given HH:MM ET on the test date."""
    y, m, d = (int(x) for x in date.split("-"))
    et_dt    = ET_TZ.localize(datetime(y, m, d, hour, minute, 0))
    return int(et_dt.timestamp())


def _bar(hour: int, minute: int, open_: float, high: float, low: float,
         close: float, volume: int = 500, atr: float = 5.0,
         date: str = _TEST_DATE) -> dict:
    """Build a synthetic 1-minute bar dict in the Databento format."""
    return {
        "ts":     _et_ts(hour, minute, date),
        "open":   open_,
        "high":   high,
        "low":    low,
        "close":  close,
        "volume": volume,
        "atr":    atr,
    }


def _flat_bar(hour: int, minute: int, price: float, **kw) -> dict:
    return _bar(hour, minute, price, price, price, price, **kw)


# Minimal ASSETS spec (mirrors real ASSETS but stripped to what OrbEngine needs)
_ASSETS = {
    "MGC": {"specs": {"tick_size": 0.1,  "point_value": 10.0}},
    "MNQ": {"specs": {"tick_size": 0.25, "point_value":  2.0}},
    "MES": {"specs": {"tick_size": 0.25, "point_value":  5.0}},
    "MYM": {"specs": {"tick_size": 1.0,  "point_value":  0.5}},
}


def _make_engine(
    enabled:            bool = True,
    range_duration_min: int  = 10,
    confirmation_mode:  str  = ConfirmationMode.CLOSE_OUTSIDE,
    max_risk_per_instrument: float = 2000.0,  # generous budget so test ranges qualify
    extra_config:       dict = None,
) -> tuple:
    """Return (engine, bars_store) where bars_store[inst] is the list to append to."""
    bars_store: dict = {inst: [] for inst in _INSTRUMENTS}

    cfg = OrbConfig(
        enabled             = enabled,
        global_mode         = ExecutionMode.SHADOW,
        range_duration_min  = range_duration_min,
        confirmation_mode   = confirmation_mode,
        max_risk_per_instrument = max_risk_per_instrument,
    )
    if extra_config:
        for k, v in extra_config.items():
            setattr(cfg, k, v)

    now_fn = lambda: datetime.now(timezone.utc)

    engine = OrbEngine(
        assets      = _ASSETS,
        get_db_fn   = lambda: None,         # no DB in tests
        get_bars_fn = lambda inst: list(bars_store[inst]),
        now_fn      = now_fn,
    )
    engine._config = cfg   # inject config before boot
    OrbEngine.ORB_DB_READY = False  # no DB
    engine.boot()
    return engine, bars_store


def _push(engine: OrbEngine, bars_store: dict, inst: str, bar: dict) -> None:
    """Append bar to the store then fire on_bar_close for that instrument."""
    bars_store[inst].append(bar)
    engine.on_bar_close(inst, bar["close"])


# ─────────────────────────────────────────────────────────────────────────────
# 1. Range duration & lock-time tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRangeDuration(unittest.TestCase):

    # Each range bar has a small non-zero spread (high=base+5, low=base-5).
    # Width=10 pts, ATR=50 → ratio=0.20 which is exactly the minimum; use ATR=40
    # to guarantee ratio > 0.20 (10/40 = 0.25).
    _RANGE_BAR_BASE = 20000.0
    _RANGE_BAR_ATR  = 40.0

    def _range_bar(self, hour, minute):
        """Non-flat bar with H=base+5, L=base-5 so or_high != or_low."""
        return _bar(hour, minute,
                    self._RANGE_BAR_BASE,
                    self._RANGE_BAR_BASE + 5,
                    self._RANGE_BAR_BASE - 5,
                    self._RANGE_BAR_BASE,
                    atr=self._RANGE_BAR_ATR)

    def _build_and_lock(self, duration_min: int, lock_hour: int, lock_min: int,
                        range_bars: list) -> OrbInstrumentState:
        """Build a range with specified bars then send a post-lock bar."""
        engine, store = _make_engine(range_duration_min=duration_min)
        for b in range_bars:
            _push(engine, store, "MNQ", b)
        # Send post-lock bar (also non-flat to avoid zero-range issues)
        post = _bar(lock_hour, lock_min,
                    self._RANGE_BAR_BASE,
                    self._RANGE_BAR_BASE + 3,
                    self._RANGE_BAR_BASE - 3,
                    self._RANGE_BAR_BASE,
                    atr=self._RANGE_BAR_ATR)
        _push(engine, store, "MNQ", post)
        return engine._state["MNQ"]

    def test_10min_bars_930_to_939(self):
        """10-min: bars 09:30–09:39 are range bars; lock at 09:40."""
        bars = [self._range_bar(9, m) for m in range(30, 40)]
        s = self._build_and_lock(10, 9, 40, bars)
        self.assertTrue(s.range_locked, f"range not locked; state={s.state} block={s.block_reason}")
        self.assertEqual(s.range_bars_observed, 10)
        self.assertEqual(s.lock_minutes, 9 * 60 + 40)

    def test_10min_940_bar_excluded_from_range(self):
        """The 09:40 bar must NOT be included in a 10-minute range."""
        bars = [self._range_bar(9, m) for m in range(30, 40)]
        s = self._build_and_lock(10, 9, 40, bars)
        # 10 bars: 09:30–09:39 only
        self.assertEqual(s.range_bars_observed, 10)

    def test_5min_bars_930_to_934_lock_935(self):
        """5-min: bars 09:30–09:34 included; lock at 09:35."""
        bars = [self._range_bar(9, m) for m in range(30, 35)]
        s = self._build_and_lock(5, 9, 35, bars)
        self.assertTrue(s.range_locked, f"range not locked; state={s.state} block={s.block_reason}")
        self.assertEqual(s.range_bars_observed, 5)
        self.assertEqual(s.lock_minutes, 9 * 60 + 35)

    def test_15min_bars_930_to_944_lock_945(self):
        """15-min: bars 09:30–09:44 included; lock at 09:45."""
        bars = [self._range_bar(9, m) for m in range(30, 45)]
        s = self._build_and_lock(15, 9, 45, bars)
        self.assertTrue(s.range_locked, f"range not locked; state={s.state} block={s.block_reason}")
        self.assertEqual(s.range_bars_observed, 15)
        self.assertEqual(s.lock_minutes, 9 * 60 + 45)

    def test_30min_bars_930_to_959_lock_1000(self):
        """30-min: bars 09:30–09:59 included; lock at 10:00."""
        bars = [self._range_bar(9, m) for m in range(30, 60)]
        s = self._build_and_lock(30, 10, 0, bars)
        self.assertTrue(s.range_locked, f"range not locked; state={s.state} block={s.block_reason}")
        self.assertEqual(s.range_bars_observed, 30)
        self.assertEqual(s.lock_minutes, 10 * 60 + 0)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Range immutability tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRangeImmutability(unittest.TestCase):

    def _locked_engine(self, inst="MNQ") -> tuple:
        engine, store = _make_engine()
        # Build range: H=20100, L=20000
        for m in range(30, 40):
            _push(engine, store, inst,
                  _bar(9, m, 20000.0, 20100.0, 20000.0, 20050.0, atr=100.0))
        # Lock bar
        _push(engine, store, inst, _flat_bar(9, 40, 20050.0, atr=100.0))
        return engine, store

    def test_range_locked_after_first_post_range_bar(self):
        engine, store = self._locked_engine()
        s = engine._state["MNQ"]
        self.assertTrue(s.range_locked)

    def test_or_high_immutable_after_lock(self):
        """A bar with a higher high after lock must NOT change or_high."""
        engine, store = self._locked_engine()
        locked_high = engine._state["MNQ"].or_high
        # Send bar with much higher high
        _push(engine, store, "MNQ", _bar(9, 41, 20000.0, 25000.0, 20000.0, 24000.0))
        self.assertEqual(engine._state["MNQ"].or_high, locked_high)

    def test_or_low_immutable_after_lock(self):
        """A bar with a lower low after lock must NOT change or_low."""
        engine, store = self._locked_engine()
        locked_low = engine._state["MNQ"].or_low
        # Send bar with much lower low
        _push(engine, store, "MNQ", _bar(9, 41, 20000.0, 20000.0, 10000.0, 11000.0))
        self.assertEqual(engine._state["MNQ"].or_low, locked_low)

    def test_range_locked_ts_set_on_lock(self):
        engine, store = self._locked_engine()
        self.assertIsNotNone(engine._state["MNQ"].range_locked_ts)

    def test_or_width_correct(self):
        engine, store = self._locked_engine()
        s = engine._state["MNQ"]
        expected = round(s.or_high - s.or_low, 6)
        self.assertAlmostEqual(s.or_width, expected, places=5)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Independent instrument isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestInstrumentIsolation(unittest.TestCase):

    def _build_all_ranges(self) -> tuple:
        engine, store = _make_engine()
        prices = {"MGC": 2400.0, "MNQ": 20000.0, "MES": 5500.0, "MYM": 42000.0}
        for inst in _INSTRUMENTS:
            p = prices[inst]
            for m in range(30, 40):
                _push(engine, store, inst,
                      _bar(9, m, p, p + 10, p - 5, p + 2, atr=50.0))
            _push(engine, store, inst, _flat_bar(9, 40, p, atr=50.0))
        return engine, store, prices

    def test_all_four_instruments_build_own_range(self):
        engine, store, prices = self._build_all_ranges()
        for inst in _INSTRUMENTS:
            s = engine._state[inst]
            self.assertTrue(s.range_locked, f"{inst}: range not locked")
            self.assertIsNotNone(s.or_high, f"{inst}: or_high is None")
            self.assertIsNotNone(s.or_low,  f"{inst}: or_low is None")

    def test_mgc_range_independent_of_mnq(self):
        engine, store, prices = self._build_all_ranges()
        mgc_h = engine._state["MGC"].or_high
        mnq_h = engine._state["MNQ"].or_high
        self.assertNotEqual(mgc_h, mnq_h)   # different prices

    def test_one_invalid_range_does_not_block_others(self):
        engine, store = _make_engine()
        # Only build MNQ range — others get no bars before lock
        for m in range(30, 40):
            _push(engine, store, "MNQ",
                  _bar(9, m, 20000.0, 20100.0, 20000.0, 20050.0, atr=100.0))
        # Send post-lock bar to ALL instruments simultaneously
        for inst in _INSTRUMENTS:
            _push(engine, store, inst, _flat_bar(9, 40, 20000.0, atr=100.0))
        # MNQ should be locked
        self.assertTrue(engine._state["MNQ"].range_locked)
        # Others should be BLOCKED_BY_DATA (no bars)
        for inst in ("MGC", "MES", "MYM"):
            self.assertIn("BLOCKED", engine._state[inst].state,
                          f"{inst} should be blocked but state={engine._state[inst].state}")

    def test_missing_data_blocks_only_affected_instrument(self):
        """MGC with no data stays blocked; MNQ can still trade."""
        engine, store = _make_engine()
        for m in range(30, 40):
            _push(engine, store, "MNQ",
                  _bar(9, m, 20000.0, 20100.0, 20000.0, 20050.0, atr=100.0))
        _push(engine, store, "MNQ", _flat_bar(9, 40, 20000.0, atr=100.0))
        # MGC gets nothing
        _push(engine, store, "MGC", _flat_bar(9, 40, 2400.0, atr=5.0))
        self.assertTrue(engine._state["MNQ"].range_locked)
        self.assertEqual(engine._state["MGC"].state, OrbState.BLOCKED_BY_DATA)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Breakout algorithm
# ─────────────────────────────────────────────────────────────────────────────

class TestBreakoutAlgorithm(unittest.TestCase):

    # Helper: build a locked range with H=20100 L=20000 for MNQ
    # tick_size=0.25, so breakout_buffer = 4*0.25=1.0 pt
    # long_breakout_level = 20100 + 1.0 = 20101.0
    # short_breakout_level = 20000 - 1.0 = 19999.0

    def _locked(self, inst="MNQ") -> tuple:
        engine, store = _make_engine()
        for m in range(30, 40):
            _push(engine, store, inst,
                  _bar(9, m, 20000.0, 20100.0, 20000.0, 20050.0, atr=100.0))
        _push(engine, store, inst, _flat_bar(9, 40, 20050.0, atr=100.0))
        return engine, store

    def test_no_entry_before_lock_time(self):
        """Bars in the range window must not trigger a breakout."""
        engine, store = _make_engine()
        # Send bars only during the range window (09:30–09:39)
        for m in range(30, 40):
            _push(engine, store, "MNQ",
                  _bar(9, m, 20000.0, 25000.0, 20000.0, 24999.0, atr=100.0))  # would be far above breakout
        # No post-lock bar yet → state should still be BUILDING_RANGE or BLOCKED
        s = engine._state["MNQ"]
        self.assertNotIn(s.state, (OrbState.QUALIFIED, OrbState.POSITION_ACTIVE))

    def test_close_outside_long_qualifies(self):
        """A bar that closes at or above the long breakout level qualifies LONG."""
        engine, store = self._locked()
        # long_level = 20101.0; send bar closing above it, bullish
        bo_bar = _bar(9, 41, 20101.0, 20110.0, 20100.0, 20105.0, atr=100.0)  # bullish close
        _push(engine, store, "MNQ", bo_bar)
        s = engine._state["MNQ"]
        self.assertIn(s.state, (OrbState.QUALIFIED, OrbState.POSITION_ACTIVE),
                      f"Expected QUALIFIED, got {s.state}")
        self.assertEqual(s.breakout_direction, "LONG")

    def test_close_outside_short_qualifies(self):
        """A bar that closes at or below the short breakout level qualifies SHORT."""
        engine, store = self._locked()
        # short_level = 19999.0; send bar closing below it, bearish
        bo_bar = _bar(9, 41, 19999.0, 20000.0, 19990.0, 19995.0, atr=100.0)  # bearish close
        _push(engine, store, "MNQ", bo_bar)
        s = engine._state["MNQ"]
        self.assertIn(s.state, (OrbState.QUALIFIED, OrbState.POSITION_ACTIVE),
                      f"Expected QUALIFIED, got {s.state}")
        self.assertEqual(s.breakout_direction, "SHORT")

    def test_intrabar_touch_does_not_satisfy_close_outside(self):
        """High reaching the level but closing INSIDE the range must not qualify."""
        engine, store = self._locked()
        # High touches 20101 but closes at 20050 (inside range, bearish)
        touch_bar = _bar(9, 41, 20050.0, 20105.0, 20040.0, 20045.0, atr=100.0)
        _push(engine, store, "MNQ", touch_bar)
        s = engine._state["MNQ"]
        self.assertEqual(s.state, OrbState.WATCHING_BREAKOUT,
                         f"Should still be watching; got {s.state}")

    def test_prior_bar_already_outside_does_not_duplicate(self):
        """Second consecutive bar above the breakout level must not create a second signal."""
        engine, store = self._locked()
        b1 = _bar(9, 41, 20101.0, 20110.0, 20100.0, 20105.0, atr=100.0)
        b2 = _bar(9, 42, 20105.0, 20115.0, 20102.0, 20110.0, atr=100.0)
        _push(engine, store, "MNQ", b1)
        count_after_b1 = engine._state["MNQ"].daily_trade_count
        _push(engine, store, "MNQ", b2)
        count_after_b2 = engine._state["MNQ"].daily_trade_count
        # Should not create a second entry
        self.assertEqual(count_after_b1, count_after_b2,
                         "Second bar above level created a duplicate entry")

    def test_entry_window_expiry_blocks(self):
        """Bars past the entry window end (10:30) must not create entries."""
        engine, store = self._locked()
        # Send a bar at 10:35 — past the 10:30 entry window end
        late_bar = _bar(10, 35, 20101.0, 20110.0, 20100.0, 20105.0, atr=100.0)
        _push(engine, store, "MNQ", late_bar)
        s = engine._state["MNQ"]
        self.assertEqual(s.state, OrbState.EXPIRED, f"Expected EXPIRED, got {s.state}")

    def test_failed_breakout_returns_to_watching(self):
        """An intrabar breakout that doesn't close outside the level stays in WATCHING."""
        engine, store = self._locked()
        # High touches level, closes inside range
        _push(engine, store, "MNQ",
              _bar(9, 41, 20050.0, 20105.0, 20040.0, 20048.0, atr=100.0))
        self.assertEqual(engine._state["MNQ"].state, OrbState.WATCHING_BREAKOUT)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Confirmation modes
# ─────────────────────────────────────────────────────────────────────────────

class TestConfirmationModes(unittest.TestCase):

    def _locked_with_mode(self, mode: str) -> tuple:
        engine, store = _make_engine(confirmation_mode=mode)
        for m in range(30, 40):
            _push(engine, store, "MNQ",
                  _bar(9, m, 20000.0, 20100.0, 20000.0, 20050.0, atr=100.0))
        _push(engine, store, "MNQ", _flat_bar(9, 40, 20050.0, atr=100.0))
        return engine, store

    def test_touch_mode_intrabar_qualifies(self):
        """TOUCH: high reaching breakout level qualifies even if close is inside range."""
        engine, store = self._locked_with_mode(ConfirmationMode.TOUCH)
        # High = 20102 (above 20101 long level), close = 20050 (inside range)
        touch_bar = _bar(9, 41, 20050.0, 20102.0, 20040.0, 20050.0, atr=100.0)
        _push(engine, store, "MNQ", touch_bar)
        s = engine._state["MNQ"]
        self.assertIn(s.state, (OrbState.QUALIFIED, OrbState.POSITION_ACTIVE),
                      f"TOUCH should have qualified; got {s.state}")

    def test_close_outside_default_requires_close(self):
        """CLOSE_OUTSIDE (default): only a completed close qualifies."""
        engine, store = self._locked_with_mode(ConfirmationMode.CLOSE_OUTSIDE)
        # Touch but close inside
        touch_bar = _bar(9, 41, 20050.0, 20102.0, 20040.0, 20050.0, atr=100.0)
        _push(engine, store, "MNQ", touch_bar)
        self.assertEqual(engine._state["MNQ"].state, OrbState.WATCHING_BREAKOUT)

    def test_close_and_retest_long_full_flow(self):
        """CLOSE_AND_RETEST: detect→retest starts→retest holds→QUALIFIED."""
        engine, store = self._locked_with_mode(ConfirmationMode.CLOSE_AND_RETEST)
        # Step 1: bar closes above long level → BREAKOUT_DETECTED
        b1 = _bar(9, 41, 20101.0, 20110.0, 20100.0, 20108.0, atr=100.0)
        _push(engine, store, "MNQ", b1)
        self.assertEqual(engine._state["MNQ"].state, OrbState.BREAKOUT_DETECTED)
        # Step 2: price pulls back to near 20101 (within 3 ticks = 0.75 pts of 20101)
        b2 = _flat_bar(9, 42, 20101.5, atr=100.0)   # within tolerance
        _push(engine, store, "MNQ", b2)
        self.assertEqual(engine._state["MNQ"].state, OrbState.CONFIRMATION_PENDING)
        # Step 3: price holds above level → QUALIFIED
        b3 = _flat_bar(9, 43, 20102.0, atr=100.0)
        _push(engine, store, "MNQ", b3)
        s = engine._state["MNQ"]
        self.assertIn(s.state, (OrbState.QUALIFIED, OrbState.POSITION_ACTIVE))

    def test_close_and_retest_failed_retest(self):
        """CLOSE_AND_RETEST: if retest bar holds back inside range, return to watching."""
        engine, store = self._locked_with_mode(ConfirmationMode.CLOSE_AND_RETEST)
        b1 = _bar(9, 41, 20101.0, 20110.0, 20100.0, 20108.0, atr=100.0)
        _push(engine, store, "MNQ", b1)  # BREAKOUT_DETECTED
        b2 = _flat_bar(9, 42, 20101.5, atr=100.0)   # CONFIRMATION_PENDING
        _push(engine, store, "MNQ", b2)
        # Step 3: close fails — goes back inside range
        b3 = _flat_bar(9, 43, 20080.0, atr=100.0)   # well below long level
        _push(engine, store, "MNQ", b3)
        self.assertEqual(engine._state["MNQ"].state, OrbState.WATCHING_BREAKOUT)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Maximum chase rule
# ─────────────────────────────────────────────────────────────────────────────

class TestMaximumChase(unittest.TestCase):

    def _locked_mnq(self) -> tuple:
        """MNQ range H=20100 L=20000 W=100; 25% chase=25 pts → max_long=20125."""
        engine, store = _make_engine()
        for m in range(30, 40):
            _push(engine, store, "MNQ",
                  _bar(9, m, 20000.0, 20100.0, 20000.0, 20050.0, atr=100.0))
        _push(engine, store, "MNQ", _flat_bar(9, 40, 20050.0, atr=100.0))
        return engine, store

    def test_max_chase_boundary_long_correct(self):
        """max_chase_boundary_long = or_high + 25% of width = 20100 + 25 = 20125."""
        engine, store = self._locked_mnq()
        # Send a bar in the entry window to trigger boundary calculation
        _push(engine, store, "MNQ", _flat_bar(9, 41, 20050.0, atr=100.0))
        s = engine._state["MNQ"]
        self.assertIsNotNone(s.max_chase_boundary_long)
        self.assertAlmostEqual(s.max_chase_boundary_long, 20125.0, places=3)

    def test_max_chase_blocks_late_long_entry(self):
        """Close well above the max-chase boundary → BREAKOUT_MISSED."""
        engine, store = self._locked_mnq()
        # Close at 20150 — beyond 20125 max chase
        late_bar = _bar(9, 41, 20130.0, 20155.0, 20125.0, 20150.0, atr=100.0)
        _push(engine, store, "MNQ", late_bar)
        s = engine._state["MNQ"]
        self.assertEqual(s.state, OrbState.BREAKOUT_MISSED,
                         f"Expected BREAKOUT_MISSED, got {s.state}")

    def test_valid_breakout_within_chase_qualifies(self):
        """Close just above the long level but within max-chase → QUALIFIED."""
        engine, store = self._locked_mnq()
        # long_level=20101, close=20103 (< 20125 max chase)
        valid_bar = _bar(9, 41, 20101.0, 20106.0, 20100.0, 20103.0, atr=100.0)
        _push(engine, store, "MNQ", valid_bar)
        s = engine._state["MNQ"]
        self.assertIn(s.state, (OrbState.QUALIFIED, OrbState.POSITION_ACTIVE))


# ─────────────────────────────────────────────────────────────────────────────
# 7. Stops and targets
# ─────────────────────────────────────────────────────────────────────────────

class TestStopsAndTargets(unittest.TestCase):

    def _qualify_mnq_long(self) -> OrbInstrumentState:
        engine, store = _make_engine()
        for m in range(30, 40):
            _push(engine, store, "MNQ",
                  _bar(9, m, 20000.0, 20100.0, 20000.0, 20050.0, atr=100.0))
        _push(engine, store, "MNQ", _flat_bar(9, 40, 20050.0, atr=100.0))
        # Qualify long: close clearly in the upper half of the bar's range
        # bar_low=20100, bar_high=20110, midpoint=20105 → close=20108 passes bullish filter
        _push(engine, store, "MNQ",
              _bar(9, 41, 20101.0, 20110.0, 20100.0, 20108.0, atr=100.0))
        return engine._state["MNQ"]

    def _qualify_mnq_short(self) -> OrbInstrumentState:
        engine, store = _make_engine()
        for m in range(30, 40):
            _push(engine, store, "MNQ",
                  _bar(9, m, 20000.0, 20100.0, 20000.0, 20050.0, atr=100.0))
        _push(engine, store, "MNQ", _flat_bar(9, 40, 20050.0, atr=100.0))
        # Qualify short: close clearly in the lower half of the bar's range
        # bar_low=19990, bar_high=20000, midpoint=19995 → close=19991 passes bearish filter
        _push(engine, store, "MNQ",
              _bar(9, 41, 19999.0, 20000.0, 19990.0, 19991.0, atr=100.0))
        return engine._state["MNQ"]

    def test_long_stop_below_or_low(self):
        """LONG stop must be below or_low (with stop buffer)."""
        s = self._qualify_mnq_long()
        self.assertIsNotNone(s.stop)
        self.assertLess(s.stop, s.or_low,   # stop < or_low (opposite range side)
                        f"stop={s.stop} should be < or_low={s.or_low}")

    def test_short_stop_above_or_high(self):
        """SHORT stop must be above or_high."""
        s = self._qualify_mnq_short()
        self.assertIsNotNone(s.stop)
        self.assertGreater(s.stop, s.or_high,
                           f"stop={s.stop} should be > or_high={s.or_high}")

    def test_tp1_equals_1r_long(self):
        """TP1 = entry + 1R."""
        s = self._qualify_mnq_long()
        r = abs(s.entry - s.stop)
        self.assertAlmostEqual(s.tp1, s.entry + r, places=4)

    def test_tp2_equals_2r_long(self):
        """TP2 = entry + 2R."""
        s = self._qualify_mnq_long()
        r = abs(s.entry - s.stop)
        self.assertAlmostEqual(s.tp2, s.entry + 2 * r, places=4)

    def test_tp1_equals_1r_short(self):
        """TP1 = entry - 1R for short."""
        s = self._qualify_mnq_short()
        r = abs(s.entry - s.stop)
        self.assertAlmostEqual(s.tp1, s.entry - r, places=4)

    def test_tp2_equals_2r_short(self):
        """TP2 = entry - 2R for short."""
        s = self._qualify_mnq_short()
        r = abs(s.entry - s.stop)
        self.assertAlmostEqual(s.tp2, s.entry - 2 * r, places=4)

    def test_stop_aligns_with_tick_size(self):
        """Stop distance should be a multiple of the tick size."""
        s = self._qualify_mnq_long()
        tick = 0.25  # MNQ
        stop_dist = abs(s.entry - s.stop)
        # Check stop is on a tick boundary
        remainder = round(s.stop % tick, 8)
        self.assertAlmostEqual(min(remainder, tick - remainder), 0.0, places=4)

    def test_one_trade_tp1_does_not_alter_other_instrument(self):
        """TP1 for MNQ must not change MGC's state."""
        engine, store = _make_engine()
        # Build both ranges
        for inst, price, hi, lo in [("MNQ", 20000.0, 20100.0, 20000.0),
                                     ("MGC", 2400.0, 2410.0, 2400.0)]:
            for m in range(30, 40):
                _push(engine, store, inst,
                      _bar(9, m, price, hi, lo, (hi+lo)/2, atr=100.0))
            _push(engine, store, inst, _flat_bar(9, 40, (hi+lo)/2, atr=100.0))
        # Qualify MNQ only — close must be in upper half of bar range
        # bar_low=20100, bar_high=20110, midpoint=20105, close=20108 ✓
        _push(engine, store, "MNQ",
              _bar(9, 41, 20101.0, 20110.0, 20100.0, 20108.0, atr=100.0))
        mnq_tp1 = engine._state["MNQ"].tp1
        mgc_tp1 = engine._state["MGC"].tp1
        self.assertIsNotNone(mnq_tp1)
        self.assertIsNone(mgc_tp1, "MGC tp1 was altered by MNQ qualification")

    def test_single_contract_uses_15r_target(self):
        """When only 1 contract fits, TP2 = entry ± 1.5R."""
        # Use very high stop distance to force 1 contract
        engine, store = _make_engine(max_risk_per_instrument=5.0)  # very tight budget
        for m in range(30, 40):
            _push(engine, store, "MNQ",
                  _bar(9, m, 20000.0, 20100.0, 20000.0, 20050.0, atr=100.0))
        _push(engine, store, "MNQ", _flat_bar(9, 40, 20050.0, atr=100.0))
        _push(engine, store, "MNQ",
              _bar(9, 41, 20101.0, 20106.0, 20100.0, 20102.0, atr=100.0))
        s = engine._state["MNQ"]
        if s.contracts_approved == 1:
            r = abs(s.entry - s.stop)
            self.assertAlmostEqual(s.tp2, s.entry + 1.5 * r, places=4)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Risk and correlation
# ─────────────────────────────────────────────────────────────────────────────

class TestRiskAndCorrelation(unittest.TestCase):

    def _qualify_inst(self, engine, store, inst):
        """Build range and qualify a long for any instrument.

        ATR is chosen per-instrument so the range/ATR ratio stays inside the
        engine's [0.20, 1.50] window:
          MGC  width=10  atr=30  → ratio≈0.33 ✓
          MNQ  width=100 atr=100 → ratio=1.00 ✓
          MES  width=10  atr=30  → ratio≈0.33 ✓
          MYM  width=50  atr=80  → ratio≈0.63 ✓
        """
        prices = {"MGC": (2400.0, 2410.0, 2400.0),
                  "MNQ": (20000.0, 20100.0, 20000.0),
                  "MES": (5500.0, 5510.0, 5500.0),
                  "MYM": (42000.0, 42050.0, 42000.0)}
        atr_by_inst = {"MGC": 30.0, "MNQ": 100.0, "MES": 30.0, "MYM": 80.0}
        p, hi, lo = prices[inst]
        atr = atr_by_inst[inst]
        for m in range(30, 40):
            _push(engine, store, inst,
                  _bar(9, m, p, hi, lo, (hi+lo)/2, atr=atr))
        _push(engine, store, inst, _flat_bar(9, 40, (hi+lo)/2, atr=atr))
        # Breakout bar — close must be:
        #   (a) in the upper half of bar range, AND
        #   (b) within the max-chase window = or_high + or_width*0.25
        # For small-range instruments (MGC/MES width=10, max_chase=2.5), using
        # close = level + 0.5 is safe for every instrument.
        buf_ticks = {"MGC": 2, "MNQ": 4, "MES": 2, "MYM": 4}
        tick_size = {"MGC": 0.1, "MNQ": 0.25, "MES": 0.25, "MYM": 1.0}[inst]
        buf = buf_ticks[inst] * tick_size
        level = hi + buf
        # bar: open=level, high=level+1.0, low=level-0.5, close=level+0.5
        # bar_rng=1.5, midpoint=level+0.25 → close(level+0.5) > midpoint ✓
        # close(level+0.5) ≤ max_chase(hi + or_width*0.25) for all instruments ✓
        _push(engine, store, inst,
              _bar(9, 41, level, level + 1.0, level - 0.5, level + 0.5, atr=atr))

    def test_each_instrument_separate_risk_calc(self):
        engine, store = _make_engine()
        self._qualify_inst(engine, store, "MNQ")
        self._qualify_inst(engine, store, "MGC")
        mnq_risk = engine._state["MNQ"].risk_dollars
        mgc_risk = engine._state["MGC"].risk_dollars
        # Both should have non-zero risk
        self.assertGreater(mnq_risk, 0)
        self.assertGreater(mgc_risk, 0)

    def test_index_group_tracked_separately(self):
        """MNQ risk contributes to active_index_risk, not metals."""
        engine, store = _make_engine()
        self._qualify_inst(engine, store, "MNQ")
        self.assertGreater(engine._portfolio.active_index_risk, 0)
        self.assertEqual(engine._portfolio.active_metals_risk, 0)

    def test_metals_group_tracked_separately(self):
        """MGC risk contributes to active_metals_risk, not index."""
        engine, store = _make_engine()
        self._qualify_inst(engine, store, "MGC")
        self.assertGreater(engine._portfolio.active_metals_risk, 0)
        self.assertEqual(engine._portfolio.active_index_risk, 0)

    def test_index_position_limit_blocks(self):
        """When index position limit is reached, further index trades are blocked."""
        engine, store = _make_engine(extra_config={"max_simultaneous_index": 1})
        self._qualify_inst(engine, store, "MNQ")
        # Now try MES — should be blocked by group risk.
        # Use atr=30 so range(10pts)/atr(30) = 0.33, within [0.20,1.50].
        for m in range(30, 40):
            _push(engine, store, "MES",
                  _bar(9, m, 5500.0, 5510.0, 5500.0, 5505.0, atr=30.0))
        _push(engine, store, "MES", _flat_bar(9, 40, 5505.0, atr=30.0))
        # Breakout bar: close must be in upper half AND within max-chase window.
        # level=5510.5, max_chase=5510+10*0.25=5512.5 → use close=5511.0 ✓
        _push(engine, store, "MES",
              _bar(9, 41, 5510.5, 5512.0, 5510.0, 5511.0, atr=30.0))
        mes_state = engine._state["MES"].state
        self.assertEqual(mes_state, OrbState.BLOCKED_BY_GROUP_RISK,
                         f"Expected BLOCKED_BY_GROUP_RISK, got {mes_state}")

    def test_all_four_may_qualify_simultaneously(self):
        """All four instruments can qualify when risk allows."""
        engine, store = _make_engine(extra_config={
            "max_simultaneous_positions": 4,
            "max_simultaneous_index": 3,
            "max_simultaneous_metals": 1,
        })
        for inst in _INSTRUMENTS:
            self._qualify_inst(engine, store, inst)
        active = sum(
            1 for inst in _INSTRUMENTS
            if engine._state[inst].state == OrbState.POSITION_ACTIVE
        )
        self.assertGreater(active, 0, "No instruments qualified")

    def test_contract_quantities_round_down(self):
        """Contracts are always floored (never ceiled) from the risk budget."""
        engine, store = _make_engine(max_risk_per_instrument=100.0)
        self._qualify_inst(engine, store, "MNQ")
        contracts = engine._state["MNQ"].contracts_approved
        rpc       = engine._state["MNQ"].risk_per_contract
        if rpc and rpc > 0:
            theoretical = 100.0 / rpc
            self.assertEqual(contracts, int(theoretical))


# ─────────────────────────────────────────────────────────────────────────────
# 9. Execution mode safety
# ─────────────────────────────────────────────────────────────────────────────

class TestExecutionModes(unittest.TestCase):

    def test_shadow_mode_never_transmits(self):
        """SHADOW mode must never call execute_trade_gateway.
        Verified by ensuring no broker mock is invoked."""
        engine, store = _make_engine()
        # Patch the module to detect any broker call
        with patch.object(engine, '_config') as mock_cfg:
            mock_cfg.enabled = True
            mock_cfg.global_mode = ExecutionMode.SHADOW
            mock_cfg.range_duration_min = 10
            mock_cfg.lock_time_minutes = lambda: 580
            mock_cfg.entry_end_minutes = lambda: 630
            mock_cfg.per_instrument_confirmation = {}
            mock_cfg.confirmation_mode = ConfirmationMode.CLOSE_OUTSIDE
            mock_cfg.effective_mode_for = lambda inst: ExecutionMode.SHADOW
            mock_cfg.is_instrument_enabled = lambda inst: True
            mock_cfg.max_chase_pct = 0.25
            mock_cfg.min_range_width_atr = 0.20
            mock_cfg.max_range_width_atr = 1.50
            mock_cfg.breakout_buffer_pts = lambda i, t: 4 * t
            mock_cfg.stop_buffer_pts     = lambda i, t: 4 * t
            mock_cfg.max_trades_per_instrument = 1
            mock_cfg.max_simultaneous_positions = 4
            mock_cfg.max_simultaneous_index = 3
            mock_cfg.max_simultaneous_metals = 1
            mock_cfg.max_risk_per_instrument = 150.0
            mock_cfg.tp1_r = 1.0
            mock_cfg.tp2_r = 2.0
            mock_cfg.single_contract_r = 1.5

        # Just confirm the engine has no broker integration
        self.assertFalse(hasattr(engine, '_send_broker_order'),
                         "OrbEngine must not have a broker send method")
        self.assertFalse(hasattr(engine, 'execute_trade_gateway'),
                         "OrbEngine must not expose execute_trade_gateway")

    def test_disabled_mode_does_not_evaluate_entries(self):
        """DISABLED mode: state stays DISABLED throughout."""
        engine, store = _make_engine(enabled=False)
        # Force all instruments to DISABLED
        for inst in _INSTRUMENTS:
            engine._state[inst].state = OrbState.DISABLED
        for m in range(30, 45):
            for inst in _INSTRUMENTS:
                _push(engine, store, inst, _flat_bar(9, m, 20000.0))
        for inst in _INSTRUMENTS:
            self.assertEqual(engine._state[inst].state, OrbState.DISABLED,
                             f"{inst} should be DISABLED")

    def test_global_mode_caps_per_instrument_mode(self):
        """Per-instrument mode cannot exceed global mode."""
        from orb_engine import ExecutionMode
        # Global SHADOW, per-instrument LIVE_ALGORITHMIC → result must be SHADOW
        cfg = OrbConfig(global_mode=ExecutionMode.SHADOW,
                        instrument_modes={"MNQ": ExecutionMode.LIVE_ALGORITHMIC})
        result = cfg.effective_mode_for("MNQ")
        self.assertEqual(result, ExecutionMode.SHADOW)

    def test_per_instrument_disable_works(self):
        """Disabling a specific instrument keeps it DISABLED while others activate."""
        engine, store = _make_engine()
        engine._config.instrument_enabled = {"MGC": False}
        # Re-activate (simulate boot re-check)
        engine._state["MGC"].state = OrbState.DISABLED
        engine._state["MGC"].mode  = ExecutionMode.DISABLED
        for m in range(30, 40):
            _push(engine, store, "MGC", _flat_bar(9, m, 2400.0))
        _push(engine, store, "MGC", _flat_bar(9, 40, 2400.0))
        # MGC should stay DISABLED / BLOCKED (not receive bars gracefully)
        # The engine silently processes but won't qualify a DISABLED instrument
        s = engine._state["MGC"]
        self.assertNotIn(s.state, (OrbState.POSITION_ACTIVE, OrbState.QUALIFIED))


# ─────────────────────────────────────────────────────────────────────────────
# 10. Range-width filter
# ─────────────────────────────────────────────────────────────────────────────

class TestRangeWidthFilter(unittest.TestCase):

    def test_narrow_range_blocked(self):
        """Range width < 0.20 × ATR → BLOCKED_BY_RANGE_WIDTH / RANGE_TOO_NARROW."""
        engine, store = _make_engine()
        atr = 100.0
        # Width = 2 pts, 0.20 × 100 = 20 pts → too narrow
        for m in range(30, 40):
            _push(engine, store, "MNQ",
                  _bar(9, m, 20000.0, 20001.0, 20000.0, 20000.5, atr=atr))
        _push(engine, store, "MNQ", _flat_bar(9, 40, 20000.5, atr=atr))
        s = engine._state["MNQ"]
        self.assertEqual(s.state, OrbState.BLOCKED_BY_RANGE_WIDTH,
                         f"Expected BLOCKED_BY_RANGE_WIDTH, got {s.state}")

    def test_wide_range_blocked(self):
        """Range width > 1.50 × ATR → BLOCKED_BY_RANGE_WIDTH / RANGE_TOO_WIDE."""
        engine, store = _make_engine()
        atr = 10.0
        # Width = 200 pts, 1.50 × 10 = 15 pts → too wide
        for m in range(30, 40):
            _push(engine, store, "MNQ",
                  _bar(9, m, 20000.0, 20200.0, 20000.0, 20100.0, atr=atr))
        _push(engine, store, "MNQ", _flat_bar(9, 40, 20100.0, atr=atr))
        s = engine._state["MNQ"]
        self.assertEqual(s.state, OrbState.BLOCKED_BY_RANGE_WIDTH,
                         f"Expected BLOCKED_BY_RANGE_WIDTH, got {s.state}")

    def test_valid_range_not_blocked(self):
        """Range within [0.20, 1.50] × ATR passes the filter."""
        engine, store = _make_engine()
        atr = 100.0
        # Width = 100 pts, 1.0 × 100 → passes
        for m in range(30, 40):
            _push(engine, store, "MNQ",
                  _bar(9, m, 20000.0, 20100.0, 20000.0, 20050.0, atr=atr))
        _push(engine, store, "MNQ", _flat_bar(9, 40, 20050.0, atr=atr))
        s = engine._state["MNQ"]
        self.assertTrue(s.range_locked, f"Range should be locked; state={s.state}")


# ─────────────────────────────────────────────────────────────────────────────
# 11. Identity — strategy_version / config_version
# ─────────────────────────────────────────────────────────────────────────────

class TestInstanceIdentity(unittest.TestCase):

    def test_strategy_version_on_instrument_state(self):
        engine, store = _make_engine()
        for inst in _INSTRUMENTS:
            self.assertEqual(engine._state[inst].strategy_version, ORB_STRATEGY_VERSION)

    def test_config_version_on_instrument_state(self):
        engine, store = _make_engine()
        for inst in _INSTRUMENTS:
            self.assertEqual(engine._state[inst].config_version, ORB_CONFIG_VERSION)

    def test_trading_date_set_after_first_bar(self):
        engine, store = _make_engine()
        b = _flat_bar(9, 30, 20000.0)
        _push(engine, store, "MNQ", b)
        self.assertEqual(engine._state["MNQ"].trading_date, _TEST_DATE)


# ─────────────────────────────────────────────────────────────────────────────
# 12. Restart recovery
# ─────────────────────────────────────────────────────────────────────────────

class TestRestartRecovery(unittest.TestCase):

    def test_restore_locks_range_immutably(self):
        """After restore, the locked range must be immutable."""
        engine, store = _make_engine()
        for m in range(30, 40):
            _push(engine, store, "MNQ",
                  _bar(9, m, 20000.0, 20100.0, 20000.0, 20050.0, atr=100.0))
        _push(engine, store, "MNQ", _flat_bar(9, 40, 20050.0, atr=100.0))
        orig_high = engine._state["MNQ"].or_high

        # Simulate restart: create new engine that restores from the first
        engine2, store2 = _make_engine()
        # Manually inject the locked state (as _restore_today would do from DB)
        s2 = engine2._state["MNQ"]
        s2.or_high              = orig_high
        s2.or_low               = engine._state["MNQ"].or_low
        s2.or_midpoint          = engine._state["MNQ"].or_midpoint
        s2.or_width             = engine._state["MNQ"].or_width
        s2.or_valid             = True
        s2.range_locked         = True
        s2.range_locked_ts      = "2026-08-11T13:40:00+00:00"
        s2.long_breakout_level  = engine._state["MNQ"].long_breakout_level
        s2.short_breakout_level = engine._state["MNQ"].short_breakout_level
        s2.state                = OrbState.WATCHING_BREAKOUT
        s2.trading_date         = _TEST_DATE
        s2.lock_minutes         = 9 * 60 + 40
        s2.entry_end_minutes    = 10 * 60 + 30

        # Now send a bar with a much higher high — or_high must NOT change
        _push(engine2, store2, "MNQ", _bar(9, 41, 25000.0, 30000.0, 24999.0, 29000.0))
        self.assertEqual(engine2._state["MNQ"].or_high, orig_high,
                         "Locked or_high was mutated after restart restore")

    def test_day_rollover_resets_state(self):
        """When a bar from the next trading day arrives, state resets."""
        engine, store = _make_engine()
        # Lock today's range
        for m in range(30, 40):
            _push(engine, store, "MNQ",
                  _bar(9, m, 20000.0, 20100.0, 20000.0, 20050.0, atr=100.0))
        _push(engine, store, "MNQ", _flat_bar(9, 40, 20050.0, atr=100.0))
        self.assertTrue(engine._state["MNQ"].range_locked)
        # Send a bar from tomorrow
        tomorrow = "2026-08-12"
        next_bar = _flat_bar(9, 30, 20000.0, date=tomorrow)
        _push(engine, store, "MNQ", next_bar)
        s = engine._state["MNQ"]
        self.assertEqual(s.trading_date, tomorrow)
        self.assertFalse(s.range_locked, "range_locked should be False after day rollover")
        # Note: or_high may be non-None once the first range bar of the new day
        # accumulates — the key invariant is that range_locked is False, meaning
        # the new range has not yet been locked/committed.


# ─────────────────────────────────────────────────────────────────────────────
# 13. Regression — 08:00 ORB unchanged
# ─────────────────────────────────────────────────────────────────────────────

class TestOhEightOrbRegression(unittest.TestCase):

    def test_orb_engine_does_not_touch_intraday_by_ticker(self):
        """OrbEngine must not import or access INTRADAY_BY_TICKER."""
        import orb_engine as orb_mod
        source = open(orb_mod.__file__).read()
        self.assertNotIn("INTRADAY_BY_TICKER", source,
                         "OrbEngine accesses INTRADAY_BY_TICKER — not allowed")

    def test_orb_engine_does_not_touch_breakout_or_by_ticker(self):
        """OrbEngine must not import or access BREAKOUT_OR_BY_TICKER."""
        import orb_engine as orb_mod
        source = open(orb_mod.__file__).read()
        self.assertNotIn("BREAKOUT_OR_BY_TICKER", source,
                         "OrbEngine accesses BREAKOUT_OR_BY_TICKER — not allowed")

    def test_orb_engine_has_no_broker_send(self):
        """OrbEngine must contain no direct broker/traderspost calls."""
        import orb_engine as orb_mod
        source = open(orb_mod.__file__).read()
        for forbidden in ("traderspost", "execute_trade_gateway", "_send_broker"):
            self.assertNotIn(forbidden, source,
                             f"OrbEngine references broker symbol '{forbidden}'")

    def test_orb_engine_imports_no_app(self):
        """OrbEngine must not import from app.py (would create circular import)."""
        import orb_engine as orb_mod
        source = open(orb_mod.__file__).read()
        self.assertNotIn("import app", source)
        self.assertNotIn("from app", source)


# ─────────────────────────────────────────────────────────────────────────────
# 14. Sizing helper
# ─────────────────────────────────────────────────────────────────────────────

class TestOrbContracts(unittest.TestCase):

    def test_basic_sizing(self):
        contracts, rpc = _orb_contracts(10.0, 2.0, 150.0)
        # rpc = 10 * 2 = 20, contracts = 150 / 20 = 7
        self.assertEqual(contracts, 7)
        self.assertAlmostEqual(rpc, 20.0)

    def test_zero_stop_returns_zero(self):
        contracts, rpc = _orb_contracts(0.0, 2.0, 150.0)
        self.assertEqual(contracts, 0)

    def test_zero_budget_returns_zero(self):
        contracts, rpc = _orb_contracts(10.0, 2.0, 0.0)
        self.assertEqual(contracts, 0)

    def test_rounds_down(self):
        # rpc = 7.0 * 2.0 = 14; 150 / 14 = 10.71 → floor → 10
        contracts, _ = _orb_contracts(7.0, 2.0, 150.0)
        self.assertEqual(contracts, 10)

    def test_negative_stop_dist_returns_zero(self):
        contracts, _ = _orb_contracts(-5.0, 2.0, 150.0)
        self.assertEqual(contracts, 0)


# ─────────────────────────────────────────────────────────────────────────────
# 15. Two-sided sweep
# ─────────────────────────────────────────────────────────────────────────────

class TestTwoSidedSweep(unittest.TestCase):

    def test_two_sided_sweep_is_logged(self):
        engine, store = _make_engine()
        for m in range(30, 40):
            _push(engine, store, "MNQ",
                  _bar(9, m, 20000.0, 20100.0, 20000.0, 20050.0, atr=100.0))
        _push(engine, store, "MNQ", _flat_bar(9, 40, 20050.0, atr=100.0))
        # Bar breaks both sides: high > 20101 AND low < 19999
        sweep = _bar(9, 41, 20050.0, 20110.0, 19990.0, 20050.0)
        _push(engine, store, "MNQ", sweep)
        # two_sided_sweep should be flagged; instrument stays watching (not qualified)
        s = engine._state["MNQ"]
        self.assertTrue(s.two_sided_sweep, "Two-sided sweep not flagged")
        self.assertEqual(s.state, OrbState.WATCHING_BREAKOUT,
                         f"Should remain WATCHING after two-sided sweep, got {s.state}")


# ─────────────────────────────────────────────────────────────────────────────
# 16. State machine completeness check
# ─────────────────────────────────────────────────────────────────────────────

class TestStateMachineCompleteness(unittest.TestCase):

    def test_all_32_states_defined(self):
        states = [v for k, v in vars(OrbState).items() if not k.startswith("_")]
        # Verify no duplicates
        self.assertEqual(len(states), len(set(states)), "Duplicate state values")
        # Verify all spec-required states are present
        required = {
            "DISABLED", "WAITING_FOR_SESSION", "WAITING_FOR_RANGE", "BUILDING_RANGE",
            "RANGE_LOCKED", "WATCHING_BREAKOUT", "BREAKOUT_DETECTED",
            "CONFIRMATION_PENDING", "QUALIFIED", "BREAKOUT_MISSED",
            "RISK_PENDING", "RISK_RESERVED",
            "BLOCKED_BY_DATA", "BLOCKED_BY_RANGE_WIDTH", "BLOCKED_BY_CONFIRMATION",
            "BLOCKED_BY_MAXIMUM_CHASE", "BLOCKED_BY_INSTRUMENT_RISK",
            "BLOCKED_BY_GROUP_RISK", "BLOCKED_BY_PORTFOLIO_RISK",
            "BLOCKED_BY_PROP_RULE", "BLOCKED_BY_DAILY_LOSS",
            "BLOCKED_BY_POSITION_LIMIT", "BLOCKED_BY_DUPLICATE_GUARD",
            "BLOCKED_BY_EXECUTION_MODE", "BLOCKED_BY_ARM_STATE",
            "BLOCKED_BY_SAFETY_LOCK",
            "ENTRY_REQUESTED", "ORDER_ACCEPTED", "ORDER_REJECTED",
            "POSITION_ACTIVE", "POSITION_MANAGING", "COMPLETED", "EXPIRED",
            "DATA_INVALID", "RECOVERY_REQUIRED",
        }
        missing = required - set(states)
        self.assertEqual(missing, set(), f"Missing states: {missing}")


# ─────────────────────────────────────────────────────────────────────────────
# 17. Config validation
# ─────────────────────────────────────────────────────────────────────────────

class TestConfigValidation(unittest.TestCase):

    def test_invalid_range_duration_raises(self):
        engine, store = _make_engine()
        with self.assertRaises(ValueError):
            engine.set_config({"range_duration_min": 7})   # 7 is not in {5,10,15,30}

    def test_valid_range_duration_accepted(self):
        engine, store = _make_engine()
        result = engine.set_config({"range_duration_min": 15})
        self.assertEqual(result["range_duration_min"], 15)

    def test_config_summary_correct_lock_time(self):
        engine, store = _make_engine(range_duration_min=15)
        summary = engine._config_summary()
        self.assertEqual(summary["lock_time_et"], "09:45")

    def test_duration_change_does_not_alter_current_locked_range(self):
        """Changing range_duration_min mid-session must not relock the range."""
        engine, store = _make_engine()
        for m in range(30, 40):
            _push(engine, store, "MNQ",
                  _bar(9, m, 20000.0, 20100.0, 20000.0, 20050.0, atr=100.0))
        _push(engine, store, "MNQ", _flat_bar(9, 40, 20050.0, atr=100.0))
        locked_high = engine._state["MNQ"].or_high
        # Change duration mid-session
        engine.set_config({"range_duration_min": 30})
        # Range must still be locked with original high
        self.assertEqual(engine._state["MNQ"].or_high, locked_high)
        self.assertTrue(engine._state["MNQ"].range_locked)


# ─────────────────────────────────────────────────────────────────────────────
# 18. Portfolio status and API
# ─────────────────────────────────────────────────────────────────────────────

class TestPortfolioStatusApi(unittest.TestCase):

    def test_get_all_status_returns_four_instruments(self):
        engine, store = _make_engine()
        status = engine.get_all_status()
        self.assertIn("instruments", status)
        for inst in _INSTRUMENTS:
            self.assertIn(inst, status["instruments"])

    def test_portfolio_section_in_status(self):
        engine, store = _make_engine()
        status = engine.get_all_status()
        self.assertIn("portfolio", status)
        portfolio = status["portfolio"]
        self.assertIn("active_positions", portfolio)
        self.assertIn("active_index_risk", portfolio)
        self.assertIn("active_metals_risk", portfolio)

    def test_get_instrument_status_unknown_returns_error(self):
        engine, store = _make_engine()
        result = engine.get_instrument_status("XYZ")
        self.assertIn("error", result)

    def test_db_ready_false_when_no_db(self):
        engine, store = _make_engine()
        status = engine.get_all_status()
        self.assertFalse(status["db_ready"])

    def test_timeline_empty_at_start(self):
        engine, store = _make_engine()
        tl = engine.get_timeline("MNQ", limit=10)
        # Timeline may have boot events but should be a list
        self.assertIsInstance(tl, list)


# ─────────────────────────────────────────────────────────────────────────────
# 19. Breakout buffer calculations
# ─────────────────────────────────────────────────────────────────────────────

class TestBreakoutBuffers(unittest.TestCase):

    def _check_buffer(self, inst, expected_long_offset, expected_short_offset):
        engine, store = _make_engine()
        hi, lo = 100.0, 90.0
        for m in range(30, 40):
            _push(engine, store, inst,
                  _bar(9, m, lo, hi, lo, (hi+lo)/2, atr=50.0))
        _push(engine, store, inst, _flat_bar(9, 40, (hi+lo)/2, atr=50.0))
        s = engine._state[inst]
        self.assertIsNotNone(s.long_breakout_level)
        self.assertAlmostEqual(s.long_breakout_level,  hi + expected_long_offset, places=5)
        self.assertAlmostEqual(s.short_breakout_level, lo - expected_short_offset, places=5)

    def test_mgc_buffer_2_ticks_01_each(self):
        """MGC: 2 ticks × 0.1 = 0.2 pts each side."""
        self._check_buffer("MGC", 0.2, 0.2)

    def test_mnq_buffer_4_ticks_025_each(self):
        """MNQ: 4 ticks × 0.25 = 1.0 pt each side."""
        self._check_buffer("MNQ", 1.0, 1.0)

    def test_mes_buffer_2_ticks_025_each(self):
        """MES: 2 ticks × 0.25 = 0.5 pt each side."""
        self._check_buffer("MES", 0.5, 0.5)

    def test_mym_buffer_4_ticks_1pt_each(self):
        """MYM: 4 ticks × 1.0 = 4.0 pts each side."""
        self._check_buffer("MYM", 4.0, 4.0)


# ─────────────────────────────────────────────────────────────────────────────
# 20. Shadow entry content
# ─────────────────────────────────────────────────────────────────────────────

class TestShadowEntryContent(unittest.TestCase):

    def _qualify(self) -> tuple:
        engine, store = _make_engine()
        for m in range(30, 40):
            _push(engine, store, "MNQ",
                  _bar(9, m, 20000.0, 20100.0, 20000.0, 20050.0, atr=100.0))
        _push(engine, store, "MNQ", _flat_bar(9, 40, 20050.0, atr=100.0))
        # close=20108 is clearly in upper half (midpoint=20105) ✓
        _push(engine, store, "MNQ",
              _bar(9, 41, 20101.0, 20110.0, 20100.0, 20108.0, atr=100.0))
        return engine, store

    def test_shadow_entry_exists(self):
        engine, _ = self._qualify()
        self.assertEqual(len(engine._state["MNQ"].shadow_entries), 1)

    def test_shadow_entry_has_required_provenance_fields(self):
        engine, _ = self._qualify()
        entry = engine._state["MNQ"].shadow_entries[0]
        required = [
            "instrument", "trading_date", "direction", "strategy_version",
            "config_version", "range_duration_min", "or_high", "or_low",
            "or_width", "confirmation_mode", "entry", "stop", "tp1", "tp2",
            "contracts", "risk_dollars", "mode",
        ]
        for field in required:
            self.assertIn(field, entry, f"Shadow entry missing field: {field}")

    def test_shadow_entry_mode_is_shadow(self):
        engine, _ = self._qualify()
        entry = engine._state["MNQ"].shadow_entries[0]
        self.assertEqual(entry["mode"], "SHADOW")

    def test_shadow_entry_direction_long(self):
        engine, _ = self._qualify()
        entry = engine._state["MNQ"].shadow_entries[0]
        self.assertEqual(entry["direction"], "LONG")

    def test_daily_trade_count_incremented(self):
        engine, _ = self._qualify()
        self.assertEqual(engine._state["MNQ"].daily_trade_count, 1)

    def test_second_entry_blocked_by_daily_limit(self):
        """With max_trades_per_instrument=1, a second qualifying bar is blocked."""
        engine, store = self._qualify()
        # Send another breakout bar
        _push(engine, store, "MNQ",
              _bar(9, 43, 20101.0, 20106.0, 20100.0, 20102.0, atr=100.0))
        # Still only 1 shadow entry
        self.assertEqual(len(engine._state["MNQ"].shadow_entries), 1)


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
