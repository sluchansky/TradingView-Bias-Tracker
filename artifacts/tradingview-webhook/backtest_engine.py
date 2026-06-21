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
import re
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
# Filename ticker tokens → backtest instrument. The full-size underlyings (GC/NQ)
# map to their micros (MGC/MNQ): the app already treats them as the same scale
# (VWAP auto-fetch sources GC=F/NQ=F), and traders often export the deeper-volume
# underlying as a proxy for the thin micro feed.
SYMBOL_ALIASES = {"MGC": "MGC", "GC": "MGC", "MNQ": "MNQ", "NQ": "MNQ"}
# Longest-first alternation + letter boundaries so "MGC1!" resolves to MGC (never
# also "GC"), "GC1!" resolves to MGC, and a 2-letter token never matches inside an
# unrelated word (e.g. "BIGCAP" / "INQUIRY").
_SYMBOL_TOKEN_RE = re.compile(
    r"(?<![A-Z])(" + "|".join(sorted(SYMBOL_ALIASES, key=len, reverse=True)) + r")(?![A-Z])")

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
                  "RANGE_EXPANSION_BREAKOUT"]

# Strategies that must never trade in the backtest right now. EXHAUSTION_FADE is a
# counter-trend fade that audited as the weakest performer — disabled by request.
# The detector + STRATEGY_DEFS entry are kept so it can be re-enabled by deleting
# it from this set (no other change needed).
DISABLED_STRATEGIES = {"EXHAUSTION_FADE"}

# ── Research no-trade filters (applied in simulate_strategy, all causal) ──────
# Default max trades per ET session-day bucket (per strategy). None/0 disables.
MAX_TRADES_PER_SESSION = 3
# Minimum reward:risk required on the FIRST (expectancy-critical) target. The 50%
# partial exits at TP1 and the runner's stop jumps to breakeven, so TP1 is the R
# that actually drives expectancy. None/0 disables.
MIN_TARGET_R = 1.5
# High-impact-news blackout windows in fractional ET hours [lo, hi). There is no
# economic-calendar feed available, so this is a configurable time-of-day blackout
# around the 08:30 ET macro release cluster rather than a true news filter.
NEWS_BLACKOUTS_ET = ((8 + 28 / 60.0, 8 + 32 / 60.0),)

# ── Optimization-study sweep dimensions (research-only; see run_optimization) ──
OPT_SCORE_THRESHOLDS = [65, 70, 75, 80, 85]
OPT_SESSIONS = ["All", "Asia", "London", "New York",
                "0800-1100", "2000-2300", "0500-0800"]
OPT_SESSION_LABELS = {
    "All": "All sessions", "Asia": "Asia", "London": "London",
    "New York": "New York", "0800-1100": "08:00–11:00 ET",
    "2000-2300": "20:00–23:00 ET", "0500-0800": "05:00–08:00 ET"}
OPT_HOUR_WINDOWS = {"0800-1100": (8.0, 11.0), "2000-2300": (20.0, 23.0),
                    "0500-0800": (5.0, 8.0)}
OPT_TRENDS = ["none", "5m", "15m", "5m+15m"]
OPT_TREND_LABELS = {"none": "No trend filter", "5m": "5m trend agree",
                    "15m": "15m trend agree", "5m+15m": "5m+15m trend agree"}
OPT_VOLUMES = ["none", "1.25", "1.5"]
OPT_VOLUME_LABELS = {"none": "No volume filter", "1.25": "RVOL ≥ 1.25×",
                     "1.5": "RVOL ≥ 1.5×"}
OPT_GRADES = ["All", "B", "A", "A+"]
OPT_GRADE_FLOOR = {"All": 0, "B": 50, "A": 70, "A+": 85}
OPT_GRADE_LABELS = {"All": "All grades", "B": "B or better",
                    "A": "A or better", "A+": "A+ only"}
OPT_MANAGEMENTS = ["target_1r", "target_1_5r", "target_2r",
                   "partial_1r_runner_2r", "be_after_1r"]
OPT_MGMT_LABELS = {
    "target_1r": "1.0R target", "target_1_5r": "1.5R target",
    "target_2r": "2.0R target",
    "partial_1r_runner_2r": "Partial @1R, runner @2R",
    "be_after_1r": "BE after 1R (2R target)"}
OPT_MIN_TRADES = 10        # min trades for a combo to enter best/worst rankings
OPT_TABLE_ROWS = 250       # ranked combinations retained for the table + CSV

# Live Edge Score grounding for the BT-score reconstruction (mirror app.py
# EDGE_COMPONENTS / RVOL_CONFIRM_THRESHOLD / SESSION_WINDOWS as of this build so
# the swept score thresholds line up 1:1 with the live gate): BOS +20, CHOCH +20,
# VWAP +15, Sweep +15, Volume +15, CVD +15, Session +10 = max 110.
BT_EDGE_SCORE_MAX = 110
BT_RVOL_CONFIRM_THRESHOLD = 1.5     # RVOL proxy for the live volume-spike confirmation
BT_SESSION_BONUS_WINDOWS = ((5.0, 8.0), (8.0, 11.0), (20.0, 23.0))  # live preferred ET windows


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


def _detect_symbol(filename, med_close):
    """Best-effort instrument detection for CSV uploads. Returns (symbol, reason)
    on success or (None, why_failed). Filename hints win — TradingView exports
    embed the ticker (e.g. MGC1!, MNQ1!, or the full-size GC1!/NQ1! proxy) — and
    the price scale is a fallback used only when it points to exactly one instrument
    (the configured ranges overlap)."""
    fn = str(filename or "").upper()
    fn_hits = sorted({SYMBOL_ALIASES[t] for t in _SYMBOL_TOKEN_RE.findall(fn)})
    if len(fn_hits) == 1:
        return fn_hits[0], f"filename '{filename}'"
    if len(fn_hits) > 1:
        return None, f"filename names more than one instrument ({', '.join(fn_hits)})"
    rng_hits = [s for s, spec in BT_SPECS.items()
                if spec["price_lo"] <= med_close <= spec["price_hi"]]
    if len(rng_hits) == 1:
        return rng_hits[0], f"price scale (~{med_close:.0f})"
    if len(rng_hits) > 1:
        return None, (f"price ~{med_close:.0f} fits both MGC and MNQ — "
                      f"name the file with the ticker or pick it manually")
    return None, f"median price {med_close:.0f} matches no known instrument scale"


def parse_candles_csv(raw_text, symbol, timeframe, source_tz="America/New_York",
                      filename=None):
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
           "first_ts": None, "last_ts": None, "sha256": None,
           "symbol": None, "timeframe": None,
           "detected_symbol": False, "detected_timeframe": False}

    # symbol/timeframe may be a concrete value OR "auto"/None to request detection.
    auto_symbol = symbol is None or str(symbol).strip().lower() in ("", "auto")
    auto_tf = timeframe is None or str(timeframe).strip().lower() in ("", "auto")
    if auto_symbol:
        symbol = None
    else:
        symbol = str(symbol).strip().upper()
        if symbol not in VALID_SYMBOLS:
            out["error"] = f"Unsupported symbol '{symbol}' (expected MGC or MNQ)."
            return out
    if auto_tf:
        timeframe = None
    else:
        timeframe = str(timeframe).strip().lower()
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

    # ── Instrument resolution + price-scale sanity ──
    med_close = statistics.median(c["close"] for c in candles)
    if auto_symbol:
        symbol, why = _detect_symbol(filename, med_close)
        if symbol is None:
            out["error"] = (f"Could not auto-detect the instrument: {why}. "
                            f"Please choose MGC or MNQ manually.")
            return out
        out["detected_symbol"] = True
    spec = BT_SPECS[symbol]
    if not (spec["price_lo"] <= med_close <= spec["price_hi"]):
        out["error"] = (f"Median price {med_close:.1f} is outside the plausible {symbol} "
                        f"range ({spec['price_lo']:.0f}–{spec['price_hi']:.0f}). "
                        f"Is this the right instrument/scale?")
        return out
    out["symbol"] = symbol

    # ── Timeframe inference from the median delta ──
    deltas = [(candles[i]["ts"] - candles[i - 1]["ts"]).total_seconds()
              for i in range(1, len(candles))]
    if deltas:
        med_delta = statistics.median(deltas)
        inferred = min(TIMEFRAME_SECONDS, key=lambda k: abs(TIMEFRAME_SECONDS[k] - med_delta))
        out["inferred_timeframe"] = inferred
        if auto_tf:
            timeframe = inferred
            out["detected_timeframe"] = True
        elif inferred != timeframe:
            out["warnings"].append(
                f"Declared timeframe {timeframe} but the data looks like {inferred} "
                f"(median spacing {med_delta:.0f}s). Using declared {timeframe}.")
        expected = TIMEFRAME_SECONDS[timeframe]
        out["gap_count"] = sum(1 for d in deltas if d > expected * 2.0)
    elif auto_tf:
        # Single candle — cannot infer spacing; fall back to a safe default.
        timeframe = "5m"
        out["inferred_timeframe"] = timeframe
        out["detected_timeframe"] = True

    out["candles"] = candles
    out["row_count"] = len(candles)
    out["first_ts"] = candles[0]["ts"]
    out["last_ts"] = candles[-1]["ts"]
    out["timeframe"] = timeframe
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
                      session_filter=None,
                      max_trades_per_session=MAX_TRADES_PER_SESSION,
                      min_target_r=MIN_TARGET_R,
                      block_extreme_volatility=True,
                      news_blackouts_et=NEWS_BLACKOUTS_ET):
    """Replay one strategy over the snapshots. Entry on the bar AFTER a
    close-confirmed signal (next-bar open ± slippage). Management: 50% off at TP1
    + stop→breakeven, runner to TP3; worst-case same-bar fill (stop/BE first).
    Returns a list of closed-trade dicts.

    Research no-trade filters (all CAUSAL — evaluated on the signal bar):
      • max_trades_per_session: cap entries per ET session-day bucket (None/0 off).
      • min_target_r: reject if the first target's RR (tp1/risk) is below this.
      • block_extreme_volatility: reject when atr_ratio >= the mode's vol_high_block.
      • news_blackouts_et: reject when the signal bar's ET hour is in a blackout.
    """
    trades = []
    n = len(snaps)
    tick = spec["tick_size"]
    pv = spec["point_value"]
    slip = slippage_ticks * tick
    mk = BT_MODES.get(mode, BT_MODES["SCALP"])
    detector = DETECTORS[strat_key]
    session_counts = {}
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

        # ── No-trade filters (causal: state as of the signal bar's close) ──
        et_sig = s["et"]
        h_et = et_sig.hour + et_sig.minute / 60.0
        # High-impact-news blackout (configurable ET windows; no live econ feed).
        if news_blackouts_et and any(lo <= h_et < hi for lo, hi in news_blackouts_et):
            i += 1
            continue
        # Extreme volatility: skip when ATR ratio is at/above the block threshold.
        if (block_extreme_volatility and s["atr_ratio"] is not None
                and s["atr_ratio"] >= mk["vol_high_block"]):
            i += 1
            continue
        # Max trades per ET session-day bucket (per strategy).
        sess_key = (s["session"] or "Off-hours",
                    _session_for_et(et_sig)[1] or et_sig.date().isoformat())
        if (max_trades_per_session
                and session_counts.get(sess_key, 0) >= max_trades_per_session):
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
        # Reject trades where the stop is wider than the first target (stop > target).
        if risk > tp1d:
            i += 1
            continue
        # Minimum reward:risk on the first (expectancy-critical) target.
        if min_target_r and (tp1d / risk) < min_target_r:
            i += 1
            continue
        # SWING legacy runner-RR gate (unchanged).
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
        session_counts[sess_key] = session_counts.get(sess_key, 0) + 1
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
                "avg_winner_r": None, "avg_loser_r": None, "tradable": False,
                "loss_reasons": [],
                "max_drawdown_r": 0.0, "best_hour": None, "best_regime": None,
                "avg_hold_minutes": None, "equity_curve": []}
    rs = [t["r_multiple"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (None if gross_profit == 0 else float("inf"))
    avg_r_val = sum(rs) / len(rs)
    avg_winner_r = (sum(wins) / len(wins)) if wins else None
    avg_loser_r = (sum(losses) / len(losses)) if losses else None
    # "Tradable" gate (research): profit factor must be a real number > 1 (or all
    # winners → ∞) AND average R must be positive. Compare the RAW pf, never the
    # display string.
    pf_ok = (pf == float("inf")) or (isinstance(pf, (int, float)) and pf > 1.0)
    tradable = bool(avg_r_val > 0 and pf_ok)
    # Why each losing trade failed — count exit reasons across losing/scratch trades.
    loss_reason_counts = {}
    for t in trades:
        if t["r_multiple"] <= 0:
            loss_reason_counts[t["exit_reason"]] = loss_reason_counts.get(t["exit_reason"], 0) + 1
    loss_reasons = [{"reason": k, "count": v} for k, v in
                    sorted(loss_reason_counts.items(), key=lambda kv: -kv[1])]
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
        "avg_r": round(avg_r_val, 3),
        "avg_winner_r": (round(avg_winner_r, 3) if avg_winner_r is not None else None),
        "avg_loser_r": (round(avg_loser_r, 3) if avg_loser_r is not None else None),
        "tradable": tradable,
        "loss_reasons": loss_reasons,
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
    # Research no-trade filters (defaults on; None/0 disables the numeric ones).
    max_tps = params.get("max_trades_per_session", MAX_TRADES_PER_SESSION)
    min_tr = params.get("min_target_r", MIN_TARGET_R)
    block_vol = params.get("block_extreme_volatility", True)
    news_bl = params.get("news_blackouts_et", NEWS_BLACKOUTS_ET)
    want = params.get("strategies") or STRATEGY_ORDER
    # Disabled strategies never trade, even when explicitly requested.
    want = [s for s in want if s in DETECTORS and s not in DISABLED_STRATEGIES]

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
                                   session_filter=session_filter,
                                   max_trades_per_session=max_tps,
                                   min_target_r=min_tr,
                                   block_extreme_volatility=block_vol,
                                   news_blackouts_et=news_bl)
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
        "filters": {
            "max_trades_per_session": max_tps,
            "min_target_r": min_tr,
            "block_extreme_volatility": bool(block_vol),
            "news_blackouts_et": [list(w) for w in news_bl] if news_bl else [],
            "disabled_strategies": sorted(DISABLED_STRATEGIES),
        },
        "strategies": {k: {kk: vv for kk, vv in per_strategy[k].items() if kk != "trades"}
                       for k in want},
        "ranking": ranking_keys,
        "trades": all_trades,
        "combined_equity_curve": combined_curve,
        "total_trades": len(all_trades),
        "signal_agreement": _signal_agreement(snaps, params.get("live_signals")),
    }


# ════════════════════════════════════════════════════════════════════════════
# OPTIMIZATION STUDY (research-only) — parameter / filter sweep per strategy.
#
# Design (architect-ruled): only the dimensions that change EXECUTION prices are
# simulated — strategy × trade-management (~24 sims). Every candidate trade is
# tagged with its causal decision-time context (BT edge score, grade, named
# session, ET hour, regime, RVOL, 5m/15m higher-timeframe trend agreement). The
# remaining dimensions (score threshold, grade, session/hour window, trend
# alignment, volume) are then a pure POST-HOC SUBSET of that tagged list — they
# change which trades are INCLUDED, never the entry/exit prices. This collapses a
# ~80k-combination brute force into a handful of simulations + fast subset
# scoring, while staying byte-isolated from the live money path.
#
# Documented approximations:
#   • The one-position-per-strategy rule is applied at candidate generation
#     (unfiltered). A post-hoc filter therefore DROPS a trade rather than freeing
#     the engine to take a later overlapping one — mildly conservative on counts.
#   • "BT score" is a causal RECONSTRUCTION of the live additive Edge Score using
#     the SAME component weights as live EDGE_COMPONENTS (BOS+20/CHOCH+20/VWAP+15/
#     Sweep+15/Volume+15/CVD+15/Session+10, max 110), NOT the live alert-driven
#     score. Volume confirmation is proxied by RVOL (the live volume-spike feed is
#     not replayable). Grade is derived from it (A+≥85 / A≥70 / B≥50).
#   • The sweep is UNCAPPED (no max-trades-per-session) and excludes the
#     news/extreme-volatility skips so filter effects are isolated.
# ════════════════════════════════════════════════════════════════════════════
def _grade_from_score(sc):
    if sc >= 85:
        return "A+"
    if sc >= 70:
        return "A"
    if sc >= 50:
        return "B"
    return "WAIT"


def _bt_edge_score(s, direction):
    """Causal reconstruction of the live additive Edge Score (max 110) from the
    signal-bar snapshot, using the SAME component weights as the live
    EDGE_COMPONENTS so the swept score thresholds line up 1:1 with the live gate:
    BOS +20, CHOCH +20, VWAP +15, Sweep +15, Volume +15, CVD +15, Session +10.
    Only fields replayable historically are used: volume confirmation is proxied
    by RVOL >= BT_RVOL_CONFIRM_THRESHOLD (the live volume-spike feed cannot be
    replayed), and the session bonus fires inside the live preferred ET windows.
    Zone proximity, the confirmation candle and the retired RVOL +/- modifier do
    NOT score — they no longer feed the live Edge Score."""
    long = (direction == "Long")
    score = 0
    if (long and s["bos_long"]) or (not long and s["bos_short"]):
        score += 20
    if (long and s["choch_long"]) or (not long and s["choch_short"]):
        score += 20
    if s["vwap"] is not None and (
        (long and s["close"] > s["vwap"]) or (not long and s["close"] < s["vwap"])
    ):
        score += 15
    if (long and s["sweep_bull"]) or (not long and s["sweep_bear"]):
        score += 15
    rv = s["rvol"]
    if rv is not None and rv >= BT_RVOL_CONFIRM_THRESHOLD:
        score += 15
    cvd = s["cvd_state"]
    if (long and cvd == "bullish") or (not long and cvd == "bearish"):
        score += 15
    et = s["et"]
    hour_f = et.hour + et.minute / 60.0
    if any(lo <= hour_f < hi for lo, hi in BT_SESSION_BONUS_WINDOWS):
        score += 10
    return max(0, min(BT_EDGE_SCORE_MAX, score))


def _htf_trend_array(candles, htf_sec):
    """Per-base-bar higher-timeframe trend sign (+1/-1/0) available AS OF that
    bar's close, using only COMPLETED htf buckets (no look-ahead). Trend = sign
    of EMA(3) − EMA(8) over completed-bucket closes."""
    n = len(candles)
    out = [0] * n
    af, asw = 2.0 / (3 + 1), 2.0 / (8 + 1)
    ema_f = ema_s = None
    cur_bucket = None
    cur_close = None
    avail = 0
    for i, c in enumerate(candles):
        b = math.floor(c["ts"].timestamp() / htf_sec)
        if cur_bucket is None:
            cur_bucket, cur_close = b, c["close"]
        elif b != cur_bucket:
            close = cur_close
            ema_f = close if ema_f is None else af * close + (1 - af) * ema_f
            ema_s = close if ema_s is None else asw * close + (1 - asw) * ema_s
            avail = 1 if ema_f > ema_s else (-1 if ema_f < ema_s else 0)
            cur_bucket, cur_close = b, c["close"]
        else:
            cur_close = c["close"]
        out[i] = avail
    return out


def _walk_managed(candles, entry_bar, direction, entry, stop, risk, slip, mgmt):
    """Resolve a trade under an R-based management model. Returns
    (exit_price, exit_bar, exit_reason, r_gross). Worst-case same-bar discipline:
    the ACTIVE stop is always checked before the target / BE arming."""
    n = len(candles)
    long = (direction == "Long")
    R = risk

    def tp(rr):
        return entry + rr * R if long else entry - rr * R

    if mgmt in ("target_1r", "target_1_5r", "target_2r"):
        rr = {"target_1r": 1.0, "target_1_5r": 1.5, "target_2r": 2.0}[mgmt]
        tgt = tp(rr)
        j = entry_bar
        while j < n:
            hi, lo = candles[j]["high"], candles[j]["low"]
            if long:
                if lo <= stop:
                    px = stop - slip
                    return (px, j, "Stop loss (-1R)", (px - entry) / R)
                if hi >= tgt:
                    return (tgt, j, f"Target {rr:g}R", (tgt - entry) / R)
            else:
                if hi >= stop:
                    px = stop + slip
                    return (px, j, "Stop loss (-1R)", (entry - px) / R)
                if lo <= tgt:
                    return (tgt, j, f"Target {rr:g}R", (entry - tgt) / R)
            j += 1
        last = candles[-1]["close"]
        r = (last - entry) / R if long else (entry - last) / R
        return (last, n - 1, "Open at end of data", r)

    if mgmt == "be_after_1r":
        tgt, one = tp(2.0), tp(1.0)
        cur_stop = stop
        armed = False
        j = entry_bar
        while j < n:
            hi, lo = candles[j]["high"], candles[j]["low"]
            if long:
                if lo <= cur_stop:
                    px = cur_stop - slip
                    return (px, j, "Breakeven stop (+1R armed)" if armed
                            else "Stop loss (-1R)", (px - entry) / R)
                if not armed and hi >= one:
                    armed = True
                    cur_stop = entry
                    if lo <= entry:
                        px = entry - slip
                        return (px, j, "Breakeven stop (+1R armed)", (px - entry) / R)
                if hi >= tgt:
                    return (tgt, j, "Target 2R (BE after 1R)", (tgt - entry) / R)
            else:
                if hi >= cur_stop:
                    px = cur_stop + slip
                    return (px, j, "Breakeven stop (+1R armed)" if armed
                            else "Stop loss (-1R)", (entry - px) / R)
                if not armed and lo <= one:
                    armed = True
                    cur_stop = entry
                    if hi >= entry:
                        px = entry + slip
                        return (px, j, "Breakeven stop (+1R armed)", (entry - px) / R)
                if lo <= tgt:
                    return (tgt, j, "Target 2R (BE after 1R)", (entry - tgt) / R)
            j += 1
        last = candles[-1]["close"]
        r = (last - entry) / R if long else (entry - last) / R
        return (last, n - 1, "Open at end of data", r)

    # partial_1r_runner_2r — 50% off at +1R, runner stop→BE, runner target +2R.
    one, tgt = tp(1.0), tp(2.0)
    cur_stop = stop
    half1 = False
    j = entry_bar
    while j < n:
        hi, lo = candles[j]["high"], candles[j]["low"]
        if long:
            if not half1:
                if lo <= cur_stop:
                    px = cur_stop - slip
                    return (px, j, "Stop loss (-1R)", (px - entry) / R)
                if hi >= one:
                    half1 = True
                    cur_stop = entry
                    if lo <= entry:
                        px = entry - slip
                        return (px, j, "TP1 +1R, runner BE",
                                0.5 * 1.0 + 0.5 * ((px - entry) / R))
            else:
                if lo <= cur_stop:
                    px = entry - slip
                    return (px, j, "TP1 +1R, runner BE",
                            0.5 * 1.0 + 0.5 * ((px - entry) / R))
                if hi >= tgt:
                    return (tgt, j, "TP1 +1R, runner +2R", 0.5 * 1.0 + 0.5 * 2.0)
        else:
            if not half1:
                if hi >= cur_stop:
                    px = cur_stop + slip
                    return (px, j, "Stop loss (-1R)", (entry - px) / R)
                if lo <= one:
                    half1 = True
                    cur_stop = entry
                    if hi >= entry:
                        px = entry + slip
                        return (px, j, "TP1 +1R, runner BE",
                                0.5 * 1.0 + 0.5 * ((entry - px) / R))
            else:
                if hi >= cur_stop:
                    px = entry + slip
                    return (px, j, "TP1 +1R, runner BE",
                            0.5 * 1.0 + 0.5 * ((entry - px) / R))
                if lo <= tgt:
                    return (tgt, j, "TP1 +1R, runner +2R", 0.5 * 1.0 + 0.5 * 2.0)
        j += 1
    last = candles[-1]["close"]
    rem = (last - entry) / R if long else (entry - last) / R
    if not half1:
        return (last, n - 1, "Open at end of data", rem)
    return (last, n - 1, "TP1 +1R, runner closed at end", 0.5 * 1.0 + 0.5 * rem)


def _opt_candidates(snaps, candles, strat_key, spec, mode, mgmt,
                    slippage_ticks, commission_per_side, tf5, tf15):
    """All candidate trades for one strategy under one management model — detector
    signal + conflict guard + valid stop plan ONLY (no quality/session/trend/
    volume/news/max-trades gates). Each trade carries its causal decision-time
    tags for post-hoc subsetting. Pre-sorted by exit time for incremental DD."""
    tick = spec["tick_size"]
    pv = spec["point_value"]
    slip = slippage_ticks * tick
    detector = DETECTORS[strat_key]
    n = len(snaps)
    out = []
    i = 0
    while i < n - 1:
        s = snaps[i]
        sig = detector(s)
        if not sig:
            i += 1
            continue
        direction = sig[0]
        if direction == "Long" and s["choch_short"]:
            i += 1
            continue
        if direction == "Short" and s["choch_long"]:
            i += 1
            continue
        entry_bar = i + 1
        raw = candles[entry_bar]["open"]
        entry = raw + slip if direction == "Long" else raw - slip
        plan = bt_stop_plan(direction, entry, s["demand_zone"], s["supply_zone"],
                            spec, s["atr"], mode, s["regime"])
        if plan is None:
            i += 1
            continue
        stop, risk = plan["stop"], plan["risk_points"]
        ex_px, ex_bar, ex_reason, r_gross = _walk_managed(
            candles, entry_bar, direction, entry, stop, risk, slip, mgmt)
        comm_r = (2.0 * commission_per_side) / (risk * pv) if risk > 0 else 0.0
        et = s["et"]
        out.append({
            "strategy": strat_key, "direction": direction,
            "r": r_gross - comm_r, "exit_reason": ex_reason,
            "entry_ts": candles[entry_bar]["ts"].isoformat(),
            "exit_ts": candles[ex_bar]["ts"].isoformat(),
            "bt_score": _bt_edge_score(s, direction),
            "session": s["session"] or "Off-hours",
            "sig_hour_f": et.hour + et.minute / 60.0, "sig_hour": et.hour,
            "regime": s["regime"], "rvol": s["rvol"],
            "htf5_agree": (tf5[i] != 0 and (direction == "Long") == (tf5[i] > 0)),
            "htf15_agree": (tf15[i] != 0 and (direction == "Long") == (tf15[i] > 0)),
        })
        i = max(ex_bar, entry_bar) + 1
    for t in out:
        t["grade"] = _grade_from_score(t["bt_score"])
    out.sort(key=lambda t: t["exit_ts"])
    return out


def _opt_session_ok(t, opt):
    if opt == "All":
        return True
    if opt in OPT_HOUR_WINDOWS:
        lo, hi = OPT_HOUR_WINDOWS[opt]
        return lo <= t["sig_hour_f"] < hi
    return t["session"] == opt


def _opt_trend_ok(t, opt):
    if opt == "none":
        return True
    if opt == "5m":
        return t["htf5_agree"]
    if opt == "15m":
        return t["htf15_agree"]
    return t["htf5_agree"] and t["htf15_agree"]


def _opt_volume_ok(t, opt):
    if opt == "none":
        return True
    rv = t["rvol"]
    if rv is None:
        return False
    return rv >= (1.25 if opt == "1.25" else 1.5)


def _opt_metrics(trades):
    """Lightweight subset metrics. `trades` MUST be pre-sorted by exit time so the
    running drawdown is chronologically correct."""
    k = len(trades)
    if k == 0:
        return None
    rs = [t["r"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gp, gl = sum(wins), abs(sum(losses))
    pf = (gp / gl) if gl > 0 else (math.inf if gp > 0 else 0.0)
    net_r = sum(rs)
    peak = cum = mdd = 0.0
    for r in rs:
        cum += r
        if cum > peak:
            peak = cum
        if peak - cum > mdd:
            mdd = peak - cum
    by_hour, by_reg = {}, {}
    for t in trades:
        by_hour.setdefault(t["sig_hour"], []).append(t["r"])
        by_reg.setdefault(t["regime"], []).append(t["r"])
    bh = max(by_hour, key=lambda h: sum(by_hour[h])) if by_hour else None
    br = max(by_reg, key=lambda g: sum(by_reg[g])) if by_reg else None
    pf_ok = (pf == math.inf) or (pf > 1.0)
    return {
        "trades": k,
        "win_rate": round(len(wins) / k * 100.0, 1),
        "pf": (None if pf == 0.0 else ("inf" if pf == math.inf else round(pf, 2))),
        "pf_num": (1e9 if pf == math.inf else round(pf, 4)),
        "net_r": round(net_r, 2),
        "avg_r": round(net_r / k, 3),
        "avg_win_r": (round(sum(wins) / len(wins), 3) if wins else None),
        "avg_loss_r": (round(sum(losses) / len(losses), 3) if losses else None),
        "max_dd_r": round(mdd, 2),
        "best_hour": bh,
        "best_hour_label": (f"{bh:02d}:00–{(bh + 1) % 24:02d}:00 ET"
                            if bh is not None else None),
        "best_regime": br,
        "tradable": bool(net_r / k > 0 and pf_ok),
    }


def _opt_rank(m):
    """Research ranking: total Net R, then Profit Factor, then smaller drawdown."""
    return (m["net_r"], m["pf_num"], -m["max_dd_r"])


def run_optimization(candles, params):
    """Sweep filter / management combinations per strategy and rank them by
    Profit Factor / Net R / Max Drawdown. Pure + read-only — never touches live
    globals, Discord, the broker path, or full_analysis."""
    symbol = params.get("symbol", "MGC")
    mode = (params.get("mode") or "SCALP").upper()
    if mode not in BT_MODES:
        mode = "SCALP"
    spec = BT_SPECS.get(symbol, BT_SPECS["MGC"])
    slippage = float(params.get("slippage_ticks", 1.0))
    commission = float(params.get("commission_per_side", 0.62))
    min_trades = int(params.get("min_trades", OPT_MIN_TRADES))
    strategies = [s for s in (params.get("strategies") or STRATEGY_ORDER)
                  if s in DETECTORS and s not in DISABLED_STRATEGIES]
    start_ts, end_ts = params.get("start_ts"), params.get("end_ts")
    if start_ts or end_ts:
        candles = [c for c in candles
                   if (start_ts is None or c["ts"] >= start_ts)
                   and (end_ts is None or c["ts"] <= end_ts)]
    need = VOL_MIN_BARS + PIVOT_LEFT + PIVOT_RIGHT + 2
    if len(candles) < need:
        return {"ok": False,
                "error": f"Not enough candles ({len(candles)}) — need ≥ {need}."}
    snaps = compute_indicators(candles, mode=mode)
    tf5 = _htf_trend_array(candles, 300)
    tf15 = _htf_trend_array(candles, 900)

    rows = []
    regime_best = {}
    REGIMES = ("TRENDING", "RANGING", "VOLATILE", "BALANCED")
    for strat in strategies:
        for mgmt in OPT_MANAGEMENTS:
            cands = _opt_candidates(snaps, candles, strat, spec, mode, mgmt,
                                    slippage, commission, tf5, tf15)
            if not cands:
                continue
            # Best-by-regime: isolate regime (session=All / trend=none / vol=none).
            for reg in REGIMES:
                reg_tr = [t for t in cands if t["regime"] == reg]
                if len(reg_tr) < min_trades:
                    continue
                for thr in OPT_SCORE_THRESHOLDS:
                    m = _opt_metrics([t for t in reg_tr if t["bt_score"] >= thr])
                    if not m or m["trades"] < min_trades:
                        continue
                    row = {"strategy": strat, "mgmt": mgmt, "score": thr,
                           "regime": reg, **m}
                    cur = regime_best.get(reg)
                    if cur is None or _opt_rank(m) > _opt_rank(cur):
                        regime_best[reg] = row
            # Main sweep: session × trend × volume × score × grade.
            for sess in OPT_SESSIONS:
                s_sub = [t for t in cands if _opt_session_ok(t, sess)]
                if not s_sub:
                    continue
                for tr in OPT_TRENDS:
                    t_sub = [t for t in s_sub if _opt_trend_ok(t, tr)]
                    if not t_sub:
                        continue
                    for vol in OPT_VOLUMES:
                        v_sub = [t for t in t_sub if _opt_volume_ok(t, vol)]
                        if not v_sub:
                            continue
                        eff_cache = {}
                        for thr in OPT_SCORE_THRESHOLDS:
                            for gr in OPT_GRADES:
                                eff = max(thr, OPT_GRADE_FLOOR[gr])
                                if eff not in eff_cache:
                                    eff_cache[eff] = _opt_metrics(
                                        [t for t in v_sub if t["bt_score"] >= eff])
                                m = eff_cache[eff]
                                if not m:
                                    continue
                                rows.append({
                                    "strategy": strat, "mgmt": mgmt,
                                    "session": sess, "trend": tr, "volume": vol,
                                    "score": thr, "grade": gr, **m})

    eligible = [r for r in rows if r["trades"] >= min_trades]
    ranked = sorted(rows, key=_opt_rank, reverse=True)
    ranked_elig = sorted(eligible, key=_opt_rank, reverse=True)
    best_overall = ranked_elig[0] if ranked_elig else (ranked[0] if ranked else None)
    best_by_strategy = {}
    for r in ranked_elig:
        best_by_strategy.setdefault(r["strategy"], r)
    best_by_session = {}
    for r in ranked_elig:
        best_by_session.setdefault(r["session"], r)
    worst = sorted(eligible, key=_opt_rank)[0] if eligible else None

    return {
        "ok": True, "kind": "optimization",
        "symbol": symbol, "mode": mode, "timeframe": params.get("timeframe"),
        "bars": len(candles),
        "first_ts": candles[0]["ts"].isoformat(),
        "last_ts": candles[-1]["ts"].isoformat(),
        "slippage_ticks": slippage, "commission_per_side": commission,
        "min_trades": min_trades, "strategies": strategies,
        "total_combos": len(rows), "eligible_combos": len(eligible),
        "labels": {
            "strategy": {k: STRATEGY_DEFS[k]["label"] for k in STRATEGY_DEFS},
            "mgmt": OPT_MGMT_LABELS, "session": OPT_SESSION_LABELS,
            "trend": OPT_TREND_LABELS, "volume": OPT_VOLUME_LABELS,
            "grade": OPT_GRADE_LABELS,
        },
        "table": ranked[:OPT_TABLE_ROWS],
        "best_overall": best_overall,
        "best_by_strategy": best_by_strategy,
        "best_by_session": best_by_session,
        "best_by_regime": regime_best,
        "worst_to_avoid": worst,
        "notes": [
            "BT score is a causal reconstruction of the live Edge Score using the "
            "same component weights as the live gate (BOS 20, CHOCH 20, VWAP 15, "
            "Sweep 15, Volume 15, CVD 15, Session 10; max 110), not the live "
            "alert-driven score. Volume confirmation is proxied by RVOL because the "
            "live volume-spike feed cannot be replayed historically.",
            "Grade is derived from BT score (A+ ≥85, A ≥70, B ≥50); the effective "
            "quality filter is the stricter of the score threshold and grade floor.",
            "Filters are post-hoc subsets of simulated trades; the "
            "one-position-per-strategy rule is applied before filtering, so a "
            "filter drops a trade rather than promoting a later overlapping one "
            "(mildly conservative on trade counts).",
            "Sweep is uncapped (no max-trades-per-session) and excludes "
            "news/extreme-volatility skips so filter effects are isolated. "
            "Exhaustion Fade is excluded.",
            f"Combinations with fewer than {min_trades} trades are shown in the "
            "table but excluded from the best/worst rankings.",
        ],
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

    # 2b) Auto-detect symbol (from filename + from price) and timeframe
    csv_text = "\n".join(rows)
    a_fn = parse_candles_csv(csv_text, "auto", "auto", filename="COMEX_MGC1!, 5.csv")
    assert a_fn["ok"] and a_fn["symbol"] == "MGC" and a_fn["timeframe"] == "5m", a_fn
    assert a_fn["detected_symbol"] and a_fn["detected_timeframe"], a_fn
    a_px = parse_candles_csv(csv_text, "auto", "auto")  # no filename → price scale (~2387 → MGC)
    assert a_px["ok"] and a_px["symbol"] == "MGC", a_px
    mnq_rows = ["Date,Time,Open,High,Low,Close,Volume"]
    for c in _synthetic_candles(120, "MNQ"):
        et = c["ts"].astimezone(ET_TZ)
        mnq_rows.append(f"{et.strftime('%Y-%m-%d')},{et.strftime('%H:%M:%S')},"
                        f"{c['open']},{c['high']},{c['low']},{c['close']},{int(c['volume'])}")
    a_mnq = parse_candles_csv("\n".join(mnq_rows), None, None, filename="CME_MNQ1!_15.csv")
    assert a_mnq["ok"] and a_mnq["symbol"] == "MNQ", a_mnq
    # full-size GC/NQ exports are aliases for the micros (one-click still detects)
    a_gc = parse_candles_csv(csv_text, "auto", "auto", filename="COMEX_GC1!, 5.csv")
    assert a_gc["ok"] and a_gc["symbol"] == "MGC", a_gc
    nq_sym, _nqw = _detect_symbol("CME_NQ1!.csv", 18000.0)
    assert nq_sym == "MNQ", (nq_sym, _nqw)
    # boundary guard: GC/NQ inside an unrelated word must NOT be read as a ticker
    bg_sym, bg_why = _detect_symbol("BIGCAP_INQUIRY.csv", 2387.0)
    assert bg_sym == "MGC" and "price scale" in bg_why, (bg_sym, bg_why)
    # filename naming a contradicting instrument must fail the price-scale sanity
    contra = parse_candles_csv(csv_text, "auto", "auto", filename="some_MNQ_export.csv")
    assert not contra["ok"] and "range" in (contra["error"] or ""), contra
    print(f"Auto-detect OK: filename→{a_fn['symbol']}/{a_fn['timeframe']}, "
          f"price→{a_px['symbol']}, mnq→{a_mnq['symbol']}, GC→{a_gc['symbol']}/NQ→{nq_sym}; "
          f"boundary-guarded + contradiction rejected ✓")

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

    # 6) BT score stays in sync with the live Edge Score components (max 110):
    #    BOS 20 + CHOCH 20 + VWAP 15 + Sweep 15 + Volume 15 + CVD 15 + Session 10.
    assert BT_EDGE_SCORE_MAX == 110, BT_EDGE_SCORE_MAX
    assert 20 + 20 + 15 + 15 + 15 + 15 + 10 == BT_EDGE_SCORE_MAX
    _pref_et = snaps[0]["et"].replace(hour=9, minute=0)   # inside 08:00–11:00 ET
    _off_et = snaps[0]["et"].replace(hour=2, minute=0)    # outside every window
    full_long = {"bos_long": True, "bos_short": False,
                 "choch_long": True, "choch_short": False,
                 "vwap": 100.0, "close": 101.0,
                 "sweep_bull": True, "sweep_bear": False,
                 "rvol": 2.0, "cvd_state": "bullish", "et": _pref_et}
    assert _bt_edge_score(full_long, "Long") == 110, _bt_edge_score(full_long, "Long")
    none_long = {"bos_long": False, "bos_short": False,
                 "choch_long": False, "choch_short": False,
                 "vwap": 100.0, "close": 99.0,
                 "sweep_bull": False, "sweep_bear": False,
                 "rvol": 0.5, "cvd_state": "neutral", "et": _off_et}
    assert _bt_edge_score(none_long, "Long") == 0, _bt_edge_score(none_long, "Long")
    # Zone proximity and confirmation candle are RETIRED from live → must not score.
    assert _bt_edge_score({**none_long, "demand_zone": 99.0, "supply_zone": 103.0,
                           "atr": 1.0, "confirm_bull": True, "confirm_bear": False},
                          "Long") == 0
    assert (_grade_from_score(110), _grade_from_score(85), _grade_from_score(70),
            _grade_from_score(50), _grade_from_score(49)) == \
        ("A+", "A+", "A", "B", "WAIT")
    print("BT score in sync with live EDGE_COMPONENTS (max 110); zone/confirmation "
          "excluded; grade bands A+/A/B/WAIT ✓")
    print("ALL SELF-TESTS PASSED ✓")


if __name__ == "__main__":
    _self_test()
