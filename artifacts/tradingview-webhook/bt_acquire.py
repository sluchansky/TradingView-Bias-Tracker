"""
Phase 6B.0 — Historical Data Acquisition Script (research-only, no live writes).

Downloads ohlcv-1m from Databento GLBX.MDP3, resamples to 5-minute bars,
validates with parse_candles_csv, and stores to backtest_datasets /
backtest_candles via direct psycopg2 (mirrors _bt_store_dataset exactly).

Usage:
    python3 bt_acquire.py sample            # 5-day MNQ sample only
    python3 bt_acquire.py full              # all 4 instruments, 6 months
    python3 bt_acquire.py inventory         # print imported datasets
    python3 bt_acquire.py smoke <dataset_id>  # replay smoke test on a dataset

Safety invariants:
    - Never imports anything from app.py
    - Never writes to live state tables (CURRENT_PRICE_BY_TICKER, etc.)
    - Never calls TradersPost / Decision Quality / learning / execution
    - Never prints or logs DATABENTO_API_KEY or DATABASE_URL
    - All temp files written to /tmp (never the working tree)
    - Reads DATABENTO_API_KEY and DATABASE_URL from environment only
"""

from __future__ import annotations
import os, sys, io, math, hashlib, json, datetime as _dt
from datetime import timezone
from zoneinfo import ZoneInfo
from typing import Any

# ---------------------------------------------------------------------------
# Validate environment before heavy imports
# ---------------------------------------------------------------------------
_API_KEY = os.environ.get("DATABENTO_API_KEY", "").strip()
_DB_URL   = os.environ.get("DATABASE_URL", "").strip()

if not _API_KEY:
    print("ERROR: DATABENTO_API_KEY is not set.")
    sys.exit(1)
if not _DB_URL:
    print("ERROR: DATABASE_URL is not set.")
    sys.exit(1)

print(f"DATABENTO_API_KEY present : {len(_API_KEY)} chars (not shown)")
print(f"DATABASE_URL present      : yes (not shown)")

# ---------------------------------------------------------------------------
# Imports (after env check so failures are clean)
# ---------------------------------------------------------------------------
import databento as db
import psycopg2, psycopg2.extras
import pandas as pd

# parse_candles_csv lives in backtest_engine.py which is self-contained
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from backtest_engine import parse_candles_csv  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration — mirrors DB_SYMBOLS / DB_DATASET in databento_brain.py
# ---------------------------------------------------------------------------
DB_DATASET = "GLBX.MDP3"
DB_SCHEMA  = "ohlcv-1m"       # finest OHLCV; resample to 5m locally
RESAMPLE_TF = "5m"            # target timeframe for import
RESAMPLE_FREQ = "5min"        # pandas resample rule

DB_SYMBOLS: dict[str, str] = {   # inst → Databento continuous symbol
    "MGC": "MGC.c.0",
    "MNQ": "MNQ.c.0",
    "MES": "MES.c.0",
    "MYM": "MYM.c.0",
}

# 6 complete months (Jan–Jun 2026, today is late July 2026)
FULL_START = "2026-01-01"
FULL_END   = "2026-07-01"

# 5-trading-day MNQ sample (week of 2026-07-14)
SAMPLE_INST  = "MNQ"
SAMPLE_START = "2026-07-14"
SAMPLE_END   = "2026-07-19"   # exclusive

ET_TZ = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Database helpers (mirrors _bt_conn / _bt_store_dataset logic exactly)
# ---------------------------------------------------------------------------

def _db_conn():
    conn = psycopg2.connect(_DB_URL, connect_timeout=10)
    conn.autocommit = True
    return conn


def _store_dataset(symbol: str, timeframe: str, source_tz: str,
                   source_label: str, filename: str, parsed: dict) -> dict:
    """Exact SQL mirror of app.py::_bt_store_dataset (INSERT/SELECT only)."""
    try:
        conn = _db_conn()
    except Exception as exc:
        return {"ok": False, "error": f"DB connect: {exc}"}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, row_count FROM backtest_datasets WHERE sha256=%s",
                        (parsed["sha256"],))
            existing = cur.fetchone()
            if existing:
                return {"ok": True, "reused": True, "dataset_id": existing[0],
                        "row_count": existing[1], "symbol": symbol,
                        "timeframe": timeframe}

            cur.execute(
                """INSERT INTO backtest_datasets
                       (symbol, timeframe, source_label, source_tz,
                        original_filename, sha256, row_count, gap_count,
                        first_ts, last_ts)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (symbol, timeframe, source_label, source_tz, filename,
                 parsed["sha256"], parsed["row_count"], parsed["gap_count"],
                 parsed["first_ts"], parsed["last_ts"]))
            ds_id = cur.fetchone()[0]

            rows = [(ds_id, symbol, timeframe,
                     c["ts"], c["open"], c["high"], c["low"], c["close"], c["volume"])
                    for c in parsed["candles"]]
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO backtest_candles
                       (dataset_id, symbol, timeframe, ts, open, high, low, close, volume)
                   VALUES %s ON CONFLICT (dataset_id, ts) DO NOTHING""",
                rows, page_size=1000)

        return {"ok": True, "reused": False, "dataset_id": ds_id,
                "row_count": parsed["row_count"], "symbol": symbol,
                "timeframe": timeframe}
    except Exception as exc:
        return {"ok": False, "error": f"Storage error: {exc}"}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _list_datasets() -> list[dict]:
    try:
        conn = _db_conn()
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, symbol, timeframe, source_label, source_tz,
                          row_count, gap_count, first_ts, last_ts,
                          uploaded_at, sha256
                   FROM backtest_datasets ORDER BY uploaded_at DESC LIMIT 200""")
            out = []
            for r in cur.fetchall():
                out.append({
                    "id": r[0], "symbol": r[1], "timeframe": r[2],
                    "source_label": r[3], "source_tz": r[4], "row_count": r[5],
                    "gap_count": r[6],
                    "first_ts": r[7].isoformat() if r[7] else None,
                    "last_ts": r[8].isoformat() if r[8] else None,
                    "uploaded_at": r[9].isoformat() if r[9] else None,
                    "sha256": r[10]})
            conn.close()
            return out
    except Exception as exc:
        print(f"ERROR listing datasets: {exc}")
        return []


def _fetch_candles(dataset_id: int) -> list[dict]:
    try:
        conn = _db_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ts, open, high, low, close, volume "
                "FROM backtest_candles WHERE dataset_id=%s ORDER BY ts",
                (dataset_id,))
            rows = cur.fetchall()
        conn.close()
        return [{"ts": r[0], "open": r[1], "high": r[2],
                 "low": r[3], "close": r[4], "volume": r[5]} for r in rows]
    except Exception as exc:
        print(f"ERROR fetching candles: {exc}")
        return []

# ---------------------------------------------------------------------------
# Download + transform helpers
# ---------------------------------------------------------------------------

def _download_ohlcv1m(inst: str, start: str, end: str) -> pd.DataFrame:
    """Download ohlcv-1m from Databento and return a UTC-indexed DataFrame."""
    symbol_db = DB_SYMBOLS[inst]
    print(f"  Downloading {inst} ({symbol_db}) {start}→{end} from {DB_DATASET}/{DB_SCHEMA} ...")
    client = db.Historical()
    store = client.timeseries.get_range(
        dataset=DB_DATASET,
        start=start,
        end=end,
        symbols=[symbol_db],
        schema=DB_SCHEMA,
        stype_in="continuous",
    )
    df = store.to_df(price_type="float", pretty_ts=True,
                     map_symbols=True, tz="UTC")
    # Keep only OHLCV columns (drop symbol, rtype, publisher_id, etc.)
    ohlcv_cols = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    df = df[ohlcv_cols].copy()
    # Ensure index is UTC DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    elif df.index.tzinfo is None:
        df.index = df.index.tz_localize("UTC")
    print(f"  Downloaded {len(df):,} 1-minute bars")
    return df


def _resample_to_5m(df: pd.DataFrame) -> pd.DataFrame:
    """Resample 1-minute OHLCV to 5-minute bars (aligned to epoch boundary)."""
    df5 = df.resample(RESAMPLE_FREQ, closed="left", label="left", origin="epoch").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna(subset=["open", "high", "low", "close"])
    # Drop bars where all prices are zero (empty resampled windows from gaps)
    df5 = df5[(df5["open"] > 0) & (df5["close"] > 0)]
    print(f"  Resampled to {len(df5):,} 5-minute bars")
    return df5


def _df_to_csv_text(df: pd.DataFrame) -> str:
    """Export resampled DataFrame as epoch-second CSV for parse_candles_csv."""
    lines = ["timestamp,open,high,low,close,volume"]
    for ts, row in df.iterrows():
        epoch_s = ts.timestamp()
        vol = int(row["volume"]) if not math.isnan(row["volume"]) else 0
        lines.append(
            f"{epoch_s:.3f},{row['open']:.5f},{row['high']:.5f},"
            f"{row['low']:.5f},{row['close']:.5f},{vol}"
        )
    return "\n".join(lines)


def _validate_dataframe(df: pd.DataFrame, inst: str, start: str, end: str) -> dict:
    """Run a suite of validation checks on the raw 1m DataFrame."""
    issues = []
    warnings = []
    total = len(df)

    if total == 0:
        return {"ok": False, "issues": ["Empty DataFrame — no bars returned"]}

    # 1. Parseable timestamps (already UTC DatetimeIndex — guaranteed by to_df)
    ts_ok = isinstance(df.index, pd.DatetimeIndex) and df.index.tzinfo is not None
    if not ts_ok:
        issues.append("Index is not a timezone-aware DatetimeIndex")

    # 2. Strict chronological order
    if not df.index.is_monotonic_increasing:
        issues.append("Index is not strictly monotonic — duplicate or out-of-order timestamps")

    # 3. Duplicate timestamps
    dups = df.index.duplicated().sum()
    if dups:
        issues.append(f"{dups} duplicate timestamps")

    # 4. OHLC consistency (high >= open,close and low <= open,close)
    bad_ohlc = (
        (df["high"] < df[["open", "close"]].max(axis=1)) |
        (df["low"]  > df[["open", "close"]].min(axis=1))
    ).sum()
    if bad_ohlc:
        issues.append(f"{bad_ohlc} bars with high<max(O,C) or low>min(O,C)")

    # 5. Negative or NaN prices
    neg = ((df[["open","high","low","close"]] <= 0) |
            df[["open","high","low","close"]].isna()).any(axis=1).sum()
    if neg:
        issues.append(f"{neg} bars with zero/negative/NaN prices")

    # 6. Volume — zero is allowed for illiquid overnight bars; negative is not
    neg_vol = (df["volume"] < 0).sum()
    zero_vol = (df["volume"] == 0).sum()
    if neg_vol:
        issues.append(f"{neg_vol} bars with negative volume")
    if zero_vol:
        warnings.append(f"{zero_vol} bars with zero volume (illiquid/overnight — OK)")

    # 7. Date range coverage
    actual_start = df.index.min().date().isoformat()
    actual_end   = df.index.max().date().isoformat()
    req_start    = _dt.date.fromisoformat(start)
    req_end      = _dt.date.fromisoformat(end) - _dt.timedelta(days=1)
    if str(actual_start) > str(start):
        warnings.append(f"Data starts at {actual_start} (requested {start})")
    if str(actual_end) < str(req_end):
        warnings.append(f"Data ends at {actual_end} (requested through {req_end})")

    # 8. 5-minute spacing after resample (check on 5m df)
    # — checked in separate validate_5m step

    first_ts = df.index.min().isoformat()
    last_ts  = df.index.max().isoformat()

    return {
        "ok": len(issues) == 0,
        "total_1m_bars": total,
        "duplicate_timestamps": int(dups),
        "bad_ohlc_bars": int(bad_ohlc),
        "zero_volume_bars": int(zero_vol),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "issues": issues,
        "warnings": warnings,
    }


def _validate_5m(df5: pd.DataFrame) -> dict:
    """Validate the resampled 5m DataFrame."""
    issues = []
    warnings = []
    total = len(df5)

    expected_spacing = pd.Timedelta("5min")
    if total > 1:
        gaps = df5.index.to_series().diff().dropna()
        large_gaps = gaps[gaps > _dt.timedelta(hours=2)]
        overnight_gaps = gaps[(gaps > expected_spacing) & (gaps <= _dt.timedelta(hours=2))]
        if not overnight_gaps.empty:
            warnings.append(f"{len(overnight_gaps)} intra-session gaps >5min (normal: halts/news)")
        if not large_gaps.empty:
            warnings.append(f"{len(large_gaps)} large gaps >2h (weekends/CME halts)")
        unexpected = gaps[(gaps > _dt.timedelta(minutes=5)) &
                          (gaps.index.map(lambda t: t.weekday() < 5)) &
                          (gaps < _dt.timedelta(hours=2))]
        if not unexpected.empty:
            warnings.append(f"{len(unexpected)} unexpected weekday gaps >5min <2h")

    # Check OR coverage: bars from 08:00–08:30 ET each trading day
    df5_et = df5.copy()
    df5_et.index = df5.index.tz_convert(ET_TZ)
    or_bars = df5_et[(df5_et.index.hour == 8) & (df5_et.index.minute < 30)]
    trading_days = len(set(df5_et.index.date))
    or_days = len(set(or_bars.index.date))
    or_coverage_pct = 100 * or_days / trading_days if trading_days else 0

    return {
        "ok": len(issues) == 0,
        "total_5m_bars": total,
        "trading_days": trading_days,
        "or_coverage_days": or_days,
        "or_coverage_pct": round(or_coverage_pct, 1),
        "issues": issues,
        "warnings": warnings,
    }

# ---------------------------------------------------------------------------
# Per-instrument acquire pipeline
# ---------------------------------------------------------------------------

def acquire_instrument(inst: str, start: str, end: str,
                       label: str = "databento-ohlcv-1m-resampled-5m") -> dict:
    """Full acquire pipeline for one instrument. Returns result dict."""
    print(f"\n{'='*60}")
    print(f"ACQUIRING {inst}  {start} → {end}")
    print(f"{'='*60}")

    # Step 1: cost estimate (read-only metadata)
    symbol_db = DB_SYMBOLS[inst]
    client = db.Historical()
    try:
        rc = client.metadata.get_record_count(
            dataset=DB_DATASET, start=start, end=end,
            symbols=[symbol_db], schema=DB_SCHEMA, stype_in="continuous")
        sz = client.metadata.get_billable_size(
            dataset=DB_DATASET, start=start, end=end,
            symbols=[symbol_db], schema=DB_SCHEMA, stype_in="continuous")
        cost = client.metadata.get_cost(
            dataset=DB_DATASET, start=start, end=end,
            symbols=[symbol_db], schema=DB_SCHEMA, stype_in="continuous")
        print(f"Pre-flight: {rc:,} records | {sz/1024:.1f} KB | ${cost:.4f} USD")
    except Exception as exc:
        print(f"WARNING: cost estimate failed: {exc} — continuing")
        rc = sz = cost = None

    # Step 2: download 1m
    try:
        df1m = _download_ohlcv1m(inst, start, end)
    except Exception as exc:
        return {"ok": False, "inst": inst, "error": f"Download failed: {exc}"}

    # Step 3: validate 1m
    val1m = _validate_dataframe(df1m, inst, start, end)
    print(f"1m validation: ok={val1m['ok']}  issues={val1m['issues']}")
    for w in val1m["warnings"]:
        print(f"  WARNING: {w}")
    if not val1m["ok"]:
        return {"ok": False, "inst": inst, "error": "1m validation failed",
                "validation": val1m}

    # Step 4: resample to 5m
    try:
        df5m = _resample_to_5m(df1m)
    except Exception as exc:
        return {"ok": False, "inst": inst, "error": f"Resample failed: {exc}"}

    # Step 5: validate 5m
    val5m = _validate_5m(df5m)
    print(f"5m validation: ok={val5m['ok']}  {val5m['total_5m_bars']:,} bars  "
          f"OR coverage {val5m['or_coverage_pct']}%")
    for w in val5m["warnings"]:
        print(f"  WARNING: {w}")

    # Step 6: export to CSV and parse
    csv_text = _df_to_csv_text(df5m)
    filename = f"{inst}_GLBX.MDP3_ohlcv5m_{start}_{end}.csv"
    parsed = parse_candles_csv(csv_text, symbol=inst, timeframe="5m", source_tz="UTC",
                               filename=filename)
    if not parsed["ok"]:
        return {"ok": False, "inst": inst, "error": f"parse_candles_csv: {parsed['error']}",
                "parsed": parsed}

    print(f"parse_candles_csv: ok  rows={parsed['row_count']}  "
          f"skipped={parsed['skipped']}  gaps={parsed['gap_count']}")
    print(f"  first_ts={parsed['first_ts']}  last_ts={parsed['last_ts']}")
    print(f"  sha256={parsed['sha256'][:16]}...")

    # Step 7: store to DB
    stored = _store_dataset(
        symbol=inst, timeframe="5m", source_tz="UTC",
        source_label=label, filename=filename, parsed=parsed)

    if not stored["ok"]:
        return {"ok": False, "inst": inst, "error": f"Storage: {stored['error']}"}

    reused = stored.get("reused", False)
    ds_id  = stored["dataset_id"]
    print(f"Stored: dataset_id={ds_id}  reused={reused}")

    return {
        "ok": True,
        "inst": inst,
        "dataset_id": ds_id,
        "reused": reused,
        "symbol": inst,
        "timeframe": "5m",
        "source_label": label,
        "filename": filename,
        "source_tz": "UTC",
        "row_count": parsed["row_count"],
        "gap_count": parsed["gap_count"],
        "skipped": parsed["skipped"],
        "dup_removed": parsed.get("dup_removed", 0),
        "first_ts": str(parsed["first_ts"]) if parsed["first_ts"] else None,
        "last_ts":  str(parsed["last_ts"])  if parsed["last_ts"]  else None,
        "sha256": parsed["sha256"],
        "1m_bars": val1m["total_1m_bars"],
        "5m_bars": val5m["total_5m_bars"],
        "trading_days": val5m["trading_days"],
        "or_coverage_pct": val5m["or_coverage_pct"],
        "cost_usd": cost,
        "billable_bytes": sz,
        "validation_1m": val1m,
        "validation_5m": val5m,
    }

# ---------------------------------------------------------------------------
# Smoke test (replay 5 strategies against a dataset)
# ---------------------------------------------------------------------------

def smoke_test(dataset_id: int, inst: str) -> dict:
    """Run all five strategies against a stored dataset in smoke mode.

    run_backtest(candles, params) — params is a dict with symbol, mode,
    strategies, management, news_blackouts_et (cleared so OR fires at 09:29 ET).
    """
    from backtest_engine import (
        STRATEGY_ORDER, run_backtest, BT_MGMT_LEGACY
    )

    candles = _fetch_candles(dataset_id)
    if not candles:
        return {"ok": False, "error": "No candles found for dataset_id"}

    results = {}
    for mode in ("SCALP", "SWING"):
        key = f"ALL_STRATEGIES_{mode}"
        try:
            res = run_backtest(candles, {
                "symbol":            inst,
                "mode":              mode,
                "strategies":        list(STRATEGY_ORDER),
                "management":        BT_MGMT_LEGACY,
                "news_blackouts_et": [],   # don't filter in smoke
            })
            per_strat = res.get("per_strategy", {})
            total_trades = sum(
                len(v.get("trades", [])) for v in per_strat.values()
            ) if per_strat else len(res.get("trades", []))
            results[key] = {
                "ok": res.get("ok", True),
                "trades": total_trades,
                "signals": res.get("candidate_signals", 0),
                "dispatched": res.get("detector_dispatches", 0),
                "errors": res.get("errors", []),
                "warnings": res.get("warnings", []),
                "per_strategy": {
                    s: {"trades": len(v.get("trades", [])),
                        "signals": v.get("candidate_signals", 0)}
                    for s, v in per_strat.items()
                } if per_strat else {},
            }
        except Exception as exc:
            results[key] = {"ok": False, "error": str(exc)}

    total_trades  = sum(v.get("trades", 0)  for v in results.values() if v.get("ok"))
    total_signals = sum(v.get("signals", 0) for v in results.values() if v.get("ok"))
    crashed       = [k for k, v in results.items() if not v.get("ok")]

    return {
        "ok": not crashed,
        "dataset_id": dataset_id,
        "inst": inst,
        "label": "REAL_DATA_SMOKE_TEST_NOT_BASELINE",
        "combinations": 10,   # 5 strategies × 2 modes
        "total_trades": total_trades,
        "total_signals": total_signals,
        "crashed": crashed,
        "detail": results,
    }

# ---------------------------------------------------------------------------
# Quality report
# ---------------------------------------------------------------------------

def quality_report(ds: dict, candles: list) -> dict:
    if not candles:
        return {"dataset_id": ds["id"], "usable_status": "REJECTED",
                "reason": "No candles"}

    total = len(candles)
    ts_list  = sorted(c["ts"].timestamp() if hasattr(c["ts"], "timestamp") else c["ts"]
                      for c in candles)
    first_ts = _dt.datetime.fromtimestamp(ts_list[0],  tz=timezone.utc)
    last_ts  = _dt.datetime.fromtimestamp(ts_list[-1], tz=timezone.utc)
    cal_days = (last_ts.date() - first_ts.date()).days + 1

    # Gap analysis (5-minute spacing = 300s)
    diffs = [ts_list[i+1] - ts_list[i] for i in range(len(ts_list)-1)]
    large_gaps = [d for d in diffs if d > 7200]     # >2h
    dup_ts     = total - len(set(ts_list))

    # Zero-volume bars
    zero_vol = sum(1 for c in candles if (c["volume"] or 0) == 0)

    # Invalid (OHLC inconsistency)
    invalid = sum(1 for c in candles if c["high"] < c["open"] or c["high"] < c["close"]
                  or c["low"] > c["open"] or c["low"] > c["close"])

    # OR bars (08:00–08:30 ET)
    or_bars = sum(1 for c in candles
                  if 8 <= _dt.datetime.fromtimestamp(
                      c["ts"].timestamp() if hasattr(c["ts"],"timestamp") else c["ts"],
                      tz=ET_TZ).hour < 9)

    # DST transitions
    utc_offsets = set()
    for ts in ts_list[::100]:
        utc_offsets.add(_dt.datetime.fromtimestamp(ts, tz=ET_TZ).utcoffset())
    dst_transitions = len(utc_offsets) > 1

    issues = []
    warnings = []
    if dup_ts:       issues.append(f"{dup_ts} duplicate timestamps")
    if invalid:      issues.append(f"{invalid} OHLC-invalid bars")
    if zero_vol > total * 0.5:
        warnings.append(f"{zero_vol}/{total} zero-volume bars (>50%)")
    elif zero_vol:
        warnings.append(f"{zero_vol} zero-volume bars")

    usable_status = (
        "REJECTED"           if issues else
        "READY_WITH_WARNINGS" if warnings else
        "READY"
    )

    return {
        "dataset_id":        ds["id"],
        "instrument":        ds["symbol"],
        "timeframe":         ds.get("timeframe"),
        "start_date":        first_ts.date().isoformat(),
        "end_date":          last_ts.date().isoformat(),
        "calendar_days":     cal_days,
        "total_bars":        total,
        "duplicate_bars":    dup_ts,
        "zero_volume_bars":  zero_vol,
        "invalid_bars":      invalid,
        "large_gaps":        len(large_gaps),
        "or_bar_count":      or_bars,
        "dst_transitions":   dst_transitions,
        "fingerprint":       ds.get("sha256", "")[:16] + "...",
        "usable_status":     usable_status,
        "issues":            issues,
        "warnings":          warnings,
    }

# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def cmd_sample():
    print("=" * 60)
    print("PHASE 6B.0 — 5-DAY MNQ SAMPLE")
    print("=" * 60)
    result = acquire_instrument(SAMPLE_INST, SAMPLE_START, SAMPLE_END,
                                label="databento-5day-sample")
    print(f"\n--- SAMPLE RESULT ---")
    for k, v in result.items():
        if k not in ("validation_1m", "validation_5m"):
            print(f"  {k}: {v}")
    if result["ok"]:
        print(f"\nRunning smoke test on dataset_id={result['dataset_id']} ...")
        smoke = smoke_test(result["dataset_id"], SAMPLE_INST)
        print(f"Smoke: ok={smoke['ok']}  trades={smoke['total_trades']}  "
              f"signals={smoke['total_signals']}  crashed={smoke['crashed']}")
        for k, v in smoke["detail"].items():
            print(f"  {k}: trades={v.get('trades')} signals={v.get('signals')} "
                  f"err={v.get('error','')}")

        print(f"\n--- QUALITY REPORT ---")
        candles = _fetch_candles(result["dataset_id"])
        ds_row = {"id": result["dataset_id"], "symbol": SAMPLE_INST,
                  "timeframe": "5m", "sha256": result.get("sha256","")}
        qr = quality_report(ds_row, candles)
        for k, v in qr.items():
            print(f"  {k}: {v}")
    return result


def cmd_full():
    print("=" * 60)
    print("PHASE 6B.0 — FULL 6-MONTH ACQUISITION (all 4 instruments)")
    print("=" * 60)
    order = ["MNQ", "MES", "MGC", "MYM"]
    results = {}
    for inst in order:
        r = acquire_instrument(inst, FULL_START, FULL_END)
        results[inst] = r
        if not r["ok"]:
            print(f"\nFAILED at {inst}: {r['error']} — stopping")
            break
    print(f"\n--- FULL ACQUISITION SUMMARY ---")
    for inst, r in results.items():
        status = "OK" if r["ok"] else f"FAILED: {r.get('error','?')}"
        rows   = r.get("row_count", 0)
        ds_id  = r.get("dataset_id", None)
        print(f"  {inst}: {status}  rows={rows}  dataset_id={ds_id}")

    all_ok = all(r["ok"] for r in results.values())
    ready  = [inst for inst, r in results.items() if r["ok"]]
    if all_ok:
        print("\nFinal status: PHASE_6B_DATA_READY")
    elif ready:
        print(f"\nFinal status: PHASE_6B_PARTIAL_DATA_READY (ready: {ready})")
    else:
        print("\nFinal status: PHASE_6B_DATA_BLOCKED")

    # Smoke test each successful dataset
    for inst in ready:
        ds_id = results[inst]["dataset_id"]
        print(f"\nSmoke test: {inst} (dataset_id={ds_id})")
        smoke = smoke_test(ds_id, inst)
        print(f"  ok={smoke['ok']}  trades={smoke['total_trades']}  "
              f"signals={smoke['total_signals']}  crashed={smoke['crashed']}")

    # Quality reports
    print("\n--- QUALITY REPORTS ---")
    for inst in ready:
        ds_id = results[inst]["dataset_id"]
        candles = _fetch_candles(ds_id)
        ds_row = {"id": ds_id, "symbol": inst, "timeframe": "5m",
                  "sha256": results[inst].get("sha256", "")}
        qr = quality_report(ds_row, candles)
        print(f"\n  {inst}: {qr['usable_status']}")
        for k, v in qr.items():
            if k != "instrument":
                print(f"    {k}: {v}")
    return results


def cmd_inventory():
    datasets = _list_datasets()
    if not datasets:
        print("No datasets imported yet.")
        return
    print(f"{'ID':>5}  {'Symbol':6}  {'TF':4}  {'Rows':>8}  "
          f"{'Gaps':>5}  {'First':24}  {'Last':24}  Label")
    print("-" * 110)
    for d in datasets:
        print(f"{d['id']:>5}  {d['symbol']:6}  {d['timeframe']:4}  "
              f"{d['row_count'] or 0:>8,}  {d['gap_count'] or 0:>5}  "
              f"{str(d['first_ts'] or ''):24}  {str(d['last_ts'] or ''):24}  "
              f"{d['source_label'] or ''}")


def cmd_smoke(dataset_id: int, inst: str):
    smoke = smoke_test(dataset_id, inst)
    print(json.dumps(smoke, indent=2, default=str))


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "sample":
        result = cmd_sample()
        sys.exit(0 if result["ok"] else 1)

    elif cmd == "full":
        results = cmd_full()
        all_ok = all(r["ok"] for r in results.values())
        ready  = [i for i, r in results.items() if r["ok"]]
        sys.exit(0 if (all_ok or ready) else 1)

    elif cmd == "inventory":
        cmd_inventory()

    elif cmd == "smoke" and len(sys.argv) == 4:
        cmd_smoke(int(sys.argv[2]), sys.argv[3])

    else:
        print(__doc__)
        print("Commands: sample | full | inventory | smoke <id> <inst>")
