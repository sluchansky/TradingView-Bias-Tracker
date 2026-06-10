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
CURRENT_PRICE = None          # updated on every inbound alert

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

BIAS_THRESHOLD  = 3
NEAR_PCT        = 0.005   # 0.5% — price "testing" a level
EXTENDED_PCT    = 0.010   # 1.0% — price "too extended"

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
    gap      = abs(bullish - bearish)
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
    return round(max(bullish, bearish) / total * 100)


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


# ---------------------------------------------------------------------------
# Edge Score
# ---------------------------------------------------------------------------

def calculate_edge_score(bias, confidence, strength):
    if bias == "Choppy":
        return round(confidence * 0.5 + (strength / 10) * 10)
    return min(100, round(confidence * 0.7 + (strength / 10) * 30))


# ---------------------------------------------------------------------------
# Price context
# ---------------------------------------------------------------------------

def get_price_context():
    """
    Scan ALERT_HISTORY and return:
      - last_price_by_type: dict[alert_type -> float]
      - all_supply_prices:  list[float]  (all historical bearish alert prices)
      - all_demand_prices:  list[float]  (all historical bullish alert prices)
    """
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

        if t in SUPPLY_TYPES:
            all_supply_prices.append(price)
        else:
            all_demand_prices.append(price)

    return last_price_by_type, all_supply_prices, all_demand_prices


def get_nearest_levels(current_price, all_supply_prices, all_demand_prices):
    """
    Returns (nearest_supply, nearest_demand):
    - nearest_supply: closest supply price AT or ABOVE current price,
      falling back to highest known supply if none above.
    - nearest_demand: closest demand price AT or BELOW current price,
      falling back to lowest known demand if none below.
    """
    nearest_supply = nearest_demand = None

    if all_supply_prices:
        if current_price is not None:
            # strictly above so that a supply alert at current price doesn't
            # collapse the distance to zero; fall back to highest known supply
            above = [p for p in all_supply_prices if p > current_price]
            nearest_supply = min(above) if above else max(all_supply_prices)
        else:
            nearest_supply = max(all_supply_prices)

    if all_demand_prices:
        if current_price is not None:
            # strictly below for the same reason
            below = [p for p in all_demand_prices if p < current_price]
            nearest_demand = max(below) if below else min(all_demand_prices)
        else:
            nearest_demand = min(all_demand_prices)

    return nearest_supply, nearest_demand


def get_market_structure(current_price, last_price_by_type):
    """
    Determine market structure from CHOCH / BOS levels relative to current price.
    Returns (structure_label, structure_detail).
    """
    if current_price is None:
        return "Unknown", "No price data received yet."

    choch_sup = last_price_by_type.get("CHOCH SUPPLY")
    choch_dem = last_price_by_type.get("CHOCH DEMAND")
    bos_sup   = last_price_by_type.get("BOS SUPPLY")
    bos_dem   = last_price_by_type.get("BOS DEMAND")

    parts = []

    # Primary structure from CHOCH
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
        # price <= choch_sup means at or below the supply level → bearish
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

    # Augment with BOS confirmation
    if bos_sup and current_price < bos_sup:
        parts.append(f"below BOS Supply ({bos_sup:.2f})")
    if bos_dem and current_price > bos_dem:
        parts.append(f"above BOS Demand ({bos_dem:.2f})")

    detail = ". ".join(parts) + "."
    return structure, detail


def get_risk_zone(bias, current_price, nearest_supply, nearest_demand):
    """
    Returns (risk_label, risk_detail, overextended: bool).
    overextended=True blocks TRADE / HIGH CONVICTION recommendations.
    """
    if current_price is None:
        return "Unknown", "No price data available.", False

    def pct(a, b):
        return abs(a - b) / b if b else 0

    if bias == "Bearish":
        if nearest_supply is not None:
            dist = pct(nearest_supply, current_price)
            if dist <= NEAR_PCT:
                return (
                    "Testing Supply",
                    f"Price is testing supply at {nearest_supply:.2f} "
                    f"({dist:.2%} away). Favor shorts.",
                    False,
                )
            elif dist >= EXTENDED_PCT:
                return (
                    "Overextended",
                    f"Price is too extended from supply ({nearest_supply:.2f}, "
                    f"{dist:.2%} away). Wait for retracement.",
                    True,
                )
            else:
                return (
                    "Approaching Supply",
                    f"Price approaching supply at {nearest_supply:.2f} "
                    f"({dist:.2%} away). Watch for rejection.",
                    False,
                )
        return "No Supply Level", "No supply level tracked yet. Use caution.", False

    elif bias == "Bullish":
        if nearest_demand is not None:
            dist = pct(current_price, nearest_demand)
            if dist <= NEAR_PCT:
                return (
                    "Testing Demand",
                    f"Price is testing demand at {nearest_demand:.2f} "
                    f"({dist:.2%} away). Favor longs.",
                    False,
                )
            elif dist >= EXTENDED_PCT:
                return (
                    "Overextended",
                    f"Price is too extended from demand ({nearest_demand:.2f}, "
                    f"{dist:.2%} away). Wait for retracement.",
                    True,
                )
            else:
                return (
                    "Approaching Demand",
                    f"Price pulling back toward demand at {nearest_demand:.2f} "
                    f"({dist:.2%} away). Watch for hold.",
                    False,
                )
        return "No Demand Level", "No demand level tracked yet. Use caution.", False

    else:  # Choppy
        msgs = []
        if nearest_supply:
            msgs.append(f"Supply {nearest_supply:.2f} ({pct(nearest_supply, current_price):.2%} away)")
        if nearest_demand:
            msgs.append(f"Demand {nearest_demand:.2f} ({pct(current_price, nearest_demand):.2%} away)")
        detail = "Choppy market. " + " · ".join(msgs) + ". No directional edge." if msgs \
            else "Choppy market. No levels tracked."
        return "Choppy", detail, False


# ---------------------------------------------------------------------------
# Trade eligibility
# ---------------------------------------------------------------------------

def calculate_recommendation(bias, confidence, overextended):
    if bias == "Choppy" or overextended:
        return "WAIT"
    if confidence >= 90:
        return "HIGH CONVICTION TRADE"
    if confidence >= 80:
        return "TRADE"
    if confidence >= 70:
        return "WATCH"
    return "WAIT"


def build_why(bias, confidence, strength, bullish, bearish, counts,
              recommendation, overextended, risk_label):
    bear_struct, bull_struct = [], []
    if counts.get("CHOCH SUPPLY"):
        bear_struct.append(f"CHOCH Supply ×{counts['CHOCH SUPPLY']}")
    if counts.get("BOS SUPPLY"):
        bear_struct.append(f"BOS Supply ×{counts['BOS SUPPLY']}")
    if counts.get("MGC SUPPLY ZONE CONFIRMED"):
        bear_struct.append(f"supply confirmed ×{counts['MGC SUPPLY ZONE CONFIRMED']}")
    if counts.get("MGC NEW SUPPLY ZONE"):
        bear_struct.append(f"new supply ×{counts['MGC NEW SUPPLY ZONE']}")
    if counts.get("CHOCH DEMAND"):
        bull_struct.append(f"CHOCH Demand ×{counts['CHOCH DEMAND']}")
    if counts.get("BOS DEMAND"):
        bull_struct.append(f"BOS Demand ×{counts['BOS DEMAND']}")
    if counts.get("MGC DEMAND ZONE CONFIRMED"):
        bull_struct.append(f"demand confirmed ×{counts['MGC DEMAND ZONE CONFIRMED']}")
    if counts.get("MGC NEW DEMAND ZONE"):
        bull_struct.append(f"new demand ×{counts['MGC NEW DEMAND ZONE']}")

    if bullish == 0 and bearish == 0:
        return "No alerts received yet. No edge to evaluate."

    if overextended:
        return (
            f"Confidence {confidence}% but price is overextended ({risk_label}). "
            "Entry not recommended until price retraces to a level."
        )

    if bias == "Choppy":
        dom_side = "supply" if bearish >= bullish else "demand"
        weak_side = "demand" if dom_side == "supply" else "supply"
        return (
            f"Confidence only {confidence}%. "
            f"Supply and demand are mixed ({dom_side} {max(bullish, bearish)}, "
            f"{weak_side} {min(bullish, bearish)}). No edge."
        )

    signal_list = bear_struct if bias == "Bearish" else bull_struct
    direction   = bias.lower()
    score_dom   = bearish if bias == "Bearish" else bullish
    score_opp   = bullish if bias == "Bearish" else bearish
    signal_text = ", ".join(signal_list) if signal_list else f"{direction} score {score_dom}"

    if recommendation == "HIGH CONVICTION TRADE":
        return (
            f"Confidence {confidence}%. {signal_text}. "
            f"Strong {direction} structure with minimal opposition. "
            f"Trend continuation likely. Score {score_dom} vs {score_opp}."
        )
    if recommendation == "TRADE":
        return (
            f"Confidence {confidence}%. {signal_text}. "
            f"Clear {direction} edge with sufficient signal weight. "
            f"Score {score_dom} vs {score_opp}."
        )
    if recommendation == "WATCH":
        return (
            f"Confidence {confidence}%. {signal_text}. "
            f"Bias is {direction} but not strong enough to commit. "
            f"Wait for additional confirmation. Score {score_dom} vs {score_opp}."
        )
    return (
        f"Confidence only {confidence}%. Signals present ({signal_text}) "
        f"but opposing pressure is too close. No reliable edge. "
        f"Score {score_dom} vs {score_opp}."
    )


# ---------------------------------------------------------------------------
# Trade plan
# ---------------------------------------------------------------------------

def build_trade_plan(bias, strength, bullish, bearish, counts):
    if bias == "Bearish":
        parts = []
        for k, label in [("CHOCH SUPPLY","CHoCH supply"), ("BOS SUPPLY","BOS supply"),
                         ("MGC SUPPLY ZONE CONFIRMED","supply confirmed"),
                         ("MGC NEW SUPPLY ZONE","new supply")]:
            if counts.get(k):
                parts.append(f"{label} ({counts[k]}×)")
        reason = (
            ", ".join(parts) + f". Bearish {bearish} vs bullish {bullish}."
            if parts else
            f"Bearish score ({bearish}) exceeds bullish ({bullish}) by {bearish - bullish}."
        )
        return {"reason": reason, "action": "Wait for retest short. Do not chase lows.",
                "longs_allowed": "No", "shorts_allowed": "Yes", "warning": None}

    elif bias == "Bullish":
        parts = []
        for k, label in [("CHOCH DEMAND","CHoCH demand"), ("BOS DEMAND","BOS demand"),
                         ("MGC DEMAND ZONE CONFIRMED","demand confirmed"),
                         ("MGC NEW DEMAND ZONE","new demand")]:
            if counts.get(k):
                parts.append(f"{label} ({counts[k]}×)")
        reason = (
            ", ".join(parts) + f". Bullish {bullish} vs bearish {bearish}."
            if parts else
            f"Bullish score ({bullish}) exceeds bearish ({bearish}) by {bullish - bearish}."
        )
        return {"reason": reason, "action": "Wait for demand hold. Do not chase highs.",
                "longs_allowed": "Yes", "shorts_allowed": "No", "warning": None}

    else:
        return {
            "reason": f"Supply and demand scores are close (Bull: {bullish}, Bear: {bearish}). No clear edge.",
            "action": "No trade. Wait for clearer supply or demand control.",
            "longs_allowed": "No", "shorts_allowed": "No",
            "warning": "Market is choppy. Standing aside is a valid position.",
        }


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


def bias_color(bias):
    return {"Bearish": 0xFF3333, "Bullish": 0x33CC66, "Choppy": 0xFFCC00}.get(bias, 0x888888)


RECOMMENDATION_EMOJI = {
    "HIGH CONVICTION TRADE": "🔥",
    "TRADE":                 "✅",
    "WATCH":                 "👀",
    "WAIT":                  "⏸️",
}


def send_discord_message(alert_data, bias, strength, bullish, bearish,
                         confidence, quality, edge_score,
                         recommendation, why, plan,
                         structure_label, structure_detail,
                         nearest_supply, nearest_demand,
                         risk_label, risk_detail,
                         last_price_by_type, color):
    if not DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL not set — skipping")
        return

    bias_emoji = {"Bullish": "🟢", "Bearish": "🔴", "Choppy": "🟡"}.get(bias, "⚪")
    rec_emoji  = RECOMMENDATION_EMOJI.get(recommendation, "")
    ticker     = alert_data.get("ticker") or "MGC"
    price      = alert_data.get("price")
    price_str  = f"${float(price):.2f}" if price is not None else "—"

    # ── Price context lines ──
    tracked_labels = [
        ("CHOCH SUPPLY",              "Last CHOCH Supply"),
        ("BOS SUPPLY",                "Last BOS Supply"),
        ("MGC SUPPLY ZONE CONFIRMED", "Last Supply Confirmed"),
        ("CHOCH DEMAND",              "Last CHOCH Demand"),
        ("BOS DEMAND",                "Last BOS Demand"),
        ("MGC DEMAND ZONE CONFIRMED", "Last Demand Confirmed"),
    ]
    price_lines = []
    for key, label in tracked_labels:
        p = last_price_by_type.get(key)
        if p is not None:
            price_lines.append(f"`{label}`: **${p:.2f}**")

    price_context_value = "\n".join(price_lines) if price_lines else "No levels tracked yet"

    sup_str = f"${nearest_supply:.2f}" if nearest_supply is not None else "—"
    dem_str = f"${nearest_demand:.2f}" if nearest_demand is not None else "—"

    # ── Window fields ──
    window_fields = []
    for label, minutes in TIME_WINDOWS.items():
        w_counts, w_total = window_summary(minutes)
        window_fields.append({
            "name": f"🕐  {label}", "value": fmt_window_counts(w_counts, w_total), "inline": True,
        })

    risk_emoji = {"Testing Supply": "⚠️", "Testing Demand": "⚠️",
                  "Overextended": "🚫", "Choppy": "🟡"}.get(risk_label, "📍")

    fields = [
        # ── Top metrics ──
        {"name": "📊  Bias",           "value": f"{bias_emoji} **{bias}**   Strength {strength}/10", "inline": True},
        {"name": "🎯  Confidence",      "value": f"**{confidence}%**",                                "inline": True},
        {"name": "⚡  Edge Score",      "value": f"**{edge_score} / 100**",                           "inline": True},
        {"name": "📣  Recommendation",  "value": f"{rec_emoji} **{recommendation}**",                 "inline": True},
        {"name": "🏆  Trade Quality",   "value": f"**{quality}** — {QUALITY_LABELS.get(quality,'')}",  "inline": True},
        {"name": "🔢  Score",           "value": f"Bull `{bullish}` · Bear `{bearish}` · Gap `{abs(bullish-bearish)}`", "inline": True},
        # ── Why ──
        {"name": "💬  Why",             "value": why,                                                  "inline": False},
        # ── Market Structure ──
        {"name": "🏗️  Market Structure","value": f"**{structure_label}**\n{structure_detail}",         "inline": False},
        # ── Price Context ──
        {"name": "💲  Price Levels",    "value": price_context_value,                                  "inline": False},
        {"name": "📈  Nearest Supply",  "value": sup_str,                                              "inline": True},
        {"name": "📉  Nearest Demand",  "value": dem_str,                                              "inline": True},
        # ── Risk Zone ──
        {"name": f"{risk_emoji}  Risk Zone", "value": f"**{risk_label}**\n{risk_detail}",             "inline": False},
        # ── Recent Alert Summary ──
        {"name": "📋  Recent Alert Summary", "value": "━━━━━━━━━━━━━━━━━━━━━━━━━━",                   "inline": False},
        *window_fields,
        # ── Action ──
        {"name": "🗺️  Action",          "value": plan["action"],                                       "inline": False},
        {"name": "🟩  Longs Allowed",   "value": plan["longs_allowed"],                               "inline": True},
        {"name": "🟥  Shorts Allowed",  "value": plan["shorts_allowed"],                              "inline": True},
    ]

    if plan["warning"]:
        fields.append({"name": "⚠️  Warning", "value": plan["warning"], "inline": False})

    embed = {
        "title":       "MGC Agent v5",
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
# Full analysis
# ---------------------------------------------------------------------------

def full_analysis(current_price_override=None):
    bullish, bearish, counts = calculate_scores()
    bias, strength           = calculate_bias(bullish, bearish)
    confidence               = calculate_confidence(bullish, bearish)
    quality                  = calculate_trade_quality(bias, confidence, bullish, bearish)
    edge_score               = calculate_edge_score(bias, confidence, strength)

    # Price context
    last_price_by_type, all_supply, all_demand = get_price_context()
    current_price = current_price_override or CURRENT_PRICE
    nearest_supply, nearest_demand = get_nearest_levels(current_price, all_supply, all_demand)

    structure_label, structure_detail = get_market_structure(current_price, last_price_by_type)
    risk_label, risk_detail, overextended = get_risk_zone(
        bias, current_price, nearest_supply, nearest_demand
    )

    recommendation = calculate_recommendation(bias, confidence, overextended)
    why            = build_why(bias, confidence, strength, bullish, bearish, counts,
                               recommendation, overextended, risk_label)
    plan           = build_trade_plan(bias, strength, bullish, bearish, counts)
    color          = bias_color(bias)

    return dict(
        bullish=bullish, bearish=bearish, counts=counts,
        bias=bias, strength=strength, confidence=confidence,
        quality=quality, edge_score=edge_score,
        recommendation=recommendation, why=why, plan=plan, color=color,
        current_price=current_price,
        last_price_by_type=last_price_by_type,
        nearest_supply=nearest_supply, nearest_demand=nearest_demand,
        structure_label=structure_label, structure_detail=structure_detail,
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

    # Parse and store price
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
        a["recommendation"], a["why"], a["plan"],
        a["structure_label"], a["structure_detail"],
        a["nearest_supply"], a["nearest_demand"],
        a["risk_label"], a["risk_detail"],
        a["last_price_by_type"], a["color"],
    )

    logger.info(
        "Alert: %s | %s (%d/10) | %d%% | Edge %d | %s | %s | Risk: %s",
        normalized, a["bias"], a["strength"], a["confidence"],
        a["edge_score"], a["recommendation"], a["structure_label"], a["risk_label"],
    )

    return jsonify({
        "status":           "ok",
        "alert_type":       normalized,
        "bias":             a["bias"],
        "strength":         a["strength"],
        "confidence":       f"{a['confidence']}%",
        "edge_score":       a["edge_score"],
        "recommendation":   a["recommendation"],
        "why":              a["why"],
        "trade_quality":    a["quality"],
        "bullish_score":    a["bullish"],
        "bearish_score":    a["bearish"],
        "current_price":    a["current_price"],
        "nearest_supply":   a["nearest_supply"],
        "nearest_demand":   a["nearest_demand"],
        "market_structure": a["structure_label"],
        "risk_zone":        a["risk_label"],
        "risk_detail":      a["risk_detail"],
        "longs_allowed":    a["plan"]["longs_allowed"],
        "shorts_allowed":   a["plan"]["shorts_allowed"],
        "action":           a["plan"]["action"],
        "total_alerts":     len(ALERT_HISTORY),
    }), 200


@app.route("/alerts", methods=["GET"])
def get_alerts():
    return jsonify({"alerts": list(ALERT_HISTORY), "count": len(ALERT_HISTORY)}), 200


@app.route("/price", methods=["GET"])
def price_context():
    last_price_by_type, all_supply, all_demand = get_price_context()
    nearest_supply, nearest_demand = get_nearest_levels(CURRENT_PRICE, all_supply, all_demand)
    structure_label, structure_detail = get_market_structure(CURRENT_PRICE, last_price_by_type)
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
        "version":             "5.0",
        "bias":                a["bias"],
        "strength":            f"{a['strength']}/10",
        "confidence":          f"{a['confidence']}%",
        "edge_score":          a["edge_score"],
        "recommendation":      a["recommendation"],
        "why":                 a["why"],
        "trade_quality":       a["quality"],
        "trade_quality_label": QUALITY_LABELS.get(a["quality"], ""),
        "bullish_score":       a["bullish"],
        "bearish_score":       a["bearish"],
        "current_price":       a["current_price"],
        "nearest_supply":      a["nearest_supply"],
        "nearest_demand":      a["nearest_demand"],
        "market_structure":    a["structure_label"],
        "structure_detail":    a["structure_detail"],
        "risk_zone":           a["risk_label"],
        "risk_detail":         a["risk_detail"],
        "overextended":        a["overextended"],
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
        "version":     "5.0",
        "alert_types": list(ALERT_TYPES.keys()),
        "endpoints":   {
            "POST /webhook": "Receive TradingView alerts",
            "GET /alerts":   "View last 100 stored alerts",
            "GET /price":    "Current price context, levels, structure, and risk zone",
            "GET /status":   "Full analysis",
        },
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
