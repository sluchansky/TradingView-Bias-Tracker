import os
import logging
import threading
from collections import deque
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ALERT_HISTORY    = deque(maxlen=100)
CURRENT_PRICE    = None
ACTIVE_TRADE     = None
LAST_ALERT_AT    = None   # datetime of most recent webhook alert (UTC)
ZONE_BROKEN_AT      = None   # {"price": float, "alerts_since": int}
MITIGATED_PRICES    = []     # [{"price": float, "ts": str}]
ZONE_MITIGATED_FLAG = False  # True once any zone is mitigated; cleared on new structure or /clear

ALERT_TYPES = {
    # ── MGC alert types ────────────────────────────────────────────────────────
    "MGC NEW SUPPLY ZONE":        {"side": "bearish", "score": 1},
    "MGC SUPPLY ZONE CONFIRMED":  {"side": "bearish", "score": 2},
    "MGC NEW DEMAND ZONE":        {"side": "bullish", "score": 1},
    "MGC DEMAND ZONE CONFIRMED":  {"side": "bullish", "score": 2},
    # ── MNQ alert types ────────────────────────────────────────────────────────
    "MNQ NEW SUPPLY ZONE":        {"side": "bearish", "score": 1},
    "MNQ SUPPLY ZONE CONFIRMED":  {"side": "bearish", "score": 2},
    "MNQ NEW DEMAND ZONE":        {"side": "bullish", "score": 1},
    "MNQ DEMAND ZONE CONFIRMED":  {"side": "bullish", "score": 2},
    # ── Shared structure alerts (apply to whichever symbol is active) ──────────
    "CHOCH SUPPLY":               {"side": "bearish", "score": 3},
    "BOS SUPPLY":                 {"side": "bearish", "score": 2},
    "CHOCH DEMAND":               {"side": "bullish", "score": 3},
    "BOS DEMAND":                 {"side": "bullish", "score": 2},
    # ── Zone state alerts (neutral — side effects only, no score contribution) ─
    "MGC ZONE BROKEN":            {"side": "neutral", "score": 0},
    "MGC ZONE MITIGATED":         {"side": "neutral", "score": 0},
    "MNQ ZONE BROKEN":            {"side": "neutral", "score": 0},
    "MNQ ZONE MITIGATED":         {"side": "neutral", "score": 0},
    # Stage 4 triggers — 5m confirmation candle closed (neutral, no score)
    "MGC BULLISH CONFIRMATION":  {"side": "neutral", "score": 0},
    "MGC BEARISH CONFIRMATION":  {"side": "neutral", "score": 0},
    "MNQ BULLISH CONFIRMATION":  {"side": "neutral", "score": 0},
    "MNQ BEARISH CONFIRMATION":  {"side": "neutral", "score": 0},
    # ── Trade lifecycle commands (sent directly from TradingView strategy) ───────
    "MGC ENTER":  {"side": "command", "score": 0},
    "MNQ ENTER":  {"side": "command", "score": 0},
    "MGC CLOSE":  {"side": "command", "score": 0},
    "MNQ CLOSE":  {"side": "command", "score": 0},
}

SUPPLY_TYPES = {k for k, v in ALERT_TYPES.items() if v["side"] == "bearish"}
DEMAND_TYPES = {k for k, v in ALERT_TYPES.items() if v["side"] == "bullish"}

BIAS_THRESHOLD = 3
NEAR_PCT       = 0.005   # 0.5%  — Testing zone
EXTENDED_PCT   = 0.010   # 1.0%  — Approaching zone
WATCH_PCT      = 0.0075  # 0.75% — Watch zone (v10)

DEFAULT_ACCOUNT_SIZE = 50_000   # $50,000 — fallback when no profile/account_size given
DEFAULT_RISK_PCT     = 0.01     # 1% — fallback when no profile/risk_pct given
MGC_POINT_VALUE      = 10       # $10 per point per MGC contract (Micro Gold = 10 oz)

ACCOUNT_PROFILES = {
    "MGC Conservative": {"account_size": 50_000,  "risk_pct": 0.005},
    "MGC Standard":     {"account_size": 50_000,  "risk_pct": 0.010},
    "MNQ Conservative": {"account_size": 100_000, "risk_pct": 0.005},
    "MNQ Standard":     {"account_size": 100_000, "risk_pct": 0.010},
}
DEFAULT_PROFILE = "MGC Standard"

BOT_NAME = "🤖 AI Trading Partner"

DISCORD_WEBHOOK_URL         = os.environ.get("DISCORD_WEBHOOK_URL", "")
DISCORD_MNQ_WEBHOOK_URL     = os.environ.get("DISCORD_MNQ_WEBHOOK_URL", "")
DISCORD_JOURNAL_WEBHOOK_URL = os.environ.get("DISCORD_JOURNAL_WEBHOOK_URL", "")


def _discord_url(hint: str = "") -> str:
    """Return the correct trade-alert webhook URL based on symbol hint.

    If the hint contains 'MNQ' (case-insensitive) and a dedicated MNQ URL is
    configured, returns that; otherwise falls back to the MGC/default URL.
    """
    if "MNQ" in str(hint).upper() and DISCORD_MNQ_WEBHOOK_URL:
        return DISCORD_MNQ_WEBHOOK_URL
    return DISCORD_WEBHOOK_URL


HEARTBEAT_INTERVAL = 3600  # seconds
EOD_HOUR_UTC       = int(os.environ.get("EOD_HOUR_UTC", 21))  # default 21:00 UTC = 4 PM ET


def _send_heartbeat():
    """Post an hourly status embed to all configured trade-alert channels."""
    now = datetime.now(timezone.utc)

    # ── Last alert time ────────────────────────────────────────────────────
    if LAST_ALERT_AT:
        delta   = now - LAST_ALERT_AT
        minutes = int(delta.total_seconds() / 60)
        if minutes < 60:
            age = f"{minutes}m ago"
        else:
            age = f"{minutes // 60}h {minutes % 60}m ago"
        last_str = f"{LAST_ALERT_AT.strftime('%H:%M UTC')}  ({age})"
    else:
        last_str = "No alerts received yet"

    # ── Active trade ───────────────────────────────────────────────────────
    if ACTIVE_TRADE:
        at         = ACTIVE_TRADE
        sym        = (at.get("profile") or "").split()[0] or "—"
        direction  = at.get("direction", "—")
        entry      = at.get("entry_price", "—")
        trade_str  = f"{direction} {sym}  |  Entry `{entry}`"
        status_str = "🟢 Active Trade in Progress"
    else:
        trade_str  = "None"
        status_str = "🔵 Watching — No Active Trade"

    embed = {
        "color":       0x00BFFF,
        "author":      {"name": BOT_NAME},
        "description": "**System heartbeat — all systems operational**",
        "fields": [
            {"name": "Last alert received", "value": last_str,   "inline": False},
            {"name": "Active trade",        "value": trade_str,  "inline": True},
            {"name": "Status",              "value": status_str, "inline": True},
        ],
        "footer": {"text": now.strftime("Hourly check-in  ·  %Y-%m-%d %H:%M UTC")},
    }

    for url in filter(None, [DISCORD_WEBHOOK_URL, DISCORD_MNQ_WEBHOOK_URL]):
        try:
            requests.post(url, json={"embeds": [embed]}, timeout=5)
        except Exception:
            pass
    logger.info("Hourly heartbeat sent.")


def _heartbeat_loop():
    """Send heartbeat now then reschedule every HEARTBEAT_INTERVAL seconds."""
    _send_heartbeat()
    threading.Timer(HEARTBEAT_INTERVAL, _heartbeat_loop).start()


# ── End-of-day summary ────────────────────────────────────────────────────────

import re as _re

def _compute_eod_stats():
    """Derive today's trading stats entirely from JOURNAL."""
    today = datetime.now(timezone.utc).date().isoformat()
    entries = [e for e in JOURNAL if e.get("datetime", "")[:10] == today]

    wins   = [e for e in entries if e.get("outcome", "").startswith("Win")]
    losses = [e for e in entries if e.get("outcome", "").startswith("Loss")]
    trades_entered = len(wins) + len(losses)

    # Net P&L — prefer stored pnl_dollars, else parse from outcome string
    pnl_total, has_pnl = 0.0, False
    for e in entries:
        if "pnl_dollars" in e:
            pnl_total += e["pnl_dollars"]
            has_pnl = True
        else:
            outcome = e.get("outcome", "")
            m = _re.search(r'\+\$?([\d,]+)|-\$?([\d,]+)', outcome)
            if m:
                if m.group(1):
                    pnl_total += float(m.group(1).replace(",", ""))
                    has_pnl = True
                elif m.group(2):
                    pnl_total -= float(m.group(2).replace(",", ""))
                    has_pnl = True

    # Best = highest edge_score win; fallback any entry
    best  = max(wins,    key=lambda e: e.get("edge_score", 0), default=None) or \
            max(entries, key=lambda e: e.get("edge_score", 0), default=None)
    # Worst = most recent loss; fallback lowest edge_score closed entry
    closed = [e for e in entries if not e.get("outcome","").startswith("Pending")]
    worst = losses[0] if losses else \
            (min(closed, key=lambda e: e.get("edge_score", 99), default=None))

    return {
        "date":           today,
        "trades_flagged": len(entries),
        "trades_entered": trades_entered,
        "wins":           len(wins),
        "losses":         len(losses),
        "net_pnl":        round(pnl_total, 2) if has_pnl else None,
        "best":           best,
        "worst":          worst,
    }


def _fmt_setup(entry):
    if not entry:
        return "—"
    return (
        f"{entry.get('symbol','—')} {entry.get('direction','—')}  ·  "
        f"Edge {entry.get('edge_score','—')}  ·  {entry.get('outcome','—')}"
    )


def _send_eod_summary():
    """Post the end-of-day summary embed to all configured Discord channels."""
    stats = _compute_eod_stats()
    now   = datetime.now(timezone.utc)

    pnl_val = stats["net_pnl"]
    if pnl_val is None:
        pnl_str, color = "—", 0x95A5A6
    elif pnl_val >= 0:
        pnl_str, color = f"+${pnl_val:,.0f}", 0x2ECC71
    else:
        pnl_str, color = f"-${abs(pnl_val):,.0f}", 0xE74C3C

    embed = {
        "color":       color,
        "author":      {"name": BOT_NAME},
        "title":       "📊 End of Day Summary",
        "description": now.strftime("%A, %B %-d, %Y"),
        "fields": [
            {"name": "Trades flagged",  "value": str(stats["trades_flagged"]), "inline": True},
            {"name": "Trades entered",  "value": str(stats["trades_entered"]), "inline": True},
            {"name": "\u200b",          "value": "\u200b",                     "inline": True},
            {"name": "Wins ✅",         "value": str(stats["wins"]),           "inline": True},
            {"name": "Losses ❌",       "value": str(stats["losses"]),         "inline": True},
            {"name": "Net P&L",         "value": f"**{pnl_str}**",            "inline": True},
            {"name": "Best setup",      "value": _fmt_setup(stats["best"]),    "inline": False},
            {"name": "Worst setup",     "value": _fmt_setup(stats["worst"]),   "inline": False},
        ],
        "footer": {"text": now.strftime("EOD  ·  %Y-%m-%d %H:%M UTC")},
    }

    for url in filter(None, [DISCORD_WEBHOOK_URL, DISCORD_MNQ_WEBHOOK_URL, DISCORD_JOURNAL_WEBHOOK_URL]):
        try:
            requests.post(url, json={"embeds": [embed]}, timeout=5)
        except Exception:
            pass
    logger.info("EOD summary sent.")


def _schedule_eod():
    """Schedule the EOD summary to fire daily at EOD_HOUR_UTC:00 UTC."""
    now  = datetime.now(timezone.utc)
    fire = now.replace(hour=EOD_HOUR_UTC, minute=0, second=0, microsecond=0)
    if fire <= now:
        fire += timedelta(days=1)
    delay = (fire - now).total_seconds()
    logger.info("EOD summary scheduled for %s UTC (in %.0fs).", fire.strftime("%H:%M"), delay)

    def _run():
        _send_eod_summary()
        _schedule_eod()

    threading.Timer(delay, _run).start()


TIME_WINDOWS                = {"15m": 15, "60m": 60, "120m": 120}

# ── Trading Journal ───────────────────────────────────────────────────────────
JOURNAL        = []          # list of journal dicts, newest-first, max 500
JOURNAL_KEYS   = set()       # dedup: (ticker, setup_stage, entry_zone_rounded)
JOURNAL_STAGES = frozenset(("Setup Forming", "Confirmation Candle", "Trade Ready"))


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
        # No CHOCH at all — check BOS levels for Attempt classification
        if bos_dem and bos_sup:
            structure = "Bullish Attempt" if current_price >= bos_dem else "Bearish Attempt"
            parts.append(
                f"BOS Demand ({bos_dem:.2f}) & Supply ({bos_sup:.2f}) detected — no CHOCH yet"
            )
        elif bos_dem:
            structure = "Bullish Attempt"
            parts.append(f"BOS Demand at {bos_dem:.2f} — no CHOCH yet. Monitoring for bullish confirmation.")
        elif bos_sup:
            structure = "Bearish Attempt"
            parts.append(f"BOS Supply at {bos_sup:.2f} — no CHOCH yet. Monitoring for bearish confirmation.")
        else:
            structure = "Undefined"
            parts.append("No CHOCH or BOS levels tracked yet")
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
        "Bullish Attempt":   "Bullish Attempt",
        "Bearish Attempt":   "Bearish Attempt",
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

    # ── Gate: Attempt structures (BOS fired, no CHOCH yet) → always WAIT ──
    if structure_class in ("Bullish Attempt", "Bearish Attempt"):
        chain += ["No CHOCH — Attempt Only", "Waiting for Confirmation", "WAIT"]
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
    if structure_label in ("Bearish Structure", "Bearish Breakdown", "Bearish Attempt"):
        return "Bearish"
    if structure_label in ("Bullish Structure", "Bullish Breakout", "Bullish Attempt"):
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

    # ── 1b. Attempt structures: BOS confirmed but no CHOCH yet ──
    if structure_label == "Bullish Attempt":
        bos_dem = last_price_by_type.get("BOS DEMAND")
        bos_str = f"${bos_dem:.2f}" if bos_dem else "—"
        return ("Watch Demand",
                f"BOS Demand ({bos_str}) confirmed — no CHOCH yet. Watch demand zone for hold.",
                "Wait for CHOCH Demand + Zone Confirmed to trigger Long Ready.",
                f"Price closes below BOS Demand level ({bos_str})")
    if structure_label == "Bearish Attempt":
        bos_sup = last_price_by_type.get("BOS SUPPLY")
        bos_str = f"${bos_sup:.2f}" if bos_sup else "—"
        return ("Watch Supply",
                f"BOS Supply ({bos_str}) confirmed — no CHOCH yet. Watch supply zone for rejection.",
                "Wait for CHOCH Supply + Zone Confirmed to trigger Short Ready.",
                f"Price closes above BOS Supply level ({bos_str})")

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


def calculate_position_sizing(trade_plan, account_size, risk_pct, profile_name=""):
    """
    Compute MGC position sizing from a generated trade plan.

    MGC (Micro Gold) = 10 troy oz. Point value = $10 per contract per point.

    Returns a dict of display strings, or {} if no trade plan exists.
    """
    if not trade_plan.get("trade_plan"):
        return {}
    try:
        # Parse entry midpoint from "lo–hi" zone string
        lo_s, hi_s    = trade_plan["entry_zone"].split("–")
        entry_mid      = (float(lo_s) + float(hi_s)) / 2
        stop           = float(trade_plan["stop_loss"])
        t1             = float(trade_plan["target1"])
        t2             = float(trade_plan["target2"])

        stop_dist      = abs(entry_mid - stop)
        t1_dist        = abs(t1 - entry_mid)
        t2_dist        = abs(t2 - entry_mid)

        if stop_dist == 0:
            return {}

        dollar_risk         = account_size * risk_pct
        risk_per_contract   = stop_dist * MGC_POINT_VALUE
        contracts           = max(1, int(dollar_risk / risk_per_contract))
        max_loss            = contracts * risk_per_contract
        profit_t1           = contracts * t1_dist * MGC_POINT_VALUE
        profit_t2           = contracts * t2_dist * MGC_POINT_VALUE

        return {
            "profile":            profile_name or "Custom",
            "account_size":       f"${account_size:,.0f}",
            "risk_per_trade":     f"{risk_pct * 100:.2f}%",
            "dollar_risk":        f"${dollar_risk:,.0f}",
            "risk_per_contract":  f"${risk_per_contract:,.0f}",
            "contracts":          str(contracts),
            "max_loss":           f"${max_loss:,.0f}",
            "profit_t1":          f"${profit_t1:,.0f}",
            "profit_t2":          f"${profit_t2:,.0f}",
        }
    except (ValueError, TypeError, ZeroDivisionError):
        return {}


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
    if verdict in ("LONG READY", "SHORT READY"):
        zone_side = "demand zone" if verdict == "LONG READY" else "supply zone"
        return (f"Confidence {confidence}%. {signal_text}. Price at {zone_side} — "
                f"entry setup ready. Score {score_dom} vs {score_opp}.")
    if verdict == "WATCH":
        return (f"Confidence {confidence}%. {signal_text}. Setup forming — "
                f"wait for entry confirmation. Score {score_dom} vs {score_opp}.")
    return (f"Confidence {confidence}%. Signals present ({signal_text}) — "
            f"insufficient edge for entry. Score {score_dom} vs {score_opp}.")


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
        "SHORT READY":  0xDD2222,
        "WATCH":        0xFFAA00,
        "WAIT":         0x888888,
        "LONG READY":   0x00CC44,
        "LONG BIAS":    0x55CC55,
        "STRONG LONG":  0x00AA00,
    }.get(verdict, 0x888888)

VERDICT_EMOJI = {
    "STRONG SHORT": "🔴🔴",
    "SHORT BIAS":   "🔴",
    "SHORT READY":  "🔴✅",
    "WATCH":        "👁️",
    "WAIT":         "⏸️",
    "LONG READY":   "🟢✅",
    "LONG BIAS":    "🟢",
    "STRONG LONG":  "🟢🟢",
}

RECOMMENDATION_EMOJI = {
    "HIGH CONVICTION TRADE": "🔥",
    "TRADE":                 "✅",
    "WATCH":                 "👀",
    "WAIT":                  "⏸️",
}


def _trade_plan_fields(tp, sizing=None):
    """Return Discord embed field dicts for the trade plan + position sizing."""
    if not tp["trade_plan"]:
        return [
            {
                "name":   "📋  Trade Plan",
                "value":  f"**No trade plan generated.**\nReason: {tp['reason']}",
                "inline": False,
            }
        ]
    direction_emoji = "🟢" if tp["direction"] == "Long" else "🔴"
    fields = [
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
    if sizing:
        fields.append({
            "name": "💰  Position Sizing",
            "value": (
                f"**Profile:** {sizing['profile']}  ·  "
                f"**Account:** {sizing['account_size']}  ·  "
                f"**Risk:** {sizing['risk_per_trade']}  ·  "
                f"**Dollar Risk:** {sizing['dollar_risk']}\n"
                f"**Risk/Contract:** {sizing['risk_per_contract']}  ·  "
                f"**Contracts:** {sizing['contracts']}  ·  "
                f"**Max Loss:** {sizing['max_loss']}\n"
                f"**T1 Profit:** {sizing['profit_t1']}  ·  "
                f"**T2 Profit:** {sizing['profit_t2']}"
            ),
            "inline": False,
        })
    return fields


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
    """Return Discord embed fields for Setup Stage section. Max 3 fields."""
    emoji  = _STAGE_EMOJI.get(setup_stage, "⭕")
    number = _STAGE_NUMBER.get(setup_stage)
    label  = f"Stage {number} — {setup_stage}" if number else setup_stage
    rules  = f"**Entry:** {entry_rule}\n**Invalidation:** {stage_invalidation}"
    return [
        {"name": "🎯  Setup Stage", "value": f"{emoji} **{label}**", "inline": True},
        {"name": "➡️  Next Step",   "value": next_step,              "inline": False},
        {"name": "📋  Rules",       "value": rules,                  "inline": False},
    ]


def send_zone_mitigated_message(alert_data, mitigated_price):
    """Minimal Discord embed for zone-consumed state — no scoring performed."""
    ticker    = alert_data.get("ticker") or "MGC"
    _url      = _discord_url(ticker)
    if not _url:
        logger.warning("DISCORD_WEBHOOK_URL not set — skipping")
        return

    price     = alert_data.get("price")
    price_str = f"${float(price):.2f}"  if price           is not None else "—"
    mz_str    = f"${float(mitigated_price):.2f}" if mitigated_price is not None else "—"

    embed = {
        "type":        "rich",
        "author":      {"name": BOT_NAME},
        "title":       "⏸ ZONE MITIGATED",
        "description": f"**{ticker}** · {price_str} · Zone consumed — no trade setup available",
        "color":       0xFFAA00,
        "fields": [
            {
                "name":   "🎯  Final Verdict",
                "value":  "⏸ **WAIT**",
                "inline": True,
            },
            {
                "name":   "🚫  Zone Status",
                "value":  f"**Consumed / Mitigated**\nMitigated at `{mz_str}`",
                "inline": True,
            },
            {
                "name":   "🗺️  Action",
                "value":  "Wait for fresh supply or demand zone.",
                "inline": False,
            },
            {
                "name":   "💬  Reason",
                "value":  (
                    "Zone already reacted and is no longer valid for entry.\n\n"
                    "No trade setup available."
                ),
                "inline": False,
            },
        ],
        "footer": {"text": f"Zone consumed at {mz_str} · Scoring skipped"},
    }
    try:
        resp = requests.post(_url, json={"embeds": [embed]}, timeout=10)
        if resp.status_code not in (200, 204):
            logger.error("Discord zone-mitigated post failed: %s %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.error("Discord zone-mitigated post exception: %s", exc)


def send_discord_message(alert_data, bias, strength, bullish, bearish,
                         confidence, quality, edge_score,
                         recommendation, verdict, reasoning_chain, why, plan,
                         setup_stage, stage_next_step, stage_entry_rule, stage_invalidation,
                         market_direction, trade_opportunity, opportunity_reason,
                         entry_trigger, invalidation, trade_plan, sizing,
                         structure_label, structure_class, structure_detail,
                         nearest_supply, nearest_demand,
                         risk_label, risk_detail,
                         last_price_by_type,
                         active_trade_info=None,
                         zone_broken_active=False,
                         zone_mitigated_near=False,
                         mitigated_zone_price=None):
    _url = _discord_url(alert_data.get("ticker", ""))
    if not _url:
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
        # ── Active Trade (v12) — top of embed when trade is running ──
        *([active_trade_info] if active_trade_info else []),
        # ── Final Verdict / Recommendation / Edge — hidden during active trade ──
        *([
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
        ] if not active_trade_info else []),
        # ── Reasoning Chain ──
        {
            "name":   "🔗  Reasoning Chain",
            "value":  f"```\n{chain_text}\n```",
            "inline": False,
        },
        # ── Zone Mitigated: replace all trade sections with consumed-zone notice ──
        *([
            {
                "name":   "🚫  Zone Status",
                "value":  "**Consumed / Mitigated**",
                "inline": True,
            },
            {
                "name":   "🗺️  Action",
                "value":  "Wait for fresh supply or demand zone.",
                "inline": False,
            },
            {
                "name":   "💬  Reason",
                "value":  (
                    "Zone already reacted and is no longer valid for entry.\n"
                    "Zone mitigation overrides all bullish/bearish scores."
                ),
                "inline": False,
            },
        ] if zone_mitigated_near else
        # ── Setup Stage (v10) — hidden during active trade ──
        (_setup_stage_fields(setup_stage, stage_next_step, stage_entry_rule, stage_invalidation)
         if not active_trade_info else []) +
        # ── Market Direction + Trade Opportunity ──
        _direction_opportunity_fields(market_direction, trade_opportunity,
                                      opportunity_reason, entry_trigger, invalidation) +
        # ── Trade Plan + Position Sizing ──
        _trade_plan_fields(trade_plan, sizing)),
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
        # ── Zone Status (conditional) ──
        *([{
            "name":   "🚫  Structure Invalidated",
            "value":  (
                f"Zone broken at `{float(ZONE_BROKEN_AT['price']):.1f}` — setup cancelled. "
                f"Confidence reduced. Wait for structure to rebuild."
            ),
            "inline": False,
        }] if zone_broken_active and ZONE_BROKEN_AT else
        [{
            "name":   "⚠️  Zone Consumed",
            "value":  (
                f"Nearest zone was previously mitigated at `{float(mitigated_zone_price):.1f}`. "
                f"Confidence reduced. Avoid entry from this level."
            ),
            "inline": False,
        }] if zone_mitigated_near and mitigated_zone_price is not None else []),
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

    if active_trade_info:
        _context_title = "📈 ACTIVE TRADE"
    elif verdict in ("LONG READY", "SHORT READY"):
        _context_title = "🔥 HIGH CONVICTION TRADE"
    else:
        _context_title = "👀 WATCHLIST SETUP"

    embed = {
        "author":      {"name": BOT_NAME},
        "title":       _context_title,
        "description": f"**{ticker}** · {price_str} · `{alert_data.get('alert_type','—')}`",
        "color":       color,
        "fields":      fields,
        "footer":      {"text": f"Received {alert_data.get('timestamp','')}"},
        "timestamp":   now_utc().isoformat(),
    }

    try:
        resp = requests.post(_url, json={"embeds": [embed]}, timeout=10)
        resp.raise_for_status()
        logger.info("Discord sent (status %s)", resp.status_code)
    except requests.RequestException as exc:
        logger.error("Discord send failed: %s", exc)


# ---------------------------------------------------------------------------
# Setup Stage Detection v10
# ---------------------------------------------------------------------------

_STAGE_EMOJI = {
    "Watching":            "👁️",
    "Setup Forming":       "⚠️",
    "Confirmation Candle": "🕯️",
    "Trade Ready":         "✅",
}

_STAGE_NUMBER = {
    "Watching":            1,
    "Setup Forming":       2,
    "Confirmation Candle": 3,
    "Trade Ready":         4,
}

READY_STAGES = ("Confirmation Candle", "Trade Ready")


def get_setup_stage(current_price, nearest_supply, nearest_demand,
                    bullish, bearish, alert_history):
    """
    Determine the current setup stage (8-stage system).
    Returns (stage, next_step, entry_rule, invalidation, direction).

    Stage progression:
      4 — Trade Ready         — 5m confirmation candle closed, enter now
      3 — Confirmation Candle — zone confirmed, watching for 5m candle close
      2 — Setup Forming       — at zone + CHOCH/BOS, waiting for zone confirmation
      1 — Watching            — monitoring; price mid-range or near zone only
    direction: "Long", "Short", or None
    """
    if current_price is None:
        return ("Watching", "Waiting for price data.", "No entry.", "N/A", None)

    # ── Recent alert window: last 5 alerts ──
    recent = [a["alert_type"] for a in list(alert_history)[-5:]]
    has_choch_bos_supply     = any(t in ("CHOCH SUPPLY", "BOS SUPPLY") for t in recent)
    has_choch_bos_demand     = any(t in ("CHOCH DEMAND", "BOS DEMAND") for t in recent)
    has_supply_confirmed     = "MGC SUPPLY ZONE CONFIRMED"              in recent
    has_demand_confirmed     = "MGC DEMAND ZONE CONFIRMED"              in recent
    has_bullish_confirmation = any(t in ("MGC BULLISH CONFIRMATION", "MNQ BULLISH CONFIRMATION")
                                   for t in recent)
    has_bearish_confirmation = any(t in ("MGC BEARISH CONFIRMATION", "MNQ BEARISH CONFIRMATION")
                                   for t in recent)

    # ── Proximity (0.75%) ──
    def dist(a, b):
        return abs(a - b) / b if b else float("inf")

    at_supply = nearest_supply is not None and dist(nearest_supply, current_price) <= WATCH_PCT
    at_demand = nearest_demand is not None and dist(current_price, nearest_demand) <= WATCH_PCT

    bearish_dominant = (bearish - bullish) >= BIAS_THRESHOLD
    bullish_dominant = (bullish - bearish) >= BIAS_THRESHOLD

    sup_str = f"${nearest_supply:.2f}" if nearest_supply else "—"
    dem_str = f"${nearest_demand:.2f}" if nearest_demand else "—"

    # ── STAGE 4: TRADE READY (Short) — bearish confirmation candle closed ──
    if (at_supply and bearish_dominant and has_choch_bos_supply
            and has_supply_confirmed and has_bearish_confirmation):
        return (
            "Trade Ready",
            "5m bearish candle confirmed. Enter short now.",
            "Enter short on close of 5m bearish confirmation candle.",
            f"Close above nearest supply ({sup_str}).",
            "Short",
        )

    # ── STAGE 4: TRADE READY (Long) — bullish confirmation candle closed ──
    if (at_demand and bullish_dominant and has_choch_bos_demand
            and has_demand_confirmed and has_bullish_confirmation):
        return (
            "Trade Ready",
            "5m bullish candle confirmed. Enter long now.",
            "Enter long on close of 5m bullish confirmation candle.",
            f"Close below nearest demand ({dem_str}).",
            "Long",
        )

    # ── STAGE 3: CONFIRMATION CANDLE (Short) — supply confirmed, watching for 5m close ──
    if at_supply and bearish_dominant and has_choch_bos_supply and has_supply_confirmed:
        return (
            "Confirmation Candle",
            "Supply confirmed. Wait for 5m bearish close below entry zone.",
            "5m bearish candle closes below entry zone. Enter on close.",
            f"Close above nearest supply ({sup_str}).",
            "Short",
        )

    # ── STAGE 3: CONFIRMATION CANDLE (Long) — demand confirmed, watching for 5m close ──
    if at_demand and bullish_dominant and has_choch_bos_demand and has_demand_confirmed:
        return (
            "Confirmation Candle",
            "Demand confirmed. Wait for 5m bullish close above entry zone.",
            "5m bullish candle closes above entry zone. Enter on close.",
            f"Close below nearest demand ({dem_str}).",
            "Long",
        )

    # ── STAGE 2: SETUP FORMING (Short) — at supply, structure building ──
    if at_supply and bearish_dominant and has_choch_bos_supply:
        return (
            "Setup Forming",
            "Wait for MGC Supply Zone Confirmed alert.",
            "Do not enter until zone is confirmed.",
            f"Close above nearest supply ({sup_str}).",
            "Short",
        )

    # ── STAGE 2: SETUP FORMING (Long) — at demand, structure building ──
    if at_demand and bullish_dominant and has_choch_bos_demand:
        return (
            "Setup Forming",
            "Wait for MGC Demand Zone Confirmed alert.",
            "Do not enter until zone is confirmed.",
            f"Close below nearest demand ({dem_str}).",
            "Long",
        )

    # ── STAGE 1: WATCHING ──
    return (
        "Watching",
        "Monitoring price action. Wait for proximity to a key level.",
        "No entry — observation only.",
        "N/A",
        None,
    )


# ---------------------------------------------------------------------------
# Zone Broken / Mitigated helpers
# ---------------------------------------------------------------------------

ZONE_BROKEN_EXPIRY       = 5      # Expire ZONE_BROKEN_AT after N subsequent non-zone alerts
MITIGATED_TOLERANCE_PCT  = 0.003  # 0.3% proximity check for consumed zone warning


def _handle_zone_broken(price):
    """Cancel pending directional setup and record the broken zone event."""
    global ZONE_BROKEN_AT, ALERT_HISTORY
    ZONE_BROKEN_AT = {"price": price, "alerts_since": 0}
    directional = SUPPLY_TYPES | DEMAND_TYPES
    # Remove last 5 directional setup alerts from history to cancel pending setup
    history_list = list(ALERT_HISTORY)
    # Exclude the ZONE BROKEN record itself (last item); reverse scan to cancel most recent first
    kept      = []
    cancelled = 0
    for rec in reversed(history_list[:-1]):
        if cancelled < 5 and rec.get("alert_type") in directional:
            cancelled += 1
            continue
        kept.append(rec)
    kept.reverse()
    # Also re-add the ZONE BROKEN record at the end
    kept.append(history_list[-1])
    ALERT_HISTORY.clear()
    ALERT_HISTORY.extend(kept)
    logger.info("Zone broken at %.1f — cancelled %d directional alerts", price, cancelled)


def _handle_zone_mitigated(price):
    """Record the consumed zone level and arm the mitigation flag."""
    global MITIGATED_PRICES, ZONE_MITIGATED_FLAG
    MITIGATED_PRICES.append({"price": price, "ts": datetime.now(timezone.utc).isoformat()})
    MITIGATED_PRICES    = MITIGATED_PRICES[-10:]
    ZONE_MITIGATED_FLAG = True
    logger.info("Zone mitigated at %.1f — flag armed, %d levels tracked", price, len(MITIGATED_PRICES))


def is_near_mitigated_zone(price):
    """Return (True, consumed_price) if price is within MITIGATED_TOLERANCE_PCT of any mitigated zone."""
    if price is None or not MITIGATED_PRICES:
        return False, None
    for mz in MITIGATED_PRICES:
        ref = mz["price"]
        if ref and abs(price - ref) / ref <= MITIGATED_TOLERANCE_PCT:
            return True, ref
    return False, None


# ---------------------------------------------------------------------------
# Full analysis
# ---------------------------------------------------------------------------

def full_analysis(current_price_override=None):
    # ── ZONE MITIGATION HARD GATE — skip ALL computation ──────────────────────
    if ZONE_MITIGATED_FLAG and ZONE_BROKEN_AT is None:
        _mz_price = MITIGATED_PRICES[-1]["price"] if MITIGATED_PRICES else None
        _cp       = current_price_override if current_price_override is not None else CURRENT_PRICE
        return dict(
            bullish=0, bearish=0, counts={},
            bias="Choppy", strength=1, confidence=0,
            quality="D", edge_score=0,
            recommendation="WAIT", verdict="WAIT",
            reasoning_chain=["Zone Consumed", "Scoring Skipped", "WAIT"],
            why="Zone mitigated — all scoring skipped.",
            plan={"action": "Wait for fresh zone.", "longs_allowed": "No", "shorts_allowed": "No", "warning": ""},
            setup_stage="No Setup",
            stage_next_step="Zone consumed — wait for fresh supply or demand zone.",
            stage_entry_rule="No entry from consumed zone.",
            stage_invalidation="Zone previously mitigated — invalid entry.",
            market_direction="Neutral",
            trade_opportunity="None",
            opportunity_reason="Zone already reacted and is no longer valid for entry.",
            entry_trigger=None, invalidation=None,
            trade_plan={
                "trade_plan": False,
                "reason":     "Zone consumed — wait for fresh supply or demand zone.",
                "entry_zone": None, "stop_loss": None,
                "target1":    None, "target2":   None,
                "rr":         None, "direction": None,
            },
            current_price=_cp,
            last_price_by_type={},
            nearest_supply=None, nearest_demand=None,
            structure_label="Zone Mitigated",
            structure_class="Undefined",
            structure_detail="Zone consumed — wait for fresh supply or demand zone.",
            risk_label="Choppy", risk_detail="Zone consumed.", overextended=False,
            zone_broken_active=False,
            zone_mitigated_near=True,
            mitigated_zone_price=_mz_price,
        )
    # ──────────────────────────────────────────────────────────────────────────

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

    # ── Confidence gate: suppress plan below 70 % or when structure is undefined ──
    _plan_eligible = (confidence >= 70 and structure_class not in ("Undefined", "Bullish Attempt", "Bearish Attempt"))
    if not _plan_eligible:
        trade_plan = {
            "trade_plan": False,
            "reason":     (f"Confidence {confidence}% — below 70 % threshold. No trade plan."
                           if confidence < 70 else
                           "Structure not yet confirmed. No trade plan."),
            "entry_zone": None, "stop_loss": None,
            "target1":    None, "target2":   None,
            "rr":         None, "direction": None,
        }

    setup_stage, stage_next_step, stage_entry_rule, stage_invalidation, stage_direction = get_setup_stage(
        current_price, nearest_supply, nearest_demand, bullish, bearish, ALERT_HISTORY
    )

    # ── Map final verdict: WATCH / LONG READY / SHORT READY / WAIT ──────────
    if trade_plan["trade_plan"]:
        if setup_stage == "Trade Ready":
            verdict = "LONG READY" if stage_direction == "Long" else "SHORT READY"
        elif setup_stage in ("Setup Forming", "Confirmation Candle"):
            verdict = "WATCH"
        else:
            # Plan generated but no active setup stage — suppress the plan
            verdict = "WAIT"
            trade_plan = {
                "trade_plan": False,
                "reason":     "No active setup stage — plan withheld.",
                "entry_zone": None, "stop_loss": None,
                "target1":    None, "target2":   None,
                "rr":         None, "direction": None,
            }
    else:
        verdict = "WAIT"

    # ── Zone Broken: cancel setup, reduce confidence, mark structure invalidated ──
    zone_broken_active = ZONE_BROKEN_AT is not None
    if zone_broken_active:
        confidence         = max(0, confidence - 30)
        setup_stage        = "Watching"
        stage_direction    = None
        stage_next_step    = "Wait for structure to rebuild after zone break"
        stage_entry_rule   = "—"
        stage_invalidation = "Structure invalidated — zone broken"
        verdict            = "WAIT"

    # ── Zone Mitigated: warn if nearest levels are near a consumed zone ──
    near_sup_mz, mz_sup_price = is_near_mitigated_zone(nearest_supply)
    near_dem_mz, mz_dem_price = is_near_mitigated_zone(nearest_demand)
    zone_mitigated_near  = (near_sup_mz or near_dem_mz) and not zone_broken_active
    mitigated_zone_price = mz_sup_price or mz_dem_price
    if zone_mitigated_near:
        # Full override — zone mitigation supersedes ALL scores, setups, and plans
        confidence         = max(0, confidence - 15)
        verdict            = "WAIT"
        recommendation     = "WAIT"
        trade_opportunity  = "None"
        opportunity_reason = "Zone already reacted and is no longer valid for entry."
        entry_trigger      = None
        invalidation       = None
        trade_plan         = {
            "trade_plan": False,
            "reason":     "Zone consumed — wait for fresh supply or demand zone.",
            "entry_zone": None, "stop_loss": None,
            "target1":    None, "target2": None,
            "rr":         None, "direction": None,
        }
        setup_stage        = "Watching"
        stage_direction    = None
        stage_next_step    = "Zone consumed — wait for fresh supply or demand zone."
        stage_entry_rule   = "No entry from consumed zone."
        stage_invalidation = (
            f"Zone previously mitigated at {mitigated_zone_price:.1f} — invalid entry."
        )

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
        stage_direction=stage_direction,
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
        zone_broken_active=zone_broken_active,
        zone_mitigated_near=zone_mitigated_near,
        mitigated_zone_price=mitigated_zone_price,
    )


# ---------------------------------------------------------------------------
# Trade Management v12
# ---------------------------------------------------------------------------

def compute_pnl(trade, current_price):
    """Returns (dollar_pnl, points_pnl) for the active trade."""
    direction = trade["direction"]
    entry     = trade["entry_price"]
    contracts = trade["contracts"]
    pts = (entry - current_price) if direction == "Short" else (current_price - entry)
    dollars = pts * MGC_POINT_VALUE * contracts
    return dollars, pts


def compute_distances(trade, current_price):
    """Returns (to_t1, to_t2, to_stop) as positive point distances remaining."""
    direction = trade["direction"]
    if direction == "Short":
        to_t1   = current_price - trade["target1"]
        to_t2   = current_price - trade["target2"]
        to_stop = trade["stop_loss"] - current_price
    else:
        to_t1   = trade["target1"] - current_price
        to_t2   = trade["target2"] - current_price
        to_stop = current_price - trade["stop_loss"]
    return to_t1, to_t2, to_stop


def check_trade_events(trade, current_price):
    """Returns list of new events: 'T1_HIT', 'T2_HIT', 'STOP_HIT'. Fires each once."""
    direction = trade["direction"]
    if direction == "Short":
        t1_hit   = current_price <= trade["target1"]
        t2_hit   = current_price <= trade["target2"]
        stop_hit = current_price >= trade["stop_loss"]
    else:
        t1_hit   = current_price >= trade["target1"]
        t2_hit   = current_price >= trade["target2"]
        stop_hit = current_price <= trade["stop_loss"]

    events = []
    if stop_hit:
        events.append("STOP_HIT")
    else:
        if t2_hit and not trade.get("t2_hit"):
            events.append("T2_HIT")
        if t1_hit and not trade.get("t1_hit"):
            events.append("T1_HIT")
    return events


def send_trade_event_message(event_type, trade, current_price):
    """Send a standalone Discord plain-text alert for a trade event."""
    _url = _discord_url(trade.get("profile", ""))
    if not _url:
        return
    dollar_pnl, pts_pnl = compute_pnl(trade, current_price)
    pnl_str = f"+${dollar_pnl:,.0f}" if dollar_pnl >= 0 else f"-${abs(dollar_pnl):,.0f}"
    msgs = {
        "T1_HIT":   (f"🎯 **T1 HIT**\n"
                     f"{pnl_str}\n"
                     f"Move Stop to Break Even"),
        "T2_HIT":   (f"🎯🎯 **T2 HIT**\n"
                     f"Trade Closed\n"
                     f"{pnl_str}"),
        "STOP_HIT": (f"❌ **STOP HIT**\n"
                     f"Trade Closed\n"
                     f"{pnl_str}"),
    }
    content = msgs.get(event_type, f"Trade event: {event_type}")
    try:
        requests.post(_url, json={"content": content}, timeout=5)
        logger.info("Trade event sent: %s", event_type)
    except requests.RequestException as exc:
        logger.error("Trade event Discord send failed: %s", exc)


def send_journal_discord_embed(entry):
    """Post a journal entry to the dedicated trading-journal Discord channel."""
    if not DISCORD_JOURNAL_WEBHOOK_URL:
        logger.warning("DISCORD_JOURNAL_WEBHOOK_URL not set — journal Discord post skipped")
        return

    direction_emoji = "📈" if entry["direction"] == "Long" else "📉"
    color           = 0x00B0FF if entry["direction"] == "Long" else 0xFF5252

    def _fp(v):
        try:
            return f"${float(v):.2f}"
        except (TypeError, ValueError):
            return "—"

    chain_text = "\n↓\n".join(entry["reasoning_chain"]) if entry["reasoning_chain"] else "—"
    if len(chain_text) > 900:
        chain_text = chain_text[:900] + "…"

    embed = {
        "author":      {"name": f"{BOT_NAME} Journal"},
        "title":       f"📓 {entry['symbol']} {direction_emoji} {entry['direction']}",
        "description": f"**{entry['setup_stage']}**  ·  Verdict: **{entry['verdict']}**",
        "color":       color,
        "timestamp":   entry["datetime"],
        "fields": [
            {"name": "📅 Date/Time",          "value": entry["datetime"][:19].replace("T", " ") + " UTC", "inline": True},
            {"name": "📊 Symbol",              "value": entry["symbol"],              "inline": True},
            {"name": "🧭 Direction",           "value": f"{direction_emoji} {entry['direction']}", "inline": True},
            {"name": "🎯 Setup Stage",         "value": entry["setup_stage"],         "inline": True},
            {"name": "⚖️ Final Verdict",       "value": entry["verdict"],             "inline": True},
            {"name": "🔥 Bias",                "value": entry["bias"],                "inline": True},
            {"name": "📐 Entry Zone",          "value": _fp(entry["entry_zone"]),     "inline": True},
            {"name": "🛑 Stop Loss",           "value": _fp(entry["stop_loss"]),      "inline": True},
            {"name": "🎯 Target 1",            "value": _fp(entry["target1"]),        "inline": True},
            {"name": "🎯 Target 2",            "value": _fp(entry["target2"]),        "inline": True},
            {"name": "💡 Confidence",          "value": entry["confidence"],          "inline": True},
            {"name": "⚡ Edge Score",           "value": str(entry["edge_score"]),     "inline": True},
            {"name": "🏗️ Market Structure",    "value": entry["market_structure"],    "inline": True},
            {"name": "⚠️ Risk Zone",            "value": entry["risk_zone"],           "inline": True},
            {"name": "📖 Reasoning Chain",     "value": f"```\n{chain_text}\n```",    "inline": False},
            {"name": "💬 Why",                 "value": entry["why"] or "—",          "inline": False},
            {"name": "📷 Screenshot",          "value": entry["screenshot"],          "inline": False},
            {"name": "📋 Outcome",             "value": f"🟡 {entry['outcome']}",     "inline": True},
        ],
        "footer": {"text": f"Journal Entry #{entry['id']}"},
    }

    try:
        resp = requests.post(
            DISCORD_JOURNAL_WEBHOOK_URL,
            json={"embeds": [embed]},
            timeout=10,
        )
        if resp.status_code not in (200, 204):
            logger.warning("Journal Discord post failed: %s %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.error("Journal Discord post error: %s", exc)


def _update_journal_outcome(new_outcome, pnl_dollars=None):
    """Update the most recent journal entry that is still Pending or at T1.

    Matches the first entry whose outcome is 'Pending' or starts with 'T1 Hit'.
    Posts a brief update line to the journal Discord channel.
    Returns the updated entry or None if nothing matched.
    """
    for entry in JOURNAL:
        o = entry.get("outcome", "")
        if o == "Pending" or o.startswith("T1 Hit"):
            entry["outcome"] = new_outcome
            if pnl_dollars is not None:
                entry["pnl_dollars"] = round(pnl_dollars, 2)
            logger.info("Journal #%d outcome → %s", entry["id"], new_outcome)
            # Post a short update line to the journal channel
            if DISCORD_JOURNAL_WEBHOOK_URL:
                _outcome_emoji = "✅" if "Win" in new_outcome else ("⚠️" if "T1" in new_outcome else "❌")
                content = (
                    f"{_outcome_emoji} **Journal #{entry['id']} updated**\n"
                    f"{entry.get('symbol','—')} {entry.get('direction','—')}  ·  "
                    f"Outcome: **{new_outcome}**"
                )
                try:
                    requests.post(
                        DISCORD_JOURNAL_WEBHOOK_URL,
                        json={"content": content},
                        timeout=5,
                    )
                except Exception:
                    pass
            return entry
    return None


def create_journal_entry(record, a, sizing):
    """Create a journal entry when setup_stage warrants one, skipping duplicates."""
    global JOURNAL, JOURNAL_KEYS

    setup_stage = a.get("setup_stage", "")
    if setup_stage not in JOURNAL_STAGES:
        return None

    _raw_ticker    = record.get("ticker") or record.get("alert_type", "")
    ticker         = "MNQ" if "MNQ" in str(_raw_ticker).upper() else "MGC"
    if record.get("ticker"):
        ticker = record["ticker"]   # preserve exact ticker (e.g. "MNQ1!") when supplied
    tp         = a.get("trade_plan") or {}
    entry_zone = tp.get("entry_zone")
    direction  = (a.get("stage_direction")
                  or tp.get("direction")
                  or a.get("market_direction", "Long"))

    # Dedup key: ticker + stage + entry zone rounded to nearest integer
    try:
        zone_key = round(float(entry_zone), 0) if entry_zone is not None else 0.0
    except (TypeError, ValueError):
        zone_key = 0.0
    dedup_key = (ticker, setup_stage, zone_key)

    if dedup_key in JOURNAL_KEYS:
        logger.info("Journal dedup skip: %s %s @ %.0f", ticker, setup_stage, zone_key)
        return None

    JOURNAL_KEYS.add(dedup_key)

    rc = a.get("reasoning_chain") or []
    entry = {
        "id":               len(JOURNAL) + 1,
        "datetime":         datetime.now(timezone.utc).isoformat(),
        "symbol":           ticker,
        "direction":        direction,
        "setup_stage":      setup_stage,
        "verdict":          a.get("verdict", "WAIT"),
        "entry_zone":       entry_zone,
        "stop_loss":        tp.get("stop_loss"),
        "target1":          tp.get("target1"),
        "target2":          tp.get("target2"),
        "bias":             a.get("bias", "—"),
        "confidence":       f"{a.get('confidence', 0)}%",
        "edge_score":       a.get("edge_score", 0),
        "market_structure": a.get("structure_label", "—"),
        "risk_zone":        a.get("risk_label", "—"),
        "reasoning_chain":  rc,
        "why":              a.get("why", "—"),
        "screenshot":       "[ Screenshot placeholder — add URL or image link ]",
        "outcome":          "Pending",
    }

    JOURNAL.insert(0, entry)
    if len(JOURNAL) > 500:
        JOURNAL.pop()

    send_journal_discord_embed(entry)
    logger.info("Journal entry #%d created: %s %s @ %s", entry["id"], ticker, setup_stage, entry_zone)
    return entry


def active_trade_field(trade, current_price):
    """Return a single Discord embed field dict showing live trade status (v11)."""
    dollar_pnl, pts_pnl   = compute_pnl(trade, current_price)
    to_t1, to_t2, to_stop = compute_distances(trade, current_price)

    pnl_emoji = "🟢" if dollar_pnl >= 0 else "🔴"
    pnl_str   = f"+${dollar_pnl:,.0f}" if dollar_pnl >= 0 else f"-${abs(dollar_pnl):,.0f}"

    status = trade.get("status", "active")
    state_str = {
        "active":    "🟢  Stage 5 — Entered",
        "breakeven": "🟡  Stage 6 — T1 Hit (Breakeven Active)",
    }.get(status, "🟢  Stage 5 — Entered")

    if status == "active":
        next_action = f"Wait for T1 at `{trade['target1']:.1f}`"
    elif status == "breakeven":
        next_action = (f"Wait for T2 at `{trade['target2']:.1f}`  ·  "
                       f"Stop at breakeven `{trade['entry_price']:.1f}`")
    else:
        next_action = "—"

    value = (
        f"**State:** {state_str}\n"
        f"**Entry:** `{trade['entry_price']:.1f}`  ·  "
        f"**Current Price:** `{current_price:.1f}`\n"
        f"**PnL:** {pnl_emoji} {pnl_str} ({pts_pnl:+.1f} pts)\n"
        f"\n"
        f"**Distance to T1:** `{to_t1:.1f} pts`\n"
        f"**Distance to T2:** `{to_t2:.1f} pts`\n"
        f"**Distance to Stop:** `{to_stop:.1f} pts`\n"
        f"\n"
        f"**Next Action:** {next_action}"
    )

    return {"name": "📊  ACTIVE TRADE MANAGEMENT", "value": value, "inline": False}


_COMMAND_TYPES = {"MGC ENTER", "MNQ ENTER", "MGC CLOSE", "MNQ CLOSE"}


def _handle_command_alert(normalized, data, parsed_price):
    """Execute ENTER / CLOSE trade commands sent via TradingView webhook.

    Returns a Flask Response to short-circuit the webhook handler, or None
    to fall through to normal alert scoring.
    """
    global ACTIVE_TRADE
    profile   = str(data.get("profile", DEFAULT_PROFILE))
    is_enter  = normalized.endswith("ENTER")

    # ── ENTER ─────────────────────────────────────────────────────────────
    if is_enter:
        try:
            entry = float(data["entry"]) if data.get("entry") else None
            stop  = float(data["stop"])  if data.get("stop")  else None
            t1    = float(data["t1"])    if data.get("t1")    else None
            t2    = float(data["t2"])    if data.get("t2")    else None
        except (ValueError, TypeError) as exc:
            return jsonify({"status": "error", "reason": str(exc)}), 400

        # Fall back to current trade plan for any missing values
        if None in (entry, stop, t1, t2):
            a  = full_analysis(current_price_override=parsed_price)
            tp = a["trade_plan"]
            try:
                if entry is None:
                    lo_s, hi_s = str(tp["entry_zone"]).split("–")
                    entry = (float(lo_s) + float(hi_s)) / 2
                if stop is None:
                    stop = float(tp["stop_loss"])
                if t1   is None:
                    t1   = float(tp["target1"])
                if t2   is None:
                    t2   = float(tp["target2"])
            except (ValueError, TypeError, KeyError) as exc:
                return jsonify({"status": "error", "reason": f"Missing trade plan params: {exc}"}), 400

        direction = str(data.get("direction", "Long"))
        contracts = int(data.get("contracts", 1))

        ACTIVE_TRADE = {
            "direction":   direction,
            "entry_price": entry,
            "stop_loss":   stop,
            "target1":     t1,
            "target2":     t2,
            "contracts":   contracts,
            "profile":     profile,
            "opened_at":   now_utc().isoformat(),
            "t1_hit":      False,
            "t2_hit":      False,
            "status":      "active",
        }
        content = (
            f"✅ **TRADE ENTERED — {direction.upper()}**\n"
            f"Entry `{entry:.1f}`  ·  Stop `{stop:.1f}`  ·  "
            f"T1 `{t1:.1f}`  ·  T2 `{t2:.1f}`  ·  Contracts `{contracts}`"
        )
        _url = _discord_url(normalized)
        if _url:
            try:
                requests.post(_url, json={"content": content}, timeout=5)
            except Exception:
                pass
        logger.info("ENTER command: %s %s @ %.1f", direction, profile, entry)
        return jsonify({"status": "entered", "trade": ACTIVE_TRADE}), 200

    # ── CLOSE ─────────────────────────────────────────────────────────────
    if not ACTIVE_TRADE:
        return jsonify({"status": "error", "reason": "No active trade to close."}), 400

    exit_price = parsed_price
    closed     = dict(ACTIVE_TRADE)

    if exit_price is not None:
        dollar_pnl, _ = compute_pnl(ACTIVE_TRADE, exit_price)
        pnl_str    = f"+${dollar_pnl:,.0f}" if dollar_pnl >= 0 else f"-${abs(dollar_pnl):,.0f}"
        content    = (
            f"🏁 **TRADE CLOSED**\n"
            f"{ACTIVE_TRADE['direction']} @ `{ACTIVE_TRADE['entry_price']:.1f}`  ·  "
            f"Exit `{exit_price:.1f}`  ·  PnL **{pnl_str}**"
        )
        outcome_str = (
            f"Win — Closed {pnl_str} ✅" if dollar_pnl >= 0
            else f"Loss — Closed {pnl_str} ❌"
        )
    else:
        dollar_pnl  = None
        content     = (
            f"🏁 **TRADE CLOSED**\n"
            f"{ACTIVE_TRADE['direction']} @ `{ACTIVE_TRADE['entry_price']:.1f}`"
        )
        outcome_str = "Closed Manually"

    ACTIVE_TRADE = None
    _update_journal_outcome(outcome_str, pnl_dollars=dollar_pnl)

    _url = _discord_url(normalized)
    if _url:
        try:
            requests.post(_url, json={"content": content}, timeout=5)
        except Exception:
            pass
    logger.info("CLOSE command: %s", outcome_str)
    return jsonify({"status": "closed", "trade": closed}), 200


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/webhook", methods=["POST"])
def webhook():
    global CURRENT_PRICE, ACTIVE_TRADE, ZONE_BROKEN_AT, ZONE_MITIGATED_FLAG

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

    # ── Command types: ENTER / CLOSE — short-circuit before scoring ──────────
    if normalized in _COMMAND_TYPES:
        resp = _handle_command_alert(normalized, data, parsed_price)
        if resp is not None:
            return resp

    record = {
        "alert_type": normalized,
        "ticker":     data.get("ticker"),
        "price":      parsed_price,
        "timestamp":  now_utc().isoformat(),
        "raw":        data,
    }
    global LAST_ALERT_AT
    LAST_ALERT_AT = datetime.now(timezone.utc)
    ALERT_HISTORY.append(record)

    # ── Zone event side-effects ──
    _zone_neutral = ("MGC ZONE BROKEN", "MNQ ZONE BROKEN", "MGC ZONE MITIGATED", "MNQ ZONE MITIGATED")
    if normalized in ("MGC ZONE BROKEN", "MNQ ZONE BROKEN"):
        if parsed_price is not None:
            _handle_zone_broken(parsed_price)
        else:
            ZONE_BROKEN_AT = {"price": None, "alerts_since": 0}
    elif normalized in ("MGC ZONE MITIGATED", "MNQ ZONE MITIGATED"):
        if parsed_price is not None:
            _handle_zone_mitigated(parsed_price)
    elif ZONE_BROKEN_AT is not None and normalized not in _zone_neutral:
        ZONE_BROKEN_AT["alerts_since"] = ZONE_BROKEN_AT.get("alerts_since", 0) + 1
        if ZONE_BROKEN_AT["alerts_since"] >= ZONE_BROKEN_EXPIRY:
            ZONE_BROKEN_AT = None
            logger.info("Zone broken state expired after %d alerts", ZONE_BROKEN_EXPIRY)

    # ── Zone Mitigation: clear flag when fresh structure forms ──────────────────
    _STRUCTURE_RESET = frozenset((
        "CHOCH SUPPLY", "CHOCH DEMAND", "BOS SUPPLY", "BOS DEMAND",
        "MGC NEW SUPPLY ZONE", "MGC NEW DEMAND ZONE",
        "MNQ NEW SUPPLY ZONE", "MNQ NEW DEMAND ZONE",
    ))
    if normalized in _STRUCTURE_RESET and ZONE_MITIGATED_FLAG:
        ZONE_MITIGATED_FLAG = False
        logger.info("Zone mitigation cleared — new structure alert: %s", normalized)

    # ── Zone Mitigation: early exit — entire engine skipped ─────────────────
    if ZONE_MITIGATED_FLAG and ZONE_BROKEN_AT is None:
        _mz_price = MITIGATED_PRICES[-1]["price"] if MITIGATED_PRICES else None
        send_zone_mitigated_message(record, _mz_price)
        logger.info("Zone mitigated early exit — %s — scoring skipped", normalized)
        return jsonify({
            "status":       "zone_mitigated",
            "alert_type":   normalized,
            "ticker":       record.get("ticker"),
            "price":        parsed_price or CURRENT_PRICE,
            "mitigated_at": _mz_price,
            "verdict":      "WAIT",
            "zone_status":  "Consumed / Mitigated",
            "action":       "Wait for fresh supply or demand zone.",
            "reason":       "Zone already reacted and is no longer valid for entry.",
        }), 200

    # ── Account Profile selection ──
    profile_name = str(data.get("profile") or DEFAULT_PROFILE).strip()
    if profile_name in ACCOUNT_PROFILES:
        prof         = ACCOUNT_PROFILES[profile_name]
        account_size = prof["account_size"]
        risk_pct     = prof["risk_pct"]
    else:
        profile_name = "Custom"
        try:
            account_size = float(data.get("account_size") or DEFAULT_ACCOUNT_SIZE)
        except (ValueError, TypeError):
            account_size = DEFAULT_ACCOUNT_SIZE
        try:
            risk_pct = float(data.get("risk_pct") or DEFAULT_RISK_PCT)
        except (ValueError, TypeError):
            risk_pct = DEFAULT_RISK_PCT

    a = full_analysis(current_price_override=parsed_price)

    if a.get("zone_mitigated_near"):
        sizing = {}
    else:
        sizing = calculate_position_sizing(a["trade_plan"], account_size, risk_pct, profile_name)

    # ── Active trade: check events, build embed field ──
    ati = None
    if ACTIVE_TRADE and parsed_price is not None:
        events = check_trade_events(ACTIVE_TRADE, parsed_price)
        for event in events:
            send_trade_event_message(event, ACTIVE_TRADE, parsed_price)
            if event == "T1_HIT":
                ACTIVE_TRADE["t1_hit"] = True
                ACTIVE_TRADE["status"] = "breakeven"
                ACTIVE_TRADE["suggested_stop"] = ACTIVE_TRADE["entry_price"]
                _update_journal_outcome("T1 Hit — Partial ⚠️")
            elif event == "T2_HIT":
                d_pnl, _ = compute_pnl(ACTIVE_TRADE, parsed_price)
                _update_journal_outcome("Win — T2 Hit ✅", pnl_dollars=d_pnl)
                ACTIVE_TRADE = None
                break
            elif event == "STOP_HIT":
                d_pnl, _ = compute_pnl(ACTIVE_TRADE, parsed_price)
                _update_journal_outcome("Loss — Stop Hit ❌", pnl_dollars=d_pnl)
                ACTIVE_TRADE = None
                break
        if ACTIVE_TRADE:
            ati = active_trade_field(ACTIVE_TRADE, parsed_price)

    send_discord_message(
        record,
        a["bias"], a["strength"], a["bullish"], a["bearish"],
        a["confidence"], a["quality"], a["edge_score"],
        a["recommendation"], a["verdict"], a["reasoning_chain"], a["why"], a["plan"],
        a["setup_stage"], a["stage_next_step"], a["stage_entry_rule"], a["stage_invalidation"],
        a["market_direction"], a["trade_opportunity"], a["opportunity_reason"],
        a["entry_trigger"], a["invalidation"], a["trade_plan"], sizing,
        a["structure_label"], a["structure_class"], a["structure_detail"],
        a["nearest_supply"], a["nearest_demand"],
        a["risk_label"], a["risk_detail"],
        a["last_price_by_type"],
        ati,
        zone_broken_active=a.get("zone_broken_active", False),
        zone_mitigated_near=a.get("zone_mitigated_near", False),
        mitigated_zone_price=a.get("mitigated_zone_price"),
    )

    # ── Trading Journal ───────────────────────────────────────────────────────
    create_journal_entry(record, a, sizing)

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
        "sizing":             sizing,
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


@app.route("/journal", methods=["GET"])
def get_journal():
    return jsonify({"entries": JOURNAL, "count": len(JOURNAL)}), 200


@app.route("/journal", methods=["POST"])
def add_journal_entry():
    """Manually create a journal entry and post it to the Discord journal channel."""
    global JOURNAL
    data = request.get_json(force=True, silent=True) or {}

    # Normalise confidence: accept "71%" or 71
    raw_conf = data.get("confidence", "—")
    if isinstance(raw_conf, (int, float)):
        conf_str = f"{int(raw_conf)}%"
    else:
        conf_str = str(raw_conf).strip()
        if conf_str.isdigit():
            conf_str = conf_str + "%"

    # Normalise reasoning_chain: accept list or newline-delimited string
    raw_chain = data.get("reasoning_chain") or data.get("reason") or []
    if isinstance(raw_chain, str):
        reasoning_chain = [r.strip() for r in raw_chain.splitlines() if r.strip()]
    else:
        reasoning_chain = list(raw_chain)

    # Normalise setup_stage: map short aliases
    _stage_map = {
        "WATCHING":              "Watching",
        "SETUP FORMING":         "Setup Forming",
        "FORMING":               "Setup Forming",
        "CONFIRMATION CANDLE":   "Confirmation Candle",
        "TRADE READY":           "Trade Ready",
        "LONG READY":            "Trade Ready",
        "SHORT READY":           "Trade Ready",
        "SHORT FORMING":         "Setup Forming",
        "ENTERED":               "Entered",
        "T1 HIT":                "T1 Hit",
        "T2 HIT":                "T2 Hit",
        "CLOSED":                "Closed",
    }
    raw_stage = str(data.get("setup_stage") or data.get("status") or "—").upper()
    setup_stage = _stage_map.get(raw_stage, data.get("setup_stage") or data.get("status") or "—")

    direction = str(data.get("direction", "—")).capitalize()

    # Derive verdict from stage if not supplied
    _stage_verdict = {
        "Watching":            "WAIT",
        "Setup Forming":       "WATCH",
        "Confirmation Candle": "WATCH",
        "Trade Ready":         "LONG READY" if direction == "Long" else "SHORT READY",
        "Entered":             "ENTERED",
    }
    auto_verdict = _stage_verdict.get(setup_stage, "WATCH")

    entry = {
        "id":               len(JOURNAL) + 1,
        "datetime":         data.get("datetime", datetime.now(timezone.utc).isoformat()),
        "symbol":           str(data.get("symbol") or (
                                "MNQ" if "MNQ" in str(data.get("profile", "")).upper() else "MGC"
                            )).upper(),
        "direction":        direction,
        "setup_stage":      setup_stage,
        "verdict":          str(data.get("verdict") or auto_verdict),
        "entry_zone":       data.get("entry_zone"),
        "stop_loss":        data.get("stop") or data.get("stop_loss"),
        "target1":          data.get("target1"),
        "target2":          data.get("target2"),
        "bias":             str(data.get("bias") or ("Bullish" if direction == "Long" else "Bearish")),
        "confidence":       conf_str,
        "edge_score":       data.get("edge_score", 0),
        "market_structure": str(data.get("market_structure") or data.get("structure") or "—"),
        "risk_zone":        str(data.get("risk_zone") or data.get("risk") or "—"),
        "reasoning_chain":  reasoning_chain,
        "why":              str(data.get("why") or data.get("next_step") or "—"),
        "screenshot":       str(data.get("screenshot") or "[ Screenshot placeholder — add URL or image link ]"),
        "outcome":          str(data.get("outcome") or "Pending"),
        "manual":           True,
    }

    JOURNAL.insert(0, entry)
    if len(JOURNAL) > 500:
        JOURNAL.pop()

    send_journal_discord_embed(entry)
    logger.info("Manual journal entry #%d created: %s %s", entry["id"], entry["symbol"], entry["direction"])
    return jsonify({"status": "created", "entry": entry}), 201


@app.route("/clear", methods=["POST"])
def clear_alerts():
    global CURRENT_PRICE, ZONE_BROKEN_AT, MITIGATED_PRICES, ZONE_MITIGATED_FLAG, JOURNAL_KEYS
    ALERT_HISTORY.clear()
    CURRENT_PRICE       = None
    ZONE_BROKEN_AT      = None
    MITIGATED_PRICES    = []
    ZONE_MITIGATED_FLAG = False
    JOURNAL_KEYS.clear()
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
        "version":             "11.0",
        "verdict":             a["verdict"],
        "recommendation":      a["recommendation"],
        "reasoning_chain":     a["reasoning_chain"],
        "setup_stage":         a["setup_stage"],
        "stage_direction":     a["stage_direction"],
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
        "discord_configured":      bool(DISCORD_WEBHOOK_URL),
        "mnq_discord_configured":  bool(DISCORD_MNQ_WEBHOOK_URL),
    }), 200


@app.route("/enter", methods=["POST"])
def enter_trade():
    global ACTIVE_TRADE
    data = request.get_json(force=True, silent=True) or {}

    if data.get("entry"):
        try:
            direction = str(data.get("direction", "Long"))
            entry     = float(data["entry"])
            stop      = float(data["stop"])
            t1        = float(data["t1"])
            t2        = float(data["t2"])
            contracts = int(data.get("contracts", 1))
            profile   = str(data.get("profile", DEFAULT_PROFILE))
        except (KeyError, ValueError, TypeError) as exc:
            return jsonify({"status": "error", "reason": str(exc)}), 400
    else:
        a  = full_analysis()
        tp = a["trade_plan"]
        if not tp.get("trade_plan"):
            return jsonify({"status": "error", "reason": "No active trade plan. Send entry/stop/t1/t2 or trigger a Short Ready / Long Ready setup first."}), 400
        try:
            lo_s, hi_s = str(tp["entry_zone"]).split("–")
            entry     = (float(lo_s) + float(hi_s)) / 2
            stop      = float(tp["stop_loss"])
            t1        = float(tp["target1"])
            t2        = float(tp["target2"])
            direction = str(tp["direction"])
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify({"status": "error", "reason": str(exc)}), 400
        profile   = str(data.get("profile", DEFAULT_PROFILE))
        acct_size = ACCOUNT_PROFILES.get(profile, {}).get("account_size", DEFAULT_ACCOUNT_SIZE)
        risk_pct  = ACCOUNT_PROFILES.get(profile, {}).get("risk_pct", DEFAULT_RISK_PCT)
        sz        = calculate_position_sizing(tp, acct_size, risk_pct, profile)
        contracts = int(sz.get("contracts", 1)) if sz else 1

    ACTIVE_TRADE = {
        "direction":   direction,
        "entry_price": entry,
        "stop_loss":   stop,
        "target1":     t1,
        "target2":     t2,
        "contracts":   contracts,
        "profile":     profile,
        "opened_at":   now_utc().isoformat(),
        "t1_hit":      False,
        "t2_hit":      False,
        "status":      "active",
    }

    content = (
        f"✅ **TRADE ENTERED — {direction.upper()}**\n"
        f"Entry `{entry:.1f}`  ·  Stop `{stop:.1f}`  ·  "
        f"T1 `{t1:.1f}`  ·  T2 `{t2:.1f}`  ·  "
        f"Contracts `{contracts}`  ·  Profile `{profile}`"
    )
    try:
        _url = _discord_url(profile)
        if _url:
            requests.post(_url, json={"content": content}, timeout=5)
    except requests.RequestException:
        pass

    logger.info("Trade entered: %s @ %.1f", direction, entry)
    return jsonify({"status": "entered", "trade": ACTIVE_TRADE}), 200


@app.route("/breakeven", methods=["POST"])
def set_breakeven():
    global ACTIVE_TRADE
    if not ACTIVE_TRADE:
        return jsonify({"status": "error", "reason": "No active trade."}), 400
    old_stop = ACTIVE_TRADE["stop_loss"]
    ACTIVE_TRADE["stop_loss"] = ACTIVE_TRADE["entry_price"]
    content = (
        f"🔒 **STOP MOVED TO BREAKEVEN**\n"
        f"{ACTIVE_TRADE['direction']} @ `{ACTIVE_TRADE['entry_price']:.1f}`  ·  "
        f"Stop `{old_stop:.1f}` → `{ACTIVE_TRADE['entry_price']:.1f}`"
    )
    try:
        _url = _discord_url(ACTIVE_TRADE.get("profile", ""))
        if _url:
            requests.post(_url, json={"content": content}, timeout=5)
    except requests.RequestException:
        pass
    logger.info("Breakeven set: stop moved to %.1f", ACTIVE_TRADE["entry_price"])
    return jsonify({"status": "breakeven_set", "stop_loss": ACTIVE_TRADE["stop_loss"]}), 200


@app.route("/close", methods=["POST"])
def close_trade():
    global ACTIVE_TRADE
    if not ACTIVE_TRADE:
        return jsonify({"status": "error", "reason": "No active trade."}), 400
    data       = request.get_json(force=True, silent=True) or {}
    exit_price = CURRENT_PRICE
    try:
        if data.get("price"):
            exit_price = float(data["price"])
    except (ValueError, TypeError):
        pass

    closed = dict(ACTIVE_TRADE)
    if exit_price is not None:
        dollar_pnl, pts_pnl = compute_pnl(ACTIVE_TRADE, exit_price)
        pnl_str = f"+${dollar_pnl:,.0f}" if dollar_pnl >= 0 else f"-${abs(dollar_pnl):,.0f}"
        content = (
            f"🏁 **TRADE CLOSED MANUALLY**\n"
            f"{ACTIVE_TRADE['direction']} @ `{ACTIVE_TRADE['entry_price']:.1f}`  ·  "
            f"Exit `{exit_price:.1f}`  ·  PnL **{pnl_str}**"
        )
        outcome_str = (
            f"Win — Closed {pnl_str} ✅" if dollar_pnl >= 0
            else f"Loss — Closed {pnl_str} ❌"
        )
    else:
        content = (
            f"🏁 **TRADE CLOSED MANUALLY**\n"
            f"{ACTIVE_TRADE['direction']} @ `{ACTIVE_TRADE['entry_price']:.1f}`"
        )
        outcome_str = "Closed Manually"

    ACTIVE_TRADE = None
    _update_journal_outcome(
        outcome_str,
        pnl_dollars=dollar_pnl if exit_price is not None else None,
    )

    try:
        _url = _discord_url(closed.get("profile", ""))
        if _url:
            requests.post(_url, json={"content": content}, timeout=5)
    except requests.RequestException:
        pass
    logger.info("Trade closed manually.")
    return jsonify({"status": "closed", "trade": closed}), 200


@app.route("/trade", methods=["GET"])
def get_trade():
    if not ACTIVE_TRADE:
        return jsonify({"status": "no_active_trade"}), 200
    result = dict(ACTIVE_TRADE)
    cp = CURRENT_PRICE
    if cp is not None:
        dollar_pnl, pts_pnl   = compute_pnl(ACTIVE_TRADE, cp)
        to_t1, to_t2, to_stop = compute_distances(ACTIVE_TRADE, cp)
        result.update({
            "current_price": cp,
            "pnl_dollars":   round(dollar_pnl, 2),
            "pnl_points":    round(pts_pnl, 2),
            "to_t1_pts":     round(to_t1, 2),
            "to_t2_pts":     round(to_t2, 2),
            "to_stop_pts":   round(to_stop, 2),
        })
    return jsonify(result), 200


@app.route("/eod", methods=["POST"])
def eod_trigger():
    """Manually trigger the end-of-day summary."""
    _send_eod_summary()
    return jsonify({"status": "sent"}), 200


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service":     "TradingView Webhook Server",
        "version":     "11.0",
        "alert_types": list(ALERT_TYPES.keys()),
        "endpoints":   {
            "POST /webhook":   "Receive TradingView alerts",
            "GET /alerts":     "View last 100 stored alerts",
            "GET /price":      "Price context, levels, structure, and risk zone",
            "GET /status":     "Full analysis with verdict and reasoning chain",
            "POST /enter":     "Open an active trade (uses current trade plan or explicit params)",
            "POST /breakeven": "Move stop loss to entry price",
            "POST /close":     "Close the active trade manually",
            "GET /trade":      "Show active trade status and live PnL",
        },
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    threading.Timer(0, _heartbeat_loop).start()   # fire immediately, then every hour
    _schedule_eod()                               # schedule daily EOD summary
    app.run(host="0.0.0.0", port=port, debug=False)
