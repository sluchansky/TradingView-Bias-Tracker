"""
Phase 3 — Canonical Decision Contract (Shadow Observer)
========================================================
Creates one authoritative, typed, auditable decision contract for the
AI Trading Partner lifecycle.

SHADOW MODE ONLY (Phase 3): Observes existing live behavior; never gates
broker transmission.  The canonical record is for audit / comparison /
dashboard visibility until explicitly promoted.

State ownership (per spec):
  Strategy Engine:   OBSERVING → SETUP_FORMING → EARLY → READY
  Qualification:     READY → QUALIFIED
  Risk Engine:       QUALIFIED → RISK_PENDING → RISK_APPROVED
  Execution safety:  RISK_APPROVED → EXECUTABLE
  Execution Gateway: EXECUTABLE → ENTRY_REQUESTED → ORDER_ACCEPTED | ORDER_REJECTED
  Position Manager:  ORDER_ACCEPTED → POSITION_ACTIVE → MANAGING → COMPLETED

EARLY legacy path (Phase 3 observation):
  EARLY is currently included in is_actionable() and CAN trigger auto-fire
  (half-size via risk multiplier).  This is the current intended behavior.
  The canonical contract maps EARLY as pre-READY and flags EARLY → EXECUTABLE
  as a legacy compatibility path.  It does NOT change live behavior.
  Migration requires explicit operator approval.

No execution code is in this file.  No thresholds, risk limits, or broker
behavior is changed here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Module version ────────────────────────────────────────────────────────────

DC_VERSION = "3.0.0"
SHADOW_MODE_ENV_KEY = "DECISION_CONTRACT_SHADOW_MODE"

# ── Canonical decision states ─────────────────────────────────────────────────


class DecisionState:
    """Canonical lifecycle states.  No value is > 20 chars."""
    # Normal forward path
    OBSERVING        = "OBSERVING"         # watching market; no live setup
    SETUP_FORMING    = "SETUP_FORMING"     # structure forming; not yet actionable
    EARLY            = "EARLY"             # intrabar pre-READY advisory signal
    READY            = "READY"             # strategy conditions satisfied
    QUALIFIED        = "QUALIFIED"         # READY + all non-risk gates passed
    RISK_PENDING     = "RISK_PENDING"      # awaiting risk reservation
    RISK_APPROVED    = "RISK_APPROVED"     # risk reserved and approved
    EXECUTABLE       = "EXECUTABLE"        # cleared for broker transmission
    ENTRY_REQUESTED  = "ENTRY_REQUESTED"   # broker order submitted
    ORDER_ACCEPTED   = "ORDER_ACCEPTED"    # broker acknowledged
    POSITION_ACTIVE  = "POSITION_ACTIVE"   # live position open
    MANAGING         = "MANAGING"          # position management active
    COMPLETED        = "COMPLETED"         # trade closed

    # WAIT / terminal / block states
    WAIT                   = "WAIT"                    # no executable setup; not a failure
    EXPIRED                = "EXPIRED"                 # entry window closed
    MISSED                 = "MISSED"                  # breakout missed (max-chase)
    BLOCKED_DATA           = "BLOCKED_DATA"            # missing/invalid data
    BLOCKED_CONFIRMATION   = "BLOCKED_CONFIRMATION"    # breakout confirmation failed
    BLOCKED_MARKET         = "BLOCKED_MARKET"          # market condition gate
    BLOCKED_RISK           = "BLOCKED_RISK"            # risk/sizing failure
    BLOCKED_PROP           = "BLOCKED_PROP"            # prop-firm rule block
    BLOCKED_DAILY_LOSS     = "BLOCKED_DAILY_LOSS"      # daily-loss cap reached
    BLOCKED_POSITION_LIMIT = "BLOCKED_POSITION_LIMIT"  # daily trade limit
    BLOCKED_DUPLICATE      = "BLOCKED_DUPLICATE"       # duplicate guard
    BLOCKED_EXECUTION_MODE = "BLOCKED_EXECUTION_MODE"  # execution mode disabled
    BLOCKED_ARM            = "BLOCKED_ARM"             # not armed
    BLOCKED_SAFETY         = "BLOCKED_SAFETY"          # safety lock active
    ORDER_REJECTED         = "ORDER_REJECTED"          # broker rejected
    CANCELLED              = "CANCELLED"               # operator cancelled

    # Manual override path
    MANUAL_REQUESTED           = "MANUAL_REQUESTED"            # manual desk order triggered
    QUALIFIED_MANUAL_OVERRIDE  = "QUALIFIED_MANUAL_OVERRIDE"   # manual path qualified

    @classmethod
    def all_states(cls) -> List[str]:
        return [v for k, v in vars(cls).items()
                if not k.startswith("_") and isinstance(v, str)]


# ── Stable machine-readable reason codes ─────────────────────────────────────


class ReasonCode:
    NO_STRUCTURE       = "NO_STRUCTURE"
    VWAP_CONFLICT      = "VWAP_CONFLICT"
    CVD_CONFLICT       = "CVD_CONFLICT"
    ZONE_MISSING       = "ZONE_MISSING"
    ZONE_MITIGATED     = "ZONE_MITIGATED"
    VOLATILITY         = "VOLATILITY"
    MAX_CHASE          = "MAX_CHASE"
    SESSION_CLOSED     = "SESSION_CLOSED"
    STALE_DATA         = "STALE_DATA"
    RISK_LIMIT         = "RISK_LIMIT"
    DAILY_LOSS         = "DAILY_LOSS"
    PROP_LIMIT         = "PROP_LIMIT"
    POSITION_LIMIT     = "POSITION_LIMIT"
    DUPLICATE          = "DUPLICATE"
    EXECUTION_DISABLED = "EXECUTION_DISABLED"
    NOT_ARMED          = "NOT_ARMED"
    SAFETY_LOCK        = "SAFETY_LOCK"
    BROKER_REJECTED    = "BROKER_REJECTED"
    NO_SETUP           = "NO_SETUP"
    DATA_UNAVAILABLE   = "DATA_UNAVAILABLE"
    RANGE_INVALID      = "RANGE_INVALID"
    ENTRY_WINDOW_CLOSED= "ENTRY_WINDOW_CLOSED"
    MANUAL_OVERRIDE    = "MANUAL_OVERRIDE"
    UNKNOWN            = "UNKNOWN"


# ── OrbEngine state → Canonical state mapping ────────────────────────────────

ORB_STATE_MAP: Dict[str, Tuple[str, str]] = {
    # (orb_state) → (canonical_state, reason_code)
    "DISABLED":                (DecisionState.OBSERVING, ReasonCode.UNKNOWN),
    "WAITING_FOR_SESSION":     (DecisionState.OBSERVING, ReasonCode.SESSION_CLOSED),
    "WAITING_FOR_RANGE":       (DecisionState.OBSERVING, ReasonCode.SESSION_CLOSED),
    "BUILDING_RANGE":          (DecisionState.OBSERVING, ReasonCode.UNKNOWN),
    "RANGE_LOCKED":            (DecisionState.OBSERVING, ReasonCode.UNKNOWN),
    "WATCHING_BREAKOUT":       (DecisionState.SETUP_FORMING, ReasonCode.UNKNOWN),
    "BREAKOUT_DETECTED":       (DecisionState.EARLY, ReasonCode.UNKNOWN),        # CLOSE_AND_RETEST first break
    "CONFIRMATION_PENDING":    (DecisionState.EARLY, ReasonCode.UNKNOWN),        # retest in progress
    "QUALIFIED":               (DecisionState.QUALIFIED, ReasonCode.UNKNOWN),
    "POSITION_ACTIVE":         (DecisionState.POSITION_ACTIVE, ReasonCode.UNKNOWN),
    "POSITION_MANAGING":       (DecisionState.MANAGING, ReasonCode.UNKNOWN),
    "COMPLETED":               (DecisionState.COMPLETED, ReasonCode.UNKNOWN),
    "EXPIRED":                 (DecisionState.EXPIRED, ReasonCode.ENTRY_WINDOW_CLOSED),
    "BREAKOUT_MISSED":         (DecisionState.MISSED, ReasonCode.MAX_CHASE),
    "BLOCKED_BY_DATA":         (DecisionState.BLOCKED_DATA, ReasonCode.DATA_UNAVAILABLE),
    "BLOCKED_BY_RANGE_WIDTH":  (DecisionState.BLOCKED_CONFIRMATION, ReasonCode.RANGE_INVALID),
    "BLOCKED_BY_CONFIRMATION": (DecisionState.BLOCKED_CONFIRMATION, ReasonCode.UNKNOWN),
    "BLOCKED_BY_INSTRUMENT_RISK":  (DecisionState.BLOCKED_RISK, ReasonCode.RISK_LIMIT),
    "BLOCKED_BY_GROUP_RISK":       (DecisionState.BLOCKED_RISK, ReasonCode.RISK_LIMIT),
    "BLOCKED_BY_PORTFOLIO_RISK":   (DecisionState.BLOCKED_RISK, ReasonCode.RISK_LIMIT),
    "BLOCKED_BY_POSITION_LIMIT":   (DecisionState.BLOCKED_POSITION_LIMIT, ReasonCode.POSITION_LIMIT),
    "BLOCKED_BY_DAILY_LOSS":       (DecisionState.BLOCKED_DAILY_LOSS, ReasonCode.DAILY_LOSS),
    "BLOCKED_BY_PROP_RULE":        (DecisionState.BLOCKED_PROP, ReasonCode.PROP_LIMIT),
    "BLOCKED_BY_DUPLICATE_GUARD":  (DecisionState.BLOCKED_DUPLICATE, ReasonCode.DUPLICATE),
    "BLOCKED_BY_EXECUTION_MODE":   (DecisionState.BLOCKED_EXECUTION_MODE, ReasonCode.EXECUTION_DISABLED),
    "BLOCKED_BY_ARM_STATE":        (DecisionState.BLOCKED_ARM, ReasonCode.NOT_ARMED),
    "BLOCKED_BY_SAFETY_LOCK":      (DecisionState.BLOCKED_SAFETY, ReasonCode.SAFETY_LOCK),
    "BLOCKED_BY_MAXIMUM_CHASE":    (DecisionState.MISSED, ReasonCode.MAX_CHASE),  # event name, not state
    "ENTRY_REQUESTED":         (DecisionState.ENTRY_REQUESTED, ReasonCode.UNKNOWN),
    "ORDER_ACCEPTED":          (DecisionState.ORDER_ACCEPTED, ReasonCode.UNKNOWN),
    "ORDER_REJECTED":          (DecisionState.ORDER_REJECTED, ReasonCode.BROKER_REJECTED),
    "DATA_INVALID":            (DecisionState.BLOCKED_DATA, ReasonCode.DATA_UNAVAILABLE),
    "RECOVERY_REQUIRED":       (DecisionState.BLOCKED_DATA, ReasonCode.DATA_UNAVAILABLE),
}


# ── Legal state transitions ───────────────────────────────────────────────────

def _build_legal_transitions() -> frozenset:
    DS = DecisionState
    _BLOCKED = [
        DS.BLOCKED_DATA, DS.BLOCKED_CONFIRMATION, DS.BLOCKED_MARKET,
        DS.BLOCKED_RISK, DS.BLOCKED_PROP, DS.BLOCKED_DAILY_LOSS,
        DS.BLOCKED_POSITION_LIMIT, DS.BLOCKED_DUPLICATE,
        DS.BLOCKED_EXECUTION_MODE, DS.BLOCKED_ARM, DS.BLOCKED_SAFETY,
        DS.MISSED, DS.EXPIRED,
    ]
    _PRE_EXEC = [
        DS.OBSERVING, DS.SETUP_FORMING, DS.EARLY, DS.READY, DS.QUALIFIED,
        DS.RISK_PENDING, DS.RISK_APPROVED, DS.EXECUTABLE,
        DS.MANUAL_REQUESTED, DS.QUALIFIED_MANUAL_OVERRIDE,
    ]
    _RESETTABLE = [
        DS.WAIT, DS.OBSERVING, DS.SETUP_FORMING, DS.EARLY, DS.READY,
        DS.QUALIFIED, DS.RISK_PENDING, DS.RISK_APPROVED,
        *_BLOCKED,
    ]
    pairs = {
        # ── Happy forward path ────────────────────────────────────────────
        (DS.OBSERVING,        DS.SETUP_FORMING),
        (DS.SETUP_FORMING,    DS.EARLY),
        (DS.SETUP_FORMING,    DS.READY),
        (DS.EARLY,            DS.READY),
        (DS.SETUP_FORMING,    DS.QUALIFIED),   # ORB: WATCHING_BREAKOUT → QUALIFIED (TOUCH/CLOSE_OUTSIDE)
        (DS.EARLY,            DS.QUALIFIED),   # ORB: BREAKOUT_DETECTED → QUALIFIED (retest holds)
        (DS.READY,            DS.QUALIFIED),
        (DS.QUALIFIED,        DS.RISK_PENDING),
        (DS.RISK_PENDING,     DS.RISK_APPROVED),
        (DS.QUALIFIED,        DS.RISK_APPROVED),   # shadow compression
        (DS.RISK_APPROVED,    DS.EXECUTABLE),
        (DS.READY,            DS.EXECUTABLE),      # shadow compression (gates compressed)
        (DS.QUALIFIED,        DS.EXECUTABLE),      # shadow compression
        (DS.EXECUTABLE,       DS.ENTRY_REQUESTED),
        (DS.ENTRY_REQUESTED,  DS.ORDER_ACCEPTED),
        (DS.ENTRY_REQUESTED,  DS.ORDER_REJECTED),
        (DS.ORDER_ACCEPTED,   DS.POSITION_ACTIVE),
        (DS.POSITION_ACTIVE,  DS.MANAGING),
        (DS.MANAGING,         DS.COMPLETED),
        (DS.POSITION_ACTIVE,  DS.COMPLETED),
        # ── EARLY legacy live-fire path (flagged, not promoted) ───────────
        # EARLY → EXECUTABLE is legal as a legacy compatibility path.
        # It MUST remain flagged as a legacy path; canonical requires READY first.
        (DS.EARLY,            DS.EXECUTABLE),      # LEGACY COMPAT: EARLY half-size auto-fire
        # ── Decay / reset ────────────────────────────────────────────────
        (DS.EARLY,            DS.OBSERVING),
        (DS.EARLY,            DS.WAIT),
        (DS.SETUP_FORMING,    DS.OBSERVING),
        (DS.SETUP_FORMING,    DS.WAIT),
        (DS.READY,            DS.WAIT),
        (DS.READY,            DS.OBSERVING),
        (DS.QUALIFIED,        DS.WAIT),
        (DS.QUALIFIED,        DS.OBSERVING),
        (DS.RISK_PENDING,     DS.WAIT),
        (DS.RISK_APPROVED,    DS.WAIT),
        (DS.EXECUTABLE,       DS.WAIT),
        (DS.EXECUTABLE,       DS.OBSERVING),
        (DS.WAIT,             DS.OBSERVING),
        # A full evaluation can skip transient SETUP_FORMING/EARLY states and
        # promote a previously waiting instrument directly to a confirmed setup.
        (DS.WAIT,             DS.READY),
        # ── OBSERVING → WAIT: confirmed legitimate production path ────────────
        # After OrbEngine resets DC to OBSERVING (SESSION_CLOSED), the next
        # full_analysis cycle legitimately evaluates the instrument as WAIT
        # (e.g. VWAP not confirmed, no structure).  Absent from LEGAL_TRANSITIONS
        # pre-Phase-3.1, causing repeated ILLEGAL TRANSITION warnings and dropped
        # transition history.  Reason code (VWAP_CONFLICT / NO_STRUCTURE / etc.)
        # is real and preserved — do not substitute UNKNOWN.
        (DS.OBSERVING,        DS.WAIT),
        # ── OBSERVING → EARLY and WAIT → EARLY: confirmed legitimate paths ──
        # On restart the DC boots in OBSERVING; the first full_analysis may score
        # EARLY (60–69) before enough signals have arrived to reach READY. Similarly
        # WAIT can transition to EARLY when partial conditions improve (e.g. CVD
        # flips or VWAP confirmed but structure still pending). The canonical happy
        # path is OBSERVING → SETUP_FORMING → EARLY, but in practice SETUP_FORMING
        # is transient and often skipped in a single evaluation tick, so the direct
        # hop is a confirmed production behaviour, not an error.
        (DS.OBSERVING,        DS.EARLY),
        (DS.WAIT,             DS.EARLY),
        (DS.ORDER_REJECTED,   DS.EXECUTABLE),
        (DS.ORDER_REJECTED,   DS.CANCELLED),
        (DS.CANCELLED,        DS.OBSERVING),
        # ── Scalp / non-ORB direct fire — legitimate production paths ────────
        # SCALP auto-fire goes READY → ENTRY_REQUESTED (no QUALIFIED/EXECUTABLE
        # step because scalp mode compresses those gates into one full_analysis call).
        # EARLY half-size fire is the same: EARLY → ENTRY_REQUESTED.
        # These are confirmed live behaviours, not test conveniences.
        (DS.READY,            DS.ENTRY_REQUESTED),
        (DS.EARLY,            DS.ENTRY_REQUESTED),
        # Manual override from any signal state (operator hits ENTER while setup live)
        (DS.READY,            DS.MANUAL_REQUESTED),
        (DS.EARLY,            DS.MANUAL_REQUESTED),
        # Manual trade result paths (manual order bypasses ENTRY_REQUESTED → ORDER_* flow)
        (DS.MANUAL_REQUESTED, DS.ORDER_ACCEPTED),
        (DS.MANUAL_REQUESTED, DS.ORDER_REJECTED),
        # ── Manual override path ─────────────────────────────────────────
        (DS.OBSERVING,        DS.MANUAL_REQUESTED),
        (DS.WAIT,             DS.MANUAL_REQUESTED),
        (DS.MANUAL_REQUESTED, DS.QUALIFIED_MANUAL_OVERRIDE),
        (DS.QUALIFIED_MANUAL_OVERRIDE, DS.RISK_PENDING),
        (DS.QUALIFIED_MANUAL_OVERRIDE, DS.RISK_APPROVED),
        (DS.QUALIFIED_MANUAL_OVERRIDE, DS.EXECUTABLE),
        (DS.QUALIFIED_MANUAL_OVERRIDE, DS.BLOCKED_RISK),
        (DS.QUALIFIED_MANUAL_OVERRIDE, DS.BLOCKED_PROP),
        (DS.QUALIFIED_MANUAL_OVERRIDE, DS.BLOCKED_DAILY_LOSS),
        (DS.QUALIFIED_MANUAL_OVERRIDE, DS.BLOCKED_DUPLICATE),
        (DS.QUALIFIED_MANUAL_OVERRIDE, DS.BLOCKED_ARM),
        (DS.QUALIFIED_MANUAL_OVERRIDE, DS.BLOCKED_SAFETY),
        (DS.QUALIFIED_MANUAL_OVERRIDE, DS.BLOCKED_EXECUTION_MODE),
    }
    # Any pre-exec → any BLOCKED or terminal
    for src in _PRE_EXEC:
        for blk in _BLOCKED:
            pairs.add((src, blk))
    # Any resettable → OBSERVING (new session resets)
    for src in _RESETTABLE:
        pairs.add((src, DS.OBSERVING))
    # A non-terminal block is a snapshot of a failed prerequisite, not a
    # permanent decision.  A subsequent full-analysis cycle may legitimately
    # recover directly to a later signal state because intermediate evaluations
    # are not guaranteed to run (for example, fresh data can make
    # BLOCKED_DATA → EARLY/READY in one tick).  Keep this shadow contract in
    # lockstep with the authoritative live verdict rather than pinning records
    # in a stale block state.
    _RECOVERY_STATES = [
        DS.WAIT, DS.SETUP_FORMING, DS.EARLY, DS.READY, DS.QUALIFIED,
        DS.RISK_PENDING, DS.RISK_APPROVED, DS.EXECUTABLE,
    ]
    for src in _BLOCKED:
        for dst in _RECOVERY_STATES:
            pairs.add((src, dst))
    # Self-transitions (repeated signal, no state change)
    for s in [DS.OBSERVING, DS.WAIT, DS.SETUP_FORMING, DS.EARLY,
              DS.READY, DS.MANAGING, DS.POSITION_ACTIVE]:
        pairs.add((s, s))
    return frozenset(pairs)


LEGAL_TRANSITIONS: frozenset = _build_legal_transitions()

# States that are permanently terminal (no further transitions for this decision_id)
PERMANENTLY_TERMINAL: frozenset = frozenset({
    DecisionState.COMPLETED,
    DecisionState.CANCELLED,
})

# States that are session-terminal (can reset to OBSERVING on a new session)
SESSION_TERMINAL: frozenset = frozenset({
    DecisionState.EXPIRED,
    DecisionState.MISSED,
    DecisionState.BLOCKED_DATA,
    DecisionState.BLOCKED_CONFIRMATION,
    DecisionState.BLOCKED_MARKET,
    DecisionState.BLOCKED_RISK,
    DecisionState.BLOCKED_PROP,
    DecisionState.BLOCKED_DAILY_LOSS,
    DecisionState.BLOCKED_POSITION_LIMIT,
    DecisionState.BLOCKED_DUPLICATE,
    DecisionState.BLOCKED_EXECUTION_MODE,
    DecisionState.BLOCKED_ARM,
    DecisionState.BLOCKED_SAFETY,
    DecisionState.ORDER_REJECTED,
})


# ── Legacy verdict helpers ────────────────────────────────────────────────────

FULL_READY_VERDICTS = ("LONG READY", "SHORT READY")
EARLY_READY_VERDICTS = ("LONG EARLY READY", "SHORT EARLY READY")


def _legacy_is_actionable(verdict: str) -> bool:
    return verdict in FULL_READY_VERDICTS or verdict in EARLY_READY_VERDICTS


def _extract_direction(verdict: str) -> str:
    if "LONG" in verdict:
        return "Long"
    if "SHORT" in verdict:
        return "Short"
    return ""


# ── Decision record ───────────────────────────────────────────────────────────

@dataclass
class DecisionTransition:
    decision_id:    str
    instrument:     str
    from_state:     Optional[str]
    to_state:       str
    reason_code:    str
    reason_text:    str
    source_module:  str
    transitioned_at: str
    extra:          Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class DecisionRecord:
    decision_id:          str
    opportunity_id:       Optional[str]
    instrument:           str
    strategy:             str
    strategy_version:     str
    direction:            str

    state:                str
    previous_state:       Optional[str]
    state_changed_at:     str
    reason_code:          str
    reason_text:          str

    verdict:              Optional[str]   # legacy verdict string
    edge_score:           Optional[float]
    confidence:           Optional[float]

    market_context_ref:   Optional[str]
    canonical_state_ts:   Optional[str]

    entry:                Optional[float]
    stop:                 Optional[float]
    tp1:                  Optional[float]
    tp2:                  Optional[float]
    quantity:             Optional[float]

    risk_status:          Optional[str]
    risk_amount:          Optional[float]
    risk_r:               Optional[float]
    risk_reservation_id:  Optional[str]

    execution_mode:       Optional[str]
    execution_enabled:    Optional[bool]
    arm_required:         bool
    armed:                Optional[bool]
    safety_lock:          Optional[bool]
    prop_status:          Optional[str]

    source_module:        str
    transition_history:   List[DecisionTransition] = field(default_factory=list)

    created_at:           str = field(default_factory=lambda: _now_utc())
    updated_at:           str = field(default_factory=lambda: _now_utc())
    expires_at:           Optional[str] = None

    # Shadow parity fields
    legacy_verdict:       Optional[str] = None
    canonical_state:      Optional[str] = None
    parity_agree:         Optional[bool] = None
    parity_diff_reason:   Optional[str] = None

    def to_dict(self) -> Dict:
        d = asdict(self)
        # transition_history is in-memory only; exclude from JSON for storage
        d.pop("transition_history", None)
        return d

    def is_executable_canonical(self) -> bool:
        return self.state == DecisionState.EXECUTABLE

    def is_executable_legacy(self) -> bool:
        return _legacy_is_actionable(self.verdict or "")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decision_id(instrument: str, strategy: str, direction: str,
                 trading_date: str) -> str:
    key = f"DC|{instrument}|{strategy}|{direction}|{trading_date}"
    return hashlib.sha256(key.encode()).hexdigest()[:24]


# ── Transition validator ──────────────────────────────────────────────────────

class TransitionError(Exception):
    pass


def validate_transition(from_state: Optional[str], to_state: str,
                        decision_id: str = "") -> Tuple[bool, str]:
    """Returns (ok, error_msg).  Never raises in shadow mode.

    Invalid transitions:
    - log clearly
    - do not crash live trading
    - fail closed for new broker transmission
    - preserve the previous valid state (caller responsibility)
    """
    if from_state is None:
        return True, ""   # first transition always legal
    if (from_state, to_state) in LEGAL_TRANSITIONS:
        return True, ""
    msg = (f"ILLEGAL TRANSITION: {from_state} → {to_state} "
           f"(decision_id={decision_id})")
    return False, msg


# ── Legacy → canonical mapping ────────────────────────────────────────────────

def _map_strict_reason(strict_reason: str, gate_debug: Dict) -> Tuple[str, str]:
    """Map legacy strict_reason / gate_debug to (canonical_state, reason_code)."""
    sr = (strict_reason or "").lower()
    gd = gate_debug or {}
    vwap_ok   = gd.get("vwap_confirmed", True)
    struct_ok = gd.get("structure_confirmed", True)
    zone_ok   = gd.get("zone_active", True)
    data_ok   = gd.get("data_available", True)

    if not data_ok or "stale" in sr or "data" in sr:
        return DecisionState.BLOCKED_DATA, ReasonCode.STALE_DATA
    if not vwap_ok or "vwap" in sr:
        return DecisionState.WAIT, ReasonCode.VWAP_CONFLICT
    if not struct_ok or "structure" in sr or "no struct" in sr:
        return DecisionState.WAIT, ReasonCode.NO_STRUCTURE
    if not zone_ok or "zone" in sr and "mitiga" in sr:
        return DecisionState.WAIT, ReasonCode.ZONE_MITIGATED
    if "zone" in sr:
        return DecisionState.WAIT, ReasonCode.ZONE_MISSING
    if "cvd" in sr:
        return DecisionState.WAIT, ReasonCode.CVD_CONFLICT
    if "volatil" in sr:
        return DecisionState.WAIT, ReasonCode.VOLATILITY
    if "session" in sr or "closed" in sr or "market" in sr:
        return DecisionState.OBSERVING, ReasonCode.SESSION_CLOSED
    return DecisionState.WAIT, ReasonCode.NO_SETUP


def map_full_analysis_to_canonical(
    verdict: str,
    result:  Dict,
    arm_state: Optional[Dict] = None,
) -> Tuple[str, str, str]:
    """Map full_analysis() output to (canonical_state, reason_code, reason_text).

    This is the bridge between legacy behavior and the canonical contract.
    It does NOT change any live behavior.
    """
    if verdict in FULL_READY_VERDICTS:
        base_state  = DecisionState.READY
        reason_code = ReasonCode.UNKNOWN
        reason_text = ""
    elif verdict in EARLY_READY_VERDICTS:
        base_state  = DecisionState.EARLY
        reason_code = ReasonCode.UNKNOWN
        reason_text = "EARLY advisory (legacy half-size path)"
    elif verdict == "SETUP BUILDING":
        return DecisionState.SETUP_FORMING, ReasonCode.UNKNOWN, "Setup building"
    elif verdict in ("MARKET CLOSED", "SESSION CLOSED"):
        return DecisionState.OBSERVING, ReasonCode.SESSION_CLOSED, verdict
    elif verdict == "WAIT":
        sr = result.get("strict_reason") or result.get("reason") or ""
        gd = result.get("gate_debug") or {}
        cs, rc = _map_strict_reason(sr, gd)
        return cs, rc, sr or "No active setup"
    else:
        # Catches empty verdict, unknown strings, etc.
        return DecisionState.OBSERVING, ReasonCode.NO_SETUP, verdict or "No setup"

    # For READY or EARLY: check execution gates (shadow observation only)
    if arm_state:
        if not arm_state.get("execution_enabled", False):
            return (DecisionState.BLOCKED_EXECUTION_MODE,
                    ReasonCode.EXECUTION_DISABLED,
                    "execution_enabled=False")
        if arm_state.get("safety_locked", False):
            return (DecisionState.BLOCKED_SAFETY,
                    ReasonCode.SAFETY_LOCK,
                    arm_state.get("safety_lock_reason") or "safety_locked")
        if not arm_state.get("armed", False):
            # READY but not armed → still at READY (operator must arm explicitly)
            return base_state, ReasonCode.NOT_ARMED, "Not armed; setup is READY"
        # All execution gates clear → EXECUTABLE (shadow-compressed path)
        return DecisionState.EXECUTABLE, ReasonCode.UNKNOWN, "All gates cleared"

    return base_state, reason_code, reason_text


def map_orb_state_to_canonical(
    orb_state: str,
    orb_status: Optional[Dict] = None,
) -> Tuple[str, str, str]:
    """Map OrbEngine state string to (canonical_state, reason_code, reason_text)."""
    cs, rc = ORB_STATE_MAP.get(orb_state, (DecisionState.OBSERVING, ReasonCode.UNKNOWN))
    reason_text = orb_status.get("block_reason", "") if orb_status else ""
    return cs, rc, reason_text


# ── Ghost snapshot enrichment ─────────────────────────────────────────────────

def enrich_ghost_snapshot(
    snapshot: Dict,
    record:   "DecisionRecord",
    orb_status: Optional[Dict] = None,
    full_analysis_result: Optional[Dict] = None,
) -> Dict:
    """Enrich a ghost_opportunities extra_snapshot with canonical decision state.

    Ghost Research Engine remains READ-ONLY with respect to live decisions.
    This function never modifies a DecisionRecord.
    """
    fa = full_analysis_result or {}
    enriched = dict(snapshot)
    enriched.update({
        "canonical_decision_state": record.state,
        "canonical_reason_code":    record.reason_code,
        "canonical_decision_id":    record.decision_id,
        "live_verdict":             record.verdict,
        "edge_score":               record.edge_score,
        "confidence":               record.confidence,
        # FVG state (from full_analysis if available)
        "fvg_state":    (fa.get("fvg_sequences") or {}).get("state", "UNKNOWN"),
        # Zone state
        "zone_state":   fa.get("zone_status") or fa.get("zone_type") or "UNKNOWN",
        # Qualification state
        "qualification_state": (
            record.state if record.state in (
                DecisionState.QUALIFIED, DecisionState.QUALIFIED_MANUAL_OVERRIDE
            ) else "NOT_QUALIFIED"
        ),
        # Risk state
        "risk_status":          record.risk_status or "UNKNOWN",
        # Execution state snapshot
        "execution_mode":       record.execution_mode or "UNKNOWN",
        "execution_enabled":    record.execution_enabled,
        "armed":                record.armed,
        "safety_lock":          record.safety_lock,
        "prop_status":          record.prop_status or "UNKNOWN",
        # ORB canonical mapping
        "orb_canonical_state":  (
            ORB_STATE_MAP.get(
                orb_status.get("state", "") if orb_status else "", ("UNKNOWN",)
            )[0]
        ),
        # Parity
        "parity_agree":         record.parity_agree,
        "parity_diff_reason":   record.parity_diff_reason,
        "dc_version":           DC_VERSION,
    })
    return enriched


# ── Decision Registry ─────────────────────────────────────────────────────────

class DecisionRegistry:
    """
    Per-instrument canonical decision lifecycle tracker (Shadow Mode).

    Wired into app.py:
    - observe_full_analysis() — called after every full_analysis() return
    - observe_orb_state()     — called after every OrbEngine bar-close
    - observe_entry_requested() — called when auto-fire submits to gateway
    - observe_position_active() — called when OrbEngine reaches POSITION_ACTIVE

    Thread-safe: one lock per instrument.
    Fail-open throughout.
    """

    DC_DB_READY: bool = False

    def __init__(
        self,
        get_db_fn:    Callable,
        re_event_fn:  Callable,
        instruments:  List[str],
        shadow_mode:  bool = True,
    ) -> None:
        self._get_db      = get_db_fn
        self._re_event    = re_event_fn
        self._instruments = list(instruments)
        self._shadow_mode = shadow_mode

        # Per-instrument current record (inst → DecisionRecord)
        self._records:  Dict[str, DecisionRecord] = {}
        self._locks:    Dict[str, threading.Lock] = {
            inst: threading.Lock() for inst in instruments
        }
        self._history:  Dict[str, List[DecisionTransition]] = {
            inst: [] for inst in instruments
        }
        self._log = logger.getChild("DC")

    # ── Boot ─────────────────────────────────────────────────────────────────

    def boot(self) -> None:
        """Probe DB tables and restore in-progress decisions."""
        try:
            db = self._get_db()
            cur = db.cursor()
            cur.execute("SELECT 1 FROM decision_records LIMIT 1")
            cur.execute("SELECT 1 FROM decision_transitions LIMIT 1")
            DecisionRegistry.DC_DB_READY = True
            self._log.info("DecisionContract: DB tables ready")
        except Exception as exc:
            self._log.warning("DecisionContract: DB probe failed (fail-open): %s", exc)
            DecisionRegistry.DC_DB_READY = False
            return

        try:
            self._restore_active()
        except Exception as exc:
            self._log.warning("DecisionContract: restore failed (fail-open): %s", exc)

    def _restore_active(self) -> None:
        """Restore non-terminal decision records from DB on restart."""
        db  = self._get_db()
        cur = db.cursor()
        terminal = list(PERMANENTLY_TERMINAL | SESSION_TERMINAL)
        placeholders = ",".join(["%s"] * len(terminal))
        cur.execute(f"""
            SELECT decision_id, opportunity_id, instrument, strategy,
                   strategy_version, direction, state, previous_state,
                   state_changed_at, reason_code, reason_text,
                   verdict, edge_score, confidence,
                   entry, stop, tp1, tp2, quantity,
                   risk_status, execution_mode, execution_enabled,
                   arm_required, armed, safety_lock, prop_status,
                   source_module, created_at, updated_at,
                   legacy_verdict, parity_agree, parity_diff_reason
            FROM decision_records
            WHERE instrument = ANY(%s::text[])
              AND state NOT IN ({placeholders})
            ORDER BY updated_at DESC
        """, (list(self._instruments), *terminal))
        rows  = cur.fetchall()
        cols  = [d[0] for d in cur.description]
        count = 0
        for row in rows:
            d = dict(zip(cols, row))
            inst = d.get("instrument", "")
            if inst not in self._instruments:
                continue
            # Only restore the most-recent record per instrument
            if inst in self._records:
                continue
            rec = self._record_from_row(d)
            self._records[inst] = rec
            count += 1
        self._log.info("DecisionContract: restored %d active records", count)

    def _record_from_row(self, d: Dict) -> DecisionRecord:
        def _ts(v) -> Optional[str]:
            return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v else None)
        return DecisionRecord(
            decision_id=d["decision_id"], opportunity_id=d.get("opportunity_id"),
            instrument=d["instrument"], strategy=d.get("strategy", ""),
            strategy_version=d.get("strategy_version", ""),
            direction=d.get("direction", ""),
            state=d["state"], previous_state=d.get("previous_state"),
            state_changed_at=_ts(d.get("state_changed_at")) or _now_utc(),
            reason_code=d.get("reason_code") or ReasonCode.UNKNOWN,
            reason_text=d.get("reason_text") or "",
            verdict=d.get("verdict"), edge_score=d.get("edge_score"),
            confidence=d.get("confidence"),
            market_context_ref=None, canonical_state_ts=None,
            entry=d.get("entry"), stop=d.get("stop"),
            tp1=d.get("tp1"), tp2=d.get("tp2"), quantity=d.get("quantity"),
            risk_status=d.get("risk_status"), risk_amount=None, risk_r=None,
            risk_reservation_id=None,
            execution_mode=d.get("execution_mode"),
            execution_enabled=d.get("execution_enabled"),
            arm_required=bool(d.get("arm_required", True)),
            armed=d.get("armed"), safety_lock=d.get("safety_lock"),
            prop_status=d.get("prop_status"), source_module=d.get("source_module", ""),
            created_at=_ts(d.get("created_at")) or _now_utc(),
            updated_at=_ts(d.get("updated_at")) or _now_utc(),
            legacy_verdict=d.get("legacy_verdict"),
            parity_agree=d.get("parity_agree"),
            parity_diff_reason=d.get("parity_diff_reason"),
        )

    # ── Main observers ────────────────────────────────────────────────────────

    def observe_full_analysis(
        self,
        inst:         str,
        analysis:     Dict,
        arm_state:    Optional[Dict] = None,
        trading_date: Optional[str]  = None,
    ) -> Optional[DecisionRecord]:
        """Observe a full_analysis() result and update the canonical record.

        Completely fail-open.  Never modifies analysis dict.  Never gates live execution.
        """
        if inst not in self._instruments:
            return None
        try:
            return self._observe_full_analysis_inner(inst, analysis, arm_state, trading_date)
        except Exception as exc:
            self._log.debug("DC observe_full_analysis (%s): %s", inst, exc)
            return None

    def _observe_full_analysis_inner(
        self, inst: str, analysis: Dict,
        arm_state: Optional[Dict], trading_date: Optional[str],
    ) -> DecisionRecord:
        verdict    = analysis.get("verdict") or ""
        edge_score = analysis.get("edge_score")
        confidence = analysis.get("confidence") or analysis.get("confidence_pct")
        strategy   = analysis.get("strategy") or analysis.get("active_strategy") or ""
        strategy_v = analysis.get("strategy_version") or ""
        direction  = _extract_direction(verdict)
        trade_plan = analysis.get("trade_plan") or {}
        td = trading_date or datetime.now(timezone.utc).date().isoformat()

        # ── Map legacy → canonical ──────────────────────────────────────
        can_state, reason_code, reason_text = map_full_analysis_to_canonical(
            verdict, analysis, arm_state
        )

        # ── Parity check ────────────────────────────────────────────────
        legacy_exec = _legacy_is_actionable(verdict)
        canon_exec  = can_state == DecisionState.EXECUTABLE
        # Shadow compression: EARLY→EXECUTABLE is legacy-compatible but flagged
        early_exec_legacy = (
            verdict in EARLY_READY_VERDICTS
            and arm_state
            and arm_state.get("armed")
            and arm_state.get("execution_enabled")
        )
        parity_agree      = (legacy_exec == canon_exec)
        parity_diff       = ""
        if not parity_agree:
            if legacy_exec and not canon_exec:
                parity_diff = f"Legacy executable but canonical={can_state}"
            else:
                parity_diff = f"Canonical executable but legacy={verdict}"

        # ── Execution/arm fields ─────────────────────────────────────────
        exec_mode    = (arm_state or {}).get("configured_mode") or (arm_state or {}).get("effective_mode")
        exec_enabled = (arm_state or {}).get("execution_enabled")
        armed        = (arm_state or {}).get("armed")
        safety_lock  = (arm_state or {}).get("safety_locked")

        # ── Risk from trade_plan ─────────────────────────────────────────
        entry = _sn(trade_plan.get("entry") or trade_plan.get("entry_price"))
        stop  = _sn(trade_plan.get("stop")  or trade_plan.get("stop_price"))
        tp1   = _sn(trade_plan.get("tp1")   or trade_plan.get("tp1_price"))
        tp2   = _sn(trade_plan.get("tp2")   or trade_plan.get("tp2_price"))
        qty   = _sn(trade_plan.get("contracts") or trade_plan.get("quantity"))

        dec_id = _decision_id(inst, strategy, direction, td)

        with self._locks[inst]:
            current = self._records.get(inst)

            # Build updated record
            now_ts = _now_utc()
            if current is None:
                rec = DecisionRecord(
                    decision_id=dec_id, opportunity_id=None,
                    instrument=inst, strategy=strategy, strategy_version=strategy_v,
                    direction=direction, state=can_state,
                    previous_state=None, state_changed_at=now_ts,
                    reason_code=reason_code, reason_text=reason_text,
                    verdict=verdict, edge_score=_sn(edge_score), confidence=_sn(confidence),
                    market_context_ref=None, canonical_state_ts=now_ts,
                    entry=entry, stop=stop, tp1=tp1, tp2=tp2, quantity=qty,
                    risk_status=None, risk_amount=None, risk_r=None, risk_reservation_id=None,
                    execution_mode=exec_mode, execution_enabled=exec_enabled,
                    arm_required=True, armed=armed, safety_lock=safety_lock, prop_status=None,
                    source_module="full_analysis",
                    legacy_verdict=verdict, canonical_state=can_state,
                    parity_agree=parity_agree, parity_diff_reason=parity_diff,
                )
            else:
                # Validate transition before applying
                ok, err = validate_transition(current.state, can_state, dec_id)
                if not ok:
                    self._log.warning("DecisionContract %s: %s", inst, err)
                    # Preserve previous valid state — do not apply illegal transition
                    can_state = current.state

                prev_state = current.state
                rec = DecisionRecord(
                    decision_id=dec_id, opportunity_id=current.opportunity_id,
                    instrument=inst, strategy=strategy or current.strategy,
                    strategy_version=strategy_v or current.strategy_version,
                    direction=direction or current.direction,
                    state=can_state,
                    previous_state=prev_state,
                    state_changed_at=now_ts if can_state != prev_state else current.state_changed_at,
                    reason_code=reason_code, reason_text=reason_text,
                    verdict=verdict, edge_score=_sn(edge_score), confidence=_sn(confidence),
                    market_context_ref=None, canonical_state_ts=now_ts,
                    entry=entry or current.entry, stop=stop or current.stop,
                    tp1=tp1 or current.tp1, tp2=tp2 or current.tp2,
                    quantity=qty or current.quantity,
                    risk_status=current.risk_status, risk_amount=current.risk_amount,
                    risk_r=current.risk_r, risk_reservation_id=current.risk_reservation_id,
                    execution_mode=exec_mode, execution_enabled=exec_enabled,
                    arm_required=True, armed=armed, safety_lock=safety_lock,
                    prop_status=current.prop_status, source_module="full_analysis",
                    created_at=current.created_at, updated_at=now_ts,
                    legacy_verdict=verdict, canonical_state=can_state,
                    parity_agree=parity_agree, parity_diff_reason=parity_diff,
                    transition_history=current.transition_history,
                )

            self._records[inst] = rec

            # Record transition if state changed
            state_changed = (current is None or current.state != can_state)
            if state_changed:
                txn = DecisionTransition(
                    decision_id=dec_id, instrument=inst,
                    from_state=current.state if current else None,
                    to_state=can_state,
                    reason_code=reason_code, reason_text=reason_text,
                    source_module="full_analysis",
                    transitioned_at=now_ts,
                    extra={"verdict": verdict, "edge_score": _sn(edge_score)},
                )
                rec.transition_history.append(txn)
                self._history[inst].append(txn)
                if len(self._history[inst]) > 200:
                    self._history[inst] = self._history[inst][-200:]

        # Fire events
        if state_changed:
            self._re_event("DC_STATE_CHANGED", inst=inst,
                           extra={"from": current.state if current else None,
                                  "to": can_state, "reason_code": reason_code})
        if not parity_agree:
            self._re_event("DC_PARITY_MISMATCH", inst=inst,
                           extra={"legacy_verdict": verdict, "canonical": can_state,
                                  "reason": parity_diff})
            self._log.debug("DC parity mismatch [%s]: %s", inst, parity_diff)

        # Persist (async, off hot path)
        if DecisionRegistry.DC_DB_READY:
            threading.Thread(
                target=self._persist_record,
                args=(rec, state_changed),
                daemon=True,
            ).start()

        return rec

    def observe_orb_state(
        self,
        inst:       str,
        orb_status: Dict,
    ) -> None:
        """Observe an OrbEngine state and update the canonical record.

        Only updates when the ORB state maps to a state further along the
        lifecycle than the current canonical state (never regresses).
        """
        if inst not in self._instruments:
            return
        try:
            self._observe_orb_state_inner(inst, orb_status)
        except Exception as exc:
            self._log.debug("DC observe_orb_state (%s): %s", inst, exc)

    def _observe_orb_state_inner(self, inst: str, orb_status: Dict) -> None:
        orb_state = orb_status.get("state", "UNKNOWN") if orb_status else "UNKNOWN"
        can_state, reason_code, reason_text = map_orb_state_to_canonical(orb_state, orb_status)
        td = (orb_status.get("trading_date") if orb_status else None) or \
             datetime.now(timezone.utc).date().isoformat()

        with self._locks[inst]:
            current = self._records.get(inst)
            strategy = (orb_status.get("strategy") if orb_status else None) or "09:30_ORB"
            direction = (orb_status.get("breakout_direction") if orb_status else None) or \
                        (current.direction if current else "")
            dec_id = _decision_id(inst, strategy, direction, td)

            ok, err = validate_transition(
                current.state if current else None, can_state, dec_id
            )
            if not ok:
                self._log.debug("DC ORB transition skipped [%s]: %s", inst, err)
                return

            now_ts = _now_utc()
            if current is None:
                rec = DecisionRecord(
                    decision_id=dec_id, opportunity_id=orb_status.get("opportunity_id") if orb_status else None,
                    instrument=inst, strategy=strategy, strategy_version="",
                    direction=direction, state=can_state,
                    previous_state=None, state_changed_at=now_ts,
                    reason_code=reason_code, reason_text=reason_text,
                    verdict=None, edge_score=None, confidence=None,
                    market_context_ref=None, canonical_state_ts=now_ts,
                    entry=None, stop=None, tp1=None, tp2=None, quantity=None,
                    risk_status=None, risk_amount=None, risk_r=None, risk_reservation_id=None,
                    execution_mode=None, execution_enabled=None, arm_required=True,
                    armed=None, safety_lock=None, prop_status=None,
                    source_module="orb_engine",
                )
            else:
                prev = current.state
                rec = DecisionRecord(
                    **{**asdict(current),
                       "state": can_state, "previous_state": prev,
                       "state_changed_at": now_ts if can_state != prev else current.state_changed_at,
                       "reason_code": reason_code, "reason_text": reason_text,
                       "canonical_state_ts": now_ts, "updated_at": now_ts,
                       "source_module": "orb_engine",
                       "canonical_state": can_state,
                    }
                )
                rec.transition_history = current.transition_history

            state_changed = current is None or current.state != can_state
            self._records[inst] = rec

            if state_changed:
                txn = DecisionTransition(
                    decision_id=dec_id, instrument=inst,
                    from_state=current.state if current else None,
                    to_state=can_state, reason_code=reason_code,
                    reason_text=reason_text, source_module="orb_engine",
                    transitioned_at=now_ts,
                    extra={"orb_state": orb_state},
                )
                rec.transition_history.append(txn)
                self._history[inst].append(txn)

        if state_changed and DecisionRegistry.DC_DB_READY:
            threading.Thread(
                target=self._persist_record,
                args=(rec, True),
                daemon=True,
            ).start()

    def observe_entry_requested(self, inst: str, source: str = "auto_fire") -> None:
        """Mark canonical state ENTRY_REQUESTED when gateway is called."""
        self._simple_transition(inst, DecisionState.ENTRY_REQUESTED,
                                ReasonCode.UNKNOWN, "Entry submitted to gateway",
                                source)

    def observe_position_active(self, inst: str) -> None:
        """Mark canonical state POSITION_ACTIVE after order accepted."""
        self._simple_transition(inst, DecisionState.POSITION_ACTIVE,
                                ReasonCode.UNKNOWN, "Position opened", "position_manager")

    def observe_completed(self, inst: str, reason: str = "") -> None:
        """Mark canonical state COMPLETED after trade closes."""
        self._simple_transition(inst, DecisionState.COMPLETED,
                                ReasonCode.UNKNOWN, reason or "Trade completed", "position_manager")

    def observe_manual_requested(self, inst: str, direction: str = "") -> None:
        """Mark canonical state MANUAL_REQUESTED for manual desk orders."""
        self._simple_transition(inst, DecisionState.MANUAL_REQUESTED,
                                ReasonCode.MANUAL_OVERRIDE,
                                "Manual trade desk override", "manual_desk")

    def observe_order_accepted(self, inst: str) -> None:
        """Mark ORDER_ACCEPTED when the broker returns a 2xx on an order send."""
        self._simple_transition(inst, DecisionState.ORDER_ACCEPTED,
                                ReasonCode.UNKNOWN, "Broker accepted order", "broker_gateway")

    def observe_order_rejected(self, inst: str, reason: str = "") -> None:
        """Mark ORDER_REJECTED when the broker returns a 4xx on an order send."""
        self._simple_transition(inst, DecisionState.ORDER_REJECTED,
                                ReasonCode.UNKNOWN,
                                reason or "Broker rejected order", "broker_gateway")

    def get_record(self, inst: str):
        """Return the current DecisionRecord for the instrument, or None (read-only)."""
        return self._records.get(inst)

    def _simple_transition(self, inst: str, to_state: str,
                           reason_code: str, reason_text: str,
                           source: str) -> None:
        if inst not in self._instruments:
            return
        try:
            with self._locks[inst]:
                current = self._records.get(inst)
                from_state = current.state if current else None
                ok, err = validate_transition(from_state, to_state,
                                              current.decision_id if current else "")
                if not ok:
                    self._log.debug("DC simple transition [%s]: %s", inst, err)
                    return
                now_ts = _now_utc()
                if current:
                    rec_dict = asdict(current)
                    rec_dict.update({
                        "state": to_state, "previous_state": from_state,
                        "state_changed_at": now_ts if to_state != from_state else current.state_changed_at,
                        "reason_code": reason_code, "reason_text": reason_text,
                        "canonical_state_ts": now_ts, "updated_at": now_ts,
                        "source_module": source, "canonical_state": to_state,
                    })
                    rec = DecisionRecord(**{k: v for k, v in rec_dict.items()
                                           if k != "transition_history"})
                    rec.transition_history = current.transition_history
                    state_changed = current.state != to_state
                else:
                    # No record exists yet; create minimal one
                    td = datetime.now(timezone.utc).date().isoformat()
                    dec_id = _decision_id(inst, "", "", td)
                    rec = DecisionRecord(
                        decision_id=dec_id, opportunity_id=None,
                        instrument=inst, strategy="", strategy_version="", direction="",
                        state=to_state, previous_state=None, state_changed_at=now_ts,
                        reason_code=reason_code, reason_text=reason_text,
                        verdict=None, edge_score=None, confidence=None,
                        market_context_ref=None, canonical_state_ts=now_ts,
                        entry=None, stop=None, tp1=None, tp2=None, quantity=None,
                        risk_status=None, risk_amount=None, risk_r=None, risk_reservation_id=None,
                        execution_mode=None, execution_enabled=None, arm_required=True,
                        armed=None, safety_lock=None, prop_status=None, source_module=source,
                    )
                    state_changed = True

                self._records[inst] = rec
                if state_changed:
                    txn = DecisionTransition(
                        decision_id=rec.decision_id, instrument=inst,
                        from_state=from_state, to_state=to_state,
                        reason_code=reason_code, reason_text=reason_text,
                        source_module=source, transitioned_at=now_ts,
                    )
                    rec.transition_history.append(txn)
                    self._history[inst].append(txn)

            if state_changed and DecisionRegistry.DC_DB_READY:
                threading.Thread(
                    target=self._persist_record, args=(rec, True), daemon=True
                ).start()
        except Exception as exc:
            self._log.debug("DC simple_transition (%s→%s): %s", inst, to_state, exc)

    # ── DB persistence ────────────────────────────────────────────────────────

    def _persist_record(self, rec: DecisionRecord, transition_changed: bool) -> None:
        """Persist record to DB.  Persistence failure NEVER causes duplicate send."""
        try:
            db  = self._get_db()
            cur = db.cursor()
            d   = rec.to_dict()
            cur.execute("""
                INSERT INTO decision_records
                    (decision_id, opportunity_id, instrument, strategy,
                     strategy_version, direction, state, previous_state,
                     state_changed_at, reason_code, reason_text,
                     verdict, edge_score, confidence,
                     entry, stop, tp1, tp2, quantity,
                     risk_status, execution_mode, execution_enabled,
                     arm_required, armed, safety_lock, prop_status,
                     source_module, updated_at,
                     legacy_verdict, canonical_state, parity_agree, parity_diff_reason)
                VALUES
                    (%(decision_id)s, %(opportunity_id)s, %(instrument)s, %(strategy)s,
                     %(strategy_version)s, %(direction)s, %(state)s, %(previous_state)s,
                     %(state_changed_at)s, %(reason_code)s, %(reason_text)s,
                     %(verdict)s, %(edge_score)s, %(confidence)s,
                     %(entry)s, %(stop)s, %(tp1)s, %(tp2)s, %(quantity)s,
                     %(risk_status)s, %(execution_mode)s, %(execution_enabled)s,
                     %(arm_required)s, %(armed)s, %(safety_lock)s, %(prop_status)s,
                     %(source_module)s, %(updated_at)s,
                     %(legacy_verdict)s, %(canonical_state)s, %(parity_agree)s,
                     %(parity_diff_reason)s)
                ON CONFLICT (decision_id) DO UPDATE SET
                    state                = EXCLUDED.state,
                    previous_state       = EXCLUDED.previous_state,
                    state_changed_at     = EXCLUDED.state_changed_at,
                    reason_code          = EXCLUDED.reason_code,
                    reason_text          = EXCLUDED.reason_text,
                    verdict              = EXCLUDED.verdict,
                    edge_score           = EXCLUDED.edge_score,
                    confidence           = EXCLUDED.confidence,
                    entry                = EXCLUDED.entry,
                    stop                 = EXCLUDED.stop,
                    tp1                  = EXCLUDED.tp1,
                    tp2                  = EXCLUDED.tp2,
                    quantity             = EXCLUDED.quantity,
                    risk_status          = EXCLUDED.risk_status,
                    execution_mode       = EXCLUDED.execution_mode,
                    execution_enabled    = EXCLUDED.execution_enabled,
                    armed                = EXCLUDED.armed,
                    safety_lock          = EXCLUDED.safety_lock,
                    prop_status          = EXCLUDED.prop_status,
                    source_module        = EXCLUDED.source_module,
                    updated_at           = EXCLUDED.updated_at,
                    legacy_verdict       = EXCLUDED.legacy_verdict,
                    canonical_state      = EXCLUDED.canonical_state,
                    parity_agree         = EXCLUDED.parity_agree,
                    parity_diff_reason   = EXCLUDED.parity_diff_reason
            """, d)

            if transition_changed and rec.transition_history:
                last_txn = rec.transition_history[-1]
                cur.execute("""
                    INSERT INTO decision_transitions
                        (decision_id, instrument, from_state, to_state,
                         reason_code, reason_text, source_module, transitioned_at, extra)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING
                """, (
                    last_txn.decision_id, last_txn.instrument,
                    last_txn.from_state, last_txn.to_state,
                    last_txn.reason_code, last_txn.reason_text,
                    last_txn.source_module, last_txn.transitioned_at,
                    json.dumps(last_txn.extra),
                ))
            db.commit()
        except Exception as exc:
            self._log.debug("DC _persist_record: %s", exc)
            # Persistence failure never blocks execution

    # ── Query API ─────────────────────────────────────────────────────────────

    def get_state(self, inst: str) -> Optional[Dict]:
        """Return current canonical decision state as dict."""
        rec = self._records.get(inst)
        if rec is None:
            return None
        d = rec.to_dict()
        d["transition_history"] = [t.to_dict() for t in rec.transition_history[-5:]]
        return d

    def get_history(self, inst: str, limit: int = 20) -> List[Dict]:
        """Return last N transitions for an instrument."""
        return [t.to_dict() for t in self._history.get(inst, [])[-limit:]]

    def get_all_states(self) -> Dict[str, Optional[Dict]]:
        """Return canonical state for all instruments."""
        return {inst: self.get_state(inst) for inst in self._instruments}

    def get_parity_mismatches(self, inst: Optional[str] = None,
                              limit: int = 20) -> List[Dict]:
        """Return recent parity mismatches from DB."""
        if not DecisionRegistry.DC_DB_READY:
            return []
        try:
            db  = self._get_db()
            cur = db.cursor()
            if inst:
                cur.execute("""
                    SELECT decision_id, instrument, state, legacy_verdict,
                           canonical_state, parity_agree, parity_diff_reason, updated_at
                    FROM decision_records
                    WHERE instrument=%s AND parity_agree=FALSE
                    ORDER BY updated_at DESC LIMIT %s
                """, (inst, limit))
            else:
                cur.execute("""
                    SELECT decision_id, instrument, state, legacy_verdict,
                           canonical_state, parity_agree, parity_diff_reason, updated_at
                    FROM decision_records
                    WHERE parity_agree=FALSE
                    ORDER BY updated_at DESC LIMIT %s
                """, (limit,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        except Exception as exc:
            self._log.debug("DC get_parity_mismatches: %s", exc)
            return []

    def reset_instrument(self, inst: str) -> None:
        """Reset instrument to OBSERVING (used on day rollover / restart)."""
        if inst not in self._instruments:
            return
        self._simple_transition(inst, DecisionState.OBSERVING,
                                ReasonCode.UNKNOWN, "Session reset", "boot")


# ── Shared helpers ────────────────────────────────────────────────────────────

def _sn(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None
