"""TradeZella → Internal Snapshot Matching Engine — PURE module.

Matches a single imported TradeZella trade row against a list of internal
trade snapshot candidates (from internal_trade_snapshots).  No imports from
app.py, no DB access, all logic fail-open.

Public API
----------
    match_tradezella_trade(tz_row, snapshot_candidates) -> MatchResult

MatchResult fields
------------------
    confidence  : str  — MATCHED_EXACT | MATCHED_HIGH_CONFIDENCE |
                          MATCHED_LOW_CONFIDENCE | AMBIGUOUS | UNMATCHED
    method      : str  — human-readable name of the matching tier that fired
    snapshot_id : str | None — internal_trade_snapshots.id (UUID) of best match
    candidate_count : int — how many candidates were evaluated
    notes       : str  — comma-separated diagnostic notes

Algorithm (five priority tiers)
--------------------------------
1. Exact broker order ID — tz_row.broker_order_id matches snapshot.broker_order_id
2. Execution fingerprint — tz_row.execution_fingerprint == snapshot.execution_fingerprint
3. Account + instrument + direction + quantity within ±5-min entry window
4. Account + instrument + direction + entry price within ±5-min entry window
5. UNMATCHED (no tier matched at all)

AMBIGUOUS is returned when ≥ 2 candidates pass the SAME tier.
No fuzzy strategy-name matching is performed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

# ── Public constants ────────────────────────────────────────────────────────

CONFIDENCE_EXACT              = "MATCHED_EXACT"
CONFIDENCE_HIGH               = "MATCHED_HIGH_CONFIDENCE"
CONFIDENCE_LOW                = "MATCHED_LOW_CONFIDENCE"
CONFIDENCE_AMBIGUOUS          = "AMBIGUOUS"
CONFIDENCE_UNMATCHED          = "UNMATCHED"

STRATEGY_SOURCE_SYSTEM        = "SYSTEM"
STRATEGY_SOURCE_MANUAL        = "MANUAL"
STRATEGY_SOURCE_IMPORTED      = "IMPORTED"
STRATEGY_SOURCE_UNMATCHED     = "UNMATCHED"

LEARNING_STATUS_ELIGIBLE      = "ELIGIBLE"
LEARNING_STATUS_REVIEW_REQUIRED = "REVIEW_REQUIRED"
LEARNING_STATUS_INELIGIBLE    = "INELIGIBLE"

# Entry-time window for time-based matching (seconds)
_MATCH_WINDOW_S = 5 * 60          # ±5 minutes
# Window used to detect external-manual trades (no candidate within 30 min)
_EXTERNAL_WINDOW_S = 30 * 60      # ±30 minutes

# ── Data class ──────────────────────────────────────────────────────────────

@dataclass
class MatchResult:
    confidence:      str
    method:          str
    snapshot_id:     Optional[str]      = None
    candidate_count: int                = 0
    notes:           str                = ""
    # Snapshot-derived fields (populated when confidence is EXACT or HIGH)
    snap_strategy_key:    Optional[str]   = None
    snap_strategy:        Optional[str]   = None
    snap_thesis_direction:Optional[str]   = None
    snap_thesis_strength: Optional[str]   = None
    snap_thesis_alignment:Optional[str]   = None
    snap_edge_score:      Optional[float] = None
    snap_grade:           Optional[str]   = None
    snap_planned_entry:   Optional[float] = None
    snap_planned_stop:    Optional[float] = None
    snap_planned_risk:    Optional[float] = None
    snap_planned_targets: Optional[dict]  = None


# ── Helpers ─────────────────────────────────────────────────────────────────

def _str(v: Any, default: str = "") -> str:
    """Safe lowercase string."""
    try:
        return str(v).strip() if v is not None else default
    except Exception:
        return default


def _float(v: Any) -> Optional[float]:
    """Safe float — None on failure."""
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


def _dt(v: Any) -> Optional[datetime]:
    """Parse ISO datetime string (or pass through datetime) → UTC-aware datetime."""
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v
    try:
        s = str(v).strip()
        if not s:
            return None
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _norm_direction(d: Any) -> str:
    """Normalise direction to 'long' or 'short' (lowercase)."""
    s = _str(d).lower()
    if s in ("long", "buy", "b"):
        return "long"
    if s in ("short", "sell", "s"):
        return "short"
    return s


def _within_window(t1: Optional[datetime], t2: Optional[datetime], seconds: float) -> bool:
    """True iff both datetimes are non-None and |t1 - t2| ≤ seconds."""
    if t1 is None or t2 is None:
        return False
    try:
        return abs((t1 - t2).total_seconds()) <= seconds
    except Exception:
        return False


def _price_close(p1: Optional[float], p2: Optional[float], tol_pct: float = 0.002) -> bool:
    """True iff prices are within tol_pct fraction of each other (default 0.2 %)."""
    if p1 is None or p2 is None:
        return False
    try:
        avg = (abs(p1) + abs(p2)) / 2.0
        if avg == 0:
            return p1 == p2
        return abs(p1 - p2) / avg <= tol_pct
    except Exception:
        return False


def _qty_match(q1: Optional[float], q2: Optional[float]) -> bool:
    """True iff quantities are non-None and equal (within floating-point epsilon)."""
    if q1 is None or q2 is None:
        return False
    try:
        return abs(q1 - q2) < 0.5          # allow ±0.5 for partial lots
    except Exception:
        return False


def _symbol_norm(sym: Any) -> str:
    """Strip common futures suffixes and return canonical uppercase root."""
    s = _str(sym).upper()
    # Strip CME continuous-contract suffixes: MGC1!, MNQH26, MNQ1!, etc.
    import re
    s = re.sub(r'\d+[!H-Z]\d*$', '', s)   # e.g. 1!, H26, M25
    s = re.sub(r'\d+$', '', s)              # trailing digits
    return s.strip()


def _extract_snap_fields(snap: Dict[str, Any]) -> dict:
    """Pull snapshot-derived merge fields from a snapshot candidate dict."""
    return {
        "snap_strategy_key":     _str(snap.get("canonical_strategy_key")) or None,
        "snap_strategy":         _str(snap.get("strategy_display_name"))  or None,
        "snap_thesis_direction": _str(snap.get("thesis_direction"))        or None,
        "snap_thesis_strength":  _str(snap.get("thesis_strength"))         or None,
        "snap_thesis_alignment": _str(snap.get("thesis_alignment"))        or None,
        "snap_edge_score":       _float(snap.get("edge_score")),
        "snap_grade":            _str(snap.get("grade"))                   or None,
        "snap_planned_entry":    _float(snap.get("planned_entry")),
        "snap_planned_stop":     _float(snap.get("planned_stop")),
        "snap_planned_risk":     _float(snap.get("planned_risk")),
        "snap_planned_targets":  snap.get("planned_targets") if isinstance(
                                     snap.get("planned_targets"), dict) else None,
    }


# ── Main matching function ───────────────────────────────────────────────────

def match_tradezella_trade(
    tz_row: Dict[str, Any],
    snapshot_candidates: List[Dict[str, Any]],
) -> MatchResult:
    """Match one TradeZella trade row against a list of snapshot candidates.

    Parameters
    ----------
    tz_row:
        Dict with keys from tradezella_trades:
            symbol, side, entry_time, exit_time, entry_price,
            quantity, broker_order_id (optional), execution_fingerprint (optional)
    snapshot_candidates:
        List of dicts from internal_trade_snapshots (prefiltered to ±15 min
        around entry_time for the same instrument by the caller).

    Returns
    -------
    MatchResult — never raises.
    """
    n = len(snapshot_candidates)

    try:
        return _match(tz_row, snapshot_candidates, n)
    except Exception as exc:
        return MatchResult(
            confidence=CONFIDENCE_UNMATCHED,
            method="error",
            candidate_count=n,
            notes=f"matching error: {exc}",
        )


def _match(tz: Dict[str, Any], snaps: List[Dict[str, Any]], n: int) -> MatchResult:
    """Internal matching logic — may raise (caller wraps in try/except)."""

    # ── Tier 1: Exact broker order ID ──────────────────────────────────────
    tz_order_id = _str(tz.get("broker_order_id")).strip()
    if tz_order_id:
        tier1 = [s for s in snaps
                 if _str(s.get("broker_order_id")).strip() == tz_order_id]
        if len(tier1) == 1:
            sf = _extract_snap_fields(tier1[0])
            return MatchResult(
                confidence=CONFIDENCE_EXACT,
                method="broker_order_id",
                snapshot_id=_str(tier1[0].get("id")) or None,
                candidate_count=n,
                notes="exact broker_order_id",
                **sf,
            )
        if len(tier1) > 1:
            return MatchResult(
                confidence=CONFIDENCE_AMBIGUOUS,
                method="broker_order_id",
                candidate_count=n,
                notes=f"ambiguous: {len(tier1)} snapshots share broker_order_id",
            )

    # ── Tier 2: Execution fingerprint ─────────────────────────────────────
    tz_fp = _str(tz.get("execution_fingerprint")).strip()
    if tz_fp:
        tier2 = [s for s in snaps
                 if _str(s.get("execution_fingerprint")).strip() == tz_fp]
        if len(tier2) == 1:
            sf = _extract_snap_fields(tier2[0])
            return MatchResult(
                confidence=CONFIDENCE_EXACT,
                method="execution_fingerprint",
                snapshot_id=_str(tier2[0].get("id")) or None,
                candidate_count=n,
                notes="exact fingerprint",
                **sf,
            )
        if len(tier2) > 1:
            return MatchResult(
                confidence=CONFIDENCE_AMBIGUOUS,
                method="execution_fingerprint",
                candidate_count=n,
                notes=f"ambiguous: {len(tier2)} snapshots share fingerprint",
            )

    # ── Common prep for time-based tiers ──────────────────────────────────
    tz_entry_dt   = _dt(tz.get("entry_time"))
    tz_dir        = _norm_direction(tz.get("side"))
    tz_qty        = _float(tz.get("quantity"))
    tz_price      = _float(tz.get("entry_price"))
    tz_sym        = _symbol_norm(tz.get("symbol"))

    def _instrument_matches(snap: Dict[str, Any]) -> bool:
        snap_inst = _symbol_norm(snap.get("instrument") or snap.get("contract") or "")
        return snap_inst == tz_sym

    def _direction_matches(snap: Dict[str, Any]) -> bool:
        return _norm_direction(snap.get("direction")) == tz_dir

    def _in_window(snap: Dict[str, Any]) -> bool:
        return _within_window(tz_entry_dt, _dt(snap.get("sent_at") or snap.get("created_at")),
                              _MATCH_WINDOW_S)

    # ── Tier 3: instrument + direction + quantity + time window ────────────
    if tz_dir and tz_qty is not None and tz_entry_dt is not None:
        tier3 = [s for s in snaps
                 if _instrument_matches(s)
                 and _direction_matches(s)
                 and _in_window(s)
                 and _qty_match(tz_qty, _float(s.get("planned_contracts")))]
        if len(tier3) == 1:
            sf = _extract_snap_fields(tier3[0])
            return MatchResult(
                confidence=CONFIDENCE_HIGH,
                method="instrument+direction+quantity+time",
                snapshot_id=_str(tier3[0].get("id")) or None,
                candidate_count=n,
                notes="matched on instrument/direction/qty within 5-min window",
                **sf,
            )
        if len(tier3) > 1:
            return MatchResult(
                confidence=CONFIDENCE_AMBIGUOUS,
                method="instrument+direction+quantity+time",
                candidate_count=n,
                notes=f"ambiguous: {len(tier3)} candidates match tier-3",
            )

    # ── Tier 4: instrument + direction + entry price + time window ─────────
    if tz_dir and tz_price is not None and tz_entry_dt is not None:
        tier4 = [s for s in snaps
                 if _instrument_matches(s)
                 and _direction_matches(s)
                 and _in_window(s)
                 and _price_close(tz_price, _float(s.get("planned_entry")))]
        if len(tier4) == 1:
            sf = _extract_snap_fields(tier4[0])
            return MatchResult(
                confidence=CONFIDENCE_HIGH,
                method="instrument+direction+entry_price+time",
                snapshot_id=_str(tier4[0].get("id")) or None,
                candidate_count=n,
                notes="matched on instrument/direction/entry-price within 5-min window",
                **sf,
            )
        if len(tier4) > 1:
            return MatchResult(
                confidence=CONFIDENCE_AMBIGUOUS,
                method="instrument+direction+entry_price+time",
                candidate_count=n,
                notes=f"ambiguous: {len(tier4)} candidates match tier-4",
            )

    # ── Tier 5: UNMATCHED ─────────────────────────────────────────────────
    parts = []
    if not tz_dir:
        parts.append("direction unknown")
    if tz_entry_dt is None:
        parts.append("no entry_time")
    if not snaps:
        parts.append("no candidates in window")
    return MatchResult(
        confidence=CONFIDENCE_UNMATCHED,
        method="none",
        candidate_count=n,
        notes=", ".join(parts) or "no tier matched",
    )


# ── External-manual detection ───────────────────────────────────────────────

def is_external_manual(
    tz_row: Dict[str, Any],
    all_snapshot_candidates: List[Dict[str, Any]],
) -> bool:
    """Return True when no system snapshot exists within ±30 min of entry_time.

    A trade that the system never fired a snapshot for is assumed to be a
    discretionary manual trade outside the bot.  FAIL-OPEN: returns False
    when entry_time is missing (we can't disprove it was a system trade).
    """
    tz_entry_dt = _dt(tz_row.get("entry_time"))
    if tz_entry_dt is None:
        return False
    for snap in all_snapshot_candidates:
        snap_dt = _dt(snap.get("sent_at") or snap.get("created_at"))
        if _within_window(tz_entry_dt, snap_dt, _EXTERNAL_WINDOW_S):
            return False
    return True


# ── Learning eligibility compute ─────────────────────────────────────────────

def compute_learning_status(match_result: MatchResult, tz_row: Dict[str, Any]) -> str:
    """Derive learning_status for a single tradezella trade after matching.

    Rules (ordered, first match wins):
    - INELIGIBLE  : match_confidence is UNMATCHED or AMBIGUOUS
    - INELIGIBLE  : is_external_manual = True and strategy_source stays IMPORTED
    - REVIEW_REQUIRED: strategy_source is UNMATCHED (no manual assignment yet)
    - REVIEW_REQUIRED: outcome/result not in (win, loss) — scratch is excluded
    - ELIGIBLE    : otherwise (operator or system has confirmed strategy + closed)

    Note: ELIGIBLE here means "eligible pending review completion". The final
    eligibility gate in journal_learning_eligibility() also requires
    review_status = REVIEWED.
    """
    conf = match_result.confidence
    if conf in (CONFIDENCE_UNMATCHED, CONFIDENCE_AMBIGUOUS):
        return LEARNING_STATUS_INELIGIBLE

    outcome = _str(tz_row.get("outcome") or tz_row.get("result")).lower()
    if outcome not in ("win", "loss"):
        return LEARNING_STATUS_INELIGIBLE

    # Strategy confirmed by system or manual assignment?
    strat_src = (tz_row.get("strategy_source") or STRATEGY_SOURCE_UNMATCHED)
    if strat_src in (STRATEGY_SOURCE_UNMATCHED, STRATEGY_SOURCE_IMPORTED, ""):
        return LEARNING_STATUS_REVIEW_REQUIRED

    return LEARNING_STATUS_ELIGIBLE
