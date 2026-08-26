"""
Profitability Engine — Phase 1
===============================
RESEARCH / DISPLAY-ONLY. Never touches the gate, scoring, sizing, or execution.

Pipeline:
    valid strategy setup → ghost observation → immutable trade plan →
    market outcome → net R result → edge ledger

Design principles:
    • All computation functions are PURE (no app.py imports, no side effects)
    • Ghost observations fire BEFORE execution gates (records every READY setup)
    • Trade plan is frozen at observation time; mutations in the live path are irrelevant
    • Conservative stop-first resolution for ambiguous intrabar data
    • Commission modelled unconditionally (not tied to any display-only toggle)
    • Strategy × instrument grouping is enforced in every aggregation
    • AI context stored as metadata only — never used to determine win/loss

DB convention: no DDL here — ghost_observations table created via DB tool / publish
schema-diff.  This module never creates tables.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# ── Ghost lifecycle constants ────────────────────────────────────────────────
GHOST_MAX_HOLD_BARS       = 240          # bars before marking 'expired' (~4 hours at 1-min)
GHOST_SAME_BAR_GUARD_SECS = 59.0        # don't exit on the bar that opened the ghost
GHOST_SOURCE_LIVE_SHADOW  = "live_shadow"
GHOST_SOURCE_PAPER        = "paper"
GHOST_SOURCE_BACKTEST     = "backtest"

# ── Default commission model ─────────────────────────────────────────────────
# Tradovate typical retail rate. Applied unconditionally for ghost observations
# (research must price in costs — not a display-only toggle).
GHOST_COMM_PER_SIDE_USD    = 0.62       # USD per contract per side (entry + exit = 1.24)
GHOST_SLIPPAGE_TICKS       = 1.0        # worst-case ticks per side (conservative)

# ── Close reasons ─────────────────────────────────────────────────────────────
CLOSE_STOP         = "stop"
CLOSE_TP1          = "tp1"
CLOSE_TP1_PARTIAL  = "tp1_partial"   # leg 1 of a two-leg trade hit — keep open for leg 2
CLOSE_TP2          = "tp2"
CLOSE_EXPIRED      = "expired"
CLOSE_AMBIGUOUS    = "ambiguous"

# ── Exit models ──────────────────────────────────────────────────────────────
EXIT_MODEL_SINGLE   = "single_leg"
EXIT_MODEL_TWO_LEG  = "two_leg_scalp"

# ── Two-leg default weights (50/50 split — standard SCALP TP1 + runner) ──────
TWO_LEG_WEIGHT_L1   = 0.5
TWO_LEG_WEIGHT_L2   = 0.5

# ── Lifecycle statuses ───────────────────────────────────────────────────────
STATUS_OPEN   = "open"
STATUS_CLOSED = "closed"
STATUS_EXPIRED= "expired"


# ---------------------------------------------------------------------------
# Observation key — stable dedup key for idempotent INSERT
# ---------------------------------------------------------------------------

def build_obs_key(
    instrument: str,
    direction: str,
    strategy_short: str,
    et_day: str,
    entry_bucket: float,
) -> str:
    """Build a stable dedup key for a ghost observation.

    ``et_day`` should be YYYYMMDD in US/Eastern time.
    ``entry_bucket`` should be entry price rounded to the nearest 0.5 price
    points so tiny quote fluctuations don't produce duplicate observations.

    The format is deliberately human-readable for manual inspection.
    """
    return f"ghost|{instrument}|{direction}|{strategy_short}|{et_day}|{int(round(entry_bucket * 2))}"


def entry_bucket_from_price(price: float) -> float:
    """Round a price to the nearest 0.5 for obs_key bucketing."""
    try:
        return round(round(float(price) * 2) / 2, 1)
    except (TypeError, ValueError):
        return 0.0


def extract_strategy_short(strategy_key: str) -> str:
    """Extract the strategy dimension from a 4-part pipe key.

    4-part format: ``{INST}|{MODE}|{STRATEGY}|{DIR}``
    Legacy format: ``MNQ_SCALP_LIQUIDITY_SWEEP_LONG``

    Returns the STRATEGY component or the full key if parsing fails.
    """
    if not strategy_key:
        return "UNKNOWN"
    if "|" in strategy_key:
        parts = strategy_key.split("|")
        if len(parts) >= 3:
            return parts[2]
    # Legacy key — strip leading {INST}_{MODE}_ prefix (first 2 parts)
    sk_parts = strategy_key.split("_")
    if len(sk_parts) > 2:
        return "_".join(sk_parts[2:])
    return strategy_key


# ---------------------------------------------------------------------------
# Commission / cost model (pure — no app.py globals)
# ---------------------------------------------------------------------------

def compute_commission_r(
    instrument: str,
    entry: float,
    stop: float,
    instrument_specs: Dict[str, Any],
    comm_per_side_usd: float = GHOST_COMM_PER_SIDE_USD,
    slippage_ticks: float = GHOST_SLIPPAGE_TICKS,
) -> Optional[float]:
    """Compute round-trip trading cost as a fraction of R.

    cost_$  = (commission_per_side × 2) + (slippage_ticks × tick_size × point_value × 2)
    risk_$  = |entry − stop| × point_value
    cost_R  = cost_$ / risk_$          (contracts cancel; this is per-contract)

    Returns a non-negative float, or None if the calculation cannot be
    completed (unknown instrument, zero risk distance, invalid prices).

    Direction-independent — cost is always adverse.
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
        # Try without the asset wrapper
        for _k, _v in instrument_specs.items():
            if isinstance(_v, dict) and _v.get("symbol") == instrument:
                inner = _v.get("specs") or _v
                spec = inner
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


# ---------------------------------------------------------------------------
# R-multiple calculation (pure)
# ---------------------------------------------------------------------------

def compute_two_leg_gross_r(
    tp1_gross_r: float,
    leg2_gross_r: float,
    leg1_weight: float = TWO_LEG_WEIGHT_L1,
    leg2_weight: float = TWO_LEG_WEIGHT_L2,
) -> float:
    """Weighted-average gross R for a two-leg exit.

    Standard SCALP exits half at TP1 and runs the remainder with a trailing
    stop, so each leg carries 50% weight.  Weights must sum to 1.0.

    Example:
        Leg 1 exits at TP1 → gross_r = +2.0R (50% weight → +1.0R contribution)
        Leg 2 runner stopped at breakeven → gross_r = 0.0R (50% weight → +0.0R)
        Total weighted gross_r = +1.0R
    """
    return round(leg1_weight * tp1_gross_r + leg2_weight * leg2_gross_r, 4)


def compute_gross_r(
    direction: str,
    entry: float,
    exit_price: float,
    stop: float,
) -> Optional[float]:
    """Compute raw (gross) R-multiple for a completed trade.

    Long:  gross_r = (exit − entry) / |entry − stop|
    Short: gross_r = (entry − exit) / |entry − stop|

    Returns None when any input is invalid or risk distance is zero.
    """
    try:
        e  = float(entry)
        ex = float(exit_price)
        s  = float(stop)
    except (TypeError, ValueError):
        return None
    risk = abs(e - s)
    if risk <= 0:
        return None
    if direction == "Long":
        return (ex - e) / risk
    elif direction == "Short":
        return (e - ex) / risk
    return None


# ---------------------------------------------------------------------------
# MFE / MAE tracking (pure — called once per bar)
# ---------------------------------------------------------------------------

def update_mfe_mae(
    direction: str,
    bar_high: float,
    bar_low: float,
    entry: float,
    risk_points: float,
    current_mfe_price: Optional[float],
    current_mae_price: Optional[float],
    current_mfe_r: float,
    current_mae_r: float,
) -> Tuple[float, float, float, float]:
    """Return updated (mfe_r, mae_r, mfe_price, mae_price) after one bar.

    MFE: maximum favorable excursion from entry (best price reached in
         the profitable direction).
    MAE: maximum adverse excursion from entry (worst price reached in
         the loss direction).

    Uses the bar's HIGH and LOW — conservative because we don't know tick
    ordering within the bar.

    risk_points = abs(entry − stop).  Must be > 0.
    """
    if risk_points <= 0:
        return current_mfe_r, current_mae_r, current_mfe_price or entry, current_mae_price or entry

    if direction == "Long":
        fav_price = bar_high     # best case for a long
        adv_price = bar_low      # worst case for a long
    elif direction == "Short":
        fav_price = bar_low      # best case for a short
        adv_price = bar_high     # worst case for a short
    else:
        return current_mfe_r, current_mae_r, current_mfe_price or entry, current_mae_price or entry

    # MFE — in R from entry
    if direction == "Long":
        new_fav_r = (fav_price - entry) / risk_points
    else:
        new_fav_r = (entry - fav_price) / risk_points

    # MAE — in R from entry (adverse direction is negative R)
    if direction == "Long":
        new_adv_r = (adv_price - entry) / risk_points   # will be negative
    else:
        new_adv_r = (entry - adv_price) / risk_points   # will be negative

    new_mfe_r     = max(current_mfe_r, new_fav_r)
    new_mae_r     = min(current_mae_r, new_adv_r)

    # Update price trackers
    if direction == "Long":
        new_mfe_price = fav_price if new_fav_r >= current_mfe_r else (current_mfe_price or entry)
        new_mae_price = adv_price if new_adv_r <= current_mae_r else (current_mae_price or entry)
    else:
        new_mfe_price = fav_price if new_fav_r >= current_mfe_r else (current_mfe_price or entry)
        new_mae_price = adv_price if new_adv_r <= current_mae_r else (current_mae_price or entry)

    return new_mfe_r, new_mae_r, new_mfe_price, new_mae_price


# ---------------------------------------------------------------------------
# Conservative bar resolution (pure)
# ---------------------------------------------------------------------------

def resolve_bar_outcome(
    direction: str,
    bar_high: float,
    bar_low: float,
    entry: float,
    stop: float,
    target1: float,
    target2: Optional[float],
    tp1_hit: bool,
    bars_held: int,
    max_hold_bars: int = GHOST_MAX_HOLD_BARS,
) -> Tuple[Optional[str], Optional[str], Optional[float], Optional[float]]:
    """Determine if this bar closes the ghost trade.

    Returns ``(status, close_reason, exit_price, gross_r)`` where:
      • status is one of "closed" / "expired" / None (still open)
      • close_reason is one of stop / tp1 / tp2 / expired / ambiguous / None
      • exit_price is the resolved fill price (or None if still open)
      • gross_r is the raw R result (or None if still open)

    Ambiguity rule (Step 10 of the spec):
      If the bar touches BOTH the stop and an active target, we conservatively
      assume the stop was hit first (worst-case for the trader).  We never
      optimistically assign the profit.

    This prevents fake expectancy from optimistic fill assumptions.
    """
    risk = abs(entry - stop)
    if risk <= 0:
        return None, None, None, None

    if direction == "Long":
        stop_touched   = bar_low  <= stop
        tp1_touched    = bar_high >= target1
        tp2_touched    = (target2 is not None) and (bar_high >= target2)
    elif direction == "Short":
        stop_touched   = bar_high >= stop
        tp1_touched    = bar_low  <= target1
        tp2_touched    = (target2 is not None) and (bar_low <= target2)
    else:
        return None, None, None, None

    # ── TP2 logic (only reachable after TP1 already hit) ─────────────────
    if tp1_hit and tp2_touched and target2 is not None:
        if stop_touched:
            # Ambiguous: same bar touches both stop and TP2 — conservatively
            # use stop outcome (we already moved stop to BE or partial, so
            # it's a breakeven — but this is the conservative path).
            exit_px = stop
            gross_r = compute_gross_r(direction, entry, exit_px, stop) or 0.0
            return STATUS_CLOSED, CLOSE_AMBIGUOUS, exit_px, gross_r
        exit_px = float(target2)
        gross_r = compute_gross_r(direction, entry, exit_px, stop) or 0.0
        return STATUS_CLOSED, CLOSE_TP2, exit_px, gross_r

    # ── TP1 logic ─────────────────────────────────────────────────────────
    if not tp1_hit and tp1_touched:
        if stop_touched:
            # Ambiguous — stop-first (conservative)
            exit_px = stop
            gross_r = compute_gross_r(direction, entry, exit_px, stop) or 0.0
            return STATUS_CLOSED, CLOSE_AMBIGUOUS, exit_px, gross_r
        # TP1 hit (clean)
        exit_px = float(target1)
        gross_r = compute_gross_r(direction, entry, exit_px, stop) or 0.0
        if target2 is None:
            # Single-target strategy: TP1 is the full exit → close now
            return STATUS_CLOSED, CLOSE_TP1, exit_px, gross_r
        # Two-leg strategy: TP1 hit but stay OPEN for leg 2.
        # Return status=None (still open) + CLOSE_TP1_PARTIAL so the caller
        # knows to set tp1_hit=True and store tp1_exit_price/tp1_gross_r
        # WITHOUT closing the observation.
        return None, CLOSE_TP1_PARTIAL, exit_px, gross_r

    # ── Stop logic ────────────────────────────────────────────────────────
    if stop_touched:
        exit_px = stop
        gross_r = compute_gross_r(direction, entry, exit_px, stop) or 0.0
        return STATUS_CLOSED, CLOSE_STOP, exit_px, gross_r

    # ── Max-hold expiry ───────────────────────────────────────────────────
    if bars_held >= max_hold_bars:
        # Use current bar close as exit (approximation — conservative)
        if direction == "Long":
            exit_px = bar_low   # last bar's low is a conservative fill assumption
        else:
            exit_px = bar_high
        gross_r = compute_gross_r(direction, entry, exit_px, stop) or 0.0
        return STATUS_EXPIRED, CLOSE_EXPIRED, exit_px, gross_r

    return None, None, None, None   # still open


def resolve_single_leg_paper_bar(
    direction: str,
    bar_high: float,
    bar_low: float,
    entry: float,
    stop: float,
    target: float,
) -> Optional[Tuple[str, str, float, float]]:
    """Resolve a one-target paper trade using the canonical bar resolver.

    Paper ledgers store a compact ``win`` / ``loss`` / ``expired`` result rather
    than the richer ghost lifecycle status.  Keeping that translation here means
    the scalp and dual paper ledgers cannot drift into different stop/target or
    SHORT-R semantics.  ``None`` means the bar did not close the trade.

    Returns ``(result, close_reason, exit_price, gross_r)``.  A stop-first
    ambiguous bar is a loss because the canonical resolver returns the stop fill.
    """
    status, close_reason, exit_price, gross_r = resolve_bar_outcome(
        direction=direction,
        bar_high=bar_high,
        bar_low=bar_low,
        entry=entry,
        stop=stop,
        target1=target,
        target2=None,
        tp1_hit=False,
        bars_held=0,
        # Wall-clock max-hold policies belong to the paper-ledger watcher.  This
        # helper is strictly a single market-bar decision.
        max_hold_bars=2**31 - 1,
    )
    if status is None or close_reason is None or exit_price is None or gross_r is None:
        return None
    result = "expired" if status == STATUS_EXPIRED else (
        "win" if float(gross_r) > 0 else "loss"
    )
    return result, close_reason, float(exit_price), round(float(gross_r), 4)


# ---------------------------------------------------------------------------
# Edge Ledger — aggregation (pure, operates on a list of closed-row dicts)
# ---------------------------------------------------------------------------

def compute_profit_factor(total_win_r: float, total_loss_r: float) -> Optional[float]:
    """Profit factor = gross_wins / abs(gross_losses).

    Returns None when there are no losses (undefined / infinite).
    """
    if abs(total_loss_r) < 1e-9:
        return None   # undefined (no losses yet)
    return round(total_win_r / abs(total_loss_r), 4)


def compute_max_drawdown(cumulative_r_series: List[float]) -> float:
    """Maximum peak-to-trough drawdown in R from a running cumulative-R series.

    Returns a non-positive float (e.g. -1.5 means a 1.5R drawdown).
    Returns 0.0 when there is no drawdown.
    """
    if not cumulative_r_series:
        return 0.0
    peak = cumulative_r_series[0]
    max_dd = 0.0
    for val in cumulative_r_series:
        peak = max(peak, val)
        dd   = val - peak
        max_dd = min(max_dd, dd)
    return round(max_dd, 4)


def compute_edge_ledger_stats(
    closed_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Aggregate a list of closed ghost-observation dicts into Edge Ledger stats.

    Each dict must have at minimum:
        net_r (float or None), gross_r (float or None), cost_r (float or None)

    Optional: mfe_r, mae_r, close_reason

    Groups by (strategy_key, instrument) — this function handles ONE group.
    Call once per (strategy_key × instrument) combination.

    Returns a dict matching the STEP 12 specification:
        total_observations, activated_trades, closed_trades, wins, losses,
        breakevens, win_rate, avg_gross_r, avg_net_r, cumulative_gross_r,
        cumulative_net_r, avg_winner_r, avg_loser_r, profit_factor,
        max_drawdown_r, avg_mfe, avg_mae, net_expectancy_r
    """
    total       = len(closed_rows)
    wins        = 0
    losses      = 0
    breakevens  = 0
    sum_gross_r = 0.0
    sum_net_r   = 0.0
    sum_win_r   = 0.0
    sum_loss_r  = 0.0
    sum_mfe     = 0.0
    sum_mae     = 0.0
    mfe_count   = 0
    mae_count   = 0
    cum_net_r_series: List[float] = []
    running = 0.0

    for row in closed_rows:
        net_r   = row.get("net_r")
        gross_r = row.get("gross_r")
        mfe_r   = row.get("mfe_r")
        mae_r   = row.get("mae_r")

        try:
            net_r   = float(net_r)   if net_r   is not None else None
            gross_r = float(gross_r) if gross_r is not None else None
            mfe_r   = float(mfe_r)   if mfe_r   is not None else None
            mae_r   = float(mae_r)   if mae_r   is not None else None
        except (TypeError, ValueError):
            net_r = gross_r = mfe_r = mae_r = None

        if gross_r is not None:
            sum_gross_r += gross_r
        if net_r is not None:
            sum_net_r += net_r
            running   += net_r
            cum_net_r_series.append(running)

            if net_r > 0.0:
                wins     += 1
                sum_win_r += net_r
            elif net_r < 0.0:
                losses    += 1
                sum_loss_r += net_r
            else:
                breakevens += 1

        if mfe_r is not None:
            sum_mfe   += mfe_r
            mfe_count += 1
        if mae_r is not None:
            sum_mae   += mae_r
            mae_count += 1

    closed = wins + losses + breakevens

    win_rate    = round(wins / closed, 4) if closed > 0 else None
    avg_gross_r = round(sum_gross_r / closed, 4) if closed > 0 else None
    avg_net_r   = round(sum_net_r   / closed, 4) if closed > 0 else None
    avg_win_r   = round(sum_win_r   / wins,   4) if wins   > 0 else None
    avg_loss_r  = round(sum_loss_r  / losses, 4) if losses > 0 else None
    pf          = compute_profit_factor(sum_win_r, sum_loss_r)
    max_dd      = compute_max_drawdown(cum_net_r_series)
    avg_mfe     = round(sum_mfe / mfe_count, 4) if mfe_count > 0 else None
    avg_mae     = round(sum_mae / mae_count, 4) if mae_count > 0 else None

    return {
        "total_observations":  total,
        "activated_trades":    total,    # Phase 1: all observations are treated as activated
        "closed_trades":       closed,
        "wins":                wins,
        "losses":              losses,
        "breakevens":          breakevens,
        "win_rate":            win_rate,
        "avg_gross_r":         avg_gross_r,
        "avg_net_r":           avg_net_r,
        "cumulative_gross_r":  round(sum_gross_r, 4),
        "cumulative_net_r":    round(sum_net_r,   4),
        "avg_winner_r":        avg_win_r,
        "avg_loser_r":         avg_loss_r,
        "profit_factor":       pf,
        "max_drawdown_r":      max_dd,
        "avg_mfe":             avg_mfe,
        "avg_mae":             avg_mae,
        "net_expectancy_r":    avg_net_r,   # alias for clarity
    }


def aggregate_by_strategy_instrument(
    closed_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Group closed rows by (strategy_key, instrument) and compute stats for each.

    Returns a list of dicts, each containing:
        strategy_key, instrument, + all fields from compute_edge_ledger_stats()
    Sorted by descending |net_expectancy_r| then alphabetically.
    """
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for row in closed_rows:
        sk   = str(row.get("strategy_key") or "UNKNOWN")
        inst = str(row.get("instrument")   or "UNKNOWN")
        key  = (sk, inst)
        if key not in groups:
            groups[key] = []
        groups[key].append(row)

    results = []
    for (sk, inst), rows in groups.items():
        stats = compute_edge_ledger_stats(rows)
        results.append({
            "strategy_key": sk,
            "instrument":   inst,
            **stats,
        })

    results.sort(
        key=lambda x: (
            -abs(x.get("net_expectancy_r") or 0),
            x.get("strategy_key", ""),
            x.get("instrument", ""),
        )
    )
    return results
