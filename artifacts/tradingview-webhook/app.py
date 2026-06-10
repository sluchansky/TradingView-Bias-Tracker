import os
import json
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
    "MGC NEW SUPPLY ZONE": {"side": "bearish", "score": 1},
    "MGC SUPPLY ZONE CONFIRMED": {"side": "bearish", "score": 2},
    "MGC NEW DEMAND ZONE": {"side": "bullish", "score": 1},
    "MGC DEMAND ZONE CONFIRMED": {"side": "bullish", "score": 2},
}

BIAS_THRESHOLD = 3

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")


def calculate_bias():
    bullish_score = 0
    bearish_score = 0

    for alert in ALERT_HISTORY:
        alert_type = alert.get("alert_type", "").upper()
        if alert_type in ALERT_TYPES:
            info = ALERT_TYPES[alert_type]
            if info["side"] == "bullish":
                bullish_score += info["score"]
            else:
                bearish_score += info["score"]

    if bearish_score - bullish_score >= BIAS_THRESHOLD:
        bias = "Bearish"
        color = 0xFF0000
    elif bullish_score - bearish_score >= BIAS_THRESHOLD:
        bias = "Bullish"
        color = 0x00FF00
    else:
        bias = "Choppy"
        color = 0xFFFF00

    return bias, bullish_score, bearish_score, color


def send_discord_message(alert_data, bias, bullish_score, bearish_score, color):
    if not DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL not set — skipping Discord notification")
        return

    alert_type = alert_data.get("alert_type", "Unknown")
    timestamp = alert_data.get("timestamp", "")

    bias_emoji = {"Bullish": "🟢", "Bearish": "🔴", "Choppy": "🟡"}.get(bias, "⚪")

    embed = {
        "title": f"TradingView Alert — {alert_type}",
        "color": color,
        "fields": [
            {"name": "Market Bias", "value": f"{bias_emoji} **{bias}**", "inline": True},
            {"name": "Bullish Score", "value": str(bullish_score), "inline": True},
            {"name": "Bearish Score", "value": str(bearish_score), "inline": True},
            {"name": "Total Alerts Tracked", "value": str(len(ALERT_HISTORY)), "inline": True},
        ],
        "footer": {"text": f"Received at {timestamp}"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if "ticker" in alert_data:
        embed["fields"].insert(0, {"name": "Ticker", "value": alert_data["ticker"], "inline": True})
    if "price" in alert_data:
        embed["fields"].insert(1, {"name": "Price", "value": str(alert_data["price"]), "inline": True})

    payload = {"embeds": [embed]}

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Discord notification sent (status %s)", resp.status_code)
    except requests.RequestException as exc:
        logger.error("Failed to send Discord notification: %s", exc)


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        data = {}

    raw_body = request.get_data(as_text=True)
    if not data and raw_body:
        data = {"alert_type": raw_body.strip()}

    alert_type = data.get("alert_type") or data.get("message") or data.get("text") or ""
    normalized = alert_type.strip().upper()

    if normalized not in ALERT_TYPES:
        logger.warning("Unrecognized alert type received: %r", alert_type)
        return jsonify({"status": "ignored", "reason": "unrecognized alert type", "received": alert_type}), 200

    record = {
        "alert_type": normalized,
        "ticker": data.get("ticker"),
        "price": data.get("price"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "raw": data,
    }
    ALERT_HISTORY.append(record)

    bias, bullish_score, bearish_score, color = calculate_bias()
    send_discord_message(record, bias, bullish_score, bearish_score, color)

    logger.info(
        "Alert processed: %s | Bias: %s | Bull: %d | Bear: %d",
        normalized, bias, bullish_score, bearish_score,
    )

    return jsonify({
        "status": "ok",
        "alert_type": normalized,
        "bias": bias,
        "bullish_score": bullish_score,
        "bearish_score": bearish_score,
        "total_alerts": len(ALERT_HISTORY),
    }), 200


@app.route("/alerts", methods=["GET"])
def get_alerts():
    return jsonify({
        "alerts": list(ALERT_HISTORY),
        "count": len(ALERT_HISTORY),
    }), 200


@app.route("/status", methods=["GET"])
def status():
    bias, bullish_score, bearish_score, _ = calculate_bias()

    counts = {k: 0 for k in ALERT_TYPES}
    for alert in ALERT_HISTORY:
        t = alert.get("alert_type", "")
        if t in counts:
            counts[t] += 1

    return jsonify({
        "status": "running",
        "bias": bias,
        "bullish_score": bullish_score,
        "bearish_score": bearish_score,
        "alert_counts": counts,
        "total_alerts_stored": len(ALERT_HISTORY),
        "discord_configured": bool(DISCORD_WEBHOOK_URL),
    }), 200


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "TradingView Webhook Server",
        "endpoints": {
            "POST /webhook": "Receive TradingView alerts",
            "GET /alerts": "View last 100 stored alerts",
            "GET /status": "Current bias and score summary",
        },
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
