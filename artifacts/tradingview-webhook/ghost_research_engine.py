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
    WIN          = "WIN"
    LOSS         = "LOSS"
    BREAKEVEN    = "BREAKEVEN"
    NO_ENTRY     = "NO_ENTRY"
    EXPIRED      = "EXPIRED"
    INVALID_DATA = "INVALID_DATA"

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
    ) -> None:
        self._get_db      = get_db_fn
        self._get_can     = get_canonical_fn
        self._get_bars    = get_bars_fn
        self._re_event    = re_event_fn
        self._instruments = list(instruments)
        self._max_variants = min(max_variants, MAX_GHOST_VARIANTS_HARD_CAP)

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
                self._open_results[d["result_id"]] = d
                restored += 1
        self._log.info("GhostResearchEngine: restored %d active experiments", restored)

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
                     structure_bos, structure_choch)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
            result_ids = [rid for rid, rd in self._open_results.items()
                         if rd.get("instrument") == inst]

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

    def get_health(self) -> Dict:
        with self._lock:
            open_count = len(self._open_results)
        today = datetime.now(timezone.utc).date().isoformat()
        try:
            db  = self._get_db()
            cur = db.cursor()
            cur.execute("""
                SELECT
                    (SELECT COUNT(*) FROM ghost_opportunities WHERE trading_date=%s) AS opps_today,
                    (SELECT COUNT(*) FROM ghost_experiments) AS total_experiments,
                    (SELECT COUNT(*) FROM ghost_experiment_results WHERE status='COMPLETED') AS completed,
                    (SELECT COUNT(*) FROM ghost_experiment_results
                     WHERE result='NO_ENTRY') AS no_entry
            """, (today,))
            row = cur.fetchone()
            return {
                "gre_version":        GRE_VERSION,
                "db_ready":           GhostResearchEngine.GRE_DB_READY,
                "strategy":           STRATEGY_NAME,
                "opportunities_today": row[0] if row else 0,
                "total_experiments":   row[1] if row else 0,
                "active_ghost_trades": open_count,
                "completed":           row[2] if row else 0,
                "no_entry_count":      row[3] if row else 0,
            }
        except Exception as exc:
            return {"db_ready": False, "error": str(exc)}

    def get_candidates(self, min_samples: int = 10) -> List[Dict]:
        """Top research candidates sorted by net expectancy."""
        try:
            db  = self._get_db()
            cur = db.cursor()
            cur.execute("""
                SELECT e.experiment_id, e.variant_name, e.evidence_state,
                       o.instrument,
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
                GROUP BY e.experiment_id, e.variant_name, e.evidence_state, o.instrument
                HAVING COUNT(r.result_id) FILTER (WHERE r.status='COMPLETED') >= %s
                ORDER BY avg_net_r DESC NULLS LAST
                LIMIT 50
            """, (min_samples,))
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            self._log.debug("GRE get_candidates: %s", exc)
            return []

    def get_experiments(self, instrument: Optional[str] = None,
                        variant: Optional[str] = None,
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
