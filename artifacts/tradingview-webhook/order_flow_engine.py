"""
Order Flow Engine V1 — live directional Edge Score confluence.

Computes order-flow metrics from Databento 1-minute bars, CVD state, and a fresh
MBP-1 top-of-book snapshot. The parent analysis optionally maps the composite
result to a bounded, direction-aware Edge Score adjustment.

SAFETY CONTRACT
───────────────
• This module never mutates gate, sizing, arm state, or execution itself.
• Its computed 0..100 score may be consumed by app.py as a bounded ±15 Edge
  confluence adjustment; unavailable data is always a no-op.
• Flag-gated: ORDER_FLOW_V1_ENABLED env var (default "0" = OFF).
• Fail-open: compute_order_flow() always returns a dict, never raises.
• bar buy_volume/sell_volume fields only exist on bars captured after
  the engine was deployed; older bars return available=False, reason=bars_pre_v1.

METRICS
───────
Computable from existing Databento trades schema (side A/B per tick):
  bar_delta            buy_volume − sell_volume per 1m bar
  delta_ratio          bar_delta / total_volume (−1.0 to +1.0)
  delta_acceleration   current bar delta − previous bar delta
  cvd                  session cumulative signed volume (existing store)
  cvd_slope            CVD N bars ago vs today (from per-bar cvd_snapshot)
  cvd_divergence       price direction vs CVD slope direction
  absorption_side      large opposing delta but price held/reversed
  absorption_strength  STRONG / MODERATE

MBP-1 metric:
  book_imbalance       (best_bid_size − best_ask_size) / total displayed size

OUTPUTS (per compute_order_flow() call)
───────
  order_flow_score           int  0–100 (50 = neutral)
  order_flow_state           str  STRONG_BULLISH / BULLISH / NEUTRAL / BEARISH / STRONG_BEARISH
  order_flow_reversal_confirmed  bool  sweep→absorption→delta_flip→confirm sequence
"""

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Feature flag ──────────────────────────────────────────────────────────────
# Default OFF — no existing behaviour changes until explicitly enabled.
ORDER_FLOW_V1_ENABLED: bool = os.environ.get("ORDER_FLOW_V1_ENABLED", "0") == "1"

# ── Scoring weights (all configurable in one place) ───────────────────────────
_WEIGHTS: dict[str, int] = {
    "cvd_state":          15,   # CVD bullish/bearish alignment
    "cvd_slope":          10,   # CVD trending in direction over N bars
    "bar_delta":          10,   # last-bar delta directional agreement
    "delta_acceleration":  8,   # delta accelerating in same direction
    "absorption":         12,   # price held vs large opposing delta
    "cvd_divergence":     10,   # price/CVD divergence (hidden strength/weakness)
    "book_imbalance":      8,   # current best-bid / best-ask displayed pressure
}

# ── Thresholds ────────────────────────────────────────────────────────────────
_DELTA_RATIO_MIN:      float = 0.15   # |delta/vol| > this = directional bar
_DELTA_RATIO_STRONG:   float = 0.60   # |delta/vol| > this = strong directional bar
_ABSORPTION_MIN_RATIO: float = 0.45   # |delta/vol| > this + price mismatch = absorption
_ABSORPTION_STRONG_R:  float = 0.70   # |delta/vol| > this = STRONG absorption
_CVD_SLOPE_BARS:       int   = 5      # look-back bars for CVD slope
_DIVERGENCE_BARS:      int   = 3      # look-back bars for divergence check
_SWEEP_WICK_RATIO:     float = 1.5    # wick / body ratio threshold for sweep detection
_SWEEP_VOL_MULT:       float = 1.2    # sweep bar volume must be ≥ this × avg vol
_BOOK_IMBALANCE_MIN:   float = 0.10   # ignore near-balanced top-of-book noise


# ── Utilities ─────────────────────────────────────────────────────────────────

def _safe_div(a: Any, b: Any, default: float = 0.0) -> float:
    try:
        if b is None or b == 0:
            return default
        return float(a) / float(b)
    except (TypeError, ZeroDivisionError, ValueError):
        return default


def _bars_have_of_fields(bars: list) -> bool:
    """Return True iff the last bar contains buy_volume/sell_volume (post-V1 bars)."""
    if not bars:
        return False
    last = bars[-1]
    return "buy_volume" in last or "sell_volume" in last


# ── Per-bar metric helpers ─────────────────────────────────────────────────────

def compute_bar_delta(bar: dict) -> Optional[int]:
    """Signed volume delta for one bar (buy aggressor − sell aggressor).
    Returns None when buy_volume/sell_volume fields are absent (pre-V1 bars).
    """
    buy  = bar.get("buy_volume")
    sell = bar.get("sell_volume")
    if buy is None and sell is None:
        return None
    return int(buy or 0) - int(sell or 0)


def compute_delta_ratio(bar: dict) -> Optional[float]:
    """bar_delta / volume.  Range [−1.0, +1.0].
    +1.0 = all trades were buy aggressors; −1.0 = all sell aggressors.
    """
    delta = compute_bar_delta(bar)
    vol   = bar.get("volume")
    if delta is None or not vol:
        return None
    return round(_safe_div(delta, vol), 4)


def compute_delta_acceleration(bars: list) -> Optional[float]:
    """Delta change between the last two bars (current bar delta − previous)."""
    if len(bars) < 2:
        return None
    d_now  = compute_bar_delta(bars[-1])
    d_prev = compute_bar_delta(bars[-2])
    if d_now is None or d_prev is None:
        return None
    return d_now - d_prev


# ── Series metric helpers ──────────────────────────────────────────────────────

def compute_cvd_slope(bars: list, n: int = _CVD_SLOPE_BARS) -> Optional[float]:
    """CVD change over the last N bars using per-bar cvd_snapshot fields.

    cvd_snapshot is written by DatabentoBrain._on_bar_close() to capture the
    session-cumulative CVD at the moment each bar closes.  Bars without the
    field (pre-V1) are skipped.

    ``n`` means "look N bars back from the last bar".  With 3 bars [A, B, C]
    and n=2, the anchor is bar A (index len-1-n = 0) and the slope is C − A.
    """
    valid = [b for b in bars if b.get("cvd_snapshot") is not None]
    if len(valid) < 2:
        return None
    recent = valid[-1]["cvd_snapshot"]
    anchor = valid[max(0, len(valid) - 1 - n)]["cvd_snapshot"]
    return recent - anchor


def compute_cvd_divergence(bars: list, n: int = _DIVERGENCE_BARS) -> Optional[str]:
    """Detect CVD/price divergence over the last N closed bars.

    BULLISH_DIV: price made a lower close but CVD rose (sellers being absorbed).
    BEARISH_DIV: price made a higher close but CVD fell (distribution).

    Returns 'BULLISH', 'BEARISH', or None.
    """
    if len(bars) < n + 1:
        return None
    window      = bars[-(n + 1):]
    price_start = window[0].get("close", 0)
    price_end   = window[-1].get("close", 0)
    cvd_start   = window[0].get("cvd_snapshot")
    cvd_end     = window[-1].get("cvd_snapshot")
    if cvd_start is None or cvd_end is None:
        return None
    price_dn = price_end < price_start
    price_up = price_end > price_start
    cvd_up   = cvd_end   > cvd_start
    cvd_dn   = cvd_end   < cvd_start
    if price_dn and cvd_up:
        return "BULLISH"   # price fell but buyers absorbed → bullish divergence
    if price_up and cvd_dn:
        return "BEARISH"   # price rose but sellers absorbed → bearish divergence
    return None


def compute_absorption(bars: list) -> tuple[Optional[str], Optional[str]]:
    """Detect buy or sell absorption in the last bar.

    Sell absorption: heavy sell delta (supply) but price closed flat or higher.
    Buy absorption:  heavy buy delta (demand)  but price closed flat or lower.

    Returns (absorption_side, absorption_strength) or (None, None).
    """
    if not bars:
        return None, None
    bar   = bars[-1]
    ratio = compute_delta_ratio(bar)
    vol   = int(bar.get("volume") or 0)
    if ratio is None or vol < 10:
        return None, None

    price_move = bar.get("close", bar.get("open", 0)) - bar.get("open", bar.get("close", 0))

    # Sell absorption: dominant sellers (ratio < −threshold) but price didn't fall
    if ratio < -_ABSORPTION_MIN_RATIO and price_move >= 0:
        strength = "STRONG" if ratio < -_ABSORPTION_STRONG_R else "MODERATE"
        return "SELLERS_ABSORBED", strength

    # Buy absorption: dominant buyers (ratio > +threshold) but price didn't rise
    if ratio > _ABSORPTION_MIN_RATIO and price_move <= 0:
        strength = "STRONG" if ratio > _ABSORPTION_STRONG_R else "MODERATE"
        return "BUYERS_ABSORBED", strength

    return None, None


def compute_book_imbalance(book_snapshot: Optional[dict]) -> Optional[float]:
    """Return normalized MBP-1 bid/ask pressure or None for unusable data.

    databento_brain only returns fresh snapshots. This remains defensive because
    Order Flow can also be called by tests, routes, or future consumers without
    that source helper.
    """
    if not isinstance(book_snapshot, dict) or book_snapshot.get("available") is not True:
        return None
    try:
        bid_size = int(book_snapshot.get("bid_size"))
        ask_size = int(book_snapshot.get("ask_size"))
        bid_price = float(book_snapshot.get("bid_price"))
        ask_price = float(book_snapshot.get("ask_price"))
    except (TypeError, ValueError):
        return None
    if bid_size <= 0 or ask_size <= 0 or bid_price <= 0 or ask_price <= bid_price:
        return None
    total = bid_size + ask_size
    if total <= 0:
        return None
    return round((bid_size - ask_size) / total, 4)


# ── Composite score and state ─────────────────────────────────────────────────

def compute_order_flow_score(
    *,
    cvd_state:        Optional[str],
    cvd_slope:        Optional[float],
    bar_delta:        Optional[int],
    delta_ratio:      Optional[float],
    delta_accel:      Optional[float],
    absorption_side:  Optional[str],
    cvd_divergence:   Optional[str],
    book_imbalance:   Optional[float] = None,
) -> int:
    """Composite 0–100 order-flow score.

    50 = neutral / no signal.
    > 50 = net buying pressure;  < 50 = net selling pressure.
    Scoring is symmetric — the caller interprets score relative to the setup.
    """
    score = 50   # neutral baseline

    # CVD direction/state
    if cvd_state == "bullish":
        score += _WEIGHTS["cvd_state"]
    elif cvd_state == "bearish":
        score -= _WEIGHTS["cvd_state"]

    # CVD slope (trending in direction)
    if cvd_slope is not None:
        if cvd_slope > 0:
            score += _WEIGHTS["cvd_slope"]
        elif cvd_slope < 0:
            score -= _WEIGHTS["cvd_slope"]

    # Last-bar delta direction
    if delta_ratio is not None:
        if delta_ratio > _DELTA_RATIO_MIN:
            score += _WEIGHTS["bar_delta"]
        elif delta_ratio < -_DELTA_RATIO_MIN:
            score -= _WEIGHTS["bar_delta"]

    # Delta acceleration (strengthening in the same direction)
    if delta_accel is not None and bar_delta is not None and bar_delta != 0:
        if delta_accel > 0 and bar_delta > 0:       # acceleration on buy side
            score += _WEIGHTS["delta_acceleration"]
        elif delta_accel < 0 and bar_delta < 0:     # acceleration on sell side
            score += _WEIGHTS["delta_acceleration"]
        elif delta_accel > 0 and bar_delta < 0:     # decelerating sell
            score -= _WEIGHTS["delta_acceleration"] // 2
        elif delta_accel < 0 and bar_delta > 0:     # decelerating buy
            score -= _WEIGHTS["delta_acceleration"] // 2

    # Absorption (seller absorption = bullish; buyer absorption = bearish)
    if absorption_side == "SELLERS_ABSORBED":
        score += _WEIGHTS["absorption"]
    elif absorption_side == "BUYERS_ABSORBED":
        score -= _WEIGHTS["absorption"]

    # CVD divergence
    if cvd_divergence == "BULLISH":
        score += _WEIGHTS["cvd_divergence"]
    elif cvd_divergence == "BEARISH":
        score -= _WEIGHTS["cvd_divergence"]

    # This changes only the existing Order Flow composite. app.py still maps the
    # composite to its pre-existing bounded ±15 Edge Score adjustment.
    if book_imbalance is not None:
        if book_imbalance >= _BOOK_IMBALANCE_MIN:
            score += _WEIGHTS["book_imbalance"]
        elif book_imbalance <= -_BOOK_IMBALANCE_MIN:
            score -= _WEIGHTS["book_imbalance"]

    return max(0, min(100, score))


def _score_to_state(score: int) -> str:
    if score >= 75:
        return "STRONG_BULLISH"
    if score >= 58:
        return "BULLISH"
    if score >= 43:
        return "NEUTRAL"
    if score >= 26:
        return "BEARISH"
    return "STRONG_BEARISH"


# ── Reversal sequence detector ────────────────────────────────────────────────

def detect_reversal_sequence(bars: list) -> bool:
    """Detect LIQUIDITY SWEEP → ABSORPTION → DELTA REVERSAL → STRUCTURE CONFIRMATION.

    Requires at least 4 bars with buy_volume/sell_volume fields.
    Returns True only if all four conditions fire in sequence on the last 4 bars.
    FAIL-OPEN: returns False on any exception or insufficient data.
    """
    try:
        if len(bars) < 4 or not _bars_have_of_fields(bars):
            return False
        b1, b2, b3, b4 = bars[-4], bars[-3], bars[-2], bars[-1]

        # ── 1. Sweep bar: abnormally large range + volume spike ───────────────
        # A sweep bar is identified by an outsized price range (relative to the
        # average of prior bars) combined with a volume spike.  We do NOT use
        # wick/body ratio because zero-body doji candles would produce infinite
        # ratios and trigger false positives.
        b1_range = b1.get("high", b1["close"]) - b1.get("low", b1["close"])
        prior_bars = bars[:-4]
        if prior_bars:
            avg_vol = sum(b.get("volume", 0) for b in prior_bars) / len(prior_bars)
            if avg_vol > 0 and b1.get("volume", 0) < avg_vol * _SWEEP_VOL_MULT:
                return False
            avg_range = sum(
                (b.get("high", b["close"]) - b.get("low", b["close"]))
                for b in prior_bars
            ) / len(prior_bars)
            if avg_range > 0 and b1_range < avg_range * _SWEEP_WICK_RATIO:
                return False
        elif b1_range < 0.5:
            # No prior bars to compare — require at least a minimal absolute range
            return False

        # ── 2. Absorption bar: large opposing delta ───────────────────────────
        b2_ratio = compute_delta_ratio(b2)
        if b2_ratio is None or abs(b2_ratio) < _ABSORPTION_MIN_RATIO:
            return False

        # ── 3. Delta reversal: delta flips between b2 and b3 ─────────────────
        b2_delta = compute_bar_delta(b2)
        b3_delta = compute_bar_delta(b3)
        if not b2_delta or not b3_delta:
            return False
        if (b2_delta > 0) == (b3_delta > 0):
            return False   # no flip

        # ── 4. Structure confirmation: b4 closes in reversal direction ────────
        reversal_bullish = b3_delta > 0
        b4_move = b4["close"] - b4["open"]
        if reversal_bullish and b4_move <= 0:
            return False
        if not reversal_bullish and b4_move >= 0:
            return False

        return True
    except Exception:
        return False


# ── Main entry point ──────────────────────────────────────────────────────────

def compute_order_flow(
    inst:       str,
    bars_deque: Any,
    cvd_record: Optional[dict] = None,
    book_snapshot: Optional[dict] = None,
) -> dict:
    """Compute all order-flow metrics for one instrument.

    Args:
        inst:       canonical instrument name ("MNQ", "MGC", etc.)
        bars_deque: DATABENTO_BARS_BY_INST[inst] — deque or list of bar dicts
        cvd_record: CVD_BY_TICKER[inst] — {state, value, direction, ts, source}

    Returns:
        dict with all order-flow fields. book_snapshot is a fresh MBP-1
        best-bid/best-ask snapshot supplied by databento_brain; it is optional
        so unavailable market depth remains a no-op. All fields are nullable.

    FAIL-OPEN: always returns a dict, never raises.
    This function is pure/fail-open. app.py owns any bounded Edge Score integration.
    """
    if not ORDER_FLOW_V1_ENABLED:
        return {"available": False, "reason": "flag_off"}

    try:
        bars = list(bars_deque) if bars_deque is not None else []

        if not bars:
            return {"available": False, "reason": "no_bars"}

        if not _bars_have_of_fields(bars):
            # Bars predating Order Flow V1 deployment lack buy/sell fields.
            # Return a partial record so callers know the state without crashing.
            return {
                "available":                    False,
                "reason":                       "bars_pre_v1",
                "order_flow_score":             None,
                "order_flow_state":             None,
                "order_flow_reversal_confirmed": False,
            }

        last = bars[-1]

        # ── Per-bar metrics ───────────────────────────────────────────────────
        bar_delta_v  = compute_bar_delta(last)
        delta_ratio_v = compute_delta_ratio(last)
        delta_accel_v = compute_delta_acceleration(bars)
        buy_vol  = int(last.get("buy_volume")  or 0) or None   # None when 0 (unavailable)
        sell_vol = int(last.get("sell_volume") or 0) or None

        # ── Series metrics ────────────────────────────────────────────────────
        cvd_slope_v  = compute_cvd_slope(bars)
        cvd_div_v    = compute_cvd_divergence(bars)
        abs_side, abs_strength = compute_absorption(bars)
        book_imbalance_v = compute_book_imbalance(book_snapshot)

        # ── CVD from shared authoritative store ───────────────────────────────
        cvd_val   = None
        cvd_state = None
        if isinstance(cvd_record, dict):
            cvd_val   = cvd_record.get("value")
            cvd_state = cvd_record.get("state")   # "bullish" / "bearish" / None

        # ── Composite score + state ───────────────────────────────────────────
        score = compute_order_flow_score(
            cvd_state=cvd_state,
            cvd_slope=cvd_slope_v,
            bar_delta=bar_delta_v,
            delta_ratio=delta_ratio_v,
            delta_accel=delta_accel_v,
            absorption_side=abs_side,
            cvd_divergence=cvd_div_v,
            book_imbalance=book_imbalance_v,
        )
        state = _score_to_state(score)

        # ── Reversal sequence detection ───────────────────────────────────────
        reversal = detect_reversal_sequence(bars)

        return {
            "available":    True,
            "instrument":   inst,
            # Per-bar
            "bar_delta":               bar_delta_v,
            "delta_ratio":             delta_ratio_v,
            "delta_acceleration":      delta_accel_v,
            # Spec labels: ask_volume = buy aggressor (hit the ask);
            #              bid_volume = sell aggressor (hit the bid)
            "ask_volume":              buy_vol,
            "bid_volume":              sell_vol,
            "book_imbalance":          book_imbalance_v,
            # Series
            "cvd":                     cvd_val,
            "cvd_slope":               round(cvd_slope_v, 2) if cvd_slope_v is not None else None,
            "cvd_divergence":          cvd_div_v,
            "absorption_side":         abs_side,
            "absorption_strength":     abs_strength,
            # Composite
            "order_flow_score":            score,
            "order_flow_state":            state,
            "order_flow_reversal_confirmed": reversal,
        }

    except Exception as exc:
        logger.debug("order_flow_engine.compute_order_flow(%s): %s", inst, exc)
        return {"available": False, "reason": str(exc)}
