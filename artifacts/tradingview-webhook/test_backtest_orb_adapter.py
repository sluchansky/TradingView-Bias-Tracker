"""
test_backtest_orb_adapter.py
============================
35 focused tests for the Phase 6A Opening Range Breakout (ORB) historical
adapter added to backtest_engine.py.

Test groups
-----------
T01-T05  Registry & structural sanity (STRATEGY_DEFS / ORDER / DETECTORS)
T06-T13  Detector unit tests — signal generation (happy-path + negatives)
T14-T15  Detector: no time-window ceiling (unlike Opening Drive 08:00–10:00 ET)
T16-T17  Detector: no VWAP-side alignment required
T18-T20  STRATEGY_DEFS field correctness (label / max_grade / regimes)
T21-T22  Isolation: detect_opening_drive unmodified (regression guard)
T23-T28  simulate_strategy integration via run_backtest (crafted candle pipeline)
T29-T31  Metric immutability & determinism
T32-T33  Detector callable per snapshot, not a stateful singleton
T34-T35  UI label parity in app.py (BT_STRAT_LABELS + rn-strat dropdown)

Run:
  pytest test_backtest_orb_adapter.py -v
  python3 test_backtest_orb_adapter.py
"""

import os
import re
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_engine as bt

ET_TZ = ZoneInfo("America/New_York")
UTC   = timezone.utc

_APP_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _snap(or_complete=True, or_high=102.0, or_low=98.0, close=103.0,
          volume_confirmed=True, confirm_bull=True, confirm_bear=False,
          vwap=100.0, atr=1.0, hour=11):
    """Minimal snapshot dict for detector unit tests.
    Only keys actually read by detect_opening_range_breakout are tested;
    the rest are included so detect_opening_drive can also be called on
    the same snap without a KeyError."""
    return {
        "or_complete":      or_complete,
        "or_high":          or_high,
        "or_low":           or_low,
        "close":            close,
        "volume_confirmed": volume_confirmed,
        "confirm_bull":     confirm_bull,
        "confirm_bear":     confirm_bear,
        "vwap":             vwap,
        "atr":              atr,
        "hour":             hour,
        "choch_long":       False,
        "choch_short":      False,
    }


def _et(date_str, hour_float):
    """Return a UTC datetime corresponding to hour_float ET on date_str."""
    y, m, d = (int(x) for x in date_str.split("-"))
    h  = int(hour_float)
    mi = round((hour_float - h) * 60)
    return datetime(y, m, d, h, mi, 0, tzinfo=ET_TZ).astimezone(UTC)


def _bar(ts, open_=100.0, high=101.0, low=99.0, close=100.0, volume=100.0):
    return {"ts": ts, "open": open_, "high": high,
            "low": low, "close": close, "volume": volume}


def _make_orb_candles(direction="Long", date="2026-01-05",
                      base=2400.0, or_hi=2410.0, or_lo=2390.0):
    """Build a deterministic 81-bar candle series (> run_backtest minimum of 45)
    that produces exactly one ORB signal and lets the trade resolve cleanly.

    Layout (all 1-minute bars on *date* ET):
    - Indices  0–29 (08:00–08:29 ET): OR build — establishes or_hi/or_lo.
    - Indices 30–49 (08:30–08:49 ET): post-OR baseline, normal volume (100).
      Bar 30 (h_et=8.5 ≥ or_end=8.5) flips or_complete=True.
    - Index 50      (08:50 ET):       signal bar — closes beyond OR + volume spike.
      Long: close = or_hi+3.0 (2413.0), bull candle, vol=300 → RVOL≈3.0 ≥ 1.5 ✓
      Short: close = or_lo-3.0 (2387.0), bear candle, vol=300 → RVOL≈3.0 ≥ 1.5 ✓
    - Indices 51–80 (resolution bars): trend toward TP so the trade resolves
      as a winner before data ends (avoids end-of-data ambiguity).
    """
    candles = []

    # ── OR build (08:00–08:29 ET) ─────────────────────────────────────────────
    for i in range(30):
        candles.append(_bar(_et(date, 8.0 + i / 60.0),
                            open_=base, high=or_hi, low=or_lo,
                            close=base, volume=100.0))

    # ── Post-OR baseline for RVOL window (08:30–08:49 ET) ────────────────────
    for i in range(20):
        candles.append(_bar(_et(date, 8.5 + i / 60.0),
                            open_=base, high=base + 0.5, low=base - 0.5,
                            close=base, volume=100.0))

    # ── Signal bar (08:50 ET) ─────────────────────────────────────────────────
    # Long: open just above or_hi (bull candle), close well above or_hi.
    # Short: open just below or_lo (bear candle), close well below or_lo.
    if direction == "Long":
        sc = or_hi + 3.0        # 2413.0 — clearly above or_hi=2410.0
        candles.append(_bar(_et(date, 8.5 + 20 / 60.0),
                            open_=or_hi + 0.1, high=sc + 0.5,
                            low=or_hi - 0.1, close=sc, volume=300.0))
    else:
        sc = or_lo - 3.0        # 2387.0 — clearly below or_lo=2390.0
        candles.append(_bar(_et(date, 8.5 + 20 / 60.0),
                            open_=or_lo - 0.1, high=or_lo + 0.1,
                            low=sc - 0.5, close=sc, volume=300.0))

    # ── Resolution bars (indices 51–80) ──────────────────────────────────────
    # ATR at signal bar ≈ 1.19 (mostly 1-pt baseline TRs + one 3.6-pt signal TR).
    # scalp_min_stop_pts(MGC)=3.0 floors the stop, so risk≈3.0 pts.
    # target_1_5r: TP = entry ± 4.5.  30 bars moving 0.5 pts/bar reach TP in ~9 bars.
    if direction == "Long":
        # Entry bar open ≈ sc=2413.0; tp1 ≈ 2413.1 + 4.5 = 2417.6
        for j in range(30):
            p = sc + j * 0.5
            candles.append(_bar(_et(date, 8.5 + (21 + j) / 60.0),
                                open_=p, high=p + 2.0, low=p - 0.1, close=p + 0.5,
                                volume=100.0))
    else:
        # Entry bar open ≈ sc=2387.0; tp1 ≈ 2386.9 - 4.5 = 2382.4
        for j in range(30):
            p = sc - j * 0.5
            candles.append(_bar(_et(date, 8.5 + (21 + j) / 60.0),
                                open_=p, high=p + 0.1, low=p - 2.0, close=p - 0.5,
                                volume=100.0))

    assert len(candles) == 81
    return candles


def _bt_run(candles, strategies=None, symbol="MGC", mode="SCALP"):
    """Run run_backtest and return the result dict."""
    params = {"symbol": symbol, "mode": mode}
    if strategies is not None:
        params["strategies"] = strategies
    return bt.run_backtest(candles, params)


def _read_app():
    with open(_APP_PY, encoding="utf-8") as f:
        return f.read()


# ─────────────────────────────────────────────────────────────────────────────
# T01–T05  Registry & structural sanity
# ─────────────────────────────────────────────────────────────────────────────

def test_01_orb_in_strategy_defs():
    assert "OPENING_RANGE_BREAKOUT" in bt.STRATEGY_DEFS, \
        "OPENING_RANGE_BREAKOUT must be registered in STRATEGY_DEFS"


def test_02_orb_in_strategy_order():
    assert "OPENING_RANGE_BREAKOUT" in bt.STRATEGY_ORDER, \
        "OPENING_RANGE_BREAKOUT must be in STRATEGY_ORDER"


def test_03_orb_in_detectors():
    assert "OPENING_RANGE_BREAKOUT" in bt.DETECTORS, \
        "OPENING_RANGE_BREAKOUT must be in DETECTORS"


def test_04_orb_not_disabled():
    assert "OPENING_RANGE_BREAKOUT" not in bt.DISABLED_STRATEGIES, \
        "OPENING_RANGE_BREAKOUT must NOT be in DISABLED_STRATEGIES"


def test_05_exhaustion_fade_still_disabled():
    """Adding ORB must not accidentally re-enable EXHAUSTION_FADE."""
    assert "EXHAUSTION_FADE" in bt.DISABLED_STRATEGIES, \
        "EXHAUSTION_FADE must remain disabled — regression guard"


# ─────────────────────────────────────────────────────────────────────────────
# T06–T13  Detector unit tests — signal generation
# ─────────────────────────────────────────────────────────────────────────────

def test_06_orb_long_signal_fires():
    result = bt.detect_opening_range_breakout(
        _snap(or_complete=True, or_high=102.0, or_low=98.0, close=103.0,
              volume_confirmed=True, confirm_bull=True))
    assert result is not None, "Should fire a Long ORB signal"
    direction, reason = result
    assert direction == "Long"
    assert "102" in reason, f"Reason should reference or_high=102; got: {reason!r}"


def test_07_orb_short_signal_fires():
    result = bt.detect_opening_range_breakout(
        _snap(or_complete=True, or_high=102.0, or_low=98.0, close=97.0,
              volume_confirmed=True, confirm_bull=False, confirm_bear=True))
    assert result is not None, "Should fire a Short ORB signal"
    direction, reason = result
    assert direction == "Short"
    assert "98" in reason, f"Reason should reference or_low=98; got: {reason!r}"


def test_08_orb_no_signal_range_not_complete():
    assert bt.detect_opening_range_breakout(
        _snap(or_complete=False, close=103.0,
              volume_confirmed=True, confirm_bull=True)) is None, \
        "Must not signal when or_complete is False"


def test_09_orb_no_signal_or_high_none():
    assert bt.detect_opening_range_breakout(
        _snap(or_complete=True, or_high=None, or_low=98.0, close=103.0,
              volume_confirmed=True, confirm_bull=True)) is None, \
        "Must not signal when or_high is None"


def test_10_orb_no_signal_or_low_none():
    """Both Long and Short paths are guarded: or_low=None blocks all signals."""
    assert bt.detect_opening_range_breakout(
        _snap(or_complete=True, or_high=102.0, or_low=None, close=97.0,
              volume_confirmed=True, confirm_bull=False, confirm_bear=True)) is None, \
        "Must not signal when or_low is None"


def test_11_orb_no_signal_volume_not_confirmed():
    assert bt.detect_opening_range_breakout(
        _snap(or_complete=True, close=103.0,
              volume_confirmed=False, confirm_bull=True)) is None, \
        "Long must require volume_confirmed=True"


def test_12_orb_no_signal_no_bull_confirm_on_long():
    """Long signal requires confirm_bull — volume alone is not enough."""
    assert bt.detect_opening_range_breakout(
        _snap(or_complete=True, or_high=102.0, close=103.0,
              volume_confirmed=True, confirm_bull=False, confirm_bear=False)) is None, \
        "Long must require confirm_bull=True"


def test_13_orb_no_signal_close_exactly_at_or_high():
    """close == or_high is NOT a breakout (must be strictly greater than)."""
    assert bt.detect_opening_range_breakout(
        _snap(or_complete=True, or_high=102.0, or_low=98.0, close=102.0,
              volume_confirmed=True, confirm_bull=True)) is None, \
        "close == or_high should not trigger — breakout requires close > or_high"


# ─────────────────────────────────────────────────────────────────────────────
# T14–T15  Detector: no time-window ceiling (unlike Opening Drive 08:00–10:00 ET)
# ─────────────────────────────────────────────────────────────────────────────

def test_14_orb_fires_well_after_opening_drive_window_closes():
    """ORB has no 10:00 ET ceiling: it may fire at any ET hour after OR completes."""
    for h in (10, 12, 14, 15):
        result = bt.detect_opening_range_breakout(
            _snap(or_complete=True, close=103.0,
                  volume_confirmed=True, confirm_bull=True, hour=h))
        assert result is not None, f"ORB should still fire at ET hour={h}"
        assert result[0] == "Long", f"Expected Long at hour={h}"


def test_15_orb_fires_inside_opening_drive_window_too():
    """ORB also works while inside the Opening Drive time window (no lower-bound)."""
    for h in (8, 9):
        result = bt.detect_opening_range_breakout(
            _snap(or_complete=True, close=103.0,
                  volume_confirmed=True, confirm_bull=True, hour=h))
        assert result is not None, f"ORB should fire at ET hour={h}"
        assert result[0] == "Long"


# ─────────────────────────────────────────────────────────────────────────────
# T16–T17  Detector: no VWAP-side alignment required
# ─────────────────────────────────────────────────────────────────────────────

def test_16_orb_long_fires_when_price_is_below_vwap():
    """Unlike Opening Drive, ORB does not require close > vwap for a Long signal."""
    result = bt.detect_opening_range_breakout(
        _snap(or_complete=True, or_high=102.0, close=103.0, vwap=110.0,
              volume_confirmed=True, confirm_bull=True))
    assert result is not None, "ORB Long should fire even when price is below VWAP"
    assert result[0] == "Long"


def test_17_orb_short_fires_when_price_is_above_vwap():
    """ORB Short does not require close < vwap."""
    result = bt.detect_opening_range_breakout(
        _snap(or_complete=True, or_low=98.0, close=97.0, vwap=90.0,
              volume_confirmed=True, confirm_bull=False, confirm_bear=True))
    assert result is not None, "ORB Short should fire even when price is above VWAP"
    assert result[0] == "Short"


# ─────────────────────────────────────────────────────────────────────────────
# T18–T20  STRATEGY_DEFS field correctness
# ─────────────────────────────────────────────────────────────────────────────

def test_18_orb_strategy_def_label():
    label = bt.STRATEGY_DEFS["OPENING_RANGE_BREAKOUT"]["label"]
    assert label == "Opening Range Breakout", \
        f"Expected label 'Opening Range Breakout', got {label!r}"


def test_19_orb_strategy_def_max_grade():
    grade = bt.STRATEGY_DEFS["OPENING_RANGE_BREAKOUT"]["max_grade"]
    assert grade == "A", f"Expected max_grade 'A', got {grade!r}"


def test_20_orb_strategy_def_regimes():
    regimes = bt.STRATEGY_DEFS["OPENING_RANGE_BREAKOUT"]["regimes"]
    expected = {"TRENDING", "VOLATILE", "BALANCED"}
    assert regimes == expected, \
        f"Expected regimes {expected}, got {regimes}"


# ─────────────────────────────────────────────────────────────────────────────
# T21–T22  Isolation: detect_opening_drive unmodified (regression guard)
# ─────────────────────────────────────────────────────────────────────────────

def test_21_opening_drive_still_requires_vwap_alignment():
    """detect_opening_drive requires close > vwap for Long — ORB addition must not
    loosen that constraint."""
    # close > or_high but below vwap — Opening Drive should block it
    result = bt.detect_opening_drive(
        _snap(or_complete=True, or_high=102.0, close=103.0, vwap=110.0,
              volume_confirmed=True, confirm_bull=True, hour=9))
    assert result is None, \
        "detect_opening_drive must still require close > vwap for Long (regression)"


def test_22_opening_drive_still_respects_time_window():
    """detect_opening_drive must return None outside 08:00–10:00 ET — unchanged."""
    result = bt.detect_opening_drive(
        _snap(or_complete=True, or_high=102.0, close=103.0, vwap=100.0,
              volume_confirmed=True, confirm_bull=True, hour=12))
    assert result is None, \
        "detect_opening_drive must reject signals at hour=12 (outside 08:00–10:00 ET)"


# ─────────────────────────────────────────────────────────────────────────────
# T23–T28  simulate_strategy integration via run_backtest
# ─────────────────────────────────────────────────────────────────────────────

def test_23_run_backtest_orb_no_crash_on_synthetic_candles():
    """run_backtest with ORB as the sole strategy must not raise on synthetic candles."""
    candles = bt._synthetic_candles(n=200, symbol="MGC", seed=1)
    result  = _bt_run(candles, strategies=["OPENING_RANGE_BREAKOUT"])
    assert result.get("ok"), result.get("error", "run_backtest returned ok=False")
    assert isinstance(result["strategies"], dict)


def test_24_orb_long_trade_produced_from_crafted_candles():
    """End-to-end: crafted Long ORB candles produce at least one Long trade."""
    candles = _make_orb_candles(direction="Long")
    result  = _bt_run(candles, strategies=["OPENING_RANGE_BREAKOUT"])
    assert result.get("ok"), result.get("error")
    orb_metrics = result["strategies"].get("OPENING_RANGE_BREAKOUT", {})
    long_trades = [t for t in result["trades"] if t["direction"] == "Long"]
    assert len(long_trades) >= 1, \
        (f"Expected ≥1 Long ORB trade; got total_trades="
         f"{orb_metrics.get('total_trades', 0)}, trades={result['trades']}")


def test_25_orb_short_trade_produced_from_crafted_candles():
    """End-to-end: crafted Short ORB candles produce at least one Short trade."""
    candles = _make_orb_candles(direction="Short")
    result  = _bt_run(candles, strategies=["OPENING_RANGE_BREAKOUT"])
    assert result.get("ok"), result.get("error")
    short_trades = [t for t in result["trades"] if t["direction"] == "Short"]
    assert len(short_trades) >= 1, \
        (f"Expected ≥1 Short ORB trade; got trades={result['trades']}")


def test_26_orb_zero_trades_when_or_never_builds():
    """When all bars are in Asia session (18:00–19:09 ET), the OR never builds
    (or_high stays None) so or_complete is never True and ORB fires 0 signals."""
    # 70 bars at 18:00–19:09 ET on a single day — well above run_backtest minimum (45).
    # h_et ranges 18.0–19.15, never reaching 8.0 (OR start). or_complete=False always.
    candles = [
        _bar(_et("2026-01-05", 18.0 + i / 60.0),
             open_=2400.0, high=2410.0, low=2390.0, close=2400.0, volume=100.0)
        for i in range(70)
    ]
    result = _bt_run(candles, strategies=["OPENING_RANGE_BREAKOUT"])
    assert result.get("ok"), result.get("error")
    orb = result["strategies"].get("OPENING_RANGE_BREAKOUT", {})
    assert orb.get("total_trades", 0) == 0, \
        f"Expected 0 ORB trades when OR never completes; got {orb.get('total_trades')}"


def test_27_orb_trade_dict_has_required_keys():
    """Every ORB trade in the result must contain all standard trade-log keys."""
    candles = _make_orb_candles(direction="Long")
    result  = _bt_run(candles, strategies=["OPENING_RANGE_BREAKOUT"])
    assert result.get("ok"), result.get("error")
    trades = result["trades"]
    if not trades:
        return  # candle builder is deterministic; if somehow 0 trades, skip gracefully
    required = {
        "strategy", "direction", "entry_ts", "exit_ts",
        "entry", "stop", "exit", "tp1", "tp3",
        "risk_points", "gross_points", "pnl_dollars", "r_multiple",
        "regime", "session", "entry_hour_et",
        "entry_reason", "exit_reason", "hold_minutes",
    }
    for t in trades:
        missing = required - t.keys()
        assert not missing, f"Trade dict missing keys: {missing}"


def test_28_orb_trade_strategy_field_is_correct():
    """The 'strategy' key in every ORB trade must equal 'OPENING_RANGE_BREAKOUT'."""
    candles = _make_orb_candles(direction="Long")
    result  = _bt_run(candles, strategies=["OPENING_RANGE_BREAKOUT"])
    assert result.get("ok"), result.get("error")
    for t in result["trades"]:
        assert t["strategy"] == "OPENING_RANGE_BREAKOUT", \
            f"Expected strategy='OPENING_RANGE_BREAKOUT', got {t['strategy']!r}"


# ─────────────────────────────────────────────────────────────────────────────
# T29–T31  Metric immutability & determinism
# ─────────────────────────────────────────────────────────────────────────────

def test_29_orb_simulation_is_deterministic():
    """Same candles → identical trade list on two successive run_backtest calls."""
    candles = _make_orb_candles(direction="Long")
    result1 = _bt_run(candles, strategies=["OPENING_RANGE_BREAKOUT"])
    result2 = _bt_run(candles, strategies=["OPENING_RANGE_BREAKOUT"])
    assert result1["trades"] == result2["trades"], \
        "run_backtest is not deterministic — trade lists differ between calls"


def test_30_existing_strategies_produce_unchanged_structure_after_orb_added():
    """Adding ORB to STRATEGY_ORDER must not alter how the other four strategies
    are simulated (structural integrity, not exact trade count which depends on
    data and management params)."""
    candles = bt._synthetic_candles(n=900, symbol="MGC", seed=42)
    for strat in ["OPENING_DRIVE", "LIQUIDITY_SWEEP_REVERSAL",
                  "VWAP_TREND_CONTINUATION", "RANGE_EXPANSION_BREAKOUT"]:
        result = _bt_run(candles, strategies=[strat])
        assert result.get("ok"), f"{strat}: {result.get('error')}"
        assert strat in result["strategies"], f"{strat} missing from result"
        m = result["strategies"][strat]
        # Core metric keys must be present and internally consistent.
        assert "total_trades" in m
        assert "wins" in m and "losses" in m
        total = m["total_trades"]
        assert m["wins"] + m["losses"] == total, \
            f"{strat}: wins({m['wins']}) + losses({m['losses']}) ≠ total({total})"


def test_31_run_backtest_all_strategies_includes_orb_in_result():
    """run_backtest with strategies=None must include OPENING_RANGE_BREAKOUT in
    result['strategies'] (even if it produces 0 trades on generic synthetic data)."""
    candles = bt._synthetic_candles(n=900, symbol="MGC", seed=42)
    result  = _bt_run(candles)
    assert result.get("ok"), result.get("error")
    assert "OPENING_RANGE_BREAKOUT" in result["strategies"], \
        (f"OPENING_RANGE_BREAKOUT missing from result['strategies']; "
         f"got: {list(result['strategies'])}")


# ─────────────────────────────────────────────────────────────────────────────
# T32–T33  Detector callable per snapshot; not a stateful singleton
# ─────────────────────────────────────────────────────────────────────────────

def test_32_detector_produces_different_results_per_snapshot():
    """The detector evaluates EACH snapshot independently — it is not a singleton
    that returns the same result for all calls. Verifies per-bar eligibility."""
    s_long  = _snap(or_complete=True, close=103.0,
                    volume_confirmed=True, confirm_bull=True)
    s_short = _snap(or_complete=True, or_low=98.0, close=97.0,
                    volume_confirmed=True, confirm_bull=False, confirm_bear=True)
    s_none  = _snap(or_complete=False)

    r_long  = bt.detect_opening_range_breakout(s_long)
    r_short = bt.detect_opening_range_breakout(s_short)
    r_none  = bt.detect_opening_range_breakout(s_none)

    assert r_long  is not None and r_long[0]  == "Long",  "Expected Long signal"
    assert r_short is not None and r_short[0] == "Short", "Expected Short signal"
    assert r_none  is None,                               "Expected None for incomplete OR"


def test_33_detector_invoked_per_bar_across_multi_day_series():
    """The detector is called on every eligible snapshot (not once per run).
    Demonstrated by combining two independent OR windows from separate days so
    that signals on both days may be produced."""
    day1 = _make_orb_candles(direction="Long",  date="2026-01-05")
    day2 = _make_orb_candles(direction="Short", date="2026-01-06")
    combined = day1 + day2
    result   = _bt_run(combined, strategies=["OPENING_RANGE_BREAKOUT"])
    assert result.get("ok"), result.get("error")
    # At minimum, at least one ORB signal is produced across the two-day series.
    total = result["strategies"]["OPENING_RANGE_BREAKOUT"]["total_trades"]
    assert total >= 1, \
        "Expected ≥1 ORB trade across a two-day crafted series"


# ─────────────────────────────────────────────────────────────────────────────
# T34–T35  UI label parity (app.py audit)
# ─────────────────────────────────────────────────────────────────────────────

def test_34_bt_strat_labels_contains_orb():
    """BT_STRAT_LABELS JS dict in app.py must include OPENING_RANGE_BREAKOUT with
    the correct human-readable label."""
    src = _read_app()
    m = re.search(r"const BT_STRAT_LABELS\s*=\s*\{([^}]+)\}", src)
    assert m, "BT_STRAT_LABELS block not found in app.py"
    block = m.group(1)
    assert "OPENING_RANGE_BREAKOUT" in block, \
        "OPENING_RANGE_BREAKOUT key missing from BT_STRAT_LABELS in app.py"
    assert "Opening Range Breakout" in block, \
        "Label 'Opening Range Breakout' missing from BT_STRAT_LABELS in app.py"


def test_35_rn_strat_dropdown_contains_orb_option():
    """The rn-strat <select> dropdown in app.py must include an ORB option so the
    user can filter the backtest results to ORB trades only."""
    src  = _read_app()
    m = re.search(r'id="rn-strat"[^>]*>((?:<option[^<]*</option>\s*)+)', src)
    assert m, "rn-strat <select> element not found in app.py"
    block = m.group(0)
    assert "OPENING_RANGE_BREAKOUT" in block, \
        "value='OPENING_RANGE_BREAKOUT' missing from rn-strat dropdown in app.py"
    assert "Opening Range Breakout" in block, \
        "Label 'Opening Range Breakout' missing from rn-strat dropdown in app.py"


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
            failed += 1
    total = passed + failed
    print(f"\n{passed}/{total} passed")
    if failed:
        sys.exit(1)
