"""
trend_alignment.py — Multi-Timeframe Trend Alignment (Phase 8B.1)
==================================================================

DISPLAY-ONLY.  No imports from app.py.  Never touches gate, scoring,
sizing, execution, learning, or ghost calculations.

Design:
* 1-minute bars (from Databento) are accumulated into 15M and 4H bar
  caches via `ingest_1m_bar()`.
* Trend is derived from the last N *closed* bars using an 8/21 EMA
  comparison — same methodology as the existing SWING HTF layer, but
  sourced entirely from Databento (not Yahoo / TradingView).
* A bar bucket is considered "closed" only when the NEXT bucket's first
  bar arrives.  The currently-forming bar is never included.
* All public functions are FAIL-OPEN: any exception returns a safe
  UNAVAILABLE state dict.

Canonical trend states (per spec):
  BULLISH / BEARISH / NEUTRAL / UNAVAILABLE / STALE

Alignment states (per spec):
  ALIGNED_LONG / ALIGNED_SHORT / CONFLICTING / MIXED / STALE / UNAVAILABLE
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ── Timeframe constants ────────────────────────────────────────────────────────
TF_15M_SEC  = 15 * 60        # 900 s
TF_4H_SEC   = 4  * 60 * 60  # 14 400 s

# ── Trend-state constants ──────────────────────────────────────────────────────
BULLISH     = "BULLISH"
BEARISH     = "BEARISH"
NEUTRAL     = "NEUTRAL"
UNAVAILABLE = "UNAVAILABLE"
STALE       = "STALE"

# ── Alignment constants ────────────────────────────────────────────────────────
ALIGNED_LONG  = "ALIGNED_LONG"
ALIGNED_SHORT = "ALIGNED_SHORT"
CONFLICTING   = "CONFLICTING"
MIXED         = "MIXED"

# ── EMA parameters ────────────────────────────────────────────────────────────
EMA_FAST     = 8
EMA_SLOW     = 21
NEUTRAL_BAND = 0.0003      # 0.03% gap between fast/slow → NEUTRAL

# ── Staleness thresholds ──────────────────────────────────────────────────────
STALE_15M_SEC = 2  * TF_15M_SEC  # 30 min — if last 15M bar is > 30 min old
STALE_4H_SEC  = 2  * TF_4H_SEC   # 8 h  — if last 4H  bar is > 8h old

# ── Minimum closed bars required before declaring a trend ─────────────────────
MIN_BARS_15M = EMA_SLOW     # 21 bars = 5.25 hours
MIN_BARS_4H  = 5            # 5 bars  = 20 hours; lower threshold for display

# ── Max bars to retain ────────────────────────────────────────────────────────
MAX_BARS_15M = 50
MAX_BARS_4H  = 20

# ─────────────────────────────────────────────────────────────────────────────
# Per-instrument state
# ─────────────────────────────────────────────────────────────────────────────

_LOCK = threading.Lock()

# MTF_STATE_BY_INST[instrument] = {
#   "bars_15m": deque of closed 15M bar dicts
#   "bars_4h":  deque of closed 4H bar dicts
#   "partial_15m": {ts_bucket, open, high, low, close, volume} or None
#   "partial_4h":  {ts_bucket, open, high, low, close, volume} or None
#   "trend_15m": BULLISH | BEARISH | NEUTRAL | UNAVAILABLE | STALE
#   "trend_4h":  (same)
#   "strength_15m": STRONG | MODERATE | WEAK | None
#   "strength_4h":  (same)
#   "last_bar_ts_15m": unix epoch float or None
#   "last_bar_ts_4h":  unix epoch float or None
# }
MTF_STATE_BY_INST: Dict[str, Dict[str, Any]] = {}


def _make_empty_state() -> Dict[str, Any]:
    return {
        "bars_15m":       deque(maxlen=MAX_BARS_15M),
        "bars_4h":        deque(maxlen=MAX_BARS_4H),
        "partial_15m":    None,
        "partial_4h":     None,
        "trend_15m":      UNAVAILABLE,
        "trend_4h":       UNAVAILABLE,
        "strength_15m":   None,
        "strength_4h":    None,
        "last_bar_ts_15m": None,
        "last_bar_ts_4h":  None,
    }


def _get_or_create(instrument: str) -> Dict[str, Any]:
    """Return (creating if needed) the per-instrument state dict.
    MUST be called under _LOCK.
    """
    if instrument not in MTF_STATE_BY_INST:
        MTF_STATE_BY_INST[instrument] = _make_empty_state()
    return MTF_STATE_BY_INST[instrument]


# ─────────────────────────────────────────────────────────────────────────────
# EMA helpers
# ─────────────────────────────────────────────────────────────────────────────

def _compute_ema(closes: List[float], period: int) -> Optional[float]:
    """Compute exponential moving average over a list of closes.
    Returns None if fewer values than period.
    """
    if len(closes) < period:
        return None
    alpha = 2.0 / (period + 1.0)
    ema = closes[0]
    for c in closes[1:]:
        ema = ema * (1.0 - alpha) + c * alpha
    return ema


def _trend_and_strength(closes: List[float]) -> tuple[str, Optional[str]]:
    """Return (trend_state, strength) from a list of closed bar closes.

    Uses EMA(8) vs EMA(21) with a neutral band.
    Strength is derived from the absolute EMA gap relative to price.
    Returns (UNAVAILABLE, None) if too few bars.
    """
    if len(closes) < EMA_SLOW:
        return UNAVAILABLE, None

    fast = _compute_ema(closes, EMA_FAST)
    slow = _compute_ema(closes, EMA_SLOW)
    if fast is None or slow is None or slow == 0:
        return UNAVAILABLE, None

    gap_pct = (fast - slow) / slow
    threshold = NEUTRAL_BAND

    if gap_pct > threshold:
        trend = BULLISH
    elif gap_pct < -threshold:
        trend = BEARISH
    else:
        trend = NEUTRAL

    # Strength: |gap_pct| relative to 3× the neutral_band
    abs_gap = abs(gap_pct)
    if abs_gap >= threshold * 4:
        strength: Optional[str] = "STRONG"
    elif abs_gap >= threshold * 2:
        strength = "MODERATE"
    elif trend != NEUTRAL:
        strength = "WEAK"
    else:
        strength = None

    return trend, strength


# ─────────────────────────────────────────────────────────────────────────────
# Bar accumulation helpers
# ─────────────────────────────────────────────────────────────────────────────

def _bucket_ts(bar_ts: float, tf_sec: int) -> int:
    """Align a bar timestamp (unix epoch) to the start of its TF bucket."""
    return (int(bar_ts) // tf_sec) * tf_sec


def _merge_into_partial(partial: Optional[Dict], bar_ts: float, tf_sec: int,
                        bar: Dict[str, Any]) -> Dict[str, Any]:
    """Create or extend a partial bar bucket with the incoming 1m bar."""
    bkt = _bucket_ts(bar_ts, tf_sec)
    if partial is None or partial["ts_bucket"] != bkt:
        return {
            "ts_bucket": bkt,
            "open":      float(bar["open"]),
            "high":      float(bar["high"]),
            "low":       float(bar["low"]),
            "close":     float(bar["close"]),
            "volume":    float(bar.get("volume") or 0),
        }
    # Extend existing bucket
    partial = dict(partial)
    if float(bar["high"]) > partial["high"]:
        partial["high"] = float(bar["high"])
    if float(bar["low"])  < partial["low"]:
        partial["low"]  = float(bar["low"])
    partial["close"]  = float(bar["close"])
    partial["volume"] += float(bar.get("volume") or 0)
    return partial


def _partial_to_closed(partial: Dict, tf_sec: int) -> Dict[str, Any]:
    """Convert a partial dict to a standard closed-bar dict."""
    return {
        "ts":    partial["ts_bucket"],
        "ts_end": partial["ts_bucket"] + tf_sec,
        "open":  partial["open"],
        "high":  partial["high"],
        "low":   partial["low"],
        "close": partial["close"],
        "volume": partial["volume"],
    }


def _recalculate_trends(state: Dict[str, Any]) -> None:
    """Recalculate trend_15m and trend_4h from current bar deques.
    MUST be called under _LOCK.
    """
    now_ts = time.time()

    # 15M trend
    bars_15m = list(state["bars_15m"])
    if not bars_15m:
        state["trend_15m"]   = UNAVAILABLE
        state["strength_15m"] = None
    else:
        last_ts = bars_15m[-1]["ts_end"]
        if now_ts - last_ts > STALE_15M_SEC:
            state["trend_15m"]   = STALE
            state["strength_15m"] = None
        else:
            closes = [b["close"] for b in bars_15m]
            state["trend_15m"], state["strength_15m"] = _trend_and_strength(closes)
        state["last_bar_ts_15m"] = last_ts

    # 4H trend
    bars_4h = list(state["bars_4h"])
    if not bars_4h:
        state["trend_4h"]   = UNAVAILABLE
        state["strength_4h"] = None
    else:
        last_ts = bars_4h[-1]["ts_end"]
        if now_ts - last_ts > STALE_4H_SEC:
            state["trend_4h"]   = STALE
            state["strength_4h"] = None
        else:
            closes = [b["close"] for b in bars_4h]
            # For 4H we use fewer bars, so lower the slow EMA requirement
            if len(closes) < MIN_BARS_4H:
                state["trend_4h"]   = UNAVAILABLE
                state["strength_4h"] = None
            else:
                # Trim EMA params to available bars
                fast_n = min(EMA_FAST, len(closes))
                slow_n = min(EMA_SLOW, len(closes))
                fast_e = _compute_ema(closes, fast_n)
                slow_e = _compute_ema(closes, slow_n)
                if fast_e is None or slow_e is None or slow_e == 0:
                    state["trend_4h"]   = UNAVAILABLE
                    state["strength_4h"] = None
                else:
                    gap_pct = (fast_e - slow_e) / slow_e
                    threshold = NEUTRAL_BAND
                    if gap_pct > threshold:
                        state["trend_4h"] = BULLISH
                    elif gap_pct < -threshold:
                        state["trend_4h"] = BEARISH
                    else:
                        state["trend_4h"] = NEUTRAL
                    abs_gap = abs(gap_pct)
                    if abs_gap >= threshold * 4:
                        state["strength_4h"] = "STRONG"
                    elif abs_gap >= threshold * 2:
                        state["strength_4h"] = "MODERATE"
                    elif state["trend_4h"] != NEUTRAL:
                        state["strength_4h"] = "WEAK"
                    else:
                        state["strength_4h"] = None
        state["last_bar_ts_4h"] = last_ts


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def ingest_1m_bar(instrument: str, bar: Dict[str, Any]) -> None:
    """Called on every completed 1-minute bar from Databento. FAIL-OPEN.

    Updates the 15M and 4H partial bars; closes them when the next
    bucket arrives; recalculates trends.

    This function is the single write path — all other functions are
    read-only.  Never raises.
    """
    try:
        bar_ts = float(bar.get("ts") or bar.get("ts_end") or 0)
        if bar_ts <= 0:
            return

        with _LOCK:
            state = _get_or_create(instrument)

            # ── 15M accumulation ──────────────────────────────────────
            new_bkt_15m = _bucket_ts(bar_ts, TF_15M_SEC)
            cur_bkt_15m = (state["partial_15m"] or {}).get("ts_bucket")

            if cur_bkt_15m is not None and new_bkt_15m > cur_bkt_15m:
                # The incoming 1m bar is in the NEXT 15M bucket.
                # Close the prior partial and push to the deque.
                closed_15m = _partial_to_closed(state["partial_15m"], TF_15M_SEC)
                state["bars_15m"].append(closed_15m)
                state["partial_15m"] = None

                # ── 4H accumulation (feed from closed 15M bars) ───────
                # We feed closed_15m into the 4H partial
                c4_bkt = _bucket_ts(closed_15m["ts"], TF_4H_SEC)
                cur_bkt_4h = (state["partial_4h"] or {}).get("ts_bucket")
                if cur_bkt_4h is not None and c4_bkt > cur_bkt_4h:
                    closed_4h = _partial_to_closed(state["partial_4h"], TF_4H_SEC)
                    state["bars_4h"].append(closed_4h)
                    state["partial_4h"] = None
                state["partial_4h"] = _merge_into_partial(
                    state["partial_4h"], float(closed_15m["ts"]), TF_4H_SEC, closed_15m
                )

                _recalculate_trends(state)

            # Start / extend the new 15M partial with the current 1m bar
            state["partial_15m"] = _merge_into_partial(
                state["partial_15m"], bar_ts, TF_15M_SEC, bar
            )

    except Exception:
        # FAIL-OPEN: never bubble up to the bar-close callback chain
        pass


def seed_from_1m_bars(instrument: str, bars_1m: list) -> int:
    """Bulk-seed from a list of completed 1m bars (e.g. from DATABENTO_BARS_BY_INST).

    Used at boot to give the trend panel an immediate state without waiting
    for live bars.  Bars must be sorted ascending by ts.  Returns the number
    of bars processed.

    All bars are treated as CLOSED (suitable for historical pre-population).
    Clears any prior state for the instrument first.
    FAIL-OPEN: never raises.
    """
    try:
        if not bars_1m:
            return 0

        # Sort ascending
        try:
            sorted_bars = sorted(bars_1m, key=lambda b: float(b.get("ts") or 0))
        except Exception:
            sorted_bars = list(bars_1m)

        with _LOCK:
            # Reset state for a clean seed
            MTF_STATE_BY_INST[instrument] = _make_empty_state()
            state = MTF_STATE_BY_INST[instrument]

        # Process each bar through the same accumulation logic but WITHOUT
        # the lock held (avoids blocking; we own the state reference already
        # since we just replaced it with a fresh dict and no other writer has
        # a reference to it yet).
        #
        # We simulate the bar-close detection: group all bars by 15M bucket,
        # then by 4H bucket, and push them as closed bars directly.

        # ── Aggregate 1m → 15M ────────────────────────────────────────────────
        buckets_15m: Dict[int, Dict] = {}
        for bar in sorted_bars:
            ts = float(bar.get("ts") or 0)
            if ts <= 0:
                continue
            bkt = _bucket_ts(ts, TF_15M_SEC)
            if bkt not in buckets_15m:
                buckets_15m[bkt] = {
                    "ts_bucket": bkt,
                    "open":  float(bar["open"]),
                    "high":  float(bar["high"]),
                    "low":   float(bar["low"]),
                    "close": float(bar["close"]),
                    "volume": float(bar.get("volume") or 0),
                }
            else:
                b = buckets_15m[bkt]
                if float(bar["high"]) > b["high"]: b["high"] = float(bar["high"])
                if float(bar["low"])  < b["low"]:  b["low"]  = float(bar["low"])
                b["close"]  = float(bar["close"])
                b["volume"] += float(bar.get("volume") or 0)

        sorted_15m_bkts = sorted(buckets_15m.items())

        # All 15M buckets from historical data are "closed" (a newer bucket
        # arrived by definition — they're historical bars).  The LAST bucket
        # might be partial (currently forming), so we exclude it.
        if len(sorted_15m_bkts) < 2:
            # Not even two full 15M buckets — nothing to close yet
            with _LOCK:
                pass  # state already reset to UNAVAILABLE
            return len(sorted_bars)

        closed_15m_bkts = sorted_15m_bkts[:-1]   # exclude the last (may be partial)
        partial_15m_bkt = sorted_15m_bkts[-1]     # last bucket → becomes the partial

        # ── Aggregate 15M → 4H ────────────────────────────────────────────────
        buckets_4h: Dict[int, Dict] = {}
        for bkt_ts, bkt_data in closed_15m_bkts:
            c4bkt = _bucket_ts(float(bkt_ts), TF_4H_SEC)
            if c4bkt not in buckets_4h:
                buckets_4h[c4bkt] = {
                    "ts_bucket": c4bkt,
                    "open":  bkt_data["open"],
                    "high":  bkt_data["high"],
                    "low":   bkt_data["low"],
                    "close": bkt_data["close"],
                    "volume": bkt_data["volume"],
                }
            else:
                b = buckets_4h[c4bkt]
                if bkt_data["high"] > b["high"]: b["high"] = bkt_data["high"]
                if bkt_data["low"]  < b["low"]:  b["low"]  = bkt_data["low"]
                b["close"]  = bkt_data["close"]
                b["volume"] += bkt_data["volume"]

        sorted_4h_bkts = sorted(buckets_4h.items())
        closed_4h_bkts = sorted_4h_bkts[:-1] if len(sorted_4h_bkts) > 1 else []
        partial_4h_bkt = sorted_4h_bkts[-1] if sorted_4h_bkts else None

        # Write to state under the lock
        with _LOCK:
            state = MTF_STATE_BY_INST[instrument]
            for _, b in closed_15m_bkts[-MAX_BARS_15M:]:
                state["bars_15m"].append(_partial_to_closed(b, TF_15M_SEC))
            if partial_15m_bkt:
                state["partial_15m"] = dict(partial_15m_bkt[1])
                state["partial_15m"]["ts_bucket"] = partial_15m_bkt[0]
            for _, b in closed_4h_bkts[-MAX_BARS_4H:]:
                state["bars_4h"].append(_partial_to_closed(b, TF_4H_SEC))
            if partial_4h_bkt:
                state["partial_4h"] = dict(partial_4h_bkt[1])
                state["partial_4h"]["ts_bucket"] = partial_4h_bkt[0]
            _recalculate_trends(state)

        return len(sorted_bars)

    except Exception:
        return 0


def get_alignment(trend_4h: str, trend_15m: str) -> str:
    """Deterministic alignment classification from two canonical trend states.

    Inputs must be one of: BULLISH, BEARISH, NEUTRAL, UNAVAILABLE, STALE.
    """
    if UNAVAILABLE in (trend_4h, trend_15m):
        return UNAVAILABLE
    if STALE in (trend_4h, trend_15m):
        return STALE
    if trend_4h == BULLISH and trend_15m == BULLISH:
        return ALIGNED_LONG
    if trend_4h == BEARISH and trend_15m == BEARISH:
        return ALIGNED_SHORT
    if trend_4h in (BULLISH, BEARISH) and trend_15m in (BULLISH, BEARISH):
        # Both decided but opposite
        return CONFLICTING
    # Any combination involving NEUTRAL
    return MIXED


def _ts_to_iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    try:
        return datetime.utcfromtimestamp(ts).replace(
            tzinfo=timezone.utc
        ).isoformat()
    except Exception:
        return None


def _age_seconds(ts: Optional[float], now_ts: float) -> Optional[int]:
    """Return a bounded whole-second age for an epoch timestamp."""
    if ts is None:
        return None
    try:
        return max(0, int(now_ts - float(ts)))
    except (TypeError, ValueError):
        return None


def _display_timeframe(
    *,
    trend: str,
    strength: Optional[str],
    last_ts: Optional[float],
    bar_count: int,
    stale_threshold_sec: int,
    now_ts: float,
) -> Dict[str, Any]:
    """Build a safe display contract for one higher timeframe.

    A stale directional calculation must never look like current directional
    guidance. The internal state keeps STALE so the resampler remains
    observable, while the public/display value intentionally becomes
    UNAVAILABLE and retains freshness, age, and source provenance.
    """
    age = _age_seconds(last_ts, now_ts)
    is_stale = trend == STALE or (
        age is not None and age > stale_threshold_sec
    )
    is_unavailable = trend == UNAVAILABLE or last_ts is None

    if is_stale:
        display_trend = UNAVAILABLE
        freshness = STALE
        reason = "closed_bar_stale"
    elif is_unavailable:
        display_trend = UNAVAILABLE
        freshness = UNAVAILABLE
        reason = "insufficient_closed_bars"
    else:
        display_trend = trend
        freshness = "CURRENT"
        reason = None

    return {
        "trend": display_trend,
        "strength": None if is_stale else strength,
        "last_closed_bar": _ts_to_iso(last_ts),
        "bar_count": bar_count,
        "stale": is_stale,
        "freshness": freshness,
        "age_seconds": age,
        "source": "databento_1m_resample_closed_bars",
        "unavailable_reason": reason,
    }


def get_mtf_state(instrument: str) -> Dict[str, Any]:
    """Return the current MTF trend state for display/API.  FAIL-OPEN.

    Always returns a valid dict (never raises).  Stale check is re-run
    at read time so displays stay accurate even without new bars.
    """
    try:
        with _LOCK:
            state = _get_or_create(instrument)
            # Re-run stale check at read time
            _recalculate_trends(state)
            t4h  = state["trend_4h"]
            t15m = state["trend_15m"]
            s4h  = state["strength_4h"]
            s15m = state["strength_15m"]
            ts4h  = state["last_bar_ts_4h"]
            ts15m = state["last_bar_ts_15m"]
            nb4h  = len(state["bars_4h"])
            nb15m = len(state["bars_15m"])

        now_ts = time.time()
        four_hour = _display_timeframe(
            trend=t4h,
            strength=s4h,
            last_ts=ts4h,
            bar_count=nb4h,
            stale_threshold_sec=STALE_4H_SEC,
            now_ts=now_ts,
        )
        fifteen_minute = _display_timeframe(
            trend=t15m,
            strength=s15m,
            last_ts=ts15m,
            bar_count=nb15m,
            stale_threshold_sec=STALE_15M_SEC,
            now_ts=now_ts,
        )
        alignment = get_alignment(
            four_hour["trend"],
            fifteen_minute["trend"],
        )
        alignment_freshness = (
            STALE if (four_hour["stale"] or fifteen_minute["stale"])
            else ("CURRENT" if alignment != UNAVAILABLE else UNAVAILABLE)
        )

        return {
            "instrument":     instrument,
            "four_hour":     four_hour,
            "fifteen_minute": fifteen_minute,
            "alignment":     alignment,
            "alignment_freshness": alignment_freshness,
            "updated_at":    _ts_to_iso(now_ts),
            "source":        "databento_1m_resample",
            "note":          (
                "Shadow/display-only higher-timeframe context from closed Databento "
                "1m-resampled bars. Stale values are exposed as UNAVAILABLE and "
                "never gate, score, size, or override Visual Brain."
            ),
        }

    except Exception:
        return {
            "instrument":     instrument,
            "four_hour": {
                "trend": UNAVAILABLE, "stale": False, "freshness": UNAVAILABLE,
                "age_seconds": None, "source": "databento_1m_resample_closed_bars",
                "unavailable_reason": "state_read_failed",
                "last_closed_bar": None, "bar_count": 0,
            },
            "fifteen_minute": {
                "trend": UNAVAILABLE, "stale": False, "freshness": UNAVAILABLE,
                "age_seconds": None, "source": "databento_1m_resample_closed_bars",
                "unavailable_reason": "state_read_failed",
                "last_closed_bar": None, "bar_count": 0,
            },
            "alignment":      UNAVAILABLE,
            "alignment_freshness": UNAVAILABLE,
            "updated_at":     _ts_to_iso(time.time()),
            "source":         "databento_1m_resample",
            "error":          "state_read_failed",
        }


def get_snapshot_for_signal(instrument: str) -> Dict[str, Optional[str]]:
    """Return the 3 trend context fields to freeze at signal time.

    FAIL-OPEN — returns None values on any error.
    """
    try:
        st = get_mtf_state(instrument)
        return {
            "four_h_trend_at_signal":       st["four_hour"]["trend"],
            "fifteen_m_trend_at_signal":    st["fifteen_minute"]["trend"],
            "trend_alignment_at_signal":    st["alignment"],
        }
    except Exception:
        return {
            "four_h_trend_at_signal":    None,
            "fifteen_m_trend_at_signal": None,
            "trend_alignment_at_signal": None,
        }
