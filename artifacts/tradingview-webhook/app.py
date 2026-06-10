import os
import logging
from collections import deque
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ALERT_HISTORY = deque(maxlen=100)
CURRENT_PRICE = None

ALERT_TYPES = {
    "MGC NEW SUPPLY ZONE":       {"side": "bearish", "score": 1},
    "MGC SUPPLY ZONE CONFIRMED": {"side": "bearish", "score": 2},
    "MGC NEW DEMAND ZONE":       {"side": "bullish", "score": 1},
    "MGC DEMAND ZONE CONFIRMED": {"side": "bullish", "score": 2},
    "CHOCH SUPPLY":              {"side": "bearish", "score": 3},
    "BOS SUPPLY":                {"side": "bearish", "score": 2},
    "CHOCH DEMAND":              {"side": "bullish", "score": 3},
    "BOS DEMAND":                {"side": "bullish", "score": 2},
}

SUPPLY_TYPES = {k for k, v in ALERT_TYPES.items() if v["side"] == "bearish"}
DEMAND_TYPES = {k for k, v in ALERT_TYPES.items() if v["side"] == "bullish"}

BIAS_THRESHOLD = 3
NEAR_PCT       = 0.005   # 0.5%  — Testing zone
EXTENDED_PCT   = 0.010   # 1.0%  — Approaching zone
WATCH_PCT      = 0.0075  # 0.75% — Watch zone (v10)

DEFAULT_ACCOUNT_SIZE = 10_000   # $10,000 — override per-alert via "account_size" field
DEFAULT_RISK_PCT     = 0.01     # 1% risk per trade — override via "risk_pct" field
MGC_POINT_VALUE      = 10       # $10 per point per MGC contract (Micro Gold = 10 oz)

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
TIME_WINDOWS        = {"15m": 15, "60m": 60, "120m": 120}


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def now_utc():
    return datetime.now(timezone.utc)

def alerts_in_window(minutes):
    cutoff = now_utc() - timedelta(minutes=minutes)
    out = []
    for alert in ALERT_HISTORY:
        try:
            if datetime.fromisoformat(alert["timestamp"]) >= cutoff:
                out.append(alert)
        except (KeyError, ValueError):
            pass
    return out

def window_summary(minutes):
    alerts = alerts_in_window(minutes)
    counts = {k: 0 for k in ALERT_TYPES}
    for a in alerts:
        t = a.get("alert_type", "")
        if t in counts:
            counts[t] += 1
    return counts, len(alerts)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_alerts(alerts):
    bullish = bearish = 0
    counts = {k: 0 for k in ALERT_TYPES}
    for alert in alerts:
        t = alert.get("alert_type", "")
        if t in ALERT_TYPES:
            counts[t] += 1
            if ALERT_TYPES[t]["side"] == "bullish":
                bullish += ALERT_TYPES[t]["score"]
            else:
                bearish += ALERT_TYPES[t]["score"]
    return bullish, bearish, counts

def calculate_scores():
    return score_alerts(ALERT_HISTORY)

def calculate_bias(bullish, bearish):
    gap = abs(bullish - bearish)
    strength = min(gap + 1, 10)
    if bearish - bullish >= BIAS_THRESHOLD:
        return "Bearish", strength
    elif bullish - bearish >= BIAS_THRESHOLD:
        return "Bullish", strength
    return "Choppy", strength

def calculate_confidence(bullish, bearish):
    total = bullish + bearish
    if total == 0:
        return 0
    return min(95, round(max(bullish, bearish) / total * 100))

def calculate_trade_quality(bias, confidence, bullish, bearish):
    if bullish + bearish == 0:
        return "D"
    if bias == "Choppy":
        return "C"
    if confidence >= 80:
        return "A+"
    if confidence >= 65:
        return "A"
    if confidence >= 55:
        return "B"
    return "C"

QUALITY_LABELS = {"A+": "Strong trend", "A": "Trend", "B": "Tradable", "C": "Choppy", "D": "Avoid"}

def calculate_edge_score(bias, confidence, strength):
    if bias == "Choppy":
        return round(confidence * 0.5 + (strength / 10) * 10)
    return min(100, round(confidence * 0.7 + (strength / 10) * 30))


# ---------------------------------------------------------------------------
# Price context
# ---------------------------------------------------------------------------

def get_price_context():
    last_price_by_type = {}
    all_supply_prices  = []
    all_demand_prices  = []
    for alert in ALERT_HISTORY:
        t = alert.get("alert_type", "")
        p = alert.get("price")
        if t not in ALERT_TYPES or p is None:
            continue
        try:
            price = float(p)
        except (ValueError, TypeError):
            continue
        last_price_by_type[t] = price
        (all_supply_prices if t in SUPPLY_TYPES else all_demand_prices).append(price)
    return last_price_by_type, all_supply_prices, all_demand_prices

def get_nearest_levels(current_price, all_supply_prices, all_demand_prices):
    nearest_supply = nearest_demand = None
    if all_supply_prices:
        if current_price is not None:
            above = [p for p in all_supply_prices if p > current_price]
            nearest_supply = min(above) if above else max(all_supply_prices)
        else:
            nearest_supply = max(all_supply_prices)
    if all_demand_prices:
        if current_price is not None:
            below = [p for p in all_demand_prices if p < current_price]
            nearest_demand = max(below) if below else min(all_demand_prices)
        else:
            nearest_demand = min(all_demand_prices)
    return nearest_supply, nearest_demand

def get_market_structure(current_price, last_price_by_type):
    if current_price is None:
        return "Undefined", "No price data received yet."
    choch_sup = last_price_by_type.get("CHOCH SUPPLY")
    choch_dem = last_price_by_type.get("CHOCH DEMAND")
    bos_sup   = last_price_by_type.get("BOS SUPPLY")
    bos_dem   = last_price_by_type.get("BOS DEMAND")
    parts = []
    if choch_sup and choch_dem:
        if current_price < choch_dem:
            structure = "Bearish Structure"
            parts.append(f"Price below CHOCH Supply ({choch_sup:.2f}) and CHOCH Demand ({choch_dem:.2f})")
        elif current_price > choch_sup:
            structure = "Bullish Structure"
            parts.append(f"Price above CHOCH Supply ({choch_sup:.2f}) and CHOCH Demand ({choch_dem:.2f})")
        else:
            structure = "Range Structure"
            parts.append(f"Price between CHOCH Supply ({choch_sup:.2f}) and CHOCH Demand ({choch_dem:.2f})")
    elif choch_sup:
        if current_price <= choch_sup:
            structure = "Bearish Structure"
            parts.append(f"Price at/below CHOCH Supply ({choch_sup:.2f})")
        else:
            structure = "Bullish Breakout"
            parts.append(f"Price above CHOCH Supply ({choch_sup:.2f})")
    elif choch_dem:
        if current_price >= choch_dem:
            structure = "Bullish Structure"
            parts.append(f"Price at/above CHOCH Demand ({choch_dem:.2f})")
        else:
            structure = "Bearish Breakdown"
            parts.append(f"Price below CHOCH Demand ({choch_dem:.2f})")
    else:
        structure = "Undefined"
        parts.append("No CHOCH levels tracked yet")
    if bos_sup and current_price < bos_sup:
        parts.append(f"below BOS Supply ({bos_sup:.2f})")
    if bos_dem and current_price > bos_dem:
        parts.append(f"above BOS Demand ({bos_dem:.2f})")
    return structure, ". ".join(parts) + "."

def get_risk_zone(bias, current_price, nearest_supply, nearest_demand):
    if current_price is None:
        return "Unknown", "No price data available.", False
    def pct(a, b):
        return abs(a - b) / b if b else 0
    if bias == "Bearish":
        if nearest_supply is not None:
            dist = pct(nearest_supply, current_price)
            if dist <= NEAR_PCT:
                return "Testing Supply", f"Price testing supply at {nearest_supply:.2f} ({dist:.2%} away). Favor shorts.", False
            elif dist >= EXTENDED_PCT:
                return "Overextended", f"Price too extended from supply ({nearest_supply:.2f}, {dist:.2%} away). Wait for retracement.", True
            else:
                return "Approaching Supply", f"Price approaching supply at {nearest_supply:.2f} ({dist:.2%} away). Watch for rejection.", False
        return "No Supply Level", "No supply level tracked yet. Use caution.", False
    elif bias == "Bullish":
        if nearest_demand is not None:
            dist = pct(current_price, nearest_demand)
            if dist <= NEAR_PCT:
                return "Testing Demand", f"Price testing demand at {nearest_demand:.2f} ({dist:.2%} away). Favor longs.", False
            elif dist >= EXTENDED_PCT:
                return "Overextended", f"Price too extended from demand ({nearest_demand:.2f}, {dist:.2%} away). Wait for retracement.", True
            else:
                return "Approaching Demand", f"Price pulling back toward demand at {nearest_demand:.2f} ({dist:.2%} away). Watch for hold.", False
        return "No Demand Level", "No demand level tracked yet. Use caution.", False
    else:
        msgs = []
        if nearest_supply:
            msgs.append(f"Supply {nearest_supply:.2f} ({pct(nearest_supply, current_price):.2%} away)")
        if nearest_demand:
            msgs.append(f"Demand {nearest_demand:.2f} ({pct(current_price, nearest_demand):.2%} away)")
        detail = "Choppy. " + " · ".join(msgs) + ". No directional edge." if msgs else "Choppy. No levels tracked."
        return "Choppy", detail, False


# ---------------------------------------------------------------------------
# Decision Engine v6
# ---------------------------------------------------------------------------

def classify_structure(structure_label):
    """Map raw structure label → decision engine class."""
    return {
        "Bearish Structure": "Bearish Trend",
        "Bullish Structure": "Bullish Trend",
        "Range Structure":   "Range",
        "Bearish Breakdown": "Reversal",
        "Bullish Breakout":  "Reversal",
        "Undefined":         "Undefined",
    }.get(structure_label, "Undefined")


def decision_engine(structure_label, risk_label, overextended,
                    bias, bullish, bearish, confidence):
    """
    Priority: Market Structure → Risk Zone → Alert Score → Confidence
    Returns (recommendation, final_verdict, structure_class, reasoning_chain)
    """
    structure_class = classify_structure(structure_label)
    chain = [structure_class]

    # ── Gate: no data ──
    if structure_class == "Undefined" or (bullish == 0 and bearish == 0):
        if bullish == 0 and bearish == 0:
            chain += ["No Alerts", "No Edge", "WAIT"]
        else:
            chain += ["No Structure Defined", "WAIT"]
        return "WAIT", "WAIT", structure_class, chain

    # ── Gate: overextended price ──
    if overextended:
        chain += [f"Overextended — {risk_label}", "Entry Blocked", "WAIT"]
        return "WAIT", "WAIT", structure_class, chain

    # ── Risk Zone ──
    chain.append(risk_label)

    # ── Alert Score ──
    gap = abs(bullish - bearish)
    if bias == "Bearish" and gap >= BIAS_THRESHOLD:
        score_desc  = f"Bearish Score Dominant ({bearish} vs {bullish})"
        score_side  = "bearish"
    elif bias == "Bullish" and gap >= BIAS_THRESHOLD:
        score_desc  = f"Bullish Score Dominant ({bullish} vs {bearish})"
        score_side  = "bullish"
    else:
        score_desc  = f"Mixed Alerts (bull {bullish} / bear {bearish})"
        score_side  = "mixed"

    chain.append(score_desc)

    # ── Structure cap: Range or Reversal ──
    if structure_class in ("Range", "Reversal"):
        cap_note = f"Structure Cap ({structure_class} → max WATCH)"
        chain.append(cap_note)
        if score_side == "mixed" or confidence < 70:
            chain += ["No Edge", "WAIT"]
            return "WAIT", "WAIT", structure_class, chain
        chain.append("WATCH")
        return "WATCH", "WAIT", structure_class, chain

    # ── Confidence tier (Bearish Trend / Bullish Trend only) ──
    if score_side == "mixed":
        chain += [f"Confidence {confidence}% — Mixed Score", "WAIT"]
        return "WAIT", "WAIT", structure_class, chain

    if confidence >= 90:
        rec        = "HIGH CONVICTION TRADE"
        conf_step  = f"Confidence {confidence}% — High Conviction"
    elif confidence >= 80:
        rec        = "TRADE"
        conf_step  = f"Confidence {confidence}% — Trade"
    elif confidence >= 70:
        rec        = "WATCH"
        conf_step  = f"Confidence {confidence}% — Watch"
    else:
        rec        = "WAIT"
        conf_step  = f"Confidence {confidence}% — Low"

    chain.append(conf_step)

    # ── Final Verdict ──
    if structure_class == "Bearish Trend":
        if rec == "HIGH CONVICTION TRADE":
            verdict = "STRONG SHORT"
        elif rec in ("TRADE", "WATCH"):
            verdict = "SHORT BIAS"
        else:
            verdict = "WAIT"
    elif structure_class == "Bullish Trend":
        if rec == "HIGH CONVICTION TRADE":
            verdict = "STRONG LONG"
        elif rec in ("TRADE", "WATCH"):
            verdict = "LONG BIAS"
        else:
            verdict = "WAIT"
    else:
        verdict = "WAIT"

    chain.append(verdict)
    return rec, verdict, structure_class, chain


# ---------------------------------------------------------------------------
# Setup Detection v7
# ---------------------------------------------------------------------------

def get_market_direction(structure_label):
    """Map raw structure label → user-facing market direction."""
    if structure_label in ("Bearish Structure", "Bearish Breakdown"):
        return "Bearish"
    if structure_label in ("Bullish Structure", "Bullish Breakout"):
        return "Bullish"
    if structure_label == "Range Structure":
        return "Range"
    return "Neutral"


def get_trade_opportunity(market_direction, structure_label, risk_label,
                          overextended, bullish, bearish, nearest_supply, nearest_demand,
                          last_price_by_type):
    """
    Determine the current trade opportunity (v9).
    Returns (opportunity, reason, entry_trigger, invalidation).
    opportunity values: "Short Setup" | "Long Setup" | "Breakout Setup" |
                        "Watch Supply" | "Watch Demand" | "None"

    Priority order:
      1. Overextended / Neutral            → None
      2. Bullish Breakout / Breakdown      → Breakout Setup
      3. Testing Supply + Bearish structure → Short Setup
      4. Testing Demand + Bullish structure → Long Setup
      5. Within 1% of supply               → Watch Supply
      6. Within 1% of demand               → Watch Demand
      7. Everything else                   → None (mid-range)
    """
    sup_str   = f"${nearest_supply:.2f}" if nearest_supply is not None else "—"
    dem_str   = f"${nearest_demand:.2f}" if nearest_demand is not None else "—"
    choch_sup = last_price_by_type.get("CHOCH SUPPLY")
    choch_dem = last_price_by_type.get("CHOCH DEMAND")

    def none_opp(reason):
        return ("None", reason, None, None)

    # ── 1. Gate: overextended / no structure ──
    if overextended:
        return none_opp("Price overextended from level. Wait for retracement.")
    if market_direction == "Neutral":
        return none_opp("No market structure defined yet.")

    # ── 2. Breakout: price burst through a CHOCH level ──
    if structure_label == "Bullish Breakout":
        cs = f"${choch_sup:.2f}" if choch_sup else "—"
        return ("Breakout Setup",
                f"Price broke above CHOCH Supply ({cs}). Bullish breakout in progress.",
                f"5m bullish candle closing above {cs}",
                f"Close back below {cs}")
    if structure_label == "Bearish Breakdown":
        cd = f"${choch_dem:.2f}" if choch_dem else "—"
        return ("Breakout Setup",
                f"Price broke below CHOCH Demand ({cd}). Bearish breakdown in progress.",
                f"5m bearish candle closing below {cd}",
                f"Close back above {cd}")

    # ── 3. Short Setup: price TESTING supply + bearish structure confirmed ──
    if risk_label == "Testing Supply" and market_direction == "Bearish":
        return ("Short Setup",
                f"Bearish structure confirmed — price testing supply ({sup_str}). Short opportunity.",
                "5m bearish confirmation candle",
                f"Close above supply zone ({sup_str})")

    # ── 4. Long Setup: price TESTING demand + bullish structure confirmed ──
    if risk_label == "Testing Demand" and market_direction == "Bullish":
        return ("Long Setup",
                f"Bullish structure confirmed — price testing demand ({dem_str}). Long opportunity.",
                "5m bullish confirmation candle",
                f"Close below demand zone ({dem_str})")

    # ── 5. Watch Supply: price within 1% of supply (any structure) ──
    if risk_label in ("Testing Supply", "Approaching Supply"):
        return ("Watch Supply",
                f"Price within 1% of supply ({sup_str}). Monitor for rejection or breakout.",
                None, None)

    # ── 6. Watch Demand: price within 1% of demand (any structure) ──
    if risk_label in ("Testing Demand", "Approaching Demand"):
        return ("Watch Demand",
                f"Price within 1% of demand ({dem_str}). Monitor for bounce or breakdown.",
                None, None)

    # ── 7. None: mid-range ──
    return none_opp("Price mid-range — not interacting with supply or demand.")


# ---------------------------------------------------------------------------
# Trade Plan Generator v7
# ---------------------------------------------------------------------------

def generate_trade_plan(trade_opportunity, structure_label, risk_label,
                        current_price, nearest_supply, nearest_demand,
                        last_price_by_type):
    """
    Generate specific entry / stop / target levels for actionable opportunities.
    Returns dict with trade_plan=True|False and level fields.
    Only generates a plan when trade_opportunity != "None".
    """
    def no_plan(reason):
        return {"trade_plan": False, "reason": reason,
                "entry_zone": None, "stop_loss": None,
                "target1": None, "target2": None, "rr": None, "direction": None}

    if trade_opportunity in ("None", "Watch Supply", "Watch Demand"):
        return no_plan(
            "Monitoring only — no trade plan until setup confirms."
            if trade_opportunity != "None" else "No trade opportunity detected."
        )

    if current_price is None:
        return no_plan("No price data available.")

    ENTRY_BUF = 0.001   # 0.1% — entry zone half-width

    def buf(level):
        return max(1, round(level * ENTRY_BUF))

    def calc_rr(reward, risk):
        if risk <= 0:
            return "—"
        return f"{round(reward / risk, 1)}:1"

    def fmt(v):
        return f"{v:.1f}"

    def long_plan(anchor):
        """Build a long trade plan anchored at a demand/breakout level."""
        b    = buf(anchor)
        lo   = anchor
        hi   = anchor + b
        stop = anchor - b - 1
        mid  = (lo + hi) / 2
        risk = mid - stop
        if nearest_supply and nearest_supply > hi:
            t2 = nearest_supply
            t1 = round((hi + t2) / 2, 1)
        else:
            t2 = round(mid + risk * 3.0, 1)
            t1 = round(mid + risk * 1.5, 1)
        return {
            "trade_plan": True, "direction": "Long",
            "entry_zone": f"{fmt(lo)}–{fmt(hi)}",
            "stop_loss":  fmt(stop),
            "target1":    fmt(t1), "target2": fmt(t2),
            "rr":         f"T1 {calc_rr(t1-mid, risk)} / T2 {calc_rr(t2-mid, risk)}",
        }

    def short_plan(anchor):
        """Build a short trade plan anchored at a supply/breakdown level."""
        b    = buf(anchor)
        hi   = anchor
        lo   = anchor - b
        stop = anchor + b
        mid  = (lo + hi) / 2
        risk = stop - mid
        if nearest_demand and nearest_demand < lo:
            t2 = nearest_demand
            t1 = round((lo + t2) / 2, 1)
        else:
            t2 = round(mid - risk * 3.0, 1)
            t1 = round(mid - risk * 1.5, 1)
        return {
            "trade_plan": True, "direction": "Short",
            "entry_zone": f"{fmt(lo)}–{fmt(hi)}",
            "stop_loss":  fmt(stop),
            "target1":    fmt(t1), "target2": fmt(t2),
            "rr":         f"T1 {calc_rr(mid-t1, risk)} / T2 {calc_rr(mid-t2, risk)}",
        }

    # ── Long Setup ──
    if trade_opportunity == "Long Setup":
        anchor = nearest_demand if nearest_demand is not None else float(current_price)
        return long_plan(anchor)

    # ── Short Setup ──
    if trade_opportunity == "Short Setup":
        anchor = nearest_supply if nearest_supply is not None else float(current_price)
        return short_plan(anchor)

    # ── Breakout Setup ──
    if trade_opportunity == "Breakout Setup":
        if structure_label == "Bullish Breakout":
            choch_sup = last_price_by_type.get("CHOCH SUPPLY", float(current_price))
            b    = buf(choch_sup)
            lo   = choch_sup + 1
            hi   = choch_sup + b
            stop = choch_sup - b
            mid  = (lo + hi) / 2
            risk = mid - stop
            t2   = round(mid + risk * 3.0, 1) if not (nearest_supply and nearest_supply > hi) else nearest_supply
            t1   = round((hi + t2) / 2, 1) if nearest_supply and nearest_supply > hi else round(mid + risk * 1.5, 1)
            return {"trade_plan": True, "direction": "Long",
                    "entry_zone": f"{fmt(lo)}–{fmt(hi)}", "stop_loss": fmt(stop),
                    "target1": fmt(t1), "target2": fmt(t2),
                    "rr": f"T1 {calc_rr(t1-mid, risk)} / T2 {calc_rr(t2-mid, risk)}"}
        else:  # Bearish Breakdown
            choch_dem = last_price_by_type.get("CHOCH DEMAND", float(current_price))
            b    = buf(choch_dem)
            hi   = choch_dem - 1
            lo   = choch_dem - b
            stop = choch_dem + b
            mid  = (lo + hi) / 2
            risk = stop - mid
            t2   = round(mid - risk * 3.0, 1) if not (nearest_demand and nearest_demand < lo) else nearest_demand
            t1   = round((lo + t2) / 2, 1) if nearest_demand and nearest_demand < lo else round(mid - risk * 1.5, 1)
            return {"trade_plan": True, "direction": "Short",
                    "entry_zone": f"{fmt(lo)}–{fmt(hi)}", "stop_loss": fmt(stop),
                    "target1": fmt(t1), "target2": fmt(t2),
                    "rr": f"T1 {calc_rr(mid-t1, risk)} / T2 {calc_rr(mid-t2, risk)}"}

    # ── Reversal Setup — direction from risk zone ──
    if trade_opportunity == "Reversal Setup":
        if risk_label == "Testing Supply" and nearest_supply is not None:
            return short_plan(nearest_supply)
        if risk_label == "Testing Demand" and nearest_demand is not None:
            return long_plan(nearest_demand)
        return no_plan("Reversal setup detected but insufficient level data.")

    return no_plan("No trade plan available for this opportunity.")


# ---------------------------------------------------------------------------
# Trade plan + Why
# ---------------------------------------------------------------------------

def build_trade_plan(bias, strength, bullish, bearish, counts):
    if bias == "Bearish":
        parts = []
        for k, label in [("CHOCH SUPPLY","CHoCH supply"), ("BOS SUPPLY","BOS supply"),
                         ("MGC SUPPLY ZONE CONFIRMED","supply confirmed"), ("MGC NEW SUPPLY ZONE","new supply")]:
            if counts.get(k):
                parts.append(f"{label} ({counts[k]}×)")
        reason = ", ".join(parts) + f". Bearish {bearish} vs bullish {bullish}." if parts else \
            f"Bearish score ({bearish}) exceeds bullish ({bullish}) by {bearish - bullish}."
        return {"reason": reason, "action": "Wait for retest short. Do not chase lows.",
                "longs_allowed": "No", "shorts_allowed": "Yes", "warning": None}
    elif bias == "Bullish":
        parts = []
        for k, label in [("CHOCH DEMAND","CHoCH demand"), ("BOS DEMAND","BOS demand"),
                         ("MGC DEMAND ZONE CONFIRMED","demand confirmed"), ("MGC NEW DEMAND ZONE","new demand")]:
            if counts.get(k):
                parts.append(f"{label} ({counts[k]}×)")
        reason = ", ".join(parts) + f". Bullish {bullish} vs bearish {bearish}." if parts else \
            f"Bullish score ({bullish}) exceeds bearish ({bearish}) by {bullish - bearish}."
        return {"reason": reason, "action": "Wait for demand hold. Do not chase highs.",
                "longs_allowed": "Yes", "shorts_allowed": "No", "warning": None}
    else:
        return {"reason": f"Supply and demand scores are close (Bull: {bullish}, Bear: {bearish}). No clear edge.",
                "action": "No trade. Wait for clearer supply or demand control.",
                "longs_allowed": "No", "shorts_allowed": "No",
                "warning": "Market is choppy. Standing aside is a valid position."}


def build_why(bias, confidence, bullish, bearish, counts,
              verdict, overextended, risk_label):
    bear_struct, bull_struct = [], []
    for k, lbl in [("CHOCH SUPPLY","CHOCH Supply"), ("BOS SUPPLY","BOS Supply"),
                   ("MGC SUPPLY ZONE CONFIRMED","supply confirmed"), ("MGC NEW SUPPLY ZONE","new supply")]:
        if counts.get(k):
            bear_struct.append(f"{lbl} ×{counts[k]}")
    for k, lbl in [("CHOCH DEMAND","CHOCH Demand"), ("BOS DEMAND","BOS Demand"),
                   ("MGC DEMAND ZONE CONFIRMED","demand confirmed"), ("MGC NEW DEMAND ZONE","new demand")]:
        if counts.get(k):
            bull_struct.append(f"{lbl} ×{counts[k]}")

    if bullish == 0 and bearish == 0:
        return "No alerts received yet. No edge to evaluate."
    if overextended:
        return (f"Confidence {confidence}% but price is overextended ({risk_label}). "
                "Entry not recommended until price retraces to a level.")
    if bias == "Choppy":
        dom_side  = "supply" if bearish >= bullish else "demand"
        weak_side = "demand" if dom_side == "supply" else "supply"
        return (f"Confidence only {confidence}%. Supply and demand are mixed "
                f"({dom_side} {max(bullish, bearish)}, {weak_side} {min(bullish, bearish)}). No edge.")

    signal_list = bear_struct if bias == "Bearish" else bull_struct
    direction   = bias.lower()
    score_dom   = bearish if bias == "Bearish" else bullish
    score_opp   = bullish if bias == "Bearish" else bearish
    signal_text = ", ".join(signal_list) if signal_list else f"{direction} score {score_dom}"

    if verdict in ("STRONG SHORT", "STRONG LONG"):
        return (f"Confidence {confidence}%. {signal_text}. Strong {direction} structure "
                f"with minimal opposition. Trend continuation likely. Score {score_dom} vs {score_opp}.")
    if verdict in ("SHORT BIAS", "LONG BIAS"):
        return (f"Confidence {confidence}%. {signal_text}. Clear {direction} edge. Score {score_dom} vs {score_opp}.")
    return (f"Confidence only {confidence}%. Signals present ({signal_text}) "
            f"but opposing pressure is too close. No reliable edge. Score {score_dom} vs {score_opp}.")


# ---------------------------------------------------------------------------
# Discord helpers
# ---------------------------------------------------------------------------

def fmt_window_counts(counts, total):
    if total == 0:
        return "No alerts"
    bear_parts, bull_parts = [], []
    for k, v in counts.items():
        if v == 0:
            continue
        short = (k.replace("MGC ", "").replace(" ZONE", "")
                  .replace("CONFIRMED", "CONF").replace("NEW SUPPLY", "NEW SUP")
                  .replace("NEW DEMAND", "NEW DEM"))
        (bear_parts if ALERT_TYPES[k]["side"] == "bearish" else bull_parts).append(f"{short} ×{v}")
    parts = []
    if bear_parts:
        parts.append("🔴 " + ", ".join(bear_parts))
    if bull_parts:
        parts.append("🟢 " + ", ".join(bull_parts))
    return "\n".join(parts) if parts else "No alerts"

def bias_color(verdict):
    return {
        "STRONG SHORT": 0xCC0000,
        "SHORT BIAS":   0xFF5555,
        "WAIT":         0xFFCC00,
        "LONG BIAS":    0x55CC55,
        "STRONG LONG":  0x00AA00,
    }.get(verdict, 0x888888)

VERDICT_EMOJI = {
    "STRONG SHORT": "🔴🔴",
    "SHORT BIAS":   "🔴",
    "WAIT":         "⏸️",
    "LONG BIAS":    "🟢",
    "STRONG LONG":  "🟢🟢",
}

RECOMMENDATION_EMOJI = {
    "HIGH CONVICTION TRADE": "🔥",
    "TRADE":                 "✅",
    "WATCH":                 "👀",
    "WAIT":                  "⏸️",
}


def _trade_plan_fields(tp):
    """Return Discord embed field dicts for the trade plan."""
    if not tp["trade_plan"]:
        return [
            {
                "name":   "📋  Trade Plan",
                "value":  f"**No trade plan generated.**\nReason: {tp['reason']}",
                "inline": False,
            }
        ]
    direction_emoji = "🟢" if tp["direction"] == "Long" else "🔴"
    return [
        {
            "name":   f"📋  Trade Plan  {direction_emoji} {tp['direction']}",
            "value":  (
                f"**Entry Zone:** {tp['entry_zone']}\n"
                f"**Stop Loss:** {tp['stop_loss']}\n"
                f"**Target 1:** {tp['target1']}\n"
                f"**Target 2:** {tp['target2']}\n"
                f"**R:R** {tp['rr']}"
            ),
            "inline": False,
        }
    ]


def _setup_fields(setup):
    """Return a list of Discord embed field dicts for the current setup."""
    s = setup["setup"]
    if s == "NONE":
        return [
            {"name": "📐  Current Setup", "value": "**NONE**",         "inline": True},
            {"name": "💬  Reason",        "value": setup["reason"],     "inline": False},
        ]
    if s == "Range Chop":
        return [
            {"name": "📐  Current Setup", "value": "**Range Chop**",              "inline": True},
            {"name": "⏳  Wait For",      "value": setup["entry_trigger"],         "inline": False},
        ]
    return [
        {"name": "📐  Current Setup", "value": f"**{s}**",             "inline": True},
        {"name": "📍  Entry Trigger",  "value": setup["entry_trigger"], "inline": False},
        {"name": "❌  Invalidation",   "value": setup["invalidation"],  "inline": True},
        {"name": "🎯  Target",         "value": setup["target"],        "inline": True},
    ]


_DIR_EMOJI = {"Bullish": "🟢", "Bearish": "🔴", "Range": "🟡", "Neutral": "⚪"}
_OPP_EMOJI = {
    "Long Setup":     "🟢",
    "Short Setup":    "🔴",
    "Breakout Setup": "⚡",
    "Watch Supply":   "👀",
    "Watch Demand":   "👀",
    "None":           "⏸️",
}

_WATCH_STATES = ("Watch Supply", "Watch Demand")


def _direction_opportunity_fields(market_direction, trade_opportunity,
                                   opportunity_reason, entry_trigger, invalidation):
    """Return Discord embed fields for Market Direction + Trade Opportunity. Max 3 fields."""
    d_emoji = _DIR_EMOJI.get(market_direction, "⚪")
    o_emoji = _OPP_EMOJI.get(trade_opportunity, "")
    opp_value = f"{o_emoji} **{trade_opportunity}**\n{opportunity_reason}"
    fields = [
        {"name": "📊  Market Direction",  "value": f"{d_emoji} **{market_direction}**", "inline": True},
        {"name": "🔍  Trade Opportunity", "value": opp_value,                           "inline": False},
    ]
    if trade_opportunity not in _WATCH_STATES and trade_opportunity != "None":
        parts = []
        if entry_trigger:
            parts.append(f"**Entry:** {entry_trigger}")
        if invalidation:
            parts.append(f"**Invalidation:** {invalidation}")
        if parts:
            fields.append({"name": "📍  Trade Rules", "value": "\n".join(parts), "inline": False})
    return fields


def _setup_stage_fields(setup_stage, next_step, entry_rule, stage_invalidation):
    """Return Discord embed fields for Setup Stage section (v10). Max 3 fields."""
    emoji = _STAGE_EMOJI.get(setup_stage, "⭕")
    rules = f"**Entry:** {entry_rule}\n**Invalidation:** {stage_invalidation}"
    return [
        {"name": "🎯  Setup Stage", "value": f"{emoji} **{setup_stage}**", "inline": True},
        {"name": "➡️  Next Step",   "value": next_step,                    "inline": False},
        {"name": "📋  Rules",       "value": rules,                        "inline": False},
    ]


def send_discord_message(alert_data, bias, strength, bullish, bearish,
                         confidence, quality, edge_score,
                         recommendation, verdict, reasoning_chain, why, plan,
                         setup_stage, stage_next_step, stage_entry_rule, stage_invalidation,
                         market_direction, trade_opportunity, opportunity_reason,
                         entry_trigger, invalidation, trade_plan,
                         structure_label, structure_class, structure_detail,
                         nearest_supply, nearest_demand,
                         risk_label, risk_detail,
                         last_price_by_type):
    if not DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL not set — skipping")
        return

    color      = bias_color(verdict)
    bias_emoji = {"Bullish": "🟢", "Bearish": "🔴", "Choppy": "🟡"}.get(bias, "⚪")
    rec_emoji  = RECOMMENDATION_EMOJI.get(recommendation, "")
    vrd_emoji  = VERDICT_EMOJI.get(verdict, "")
    ticker     = alert_data.get("ticker") or "MGC"
    price      = alert_data.get("price")
    price_str  = f"${float(price):.2f}" if price is not None else "—"

    # Reasoning chain — arrow-linked
    chain_text = "\n↓\n".join(reasoning_chain)

    # Price levels
    tracked_labels = [
        ("CHOCH SUPPLY",              "Last CHOCH Supply"),
        ("BOS SUPPLY",                "Last BOS Supply"),
        ("MGC SUPPLY ZONE CONFIRMED", "Last Supply Conf"),
        ("CHOCH DEMAND",              "Last CHOCH Demand"),
        ("BOS DEMAND",                "Last BOS Demand"),
        ("MGC DEMAND ZONE CONFIRMED", "Last Demand Conf"),
    ]
    price_lines = [f"`{lbl}`: **${last_price_by_type[k]:.2f}**"
                   for k, lbl in tracked_labels if k in last_price_by_type]

    sup_str = f"${nearest_supply:.2f}" if nearest_supply is not None else "—"
    dem_str = f"${nearest_demand:.2f}" if nearest_demand is not None else "—"

    risk_emoji = {"Testing Supply": "⚠️", "Testing Demand": "⚠️",
                  "Overextended": "🚫", "Choppy": "🟡"}.get(risk_label, "📍")

    window_fields = []
    for label, minutes in TIME_WINDOWS.items():
        w_counts, w_total = window_summary(minutes)
        window_fields.append({
            "name": f"🕐  {label}", "value": fmt_window_counts(w_counts, w_total), "inline": True,
        })

    fields = [
        # ── Final Verdict (lead) ──
        {
            "name":   "🎯  Final Verdict",
            "value":  f"{vrd_emoji} **{verdict}**",
            "inline": True,
        },
        {
            "name":   "📣  Recommendation",
            "value":  f"{rec_emoji} **{recommendation}**",
            "inline": True,
        },
        {
            "name":   "⚡  Edge Score",
            "value":  f"**{edge_score} / 100**",
            "inline": True,
        },
        # ── Reasoning Chain ──
        {
            "name":   "🔗  Reasoning Chain",
            "value":  f"```\n{chain_text}\n```",
            "inline": False,
        },
        # ── Setup Stage (v10) ──
        *_setup_stage_fields(setup_stage, stage_next_step, stage_entry_rule, stage_invalidation),
        # ── Market Direction + Trade Opportunity ──
        *_direction_opportunity_fields(market_direction, trade_opportunity,
                                       opportunity_reason, entry_trigger, invalidation),
        # ── Trade Plan ──
        *_trade_plan_fields(trade_plan),
        # ── Why ──
        {
            "name":   "💬  Why",
            "value":  why,
            "inline": False,
        },
        # ── Bias / Confidence / Quality (combined) ──
        {
            "name":  "📊  Bias · Confidence · Quality",
            "value": (
                f"{bias_emoji} **{bias}** Strength {strength}/10 · "
                f"**{confidence}%** · "
                f"**{quality}** {QUALITY_LABELS.get(quality,'')}\n"
                f"Bull `{bullish}` · Bear `{bearish}` · Gap `{abs(bullish-bearish)}`"
            ),
            "inline": False,
        },
        # ── Market Structure ──
        {
            "name":   "🏗️  Market Structure",
            "value":  f"**{structure_label}** → `{structure_class}`\n{structure_detail}",
            "inline": False,
        },
        # ── Price context ──
        {
            "name":   "💲  Price Levels",
            "value":  "\n".join(price_lines) if price_lines else "No levels tracked yet",
            "inline": False,
        },
        {
            "name":   "📈 Supply · 📉 Demand",
            "value":  f"{sup_str}  ·  {dem_str}",
            "inline": True,
        },
        # ── Risk Zone ──
        {
            "name":   f"{risk_emoji}  Risk Zone",
            "value":  f"**{risk_label}**\n{risk_detail}",
            "inline": False,
        },
        # ── Windows ──
        *window_fields,
        # ── Action / Permissions ──
        {
            "name":   "🗺️  Action",
            "value":  plan["action"],
            "inline": False,
        },
        {
            "name":   "🟩 Longs · 🟥 Shorts",
            "value":  f"Longs: {plan['longs_allowed']}  ·  Shorts: {plan['shorts_allowed']}",
            "inline": False,
        },
    ]

    if plan["warning"]:
        fields.append({"name": "⚠️  Warning", "value": plan["warning"], "inline": False})

    embed = {
        "title":       "MGC Agent v10",
        "description": f"**{ticker}** · {price_str} · `{alert_data.get('alert_type','—')}`",
        "color":       color,
        "fields":      fields,
        "footer":      {"text": f"Received {alert_data.get('timestamp','')}"},
        "timestamp":   now_utc().isoformat(),
    }

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
        resp.raise_for_status()
        logger.info("Discord sent (status %s)", resp.status_code)
    except requests.RequestException as exc:
        logger.error("Discord send failed: %s", exc)


# ---------------------------------------------------------------------------
# Setup Stage Detection v10
# ---------------------------------------------------------------------------

_STAGE_EMOJI = {
    "No Setup":            "⭕",
    "Watch Supply":        "👁️",
    "Watch Demand":        "👁️",
    "Short Setup Forming": "⚠️",
    "Long Setup Forming":  "⚠️",
    "Short Ready":         "🔴",
    "Long Ready":          "🟢",
}

READY_STAGES = ("Short Ready", "Long Ready")


def get_setup_stage(current_price, nearest_supply, nearest_demand,
                    bullish, bearish, alert_history):
    """
    Determine the current setup stage (v10).
    Returns (stage, next_step, entry_rule, invalidation_note).

    Stages (in priority order):
      Short Ready        — supply confirmed + all forming conditions met
      Long Ready         — demand confirmed + all forming conditions met
      Short Setup Forming— at supply + bearish dominant + CHOCH/BOS Supply recent
      Long Setup Forming — at demand + bullish dominant + CHOCH/BOS Demand recent
      Watch Supply       — price within 0.75% of supply
      Watch Demand       — price within 0.75% of demand
      No Setup           — mid-range / no proximity
    """
    if current_price is None:
        return ("No Setup", "Waiting for price data.", "No entry.", "N/A")

    # ── Recent alert window: last 5 alerts ──
    recent = [a["alert_type"] for a in list(alert_history)[-5:]]
    has_choch_bos_supply = any(t in ("CHOCH SUPPLY", "BOS SUPPLY")          for t in recent)
    has_choch_bos_demand = any(t in ("CHOCH DEMAND", "BOS DEMAND")          for t in recent)
    has_supply_confirmed = "MGC SUPPLY ZONE CONFIRMED"                       in recent
    has_demand_confirmed = "MGC DEMAND ZONE CONFIRMED"                       in recent

    # ── Proximity (0.75%) ──
    def dist(a, b):
        return abs(a - b) / b if b else float("inf")

    at_supply = nearest_supply is not None and dist(nearest_supply, current_price) <= WATCH_PCT
    at_demand = nearest_demand is not None and dist(current_price, nearest_demand) <= WATCH_PCT

    bearish_dominant = (bearish - bullish) >= BIAS_THRESHOLD
    bullish_dominant = (bullish - bearish) >= BIAS_THRESHOLD

    sup_str = f"${nearest_supply:.2f}" if nearest_supply else "—"
    dem_str = f"${nearest_demand:.2f}" if nearest_demand else "—"

    # ── SHORT READY ──
    if at_supply and bearish_dominant and has_choch_bos_supply and has_supply_confirmed:
        return (
            "Short Ready",
            "Supply confirmed. Enter short on next 5m bearish close.",
            "5m bearish candle closes below entry zone. Enter on close.",
            f"Close above nearest supply ({sup_str}).",
        )

    # ── LONG READY ──
    if at_demand and bullish_dominant and has_choch_bos_demand and has_demand_confirmed:
        return (
            "Long Ready",
            "Demand confirmed. Enter long on next 5m bullish close.",
            "5m bullish candle closes above entry zone. Enter on close.",
            f"Close below nearest demand ({dem_str}).",
        )

    # ── SHORT SETUP FORMING ──
    if at_supply and bearish_dominant and has_choch_bos_supply:
        return (
            "Short Setup Forming",
            "Wait for 5m rejection candle from supply.",
            "Do not enter until confirmation candle closes.",
            f"Close above nearest supply ({sup_str}).",
        )

    # ── LONG SETUP FORMING ──
    if at_demand and bullish_dominant and has_choch_bos_demand:
        return (
            "Long Setup Forming",
            "Wait for 5m bounce candle from demand.",
            "Do not enter until confirmation candle closes.",
            f"Close below nearest demand ({dem_str}).",
        )

    # ── WATCH SUPPLY ──
    if at_supply:
        return (
            "Watch Supply",
            "Monitor price action at supply. Watch for bearish structure to form.",
            "No entry — observation only.",
            "N/A",
        )

    # ── WATCH DEMAND ──
    if at_demand:
        return (
            "Watch Demand",
            "Monitor price action at demand. Watch for bullish structure to form.",
            "No entry — observation only.",
            "N/A",
        )

    # ── NO SETUP ──
    return (
        "No Setup",
        "Price mid-range. Wait for proximity to a key level.",
        "No entry.",
        "N/A",
    )


# ---------------------------------------------------------------------------
# Full analysis
# ---------------------------------------------------------------------------

def full_analysis(current_price_override=None):
    bullish, bearish, counts = calculate_scores()
    bias, strength           = calculate_bias(bullish, bearish)
    confidence               = calculate_confidence(bullish, bearish)
    quality                  = calculate_trade_quality(bias, confidence, bullish, bearish)
    edge_score               = calculate_edge_score(bias, confidence, strength)

    last_price_by_type, all_supply, all_demand = get_price_context()
    current_price = current_price_override if current_price_override is not None else CURRENT_PRICE
    nearest_supply, nearest_demand = get_nearest_levels(current_price, all_supply, all_demand)

    structure_label, structure_detail = get_market_structure(current_price, last_price_by_type)
    risk_label, risk_detail, overextended = get_risk_zone(
        bias, current_price, nearest_supply, nearest_demand
    )

    recommendation, verdict, structure_class, reasoning_chain = decision_engine(
        structure_label, risk_label, overextended,
        bias, bullish, bearish, confidence
    )

    market_direction = get_market_direction(structure_label)
    trade_opportunity, opportunity_reason, entry_trigger, invalidation = get_trade_opportunity(
        market_direction, structure_label, risk_label,
        overextended, bullish, bearish, nearest_supply, nearest_demand, last_price_by_type
    )

    trade_plan = generate_trade_plan(
        trade_opportunity, structure_label, risk_label,
        current_price, nearest_supply, nearest_demand, last_price_by_type
    )

    setup_stage, stage_next_step, stage_entry_rule, stage_invalidation = get_setup_stage(
        current_price, nearest_supply, nearest_demand, bullish, bearish, ALERT_HISTORY
    )

    # Override verdict: only TRADE when stage is READY
    if setup_stage not in READY_STAGES:
        verdict = "WAIT"

    why  = build_why(bias, confidence, bullish, bearish, counts,
                     verdict, overextended, risk_label)
    plan = build_trade_plan(bias, strength, bullish, bearish, counts)

    return dict(
        bullish=bullish, bearish=bearish, counts=counts,
        bias=bias, strength=strength, confidence=confidence,
        quality=quality, edge_score=edge_score,
        recommendation=recommendation, verdict=verdict,
        reasoning_chain=reasoning_chain, why=why, plan=plan,
        setup_stage=setup_stage, stage_next_step=stage_next_step,
        stage_entry_rule=stage_entry_rule, stage_invalidation=stage_invalidation,
        market_direction=market_direction,
        trade_opportunity=trade_opportunity,
        opportunity_reason=opportunity_reason,
        entry_trigger=entry_trigger,
        invalidation=invalidation,
        trade_plan=trade_plan,
        current_price=current_price,
        last_price_by_type=last_price_by_type,
        nearest_supply=nearest_supply, nearest_demand=nearest_demand,
        structure_label=structure_label, structure_class=structure_class,
        structure_detail=structure_detail,
        risk_label=risk_label, risk_detail=risk_detail, overextended=overextended,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/webhook", methods=["POST"])
def webhook():
    global CURRENT_PRICE

    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        data = {}

    raw_body = request.get_data(as_text=True)
    if not data and raw_body:
        data = {"alert_type": raw_body.strip()}

    alert_type = (data.get("alert_type") or data.get("message") or data.get("text") or "")
    normalized = alert_type.strip().upper()

    if normalized not in ALERT_TYPES:
        logger.warning("Unrecognized alert type: %r", alert_type)
        return jsonify({"status": "ignored", "reason": "unrecognized alert type", "received": alert_type}), 200

    raw_price = data.get("price")
    try:
        parsed_price = float(raw_price) if raw_price is not None else None
    except (ValueError, TypeError):
        parsed_price = None

    if parsed_price is not None:
        CURRENT_PRICE = parsed_price

    record = {
        "alert_type": normalized,
        "ticker":     data.get("ticker"),
        "price":      parsed_price,
        "timestamp":  now_utc().isoformat(),
        "raw":        data,
    }
    ALERT_HISTORY.append(record)

    a = full_analysis(current_price_override=parsed_price)

    send_discord_message(
        record,
        a["bias"], a["strength"], a["bullish"], a["bearish"],
        a["confidence"], a["quality"], a["edge_score"],
        a["recommendation"], a["verdict"], a["reasoning_chain"], a["why"], a["plan"],
        a["setup_stage"], a["stage_next_step"], a["stage_entry_rule"], a["stage_invalidation"],
        a["market_direction"], a["trade_opportunity"], a["opportunity_reason"],
        a["entry_trigger"], a["invalidation"], a["trade_plan"],
        a["structure_label"], a["structure_class"], a["structure_detail"],
        a["nearest_supply"], a["nearest_demand"],
        a["risk_label"], a["risk_detail"],
        a["last_price_by_type"],
    )

    logger.info(
        "Alert: %s | %s (%d/10) | %d%% | Edge %d | %s → %s | Struct: %s | Risk: %s",
        normalized, a["bias"], a["strength"], a["confidence"], a["edge_score"],
        a["recommendation"], a["verdict"], a["structure_class"], a["risk_label"],
    )

    return jsonify({
        "status":           "ok",
        "alert_type":       normalized,
        "verdict":          a["verdict"],
        "recommendation":   a["recommendation"],
        "reasoning_chain":    a["reasoning_chain"],
        "setup_stage":        a["setup_stage"],
        "stage_next_step":    a["stage_next_step"],
        "stage_entry_rule":   a["stage_entry_rule"],
        "stage_invalidation": a["stage_invalidation"],
        "market_direction":   a["market_direction"],
        "trade_opportunity":  a["trade_opportunity"],
        "opportunity_reason": a["opportunity_reason"],
        "trade_plan":         a["trade_plan"],
        "why":                a["why"],
        "bias":             a["bias"],
        "strength":         a["strength"],
        "confidence":       f"{a['confidence']}%",
        "edge_score":       a["edge_score"],
        "trade_quality":    a["quality"],
        "bullish_score":    a["bullish"],
        "bearish_score":    a["bearish"],
        "market_structure": a["structure_label"],
        "structure_class":  a["structure_class"],
        "risk_zone":        a["risk_label"],
        "risk_detail":      a["risk_detail"],
        "current_price":    a["current_price"],
        "nearest_supply":   a["nearest_supply"],
        "nearest_demand":   a["nearest_demand"],
        "longs_allowed":    a["plan"]["longs_allowed"],
        "shorts_allowed":   a["plan"]["shorts_allowed"],
        "action":           a["plan"]["action"],
        "total_alerts":     len(ALERT_HISTORY),
    }), 200


@app.route("/alerts", methods=["GET"])
def get_alerts():
    return jsonify({"alerts": list(ALERT_HISTORY), "count": len(ALERT_HISTORY)}), 200


@app.route("/clear", methods=["POST"])
def clear_alerts():
    global CURRENT_PRICE
    ALERT_HISTORY.clear()
    CURRENT_PRICE = None
    logger.info("Alert history cleared.")
    return jsonify({"status": "cleared", "alerts_remaining": 0}), 200


@app.route("/price", methods=["GET"])
def price_context():
    last_price_by_type, all_supply, all_demand = get_price_context()
    nearest_supply, nearest_demand = get_nearest_levels(CURRENT_PRICE, all_supply, all_demand)
    structure_label, structure_detail = get_market_structure(CURRENT_PRICE, last_price_by_type)
    structure_class = classify_structure(structure_label)
    bullish, bearish, counts = calculate_scores()
    bias, _ = calculate_bias(bullish, bearish)
    risk_label, risk_detail, overextended = get_risk_zone(
        bias, CURRENT_PRICE, nearest_supply, nearest_demand
    )
    return jsonify({
        "current_price":      CURRENT_PRICE,
        "nearest_supply":     nearest_supply,
        "nearest_demand":     nearest_demand,
        "market_structure":   structure_label,
        "structure_class":    structure_class,
        "structure_detail":   structure_detail,
        "risk_zone":          risk_label,
        "risk_detail":        risk_detail,
        "overextended":       overextended,
        "last_price_by_type": last_price_by_type,
        "all_supply_prices":  sorted(set(all_supply), reverse=True),
        "all_demand_prices":  sorted(set(all_demand), reverse=True),
    }), 200


@app.route("/status", methods=["GET"])
def status():
    a = full_analysis()
    windows = {}
    for label, minutes in TIME_WINDOWS.items():
        w_counts, w_total = window_summary(minutes)
        w_bull, w_bear, _ = score_alerts(alerts_in_window(minutes))
        windows[label] = {"alert_counts": w_counts, "total": w_total,
                          "bullish_score": w_bull, "bearish_score": w_bear}
    return jsonify({
        "status":              "running",
        "version":             "10.0",
        "verdict":             a["verdict"],
        "recommendation":      a["recommendation"],
        "reasoning_chain":     a["reasoning_chain"],
        "setup_stage":         a["setup_stage"],
        "stage_next_step":     a["stage_next_step"],
        "stage_entry_rule":    a["stage_entry_rule"],
        "stage_invalidation":  a["stage_invalidation"],
        "market_direction":    a["market_direction"],
        "trade_opportunity":   a["trade_opportunity"],
        "opportunity_reason":  a["opportunity_reason"],
        "trade_plan":          a["trade_plan"],
        "why":                 a["why"],
        "bias":                a["bias"],
        "strength":            f"{a['strength']}/10",
        "confidence":          f"{a['confidence']}%",
        "edge_score":          a["edge_score"],
        "trade_quality":       a["quality"],
        "trade_quality_label": QUALITY_LABELS.get(a["quality"], ""),
        "bullish_score":       a["bullish"],
        "bearish_score":       a["bearish"],
        "market_structure":    a["structure_label"],
        "structure_class":     a["structure_class"],
        "structure_detail":    a["structure_detail"],
        "risk_zone":           a["risk_label"],
        "risk_detail":         a["risk_detail"],
        "overextended":        a["overextended"],
        "current_price":       a["current_price"],
        "nearest_supply":      a["nearest_supply"],
        "nearest_demand":      a["nearest_demand"],
        "longs_allowed":       a["plan"]["longs_allowed"],
        "shorts_allowed":      a["plan"]["shorts_allowed"],
        "action":              a["plan"]["action"],
        "warning":             a["plan"]["warning"],
        "alert_counts":        a["counts"],
        "windows":             windows,
        "total_alerts_stored": len(ALERT_HISTORY),
        "discord_configured":  bool(DISCORD_WEBHOOK_URL),
    }), 200


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service":     "TradingView Webhook Server",
        "version":     "10.0",
        "alert_types": list(ALERT_TYPES.keys()),
        "endpoints":   {
            "POST /webhook": "Receive TradingView alerts",
            "GET /alerts":   "View last 100 stored alerts",
            "GET /price":    "Price context, levels, structure, and risk zone",
            "GET /status":   "Full analysis with verdict and reasoning chain",
        },
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
