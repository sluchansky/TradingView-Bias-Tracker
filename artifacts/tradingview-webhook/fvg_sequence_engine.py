"""
FVG / IFVG Shadow Sequence Engine — Step B.

Deterministic shadow state machine that tracks FVG and IFVG zone interactions
from zone creation through to a hypothetical "shadow ready" trade candidate.

SHADOW / DISPLAY-ONLY CONTRACT:
  - Never modifies gate verdicts, edge scores, position sizes, or execution.
  - Never emits production READY or production trade alerts.
  - Never calls TradersPost or any broker path.
  - Fail-open everywhere — any exception leaves production state unchanged.

Setup families:
  FVG_CONTINUATION — price returns to, touches, and holds a plain FVG zone.
  IFVG_REVERSAL    — a failed FVG spawns an IFVG that retests and reverses.

State machines:
  FVG_CONTINUATION:  RETURN_PENDING → TOUCHED → HOLD_PENDING → HOLD_CONFIRMED
                      → STRUCTURE_PENDING → MOMENTUM_PENDING → ENTRY_WINDOW
                      → SHADOW_READY  (terminal)
                      or  EXPIRED / INVALIDATED  (terminal)

  IFVG_REVERSAL:     INVERTED → RETEST_PENDING → RETESTED → HOLD_PENDING
                      → HOLD_CONFIRMED → STRUCTURE_PENDING → MOMENTUM_PENDING
                      → ENTRY_WINDOW → SHADOW_READY
                      or  EXPIRED / INVALIDATED
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# State constants
# ─────────────────────────────────────────────────────────────────────────────

# Shared states (both families)
SC_HOLD_PENDING      = "HOLD_PENDING"
SC_HOLD_CONFIRMED    = "HOLD_CONFIRMED"
SC_STRUCTURE_PENDING = "STRUCTURE_PENDING"
SC_MOMENTUM_PENDING  = "MOMENTUM_PENDING"
SC_ENTRY_WINDOW      = "ENTRY_WINDOW"
SC_SHADOW_READY      = "SHADOW_READY"
SC_EXPIRED           = "EXPIRED"
SC_INVALIDATED       = "INVALIDATED"

# FVG_CONTINUATION-specific
SC_RETURN_PENDING = "RETURN_PENDING"
SC_TOUCHED        = "TOUCHED"

# IFVG_REVERSAL-specific
SC_INVERTED       = "INVERTED"
SC_RETEST_PENDING = "RETEST_PENDING"
SC_RETESTED       = "RETESTED"

TERMINAL_SEQ_STATES = frozenset({SC_SHADOW_READY, SC_EXPIRED, SC_INVALIDATED})

# Setup families
SF_CONTINUATION = "FVG_CONTINUATION"
SF_REVERSAL     = "IFVG_REVERSAL"

# Entry window labels
EW_AVAILABLE = "ENTRY_AVAILABLE"
EW_LATE      = "ENTRY_LATE"
EW_CHASING   = "ENTRY_CHASING"
EW_EXPIRED   = "ENTRY_EXPIRED"

# State advance order for primary election
_STATE_ORDER: Dict[str, int] = {
    SC_RETURN_PENDING: 0, SC_INVERTED: 0, SC_RETEST_PENDING: 1,
    SC_TOUCHED: 2, SC_RETESTED: 3, SC_HOLD_PENDING: 4,
    SC_HOLD_CONFIRMED: 5, SC_STRUCTURE_PENDING: 6,
    SC_MOMENTUM_PENDING: 7, SC_ENTRY_WINDOW: 8,
}

# ─────────────────────────────────────────────────────────────────────────────
# Tuning knobs (shadow-only — never affect production paths)
# ─────────────────────────────────────────────────────────────────────────────
_MAX_SEQ_AGE_SECS   = 7200    # 2 hours; sequences older than this expire
_MAX_PRICE_ATR_DIST = 3.0     # expire if price drifts this many ATRs against zone
_ENTRY_AVAIL_SECS   = 60      # window: ENTRY_AVAILABLE
_ENTRY_LATE_SECS    = 120     # window: ENTRY_LATE (after this → ENTRY_EXPIRED)
_MOMENTUM_REQUIRED  = 3       # momentum checks needed out of _MOMENTUM_TOTAL
_MOMENTUM_TOTAL     = 5
_STOP_ATR_BUF       = 0.20    # stop = zone edge ± ATR * buf

# ─────────────────────────────────────────────────────────────────────────────
# Module-level state
# ─────────────────────────────────────────────────────────────────────────────
SEQUENCES_BY_INST: Dict[str, List[Dict]] = {}
_SEQ_LOCK = threading.Lock()
_FVG_SEQ_DB_READY = False
_db_fn: Any = None  # injected from app.py boot probe


# ─────────────────────────────────────────────────────────────────────────────
# DB boot probe
# ─────────────────────────────────────────────────────────────────────────────
def check_fvg_seq_db_ready(get_db_fn) -> bool:
    """Probe the fvg_shadow_sequences table. Fail-open if unavailable."""
    global _FVG_SEQ_DB_READY, _db_fn
    _db_fn = get_db_fn
    try:
        conn = get_db_fn()
        cur  = conn.cursor()
        cur.execute("SELECT 1 FROM fvg_shadow_sequences LIMIT 1")
        cur.close()
        _FVG_SEQ_DB_READY = True
        logger.info("fvg_shadow_sequences table ready")
    except Exception as exc:
        logger.warning("fvg_shadow_sequences not ready: %s (in-memory only)", exc)
        _FVG_SEQ_DB_READY = False
    return _FVG_SEQ_DB_READY


# ─────────────────────────────────────────────────────────────────────────────
# Tiny utilities
# ─────────────────────────────────────────────────────────────────────────────
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(val: Any) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    try:
        s = str(val).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _safe_float(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _age_secs(started: Optional[datetime], now: datetime) -> float:
    if started is None:
        return 0.0
    return max((now - started).total_seconds(), 0.0)


def _compute_atr(bars: List[Dict], period: int = 14) -> float:
    trs: List[float] = []
    for i in range(1, len(bars)):
        h  = _safe_float(bars[i].get("high"))  or 0.0
        l  = _safe_float(bars[i].get("low"))   or 0.0
        pc = _safe_float(bars[i - 1].get("close")) or 0.0
        if h > 0.0 and l > 0.0:
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not trs:
        return 0.0
    return sum(trs[-period:]) / len(trs[-period:])


def _volume_sma(bars: List[Dict], period: int = 20) -> float:
    vols = [_safe_float(b.get("volume")) or 0.0 for b in bars[-period:]]
    nonzero = [v for v in vols if v > 0.0]
    return sum(nonzero) / len(nonzero) if nonzero else 0.0


def _dt_to_iso(v: Any) -> Optional[str]:
    if isinstance(v, datetime):
        return v.isoformat()
    return v  # already string or None


# ─────────────────────────────────────────────────────────────────────────────
# Structure-event helpers
# ─────────────────────────────────────────────────────────────────────────────
def _is_bullish_struct(alert_type: str) -> bool:
    return alert_type in ("BOS DEMAND", "CHOCH DEMAND")


def _is_bearish_struct(alert_type: str) -> bool:
    return alert_type in ("BOS SUPPLY", "CHOCH SUPPLY")


def _struct_events_after(
    alert_history: List[Dict],
    inst: str,
    direction: str,
    after_ts: Optional[datetime],
) -> List[Dict]:
    """Return structure events for inst+direction occurring strictly AFTER after_ts.

    A BOS/CHOCH that happened before the zone touch cannot satisfy post-touch
    confirmation (Part 5 requirement).
    """
    is_bullish = (direction == "BULLISH")
    results: List[Dict] = []
    for evt in alert_history:
        evt_inst = evt.get("instrument") or evt.get("ticker") or ""
        if evt_inst != inst:
            continue
        atype = evt.get("alert_type", "")
        if is_bullish and not _is_bullish_struct(atype):
            continue
        if not is_bullish and not _is_bearish_struct(atype):
            continue
        if after_ts is not None:
            evt_ts = _parse_ts(evt.get("timestamp"))
            if evt_ts is None or evt_ts <= after_ts:
                continue
        results.append(evt)
    return results


def _opposite_struct_events_after(
    alert_history: List[Dict],
    inst: str,
    direction: str,
    after_ts: Optional[datetime],
) -> List[Dict]:
    opp = "BEARISH" if direction == "BULLISH" else "BULLISH"
    return _struct_events_after(alert_history, inst, opp, after_ts)


# ─────────────────────────────────────────────────────────────────────────────
# Transition helpers
# ─────────────────────────────────────────────────────────────────────────────
def _transition(seq: Dict, new_state: str, now: datetime, **extras) -> None:
    prev = seq["current_state"]
    seq["current_state"]      = new_state
    seq["last_transition_at"] = now
    seq["_dirty"]             = True
    for k, v in extras.items():
        seq[k] = v
    logger.debug(
        "FVG SEQ %s %s %s: %s → %s",
        seq["instrument"], seq["setup_family"], seq["direction"], prev, new_state,
    )


def _invalidate(seq: Dict, reason: str, now: datetime) -> None:
    _transition(seq, SC_INVALIDATED, now, invalidated_at=now, invalidation_reason=reason)


def _expire(seq: Dict, reason: str, now: datetime) -> None:
    _transition(seq, SC_EXPIRED, now, expired_at=now, invalidation_reason=reason)


# ─────────────────────────────────────────────────────────────────────────────
# Momentum confirmation (Part 6)
# ─────────────────────────────────────────────────────────────────────────────
def _check_momentum(
    direction: str,
    last_bar: Dict,
    atr: float,
    vol_sma: float,
    zone_lower: float,
    zone_upper: float,
    cvd: Optional[Dict],
    bars: List[Dict],
) -> Dict:
    """Transparent momentum check — explicit named checks, no opaque score.

    Returns a dict matching the Part 6 schema with a boolean 'confirmed'.
    Requires _MOMENTUM_REQUIRED of _MOMENTUM_TOTAL checks to confirm.
    """
    b_open  = _safe_float(last_bar.get("open"))  or 0.0
    b_high  = _safe_float(last_bar.get("high"))  or 0.0
    b_low   = _safe_float(last_bar.get("low"))   or 0.0
    b_close = _safe_float(last_bar.get("close")) or 0.0
    b_vol   = _safe_float(last_bar.get("volume")) or 0.0
    bar_range   = max(b_high - b_low, 1e-6)
    atr_safe    = max(atr, 1e-6)
    is_long     = (direction == "BULLISH")

    # 1. Displacement — directional bar body >= 1× ATR
    body_dir = (b_close - b_open) if is_long else (b_open - b_close)
    check_displacement = body_dir >= atr_safe

    # 2. Close strength — close in top/bottom 30% of bar's high-low range
    if is_long:
        check_close_strength = (b_close - b_low) / bar_range >= 0.70
    else:
        check_close_strength = (b_high - b_close) / bar_range >= 0.70

    # 3. CVD / delta agreement
    check_cvd = False
    if cvd and isinstance(cvd, dict):
        cvd_dir = cvd.get("direction")
        check_cvd = (is_long and cvd_dir == "rising") or (not is_long and cvd_dir == "falling")

    # 4. Volume expansion — last bar volume > rolling SMA * 1.2
    check_volume = (vol_sma > 0.0) and (b_vol > vol_sma * 1.2)

    # 5. Movement away from zone — close is >= 0.5 ATR outside the zone edge
    if is_long:
        check_movement = b_close >= zone_upper + 0.5 * atr_safe
    else:
        check_movement = b_close <= zone_lower - 0.5 * atr_safe

    checks = {
        "displacement":   check_displacement,
        "close_strength": check_close_strength,
        "cvd":            check_cvd,
        "volume":         check_volume,
        "movement":       check_movement,
    }
    confirmed_count = sum(checks.values())

    return {
        "direction":       direction,
        "confirmed":       confirmed_count >= _MOMENTUM_REQUIRED,
        "checks":          checks,
        "confirmed_count": confirmed_count,
        "required_count":  _MOMENTUM_REQUIRED,
        "total_checks":    _MOMENTUM_TOTAL,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Entry window classification (Part 7)
# ─────────────────────────────────────────────────────────────────────────────
def _classify_entry_window(
    seq: Dict,
    last_bar: Dict,
    atr: float,
    now: datetime,
) -> Dict:
    """Classify the current entry window state and return a snapshot dict."""
    opened_at          = _parse_ts(seq.get("entry_window_opened_at"))
    confirmation_price = seq.get("_confirmation_price") or 0.0
    direction          = seq["direction"]
    zone_lower         = seq["zone_lower"]
    zone_upper         = seq["zone_upper"]
    current_price      = _safe_float(last_bar.get("close")) or 0.0
    atr_safe           = max(atr, 1e-6)

    age_secs = _age_secs(opened_at, now)

    # Distance from zone edge (in the direction of trade)
    if direction == "BULLISH":
        distance_from_zone = max(current_price - zone_upper, 0.0)
    else:
        distance_from_zone = max(zone_lower - current_price, 0.0)
    atr_distance = distance_from_zone / atr_safe

    # Distance moved since confirmation
    if direction == "BULLISH":
        distance_moved = current_price - confirmation_price
    else:
        distance_moved = confirmation_price - current_price

    # Estimated target consumed
    stop_price = (
        zone_lower - _STOP_ATR_BUF * atr_safe if direction == "BULLISH"
        else zone_upper + _STOP_ATR_BUF * atr_safe
    )
    risk = abs(confirmation_price - stop_price)
    if risk > 0.0:
        if direction == "BULLISH":
            consumed_pct = min((current_price - confirmation_price) / risk * 100.0, 100.0)
        else:
            consumed_pct = min((confirmation_price - current_price) / risk * 100.0, 100.0)
    else:
        consumed_pct = 0.0

    # Classify (most restrictive first)
    if consumed_pct >= 75.0:
        label = EW_EXPIRED
    elif age_secs >= _ENTRY_LATE_SECS:
        label = EW_EXPIRED
    elif atr_distance >= 1.0:
        label = EW_CHASING
    elif age_secs >= _ENTRY_AVAIL_SECS:
        label = EW_LATE
    else:
        label = EW_AVAILABLE

    return {
        "label":                   label,
        "opened_at":               _dt_to_iso(opened_at),
        "age_seconds":             round(age_secs, 1),
        "confirmation_price":      round(confirmation_price, 4),
        "current_price":           round(current_price, 4),
        "distance_from_zone":      round(distance_from_zone, 4),
        "distance_moved":          round(distance_moved, 4),
        "atr_distance":            round(atr_distance, 4),
        "target_consumed_percent": round(max(consumed_pct, 0.0), 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Shadow trade plan (Part 8)
# ─────────────────────────────────────────────────────────────────────────────
def _build_shadow_plan(seq: Dict, last_bar: Dict, atr: float) -> Dict:
    """Produce a hypothetical entry/stop/target plan.

    Uses canonical zone geometry — no independent risk engine invented.
    SHADOW-ONLY: no broker call, no position size, no TradersPost.
    """
    direction  = seq["direction"]
    zone_lower = seq["zone_lower"]
    zone_upper = seq["zone_upper"]
    atr_safe   = max(atr, 1e-6)

    # Entry at current close (confirmation bar)
    entry = _safe_float(last_bar.get("close")) or (
        zone_upper if direction == "BULLISH" else zone_lower
    )

    # Stop just outside the zone
    if direction == "BULLISH":
        stop = round(zone_lower - _STOP_ATR_BUF * atr_safe, 4)
    else:
        stop = round(zone_upper + _STOP_ATR_BUF * atr_safe, 4)

    risk = abs(entry - stop)
    if risk < 1e-4:
        risk = atr_safe * 0.5

    # Targets at 1R, 2R, 3R
    if direction == "BULLISH":
        targets = [round(entry + risk * r, 4) for r in (1.0, 2.0, 3.0)]
    else:
        targets = [round(entry - risk * r, 4) for r in (1.0, 2.0, 3.0)]

    return {
        "instrument":         seq["instrument"],
        "direction":          direction,
        "setup_family":       seq["setup_family"],
        "sequence_id":        seq["sequence_id"],
        "zone": {
            "lower":    seq["zone_lower"],
            "upper":    seq["zone_upper"],
            "midpoint": seq["zone_midpoint"],
        },
        "hypothetical_entry":   round(entry, 4),
        "hypothetical_stop":    stop,
        "hypothetical_targets": targets,
        "hypothetical_rr":      [1.0, 2.0, 3.0],
        "current_state":        SC_SHADOW_READY,
        "created_at":           _now_utc().isoformat(),
        # ── SHADOW-ONLY SAFETY MARKERS ──────────────────────────────────────
        # These keys are present so any consumer can verify isolation.
        "shadow_only":           True,
        "production_ready":      False,  # NEVER True in this module
        "execution_eligible":    False,  # NEVER True in this module
    }


# ─────────────────────────────────────────────────────────────────────────────
# Explain-why text (Part 14) — deterministic templates, no LLM
# ─────────────────────────────────────────────────────────────────────────────
def _build_explain_why(seq: Dict) -> Dict:
    family    = seq["setup_family"]
    direction = seq["direction"]
    state     = seq["current_state"]
    mom       = seq.get("momentum_snapshot") or {}
    mom_count = mom.get("confirmed_count", 0)
    side      = "Bullish" if direction == "BULLISH" else "Bearish"
    opp_side  = "Bearish" if direction == "BULLISH" else "Bullish"

    why_exists: List[str] = []
    why_not_ready: List[str] = []
    why_ready: List[str] = []

    # Build narrative of completed steps
    if family == SF_REVERSAL:
        why_exists.append(f"{opp_side} FVG failed — price invalidated it")
        why_exists.append(f"{side} IFVG created")
        if seq.get("retest_at"):
            why_exists.append("IFVG retest confirmed")
    else:
        why_exists.append(f"{side} FVG detected")

    if seq.get("touch_at"):
        why_exists.append("Zone touch confirmed")
    if seq.get("structure_confirmed_at"):
        why_exists.append(f"{side} BOS / CHOCH confirmed")
    if mom_count:
        why_exists.append(f"{mom_count} of {_MOMENTUM_TOTAL} momentum checks passed")

    # Why not ready / why ready
    if state == SC_SHADOW_READY:
        why_ready = [
            "All required sequence steps complete",
            "Entry window still valid",
        ]
    elif state in (SC_EXPIRED, SC_INVALIDATED):
        why_not_ready = [seq.get("invalidation_reason") or state.lower()]
    else:
        _hints: Dict[str, str] = {
            SC_RETURN_PENDING:    "Price has not returned to zone yet",
            SC_TOUCHED:           "Awaiting hold decision",
            SC_HOLD_PENDING:      f"Price must close {'above' if direction == 'BULLISH' else 'below'} zone",
            SC_HOLD_CONFIRMED:    f"Awaiting {side.lower()} BOS or CHOCH",
            SC_STRUCTURE_PENDING: f"Awaiting {side.lower()} BOS or CHOCH after zone touch",
            SC_MOMENTUM_PENDING:  f"Momentum threshold not met ({mom_count}/{_MOMENTUM_REQUIRED})",
            SC_ENTRY_WINDOW:      "Classifying entry window",
            SC_INVERTED:          "Awaiting price return to IFVG zone",
            SC_RETEST_PENDING:    "Awaiting IFVG retest",
            SC_RETESTED:          f"Awaiting post-retest close {'above' if direction == 'BULLISH' else 'below'} zone",
        }
        why_not_ready = [_hints.get(state, f"Waiting: {state}")]

    return {
        "why_exists":    why_exists,
        "why_not_ready": why_not_ready,
        "why_ready":     why_ready,
    }


def _next_required_event(seq: Dict) -> str:
    state     = seq["current_state"]
    direction = seq["direction"]
    mom       = seq.get("momentum_snapshot") or {}
    mom_count = mom.get("confirmed_count", 0)
    side      = "Bullish" if direction == "BULLISH" else "Bearish"
    above_below = "above" if direction == "BULLISH" else "below"

    _hints: Dict[str, str] = {
        SC_RETURN_PENDING:    "Price return to zone",
        SC_TOUCHED:           "Zone hold decision",
        SC_HOLD_PENDING:      f"Close {above_below} zone",
        SC_HOLD_CONFIRMED:    f"{side} BOS or CHOCH",
        SC_STRUCTURE_PENDING: f"{side} BOS or CHOCH after touch",
        SC_MOMENTUM_PENDING:  f"Positive displacement / CVD confirmation ({mom_count}/{_MOMENTUM_REQUIRED})",
        SC_ENTRY_WINDOW:      "Entry window classification",
        SC_SHADOW_READY:      "Shadow plan complete ✓",
        SC_EXPIRED:           "—",
        SC_INVALIDATED:       "—",
        SC_INVERTED:          "Price return to IFVG zone",
        SC_RETEST_PENDING:    "IFVG retest from correct side",
        SC_RETESTED:          f"Close {above_below} zone after retest",
    }
    return _hints.get(state, state)


# ─────────────────────────────────────────────────────────────────────────────
# Expiry checks
# ─────────────────────────────────────────────────────────────────────────────
def _should_expire(
    seq: Dict,
    zone: Optional[Dict],
    bars: List[Dict],
    atr: float,
    now: datetime,
) -> Optional[str]:
    """Return a reason string if the sequence should expire, else None."""
    # Age check
    started = _parse_ts(seq.get("started_at"))
    if started and _age_secs(started, now) > _MAX_SEQ_AGE_SECS:
        return f"sequence age > {_MAX_SEQ_AGE_SECS}s"

    # Zone terminal check (for early states)
    state = seq["current_state"]
    if zone and zone.get("status") in ("FAILED", "EXPIRED"):
        if state in (SC_RETURN_PENDING, SC_TOUCHED, SC_HOLD_PENDING,
                     SC_INVERTED, SC_RETEST_PENDING):
            return f"zone {zone['status'].lower()}"

    # Price-drift check (price moved too far against the zone)
    if bars and atr > 0.0:
        current_price = _safe_float(bars[-1].get("close")) or 0.0
        atr_safe      = max(atr, 1e-6)
        zone_lower    = seq["zone_lower"]
        zone_upper    = seq["zone_upper"]
        direction     = seq["direction"]
        if direction == "BULLISH":
            # Negative drift = price below zone lower
            drift_atr = (current_price - zone_lower) / atr_safe
        else:
            drift_atr = (zone_upper - current_price) / atr_safe
        if drift_atr < -_MAX_PRICE_ATR_DIST:
            return f"price drifted {abs(drift_atr):.1f} ATRs against zone"

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Core state machine: FVG_CONTINUATION
# ─────────────────────────────────────────────────────────────────────────────
def _advance_continuation(
    seq: Dict,
    zone: Optional[Dict],
    last_bar: Dict,
    atr: float,
    vol_sma: float,
    alert_history: List[Dict],
    cvd: Optional[Dict],
    bars: List[Dict],
    now: datetime,
) -> None:
    state      = seq["current_state"]
    direction  = seq["direction"]
    inst       = seq["instrument"]
    bar_close  = _safe_float(last_bar.get("close")) or 0.0
    zone_lower = seq["zone_lower"]
    zone_upper = seq["zone_upper"]

    # RETURN_PENDING → TOUCHED: first touch of zone detected
    if state == SC_RETURN_PENDING:
        if zone and zone.get("first_touch_at"):
            touch_ts = _parse_ts(zone["first_touch_at"]) or now
            _transition(seq, SC_TOUCHED, now, touch_at=touch_ts)
            state = SC_TOUCHED

    # TOUCHED → HOLD_PENDING: immediately advance — we're watching for hold
    if state == SC_TOUCHED:
        _transition(seq, SC_HOLD_PENDING, now)
        state = SC_HOLD_PENDING

    # HOLD_PENDING → HOLD_CONFIRMED or stay
    if state == SC_HOLD_PENDING:
        if zone:
            zone_status = zone.get("status", "")
            if zone_status == "HOLDING":
                _transition(seq, SC_HOLD_CONFIRMED, now)
                state = SC_HOLD_CONFIRMED
            elif zone_status in ("FAILED", "EXPIRED"):
                _invalidate(seq, f"zone {zone_status.lower()} during hold-pending", now)
                return
        else:
            _invalidate(seq, "zone lost before hold confirmation", now)
            return

    # HOLD_CONFIRMED → STRUCTURE_PENDING: auto
    if state == SC_HOLD_CONFIRMED:
        _transition(seq, SC_STRUCTURE_PENDING, now)
        state = SC_STRUCTURE_PENDING

    # STRUCTURE_PENDING → MOMENTUM_PENDING: post-touch structure event required
    if state == SC_STRUCTURE_PENDING:
        touch_ts = _parse_ts(seq.get("touch_at"))
        struct_events = _struct_events_after(alert_history, inst, direction, touch_ts)
        if struct_events:
            _transition(seq, SC_MOMENTUM_PENDING, now, structure_confirmed_at=now)
            state = SC_MOMENTUM_PENDING
        else:
            # Two or more strong opposite structure events invalidates the setup
            opp = _opposite_struct_events_after(alert_history, inst, direction, touch_ts)
            if len(opp) >= 2:
                _invalidate(seq, "dominant opposite structure after touch", now)
                return

    # MOMENTUM_PENDING → ENTRY_WINDOW: momentum confirmed
    if state == SC_MOMENTUM_PENDING:
        mom = _check_momentum(
            direction, last_bar, atr, vol_sma,
            zone_lower, zone_upper, cvd, bars,
        )
        seq["momentum_snapshot"] = mom
        if mom["confirmed"]:
            _transition(
                seq, SC_ENTRY_WINDOW, now,
                momentum_confirmed_at=now,
                entry_window_opened_at=now,
                _confirmation_price=bar_close,
            )
            state = SC_ENTRY_WINDOW

    # ENTRY_WINDOW → SHADOW_READY (primary only) or EXPIRED
    if state == SC_ENTRY_WINDOW:
        ew = _classify_entry_window(seq, last_bar, atr, now)
        seq["entry_window_snapshot"] = ew
        if ew["label"] in (EW_EXPIRED, EW_CHASING):
            _expire(seq, f"entry window {ew['label'].lower()}", now)
        elif ew["label"] == EW_AVAILABLE and seq.get("is_primary"):
            plan = _build_shadow_plan(seq, last_bar, atr)
            _transition(seq, SC_SHADOW_READY, now, shadow_ready_at=now, shadow_plan=plan)


# ─────────────────────────────────────────────────────────────────────────────
# Core state machine: IFVG_REVERSAL
# ─────────────────────────────────────────────────────────────────────────────
def _advance_reversal(
    seq: Dict,
    zone: Optional[Dict],
    last_bar: Dict,
    atr: float,
    vol_sma: float,
    alert_history: List[Dict],
    cvd: Optional[Dict],
    bars: List[Dict],
    now: datetime,
) -> None:
    state      = seq["current_state"]
    direction  = seq["direction"]
    inst       = seq["instrument"]
    bar_close  = _safe_float(last_bar.get("close")) or 0.0
    zone_lower = seq["zone_lower"]
    zone_upper = seq["zone_upper"]

    # INVERTED → RETEST_PENDING: auto
    if state == SC_INVERTED:
        _transition(seq, SC_RETEST_PENDING, now)
        state = SC_RETEST_PENDING

    # RETEST_PENDING → RETESTED: zone hit retest in fvg_engine
    if state == SC_RETEST_PENDING:
        if zone and zone.get("status") == "RETESTED":
            retest_ts = _parse_ts(zone.get("updated_at")) or now
            _transition(seq, SC_RETESTED, now, retest_at=retest_ts)
            state = SC_RETESTED
        elif zone and zone.get("status") in ("FAILED", "EXPIRED"):
            _invalidate(seq, f"IFVG zone {zone.get('status','').lower()} before retest", now)
            return

    # RETESTED → HOLD_PENDING: advance immediately
    if state == SC_RETESTED:
        _transition(seq, SC_HOLD_PENDING, now)
        state = SC_HOLD_PENDING

    # HOLD_PENDING → HOLD_CONFIRMED: price closed back outside the zone
    # (fvg_engine marks IFVG zones RETESTED=terminal; hold is detected from bar data)
    if state == SC_HOLD_PENDING:
        if direction == "BULLISH":
            # Bullish IFVG: price dropped to zone → hold = close rallies back above upper
            if bar_close > zone_upper:
                _transition(seq, SC_HOLD_CONFIRMED, now)
                state = SC_HOLD_CONFIRMED
        else:
            # Bearish IFVG: price rallied to zone → hold = close drops back below lower
            if bar_close < zone_lower:
                _transition(seq, SC_HOLD_CONFIRMED, now)
                state = SC_HOLD_CONFIRMED

    # HOLD_CONFIRMED → STRUCTURE_PENDING: auto
    if state == SC_HOLD_CONFIRMED:
        _transition(seq, SC_STRUCTURE_PENDING, now)
        state = SC_STRUCTURE_PENDING

    # STRUCTURE_PENDING → MOMENTUM_PENDING: post-retest structure required
    if state == SC_STRUCTURE_PENDING:
        retest_ts = _parse_ts(seq.get("retest_at"))
        struct_events = _struct_events_after(alert_history, inst, direction, retest_ts)
        if struct_events:
            _transition(seq, SC_MOMENTUM_PENDING, now, structure_confirmed_at=now)
            state = SC_MOMENTUM_PENDING
        else:
            opp = _opposite_struct_events_after(alert_history, inst, direction, retest_ts)
            if len(opp) >= 2:
                _invalidate(seq, "dominant opposite structure after IFVG retest", now)
                return

    # MOMENTUM_PENDING → ENTRY_WINDOW
    if state == SC_MOMENTUM_PENDING:
        mom = _check_momentum(
            direction, last_bar, atr, vol_sma,
            zone_lower, zone_upper, cvd, bars,
        )
        seq["momentum_snapshot"] = mom
        if mom["confirmed"]:
            _transition(
                seq, SC_ENTRY_WINDOW, now,
                momentum_confirmed_at=now,
                entry_window_opened_at=now,
                _confirmation_price=bar_close,
            )
            state = SC_ENTRY_WINDOW

    # ENTRY_WINDOW → SHADOW_READY or EXPIRED
    if state == SC_ENTRY_WINDOW:
        ew = _classify_entry_window(seq, last_bar, atr, now)
        seq["entry_window_snapshot"] = ew
        if ew["label"] in (EW_EXPIRED, EW_CHASING):
            _expire(seq, f"entry window {ew['label'].lower()}", now)
        elif ew["label"] == EW_AVAILABLE and seq.get("is_primary"):
            plan = _build_shadow_plan(seq, last_bar, atr)
            _transition(seq, SC_SHADOW_READY, now, shadow_ready_at=now, shadow_plan=plan)


# ─────────────────────────────────────────────────────────────────────────────
# Primary election (Part 10)
# ─────────────────────────────────────────────────────────────────────────────
def _elect_primaries(seqs: List[Dict]) -> None:
    """Mark is_primary for the highest-ranked active sequence per direction.
    Only one PRIMARY per direction; secondary sequences remain tracked."""
    by_dir: Dict[str, List[Dict]] = {}
    for seq in seqs:
        if seq["current_state"] in TERMINAL_SEQ_STATES:
            seq["is_primary"] = False
            continue
        by_dir.setdefault(seq["direction"], []).append(seq)

    for direction, candidates in by_dir.items():
        # Primary = most-advanced state, then highest zone rank_score
        candidates.sort(
            key=lambda s: (
                _STATE_ORDER.get(s["current_state"], -1),
                s.get("rank_score", 0.0),
            ),
            reverse=True,
        )
        for i, seq in enumerate(candidates):
            seq["is_primary"] = (i == 0)
            seq["_dirty"] = True


# ─────────────────────────────────────────────────────────────────────────────
# DB persistence
# ─────────────────────────────────────────────────────────────────────────────
def _db_upsert(seq: Dict) -> None:
    if not _FVG_SEQ_DB_READY or _db_fn is None:
        return
    try:
        import json as _json
        conn = _db_fn()
        cur  = conn.cursor()
        cur.execute(
            """
            INSERT INTO fvg_shadow_sequences (
                sequence_id, fvg_id, parent_fvg_id, instrument, direction,
                setup_family, zone_lower, zone_upper, zone_midpoint,
                started_at, current_state, last_transition_at,
                touch_at, inversion_at, retest_at,
                structure_confirmed_at, momentum_confirmed_at,
                entry_window_opened_at, shadow_ready_at,
                expired_at, invalidated_at, invalidation_reason,
                shadow_plan, momentum_snapshot, entry_window_snapshot,
                final_status, updated_at
            ) VALUES (
                %s,%s,%s,%s,%s, %s,%s,%s,%s,
                %s,%s,%s, %s,%s,%s, %s,%s, %s,%s,
                %s,%s,%s, %s,%s,%s, %s, now()
            )
            ON CONFLICT (sequence_id) DO UPDATE SET
                current_state           = EXCLUDED.current_state,
                last_transition_at      = EXCLUDED.last_transition_at,
                touch_at                = EXCLUDED.touch_at,
                inversion_at            = EXCLUDED.inversion_at,
                retest_at               = EXCLUDED.retest_at,
                structure_confirmed_at  = EXCLUDED.structure_confirmed_at,
                momentum_confirmed_at   = EXCLUDED.momentum_confirmed_at,
                entry_window_opened_at  = EXCLUDED.entry_window_opened_at,
                shadow_ready_at         = EXCLUDED.shadow_ready_at,
                expired_at              = EXCLUDED.expired_at,
                invalidated_at          = EXCLUDED.invalidated_at,
                invalidation_reason     = EXCLUDED.invalidation_reason,
                shadow_plan             = EXCLUDED.shadow_plan,
                momentum_snapshot       = EXCLUDED.momentum_snapshot,
                entry_window_snapshot   = EXCLUDED.entry_window_snapshot,
                final_status            = EXCLUDED.final_status,
                updated_at              = now()
            """,
            (
                seq["sequence_id"],
                seq["fvg_id"],
                seq.get("parent_fvg_id"),
                seq["instrument"],
                seq["direction"],
                seq["setup_family"],
                seq["zone_lower"],
                seq["zone_upper"],
                seq["zone_midpoint"],
                seq.get("started_at"),
                seq["current_state"],
                seq.get("last_transition_at"),
                seq.get("touch_at"),
                seq.get("inversion_at"),
                seq.get("retest_at"),
                seq.get("structure_confirmed_at"),
                seq.get("momentum_confirmed_at"),
                seq.get("entry_window_opened_at"),
                seq.get("shadow_ready_at"),
                seq.get("expired_at"),
                seq.get("invalidated_at"),
                seq.get("invalidation_reason"),
                _json.dumps(seq["shadow_plan"]) if seq.get("shadow_plan") else None,
                _json.dumps(seq["momentum_snapshot"]) if seq.get("momentum_snapshot") else None,
                _json.dumps(seq["entry_window_snapshot"]) if seq.get("entry_window_snapshot") else None,
                seq["current_state"] if seq["current_state"] in TERMINAL_SEQ_STATES else None,
            ),
        )
        conn.commit()
        cur.close()
    except Exception as exc:
        logger.debug("fvg_shadow_sequences upsert error: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Public API — process_bar_close
# ─────────────────────────────────────────────────────────────────────────────
def process_bar_close(
    inst: str,
    bars: List[Dict],
    zones: List[Dict],
    cvd: Optional[Dict] = None,
    alert_history: Optional[List[Dict]] = None,
) -> None:
    """Advance all sequences for this instrument on each 1m bar close.

    SHADOW/DISPLAY-ONLY — never touches production gate, scoring, or execution.

    Args:
        inst:          Canonical instrument string (e.g. "MNQ").
        bars:          All known 1m bars for this instrument (from DATABENTO_BARS_BY_INST).
        zones:         All FVG zones (include_terminal=True) from fvg_engine.get_zones().
        cvd:           CVD_BY_TICKER entry for this instrument (optional).
        alert_history: Snapshot of ALERT_HISTORY deque (optional).
    """
    if not bars:
        return
    alert_history = alert_history or []

    try:
        with _SEQ_LOCK:
            _process_locked(inst, bars, zones, cvd, alert_history)
    except Exception as exc:
        logger.debug("FVG SEQ process_bar_close(%s): %s", inst, exc, exc_info=True)


def _process_locked(
    inst: str,
    bars: List[Dict],
    zones: List[Dict],
    cvd: Optional[Dict],
    alert_history: List[Dict],
) -> None:
    now      = _now_utc()
    last_bar = bars[-1]
    atr      = _compute_atr(bars)
    vol_sma  = _volume_sma(bars)

    # Zone lookup by fvg_id
    zone_by_id: Dict[str, Dict] = {z["fvg_id"]: z for z in zones}

    # Existing sequence lookup by fvg_id
    current_seqs = SEQUENCES_BY_INST.setdefault(inst, [])
    existing_fvg_ids: set = {s["fvg_id"] for s in current_seqs}

    # ── Create sequences for newly seen zones ─────────────────────────────────
    for zone in zones:
        fvg_id = zone["fvg_id"]
        if fvg_id in existing_fvg_ids:
            # Refresh rank_score from current zone snapshot
            for s in current_seqs:
                if s["fvg_id"] == fvg_id:
                    s["rank_score"] = zone.get("rank_score", s.get("rank_score", 0.0))
            continue

        # Determine family and direction
        parent_fvg_id = zone.get("parent_fvg_id")
        if parent_fvg_id:
            family    = SF_REVERSAL
            direction = zone.get("ifvg_direction") or "BULLISH"
        else:
            family    = SF_CONTINUATION
            direction = zone.get("direction") or "BULLISH"

        initial_state = SC_INVERTED if parent_fvg_id else SC_RETURN_PENDING

        seq: Dict = {
            "sequence_id":            str(uuid.uuid4()),
            "fvg_id":                 fvg_id,
            "parent_fvg_id":          parent_fvg_id,
            "instrument":             inst,
            "direction":              direction,
            "setup_family":           family,
            "zone_lower":             zone["lower"],
            "zone_upper":             zone["upper"],
            "zone_midpoint":          zone["midpoint"],
            "started_at":             now,
            "current_state":          initial_state,
            "last_transition_at":     now,
            "touch_at":               _parse_ts(zone.get("first_touch_at")),
            "inversion_at":           _parse_ts(zone.get("inverted_at")),
            "retest_at":              None,
            "structure_confirmed_at": None,
            "momentum_confirmed_at":  None,
            "entry_window_opened_at": None,
            "shadow_ready_at":        None,
            "expired_at":             None,
            "invalidated_at":         None,
            "invalidation_reason":    None,
            "momentum_snapshot":      None,
            "entry_window_snapshot":  None,
            "shadow_plan":            None,
            "is_primary":             False,
            "rank_score":             zone.get("rank_score", 0.0),
            "_dirty":                 True,
            "_confirmation_price":    None,
        }
        current_seqs.append(seq)
        existing_fvg_ids.add(fvg_id)

    # ── Advance each non-terminal sequence ────────────────────────────────────
    for seq in list(current_seqs):
        if seq["current_state"] in TERMINAL_SEQ_STATES:
            continue

        zone = zone_by_id.get(seq["fvg_id"])

        # Expiry gate
        reason = _should_expire(seq, zone, bars, atr, now)
        if reason:
            _expire(seq, reason, now)
            continue

        if seq["setup_family"] == SF_CONTINUATION:
            _advance_continuation(
                seq, zone, last_bar, atr, vol_sma, alert_history, cvd, bars, now,
            )
        else:
            _advance_reversal(
                seq, zone, last_bar, atr, vol_sma, alert_history, cvd, bars, now,
            )

    # ── Elect primaries ────────────────────────────────────────────────────────
    _elect_primaries(current_seqs)

    # ── Flush dirty sequences to DB ───────────────────────────────────────────
    for seq in current_seqs:
        if seq.pop("_dirty", False):
            _db_upsert(seq)

    # ── Trim old terminal sequences (keep most-recent 20) ────────────────────
    non_terminal = [s for s in current_seqs if s["current_state"] not in TERMINAL_SEQ_STATES]
    terminal     = [s for s in current_seqs if s["current_state"] in TERMINAL_SEQ_STATES]
    if len(terminal) > 20:
        terminal.sort(
            key=lambda s: _parse_ts(s.get("last_transition_at")) or now,
            reverse=True,
        )
        terminal = terminal[:20]
    SEQUENCES_BY_INST[inst] = non_terminal + terminal


# ─────────────────────────────────────────────────────────────────────────────
# Serialisation helper
# ─────────────────────────────────────────────────────────────────────────────
def _serialise(seq: Dict) -> Dict:
    """Return a public-safe copy with datetime → ISO and explain-why added."""
    out: Dict = {}
    for k, v in seq.items():
        if k.startswith("_"):
            continue
        out[k] = v.isoformat() if isinstance(v, datetime) else v
    out["explain_why"]         = _build_explain_why(seq)
    out["next_required_event"] = _next_required_event(seq)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Public read API
# ─────────────────────────────────────────────────────────────────────────────
def get_sequences(inst: str, include_terminal: bool = False) -> List[Dict]:
    """Return serialised copies of sequences for inst."""
    with _SEQ_LOCK:
        seqs = list(SEQUENCES_BY_INST.get(inst, []))
    result: List[Dict] = []
    for seq in seqs:
        if not include_terminal and seq["current_state"] in TERMINAL_SEQ_STATES:
            continue
        result.append(_serialise(seq))
    return result


def get_primary_sequences(inst: str) -> Dict[str, Optional[Dict]]:
    """Return the primary sequence per direction (None if none active)."""
    with _SEQ_LOCK:
        seqs = list(SEQUENCES_BY_INST.get(inst, []))
    result: Dict[str, Optional[Dict]] = {"BULLISH": None, "BEARISH": None}
    for seq in seqs:
        if seq.get("is_primary") and seq["current_state"] not in TERMINAL_SEQ_STATES:
            d = seq["direction"]
            if d in result:
                result[d] = _serialise(seq)
    return result


def get_summary(inst: str) -> Dict:
    """Compact per-instrument summary for full_analysis / FVGScannerPanel."""
    with _SEQ_LOCK:
        seqs = list(SEQUENCES_BY_INST.get(inst, []))

    active       = [s for s in seqs if s["current_state"] not in TERMINAL_SEQ_STATES]
    shadow_ready = [s for s in seqs if s["current_state"] == SC_SHADOW_READY]

    primary_bull = next(
        (s for s in active if s.get("is_primary") and s["direction"] == "BULLISH"), None
    )
    primary_bear = next(
        (s for s in active if s.get("is_primary") and s["direction"] == "BEARISH"), None
    )

    return {
        "instrument":         inst,
        "active_count":       len(active),
        "shadow_ready_count": len(shadow_ready),
        "primary_bullish":    _serialise(primary_bull) if primary_bull else None,
        "primary_bearish":    _serialise(primary_bear) if primary_bear else None,
    }


def get_all_summary() -> Dict[str, Any]:
    """Return summary for all instruments (for full_analysis seam)."""
    with _SEQ_LOCK:
        insts = list(SEQUENCES_BY_INST.keys())
    return {inst: get_summary(inst) for inst in insts}


def get_chart_data(inst: str) -> List[Dict]:
    """Return sequence overlay data for chart endpoint (zone bounds + key events)."""
    with _SEQ_LOCK:
        seqs = list(SEQUENCES_BY_INST.get(inst, []))
    result: List[Dict] = []
    for seq in seqs:
        result.append({
            "sequence_id":            seq["sequence_id"],
            "fvg_id":                 seq["fvg_id"],
            "direction":              seq["direction"],
            "setup_family":           seq["setup_family"],
            "zone_lower":             seq["zone_lower"],
            "zone_upper":             seq["zone_upper"],
            "current_state":          seq["current_state"],
            "is_primary":             seq.get("is_primary", False),
            "touch_at":               _dt_to_iso(seq.get("touch_at")),
            "inversion_at":           _dt_to_iso(seq.get("inversion_at")),
            "retest_at":              _dt_to_iso(seq.get("retest_at")),
            "entry_window_opened_at": _dt_to_iso(seq.get("entry_window_opened_at")),
            "shadow_ready_at":        _dt_to_iso(seq.get("shadow_ready_at")),
        })
    return result


def reset_all() -> None:
    """Clear all in-memory sequences. Test/admin only."""
    global SEQUENCES_BY_INST
    with _SEQ_LOCK:
        SEQUENCES_BY_INST = {}
