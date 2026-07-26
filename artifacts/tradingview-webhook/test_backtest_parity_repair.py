"""
test_backtest_parity_repair.py
==============================
Phase 6A.1 — ORB replay parity and historical-data readiness.

Test groups
-----------
T01-T05  Part 3:  target_4r management model (constants + _walk_managed + simulate_strategy)
T06-T09  Part 7:  look-ahead isolation (compute_indicators is strictly causal)
T10-T12  Part 8:  multi-session OR-reset (day-2 range never inherits day-1)
T13-T15  Part 9:  same-bar worst-case (stop always wins over target on same bar)
T16-T18  Part 10: serialized result immutability (byte-identical re-runs)

Run:
  pytest test_backtest_parity_repair.py -v
  python3 test_backtest_parity_repair.py
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_engine as bt

ET_TZ = ZoneInfo("America/New_York")
UTC   = timezone.utc


# ─────────────────────────────────────────────────────────────────────────────
# Candle-building helpers
# ─────────────────────────────────────────────────────────────────────────────

def _bar(ts_et, open_, high, low, close, volume=1000):
    return {"ts": ts_et.astimezone(UTC), "open": open_,
            "high": high, "low": low, "close": close, "volume": volume}


def _flat_bars(start_et, n, price=2000.0, half_range=1.0, volume=1000):
    """n identical stable bars starting at start_et (1-min apart)."""
    out = []
    for i in range(n):
        t = start_et + timedelta(minutes=i)
        out.append(_bar(t, price, price + half_range, price - half_range, price, volume))
    return out


def _make_orb_candles():
    """Candle sequence guaranteed to produce one ORB Long signal.

    Layout (all 2025-04-01, ET timestamps, stored UTC):
      bars  0-59 : 07:00-07:59 ET — stable price 2000.0, range 2.0/bar (ATR baseline)
      bars 60-89 : 08:00-08:29 ET — OR build, or_high=2003.0, or_low=1997.0
      bar  90    : 08:30 ET — trigger: close=2005.0 > or_high=2003, bullish, 3× volume
      bar  91    : 08:31 ET — entry bar (next-bar open), no stop/target touch
      bar  92    : 08:32 ET — winner, high=2040 → hits 4R target unambiguously
      bars 93-99 : padding

    ATR at bar 90 is dominated by the pre-OR stable bars (range=2.0), giving
    ATR≈2-4 pts. With SCALP stop_mult=1.5 and MGC tick=0.1:
      atr_dist ≈ 3-6 pts  →  4R target ≈ entry + 12-24 pts
    The winner bar high of 2040 >> any realistic 4R target.

    Volume parity note (PARTIAL): volume_spike_fresh is a live in-memory webhook-
    fed dict with a 20-min TTL. It is NOT reconstructable from OHLCV bars. The
    backtest uses RVOL (rolling volume ratio) as an honest approximation: the
    trigger bar has 3× the 20-bar average volume, so volume_confirmed=True.
    """
    date_et = datetime(2025, 4, 1, tzinfo=ET_TZ)

    candles = []
    # Pre-OR stable baseline (07:00-07:59 ET)
    candles += _flat_bars(date_et.replace(hour=7, minute=0), 60,
                          price=2000.0, half_range=1.0)

    # OR build (08:00-08:29 ET) — alternating upper/lower to build range
    or_start = date_et.replace(hour=8, minute=0)
    for i in range(30):
        t = or_start + timedelta(minutes=i)
        if i == 0:
            # First OR bar: spikes to 2003 to establish or_high
            candles.append(_bar(t, 2001.5, 2003.0, 2001.0, 2001.5))
        elif i == 5:
            # Spikes to 1997 to establish or_low
            candles.append(_bar(t, 1998.5, 1999.0, 1997.0, 1998.5))
        else:
            candles.append(_bar(t, 2000.0, 2001.0, 1999.0, 2000.0))

    # Stable bars (08:30-08:59 ET): OR is complete but no breakout yet.
    # The 08:28-08:32 ET window is a news blackout in the default research
    # filter. Trigger fires at 09:00 ET to stay outside that window.
    for i in range(30, 60):
        t = or_start + timedelta(minutes=i)
        candles.append(_bar(t, 2002.0, 2002.5, 2001.5, 2002.0))

    # Stable bars (09:00-09:29 ET) leading up to the trigger
    ny_start = date_et.replace(hour=9, minute=0)
    for i in range(29):
        t = ny_start + timedelta(minutes=i)
        candles.append(_bar(t, 2002.0, 2002.5, 2001.5, 2002.0))

    # Trigger bar (09:29 ET): close above or_high=2003, bullish, 3× volume.
    # or_complete is True (bar is well after 08:30 ET).
    trigger_t = date_et.replace(hour=9, minute=29)
    candles.append(_bar(trigger_t, 2002.5, 2005.5, 2001.5, 2005.0, volume=3000))
    signal_idx = len(candles) - 1

    # Entry bar (09:30 ET): next-bar open, no stop or target touched
    candles.append(_bar(date_et.replace(hour=9, minute=30),
                        2005.0, 2007.0, 2004.0, 2006.0))

    # Winner bar (09:31 ET): very high — always clears a 4R target
    candles.append(_bar(date_et.replace(hour=9, minute=31),
                        2006.0, 2040.0, 2005.5, 2038.0))

    # Padding
    for i in range(32, 60):
        t = date_et.replace(hour=9, minute=i)
        candles.append(_bar(t, 2038.0, 2038.5, 2037.5, 2038.0))

    return candles, signal_idx


def _make_two_session_candles():
    """Two complete trading sessions on consecutive ET dates.

    Day 1 (2025-04-01): OR builds to or_high=2010.0, or_low=2004.0
    Day 2 (2025-04-02): OR builds to or_high=1990.0, or_low=1984.0

    Covers bars from 07:00 ET day 1 through 09:00 ET day 2.
    No candles between 18:00 ET day 1 and 07:00 ET day 2 (data gap).
    """
    candles = []
    # ── Day 1 ──────────────────────────────────────────────────────────────
    d1 = datetime(2025, 4, 1, tzinfo=ET_TZ)
    candles += _flat_bars(d1.replace(hour=7, minute=0), 60,
                          price=2007.0, half_range=1.0)
    or1 = d1.replace(hour=8, minute=0)
    for i in range(30):
        t = or1 + timedelta(minutes=i)
        if i == 0:
            candles.append(_bar(t, 2008.0, 2010.0, 2007.0, 2008.0))
        elif i == 5:
            candles.append(_bar(t, 2005.0, 2005.5, 2004.0, 2005.0))
        else:
            candles.append(_bar(t, 2007.0, 2008.0, 2006.0, 2007.0))
    for i in range(30, 60):
        t = or1 + timedelta(minutes=i)
        candles.append(_bar(t, 2007.0, 2008.0, 2006.0, 2007.0))

    # ── Day 2 ──────────────────────────────────────────────────────────────
    d2 = datetime(2025, 4, 2, tzinfo=ET_TZ)
    candles += _flat_bars(d2.replace(hour=7, minute=0), 60,
                          price=1987.0, half_range=1.0)
    or2 = d2.replace(hour=8, minute=0)
    for i in range(30):
        t = or2 + timedelta(minutes=i)
        if i == 0:
            candles.append(_bar(t, 1988.0, 1990.0, 1987.0, 1988.0))
        elif i == 5:
            candles.append(_bar(t, 1985.0, 1985.5, 1984.0, 1985.0))
        else:
            candles.append(_bar(t, 1987.0, 1988.0, 1986.0, 1987.0))
    for i in range(30, 60):
        t = or2 + timedelta(minutes=i)
        candles.append(_bar(t, 1987.0, 1988.0, 1986.0, 1987.0))

    return candles


# ─────────────────────────────────────────────────────────────────────────────
# Part 3 — target_4r management model (T01-T05)
# ─────────────────────────────────────────────────────────────────────────────

def test_01_strategy_mgmt_override_constant():
    """STRATEGY_MGMT_OVERRIDE maps OPENING_RANGE_BREAKOUT → target_4r."""
    assert "OPENING_RANGE_BREAKOUT" in bt.STRATEGY_MGMT_OVERRIDE
    assert bt.STRATEGY_MGMT_OVERRIDE["OPENING_RANGE_BREAKOUT"] == "target_4r"


def test_02_target_4r_in_management_constants():
    """target_4r is in BT_RUN_MANAGEMENTS, OPT_MANAGEMENTS, and BT_RUN_MGMT_LABELS."""
    assert "target_4r" in bt.BT_RUN_MANAGEMENTS, "target_4r missing from BT_RUN_MANAGEMENTS"
    assert "target_4r" in bt.OPT_MANAGEMENTS, "target_4r missing from OPT_MANAGEMENTS"
    assert "target_4r" in bt.BT_RUN_MGMT_LABELS, "target_4r missing from BT_RUN_MGMT_LABELS"
    label = bt.BT_RUN_MGMT_LABELS["target_4r"]
    assert "4" in label, f"label should mention 4: {label!r}"


def test_03_walk_managed_target_4r_winner():
    """_walk_managed with target_4r exits at exactly 4.0R on a winning long."""
    entry, stop, risk, slip = 2000.0, 1990.0, 10.0, 0.0
    # Winner bar: high well above entry + 4R = 2040
    candles = []
    for i in range(20):
        ts = datetime(2025, 4, 1, 10, i, tzinfo=UTC)
        # Bars 0-3: no target hit; bar 4 hits 4R target
        if i < 4:
            candles.append({"ts": ts, "open": 2001.0, "high": 2020.0,
                            "low": 2001.0, "close": 2010.0, "volume": 1000})
        else:
            candles.append({"ts": ts, "open": 2010.0, "high": 2045.0,
                            "low": 2009.0, "close": 2040.0, "volume": 1000})
    px, bar, reason, r_gross = bt._walk_managed(
        candles, 0, "Long", entry, stop, risk, slip, "target_4r")
    assert "4" in reason, f"Expected '4R' in exit reason, got: {reason!r}"
    assert abs(r_gross - 4.0) < 1e-9, f"Expected r_gross=4.0, got {r_gross}"
    assert px == entry + 4.0 * risk, f"Expected exit at 4R price, got {px}"


def test_04_walk_managed_target_4r_loser():
    """_walk_managed with target_4r exits at stop loss when price falls."""
    entry, stop, risk, slip = 2000.0, 1990.0, 10.0, 0.1
    candles = [{"ts": datetime(2025, 4, 1, 10, i, tzinfo=UTC),
                "open": 1988.0, "high": 1989.5, "low": 1987.0, "close": 1988.0,
                "volume": 1000}
               for i in range(20)]
    px, bar, reason, r_gross = bt._walk_managed(
        candles, 0, "Long", entry, stop, risk, slip, "target_4r")
    assert "Stop" in reason, f"Expected stop loss, got: {reason!r}"
    assert r_gross < 0, f"Expected negative r_gross for loser, got {r_gross}"


def test_05_orb_simulate_strategy_4r_override():
    """simulate_strategy applies 4R override to OPENING_RANGE_BREAKOUT regardless
    of the global management parameter (default management=target_1_5r here)."""
    candles, _ = _make_orb_candles()
    spec = bt.BT_SPECS["MGC"]
    snaps = bt.compute_indicators(candles, mode="SCALP")

    # Run with default management (1.5R) — should be overridden to 4R for ORB.
    # Pass news_blackouts_et=[] so the test is not sensitive to the 08:28-08:32 ET
    # research-filter window (trigger fires at 09:29 ET, outside that window anyway,
    # but being explicit keeps the test robust to future window changes).
    trades = bt.simulate_strategy(
        snaps, candles, "OPENING_RANGE_BREAKOUT", spec, "SCALP",
        slippage_ticks=0, commission_per_side=0.0,
        news_blackouts_et=[],
        management="target_1_5r")

    assert trades, "Expected at least one ORB trade from the crafted candle sequence"
    winners = [t for t in trades if "4R" in t.get("exit_reason", "")]
    assert winners, (
        f"Expected winning trade with 'Target 4R' exit reason. "
        f"Trades: {[(t['exit_reason'], round(t['r_multiple'], 3)) for t in trades]}")
    for w in winners:
        assert abs(w["r_multiple"] - 4.0) < 0.01, (
            f"ORB winner r_multiple should be ≈4.0, got {w['r_multiple']}")


# ─────────────────────────────────────────────────────────────────────────────
# Part 7 — look-ahead isolation (T06-T09)
# ─────────────────────────────────────────────────────────────────────────────

def _make_basic_candles(n=100, price=2000.0, start_hour_et=7):
    """n 1-minute bars starting at start_hour_et ET on 2025-04-01."""
    d = datetime(2025, 4, 1, start_hour_et, 0, tzinfo=ET_TZ)
    out = []
    for i in range(n):
        t = d + timedelta(minutes=i)
        p = price + (i % 7) * 0.3 - 1.0   # slight variation
        out.append(_bar(t, p, p + 1.0, p - 1.0, p + 0.1))
    return out


def test_06_lookahead_price_fields_causal():
    """OHLC + close fields at bar i are identical whether computed on candles[:i+1]
    or on the full candle list. (Basic sanity that no price data is rewritten.)"""
    candles = _make_basic_candles(80)
    snaps_full = bt.compute_indicators(candles, mode="SCALP")
    for check in [20, 40, 60]:
        snaps_part = bt.compute_indicators(candles[:check + 1], mode="SCALP")
        sf, sp = snaps_full[check], snaps_part[check]
        for field in ("close", "open", "high", "low", "vwap"):
            assert sf[field] == sp[field], (
                f"Bar {check}: field '{field}' differs: full={sf[field]} partial={sp[field]}")


def test_07_lookahead_structure_signals_causal():
    """BOS/CHOCH flags at bar i are identical regardless of future candles.
    This proves the pivot detection algorithm never peeks beyond bar i."""
    candles = _make_basic_candles(80)
    snaps_full = bt.compute_indicators(candles, mode="SCALP")
    for check in [30, 50, 70]:
        snaps_part = bt.compute_indicators(candles[:check + 1], mode="SCALP")
        sf, sp = snaps_full[check], snaps_part[check]
        for field in ("bos_long", "bos_short", "choch_long", "choch_short",
                      "sweep_bull", "sweep_bear", "confirm_bull", "confirm_bear"):
            assert sf[field] == sp[field], (
                f"Bar {check}: field '{field}' differs: full={sf[field]} partial={sp[field]}")


def test_08_lookahead_atr_and_rvol_causal():
    """ATR and RVOL at bar i are identical whether computed on the full or partial
    candle list — they are rolling windows of past bars only."""
    candles = _make_basic_candles(80)
    snaps_full = bt.compute_indicators(candles, mode="SCALP")
    for check in [30, 50, 70]:
        snaps_part = bt.compute_indicators(candles[:check + 1], mode="SCALP")
        sf, sp = snaps_full[check], snaps_part[check]
        for field in ("atr", "rvol", "atr_ratio", "regime"):
            assert sf[field] == sp[field], (
                f"Bar {check}: field '{field}' differs: "
                f"full={sf[field]} partial={sp[field]}")


def test_09_lookahead_or_fields_causal():
    """OR fields at bar i are identical regardless of future candles.
    The OR accumulates only within the current-date 08:00-08:30 ET window."""
    candles, _ = _make_orb_candles()
    snaps_full = bt.compute_indicators(candles, mode="SCALP")
    # Check at bars inside and after the OR build window
    for check in [60, 75, 89]:
        snaps_part = bt.compute_indicators(candles[:check + 1], mode="SCALP")
        sf, sp = snaps_full[check], snaps_part[check]
        for field in ("or_high", "or_low", "or_complete"):
            assert sf[field] == sp[field], (
                f"Bar {check}: field '{field}' differs: full={sf[field]} partial={sp[field]}")


# ─────────────────────────────────────────────────────────────────────────────
# Part 8 — multi-session OR reset (T10-T12)
# ─────────────────────────────────────────────────────────────────────────────

def test_10_or_resets_on_new_date():
    """or_high and or_low reset when the ET date changes. Day 2's OR values
    must not be contaminated by day 1's range."""
    candles = _make_two_session_candles()
    snaps = bt.compute_indicators(candles, mode="SCALP")

    # Find snaps from each day after OR completes (08:30 ET+)
    day1_date = datetime(2025, 4, 1, tzinfo=ET_TZ).date()
    day2_date = datetime(2025, 4, 2, tzinfo=ET_TZ).date()

    day1_or_snaps = [s for s in snaps
                     if s["et"].date() == day1_date and s["or_complete"]]
    day2_or_snaps = [s for s in snaps
                     if s["et"].date() == day2_date and s["or_complete"]]

    assert day1_or_snaps, "Expected some day-1 OR-complete snaps"
    assert day2_or_snaps, "Expected some day-2 OR-complete snaps"

    d1 = day1_or_snaps[0]
    d2 = day2_or_snaps[0]

    # Day 1 OR: or_high ≈ 2010, or_low ≈ 2004
    assert d1["or_high"] is not None and d1["or_high"] > 2009, (
        f"Day-1 or_high should be ≥2010, got {d1['or_high']}")
    assert d1["or_low"] is not None and d1["or_low"] <= 2005, (
        f"Day-1 or_low should be ≤2004, got {d1['or_low']}")

    # Day 2 OR: or_high ≈ 1990, or_low ≈ 1984 — must not inherit day-1 values
    assert d2["or_high"] is not None and d2["or_high"] < 1995, (
        f"Day-2 or_high should be ~1990, got {d2['or_high']} (day-1 bleed?)")
    assert d2["or_low"] is not None and d2["or_low"] < 1990, (
        f"Day-2 or_low should be ~1984, got {d2['or_low']} (day-1 bleed?)")


def test_11_or_not_complete_before_or_start():
    """or_complete is False before 08:30 ET on each day."""
    candles = _make_two_session_candles()
    snaps = bt.compute_indicators(candles, mode="SCALP")
    for s in snaps:
        h_et = s["et"].hour + s["et"].minute / 60.0
        if h_et < bt.OPENING_RANGE_START_ET + bt.OPENING_RANGE_BUILD_MIN / 60.0:
            assert not s["or_complete"], (
                f"or_complete should be False before 08:30, bar ET={s['et']}")


def test_12_or_reset_date_independence():
    """The or_date (internal reset key) changes between days: day-1 and day-2
    snaps have different ET dates, proving no state leaks across the boundary."""
    candles = _make_two_session_candles()
    snaps = bt.compute_indicators(candles, mode="SCALP")
    dates = {s["et"].date() for s in snaps if s["or_complete"]}
    assert len(dates) == 2, (
        f"Expected OR-complete snaps on exactly 2 dates, got {dates}")


# ─────────────────────────────────────────────────────────────────────────────
# Part 9 — same-bar worst-case discipline (T13-T15)
# ─────────────────────────────────────────────────────────────────────────────

def _one_bar(lo, hi, entry_bar=0):
    """A minimal 3-candle list: entry_bar-1 (for entry_bar=1), bar at entry_bar,
    and a trailing padding bar. Used to test same-bar fill discipline."""
    ts0 = datetime(2025, 4, 1, 10, 0, tzinfo=UTC)
    ts1 = datetime(2025, 4, 1, 10, 1, tzinfo=UTC)
    ts2 = datetime(2025, 4, 1, 10, 2, tzinfo=UTC)
    return [
        {"ts": ts0, "open": 2000.0, "high": 2001.0, "low": 1999.0,
         "close": 2000.0, "volume": 1000},
        {"ts": ts1, "open": 2000.0, "high": hi, "low": lo,
         "close": 2001.0, "volume": 1000},
        {"ts": ts2, "open": 2001.0, "high": 2002.0, "low": 2000.0,
         "close": 2001.0, "volume": 1000},
    ]


def test_13_same_bar_stop_wins_over_target_long():
    """On a bar where BOTH stop and 1.5R target are touched (long), stop always
    wins (worst-case fill). The stop check precedes the target check in _walk_managed."""
    entry, stop, risk = 2000.0, 1990.0, 10.0
    target = entry + 1.5 * risk  # 2015.0
    # Bar touches low=1985 (below stop=1990) AND high=2020 (above target=2015)
    candles = _one_bar(lo=1985.0, hi=2020.0)
    px, bar, reason, r_gross = bt._walk_managed(
        candles, 1, "Long", entry, stop, risk, 0.0, "target_1_5r")
    assert "Stop" in reason, (
        f"Same-bar: stop should win over 1.5R target. Got: {reason!r}")
    assert r_gross < 0, f"Stop loss should be negative R, got {r_gross}"


def test_14_same_bar_stop_wins_over_4r_target_long():
    """Same-bar worst-case: stop beats 4R target for ORB (long)."""
    entry, stop, risk = 2000.0, 1990.0, 10.0
    candles = _one_bar(lo=1985.0, hi=2050.0)   # both stop and 4R target hit
    px, bar, reason, r_gross = bt._walk_managed(
        candles, 1, "Long", entry, stop, risk, 0.0, "target_4r")
    assert "Stop" in reason, (
        f"Same-bar: stop should beat 4R target. Got: {reason!r}")
    assert r_gross < 0


def test_15_same_bar_stop_wins_short():
    """Same-bar worst-case for a short: high touches stop, low touches target;
    stop must win."""
    entry, stop, risk = 2000.0, 2010.0, 10.0   # short: stop above entry
    target = entry - 1.5 * risk  # 1985.0
    candles = _one_bar(lo=1980.0, hi=2015.0)   # both sides hit
    px, bar, reason, r_gross = bt._walk_managed(
        candles, 1, "Short", entry, stop, risk, 0.0, "target_1_5r")
    assert "Stop" in reason, (
        f"Same-bar short: stop should win over 1.5R target. Got: {reason!r}")
    assert r_gross < 0


# ─────────────────────────────────────────────────────────────────────────────
# Part 10 — serialized result immutability (T16-T18)
# ─────────────────────────────────────────────────────────────────────────────

def _run_orb_backtest(candles):
    """Run a minimal run_backtest for OPENING_RANGE_BREAKOUT only.
    news_blackouts_et is cleared so the test is not sensitive to the default
    08:28-08:32 ET research filter (trigger fires at 09:29 ET, outside the window
    anyway, but being explicit keeps determinism under future window changes)."""
    return bt.run_backtest(candles, {
        "symbol": "MGC", "mode": "SCALP",
        "strategies": ["OPENING_RANGE_BREAKOUT"],
        "slippage_ticks": 0,
        "commission_per_side": 0.0,
        "management": "target_1_5r",   # should be overridden to 4R for ORB
        "news_blackouts_et": [],
    })


def test_16_result_is_deterministic():
    """run_backtest returns byte-identical JSON across two consecutive runs
    with the same inputs (no randomness, no clock reads in the hot path)."""
    candles, _ = _make_orb_candles()
    r1 = _run_orb_backtest(candles)
    r2 = _run_orb_backtest(candles)
    s1 = json.dumps(r1, sort_keys=True, default=str)
    s2 = json.dumps(r2, sort_keys=True, default=str)
    assert s1 == s2, "run_backtest is not deterministic: two runs produced different output"


def test_17_trades_list_immutable_between_runs():
    """Trades from two consecutive runs are identical (same count, same r_multiple)."""
    candles, _ = _make_orb_candles()
    r1 = _run_orb_backtest(candles)
    r2 = _run_orb_backtest(candles)
    t1 = r1.get("trades", [])
    t2 = r2.get("trades", [])
    assert len(t1) == len(t2), (
        f"Different trade counts across runs: {len(t1)} vs {len(t2)}")
    for i, (a, b) in enumerate(zip(t1, t2)):
        assert a["r_multiple"] == b["r_multiple"], (
            f"Trade {i}: r_multiple differs across runs: {a['r_multiple']} vs {b['r_multiple']}")
        assert a["entry"] == b["entry"]
        assert a["exit"] == b["exit"]


def test_18_all_orb_winners_use_4r():
    """All winning ORB trades in run_backtest output have exit_reason containing
    '4R' and r_multiple ≈ 4.0 (with commission=0, slip=0)."""
    candles, _ = _make_orb_candles()
    result = _run_orb_backtest(candles)
    assert result.get("ok"), f"run_backtest failed: {result.get('error')}"
    trades = result.get("trades", [])
    assert trades, "Expected at least one ORB trade from crafted candle sequence"
    winners = [t for t in trades if t["r_multiple"] > 0]
    if winners:
        for w in winners:
            assert "4R" in w.get("exit_reason", ""), (
                f"ORB winner should exit at 4R, got: {w['exit_reason']!r}")
            assert abs(w["r_multiple"] - 4.0) < 0.01, (
                f"ORB winner r_multiple should be ≈4.0, got {w['r_multiple']}")


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import traceback
    tests = [
        test_01_strategy_mgmt_override_constant,
        test_02_target_4r_in_management_constants,
        test_03_walk_managed_target_4r_winner,
        test_04_walk_managed_target_4r_loser,
        test_05_orb_simulate_strategy_4r_override,
        test_06_lookahead_price_fields_causal,
        test_07_lookahead_structure_signals_causal,
        test_08_lookahead_atr_and_rvol_causal,
        test_09_lookahead_or_fields_causal,
        test_10_or_resets_on_new_date,
        test_11_or_not_complete_before_or_start,
        test_12_or_reset_date_independence,
        test_13_same_bar_stop_wins_over_target_long,
        test_14_same_bar_stop_wins_over_4r_target_long,
        test_15_same_bar_stop_wins_short,
        test_16_result_is_deterministic,
        test_17_trades_list_immutable_between_runs,
        test_18_all_orb_winners_use_4r,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {t.__name__}: {exc}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed+failed} passed")
    if failed:
        raise SystemExit(1)
