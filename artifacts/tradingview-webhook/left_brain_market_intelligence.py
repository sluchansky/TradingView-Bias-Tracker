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
