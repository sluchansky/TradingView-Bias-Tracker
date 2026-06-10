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

TIME_WINDOWS = {
    "15m":  15,
    "60m":  60,
    "120m": 120,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_utc():
    return datetime.now(timezone.utc)


def alerts_in_window(minutes):
    cutoff = now_utc() - timedelta(minutes=minutes)
    result = []
    for alert in ALERT_HISTORY:
        ts = alert.get("timestamp", "")
        try:
            parsed = datetime.fromisoformat(ts)
            if parsed >= cutoff:
                result.append(alert)
        except (ValueError, TypeError):
            pass
    return result


def window_summary(minutes):
    alerts = alerts_in_window(minutes)
    counts = {k: 0 for k in ALERT_TYPES}
    for a in alerts:
        t = a.get("alert_type", "")
        if t in counts:
            counts[t] += 1
    total = sum(counts.values())
    return counts, total


def score_alerts(alerts):
    bullish = 0
    bearish = 0
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
    else:
        return "Choppy", strength


def calculate_confidence(bullish, bearish):
    total = bullish + bearish
    if total == 0:
        return 0
    dominant = max(bullish, bearish)
    return round((dominant / total) * 100)


def calculate_trade_quality(bias, confidence, bullish, bearish):
    total = bullish + bearish
    if total == 0:
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


QUALITY_LABELS = {
    "A+": "Strong trend",
    "A":  "Trend",
    "B":  "Tradable",
    "C":  "Choppy",
    "D":  "Avoid",
}


# ---------------------------------------------------------------------------
# Trade plan
# ---------------------------------------------------------------------------

def build_trade_plan(bias, strength, bullish, bearish, counts):
    if bias == "Bearish":
        parts = []
        if counts.get("CHOCH SUPPLY", 0):
            parts.append(f"CHoCH supply break ({counts['CHOCH SUPPLY']}×)")
        if counts.get("BOS SUPPLY", 0):
            parts.append(f"BOS supply ({counts['BOS SUPPLY']}×)")
        if counts.get("MGC SUPPLY ZONE CONFIRMED", 0):
            parts.append(f"supply zone confirmed ({counts['MGC SUPPLY ZONE CONFIRMED']}×)")
        if counts.get("MGC NEW SUPPLY ZONE", 0):
            parts.append(f"new supply zone ({counts['MGC NEW SUPPLY ZONE']}×)")
        reason = (
            (", ".join(parts) + f". Bearish {bearish} vs bullish {bullish}.")
            if parts else
            f"Bearish score ({bearish}) exceeds bullish ({bullish}) by {bearish - bullish}."
        )
        return {
            "reason":         reason,
            "action":         "Wait for retest short. Do not chase lows.",
            "longs_allowed":  "No",
            "shorts_allowed": "Yes",
            "warning":        None,
        }

    elif bias == "Bullish":
        parts = []
        if counts.get("CHOCH DEMAND", 0):
            parts.append(f"CHoCH demand break ({counts['CHOCH DEMAND']}×)")
        if counts.get("BOS DEMAND", 0):
            parts.append(f"BOS demand ({counts['BOS DEMAND']}×)")
        if counts.get("MGC DEMAND ZONE CONFIRMED", 0):
            parts.append(f"demand zone confirmed ({counts['MGC DEMAND ZONE CONFIRMED']}×)")
        if counts.get("MGC NEW DEMAND ZONE", 0):
            parts.append(f"new demand zone ({counts['MGC NEW DEMAND ZONE']}×)")
        reason = (
            (", ".join(parts) + f". Bullish {bullish} vs bearish {bearish}.")
            if parts else
            f"Bullish score ({bullish}) exceeds bearish ({bearish}) by {bullish - bearish}."
        )
        return {
            "reason":         reason,
            "action":         "Wait for demand hold. Do not chase highs.",
            "longs_allowed":  "Yes",
            "shorts_allowed": "No",
            "warning":        None,
        }

    else:
        return {
            "reason":         f"Supply and demand scores are close (Bull: {bullish}, Bear: {bearish}). No clear edge.",
            "action":         "No trade. Wait for clearer supply or demand control.",
            "longs_allowed":  "No",
            "shorts_allowed": "No",
            "warning":        "Market is choppy. Standing aside is a valid position.",
        }


# ---------------------------------------------------------------------------
# Window summary string (for Discord)
# ---------------------------------------------------------------------------

def fmt_window_counts(counts, total):
    if total == 0:
        return "No alerts"
    parts = []
    # Group by side
    bear_parts = []
    bull_parts = []
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
        if ALERT_TYPES[k]["side"] == "bearish":
            bear_parts.append(f"{short} ×{v}")
        else:
            bull_parts.append(f"{short} ×{v}")
    if bear_parts:
        parts.append("🔴 " + ", ".join(bear_parts))
    if bull_parts:
        parts.append("🟢 " + ", ".join(bull_parts))
    return "\n".join(parts) if parts else "No alerts"


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------

def bias_color(bias):
    return {"Bearish": 0xFF3333, "Bullish": 0x33CC66, "Choppy": 0xFFCC00}.get(bias, 0x888888)


def send_discord_message(alert_data, bias, strength, bullish, bearish,
                         confidence, quality, plan, color):
    if not DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL not set — skipping Discord notification")
        return

    bias_emoji = {"Bullish": "🟢", "Bearish": "🔴", "Choppy": "🟡"}.get(bias, "⚪")
    ticker    = alert_data.get("ticker") or "MGC"
    price     = alert_data.get("price")
    price_str = f"${price}" if price is not None else "—"
    quality_label = QUALITY_LABELS.get(quality, "")

    # Build time-window summary fields
    window_fields = []
    for label, minutes in TIME_WINDOWS.items():
        w_counts, w_total = window_summary(minutes)
        window_fields.append({
            "name":   f"🕐  {label} Window",
            "value":  fmt_window_counts(w_counts, w_total),
            "inline": True,
        })

    fields = [
        # ── Header row ──
        {
            "name":   "📊  Bias",
            "value":  f"{bias_emoji} **{bias}**   (Strength {strength}/10)",
            "inline": True,
        },
        {
            "name":   "🎯  Confidence",
            "value":  f"**{confidence}%**",
            "inline": True,
        },
        {
            "name":   "🏆  Trade Quality",
            "value":  f"**{quality}** — {quality_label}",
            "inline": True,
        },
        # ── Score ──
        {
            "name":   "🔢  Score",
            "value":  f"Bullish `{bullish}` · Bearish `{bearish}` · Gap `{abs(bullish - bearish)}`",
            "inline": False,
        },
        # ── Recent Alert Summary ──
        {
            "name":   "📋  Recent Alert Summary",
            "value":  "━━━━━━━━━━━━━━━━━━━━━",
            "inline": False,
        },
        *window_fields,
        # ── Reason ──
        {
            "name":   "🧠  Reason",
            "value":  plan["reason"],
            "inline": False,
        },
        # ── Action ──
        {
            "name":   "⚡  Action",
            "value":  plan["action"],
            "inline": False,
        },
        # ── Longs / Shorts ──
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
            "name":   "⚠️  Warning",
            "value":  plan["warning"],
            "inline": False,
        })

    embed = {
        "title":       "MGC Agent v3",
        "description": f"**Ticker:** {ticker}   **Price:** {price_str}\n**Alert:** {alert_data.get('alert_type', '—')}",
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
        logger.warning("Unrecognized alert type: %r", alert_type)
        return jsonify({
            "status":   "ignored",
            "reason":   "unrecognized alert type",
            "received": alert_type,
        }), 200

    record = {
        "alert_type": normalized,
        "ticker":     data.get("ticker"),
        "price":      data.get("price"),
        "timestamp":  now_utc().isoformat(),
        "raw":        data,
    }
    ALERT_HISTORY.append(record)

    bullish, bearish, counts = calculate_scores()
    bias, strength           = calculate_bias(bullish, bearish)
    confidence               = calculate_confidence(bullish, bearish)
    quality                  = calculate_trade_quality(bias, confidence, bullish, bearish)
    plan                     = build_trade_plan(bias, strength, bullish, bearish, counts)
    color                    = bias_color(bias)

    send_discord_message(record, bias, strength, bullish, bearish,
                         confidence, quality, plan, color)

    logger.info(
        "Alert: %s | %s (%d/10) | %d%% conf | Quality: %s | Bull: %d Bear: %d",
        normalized, bias, strength, confidence, quality, bullish, bearish,
    )

    return jsonify({
        "status":         "ok",
        "alert_type":     normalized,
        "bias":           bias,
        "strength":       strength,
        "confidence":     f"{confidence}%",
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
    bullish, bearish, counts = calculate_scores()
    bias, strength           = calculate_bias(bullish, bearish)
    confidence               = calculate_confidence(bullish, bearish)
    quality                  = calculate_trade_quality(bias, confidence, bullish, bearish)
    plan                     = build_trade_plan(bias, strength, bullish, bearish, counts)

    windows = {}
    for label, minutes in TIME_WINDOWS.items():
        w_counts, w_total = window_summary(minutes)
        w_bull, w_bear, _ = score_alerts(alerts_in_window(minutes))
        windows[label] = {
            "alert_counts": w_counts,
            "total":        w_total,
            "bullish_score": w_bull,
            "bearish_score": w_bear,
        }

    return jsonify({
        "status":              "running",
        "version":             "3.0",
        "bias":                bias,
        "strength":            f"{strength}/10",
        "confidence":          f"{confidence}%",
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
        "service": "TradingView Webhook Server",
        "version": "3.0",
        "alert_types": list(ALERT_TYPES.keys()),
        "endpoints": {
            "POST /webhook": "Receive TradingView alerts",
            "GET /alerts":   "View last 100 stored alerts",
            "GET /status":   "Full bias, confidence, quality, and window breakdown",
        },
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
