import os
import logging
from collections import deque
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ALERT_HISTORY = deque(maxlen=100)

ALERT_TYPES = {
    "MGC NEW SUPPLY ZONE":        {"side": "bearish", "score": 1},
    "MGC SUPPLY ZONE CONFIRMED":  {"side": "bearish", "score": 2},
    "MGC NEW DEMAND ZONE":        {"side": "bullish", "score": 1},
    "MGC DEMAND ZONE CONFIRMED":  {"side": "bullish", "score": 2},
}

BIAS_THRESHOLD = 3

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")


# ---------------------------------------------------------------------------
# Scoring & bias
# ---------------------------------------------------------------------------

def calculate_scores():
    bullish = 0
    bearish = 0
    counts = {k: 0 for k in ALERT_TYPES}
    for alert in ALERT_HISTORY:
        t = alert.get("alert_type", "")
        if t in ALERT_TYPES:
            counts[t] += 1
            if ALERT_TYPES[t]["side"] == "bullish":
                bullish += ALERT_TYPES[t]["score"]
            else:
                bearish += ALERT_TYPES[t]["score"]
    return bullish, bearish, counts


def calculate_bias(bullish, bearish):
    gap = abs(bullish - bearish)
    # Strength 1-10: gap 0→1, gap 1→2, ..., gap 9+→10
    strength = min(gap + 1, 10)

    if bearish - bullish >= BIAS_THRESHOLD:
        return "Bearish", strength
    elif bullish - bearish >= BIAS_THRESHOLD:
        return "Bullish", strength
    else:
        return "Choppy", strength


# ---------------------------------------------------------------------------
# Trade plan generator
# ---------------------------------------------------------------------------

def build_trade_plan(bias, strength, bullish, bearish, counts):
    if bias == "Bearish":
        confirmed = counts["MGC SUPPLY ZONE CONFIRMED"]
        new_zones  = counts["MGC NEW SUPPLY ZONE"]
        parts = []
        if confirmed:
            parts.append(f"{confirmed} supply zone(s) confirmed")
        if new_zones:
            parts.append(f"{new_zones} new supply zone(s) identified")
        reason = (
            ", ".join(parts) + f". Bearish score ({bearish}) exceeds bullish ({bullish}) "
            f"by {bearish - bullish} point(s)."
        ) if parts else (
            f"Bearish score ({bearish}) exceeds bullish ({bullish}) by {bearish - bullish} point(s)."
        )
        action        = "Wait for retest short. Do not chase lows."
        longs_allowed = "No"
        shorts_allowed = "Yes"
        warning       = None

    elif bias == "Bullish":
        confirmed  = counts["MGC DEMAND ZONE CONFIRMED"]
        new_zones  = counts["MGC NEW DEMAND ZONE"]
        parts = []
        if confirmed:
            parts.append(f"{confirmed} demand zone(s) confirmed")
        if new_zones:
            parts.append(f"{new_zones} new demand zone(s) identified")
        reason = (
            ", ".join(parts) + f". Bullish score ({bullish}) exceeds bearish ({bearish}) "
            f"by {bullish - bearish} point(s)."
        ) if parts else (
            f"Bullish score ({bullish}) exceeds bearish ({bearish}) by {bullish - bearish} point(s)."
        )
        action        = "Wait for demand hold. Do not chase highs."
        longs_allowed = "Yes"
        shorts_allowed = "No"
        warning       = None

    else:  # Choppy
        reason = (
            f"Supply and demand scores are close (Bull: {bullish}, Bear: {bearish}). "
            "No clear directional edge."
        )
        action        = "No trade. Wait for clearer supply or demand control."
        longs_allowed = "No"
        shorts_allowed = "No"
        warning       = "Market is choppy. Standing aside is a valid position."

    return {
        "reason":         reason,
        "action":         action,
        "longs_allowed":  longs_allowed,
        "shorts_allowed": shorts_allowed,
        "warning":        warning,
    }


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------

def send_discord_message(alert_data, bias, strength, bullish, bearish, plan, color):
    if not DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL not set — skipping Discord notification")
        return

    bias_emoji = {"Bullish": "🟢", "Bearish": "🔴", "Choppy": "🟡"}.get(bias, "⚪")
    ticker = alert_data.get("ticker") or "MGC"
    price  = alert_data.get("price")
    price_str = f"${price}" if price is not None else "—"

    description_lines = [
        f"**Ticker:** {ticker}   **Price:** {price_str}",
        f"**Alert:** {alert_data.get('alert_type', '—')}",
    ]

    fields = [
        # ── Bias ──
        {
            "name": "📊  Bias",
            "value": f"{bias_emoji} **{bias}**   (Strength {strength}/10)",
            "inline": False,
        },
        # ── Score ──
        {
            "name": "🔢  Score",
            "value": f"Bullish `{bullish}` · Bearish `{bearish}` · Gap `{abs(bullish - bearish)}`",
            "inline": False,
        },
        # ── Reason ──
        {
            "name": "🧠  Reason",
            "value": plan["reason"],
            "inline": False,
        },
        # ── Action ──
        {
            "name": "⚡  Action",
            "value": plan["action"],
            "inline": False,
        },
        # ── Longs / Shorts ──
        {
            "name": "🟩  Longs Allowed",
            "value": plan["longs_allowed"],
            "inline": True,
        },
        {
            "name": "🟥  Shorts Allowed",
            "value": plan["shorts_allowed"],
            "inline": True,
        },
    ]

    if plan["warning"]:
        fields.append({
            "name": "⚠️  Warning",
            "value": plan["warning"],
            "inline": False,
        })

    embed = {
        "title": "MGC Agent Readout",
        "description": "\n".join(description_lines),
        "color": color,
        "fields": fields,
        "footer": {"text": f"Received at {alert_data.get('timestamp', '')}"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        resp = requests.post(
            DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10
        )
        resp.raise_for_status()
        logger.info("Discord notification sent (status %s)", resp.status_code)
    except requests.RequestException as exc:
        logger.error("Failed to send Discord notification: %s", exc)


# ---------------------------------------------------------------------------
# Color helper
# ---------------------------------------------------------------------------

def bias_color(bias):
    return {"Bearish": 0xFF0000, "Bullish": 0x00FF00, "Choppy": 0xFFFF00}.get(bias, 0x888888)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        data = {}

    raw_body = request.get_data(as_text=True)
    if not data and raw_body:
        data = {"alert_type": raw_body.strip()}

    alert_type = (
        data.get("alert_type") or data.get("message") or data.get("text") or ""
    )
    normalized = alert_type.strip().upper()

    if normalized not in ALERT_TYPES:
        logger.warning("Unrecognized alert type received: %r", alert_type)
        return jsonify({
            "status": "ignored",
            "reason": "unrecognized alert type",
            "received": alert_type,
        }), 200

    record = {
        "alert_type": normalized,
        "ticker":     data.get("ticker"),
        "price":      data.get("price"),
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "raw":        data,
    }
    ALERT_HISTORY.append(record)

    bullish, bearish, counts = calculate_scores()
    bias, strength           = calculate_bias(bullish, bearish)
    plan                     = build_trade_plan(bias, strength, bullish, bearish, counts)
    color                    = bias_color(bias)

    send_discord_message(record, bias, strength, bullish, bearish, plan, color)

    logger.info(
        "Alert: %s | Bias: %s (%d/10) | Bull: %d | Bear: %d",
        normalized, bias, strength, bullish, bearish,
    )

    return jsonify({
        "status":         "ok",
        "alert_type":     normalized,
        "bias":           bias,
        "strength":       strength,
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
    bullish, bearish, counts = calculate_scores()
    bias, strength           = calculate_bias(bullish, bearish)
    plan                     = build_trade_plan(bias, strength, bullish, bearish, counts)

    return jsonify({
        "status":           "running",
        "version":          "2.0",
        "bias":             bias,
        "strength":         f"{strength}/10",
        "bullish_score":    bullish,
        "bearish_score":    bearish,
        "longs_allowed":    plan["longs_allowed"],
        "shorts_allowed":   plan["shorts_allowed"],
        "action":           plan["action"],
        "warning":          plan["warning"],
        "alert_counts":     counts,
        "total_alerts_stored": len(ALERT_HISTORY),
        "discord_configured":  bool(DISCORD_WEBHOOK_URL),
    }), 200


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "TradingView Webhook Server",
        "version": "2.0",
        "endpoints": {
            "POST /webhook": "Receive TradingView alerts",
            "GET /alerts":   "View last 100 stored alerts",
            "GET /status":   "Current bias, strength, and trade plan",
        },
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
