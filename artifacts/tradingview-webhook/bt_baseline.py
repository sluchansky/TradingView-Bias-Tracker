"""bt_baseline.py — Phase 6B.1 immutable real-data baseline system.

MEASUREMENT ONLY.  This module never:
  • mutates live state (no writes to live tables)
  • calls Databento, TradersPost, or any execution gateway
  • feeds decision quality, learning, or strategy promotion
  • downloads market data (all data must already be in backtest_datasets)

All DB writes go to: baseline_configs, baseline_matrix_results,
baseline_trades, baseline_breakdowns.  Those tables are INSERT-only —
no UPDATE or DELETE of a committed baseline is ever performed.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DB_URL = os.environ.get("DATABASE_URL", "")

BASELINE_INSTRUMENTS = ["MNQ", "MES", "MGC", "MYM"]
BASELINE_MODES       = ["SCALP", "SWING"]
BASELINE_STRATEGIES  = [
    "OPENING_DRIVE",
    "LIQUIDITY_SWEEP_REVERSAL",
    "VWAP_TREND_CONTINUATION",
    "RANGE_EXPANSION_BREAKOUT",
    "OPENING_RANGE_BREAKOUT",
]

# dataset_ids that were validated as READY during Phase 6B.0
OFFICIAL_DATASET_IDS: Dict[str, int] = {
    "MNQ": 8,
    "MES": 9,
    "MGC": 10,
    "MYM": 11,
}

# Source labels that prove the dataset is real Databento data (not synthetic)
REAL_DATABENTO_LABELS = frozenset({
    "databento-ohlcv-1m-resampled-5m",
})

# Official baseline commission and slippage (mirrors existing backtest defaults)
BASELINE_COMMISSION_PER_SIDE = 0.62
BASELINE_SLIPPAGE_TICKS      = 1.0

ENGINE_VERSION = "backtest_engine-v1.0-phase6b1"

# ---------------------------------------------------------------------------
# Reliability labels (sample size only — NOT performance / approval labels)
# ---------------------------------------------------------------------------
def _reliability_label(n: int) -> str:
    if n < 20:  return "INSUFFICIENT"
    if n < 50:  return "LOW_SAMPLE"
    if n < 100: return "DEVELOPING"
    if n < 200: return "MODERATE"
    return "STRONG_SAMPLE"

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _bl_conn(timeout_ms: int = 300_000):
    """Autocommit connection dedicated to baseline tables."""
    if not _DB_URL:
        return None
    try:
        conn = psycopg2.connect(_DB_URL, connect_timeout=10)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = %s", (timeout_ms,))
        return conn
    except Exception as exc:
        import logging
        logging.warning("bl_conn failed: %s", exc)
        return None


def _jdump(obj: Any) -> Any:
    """Recursively replace non-finite floats with None and sets with sorted lists
    (JSONB-safe — psycopg2 Json cannot serialize set/frozenset)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (set, frozenset)):
        return sorted(_jdump(v) for v in obj)
    if isinstance(obj, dict):
        return {k: _jdump(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jdump(v) for v in obj]
    return obj


def _null(reason: str) -> Dict:
    """Canonical unsupported-metric sentinel."""
    return {"value": None, "reason": reason}

# ---------------------------------------------------------------------------
# Config freeze
# ---------------------------------------------------------------------------
def _get_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "--no-optional-locks", "rev-parse", "--short=7", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"


def _freeze_config(commit: str) -> Tuple[Dict, str]:
    """Return (config_dict, config_hash_16hex).  Imports from backtest_engine so
    the snapshot is always byte-identical to the running engine constants."""
    import backtest_engine as bt

    cfg: Dict = {
        "source_commit":     commit,
        "engine_module":     "backtest_engine",
        "engine_version":    ENGINE_VERSION,
        "management":        bt.BT_DEFAULT_MGMT,
        "management_label":  bt.BT_RUN_MGMT_LABELS.get(bt.BT_DEFAULT_MGMT, bt.BT_DEFAULT_MGMT),
        "commission_per_side": BASELINE_COMMISSION_PER_SIDE,
        "slippage_ticks":    BASELINE_SLIPPAGE_TICKS,
        "instruments":       BASELINE_INSTRUMENTS,
        "modes":             BASELINE_MODES,
        "strategies":        BASELINE_STRATEGIES,
        "strategy_registry_snapshot": {
            k: dict(v) for k, v in bt.STRATEGY_DEFS.items()
            if k in BASELINE_STRATEGIES
        },
        "detector_registry_snapshot": sorted(bt.DETECTORS.keys()),
        "disabled_strategies": sorted(bt.DISABLED_STRATEGIES),
        "bt_specs": {k: dict(v) for k, v in bt.BT_SPECS.items()},
        "bt_modes": {k: dict(v) for k, v in bt.BT_MODES.items()},
        "news_blackouts_et": [list(w) for w in bt.NEWS_BLACKOUTS_ET],
        "max_trades_per_session": bt.MAX_TRADES_PER_SESSION,
        "min_target_r":     bt.MIN_TARGET_R,
        "pivot_left":       bt.PIVOT_LEFT,
        "pivot_right":      bt.PIVOT_RIGHT,
        "atr_bars":         bt.ATR_BARS,
        "vol_min_bars":     bt.VOL_MIN_BARS,
        "rvol_lookback":    bt.RVOL_LOOKBACK,
        "opening_range_start_et":  bt.OPENING_RANGE_START_ET,
        "opening_range_build_min": bt.OPENING_RANGE_BUILD_MIN,
        "opening_drive_end_et":    bt.OPENING_DRIVE_END_ET,
        "vwap_reset_et":    bt.VWAP_RESET_ET,
        "valid_symbols":    list(bt.VALID_SYMBOLS),
        "valid_timeframes": list(bt.VALID_TIMEFRAMES),
        "timeframe":        "5m",
        "timezone_policy":  "UTC storage; ET session detection via pytz America/New_York",
        "roll_policy":      ("Databento continuous front contract; stype_in='continuous'; "
                             "stype_out default instrument_id"),
        "same_bar_conflict_policy":  "stop_wins_ties (worst-case: stop resolved before target on same bar)",
        "next_bar_open_entry_policy": "entry on open of bar AFTER signal bar ± slippage",
        "session_definitions": {
            "Asia":      "18:00–02:00 ET",
            "London":    "02:00–08:00 ET",
            "New York":  "08:00–17:00 ET",
            "Off-hours": "other",
        },
        "unsupported_metrics": {
            "mfe_r":        _null("MFE not tracked per-trade in simulate_strategy"),
            "mae_r":        _null("MAE not tracked per-trade in simulate_strategy"),
            "or_high":      _null("OR high/low not forwarded in trade record; in snapshot only"),
            "or_low":       _null("OR high/low not forwarded in trade record; in snapshot only"),
            "rvol_at_signal":  _null("RVOL not forwarded in trade record"),
            "atr_at_signal":   _null("ATR not forwarded in trade record"),
            "signal_ts":    _null("Signal bar ts inferred as entry_ts minus one 5m bar"),
            "candidate_count": _null("Per-strategy candidate count not returned by run_backtest"),
            "confirmed_signal_count": _null("Not separately counted in current engine"),
            "blocked_signal_count":   _null("Not separately counted in current engine"),
            "incomplete_trade_count": _null("One-position loop closes all trades at data end"),
        },
    }
    raw = json.dumps(cfg, sort_keys=True, default=str)
    cfg_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return cfg, cfg_hash

# ---------------------------------------------------------------------------
# Dataset validation
# ---------------------------------------------------------------------------
def _verify_datasets(dataset_ids: List[int], conn) -> Dict:
    """Return {ok, datasets[], errors[]}. All must exist and be real Databento."""
    errors = []
    datasets = []
    for ds_id in dataset_ids:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, symbol, timeframe, source_label, row_count,
                          gap_count, first_ts, last_ts, sha256, original_filename
                   FROM backtest_datasets WHERE id=%s""",
                (ds_id,),
            )
            row = cur.fetchone()
        if row is None:
            errors.append(f"dataset_id={ds_id} not found in backtest_datasets")
            continue
        ds_id_, sym, tf, label, row_count, gap_count, first_ts, last_ts, sha256, fname = row
        if label not in REAL_DATABENTO_LABELS:
            errors.append(
                f"dataset_id={ds_id} ({sym}) has source_label='{label}' — "
                f"only {sorted(REAL_DATABENTO_LABELS)} are accepted as real Databento"
            )
        if row_count < 1000:
            errors.append(f"dataset_id={ds_id} ({sym}) has only {row_count} bars — too small for baseline")
        datasets.append({
            "dataset_id": ds_id_,
            "symbol": sym,
            "timeframe": tf,
            "source_label": label,
            "row_count": row_count,
            "gap_count": gap_count,
            "first_ts": first_ts.isoformat() if first_ts else None,
            "last_ts": last_ts.isoformat() if last_ts else None,
            "sha256": sha256 or "",
            "filename": fname,
            "quality_status": "READY",
            "is_real_databento": label in REAL_DATABENTO_LABELS,
            "is_synthetic": False,
            "is_immutable": True,
            "stored_outside_git": True,
        })
    # Verify each instrument has exactly one dataset
    by_symbol = {}
    for ds in datasets:
        by_symbol.setdefault(ds["symbol"], []).append(ds["dataset_id"])
    for inst in BASELINE_INSTRUMENTS:
        if inst not in by_symbol:
            errors.append(f"No dataset found for instrument {inst}")
        elif len(by_symbol[inst]) > 1:
            errors.append(f"Multiple datasets for {inst}: {by_symbol[inst]}")
    return {"ok": not errors, "datasets": datasets, "errors": errors}

# ---------------------------------------------------------------------------
# Extended metrics (beyond _strategy_metrics in backtest_engine)
# ---------------------------------------------------------------------------
def _extended_metrics(trades: List[Dict], inst: str) -> Dict:
    """Compute all required Part 6 metrics from a flat trade list."""
    import backtest_engine as bt
    n = len(trades)
    if n == 0:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "breakeven_trades": 0,
            "win_rate": None, "gross_positive_r": 0.0, "gross_negative_r": 0.0,
            "net_r": 0.0, "avg_r": None, "median_r": None, "expectancy": None,
            "profit_factor": None, "max_drawdown_r": 0.0, "max_runup_r": 0.0,
            "longest_win_streak": 0, "longest_loss_streak": 0,
            "avg_hold_minutes": None, "median_hold_minutes": None,
            "min_hold_minutes": None, "max_hold_minutes": None,
            "long_count": 0, "short_count": 0, "long_wins": 0, "short_wins": 0,
            "long_net_r": 0.0, "short_net_r": 0.0,
            "commission_impact": 0.0, "slippage_impact": 0.0,
            "first_trade_ts": None, "last_trade_ts": None,
            "reliability_label": "INSUFFICIENT",
            "warnings": ["zero_trades"],
        }

    rs = [t["r_multiple"] for t in trades]
    spec = bt.BT_SPECS.get(inst, bt.BT_SPECS["MGC"])
    pv = spec["point_value"]
    tick = spec["tick_size"]

    wins      = [r for r in rs if r > 0.0]
    losses    = [r for r in rs if r < 0.0]
    breakeven = [r for r in rs if r == 0.0]

    gross_pos = sum(wins)
    gross_neg = abs(sum(losses))
    net_r     = round(sum(rs), 4)
    avg_r     = round(sum(rs) / n, 4)
    med_r     = round(statistics.median(rs), 4) if n >= 2 else round(rs[0], 4)
    expectancy = round(net_r / n, 4) if n > 0 else None

    pf: Any
    if gross_neg == 0.0:
        pf = None if gross_pos == 0.0 else float("inf")
    else:
        pf = round(gross_pos / gross_neg, 4)

    win_rate = round(len(wins) / n * 100.0, 2) if n > 0 else None

    # Equity curve for drawdown / run-up
    ordered = sorted(trades, key=lambda t: t["exit_ts"])
    cum, peak, trough = 0.0, 0.0, 0.0
    max_dd, max_runup = 0.0, 0.0
    for t in ordered:
        cum += t["r_multiple"]
        peak   = max(peak, cum)
        trough = min(trough, cum)
        max_dd    = max(max_dd,    peak - cum)
        max_runup = max(max_runup, cum - trough)

    # Streaks
    longest_win = longest_loss = cur_win = cur_loss = 0
    for r in [t["r_multiple"] for t in ordered]:
        if r > 0:
            cur_win += 1; cur_loss = 0
        else:
            cur_loss += 1; cur_win = 0
        longest_win  = max(longest_win,  cur_win)
        longest_loss = max(longest_loss, cur_loss)

    # Hold times
    holds = [t["hold_minutes"] for t in trades]
    avg_hold = round(sum(holds) / n, 2)
    med_hold = round(statistics.median(holds), 2) if n >= 2 else round(holds[0], 2)
    min_hold = round(min(holds), 2)
    max_hold = round(max(holds), 2)

    # Long / Short split
    longs  = [t for t in trades if t["direction"] == "Long"]
    shorts = [t for t in trades if t["direction"] == "Short"]
    l_wins = [t for t in longs  if t["r_multiple"] > 0]
    s_wins = [t for t in shorts if t["r_multiple"] > 0]

    # Commission + slippage impact (in R terms, approximate)
    # commission = 2 × 0.62 = $1.24 per trade; in R = 1.24 / (risk_pts × pv)
    # slippage   = 1 tick on entry: tick × pv / (risk_pts × pv) = tick / risk_pts
    commission_r = 0.0
    slippage_r   = 0.0
    for t in trades:
        rp = t.get("risk_points", 0) or 0
        if rp > 0:
            commission_r += (BASELINE_COMMISSION_PER_SIDE * 2.0) / (rp * pv)
            slippage_r   += (BASELINE_SLIPPAGE_TICKS * tick) / rp

    # First / last trade ts
    first_ts = min(t["entry_ts"] for t in trades)
    last_ts  = max(t["exit_ts"]  for t in trades)

    # Warnings
    warnings: List[str] = []
    if n < 20:
        warnings.append("insufficient_sample")
    if win_rate == 0.0:
        warnings.append("no_winning_trades")
    if win_rate == 100.0:
        warnings.append("no_losing_trades")
    if pf is None:
        warnings.append("profit_factor_undefined")
    if pf == float("inf"):
        warnings.append("profit_factor_infinite_no_losers")
    if max_dd == 0.0 and n > 0:
        warnings.append("drawdown_zero_possibly_no_losers")
    if len(longs) == 0 or len(shorts) == 0:
        warnings.append("one_sided_direction_sample")
    # Session concentration
    sessions = {}
    for t in trades:
        sessions[t.get("session", "Off-hours")] = sessions.get(t.get("session", "Off-hours"), 0) + 1
    top_sess = max(sessions, key=sessions.__getitem__) if sessions else "n/a"
    if sessions.get(top_sess, 0) / n > 0.90:
        warnings.append(f"single_session_concentration:{top_sess}")
    # Regime concentration
    regimes = {}
    for t in trades:
        regimes[t.get("regime", "UNKNOWN")] = regimes.get(t.get("regime", "UNKNOWN"), 0) + 1
    top_reg = max(regimes, key=regimes.__getitem__) if regimes else "n/a"
    if regimes.get(top_reg, 0) / n > 0.90:
        warnings.append(f"single_regime_concentration:{top_reg}")

    return {
        "total_trades":       n,
        "wins":               len(wins),
        "losses":             len(losses),
        "breakeven_trades":   len(breakeven),
        "win_rate":           win_rate,
        "gross_positive_r":   round(gross_pos, 4),
        "gross_negative_r":   round(gross_neg, 4),
        "net_r":              net_r,
        "avg_r":              avg_r,
        "median_r":           med_r,
        "expectancy":         expectancy,
        "profit_factor":      round(pf, 4) if isinstance(pf, float) and math.isfinite(pf) else (None if pf is None else "inf"),
        "max_drawdown_r":     round(max_dd, 4),
        "max_runup_r":        round(max_runup, 4),
        "longest_win_streak":  longest_win,
        "longest_loss_streak": longest_loss,
        "avg_hold_minutes":   avg_hold,
        "median_hold_minutes": med_hold,
        "min_hold_minutes":   min_hold,
        "max_hold_minutes":   max_hold,
        "long_count":         len(longs),
        "short_count":        len(shorts),
        "long_wins":          len(l_wins),
        "short_wins":         len(s_wins),
        "long_net_r":         round(sum(t["r_multiple"] for t in longs), 4),
        "short_net_r":        round(sum(t["r_multiple"] for t in shorts), 4),
        "commission_impact":  round(commission_r, 4),
        "slippage_impact":    round(slippage_r, 4),
        "first_trade_ts":     first_ts,
        "last_trade_ts":      last_ts,
        "reliability_label":  _reliability_label(n),
        "warnings":           warnings,
    }

# ---------------------------------------------------------------------------
# Per-trade record builder
# ---------------------------------------------------------------------------
def _build_trade_records(trades: List[Dict], baseline_id: str, matrix_id: int,
                         dataset_id: int, inst: str, mode: str, strategy: str) -> List[Tuple]:
    """Return list of value tuples for INSERT into baseline_trades."""
    rows = []
    from datetime import timedelta
    FIVE_MIN = timedelta(minutes=5)
    for t in trades:
        entry_ts = t["entry_ts"]   # ISO string
        exit_ts  = t["exit_ts"]
        dir_     = t["direction"]
        # signal_ts: approximate as entry_ts − 5 min (entry is next-bar-open)
        try:
            entry_dt = datetime.fromisoformat(entry_ts) if isinstance(entry_ts, str) else entry_ts
            signal_ts = (entry_dt - FIVE_MIN).isoformat()
        except Exception:
            signal_ts = None
        # Weekday (0=Mon) and month from entry_ts
        try:
            entry_dt2 = datetime.fromisoformat(entry_ts.replace("Z", "+00:00")) if isinstance(entry_ts, str) else entry_ts
            weekday = entry_dt2.weekday()
            month   = entry_dt2.month
        except Exception:
            weekday = None
            month   = None

        rows.append((
            baseline_id,
            matrix_id,
            dataset_id,
            inst,
            mode,
            strategy,
            dir_,
            signal_ts,
            entry_ts,
            t.get("entry"),
            t.get("stop"),
            t.get("tp1"),
            exit_ts,
            t.get("exit"),
            t.get("exit_reason"),
            t.get("risk_points"),
            1.0,                     # initial_risk_R = 1.0 by definition
            t.get("r_multiple"),
            t.get("hold_minutes"),
            None,                    # mfe_r — unsupported
            None,                    # mae_r — unsupported
            t.get("session"),
            t.get("entry_hour_et"),
            weekday,
            month,
            t.get("regime"),
            None,                    # trend_regime — not in trade dict
            None,                    # or_high — unsupported
            None,                    # or_low — unsupported
            None,                    # atr_at_signal — unsupported
            None,                    # rvol_at_signal — unsupported
            json.dumps([]),          # warnings
        ))
    return rows

# ---------------------------------------------------------------------------
# Matrix runner
# ---------------------------------------------------------------------------
def _run_combination(inst: str, mode: str, strategy: str, candles: List[Dict]) -> Dict:
    """Run one (inst, mode, strategy) combination. Returns result dict."""
    import backtest_engine as bt
    status = "FAILED"
    error_detail = None
    raw_result   = None
    trades: List[Dict] = []
    try:
        raw_result = bt.run_backtest(candles, {
            "symbol":            inst,
            "mode":              mode,
            "strategies":        [strategy],
            "management":        bt.BT_DEFAULT_MGMT,
            "commission_per_side": BASELINE_COMMISSION_PER_SIDE,
            "slippage_ticks":    BASELINE_SLIPPAGE_TICKS,
            "news_blackouts_et": list(bt.NEWS_BLACKOUTS_ET),
            "max_trades_per_session": bt.MAX_TRADES_PER_SESSION,
            "min_target_r":      bt.MIN_TARGET_R,
        })
        if raw_result.get("ok"):
            # run_backtest returns all trades across all requested strategies
            trades = raw_result.get("trades", [])
            # Filter to just the requested strategy (should only have one, but guard)
            trades = [t for t in trades if t.get("strategy") == strategy]
            status = "COMPLETE" if trades else "COMPLETE_ZERO_TRADES"
        else:
            error_detail = raw_result.get("error", "run_backtest returned ok=False")
    except Exception as exc:
        error_detail = f"{type(exc).__name__}: {exc}"

    return {
        "status":       status,
        "trades":       trades,
        "raw":          raw_result,
        "error_detail": error_detail,
    }

# ---------------------------------------------------------------------------
# Breakdown computation
# ---------------------------------------------------------------------------
def _compute_breakdowns(all_combos: List[Dict]) -> List[Dict]:
    """Compute Part 9 breakdowns. Returns list of dicts for DB insert."""
    rows: List[Dict] = []

    def _agg(trade_list: List[Dict], inst=None, mode=None, strategy=None,
              breakdown_type: str = "", breakdown_value: str = "") -> Dict:
        n = len(trade_list)
        if n == 0:
            return {"breakdown_type": breakdown_type, "breakdown_value": breakdown_value,
                    "instrument": inst, "mode": mode, "strategy": strategy,
                    "trade_count": 0, "win_rate": None, "net_r": 0.0,
                    "avg_r": None, "expectancy": None, "profit_factor": None,
                    "max_drawdown_r": 0.0, "reliability_label": "INSUFFICIENT"}
        rs = [t["r_multiple"] for t in trade_list]
        wins = [r for r in rs if r > 0]
        losses_abs = abs(sum(r for r in rs if r < 0))
        net = round(sum(rs), 4)
        avg = round(sum(rs) / n, 4)
        pf: Any = None
        if losses_abs > 0:
            pf = round(sum(wins) / losses_abs, 4)
        elif sum(wins) > 0:
            pf = float("inf")
        # Drawdown
        cum, peak, dd = 0.0, 0.0, 0.0
        for t in sorted(trade_list, key=lambda x: x["exit_ts"]):
            cum += t["r_multiple"]
            peak = max(peak, cum)
            dd   = max(dd, peak - cum)
        return {
            "breakdown_type":  breakdown_type,
            "breakdown_value": breakdown_value,
            "instrument":      inst,
            "mode":            mode,
            "strategy":        strategy,
            "trade_count":     n,
            "win_rate":        round(len(wins) / n * 100.0, 2) if n > 0 else None,
            "net_r":           net,
            "avg_r":           avg,
            "expectancy":      round(net / n, 4) if n > 0 else None,
            "profit_factor":   pf if isinstance(pf, float) and math.isfinite(pf) else (None if pf is None else None),
            "max_drawdown_r":  round(dd, 4),
            "reliability_label": _reliability_label(n),
        }

    # Collect all trades with context
    tagged: List[Dict] = []
    for combo in all_combos:
        if combo["status"] not in ("COMPLETE", "COMPLETE_ZERO_TRADES"):
            continue
        for t in combo["trades"]:
            tagged.append({**t, "_inst": combo["inst"], "_mode": combo["mode"],
                           "_strategy": combo["strategy"]})

    all_trades = tagged

    # By instrument
    for inst in BASELINE_INSTRUMENTS:
        rows.append(_agg([t for t in all_trades if t["_inst"] == inst],
                         inst=inst, breakdown_type="instrument", breakdown_value=inst))

    # By strategy
    for strat in BASELINE_STRATEGIES:
        rows.append(_agg([t for t in all_trades if t["_strategy"] == strat],
                         strategy=strat, breakdown_type="strategy", breakdown_value=strat))

    # By mode
    for mode in BASELINE_MODES:
        rows.append(_agg([t for t in all_trades if t["_mode"] == mode],
                         mode=mode, breakdown_type="mode", breakdown_value=mode))

    # By direction
    for direction in ("Long", "Short"):
        rows.append(_agg([t for t in all_trades if t.get("direction") == direction],
                         breakdown_type="direction", breakdown_value=direction))

    # By session
    for sess in ("New York", "London", "Asia", "Off-hours"):
        rows.append(_agg([t for t in all_trades if t.get("session") == sess],
                         breakdown_type="session", breakdown_value=sess))

    # By ET hour (0-23)
    for hour in range(24):
        hour_trades = [t for t in all_trades if t.get("entry_hour_et") == hour]
        if hour_trades:
            rows.append(_agg(hour_trades, breakdown_type="et_hour",
                             breakdown_value=str(hour)))

    # By weekday (0=Monday)
    for wd in range(7):
        try:
            wd_trades = []
            for t in all_trades:
                try:
                    ts = t.get("entry_ts", "")
                    if isinstance(ts, str):
                        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if ts.weekday() == wd:
                        wd_trades.append(t)
                except Exception:
                    pass
            if wd_trades:
                rows.append(_agg(wd_trades, breakdown_type="weekday",
                                 breakdown_value=str(wd)))
        except Exception:
            pass

    # By month
    for mo in range(1, 13):
        try:
            mo_trades = []
            for t in all_trades:
                try:
                    ts = t.get("entry_ts", "")
                    if isinstance(ts, str):
                        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if ts.month == mo:
                        mo_trades.append(t)
                except Exception:
                    pass
            if mo_trades:
                rows.append(_agg(mo_trades, breakdown_type="month",
                                 breakdown_value=str(mo)))
        except Exception:
            pass

    # By volatility regime
    all_regimes = set(t.get("regime", "UNKNOWN") for t in all_trades if t.get("regime"))
    for reg in all_regimes:
        rows.append(_agg([t for t in all_trades if t.get("regime") == reg],
                         breakdown_type="volatility_regime", breakdown_value=reg))

    # By instrument × mode
    for inst in BASELINE_INSTRUMENTS:
        for mode in BASELINE_MODES:
            grp = [t for t in all_trades if t["_inst"] == inst and t["_mode"] == mode]
            rows.append(_agg(grp, inst=inst, mode=mode,
                             breakdown_type="instrument_mode",
                             breakdown_value=f"{inst}_{mode}"))

    # By sample-size band (derived label)
    return rows

# ---------------------------------------------------------------------------
# Rankings
# ---------------------------------------------------------------------------
def _compute_rankings(combos: List[Dict]) -> Dict:
    """Part 11 descriptive rankings. Never used to influence live trading."""
    completed = [c for c in combos if c["status"] in ("COMPLETE", "COMPLETE_ZERO_TRADES")]
    with_trades = [c for c in completed if c["metrics"]["total_trades"] > 0]
    min20 = [c for c in with_trades if c["metrics"]["total_trades"] >= 20]

    def _label(c):
        return f"{c['inst']}/{c['mode']}/{c['strategy']}"

    def _safe_pf(c):
        pf = c["metrics"].get("profit_factor")
        if pf == "inf" or pf == float("inf"):
            return 1e9
        if pf is None or not isinstance(pf, (int, float)):
            return -1.0
        return float(pf)

    def _best_strat_by_inst(inst):
        grp = [c for c in min20 if c["inst"] == inst]
        if not grp:
            return None
        return max(grp, key=lambda c: c["metrics"]["net_r"])

    def _best_mode_by_inst(inst):
        result = {}
        for mode in BASELINE_MODES:
            grp = [c for c in with_trades if c["inst"] == inst and c["mode"] == mode]
            if grp:
                net = round(sum(c["metrics"]["net_r"] for c in grp), 4)
                trades_n = sum(c["metrics"]["total_trades"] for c in grp)
                result[mode] = {"net_r": net, "total_trades": trades_n,
                                "label": _reliability_label(trades_n)}
        return result

    rank_by = lambda key, fn, lst=min20: sorted(
        lst, key=fn, reverse=True
    )[:10] if lst else []

    def _fmt(c):
        m = c["metrics"]
        return {
            "label":        _label(c),
            "inst":         c["inst"],
            "mode":         c["mode"],
            "strategy":     c["strategy"],
            "total_trades": m["total_trades"],
            "net_r":        m["net_r"],
            "expectancy":   m["expectancy"],
            "profit_factor": m.get("profit_factor"),
            "win_rate":     m["win_rate"],
            "max_drawdown_r": m["max_drawdown_r"],
            "reliability_label": m["reliability_label"],
        }

    return {
        "note": ("DESCRIPTIVE ONLY — HISTORICAL RESEARCH — NOT LIVE PERFORMANCE — "
                 "NOT FINANCIAL ADVICE — rankings do not influence live trading"),
        "highest_net_r": [_fmt(c) for c in sorted(with_trades, key=lambda c: c["metrics"]["net_r"], reverse=True)[:10]],
        "highest_expectancy": [_fmt(c) for c in sorted(min20, key=lambda c: (c["metrics"]["expectancy"] or -999), reverse=True)[:10]],
        "highest_profit_factor": [_fmt(c) for c in sorted(min20, key=_safe_pf, reverse=True)[:10]],
        "lowest_max_drawdown": [_fmt(c) for c in sorted(min20, key=lambda c: c["metrics"]["max_drawdown_r"])[:10]],
        "largest_sample": [_fmt(c) for c in sorted(with_trades, key=lambda c: c["metrics"]["total_trades"], reverse=True)[:10]],
        "best_long_net_r": [_fmt(c) for c in sorted(with_trades, key=lambda c: c["metrics"]["long_net_r"], reverse=True)[:10]],
        "best_short_net_r": [_fmt(c) for c in sorted(with_trades, key=lambda c: c["metrics"]["short_net_r"], reverse=True)[:10]],
        "highest_win_rate_min20": [_fmt(c) for c in sorted(min20, key=lambda c: (c["metrics"]["win_rate"] or 0), reverse=True)[:10]],
        "highest_expectancy_min20": [_fmt(c) for c in sorted(min20, key=lambda c: (c["metrics"]["expectancy"] or -999), reverse=True)[:10]],
        "best_strategy_by_instrument": {
            inst: (_fmt(_best_strat_by_inst(inst)) if _best_strat_by_inst(inst) else None)
            for inst in BASELINE_INSTRUMENTS
        },
        "best_mode_by_instrument": {
            inst: _best_mode_by_inst(inst) for inst in BASELINE_INSTRUMENTS
        },
    }

# ---------------------------------------------------------------------------
# DB storage helpers
# ---------------------------------------------------------------------------
def _store_matrix_result(conn, baseline_id: str, combo: Dict) -> Optional[int]:
    m = combo["metrics"]
    pf_val = m.get("profit_factor")
    pf_db = (None if pf_val in (None, "inf", float("inf"))
             else float(pf_val) if isinstance(pf_val, (int, float)) else None)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO baseline_matrix_results
               (baseline_id, dataset_id, dataset_sha256, instrument, mode, strategy,
                status, start_date, end_date, bar_count, session_count,
                dispatch_count, candidate_count, completed_trades,
                wins, losses, breakeven_trades, win_rate,
                gross_positive_r, gross_negative_r, net_r, avg_r, median_r,
                expectancy, profit_factor, max_drawdown_r, max_runup_r,
                longest_win_streak, longest_loss_streak,
                avg_hold_minutes, median_hold_minutes, min_hold_minutes, max_hold_minutes,
                long_count, short_count, long_wins, short_wins,
                long_net_r, short_net_r, commission_impact, slippage_impact,
                first_trade_ts, last_trade_ts, reliability_label,
                warnings, error_detail, metrics_json, unsupported_metrics)
               VALUES (%s,%s,%s,%s,%s,%s,
                       %s,%s,%s,%s,%s,
                       %s,%s,%s,
                       %s,%s,%s,%s,
                       %s,%s,%s,%s,%s,
                       %s,%s,%s,%s,
                       %s,%s,
                       %s,%s,%s,%s,
                       %s,%s,%s,%s,
                       %s,%s,%s,%s,
                       %s,%s,%s,
                       %s,%s,%s,%s)
               RETURNING id""",
            (
                baseline_id, combo["dataset_id"], combo["dataset_sha256"],
                combo["inst"], combo["mode"], combo["strategy"],
                combo["status"],
                combo.get("start_date"), combo.get("end_date"),
                combo.get("bar_count"), None,   # session_count — not computed
                None, None,                      # dispatch/candidate — unsupported
                m["total_trades"],
                m["wins"], m["losses"], m["breakeven_trades"],
                m["win_rate"],
                m["gross_positive_r"], m["gross_negative_r"],
                m["net_r"], m["avg_r"], m["median_r"],
                m["expectancy"], pf_db,
                m["max_drawdown_r"], m["max_runup_r"],
                m["longest_win_streak"], m["longest_loss_streak"],
                m["avg_hold_minutes"], m["median_hold_minutes"],
                m["min_hold_minutes"], m["max_hold_minutes"],
                m["long_count"], m["short_count"],
                m["long_wins"], m["short_wins"],
                m["long_net_r"], m["short_net_r"],
                m["commission_impact"], m["slippage_impact"],
                m["first_trade_ts"], m["last_trade_ts"],
                m["reliability_label"],
                psycopg2.extras.Json(m["warnings"]),
                combo.get("error_detail"),
                psycopg2.extras.Json(_jdump(m)),
                psycopg2.extras.Json(_jdump(combo.get("unsupported_metrics", {}))),
            ),
        )
        return cur.fetchone()[0]


def _store_trades(conn, rows: List[Tuple]) -> int:
    if not rows:
        return 0
    psycopg2.extras.execute_values(
        conn.cursor(),
        """INSERT INTO baseline_trades
           (baseline_id, matrix_result_id, dataset_id, instrument, mode, strategy,
            direction, signal_ts, entry_ts, entry_price, stop_price, target_price,
            exit_ts, exit_price, exit_reason, initial_risk_pts, initial_risk_r,
            realized_r, hold_minutes, mfe_r, mae_r, session, et_hour, weekday, month,
            vol_regime, trend_regime, or_high, or_low, atr_at_signal, rvol_at_signal,
            warnings)
           VALUES %s""",
        rows,
        page_size=500,
    )
    return len(rows)


def _store_breakdowns(conn, baseline_id: str, rows: List[Dict]) -> int:
    if not rows:
        return 0
    data = [(
        baseline_id,
        r["breakdown_type"], r["breakdown_value"],
        r.get("instrument"), r.get("mode"), r.get("strategy"),
        r["trade_count"], r["win_rate"], r["net_r"], r["avg_r"],
        r["expectancy"], r["profit_factor"], r["max_drawdown_r"],
        r["reliability_label"],
    ) for r in rows]
    psycopg2.extras.execute_values(
        conn.cursor(),
        """INSERT INTO baseline_breakdowns
           (baseline_id, breakdown_type, breakdown_value, instrument, mode, strategy,
            trade_count, win_rate, net_r, avg_r, expectancy, profit_factor,
            max_drawdown_r, reliability_label)
           VALUES %s""",
        data,
        page_size=200,
    )
    return len(rows)

# ---------------------------------------------------------------------------
# Main entry: generate_baseline
# ---------------------------------------------------------------------------
def generate_baseline(dataset_ids: List[int]) -> Dict:
    """Run the full 40-combination matrix and store an immutable baseline record.

    Returns a report dict with baseline_id, summary, per-combo results,
    and rankings.  On error returns {"ok": False, "error": ...}.
    """
    # ── Guard: DB available ──────────────────────────────────────────────────
    conn = _bl_conn()
    if conn is None:
        return {"ok": False, "error": "Database unavailable"}

    # ── Part 1: Pre-flight ───────────────────────────────────────────────────
    if sorted(dataset_ids) != sorted(OFFICIAL_DATASET_IDS.values()):
        return {
            "ok": False,
            "error": (
                f"Exactly these dataset_ids are required for the official baseline: "
                f"{sorted(OFFICIAL_DATASET_IDS.values())} — got {sorted(dataset_ids)}"
            ),
        }

    # ── Part 2: Verify datasets ──────────────────────────────────────────────
    verification = _verify_datasets(dataset_ids, conn)
    if not verification["ok"]:
        return {"ok": False, "error": "Dataset verification failed",
                "dataset_errors": verification["errors"]}

    ds_by_inst = {ds["symbol"]: ds for ds in verification["datasets"]}

    # ── Parts 3-4: Config + baseline ID ─────────────────────────────────────
    commit = _get_commit()
    config, config_hash = _freeze_config(commit)
    baseline_id = f"BL-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{commit}"
    generated_at = datetime.now(timezone.utc)

    # ── Store baseline_config ────────────────────────────────────────────────
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO baseline_configs
                   (baseline_id, source_commit, engine_version, generated_at,
                    dataset_ids, instruments, modes, strategies, config_json, config_hash)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    baseline_id, commit, ENGINE_VERSION, generated_at,
                    psycopg2.extras.Json(dataset_ids),
                    psycopg2.extras.Json(BASELINE_INSTRUMENTS),
                    psycopg2.extras.Json(BASELINE_MODES),
                    psycopg2.extras.Json(BASELINE_STRATEGIES),
                    psycopg2.extras.Json(_jdump(config)),
                    config_hash,
                ),
            )
    except Exception as exc:
        if "unique" in str(exc).lower():
            return {"ok": False, "error": f"Baseline ID collision: {baseline_id} already exists"}
        return {"ok": False, "error": f"Failed to store baseline config: {exc}"}

    # ── Part 5: Run the full matrix ──────────────────────────────────────────
    import backtest_engine as bt

    combos: List[Dict] = []
    total_bars_processed = 0

    for inst in BASELINE_INSTRUMENTS:
        ds = ds_by_inst[inst]
        dataset_id = ds["dataset_id"]
        ds_sha256  = ds["sha256"]

        # Load candles from DB once per instrument
        candles = _load_candles(dataset_id, conn)
        if not candles:
            for strat in BASELINE_STRATEGIES:
                for mode in BASELINE_MODES:
                    combo: Dict = {
                        "inst": inst, "mode": mode, "strategy": strat,
                        "dataset_id": dataset_id, "dataset_sha256": ds_sha256,
                        "status": "DATA_UNAVAILABLE",
                        "trades": [], "metrics": _extended_metrics([], inst),
                        "start_date": None, "end_date": None, "bar_count": 0,
                        "error_detail": f"No candles loaded for dataset_id={dataset_id}",
                        "unsupported_metrics": config["unsupported_metrics"],
                    }
                    combos.append(combo)
            continue

        bar_count = len(candles)
        total_bars_processed += bar_count
        start_date = candles[0]["ts"].date().isoformat()  if hasattr(candles[0]["ts"], "date") else str(candles[0]["ts"])[:10]
        end_date   = candles[-1]["ts"].date().isoformat() if hasattr(candles[-1]["ts"], "date") else str(candles[-1]["ts"])[:10]

        for mode in BASELINE_MODES:
            for strat in BASELINE_STRATEGIES:
                run  = _run_combination(inst, mode, strat, candles)
                trades = run["trades"]
                metrics = _extended_metrics(trades, inst)
                combo = {
                    "inst": inst, "mode": mode, "strategy": strat,
                    "dataset_id": dataset_id, "dataset_sha256": ds_sha256,
                    "status": run["status"],
                    "trades": trades,
                    "metrics": metrics,
                    "start_date": start_date,
                    "end_date": end_date,
                    "bar_count": bar_count,
                    "error_detail": run.get("error_detail"),
                    "unsupported_metrics": config["unsupported_metrics"],
                }
                combos.append(combo)

    # ── Store matrix results + trades ────────────────────────────────────────
    total_trade_records = 0
    for combo in combos:
        try:
            matrix_id = _store_matrix_result(conn, baseline_id, combo)
            if matrix_id and combo["trades"]:
                trade_rows = _build_trade_records(
                    combo["trades"], baseline_id, matrix_id,
                    combo["dataset_id"], combo["inst"], combo["mode"], combo["strategy"],
                )
                total_trade_records += _store_trades(conn, trade_rows)
        except Exception as exc:
            import logging
            logging.warning("baseline store combo failed %s/%s/%s: %s",
                            combo["inst"], combo["mode"], combo["strategy"], exc)

    # ── Part 9: Breakdowns ───────────────────────────────────────────────────
    breakdown_rows = _compute_breakdowns(combos)
    _store_breakdowns(conn, baseline_id, breakdown_rows)

    # ── Part 10: Summary ─────────────────────────────────────────────────────
    completed     = [c for c in combos if c["status"] in ("COMPLETE", "COMPLETE_ZERO_TRADES")]
    zero_trade    = [c for c in combos if c["status"] == "COMPLETE_ZERO_TRADES"]
    failed        = [c for c in combos if c["status"] == "FAILED"]
    unavailable   = [c for c in combos if c["status"] == "DATA_UNAVAILABLE"]

    all_trades_flat = [t for c in combos for t in c["trades"]]
    total_wins   = sum(1 for t in all_trades_flat if t["r_multiple"] > 0)
    total_losses = sum(1 for t in all_trades_flat if t["r_multiple"] <= 0)
    agg_net_r    = round(sum(t["r_multiple"] for t in all_trades_flat), 4)
    agg_n        = len(all_trades_flat)
    agg_exp      = round(agg_net_r / agg_n, 4) if agg_n > 0 else None

    agg_pos = sum(t["r_multiple"] for t in all_trades_flat if t["r_multiple"] > 0)
    agg_neg = abs(sum(t["r_multiple"] for t in all_trades_flat if t["r_multiple"] < 0))
    agg_pf  = round(agg_pos / agg_neg, 4) if agg_neg > 0 else None

    # Aggregate drawdown on combined equity curve
    all_ordered = sorted(all_trades_flat, key=lambda t: t["exit_ts"])
    cum, peak, agg_dd = 0.0, 0.0, 0.0
    for t in all_ordered:
        cum += t["r_multiple"]
        peak = max(peak, cum)
        agg_dd = max(agg_dd, peak - cum)

    # Reliability distribution
    rel_dist: Dict[str, int] = {}
    for c in completed:
        lbl = c["metrics"]["reliability_label"]
        rel_dist[lbl] = rel_dist.get(lbl, 0) + 1

    # All warnings across combos
    all_warnings = list({w for c in combos for w in c["metrics"].get("warnings", [])})
    all_failures = [{"combo": f"{c['inst']}/{c['mode']}/{c['strategy']}",
                     "error": c["error_detail"]} for c in failed + unavailable]

    summary = {
        "baseline_id":            baseline_id,
        "generation_timestamp":   generated_at.isoformat(),
        "source_commit":          commit,
        "config_hash":            config_hash,
        "engine_version":         ENGINE_VERSION,
        "total_matrix_combinations": len(combos),
        "completed_combinations": len(completed) - len(zero_trade),
        "zero_trade_combinations": len(zero_trade),
        "failed_combinations":    len(failed),
        "unavailable_combinations": len(unavailable),
        "total_bars_processed":   total_bars_processed,
        "total_trades":           agg_n,
        "total_wins":             total_wins,
        "total_losses":           total_losses,
        "aggregate_net_r":        agg_net_r,
        "aggregate_expectancy":   agg_exp,
        "aggregate_profit_factor": agg_pf,
        "aggregate_max_drawdown_r": round(agg_dd, 4),
        "dataset_coverage": {
            inst: {"dataset_id": ds_by_inst[inst]["dataset_id"],
                   "first_ts":   ds_by_inst[inst]["first_ts"],
                   "last_ts":    ds_by_inst[inst]["last_ts"],
                   "row_count":  ds_by_inst[inst]["row_count"]}
            for inst in BASELINE_INSTRUMENTS
        },
        "warnings":  all_warnings,
        "failures":  all_failures,
        "reliability_distribution": rel_dist,
        "aggregate_note": (
            "Aggregate figures combine MNQ, MES, MGC, MYM across SCALP and SWING — "
            "DESCRIPTIVE ONLY. HISTORICAL RESEARCH. NOT LIVE PERFORMANCE. NOT FINANCIAL ADVICE."
        ),
    }

    # ── Part 11: Rankings ────────────────────────────────────────────────────
    rankings = _compute_rankings(combos)

    # ── Update summary_json on baseline_configs ──────────────────────────────
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE baseline_configs SET summary_json=%s WHERE baseline_id=%s",
                (psycopg2.extras.Json(_jdump(summary)), baseline_id),
            )
    except Exception:
        pass  # summary_json is convenience — not critical

    # ── Final report ─────────────────────────────────────────────────────────
    per_combo = []
    for c in combos:
        m = c["metrics"]
        per_combo.append({
            "inst": c["inst"], "mode": c["mode"], "strategy": c["strategy"],
            "dataset_id": c["dataset_id"],
            "status": c["status"],
            "total_trades": m["total_trades"],
            "wins": m["wins"], "losses": m["losses"],
            "net_r": m["net_r"], "expectancy": m["expectancy"],
            "profit_factor": m.get("profit_factor"),
            "win_rate": m["win_rate"],
            "max_drawdown_r": m["max_drawdown_r"],
            "reliability_label": m["reliability_label"],
            "warnings": m["warnings"],
            "error_detail": c.get("error_detail"),
        })

    conn.close()
    return {
        "ok":          True,
        "baseline_id": baseline_id,
        "config_hash": config_hash,
        "summary":     summary,
        "per_combo":   per_combo,
        "rankings":    rankings,
        "datasets":    verification["datasets"],
        "config":      config,
    }

# ---------------------------------------------------------------------------
# Candle loader (standalone — mirrors _bt_load_candles from app.py)
# ---------------------------------------------------------------------------
def _load_candles(dataset_id: int, conn) -> List[Dict]:
    from datetime import timezone as _tz
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT ts, open, high, low, close, volume
                   FROM backtest_candles WHERE dataset_id=%s ORDER BY ts ASC""",
                (dataset_id,),
            )
            out = []
            for r in cur.fetchall():
                ts = r[0]
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=_tz.utc)
                out.append({"ts": ts.astimezone(_tz.utc), "open": float(r[1]),
                             "high": float(r[2]), "low": float(r[3]),
                             "close": float(r[4]),
                             "volume": float(r[5]) if r[5] is not None else 0.0})
            return out
    except Exception as exc:
        import logging
        logging.warning("bl_load_candles failed: %s", exc)
        return []

# ---------------------------------------------------------------------------
# Read API helpers
# ---------------------------------------------------------------------------
def get_baselines() -> List[Dict]:
    """List all baseline configs with summary metadata."""
    conn = _bl_conn()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT baseline_id, source_commit, engine_version, generated_at,
                          dataset_ids, instruments, modes, strategies, config_hash,
                          summary_json
                   FROM baseline_configs ORDER BY generated_at DESC LIMIT 100"""
            )
            out = []
            for r in cur.fetchall():
                summary = r[9] or {}
                out.append({
                    "baseline_id":       r[0],
                    "source_commit":     r[1],
                    "engine_version":    r[2],
                    "generated_at":      r[3].isoformat() if r[3] else None,
                    "dataset_ids":       r[4],
                    "instruments":       r[5],
                    "modes":             r[6],
                    "strategies":        r[7],
                    "config_hash":       r[8],
                    "total_trades":      summary.get("total_trades"),
                    "aggregate_net_r":   summary.get("aggregate_net_r"),
                    "completed_combinations": summary.get("completed_combinations"),
                    "zero_trade_combinations": summary.get("zero_trade_combinations"),
                    "failed_combinations": summary.get("failed_combinations"),
                    "reliability_distribution": summary.get("reliability_distribution"),
                    "warnings":          summary.get("warnings", []),
                })
            return out
    except Exception as exc:
        import logging
        logging.warning("get_baselines failed: %s", exc)
        return []
    finally:
        conn.close()


def get_baseline(baseline_id: str) -> Optional[Dict]:
    """Return full baseline detail: config + matrix results + summary."""
    conn = _bl_conn()
    if conn is None:
        return None
    try:
        # Config
        with conn.cursor() as cur:
            cur.execute(
                """SELECT baseline_id, source_commit, engine_version, generated_at,
                          dataset_ids, instruments, modes, strategies, config_json,
                          config_hash, summary_json
                   FROM baseline_configs WHERE baseline_id=%s""",
                (baseline_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        cfg = {
            "baseline_id":    row[0], "source_commit": row[1],
            "engine_version": row[2],
            "generated_at":   row[3].isoformat() if row[3] else None,
            "dataset_ids":    row[4], "instruments": row[5],
            "modes":          row[6], "strategies":  row[7],
            "config":         row[8], "config_hash": row[9],
            "summary":        row[10] or {},
        }
        # Matrix results
        with conn.cursor() as cur:
            cur.execute(
                """SELECT instrument, mode, strategy, status,
                          completed_trades, wins, losses, win_rate,
                          net_r, avg_r, expectancy, profit_factor,
                          max_drawdown_r, reliability_label, warnings, error_detail,
                          start_date, end_date, bar_count, dataset_id, dataset_sha256
                   FROM baseline_matrix_results WHERE baseline_id=%s
                   ORDER BY instrument, mode, strategy""",
                (baseline_id,),
            )
            matrix = []
            for r in cur.fetchall():
                matrix.append({
                    "instrument": r[0], "mode": r[1], "strategy": r[2],
                    "status": r[3], "total_trades": r[4],
                    "wins": r[5], "losses": r[6], "win_rate": float(r[7]) if r[7] is not None else None,
                    "net_r": float(r[8]) if r[8] is not None else None,
                    "avg_r": float(r[9]) if r[9] is not None else None,
                    "expectancy": float(r[10]) if r[10] is not None else None,
                    "profit_factor": float(r[11]) if r[11] is not None else None,
                    "max_drawdown_r": float(r[12]) if r[12] is not None else None,
                    "reliability_label": r[13], "warnings": r[14], "error_detail": r[15],
                    "start_date": str(r[16]) if r[16] else None,
                    "end_date": str(r[17]) if r[17] else None,
                    "bar_count": r[18], "dataset_id": r[19], "dataset_sha256": r[20],
                })
        cfg["matrix_results"] = matrix
        return cfg
    except Exception as exc:
        import logging
        logging.warning("get_baseline failed: %s", exc)
        return None
    finally:
        conn.close()


def get_baseline_trades(baseline_id: str, filters: Optional[Dict] = None) -> List[Dict]:
    """Return trade records for a baseline, with optional filters."""
    conn = _bl_conn()
    if conn is None:
        return []
    filters = filters or {}
    try:
        q  = ["SELECT instrument, mode, strategy, direction, signal_ts, entry_ts,",
              "       entry_price, stop_price, target_price, exit_ts, exit_price,",
              "       exit_reason, initial_risk_pts, realized_r, hold_minutes,",
              "       session, et_hour, weekday, month, vol_regime, trend_regime",
              "FROM baseline_trades WHERE baseline_id=%s"]
        params: List[Any] = [baseline_id]
        for col, key in [("instrument","instrument"), ("mode","mode"),
                          ("strategy","strategy"), ("direction","direction"),
                          ("session","session"), ("weekday","weekday"),
                          ("month","month"), ("vol_regime","volatility_regime"),
                          ("trend_regime","trend_regime")]:
            if filters.get(key):
                q.append(f"AND {col}=%s"); params.append(filters[key])
        q.append("ORDER BY entry_ts ASC LIMIT 10000")
        with conn.cursor() as cur:
            cur.execute(" ".join(q), tuple(params))
            out = []
            for r in cur.fetchall():
                out.append({
                    "instrument": r[0], "mode": r[1], "strategy": r[2], "direction": r[3],
                    "signal_ts": r[4].isoformat() if r[4] else None,
                    "entry_ts":  r[5].isoformat() if r[5] else None,
                    "entry_price": float(r[6]) if r[6] is not None else None,
                    "stop_price":  float(r[7]) if r[7] is not None else None,
                    "target_price": float(r[8]) if r[8] is not None else None,
                    "exit_ts":  r[9].isoformat() if r[9] else None,
                    "exit_price": float(r[10]) if r[10] is not None else None,
                    "exit_reason": r[11],
                    "initial_risk_pts": float(r[12]) if r[12] is not None else None,
                    "realized_r": float(r[13]) if r[13] is not None else None,
                    "hold_minutes": float(r[14]) if r[14] is not None else None,
                    "session": r[15], "et_hour": r[16], "weekday": r[17],
                    "month": r[18], "vol_regime": r[19], "trend_regime": r[20],
                })
            return out
    except Exception as exc:
        import logging
        logging.warning("get_baseline_trades failed: %s", exc)
        return []
    finally:
        conn.close()


def get_baseline_breakdowns(baseline_id: str) -> List[Dict]:
    """Return all grouped breakdowns for a baseline."""
    conn = _bl_conn()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT breakdown_type, breakdown_value, instrument, mode, strategy,
                          trade_count, win_rate, net_r, avg_r, expectancy,
                          profit_factor, max_drawdown_r, reliability_label
                   FROM baseline_breakdowns WHERE baseline_id=%s
                   ORDER BY breakdown_type, breakdown_value""",
                (baseline_id,),
            )
            out = []
            for r in cur.fetchall():
                out.append({
                    "breakdown_type": r[0], "breakdown_value": r[1],
                    "instrument": r[2], "mode": r[3], "strategy": r[4],
                    "trade_count": r[5],
                    "win_rate": float(r[6]) if r[6] is not None else None,
                    "net_r": float(r[7]) if r[7] is not None else None,
                    "avg_r": float(r[8]) if r[8] is not None else None,
                    "expectancy": float(r[9]) if r[9] is not None else None,
                    "profit_factor": float(r[10]) if r[10] is not None else None,
                    "max_drawdown_r": float(r[11]) if r[11] is not None else None,
                    "reliability_label": r[12],
                })
            return out
    except Exception as exc:
        import logging
        logging.warning("get_baseline_breakdowns failed: %s", exc)
        return []
    finally:
        conn.close()
