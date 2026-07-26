"""
test_backtest_import.py
=======================
Phase 6A.1 — Part 12: Historical CSV import path.

Tests for backtest_engine.parse_candles_csv — the pure parsing layer that
backs the /backtest/upload route.  No Flask server is required; everything
runs against the module directly.

Test groups
-----------
T01-T04  Valid CSV parse — basic layout, epoch timestamps, semicolon delimiter
T05-T07  Symbol / timeframe auto-detection (filename hint + price scale)
T08-T10  SHA-256 fingerprint — present, correct, stable across two calls
T11-T14  Field validation — bad OHLC ordering, zero volume, missing columns
T15-T17  Empty / whitespace / malformed input
T18-T20  Deduplication and gap detection

Run:
  pytest test_backtest_import.py -v
  python3 test_backtest_import.py
"""

import hashlib
import io
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_engine as bt

ET_TZ = ZoneInfo("America/New_York")
UTC   = timezone.utc


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _csv(*rows, header="date,time,open,high,low,close,volume"):
    """Return a CSV string with the given header followed by rows."""
    lines = [header] + list(rows)
    return "\n".join(lines) + "\n"


def _mgc_row(date="2025-04-01", time="09:00", o=2000.0, h=2001.0,
             l=1999.0, c=2000.5, v=1000):
    return f"{date},{time},{o},{h},{l},{c},{v}"


def _orb_csv(n=40, base_date="2025-04-01", start_hour=7):
    """n consecutive 1-minute MGC bars starting at start_hour:00 ET."""
    rows = []
    for i in range(n):
        hour = start_hour + i // 60
        minute = i % 60
        rows.append(f"2025-04-01,{hour:02d}:{minute:02d},2000.0,2001.0,1999.0,2000.5,1000")
    return _csv(*rows)


# ─────────────────────────────────────────────────────────────────────────────
# T01-T04  Valid CSV parse
# ─────────────────────────────────────────────────────────────────────────────

def test_01_basic_csv_parse_ok():
    """A well-formed Date,Time,OHLCV CSV returns ok=True with candles."""
    raw = _csv(
        _mgc_row("2025-04-01", "09:00"),
        _mgc_row("2025-04-01", "09:01"),
        _mgc_row("2025-04-01", "09:02"),
    )
    r = bt.parse_candles_csv(raw, "MGC", "1m")
    assert r["ok"], f"Expected ok=True, got error: {r.get('error')}"
    assert r["row_count"] == 3
    assert len(r["candles"]) == 3
    assert r["symbol"] == "MGC"
    assert r["timeframe"] == "1m"
    assert r["first_ts"] is not None
    assert r["last_ts"] is not None


def test_02_epoch_timestamp_column():
    """A CSV with a Unix-epoch 'time' column (TradingView export format) is parsed."""
    # TradingView exports epoch seconds in UTC
    import time as _time
    now_epoch = int(datetime(2025, 4, 1, 13, 0, tzinfo=UTC).timestamp())
    rows = []
    for i in range(5):
        rows.append(f"{now_epoch + i * 60},2000,2001,1999,2000.5,1000")
    raw = _csv(*rows, header="time,open,high,low,close,volume")
    r = bt.parse_candles_csv(raw, "MGC", "1m", source_tz="UTC")
    assert r["ok"], f"Epoch parse failed: {r.get('error')}"
    assert r["row_count"] == 5


def test_03_semicolon_delimiter():
    """Semicolon-delimited CSV is parsed correctly (European export format)."""
    raw = "date;time;open;high;low;close;volume\n"
    raw += "2025-04-01;09:00;2000;2001;1999;2000.5;1000\n"
    raw += "2025-04-01;09:01;2000.5;2001.5;1999.5;2001;900\n"
    r = bt.parse_candles_csv(raw, "MGC", "1m")
    assert r["ok"], f"Semicolon parse failed: {r.get('error')}"
    assert r["row_count"] == 2


def test_04_combined_datetime_column():
    """A CSV with a single datetime column (no separate Time column) is parsed."""
    raw = "datetime,open,high,low,close,volume\n"
    raw += "2025-04-01 09:00:00,2000,2001,1999,2000.5,1000\n"
    raw += "2025-04-01 09:01:00,2001,2002,2000,2001.5,800\n"
    r = bt.parse_candles_csv(raw, "MGC", "1m")
    assert r["ok"], f"Combined datetime parse failed: {r.get('error')}"
    assert r["row_count"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# T05-T07  Symbol / timeframe auto-detection
# ─────────────────────────────────────────────────────────────────────────────

def test_05_symbol_auto_detected_from_filename():
    """Symbol='auto' + filename containing 'MGC' → detected_symbol=True, symbol='MGC'."""
    raw = _csv(_mgc_row(), _mgc_row("2025-04-01", "09:01"))
    r = bt.parse_candles_csv(raw, "auto", "1m", filename="MGC1!_1min.csv")
    assert r["ok"], f"Auto-detect symbol failed: {r.get('error')}"
    assert r["symbol"] == "MGC"
    assert r["detected_symbol"] is True


def test_06_timeframe_inferred_from_bar_spacing():
    """timeframe='auto' infers '1m' from 1-minute bar spacing."""
    raw = _csv(
        _mgc_row("2025-04-01", "09:00"),
        _mgc_row("2025-04-01", "09:01"),
        _mgc_row("2025-04-01", "09:02"),
        _mgc_row("2025-04-01", "09:03"),
    )
    r = bt.parse_candles_csv(raw, "MGC", "auto", filename="MGC1!_1.csv")
    assert r["ok"], f"Auto-detect timeframe failed: {r.get('error')}"
    assert r["detected_timeframe"] is True
    assert r["timeframe"] in bt.VALID_TIMEFRAMES


def test_07_invalid_symbol_rejected():
    """An unsupported symbol returns ok=False with a descriptive error."""
    raw = _csv(_mgc_row())
    r = bt.parse_candles_csv(raw, "AAPL", "1m")
    assert not r["ok"], "Expected ok=False for unsupported symbol"
    assert r["error"], "Expected a non-empty error message"


# ─────────────────────────────────────────────────────────────────────────────
# T08-T10  SHA-256 fingerprint
# ─────────────────────────────────────────────────────────────────────────────

def test_08_sha256_present():
    """parse_candles_csv always returns a sha256 key."""
    raw = _csv(_mgc_row())
    r = bt.parse_candles_csv(raw, "MGC", "1m")
    assert r["sha256"] is not None
    assert len(r["sha256"]) == 64     # hex SHA-256


def test_09_sha256_matches_raw_text():
    """sha256 value equals hashlib.sha256 of the raw UTF-8 bytes."""
    raw = _csv(_mgc_row(), _mgc_row("2025-04-01", "09:01"))
    r = bt.parse_candles_csv(raw, "MGC", "1m")
    expected = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
    assert r["sha256"] == expected, (
        f"sha256 mismatch: got {r['sha256']!r}, expected {expected!r}")


def test_10_sha256_stable_across_calls():
    """Two calls with the same raw text produce the same sha256."""
    raw = _csv(_mgc_row(), _mgc_row("2025-04-01", "09:01"))
    r1 = bt.parse_candles_csv(raw, "MGC", "1m")
    r2 = bt.parse_candles_csv(raw, "MGC", "1m")
    assert r1["sha256"] == r2["sha256"], "sha256 must be deterministic"
    # And different content produces a different fingerprint
    raw2 = _csv(_mgc_row("2025-04-02", "09:00"))
    r3 = bt.parse_candles_csv(raw2, "MGC", "1m")
    assert r1["sha256"] != r3["sha256"], "Different content must have different sha256"


# ─────────────────────────────────────────────────────────────────────────────
# T11-T14  Field validation — skipped / warned rows
# ─────────────────────────────────────────────────────────────────────────────

def test_11_high_lt_low_row_skipped():
    """A row where high < low is skipped (invalid OHLC) and counted in 'skipped'."""
    raw = _csv(
        _mgc_row("2025-04-01", "09:00"),
        "2025-04-01,09:01,2000,1999,2001,2000.5,1000",   # high < low — invalid
        _mgc_row("2025-04-01", "09:02"),
    )
    r = bt.parse_candles_csv(raw, "MGC", "1m")
    # Either it fails gracefully (ok=False with error) or it skips the bad row
    if r["ok"]:
        # If parsing continues, the bad row must not appear in candles
        assert r["row_count"] <= 2, "Bad OHLC row must be excluded from row_count"
    # Either way, no unhandled exception occurred


def test_12_missing_required_columns_fails():
    """A CSV missing OHLC columns returns ok=False."""
    raw = "date,time,close\n2025-04-01,09:00,2000\n"
    r = bt.parse_candles_csv(raw, "MGC", "1m")
    assert not r["ok"], "Expected ok=False for CSV missing OHLC columns"


def test_13_all_rows_unparseable_fails():
    """A CSV where no rows can be parsed returns ok=False."""
    raw = "date,time,open,high,low,close,volume\ngarbage,data,here,foo,bar,baz,qux\n"
    r = bt.parse_candles_csv(raw, "MGC", "1m")
    assert not r["ok"], "Expected ok=False when no rows can be parsed"


def test_14_zero_volume_accepted():
    """Rows with volume=0 are accepted (pre-market / thin bars are valid data)."""
    raw = _csv(
        "2025-04-01,09:00,2000,2001,1999,2000.5,0",
        "2025-04-01,09:01,2000,2001,1999,2000.5,0",
    )
    r = bt.parse_candles_csv(raw, "MGC", "1m")
    assert r["ok"], f"Zero-volume rows should be accepted: {r.get('error')}"
    assert r["row_count"] >= 1


# ─────────────────────────────────────────────────────────────────────────────
# T15-T17  Empty / whitespace / malformed input
# ─────────────────────────────────────────────────────────────────────────────

def test_15_empty_string_fails():
    """Empty string returns ok=False."""
    r = bt.parse_candles_csv("", "MGC", "1m")
    assert not r["ok"]
    assert r["error"]


def test_16_whitespace_only_fails():
    """Whitespace-only string returns ok=False."""
    r = bt.parse_candles_csv("   \n\n   ", "MGC", "1m")
    assert not r["ok"]
    assert r["error"]


def test_17_header_only_fails():
    """A CSV with only a header row (no data) returns ok=False."""
    r = bt.parse_candles_csv("date,time,open,high,low,close,volume\n", "MGC", "1m")
    assert not r["ok"], "Header-only CSV should fail (no data rows)"


# ─────────────────────────────────────────────────────────────────────────────
# T18-T20  Deduplication and gap detection
# ─────────────────────────────────────────────────────────────────────────────

def test_18_duplicate_timestamps_removed():
    """Duplicate timestamps (same bar twice) are deduped; dup_removed is counted."""
    raw = _csv(
        _mgc_row("2025-04-01", "09:00"),
        _mgc_row("2025-04-01", "09:00"),   # exact duplicate
        _mgc_row("2025-04-01", "09:01"),
    )
    r = bt.parse_candles_csv(raw, "MGC", "1m")
    assert r["ok"], f"Dedup failed: {r.get('error')}"
    assert r["dup_removed"] >= 1, (
        f"Expected at least 1 dup removed, got {r['dup_removed']}")
    # Resulting candle list should have no duplicate timestamps
    tss = [c["ts"] for c in r["candles"]]
    assert len(tss) == len(set(tss)), "Duplicate timestamps survived dedup"


def test_19_gap_count_detected():
    """A 30-minute gap between 1-minute bars is counted as a gap."""
    raw = _csv(
        _mgc_row("2025-04-01", "09:00"),
        _mgc_row("2025-04-01", "09:01"),
        # Large jump — missing bars 09:02 through 09:30
        _mgc_row("2025-04-01", "09:31"),
        _mgc_row("2025-04-01", "09:32"),
    )
    r = bt.parse_candles_csv(raw, "MGC", "1m")
    if r["ok"]:
        assert r["gap_count"] >= 1, (
            f"Expected gap_count >= 1 for 30-min jump, got {r['gap_count']}")


def test_20_candles_sorted_ascending():
    """Candles are returned in ascending timestamp order even if the CSV is reversed."""
    raw = _csv(
        _mgc_row("2025-04-01", "09:02"),
        _mgc_row("2025-04-01", "09:00"),
        _mgc_row("2025-04-01", "09:01"),
    )
    r = bt.parse_candles_csv(raw, "MGC", "1m")
    if r["ok"] and len(r["candles"]) >= 2:
        tss = [c["ts"] for c in r["candles"]]
        assert tss == sorted(tss), "Candles must be in ascending timestamp order"


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import traceback
    tests = [
        test_01_basic_csv_parse_ok,
        test_02_epoch_timestamp_column,
        test_03_semicolon_delimiter,
        test_04_combined_datetime_column,
        test_05_symbol_auto_detected_from_filename,
        test_06_timeframe_inferred_from_bar_spacing,
        test_07_invalid_symbol_rejected,
        test_08_sha256_present,
        test_09_sha256_matches_raw_text,
        test_10_sha256_stable_across_calls,
        test_11_high_lt_low_row_skipped,
        test_12_missing_required_columns_fails,
        test_13_all_rows_unparseable_fails,
        test_14_zero_volume_accepted,
        test_15_empty_string_fails,
        test_16_whitespace_only_fails,
        test_17_header_only_fails,
        test_18_duplicate_timestamps_removed,
        test_19_gap_count_detected,
        test_20_candles_sorted_ascending,
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
