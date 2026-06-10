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

ALERT_TYPES = {
    # Zone alerts
    "MGC NEW SUPPLY ZONE":       {"side": "bearish", "score": 1},
    "MGC SUPPLY ZONE CONFIRMED": {"side": "bearish", "score": 2},
    "MGC NEW DEMAND ZONE":       {"side": "bullish", "score": 1},
    "MGC DEMAND ZONE CONFIRMED": {"side": "bullish", "score": 2},
    # Structure alerts
    "CHOCH SUPPLY":              {"side": "bearish", "score": 3},
    "BOS SUPPLY":                {"side": "bearish", "score": 2},
    "CHOCH DEMAND":              {"side": "bullish", "score": 3},
    "BOS DEMAND":                {"side": "bullish", "score": 2},
}

BIAS_THRESHOLD = 3
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

TIME_WINDOWS = {"15m": 15, "60m": 60, "120m": 120}


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
# Edge Score (0-100)
# Weighted blend of confidence (70%) and bias strength (30%)
# ---------------------------------------------------------------------------

def calculate_edge_score(bias, confidence, strength):
    if bias == "Choppy":
        # Penalise choppy — cap the strength contribution
        return round(confidence * 0.5 + (strength / 10) * 10)
    return min(100, round(confidence * 0.7 + (strength / 10) * 30))


# ---------------------------------------------------------------------------
# Trade Eligibility (Recommendation + Why)
# ---------------------------------------------------------------------------

def calculate_recommendation(bias, confidence):
    if bias == "Choppy":
        return "WAIT"
    if confidence >= 90:
        return "HIGH CONVICTION TRADE"
    if confidence >= 80:
        return "TRADE"
    if confidence >= 70:
        return "WATCH"
    return "WAIT"


def build_why(bias, confidence, strength, bullish, bearish, counts, recommendation):
    """
    Generate a plain-language explanation for the recommendation.
    """
    # Identify the dominant structural signals
    bear_struct = []
    bull_struct = []
    if counts.get("CHOCH SUPPLY", 0):
        bear_struct.append(f"CHOCH Supply ×{counts['CHOCH SUPPLY']}")
    if counts.get("BOS SUPPLY", 0):
        bear_struct.append(f"BOS Supply ×{counts['BOS SUPPLY']}")
    if counts.get("MGC SUPPLY ZONE CONFIRMED", 0):
        bear_struct.append(f"supply confirmed ×{counts['MGC SUPPLY ZONE CONFIRMED']}")
    if counts.get("MGC NEW SUPPLY ZONE", 0):
        bear_struct.append(f"new supply ×{counts['MGC NEW SUPPLY ZONE']}")

    if counts.get("CHOCH DEMAND", 0):
        bull_struct.append(f"CHOCH Demand ×{counts['CHOCH DEMAND']}")
    if counts.get("BOS DEMAND", 0):
        bull_struct.append(f"BOS Demand ×{counts['BOS DEMAND']}")
    if counts.get("MGC DEMAND ZONE CONFIRMED", 0):
        bull_struct.append(f"demand confirmed ×{counts['MGC DEMAND ZONE CONFIRMED']}")
    if counts.get("MGC NEW DEMAND ZONE", 0):
        bull_struct.append(f"new demand ×{counts['MGC NEW DEMAND ZONE']}")

    # No signals at all
    if bullish == 0 and bearish == 0:
        return "No alerts received yet. No edge to evaluate."

    # Choppy
    if bias == "Choppy":
        dominant_side = "supply" if bearish >= bullish else "demand"
        weaker_side   = "demand" if dominant_side == "supply" else "supply"
        return (
            f"Confidence only {confidence}%. "
            f"Supply and demand are mixed ({dominant_side} {max(bullish, bearish)}, "
            f"{weaker_side} {min(bullish, bearish)}). No edge."
        )

    # Biased — build a narrative from signals
    if bias == "Bearish":
        signal_list = bear_struct
        direction   = "bearish"
        score_str   = f"Bearish score {bearish}, bullish {bullish}"
    else:
        signal_list = bull_struct
        direction   = "bullish"
        score_str   = f"Bullish score {bullish}, bearish {bearish}"

    signal_text = ", ".join(signal_list) if signal_list else f"{direction} score {max(bullish, bearish)}"

    if recommendation == "HIGH CONVICTION TRADE":
        return (
            f"Confidence {confidence}%. {signal_text}. "
            f"Strong {direction} structure with minimal opposition. "
            f"Trend continuation likely. {score_str}."
        )
    if recommendation == "TRADE":
        return (
            f"Confidence {confidence}%. {signal_text}. "
            f"Clear {direction} edge with sufficient signal weight. {score_str}."
        )
    if recommendation == "WATCH":
        return (
            f"Confidence {confidence}%. {signal_text}. "
            f"Bias is {direction} but not strong enough to commit. "
            f"Wait for additional confirmation. {score_str}."
        )
    # WAIT
    return (
        f"Confidence only {confidence}%. "
        f"Signals present ({signal_text}) but opposing pressure is too close. "
        f"No reliable edge. {score_str}."
    )


# ---------------------------------------------------------------------------
# Trade plan
# ---------------------------------------------------------------------------

def build_trade_plan(bias, strength, bullish, bearish, counts):
    if bias == "Bearish":
        parts = []
        if counts.get("CHOCH SUPPLY", 0):
            parts.append(f"CHoCH supply ({counts['CHOCH SUPPLY']}×)")
        if counts.get("BOS SUPPLY", 0):
            parts.append(f"BOS supply ({counts['BOS SUPPLY']}×)")
        if counts.get("MGC SUPPLY ZONE CONFIRMED", 0):
            parts.append(f"supply confirmed ({counts['MGC SUPPLY ZONE CONFIRMED']}×)")
        if counts.get("MGC NEW SUPPLY ZONE", 0):
            parts.append(f"new supply ({counts['MGC NEW SUPPLY ZONE']}×)")
        reason = (
            ", ".join(parts) + f". Bearish {bearish} vs bullish {bullish}."
            if parts else
            f"Bearish score ({bearish}) exceeds bullish ({bullish}) by {bearish - bullish}."
        )
        return {
            "reason": reason,
            "action": "Wait for retest short. Do not chase lows.",
            "longs_allowed": "No", "shorts_allowed": "Yes", "warning": None,
        }
    elif bias == "Bullish":
        parts = []
        if counts.get("CHOCH DEMAND", 0):
            parts.append(f"CHoCH demand ({counts['CHOCH DEMAND']}×)")
        if counts.get("BOS DEMAND", 0):
            parts.append(f"BOS demand ({counts['BOS DEMAND']}×)")
        if counts.get("MGC DEMAND ZONE CONFIRMED", 0):
            parts.append(f"demand confirmed ({counts['MGC DEMAND ZONE CONFIRMED']}×)")
        if counts.get("MGC NEW DEMAND ZONE", 0):
            parts.append(f"new demand ({counts['MGC NEW DEMAND ZONE']}×)")
        reason = (
            ", ".join(parts) + f". Bullish {bullish} vs bearish {bearish}."
            if parts else
            f"Bullish score ({bullish}) exceeds bearish ({bearish}) by {bullish - bearish}."
        )
        return {
            "reason": reason,
            "action": "Wait for demand hold. Do not chase highs.",
            "longs_allowed": "Yes", "shorts_allowed": "No", "warning": None,
        }
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
        short = (
            k.replace("MGC ", "")
             .replace(" ZONE", "")
             .replace("CONFIRMED", "CONF")
             .replace("NEW SUPPLY", "NEW SUP")
             .replace("NEW DEMAND", "NEW DEM")
        )
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
                         recommendation, why, plan, color):
    if not DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL not set — skipping")
        return

    bias_emoji = {"Bullish": "🟢", "Bearish": "🔴", "Choppy": "🟡"}.get(bias, "⚪")
    rec_emoji  = RECOMMENDATION_EMOJI.get(recommendation, "")
    ticker     = alert_data.get("ticker") or "MGC"
    price      = alert_data.get("price")
    price_str  = f"${price}" if price is not None else "—"

    window_fields = []
    for label, minutes in TIME_WINDOWS.items():
        w_counts, w_total = window_summary(minutes)
        window_fields.append({
            "name": f"🕐  {label}",
            "value": fmt_window_counts(w_counts, w_total),
            "inline": True,
        })

    fields = [
        # ── Top metrics ──
        {
            "name":   "📊  Bias",
            "value":  f"{bias_emoji} **{bias}**   Strength {strength}/10",
            "inline": True,
        },
        {
            "name":   "🎯  Confidence",
            "value":  f"**{confidence}%**",
            "inline": True,
        },
        {
            "name":   "⚡  Edge Score",
            "value":  f"**{edge_score} / 100**",
            "inline": True,
        },
        # ── Recommendation ──
        {
            "name":   "📣  Recommendation",
            "value":  f"{rec_emoji} **{recommendation}**",
            "inline": True,
        },
        {
            "name":   "🏆  Trade Quality",
            "value":  f"**{quality}** — {QUALITY_LABELS.get(quality, '')}",
            "inline": True,
        },
        {
            "name":   "🔢  Score",
            "value":  f"Bull `{bullish}` · Bear `{bearish}` · Gap `{abs(bullish - bearish)}`",
            "inline": True,
        },
        # ── Why ──
        {
            "name":   "💬  Why",
            "value":  why,
            "inline": False,
        },
        # ── Recent Alert Summary ──
        {
            "name":   "📋  Recent Alert Summary",
            "value":  "━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "inline": False,
        },
        *window_fields,
        # ── Action ──
        {
            "name":   "🗺️  Action",
            "value":  plan["action"],
            "inline": False,
        },
        {
            "name":   "🟩  Longs Allowed",
            "value":  plan["longs_allowed"],
            "inline": True,
        },
        {
            "name":   "🟥  Shorts Allowed",
            "value":  plan["shorts_allowed"],
            "inline": True,
        },
    ]

    if plan["warning"]:
        fields.append({
            "name": "⚠️  Warning", "value": plan["warning"], "inline": False,
        })

    embed = {
        "title":       "MGC Agent v4",
        "description": f"**{ticker}** · {price_str} · `{alert_data.get('alert_type', '—')}`",
        "color":       color,
        "fields":      fields,
        "footer":      {"text": f"Received {alert_data.get('timestamp', '')}"},
        "timestamp":   now_utc().isoformat(),
    }

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
        resp.raise_for_status()
        logger.info("Discord sent (status %s)", resp.status_code)
    except requests.RequestException as exc:
        logger.error("Discord send failed: %s", exc)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def full_analysis():
    bullish, bearish, counts = calculate_scores()
    bias, strength           = calculate_bias(bullish, bearish)
    confidence               = calculate_confidence(bullish, bearish)
    quality                  = calculate_trade_quality(bias, confidence, bullish, bearish)
    edge_score               = calculate_edge_score(bias, confidence, strength)
    recommendation           = calculate_recommendation(bias, confidence)
    why                      = build_why(bias, confidence, strength, bullish, bearish, counts, recommendation)
    plan                     = build_trade_plan(bias, strength, bullish, bearish, counts)
    color                    = bias_color(bias)
    return (bullish, bearish, counts, bias, strength, confidence,
            quality, edge_score, recommendation, why, plan, color)


@app.route("/webhook", methods=["POST"])
def webhook():
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

    record = {
        "alert_type": normalized,
        "ticker":     data.get("ticker"),
        "price":      data.get("price"),
        "timestamp":  now_utc().isoformat(),
        "raw":        data,
    }
    ALERT_HISTORY.append(record)

    (bullish, bearish, counts, bias, strength, confidence,
     quality, edge_score, recommendation, why, plan, color) = full_analysis()

    send_discord_message(record, bias, strength, bullish, bearish,
                         confidence, quality, edge_score,
                         recommendation, why, plan, color)

    logger.info(
        "Alert: %s | %s (%d/10) | %d%% | Edge %d | %s | Quality %s",
        normalized, bias, strength, confidence, edge_score, recommendation, quality,
    )

    return jsonify({
        "status":         "ok",
        "alert_type":     normalized,
        "bias":           bias,
        "strength":       strength,
        "confidence":     f"{confidence}%",
        "edge_score":     edge_score,
        "recommendation": recommendation,
        "why":            why,
        "trade_quality":  quality,
        "bullish_score":  bullish,
        "bearish_score":  bearish,
        "longs_allowed":  plan["longs_allowed"],
        "shorts_allowed": plan["shorts_allowed"],
        "action":         plan["action"],
        "total_alerts":   len(ALERT_HISTORY),
    }), 200


@app.route("/alerts", methods=["GET"])
def get_alerts():
    return jsonify({"alerts": list(ALERT_HISTORY), "count": len(ALERT_HISTORY)}), 200


@app.route("/status", methods=["GET"])
def status():
    (bullish, bearish, counts, bias, strength, confidence,
     quality, edge_score, recommendation, why, plan, _) = full_analysis()

    windows = {}
    for label, minutes in TIME_WINDOWS.items():
        w_counts, w_total = window_summary(minutes)
        w_bull, w_bear, _ = score_alerts(alerts_in_window(minutes))
        windows[label] = {
            "alert_counts":  w_counts,
            "total":         w_total,
            "bullish_score": w_bull,
            "bearish_score": w_bear,
        }

    return jsonify({
        "status":              "running",
        "version":             "4.0",
        "bias":                bias,
        "strength":            f"{strength}/10",
        "confidence":          f"{confidence}%",
        "edge_score":          edge_score,
        "recommendation":      recommendation,
        "why":                 why,
        "trade_quality":       quality,
        "trade_quality_label": QUALITY_LABELS.get(quality, ""),
        "bullish_score":       bullish,
        "bearish_score":       bearish,
        "longs_allowed":       plan["longs_allowed"],
        "shorts_allowed":      plan["shorts_allowed"],
        "action":              plan["action"],
        "warning":             plan["warning"],
        "alert_counts":        counts,
        "windows":             windows,
        "total_alerts_stored": len(ALERT_HISTORY),
        "discord_configured":  bool(DISCORD_WEBHOOK_URL),
    }), 200


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service":     "TradingView Webhook Server",
        "version":     "4.0",
        "alert_types": list(ALERT_TYPES.keys()),
        "endpoints":   {
            "POST /webhook": "Receive TradingView alerts",
            "GET /alerts":   "View last 100 stored alerts",
            "GET /status":   "Full analysis: bias, confidence, edge score, recommendation",
        },
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
