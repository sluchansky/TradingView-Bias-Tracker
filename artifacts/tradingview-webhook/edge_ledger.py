"""
Edge Ledger — Phase 8A
======================
RESEARCH / ACCOUNTING / DISPLAY-ONLY.

Provides immutable signal-vs-management accounting for every READY setup.
Separates:
  • Signal Outcome  — "Was the original trade call good?"
                      Uses original frozen entry/stop/targets; ignores management.
  • Managed Outcome — "How well did execution handle the signal?"
                      Uses actual fills/exits/stop-moves from native_journal.

Design principles:
  • All computation functions are PURE (no app.py imports, no side effects).
  • Original signal fields frozen at ghost_observe_setup time; NEVER mutated.
  • Signal outcome resolved via bar data (same as ghost_observations watcher).
  • Managed outcome sourced from native_journal (actual fills and P&L).
  • Costs clearly labelled ESTIMATED (ghost/shadow) vs ACTUAL (live/paper).
  • Zero-cost outcomes are forbidden — null stored when cost cannot be computed.
  • Learning engine is NOT changed in Phase 8A (edge_ledger_ready_for_learning
    is a staging flag only — no consumer reads it yet).

DB convention: no DDL here — edge_ledger table created via DB tool / publish
schema-diff.  This module never creates tables.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

# ── Sample partition constants ────────────────────────────────────────────────
PARTITION_DEVELOPMENT      = "DEVELOPMENT"
PARTITION_FORWARD_VALID    = "FORWARD_VALIDATION"
PARTITION_SHADOW           = "SHADOW"
PARTITION_PAPER            = "PAPER"
PARTITION_LIVE             = "LIVE"
PARTITION_HISTORICAL       = "HISTORICAL"
PARTITION_UNKNOWN          = "UNKNOWN"

# ── Management comparison outcomes ───────────────────────────────────────────
MGMT_HELPED                = "MANAGEMENT_HELPED"
MGMT_HURT                  = "MANAGEMENT_HURT"
MGMT_NEUTRAL               = "MANAGEMENT_NEUTRAL"
MGMT_UNAVAILABLE           = "COMPARISON_UNAVAILABLE"
MGMT_NEUTRAL_THRESHOLD_R   = 0.05   # |delta_r| ≤ this → NEUTRAL

# ── Backfill safety classifications ──────────────────────────────────────────
BACKFILL_SAFE              = "SAFE_TO_BACKFILL"
BACKFILL_PARTIAL           = "PARTIAL_BACKFILL"
BACKFILL_UNSAFE            = "UNSAFE_TO_BACKFILL"

# ── Cost model ────────────────────────────────────────────────────────────────
COST_MODEL_VERSION         = "v1"   # must bump when commission constants change


# ---------------------------------------------------------------------------
# Stable edge_id key
# ---------------------------------------------------------------------------

def build_edge_id(
    instrument: str,
    direction: str,
    strategy_key: str,
    obs_key: str,
) -> str:
    """Build a stable, unique edge_id for an edge_ledger row.

    Uses the same obs_key that ghost_observations uses for its dedup — this
    guarantees a 1:1 correspondence between ghost_obs and edge_ledger rows
    without a separate UUID sequence.

    Format: ``el|{obs_key}`` — human-readable, safe for SQL UNIQUE constraint.
    """
    return f"el|{obs_key}"


# ---------------------------------------------------------------------------
# Signal term extraction
# ---------------------------------------------------------------------------

def extract_frozen_signal_terms(
    result: Dict[str, Any],
    instrument: str,
    instrument_specs: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Extract all immutable signal fields from a full_analysis result dict.

    Returns a dict ready for INSERT into edge_ledger's original-signal columns,
    or None if the required minimum fields (entry, stop, tp1) are missing.

    This function is PURE — no side effects, no DB access.
    """
    trade_plan = result.get("trade_plan") or {}
    ctx        = result.get("learning_ctx") or {}
    verdict    = result.get("verdict") or ""

    entry  = trade_plan.get("entry")
    stop   = trade_plan.get("stop")
    tp1    = trade_plan.get("target") or trade_plan.get("tp1")
    tp2    = trade_plan.get("target2") or trade_plan.get("tp2")

    try:
        entry = float(entry)
        stop  = float(stop)
        tp1   = float(tp1)
    except (TypeError, ValueError):
        return None

    if entry is None or stop is None or tp1 is None:
        return None

    risk_pts = abs(entry - stop)
    if risk_pts <= 0:
        return None

    # Optional tp2
    try:
        tp2 = float(tp2) if tp2 is not None else None
    except (TypeError, ValueError):
        tp2 = None

    # Risk in dollars (estimated)
    risk_dollars = None
    spec = instrument_specs.get(instrument) or {}
    if isinstance(spec, dict):
        try:
            pv = float(spec.get("point_value") or 0)
            if pv > 0:
                risk_dollars = round(risk_pts * pv, 2)
        except (TypeError, ValueError):
            pass

    # R:R ratio
    try:
        rr = round(abs(tp1 - entry) / risk_pts, 3) if risk_pts > 0 else None
    except ZeroDivisionError:
        rr = None

    # Edge scores
    edge_score  = result.get("edge_score")
    directions  = result.get("directions") or {}
    long_block  = directions.get("Long")  or {}
    short_block = directions.get("Short") or {}
    long_score  = long_block.get("edge_score")  if isinstance(long_block,  dict) else None
    short_score = short_block.get("edge_score") if isinstance(short_block, dict) else None

    # Direction margin
    try:
        decision_margin = (
            round(abs(float(long_score) - float(short_score)), 2)
            if long_score is not None and short_score is not None
            else None
        )
    except (TypeError, ValueError):
        decision_margin = None

    # Thesis / alignment
    thesis_block = result.get("left_brain") or {}
    left_brain   = (thesis_block.get("direction") or
                    thesis_block.get("thesis_direction") or None)
    alignment    = (result.get("thesis_alignment") or
                    thesis_block.get("alignment") or None)

    # Confirmations / blockers
    confirmations   = result.get("confirmations") or []
    blockers        = result.get("blockers") or []
    opp_struct      = result.get("opposing_structure") or None
    risk_state      = result.get("risk_state") or None

    # Market context snapshot
    vol_block  = result.get("volatility") or {}
    cvd_block  = result.get("cvd") or {}
    atr_at_sig = vol_block.get("atr_pts") or vol_block.get("current_atr") or None
    cvd_dir    = (cvd_block.get("direction") if isinstance(cvd_block, dict)
                  else None)
    sess       = ctx.get("session") or None
    regime     = ctx.get("regime") or "UNKNOWN"
    strategy_key = (ctx.get("strategy_key") or
                    (result.get("strategy_scanner") or {}).get("strategy_key") or
                    "UNKNOWN")

    market_context: Dict[str, Any] = {}
    if atr_at_sig is not None:
        market_context["atr_pts"] = atr_at_sig
    if cvd_dir:
        market_context["cvd_direction"] = cvd_dir
    if regime:
        market_context["regime"] = regime
    if sess:
        market_context["session"] = sess

    return {
        # Geometry — immutable
        "original_entry":        entry,
        "original_stop":         stop,
        "original_tp1":          tp1,
        "original_tp2":          tp2,
        "original_targets":      {"t1": tp1, "t2": tp2} if tp2 else {"t1": tp1},
        "original_risk_points":  round(risk_pts, 4),
        "original_risk_dollars": risk_dollars,
        "original_rr":           rr,
        # Scores — immutable
        "edge_score":            (int(round(float(edge_score))) if edge_score is not None else None),
        "long_score":            (round(float(long_score), 2)  if long_score  is not None else None),
        "short_score":           (round(float(short_score), 2) if short_score is not None else None),
        "decision_margin":       decision_margin,
        "grade":                 result.get("grade"),
        "readiness":             verdict or None,
        # Thesis — immutable
        "left_brain_thesis":     left_brain,
        "thesis_alignment":      alignment,
        # Gate context — immutable
        "confirmations":         confirmations if isinstance(confirmations, list) else [],
        "blockers":              blockers      if isinstance(blockers,      list) else [],
        "opposing_structure":    str(opp_struct) if opp_struct else None,
        "risk_state":            str(risk_state) if risk_state else None,
        "market_context":        market_context,
        # Classification
        "strategy_key":          strategy_key,
        "session":               sess,
    }


# ---------------------------------------------------------------------------
# Cost model
# ---------------------------------------------------------------------------

def compute_signal_cost_r(
    instrument: str,
    entry: float,
    stop: float,
    instrument_specs: Dict[str, Any],
    comm_per_side_usd: float,
    slippage_ticks: float,
) -> Optional[float]:
    """Compute estimated round-trip cost as a fraction of R (ESTIMATED).

    Replicates the same formula as profitability_engine.compute_commission_r
    so edge_ledger cost estimates are consistent with ghost_observations cost_r.

    Returns None when computation is impossible (unknown instrument, zero risk).
    This prevents silent zero-cost assumptions — callers should store NULL and
    mark the net result as incomplete when this returns None.
    """
    try:
        e = float(entry)
        s = float(stop)
    except (TypeError, ValueError):
        return None
    if e <= 0 or s <= 0 or e == s:
        return None

    spec = instrument_specs.get(instrument) or {}
    if not spec:
        for _k, _v in instrument_specs.items():
            if isinstance(_v, dict) and _v.get("symbol") == instrument:
                spec = _v.get("specs") or _v
                break
    if not spec:
        return None

    try:
        pv   = float(spec.get("point_value") or 0)
        tick = float(spec.get("tick_size") or 0)
    except (TypeError, ValueError):
        return None
    if pv <= 0 or tick <= 0:
        return None

    risk_dollars = abs(e - s) * pv
    if risk_dollars <= 0:
        return None

    commission = comm_per_side_usd * 2.0
    slippage   = slippage_ticks * tick * pv * 2.0
    return (commission + slippage) / risk_dollars


def compute_signal_net_r(
    gross_r: Optional[float],
    cost_r: Optional[float],
) -> Optional[float]:
    """Compute net R: gross_r minus cost_r.

    If cost_r is None (cost cannot be estimated), returns None — do NOT
    silently treat missing costs as zero.  The caller must store NULL and
    mark data_complete=FALSE when this returns None.
    """
    if gross_r is None:
        return None
    if cost_r is None:
        return None   # cost unknown → net is incomplete, not equal to gross
    return round(gross_r - cost_r, 4)


def compute_cost_dollars(
    cost_r: Optional[float],
    risk_dollars: Optional[float],
) -> Optional[float]:
    """Convert cost_r fraction to dollar cost estimate."""
    if cost_r is None or risk_dollars is None:
        return None
    try:
        return round(float(cost_r) * float(risk_dollars), 2)
    except (TypeError, ValueError):
        return None


def compute_pnl_dollars(
    r_multiple: Optional[float],
    risk_dollars: Optional[float],
) -> Optional[float]:
    """Convert an R-multiple to estimated dollar P&L."""
    if r_multiple is None or risk_dollars is None:
        return None
    try:
        return round(float(r_multiple) * float(risk_dollars), 2)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Signal vs Management comparison
# ---------------------------------------------------------------------------

def compute_comparison(
    signal_net_r: Optional[float],
    managed_net_r: Optional[float],
) -> Tuple[Optional[float], str]:
    """Compute delta and classify management impact.

    Returns (delta_r, classification) where:
      delta_r        = managed_net_r − signal_net_r (positive = management added R)
      classification ∈ {MANAGEMENT_HELPED, MANAGEMENT_HURT, MANAGEMENT_NEUTRAL,
                        COMPARISON_UNAVAILABLE}

    Classification thresholds:
      |delta_r| ≤ 0.05R → NEUTRAL (within rounding noise)
      delta_r >  0.05R  → HELPED
      delta_r < -0.05R  → HURT

    Per the task spec examples:
      Signal Net R: +2.0 / Managed Net R: +0.3  →  HURT   (delta = −1.7R)
      Signal Net R: −1.0 / Managed Net R: −0.25 →  HELPED (delta = +0.75R)
    """
    if signal_net_r is None or managed_net_r is None:
        return None, MGMT_UNAVAILABLE

    try:
        s = float(signal_net_r)
        m = float(managed_net_r)
    except (TypeError, ValueError):
        return None, MGMT_UNAVAILABLE

    delta = round(m - s, 4)
    if abs(delta) <= MGMT_NEUTRAL_THRESHOLD_R:
        return delta, MGMT_NEUTRAL
    elif delta > 0:
        return delta, MGMT_HELPED
    else:
        return delta, MGMT_HURT


def management_helped_flag(comparison_reason: str) -> Optional[bool]:
    """Convert classification string to the boolean management_helped column.

    HELPED → True, HURT → False, NEUTRAL → None (NULL), UNAVAILABLE → None.
    """
    if comparison_reason == MGMT_HELPED:
        return True
    if comparison_reason == MGMT_HURT:
        return False
    return None   # NEUTRAL or UNAVAILABLE stored as NULL


# ---------------------------------------------------------------------------
# Sample partition assignment
# ---------------------------------------------------------------------------

def assign_sample_partition(
    source: str,
    execution_mode: Optional[str] = None,
) -> str:
    """Assign a sample_partition value from the source / execution context.

    Rules (deterministic — no date-based logic in Phase 8A):
      source="databento_scan" | "live_shadow" → SHADOW
      source="paper"                          → PAPER
      source="backtest"                       → HISTORICAL
      execution_mode="traderspost" with live source → LIVE
      execution_mode="paper"                  → PAPER
      Anything else                           → UNKNOWN

    FORWARD_VALIDATION is NOT automatically assigned in Phase 8A (per Part 9).
    DEVELOPMENT is not assigned automatically — reserved for manual annotation.
    """
    src = (source or "").lower().strip()
    mode = (execution_mode or "").lower().strip()

    if src in ("databento_scan", "live_shadow"):
        return PARTITION_SHADOW
    if src == "paper" or mode == "paper":
        return PARTITION_PAPER
    if src == "backtest":
        return PARTITION_HISTORICAL
    if mode in ("traderspost", "live"):
        return PARTITION_LIVE
    return PARTITION_UNKNOWN


# ---------------------------------------------------------------------------
# Backfill safety classification
# ---------------------------------------------------------------------------

def classify_backfill_safety(row: Dict[str, Any]) -> str:
    """Classify an existing trade record for backfill safety.

    Returns one of:
      SAFE_TO_BACKFILL    — original entry/stop/targets provably preserved
      PARTIAL_BACKFILL    — some original terms known; others uncertain
      UNSAFE_TO_BACKFILL  — management mutations make original unknowable

    Rules:
    • edge_ledger row already exists → SAFE (terms already frozen)
    • internal_trade_snapshots row exists (has planned_entry/stop/targets) → SAFE
    • native_journal row with planned context (Phase A or later) → SAFE
    • strategy_trades row with entry/stop/target + no snapshot → PARTIAL
      (entry is typically the actual fill; stop may or may not have moved)
    • Only actual_exit / net_pnl known (no original geometry) → UNSAFE
    • stop was mutated (be_move or trailing evidence) with no pre-mutation record → UNSAFE

    Never reconstruct an original stop from a final moved stop.
    """
    sources = row.get("sources") or []  # list of available source systems

    has_snapshot  = row.get("has_snapshot", False)      # internal_trade_snapshots
    has_nj        = row.get("has_native_journal", False) # native_journal (Phase A+)
    has_el        = row.get("has_edge_ledger", False)    # already in edge_ledger
    has_st        = row.get("has_strategy_trades", False)# strategy_trades entry
    stop_mutated  = row.get("stop_mutated", False)       # evidence of BE/trail move
    original_known = row.get("original_geometry_known", False)  # explicit override

    if has_el or original_known:
        return BACKFILL_SAFE
    if has_snapshot or has_nj:
        # Snapshots and NJ both capture planned (pre-execution) geometry
        return BACKFILL_SAFE
    if has_st and not stop_mutated:
        # strategy_trades captures entry/stop at trade recording time; stop
        # may have moved to BE before record was written — treat as PARTIAL
        return BACKFILL_PARTIAL
    if has_st and stop_mutated:
        return BACKFILL_UNSAFE
    # No reliable source for original geometry
    return BACKFILL_UNSAFE


# ---------------------------------------------------------------------------
# Diagnostics aggregation
# ---------------------------------------------------------------------------

def compute_el_diagnostics(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Aggregate edge_ledger rows into per-strategy EDGE LEDGER DIAGNOSTICS.

    Each input dict should have:
        strategy_key, instrument, sample_partition,
        signal_outcome_status, managed_outcome_status,
        signal_gross_r, signal_net_r,
        managed_net_r,
        signal_cost_r (may be None),
        comparison_complete (bool)

    Returns a list of dicts, one per (strategy_key, instrument) group, sorted
    by descending |avg_signal_net_r| then alphabetically.
    """
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for row in rows:
        sk   = str(row.get("strategy_key") or "UNKNOWN")
        inst = str(row.get("instrument")   or "UNKNOWN")
        key  = (sk, inst)
        if key not in groups:
            groups[key] = []
        groups[key].append(row)

    results = []
    for (sk, inst), grp in groups.items():
        total  = len(grp)
        unresolved      = sum(1 for r in grp if r.get("signal_outcome_status") in (None, "open", ""))
        signal_resolved = sum(1 for r in grp if r.get("signal_outcome_status") in ("closed", "expired"))
        managed_resolved= sum(1 for r in grp if r.get("managed_outcome_status") == "CLOSED")
        comparison_done = sum(1 for r in grp if r.get("comparison_complete"))

        # Signal R stats
        sig_net_rs  = [float(r["signal_net_r"])  for r in grp
                       if r.get("signal_net_r")  is not None]
        sig_gross_rs= [float(r["signal_gross_r"]) for r in grp
                       if r.get("signal_gross_r") is not None]
        mgmt_net_rs = [float(r["managed_net_r"]) for r in grp
                       if r.get("managed_net_r") is not None]
        deltas      = [float(r["signal_vs_managed_delta_r"]) for r in grp
                       if r.get("signal_vs_managed_delta_r") is not None]

        avg_sig_gross_r = round(sum(sig_gross_rs) / len(sig_gross_rs), 4) if sig_gross_rs else None
        avg_sig_net_r   = round(sum(sig_net_rs)   / len(sig_net_rs),   4) if sig_net_rs   else None
        avg_mgmt_net_r  = round(sum(mgmt_net_rs)  / len(mgmt_net_rs),  4) if mgmt_net_rs  else None
        avg_delta       = round(sum(deltas)        / len(deltas),       4) if deltas        else None

        # Cost completeness
        with_cost    = sum(1 for r in grp if r.get("signal_cost_r") is not None)
        cost_pct     = round(with_cost / total, 4) if total > 0 else 0.0

        # Partition counts
        partition_counts: Dict[str, int] = {}
        for r in grp:
            p = str(r.get("sample_partition") or PARTITION_UNKNOWN)
            partition_counts[p] = partition_counts.get(p, 0) + 1

        results.append({
            "strategy_key":          sk,
            "instrument":            inst,
            "total_ledger_signals":  total,
            "unresolved":            unresolved,
            "signal_outcomes_resolved": signal_resolved,
            "managed_outcomes_resolved": managed_resolved,
            "comparisons_complete":  comparison_done,
            "avg_signal_gross_r":    avg_sig_gross_r,
            "avg_signal_net_r":      avg_sig_net_r,
            "avg_managed_net_r":     avg_mgmt_net_r,
            "avg_management_delta":  avg_delta,
            "cost_completeness":     cost_pct,
            "sample_partition_counts": partition_counts,
            "data_completeness_pct": round(
                sum(1 for r in grp if r.get("data_complete")) / total, 4
            ) if total > 0 else 0.0,
        })

    results.sort(
        key=lambda x: (
            -abs(x.get("avg_signal_net_r") or 0),
            x.get("strategy_key", ""),
            x.get("instrument",   ""),
        )
    )
    return results
