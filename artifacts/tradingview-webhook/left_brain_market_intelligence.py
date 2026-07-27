"""
Left Brain Market Intelligence — Phase 1B (DISPLAY-ONLY)

Deterministic, no-AI market-state classification computed at every Databento
1m bar close.  Never touches the gate, scoring, execution, or sizing.

Consumed by _databento_bar_scan (daemon thread) via:
    _LEFT_BRAIN_MI_BY_INST[inst] = compute_left_brain_mi(inst, full_analysis_result)

Then attached to full_analysis result by:
    result["left_brain"] = {"market_intelligence": _LEFT_BRAIN_MI_BY_INST.get(inst)}

Feature flag: LEFT_BRAIN_MARKET_INTELLIGENCE_ENABLED (default OFF).
Flag OFF → this module is never imported → goldens byte-identical.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, time as _time
from typing import Any

logger = logging.getLogger(__name__)

# ── Canonical state enumerations ─────────────────────────────────────────────

MARKET_STATES = frozenset({
    "TRENDING_UP_STRONG",
    "TRENDING_UP_MILD",
    "TRENDING_DOWN_STRONG",
    "TRENDING_DOWN_MILD",
    "MEAN_REVERTING_RANGE",
    "DISTRIBUTION_TOPPING",
    "ACCUMULATION_BOTTOMING",
    "HIGH_VOLATILITY_ROTATION",
    "OPENING_DRIVE",
    "LATE_SESSION_DRIFT",
    "UNKNOWN",
})

SESSION_CHARACTERS = frozenset({
    "STRONG_TREND_DAY",
    "WEAK_TREND_DAY",
    "RANGE_EXPANSION",
    "INSIDE_DAY",
    "REVERSAL_DAY",
    "OPENING_DRIVE_DAY",
    "NEWS_DRIVEN",
    "CHOPPY",
    "UNKNOWN",
})

SESSION_PHASES = frozenset({
    "PRE_MARKET",
    "OPENING_DRIVE",
    "MORNING_SESSION",
    "MIDDAY_CHOP",
    "AFTERNOON_EXPANSION",
    "LATE_SESSION",
    "CLOSE_APPROACH",
})

AUCTION_CONTROLS = frozenset({"BUYER", "SELLER", "CONTESTED"})

PLAYBOOK_FAMILIES = frozenset({
    "MOMENTUM_CONTINUATION",
    "PULLBACK_TO_VWAP",
    "RANGE_FADE",
    "BREAKOUT",
    "REVERSAL_SETUP",
    "OPENING_RANGE_BREAK",
    "LATE_DAY_TREND_FOLLOW",
    "AVOID_CHOPPY_CONDITIONS",
})

# ── ET time-zone helpers ──────────────────────────────────────────────────────
# We use fixed UTC offsets (−5 EDT / −4 EST) per the market's Eastern Time.
# The exact DST boundary does not affect phase logic at the 30-min precision we
# need, so we approximate: ET ≈ UTC−4 during CDT season, UTC−5 otherwise.

_DST_START_MONTH = 3    # March
_DST_END_MONTH   = 11   # November

def _to_et_time(dt_utc: datetime) -> _time:
    """Return the wall-clock time in US Eastern from a UTC datetime."""
    offset_h = -4 if _DST_START_MONTH <= dt_utc.month < _DST_END_MONTH else -5
    et_h = (dt_utc.hour + offset_h) % 24
    return _time(et_h, dt_utc.minute, dt_utc.second)


def _session_phase(dt_utc: datetime) -> str:
    """Classify session phase from ET wall-clock time."""
    t = _to_et_time(dt_utc)
    if t < _time(9, 30):
        return "PRE_MARKET"
    if t < _time(10, 0):
        return "OPENING_DRIVE"
    if t < _time(11, 30):
        return "MORNING_SESSION"
    if t < _time(13, 0):
        return "MIDDAY_CHOP"
    if t < _time(14, 0):
        return "AFTERNOON_EXPANSION"
    if t < _time(15, 0):
        return "LATE_SESSION"
    return "CLOSE_APPROACH"


# ── Evidence extraction helpers ───────────────────────────────────────────────

def _cvd_direction(a: dict) -> str | None:
    """'bullish' | 'bearish' | None from the analysis result."""
    cvd = a.get("cvd") or {}
    return cvd.get("state") or cvd.get("direction")


def _price_vs_vwap(a: dict) -> str | None:
    """'above' | 'below' | 'at' | None."""
    price = a.get("current_price")
    vwap  = a.get("vwap_value")
    vstat = a.get("vwap_status")
    if vstat != "ok" or price is None or vwap is None:
        return None
    diff = price - vwap
    atr_pts = (a.get("volatility") or {}).get("atr_pts") or 1.0
    if abs(diff) < atr_pts * 0.1:
        return "at"
    return "above" if diff > 0 else "below"


def _structure_direction(a: dict) -> str | None:
    """'Long' | 'Short' | None from the most recent valid structure."""
    # Prefer the authoritative strict direction when available
    d = a.get("strict_direction") or a.get("candidate")
    if d in ("Long", "Short"):
        return d
    # Fall back to edge breakdown
    for comp in (a.get("edge_breakdown") or {}).values():
        if isinstance(comp, dict) and "direction" in comp:
            return comp["direction"]
    return None


def _vol_regime(a: dict) -> str | None:
    """'NORMAL' | 'HIGH_BLOCK' | 'QUIET_BLOCK' | 'EXTREME_HIGH' | None."""
    return (a.get("volatility") or {}).get("regime")


def _atr_ratio(a: dict) -> float | None:
    return (a.get("volatility") or {}).get("atr_ratio")


def _freshness_age_sec(ts_str: str | None) -> float | None:
    """Seconds since the timestamp; None if unparseable."""
    if not ts_str:
        return None
    try:
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except Exception:
        return None


# ── Auction control ───────────────────────────────────────────────────────────

def _compute_auction_control(a: dict) -> str:
    """BUYER / SELLER / CONTESTED from CVD + price-vs-VWAP agreement."""
    cvd_dir  = _cvd_direction(a)
    pv_vwap  = _price_vs_vwap(a)

    # Agreement matrix
    if cvd_dir == "bullish" and pv_vwap == "above":
        return "BUYER"
    if cvd_dir == "bearish" and pv_vwap == "below":
        return "SELLER"
    if cvd_dir == "bullish" and pv_vwap in ("above", "at"):
        return "BUYER"
    if cvd_dir == "bearish" and pv_vwap in ("below", "at"):
        return "SELLER"
    return "CONTESTED"


# ── Data confidence ───────────────────────────────────────────────────────────

def _compute_data_confidence(a: dict) -> int:
    """0–100 freshness-based confidence.  NOT agreement-based.

    Scoring:
      VWAP available + fresh (< 5 min)   : 30 pts
      VWAP source = databento              : +5  pts
      CVD available + fresh (< 5 min)    : 25 pts
      Price available + fresh (< 2 min)  : 25 pts
      Volatility available + fresh        : 15 pts
    """
    score = 0

    # VWAP
    vwap_diag  = a.get("vwap_diagnostics") or {}
    vwap_age   = vwap_diag.get("vwap_age_ms")
    if vwap_age is not None and vwap_age < 5 * 60 * 1000:
        score += 30
        if vwap_diag.get("vwap_source") == "databento":
            score += 5
    elif a.get("vwap_status") == "ok":
        score += 15  # present but age unknown

    # CVD
    cvd_rec = a.get("cvd") or {}
    cvd_age = _freshness_age_sec(cvd_rec.get("ts"))
    if cvd_age is not None and cvd_age < 5 * 60:
        score += 25
    elif cvd_rec.get("state") is not None:
        score += 10

    # Price
    price_ts  = a.get("current_price_ts") or a.get("last_price_ts")
    price_age = _freshness_age_sec(price_ts)
    if price_age is not None and price_age < 2 * 60:
        score += 25
    elif a.get("current_price") is not None:
        score += 12

    # Volatility
    vol = a.get("volatility") or {}
    vol_age = _freshness_age_sec(vol.get("ts"))
    if vol_age is not None and vol_age < 10 * 60:
        score += 15
    elif vol.get("atr_pts") is not None:
        score += 7

    return min(100, score)


# ── Directional outlook ───────────────────────────────────────────────────────

def _compute_directional_outlook(a: dict) -> dict[str, int]:
    """Evidence-family-weighted directional outlook summing to exactly 100.

    Evidence families and max weights:
        VWAP position        (weight 20)
        Structure/trend      (weight 25)
        CVD                  (weight 20)
        Volatility regime    (weight 15)
        Session phase bias   (weight 10)
        Price action / bar   (weight 10)
    Total = 100.  Each family contributes to long / short / neutral.
    All families are capped to prevent double-counting.
    """
    long_pts = short_pts = neutral_pts = 0

    # ── VWAP (20) ─────────────────────────────────────────────────────────────
    pv = _price_vs_vwap(a)
    if pv == "above":
        long_pts    += 20
    elif pv == "below":
        short_pts   += 20
    else:
        neutral_pts += 20

    # ── Structure / trend (25) ────────────────────────────────────────────────
    struct_dir = _structure_direction(a)
    if struct_dir == "Long":
        long_pts    += 25
    elif struct_dir == "Short":
        short_pts   += 25
    else:
        neutral_pts += 25

    # ── CVD (20) ──────────────────────────────────────────────────────────────
    cvd_dir = _cvd_direction(a)
    if cvd_dir == "bullish":
        long_pts    += 20
    elif cvd_dir == "bearish":
        short_pts   += 20
    else:
        neutral_pts += 20

    # ── Volatility regime (15) ────────────────────────────────────────────────
    regime = _vol_regime(a)
    if regime == "HIGH_BLOCK":
        # Elevated vol: ambiguous direction but strongly present
        neutral_pts += 15
    elif regime in ("NORMAL", None):
        neutral_pts += 15
    else:
        neutral_pts += 15

    # ── Session phase bias (10) ───────────────────────────────────────────────
    phase = a.get("_lb_session_phase")  # injected by caller
    if phase in ("OPENING_DRIVE", "MORNING_SESSION"):
        # Morning sessions favour continuation of any established direction
        if struct_dir == "Long":
            long_pts    += 10
        elif struct_dir == "Short":
            short_pts   += 10
        else:
            neutral_pts += 10
    elif phase in ("LATE_SESSION", "CLOSE_APPROACH"):
        # Late session: slight mean-reversion / neutral tilt
        neutral_pts += 10
    else:
        neutral_pts += 10

    # ── Price action / bar direction (10) ─────────────────────────────────────
    # Use the current verdict direction as a proxy for recent bar direction.
    verdict_dir = a.get("strict_direction")
    if verdict_dir == "Long":
        long_pts    += 10
    elif verdict_dir == "Short":
        short_pts   += 10
    else:
        neutral_pts += 10

    # Normalise to exactly 100
    total = long_pts + short_pts + neutral_pts
    if total <= 0:
        return {"long": 0, "short": 0, "neutral": 100}
    factor = 100.0 / total
    l = round(long_pts  * factor)
    s = round(short_pts * factor)
    n = 100 - l - s   # absorb rounding residual in neutral
    return {"long": l, "short": s, "neutral": n}


# ── Market state ─────────────────────────────────────────────────────────────

def _compute_market_state(a: dict, phase: str, outlook: dict) -> str:
    """Classify into one of 11 market states."""
    pv       = _price_vs_vwap(a)
    cvd_dir  = _cvd_direction(a)
    struct   = _structure_direction(a)
    regime   = _vol_regime(a)
    atr_r    = _atr_ratio(a)

    # Opening drive takes precedence in the first 30 min
    if phase == "OPENING_DRIVE":
        return "OPENING_DRIVE"

    # Late session low-vol drift
    if phase in ("LATE_SESSION", "CLOSE_APPROACH") and regime in (None, "NORMAL"):
        r = atr_r or 1.0
        if r < 0.7:
            return "LATE_SESSION_DRIFT"

    # High-vol rotation: ATR ratio > 2.0 with mixed signals
    if atr_r is not None and atr_r > 2.0:
        return "HIGH_VOLATILITY_ROTATION"

    long_bias  = outlook["long"]  > 50
    short_bias = outlook["short"] > 50

    if long_bias:
        strong = (pv == "above" and cvd_dir == "bullish" and struct == "Long")
        return "TRENDING_UP_STRONG" if strong else "TRENDING_UP_MILD"

    if short_bias:
        strong = (pv == "below" and cvd_dir == "bearish" and struct == "Short")
        return "TRENDING_DOWN_STRONG" if strong else "TRENDING_DOWN_MILD"

    # Neither side dominant → range / transition
    # Topping: price was above VWAP but structure flipped to Short
    if pv == "above" and struct == "Short":
        return "DISTRIBUTION_TOPPING"

    # Bottoming: price was below VWAP but structure flipped to Long
    if pv == "below" and struct == "Long":
        return "ACCUMULATION_BOTTOMING"

    return "MEAN_REVERTING_RANGE"


# ── Session character ─────────────────────────────────────────────────────────

def _compute_session_character(a: dict, phase: str, market_state: str) -> str:
    """Classify session character from higher-level cues."""
    if phase == "OPENING_DRIVE" and market_state == "OPENING_DRIVE":
        return "OPENING_DRIVE_DAY"

    # Check for news-driven (high vol + rapid regime shift)
    regime = _vol_regime(a)
    atr_r  = _atr_ratio(a)
    if regime == "HIGH_BLOCK" or (atr_r is not None and atr_r > 2.5):
        return "NEWS_DRIVEN"

    if market_state in ("TRENDING_UP_STRONG", "TRENDING_DOWN_STRONG"):
        return "STRONG_TREND_DAY"
    if market_state in ("TRENDING_UP_MILD", "TRENDING_DOWN_MILD"):
        return "WEAK_TREND_DAY"
    if market_state in ("DISTRIBUTION_TOPPING", "ACCUMULATION_BOTTOMING"):
        return "REVERSAL_DAY"
    if market_state == "MEAN_REVERTING_RANGE":
        return "CHOPPY"
    if market_state == "HIGH_VOLATILITY_ROTATION":
        return "RANGE_EXPANSION"
    return "UNKNOWN"


# ── Suitable playbooks ────────────────────────────────────────────────────────

_PLAYBOOK_MAP: dict[str, list[str]] = {
    "TRENDING_UP_STRONG":      ["MOMENTUM_CONTINUATION", "PULLBACK_TO_VWAP"],
    "TRENDING_UP_MILD":        ["PULLBACK_TO_VWAP", "MOMENTUM_CONTINUATION"],
    "TRENDING_DOWN_STRONG":    ["MOMENTUM_CONTINUATION", "PULLBACK_TO_VWAP"],
    "TRENDING_DOWN_MILD":      ["PULLBACK_TO_VWAP", "MOMENTUM_CONTINUATION"],
    "MEAN_REVERTING_RANGE":    ["RANGE_FADE", "AVOID_CHOPPY_CONDITIONS"],
    "DISTRIBUTION_TOPPING":    ["REVERSAL_SETUP", "PULLBACK_TO_VWAP"],
    "ACCUMULATION_BOTTOMING":  ["REVERSAL_SETUP", "PULLBACK_TO_VWAP"],
    "HIGH_VOLATILITY_ROTATION":["BREAKOUT", "AVOID_CHOPPY_CONDITIONS"],
    "OPENING_DRIVE":           ["OPENING_RANGE_BREAK", "MOMENTUM_CONTINUATION"],
    "LATE_SESSION_DRIFT":      ["LATE_DAY_TREND_FOLLOW", "AVOID_CHOPPY_CONDITIONS"],
    "UNKNOWN":                 ["AVOID_CHOPPY_CONDITIONS"],
}

_PHASE_PLAYBOOK_BONUS: dict[str, str] = {
    "OPENING_DRIVE":       "OPENING_RANGE_BREAK",
    "LATE_SESSION":        "LATE_DAY_TREND_FOLLOW",
    "CLOSE_APPROACH":      "LATE_DAY_TREND_FOLLOW",
}


def _compute_playbooks(market_state: str, phase: str) -> list[str]:
    """Return an ordered list of suitable playbook families (max 8)."""
    books = list(_PLAYBOOK_MAP.get(market_state, ["AVOID_CHOPPY_CONDITIONS"]))
    # Phase bonus: insert at front if not already present
    bonus = _PHASE_PLAYBOOK_BONUS.get(phase)
    if bonus and bonus not in books:
        books.insert(0, bonus)
    # Deduplicate preserving order
    seen: set[str] = set()
    result: list[str] = []
    for b in books:
        if b not in seen:
            seen.add(b)
            result.append(b)
    return result[:8]


# ── Evidence arrays ───────────────────────────────────────────────────────────

def _collect_evidence(a: dict, market_state: str, phase: str) -> tuple[list[str], list[str]]:
    """Return (supporting_evidence, missing_evidence) as human-readable strings."""
    supporting: list[str] = []
    missing:    list[str] = []

    pv       = _price_vs_vwap(a)
    cvd_dir  = _cvd_direction(a)
    struct   = _structure_direction(a)
    regime   = _vol_regime(a)
    vwap_ok  = a.get("vwap_status") == "ok"

    # VWAP
    if not vwap_ok:
        missing.append("VWAP unavailable — price-context checks disabled")
    elif pv == "above":
        supporting.append("Price above VWAP — buyers have control of session anchor")
    elif pv == "below":
        supporting.append("Price below VWAP — sellers have control of session anchor")
    else:
        supporting.append("Price at VWAP — contested auction; direction unclear")

    # Structure
    if struct == "Long":
        supporting.append("Confirmed BOS/CHOCH in Long direction — structural demand present")
    elif struct == "Short":
        supporting.append("Confirmed BOS/CHOCH in Short direction — structural supply present")
    else:
        missing.append("No confirmed structure break — setup-building phase")

    # CVD
    if cvd_dir == "bullish":
        supporting.append("Cumulative Volume Delta positive — aggressive buy-side participation")
    elif cvd_dir == "bearish":
        supporting.append("Cumulative Volume Delta negative — aggressive sell-side participation")
    else:
        missing.append("CVD direction unknown — cannot confirm order-flow alignment")

    # Volatility
    if regime == "NORMAL":
        supporting.append("Volatility normal — conditions suitable for setups")
    elif regime == "HIGH_BLOCK":
        supporting.append("Elevated volatility — wide ranges, risk larger than usual")
    elif regime in ("QUIET_BLOCK",):
        missing.append("Low volatility — minimal range; momentum setups may stall")
    elif regime is None:
        missing.append("Volatility data unavailable — ATR not yet computed")

    # Phase-specific
    if phase == "OPENING_DRIVE":
        supporting.append("Opening drive window (09:30–10:00 ET) — highest-momentum session phase")
    elif phase == "MIDDAY_CHOP":
        missing.append("Midday chop period — liquidity thinner, false breakouts more common")

    return supporting, missing


# ── 'What changes the thesis' ─────────────────────────────────────────────────

def _what_changes_thesis(market_state: str, auction_control: str) -> str:
    """Deterministic single-sentence thesis invalidation condition."""
    if market_state in ("TRENDING_UP_STRONG", "TRENDING_UP_MILD"):
        return ("Thesis invalidated if price recaptures VWAP from below after "
                "a confirmed CHOCH to the downside or CVD flips bearish.")
    if market_state in ("TRENDING_DOWN_STRONG", "TRENDING_DOWN_MILD"):
        return ("Thesis invalidated if price recaptures VWAP from above after "
                "a confirmed CHOCH to the upside or CVD flips bullish.")
    if market_state == "DISTRIBUTION_TOPPING":
        return ("Distribution thesis fails if price reclaims the recent swing "
                "high with a strong bullish CHOCH on above-average volume.")
    if market_state == "ACCUMULATION_BOTTOMING":
        return ("Accumulation thesis fails if price breaks the recent swing "
                "low with a strong bearish CHOCH on above-average volume.")
    if market_state == "OPENING_DRIVE":
        return ("Opening drive thesis stalls if price fails to hold the OR "
                "high / low and reverts back inside the 09:30 range within 30 min.")
    return ("Thesis changes on a confirmed structure break in the opposite "
            "direction accompanied by a CVD reversal.")


# ── Narrative template ────────────────────────────────────────────────────────

def _narrative(market_state: str, session_char: str, phase: str,
               auction: str, outlook: dict) -> str:
    """Deterministic single-paragraph market summary (no AI calls)."""
    dominant = ("buyers" if auction == "BUYER"
                else "sellers" if auction == "SELLER"
                else "neither side")
    trend_word = (
        "uptrend" if market_state in ("TRENDING_UP_STRONG", "TRENDING_UP_MILD") else
        "downtrend" if market_state in ("TRENDING_DOWN_STRONG", "TRENDING_DOWN_MILD") else
        "ranging market"
    )
    long_pct   = outlook["long"]
    short_pct  = outlook["short"]
    phase_desc = phase.replace("_", " ").lower()
    char_desc  = session_char.replace("_", " ").lower()

    return (
        f"Market is in a {trend_word} with {dominant} controlling the auction "
        f"({auction}). Session is classified as a {char_desc} during the "
        f"{phase_desc}. Directional evidence: {long_pct}% long / {short_pct}% "
        f"short. Market state: {market_state}."
    )


# ── Neutral (fallback) block ──────────────────────────────────────────────────

def _neutral_block(reason: str = "unavailable") -> dict[str, Any]:
    return {
        "available":           False,
        "reason":              reason,
        "market_state":        "UNKNOWN",
        "session_character":   "UNKNOWN",
        "session_phase":       "UNKNOWN",
        "auction_control":     "CONTESTED",
        "directional_outlook": {"long": 0, "short": 0, "neutral": 100},
        "data_confidence":     0,
        "suitable_playbooks":  ["AVOID_CHOPPY_CONDITIONS"],
        "supporting_evidence": [],
        "missing_evidence":    ["No data available"],
        "what_changes_thesis": "Insufficient data to determine thesis.",
        "narrative":           "Market Intelligence unavailable.",
    }


# ── Public entry point ────────────────────────────────────────────────────────

def compute_left_brain_mi(inst: str, analysis_result: dict) -> dict[str, Any]:
    """Compute the Left Brain Market Intelligence block for one instrument.

    Parameters
    ----------
    inst : str
        Instrument key ("MGC" / "MNQ" / "MES" / "MYM").
    analysis_result : dict
        A read-only snapshot of full_analysis() output.

    Returns
    -------
    dict with the canonical MI schema (all keys always present).
    NEVER raises — FAIL-OPEN.
    """
    try:
        a = analysis_result  # shorter alias

        # Inject session phase into analysis snapshot (read-only side-channel)
        now_utc = datetime.now(timezone.utc)
        phase   = _session_phase(now_utc)
        # Temporarily annotate for helpers that read _lb_session_phase
        a_annotated: dict = {**a, "_lb_session_phase": phase}

        outlook     = _compute_directional_outlook(a_annotated)
        market_state = _compute_market_state(a_annotated, phase, outlook)
        session_char = _compute_session_character(a_annotated, phase, market_state)
        auction      = _compute_auction_control(a_annotated)
        confidence   = _compute_data_confidence(a_annotated)
        playbooks    = _compute_playbooks(market_state, phase)
        supporting, missing = _collect_evidence(a_annotated, market_state, phase)
        changes_thesis = _what_changes_thesis(market_state, auction)
        narr           = _narrative(market_state, session_char, phase, auction, outlook)

        return {
            "available":           True,
            "instrument":          inst,
            "computed_at":         now_utc.isoformat(),
            "market_state":        market_state,
            "session_character":   session_char,
            "session_phase":       phase,
            "auction_control":     auction,
            "directional_outlook": outlook,
            "data_confidence":     confidence,
            "suitable_playbooks":  playbooks,
            "supporting_evidence": supporting,
            "missing_evidence":    missing,
            "what_changes_thesis": changes_thesis,
            "narrative":           narr,
        }

    except Exception as exc:
        logger.debug("Left Brain MI compute_left_brain_mi(%s): %s", inst, exc)
        return _neutral_block(str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2 — Dynamic Thesis Engine (DISPLAY-ONLY)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Upgrades the Left Brain from a single-bar observer to a continuous market
# analyst that tracks HOW the market got here, WHAT changed, and WHAT would
# invalidate the current thesis.
#
# Architecture:
#   compute_left_brain_mi()  →  compute_left_brain_thesis()
#                                      ↓
#                              Market Memory (significant transitions only)
#                                      ↓
#                              Dynamic Thesis (direction, strength, momentum)
#                                      ↓
#                              Playbook Ranking (with per-criterion reasoning)
#
# Execution safety: NEVER modifies gate, edge score, sizing, stops, targets,
# broker communication, or any money-path logic.  100 % display / advisory.
# ─────────────────────────────────────────────────────────────────────────────

# ── Canonical Phase 2 enumerations ───────────────────────────────────────────

THESIS_DIRECTIONS = frozenset({"BULLISH", "BEARISH", "NEUTRAL", "CONFLICTED"})
THESIS_MOMENTUMS  = frozenset({"INCREASING", "STABLE", "WEAKENING", "REVERSING"})

_EVENT_LABEL: dict[str, str] = {
    "MARKET_STATE_CHANGE":  "Market state changed",
    "SESSION_CHAR_CHANGE":  "Session character changed",
    "CONTROL_CHANGE":       "Auction control changed",
    "OUTLOOK_SHIFT":        "Directional outlook shifted",
    "PLAYBOOK_CHANGE":      "Top playbook changed",
    "CONFIDENCE_CHANGE":    "Data confidence changed",
    "THESIS_ESTABLISHED":   "Thesis established",
    "THESIS_STRENGTHENED":  "Thesis strengthened",
    "THESIS_WEAKENED":      "Thesis weakened",
}

# Playbook evaluation criteria.  Each entry: (criterion_key, label, points, predicate).
# predicate takes a single MI dict and returns bool.  FAIL-OPEN (returns False on exception).
_PLAYBOOK_CRITERIA: dict[str, list[tuple]] = {
    "MOMENTUM_CONTINUATION": [
        ("trend_established", "Trend established",
         25, lambda m: m.get("market_state") in (
             "TRENDING_UP_STRONG", "TRENDING_DOWN_STRONG",
             "TRENDING_UP_MILD",   "TRENDING_DOWN_MILD")),
        ("auction_aligned",   "Auction control aligned",
         25, lambda m: m.get("auction_control") in ("BUYER", "SELLER")),
        ("vwap_aligned",      "Price on correct VWAP side",
         25, lambda m: any("VWAP" in e and ("above" in e or "below" in e)
                            for e in (m.get("supporting_evidence") or []))),
        ("cvd_confirms",      "CVD confirms direction",
         15, lambda m: any("Cumulative Volume Delta" in e
                            for e in (m.get("supporting_evidence") or []))),
        ("phase_suitable",    "Session phase suitable for momentum",
         10, lambda m: m.get("session_phase") in (
             "OPENING_DRIVE", "MORNING_SESSION", "AFTERNOON_EXPANSION")),
    ],
    "PULLBACK_TO_VWAP": [
        ("trend_active",    "Active trend in place",
         30, lambda m: m.get("market_state") in (
             "TRENDING_UP_STRONG", "TRENDING_DOWN_STRONG",
             "TRENDING_UP_MILD",   "TRENDING_DOWN_MILD")),
        ("structure_present", "Structure break confirmed",
         25, lambda m: any("BOS/CHOCH" in e
                            for e in (m.get("supporting_evidence") or []))),
        ("vwap_available",  "VWAP available for reference",
         20, lambda m: any("VWAP" in e
                            for e in (m.get("supporting_evidence") or []))),
        ("cvd_supports",    "CVD supports thesis direction",
         15, lambda m: any("Cumulative Volume Delta" in e
                            for e in (m.get("supporting_evidence") or []))),
        ("not_midday",      "Not in midday chop",
         10, lambda m: m.get("session_phase") != "MIDDAY_CHOP"),
    ],
    "RANGE_FADE": [
        ("range_market",     "Range / mean-reverting market",
         35, lambda m: m.get("market_state") == "MEAN_REVERTING_RANGE"),
        ("vwap_reference",   "VWAP available as range reference",
         25, lambda m: any("VWAP" in e
                            for e in (m.get("supporting_evidence") or []))),
        ("auction_contested","Auction contested — no dominant side",
         25, lambda m: m.get("auction_control") == "CONTESTED"),
        ("phase_suitable",   "Suitable session phase",
         15, lambda m: m.get("session_phase") in (
             "MORNING_SESSION", "MIDDAY_CHOP", "AFTERNOON_EXPANSION")),
    ],
    "BREAKOUT": [
        ("high_vol",       "High-volatility rotation present",
         35, lambda m: m.get("market_state") == "HIGH_VOLATILITY_ROTATION"),
        ("cvd_strong",     "Strong CVD in breakout direction",
         30, lambda m: any("aggressive" in e.lower()
                            for e in (m.get("supporting_evidence") or []))),
        ("vwap_cleared",   "Price has cleared VWAP",
         20, lambda m: any("VWAP" in e and ("above" in e or "below" in e)
                            for e in (m.get("supporting_evidence") or []))),
        ("phase_suitable", "Session in expansion phase",
         15, lambda m: m.get("session_phase") in (
             "OPENING_DRIVE", "MORNING_SESSION", "AFTERNOON_EXPANSION")),
    ],
    "REVERSAL_SETUP": [
        ("reversal_state", "Distribution or accumulation in progress",
         35, lambda m: m.get("market_state") in (
             "DISTRIBUTION_TOPPING", "ACCUMULATION_BOTTOMING")),
        ("structure_flip", "Structure break (or flip in progress)",
         30, lambda m: any("BOS/CHOCH" in e
                            for e in (m.get("supporting_evidence") or []))),
        ("cvd_diverging",  "CVD / auction showing divergence",
         20, lambda m: m.get("auction_control") == "CONTESTED"),
        ("phase_suitable", "Suitable session phase",
         15, lambda m: m.get("session_phase") not in (
             "PRE_MARKET", "LATE_SESSION", "CLOSE_APPROACH")),
    ],
    "OPENING_RANGE_BREAK": [
        ("opening_phase",   "Opening drive window active (09:30–10:00 ET)",
         40, lambda m: m.get("session_phase") == "OPENING_DRIVE"),
        ("momentum_state",  "Opening drive market state",
         30, lambda m: m.get("market_state") == "OPENING_DRIVE"),
        ("auction_decisive","Decisive auction control established",
         20, lambda m: m.get("auction_control") in ("BUYER", "SELLER")),
        ("cvd_present",     "CVD confirms initial direction",
         10, lambda m: any("Cumulative Volume Delta" in e
                            for e in (m.get("supporting_evidence") or []))),
    ],
    "LATE_DAY_TREND_FOLLOW": [
        ("late_phase",  "Late session window active",
         40, lambda m: m.get("session_phase") in ("LATE_SESSION", "CLOSE_APPROACH")),
        ("drift_state", "Late-session drift or mild trend",
         30, lambda m: m.get("market_state") in (
             "LATE_SESSION_DRIFT", "TRENDING_UP_MILD", "TRENDING_DOWN_MILD")),
        ("vwap_clear",  "Price clear of VWAP in trend direction",
         20, lambda m: any("VWAP" in e and ("above" in e or "below" in e)
                            for e in (m.get("supporting_evidence") or []))),
        ("not_high_vol","Volatility not extreme",
         10, lambda m: m.get("market_state") != "HIGH_VOLATILITY_ROTATION"),
    ],
    "AVOID_CHOPPY_CONDITIONS": [
        ("choppy_confirmed", "Choppy or unknown market state",
         40, lambda m: m.get("market_state") in ("MEAN_REVERTING_RANGE", "UNKNOWN")),
        ("no_structure",     "No confirmed structure break",
         30, lambda m: not any("BOS/CHOCH" in e
                                for e in (m.get("supporting_evidence") or []))),
        ("low_confidence",   "Low data confidence or unclear outlook",
         30, lambda m: (m.get("data_confidence") or 100) < 50),
    ],
}


# ── Shared timestamp parser ───────────────────────────────────────────────────

def _parse_ts(ts_str: str | None) -> datetime | None:
    """Parse an ISO timestamp string to a UTC-aware datetime, or None."""
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# ── Thesis core classifiers ───────────────────────────────────────────────────

def _thesis_direction(outlook: dict) -> str:
    """Classify thesis direction from directional_outlook probabilities."""
    l = outlook.get("long",  0)
    s = outlook.get("short", 0)
    if l > 55:
        return "BULLISH"
    if s > 55:
        return "BEARISH"
    if abs(l - s) < 15:
        return "NEUTRAL"
    return "CONFLICTED"


def _thesis_strength(
    direction: str,
    outlook: dict,
    confidence: int,
    auction_control: str,
    market_state: str,
) -> int:
    """0–100 thesis strength.  Separate from edge_score and data_confidence.

    Strength = dominant evidence weight × data confidence, with a small bonus
    when a strong trending state and aligned auction are both present.
    """
    if direction == "BULLISH":
        raw = outlook.get("long", 0)
    elif direction == "BEARISH":
        raw = outlook.get("short", 0)
    elif direction == "NEUTRAL":
        raw = outlook.get("neutral", 0)
    else:  # CONFLICTED
        raw = max(outlook.get("long", 0), outlook.get("short", 0))

    base = round(raw * confidence / 100)

    # +8 bonus: strong trending state + aligned auction
    _strong = {"TRENDING_UP_STRONG", "TRENDING_DOWN_STRONG"}
    if market_state in _strong:
        if (direction == "BULLISH" and auction_control == "BUYER") or \
           (direction == "BEARISH" and auction_control == "SELLER"):
            base = min(100, base + 8)

    return min(100, max(0, base))


def _thesis_momentum(
    prev_strength: int | None,
    cur_strength: int,
    direction_changed: bool,
) -> str:
    """INCREASING | STABLE | WEAKENING | REVERSING."""
    if direction_changed:
        return "REVERSING"
    if prev_strength is None:
        return "STABLE"
    delta = cur_strength - prev_strength
    if delta >= 8:
        return "INCREASING"
    if delta <= -8:
        return "WEAKENING"
    return "STABLE"


# ── Structured thesis content ─────────────────────────────────────────────────

def _thesis_narrative_structured(mi: dict, direction: str) -> list[str]:
    """3–6 factual bullet strings explaining WHY the current thesis holds."""
    outlook = mi.get("directional_outlook") or {}
    auction = mi.get("auction_control", "CONTESTED")
    ms      = mi.get("market_state", "UNKNOWN")
    phase   = mi.get("session_phase", "UNKNOWN")
    conf    = mi.get("data_confidence", 0)
    l       = outlook.get("long",  0)
    s       = outlook.get("short", 0)

    reasons: list[str] = []

    # 1. Primary evidence weight
    if direction == "BULLISH":
        reasons.append(
            f"Directional evidence {l}% long vs {s}% short — buy-side leads the weight.")
    elif direction == "BEARISH":
        reasons.append(
            f"Directional evidence {s}% short vs {l}% long — sell-side leads the weight.")
    elif direction == "NEUTRAL":
        reasons.append(
            f"Directional evidence balanced: {l}% long / {s}% short — no dominant side.")
    else:
        reasons.append(
            f"Directional evidence conflicted: {l}% long / {s}% short — mixed signals.")

    # 2. Auction control
    if auction == "BUYER":
        reasons.append("Buyers control the auction (CVD positive, price above VWAP).")
    elif auction == "SELLER":
        reasons.append("Sellers control the auction (CVD negative, price below VWAP).")
    else:
        reasons.append("Auction is contested — neither side has clear control.")

    # 3. Market state
    reasons.append(f"Market state: {ms.replace('_', ' ').title()}.")

    # 4. Session context
    reasons.append(f"Session phase: {phase.replace('_', ' ').title()}.")

    # 5. Confidence note
    if conf >= 70:
        reasons.append(f"Data confidence {conf}% — evidence is well-supported.")
    elif conf >= 40:
        reasons.append(f"Data confidence {conf}% — moderate data availability.")
    else:
        reasons.append(
            f"Data confidence {conf}% — limited data; treat thesis with caution.")

    return reasons


def _thesis_invalidation_structured(mi: dict) -> dict[str, list[str]]:
    """Return structured {"weakens_if": [...], "fails_if": [...]} conditions."""
    ms = mi.get("market_state", "UNKNOWN")

    if ms in ("TRENDING_UP_STRONG", "TRENDING_UP_MILD"):
        return {
            "weakens_if": [
                "Buyers fail to defend VWAP on a retest.",
                "CVD begins to flatten or turn negative.",
                "Lower high forms on the most recent swing.",
            ],
            "fails_if": [
                "Price breaks and accepts below VWAP with a bearish CHOCH.",
                "Sellers take auction control (CVD negative + price below VWAP).",
            ],
        }
    if ms in ("TRENDING_DOWN_STRONG", "TRENDING_DOWN_MILD"):
        return {
            "weakens_if": [
                "Sellers fail to defend VWAP on a retest.",
                "CVD begins to flatten or turn positive.",
                "Higher low forms on the most recent swing.",
            ],
            "fails_if": [
                "Price breaks and accepts above VWAP with a bullish CHOCH.",
                "Buyers take auction control (CVD positive + price above VWAP).",
            ],
        }
    if ms == "DISTRIBUTION_TOPPING":
        return {
            "weakens_if": [
                "Selling pressure stalls and buyers step in at support.",
                "CVD stabilises without expanding further negative.",
            ],
            "fails_if": [
                "Price reclaims swing high with a strong bullish CHOCH on elevated volume.",
                "Buyers regain auction control decisively.",
            ],
        }
    if ms == "ACCUMULATION_BOTTOMING":
        return {
            "weakens_if": [
                "Buying pressure stalls and sellers re-emerge at resistance.",
                "CVD fails to improve further from current levels.",
            ],
            "fails_if": [
                "Price breaks swing low with a strong bearish CHOCH on elevated volume.",
                "Sellers retake auction control decisively.",
            ],
        }
    if ms == "OPENING_DRIVE":
        return {
            "weakens_if": [
                "Price stalls near the prior day high/low without range expansion.",
                "Volume drops below average on the drive bar.",
            ],
            "fails_if": [
                "Price reverts inside the opening range within 30 minutes of the open.",
                "Opposite auction control established before 10:00 ET.",
            ],
        }
    if ms == "MEAN_REVERTING_RANGE":
        return {
            "weakens_if": [
                "Range begins to expand beyond 1.5× ATR.",
                "One side dominates CVD for multiple consecutive bars.",
            ],
            "fails_if": [
                "Price breaks range boundary with confirmed BOS and sustained auction control.",
            ],
        }
    # Default / HIGH_VOLATILITY_ROTATION / LATE_SESSION_DRIFT / UNKNOWN
    return {
        "weakens_if": [
            "Data confidence drops below 40%.",
            "Auction control shifts to the opposite side.",
        ],
        "fails_if": [
            "Confirmed structure break in the opposite direction accompanied by CVD reversal.",
        ],
    }


def _compute_playbook_reasoning(mi: dict) -> list[dict]:
    """Return per-playbook reasoning dicts for the top-3 suitable playbooks.

    Each entry: {name, fit_score (0-100), reasons: [str], missing: [str]}.

    Scoring rules:
    - ALL suitable playbooks are scored (not just the first 3).
    - Sorted by fit_score descending; ties broken by playbook name ascending
      for deterministic ordering across repeated calls with identical inputs.
    - The top-3 highest-scoring playbooks are returned.
    """
    playbooks = mi.get("suitable_playbooks") or []
    scored: list[dict] = []

    for pb_name in playbooks:                # score ALL, then sort
        criteria = _PLAYBOOK_CRITERIA.get(pb_name)
        if not criteria:
            scored.append({"name": pb_name, "fit_score": 50,
                           "reasons": [], "missing": []})
            continue

        max_pts = sum(pts for _, _, pts, _ in criteria)
        earned  = 0
        reasons: list[str] = []
        missing: list[str] = []

        for _key, label, pts, pred in criteria:
            try:
                met = bool(pred(mi))
            except Exception:
                met = False
            if met:
                earned += pts
                reasons.append(label)
            else:
                missing.append(label)

        fit_score = round(earned * 100 / max_pts) if max_pts > 0 else 0
        scored.append({"name": pb_name, "fit_score": fit_score,
                       "reasons": reasons, "missing": missing})

    # Highest fit_score first; equal scores ordered by name (deterministic)
    scored.sort(key=lambda x: (-x["fit_score"], x["name"]))
    return scored[:3]


# ── Memory event detection ────────────────────────────────────────────────────

def _detect_significant_changes(
    mi:             dict,
    prev_mi:        dict | None,
    direction:      str,
    prev_direction: str | None,
    strength:       int,
    prev_strength:  int | None,
) -> list[dict]:
    """Detect significant MI transitions and return new memory event records.

    Returns an empty list when prev_mi is None (first bar — no comparison).
    Each event record: {ts, event_type, label, from_value, to_value,
                        reason, evidence, confidence_at_time}.
    """
    if prev_mi is None:
        return []

    ts_str = mi.get("computed_at", datetime.now(timezone.utc).isoformat())
    conf   = mi.get("data_confidence", 0)
    events: list[dict] = []

    def _evt(event_type: str, from_val: Any, to_val: Any,
             reason: str, evidence: list | None = None) -> dict:
        return {
            "ts":                 ts_str,
            "event_type":         event_type,
            "label":              _EVENT_LABEL.get(event_type, event_type),
            "from_value":         str(from_val),
            "to_value":           str(to_val),
            "reason":             reason,
            "evidence":           evidence or [],
            "confidence_at_time": conf,
        }

    # 1. Market state change
    prev_ms = prev_mi.get("market_state")
    cur_ms  = mi.get("market_state")
    if prev_ms and cur_ms and prev_ms != cur_ms:
        events.append(_evt(
            "MARKET_STATE_CHANGE", prev_ms, cur_ms,
            f"Market transitioned from {prev_ms.replace('_', ' ')} "
            f"to {cur_ms.replace('_', ' ')}",
            mi.get("supporting_evidence") or [],
        ))

    # 2. Session character change
    prev_sc = prev_mi.get("session_character")
    cur_sc  = mi.get("session_character")
    if prev_sc and cur_sc and prev_sc != cur_sc:
        events.append(_evt(
            "SESSION_CHAR_CHANGE", prev_sc, cur_sc,
            f"Session character shifted: {prev_sc.replace('_', ' ')} → "
            f"{cur_sc.replace('_', ' ')}",
        ))

    # 3. Auction control change
    prev_ac = prev_mi.get("auction_control")
    cur_ac  = mi.get("auction_control")
    if prev_ac and cur_ac and prev_ac != cur_ac:
        events.append(_evt(
            "CONTROL_CHANGE", prev_ac, cur_ac,
            f"Auction control shifted from {prev_ac} to {cur_ac}",
        ))

    # 4. Directional outlook significant shift (dominant bucket ≥ 15 pts)
    prev_out = prev_mi.get("directional_outlook") or {}
    cur_out  = mi.get("directional_outlook") or {}
    prev_dom = max(prev_out.get("long", 0), prev_out.get("short", 0))
    cur_dom  = max(cur_out.get("long",  0), cur_out.get("short",  0))
    if abs(cur_dom - prev_dom) >= 15:
        delta = cur_dom - prev_dom
        # Build guaranteed non-empty evidence list (never copies stale data).
        # Priority: 1) current MI supporting evidence, 2) derived from the change.
        _os_evid: list[str] = [
            e for e in (mi.get("supporting_evidence") or []) if e and isinstance(e, str)
        ]
        if not _os_evid:
            # Derive from the per-direction probability changes
            _dir_changes = [
                ("Bullish",  prev_out.get("long",    0), cur_out.get("long",    0)),
                ("Bearish",  prev_out.get("short",   0), cur_out.get("short",   0)),
                ("Neutral",  prev_out.get("neutral", 0), cur_out.get("neutral", 0)),
            ]
            for _dn, _prev_pct, _cur_pct in _dir_changes:
                if abs(_cur_pct - _prev_pct) >= 10:
                    _os_evid.append(
                        f"{_dn} directional weight changed from {_prev_pct}% to {_cur_pct}%."
                    )
            if not _os_evid:          # ultimate fallback
                _os_evid.append(
                    f"Dominant directional weight shifted from {prev_dom}% to {cur_dom}%."
                )
        events.append(_evt(
            "OUTLOOK_SHIFT", f"{prev_dom}%", f"{cur_dom}%",
            f"Dominant directional weight shifted by {delta:+d} pts",
            _os_evid,
        ))

    # 5. Top playbook change
    prev_pb = ((prev_mi.get("suitable_playbooks") or [None])[0])
    cur_pb  = ((mi.get("suitable_playbooks")      or [None])[0])
    if prev_pb and cur_pb and prev_pb != cur_pb:
        events.append(_evt(
            "PLAYBOOK_CHANGE", prev_pb, cur_pb,
            f"Primary playbook: {prev_pb.replace('_', ' ')} → "
            f"{cur_pb.replace('_', ' ')}",
        ))

    # 6. Data confidence material change (≥ 15 pts)
    prev_conf = prev_mi.get("data_confidence", 0)
    cur_conf  = mi.get("data_confidence", 0)
    if abs(cur_conf - prev_conf) >= 15:
        events.append(_evt(
            "CONFIDENCE_CHANGE", prev_conf, cur_conf,
            f"Data confidence changed by {cur_conf - prev_conf:+d} pts",
        ))

    # 7. Thesis direction change → new thesis
    if prev_direction is not None and direction != prev_direction:
        events.append(_evt(
            "THESIS_ESTABLISHED", prev_direction, direction,
            f"New {direction} thesis established (was {prev_direction})",
        ))
    elif prev_direction is None:
        events.append(_evt(
            "THESIS_ESTABLISHED", "NONE", direction,
            f"Initial {direction} thesis established",
        ))

    # 8. Thesis strength change (≥ 10 pts) — only while direction is stable
    if prev_strength is not None and prev_direction == direction:
        delta = strength - prev_strength
        if delta >= 10:
            events.append(_evt(
                "THESIS_STRENGTHENED", prev_strength, strength,
                f"Thesis strength increased {prev_strength} → {strength} (+{delta})",
            ))
        elif delta <= -10:
            events.append(_evt(
                "THESIS_WEAKENED", prev_strength, strength,
                f"Thesis strength decreased {prev_strength} → {strength} ({delta})",
            ))

    return events


# ── Stability + timeline ──────────────────────────────────────────────────────

def _thesis_stability(memory_events: list, established_at: str | None) -> dict:
    """Compute stability metrics from the memory event stream."""
    transitions = [e for e in memory_events
                   if e.get("event_type") == "THESIS_ESTABLISHED"]
    n = len(transitions)

    # Time in current thesis
    time_in_min: float | None = None
    if established_at:
        est = _parse_ts(established_at)
        if est:
            time_in_min = round(
                (datetime.now(timezone.utc) - est).total_seconds() / 60, 1)

    # Average thesis duration
    avg_duration_min: float | None = None
    if n >= 2:
        t0 = _parse_ts(transitions[0].get("ts"))
        tn = _parse_ts(transitions[-1].get("ts"))
        if t0 and tn:
            total_sec = (tn - t0).total_seconds()
            avg_duration_min = round(total_sec / 60 / n, 1)

    # Rapid flip warning: ≥ 3 THESIS_ESTABLISHED events in last 30 min
    rapid_flip = False
    now_utc = datetime.now(timezone.utc)
    try:
        recent = [
            e for e in transitions
            if (_pts := _parse_ts(e.get("ts"))) is not None
            and (now_utc - _pts).total_seconds() < 1800
        ]
        rapid_flip = len(recent) >= 3
    except Exception:
        pass

    if rapid_flip:
        note = "Market currently unstable. Conviction reduced."
    elif n == 0:
        note = "Thesis stable — no transitions recorded."
    elif n == 1:
        note = "One thesis transition recorded."
    else:
        note = f"Thesis has transitioned {n} time(s)."

    return {
        "time_in_current_thesis_min":  time_in_min,
        "number_of_transitions":        n,
        "average_thesis_duration_min":  avg_duration_min,
        "rapid_flip_warning":           rapid_flip,
        "stability_note":               note,
    }


def _thesis_timeline(memory_events: list, max_events: int = 20) -> list[dict]:
    """Newest-first list of compact timeline events (max `max_events`)."""
    def _sort_key(e: dict) -> float:
        ts = _parse_ts(e.get("ts"))
        return ts.timestamp() if ts else 0.0

    items = [
        {
            "ts":         e.get("ts"),
            "label":      e.get("label", e.get("event_type", "")),
            "event_type": e.get("event_type"),
            "from_value": e.get("from_value"),
            "to_value":   e.get("to_value"),
        }
        for e in memory_events
    ]
    items.sort(key=_sort_key, reverse=True)
    return items[:max_events]


# ── Neutral thesis fallback ───────────────────────────────────────────────────

def _neutral_thesis(inst: str) -> dict[str, Any]:
    return {
        "available":       False,
        "instrument":      inst,
        "direction":       "NEUTRAL",
        "strength":        0,
        "momentum":        "STABLE",
        "established_at":  None,
        "last_updated_at": None,
        "narrative":       ["Thesis unavailable — insufficient data."],
        "invalidation":    {"weakens_if": [], "fails_if": []},
        "playbooks":       [],
        "stability": {
            "time_in_current_thesis_min":  None,
            "number_of_transitions":        0,
            "average_thesis_duration_min":  None,
            "rapid_flip_warning":           False,
            "stability_note":               "No thesis data.",
        },
        "timeline": [],
    }


# ── Public Phase 2 entry point ────────────────────────────────────────────────

def compute_left_brain_thesis(
    inst:          str,
    mi:            dict,
    prev_mi:       dict | None,
    prev_thesis:   dict | None,
    memory_events: list,
) -> dict[str, Any]:
    """Compute the Left Brain Dynamic Thesis for one instrument.

    Parameters
    ----------
    inst          : Instrument key ("MGC" / "MNQ" / "MES" / "MYM").
    mi            : Current MI block from compute_left_brain_mi().
    prev_mi       : Previous MI block (None on first call).
    prev_thesis   : Previous thesis dict (None on first call).
    memory_events : Existing memory events oldest-first (from _LB_MARKET_MEMORY_BY_INST).

    Returns
    -------
    dict with:
        "thesis"     : full thesis dict (all keys always present)
        "new_events" : list of new memory event dicts for the caller to append

    NEVER raises — FAIL-OPEN: returns neutral thesis + empty events on error.
    """
    try:
        if not (mi and mi.get("available")):
            return {"thesis": _neutral_thesis(inst), "new_events": []}

        outlook = mi.get("directional_outlook") or {}
        auction = mi.get("auction_control", "CONTESTED")
        ms      = mi.get("market_state", "UNKNOWN")
        conf    = mi.get("data_confidence", 0)

        direction = _thesis_direction(outlook)
        strength  = _thesis_strength(direction, outlook, conf, auction, ms)

        prev_dir = (prev_thesis or {}).get("direction")
        prev_str = (prev_thesis or {}).get("strength")
        dir_changed = (prev_dir is not None and direction != prev_dir)
        momentum    = _thesis_momentum(prev_str, strength, dir_changed)

        # Detect significant changes (returns [] when prev_mi is None)
        new_events = _detect_significant_changes(
            mi, prev_mi, direction, prev_dir, strength, prev_str)

        # Preserve established_at unless direction changed
        if dir_changed or prev_thesis is None:
            established_at = mi.get("computed_at")
        else:
            established_at = (
                (prev_thesis or {}).get("established_at") or mi.get("computed_at"))

        all_events = list(memory_events) + new_events

        thesis: dict[str, Any] = {
            "available":       True,
            "instrument":      inst,
            "direction":       direction,
            "strength":        strength,
            "momentum":        momentum,
            "established_at":  established_at,
            "last_updated_at": mi.get("computed_at"),
            "narrative":       _thesis_narrative_structured(mi, direction),
            "invalidation":    _thesis_invalidation_structured(mi),
            "playbooks":       _compute_playbook_reasoning(mi),
            "stability":       _thesis_stability(all_events, established_at),
            "timeline":        _thesis_timeline(all_events, max_events=20),
        }

        return {"thesis": thesis, "new_events": new_events}

    except Exception as exc:
        logger.debug("compute_left_brain_thesis(%s): %s", inst, exc)
        return {"thesis": _neutral_thesis(inst), "new_events": []}
