"""
ghost_research_engine.py — Phase 2 Ghost Research & Evidence Engine
=====================================================================

Observes the 09:30 OrbEngine (shadow mode) and converts every legitimate
breakout opportunity into a controlled multi-variant ghost experiment.

SAFETY CONTRACT
---------------
• Never calls execute_trade_gateway or any broker path.
• Never modifies OrbEngine config, live weights, scoring, or execution mode.
• Never writes to strategy_trades, STRATEGY_WEIGHTS, or LEARNING_ANALYTICS.
• Never feeds results into _recompute_learning().
• Research results require mandatory human review before any live change.
• Fully fail-open: every call site is wrapped in try/except with no re-raise.
• All heavy statistics (bootstrap, Monte Carlo) run OFF the bar-close hot path.

ARCHITECTURE
------------
Databento → OrbEngine → GhostResearchEngine.on_bar_close()
    → detect BREAKOUT_DETECTED transition
    → create ghost_opportunities row (immutable market snapshot)
    → create ghost_experiments rows (10 variants)
    → per-bar: check entry/exit conditions for all open experiments
    → write ghost_experiment_results
    → compute evidence states, statistics, bootstrap CI, Monte Carlo DD
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Version ───────────────────────────────────────────────────────────────────

GRE_VERSION = "1.0.0"

# ── Constants ─────────────────────────────────────────────────────────────────

STRATEGY_NAME     = "09:30_ORB"
EXPERIMENT_FAMILY = "ORB_09_30"

# ── Phase 4: Multi-family constants ───────────────────────────────────────────
# strategy_family: the research family (routing / grouping / filtering)
# strategy:        the specific baseline/rule identity within that family
STRATEGY_FAMILY_ORB = "09:30_ORB"        # ORB family (existing)
STRATEGY_FAMILY_FVG = "FVG_REVISIT"      # Phase 4 new family
FVG_STRATEGY_NAME   = "FVG_RESEARCH_BASELINE_V1"  # specific strategy within FVG family

# ── FVG interaction depth thresholds (Section 15 of spec) ────────────────────
_FVG_NEAR_EDGE_FRAC   = 0.20   # 0–20%  of gap from entry side → NEAR_EDGE
_FVG_SHALLOW_FRAC     = 0.40   # 20–40%                        → SHALLOW_FILL
_FVG_MIDPOINT_FRAC    = 0.60   # 40–60%                        → MIDPOINT
_FVG_DEEP_FRAC        = 0.80   # 60–80%                        → DEEP_FILL
# >80%                                                          → FULL_FILL

# Variant-specific depth requirements
_FVG_MIDPOINT_MIN_PCT  = 0.50  # MIDPOINT_ENTRY: 50%+ fill required
_FVG_DEEP_FILL_MIN_PCT = 0.70  # DEEP_FILL_ENTRY: 70%+ fill required

# FVG baseline target multiplier (2R; TP_1R and TP_1_5R are separate variants)
_FVG_BASELINE_TARGET_R = 2.0

# Maximum bars to wait for entry before expiring a WATCHING_ENTRY experiment
_FVG_MAX_WAITING_BARS = 60

MAX_GHOST_VARIANTS_PER_OPPORTUNITY = 10   # configurable ceiling
MAX_GHOST_VARIANTS_HARD_CAP        = 12   # absolute hard ceiling

# Cost model — mirrors profitability_engine.py defaults
GHOST_COMM_PER_SIDE_USD = 0.62
GHOST_SLIPPAGE_TICKS    = 1.0

# Entry window: minutes past midnight ET after which NO_ENTRY is forced
# Default OrbEngine entry window ends at 10:30 ET = 630 minutes
DEFAULT_ENTRY_END_MINUTES = 630

# Evidence state thresholds
THRESH_OBSERVING  = 25
THRESH_VALIDATION = 50
THRESH_READY_PF   = 1.25   # profit factor gate for PROMISING
THRESH_READY_EXP  = 0.05   # min positive net expectancy (R)
THRESH_READY_DAYS = 3      # distinct trading days

# Statistics
BOOTSTRAP_N_ITER  = 1000
MONTE_CARLO_N_SIM = 2000

# Rejection categories
class RejectionCat:
    MARKET          = "MARKET"
    CONFIRMATION    = "CONFIRMATION"
    CONTEXT_FILTER  = "CONTEXT_FILTER"
    RISK            = "RISK"
    PROP            = "PROP"
    DAILY_LOSS      = "DAILY_LOSS"
    POSITION_LIMIT  = "POSITION_LIMIT"
    EXECUTION_SAFETY= "EXECUTION_SAFETY"
    DATA            = "DATA"
    OTHER           = "OTHER"

_RISK_BLOCK_PREFIXES = ("BLOCKED_BY_INSTRUMENT_RISK", "BLOCKED_BY_GROUP_RISK",
                        "BLOCKED_BY_PORTFOLIO_RISK", "BLOCKED_BY_POSITION_LIMIT")
_PROP_BLOCK          = "BLOCKED_BY_PROP_RULE"
_DAILY_LOSS_BLOCK    = "BLOCKED_BY_DAILY_LOSS"
_EXEC_BLOCKS         = ("BLOCKED_BY_EXECUTION_MODE", "BLOCKED_BY_ARM_STATE",
                        "BLOCKED_BY_SAFETY_LOCK")
_DATA_BLOCKS         = ("BLOCKED_BY_DATA", "BLOCKED_BY_RANGE_WIDTH",
                        "BLOCKED_BY_MAXIMUM_CHASE")

def _rejection_category(orb_state: str, block_reason: str) -> str:
    s = (orb_state + "|" + block_reason).upper()
    if _PROP_BLOCK.upper()       in s: return RejectionCat.PROP
    if _DAILY_LOSS_BLOCK.upper() in s: return RejectionCat.DAILY_LOSS
    for p in _RISK_BLOCK_PREFIXES:
        if p.upper() in s: return RejectionCat.RISK
    for p in _EXEC_BLOCKS:
        if p.upper() in s: return RejectionCat.EXECUTION_SAFETY
    for p in _DATA_BLOCKS:
        if p.upper() in s: return RejectionCat.DATA
    if "BLOCKED_BY_CONFIRMATION" in s: return RejectionCat.CONFIRMATION
    if "BLOCKED" in s:                 return RejectionCat.MARKET
    return RejectionCat.OTHER

# ── Evidence states ───────────────────────────────────────────────────────────

class EvidenceState:
    INSUFFICIENT_DATA  = "INSUFFICIENT_DATA"
    OBSERVING          = "OBSERVING"
    PROMISING          = "PROMISING"
    VALIDATING         = "VALIDATING"
    READY_FOR_REVIEW   = "READY_FOR_REVIEW"
    REJECTED           = "REJECTED"
    RETIRED            = "RETIRED"

# ── Experiment result statuses ────────────────────────────────────────────────

class ResultStatus:
    PENDING        = "PENDING"         # waiting for entry
    WATCHING_ENTRY = "WATCHING_ENTRY"  # post-breakout, checking entry conditions
    ACTIVE         = "ACTIVE"          # entered, tracking exit
    COMPLETED      = "COMPLETED"       # final outcome recorded

class OutcomeResult:
    WIN                      = "WIN"
    LOSS                     = "LOSS"
    BREAKEVEN                = "BREAKEVEN"
    NO_ENTRY                 = "NO_ENTRY"
    EXPIRED                  = "EXPIRED"
    INVALID_DATA             = "INVALID_DATA"
    INVALIDATED_BEFORE_ENTRY = "INVALIDATED_BEFORE_ENTRY"  # Phase 4: zone gone before entry

# ── Variant catalogue ─────────────────────────────────────────────────────────

class Variant:
    BASELINE          = "BASELINE"
    TOUCH             = "TOUCH"
    CLOSE_AND_RETEST  = "CLOSE_AND_RETEST"
    BUFFER_PLUS_2     = "BUFFER_PLUS_2"
    BUFFER_MINUS_2    = "BUFFER_MINUS_2"
    TP_1R             = "TP_1R"
    TP_1_5R           = "TP_1_5R"
    TP_2R             = "TP_2R"
    TREND_REQUIRED    = "TREND_REQUIRED"
    CVD_ALIGNED       = "CVD_ALIGNED"   # replaces NO_TREND_FILTER (baseline already informational)

ALL_VARIANTS = [
    Variant.BASELINE,
    Variant.TOUCH,
    Variant.CLOSE_AND_RETEST,
    Variant.BUFFER_PLUS_2,
    Variant.BUFFER_MINUS_2,
    Variant.TP_1R,
    Variant.TP_1_5R,
    Variant.TP_2R,
    Variant.TREND_REQUIRED,
    Variant.CVD_ALIGNED,
]
assert len(ALL_VARIANTS) <= MAX_GHOST_VARIANTS_HARD_CAP

# ── Phase 4: FVG_REVISIT variant catalogue (Section 14 of spec) ──────────────

class FvgVariant:
    BASELINE             = "BASELINE"
    NEAR_EDGE_ENTRY      = "NEAR_EDGE_ENTRY"
    MIDPOINT_ENTRY       = "MIDPOINT_ENTRY"
    DEEP_FILL_ENTRY      = "DEEP_FILL_ENTRY"
    FIRST_TOUCH_ONLY     = "FIRST_TOUCH_ONLY"
    SECOND_TOUCH_ALLOWED = "SECOND_TOUCH_ALLOWED"
    TREND_REQUIRED       = "TREND_REQUIRED"
    CVD_ALIGNED          = "CVD_ALIGNED"
    TP_1R                = "TP_1R"
    TP_1_5R              = "TP_1_5R"

FVG_ALL_VARIANTS: List[str] = [
    FvgVariant.BASELINE,
    FvgVariant.NEAR_EDGE_ENTRY,
    FvgVariant.MIDPOINT_ENTRY,
    FvgVariant.DEEP_FILL_ENTRY,
    FvgVariant.FIRST_TOUCH_ONLY,
    FvgVariant.SECOND_TOUCH_ALLOWED,
    FvgVariant.TREND_REQUIRED,
    FvgVariant.CVD_ALIGNED,
    FvgVariant.TP_1R,
    FvgVariant.TP_1_5R,
]
assert len(FVG_ALL_VARIANTS) == 10
assert len(FVG_ALL_VARIANTS) <= MAX_GHOST_VARIANTS_HARD_CAP, (
    f"FVG_ALL_VARIANTS ({len(FVG_ALL_VARIANTS)}) exceeds hard cap ({MAX_GHOST_VARIANTS_HARD_CAP})"
)

# ── Tick sizes per instrument ─────────────────────────────────────────────────

_TICK_SIZE = {
    "MGC": 0.10,
    "MNQ": 0.25,
    "MES": 0.25,
    "MYM": 1.0,
}

_POINT_VALUE = {
    "MGC": 10.0,
    "MNQ": 2.0,
    "MES": 5.0,
    "MYM": 0.50,
}

def _tick(inst: str) -> float:
    return _TICK_SIZE.get(inst, 0.25)

# ── Small helpers ─────────────────────────────────────────────────────────────

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def _sn(v: Any) -> Optional[float]:
    """Safe numeric cast."""
    try:
        n = float(v)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None

def _ss(v: Any, fb: str = "") -> str:
    return v if isinstance(v, str) else fb

def _opportunity_id(inst: str, trading_date: str, breakout_bar_ts: Any, direction: str) -> str:
    key = f"GRE|{inst}|{trading_date}|{breakout_bar_ts}|{direction}"
    return hashlib.sha256(key.encode()).hexdigest()[:24]

def _experiment_id(opportunity_id: str, variant: str) -> str:
    return f"{opportunity_id}_{variant}"

def _result_id(experiment_id: str) -> str:
    return f"RES_{experiment_id}"

# ── Phase 4: FVG identity helpers ─────────────────────────────────────────────

_FVG_HASH_VERSION = "V1"  # bump if hashing logic changes (invalidates replay identity)

def _fvg_research_id(inst: str, direction: str, bar_ts: Any, upper: float, lower: float) -> str:
    """
    Deterministic 24-hex research identity for an FVG zone.
    Based only on IMMUTABLE creation attributes — touch_count/status/mitigation excluded.
    Same Databento bars replayed → same research_fvg_id regardless of source_fvg_id (uuid4).
    """
    key = f"FVGR_{_FVG_HASH_VERSION}|{inst}|{direction.upper()}|{bar_ts}|{upper:.4f}|{lower:.4f}"
    return hashlib.sha256(key.encode()).hexdigest()[:24]

def _fvg_revisit_id(rfid: str, revisit_n: int, revisit_bar_ts: Any) -> str:
    """
    Deterministic revisit session identity.
    Same revisit callback → same revisit_id.  Later revisit → different revisit_id.
    """
    key = f"FVGR_VISIT_{_FVG_HASH_VERSION}|{rfid}|{revisit_n}|{revisit_bar_ts}|{FVG_STRATEGY_NAME}"
    return hashlib.sha256(key.encode()).hexdigest()[:24]

def _fvg_opportunity_id(inst: str, rfid: str, revisit_n: int, revisit_bar_ts: Any) -> str:
    """One ghost opportunity row per FVG per revisit session."""
    key = f"FVGO_{_FVG_HASH_VERSION}|{inst}|{rfid}|{revisit_n}|{revisit_bar_ts}"
    return hashlib.sha256(key.encode()).hexdigest()[:24]

def _fvg_depth_pct(zone: Dict, bar_low: float, bar_high: float) -> float:
    """
    Fraction of the FVG gap filled by the bar (0.0 = entry edge untouched, 1.0 = full).
    BULLISH FVG: price revisits from upper side → depth from upper boundary down.
    BEARISH FVG: price revisits from lower side → depth from lower boundary up.
    """
    upper = float(zone.get("upper", 0))
    lower = float(zone.get("lower", 0))
    gap   = max(upper - lower, 1e-9)
    if str(zone.get("direction", "")).upper() == "BULLISH":
        penetration = upper - max(bar_low, lower)
    else:
        penetration = min(bar_high, upper) - lower
    return max(0.0, min(1.0, penetration / gap))

def _fvg_classify_location(depth_pct: float) -> str:
    if depth_pct <= _FVG_NEAR_EDGE_FRAC:  return "NEAR_EDGE"
    if depth_pct <= _FVG_SHALLOW_FRAC:    return "SHALLOW_FILL"
    if depth_pct <= _FVG_MIDPOINT_FRAC:   return "MIDPOINT"
    if depth_pct <= _FVG_DEEP_FRAC:       return "DEEP_FILL"
    return "FULL_FILL"

def _fvg_bar_overlaps(zone: Dict, bar_low: float, bar_high: float) -> bool:
    """True if the bar's range overlaps the FVG zone at all."""
    return bar_low <= float(zone.get("upper", 0)) and bar_high >= float(zone.get("lower", 0))

# ── Profitability helpers (reuse from profitability_engine) ───────────────────

def _compute_gross_r(direction: str, entry: float, exit_price: float, stop: float) -> Optional[float]:
    try:
        entry = float(entry); exit_price = float(exit_price); stop = float(stop)
        risk = abs(entry - stop)
        if risk <= 0: return None
        if direction == "Long":  return (exit_price - entry) / risk
        if direction == "Short": return (entry - exit_price) / risk
        return None
    except (TypeError, ValueError):
        return None

def _update_mfe_mae(
    direction: str, bar_high: float, bar_low: float,
    entry: float, risk_points: float,
    cur_mfe_price: Optional[float], cur_mae_price: Optional[float],
    cur_mfe_r: float, cur_mae_r: float,
) -> Tuple[float, float, Optional[float], Optional[float]]:
    """Returns (mfe_r, mae_r, mfe_price, mae_price)."""
    try:
        if risk_points <= 0: return cur_mfe_r, cur_mae_r, cur_mfe_price, cur_mae_price
        if direction == "Long":
            fav = bar_high; adv = bar_low
            fav_r = (fav - entry) / risk_points
            adv_r = (adv - entry) / risk_points
        else:
            fav = bar_low; adv = bar_high
            fav_r = (entry - fav) / risk_points
            adv_r = (entry - adv) / risk_points
        new_mfe_r     = max(cur_mfe_r, fav_r)
        new_mae_r     = min(cur_mae_r, adv_r)
        new_mfe_price = fav if fav_r >= cur_mfe_r else cur_mfe_price
        new_mae_price = adv if adv_r <= cur_mae_r else cur_mae_price
        return new_mfe_r, new_mae_r, new_mfe_price, new_mae_price
    except Exception:
        return cur_mfe_r, cur_mae_r, cur_mfe_price, cur_mae_price

def _commission_r(inst: str, entry: float, stop: float,
                  comm_per_side: float = GHOST_COMM_PER_SIDE_USD,
                  slip_ticks: float = GHOST_SLIPPAGE_TICKS) -> float:
    risk_pts  = abs(entry - stop)
    tick_sz   = _TICK_SIZE.get(inst, 0.25)
    pt_val    = _POINT_VALUE.get(inst, 2.0)
    if risk_pts <= 0 or pt_val <= 0: return 0.0
    comm_cost  = comm_per_side * 2                      # round-trip
    slip_cost  = slip_ticks * tick_sz * pt_val * 2      # entry + exit
    return (comm_cost + slip_cost) / (risk_pts * pt_val)

def _compute_max_drawdown(r_series: List[float]) -> float:
    if not r_series: return 0.0
    peak = cumsum = 0.0
    worst = 0.0
    for r in r_series:
        cumsum += r
        peak = max(peak, cumsum)
        worst = min(worst, cumsum - peak)
    return round(worst, 4)

# ── Statistics aggregation ────────────────────────────────────────────────────

def _aggregate_results(rows: List[Dict]) -> Dict:
    """Compute statistics from a list of completed result dicts."""
    entered = [r for r in rows if r.get("result") not in (OutcomeResult.NO_ENTRY, OutcomeResult.EXPIRED, None)]
    closed  = [r for r in entered if r.get("result") in (OutcomeResult.WIN, OutcomeResult.LOSS, OutcomeResult.BREAKEVEN)]

    wins      = [r for r in closed if r.get("result") == OutcomeResult.WIN]
    losses    = [r for r in closed if r.get("result") == OutcomeResult.LOSS]
    net_rs    = [r["net_r"] for r in closed if r.get("net_r") is not None]
    gross_rs  = [r["gross_r"] for r in closed if r.get("gross_r") is not None]
    winner_rs = [r["net_r"] for r in wins if r.get("net_r") is not None]
    loser_rs  = [r["net_r"] for r in losses if r.get("net_r") is not None]
    mfe_rs    = [r["mfe_r"] for r in entered if r.get("mfe_r") is not None]
    mae_rs    = [r["mae_r"] for r in entered if r.get("mae_r") is not None]

    win_rate  = (len(wins) / len(closed)) if closed else None
    avg_net_r = (sum(net_rs) / len(net_rs)) if net_rs else None
    avg_grs   = (sum(gross_rs) / len(gross_rs)) if gross_rs else None
    avg_win   = (sum(winner_rs) / len(winner_rs)) if winner_rs else None
    avg_loss  = (sum(loser_rs) / len(loser_rs)) if loser_rs else None
    cum_net   = sum(net_rs) if net_rs else 0.0
    cum_grs   = sum(gross_rs) if gross_rs else 0.0

    gross_wins  = sum(r for r in winner_rs if r > 0)
    gross_loss  = abs(sum(r for r in loser_rs if r < 0))
    profit_fac  = (gross_wins / gross_loss) if gross_loss > 0 else (float("inf") if gross_wins > 0 else None)

    net_rs_sorted = sorted(net_rs)
    median_r  = net_rs_sorted[len(net_rs_sorted)//2] if net_rs_sorted else None
    max_winner = max(net_rs_sorted) if net_rs_sorted else None
    max_loser  = min(net_rs_sorted) if net_rs_sorted else None

    # running drawdown over chronological results
    dd = _compute_max_drawdown(net_rs)

    # std dev
    std_dev = None
    if len(net_rs) >= 2:
        mean = sum(net_rs) / len(net_rs)
        variance = sum((r - mean) ** 2 for r in net_rs) / (len(net_rs) - 1)
        std_dev = math.sqrt(variance)

    # distinct trading days (from opportunity_id prefix isn't enough, use result timestamps)
    distinct_days = len(set(
        r.get("entry_timestamp", "")[:10] for r in closed if r.get("entry_timestamp")
    ))

    return {
        "sample_count":     len(rows),
        "entered_count":    len(entered),
        "no_entry_count":   len(rows) - len(entered),
        "closed_count":     len(closed),
        "win_count":        len(wins),
        "loss_count":       len(losses),
        "breakeven_count":  len([r for r in closed if r.get("result") == OutcomeResult.BREAKEVEN]),
        "win_rate":         win_rate,
        "avg_gross_r":      avg_grs,
        "avg_net_r":        avg_net_r,
        "median_r":         median_r,
        "net_expectancy":   avg_net_r,
        "profit_factor":    profit_fac,
        "cumulative_gross_r": cum_grs,
        "cumulative_net_r": cum_net,
        "avg_winner_r":     avg_win,
        "avg_loser_r":      avg_loss,
        "largest_winner":   max_winner,
        "largest_loser":    max_loser,
        "max_drawdown":     dd,
        "avg_mfe":          (sum(mfe_rs) / len(mfe_rs)) if mfe_rs else None,
        "avg_mae":          (sum(mae_rs) / len(mae_rs)) if mae_rs else None,
        "std_dev":          std_dev,
        "distinct_days":    distinct_days,
    }

# ── Bootstrap CI ─────────────────────────────────────────────────────────────

def _bootstrap_ci(
    net_rs: List[float],
    n_boot: int = BOOTSTRAP_N_ITER,
    confidence: float = 0.95,
    seed: int = 42,
) -> Dict:
    """Deterministic bootstrap confidence interval for net expectancy."""
    n = len(net_rs)
    if n < 5:
        return {"expectancy": None, "ci_low": None, "ci_high": None,
                "confidence": confidence, "sample_count": n, "status": "INSUFFICIENT_SAMPLES"}
    rng = random.Random(seed)
    boot_means = []
    for _ in range(n_boot):
        sample = [rng.choice(net_rs) for _ in range(n)]
        boot_means.append(sum(sample) / n)
    boot_means.sort()
    alpha = (1 - confidence) / 2
    lo_idx = int(alpha * n_boot)
    hi_idx = int((1 - alpha) * n_boot)
    return {
        "expectancy":   sum(net_rs) / n,
        "ci_low":       boot_means[lo_idx],
        "ci_high":      boot_means[min(hi_idx, len(boot_means) - 1)],
        "confidence":   confidence,
        "sample_count": n,
        "n_bootstrap":  n_boot,
        "status":       "OK",
    }

# ── Monte Carlo drawdown ──────────────────────────────────────────────────────

def _monte_carlo_drawdown(
    net_rs: List[float],
    n_sims: int = MONTE_CARLO_N_SIM,
    seed: int = 42,
) -> Dict:
    """Deterministic Monte Carlo drawdown simulation.

    Risk convention: pN_dd is the Nth percentile WORST CASE.
    - p95_dd means 95% of simulations had drawdown worse than this value.
    - Values are non-positive (or 0.0 if always profitable).
    - p95_dd is more negative than median_dd (worse tail scenario).
    """
    n = len(net_rs)
    if n < 5:
        return {"status": "INSUFFICIENT_SAMPLES", "sample_count": n,
                "median_dd": None, "p90_dd": None, "p95_dd": None, "worst_dd": None}
    rng = random.Random(seed)
    sim_dds = []
    for _ in range(n_sims):
        shuffled = net_rs[:]
        rng.shuffle(shuffled)
        sim_dds.append(_compute_max_drawdown(shuffled))
    sim_dds.sort()  # ascending: most negative (worst) → least negative (best)

    def _worst_pct(p: float) -> float:
        """Nth worst-case percentile: p=0.95 → 5th percentile of ascending list."""
        idx = int((1.0 - p) * len(sim_dds))
        return sim_dds[max(0, min(idx, len(sim_dds) - 1))]

    return {
        "status":        "OK",
        "sample_count":  n,
        "n_simulations": n_sims,
        "median_dd":     _worst_pct(0.50),  # 50th pct worst = median
        "p90_dd":        _worst_pct(0.90),  # 90th pct worst (more negative than median)
        "p95_dd":        _worst_pct(0.95),  # 95th pct worst (most negative of the three)
        "worst_dd":      min(sim_dds),
    }

# ── Evidence state machine ────────────────────────────────────────────────────

def _compute_evidence_state(
    stats: Dict,
    current_state: str,
    bootstrap: Optional[Dict] = None,
    mc: Optional[Dict] = None,
    is_forward_frozen: bool = False,
) -> str:
    """Pure function — never modifies live config.

    Non-regression rule: RETIRED, REJECTED, READY_FOR_REVIEW, and VALIDATING
    are terminal or protected states that are never regressed to a lower tier
    by new statistics alone.  READY_FOR_REVIEW in particular requires a
    deliberate operator action to change.
    """
    n    = stats.get("closed_count", 0)
    exp  = stats.get("net_expectancy")
    pf   = stats.get("profit_factor")
    dd   = stats.get("max_drawdown", 0)
    days = stats.get("distinct_days", 0)

    # Terminal / protected — non-regression guard runs FIRST before any sample count check
    if current_state in (EvidenceState.RETIRED, EvidenceState.REJECTED,
                         EvidenceState.READY_FOR_REVIEW, EvidenceState.VALIDATING):
        return current_state

    if n < THRESH_OBSERVING:
        return EvidenceState.INSUFFICIENT_DATA

    if n < THRESH_VALIDATION:
        return EvidenceState.OBSERVING

    # n >= 50: evaluate for PROMISING
    if exp is None or exp <= THRESH_READY_EXP:
        return EvidenceState.OBSERVING
    if pf is None or pf < THRESH_READY_PF:
        return EvidenceState.OBSERVING
    if days < THRESH_READY_DAYS:
        return EvidenceState.OBSERVING

    # CI check
    if bootstrap and bootstrap.get("status") == "OK":
        if (bootstrap.get("ci_low") or 0) <= 0:
            return EvidenceState.OBSERVING  # CI crosses zero — not compelling

    # Monte Carlo drawdown sanity
    if mc and mc.get("status") == "OK":
        if abs(mc.get("p95_dd") or 0) > 5.0:  # >5R p95 drawdown
            return EvidenceState.OBSERVING

    # Sufficient evidence → PROMISING or VALIDATING
    if is_forward_frozen:
        return EvidenceState.VALIDATING

    # Check if already VALIDATING or READY_FOR_REVIEW — don't regress
    if current_state in (EvidenceState.VALIDATING, EvidenceState.READY_FOR_REVIEW):
        return current_state

    return EvidenceState.PROMISING

def _gate_ready_for_review(stats: Dict, bootstrap: Dict, mc: Dict) -> bool:
    """Additional gate for READY_FOR_REVIEW — never auto-applies to live."""
    n   = stats.get("closed_count", 0)
    exp = stats.get("net_expectancy")
    pf  = stats.get("profit_factor")
    days = stats.get("distinct_days", 0)
    if n < THRESH_VALIDATION: return False
    if exp is None or exp <= THRESH_READY_EXP: return False
    if pf  is None or pf  < THRESH_READY_PF:  return False
    if days < THRESH_READY_DAYS:               return False
    if bootstrap.get("status") != "OK":        return False
    if (bootstrap.get("ci_low") or 0) <= 0:   return False
    return True

# ── Main engine ───────────────────────────────────────────────────────────────

class GhostResearchEngine:
    """
    Phase 2 Ghost Research & Evidence Engine.

    Wired into app.py's _orb_bar_close() hook (AFTER OrbEngine processes).
    Fail-open throughout — never interrupts live processing.
    """

    # Class-level DB readiness flag (set by boot probe, mirrors GHOST_OBS_DB_READY pattern)
    GRE_DB_READY: bool = False

    def __init__(
        self,
        get_db_fn: Callable,
        get_canonical_fn: Callable,
        get_bars_fn: Callable,
        re_event_fn: Callable,
        instruments: List[str],
        max_variants: int = MAX_GHOST_VARIANTS_PER_OPPORTUNITY,
        dc_registry_fn: Optional[Callable] = None,
    ) -> None:
        self._get_db       = get_db_fn
        self._get_can      = get_canonical_fn
        self._get_bars     = get_bars_fn
        self._re_event     = re_event_fn
        self._instruments  = list(instruments)
        self._max_variants = min(max_variants, MAX_GHOST_VARIANTS_HARD_CAP)
        # Optional lazy getter for the DecisionRegistry — provided by app.py at boot.
        # Called at opportunity-freeze time (never at init) so DC being initialised
        # after GRE is safe. None = DC enrichment silently skipped (fail-open).
        self._dc_registry_fn: Optional[Callable] = dc_registry_fn

        # Per-instrument last-seen OrbEngine state (for transition detection)
        self._last_orb_state:  Dict[str, str] = {i: "" for i in instruments}
        # active opportunity_id per instrument per trading date
        self._active_opp:      Dict[str, str] = {}  # inst → opportunity_id
        # in-memory open experiment results (result_id → dict)
        self._open_results:    Dict[str, Dict] = {}
        # per-result sub-state for CLOSE_AND_RETEST
        self._retest_state:    Dict[str, str] = {}  # result_id → "WAITING_RETEST" | ""

        self._lock     = threading.Lock()
        self._stats_cache: Dict = {}  # lightweight in-memory stats cache
        self._stats_ts:    float = 0.0

        self._log = logger.getChild("GRE")

        # ── Phase 4: FVG_REVISIT tracking ─────────────────────────────────────
        # Per research_fvg_id: was bar inside zone on the PREVIOUS bar?
        self._fvg_inside_prev:   Dict[str, bool] = {}
        # Per research_fvg_id: how many revisit sessions detected so far today?
        self._fvg_revisit_count: Dict[str, int]  = {}
        # Per "rfid|n": opportunity_id already created (prevents duplicates)
        self._fvg_opp_created:   Dict[str, str]  = {}

    # ── Boot ─────────────────────────────────────────────────────────────────

    def boot(self) -> None:
        """Probe DB tables and restore active experiments from previous session."""
        try:
            db = self._get_db()
            db.cursor().execute("SELECT 1 FROM ghost_opportunities LIMIT 1")
            db.cursor().execute("SELECT 1 FROM ghost_experiments LIMIT 1")
            db.cursor().execute("SELECT 1 FROM ghost_experiment_results LIMIT 1")
            GhostResearchEngine.GRE_DB_READY = True
            self._log.info("GhostResearchEngine: DB tables ready")
        except Exception as exc:
            self._log.warning("GhostResearchEngine: DB probe failed (fail-open): %s", exc)
            GhostResearchEngine.GRE_DB_READY = False
            return

        try:
            self._restore_active_experiments()
        except Exception as exc:
            self._log.warning("GhostResearchEngine: restore failed (fail-open): %s", exc)

    def _restore_active_experiments(self) -> None:
        """Reload PENDING/WATCHING_ENTRY/ACTIVE experiments from DB on restart."""
        db  = self._get_db()
        cur = db.cursor()
        cur.execute("""
            SELECT r.result_id, r.experiment_id, r.opportunity_id, r.status,
                   r.entry_price, r.stop_price, r.tp1_price, r.tp2_price,
                   r.mfe_r, r.mae_r, r.mfe_price, r.mae_price,
                   r.bars_held, r.last_bar_ts,
                   e.variant_name, e.planned_entry, e.planned_stop,
                   e.planned_tp1, e.planned_tp2, e.filter_rules, e.entry_rule,
                   o.instrument, o.direction, o.trading_date, o.breakout_direction,
                   o.breakout_level, o.or_high, o.or_low,
                   o.trend_15m, o.trend_4h, o.cvd_direction
            FROM ghost_experiment_results r
            JOIN ghost_experiments e ON e.experiment_id = r.experiment_id
            JOIN ghost_opportunities o ON o.opportunity_id = r.opportunity_id
            WHERE r.status IN ('PENDING','WATCHING_ENTRY','ACTIVE')
        """)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        restored = 0
        with self._lock:
            for row in rows:
                d = dict(zip(cols, row))
                # Mark FVG experiments so _process_open_experiments skips them
                if str(d.get("entry_rule", "")).startswith("FVG_"):
                    d["_fvg_family"] = True
                self._open_results[d["result_id"]] = d
                restored += 1
        self._log.info("GhostResearchEngine: restored %d active experiments", restored)

        # Restore FVG revisit dedup state from today's DB records
        try:
            today = datetime.now(timezone.utc).date().isoformat()
            cur.execute("""
                SELECT research_fvg_id, revisit_id, opportunity_id,
                       (extra_snapshot->>'fvg_revisit_number')::int AS revisit_n
                FROM ghost_opportunities
                WHERE strategy_family = %s AND trading_date = %s
                  AND research_fvg_id IS NOT NULL
            """, (STRATEGY_FAMILY_FVG, today))
            fvg_rows = cur.fetchall()
            with self._lock:
                for rfid, _rev_id, opp_id, rn in fvg_rows:
                    if rfid and rn is not None:
                        opp_key = f"{rfid}|{rn}"
                        self._fvg_opp_created[opp_key] = opp_id or ""
                        prev = self._fvg_revisit_count.get(rfid, 0)
                        if rn > prev:
                            self._fvg_revisit_count[rfid] = rn
            self._log.info("GRE: restored %d FVG revisit dedup entries", len(fvg_rows))
        except Exception as exc:
            self._log.debug("GRE FVG revisit restore (fail-open): %s", exc)

    # ── Main bar-close hook ───────────────────────────────────────────────────

    def on_bar_close(self, inst: str, orb_status: Dict, price: float) -> None:
        """
        Called from app.py _orb_bar_close() AFTER OrbEngine.on_bar_close().
        Fail-open: any exception is caught and logged; live processing is never blocked.
        """
        if not GhostResearchEngine.GRE_DB_READY:
            return
        try:
            bars = self._get_bars(inst)
            last_bar = bars[-1] if bars else None
            self._process_inst(inst, orb_status, last_bar, price)
        except Exception as exc:
            self._log.debug("GhostResearchEngine bar-close (%s): %s", inst, exc)

    def _process_inst(self, inst: str, orb: Dict, bar: Optional[Dict], price: float) -> None:
        state      = _ss(orb.get("state"), "UNKNOWN")
        prev_state = self._last_orb_state.get(inst, "")
        trading_date = _ss(orb.get("trading_date"), "")

        # ── Detect new opportunity on BREAKOUT_DETECTED ───────────────────
        if state == "BREAKOUT_DETECTED" and prev_state != "BREAKOUT_DETECTED":
            self._on_breakout_detected(inst, orb, bar, price, trading_date)

        # ── When OrbEngine qualifies → update opportunity + enter BASELINE ─
        if state == "POSITION_ACTIVE" and prev_state not in ("POSITION_ACTIVE",):
            self._on_position_active(inst, orb, trading_date)

        # ── When OrbEngine blocks → update opportunity rejection ──────────
        if state.startswith("BLOCKED_") and not prev_state.startswith("BLOCKED_"):
            self._on_blocked(inst, orb, state, trading_date)

        if state == "BREAKOUT_MISSED" and prev_state != "BREAKOUT_MISSED":
            self._on_breakout_missed(inst, orb, trading_date)

        # ── Update state tracking ─────────────────────────────────────────
        self._last_orb_state[inst] = state

        # ── Process all open experiments for this instrument ──────────────
        if bar is not None:
            self._process_open_experiments(inst, bar, price, orb)

    # ── Opportunity detection ─────────────────────────────────────────────────

    def _on_breakout_detected(self, inst: str, orb: Dict, bar: Optional[Dict],
                               price: float, trading_date: str) -> None:
        direction = _ss(orb.get("breakout_direction"), "")
        if not direction or not trading_date:
            return

        bk_ts  = orb.get("breakout_bar_ts") or (bar.get("ts") if bar else None)
        opp_id = _opportunity_id(inst, trading_date, bk_ts, direction)

        # Dedup: one opportunity per inst per day per direction
        with self._lock:
            if self._active_opp.get(inst) == opp_id:
                return
            self._active_opp[inst] = opp_id

        canonical = {}
        try:
            canonical = self._get_can(inst) or {}
        except Exception:
            pass

        snapshot = self._build_snapshot(inst, orb, bar, price, canonical)

        # ── Enrich snapshot with canonical Decision Contract state (FAIL-OPEN) ─
        # The DC registry is provided lazily via _dc_registry_fn (app.py passes a
        # lambda: globals().get("_DECISION_REGISTRY")).  Enrichment is IMMUTABLE —
        # snapshot captures the DC state AT opportunity-freeze time; later DC
        # transitions never touch this dict or the persisted ghost_opportunities row.
        try:
            if self._dc_registry_fn is not None:
                _dc = self._dc_registry_fn()
                if _dc is not None:
                    _rec = _dc.get_record(inst)
                    if _rec is not None:
                        from decision_contract import enrich_ghost_snapshot as _egs  # lazy import
                        snapshot = _egs(snapshot, _rec)
        except Exception as _dc_enrich_exc:
            self._log.debug("GRE DC enrich (%s): %s", inst, _dc_enrich_exc)

        ok = self._insert_opportunity(opp_id, inst, trading_date, direction, orb, snapshot)
        if not ok:
            return

        self._re_event("ORB_OPPORTUNITY_RECORDED", inst=inst,
                       extra={"opportunity_id": opp_id, "direction": direction})

        # Create variants
        n = self._create_variants(opp_id, inst, direction, orb, snapshot)
        self._re_event("GHOST_VARIANTS_CREATED", inst=inst,
                       extra={"opportunity_id": opp_id, "count": n})
        self._log.info("GRE [%s] opportunity %s — %d variants created", inst, opp_id, n)

    def _on_position_active(self, inst: str, orb: Dict, trading_date: str) -> None:
        """OrbEngine qualified. Update planned entry/stop/tp on the opportunity and enter BASELINE."""
        direction = _ss(orb.get("breakout_direction"), "")
        bk_ts  = orb.get("breakout_bar_ts")
        opp_id = self._active_opp.get(inst)
        if not opp_id:
            return

        entry = _sn(orb.get("entry"))
        stop  = _sn(orb.get("stop"))
        tp1   = _sn(orb.get("tp1"))
        tp2   = _sn(orb.get("tp2"))
        contr = orb.get("contracts")

        try:
            db = self._get_db()
            db.cursor().execute("""
                UPDATE ghost_opportunities
                   SET entry_price_planned=%s, stop_price_planned=%s,
                       tp1_planned=%s, tp2_planned=%s, contracts_planned=%s
                 WHERE opportunity_id=%s AND entry_price_planned IS NULL
            """, (entry, stop, tp1, tp2, contr, opp_id))
            db.commit()
        except Exception as exc:
            self._log.debug("GRE update opp plan (%s): %s", inst, exc)

        # Enter BASELINE experiment immediately (OrbEngine already took this trade)
        if entry and stop:
            baseline_exp_id = _experiment_id(opp_id, Variant.BASELINE)
            result_id       = _result_id(baseline_exp_id)
            cost_r = _commission_r(inst, entry, stop)
            self._enter_experiment(result_id, baseline_exp_id, opp_id, entry, stop, tp1, tp2,
                                   cost_r=cost_r, qualified_ts=_now_utc())
            self._re_event("GHOST_EXPERIMENT_ENTERED", inst=inst,
                           extra={"result_id": result_id, "variant": Variant.BASELINE})

    def _on_blocked(self, inst: str, orb: Dict, state: str, trading_date: str) -> None:
        opp_id = self._active_opp.get(inst)
        if not opp_id: return
        block  = _ss(orb.get("block_reason"), state)
        cat    = _rejection_category(state, block)
        try:
            db = self._get_db()
            db.cursor().execute("""
                UPDATE ghost_opportunities
                   SET rejection_category=%s, block_reason=%s
                 WHERE opportunity_id=%s
            """, (cat, block, opp_id))
            db.commit()
        except Exception as exc:
            self._log.debug("GRE update rejection (%s): %s", inst, exc)
        # Mark all PENDING experiments as their entry simulation can still run
        # (we research "would this have won if we'd ignored the blocker?")

    def _on_breakout_missed(self, inst: str, orb: Dict, trading_date: str) -> None:
        """Entry window closed without a qualified trade. Force-expire WATCHING_ENTRY experiments."""
        opp_id = self._active_opp.get(inst)
        if not opp_id: return
        with self._lock:
            for result_id, rd in list(self._open_results.items()):
                if rd.get("opportunity_id") == opp_id and rd.get("status") in (
                        ResultStatus.PENDING, ResultStatus.WATCHING_ENTRY):
                    self._complete_experiment(result_id, exit_price=None,
                                              exit_reason="BREAKOUT_MISSED",
                                              result=OutcomeResult.NO_ENTRY,
                                              exit_ts=_now_utc())

    # ── Opportunity DB insert ─────────────────────────────────────────────────

    def _build_snapshot(self, inst: str, orb: Dict, bar: Optional[Dict],
                        price: float, canonical: Dict) -> Dict:
        """Extract canonical context snapshot. UNKNOWN stays UNKNOWN."""
        trend   = canonical.get("trend") or {}
        cvd     = canonical.get("cvd") or {}
        volume  = canonical.get("volume") or {}
        vwap    = canonical.get("vwap") or {}
        atr_d   = canonical.get("atr") or {}
        struct  = canonical.get("structure") or {}

        vwap_val = _sn(vwap.get("vwap") or vwap.get("value"))
        vwap_side = _ss(vwap.get("side") or vwap.get("vwap_side"), "UNKNOWN")
        vwap_dist = None
        if vwap_val and price and abs(price) > 0:
            vwap_dist = round((price - vwap_val) / price * 100, 4)

        bar_high = _sn(bar.get("high")) if bar else None
        bar_low  = _sn(bar.get("low"))  if bar else None

        return {
            "current_price":   price,
            "bar_high":        bar_high,
            "bar_low":         bar_low,
            "current_atr":     _sn(orb.get("current_atr")),
            "current_vwap":    vwap_val,
            "vwap_side":       vwap_side,
            "vwap_distance_pct": vwap_dist,
            "trend_15m":       _ss(trend.get("trend_15m"), "UNKNOWN"),
            "trend_4h":        _ss(trend.get("trend_4h"), "UNKNOWN"),
            "trend_alignment": _ss(trend.get("alignment") or trend.get("trend_alignment"), "UNKNOWN"),
            "cvd_direction":   _ss(cvd.get("direction") or cvd.get("state"), "UNKNOWN"),
            "cvd_value":       _sn(cvd.get("value")),
            "volume_value":    _sn(volume.get("volume") or volume.get("value")),
            "relative_volume": _sn(volume.get("relative_volume") or volume.get("databento_rvol")),
            "structure_bos":   bool(struct.get("bos") or struct.get("bullish_bos") or struct.get("bearish_bos")),
            "structure_choch": bool(struct.get("choch") or struct.get("choch_bullish") or struct.get("choch_bearish")),
        }

    def _insert_opportunity(self, opp_id: str, inst: str, trading_date: str,
                            direction: str, orb: Dict, snap: Dict) -> bool:
        try:
            db  = self._get_db()
            cur = db.cursor()
            bk_level = _sn(orb.get("long_breakout_level") if direction == "Long"
                           else orb.get("short_breakout_level"))
            cur.execute("""
                INSERT INTO ghost_opportunities
                    (opportunity_id, trading_date, instrument, strategy, strategy_version,
                     config_version, orb_state, orb_event, direction, breakout_direction,
                     or_high, or_low, or_width, or_midpoint, range_duration_min,
                     range_locked_ts, breakout_bar_ts, breakout_level, confirmation_mode,
                     max_chase_pct, current_price, current_atr, current_vwap, vwap_side,
                     vwap_distance_pct, trend_15m, trend_4h, trend_alignment,
                     cvd_direction, cvd_value, volume_value, relative_volume,
                     structure_bos, structure_choch,
                     dc_decision_id, dc_state, dc_reason_code, dc_verdict,
                     dc_edge_score, dc_confidence, dc_qualified, dc_risk_status,
                     dc_execution_mode, dc_execution_enabled, dc_armed,
                     dc_parity_agree, dc_version)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (opportunity_id) DO NOTHING
                RETURNING id
            """, (
                opp_id, trading_date, inst, STRATEGY_NAME,
                _ss(orb.get("strategy_version"), "unknown"),
                _ss(orb.get("config_version"), "unknown"),
                "BREAKOUT_DETECTED", "BREAKOUT_DETECTED",
                direction, direction,
                _sn(orb.get("or_high")), _sn(orb.get("or_low")),
                _sn(orb.get("or_width")), _sn(orb.get("or_midpoint")),
                orb.get("range_duration_min"),
                _ss(orb.get("range_locked_ts"), None),
                orb.get("breakout_bar_ts"),
                bk_level,
                _ss(orb.get("confirmation_mode"), "UNKNOWN"),
                _sn(orb.get("max_chase_pct") or orb.get("config", {}).get("max_chase_pct")),
                snap.get("current_price"), snap.get("current_atr"),
                snap.get("current_vwap"), snap.get("vwap_side"),
                snap.get("vwap_distance_pct"),
                snap.get("trend_15m"), snap.get("trend_4h"), snap.get("trend_alignment"),
                snap.get("cvd_direction"), snap.get("cvd_value"),
                snap.get("volume_value"), snap.get("relative_volume"),
                snap.get("structure_bos"), snap.get("structure_choch"),
                # ── Canonical Decision Contract enrichment (NULL when DC unavailable) ──
                snap.get("canonical_decision_id"),
                snap.get("canonical_decision_state"),
                snap.get("canonical_reason_code"),
                snap.get("live_verdict"),
                snap.get("edge_score"),
                snap.get("confidence"),
                snap.get("qualification_state"),
                snap.get("risk_status"),
                snap.get("execution_mode"),
                snap.get("execution_enabled"),
                snap.get("armed"),
                snap.get("parity_agree"),
                snap.get("dc_version"),
            ))
            row = cur.fetchone()
            db.commit()
            return row is not None  # False if ON CONFLICT (already exists)
        except Exception as exc:
            self._log.warning("GRE insert opportunity (%s): %s", inst, exc)
            return False

    # ── Variant creation ──────────────────────────────────────────────────────

    def _create_variants(self, opp_id: str, inst: str, direction: str,
                         orb: Dict, snap: Dict) -> int:
        """Create up to max_variants experiment rows. Returns count inserted."""
        tick = _tick(inst)
        conf_mode = _ss(orb.get("confirmation_mode"), "CLOSE_OUTSIDE")
        entry = _sn(orb.get("entry"))
        stop  = _sn(orb.get("stop"))
        tp1   = _sn(orb.get("tp1"))
        tp2   = _sn(orb.get("tp2"))
        contr = orb.get("contracts")
        sv    = _ss(orb.get("strategy_version"), "unknown")
        cv    = _ss(orb.get("config_version"), "unknown")

        # breakout level for buffer variants
        bk_level = _sn(orb.get("long_breakout_level") if direction == "Long"
                       else orb.get("short_breakout_level"))

        # risk R for TP variants
        risk_pts = abs((entry or 0) - (stop or 0)) if entry and stop else None

        variants: List[Dict] = []

        # 1. BASELINE — exact OrbEngine configuration
        variants.append({
            "variant": Variant.BASELINE,
            "parameter_diff": {},
            "entry_rule": conf_mode,
            "confirmation_rule": conf_mode,
            "stop_rule": "FIXED",
            "target_rule": "ORB_CONFIG",
            "filter_rules": {},
            "planned_entry": entry, "planned_stop": stop,
            "planned_tp1": tp1, "planned_tp2": tp2,
            "planned_contracts": contr,
        })

        # 2. TOUCH — entry on first bar that touches the breakout level
        variants.append({
            "variant": Variant.TOUCH,
            "parameter_diff": {"confirmation_mode": {"baseline": conf_mode, "variant": "TOUCH"}},
            "entry_rule": "TOUCH",
            "confirmation_rule": "TOUCH",
            "stop_rule": "FIXED",
            "target_rule": "ORB_CONFIG",
            "filter_rules": {},
            "planned_entry": bk_level, "planned_stop": stop,
            "planned_tp1": tp1, "planned_tp2": tp2,
            "planned_contracts": contr,
        })

        # 3. CLOSE_AND_RETEST — close outside then retest the level
        variants.append({
            "variant": Variant.CLOSE_AND_RETEST,
            "parameter_diff": {"confirmation_mode": {"baseline": conf_mode, "variant": "CLOSE_AND_RETEST"}},
            "entry_rule": "CLOSE_AND_RETEST",
            "confirmation_rule": "CLOSE_AND_RETEST",
            "stop_rule": "FIXED",
            "target_rule": "ORB_CONFIG",
            "filter_rules": {},
            "planned_entry": bk_level, "planned_stop": stop,
            "planned_tp1": tp1, "planned_tp2": tp2,
            "planned_contracts": contr,
        })

        # 4. BUFFER_PLUS_2 — breakout level + 2 ticks (entry requires more conviction)
        if bk_level is not None:
            buf_entry = (bk_level + 2 * tick) if direction == "Long" else (bk_level - 2 * tick)
            variants.append({
                "variant": Variant.BUFFER_PLUS_2,
                "parameter_diff": {"breakout_buffer_ticks": {"baseline": 0, "variant": 2}},
                "entry_rule": "CLOSE_OUTSIDE",
                "confirmation_rule": "CLOSE_OUTSIDE",
                "stop_rule": "FIXED",
                "target_rule": "ORB_CONFIG",
                "filter_rules": {"breakout_level_offset_ticks": 2},
                "planned_entry": buf_entry, "planned_stop": stop,
                "planned_tp1": tp1, "planned_tp2": tp2,
                "planned_contracts": contr,
            })

        # 5. BUFFER_MINUS_2 — 2 ticks closer to range (earlier entry)
        if bk_level is not None:
            buf2_entry = (bk_level - 2 * tick) if direction == "Long" else (bk_level + 2 * tick)
            variants.append({
                "variant": Variant.BUFFER_MINUS_2,
                "parameter_diff": {"breakout_buffer_ticks": {"baseline": 0, "variant": -2}},
                "entry_rule": "CLOSE_OUTSIDE",
                "confirmation_rule": "CLOSE_OUTSIDE",
                "stop_rule": "FIXED",
                "target_rule": "1R",
                "filter_rules": {"breakout_level_offset_ticks": -2},
                "planned_entry": buf2_entry, "planned_stop": stop,
                "planned_tp1": tp1, "planned_tp2": tp2,
                "planned_contracts": contr,
            })

        # 6. TP_1R
        if entry and stop and risk_pts:
            tp1r = (entry + risk_pts) if direction == "Long" else (entry - risk_pts)
            variants.append({
                "variant": Variant.TP_1R,
                "parameter_diff": {"target_r": {"baseline": "CONFIG", "variant": 1.0}},
                "entry_rule": conf_mode, "confirmation_rule": conf_mode,
                "stop_rule": "FIXED", "target_rule": "1R",
                "filter_rules": {},
                "planned_entry": entry, "planned_stop": stop,
                "planned_tp1": tp1r, "planned_tp2": tp1r,
                "planned_contracts": contr,
            })

        # 7. TP_1.5R
        if entry and stop and risk_pts:
            tp15r = (entry + 1.5 * risk_pts) if direction == "Long" else (entry - 1.5 * risk_pts)
            variants.append({
                "variant": Variant.TP_1_5R,
                "parameter_diff": {"target_r": {"baseline": "CONFIG", "variant": 1.5}},
                "entry_rule": conf_mode, "confirmation_rule": conf_mode,
                "stop_rule": "FIXED", "target_rule": "1.5R",
                "filter_rules": {},
                "planned_entry": entry, "planned_stop": stop,
                "planned_tp1": tp15r, "planned_tp2": tp15r,
                "planned_contracts": contr,
            })

        # 8. TP_2R
        if entry and stop and risk_pts:
            tp2r = (entry + 2.0 * risk_pts) if direction == "Long" else (entry - 2.0 * risk_pts)
            variants.append({
                "variant": Variant.TP_2R,
                "parameter_diff": {"target_r": {"baseline": "CONFIG", "variant": 2.0}},
                "entry_rule": conf_mode, "confirmation_rule": conf_mode,
                "stop_rule": "FIXED", "target_rule": "2R",
                "filter_rules": {},
                "planned_entry": entry, "planned_stop": stop,
                "planned_tp1": tp2r, "planned_tp2": tp2r,
                "planned_contracts": contr,
            })

        # 9. TREND_REQUIRED — require 15m + 4H alignment with direction
        trend_aligned = self._check_trend_aligned(snap, direction)
        variants.append({
            "variant": Variant.TREND_REQUIRED,
            "parameter_diff": {"trend_filter": {"baseline": "INFORMATIONAL", "variant": "REQUIRED"}},
            "entry_rule": conf_mode, "confirmation_rule": conf_mode,
            "stop_rule": "FIXED", "target_rule": "ORB_CONFIG",
            "filter_rules": {"trend_required": True, "direction": direction},
            # Pre-evaluate: if trend not aligned, experiment starts as NO_ENTRY
            "pre_no_entry": not trend_aligned,
            "planned_entry": entry if trend_aligned else None,
            "planned_stop": stop, "planned_tp1": tp1, "planned_tp2": tp2,
            "planned_contracts": contr,
        })

        # 10. CVD_ALIGNED — require CVD direction aligns with trade
        cvd_aligned = self._check_cvd_aligned(snap, direction)
        variants.append({
            "variant": Variant.CVD_ALIGNED,
            "parameter_diff": {"cvd_filter": {"baseline": "INFORMATIONAL", "variant": "REQUIRED"}},
            "entry_rule": conf_mode, "confirmation_rule": conf_mode,
            "stop_rule": "FIXED", "target_rule": "ORB_CONFIG",
            "filter_rules": {"cvd_required": True, "direction": direction},
            "pre_no_entry": not cvd_aligned,
            "planned_entry": entry if cvd_aligned else None,
            "planned_stop": stop, "planned_tp1": tp1, "planned_tp2": tp2,
            "planned_contracts": contr,
        })

        # Enforce budget
        variants = variants[:self._max_variants]

        inserted = 0
        for v in variants:
            if self._insert_experiment_and_result(opp_id, inst, direction, v, sv, cv):
                inserted += 1
        return inserted

    def _check_trend_aligned(self, snap: Dict, direction: str) -> bool:
        t15 = _ss(snap.get("trend_15m"), "UNKNOWN").upper()
        t4h = _ss(snap.get("trend_4h"), "UNKNOWN").upper()
        if direction == "Long":
            return t15 == "BULLISH" and t4h == "BULLISH"
        return t15 == "BEARISH" and t4h == "BEARISH"

    def _check_cvd_aligned(self, snap: Dict, direction: str) -> bool:
        cvd = _ss(snap.get("cvd_direction"), "UNKNOWN").upper()
        if direction == "Long":
            return "BULL" in cvd or cvd in ("POSITIVE", "LONG")
        return "BEAR" in cvd or cvd in ("NEGATIVE", "SHORT")

    def _insert_experiment_and_result(self, opp_id: str, inst: str, direction: str,
                                      vdef: Dict, sv: str, cv: str) -> bool:
        variant = vdef["variant"]
        exp_id  = _experiment_id(opp_id, variant)
        res_id  = _result_id(exp_id)
        pre_no_entry = vdef.pop("pre_no_entry", False)

        try:
            db  = self._get_db()
            cur = db.cursor()
            cur.execute("""
                INSERT INTO ghost_experiments
                    (experiment_id, opportunity_id, strategy, strategy_version, config_version,
                     experiment_family, variant_name, parameter_diff, entry_rule,
                     confirmation_rule, stop_rule, target_rule, management_rule,
                     filter_rules, simulated_slippage, simulated_commissions,
                     planned_entry, planned_stop, planned_tp1, planned_tp2, planned_contracts)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (opportunity_id, variant_name) DO NOTHING
                RETURNING experiment_id
            """, (
                exp_id, opp_id, STRATEGY_NAME, sv, cv,
                EXPERIMENT_FAMILY, variant,
                json.dumps(vdef.get("parameter_diff", {})),
                vdef["entry_rule"], vdef["confirmation_rule"],
                vdef["stop_rule"], vdef["target_rule"],
                vdef.get("management_rule", "FIXED"),
                json.dumps(vdef.get("filter_rules", {})),
                GHOST_SLIPPAGE_TICKS, GHOST_COMM_PER_SIDE_USD,
                vdef.get("planned_entry"), vdef.get("planned_stop"),
                vdef.get("planned_tp1"), vdef.get("planned_tp2"),
                vdef.get("planned_contracts"),
            ))
            if cur.fetchone() is None:
                db.rollback()
                return False  # already existed

            # Determine initial result status
            init_status = ResultStatus.PENDING
            init_result = None
            init_entry  = vdef.get("planned_entry")

            if pre_no_entry:
                init_status = ResultStatus.COMPLETED
                init_result = OutcomeResult.NO_ENTRY
                init_entry  = None

            if variant == Variant.BASELINE:
                # BASELINE waits for OrbEngine to go POSITION_ACTIVE
                init_status = ResultStatus.WATCHING_ENTRY

            cur.execute("""
                INSERT INTO ghost_experiment_results
                    (result_id, experiment_id, opportunity_id, status,
                     entry_price, stop_price, tp1_price, tp2_price, result)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (experiment_id) DO NOTHING
            """, (
                res_id, exp_id, opp_id, init_status,
                init_entry if not pre_no_entry else None,
                vdef.get("planned_stop"), vdef.get("planned_tp1"), vdef.get("planned_tp2"),
                init_result,
            ))
            db.commit()

            # Add to in-memory open results (only non-completed)
            if init_status != ResultStatus.COMPLETED:
                with self._lock:
                    self._open_results[res_id] = {
                        "result_id": res_id, "experiment_id": exp_id,
                        "opportunity_id": opp_id, "status": init_status,
                        "variant_name": variant, "instrument": inst,
                        "direction": direction,
                        "entry_price": init_entry, "stop_price": vdef.get("planned_stop"),
                        "tp1_price": vdef.get("planned_tp1"), "tp2_price": vdef.get("planned_tp2"),
                        "entry_rule": vdef["entry_rule"],
                        "filter_rules": vdef.get("filter_rules", {}),
                        "mfe_r": 0.0, "mae_r": 0.0, "mfe_price": None, "mae_price": None,
                        "bars_held": 0, "last_bar_ts": None,
                    }
            return True
        except Exception as exc:
            self._log.warning("GRE insert variant (%s %s): %s", variant, opp_id, exc)
            return False

    # ── Open experiment processing ────────────────────────────────────────────

    def _process_open_experiments(self, inst: str, bar: Dict,
                                  price: float, orb: Dict) -> None:
        bar_ts = bar.get("ts")
        bar_h  = _sn(bar.get("high"))
        bar_l  = _sn(bar.get("low"))
        bar_c  = _sn(bar.get("close") or price)

        with self._lock:
            # FVG experiments (_fvg_family=True) are handled by on_fvg_bar_close; skip here
            result_ids = [rid for rid, rd in self._open_results.items()
                         if rd.get("instrument") == inst
                         and not rd.get("_fvg_family")]

        for result_id in result_ids:
            try:
                self._process_one_experiment(result_id, inst, bar, bar_ts, bar_h, bar_l, bar_c, orb)
            except Exception as exc:
                self._log.debug("GRE process experiment (%s): %s", result_id, exc)

    def _process_one_experiment(self, result_id: str, inst: str,
                                bar: Dict, bar_ts: Any,
                                bar_h: Optional[float], bar_l: Optional[float],
                                bar_c: float, orb: Dict) -> None:
        with self._lock:
            rd = self._open_results.get(result_id)
            if rd is None: return
            rd = dict(rd)  # snapshot to avoid lock contention

        status   = rd.get("status", ResultStatus.PENDING)
        variant  = rd.get("variant_name", "")
        direction = rd.get("direction", "")
        stop     = _sn(rd.get("stop_price"))
        tp1      = _sn(rd.get("tp1_price"))
        tp2      = _sn(rd.get("tp2_price"))
        entry    = _sn(rd.get("entry_price"))

        # ── WATCHING_ENTRY / PENDING → check entry conditions ─────────────
        if status in (ResultStatus.PENDING, ResultStatus.WATCHING_ENTRY):
            entry_rule = rd.get("entry_rule", "CLOSE_OUTSIDE")
            opp_id     = rd.get("opportunity_id", "")
            entered    = False
            entry_price_actual = None

            if variant == Variant.BASELINE:
                # BASELINE enters via _on_position_active(); skip here unless it has entry already
                if entry is not None and status == ResultStatus.WATCHING_ENTRY:
                    entered = True
                    entry_price_actual = entry

            elif entry_rule == "TOUCH":
                bk_level = _sn(rd.get("entry_price"))  # stored as breakout level
                if bk_level and bar_h is not None and bar_l is not None:
                    if direction == "Long" and bar_l <= bk_level:
                        entered = True; entry_price_actual = bk_level
                    elif direction == "Short" and bar_h >= bk_level:
                        entered = True; entry_price_actual = bk_level

            elif entry_rule == "CLOSE_AND_RETEST":
                sub = self._retest_state.get(result_id, "WAITING_CLOSE")
                bk_level = entry or _sn(rd.get("entry_price"))
                if sub == "WAITING_CLOSE":
                    # need a bar close outside the breakout level
                    if bk_level and bar_c:
                        if direction == "Long" and bar_c > bk_level:
                            self._retest_state[result_id] = "WAITING_RETEST"
                        elif direction == "Short" and bar_c < bk_level:
                            self._retest_state[result_id] = "WAITING_RETEST"
                elif sub == "WAITING_RETEST":
                    # now wait for price to retest back to the level
                    if bk_level and bar_h is not None and bar_l is not None:
                        if direction == "Long" and bar_l <= bk_level:
                            entered = True; entry_price_actual = bk_level
                        elif direction == "Short" and bar_h >= bk_level:
                            entered = True; entry_price_actual = bk_level

            elif entry_rule in ("CLOSE_OUTSIDE",):
                # close outside the planned entry level
                planned = _sn(rd.get("entry_price"))
                if planned and bar_c:
                    if direction == "Long" and bar_c >= planned:
                        entered = True; entry_price_actual = planned
                    elif direction == "Short" and bar_c <= planned:
                        entered = True; entry_price_actual = planned

            if entered and entry_price_actual is not None:
                stop_actual = stop
                if stop_actual is None:
                    stop_actual = _sn(rd.get("stop_price"))
                cost_r = _commission_r(inst, entry_price_actual, stop_actual or entry_price_actual)
                self._enter_experiment(result_id, rd["experiment_id"], rd["opportunity_id"],
                                       entry_price_actual, stop_actual,
                                       tp1, tp2, cost_r=cost_r, qualified_ts=_now_utc())
                with self._lock:
                    if result_id in self._open_results:
                        self._open_results[result_id]["status"] = ResultStatus.ACTIVE
                        self._open_results[result_id]["entry_price"] = entry_price_actual
                return  # will be picked up next bar as ACTIVE

        # ── ACTIVE → track MAE/MFE, check exit ────────────────────────────
        if status == ResultStatus.ACTIVE:
            if entry is None or stop is None: return
            if bar_ts and rd.get("last_bar_ts") and bar_ts <= rd["last_bar_ts"]:
                return  # already processed this bar (idempotency)

            risk_pts = abs(entry - stop)
            if bar_h is not None and bar_l is not None and risk_pts > 0:
                mfe_r, mae_r, mfe_p, mae_p = _update_mfe_mae(
                    direction, bar_h, bar_l, entry, risk_pts,
                    _sn(rd.get("mfe_price")), _sn(rd.get("mae_price")),
                    rd.get("mfe_r", 0.0) or 0.0, rd.get("mae_r", 0.0) or 0.0,
                )
                with self._lock:
                    if result_id in self._open_results:
                        self._open_results[result_id].update({
                            "mfe_r": mfe_r, "mae_r": mae_r,
                            "mfe_price": mfe_p, "mae_price": mae_p,
                            "bars_held": (rd.get("bars_held") or 0) + 1,
                            "last_bar_ts": bar_ts,
                        })

            # Exit detection
            if bar_h is None or bar_l is None: return
            stop_hit = (direction == "Long" and bar_l <= stop) or \
                       (direction == "Short" and bar_h >= stop)
            tp1_hit  = tp1 and (
                (direction == "Long" and bar_h >= tp1) or
                (direction == "Short" and bar_l <= tp1)
            )
            tp2_hit  = tp2 and (
                (direction == "Long" and bar_h >= tp2) or
                (direction == "Short" and bar_l <= tp2)
            )

            if stop_hit or tp1_hit or tp2_hit:
                # Same-bar ambiguity: conservative resolution (stop wins)
                ambig = False
                if stop_hit and (tp1_hit or tp2_hit):
                    ambig = True
                    exit_price  = stop
                    exit_reason = "STOP_HIT"
                elif tp2_hit:
                    exit_price  = tp2
                    exit_reason = "TP2_HIT"
                elif tp1_hit:
                    exit_price  = tp1
                    exit_reason = "TP1_HIT"
                else:
                    exit_price  = stop
                    exit_reason = "STOP_HIT"

                self._complete_experiment(result_id, exit_price=exit_price,
                                          exit_reason=exit_reason,
                                          result=None,  # computed in complete
                                          exit_ts=_now_utc(),
                                          ambiguous_bar=ambig,
                                          tp1_hit=bool(tp1_hit and not stop_hit),
                                          tp2_hit=bool(tp2_hit and not stop_hit))

    # ── Experiment enter/complete ─────────────────────────────────────────────

    def _enter_experiment(self, result_id: str, exp_id: str, opp_id: str,
                          entry: float, stop: Optional[float],
                          tp1: Optional[float], tp2: Optional[float],
                          cost_r: float = 0.0,
                          qualified_ts: Optional[str] = None) -> None:
        now = _now_utc()
        try:
            db = self._get_db()
            db.cursor().execute("""
                UPDATE ghost_experiment_results
                   SET status=%s, entry_timestamp=%s, entry_price=%s,
                       stop_price=%s, tp1_price=%s, tp2_price=%s,
                       qualified_timestamp=COALESCE(qualified_timestamp,%s),
                       cost_r=%s, updated_at=NOW()
                 WHERE result_id=%s AND status IN ('PENDING','WATCHING_ENTRY')
            """, (ResultStatus.ACTIVE, now, entry, stop, tp1, tp2,
                  qualified_ts or now, cost_r, result_id))
            db.commit()
        except Exception as exc:
            self._log.debug("GRE enter experiment (%s): %s", result_id, exc)
        with self._lock:
            if result_id in self._open_results:
                self._open_results[result_id].update({
                    "status": ResultStatus.ACTIVE,
                    "entry_price": entry, "stop_price": stop,
                    "tp1_price": tp1, "tp2_price": tp2, "cost_r": cost_r,
                })

    def _complete_experiment(self, result_id: str,
                             exit_price: Optional[float],
                             exit_reason: str,
                             result: Optional[str],
                             exit_ts: str,
                             ambiguous_bar: bool = False,
                             tp1_hit: bool = False,
                             tp2_hit: bool = False) -> None:
        with self._lock:
            rd = self._open_results.pop(result_id, None)
        if rd is None:
            return

        entry    = _sn(rd.get("entry_price"))
        stop     = _sn(rd.get("stop_price"))
        tp1      = _sn(rd.get("tp1_price"))
        tp2      = _sn(rd.get("tp2_price"))
        direction = rd.get("direction", "")
        inst      = rd.get("instrument", "")
        cost_r    = _sn(rd.get("cost_r")) or 0.0
        mfe_r     = rd.get("mfe_r", 0.0) or 0.0
        mae_r     = rd.get("mae_r", 0.0) or 0.0
        bars_held = rd.get("bars_held", 0) or 0

        # Compute R values
        gross_r = net_r = gross_pnl = net_pnl = None
        final_result = result

        if entry and exit_price and stop:
            gross_r = _compute_gross_r(direction, entry, exit_price, stop)
            if gross_r is not None:
                net_r = gross_r - cost_r
                risk_pts = abs(entry - stop)
                pt_val   = _POINT_VALUE.get(inst, 2.0)
                gross_pnl = gross_r * risk_pts * pt_val
                net_pnl   = net_r   * risk_pts * pt_val

            # Determine result
            if final_result is None:
                if gross_r is not None:
                    if gross_r > 0.05:    final_result = OutcomeResult.WIN
                    elif gross_r < -0.05: final_result = OutcomeResult.LOSS
                    else:                 final_result = OutcomeResult.BREAKEVEN
        elif result in (OutcomeResult.NO_ENTRY, OutcomeResult.EXPIRED):
            final_result = result
        else:
            final_result = OutcomeResult.NO_ENTRY

        stop_hit = exit_reason == "STOP_HIT"

        try:
            db = self._get_db()
            db.cursor().execute("""
                UPDATE ghost_experiment_results
                   SET status=%s, exit_timestamp=%s, exit_price=%s, exit_reason=%s,
                       gross_r=%s, net_r=%s, cost_r=%s, gross_pnl=%s, net_pnl=%s,
                       mfe_r=%s, mae_r=%s, mfe_price=%s, mae_price=%s,
                       max_favorable_r=%s, max_adverse_r=%s,
                       bars_held=%s, tp1_hit=%s, tp2_hit=%s, stop_hit=%s,
                       ambiguous_bar=%s, result=%s, updated_at=NOW()
                 WHERE result_id=%s
            """, (
                ResultStatus.COMPLETED, exit_ts, exit_price, exit_reason,
                gross_r, net_r, cost_r, gross_pnl, net_pnl,
                mfe_r, mae_r, rd.get("mfe_price"), rd.get("mae_price"),
                mfe_r, mae_r,
                bars_held, tp1_hit, tp2_hit, stop_hit,
                ambiguous_bar, final_result, result_id,
            ))
            db.commit()
        except Exception as exc:
            self._log.debug("GRE complete experiment (%s): %s", result_id, exc)
            return

        self._re_event("GHOST_EXPERIMENT_COMPLETED", inst=inst,
                       net_r=net_r,
                       extra={"result_id": result_id, "result": final_result,
                              "variant": rd.get("variant_name")})

        # Trigger async evidence state refresh (off the hot path)
        exp_id = rd.get("experiment_id", "")
        if exp_id:
            t = threading.Thread(target=self._refresh_evidence_state,
                                 args=(exp_id,), daemon=True)
            t.start()

    # ── Evidence state refresh (off hot path) ────────────────────────────────

    def _refresh_evidence_state(self, experiment_id: str) -> None:
        try:
            db  = self._get_db()
            cur = db.cursor()

            # Fetch all completed results for this variant × instrument × strategy
            cur.execute("""
                SELECT e2.experiment_id, r2.net_r, r2.gross_r, r2.mfe_r, r2.mae_r,
                       r2.result, r2.entry_timestamp
                FROM ghost_experiments e1
                JOIN ghost_experiments e2 ON e2.variant_name=e1.variant_name
                    AND e2.strategy=e1.strategy
                JOIN ghost_opportunities o2 ON o2.opportunity_id=e2.opportunity_id
                    AND o2.instrument=(SELECT instrument FROM ghost_opportunities
                                       WHERE opportunity_id=e1.opportunity_id LIMIT 1)
                JOIN ghost_experiment_results r2 ON r2.experiment_id=e2.experiment_id
                WHERE e1.experiment_id=%s
                  AND r2.status='COMPLETED'
            """, (experiment_id,))
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            result_rows = [dict(zip(cols, r)) for r in rows]

            if not result_rows:
                return

            stats = _aggregate_results(result_rows)
            net_rs = [r["net_r"] for r in result_rows if r.get("net_r") is not None]
            boot   = _bootstrap_ci(net_rs)
            mc     = _monte_carlo_drawdown(net_rs)

            # Fetch current evidence state
            cur.execute("SELECT evidence_state FROM ghost_experiments WHERE experiment_id=%s",
                        (experiment_id,))
            row = cur.fetchone()
            cur_state = row[0] if row else EvidenceState.INSUFFICIENT_DATA

            new_state = _compute_evidence_state(stats, cur_state, boot, mc)

            # Check READY_FOR_REVIEW gate
            if new_state == EvidenceState.PROMISING and _gate_ready_for_review(stats, boot, mc):
                new_state = EvidenceState.READY_FOR_REVIEW

            if new_state != cur_state:
                cur.execute("""
                    UPDATE ghost_experiments SET evidence_state=%s
                     WHERE experiment_id=%s
                """, (new_state, experiment_id))
                db.commit()
                self._re_event("EVIDENCE_STATE_CHANGED", strategy=STRATEGY_NAME,
                               extra={"experiment_id": experiment_id,
                                      "from": cur_state, "to": new_state})
                if new_state == EvidenceState.READY_FOR_REVIEW:
                    self._re_event("READY_FOR_REVIEW", strategy=STRATEGY_NAME,
                                   extra={"experiment_id": experiment_id,
                                          "net_expectancy": stats.get("net_expectancy"),
                                          "profit_factor": stats.get("profit_factor")})
                    self._log.info("GRE READY_FOR_REVIEW: %s (n=%d exp=%.3f pf=%s)",
                                   experiment_id, stats.get("closed_count", 0),
                                   stats.get("net_expectancy") or 0,
                                   stats.get("profit_factor"))
        except Exception as exc:
            self._log.debug("GRE evidence refresh (%s): %s", experiment_id, exc)

    # ── API endpoints ─────────────────────────────────────────────────────────

    def get_health(self, family: Optional[str] = None) -> Dict:
        """
        Returns GRE health summary.
        Pass family='09:30_ORB' or 'FVG_REVISIT' to scope to a single research family.
        Omit / pass None for the global (all-families) view.
        """
        with self._lock:
            open_all = self._open_results
            open_count = sum(1 for rd in open_all.values() if not family or rd.get("strategy_family") == family)
            # For FVG: count by _fvg_family sentinel (may lack strategy_family in old restored rows)
            if family == STRATEGY_FAMILY_FVG:
                open_count = sum(1 for rd in open_all.values() if rd.get("_fvg_family"))
            elif family == STRATEGY_FAMILY_ORB:
                open_count = sum(1 for rd in open_all.values() if not rd.get("_fvg_family"))
            else:
                open_count = len(open_all)

        today = datetime.now(timezone.utc).date().isoformat()
        fam_clause = "AND strategy_family=%s" if family else ""
        fam_params = [family] if family else []
        try:
            db  = self._get_db()
            cur = db.cursor()
            cur.execute(f"""
                SELECT
                    COUNT(*) FILTER (WHERE trading_date=%s) AS opps_today,
                    COUNT(*) AS total_opps
                FROM ghost_opportunities
                WHERE 1=1 {fam_clause}
            """, [today] + fam_params)
            opp_row = cur.fetchone()

            cur.execute(f"""
                SELECT COUNT(*) FROM ghost_experiments WHERE 1=1 {fam_clause}
            """, fam_params)
            exp_row = cur.fetchone()

            cur.execute(f"""
                SELECT
                    COUNT(*) FILTER (WHERE r.status='COMPLETED') AS completed,
                    COUNT(*) FILTER (WHERE r.result='NO_ENTRY') AS no_entry
                FROM ghost_experiment_results r
                JOIN ghost_experiments e ON e.experiment_id = r.experiment_id
                WHERE 1=1 {fam_clause.replace('strategy_family', 'e.strategy_family')}
            """, fam_params)
            res_row = cur.fetchone()

            # Per-family breakdown (only in global view)
            family_breakdown: Dict = {}
            if not family:
                cur.execute("""
                    SELECT strategy_family,
                           COUNT(*) FILTER (WHERE trading_date=%s) AS opps_today,
                           COUNT(*) AS total_opps
                    FROM ghost_opportunities
                    GROUP BY strategy_family
                """, [today])
                for fam_r in cur.fetchall():
                    family_breakdown[fam_r[0] or "UNKNOWN"] = {
                        "opps_today": fam_r[1], "total_opps": fam_r[2],
                    }

            return {
                "gre_version":          GRE_VERSION,
                "db_ready":             GhostResearchEngine.GRE_DB_READY,
                "families":             [STRATEGY_FAMILY_ORB, STRATEGY_FAMILY_FVG],
                "filter_family":        family,
                "opportunities_today":  opp_row[0] if opp_row else 0,
                "total_opportunities":  opp_row[1] if opp_row else 0,
                "total_experiments":    exp_row[0] if exp_row else 0,
                "active_ghost_trades":  open_count,
                "completed":            res_row[0] if res_row else 0,
                "no_entry_count":       res_row[1] if res_row else 0,
                "family_breakdown":     family_breakdown,
            }
        except Exception as exc:
            return {"db_ready": False, "error": str(exc)}

    def get_candidates(self, min_samples: int = 10,
                       family: Optional[str] = None) -> List[Dict]:
        """Top research candidates sorted by net expectancy.
        Pass family='FVG_REVISIT' or '09:30_ORB' to scope to one research family."""
        fam_clause = "AND o.strategy_family=%s" if family else ""
        try:
            db  = self._get_db()
            cur = db.cursor()
            cur.execute(f"""
                SELECT e.experiment_id, e.variant_name, e.evidence_state,
                       o.instrument, o.strategy_family,
                       COUNT(r.result_id) FILTER (WHERE r.status='COMPLETED') AS closed,
                       COUNT(r.result_id) FILTER (WHERE r.result='NO_ENTRY') AS no_entry,
                       COUNT(r.result_id) FILTER (WHERE r.result='WIN') AS wins,
                       AVG(r.net_r) FILTER (WHERE r.status='COMPLETED'
                                            AND r.result NOT IN ('NO_ENTRY','EXPIRED')) AS avg_net_r,
                       SUM(CASE WHEN r.net_r>0 AND r.status='COMPLETED'
                                 AND r.result NOT IN ('NO_ENTRY','EXPIRED') THEN r.net_r ELSE 0 END)
                       / NULLIF(ABS(SUM(CASE WHEN r.net_r<0 AND r.status='COMPLETED'
                                 AND r.result NOT IN ('NO_ENTRY','EXPIRED') THEN r.net_r ELSE 0 END)), 0) AS profit_factor,
                       MIN(r.net_r) FILTER (WHERE r.status='COMPLETED'
                                            AND r.result NOT IN ('NO_ENTRY','EXPIRED')) AS max_dd_proxy
                FROM ghost_experiments e
                JOIN ghost_opportunities o ON o.opportunity_id=e.opportunity_id
                LEFT JOIN ghost_experiment_results r ON r.experiment_id=e.experiment_id
                WHERE 1=1 {fam_clause}
                GROUP BY e.experiment_id, e.variant_name, e.evidence_state,
                         o.instrument, o.strategy_family
                HAVING COUNT(r.result_id) FILTER (WHERE r.status='COMPLETED') >= %s
                ORDER BY avg_net_r DESC NULLS LAST
                LIMIT 50
            """, ([family] if family else []) + [min_samples])
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            self._log.debug("GRE get_candidates: %s", exc)
            return []

    def get_experiments(self, instrument: Optional[str] = None,
                        variant: Optional[str] = None,
                        family: Optional[str] = None,
                        limit: int = 100) -> List[Dict]:
        try:
            db  = self._get_db()
            cur = db.cursor()
            clauses = []
            params: List = []
            if instrument:
                clauses.append("o.instrument=%s"); params.append(instrument)
            if variant:
                clauses.append("e.variant_name=%s"); params.append(variant)
            if family:
                clauses.append("o.strategy_family=%s"); params.append(family)
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            params.append(limit)
            cur.execute(f"""
                SELECT e.experiment_id, e.variant_name, e.evidence_state,
                       e.entry_rule, e.target_rule, e.parameter_diff,
                       o.instrument, o.trading_date, o.direction,
                       r.status, r.result, r.net_r, r.gross_r, r.bars_held,
                       r.entry_timestamp, r.exit_timestamp
                FROM ghost_experiments e
                JOIN ghost_opportunities o ON o.opportunity_id=e.opportunity_id
                LEFT JOIN ghost_experiment_results r ON r.experiment_id=e.experiment_id
                {where}
                ORDER BY e.created_at DESC
                LIMIT %s
            """, params)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            self._log.debug("GRE get_experiments: %s", exc)
            return []

    def get_candidate(self, experiment_id: str) -> Dict:
        """Drill-down stats for one candidate including bootstrap + Monte Carlo."""
        try:
            db  = self._get_db()
            cur = db.cursor()
            cur.execute("""
                SELECT r.net_r, r.gross_r, r.mfe_r, r.mae_r, r.result, r.entry_timestamp,
                       r.tp1_hit, r.tp2_hit, r.stop_hit, r.bars_held, r.ambiguous_bar
                FROM ghost_experiment_results r
                WHERE r.experiment_id=%s AND r.status='COMPLETED'
                  AND r.result NOT IN ('NO_ENTRY','EXPIRED')
            """, (experiment_id,))
            rows    = cur.fetchall()
            cols    = [d[0] for d in cur.description]
            results = [dict(zip(cols, r)) for r in rows]

            stats  = _aggregate_results(results)
            net_rs = [r["net_r"] for r in results if r.get("net_r") is not None]
            boot   = _bootstrap_ci(net_rs)
            mc     = _monte_carlo_drawdown(net_rs)

            # Baseline comparison
            cur.execute("""
                SELECT e.opportunity_id FROM ghost_experiments WHERE experiment_id=%s
            """, (experiment_id,))
            row = cur.fetchone()
            opp_id = row[0] if row else None
            baseline_stats = None
            if opp_id:
                base_exp_id = _experiment_id(opp_id, Variant.BASELINE)
                cur.execute("""
                    SELECT r.net_r, r.gross_r, r.mfe_r, r.mae_r, r.result
                    FROM ghost_experiment_results r
                    WHERE r.experiment_id=%s AND r.status='COMPLETED'
                      AND r.result NOT IN ('NO_ENTRY','EXPIRED')
                """, (base_exp_id,))
                base_rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
                baseline_stats = _aggregate_results(base_rows)

            # Context breakdowns
            cur.execute("""
                SELECT o.trend_15m, o.trend_4h, o.cvd_direction,
                       o.vwap_side, o.direction, r.net_r, r.result
                FROM ghost_experiment_results r
                JOIN ghost_experiments e ON e.experiment_id=r.experiment_id
                JOIN ghost_opportunities o ON o.opportunity_id=e.opportunity_id
                WHERE r.experiment_id=%s AND r.status='COMPLETED'
                  AND r.result NOT IN ('NO_ENTRY','EXPIRED')
            """, (experiment_id,))
            ctx_rows = cur.fetchall()
            ctx_cols = [d[0] for d in cur.description]
            breakdowns = self._compute_breakdowns([dict(zip(ctx_cols, r)) for r in ctx_rows])

            return {
                "experiment_id": experiment_id,
                "stats":         stats,
                "bootstrap_ci":  boot,
                "monte_carlo":   mc,
                "baseline_stats":baseline_stats,
                "breakdowns":    breakdowns,
            }
        except Exception as exc:
            self._log.debug("GRE get_candidate: %s", exc)
            return {"error": str(exc)}

    def _compute_breakdowns(self, rows: List[Dict]) -> Dict:
        """Slice results by trend, CVD, VWAP for context analysis."""
        def _group(key: str) -> Dict:
            groups: Dict[str, List] = {}
            for r in rows:
                val = _ss(r.get(key), "UNKNOWN")
                groups.setdefault(val, []).append(r)
            result = {}
            for val, group_rows in groups.items():
                s = _aggregate_results(group_rows)
                result[val] = {
                    "n": s["closed_count"],
                    "net_expectancy": s["net_expectancy"],
                    "win_rate": s["win_rate"],
                    "profit_factor": s["profit_factor"],
                    "status": "INSUFFICIENT_DATA" if s["closed_count"] < 10 else "OK",
                }
            return result
        return {
            "by_trend_15m":  _group("trend_15m"),
            "by_trend_4h":   _group("trend_4h"),
            "by_cvd":        _group("cvd_direction"),
            "by_vwap_side":  _group("vwap_side"),
            "by_direction":  _group("direction"),
        }

    def get_opportunity(self, opportunity_id: str) -> Dict:
        try:
            db  = self._get_db()
            cur = db.cursor()
            cur.execute("SELECT * FROM ghost_opportunities WHERE opportunity_id=%s",
                        (opportunity_id,))
            row = cur.fetchone()
            if not row: return {}
            cols = [d[0] for d in cur.description]
            opp  = dict(zip(cols, row))
            cur.execute("""
                SELECT e.experiment_id, e.variant_name, r.status, r.result,
                       r.net_r, r.gross_r, r.entry_price, r.exit_price
                FROM ghost_experiments e
                LEFT JOIN ghost_experiment_results r ON r.experiment_id=e.experiment_id
                WHERE e.opportunity_id=%s
            """, (opportunity_id,))
            ecols = [d[0] for d in cur.description]
            opp["experiments"] = [dict(zip(ecols, r)) for r in cur.fetchall()]
            return opp
        except Exception as exc:
            return {"error": str(exc)}

    def get_baseline_vs_variant(self, instrument: Optional[str] = None) -> List[Dict]:
        """Paired baseline vs each variant comparison."""
        try:
            db  = self._get_db()
            cur = db.cursor()
            params: List = [Variant.BASELINE]
            inst_clause = "AND o.instrument=%s" if instrument else ""
            if instrument: params.append(instrument)
            cur.execute(f"""
                SELECT
                    v.variant_name,
                    o.instrument,
                    COUNT(*) FILTER (WHERE r_b.result IS NOT NULL AND r_v.result IS NOT NULL) AS paired_count,
                    AVG(r_b.net_r) FILTER (WHERE r_b.result NOT IN ('NO_ENTRY','EXPIRED')) AS baseline_exp,
                    AVG(r_v.net_r) FILTER (WHERE r_v.result NOT IN ('NO_ENTRY','EXPIRED')) AS variant_exp,
                    AVG(r_b.net_r) FILTER (WHERE r_b.result NOT IN ('NO_ENTRY','EXPIRED'))
                        - AVG(r_v.net_r) FILTER (WHERE r_v.result NOT IN ('NO_ENTRY','EXPIRED')) AS delta_exp,
                    COUNT(*) FILTER (WHERE r_b.result='WIN'  AND r_v.result NOT IN ('WIN')) AS wins_avoided,
                    COUNT(*) FILTER (WHERE r_b.result='LOSS' AND r_v.result NOT IN ('LOSS')) AS losses_avoided,
                    COUNT(*) FILTER (WHERE r_b.result='WIN'  AND r_v.result='NO_ENTRY') AS wins_missed,
                    COUNT(*) FILTER (WHERE r_b.result='LOSS' AND r_v.result='NO_ENTRY') AS losses_missed,
                    COUNT(*) FILTER (WHERE r_v.result='NO_ENTRY') AS variant_no_entry,
                    COUNT(*) FILTER (WHERE r_b.result='NO_ENTRY') AS baseline_no_entry
                FROM ghost_experiments b
                JOIN ghost_experiments v ON v.opportunity_id=b.opportunity_id
                    AND v.variant_name != %s
                JOIN ghost_opportunities o ON o.opportunity_id=b.opportunity_id
                LEFT JOIN ghost_experiment_results r_b ON r_b.experiment_id=b.experiment_id
                LEFT JOIN ghost_experiment_results r_v ON r_v.experiment_id=v.experiment_id
                WHERE b.variant_name=%s {inst_clause}
                GROUP BY v.variant_name, o.instrument
                ORDER BY variant_exp DESC NULLS LAST
            """, params)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            self._log.debug("GRE baseline_vs_variant: %s", exc)
            return []

    # ═══════════════════════════════════════════════════════════════════════════
    # Phase 4 — FVG_REVISIT Research Family
    # ═══════════════════════════════════════════════════════════════════════════
    #
    # SAFETY CONTRACT
    # ───────────────
    # • Never calls execute_trade_gateway or any broker path.
    # • Never modifies gate, scoring, sizing, or execution mode.
    # • Results are shadow/research only and require mandatory human review.
    # ═══════════════════════════════════════════════════════════════════════════

    # ── Public entry point ────────────────────────────────────────────────────

    def on_fvg_bar_close(
        self,
        inst:      str,
        zones:     List[Dict],
        bar:       Dict,
        price:     float,
        canonical: Optional[Dict] = None,
    ) -> None:
        """
        Called from app.py _fvg_bar_close() after fvg_engine.process_bar_close().
        Fail-open: any exception is caught and logged; live processing never blocked.
        """
        if not GhostResearchEngine.GRE_DB_READY:
            return
        try:
            self._fvg_process_inst(inst, zones, bar, price, canonical or {})
        except Exception as exc:
            self._log.debug("GRE FVG bar-close (%s): %s", inst, exc)

    # ── Per-instrument dispatch ───────────────────────────────────────────────

    def _fvg_process_inst(
        self,
        inst:      str,
        zones:     List[Dict],
        bar:       Dict,
        price:     float,
        canonical: Dict,
    ) -> None:
        bar_low  = float(bar.get("low",  price))
        bar_high = float(bar.get("high", price))
        bar_ts   = bar.get("ts")

        # Build lookup: research_fvg_id → zone (for open-experiment zone-status checks)
        zones_by_rfid: Dict[str, Dict] = {}
        for z in zones:
            rfid = _fvg_research_id(
                inst,
                str(z.get("direction", "")),
                z.get("bar_ts"),
                float(z.get("upper", 0)),
                float(z.get("lower", 0)),
            )
            zones_by_rfid[rfid] = z

        # 1. Detect new revisit sessions and create ghost opportunities
        for z in zones:
            if z.get("status") not in ("ACTIVE", "TOUCHED"):
                continue
            try:
                self._fvg_check_revisit(inst, z, bar_low, bar_high, bar_ts, price, canonical)
            except Exception as exc:
                self._log.debug("GRE FVG revisit-check (%s): %s", inst, exc)

        # 2. Evaluate open FVG experiments against this bar
        self._fvg_process_open_experiments(
            inst, zones_by_rfid, bar, bar_ts, bar_low, bar_high, price, canonical,
        )

    # ── Revisit detection ─────────────────────────────────────────────────────

    def _fvg_check_revisit(
        self,
        inst:      str,
        zone:      Dict,
        bar_low:   float,
        bar_high:  float,
        bar_ts:    Any,
        price:     float,
        canonical: Dict,
    ) -> None:
        upper = float(zone.get("upper", 0))
        lower = float(zone.get("lower", 0))
        rfid  = _fvg_research_id(
            inst, str(zone.get("direction", "")), zone.get("bar_ts"), upper, lower,
        )

        inside_now = _fvg_bar_overlaps(zone, bar_low, bar_high)
        was_inside = self._fvg_inside_prev.get(rfid, False)
        self._fvg_inside_prev[rfid] = inside_now

        # New revisit session: price just entered zone (was outside, now inside)
        if not (inside_now and not was_inside):
            return

        revisit_n = self._fvg_revisit_count.get(rfid, 0) + 1
        self._fvg_revisit_count[rfid] = revisit_n

        opp_key = f"{rfid}|{revisit_n}"
        if opp_key in self._fvg_opp_created:
            return  # already created for this revisit session

        opp_id   = _fvg_opportunity_id(inst, rfid, revisit_n, bar_ts)
        self._fvg_opp_created[opp_key] = opp_id

        revisit_id = _fvg_revisit_id(rfid, revisit_n, bar_ts)
        depth_pct  = _fvg_depth_pct(zone, bar_low, bar_high)

        snap = self._build_fvg_snapshot(
            inst, zone, rfid, revisit_n, revisit_id,
            bar_low, bar_high, bar_ts, price, depth_pct, canonical,
        )

        # DC enrichment (fail-open)
        try:
            if self._dc_registry_fn is not None:
                _dc_inst = self._dc_registry_fn()
                if _dc_inst is not None:
                    _rec = _dc_inst.get_record(inst)
                    if _rec is not None:
                        from decision_contract import enrich_ghost_snapshot as _egs  # noqa: PLC0415
                        snap = _egs(snap, _rec)
        except Exception as exc:
            self._log.debug("GRE FVG DC enrich (%s): %s", inst, exc)

        ok = self._insert_fvg_opportunity(
            opp_id, inst, zone, rfid, revisit_id, revisit_n, snap,
        )
        if not ok:
            return

        self._re_event("FVG_OPPORTUNITY_RECORDED", inst=inst, extra={
            "opportunity_id": opp_id, "research_fvg_id": rfid,
            "revisit_n": revisit_n, "depth_pct": round(depth_pct, 3),
            "location": _fvg_classify_location(depth_pct),
        })

        n = self._create_fvg_variants(
            opp_id, inst, zone, rfid, revisit_id, revisit_n, snap, depth_pct, price,
        )
        self._re_event("FVG_VARIANTS_CREATED", inst=inst, extra={
            "opportunity_id": opp_id, "count": n,
        })
        self._log.info(
            "GRE FVG [%s] opp=%s revisit#%d depth=%s variants=%d",
            inst, opp_id, revisit_n, _fvg_classify_location(depth_pct), n,
        )

    # ── Snapshot builder ──────────────────────────────────────────────────────

    def _build_fvg_snapshot(
        self,
        inst:       str,
        zone:       Dict,
        rfid:       str,
        revisit_n:  int,
        revisit_id: str,
        bar_low:    float,
        bar_high:   float,
        bar_ts:     Any,
        price:      float,
        depth_pct:  float,
        canonical:  Dict,
    ) -> Dict:
        upper = float(zone.get("upper", 0))
        lower = float(zone.get("lower", 0))
        gap   = upper - lower
        tick  = _TICK_SIZE.get(inst, 0.25)
        gap_ticks = int(round(gap / tick)) if tick > 0 else None

        # ATR from canonical (try several key patterns used across engines)
        atr: Optional[float] = _sn(
            canonical.get("atr")
            or (canonical.get("atr_d") or {}).get("atr")
            or canonical.get("current_atr")
        )
        gap_atr = round(gap / atr, 4) if (atr and atr > 0) else None

        # FVG age at revisit
        age_seconds: Optional[int] = None
        try:
            created_str = zone.get("created_at")
            if created_str and bar_ts:
                from datetime import datetime  # noqa: PLC0415 (already imported at top)
                created_epoch = datetime.fromisoformat(
                    str(created_str).replace("Z", "+00:00")
                ).timestamp()
                age_seconds = int(bar_ts - created_epoch)
        except Exception:
            pass

        # Canonical context blocks
        trend  = canonical.get("trend")  or {}
        cvd    = canonical.get("cvd")    or {}
        volume = canonical.get("volume") or {}
        vwap   = canonical.get("vwap")   or {}
        struct = canonical.get("structure") or {}

        vwap_val  = _sn(vwap.get("vwap") or vwap.get("value"))
        vwap_side = _ss(vwap.get("side") or vwap.get("vwap_side"), "UNKNOWN")
        vwap_dist: Optional[float] = None
        if vwap_val and price and abs(price) > 0:
            vwap_dist = round((price - vwap_val) / price * 100, 4)

        extra: Dict[str, Any] = {
            "fvg_gap_pts":              round(gap, 4),
            "fvg_gap_ticks":            gap_ticks,
            "fvg_gap_atr_ratio":        gap_atr,
            "fvg_age_seconds":          age_seconds,
            "fvg_prior_touch_count":    zone.get("touch_count", 0),
            "fvg_interaction_depth":    round(depth_pct, 4),
            "fvg_interaction_location": _fvg_classify_location(depth_pct),
            "fvg_status_at_revisit":    zone.get("status"),
            "fvg_upper":                upper,
            "fvg_lower":                lower,
            "fvg_midpoint":             float(zone.get("midpoint", (upper + lower) / 2)),
            "fvg_revisit_number":       revisit_n,  # stored for restore dedup
        }

        return {
            # Canonical snapshot
            "current_price":      price,
            "current_atr":        atr,
            "current_vwap":       vwap_val,
            "vwap_side":          vwap_side,
            "vwap_distance_pct":  vwap_dist,
            "trend_15m":          _ss(trend.get("trend_15m"), "UNKNOWN"),
            "trend_4h":           _ss(trend.get("trend_4h"), "UNKNOWN"),
            "trend_alignment":    _ss(trend.get("alignment") or trend.get("trend_alignment"), "UNKNOWN"),
            "cvd_direction":      _ss(cvd.get("direction") or cvd.get("state"), "UNKNOWN"),
            "cvd_value":          _sn(cvd.get("value")),
            "volume_value":       _sn(volume.get("volume") or volume.get("value")),
            "relative_volume":    _sn(volume.get("relative_volume") or volume.get("databento_rvol")),
            "structure_bos":      bool(struct.get("bos") or struct.get("bullish_bos") or struct.get("bearish_bos")),
            "structure_choch":    bool(struct.get("choch") or struct.get("choch_bullish") or struct.get("choch_bearish")),
            # FVG identity (for reference in experiments)
            "research_fvg_id":    rfid,
            "revisit_id":         revisit_id,
            "revisit_n":          revisit_n,
            # Extra JSONB snapshot
            "_extra_snapshot":    extra,
        }

    # ── Opportunity DB insert ─────────────────────────────────────────────────

    def _insert_fvg_opportunity(
        self,
        opp_id:     str,
        inst:       str,
        zone:       Dict,
        rfid:       str,
        revisit_id: str,
        revisit_n:  int,
        snap:       Dict,
    ) -> bool:
        try:
            db  = self._get_db()
            cur = db.cursor()

            direction     = "Long" if str(zone.get("direction", "")).upper() == "BULLISH" else "Short"
            trading_date  = datetime.now(timezone.utc).date().isoformat()
            source_fvg_id = str(zone.get("id") or zone.get("zone_id") or zone.get("fvg_id") or "")
            extra         = snap.get("_extra_snapshot") or {}

            cur.execute("""
                INSERT INTO ghost_opportunities
                    (opportunity_id, trading_date, instrument,
                     strategy, strategy_family, strategy_version, config_version,
                     orb_state, orb_event, direction, breakout_direction,
                     source_fvg_id, research_fvg_id, revisit_id,
                     current_price, current_atr, current_vwap, vwap_side, vwap_distance_pct,
                     trend_15m, trend_4h, trend_alignment,
                     cvd_direction, cvd_value, volume_value, relative_volume,
                     structure_bos, structure_choch,
                     extra_snapshot,
                     dc_decision_id, dc_state, dc_reason_code, dc_verdict,
                     dc_edge_score, dc_confidence, dc_qualified, dc_risk_status,
                     dc_execution_mode, dc_execution_enabled, dc_armed,
                     dc_parity_agree, dc_version)
                VALUES (
                    %s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,
                    %s,%s,%s,%s,%s,
                    %s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,
                    %s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                ON CONFLICT (opportunity_id) DO NOTHING
                RETURNING id
            """, (
                opp_id, trading_date, inst,
                FVG_STRATEGY_NAME, STRATEGY_FAMILY_FVG, "FVG_RESEARCH_BASELINE_V1", "1.0",
                "FVG_REVISIT", "FVG_REVISIT_OPPORTUNITY", direction, direction,
                source_fvg_id or None, rfid, revisit_id,
                snap.get("current_price"), snap.get("current_atr"),
                snap.get("current_vwap"), snap.get("vwap_side"), snap.get("vwap_distance_pct"),
                snap.get("trend_15m"), snap.get("trend_4h"), snap.get("trend_alignment"),
                snap.get("cvd_direction"), snap.get("cvd_value"),
                snap.get("volume_value"), snap.get("relative_volume"),
                snap.get("structure_bos"), snap.get("structure_choch"),
                json.dumps(
                    {k: v for k, v in extra.items() if v is not None}, default=str,
                ),
                snap.get("canonical_decision_id"), snap.get("canonical_decision_state"),
                snap.get("canonical_reason_code"), snap.get("live_verdict"),
                snap.get("edge_score"), snap.get("confidence"),
                snap.get("qualification_state"), snap.get("risk_status"),
                snap.get("execution_mode"), snap.get("execution_enabled"), snap.get("armed"),
                snap.get("parity_agree"), snap.get("dc_version"),
            ))
            row = cur.fetchone()
            db.commit()
            return row is not None
        except Exception as exc:
            self._log.warning("GRE FVG insert_opportunity (%s): %s", inst, exc)
            return False

    # ── Variant creation ──────────────────────────────────────────────────────

    def _create_fvg_variants(
        self,
        opp_id:    str,
        inst:      str,
        zone:      Dict,
        rfid:      str,
        revisit_id: str,
        revisit_n: int,
        snap:      Dict,
        depth_pct: float,
        price:     float,
    ) -> int:
        direction = "Long" if str(zone.get("direction", "")).upper() == "BULLISH" else "Short"
        upper = float(zone.get("upper", 0))
        lower = float(zone.get("lower", 0))
        tick  = _TICK_SIZE.get(inst, 0.25)

        # Stop: beyond opposite FVG boundary + 2 ticks (deterministic per zone)
        planned_stop = (lower - 2 * tick) if direction == "Long" else (upper + 2 * tick)
        risk = abs(price - planned_stop) if planned_stop else None

        def _tp(mult: float) -> Optional[float]:
            if not risk or risk <= 0:
                return None
            return (price + mult * risk) if direction == "Long" else (price - mult * risk)

        # Snapshot context used by filter_rules (for entry-time evaluation)
        trend_15m = str(snap.get("trend_15m", "UNKNOWN")).upper()
        cvd_dir   = str(snap.get("cvd_direction", "UNKNOWN")).upper()

        # Common FVG identity anchors stored in filter_rules for use at entry time
        _fvg_ctx: Dict = {
            "_rfid":          rfid,
            "_revisit_n":     revisit_n,
            "_fvg_upper":     upper,
            "_fvg_lower":     lower,
            "_fvg_direction": str(zone.get("direction", "")),
            "_trend_15m":     trend_15m,
            "_cvd_direction": cvd_dir,
        }

        variants_cfg = [
            {
                "variant":    FvgVariant.BASELINE,
                "entry_rule": "FVG_ZONE_TOUCH",
                "filter":     {},
                "tp_mult":    _FVG_BASELINE_TARGET_R,
                "param":      {"tp_r": _FVG_BASELINE_TARGET_R, "baseline": True},
            },
            {
                "variant":    FvgVariant.NEAR_EDGE_ENTRY,
                "entry_rule": "FVG_NEAR_EDGE",
                "filter":     {"max_depth_pct": _FVG_NEAR_EDGE_FRAC},
                "tp_mult":    _FVG_BASELINE_TARGET_R,
                "param":      {"entry_depth_max_pct": _FVG_NEAR_EDGE_FRAC,
                               "tp_r": _FVG_BASELINE_TARGET_R},
            },
            {
                "variant":    FvgVariant.MIDPOINT_ENTRY,
                "entry_rule": "FVG_MIDPOINT",
                "filter":     {"min_depth_pct": _FVG_MIDPOINT_MIN_PCT},
                "tp_mult":    _FVG_BASELINE_TARGET_R,
                "param":      {"entry_depth_min_pct": _FVG_MIDPOINT_MIN_PCT,
                               "tp_r": _FVG_BASELINE_TARGET_R},
            },
            {
                "variant":    FvgVariant.DEEP_FILL_ENTRY,
                "entry_rule": "FVG_DEEP_FILL",
                "filter":     {"min_depth_pct": _FVG_DEEP_FILL_MIN_PCT},
                "tp_mult":    _FVG_BASELINE_TARGET_R,
                "param":      {"entry_depth_min_pct": _FVG_DEEP_FILL_MIN_PCT,
                               "tp_r": _FVG_BASELINE_TARGET_R},
            },
            {
                "variant":      FvgVariant.FIRST_TOUCH_ONLY,
                "entry_rule":   "FVG_ZONE_TOUCH",
                "filter":       {"max_revisit_n": 1},
                "tp_mult":      _FVG_BASELINE_TARGET_R,
                "param":        {"max_revisit_n": 1, "tp_r": _FVG_BASELINE_TARGET_R},
                "pre_no_entry": revisit_n > 1,
            },
            {
                "variant":      FvgVariant.SECOND_TOUCH_ALLOWED,
                "entry_rule":   "FVG_ZONE_TOUCH",
                "filter":       {"max_revisit_n": 2},
                "tp_mult":      _FVG_BASELINE_TARGET_R,
                "param":        {"max_revisit_n": 2, "tp_r": _FVG_BASELINE_TARGET_R},
                "pre_no_entry": revisit_n > 2,
            },
            {
                "variant":    FvgVariant.TREND_REQUIRED,
                "entry_rule": "FVG_ZONE_TOUCH",
                "filter":     {"require_trend_align": True},
                "tp_mult":    _FVG_BASELINE_TARGET_R,
                "param":      {"require_15m_trend_alignment": True,
                               "tp_r": _FVG_BASELINE_TARGET_R},
            },
            {
                "variant":    FvgVariant.CVD_ALIGNED,
                "entry_rule": "FVG_ZONE_TOUCH",
                "filter":     {"require_cvd_align": True},
                "tp_mult":    _FVG_BASELINE_TARGET_R,
                "param":      {"require_cvd_alignment": True,
                               "tp_r": _FVG_BASELINE_TARGET_R},
            },
            {
                "variant":    FvgVariant.TP_1R,
                "entry_rule": "FVG_ZONE_TOUCH",
                "filter":     {},
                "tp_mult":    1.0,
                "param":      {"tp_r": 1.0},
            },
            {
                "variant":    FvgVariant.TP_1_5R,
                "entry_rule": "FVG_ZONE_TOUCH",
                "filter":     {},
                "tp_mult":    1.5,
                "param":      {"tp_r": 1.5},
            },
        ]

        created = 0
        for cfg in variants_cfg[:self._max_variants]:
            # Merge FVG identity + tp_mult into filter_rules for entry-time evaluation
            filter_r = {**cfg["filter"], **_fvg_ctx, "tp_mult": cfg["tp_mult"]}
            ok = self._insert_fvg_experiment_and_result(
                opp_id, inst, direction,
                cfg["variant"],
                planned_stop, _tp(cfg["tp_mult"]),
                cfg["entry_rule"], filter_r, cfg.get("param", {}),
                pre_no_entry=cfg.get("pre_no_entry", False),
            )
            if ok:
                created += 1
        return created

    # ── Experiment + result DB insert ─────────────────────────────────────────

    def _insert_fvg_experiment_and_result(
        self,
        opp_id:       str,
        inst:         str,
        direction:    str,
        variant:      str,
        planned_stop: Optional[float],
        planned_tp1:  Optional[float],
        entry_rule:   str,
        filter_rules: Dict,
        param_diff:   Dict,
        pre_no_entry: bool = False,
    ) -> bool:
        exp_id = _experiment_id(opp_id, variant)
        res_id = _result_id(exp_id)
        try:
            db  = self._get_db()
            cur = db.cursor()
            tp_lbl = f"TP_{filter_rules.get('tp_mult', _FVG_BASELINE_TARGET_R):.1f}R"
            cur.execute("""
                INSERT INTO ghost_experiments
                    (experiment_id, opportunity_id,
                     strategy, strategy_family, strategy_version, config_version,
                     experiment_family, variant_name, parameter_diff, entry_rule,
                     confirmation_rule, stop_rule, target_rule, management_rule,
                     filter_rules, simulated_slippage, simulated_commissions,
                     planned_entry, planned_stop, planned_tp1, planned_tp2,
                     planned_contracts)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (opportunity_id, variant_name) DO NOTHING
                RETURNING experiment_id
            """, (
                exp_id, opp_id,
                FVG_STRATEGY_NAME, STRATEGY_FAMILY_FVG, "FVG_RESEARCH_BASELINE_V1", "1.0",
                STRATEGY_FAMILY_FVG, variant,
                json.dumps(param_diff), entry_rule,
                "FVG_BAR_CLOSE_INSIDE_ZONE",
                "FVG_OPPOSITE_BOUNDARY_PLUS_2TICK",
                tp_lbl, "FIXED",
                json.dumps(filter_rules),
                GHOST_SLIPPAGE_TICKS, GHOST_COMM_PER_SIDE_USD,
                None, planned_stop, planned_tp1, None, None,
            ))
            if cur.fetchone() is None:
                db.rollback()
                return False

            init_status = ResultStatus.COMPLETED if pre_no_entry else ResultStatus.WATCHING_ENTRY
            init_result = OutcomeResult.NO_ENTRY  if pre_no_entry else None

            cur.execute("""
                INSERT INTO ghost_experiment_results
                    (result_id, experiment_id, opportunity_id, status,
                     entry_price, stop_price, tp1_price, tp2_price, result)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (experiment_id) DO NOTHING
            """, (
                res_id, exp_id, opp_id, init_status,
                None, planned_stop, planned_tp1, None, init_result,
            ))
            db.commit()

            if init_status != ResultStatus.COMPLETED:
                with self._lock:
                    self._open_results[res_id] = {
                        "result_id":      res_id,
                        "experiment_id":  exp_id,
                        "opportunity_id": opp_id,
                        "status":         init_status,
                        "variant_name":   variant,
                        "instrument":     inst,
                        "direction":      direction,
                        "entry_price":    None,
                        "stop_price":     planned_stop,
                        "tp1_price":      planned_tp1,
                        "tp2_price":      None,
                        "entry_rule":     entry_rule,
                        "filter_rules":   filter_rules,
                        "mfe_r": 0.0, "mae_r": 0.0,
                        "mfe_price": None, "mae_price": None,
                        "bars_held": 0, "last_bar_ts": None,
                        "_fvg_family": True,   # sentinel: skip ORB _process_open_experiments
                    }
            return True
        except Exception as exc:
            self._log.warning("GRE FVG insert_experiment (%s %s): %s", variant, opp_id, exc)
            return False

    # ── Open FVG experiment evaluation ────────────────────────────────────────

    def _fvg_process_open_experiments(
        self,
        inst:          str,
        zones_by_rfid: Dict[str, Dict],
        bar:           Dict,
        bar_ts:        Any,
        bar_low:       float,
        bar_high:      float,
        price:         float,
        canonical:     Dict,
    ) -> None:
        bar_c = float(bar.get("close") or price)
        with self._lock:
            result_ids = [
                rid for rid, rd in self._open_results.items()
                if rd.get("instrument") == inst and rd.get("_fvg_family")
            ]
        for result_id in result_ids:
            try:
                self._fvg_process_one_experiment(
                    result_id, inst, zones_by_rfid,
                    bar_ts, bar_low, bar_high, bar_c, canonical,
                )
            except Exception as exc:
                self._log.debug("GRE FVG process-experiment (%s): %s", result_id, exc)

    def _fvg_process_one_experiment(
        self,
        result_id:     str,
        inst:          str,
        zones_by_rfid: Dict[str, Dict],
        bar_ts:        Any,
        bar_h:         Optional[float],
        bar_l:         Optional[float],
        bar_c:         float,
        canonical:     Dict,
    ) -> None:
        with self._lock:
            rd = self._open_results.get(result_id)
            if rd is None:
                return
            rd = dict(rd)   # snapshot

        status    = rd.get("status")
        direction = rd.get("direction", "Long")
        stop      = _sn(rd.get("stop_price"))
        tp1       = _sn(rd.get("tp1_price"))
        entry     = _sn(rd.get("entry_price"))
        filter_r  = rd.get("filter_rules") or {}

        rfid      = filter_r.get("_rfid", "")
        revisit_n = filter_r.get("_revisit_n", 0)
        fvg_upper = filter_r.get("_fvg_upper")
        fvg_lower = filter_r.get("_fvg_lower")
        fvg_dir   = filter_r.get("_fvg_direction", "BULLISH")

        # ── WATCHING_ENTRY ────────────────────────────────────────────────────
        if status in (ResultStatus.WATCHING_ENTRY, ResultStatus.PENDING):

            # Check zone still exists / not invalidated
            zone = zones_by_rfid.get(rfid)
            zone_status = (zone or {}).get("status", "UNKNOWN")
            if zone_status in ("MITIGATED", "FAILED", "EXPIRED"):
                self._complete_experiment(
                    result_id, exit_price=None, exit_reason="FVG_ZONE_GONE",
                    result=OutcomeResult.INVALIDATED_BEFORE_ENTRY, exit_ts=_now_utc(),
                )
                return

            # Time-based entry expiry
            bars_waiting = (rd.get("bars_held") or 0) + 1
            with self._lock:
                rd2 = self._open_results.get(result_id)
                if rd2:
                    rd2["bars_held"]   = bars_waiting
                    rd2["last_bar_ts"] = bar_ts
            if bars_waiting > _FVG_MAX_WAITING_BARS:
                self._complete_experiment(
                    result_id, exit_price=None, exit_reason="FVG_ENTRY_EXPIRED",
                    result=OutcomeResult.EXPIRED, exit_ts=_now_utc(),
                )
                return

            # Check variant-specific entry condition
            entered, entry_price = self._fvg_check_entry_condition(
                rd.get("entry_rule", ""), direction, filter_r, canonical,
                bar_l, bar_h, bar_c, fvg_upper, fvg_lower, fvg_dir, revisit_n,
            )
            if entered and entry_price is not None:
                tp_mult = filter_r.get("tp_mult", _FVG_BASELINE_TARGET_R)
                risk    = abs(entry_price - stop) if stop is not None else 0.0
                actual_tp1 = (
                    (entry_price + tp_mult * risk) if direction == "Long"
                    else (entry_price - tp_mult * risk)
                ) if risk > 0 else tp1
                cost_r = _commission_r(inst, entry_price, stop) if stop else 0.0
                self._enter_experiment(
                    result_id, rd["experiment_id"], rd["opportunity_id"],
                    entry_price, stop, actual_tp1, None,
                    cost_r=cost_r, qualified_ts=_now_utc(),
                )
                with self._lock:
                    rd3 = self._open_results.get(result_id)
                    if rd3:
                        rd3["tp1_price"] = actual_tp1

        # ── ACTIVE ────────────────────────────────────────────────────────────
        elif status == ResultStatus.ACTIVE:
            if stop is None or bar_h is None or bar_l is None:
                return

            stop_hit = (
                (direction == "Long"  and bar_l <= stop) or
                (direction == "Short" and bar_h >= stop)
            )
            tp1_hit = bool(tp1 and (
                (direction == "Long"  and bar_h >= tp1) or
                (direction == "Short" and bar_l <= tp1)
            ))

            if stop_hit and tp1_hit:
                # Same-bar ambiguity: conservative → stop wins
                self._complete_experiment(
                    result_id, exit_price=stop, exit_reason="STOP_HIT",
                    result=OutcomeResult.LOSS, exit_ts=_now_utc(), ambiguous_bar=True,
                )
                return
            if tp1_hit:
                self._complete_experiment(
                    result_id, exit_price=tp1, exit_reason="TP1_HIT",
                    result=OutcomeResult.WIN, exit_ts=_now_utc(), tp1_hit=True,
                )
                return
            if stop_hit:
                self._complete_experiment(
                    result_id, exit_price=stop, exit_reason="STOP_HIT",
                    result=OutcomeResult.LOSS, exit_ts=_now_utc(),
                )
                return

            # Track MFE / MAE while open
            if entry is not None and stop is not None:
                risk_pts = abs(entry - stop)
                if risk_pts > 0:
                    with self._lock:
                        rd2 = self._open_results.get(result_id)
                        if rd2:
                            mfe_r, mae_r, mfe_p, mae_p = _update_mfe_mae(
                                direction, bar_h, bar_l, entry, risk_pts,
                                _sn(rd2.get("mfe_price")), _sn(rd2.get("mae_price")),
                                rd2.get("mfe_r", 0.0) or 0.0,
                                rd2.get("mae_r", 0.0) or 0.0,
                            )
                            rd2.update({
                                "mfe_r": mfe_r, "mae_r": mae_r,
                                "mfe_price": mfe_p, "mae_price": mae_p,
                                "bars_held":   (rd2.get("bars_held") or 0) + 1,
                                "last_bar_ts": bar_ts,
                            })

    # ── Variant-specific entry condition ──────────────────────────────────────

    def _fvg_check_entry_condition(
        self,
        entry_rule: str,
        direction:  str,
        filter_r:   Dict,
        canonical:  Dict,
        bar_l:      Optional[float],
        bar_h:      Optional[float],
        bar_c:      float,
        fvg_upper:  Optional[float],
        fvg_lower:  Optional[float],
        fvg_dir:    str,
        revisit_n:  int,
    ) -> Tuple[bool, Optional[float]]:
        """
        Evaluate variant-specific FVG entry condition.
        Returns (entered, entry_price).  Entry price = bar CLOSE (conservative fill).
        """
        if bar_l is None or bar_h is None:
            return False, None
        if fvg_upper is None or fvg_lower is None:
            return False, None

        gap = max(fvg_upper - fvg_lower, 1e-9)

        # How far INTO the zone does the close reach (0=entry edge, 1=full)
        close_inside = fvg_lower <= bar_c <= fvg_upper
        if fvg_dir.upper() == "BULLISH":
            close_depth_pct = max(0.0, min(1.0, (fvg_upper - bar_c) / gap)) if close_inside else 0.0
        else:
            close_depth_pct = max(0.0, min(1.0, (bar_c - fvg_lower) / gap)) if close_inside else 0.0

        # ── Pre-condition: revisit count gate ─────────────────────────────────
        max_revisit = filter_r.get("max_revisit_n")
        if max_revisit is not None and revisit_n > max_revisit:
            return False, None

        # ── Pre-condition: trend alignment ────────────────────────────────────
        if filter_r.get("require_trend_align"):
            cur_trend = str(
                canonical.get("trend", {}).get("trend_15m", "")
                or filter_r.get("_trend_15m", "")
            ).upper()
            if direction == "Long"  and "BULL" not in cur_trend:
                return False, None
            if direction == "Short" and "BEAR" not in cur_trend:
                return False, None

        # ── Pre-condition: CVD alignment ──────────────────────────────────────
        if filter_r.get("require_cvd_align"):
            cur_cvd = str(
                canonical.get("cvd", {}).get("direction", "")
                or filter_r.get("_cvd_direction", "")
            ).upper()
            if direction == "Long"  and ("BEAR" in cur_cvd or cur_cvd in ("SHORT", "NEGATIVE")):
                return False, None
            if direction == "Short" and ("BULL" in cur_cvd or cur_cvd in ("LONG", "POSITIVE")):
                return False, None

        # ── Rule-specific entry checks ────────────────────────────────────────

        if entry_rule == "FVG_NEAR_EDGE":
            max_d = filter_r.get("max_depth_pct", _FVG_NEAR_EDGE_FRAC)
            if close_inside and close_depth_pct <= max_d:
                return True, bar_c
            return False, None

        if entry_rule == "FVG_MIDPOINT":
            min_d = filter_r.get("min_depth_pct", _FVG_MIDPOINT_MIN_PCT)
            if close_inside and close_depth_pct >= min_d:
                return True, bar_c
            return False, None

        if entry_rule == "FVG_DEEP_FILL":
            min_d = filter_r.get("min_depth_pct", _FVG_DEEP_FILL_MIN_PCT)
            if close_inside and close_depth_pct >= min_d:
                return True, bar_c
            return False, None

        # FVG_ZONE_TOUCH (BASELINE and all filter-only variants): close inside zone
        if close_inside:
            return True, bar_c

        return False, None
