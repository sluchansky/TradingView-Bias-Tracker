"""
Strategy Backtesting Engine — SELF-CONTAINED, PURE, READ-ONLY research.

This module re-creates the trading app's indicator + strategy logic from raw OHLCV
candles and replays them candle-by-candle so the five named strategies can be
compared historically. It is deliberately decoupled from the live app:

  • It imports NOTHING from app.py and shares NO mutable global with the live
    money path. The instrument specs, mode knobs and stop math below are COPIES of
    the live defaults (architect ruling: copy, never refactor live code), so a
    change here can never alter live gating / sizing / dedupe / execution.
  • Every function is pure: same inputs → same outputs. No DB, no Flask, no
    network, no Discord, no clock reads inside the hot path.
  • Strictly CAUSAL (no look-ahead): indicators at bar i use only bars 0..i; a
    signal that closes on bar i can only ENTER on bar i+1's open; a same-bar
    stop+target collision is always resolved WORST-CASE (stop first).

Fidelity note: BOS / CHOCH / supply-demand zones have no Pine source in the repo
(they come from an external Smart-Money-Concepts indicator), so they are
reconstructed with standard pivot-based SMC logic — faithful to the concept, not
byte-identical to the live indicator. Liquidity sweeps and the 5m confirmation
candle DO have Pine sources and are matched exactly (leftBars=10/rightBars=3;
bull confirm = close>open and close>close[1]).
"""

import csv
import io
import math
import hashlib
import statistics
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ET_TZ = ZoneInfo("America/New_York")

# ── Copied instrument specs (mirror live INSTRUMENT_SPECS defaults) ───────────
# min_stop_ticks env overrides are live-only; the backtest uses the documented
# defaults for reproducibility.
BT_SPECS = {
    "MNQ": {"tp1": 20.0, "tp2": 40.0, "tp3": 60.0, "stop_buf": 5.0,
            "point_value": 2.0, "tick_size": 0.25, "min_stop_ticks": 40,
            "price_lo": 3000.0, "price_hi": 60000.0},
    "MGC": {"tp1": 5.0, "tp2": 10.0, "tp3": 15.0, "stop_buf": 1.0,
            "point_value": 10.0, "tick_size": 0.1, "min_stop_ticks": 50,
            "price_lo": 400.0, "price_hi": 12000.0},
}

# ── Copied mode knobs (mirror live MODES) — only what the backtest needs ───────
BT_MODES = {
    "SCALP": {"stop_mult": 1.0, "enforce_min_rr": False,
              "vol_high_caution": 1.6, "vol_high_block": 2.5,
              "vol_quiet_caution": 0.55, "vol_quiet_block": 0.35,
              "rvol_confirm": 1.5, "near_pct": 0.006, "extended_pct": 0.016},
    "SWING": {"stop_mult": 1.5, "enforce_min_rr": True,
              "vol_high_caution": 1.8, "vol_high_block": 3.0,
              "vol_quiet_caution": 0.50, "vol_quiet_block": 0.30,
              "rvol_confirm": 1.5, "near_pct": 0.005, "extended_pct": 0.010},
}

# ── Detection constants (mirror live STRAT_* + volatility/opening-range) ───────
PIVOT_LEFT              = 10     # ta.pivothigh/low leftBars  (liquidity_sweep.pine)
PIVOT_RIGHT            = 3       # ta.pivothigh/low rightBars (liquidity_sweep.pine)
ATR_BARS               = 14     # recent-ATR window (VOL_ATR_BARS)
VOL_MIN_BARS           = 30     # min bars before a volatility baseline is trusted
RVOL_LOOKBACK          = 20     # rolling average-volume window for RVOL
STRAT_VWAP_PULLBACK_ATR = 0.6   # within 0.6*ATR of VWAP == pullback into VWAP
STRAT_EXHAUSTION_EXT_ATR = 2.0  # >= 2.0*ATR from VWAP == overextended
STRAT_RANGE_TIGHT_ATR  = 1.5    # consolidation width <= 1.5*ATR == tight range
RANGE_LOOKBACK_MIN     = 30     # rolling consolidation window (minutes)
OPENING_RANGE_START_ET = 8.0    # OR builds from 08:00 ET
OPENING_RANGE_BUILD_MIN = 30    # ...over 30 minutes
OPENING_DRIVE_END_ET   = 10.0   # Opening Drive eligible 08:00–10:00 ET
VWAP_RESET_ET          = 18.0   # session-anchored VWAP resets at 18:00 ET (CME globex reopen)
CVD_SLOPE_BARS         = 3      # bars used to read the CVD slope direction

TIMEFRAME_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900}
VALID_SYMBOLS = ("MGC", "MNQ")
VALID_TIMEFRAMES = ("1m", "3m", "5m", "15m")

STRATEGY_DEFS = {
    "OPENING_DRIVE":            {"label": "Opening Drive",            "max_grade": "A+",
                                 "regimes": {"TRENDING", "VOLATILE", "BALANCED"}},
    "LIQUIDITY_SWEEP_REVERSAL": {"label": "Liquidity Sweep Reversal", "max_grade": "A+",
                                 "regimes": {"VOLATILE", "RANGING", "BALANCED"}},
    "VWAP_TREND_CONTINUATION":  {"label": "VWAP Trend Continuation",  "max_grade": "A+",
                                 "regimes": {"TRENDING", "BALANCED"}},
    "RANGE_EXPANSION_BREAKOUT": {"label": "Range Expansion Breakout", "max_grade": "A",
                                 "regimes": {"RANGING", "BALANCED"}},
    "EXHAUSTION_FADE":          {"label": "Exhaustion Fade",          "max_grade": "A",
                                 "regimes": {"VOLATILE", "BALANCED"}},
}
STRATEGY_ORDER = ["OPENING_DRIVE", "LIQUIDITY_SWEEP_REVERSAL", "VWAP_TREND_CONTINUATION",
                  "RANGE_EXPANSION_BREAKOUT", "EXHAUSTION_FADE"]


# ════════════════════════════════════════════════════════════════════════════
# CSV INGESTION
# ════════════════════════════════════════════════════════════════════════════
def _norm(s):
    return str(s or "").strip().lower().replace(" ", "").replace("_", "")


_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y",
                 "%d-%m-%Y", "%Y%m%d", "%d.%m.%Y")
_TIME_FORMATS = ("%H:%M:%S", "%H:%M", "%H%M%S", "%I:%M:%S %p", "%I:%M %p")
_DATETIME_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                     "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%Y/%m/%d %H:%M:%S",
                     "%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(date_str, time_str, source_tz):
    """Combine Date + Time into a tz-aware UTC datetime. Returns None on failure.

    A pure epoch (all-digit, >= 10 chars) in the date field is treated as Unix
    seconds (UTC). Otherwise the date/time strings are read in `source_tz` then
    converted to UTC.
    """
    d = str(date_str or "").strip()
    t = str(time_str or "").strip()

    # Epoch seconds (TradingView "time" column) — already UTC.
    if d and t == "" and d.replace(".", "").isdigit() and len(d.split(".")[0]) >= 10:
        try:
            return datetime.fromtimestamp(float(d), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None

    tz = ZoneInfo(source_tz) if isinstance(source_tz, str) else (source_tz or ET_TZ)

    # Combined datetime in a single column (no separate time).
    if d and t == "":
        for fmt in _DATETIME_FORMATS:
            try:
                naive = datetime.strptime(d, fmt)
                aware = naive.replace(tzinfo=timezone.utc) if fmt.endswith("Z") else naive.replace(tzinfo=tz)
                return aware.astimezone(timezone.utc)
            except ValueError:
                continue
        return None

    # Separate Date + Time columns.
    parsed_date = None
    for fmt in _DATE_FORMATS:
        try:
            parsed_date = datetime.strptime(d, fmt).date()
            break
        except ValueError:
            continue
    if parsed_date is None:
        return None
    parsed_time = None
    for fmt in _TIME_FORMATS:
        try:
            parsed_time = datetime.strptime(t, fmt).time()
            break
        except ValueError:
            continue
    if parsed_time is None:
        return None
    naive = datetime.combine(parsed_date, parsed_time)
    return naive.replace(tzinfo=tz).astimezone(timezone.utc)


def _to_float(v):
    try:
        s = str(v).strip().replace(",", "")
        if s == "" or s.lower() in ("nan", "null", "none"):
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


def parse_candles_csv(raw_text, symbol, timeframe, source_tz="America/New_York"):
    """Parse a TradingView/broker OHLCV CSV into a sorted, deduped candle list.

    Accepts the documented Date,Time,Open,High,Low,Close,Volume layout plus
    tolerant variants: a single datetime/epoch column, header aliases, and
    ;/tab/comma delimiters. Returns a dict:
      ok, error, candles[], warnings[], row_count, skipped, dup_removed,
      inferred_timeframe, gap_count, first_ts, last_ts, sha256.
    Never raises — all failures degrade to ok=False with a human-readable error.
    """
    out = {"ok": False, "error": None, "candles": [], "warnings": [],
           "row_count": 0, "skipped": 0, "dup_removed": 0,
           "inferred_timeframe": None, "gap_count": 0,
           "first_ts": None, "last_ts": None, "sha256": None}

    if symbol not in VALID_SYMBOLS:
        out["error"] = f"Unsupported symbol '{symbol}' (expected MGC or MNQ)."
        return out
    if timeframe not in VALID_TIMEFRAMES:
        out["error"] = f"Unsupported timeframe '{timeframe}' (expected 1m/3m/5m/15m)."
        return out

    if isinstance(raw_text, bytes):
        try:
            raw_text = raw_text.decode("utf-8-sig")
        except UnicodeDecodeError:
            raw_text = raw_text.decode("latin-1", errors="replace")
    raw_text = raw_text.lstrip("\ufeff")
    out["sha256"] = hashlib.sha256(raw_text.encode("utf-8", "replace")).hexdigest()

    sample = raw_text[:4096]
    delimiter = ","
    for cand in (",", ";", "\t", "|"):
        if sample.count(cand) >= 3:
            delimiter = cand
            break
    try:
        reader = csv.reader(io.StringIO(raw_text), delimiter=delimiter)
        rows = [r for r in reader if any(str(c).strip() for c in r)]
    except csv.Error as exc:
        out["error"] = f"CSV parse error: {exc}"
        return out
    if not rows:
        out["error"] = "Empty file."
        return out

    # ── Header detection (alias map → column index) ──
    header = rows[0]
    norm_header = [_norm(c) for c in header]
    alias = {
        "date": {"date"}, "time": {"time"},
        "datetime": {"datetime", "timestamp", "time", "date"},
        "open": {"open", "o"}, "high": {"high", "h"}, "low": {"low", "l"},
        "close": {"close", "c", "last", "price"},
        "volume": {"volume", "vol", "v"},
    }

    def find(keys):
        for i, h in enumerate(norm_header):
            if h in keys:
                return i
        return None

    has_header = any(h in {"open", "o", "high", "h", "low", "l", "close", "c",
                           "date", "time", "datetime", "timestamp"} for h in norm_header)
    if has_header:
        idx = {k: find(v) for k, v in alias.items()}
        data_rows = rows[1:]
    else:
        # Headerless: assume Date,Time,Open,High,Low,Close,Volume order; if the
        # first field looks like an epoch, treat as time,o,h,l,c,v.
        first = rows[0]
        if first and str(first[0]).strip().replace(".", "").isdigit() and len(str(first[0]).split(".")[0]) >= 10:
            idx = {"datetime": 0, "date": 0, "time": None, "open": 1, "high": 2,
                   "low": 3, "close": 4, "volume": 5}
        else:
            idx = {"date": 0, "time": 1, "datetime": None, "open": 2, "high": 3,
                   "low": 4, "close": 5, "volume": 6}
        data_rows = rows

    o_i, h_i, l_i, c_i = idx.get("open"), idx.get("high"), idx.get("low"), idx.get("close")
    if None in (o_i, h_i, l_i, c_i):
        out["error"] = ("Could not find Open/High/Low/Close columns. Expected a header "
                        "like: Date,Time,Open,High,Low,Close,Volume.")
        return out
    d_i, t_i, dt_i, v_i = idx.get("date"), idx.get("time"), idx.get("datetime"), idx.get("volume")

    seen = {}
    skipped = 0
    for r in data_rows:
        try:
            maxneed = max(x for x in (o_i, h_i, l_i, c_i, d_i, t_i, dt_i, v_i) if x is not None)
            if len(r) <= maxneed:
                skipped += 1
                continue
            if t_i is not None and d_i is not None:
                ts = _parse_dt(r[d_i], r[t_i], source_tz)
            elif dt_i is not None:
                ts = _parse_dt(r[dt_i], "", source_tz)
            elif d_i is not None:
                ts = _parse_dt(r[d_i], "", source_tz)
            else:
                ts = None
            o, h, l, c = _to_float(r[o_i]), _to_float(r[h_i]), _to_float(r[l_i]), _to_float(r[c_i])
            vol = _to_float(r[v_i]) if v_i is not None else None
            if ts is None or None in (o, h, l, c):
                skipped += 1
                continue
            if h < l or h <= 0 or l <= 0:
                skipped += 1
                continue
            seen[ts] = {"ts": ts, "open": o, "high": h, "low": l, "close": c,
                        "volume": vol if vol is not None else 0.0}
        except (IndexError, ValueError, TypeError):
            skipped += 1
            continue

    if not seen:
        out["error"] = ("No valid rows parsed. Check the column order/format "
                        "(Date,Time,Open,High,Low,Close,Volume).")
        return out

    candles = [seen[k] for k in sorted(seen.keys())]
    out["dup_removed"] = (len(data_rows) - skipped) - len(candles)
    out["skipped"] = skipped

    # ── Price-scale sanity (catches an MNQ file uploaded as MGC, etc.) ──
    spec = BT_SPECS[symbol]
    med_close = statistics.median(c["close"] for c in candles)
    if not (spec["price_lo"] <= med_close <= spec["price_hi"]):
        out["error"] = (f"Median price {med_close:.1f} is outside the plausible {symbol} "
                        f"range ({spec['price_lo']:.0f}–{spec['price_hi']:.0f}). "
                        f"Is this the right instrument/scale?")
        return out

    # ── Timeframe inference from the median delta ──
    deltas = [(candles[i]["ts"] - candles[i - 1]["ts"]).total_seconds()
              for i in range(1, len(candles))]
    if deltas:
        med_delta = statistics.median(deltas)
        inferred = min(TIMEFRAME_SECONDS, key=lambda k: abs(TIMEFRAME_SECONDS[k] - med_delta))
        out["inferred_timeframe"] = inferred
        if inferred != timeframe:
            out["warnings"].append(
                f"Declared timeframe {timeframe} but the data looks like {inferred} "
                f"(median spacing {med_delta:.0f}s). Using declared {timeframe}.")
        expected = TIMEFRAME_SECONDS[timeframe]
        out["gap_count"] = sum(1 for d in deltas if d > expected * 2.0)

    out["candles"] = candles
    out["row_count"] = len(candles)
    out["first_ts"] = candles[0]["ts"]
    out["last_ts"] = candles[-1]["ts"]
    out["ok"] = True
    return out


# ════════════════════════════════════════════════════════════════════════════
# CAUSAL INDICATOR RECONSTRUCTION
# ════════════════════════════════════════════════════════════════════════════
def _session_for_et(et_dt):
    """(name, id) for an ET datetime — Asia 18→02 (wraps), London 02→08,
    New York 08→16; (None,None) in the 16–18 gap."""
    h = et_dt.hour + et_dt.minute / 60.0
    d = et_dt.date()
    if h >= 18.0:
        return ("Asia", d.isoformat())
    if h < 2.0:
        return ("Asia", (d - timedelta(days=1)).isoformat())
    if 2.0 <= h < 8.0:
        return ("London", d.isoformat())
    if 8.0 <= h < 16.0:
        return ("New York", d.isoformat())
    return (None, None)


def compute_indicators(candles, mode="SCALP"):
    """Return a list of per-bar causal indicator snapshots (one dict per candle).

    Every value at index i is computed using ONLY candles[0..i] (no look-ahead).
    Snapshot keys: ts, et, hour, session, open/high/low/close/volume, vwap,
    atr, atr_ratio, regime, rvol, cvd, cvd_state, swing_high, swing_low,
    trend, bos_long, bos_short, choch_long, choch_short, hh, hl, lh, ll,
    demand_zone, supply_zone, demand_mitigated, supply_mitigated,
    sweep_bull, sweep_bear, confirm_bull, confirm_bear, or_high, or_low,
    or_complete, range_high, range_low, range_width.
    """
    mk = BT_MODES.get(mode, BT_MODES["SCALP"])
    n = len(candles)
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    opens = [c["open"] for c in candles]
    vols = [c["volume"] or 0.0 for c in candles]
    range_bars = max(2, int(RANGE_LOOKBACK_MIN * 60 / max(
        1, _infer_tf_seconds(candles))))

    snaps = []
    # Rolling state.
    trs = []                       # session true-range list (for baseline)
    sess_vwap_num = sess_vwap_den = 0.0
    cvd = 0.0
    cur_session_id = None
    swing_high = swing_low = None
    prev_ph = prev_pl = None       # previous confirmed pivot values (for HH/HL/LH/LL)
    trend = 0
    last_bear_idx = last_bull_idx = None
    demand_zone = supply_zone = None
    demand_mit = supply_mit = False
    last_swept_high = last_swept_low = None
    or_high = or_low = None
    or_date = None
    or_complete = False
    prev_close = None

    for i in range(n):
        c = candles[i]
        et = c["ts"].astimezone(ET_TZ)
        h_et = et.hour + et.minute / 60.0
        sess_name, sess_id_part = _session_for_et(et)

        # VWAP session anchor: reset at the first bar at/after 18:00 ET each day.
        vwap_day = (et.date() if h_et >= VWAP_RESET_ET else (et.date() - timedelta(days=1))).isoformat()
        if vwap_day != cur_session_id:
            cur_session_id = vwap_day
            sess_vwap_num = sess_vwap_den = 0.0
            cvd = 0.0
            trs = []
        typical = (c["high"] + c["low"] + c["close"]) / 3.0
        vol = vols[i]
        sess_vwap_num += typical * (vol if vol > 0 else 1.0)
        sess_vwap_den += (vol if vol > 0 else 1.0)
        vwap = sess_vwap_num / sess_vwap_den if sess_vwap_den else c["close"]

        # CVD proxy (per-session cumulative signed volume).
        sign = 1.0 if c["close"] > c["open"] else (-1.0 if c["close"] < c["open"] else 0.0)
        cvd += sign * (vol if vol > 0 else 1.0)

        # True range + ATR(14) + baseline ratio.
        tr = (c["high"] - c["low"]) if prev_close is None else max(
            c["high"] - c["low"], abs(c["high"] - prev_close), abs(c["low"] - prev_close))
        trs.append(tr)
        recent = trs[-ATR_BARS:]
        atr = sum(recent) / len(recent) if recent else 0.0
        if len(trs) >= VOL_MIN_BARS:
            baseline = statistics.median(trs)
            atr_ratio = (atr / baseline) if baseline > 0 else None
        else:
            atr_ratio = None
        prev_close = c["close"]

        # RVOL (current vol / trailing average).
        if i >= 1:
            window = vols[max(0, i - RVOL_LOOKBACK):i]
            avg_v = (sum(window) / len(window)) if window else 0.0
            rvol = (vol / avg_v) if avg_v > 0 else None
        else:
            rvol = None

        # Pivots confirmed at bar (i-RIGHT) using strict local-extreme test.
        p = i - PIVOT_RIGHT
        if p - PIVOT_LEFT >= 0:
            seg_h = highs[p - PIVOT_LEFT:p + PIVOT_RIGHT + 1]
            seg_l = lows[p - PIVOT_LEFT:p + PIVOT_RIGHT + 1]
            center_h, center_l = highs[p], lows[p]
            if center_h == max(seg_h) and seg_h.count(center_h) == 1:
                swing_high = center_h
                if prev_ph is not None:
                    pass  # HH/LH classified at break time via prev_ph below
                prev_ph_new = center_h
            else:
                prev_ph_new = None
            if center_l == min(seg_l) and seg_l.count(center_l) == 1:
                swing_low = center_l
                prev_pl_new = center_l
            else:
                prev_pl_new = None
        else:
            prev_ph_new = prev_pl_new = None

        hh = hl = lh = ll = False
        if prev_ph_new is not None:
            if prev_ph is not None:
                hh = prev_ph_new > prev_ph
                lh = prev_ph_new < prev_ph
            prev_ph = prev_ph_new
        if prev_pl_new is not None:
            if prev_pl is not None:
                hl = prev_pl_new > prev_pl
                ll = prev_pl_new < prev_pl
            prev_pl = prev_pl_new

        # Structure: close breaking the most recent confirmed swing.
        bos_long = bos_short = choch_long = choch_short = False
        if swing_high is not None and c["close"] > swing_high:
            if trend < 0:
                choch_long = True
            else:
                bos_long = True
            trend = 1
            # Demand order block = last bearish candle before this break.
            if last_bear_idx is not None:
                demand_zone = highs[last_bear_idx]
                demand_mit = False
            swing_high = None  # consumed; await the next confirmed pivot high
        if swing_low is not None and c["close"] < swing_low:
            if trend > 0:
                choch_short = True
            else:
                bos_short = True
            trend = -1
            if last_bull_idx is not None:
                supply_zone = lows[last_bull_idx]
                supply_mit = False
            swing_low = None

        # Zone mitigation (price tapping back into the proximal edge).
        if demand_zone is not None and c["low"] <= demand_zone:
            demand_mit = True
        if supply_zone is not None and c["high"] >= supply_zone:
            supply_mit = True

        # Liquidity sweep (matches liquidity_sweep.pine, once per swing level).
        sweep_bull = sweep_bear = False
        # NB: sweeps use the swing as of this bar — recompute the "active" swing
        # for sweeps from the last confirmed pivot still standing.
        active_sh = _last_standing(highs, i, PIVOT_LEFT, PIVOT_RIGHT, want_high=True)
        active_sl = _last_standing(lows, i, PIVOT_LEFT, PIVOT_RIGHT, want_high=False)
        if active_sh is not None and c["high"] > active_sh and c["close"] < active_sh:
            if last_swept_high != active_sh:
                sweep_bear = True
                last_swept_high = active_sh
        if active_sl is not None and c["low"] < active_sl and c["close"] > active_sl:
            if last_swept_low != active_sl:
                sweep_bull = True
                last_swept_low = active_sl

        # 5m confirmation candle (matches confirmation_candle.pine).
        confirm_bull = (i >= 1 and c["close"] > c["open"] and c["close"] > closes[i - 1])
        confirm_bear = (i >= 1 and c["close"] < c["open"] and c["close"] < closes[i - 1])

        # Track last opposite-color candle indices for the NEXT order block.
        if c["close"] < c["open"]:
            last_bear_idx = i
        elif c["close"] > c["open"]:
            last_bull_idx = i

        # Opening range (08:00–08:30 ET) per ET day.
        et_date = et.date().isoformat()
        if or_date != et_date:
            or_date, or_high, or_low, or_complete = et_date, None, None, False
        or_end = OPENING_RANGE_START_ET + OPENING_RANGE_BUILD_MIN / 60.0
        if OPENING_RANGE_START_ET <= h_et < or_end:
            or_high = c["high"] if or_high is None else max(or_high, c["high"])
            or_low = c["low"] if or_low is None else min(or_low, c["low"])
        elif h_et >= or_end and or_high is not None:
            or_complete = True

        # Rolling consolidation range (exclude the current bar).
        if i >= 2:
            seg = candles[max(0, i - range_bars):i]
            rng_high = max(x["high"] for x in seg)
            rng_low = min(x["low"] for x in seg)
            rng_width = rng_high - rng_low
        else:
            rng_high = rng_low = rng_width = None

        regime = _classify_regime(atr_ratio, mk, vwap, c["close"], atr, trend)

        snaps.append({
            "i": i, "ts": c["ts"], "et": et, "hour": et.hour,
            "session": sess_name, "open": c["open"], "high": c["high"],
            "low": c["low"], "close": c["close"], "volume": vol,
            "vwap": vwap, "atr": atr, "atr_ratio": atr_ratio, "regime": regime,
            "rvol": rvol, "cvd": cvd,
            "cvd_state": _cvd_state(snaps, cvd),
            "swing_high": active_sh, "swing_low": active_sl, "trend": trend,
            "bos_long": bos_long, "bos_short": bos_short,
            "choch_long": choch_long, "choch_short": choch_short,
            "hh": hh, "hl": hl, "lh": lh, "ll": ll,
            "demand_zone": demand_zone, "supply_zone": supply_zone,
            "demand_mitigated": demand_mit, "supply_mitigated": supply_mit,
            "sweep_bull": sweep_bull, "sweep_bear": sweep_bear,
            "confirm_bull": confirm_bull, "confirm_bear": confirm_bear,
            "or_high": or_high, "or_low": or_low, "or_complete": or_complete,
            "range_high": rng_high, "range_low": rng_low, "range_width": rng_width,
            "volume_confirmed": bool(rvol is not None and rvol >= mk["rvol_confirm"]),
        })
    return snaps


def _infer_tf_seconds(candles):
    if len(candles) < 2:
        return 300
    deltas = [(candles[i]["ts"] - candles[i - 1]["ts"]).total_seconds()
              for i in range(1, min(len(candles), 50))]
    return statistics.median(deltas) if deltas else 300


def _last_standing(series, i, left, right, want_high):
    """The most recent confirmed pivot value at/at-before bar i (causal)."""
    last = None
    p = i - right
    lo = max(left, 0)
    # Walk back a bounded window to find the latest confirmed pivot.
    for q in range(p, max(lo - 1, left - 1), -1):
        if q - left < 0:
            break
        seg = series[q - left:q + right + 1]
        center = series[q]
        if want_high:
            if center == max(seg) and seg.count(center) == 1:
                last = center
                break
        else:
            if center == min(seg) and seg.count(center) == 1:
                last = center
                break
    return last


def _classify_regime(atr_ratio, mk, vwap, close, atr, trend):
    if atr_ratio is None:
        return "BALANCED"
    if atr_ratio >= mk["vol_high_caution"]:
        return "VOLATILE"
    dist_atr = (abs(close - vwap) / atr) if atr else 0.0
    if trend != 0 and dist_atr >= 1.0:
        return "TRENDING"
    if atr_ratio <= mk["vol_quiet_caution"]:
        return "RANGING"
    return "BALANCED"


def _cvd_state(prev_snaps, cvd_now):
    if len(prev_snaps) < CVD_SLOPE_BARS:
        return None
    past = prev_snaps[-CVD_SLOPE_BARS]["cvd"]
    if cvd_now > past:
        return "bullish"
    if cvd_now < past:
        return "bearish"
    return None


# ════════════════════════════════════════════════════════════════════════════
# STRATEGY DETECTORS — each returns (direction, entry_reason) or None at bar i.
# Pure functions of the causal snapshot `s` (state AS OF the close of bar i).
# ════════════════════════════════════════════════════════════════════════════
def _near_vwap(s):
    return s["atr"] and abs(s["close"] - s["vwap"]) <= STRAT_VWAP_PULLBACK_ATR * s["atr"]


def detect_opening_drive(s):
    if not (OPENING_RANGE_START_ET <= s["hour"] + 0 < OPENING_DRIVE_END_ET):
        return None
    if not s["or_complete"] or s["or_high"] is None:
        return None
    if s["close"] > s["or_high"] and s["close"] > s["vwap"] and s["volume_confirmed"]:
        return ("Long", f"Opening-range breakout above {s['or_high']:.2f} (08–10 ET) with volume")
    if s["close"] < s["or_low"] and s["close"] < s["vwap"] and s["volume_confirmed"]:
        return ("Short", f"Opening-range breakdown below {s['or_low']:.2f} (08–10 ET) with volume")
    return None


def detect_liquidity_sweep_reversal(s):
    if s["sweep_bull"] and s["close"] > s["vwap"] - s["atr"]:
        return ("Long", "Bullish liquidity sweep — swept sell-side stops & reclaimed")
    if s["sweep_bear"] and s["close"] < s["vwap"] + s["atr"]:
        return ("Short", "Bearish liquidity sweep — swept buy-side stops & reclaimed")
    return None


def detect_vwap_trend_continuation(s):
    if s["trend"] > 0 and s["close"] > s["vwap"] and _near_vwap(s) and s["confirm_bull"]:
        return ("Long", "VWAP pullback continuation in uptrend (confirmed)")
    if s["trend"] < 0 and s["close"] < s["vwap"] and _near_vwap(s) and s["confirm_bear"]:
        return ("Short", "VWAP pullback continuation in downtrend (confirmed)")
    return None


def detect_range_expansion_breakout(s):
    if s["range_width"] is None or not s["atr"]:
        return None
    tight = s["range_width"] <= STRAT_RANGE_TIGHT_ATR * s["atr"]
    if not tight:
        return None
    if s["close"] > s["range_high"] and s["volume_confirmed"]:
        return ("Long", "Range expansion breakout above consolidation")
    if s["close"] < s["range_low"] and s["volume_confirmed"]:
        return ("Short", "Range expansion breakdown below consolidation")
    return None


def detect_exhaustion_fade(s):
    if not s["atr"]:
        return None
    ext = abs(s["close"] - s["vwap"]) / s["atr"]
    if ext < STRAT_EXHAUSTION_EXT_ATR:
        return None
    # Overextended ABOVE vwap → fade short on a bearish reversal sign.
    if s["close"] > s["vwap"] and (s["sweep_bear"] or s["confirm_bear"]):
        return ("Short", f"Exhaustion fade — {ext:.1f}×ATR above VWAP, bearish reversal")
    if s["close"] < s["vwap"] and (s["sweep_bull"] or s["confirm_bull"]):
        return ("Long", f"Exhaustion fade — {ext:.1f}×ATR below VWAP, bullish reversal")
    return None


DETECTORS = {
    "OPENING_DRIVE": detect_opening_drive,
    "LIQUIDITY_SWEEP_REVERSAL": detect_liquidity_sweep_reversal,
    "VWAP_TREND_CONTINUATION": detect_vwap_trend_continuation,
    "RANGE_EXPANSION_BREAKOUT": detect_range_expansion_breakout,
    "EXHAUSTION_FADE": detect_exhaustion_fade,
}


# ════════════════════════════════════════════════════════════════════════════
# STOP / TARGET PLAN — copied from live _dynamic_stop_plan (ATR explicit input).
# ════════════════════════════════════════════════════════════════════════════
def bt_stop_plan(direction, entry, demand_zone, supply_zone, spec, atr, mode, regime):
    """ATR×mult stop blended with the nearest zone, floored at min ticks. Mirrors
    _dynamic_stop_plan: the WIDER of the ATR stop and the structure stop, snapped
    up to whole ticks, with the volatility-wins-over-mode multiplier."""
    if atr is None or atr <= 0:
        return None
    tick = spec["tick_size"]
    buf = spec["stop_buf"]
    min_ticks = int(spec["min_stop_ticks"])
    mk = BT_MODES.get(mode, BT_MODES["SCALP"])
    if regime in ("VOLATILE",):
        mult = 2.0
    else:
        mult = mk["stop_mult"]
    atr_dist = atr * mult
    if direction == "Long":
        atr_stop = entry - atr_dist
        structure_stop = (demand_zone - buf) if demand_zone is not None else None
        calc_stop = min(atr_stop, structure_stop) if structure_stop is not None else atr_stop
    else:
        atr_stop = entry + atr_dist
        structure_stop = (supply_zone + buf) if supply_zone is not None else None
        calc_stop = max(atr_stop, structure_stop) if structure_stop is not None else atr_stop
    calc_ticks = abs(entry - calc_stop) / tick if tick else 0.0
    final_ticks = max(math.ceil(calc_ticks - 1e-9), min_ticks)
    final_dist = final_ticks * tick
    final_stop = (entry - final_dist) if direction == "Long" else (entry + final_dist)
    if direction == "Long" and not final_stop < entry:
        return None
    if direction == "Short" and not final_stop > entry:
        return None
    risk = abs(entry - final_stop)
    if risk <= 0:
        return None
    return {"stop": final_stop, "risk_points": risk, "multiplier": mult,
            "stop_ticks": int(final_ticks)}


# ════════════════════════════════════════════════════════════════════════════
# TRADE SIMULATOR — one strategy at a time, single open position, no look-ahead.
# ════════════════════════════════════════════════════════════════════════════
def simulate_strategy(snaps, candles, strat_key, spec, mode,
                      slippage_ticks=1.0, commission_per_side=0.62,
                      session_filter=None):
    """Replay one strategy over the snapshots. Entry on the bar AFTER a
    close-confirmed signal (next-bar open ± slippage). Management: 50% off at TP1
    + stop→breakeven, runner to TP3; worst-case same-bar fill (stop/BE first).
    Returns a list of closed-trade dicts."""
    trades = []
    n = len(snaps)
    tick = spec["tick_size"]
    pv = spec["point_value"]
    slip = slippage_ticks * tick
    mk = BT_MODES.get(mode, BT_MODES["SCALP"])
    detector = DETECTORS[strat_key]
    i = 0
    while i < n - 1:
        s = snaps[i]
        if session_filter and s["session"] != session_filter:
            i += 1
            continue
        sig = detector(s)
        if not sig:
            i += 1
            continue
        direction, entry_reason = sig
        # Conflict guard: skip if opposite same-bar structure break.
        if direction == "Long" and s["choch_short"]:
            i += 1
            continue
        if direction == "Short" and s["choch_long"]:
            i += 1
            continue

        entry_bar = i + 1
        raw_entry = candles[entry_bar]["open"]
        entry = raw_entry + slip if direction == "Long" else raw_entry - slip
        plan = bt_stop_plan(direction, entry,
                            s["demand_zone"], s["supply_zone"], spec, s["atr"],
                            mode, s["regime"])
        if plan is None:
            i += 1
            continue
        stop = plan["stop"]
        risk = plan["risk_points"]
        tp1d, tp2d, tp3d = spec["tp1"], spec["tp2"], spec["tp3"]
        if mk["enforce_min_rr"] and (tp2d / risk) < 2.0:
            i += 1
            continue
        if direction == "Long":
            tp1, tp3 = entry + tp1d, entry + tp3d
        else:
            tp1, tp3 = entry - tp1d, entry - tp3d

        # ── Walk forward to resolve the trade ──
        result = _walk_trade(snaps, candles, entry_bar, direction, entry, stop,
                             tp1, tp3, slip)
        exit_price, exit_bar, exit_reason, half1_pts, half2_pts = result
        gross_pts = 0.5 * half1_pts + 0.5 * half2_pts
        commission = commission_per_side * 2.0
        pnl_dollars = gross_pts * pv - commission
        r_mult = pnl_dollars / (risk * pv) if risk > 0 else 0.0
        entry_ts = candles[entry_bar]["ts"]
        exit_ts = candles[exit_bar]["ts"]
        hold_min = (exit_ts - entry_ts).total_seconds() / 60.0
        trades.append({
            "strategy": strat_key, "direction": direction,
            "entry_ts": entry_ts.isoformat(), "exit_ts": exit_ts.isoformat(),
            "entry": round(entry, 4), "stop": round(stop, 4),
            "exit": round(exit_price, 4),
            "tp1": round(tp1, 4), "tp3": round(tp3, 4),
            "risk_points": round(risk, 4), "gross_points": round(gross_pts, 4),
            "pnl_dollars": round(pnl_dollars, 2), "r_multiple": round(r_mult, 4),
            "regime": s["regime"], "session": s["session"] or "Off-hours",
            "entry_hour_et": entry_ts.astimezone(ET_TZ).hour,
            "entry_reason": entry_reason, "exit_reason": exit_reason,
            "hold_minutes": round(hold_min, 1),
        })
        # Resume scanning AFTER the trade closes (one open position per strategy).
        i = max(exit_bar, entry_bar) + 1
    return trades


def _walk_trade(snaps, candles, entry_bar, direction, entry, stop, tp1, tp3, slip):
    """Resolve a single trade. Returns
    (exit_price, exit_bar, exit_reason, half1_points, half2_points).
    half1 = TP1 runner's points, half2 = runner's points (both signed, per unit)."""
    n = len(candles)
    stage = 0          # 0 = full size @ initial stop; 1 = half size @ breakeven
    be = entry
    half1_pts = 0.0
    j = entry_bar
    while j < n:
        bar = candles[j]
        hi, lo = bar["high"], bar["low"]
        if direction == "Long":
            if stage == 0:
                hit_stop = lo <= stop
                hit_tp1 = hi >= tp1
                if hit_stop:                       # worst-case: stop wins ties
                    px = stop - slip
                    return (px, j, "Stop loss (-1R)", (px - entry), (px - entry))
                if hit_tp1:
                    half1_pts = (tp1 - entry)      # locked at TP1, no slip on limit
                    stage = 1
                    # Same-bar worst-case: the runner's stop jumps to breakeven the
                    # instant TP1 fills. If THIS bar also traded back down to BE we
                    # cannot prove the intrabar order, so resolve stop/BE first and
                    # close the runner here rather than optimistically carrying it.
                    if lo <= be:
                        px = be - slip
                        return (px, j, "TP1 hit, runner stopped at breakeven",
                                half1_pts, (px - entry))
            else:  # stage 1 — runner to TP3, stop at breakeven
                hit_be = lo <= be
                hit_tp3 = hi >= tp3
                if hit_be:
                    px = be - slip
                    return (px, j, "TP1 hit, runner stopped at breakeven", half1_pts, (px - entry))
                if hit_tp3:
                    return (tp3, j, "TP1 + runner to TP3", half1_pts, (tp3 - entry))
        else:  # Short
            if stage == 0:
                hit_stop = hi >= stop
                hit_tp1 = lo <= tp1
                if hit_stop:
                    px = stop + slip
                    return (px, j, "Stop loss (-1R)", (entry - px), (entry - px))
                if hit_tp1:
                    half1_pts = (entry - tp1)
                    stage = 1
                    # Same-bar worst-case (short): if this bar also traded back up
                    # to breakeven after TP1, resolve stop/BE first and close here.
                    if hi >= be:
                        px = be + slip
                        return (px, j, "TP1 hit, runner stopped at breakeven",
                                half1_pts, (entry - px))
            else:
                hit_be = hi >= be
                hit_tp3 = lo <= tp3
                if hit_be:
                    px = be + slip
                    return (px, j, "TP1 hit, runner stopped at breakeven", half1_pts, (entry - px))
                if hit_tp3:
                    return (tp3, j, "TP1 + runner to TP3", half1_pts, (entry - tp3))
        j += 1
    # Ran out of data — close remainder at the last close.
    last = candles[-1]["close"]
    if direction == "Long":
        rem = (last - entry)
    else:
        rem = (entry - last)
    if stage == 0:
        return (last, n - 1, "Open at end of data (closed at last close)", rem, rem)
    return (last, n - 1, "TP1 hit, runner closed at end of data", half1_pts, rem)


# ════════════════════════════════════════════════════════════════════════════
# METRICS + AGGREGATION
# ════════════════════════════════════════════════════════════════════════════
def _max_drawdown_r(equity):
    peak = 0.0
    max_dd = 0.0
    cum = 0.0
    for r in equity:
        cum += r
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return max_dd


def _strategy_metrics(trades):
    if not trades:
        return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": None,
                "profit_factor": None, "net_pnl": 0.0, "net_r": 0.0, "avg_r": None,
                "max_drawdown_r": 0.0, "best_hour": None, "best_regime": None,
                "avg_hold_minutes": None, "equity_curve": []}
    rs = [t["r_multiple"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (None if gross_profit == 0 else float("inf"))
    # Best hour / regime by net R.
    by_hour, by_regime = {}, {}
    for t in trades:
        by_hour.setdefault(t["entry_hour_et"], []).append(t["r_multiple"])
        by_regime.setdefault(t["regime"], []).append(t["r_multiple"])
    best_hour = max(by_hour, key=lambda h: sum(by_hour[h])) if by_hour else None
    best_regime = max(by_regime, key=lambda g: sum(by_regime[g])) if by_regime else None
    # Equity curve ordered by exit time.
    ordered = sorted(trades, key=lambda t: t["exit_ts"])
    cum = 0.0
    curve = []
    for t in ordered:
        cum += t["r_multiple"]
        curve.append({"ts": t["exit_ts"], "cum_r": round(cum, 4)})
    return {
        "total_trades": len(trades),
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100.0, 1),
        "profit_factor": (round(pf, 2) if pf not in (None, float("inf")) else
                          ("∞" if pf == float("inf") else None)),
        "net_pnl": round(sum(t["pnl_dollars"] for t in trades), 2),
        "net_r": round(sum(rs), 2),
        "avg_r": round(sum(rs) / len(rs), 3),
        "max_drawdown_r": round(_max_drawdown_r([t["r_multiple"] for t in ordered]), 2),
        "best_hour": best_hour,
        "best_hour_label": (f"{best_hour:02d}:00–{(best_hour + 1) % 24:02d}:00 ET"
                            if best_hour is not None else None),
        "best_regime": best_regime,
        "avg_hold_minutes": round(sum(t["hold_minutes"] for t in trades) / len(trades), 1),
        "equity_curve": curve,
    }


def _signal_agreement(snaps, live_signals):
    """Compare reconstructed structure/zone/sweep signals to captured live
    signals. With no captured live data (the default for historical CSVs) this
    returns available=False plus the reconstructed counts for transparency."""
    recon = {
        "bos": sum(1 for s in snaps if s["bos_long"] or s["bos_short"]),
        "choch": sum(1 for s in snaps if s["choch_long"] or s["choch_short"]),
        "zone": sum(1 for s in snaps if s["bos_long"] or s["bos_short"]
                    or s["choch_long"] or s["choch_short"]),
        "sweep": sum(1 for s in snaps if s["sweep_bull"] or s["sweep_bear"]),
    }
    if not live_signals:
        return {"available": False,
                "message": ("No captured live TradingView signals for this period. "
                            "Agreement scoring needs forward-captured live alerts "
                            "(none are stored for historical ranges)."),
                "reconstructed": recon}
    # Forward-capture comparison (BT007): match within a tolerance window.
    return _match_live_signals(snaps, live_signals, recon)


def _match_live_signals(snaps, live_signals, recon):
    tol = timedelta(minutes=10)
    buckets = {"bos": [], "choch": [], "zone": [], "sweep": []}
    for s in snaps:
        if s["bos_long"] or s["bos_short"]:
            buckets["bos"].append(s["ts"]); buckets["zone"].append(s["ts"])
        if s["choch_long"] or s["choch_short"]:
            buckets["choch"].append(s["ts"]); buckets["zone"].append(s["ts"])
        if s["sweep_bull"] or s["sweep_bear"]:
            buckets["sweep"].append(s["ts"])
    live = {"bos": [], "choch": [], "zone": [], "sweep": []}
    for ev in live_signals:
        kind = ev.get("kind"); ts = ev.get("ts")
        if kind in live and ts is not None:
            live[kind].append(ts)
            if kind in ("bos", "choch"):
                live["zone"].append(ts)

    def pct(kind):
        recon_ts, live_ts = buckets[kind], live[kind]
        if not live_ts and not recon_ts:
            return None
        denom = max(len(live_ts), 1)
        matched = 0
        for lt in live_ts:
            if any(abs((lt - rt).total_seconds()) <= tol.total_seconds() for rt in recon_ts):
                matched += 1
        return round(matched / denom * 100.0, 1)

    parts = {k: pct(k) for k in ("bos", "choch", "zone", "sweep")}
    vals = [v for v in parts.values() if v is not None]
    overall = round(sum(vals) / len(vals), 1) if vals else None
    diagnostics = None
    if overall is not None and overall < 90.0:
        diagnostics = [f"{k.upper()} agreement {v}%" for k, v in parts.items()
                       if v is not None and v < 90.0]
    return {"available": True, "match": parts, "overall": overall,
            "reconstructed": recon, "diagnostics": diagnostics}


def run_backtest(candles, params):
    """Top-level entry. `params`: symbol, timeframe, mode, strategies(list|None=all),
    session(None|Asia|London|New York), start_ts/end_ts(datetime|None),
    slippage_ticks, commission_per_side, live_signals(optional list).
    Returns a results dict (JSON-serializable) — pure, no side effects."""
    symbol = params.get("symbol", "MGC")
    mode = (params.get("mode") or "SCALP").upper()
    if mode not in BT_MODES:
        mode = "SCALP"
    spec = BT_SPECS.get(symbol, BT_SPECS["MGC"])
    session_filter = params.get("session") or None
    slippage = float(params.get("slippage_ticks", 1.0))
    commission = float(params.get("commission_per_side", 0.62))
    want = params.get("strategies") or STRATEGY_ORDER
    want = [s for s in want if s in DETECTORS]

    # Date-range filter (inclusive), applied BEFORE indicator computation so the
    # session-anchored VWAP/ATR warm up within the selected window.
    start_ts, end_ts = params.get("start_ts"), params.get("end_ts")
    if start_ts or end_ts:
        candles = [c for c in candles
                   if (start_ts is None or c["ts"] >= start_ts)
                   and (end_ts is None or c["ts"] <= end_ts)]
    if len(candles) < VOL_MIN_BARS + PIVOT_LEFT + PIVOT_RIGHT + 2:
        return {"ok": False, "error": (f"Not enough candles in range "
                f"({len(candles)}) — need at least {VOL_MIN_BARS + PIVOT_LEFT + PIVOT_RIGHT + 2}.")}

    snaps = compute_indicators(candles, mode=mode)
    per_strategy = {}
    all_trades = []
    for key in want:
        trades = simulate_strategy(snaps, candles, key, spec, mode,
                                   slippage_ticks=slippage,
                                   commission_per_side=commission,
                                   session_filter=session_filter)
        m = _strategy_metrics(trades)
        m["key"] = key
        m["label"] = STRATEGY_DEFS[key]["label"]
        m["trades"] = trades
        per_strategy[key] = m
        all_trades.extend(trades)

    # Ranking: net R desc, then profit factor, then win rate.
    def rank_key(m):
        pf = m["profit_factor"]
        pf_num = (1e9 if pf == "∞" else (pf if isinstance(pf, (int, float)) else -1))
        return (m["net_r"], pf_num, m["win_rate"] or 0)
    ranking = sorted((per_strategy[k] for k in want),
                     key=rank_key, reverse=True)
    ranking_keys = [m["key"] for m in ranking]

    # Combined equity curve across all selected strategies (by exit time).
    cum = 0.0
    combined_curve = []
    for t in sorted(all_trades, key=lambda x: x["exit_ts"]):
        cum += t["r_multiple"]
        combined_curve.append({"ts": t["exit_ts"], "cum_r": round(cum, 4)})

    return {
        "ok": True,
        "symbol": symbol, "mode": mode, "timeframe": params.get("timeframe"),
        "session": session_filter or "All",
        "bars": len(candles),
        "first_ts": candles[0]["ts"].isoformat(),
        "last_ts": candles[-1]["ts"].isoformat(),
        "slippage_ticks": slippage, "commission_per_side": commission,
        "strategies": {k: {kk: vv for kk, vv in per_strategy[k].items() if kk != "trades"}
                       for k in want},
        "ranking": ranking_keys,
        "trades": all_trades,
        "combined_equity_curve": combined_curve,
        "total_trades": len(all_trades),
        "signal_agreement": _signal_agreement(snaps, params.get("live_signals")),
    }


# ════════════════════════════════════════════════════════════════════════════
# SELF-TEST — synthetic data + invariant checks. Run: python backtest_engine.py
# ════════════════════════════════════════════════════════════════════════════
def _synthetic_candles(n=900, symbol="MGC", seed=7):
    import random
    rnd = random.Random(seed)
    base = 2400.0 if symbol == "MGC" else 18000.0
    step = 0.5 if symbol == "MGC" else 5.0
    start = datetime(2026, 1, 5, 0, 0, tzinfo=ET_TZ).astimezone(timezone.utc)
    out = []
    price = base
    for k in range(n):
        drift = math.sin(k / 30.0) * step * 4
        price += rnd.uniform(-step, step) + drift * 0.05
        o = price
        c = price + rnd.uniform(-step, step) * 3
        hi = max(o, c) + rnd.uniform(0, step) * 3
        lo = min(o, c) - rnd.uniform(0, step) * 3
        vol = rnd.uniform(50, 500)
        ts = start + timedelta(minutes=5 * k)
        out.append({"ts": ts, "open": round(o, 2), "high": round(hi, 2),
                    "low": round(lo, 2), "close": round(c, 2), "volume": vol})
        price = c
    return out


def _self_test():
    print("── backtest_engine self-test ──")
    # 1) CSV round-trip
    rows = ["Date,Time,Open,High,Low,Close,Volume"]
    cs = _synthetic_candles(120, "MGC")
    for c in cs:
        et = c["ts"].astimezone(ET_TZ)
        rows.append(f"{et.strftime('%Y-%m-%d')},{et.strftime('%H:%M:%S')},"
                    f"{c['open']},{c['high']},{c['low']},{c['close']},{int(c['volume'])}")
    parsed = parse_candles_csv("\n".join(rows), "MGC", "5m")
    assert parsed["ok"], parsed["error"]
    assert parsed["row_count"] == 120, parsed["row_count"]
    assert parsed["inferred_timeframe"] == "5m", parsed["inferred_timeframe"]
    print(f"CSV parse OK: {parsed['row_count']} rows, tf={parsed['inferred_timeframe']}, "
          f"sha={parsed['sha256'][:8]}")

    # 2) Scale-mismatch rejection
    bad = parse_candles_csv("\n".join(rows), "MNQ", "5m")
    assert not bad["ok"] and "range" in (bad["error"] or ""), bad
    print(f"Scale-mismatch rejected: {bad['error'][:60]}...")

    # 3) Indicators causal + no NaNs
    big = _synthetic_candles(900, "MGC")
    snaps = compute_indicators(big, mode="SCALP")
    assert len(snaps) == 900
    assert all(s["vwap"] is not None for s in snaps)
    print(f"Indicators OK: {len(snaps)} snapshots; "
          f"sweeps={sum(1 for s in snaps if s['sweep_bull'] or s['sweep_bear'])}, "
          f"bos={sum(1 for s in snaps if s['bos_long'] or s['bos_short'])}, "
          f"choch={sum(1 for s in snaps if s['choch_long'] or s['choch_short'])}")

    # 4) Full run + invariants
    res = run_backtest(big, {"symbol": "MGC", "timeframe": "5m", "mode": "SCALP"})
    assert res["ok"], res.get("error")
    for t in res["trades"]:
        assert t["exit_ts"] >= t["entry_ts"], "exit before entry (look-ahead!)"
        assert t["risk_points"] > 0
        for k in ("strategy", "regime", "session", "entry_reason", "exit_reason"):
            assert t[k], f"missing tag {k}"
    print(f"Run OK: {res['total_trades']} trades across {len(res['strategies'])} strategies; "
          f"ranking={res['ranking']}")
    for k in STRATEGY_ORDER:
        m = res["strategies"][k]
        print(f"   {m['label']:<26} trades={m['total_trades']:<3} "
              f"win%={m['win_rate']} netR={m['net_r']} PF={m['profit_factor']} "
              f"maxDD={m['max_drawdown_r']}")
    assert res["signal_agreement"]["available"] is False
    print("Signal agreement honestly unavailable (no live capture). ✓")

    # 5) SWING enforce-min-RR path
    res2 = run_backtest(big, {"symbol": "MGC", "timeframe": "5m", "mode": "SWING"})
    assert res2["ok"]
    print(f"SWING run OK: {res2['total_trades']} trades")
    print("ALL SELF-TESTS PASSED ✓")


if __name__ == "__main__":
    _self_test()
