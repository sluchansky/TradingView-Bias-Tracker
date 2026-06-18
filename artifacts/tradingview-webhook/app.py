import os
import re
import time
import math
import logging
import threading
import queue
import contextlib
from collections import deque
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
from flask import Flask, request, jsonify, Response
import requests

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


_REDACT_RE = re.compile(
    r'("(?:password|token)"\s*:\s*")[^"]*(")',
    re.IGNORECASE,
)


def _redact(text: str) -> str:
    """Mask sensitive values before they reach the request log."""
    try:
        return _REDACT_RE.sub(r"\1***\2", text)
    except Exception:
        return text


@app.before_request
def _log_incoming_request():
    # Skip the dashboard's 3-second polling so the log stays readable.
    if request.path in ("/trade", "/status") and request.method == "GET":
        return
    try:
        body = request.get_data(as_text=True)
    except Exception:
        body = "<unreadable>"
    logger.info(
        "INCOMING %s %s | BODY: %s",
        request.method,
        request.path,
        (_redact(body)[:500] if body else "<empty>"),
    )

ALERT_HISTORY    = deque(maxlen=100)
CURRENT_PRICE    = None
CURRENT_PRICE_BY_TICKER = {}   # {"MNQ": float, "MGC": float} — latest price per instrument (alert-driven)
CURRENT_PRICE_TS_BY_TICKER = {}  # {"MNQ": iso8601} — UTC time the alert price above was last set
# Yahoo-sourced fallback price per instrument, refreshed by the VWAP auto-fetch
# loop. DISPLAY-ONLY: it keeps the dashboard price readout live after a restart or
# during quiet markets, and is NEVER read by the gate / scoring (which stay on the
# authoritative alert-driven price above).
AUTO_PRICE_BY_TICKER = {}      # {"MNQ": {"value": float, "ts": iso8601}}
ACTIVE_TRADE     = None
# Serialises the ENTER critical section so two concurrent ENTER requests can
# never race on the ACTIVE_TRADE record.
_ENTER_LOCK      = threading.Lock()
LAST_ALERT_AT    = None   # datetime of most recent recognized/scored webhook alert (UTC)
LAST_WEBHOOK_AT  = None   # datetime of most recent inbound POST /webhook (UTC), ANY type
LAST_LIVE_CARD_AT = {}    # instrument ("MGC"/"MNQ") -> datetime of last live card sent (UTC)
# Per-evaluation performance diagnostics: the last EVAL_METRICS_MAX scored alerts,
# each with phase timings (indicator/volatility/scoring/notes/screenshot/journal),
# the webhook->alert delay and the volatility reading. Surfaced as JSON on
# /eval-metrics and rendered live on the Diagnostics page (/diagnostics-live).
EVAL_METRICS_MAX  = 100
EVAL_METRICS      = deque(maxlen=EVAL_METRICS_MAX)
EVAL_METRICS_LOCK = threading.Lock()   # guards append (worker) vs snapshot (/eval-metrics)
# ── Cumulative observability counters (since process start; reset on restart) ──
# Surfaced as the "stats" block on /eval-metrics and the Diagnostics summary cards.
# Guarded by COUNTERS_LOCK, which is NEVER nested inside EVAL_METRICS_LOCK.
COUNTERS = {
    "webhooks_received":      0,   # every inbound POST /webhook (any type)
    "evaluations_run":        0,   # every recorded evaluation (webhook + heartbeat)
    "ready_setups_detected":  0,   # deduped: non-READY -> READY transitions only
    "alerts_sent":            0,   # live-card / tiered alerts actually dispatched
    "duplicates_ignored":     0,   # inbound POSTs flagged duplicate within cooldown
    "signals_passed_filters": 0,   # webhook (non-duplicate) evals with a READY verdict
    "signals_rejected":       0,   # webhook (non-duplicate) evals with a WAIT verdict
    "wait_reasons_breakdown": {},  # raw failed-gate name -> count (WAIT verdicts + duplicates)
    "rejection_reasons":      {},  # canonical reason -> count (req 6; condition-level on WAIT)
}
# Canonical rejection-reason keys (req 6). Counted per WAIT eval from gate_debug so
# the operator sees, broken out, which individual conditions are holding setups back
# (the raw wait_reasons_breakdown lumps the SCALP confirmations into one bucket).
# NOTE: "session_filter" is intentionally never incremented — the trading session is
# a +10 BONUS, never a hard gate, so it can never reject a setup; it is shown at 0 so
# the operator can SEE that session is not the bottleneck (it is not a filter).
REJECTION_REASON_KEYS = (
    "zone_valid", "vwap_confirmed", "structure_confirmed", "candle_confirmed",
    "volatility_block", "edge_score_low", "conflicting_structure",
    "session_filter", "cooldown_duplicate",
)
COUNTERS_LOCK = threading.Lock()
# Per-instrument last verdict (was it READY?) so ready_setups_detected counts a
# fresh setup once per non-READY -> READY transition, not on every heartbeat re-eval.
_READY_STATE_BY_INST = {}
# ── Inbound signal de-duplication (additive, DIAGNOSTIC) ──────────────────────
# TradingView now fires the SAME alert every minute while a condition holds. We
# key the last-seen time on (instrument, alert_type) — the alert_type string
# already encodes direction + setup (e.g. "MGC BULLISH CONFIRMATION"), so a
# direction flip or a different setup is a DIFFERENT key and is never treated as a
# duplicate. This layer ONLY counts duplicates + annotates the eval record; it
# NEVER skips full_analysis and NEVER suppresses a dispatch (the authoritative,
# zone-aware non-duplication already lives downstream in create_journal_entry /
# the EARLY anchor dedupe / the tiered + READY re-post throttles).
SIGNAL_DEDUP      = {}              # (instrument, alert_type) -> last-seen datetime (UTC)
DEDUP_LOCK        = threading.Lock()
# ── Persistent per-instrument setup-state machine (additive, DISPLAY-ONLY) ────
# Derived AFTER each evaluation (never inside full_analysis) so it can never alter
# the gate/verdict. States: FORMING / READY / ACTIVE / INVALIDATED / EXPIRED.
SETUP_STATE       = {}              # instrument ("MGC"/"MNQ") -> state dict
STATE_LOCK        = threading.Lock()
# Lightweight rolling-window timestamp logs (last hour) for diagnostics that the
# capped EVAL_METRICS deque can't answer once the heartbeat floods it. Guarded by
# COUNTERS_LOCK (appended in webhook(), read+trimmed in /eval-metrics).
_WEBHOOK_TS       = deque(maxlen=5000)   # inbound POST /webhook timestamps (UTC)
_DUP_TS           = deque(maxlen=5000)   # duplicate-signal timestamps (UTC)
# ── Trade-management watcher state (additive; separate from manual ACTIVE_TRADE) ──
MANAGED_TRADES_BY_KEY = {}  # (instrument,direction,entry_lo,date) -> managed-trade dict
LAST_READY_BY_TICKER  = {}  # instrument -> last READY card entry snapshot (for /why)
ZONE_BROKEN_AT      = None   # {"price": float, "alerts_since": int}
MITIGATED_PRICES    = []     # [{"price": float, "ts": str}]
ZONE_MITIGATED_FLAG = False  # True once any zone is mitigated; cleared on new structure or /clear
VWAP_BY_TICKER      = {}      # {"MNQ": {"value": float, "ts": iso}, "MGC": {...}} — latest VWAP per instrument
VOLATILITY_BY_TICKER = {}     # {"MNQ": {"atr_pts","ratio","ts"}, "MGC": {...}} — latest volatility per instrument

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
    # ── Shared swing-structure alerts (HH/HL bullish, LH/LL bearish) ───────────
    #    side "structure" keeps them OUT of bias scoring (score_alerts) and the
    #    supply/demand level builder; the READY structure gate detects them by
    #    name in evaluate_strict_setup. Un-prefixed → require a `ticker` field.
    "HH":                         {"side": "structure", "score": 0},
    "HL":                         {"side": "structure", "score": 0},
    "LH":                         {"side": "structure", "score": 0},
    "LL":                         {"side": "structure", "score": 0},
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
    # ── Liquidity sweep alerts (stop-hunt then reversal; display/edge only, no score) ─
    "MGC BULLISH SWEEP":  {"side": "sweep", "score": 0},
    "MGC BEARISH SWEEP":  {"side": "sweep", "score": 0},
    "MNQ BULLISH SWEEP":  {"side": "sweep", "score": 0},
    "MNQ BEARISH SWEEP":  {"side": "sweep", "score": 0},
    # ── Trade lifecycle commands (sent directly from TradingView strategy) ───────
    "MGC ENTER":  {"side": "command", "score": 0},
    "MNQ ENTER":  {"side": "command", "score": 0},
    "MGC CLOSE":  {"side": "command", "score": 0},
    "MNQ CLOSE":  {"side": "command", "score": 0},
    # ── Data-only VWAP push (updates the VWAP store; no scoring) ─────────────────
    "MGC VWAP":   {"side": "data", "score": 0},
    "MNQ VWAP":   {"side": "data", "score": 0},
}

SUPPLY_TYPES = {k for k, v in ALERT_TYPES.items() if v["side"] == "bearish"}
DEMAND_TYPES = {k for k, v in ALERT_TYPES.items() if v["side"] == "bullish"}
SWEEP_TYPES  = {k for k, v in ALERT_TYPES.items() if v["side"] == "sweep"}

# ---------------------------------------------------------------------------
# Trading mode profiles — SCALP (fast, sensitive) vs SWING (slower, stricter)
# ---------------------------------------------------------------------------
# SCALP fires earlier and on smaller moves: lower bias gap, lower confidence
# tiers, wider "at-zone" windows, more room before "overextended", recent-window
# scoring, and BOS-only ("Attempt") structures become tradable at reduced size.
# SWING preserves the original, stricter swing-trade behaviour.
MODES = {
    "SCALP": {
        "BIAS_THRESHOLD":    2,
        "NEAR_PCT":          0.006,   # 0.6%  — Testing zone
        "EXTENDED_PCT":      0.016,   # 1.6%  — Overextended ceiling (more room to run)
        "WATCH_PCT":         0.010,   # 1.0%  — "at zone" proximity window
        "CONF_HIGH":         85,      # high-conviction tier
        "CONF_TRADE":        68,      # trade tier
        "CONF_WATCH":        58,      # watch/plan-eligible tier
        "MIN_TOTAL_SCORE":   4,       # require real confluence before TRADE/HIGH tiers
        "SCORE_WINDOW_MIN":  45,      # score only the last 45 min of alerts
        "STAGE_WINDOW_MIN":  30,      # setup-stage looks back 30 min, not last 5 alerts
        "ATTEMPT_TRADABLE":  True,    # BOS-only structures can trade (capped at BIAS)
        "RISK_MULT_ATTEMPT": 0.5,     # half size on BOS-only entries
        # Volatility regime thresholds — ratio of recent 1m ATR to the session-
        # typical range. CAUTION flags + dents the Edge Score; BLOCK holds the setup.
        "VOL_QUIET_CAUTION": 0.55,    # <= this = quiet (flag)
        "VOL_QUIET_BLOCK":   0.35,    # <= this = dead (hold)
        "VOL_HIGH_CAUTION":  1.6,     # >= this = elevated (flag)
        "VOL_HIGH_BLOCK":    2.5,     # >= this = wild (extreme)
        # SCALP: volatility is NOT a hard gate. It folds into the Edge Score as a
        # modifier (Normal +10 / Elevated 0 / Extreme -10) instead of forcing WAIT.
        "VOL_HARD_GATE":     False,
        # ── READY gate (mode-tunable). SCALP loosens the gate so it fires earlier:
        #    VWAP, structure AND zone are DEMOTED from hard gates to scoring
        #    confirmations, and >=2 confirmations are required. SWING keeps the strict
        #    zone AND vwap AND structure AND edge>=80 behaviour exactly. A loosened
        #    zone still contributes its 25pt Edge component — it just no longer
        #    hard-blocks READY in SCALP.
        #    Two-tier readiness: EDGE_READY_THRESHOLD (35) is the ACTIONABLE floor and
        #    EDGE_FULL_READY_THRESHOLD (50) the FULL-READY floor. A passing setup that
        #    scores 35-49 is an EARLY READY (labelled, lower conviction); 50+ is a full
        #    READY. SWING sets both floors to 80, so it never produces an EARLY READY. ──
        "EDGE_READY_THRESHOLD":      35,   # actionable floor (EARLY READY)
        "EDGE_FULL_READY_THRESHOLD": 50,   # full-READY floor (35-49 = EARLY READY)
        "GATE_REQUIRE_VWAP":      False,
        "GATE_REQUIRE_STRUCTURE": False,
        "GATE_REQUIRE_ZONE":      False,
        "MIN_CONFIRMATIONS":      2,
        # ── Score-aware conflict (SCALP). When opposing structure sits on BOTH sides
        #    within CONFLICT_WINDOW_MIN, take the DOMINANT side unless the two sides'
        #    Edge Scores are within CONFLICT_WAIT_GAP (then it's a true conflict →
        #    WAIT). CONFLICT_DOMINANT_GAP (>= 20 ahead) flags a clearly dominant side
        #    in the diagnostics block. SWING keeps the original always-WAIT behaviour. ──
        "CONFLICT_SCORE_AWARE":   True,
        "CONFLICT_WAIT_GAP":      10,
        "CONFLICT_DOMINANT_GAP":  20,
        # Tiered WATCH/ARMED early alerts (SCALP only — fire before a full READY).
        "ENABLE_TIERED_ALERTS":     True,
        "WATCH_ARMED_COOLDOWN_SEC": 900,
        # Dynamic ATR stops widen the stop, which lowers the fixed-target R:R. SCALP
        # no longer hard-blocks a setup purely because TP2 < 1:2 — R:R is displayed,
        # not gated. (Set True to restore the strict ">= 1:2 on TP2 or no trade" veto.)
        "ENFORCE_MIN_RR":           False,
    },
    "SWING": {
        "BIAS_THRESHOLD":    3,
        "NEAR_PCT":          0.005,   # 0.5%
        "EXTENDED_PCT":      0.010,   # 1.0%
        "WATCH_PCT":         0.0075,  # 0.75%
        "CONF_HIGH":         90,
        "CONF_TRADE":        80,
        "CONF_WATCH":        70,
        "MIN_TOTAL_SCORE":   0,
        "SCORE_WINDOW_MIN":  None,    # score full history
        "STAGE_WINDOW_MIN":  None,    # last 5 alerts
        "ATTEMPT_TRADABLE":  False,
        "RISK_MULT_ATTEMPT": 1.0,
        # Volatility thresholds — swing tolerates a bit more before holding.
        "VOL_QUIET_CAUTION": 0.50,
        "VOL_QUIET_BLOCK":   0.30,
        "VOL_HIGH_CAUTION":  1.8,
        "VOL_HIGH_BLOCK":    3.0,
        # SWING keeps volatility as a hard gate - a BLOCK regime holds READY->WAIT.
        "VOL_HARD_GATE":     True,
        # SWING keeps the original strict gate: zone AND vwap AND structure AND
        # edge>=80, with no tiered early alerts. Expressed via the same cfg keys so
        # the READY boolean reduces to the historical behaviour exactly.
        "EDGE_READY_THRESHOLD":      80,
        "EDGE_FULL_READY_THRESHOLD": 80,   # SWING: floor == full → no EARLY READY band
        "GATE_REQUIRE_VWAP":      True,
        "GATE_REQUIRE_STRUCTURE": True,
        "GATE_REQUIRE_ZONE":      True,
        "MIN_CONFIRMATIONS":      0,
        # SWING keeps the original always-WAIT-on-conflict (not score-aware).
        "CONFLICT_SCORE_AWARE":   False,
        "CONFLICT_WAIT_GAP":      0,
        "CONFLICT_DOMINANT_GAP":  0,
        "ENABLE_TIERED_ALERTS":     False,
        "WATCH_ARMED_COOLDOWN_SEC": 900,
        # SWING keeps the original strict "TP2 must be >= 1:2 R:R or no trade" veto.
        "ENFORCE_MIN_RR":           True,
    },
}

TRADING_MODE = os.environ.get("TRADING_MODE", "SCALP").upper()
if TRADING_MODE not in MODES:
    TRADING_MODE = "SCALP"


def cfg(key):
    """Read a threshold for the currently active trading mode."""
    return MODES.get(TRADING_MODE, MODES["SCALP"])[key]


# ── Verdict helpers (explicit — NEVER substring / endswith matching) ───────────
# EARLY READY (SCALP Edge 35-49) is actionable but lower-conviction than a full
# READY (Edge >= 50). Both end in the word "READY", so a stray verdict.endswith(
# "READY") would treat them identically. To make every consumer's intent explicit
# (and to keep full-conviction-only paths exact), ALL verdict checks go through
# these helpers instead of string matching.
FULL_READY_VERDICTS  = ("LONG READY", "SHORT READY")
EARLY_READY_VERDICTS = ("LONG EARLY READY", "SHORT EARLY READY")


def is_full_ready(verdict):
    """A full-conviction READY (Edge >= the full-READY floor)."""
    return verdict in FULL_READY_VERDICTS


def is_early_ready(verdict):
    """An EARLY READY (Edge in the actionable-but-below-full band; SCALP only)."""
    return verdict in EARLY_READY_VERDICTS


def is_actionable(verdict):
    """A tradeable alert — full READY OR EARLY READY (the union)."""
    return verdict in FULL_READY_VERDICTS or verdict in EARLY_READY_VERDICTS


def ready_direction(verdict):
    """Long / Short for any (full or early) READY verdict, else None."""
    if verdict in ("LONG READY", "LONG EARLY READY"):
        return "Long"
    if verdict in ("SHORT READY", "SHORT EARLY READY"):
        return "Short"
    return None


DEFAULT_ACCOUNT_SIZE = 50_000   # $50,000 — fallback when no profile/account_size given
DEFAULT_RISK_PCT     = 0.01     # 1% — fallback when no profile/risk_pct given
MGC_POINT_VALUE      = 10       # $10 per point per MGC contract (Micro Gold = 10 oz)

# ── Per-instrument trade specs (strict-recommendation ruleset) ──────────────
#   tp1 / tp2 / tp3 = fixed target distances in price points from the entry zone
#   stop_buf    = buffer placed BEYOND the demand/supply zone for the structure stop
#   point_value = $ per point per contract  (MNQ Micro Nasdaq = $2, MGC Micro Gold = $10)
#   tick_size   = minimum price increment (MGC 0.1, MNQ 0.25)
#   min_stop_ticks = floor on the dynamic-stop distance so it can never be
#                    unrealistically tight (MGC 50, MNQ 40 — both env-overridable)
def _spec_int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)

INSTRUMENT_SPECS = {
    "MNQ": {"tp1": 20.0, "tp2": 40.0, "tp3": 60.0, "stop_buf": 5.0, "point_value": 2.0,
            "tick_size": 0.25, "min_stop_ticks": _spec_int_env("MNQ_MIN_STOP_TICKS", 40)},
    "MGC": {"tp1": 5.0,  "tp2": 10.0, "tp3": 15.0, "stop_buf": 1.0, "point_value": 10.0,
            "tick_size": 0.1,  "min_stop_ticks": _spec_int_env("MGC_MIN_STOP_TICKS", 50)},
}

def instrument_of(ticker):
    """Normalize any raw ticker (e.g. 'MNQ1!', 'MGC') to 'MNQ' or 'MGC'.

    NOTE: this is the *lenient* legacy normalizer — anything that does not
    contain 'MNQ' (including None/empty/unknown) silently becomes 'MGC'. It is
    safe only for display/legacy fallbacks. For ingesting a TradingView alert
    use resolve_instrument(), which is ticker-first and never silently defaults.
    """
    return "MNQ" if "MNQ" in str(ticker or "").upper() else "MGC"

def _instrument_from_text(value):
    """Return 'MNQ'/'MGC' iff `value` unambiguously names exactly one of them,
    else None (neither present, or — defensively — both present)."""
    s = str(value or "").upper()
    has_mnq, has_mgc = "MNQ" in s, "MGC" in s
    if has_mnq and not has_mgc:
        return "MNQ"
    if has_mgc and not has_mnq:
        return "MGC"
    return None

def resolve_instrument(ticker_field, alert_type):
    """Authoritative instrument resolution for an incoming TradingView alert.

    The payload `ticker` field is the source of truth; the alert title is
    consulted ONLY when the ticker field is absent. Guarantees:
      • MGC tickers always resolve to MGC, MNQ tickers always resolve to MNQ.
      • Title parsing is never used when a usable ticker field is present.
      • Nothing silently defaults to MGC — unresolvable alerts are rejected.

    Returns a dict:
      instrument     : 'MGC' | 'MNQ' | None   (None ⇒ caller must reject)
      source         : 'ticker' | 'title' | None
      ticker_present : bool
      ok             : bool                    (False ⇒ reject the alert)
      error          : str | None              (human-readable reject reason)
    """
    ticker_present = bool(str(ticker_field or "").strip())
    from_ticker    = _instrument_from_text(ticker_field)
    from_title     = _instrument_from_text(alert_type)

    # 1) Ticker field present and recognized → authoritative.
    if from_ticker is not None:
        if from_title is not None and from_title != from_ticker:
            return {"instrument": None, "source": "ticker", "ticker_present": True,
                    "ok": False,
                    "error": (f"ticker '{ticker_field}' ({from_ticker}) contradicts "
                              f"alert title '{alert_type}' ({from_title})")}
        return {"instrument": from_ticker, "source": "ticker",
                "ticker_present": True, "ok": True, "error": None}

    # 2) Ticker field present but unrecognized (neither MGC nor MNQ) → reject.
    if ticker_present:
        return {"instrument": None, "source": "ticker", "ticker_present": True,
                "ok": False, "error": f"unrecognized ticker '{ticker_field}'"}

    # 3) No ticker field → fall back to the title prefix (allowed only when the
    #    ticker is unavailable). Instrument-prefixed alert types resolve here.
    if from_title is not None:
        return {"instrument": from_title, "source": "title",
                "ticker_present": False, "ok": True, "error": None}

    # 4) Shared alert (BOS/CHOCH/HH/HL/LH/LL) with no ticker → genuinely unresolvable.
    return {"instrument": None, "source": None, "ticker_present": False,
            "ok": False,
            "error": ("missing ticker field on a shared alert — BOS/CHOCH/HH/HL/LH/LL "
                      "carry no instrument in the title and cannot be attributed")}

def spec_for(ticker):
    return INSTRUMENT_SPECS[instrument_of(ticker)]

def point_value_for(ticker):
    return INSTRUMENT_SPECS[instrument_of(ticker)]["point_value"]

def current_price_for(ticker):
    """Latest alert-driven price for a specific instrument (None if none seen).
    Strictly per-instrument — never falls back across instruments, so an MNQ view
    cannot show MGC's price against MNQ's VWAP."""
    return CURRENT_PRICE_BY_TICKER.get(instrument_of(ticker))


def display_price_for(ticker):
    """Best price to SHOW on the dashboard — returns (value, source). DISPLAY-ONLY:
    never read by the gate / scoring, which always use the authoritative
    alert-driven current_price_for().

    The alert/chart price is authoritative while fresh (within PRICE_FRESH_MIN);
    once it goes stale — or was never received, e.g. right after a deploy/restart —
    we fall back to the auto-fetched market price so the readout always shows a live
    number instead of a blank. source is "alert", "auto", "stale" or None."""
    inst = instrument_of(ticker)
    alert_p = CURRENT_PRICE_BY_TICKER.get(inst)
    ts = CURRENT_PRICE_TS_BY_TICKER.get(inst)
    fresh = False
    if alert_p is not None and ts:
        try:
            age_min = (now_utc() - datetime.fromisoformat(ts)).total_seconds() / 60.0
            fresh = age_min <= PRICE_FRESH_MIN
        except (ValueError, TypeError):
            fresh = True  # unparseable ts → treat the alert price as current
    if alert_p is not None and fresh:
        return alert_p, "alert"
    auto = AUTO_PRICE_BY_TICKER.get(inst)
    if auto and auto.get("value") is not None:
        return auto["value"], "auto"
    if alert_p is not None:
        return alert_p, "stale"  # last resort: a stale price beats nothing
    return None, None


def last_valid_data_for(ticker):
    """(price, iso_ts, source) of the last KNOWN market price for an instrument —
    used for the 'last valid data' readout while the market is CLOSED. Prefers the
    last alert/chart price AND its timestamp (the last real tape the bot saw, which
    is what 'last valid' should reflect); falls back to the auto-fetched value only
    when no alert has ever been received. DISPLAY-ONLY — never read by the gate."""
    inst    = instrument_of(ticker)
    alert_p = CURRENT_PRICE_BY_TICKER.get(inst)
    if alert_p is not None:
        return alert_p, CURRENT_PRICE_TS_BY_TICKER.get(inst), "alert"
    auto = AUTO_PRICE_BY_TICKER.get(inst) or {}
    if auto.get("value") is not None:
        return auto.get("value"), auto.get("ts"), "auto"
    return None, None, None


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
# Optional dedicated channel for 🔥 A+ setups. When unset, A+ alerts stay in the
# normal per-instrument channel and are simply labelled — never a required secret.
DISCORD_APLUS_WEBHOOK_URL   = os.environ.get("DISCORD_APLUS_WEBHOOK_URL", "")
# Optional mobile-ping mention prepended to the LIVE trade card the instant a new
# setup goes READY (and ONLY then — never on heartbeats, re-posts, zone notices,
# or journal updates). Set Discord notifications to "Only @mentions" and this is
# the single message that buzzes your phone. Defaults to @everyone so a personal
# trading server works with zero setup; override with a user/role mention
# (e.g. "<@123456789012345678>") to ping just yourself. Blank = no ping at all.
DISCORD_ALERT_MENTION       = os.environ.get("DISCORD_ALERT_MENTION", "@everyone").strip()

# ── Live-instance gate ────────────────────────────────────────────────────────
# The Discord webhook secrets are shared between the Replit workspace (dev) and
# the published deployment (prod). Both run this exact file, so without a gate the
# dev instance and the prod instance BOTH post the time-based check-ins (heartbeat,
# EOD, weekly, trade-ready re-post) to the SAME live channel — the user sees every
# scheduled alert twice. Only the production instance is the "live" sender.
#   • REPLIT_DEPLOYMENT == "1"  → set by Replit inside deployments, unset in the
#     workspace, so it cleanly distinguishes prod from dev.
#   • DISCORD_LIVE == "1"       → explicit override exported by scripts/prod-start.sh
#     (belt-and-suspenders in case the deployment entrypoint changes).
# Webhook-driven alerts are unaffected: TradingView only POSTs to the production
# URL, so the dev instance never receives a real alert to forward.
DISCORD_LIVE_ENABLED = (
    os.environ.get("REPLIT_DEPLOYMENT") == "1"
    or os.environ.get("DISCORD_LIVE") == "1"
)


def _discord_url(hint: str = "") -> str:
    """Return the correct trade-alert webhook URL based on symbol hint.

    If the hint contains 'MNQ' (case-insensitive) and a dedicated MNQ URL is
    configured, returns that; otherwise falls back to the MGC/default URL.
    """
    if "MNQ" in str(hint).upper() and DISCORD_MNQ_WEBHOOK_URL:
        return DISCORD_MNQ_WEBHOOK_URL
    return DISCORD_WEBHOOK_URL


HEARTBEAT_INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", 300))  # seconds (default 5 min)
# Recurring "trade ready" alert: re-post the clean card every 5 min while a setup
# is READY (in addition to the instant alert on the triggering webhook).
TRADE_READY_INTERVAL = int(os.environ.get("TRADE_READY_INTERVAL", 300))  # seconds (default 5 min)

# ── EARLY intrabar pre-READY alert config (additive, DISPLAY-ONLY) ────────────
# An ⚡ EARLY LONG/SHORT fires the instant a liquidity sweep + a structure shift
# (CHOCH / BOS / HH-HL-LH-LL "displacement") appear together in the window —
# BEFORE the 5m candle-close confirmation that the strict READY card waits for.
# It is PURELY ADDITIVE: it never touches evaluate_strict_setup, the READY
# verdict/score/SWING parity, the journal, or managed trades. READY remains the
# single confirmed signal; EARLY is an unconfirmed heads-up that precedes it.
EARLY_ALERTS_ENABLED = os.environ.get("ENABLE_EARLY_ALERTS", "true").strip().lower() in ("1", "true", "yes", "on")
# Where EARLY posts: "main" (default, the live signal channel) | "journal" | "none".
EARLY_ALERT_CHANNEL  = os.environ.get("EARLY_ALERT_CHANNEL", "main").strip().lower()
# Phone ping (@mention) on EARLY — OFF by default so a speculative early signal
# doesn't buzz; the confirmed READY card keeps its own ping. Flip on to ping EARLY.
EARLY_ALERT_PING     = os.environ.get("EARLY_ALERT_PING", "false").strip().lower() in ("1", "true", "yes", "on")
# How recent (minutes) the sweep AND structure must both be to form an EARLY setup.
EARLY_WINDOW_MIN     = float(os.environ.get("EARLY_WINDOW_MIN", 10))
# Secondary guard against re-firing the same setup within this many seconds (the
# per-setup dedupe is primary; this just bounds edge cases).
EARLY_ALERT_COOLDOWN_SEC = int(os.environ.get("EARLY_ALERT_COOLDOWN_SEC", 180))

# ── Heartbeat (periodic) market re-evaluation (additive, DIAGNOSTIC-ONLY) ──────
# Re-scores every instrument on a fixed cadence so the Diagnostics page reflects a
# live, continuously-updated read instead of only the (infrequent) TradingView
# webhooks. It calls full_analysis + records eval metrics ONLY — it NEVER posts to
# Discord, journals, or sends a live/EARLY/tiered alert (those stay driven by the
# webhook path + _trade_ready_loop), so it is safe on BOTH dev and prod and can
# never change a READY/WAIT verdict or double-post. Distinct from the Discord
# check-in HEARTBEAT_INTERVAL above.
EVAL_HEARTBEAT_ENABLED  = os.environ.get("EVAL_HEARTBEAT_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
EVAL_HEARTBEAT_INTERVAL = int(os.environ.get("EVAL_HEARTBEAT_INTERVAL", 15))  # seconds (default 15s)
# ── Inbound signal de-dup + setup-state lifecycle (additive, DIAGNOSTIC) ──────
# How long the SAME (instrument, alert_type) is treated as a repeat of the same
# signal (TradingView now repeats alerts every minute while the condition holds).
# Per-instrument now: MNQ moves faster than MGC, so MNQ de-dups for 60s and MGC for
# 90s (was a single 240s scalar — far too long for SCALP, it swallowed live repeats).
# A global SIGNAL_DEDUP_COOLDOWN_SEC env (if set > 0) overrides BOTH as one knob.
_SIGNAL_DEDUP_COOLDOWN_OVERRIDE = int(os.environ.get("SIGNAL_DEDUP_COOLDOWN_SEC", 0))
SIGNAL_DEDUP_COOLDOWN_MNQ = int(os.environ.get("SIGNAL_DEDUP_COOLDOWN_MNQ", 60))   # MNQ default 60s
SIGNAL_DEDUP_COOLDOWN_MGC = int(os.environ.get("SIGNAL_DEDUP_COOLDOWN_MGC", 90))   # MGC default 90s
# Back-compat scalar for non-instrument-scoped callers/diagnostics: the override if
# set, else the MNQ value (the shorter of the two).
SIGNAL_DEDUP_COOLDOWN_SEC = _SIGNAL_DEDUP_COOLDOWN_OVERRIDE or SIGNAL_DEDUP_COOLDOWN_MNQ


def signal_dedup_cooldown_sec(instrument):
    """Per-instrument de-dup cooldown (seconds). A global SIGNAL_DEDUP_COOLDOWN_SEC
    env (>0) wins for every instrument (single-knob override); otherwise MNQ=60s,
    MGC=90s (each env-overridable). Only MNQ-resolving tickers get the 60s value;
    every other ticker (including unknowns, which instrument_of() maps to MGC)
    falls back to the longer, more conservative 90s MGC value."""
    if _SIGNAL_DEDUP_COOLDOWN_OVERRIDE:
        return _SIGNAL_DEDUP_COOLDOWN_OVERRIDE
    return SIGNAL_DEDUP_COOLDOWN_MGC if instrument_of(instrument) == "MGC" else SIGNAL_DEDUP_COOLDOWN_MNQ
# How long a non-ACTIVE setup (FORMING/READY) or an ACTIVE setup may persist before
# the per-instrument lifecycle marks it EXPIRED (display-only; never gates).
SETUP_STATE_TTL_SEC       = int(os.environ.get("SETUP_STATE_TTL_SEC", 1800))  # default 30 min
EOD_HOUR_UTC       = int(os.environ.get("EOD_HOUR_UTC", 21))  # default 21:00 UTC = 4 PM ET
# ── Weekly performance report (additive) ──────────────────────────────────────
# Posts a week-in-review embed once a week, just after the close. Default is
# Friday (weekday()==4) at 21:15 UTC — 15 min after the daily EOD so the two
# never collide. All knobs are optional env overrides.
WEEKLY_REPORT_DOW       = int(os.environ.get("WEEKLY_REPORT_DOW", 4))   # Mon=0 … Sun=6, default Fri
WEEKLY_REPORT_HOUR_UTC  = int(os.environ.get("WEEKLY_REPORT_HOUR_UTC", EOD_HOUR_UTC))
WEEKLY_REPORT_MINUTE    = int(os.environ.get("WEEKLY_REPORT_MINUTE", 15))
WEEKLY_REPORT_DAYS      = int(os.environ.get("WEEKLY_REPORT_DAYS", 7))  # lookback window in days

# ── Automatic VWAP fetch ──────────────────────────────────────────────────────
# Session VWAP is pulled from a public market-data feed so the operator never has
# to type it. A VWAP pushed from a TradingView chart (source "chart") is exact and
# wins for VWAP_OVERRIDE_GRACE_MIN minutes; after that the auto value resumes.
VWAP_FETCH_INTERVAL    = int(os.environ.get("VWAP_FETCH_INTERVAL", 60))   # seconds
PRICE_FETCH_INTERVAL   = int(os.environ.get("PRICE_FETCH_INTERVAL", 10))  # seconds — DISPLAY-ONLY price refresh (faster than VWAP so the dashboard price stays near-live)
VWAP_OVERRIDE_GRACE_MIN = int(os.environ.get("VWAP_OVERRIDE_GRACE_MIN", 10))  # minutes
# How long an alert/chart price stays the AUTHORITATIVE dashboard readout after it
# arrives. While fresh, the dashboard shows the exact chart price; once it goes
# stale (no alerts) the readout falls back to the auto-fetched market price so it
# keeps moving. Display-only — never affects the gate.
PRICE_FRESH_MIN = float(os.environ.get("PRICE_FRESH_MIN", 5))  # minutes
# MGC (micro gold) ≈ GC=F, MNQ (micro Nasdaq) ≈ NQ=F — same price, so same VWAP.
VWAP_FEED_SYMBOL = {"MGC": "GC=F", "MNQ": "NQ=F"}


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
        last_str = f"{fmt_et(LAST_ALERT_AT, '%H:%M ET')}  ({age})"
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
        "footer": {"text": "Check-in  ·  " + fmt_et(now, "%Y-%m-%d %H:%M ET")},
    }

    for url in filter(None, [DISCORD_WEBHOOK_URL, DISCORD_MNQ_WEBHOOK_URL]):
        try:
            requests.post(url, json={"embeds": [embed]}, timeout=5)
        except Exception:
            pass
    logger.info("Heartbeat sent.")


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

    # Best = highest-Edge win; fallback any entry. Ranking uses the internal
    # _edge_score_for_entry (legacy fallback ok to order old entries); the figure
    # shown for the chosen setup is sanitized to transparent-only by _fmt_setup.
    best  = max(wins,    key=_edge_score_for_entry, default=None) or \
            max(entries, key=_edge_score_for_entry, default=None)
    # Worst = most recent loss; fallback lowest-Edge closed entry
    closed = [e for e in entries if not e.get("outcome","").startswith("Pending")]
    worst = losses[0] if losses else \
            (min(closed, key=_edge_score_for_entry, default=None))

    # ── Additive recap metrics ──
    longs  = [e for e in entries if e.get("direction") == "Long"]
    shorts = [e for e in entries if e.get("direction") == "Short"]
    win_rate = (len(wins) / trades_entered * 100.0) if trades_entered else None

    def _conf_key(e):
        sc = e.get("strict_score")
        try:
            return float(sc)
        except (TypeError, ValueError):
            return _edge_score_for_entry(e)
    highest_conf = max(entries, key=_conf_key, default=None)

    # ── Trade-strength split + Edge Score stats (READY journal entries) ──
    possible = [e for e in entries if _entry_trade_strength(e) == "Possible Trade"]
    strong   = [e for e in entries if _entry_trade_strength(e) == "Strong Trade"]
    aplus    = [e for e in entries if _entry_trade_strength(e) == "A+ Setup"]
    ready_entries = possible + strong + aplus
    # Average Edge is a DISPLAYED figure → transparent scores only ("—" entries
    # are excluded so a legacy bias-derived number can never skew the average).
    edge_vals = [v for e in ready_entries
                 if isinstance((v := _display_edge_score(e)), (int, float))]
    avg_edge  = round(sum(edge_vals) / len(edge_vals), 1) if edge_vals else None
    highest_edge = max(ready_entries, key=_edge_score_for_entry, default=None)
    lowest_edge  = min(ready_entries, key=_edge_score_for_entry, default=None)

    # Per-instrument net P&L (only over entries with a known P&L).
    inst_pnl = {}
    for e in entries:
        if "pnl_dollars" not in e:
            continue
        inst = instrument_of(e.get("symbol", ""))
        inst_pnl[inst] = inst_pnl.get(inst, 0.0) + e["pnl_dollars"]
    best_instrument  = max(inst_pnl, key=inst_pnl.get) if inst_pnl else None
    worst_instrument = min(inst_pnl, key=inst_pnl.get) if inst_pnl else None

    return {
        "date":             today,
        "trades_flagged":   len(entries),
        "trades_entered":   trades_entered,
        "wins":             len(wins),
        "losses":           len(losses),
        "net_pnl":          round(pnl_total, 2) if has_pnl else None,
        "best":             best,
        "worst":            worst,
        # ── Additive ──
        "total_setups":     len(entries),
        "long_setups":      len(longs),
        "short_setups":     len(shorts),
        "win_rate":         win_rate,
        "highest_conf":     highest_conf,
        "best_instrument":  best_instrument,
        "worst_instrument": worst_instrument,
        "inst_pnl":         inst_pnl,
        # ── Trade-strength + Edge Score (additive) ──
        "possible_count":   len(possible),
        "strong_count":     len(strong),
        "aplus_count":      len(aplus),
        "avg_edge_score":   avg_edge,
        "highest_edge":     highest_edge,
        "lowest_edge":      lowest_edge,
    }


def _fmt_setup(entry):
    if not entry:
        return "—"
    return (
        f"{entry.get('symbol','—')} {entry.get('direction','—')}  ·  "
        f"Edge {_display_edge_score(entry)}  ·  {entry.get('outcome','—')}"
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

    win_rate_str = f"{stats['win_rate']:.0f}%" if stats.get("win_rate") is not None else "—"

    def _fmt_inst(inst):
        if not inst:
            return "—"
        pnl = stats.get("inst_pnl", {}).get(inst)
        if pnl is None:
            return inst
        sign = f"+${pnl:,.0f}" if pnl >= 0 else f"-${abs(pnl):,.0f}"
        return f"{inst}  ·  {sign}"

    embed = {
        "color":       color,
        "author":      {"name": BOT_NAME},
        "title":       "📊 End of Day Summary",
        "description": fmt_et(now, "%A, %B %-d, %Y"),
        "fields": [
            {"name": "Total setups",    "value": str(stats["total_setups"]),   "inline": True},
            {"name": "Long setups",     "value": str(stats["long_setups"]),    "inline": True},
            {"name": "Short setups",    "value": str(stats["short_setups"]),   "inline": True},
            {"name": "Trades taken",    "value": str(stats["trades_entered"]), "inline": True},
            {"name": "Win rate",        "value": win_rate_str,                 "inline": True},
            {"name": "Net P&L",         "value": f"**{pnl_str}**",            "inline": True},
            {"name": "Wins ✅",         "value": str(stats["wins"]),           "inline": True},
            {"name": "Losses ❌",       "value": str(stats["losses"]),         "inline": True},
            {"name": "\u200b",          "value": "\u200b",                     "inline": True},
            {"name": "Best instrument",  "value": _fmt_inst(stats.get("best_instrument")),  "inline": True},
            {"name": "Worst instrument", "value": _fmt_inst(stats.get("worst_instrument")), "inline": True},
            {"name": "\u200b",          "value": "\u200b",                     "inline": True},
            {"name": "🟡 Possible trades", "value": str(stats.get("possible_count", 0)), "inline": True},
            {"name": "🟢 Strong trades",   "value": str(stats.get("strong_count", 0)),   "inline": True},
            {"name": "🔥 A+ setups",       "value": str(stats.get("aplus_count", 0)),    "inline": True},
            {"name": "⚡ Avg Edge Score",  "value": (f"{stats['avg_edge_score']:.1f}" if stats.get("avg_edge_score") is not None else "—"), "inline": True},
            {"name": "🔼 Highest Edge Score", "value": _fmt_setup(stats.get("highest_edge")), "inline": False},
            {"name": "🔽 Lowest Edge Score",  "value": _fmt_setup(stats.get("lowest_edge")),  "inline": False},
            {"name": "🔥 Highest-confidence setup", "value": _fmt_setup(stats.get("highest_conf")), "inline": False},
            {"name": "Best setup",      "value": _fmt_setup(stats["best"]),    "inline": False},
            {"name": "Worst setup",     "value": _fmt_setup(stats["worst"]),   "inline": False},
        ],
        "footer": {"text": "EOD  ·  " + fmt_et(now, "%Y-%m-%d %H:%M ET")},
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


# ───────────────────────────────────────────────────────────────────────────
# Weekly performance report (Feature 4, additive). Mirrors the EOD scheduler but
# fires once a week, just after the close. Reuses compute_performance_stats for
# the core W/L/BE/PF/Avg-R numbers, then layers week-specific extras (net P&L,
# net R, best/worst setup/instrument/direction, A+ split). Fully fail-open.
# ───────────────────────────────────────────────────────────────────────────

def _weekly_entries(days=None):
    """Journal entries whose timestamp falls within the lookback window."""
    days   = WEEKLY_REPORT_DAYS if days is None else days
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for e in JOURNAL:
        ts = e.get("datetime", "")
        try:
            dt = datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt >= cutoff:
            out.append(e)
    return out


def _entry_realized_r(entry, state):
    """Prefer the watcher's exact R (r_multiple) when present, else the proxy."""
    r = entry.get("r_multiple")
    try:
        if r is not None:
            return float(r)
    except (TypeError, ValueError):
        pass
    return _realized_r(entry, state)


def _compute_weekly_stats(days=None):
    """Derive week-in-review analytics from JOURNAL over the lookback window."""
    week = _weekly_entries(days)
    perf = compute_performance_stats(week)

    net_pnl, has_pnl, r_total = 0.0, False, 0.0
    inst_pnl, dir_pnl = {}, {}
    wins_list, losses_list = [], []

    for e in week:
        pnl   = e.get("pnl_dollars")
        state = _outcome_state(e.get("outcome"), pnl)
        if state is None:
            continue  # still open — not part of realized weekly stats
        r_total += _entry_realized_r(e, state)
        if state == "win":
            wins_list.append(e)
        elif state == "loss":
            losses_list.append(e)
        if pnl is not None:
            try:
                pnl_f = float(pnl)
            except (TypeError, ValueError):
                continue
            net_pnl += pnl_f
            has_pnl = True
            inst = instrument_of(e.get("symbol", ""))
            inst_pnl[inst] = inst_pnl.get(inst, 0.0) + pnl_f
            d = e.get("direction") or "—"
            dir_pnl[d] = dir_pnl.get(d, 0.0) + pnl_f

    best  = max(wins_list,   key=_edge_score_for_entry, default=None)
    worst = (losses_list[0] if losses_list
             else min(wins_list, key=_edge_score_for_entry, default=None))

    best_instrument  = max(inst_pnl, key=inst_pnl.get) if inst_pnl else None
    worst_instrument = min(inst_pnl, key=inst_pnl.get) if inst_pnl else None
    best_direction   = max(dir_pnl, key=dir_pnl.get) if dir_pnl else None
    worst_direction  = min(dir_pnl, key=dir_pnl.get) if dir_pnl else None

    ready = [e for e in week if _entry_trade_strength(e) in
             ("Possible Trade", "Strong Trade", "A+ Setup")]
    # Displayed average → transparent scores only (see _compute_eod_stats).
    edge_vals = [v for e in ready
                 if isinstance((v := _display_edge_score(e)), (int, float))]
    avg_edge  = round(sum(edge_vals) / len(edge_vals), 1) if edge_vals else None

    possible = sum(1 for e in week if _entry_trade_strength(e) == "Possible Trade")
    strong   = sum(1 for e in week if _entry_trade_strength(e) == "Strong Trade")
    aplus    = sum(1 for e in week if _entry_trade_strength(e) == "A+ Setup")

    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=(WEEKLY_REPORT_DAYS if days is None else days))
    return {
        "start":            cutoff.date().isoformat(),
        "end":              now.date().isoformat(),
        "total_setups":     len(week),
        "wins":             perf["wins"],
        "losses":           perf["losses"],
        "breakevens":       perf["breakevens"],
        "decided":          perf["decided"],
        "closed":           perf["closed"],
        "win_rate":         perf["win_rate"],
        "profit_factor":    perf["profit_factor"],
        "gross_win":        perf["gross_win"],
        "gross_loss":       perf["gross_loss"],
        "avg_r":            perf["avg_r"],
        "net_r":            round(r_total, 2),
        "net_pnl":          round(net_pnl, 2) if has_pnl else None,
        "best":             best,
        "worst":            worst,
        "best_instrument":  best_instrument,
        "worst_instrument": worst_instrument,
        "inst_pnl":         inst_pnl,
        "best_direction":   best_direction,
        "worst_direction":  worst_direction,
        "dir_pnl":          dir_pnl,
        "avg_edge_score":   avg_edge,
        "possible_count":   possible,
        "strong_count":     strong,
        "aplus_count":      aplus,
        "by_strength":      perf["by_strength"],
    }


def _send_weekly_report(days=None):
    """Build and post the weekly performance embed to all configured channels."""
    s   = _compute_weekly_stats(days)
    now = datetime.now(timezone.utc)

    pnl_val = s["net_pnl"]
    if pnl_val is None:
        pnl_str, color = "—", 0x5865F2
    elif pnl_val >= 0:
        pnl_str, color = f"+${pnl_val:,.0f}", 0x2ECC71
    else:
        pnl_str, color = f"-${abs(pnl_val):,.0f}", 0xE74C3C

    wr   = f"{s['win_rate']:.0f}%" if s.get("win_rate") is not None else "—"
    if s["profit_factor"] is not None:
        pf = f"{s['profit_factor']:.2f}"
    else:
        pf = "∞" if s.get("gross_win", 0) > 0 else "—"
    avgr = f"{s['avg_r']:+.2f}R" if s.get("avg_r") is not None else "—"
    netr = f"{s['net_r']:+.2f}R"

    def _fmt_inst(inst, pool):
        if not inst:
            return "—"
        pnl = pool.get(inst)
        if pnl is None:
            return str(inst)
        sign = f"+${pnl:,.0f}" if pnl >= 0 else f"-${abs(pnl):,.0f}"
        return f"{inst}  ·  {sign}"

    def _fmt_strength(k):
        b = s.get("by_strength", {}).get(k)
        if not b or b.get("closed", 0) == 0:
            return f"{k}: —"
        wr_b = f"{b['win_rate']:.0f}%" if b.get("win_rate") is not None else "—"
        ar_b = f"{b['avg_r']:+.2f}R" if b.get("avg_r") is not None else "—"
        return (f"**{k}**: {b['wins']}W / {b['losses']}L / {b['breakevens']}BE  ·  "
                f"WR {wr_b} · Avg {ar_b}")
    strength_text = "\n".join(_fmt_strength(k) for k in
                              ("Possible Trade", "Strong Trade", "A+ Setup"))

    embed = {
        "color":       color,
        "author":      {"name": f"{BOT_NAME} · Weekly"},
        "title":       "🗓️ Weekly Performance Report",
        "description": f"{s['start']} → {s['end']}",
        "fields": [
            {"name": "Total setups",   "value": str(s["total_setups"]),  "inline": True},
            {"name": "Trades decided", "value": str(s["decided"]),       "inline": True},
            {"name": "Win rate",       "value": wr,                      "inline": True},
            {"name": "Wins ✅",        "value": str(s["wins"]),          "inline": True},
            {"name": "Losses ❌",      "value": str(s["losses"]),        "inline": True},
            {"name": "Breakeven ⚖️",   "value": str(s["breakevens"]),    "inline": True},
            {"name": "Profit Factor",  "value": pf,                      "inline": True},
            {"name": "Avg R",          "value": avgr,                    "inline": True},
            {"name": "Net R",          "value": netr,                    "inline": True},
            {"name": "Net P&L",        "value": f"**{pnl_str}**",        "inline": True},
            {"name": "⚡ Avg Edge",    "value": (f"{s['avg_edge_score']:.1f}"
                                                 if s.get("avg_edge_score") is not None else "—"),
                                                                          "inline": True},
            {"name": "\u200b",         "value": "\u200b",                "inline": True},
            {"name": "Best instrument",  "value": _fmt_inst(s.get("best_instrument"),  s.get("inst_pnl", {})), "inline": True},
            {"name": "Worst instrument", "value": _fmt_inst(s.get("worst_instrument"), s.get("inst_pnl", {})), "inline": True},
            {"name": "\u200b",           "value": "\u200b",              "inline": True},
            {"name": "Best direction",   "value": _fmt_inst(s.get("best_direction"),  s.get("dir_pnl", {})),  "inline": True},
            {"name": "Worst direction",  "value": _fmt_inst(s.get("worst_direction"), s.get("dir_pnl", {})),  "inline": True},
            {"name": "\u200b",           "value": "\u200b",              "inline": True},
            {"name": "🟡 Possible",  "value": str(s.get("possible_count", 0)), "inline": True},
            {"name": "🟢 Strong",    "value": str(s.get("strong_count", 0)),   "inline": True},
            {"name": "🔥 A+ setups", "value": str(s.get("aplus_count", 0)),    "inline": True},
            {"name": "🎯 By Trade Strength", "value": strength_text[:1024], "inline": False},
            {"name": "🔼 Best setup",  "value": _fmt_setup(s.get("best")),  "inline": False},
            {"name": "🔽 Worst setup", "value": _fmt_setup(s.get("worst")), "inline": False},
        ],
        "footer": {"text": "Weekly  ·  " + fmt_et(now, "%Y-%m-%d %H:%M ET")},
    }

    for url in filter(None, [DISCORD_WEBHOOK_URL, DISCORD_MNQ_WEBHOOK_URL, DISCORD_JOURNAL_WEBHOOK_URL]):
        try:
            requests.post(url, json={"embeds": [embed]}, timeout=5)
        except Exception:
            pass
    logger.info("Weekly report sent (%s → %s).", s["start"], s["end"])
    return s


def _schedule_weekly_report():
    """Schedule the weekly report for the next configured weekday + time (UTC)."""
    now = datetime.now(timezone.utc)
    days_ahead = (WEEKLY_REPORT_DOW - now.weekday()) % 7
    fire = now.replace(hour=WEEKLY_REPORT_HOUR_UTC, minute=WEEKLY_REPORT_MINUTE,
                       second=0, microsecond=0) + timedelta(days=days_ahead)
    if fire <= now:
        fire += timedelta(days=7)
    delay = (fire - now).total_seconds()
    logger.info("Weekly report scheduled for %s UTC (in %.0fs).",
                fire.strftime("%a %H:%M"), delay)

    def _run():
        try:
            _send_weekly_report()
        except Exception as exc:
            logger.error("Weekly report error: %s", exc)
        _schedule_weekly_report()

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


# US Eastern time for DISPLAY only — storage stays UTC. ZoneInfo handles the
# EST/EDT daylight-saving switch automatically (e.g. EDT in summer, EST in winter).
ET_TZ = ZoneInfo("America/New_York")


def fmt_et(value, fmt="%Y-%m-%d %H:%M ET"):
    """Format a UTC datetime or ISO-8601 string in US Eastern time for display.

    Accepts an aware/naive datetime (naive is treated as UTC) or an ISO string.
    Returns "" for None and echoes back an unparseable string unchanged.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ET_TZ).strftime(fmt)


# ── Preferred trading-session windows (Eastern Time) ─────────────────────────
# A READY setup inside one of these windows earns a +10 Edge Score "Session
# Bonus" so the bot prioritizes the most active hours. The windows + bonus are
# display/score only — they NEVER change the READY/WAIT gate. ET handles the
# EST/EDT daylight-saving switch automatically.
SESSION_WINDOWS = (
    ("05:00–08:00 ET", 5.0, 8.0),
    ("08:00–11:00 ET", 8.0, 11.0),
    ("20:00–23:00 ET", 20.0, 23.0),
)
SESSION_BONUS_POINTS = 10


def get_session_state(now=None):
    """Preferred-trading-window state for a UTC instant (now defaults to UTC now).

    Returns {"preferred": bool, "bonus": int, "window": str}. `bonus` is +10
    inside a preferred window, else 0. Pure function of the clock — safe to call
    from scoring, status, why, and the card without side effects.
    """
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    et   = base.astimezone(ET_TZ)
    hour = et.hour + et.minute / 60.0
    for label, start, end in SESSION_WINDOWS:
        if start <= hour < end:
            return {"preferred": True, "bonus": SESSION_BONUS_POINTS, "window": label}
    return {"preferred": False, "bonus": 0, "window": "Outside preferred window"}


# ── Market session (CME/COMEX futures hours) ─────────────────────────────────
# MNQ (CME Globex) and MGC (COMEX) share the same electronic schedule: the week
# opens Sunday 18:00 ET and runs continuously to Friday 17:00 ET, with a daily
# maintenance halt 17:00–18:00 ET (Mon–Thu). While the market is CLOSED there is
# no live tape, so we PAUSE the READY/ARMED gate and the diagnostic eval counters
# rather than report a quiet market as a stream of "failed" setups. Hours are in
# ET so the EST/EDT switch is automatic. Set MARKET_HOURS_ENABLED=0 to disable the
# pause entirely (e.g. replaying weekend test data) — every downstream consumer
# then behaves exactly as it did before this feature.
MARKET_HOURS_ENABLED = os.environ.get("MARKET_HOURS_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
MARKET_OPEN_HOUR_ET  = 18   # weekly open (Sun) + daily reopen after maintenance
MARKET_CLOSE_HOUR_ET = 17   # daily maintenance start (Mon–Thu) + Friday weekly close


def market_session_status(now=None):
    """CME/COMEX futures session state for MNQ & MGC (shared schedule).

    Pure function of the clock (no side effects) — safe to call from the gate,
    status, heartbeat and dashboard. `now` defaults to UTC now. Returns:
      {"open": bool, "status": "OPEN"|"CLOSED",
       "next_open": datetime|None (UTC, aware), "next_open_et": str,
       "reason": str}
    When MARKET_HOURS_ENABLED is False it always reports OPEN, so every downstream
    consumer behaves exactly as it did before this feature was added."""
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    if not MARKET_HOURS_ENABLED:
        return {"open": True, "status": "OPEN", "next_open": None,
                "next_open_et": "", "reason": ""}

    et   = base.astimezone(ET_TZ)
    wd   = et.weekday()                  # Mon=0 … Sun=6
    hour = et.hour + et.minute / 60.0

    if wd == 5:                                       # Saturday — closed all day
        is_open = False
    elif wd == 6:                                     # Sunday — opens 18:00 ET
        is_open = hour >= MARKET_OPEN_HOUR_ET
    elif wd == 4:                                     # Friday — closes 17:00 ET
        is_open = hour < MARKET_CLOSE_HOUR_ET
    else:                                             # Mon–Thu — 17:00–18:00 halt
        is_open = not (MARKET_CLOSE_HOUR_ET <= hour < MARKET_OPEN_HOUR_ET)

    if is_open:
        return {"open": True, "status": "OPEN", "next_open": None,
                "next_open_et": "", "reason": ""}

    # ── Closed: compute the next reopen instant (ET → UTC) ───────────────────
    in_daily_halt = (wd in (0, 1, 2, 3)
                     and MARKET_CLOSE_HOUR_ET <= hour < MARKET_OPEN_HOUR_ET)
    if in_daily_halt:
        no_et  = et.replace(hour=MARKET_OPEN_HOUR_ET, minute=0, second=0, microsecond=0)
        reason = "Daily maintenance break"
    else:
        # Weekend (Fri ≥17:00, Sat, or Sun <18:00) → upcoming Sunday 18:00 ET.
        days_until_sun = (6 - wd) % 7                 # Fri→2, Sat→1, Sun→0
        no_et  = (et + timedelta(days=days_until_sun)).replace(
                     hour=MARKET_OPEN_HOUR_ET, minute=0, second=0, microsecond=0)
        reason = "Weekend close"
    next_open = no_et.astimezone(timezone.utc)
    return {
        "open": False, "status": "CLOSED", "next_open": next_open,
        "next_open_et": fmt_et(next_open, "%a %b %-d, %-I:%M %p ET"),
        "reason": reason,
    }


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
            elif ALERT_TYPES[t]["side"] == "bearish":
                bearish += ALERT_TYPES[t]["score"]
    return bullish, bearish, counts

def calculate_scores():
    return score_alerts(ALERT_HISTORY)

def calculate_bias(bullish, bearish):
    gap = abs(bullish - bearish)
    strength = min(gap + 1, 10)
    bt = cfg("BIAS_THRESHOLD")
    if bearish - bullish >= bt:
        return "Bearish", strength
    elif bullish - bearish >= bt:
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

def get_price_context(inst=None):
    """Build supply/demand levels and last-price-by-type from alert history.

    When `inst` ("MGC"/"MNQ") is given, only that instrument's alerts contribute,
    so nearest levels / structure / trade-plan anchors stay instrument-coherent.
    Zone types are instrument-prefixed; BOS/CHOCH carry no prefix so the record
    ticker decides, and unprefixed/untickered alerts are treated as shared."""
    last_price_by_type = {}
    all_supply_prices  = []
    all_demand_prices  = []
    for alert in ALERT_HISTORY:
        t = alert.get("alert_type", "")
        p = alert.get("price")
        if t not in ALERT_TYPES or p is None:
            continue
        if inst is not None:
            # Prefer the instrument resolved at ingestion; fall back to title/ticker
            # parsing only for legacy records. Shared alerts that can't be attributed
            # are excluded rather than counted for both instruments.
            a_inst = (alert.get("instrument")
                      or _instrument_from_text(t)
                      or _instrument_from_text(alert.get("ticker")))
            if a_inst != inst:
                continue
        try:
            price = float(p)
        except (ValueError, TypeError):
            continue
        last_price_by_type[t] = price
        # Sweep alerts are stop-hunt markers, not zone levels — track their last
        # price but never let them define supply/demand. Every other type keeps its
        # existing supply-vs-demand classification.
        if t in SUPPLY_TYPES:
            all_supply_prices.append(price)
        elif t not in SWEEP_TYPES:
            all_demand_prices.append(price)
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
    near_pct = cfg("NEAR_PCT")
    ext_pct  = cfg("EXTENDED_PCT")
    def pct(a, b):
        return abs(a - b) / b if b else 0
    if bias == "Bearish":
        if nearest_supply is not None:
            dist = pct(nearest_supply, current_price)
            if dist <= near_pct:
                return "Testing Supply", f"Price testing supply at {nearest_supply:.2f} ({dist:.2%} away). Favor shorts.", False
            elif dist >= ext_pct:
                return "Overextended", f"Price too extended from supply ({nearest_supply:.2f}, {dist:.2%} away). Wait for retracement.", True
            else:
                return "Approaching Supply", f"Price approaching supply at {nearest_supply:.2f} ({dist:.2%} away). Watch for rejection.", False
        return "No Supply Level", "No supply level tracked yet. Use caution.", False
    elif bias == "Bullish":
        if nearest_demand is not None:
            dist = pct(current_price, nearest_demand)
            if dist <= near_pct:
                return "Testing Demand", f"Price testing demand at {nearest_demand:.2f} ({dist:.2%} away). Favor longs.", False
            elif dist >= ext_pct:
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

    # ── Gate: Attempt structures (BOS fired, no CHOCH yet) ──
    # SWING: always WAIT. SCALP: tradable as reduced-conviction trend (capped below).
    is_attempt = structure_class in ("Bullish Attempt", "Bearish Attempt")
    if is_attempt and not cfg("ATTEMPT_TRADABLE"):
        chain += ["No CHOCH — Attempt Only", "Waiting for Confirmation", "WAIT"]
        return "WAIT", "WAIT", structure_class, chain

    # ── Gate: overextended price ──
    if overextended:
        chain += [f"Overextended — {risk_label}", "Entry Blocked", "WAIT"]
        return "WAIT", "WAIT", structure_class, chain

    # ── Risk Zone ──
    chain.append(risk_label)

    # ── Alert Score ──
    bt  = cfg("BIAS_THRESHOLD")
    gap = abs(bullish - bearish)
    if bias == "Bearish" and gap >= bt:
        score_desc  = f"Bearish Score Dominant ({bearish} vs {bullish})"
        score_side  = "bearish"
    elif bias == "Bullish" and gap >= bt:
        score_desc  = f"Bullish Score Dominant ({bullish} vs {bearish})"
        score_side  = "bullish"
    else:
        score_desc  = f"Mixed Alerts (bull {bullish} / bear {bearish})"
        score_side  = "mixed"

    chain.append(score_desc)

    # ── Map Attempt structures onto their trend direction (capped at BIAS below) ──
    if structure_class == "Bullish Attempt":
        trend_class = "Bullish Trend"
        chain.append("Attempt → reduced-conviction Bullish")
    elif structure_class == "Bearish Attempt":
        trend_class = "Bearish Trend"
        chain.append("Attempt → reduced-conviction Bearish")
    else:
        trend_class = structure_class

    # ── Structure cap: Range or Reversal ──
    if trend_class in ("Range", "Reversal"):
        cap_note = f"Structure Cap ({trend_class} → max WATCH)"
        chain.append(cap_note)
        if score_side == "mixed" or confidence < cfg("CONF_WATCH"):
            chain += ["No Edge", "WAIT"]
            return "WAIT", "WAIT", structure_class, chain
        chain.append("WATCH")
        return "WATCH", "WAIT", structure_class, chain

    # ── Confidence tier (Bearish Trend / Bullish Trend / tradable Attempt only) ──
    if score_side == "mixed":
        chain += [f"Confidence {confidence}% — Mixed Score", "WAIT"]
        return "WAIT", "WAIT", structure_class, chain

    conf_high      = cfg("CONF_HIGH")
    conf_trade     = cfg("CONF_TRADE")
    conf_watch     = cfg("CONF_WATCH")
    has_confluence = (bullish + bearish) >= cfg("MIN_TOTAL_SCORE")

    if confidence >= conf_high and has_confluence and not is_attempt:
        rec        = "HIGH CONVICTION TRADE"
        conf_step  = f"Confidence {confidence}% — High Conviction"
    elif confidence >= conf_trade and has_confluence:
        rec        = "TRADE"
        conf_step  = f"Confidence {confidence}% — Trade"
    elif confidence >= conf_watch:
        rec        = "WATCH"
        conf_step  = f"Confidence {confidence}% — Watch"
    else:
        rec        = "WAIT"
        conf_step  = f"Confidence {confidence}% — Low"

    # BOS-only entries never reach high conviction without a CHOCH.
    if is_attempt and rec == "HIGH CONVICTION TRADE":
        rec = "TRADE"

    if not has_confluence and rec in ("TRADE", "HIGH CONVICTION TRADE"):
        conf_step += f" (low confluence {bullish + bearish})"

    chain.append(conf_step)

    # ── Final Verdict ──
    if trend_class == "Bearish Trend":
        if rec == "HIGH CONVICTION TRADE":
            verdict = "STRONG SHORT"
        elif rec in ("TRADE", "WATCH"):
            verdict = "SHORT BIAS"
        else:
            verdict = "WAIT"
    elif trend_class == "Bullish Trend":
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
    # SCALP: tradable as a reduced-size setup anchored at the BOS level.
    # SWING: watch-only until a CHOCH confirms.
    if structure_label == "Bullish Attempt":
        bos_dem = last_price_by_type.get("BOS DEMAND")
        bos_str = f"${bos_dem:.2f}" if bos_dem else "—"
        if cfg("ATTEMPT_TRADABLE") and risk_label in ("Testing Demand", "Approaching Demand"):
            return ("Long Setup",
                    f"BOS Demand ({bos_str}) — scalp long at demand (no CHOCH; reduced size).",
                    "5m bullish confirmation candle holds above BOS Demand.",
                    f"Price closes below BOS Demand level ({bos_str})")
        return ("Watch Demand",
                f"BOS Demand ({bos_str}) confirmed — no CHOCH yet. Watch demand zone for hold.",
                "Wait for CHOCH Demand + Zone Confirmed to trigger Long Ready.",
                f"Price closes below BOS Demand level ({bos_str})")
    if structure_label == "Bearish Attempt":
        bos_sup = last_price_by_type.get("BOS SUPPLY")
        bos_str = f"${bos_sup:.2f}" if bos_sup else "—"
        if cfg("ATTEMPT_TRADABLE") and risk_label in ("Testing Supply", "Approaching Supply"):
            return ("Short Setup",
                    f"BOS Supply ({bos_str}) — scalp short at supply (no CHOCH; reduced size).",
                    "5m bearish confirmation candle holds below BOS Supply.",
                    f"Price closes above BOS Supply level ({bos_str})")
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
    Compute position sizing from a generated trade plan.

    Point value is read from the plan (per-instrument: MNQ = $2/pt, MGC = $10/pt),
    falling back to MGC's $10/pt for legacy plans.

    Returns a dict of display strings, or {} if no trade plan exists.
    """
    if not trade_plan.get("trade_plan"):
        return {}
    try:
        point_value    = float(trade_plan.get("point_value", MGC_POINT_VALUE))
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
        risk_per_contract   = stop_dist * point_value
        contracts           = max(1, int(dollar_risk / risk_per_contract))
        max_loss            = contracts * risk_per_contract
        profit_t1           = contracts * t1_dist * point_value
        profit_t2           = contracts * t2_dist * point_value

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
    if is_actionable(verdict):
        zone_side = "demand zone" if ready_direction(verdict) == "Long" else "supply zone"
        _setup    = "early entry setup forming" if is_early_ready(verdict) else "entry setup ready"
        return (f"Confidence {confidence}%. {signal_text}. Price at {zone_side} — "
                f"{_setup}. Score {score_dom} vs {score_opp}.")
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
        _side = ALERT_TYPES[k]["side"]
        _is_bear = _side == "bearish" or (_side == "sweep" and "BEARISH" in k)
        (bear_parts if _is_bear else bull_parts).append(f"{short} ×{v}")
    parts = []
    if bear_parts:
        parts.append("🔴 " + ", ".join(bear_parts))
    if bull_parts:
        parts.append("🟢 " + ", ".join(bull_parts))
    return "\n".join(parts) if parts else "No alerts"

def _humanize_gate_rejections(gd):
    """Translate a gate_debug `failed_conditions` list into plain-English strings for
    the alert-diagnostics block / dashboard "Why Not Ready" panel. Display-only. The
    raw edge_score(<thr) line is dropped — the EdgeScore vs required_score already
    conveys it numerically — so the list reads as human gate reasons only."""
    gd = gd or {}
    out = []
    for f in (gd.get("failed_conditions") or []):
        if not f:
            continue
        if f.startswith("edge_score("):
            continue
        if f == "conflicting_structure":
            out.append("Conflicting structure — long & short both active")
        elif f == "zone_valid":
            out.append("Price not at a valid trade-side zone")
        elif f == "vwap_confirmed":
            out.append("VWAP not confirming the direction")
        elif f == "structure_confirmed":
            out.append("No confirming market structure (BOS/CHOCH)")
        elif f == "volatility_block":
            out.append("Volatility out of tradeable range")
        elif f.startswith("confirmations("):
            try:
                inner = f[f.index("(") + 1:f.index(")")]
                got, need = inner.split("<")
                out.append("Only %s of %s confirmations present" % (got, need))
            except (ValueError, IndexError):
                out.append("Not enough confirmations present")
        else:
            out.append(f)
    return out

def bias_color(verdict):
    return {
        "STRONG SHORT": 0xCC0000,
        "SHORT BIAS":   0xFF5555,
        "SHORT READY":  0xDD2222,
        "SHORT EARLY READY": 0xE0781E,
        "WATCH":        0xFFAA00,
        "WAIT":         0x888888,
        "LONG READY":   0x00CC44,
        "LONG EARLY READY":  0xBFA600,
        "LONG BIAS":    0x55CC55,
        "STRONG LONG":  0x00AA00,
    }.get(verdict, 0x888888)

VERDICT_EMOJI = {
    "STRONG SHORT": "🔴🔴",
    "SHORT BIAS":   "🔴",
    "SHORT READY":  "🔴✅",
    "SHORT EARLY READY": "🟡🔴",
    "WATCH":        "👁️",
    "WAIT":         "⏸️",
    "LONG READY":   "🟢✅",
    "LONG EARLY READY":  "🟡🟢",
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
                + (
                    f"\n**ATR Stop:** {tp.get('atr_pts')} pts × {tp.get('atr_multiplier')} → "
                    f"{tp.get('stop_distance_ticks')} ticks (${tp.get('risk_dollars_per_contract')}/contract)"
                    if tp.get("atr_pts") is not None else ""
                )
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


def _strict_checklist_field(strict_label, strict_score, confluences,
                            vwap_value, vwap_status):
    """Return a single embed field rendering the strict 4-point checklist + score."""
    c = confluences or {}
    direction = c.get("direction")
    is_short  = direction == "Short"

    def _mark(ok):
        return "✅" if ok else "❌"

    if vwap_value is not None:
        vwap_txt = f"{float(vwap_value):.2f} ({vwap_status})"
    else:
        vwap_txt = f"— ({vwap_status})"

    side_word = "below" if is_short else "above"
    # The 4 READY gates, identical to what evaluate_strict_setup gates on, so the
    # card checklist and the verdict never disagree: zone-valid (mitigation + a
    # same-direction reaction), structure (ANY one of CHOCH/BOS/HH-HL or LH-LL),
    # price-vs-VWAP, and Edge Score ≥ the READY threshold.
    struct_hint = "(CHOCH/BOS/LH-LL)" if is_short else "(CHOCH/BOS/HH-HL)"
    lines = [
        f"{_mark(c.get('zone_mitigated'))} {'Supply' if is_short else 'Demand'} zone mitigated + reaction",
        f"{_mark(c.get('structure_confirmed'))} Structure {struct_hint}",
        f"{_mark(c.get('vwap'))} Price {side_word} VWAP  ·  VWAP {vwap_txt}",
        f"{_mark(c.get('edge_ok'))} Edge Score ≥ {cfg('EDGE_READY_THRESHOLD')}",
    ]
    label_emoji = {"A+ Setup": "🔥", "Strong Trade": "🟢", "Possible Trade": "🟡"}.get(strict_label, "⏸")
    header = f"{label_emoji} **{strict_label}** · Score **{strict_score}/100**"
    if direction:
        header += f" · {direction}"
    return {
        "name":   "✅  Trade Checklist",
        "value":  header + "\n" + "\n".join(lines),
        "inline": False,
    }


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
                         mitigated_zone_price=None,
                         strict_label="WAIT", strict_score=0,
                         confluences=None, vwap_value=None, vwap_status="n/a"):
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
        ] if not active_trade_info else []),
        # ── Reasoning Chain ──
        {
            "name":   "🔗  Reasoning Chain",
            "value":  f"```\n{chain_text}\n```",
            "inline": False,
        },
        # ── Strict Trade Checklist (hidden during active trade / consumed zone) ──
        *([_strict_checklist_field(strict_label, strict_score, confluences,
                                   vwap_value, vwap_status)]
          if not active_trade_info and not zone_mitigated_near else []),
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
    elif is_full_ready(verdict):
        _context_title = "🔥 HIGH CONVICTION TRADE"
    elif is_early_ready(verdict):
        _context_title = "⚡ EARLY SETUP"
    else:
        _context_title = "👀 WATCHLIST SETUP"

    embed = {
        "author":      {"name": BOT_NAME},
        "title":       _context_title,
        "description": f"**{ticker}** · {price_str} · `{alert_data.get('alert_type','—')}`",
        "color":       color,
        "fields":      fields,
        "footer":      {"text": f"Received {fmt_et(alert_data.get('timestamp',''))}"},
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

    # ── Recent alerts: a time window in SCALP mode, else the last 5 alerts ──
    stage_window = cfg("STAGE_WINDOW_MIN")
    if stage_window:
        _cutoff = now_utc() - timedelta(minutes=stage_window)
        recent = []
        for a in alert_history:
            try:
                if datetime.fromisoformat(a["timestamp"]) >= _cutoff:
                    recent.append(a)
            except (KeyError, ValueError):
                pass
    else:
        recent = list(alert_history)[-5:]

    def _stage_latest_ts(types):
        """Latest timestamp among `recent` alerts whose type is in `types`, else None.
        Intentionally non-instrument-specific, matching this function's broad staging."""
        latest = None
        for a in recent:
            if a.get("alert_type") not in types:
                continue
            try:
                ts = datetime.fromisoformat(a["timestamp"])
            except (KeyError, ValueError):
                continue
            if latest is None or ts > latest:
                latest = ts
        return latest

    _sup_struct_ts = _stage_latest_ts(("CHOCH SUPPLY", "BOS SUPPLY"))
    _dem_struct_ts = _stage_latest_ts(("CHOCH DEMAND", "BOS DEMAND"))
    _bull_conf_ts  = _stage_latest_ts(("MGC BULLISH CONFIRMATION", "MNQ BULLISH CONFIRMATION"))
    _bear_conf_ts  = _stage_latest_ts(("MGC BEARISH CONFIRMATION", "MNQ BEARISH CONFIRMATION"))

    has_choch_bos_supply     = _sup_struct_ts is not None
    has_choch_bos_demand     = _dem_struct_ts is not None
    has_supply_confirmed     = _stage_latest_ts(("MGC SUPPLY ZONE CONFIRMED",
                                                 "MNQ SUPPLY ZONE CONFIRMED")) is not None
    has_demand_confirmed     = _stage_latest_ts(("MGC DEMAND ZONE CONFIRMED",
                                                 "MNQ DEMAND ZONE CONFIRMED")) is not None
    # Confirmation only counts when it closed AFTER the same-direction structure —
    # the indicator marks every 5m bar, so an un-gated presence check is always true.
    has_bullish_confirmation = (_bull_conf_ts is not None and _dem_struct_ts is not None
                                and _bull_conf_ts >= _dem_struct_ts)
    has_bearish_confirmation = (_bear_conf_ts is not None and _sup_struct_ts is not None
                                and _bear_conf_ts >= _sup_struct_ts)

    # ── Proximity ("at zone" window, mode-dependent) ──
    def dist(a, b):
        return abs(a - b) / b if b else float("inf")

    watch_pct = cfg("WATCH_PCT")
    at_supply = nearest_supply is not None and dist(nearest_supply, current_price) <= watch_pct
    at_demand = nearest_demand is not None and dist(current_price, nearest_demand) <= watch_pct

    bt = cfg("BIAS_THRESHOLD")
    bearish_dominant = (bearish - bullish) >= bt
    bullish_dominant = (bullish - bearish) >= bt

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


def _handle_zone_broken(price, instrument=None):
    """Cancel pending directional setup and record the broken zone event."""
    global ZONE_BROKEN_AT, ALERT_HISTORY
    ZONE_BROKEN_AT = {"price": price, "alerts_since": 0, "instrument": instrument}
    directional = SUPPLY_TYPES | DEMAND_TYPES
    # Remove last 5 directional setup alerts from history to cancel pending setup
    history_list = list(ALERT_HISTORY)
    # Exclude the ZONE BROKEN record itself (last item); reverse scan to cancel most recent first
    kept      = []
    cancelled = 0
    for rec in reversed(history_list[:-1]):
        # Cancel only the broken instrument's directional alerts — a broken MGC
        # zone must not delete MNQ's setup records (and vice versa). An untagged
        # (legacy) break falls back to cancelling any instrument's setup.
        same_inst = (instrument is None or rec.get("instrument") == instrument)
        if cancelled < 5 and same_inst and rec.get("alert_type") in directional:
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
# Strict recommendation ruleset (checklist + 0-100 score)
# ---------------------------------------------------------------------------

def get_vwap(ticker, max_age_min=None):
    """Return (vwap_value, status) for an instrument.

    status: 'ok' | 'missing' | 'stale'. A VWAP older than max_age_min (default the
    active mode's STAGE_WINDOW_MIN, falling back to 30 min) is treated as stale and
    therefore unusable — the strict gate must not trade on a price-vs-VWAP check it
    cannot trust.
    """
    rec = VWAP_BY_TICKER.get(instrument_of(ticker))
    if not rec or rec.get("value") is None:
        return None, "missing"
    if max_age_min is None:
        max_age_min = cfg("STAGE_WINDOW_MIN") or 30
    try:
        age_min = (now_utc() - datetime.fromisoformat(rec["ts"])).total_seconds() / 60.0
        if age_min > max_age_min:
            return None, "stale"
    except (KeyError, ValueError, TypeError):
        return None, "missing"
    return float(rec["value"]), "ok"


def _fetch_vwap_from_market(instrument):
    """Compute today's session VWAP for an instrument from a public market feed.

    Returns (value, error). VWAP = Σ(typical_price × volume) / Σ(volume) over the
    1-minute bars of the current trading day, where typical_price = (H+L+C)/3.
    MGC/MNQ track GC=F/NQ=F (same price level), so the VWAP is interchangeable.
    """
    symbol = VWAP_FEED_SYMBOL.get(instrument)
    if not symbol:
        return None, f"no feed symbol for {instrument}"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    try:
        resp = requests.get(
            url,
            params={"interval": "1m", "range": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}"
        result = resp.json()["chart"]["result"][0]
        quote  = result["indicators"]["quote"][0]
        highs, lows, closes = quote["high"], quote["low"], quote["close"]
        volumes = quote["volume"]
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
        return None, f"fetch error: {exc}"

    num = den = 0.0
    for high, low, close, vol in zip(highs, lows, closes, volumes):
        if high is None or low is None or close is None or not vol:
            continue
        num += ((high + low + close) / 3.0) * vol
        den += vol
    if den <= 0:
        return None, "no volume data"
    return round(num / den, 4), None


def _chart_override_active(instrument):
    """True if a chart/manual VWAP push for this instrument is still within its
    grace window — the background fetch must not overwrite it while it is."""
    rec = VWAP_BY_TICKER.get(instrument)
    if not rec or rec.get("value") is None or rec.get("source") not in ("chart", "manual"):
        return False
    try:
        age_min = (now_utc() - datetime.fromisoformat(rec["ts"])).total_seconds() / 60.0
    except (KeyError, ValueError, TypeError):
        return False
    return age_min <= VWAP_OVERRIDE_GRACE_MIN


def _update_vwap_auto(instrument):
    """Refresh VWAP for one instrument from the market feed, unless a recent
    chart/manual push (the exact value) is still within its grace window."""
    if _chart_override_active(instrument):
        return  # keep the exact, operator-supplied value
    value, err = _fetch_vwap_from_market(instrument)
    if value is None:
        logger.warning("VWAP auto-fetch failed for %s: %s", instrument, err)
        return
    # Re-check after the (slow) HTTP fetch: a chart/manual push may have landed
    # while it was in flight — never clobber a fresh operator value.
    if _chart_override_active(instrument):
        return
    VWAP_BY_TICKER[instrument] = {
        "value": value, "ts": now_utc().isoformat(), "source": "auto",
    }
    logger.info("VWAP auto-fetch: %s = %s", instrument, value)


def _update_price_auto(instrument):
    """Refresh the DISPLAY-ONLY fallback price for one instrument from the market
    feed (same source as VWAP: MGC≈GC=F, MNQ≈NQ=F). Best-effort — any failure just
    leaves the previous value in place. Never feeds the gate / scoring."""
    bar = _fetch_latest_bar(instrument)
    if bar and bar.get("close") is not None:
        AUTO_PRICE_BY_TICKER[instrument] = {
            "value": round(float(bar["close"]), 4), "ts": now_utc().isoformat(),
        }


# ───────────────────────────────────────────────────────────────────────────
# Volatility monitor (additive, FAIL-OPEN). A per-instrument 1-minute ATR is
# compared to the session-typical range to classify the current volatility
# regime. It surfaces as context on the card + dashboard AND gates the strict
# setup: a CAUTION regime dents the Edge Score and flags the card, while a BLOCK
# regime (market too dead or too wild) holds an otherwise-READY setup. Missing
# or stale data NEVER blocks a trade — it only shows as "unavailable".
# ───────────────────────────────────────────────────────────────────────────
VOL_ATR_BARS           = 14   # recent window (1-min bars) for the current ATR
VOL_MIN_BARS           = 30   # session bars required before a baseline is trusted
VOLATILITY_MAX_AGE_MIN = 10   # a reading older than this is stale -> unavailable


def _median(vals):
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _fetch_volatility_from_market(instrument):
    """Return (recent_atr_pts, baseline_pts, ratio) for an instrument from the
    public 1-min feed.

    recent_atr = mean true-range of the last VOL_ATR_BARS bars; baseline = median
    true-range across all valid session bars; ratio = recent_atr / baseline. The
    ratio is self-normalising, so one set of thresholds works for both MGC and
    MNQ despite very different absolute point sizes. (None, None, None) on error.
    """
    symbol = VWAP_FEED_SYMBOL.get(instrument)
    if not symbol:
        return None, None, None
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    try:
        resp = requests.get(url, params={"interval": "1m", "range": "1d"},
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        if resp.status_code != 200:
            return None, None, None
        result = resp.json()["chart"]["result"][0]
        quote  = result["indicators"]["quote"][0]
        highs, lows, closes = quote["high"], quote["low"], quote["close"]
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
        logger.warning("Volatility fetch failed for %s: %s", instrument, exc)
        return None, None, None

    trs, prev_close = [], None
    for high, low, close in zip(highs, lows, closes):
        if high is None or low is None or close is None:
            continue
        tr = (high - low) if prev_close is None else max(
            high - low, abs(high - prev_close), abs(low - prev_close))
        if tr >= 0:
            trs.append(tr)
        prev_close = close

    if len(trs) < VOL_MIN_BARS:
        return None, None, None
    recent     = trs[-VOL_ATR_BARS:]
    recent_atr = sum(recent) / len(recent)
    baseline   = _median(trs)
    if baseline <= 0:
        return None, None, None
    return round(recent_atr, 4), round(baseline, 4), round(recent_atr / baseline, 3)


def _update_volatility_auto(instrument):
    """Refresh the stored volatility reading for one instrument (best-effort)."""
    atr_pts, baseline_pts, ratio = _fetch_volatility_from_market(instrument)
    if atr_pts is None or ratio is None:
        return
    VOLATILITY_BY_TICKER[instrument] = {
        "atr_pts": atr_pts, "baseline_pts": baseline_pts,
        "ratio": ratio, "ts": now_utc().isoformat(),
    }
    logger.info("Volatility auto-fetch: %s ATR=%.4f base=%.4f ratio=%.2f",
                instrument, atr_pts, baseline_pts or 0.0, ratio)


def _vol_regime_score_adj(regime):
    """Edge-score modifier for a volatility regime when volatility is a SCALP
    score modifier (not a hard gate): Normal +10, Elevated/Quiet 0, Extreme
    (Wild/Dead) -10. Returns 0 in hard-gate (SWING) mode so the Edge Score is
    unchanged there - the regime holds the setup at the gate instead."""
    if cfg("VOL_HARD_GATE"):
        return 0
    if regime == "NORMAL":
        return 10
    if regime in ("HIGH_BLOCK", "QUIET_BLOCK"):
        return -10
    return 0


def get_volatility(ticker):
    """Return the current volatility reading + regime for an instrument.

    STRICTLY per-instrument (no global fallback) so MGC volatility can never
    bleed into MNQ analysis and vice-versa. FAIL-OPEN: when the reading is
    missing or stale, status != 'ok' and BOTH `blocked` and `caution` are False,
    so the strict gate never holds a trade on data it does not have.
    """
    inst = instrument_of(ticker)
    base = {"atr_pts": None, "baseline_pts": None, "ratio": None, "regime": "NA",
            "status": "missing", "blocked": False, "caution": False,
            "hard_gate": bool(cfg("VOL_HARD_GATE")), "score_adj": 0,
            "threshold_elevated": cfg("VOL_HIGH_CAUTION"),
            "threshold_extreme": cfg("VOL_HIGH_BLOCK"),
            "decision": "Unavailable - no score effect",
            "label": "Unavailable", "display": "—"}
    rec = VOLATILITY_BY_TICKER.get(inst)
    if not rec or rec.get("ratio") is None:
        return base
    try:
        age_min = (now_utc() - datetime.fromisoformat(rec["ts"])).total_seconds() / 60.0
    except (KeyError, ValueError, TypeError):
        return base
    if age_min > VOLATILITY_MAX_AGE_MIN:
        base["status"] = "stale"
        return base

    try:
        ratio = float(rec["ratio"])
    except (TypeError, ValueError):
        return base
    atr_pts      = rec.get("atr_pts")
    baseline_pts = rec.get("baseline_pts")
    if baseline_pts is None:
        # Older readings stored before baseline tracking — derive from the ratio.
        try:
            baseline_pts = round(float(atr_pts) / ratio, 4) if ratio else None
        except (TypeError, ValueError, ZeroDivisionError):
            baseline_pts = None
    if ratio <= cfg("VOL_QUIET_BLOCK"):
        regime, label, blocked, caution = "QUIET_BLOCK", "Dead / too quiet", True, False
    elif ratio >= cfg("VOL_HIGH_BLOCK"):
        regime, label, blocked, caution = "HIGH_BLOCK", "Wild / too volatile", True, False
    elif ratio <= cfg("VOL_QUIET_CAUTION"):
        regime, label, blocked, caution = "QUIET_CAUTION", "Quiet", False, True
    elif ratio >= cfg("VOL_HIGH_CAUTION"):
        regime, label, blocked, caution = "HIGH_CAUTION", "Elevated", False, True
    else:
        regime, label, blocked, caution = "NORMAL", "Normal", False, False

    hard_gate = bool(cfg("VOL_HARD_GATE"))
    score_adj = _vol_regime_score_adj(regime)
    if hard_gate and blocked:
        decision, adj_tag = f"{label} - HOLD (volatility gate)", "HOLD"
    elif score_adj > 0:
        decision, adj_tag = f"{label} - Edge +{score_adj}", f"+{score_adj}"
    elif score_adj < 0:
        decision, adj_tag = f"{label} - Edge {score_adj}", f"{score_adj}"
    else:
        decision, adj_tag = f"{label} - no score effect", "0"

    try:
        atr_disp = f"{float(atr_pts):.2f}" if atr_pts is not None else "—"
    except (TypeError, ValueError):
        atr_disp = "—"
    try:
        base_disp = f"{float(baseline_pts):.2f}" if baseline_pts is not None else "—"
    except (TypeError, ValueError):
        base_disp = "—"
    return {"atr_pts": atr_pts, "baseline_pts": baseline_pts, "ratio": ratio,
            "regime": regime, "status": "ok", "blocked": blocked, "caution": caution,
            "hard_gate": hard_gate, "score_adj": score_adj,
            "threshold_elevated": cfg("VOL_HIGH_CAUTION"),
            "threshold_extreme": cfg("VOL_HIGH_BLOCK"),
            "decision": decision, "label": label,
            "display": f"ATR {atr_disp} pts · {ratio:.2f}× (base {base_disp}) · {label} [{adj_tag}]"}


def _vwap_autofetch_loop():
    """Refresh VWAP for all tracked instruments, evaluate managed trades, then
    reschedule. The managed-trade watch is additive and fail-open so it can
    never disrupt the VWAP refresh or kill the loop."""
    try:
        for instrument in VWAP_FEED_SYMBOL:
            _update_vwap_auto(instrument)
    except Exception as exc:  # never let the loop die
        logger.warning("VWAP auto-fetch loop error: %s", exc)
    try:
        for instrument in VWAP_FEED_SYMBOL:
            _update_volatility_auto(instrument)
    except Exception as exc:  # volatility is best-effort — never disrupt VWAP/watcher
        logger.warning("Volatility auto-fetch loop error: %s", exc)
    try:
        _watch_managed_trades()
    except Exception as exc:
        logger.warning("Managed-trade watch error: %s", exc)
    finally:
        threading.Timer(VWAP_FETCH_INTERVAL, _vwap_autofetch_loop).start()


def _price_autofetch_loop():
    """Refresh the DISPLAY-ONLY dashboard price for all instruments on a fast cadence
    (PRICE_FETCH_INTERVAL), independent of the slower VWAP/volatility loop, so the
    dashboard price stays near-live even on a quiet market. Best-effort: any failure
    leaves the previous value in place and it never feeds the gate / scoring."""
    try:
        for instrument in VWAP_FEED_SYMBOL:
            _update_price_auto(instrument)
    except Exception as exc:  # display fallback only — never let the loop die
        logger.warning("Price auto-fetch loop error: %s", exc)
    finally:
        threading.Timer(PRICE_FETCH_INTERVAL, _price_autofetch_loop).start()


def _active_ticker():
    """Best-effort active instrument from the most recent resolved alert."""
    for rec in reversed(ALERT_HISTORY):
        if rec.get("instrument"):
            return rec["instrument"]
        if rec.get("ticker"):
            return instrument_of(rec["ticker"])
        t = str(rec.get("alert_type", ""))
        if t.startswith("MNQ"):
            return "MNQ"
        if t.startswith("MGC"):
            return "MGC"
    return "MGC"


# ── Unified additive Edge Score (single source of truth: gate + display) ──────
# Six confluence components, max 100. compute_trade_edge_components is the ONLY
# place points are assigned, so the READY gate (evaluate_strict_setup) and the
# displayed Edge Score (compute_edge_breakdown) can never diverge.
EDGE_COMPONENTS = (
    ("zone_valid",          "Zone Mitigated",         25),
    ("vwap_confirmed",      "VWAP Confirmation",      20),
    ("structure_confirmed", "Structure Confirmation", 20),
    ("liquidity_sweep",     "Liquidity Sweep",        15),
    ("confirmation_candle", "Confirmation Candle",    10),
    ("preferred_session",   "Session Bonus",          10),
)
EDGE_READY_THRESHOLD = 80   # minimum Edge Score for a READY setup
CONFLICT_WINDOW_MIN  = 10   # opposing structure within this many minutes = conflict


def compute_trade_edge_components(signals, vol_adj=0):
    """THE Edge Score — pure additive sum of the six confluence components (max
    100), plus an optional volatility modifier (SCALP only). `signals` is a dict
    of booleans keyed by EDGE_COMPONENTS[*][0]. `vol_adj` is the volatility Edge
    modifier (Normal +10 / Elevated 0 / Extreme -10; 0 when volatility is a hard
    gate or unavailable). Returns (score, breakdown) where breakdown lists
    {"label", "points"} for each credited component. Shared by the READY gate and
    the display layer so the gate score and the shown Edge Score are identical."""
    breakdown = []
    for key, label, pts in EDGE_COMPONENTS:
        if signals.get(key):
            breakdown.append({"label": label, "points": pts})
    if vol_adj:
        breakdown.append({"label": "Volatility", "points": vol_adj})
    score = max(0, min(100, sum(item["points"] for item in breakdown)))
    return score, breakdown


def evaluate_strict_setup(current_price, ticker, vwap, vwap_status,
                          nearest_supply, nearest_demand,
                          bullish, bearish, confidence, alert_history,
                          volatility=None, session=None):
    """Strict checklist recommendation.

    A trade is recommended ONLY when ALL of:
        LONG : BOS Demand + Bullish CHOCH + 5m bullish confirmation + price > VWAP
        SHORT: BOS Supply + Bearish CHOCH + 5m bearish confirmation + price < VWAP
    (IDM "taken" is treated as satisfied by CHOCH.)

    Returns dict: label ("Strong Trade"|"Possible Trade"|"WAIT"), direction
    ("Long"|"Short"|None), score (0-100), confluences (per-condition detail for
    journal/embed), reason (human-readable), and missing (list of unmet conditions).
    """
    inst = instrument_of(ticker)

    # ── Recent alerts within the active window (mode-dependent), ticker-aware ──
    stage_window = cfg("STAGE_WINDOW_MIN")
    if stage_window:
        cutoff = now_utc() - timedelta(minutes=stage_window)
        recent = []
        for a in alert_history:
            try:
                if datetime.fromisoformat(a["timestamp"]) >= cutoff:
                    recent.append(a)
            except (KeyError, ValueError):
                pass
    else:
        recent = list(alert_history)[-8:]

    def _latest_ts(alert_type, ticker_scoped=False):
        """Most recent timestamp (datetime, UTC) of `alert_type` within the active
        window, applying the same instrument scoping as `_has`. None if absent."""
        latest = None
        for a in recent:
            if a.get("alert_type") != alert_type:
                continue
            if not ticker_scoped:
                # CHOCH/BOS carry no symbol prefix — use the resolved instrument
                # stored at ingestion. An untickered shared alert no longer matches
                # either instrument; old in-memory records fall back to text parse.
                a_inst = (a.get("instrument")
                          or _instrument_from_text(a.get("ticker"))
                          or _instrument_from_text(a.get("alert_type")))
                if a_inst != inst:
                    continue
            # CONFIRMATION/CONFIRMED types embed the instrument in their name, so a
            # plain type match is already instrument-scoped (ticker_scoped=True).
            try:
                ts = datetime.fromisoformat(a["timestamp"])
            except (KeyError, ValueError):
                continue
            if latest is None or ts > latest:
                latest = ts
        return latest

    def _has(alert_type, ticker_scoped=False):
        return _latest_ts(alert_type, ticker_scoped) is not None

    def _after_anchor(confirm_ts, *anchors):
        """A confirmation only COUNTS when it landed at/after the most recent
        same-direction structure anchor present in the window — i.e. a genuine
        post-structure confirmation candle, not every-bar noise. False if there is
        no confirmation, or no structure anchor to confirm."""
        present = [t for t in anchors if t is not None]
        if confirm_ts is None or not present:
            return False
        return confirm_ts >= max(present)

    bos_dem_ts   = _latest_ts("BOS DEMAND")
    bos_sup_ts   = _latest_ts("BOS SUPPLY")
    choch_dem_ts = _latest_ts("CHOCH DEMAND")
    choch_sup_ts = _latest_ts("CHOCH SUPPLY")

    has_bos_demand   = bos_dem_ts is not None
    has_bos_supply   = bos_sup_ts is not None
    has_choch_demand = choch_dem_ts is not None
    has_choch_supply = choch_sup_ts is not None
    # The "Confirmation Candle" indicator marks a shape on EVERY 5m bar, so a plain
    # presence check is almost always true and makes the confirmation gate
    # meaningless. Require the confirmation to have closed AFTER the same-direction
    # structure (BOS/CHOCH) was in place — that is what a real confirmation candle
    # is. Pre-structure every-bar confirmations are ignored as noise.
    has_bull_confirm = _after_anchor(
        _latest_ts(f"{inst} BULLISH CONFIRMATION", ticker_scoped=True),
        bos_dem_ts, choch_dem_ts)
    has_bear_confirm = _after_anchor(
        _latest_ts(f"{inst} BEARISH CONFIRMATION", ticker_scoped=True),
        bos_sup_ts, choch_sup_ts)
    has_dem_confirm  = _has(f"{inst} DEMAND ZONE CONFIRMED", ticker_scoped=True)
    has_sup_confirm  = _has(f"{inst} SUPPLY ZONE CONFIRMED", ticker_scoped=True)
    has_bull_sweep   = _has(f"{inst} BULLISH SWEEP", ticker_scoped=True)
    has_bear_sweep   = _has(f"{inst} BEARISH SWEEP", ticker_scoped=True)

    # ── VWAP condition (required gate) ──
    vwap_ok     = vwap_status == "ok" and vwap is not None and current_price is not None
    price_above = bool(vwap_ok and current_price > vwap)
    price_below = bool(vwap_ok and current_price < vwap)

    # ── Swing-structure alerts (HH/HL bullish, LH/LL bearish) — shared & ticker-
    #    scoped at ingestion like CHOCH/BOS. ANY ONE structure signal in the trade
    #    direction now satisfies the structure gate (no longer BOS *and* CHOCH). ──
    hh_ts = _latest_ts("HH"); hl_ts = _latest_ts("HL")
    lh_ts = _latest_ts("LH"); ll_ts = _latest_ts("LL")
    structure_long  = bool(has_bos_demand or has_choch_demand or hh_ts or hl_ts)
    structure_short = bool(has_bos_supply or has_choch_supply or lh_ts or ll_ts)

    # ── Zone-valid (required gate): the trade-side zone must have been MITIGATED
    #    AND then REACTED to. Mitigation alone is the old "consumed / stand-aside"
    #    state; pairing it with a same-direction reaction (5m confirmation candle,
    #    zone-confirmed alert, or liquidity sweep) is what makes the retest
    #    tradeable. Instrument+side scoped via the per-instrument nearest level. ──
    has_mitigated_demand = bool(ZONE_MITIGATED_FLAG and is_near_mitigated_zone(nearest_demand)[0])
    has_mitigated_supply = bool(ZONE_MITIGATED_FLAG and is_near_mitigated_zone(nearest_supply)[0])
    reaction_long  = bool(has_bull_confirm or has_dem_confirm or has_bull_sweep)
    reaction_short = bool(has_bear_confirm or has_sup_confirm or has_bear_sweep)
    zone_valid_long  = bool(has_mitigated_demand and reaction_long)
    zone_valid_short = bool(has_mitigated_supply and reaction_short)

    # ── Conflict (recency-aware): opposing structure on BOTH sides within a short
    #    window = genuinely choppy → stand aside. A STALE opposite structure does
    #    NOT block (replaces the old over-broad "any opposite in window" rule). ──
    long_struct_ts  = max([t for t in (bos_dem_ts, choch_dem_ts, hh_ts, hl_ts) if t], default=None)
    short_struct_ts = max([t for t in (bos_sup_ts, choch_sup_ts, lh_ts, ll_ts) if t], default=None)
    opposing_present = bool(
        long_struct_ts and short_struct_ts
        and abs((long_struct_ts - short_struct_ts).total_seconds()) <= CONFLICT_WINDOW_MIN * 60
    )

    # ── Preferred-session bonus (NEVER blocks; only ADDS to the Edge Score) ──
    sess_state   = session or get_session_state()
    session_pref = bool(sess_state.get("preferred"))

    # ── Volatility (FAIL-OPEN). SWING: hard gate — a BLOCK regime holds READY→WAIT.
    #    SCALP: NOT a gate — folds into the Edge Score as a modifier (Normal +10 /
    #    Elevated 0 / Extreme -10) so elevated volatility never forces WAIT alone. ──
    vol       = volatility or {}
    vol_block = bool(vol.get("blocked")) and bool(cfg("VOL_HARD_GATE"))
    vol_adj   = int(vol.get("score_adj") or 0)

    # ── READY-gate configuration (mode-tunable). SWING keeps zone, VWAP & structure
    #    as hard gates with edge>=80; SCALP demotes ALL THREE to confirmations, drops
    #    the actionable edge floor to 35 (full READY at 50), and requires
    #    MIN_CONFIRMATIONS confluences instead. A demoted zone still scores its 25pt
    #    Edge component — it just no longer hard-blocks READY in SCALP. ──
    ready_threshold      = cfg("EDGE_READY_THRESHOLD")        # actionable floor (EARLY)
    full_ready_threshold = cfg("EDGE_FULL_READY_THRESHOLD")   # full-READY floor
    require_vwap      = bool(cfg("GATE_REQUIRE_VWAP"))
    require_structure = bool(cfg("GATE_REQUIRE_STRUCTURE"))
    require_zone      = bool(cfg("GATE_REQUIRE_ZONE"))
    min_confirmations = int(cfg("MIN_CONFIRMATIONS"))

    # ── Trend alignment (real, derived — never fabricated): the candidate side
    #    agrees with the live bias score (bullish vs bearish). Counts as one
    #    confirmation; purely additive, so it never blocks a setup on its own. ──
    trend_long  = bool(bullish > bearish)
    trend_short = bool(bearish > bullish)

    # ── Unified additive Edge Score per direction (single source of truth shared
    #    with the display layer via compute_trade_edge_components). ──
    def _signals(direction):
        if direction == "Long":
            return {"zone_valid": zone_valid_long, "vwap_confirmed": price_above,
                    "structure_confirmed": structure_long, "liquidity_sweep": has_bull_sweep,
                    "confirmation_candle": has_bull_confirm, "preferred_session": session_pref}
        return {"zone_valid": zone_valid_short, "vwap_confirmed": price_below,
                "structure_confirmed": structure_short, "liquidity_sweep": has_bear_sweep,
                "confirmation_candle": has_bear_confirm, "preferred_session": session_pref}

    def _edge_for(direction):
        return compute_trade_edge_components(_signals(direction), vol_adj)

    # ── Score-aware conflict resolution (single source for the gate AND the
    #    diagnostics block). `opposing_present` (above) = opposing structure on both
    #    sides within the conflict window. In SCALP we only WAIT on a TRUE conflict —
    #    the two directions are genuinely balanced (Edge gap <= CONFLICT_WAIT_GAP);
    #    otherwise we commit to the dominant (higher-Edge) side. SWING keeps the
    #    original always-WAIT-on-opposing behaviour (CONFLICT_SCORE_AWARE=False). ──
    long_score           = _edge_for("Long")[0]
    short_score          = _edge_for("Short")[0]
    conflict_gap         = abs(long_score - short_score)
    dominant_direction   = "Long" if long_score >= short_score else "Short"
    score_aware_conflict = bool(cfg("CONFLICT_SCORE_AWARE"))
    conflict_wait_gap    = int(cfg("CONFLICT_WAIT_GAP"))
    if opposing_present and score_aware_conflict:
        true_conflict = conflict_gap <= conflict_wait_gap
    else:
        true_conflict = opposing_present

    def _confirmations(direction):
        """Count the REAL confirmations present for `direction`. SCALP READY needs
        >= MIN_CONFIRMATIONS of them; VWAP and structure are confirmations here, not
        hard gates. Structure contributes per-signal (BOS / CHOCH / swing) so a
        richer structure picture counts for more. 'volume' has no alert source in
        this system, so it is never counted or fabricated."""
        if direction == "Long":
            flags = [price_above, has_bos_demand, has_choch_demand,
                     bool(hh_ts or hl_ts), has_bull_sweep, has_bull_confirm, trend_long]
        else:
            flags = [price_below, has_bos_supply, has_choch_supply,
                     bool(lh_ts or ll_ts), has_bear_sweep, has_bear_confirm, trend_short]
        return sum(1 for f in flags if f)

    def _gate_debug(direction):
        sig = _signals(direction)
        score, _bd = _edge_for(direction)
        # Granular per-gate signals (for the /diagnostics breakdown). Structure is
        # ANY ONE of BOS/CHOCH/swing — these individual flags are shown for
        # visibility; "structure_confirmed" is the actual gate.
        if direction == "Long":
            _bos, _choch = has_bos_demand, has_choch_demand
            _swing       = bool(hh_ts or hl_ts)
            _zone_pres   = nearest_demand is not None
            _zone_mit    = has_mitigated_demand
            _reaction    = reaction_long
        else:
            _bos, _choch = has_bos_supply, has_choch_supply
            _swing       = bool(lh_ts or ll_ts)
            _zone_pres   = nearest_supply is not None
            _zone_mit    = has_mitigated_supply
            _reaction    = reaction_short
        return {
            "direction":             direction,
            "bos":                   bool(_bos),
            "choch":                 bool(_choch),
            "swing":                 bool(_swing),
            "zone_present":          bool(_zone_pres),
            "zone_mitigated":        bool(_zone_mit),
            "reaction":              bool(_reaction),
            "session_pref":          bool(session_pref),
            "zone_valid":            bool(sig["zone_valid"]),
            "vwap_confirmed":        bool(sig["vwap_confirmed"]),
            "structure_confirmed":   bool(sig["structure_confirmed"]),
            "candle_confirmed":      bool(sig["confirmation_candle"]),
            "liquidity_sweep":       bool(sig["liquidity_sweep"]),
            "trend_aligned":         bool(trend_long if direction == "Long" else trend_short),
            "volume_confirmed":      None,
            "confirmations_passed":  _confirmations(direction),
            "confirmations_needed":  min_confirmations,
            "require_vwap":          require_vwap,
            "require_structure":     require_structure,
            "require_zone":          require_zone,
            "ready_threshold":       ready_threshold,
            "full_ready_threshold":  full_ready_threshold,
            "conflicting_structure": true_conflict,
            "volatility_block":      vol_block,
            "edge_score":            score,
            "edge_ok":               bool(score >= ready_threshold),
            "failed_conditions":     [],
        }

    def _failed_gates(direction):
        gd = _gate_debug(direction)
        fails = []
        if gd["conflicting_structure"]:
            fails.append("conflicting_structure")
        # Zone, VWAP & structure are hard gates only when the mode requires them
        # (SWING); in SCALP they are confirmations, surfaced via confirmations_passed
        # below. A demoted zone still scores its 25pt Edge component.
        if require_zone and not gd["zone_valid"]:
            fails.append("zone_valid")
        if require_vwap and not gd["vwap_confirmed"]:
            fails.append("vwap_confirmed")
        if require_structure and not gd["structure_confirmed"]:
            fails.append("structure_confirmed")
        if gd["confirmations_passed"] < min_confirmations:
            fails.append("confirmations(%d<%d)"
                         % (gd["confirmations_passed"], min_confirmations))
        if gd["volatility_block"]:
            fails.append("volatility_block")
        if not gd["edge_ok"]:
            fails.append("edge_score(%d<%d)" % (gd["edge_score"], ready_threshold))
        gd["failed_conditions"] = fails
        return gd, fails

    def _readiness(direction):
        """Readiness LEVEL for a direction: 'FULL' (Edge >= full-READY floor),
        'EARLY' (Edge >= the actionable floor but below full) or None. The gate
        conditions (mode-required zone/vwap/structure, confirmations count, no true
        conflict, no vol block) are IDENTICAL for FULL and EARLY — only the Edge band
        differs. SWING sets both floors to 80, so it only ever returns 'FULL' or None
        (no EARLY band): it reduces to the historical zone AND vwap AND structure AND
        edge>=80 gate exactly. SCALP: actionable floor 35, full floor 50. The
        consumed/broken-zone safety still lives downstream in full_analysis
        (zone_broken_active / zone_mitigated_near)."""
        sig = _signals(direction)
        score, _bd = _edge_for(direction)
        gates_ok = bool(
            (not require_zone or sig["zone_valid"]) and not true_conflict and not vol_block
            and (not require_vwap or sig["vwap_confirmed"])
            and (not require_structure or sig["structure_confirmed"])
            and _confirmations(direction) >= min_confirmations
        )
        if not gates_ok:
            return None
        if score >= full_ready_threshold:
            return "FULL"
        if score >= ready_threshold:
            return "EARLY"
        return None

    def _is_ready(direction):
        """Actionable (full OR early) READY for a direction."""
        return _readiness(direction) is not None

    def _confluences(direction):
        """Per-condition detail for journal/embed/edge-display. `zone_mitigated`
        carries the zone-VALID signal (mitigation + reaction) so the display Edge
        Score (compute_edge_breakdown) and this gate score stay identical, and so
        the consumed-zone override in full_analysis is bypassed ONLY on a genuine
        valid reaction."""
        if direction == "Short":
            return {
                "direction":           "Short",
                "bos":                 bool(has_bos_supply),
                "choch":               bool(has_choch_supply),
                "structure_confirmed": structure_short,
                "confirmation":        bool(has_bear_confirm or zone_valid_short),
                "confirmation_candle": bool(has_bear_confirm),
                "vwap":                price_below,
                "vwap_status":         vwap_status,
                "vwap_value":          vwap,
                "zone_confirmed":      bool(has_sup_confirm),
                "liquidity_sweep":     bool(has_bear_sweep),
                "zone_mitigated":      zone_valid_short,
                "edge_ok":             bool(_edge_for("Short")[0] >= ready_threshold),
            }
        return {
            "direction":           "Long" if direction == "Long" else None,
            "bos":                 bool(has_bos_demand),
            "choch":               bool(has_choch_demand),
            "structure_confirmed": structure_long,
            "confirmation":        bool(has_bull_confirm or zone_valid_long),
            "confirmation_candle": bool(has_bull_confirm),
            "vwap":                price_above,
            "vwap_status":         vwap_status,
            "vwap_value":          vwap,
            "zone_confirmed":      bool(has_dem_confirm),
            "liquidity_sweep":     bool(has_bull_sweep),
            "zone_mitigated":      zone_valid_long,
            "edge_ok":             bool(_edge_for("Long")[0] >= ready_threshold),
        }

    def _dir_block(direction):
        gd, fails = _failed_gates(direction)
        score = gd["edge_score"]
        readiness = _readiness(direction)
        ready = readiness is not None
        checklist = {
            "zone":      gd["zone_valid"],
            "structure": gd["structure_confirmed"],
            "vwap":      gd["vwap_confirmed"],
            "edge":      gd["edge_ok"],
        }
        met = sum(1 for v in checklist.values() if v)
        if true_conflict:
            reason = ("Conflicting structure — opposing structure on both sides within "
                      f"{CONFLICT_WINDOW_MIN} min, Edge gap {conflict_gap} <= "
                      f"{conflict_wait_gap}. Stand aside.")
        elif ready:
            _lvl = "EARLY READY" if readiness == "EARLY" else "READY"
            if require_vwap and require_structure:
                reason = (f"{direction} {_lvl} — zone reaction, structure, VWAP and "
                          f"Edge {score} aligned.")
            else:
                _lead = "zone reaction + " if gd["zone_valid"] else ""
                reason = (f"{direction} {_lvl} — {_lead}"
                          f"{gd['confirmations_passed']} confirmations, Edge {score}.")
        elif vol_block:
            held = "too volatile" if vol.get("regime") == "HIGH_BLOCK" else "too quiet"
            reason = (f"{direction} on hold — market {held}: "
                      f"{vol.get('display', 'volatility out of range')}.")
        else:
            reason = (f"{direction} WAIT — failed gate(s): "
                      f"{', '.join(fails) if fails else 'confluence'}.")
        return {
            "direction":   direction,
            "checklist":   checklist,
            "met":         met,
            "ready":       ready,
            "readiness":   readiness,
            "score":       score,
            "label":       "READY" if ready else "WAIT",
            "missing":     fails,
            "reason":      reason,
            "conflict":    true_conflict,
            "gate_debug":  gd,
            "confluences": _confluences(direction),
        }

    directions = {"Long": _dir_block("Long"), "Short": _dir_block("Short")}

    def _ret(payload):
        payload["directions"] = directions
        # Diagnostics (additive): per-direction Edge Scores + conflict resolution.
        # setdefault so a caller that already set them (none today) is never clobbered.
        payload.setdefault("long_score", long_score)
        payload.setdefault("short_score", short_score)
        payload.setdefault("conflict_gap", conflict_gap)
        payload.setdefault("dominant_direction", dominant_direction)
        return payload

    # ── True conflict → stand aside (both sides). SCALP only reaches here when the
    #    two directions are balanced within CONFLICT_WAIT_GAP; a clearly dominant
    #    side falls through to candidate selection below. ──
    if true_conflict:
        gd = _gate_debug("Long")
        gd["conflicting_structure"] = True
        gd["failed_conditions"] = ["conflicting_structure"]
        return _ret({
            "label":      "WAIT",
            "direction":  None,
            "score":      0,
            "confluences": _confluences(None),
            "candidate":  None,
            "reason":     ("Conflicting structure — opposing structure on both sides "
                           f"within {CONFLICT_WINDOW_MIN} min, Edge gap {conflict_gap} "
                           f"<= {conflict_wait_gap}. Stand aside."),
            "missing":    ["conflicting_structure"],
            "gate_debug": gd,
        })

    # ── Candidate direction. SWING is VWAP-led: the side price sits on vs VWAP
    #    (only one side can gate — price cannot be both above and below), falling
    #    back to the side with structure when VWAP is unusable. SCALP no longer
    #    requires VWAP, so it ranks both sides by readiness, zone validity,
    #    confirmation count and Edge, using the VWAP side only as a tie-break
    #    (Long wins an exact tie, preserving the legacy default). ──
    if require_vwap:
        if price_above:
            candidate = "Long"
        elif price_below:
            candidate = "Short"
        elif structure_short and not structure_long:
            candidate = "Short"
        else:
            candidate = "Long"
    else:
        def _cand_key(d):
            return (
                1 if directions[d]["ready"] else 0,
                1 if directions[d]["gate_debug"]["zone_valid"] else 0,
                _confirmations(d),
                directions[d]["score"],
                1 if ((d == "Long" and price_above)
                      or (d == "Short" and price_below)) else 0,
            )
        candidate = "Long" if _cand_key("Long") >= _cand_key("Short") else "Short"

    # ── Score-aware conflict (SCALP): opposing structure on both sides but NOT a
    #    true conflict → commit to the dominant (higher-Edge) side instead of
    #    standing aside. SWING never reaches here (true_conflict already returned
    #    WAIT above when opposing_present). ──
    if opposing_present and not true_conflict and score_aware_conflict:
        candidate = dominant_direction

    blk = directions[candidate]
    if blk["ready"]:
        score = blk["score"]
        return _ret({
            "label":      "Strong Trade" if score >= 90 else "Possible Trade",
            "direction":  candidate,
            "candidate":  candidate,
            "score":      score,
            "readiness":  blk["readiness"],
            "confluences": blk["confluences"],
            "reason":     blk["reason"],
            "missing":    [],
            "gate_debug": blk["gate_debug"],
        })

    # ── WAIT — name the failed gate(s) for the candidate side ──
    gd, fails = _failed_gates(candidate)
    if vol_block:
        held = "too volatile" if vol.get("regime") == "HIGH_BLOCK" else "too quiet"
        reason = (f"{candidate} on hold — market {held}: "
                  f"{vol.get('display', 'volatility out of range')}.")
    else:
        reason = (f"{candidate} WAIT — failed gate(s): "
                  f"{', '.join(fails) if fails else 'confluence'}.")
    return _ret({
        "label":      "WAIT",
        "direction":  None,
        "candidate":  candidate,
        "score":      blk["score"],
        "confluences": blk["confluences"],
        "reason":     reason,
        "missing":    fails,
        "gate_debug": gd,
    })


def _dynamic_stop_plan(direction, entry, nearest_demand, nearest_supply,
                       ticker, volatility, mode=None):
    """ATR(14)-based dynamic stop, blended with the nearest demand/supply structure.

    The final stop is the WIDER of two candidates — never tighter:
      • atrStop       = entry ∓ (ATR × multiplier)
      • structureStop = nearest_demand − stop_buf (Long) / nearest_supply + stop_buf (Short)
    then floored at the instrument's `min_stop_ticks` so it can never be
    unrealistically tight, and snapped to whole ticks.

    Multiplier precedence — VOLATILITY WINS over trading mode:
      2.0 when the regime is elevated/extreme (HIGH_CAUTION / HIGH_BLOCK),
      else 1.0 in SCALP, else 1.5 (normal intraday / SWING).

    FAIL-CLOSED at the trade-plan layer only: returns {"ok": False, "reason": ...}
    when the ATR reading needed to size the stop is missing/stale, or when the
    resulting stop would violate a directional safety rule. (The volatility GATE's
    scoring stays fail-open elsewhere — this gate is execution safety, not scoring.)
    """
    spec = spec_for(ticker)
    tick      = spec["tick_size"]
    pv        = spec["point_value"]
    buf       = spec["stop_buf"]
    min_ticks = int(spec["min_stop_ticks"])

    vol = volatility or {}
    atr = vol.get("atr_pts")
    if vol.get("status") != "ok" or atr is None:
        return {"ok": False, "reason": "ATR stop data unavailable"}
    try:
        atr = float(atr)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "ATR stop data unavailable"}
    if atr <= 0:
        return {"ok": False, "reason": "ATR stop data unavailable"}

    # ── Multiplier (volatility wins over mode) ──
    regime = vol.get("regime")
    mode   = (mode or TRADING_MODE)
    if regime in ("HIGH_CAUTION", "HIGH_BLOCK"):
        mult = 2.0
    elif mode == "SCALP":
        mult = 1.0
    else:
        mult = 1.5

    atr_dist = atr * mult
    if direction == "Long":
        atr_stop = entry - atr_dist
        structure_stop = (nearest_demand - buf) if nearest_demand is not None else None
        # Whichever sits FURTHER BELOW entry = the safer, wider stop.
        calc_stop = min(atr_stop, structure_stop) if structure_stop is not None else atr_stop
    else:  # Short
        atr_stop = entry + atr_dist
        structure_stop = (nearest_supply + buf) if nearest_supply is not None else None
        # Whichever sits FURTHER ABOVE entry.
        calc_stop = max(atr_stop, structure_stop) if structure_stop is not None else atr_stop

    calc_dist  = abs(entry - calc_stop)
    calc_ticks = (calc_dist / tick) if tick else 0.0
    # Snap UP to whole ticks (never tighter than calculated), then apply the floor.
    final_ticks = max(math.ceil(calc_ticks - 1e-9), min_ticks)
    final_dist  = final_ticks * tick
    final_stop  = (entry - final_dist) if direction == "Long" else (entry + final_dist)

    # ── Safety: the stop must sit on the correct side of entry ──
    if direction == "Long" and not (final_stop < entry):
        return {"ok": False, "reason": "Computed stop is not below entry (Long safety)."}
    if direction == "Short" and not (final_stop > entry):
        return {"ok": False, "reason": "Computed stop is not above entry (Short safety)."}

    risk_points = abs(entry - final_stop)
    if risk_points <= 0:
        return {"ok": False, "reason": "Invalid stop distance."}

    return {
        "ok":                  True,
        "multiplier":          mult,
        "atr_pts":             round(atr, 4),
        "atr_stop":            round(atr_stop, 4),
        "structure_stop":      (round(structure_stop, 4) if structure_stop is not None else None),
        "calculated_stop":     round(calc_stop, 4),
        "calculated_ticks":    round(calc_ticks, 2),
        "min_stop_ticks":      min_ticks,
        "tick_size":           tick,
        "stop_distance_ticks": int(final_ticks),
        "final_stop":          round(final_stop, 4),
        "risk_points":         round(risk_points, 4),
        "risk_dollars":        round(risk_points * pv, 2),
        "regime":              regime,
        "vol_label":           vol.get("label"),
        "vol_ratio":           vol.get("ratio"),
        "nearest_demand":      nearest_demand,
        "nearest_supply":      nearest_supply,
        "min_floor_applied":   final_ticks > math.ceil(calc_ticks - 1e-9),
    }


def build_strict_trade_plan(direction, ticker, current_price,
                            nearest_supply, nearest_demand,
                            volatility=None, mode=None):
    """Fixed-target plan per instrument with an ATR(14)-based DYNAMIC stop.

    Targets stay fixed (MNQ TP1/TP2/TP3 = 20/40/60 pts, MGC = 5/10/15). The stop is
    sized by `_dynamic_stop_plan` — ATR×multiplier blended with the nearest zone and
    floored at the instrument's min stop ticks. Returns a no-plan dict if the
    anchoring zone is missing, the ATR reading needed to size the stop is
    unavailable, or (SWING / ENFORCE_MIN_RR) the fixed TP2 can't make 1:2.
    """
    spec = spec_for(ticker)
    inst = instrument_of(ticker)
    tp1d, tp2d, buf, pv = spec["tp1"], spec["tp2"], spec["stop_buf"], spec["point_value"]
    tp3d = spec.get("tp3", tp2d * 1.5)

    def no_plan(reason):
        return {"trade_plan": False, "reason": reason,
                "entry_zone": None, "stop_loss": None,
                "target1": None, "target2": None,
                "rr": None, "direction": direction,
                "instrument": inst, "point_value": pv,
                # Additive management keys kept present (None) for reader parity.
                "target3": None, "be_level": None, "partial_level": None,
                "runner_target": None, "risk_points": None, "reward_points": None,
                "rr_num": None, "max_invalidation": None, "management": None,
                # Additive ATR-stop metadata kept present (None) for reader parity.
                "atr_pts": None, "atr_multiplier": None, "atr_stop": None,
                "structure_stop": None, "calculated_stop": None,
                "min_stop_ticks": None, "tick_size": None,
                "stop_distance_ticks": None, "risk_dollars_per_contract": None,
                "nearest_demand": nearest_demand, "nearest_supply": nearest_supply,
                "volatility_regime": None, "volatility_label": None,
                "min_floor_applied": None}

    if direction == "Long":
        anchor = nearest_demand
        if anchor is None:
            return no_plan("No demand zone to anchor the entry.")
        lo, hi = anchor, anchor + buf
        t1, t2, t3 = anchor + tp1d, anchor + tp2d, anchor + tp3d
    else:  # Short
        anchor = nearest_supply
        if anchor is None:
            return no_plan("No supply zone to anchor the entry.")
        lo, hi = anchor - buf, anchor
        t1, t2, t3 = anchor - tp1d, anchor - tp2d, anchor - tp3d

    entry = (lo + hi) / 2

    # ── ATR(14)-based dynamic stop (replaces the old fixed anchor ± buf stop) ──
    sp = _dynamic_stop_plan(direction, entry, nearest_demand, nearest_supply,
                            ticker, volatility, mode)
    if not sp["ok"]:
        # ATR unavailable / safety violation → no trade plan (caller maps to WAIT).
        return no_plan(sp["reason"])
    stop = sp["final_stop"]
    risk = sp["risk_points"]
    if risk <= 0:
        return no_plan("Invalid stop distance.")
    r1, r2 = abs(t1 - entry) / risk, abs(t2 - entry) / risk
    # Wider ATR stops lower the fixed-target R:R. SCALP displays R:R instead of
    # gating on it; SWING (ENFORCE_MIN_RR) keeps the strict ">= 1:2 on TP2" veto.
    if cfg("ENFORCE_MIN_RR") and r2 < 2.0:
        return no_plan(f"Stop distance {risk:.1f} pts makes fixed TP2 only {r2:.1f}R (min 1:2).")

    # ── Additive trade-management plan (does NOT alter the existing fields) ──
    be_level  = t1                 # move stop to breakeven once TP1 prints
    partial   = (t1 + t2) / 2      # scale out a partial between TP1 and TP2
    runner    = t3                 # let the runner ride to TP3
    reward    = abs(t3 - entry)    # reward measured to the runner target
    rr_runner = reward / risk
    zone_word = "demand" if direction == "Long" else "supply"
    side_word = "below" if direction == "Long" else "above"

    fmt = (lambda v: f"{v:.1f}") if inst == "MNQ" else (lambda v: f"{v:.2f}")
    invalidation = (f"Price closing {side_word} the stop ({fmt(stop)}) or losing the "
                    f"{zone_word} zone invalidates the setup.")
    return {
        "trade_plan": True,
        "reason": f"{inst} {direction} — fixed targets (TP1 {tp1d:g} / TP2 {tp2d:g} / TP3 {tp3d:g} pts), ATR-dynamic stop.",
        "entry_zone": f"{fmt(lo)}–{fmt(hi)}",
        "stop_loss":  fmt(stop),
        "target1":    fmt(t1),
        "target2":    fmt(t2),
        "rr":         f"T1 1:{r1:.1f} / T2 1:{r2:.1f}",
        "direction":  direction,
        "instrument": inst,
        "point_value": pv,
        # ── Additive management fields ──
        "target3":          fmt(t3),
        "be_level":         fmt(be_level),
        "partial_level":    fmt(partial),
        "runner_target":    fmt(runner),
        "risk_points":      round(risk, 2),
        "reward_points":    round(reward, 2),
        "rr_num":           round(rr_runner, 2),
        "max_invalidation": invalidation,
        # ── Additive ATR dynamic-stop metadata (display/diagnostics; single source) ──
        "atr_pts":                   sp["atr_pts"],
        "atr_multiplier":            sp["multiplier"],
        "atr_stop":                  fmt(sp["atr_stop"]),
        "structure_stop":            (fmt(sp["structure_stop"]) if sp["structure_stop"] is not None else None),
        "calculated_stop":           fmt(sp["calculated_stop"]),
        "min_stop_ticks":            sp["min_stop_ticks"],
        "tick_size":                 sp["tick_size"],
        "stop_distance_ticks":       sp["stop_distance_ticks"],
        "risk_dollars_per_contract": sp["risk_dollars"],
        "nearest_demand":            nearest_demand,
        "nearest_supply":            nearest_supply,
        "volatility_regime":         sp["regime"],
        "volatility_label":          sp["vol_label"],
        "min_floor_applied":         sp["min_floor_applied"],
        # Numeric snapshot consumed by the trade-management watcher. Stop/risk come
        # from the FINAL dynamic stop only (no drift vs the displayed stop).
        "management": {
            "direction": direction, "instrument": inst, "point_value": pv,
            "entry": round(entry, 4), "entry_lo": round(lo, 4), "entry_hi": round(hi, 4),
            "stop": round(stop, 4),
            "tp1": round(t1, 4), "tp2": round(t2, 4), "tp3": round(t3, 4),
            "be_level": round(be_level, 4), "partial": round(partial, 4),
            "runner": round(runner, 4), "risk_points": round(risk, 4),
        },
    }


# ---------------------------------------------------------------------------
# Full analysis
# ---------------------------------------------------------------------------

# ── Per-evaluation timing instrumentation ───────────────────────────────────
# A thread-local accumulator lets full_analysis() and the card builder record how
# long each phase takes WITHOUT changing their signatures or affecting the many
# read-only callers (dashboard / status / why / periodic loop). The webhook
# worker calls _eval_timing_begin() before scoring an alert; every _timed(key)
# block then adds to that thread's dict. Callers that never call _begin() (every
# read-only path) get a no-op, so there is zero overhead and no cross-talk.
_EVAL_TIMING = threading.local()


def _eval_timing_begin():
    _EVAL_TIMING.data = {}


def _eval_timing_get():
    return getattr(_EVAL_TIMING, "data", None)


@contextlib.contextmanager
def _timed(key):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        d = getattr(_EVAL_TIMING, "data", None)
        if d is not None:
            d[key] = round(d.get(key, 0.0) + (time.perf_counter() - t0) * 1000.0, 3)


def full_analysis(current_price_override=None, ticker_override=None):
    # Which instrument this analysis is for: an explicit override (dashboard tab)
    # wins; otherwise fall back to the most-recently-alerted instrument.
    active_ticker = instrument_of(ticker_override) if ticker_override else _active_ticker()

    score_window = cfg("SCORE_WINDOW_MIN")
    if score_window:
        bullish, bearish, counts = score_alerts(alerts_in_window(score_window))
    else:
        bullish, bearish, counts = calculate_scores()
    bias, strength           = calculate_bias(bullish, bearish)
    confidence               = calculate_confidence(bullish, bearish)
    quality                  = calculate_trade_quality(bias, confidence, bullish, bearish)
    edge_score               = calculate_edge_score(bias, confidence, strength)

    with _timed("indicatorCalcMs"):
        last_price_by_type, all_supply, all_demand = get_price_context(active_ticker)
    current_price = current_price_override if current_price_override is not None else current_price_for(active_ticker)
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

    # ── Strict recommendation ruleset — AUTHORITATIVE verdict ────────────────
    # A trade is recommended ONLY when BOS + CHOCH + 5m confirmation candle + the
    # price-vs-VWAP filter all align on one side. Score 0-100 → Strong/Possible/WAIT.
    # active_ticker resolved at the top (honours the dashboard's instrument tab).
    with _timed("indicatorCalcMs"):
        vwap_value, vwap_status = get_vwap(active_ticker)
    with _timed("volatilityCalcMs"):
        volatility = get_volatility(active_ticker)
    # Preferred-session state (ET) computed ONCE here and threaded through the gate
    # AND stored on the result, so the +10 Session Bonus the gate credits and the
    # Session block on the card/status are derived from the same instant.
    session_state = get_session_state()
    # Market-session state (CME/COMEX hours, shared by MNQ & MGC). Computed once
    # here and applied as a single neutralising override right before `return`, so
    # all result keys still exist (single-return-path invariant) and the gate is
    # paused — not deleted — while the market is closed.
    market = market_session_status()
    with _timed("scoringMs"):
        strict = evaluate_strict_setup(
            current_price, active_ticker, vwap_value, vwap_status,
            nearest_supply, nearest_demand, bullish, bearish, confidence, ALERT_HISTORY,
            volatility=volatility, session=session_state,
        )
    strict_label     = strict["label"]
    strict_score     = strict["score"]
    strict_direction = strict["direction"]
    confluences      = strict["confluences"]
    strict_reason    = strict.get("reason", "")
    strict_missing   = strict.get("missing", [])

    if strict_label in ("Strong Trade", "Possible Trade") and strict_direction:
        trade_plan = build_strict_trade_plan(
            strict_direction, active_ticker, current_price, nearest_supply, nearest_demand,
            volatility=volatility, mode=TRADING_MODE,
        )
        if trade_plan["trade_plan"]:
            # EARLY READY (SCALP Edge 35-49) is actionable but labelled lower
            # conviction; a full READY is Edge >= the full-READY floor. Both still
            # require a valid trade plan (R:R + anchor zone), so an EARLY setup that
            # can't form a real plan still falls through to WAIT below.
            _early = strict.get("readiness") == "EARLY"
            if strict_direction == "Long":
                verdict = "LONG EARLY READY" if _early else "LONG READY"
            else:
                verdict = "SHORT EARLY READY" if _early else "SHORT READY"
        else:
            # Fixed targets can't meet the 1:2 R:R (or no anchor zone) → no trade.
            verdict        = "WAIT"
            strict_label   = "WAIT"
            strict_reason  = trade_plan.get("reason", strict_reason)
    else:
        verdict    = "WAIT"
        trade_plan = {
            "trade_plan": False,
            "reason":     strict_reason or "Strict conditions not met.",
            "entry_zone": None, "stop_loss": None,
            "target1":    None, "target2":   None,
            "rr":         None, "direction": strict_direction,
            "instrument": active_ticker, "point_value": point_value_for(active_ticker),
        }

    # get_setup_stage retained for lifecycle display context in the embed.
    setup_stage, stage_next_step, stage_entry_rule, stage_invalidation, stage_direction = get_setup_stage(
        current_price, nearest_supply, nearest_demand, bullish, bearish, ALERT_HISTORY
    )

    # Align the displayed lifecycle stage with the authoritative strict verdict.
    if is_actionable(verdict):
        setup_stage      = "Trade Ready"
        stage_direction  = strict_direction
        if is_early_ready(verdict):
            stage_next_step  = strict_reason or (
                "Early setup — enter aggressive (reduced size) or wait for the "
                "confirmation candle.")
            stage_entry_rule = (
                f"Enter {strict_direction.lower()} early at reduced size, "
                "or wait for a confirmation candle.")
        else:
            stage_next_step  = strict_reason or "All strict conditions met — enter now."
            stage_entry_rule = f"Enter {strict_direction.lower()} per strict checklist."

    # ── Zone Broken: cancel setup, reduce confidence, mark structure invalidated ──
    # Scoped to the analyzed instrument: a broken MGC zone must not invalidate a
    # valid MNQ setup (and vice versa). Untagged (legacy) breaks apply globally.
    zone_broken_active = (
        ZONE_BROKEN_AT is not None
        and ZONE_BROKEN_AT.get("instrument") in (None, instrument_of(active_ticker))
    )
    if zone_broken_active:
        confidence         = max(0, confidence - 30)
        setup_stage        = "Watching"
        stage_direction    = None
        stage_next_step    = "Wait for structure to rebuild after zone break"
        stage_entry_rule   = "—"
        stage_invalidation = "Structure invalidated — zone broken"
        verdict            = "WAIT"
        strict_label       = "WAIT"
        strict_score       = 0
        strict_reason      = "Structure invalidated — zone broken."
        trade_plan         = {
            "trade_plan": False,
            "reason":     "Structure invalidated — zone broken.",
            "entry_zone": None, "stop_loss": None,
            "target1":    None, "target2": None,
            "rr":         None, "direction": None,
        }

    # ── Zone Mitigated: a consumed zone blocks entries UNLESS the mitigation is
    # a confirmed bullish reaction (handled as a LONG via the strict gate). ──
    mitig_confirmed = bool(confluences.get("zone_mitigated"))
    near_sup_mz, mz_sup_price = is_near_mitigated_zone(nearest_supply)
    near_dem_mz, mz_dem_price = is_near_mitigated_zone(nearest_demand)
    # Block only when mitigation is active (flag not yet cleared by a structure
    # reset) AND the ACTIVE instrument's nearest zone actually sits on a mitigated
    # price. The flag alone is global, so requiring proximity keeps a mitigated
    # MGC zone from WAIT-blocking an unrelated MNQ alert (and vice-versa).
    zone_mitigated_near = (
        ZONE_MITIGATED_FLAG and (near_sup_mz or near_dem_mz)
        and not zone_broken_active
        and not mitig_confirmed
    )
    mitigated_zone_price = (
        mz_sup_price or mz_dem_price
        or (MITIGATED_PRICES[-1]["price"] if MITIGATED_PRICES else None)
    )
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
        strict_label       = "WAIT"
        strict_reason      = "Zone consumed — wait for fresh supply or demand zone."

    why  = build_why(bias, confidence, bullish, bearish, counts,
                     verdict, overextended, risk_label)
    plan = build_trade_plan(bias, strength, bullish, bearish, counts)

    # Dashboard price readout (DISPLAY-ONLY; the gate above uses `current_price`).
    # An explicit price override is echoed as-is; otherwise show the fresh alert
    # price, falling back to the auto-fetched market price so the readout never
    # goes blank after a restart / quiet market.
    if current_price_override is not None:
        display_price, price_source = current_price, "alert"
    else:
        display_price, price_source = display_price_for(active_ticker)

    result = dict(
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
        display_price=display_price, price_source=price_source,
        last_price_by_type=last_price_by_type,
        nearest_supply=nearest_supply, nearest_demand=nearest_demand,
        structure_label=structure_label, structure_class=structure_class,
        structure_detail=structure_detail,
        risk_label=risk_label, risk_detail=risk_detail, overextended=overextended,
        zone_broken_active=zone_broken_active,
        zone_mitigated_near=zone_mitigated_near,
        mitigated_zone_price=mitigated_zone_price,
        strict_label=strict_label, strict_score=strict_score,
        strict_direction=strict_direction, strict_reason=strict_reason,
        strict_missing=strict_missing, confluences=confluences,
        vwap_value=vwap_value, vwap_status=vwap_status,
        volatility=volatility,
        active_ticker=active_ticker,
    )

    # Preferred-session state (Eastern Time) — drives the +10 Session Bonus in the
    # Edge Score and the Session block on /status, /why and the trade card. Reuses
    # the SAME instant threaded into the gate so the bonus stays consistent.
    result["session"] = session_state
    # Per-gate WAIT debug (which READY gate failed) — surfaced on /status + logs.
    result["gate_debug"] = strict.get("gate_debug")
    # Candidate direction the gate evaluated (Long/Short/None) — for /diagnostics.
    result["gate_candidate"] = strict.get("candidate")

    # ── Tiered alert level (SCALP early-warning ladder) — additive, DISPLAY-ONLY.
    #    Computed independently of the verdict; it NEVER alters verdict / score /
    #    direction / trade plan, so SWING decisions stay byte-for-byte identical.
    #      WATCH           = price is at a fresh trade-side zone (within WATCH_PCT)
    #      ARMED           = at the zone AND >= 1 real confirmation present
    #      WATCH FOR ENTRY = at the zone, confirmations satisfied AND Edge >= 50 but
    #                        the full READY gate has not passed yet — one decisive
    #                        trigger (BOS/CHOCH/rejection candle/VWAP) tips it READY
    #      READY           = the full READY gate passed (verdict LONG/SHORT READY)
    #      None            = no candidate side, zone consumed/broken, price away
    #    The trade-side zone is demand for Long / supply for Short. A consumed
    #    (mitigated-near) or broken zone yields None — we never WATCH a dead zone. ──
    alert_level = None
    _cand   = result.get("gate_candidate")
    _gd_lvl = result.get("gate_debug") or {}
    # Actionable verdicts (full READY *and* EARLY READY) own the signal — mark the
    # operational level READY so the tiered ladder defers (no redundant WATCH/ARMED).
    if is_actionable(result["verdict"]):
        alert_level = "READY"
    elif (_cand in ("Long", "Short")
          and not zone_broken_active and not zone_mitigated_near):
        _zone = nearest_demand if _cand == "Long" else nearest_supply
        if (_zone and current_price
                and abs(current_price - _zone) / _zone <= cfg("WATCH_PCT")):
            _confs = int(_gd_lvl.get("confirmations_passed") or 0)
            _need  = int(_gd_lvl.get("confirmations_needed") or 0)
            _edge  = int(_gd_lvl.get("edge_score") or 0)
            if _confs >= _need and _edge >= 50:
                alert_level = "WATCH FOR ENTRY"
            elif _confs >= 1:
                alert_level = "ARMED"
            else:
                alert_level = "WATCH"
    result["alert_level"] = alert_level

    # ── Unified Edge Score (single source of truth) ──────────────────────────
    # The transparent, confluence-based Edge Score replaces the legacy bias-derived
    # score on EVERY user-facing surface (status, why, alerts, journal, recaps,
    # dashboard, trade management). The legacy value is kept internal-only as a
    # last-resort ranking fallback for old/manual journal entries; never displayed.
    eb = _analysis_edge_breakdown(result)
    result["legacy_edge_score"] = edge_score
    result["edge_breakdown"]    = eb
    result["edge_score"]        = eb["score"]
    result["edge_grade"]        = eb["grade"]
    # ── Conviction tier (display-only, score-banded) — distinct from alert_level
    #    (operational dispatch state). HIGH CONVICTION >=70 / READY 50-69 / EARLY
    #    READY 35-49 / None. Names the STRENGTH of the Edge Score; never gates. ──
    result["conviction_tier"]   = _score_tier(result["edge_score"])

    # ── Gate diagnostics (additive, display-only) — surfaced on every alert,
    #    /status and the dashboard probability gauge. These are the SAME numbers
    #    the gate used to decide READY / WAIT / conflict (sourced from `strict`),
    #    plus the single volatility read, so the displayed values can never diverge
    #    from the decision. All keys ALWAYS set (single-return-path invariant). ──
    result["long_score"]            = strict.get("long_score")
    result["short_score"]           = strict.get("short_score")
    result["conflict_gap"]          = strict.get("conflict_gap")
    result["dominant_direction"]    = strict.get("dominant_direction")
    result["current_atr"]           = volatility.get("atr_pts")
    result["volatility_multiplier"] = volatility.get("ratio")
    result["ready_reason"]          = strict_reason
    result["rejected_reasons"]      = strict_missing
    # ── Alert diagnostics (additive, DISPLAY/observability ONLY) ─────────────
    # One transparent snapshot bundling the two per-direction scores, the unified
    # Edge Score, the conflict gap, the live ATR / volatility multiplier, plus the
    # human READY reason and the humanised rejected gate reasons. Attached to every
    # alert + /status + the dashboard diagnostics modules. NEVER feeds the trade
    # decision, so SWING is byte-for-byte unchanged. The market-closed override
    # below zeros it so a paused market can't present a stale "why".
    _diag_gd    = result.get("gate_debug") or {}
    _diag_long  = int(strict.get("long_score",  _diag_gd.get("long_score",  0)) or 0)
    _diag_short = int(strict.get("short_score", _diag_gd.get("short_score", 0)) or 0)
    _diag_gap   = int(strict.get("conflict_gap", _diag_gd.get("conflict_gap", 0)) or 0)
    if _diag_long > _diag_short:
        _diag_dom = "Long"
    elif _diag_short > _diag_long:
        _diag_dom = "Short"
    else:
        _diag_dom = "Neutral"
    _diag_ready = is_actionable(result["verdict"])
    result["alert_diagnostics"] = {
        "long_score":            _diag_long,
        "short_score":           _diag_short,
        "edge_score":            int(result.get("edge_score") or 0),
        "conflict_gap":          _diag_gap,
        "dominant_direction":    _diag_dom,
        "current_atr":           (volatility or {}).get("atr_pts"),
        "volatility_multiplier": (volatility or {}).get("ratio"),
        "ready_reason":          (strict_reason if _diag_ready else ""),
        "rejected_reasons":      _humanize_gate_rejections(_diag_gd),
        "current_score":         int(result.get("edge_score") or 0),
        "required_score":        int(cfg("EDGE_READY_THRESHOLD")),
    }
    # Decision-support header (Quality/Probability/Risk/Reward/Window/Recommendation)
    # — all real-derived from the fields set above.
    result["decision_support"]      = _decision_support(result)

    # ── Per-direction Edge Scores (additive, display-only) ───────────────────
    # The dashboard Long/Short toggle shows the bull case AND the bear case. The
    # favored side REUSES the authoritative breakdown verbatim (guaranteed parity
    # with edge_score above); the other side is scored from its own confluences
    # with a WAIT state so it is never floored at 75. A conflict or a hard blocker
    # (zone broken / consumed) zeros both sides — neither is tradeable.
    dirs_raw = strict.get("directions") or {}
    auth_dir = (result.get("confluences") or {}).get("direction")
    blockers = bool(result.get("zone_broken_active") or result.get("zone_mitigated_near"))
    out_dirs = {}
    for _d in ("Long", "Short"):
        block = dict(dirs_raw.get(_d) or {})
        if block.get("conflict"):
            block.update(
                ready=False, label="WAIT", score=0,
                edge_score=0, edge_grade=_grade_for_score(0),
                edge_breakdown={
                    "score": 0, "grade": _grade_for_score(0),
                    "score_breakdown": [],
                    "risk_adjustments": [{"label": "Conflicting structure — stand aside", "points": None}],
                    "reasons": [], "risks": ["Conflicting structure — stand aside"],
                },
            )
        elif _d == auth_dir:
            # Mirror the authoritative final decision so the favored side is identical.
            ready_dir = is_actionable(result["verdict"])
            block.update(
                ready=bool(ready_dir),
                label="READY" if ready_dir else "WAIT",
                score=0 if blockers else result["strict_score"],
                reason=result.get("strict_reason") or block.get("reason"),
                edge_score=result["edge_score"],
                edge_grade=result["edge_grade"],
                edge_breakdown=result["edge_breakdown"],
            )
        else:
            a_copy = dict(result)
            a_copy["confluences"]      = block.get("confluences") or {}
            a_copy["strict_direction"] = _d
            a_copy["strict_label"]     = "WAIT"
            a_copy["verdict"]          = "WAIT"
            eb_d = _analysis_edge_breakdown(a_copy)
            block.update(edge_score=eb_d["score"], edge_grade=eb_d["grade"], edge_breakdown=eb_d)
            if blockers:
                block.update(ready=False, label="WAIT", score=0)
        out_dirs[_d] = block
    result["directions"] = out_dirs

    # ── Market-session awareness (additive; single neutralising override) ────
    # MNQ/MGC share the CME/COMEX schedule. While the market is CLOSED there is no
    # live tape, so reporting WAIT (or counting "failed" evals) is misleading. We
    # always surface the closed state, the next open, and the last valid price/time;
    # and when closed we PAUSE the trade decision (no READY/ARMED, no edge/confirm/
    # session scoring presented as a live read). All keys still exist, so every
    # hard-indexed consumer is safe (single-return-path invariant).
    result["market_open"]   = market["open"]
    result["market_status"] = market["status"]
    result["next_open"]     = market["next_open_et"]
    result["next_open_utc"] = market["next_open"].isoformat() if market["next_open"] else None
    result["market_reason"] = market["reason"]
    _lv_price, _lv_ts, _lv_src = last_valid_data_for(active_ticker)
    result["last_valid_price"]  = _lv_price
    result["last_valid_time"]   = fmt_et(_lv_ts, "%b %-d, %-I:%M %p ET") if _lv_ts else None
    result["last_valid_source"] = _lv_src
    if not market["open"]:
        result["verdict"]         = "MARKET CLOSED"
        result["strict_label"]    = "MARKET CLOSED"
        result["strict_reason"]   = (
            "Market closed — live alerts paused"
            + (f". Next open: {market['next_open_et']}." if market["next_open_et"] else ".")
        )
        result["alert_level"]     = None
        result["conviction_tier"] = None
        result["edge_score"]      = 0
        result["edge_grade"]      = None
        # Neutralise the live diagnostics too — there is no live tape when closed,
        # so stale per-direction scores must not read as a tradeable signal.
        result["long_score"]         = None
        result["short_score"]        = None
        result["conflict_gap"]       = None
        result["dominant_direction"] = None
        result["ready_reason"]       = result["strict_reason"]
        result["rejected_reasons"]   = ["market_closed"]
        result["alert_diagnostics"] = {
            "long_score":            0,
            "short_score":           0,
            "edge_score":            0,
            "conflict_gap":          0,
            "dominant_direction":    "Neutral",
            "current_atr":           None,
            "volatility_multiplier": None,
            "ready_reason":          "",
            "rejected_reasons":      ["Market closed — live alerts paused"],
            "current_score":         0,
            "required_score":        int(cfg("EDGE_READY_THRESHOLD")),
        }
        result["decision_support"]   = _decision_support(result)
        for _d in ("Long", "Short"):
            _blk = result["directions"].get(_d)
            if _blk:
                _blk.update(ready=False, label="WAIT")
    return result


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

    embed = _build_trade_card_embed(entry, f"Journal Entry #{entry['id']}")

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


def _build_trade_card_embed(entry, footer_text):
    """Build the clean trade-card embed shared by the journal channel and the
    live alert channel. `entry` is the dict produced by _build_card_entry()."""
    direction_emoji = "📈" if entry["direction"] == "Long" else "📉"
    # Strength label is driven by the Edge Score (Possible 75-89 / Strong 90-94 / A+ 95-100),
    # falling back to the strict gate label for legacy entries without one.
    strength        = entry.get("trade_strength") or entry.get("strict_label", "Possible Trade")
    strength_disp   = _strength_display(strength)
    if strength == "A+ Setup":
        color = 0xFF4500
    elif strength == "Strong Trade":
        color = 0x2ECC71
    else:
        color = 0xF1C40F

    def _val(v):
        return str(v) if v not in (None, "") else "—"

    def _lvl(v):
        try:
            return f"{float(v):.2f}"
        except (TypeError, ValueError):
            return "—"

    def _typed(t, lvl):
        lvl_str = _lvl(lvl)
        if t and lvl_str != "—":
            return f"{t} @ {lvl_str}"
        return _val(t) if t else lvl_str

    score = entry.get("strict_score")
    conf_score = f"{score}/100" if score not in (None, "") else _val(entry.get("confidence"))
    why_text = entry.get("why_qualifies") or entry.get("why") or "—"
    if len(why_text) > 1000:
        why_text = why_text[:1000] + "…"

    notes_text = entry.get("setup_notes") or "—"
    if len(notes_text) > 1000:
        notes_text = notes_text[:1000] + "…"

    # Session focus + next-step lines for the card (Feature: Session Focus).
    if entry.get("session_preferred"):
        session_line = (f"✅ Preferred Trading Window: YES "
                        f"({entry.get('session_window', '—')})\n"
                        f"Session Bonus: +{entry.get('session_bonus', SESSION_BONUS_POINTS)}")
    else:
        session_line = "❌ Preferred Trading Window: NO\nSession Bonus: 0"
    next_step = entry.get("next_step") or entry.get("stage_next_step") or "—"

    vol      = entry.get("volatility") or {}
    vol_disp = vol.get("display") if vol.get("status") == "ok" else "—"

    # Display-only Edge Score / Grade / Reasons / Risk block replaces the free-text
    # AI Analysis field on READY cards; fall back to notes when no breakdown exists.
    eb = entry.get("edge_breakdown")
    if eb and (eb.get("reasons") or eb.get("risks") or eb.get("score")):
        analysis_field = _render_edge_block_field(eb)
    else:
        analysis_field = {"name": "🤖 AI Analysis", "value": notes_text, "inline": False}

    tier = entry.get("conviction_tier")
    tier_disp = f"  ·  Tier: **{tier}**" if tier else ""

    # ── Decision-support header (all real-derived; never fabricated) ──────────
    ds = entry.get("decision_support") or _decision_support(entry)
    ds_field = {
        "name": "🧭 Decision Support",
        "value": (
            f"⭐ **Quality:** {ds['quality']}\n"
            f"🎯 **Probability:** {ds['probability']}%\n"
            f"⚠️ **Risk:** {ds['risk']}\n"
            f"💰 **Reward:** {ds['reward']}\n"
            f"🪟 **Trade Window:** {ds['trade_window']}\n"
            f"✅ **Recommendation:** {ds['recommendation']}"
        ),
        "inline": False,
    }
    # ── Per-direction gate diagnostics (the SAME numbers that drove the gate) ──
    diag_field = {
        "name": "🔬 Diagnostics",
        "value": (
            f"Long {_val(entry.get('long_score'))} · Short {_val(entry.get('short_score'))} · "
            f"Gap {_val(entry.get('conflict_gap'))} · Dominant {_val(entry.get('dominant_direction'))}\n"
            f"Edge {_val(entry.get('edge_score'))} · ATR {_val(entry.get('current_atr'))} · "
            f"Vol× {_val(entry.get('volatility_multiplier'))}"
        ),
        "inline": False,
    }
    embed = {
        "author":      {"name": f"{BOT_NAME} · {strength_disp} Detected"},
        "title":       f"📓 {entry['symbol']} {direction_emoji} {entry['direction']}",
        "description": f"**{strength_disp}**  ·  Verdict: **{entry['verdict']}**{tier_disp}",
        "color":       color,
        "timestamp":   entry["datetime"],
        "fields": [
            ds_field,
            diag_field,
            {"name": "📊 Instrument",          "value": _val(entry.get("symbol")),    "inline": True},
            {"name": "🕐 Time",                "value": fmt_et(entry["datetime"], "%Y-%m-%d %H:%M:%S ET"), "inline": True},
            {"name": "🧭 Direction",           "value": f"{direction_emoji} {entry['direction']}", "inline": True},
            {"name": "🏗️ BOS Type",            "value": _typed(entry.get("bos_type"), entry.get("bos_level")), "inline": True},
            {"name": "🔀 CHOCH Type",          "value": _typed(entry.get("choch_type"), entry.get("choch_level")), "inline": True},
            {"name": "📐 Entry",               "value": _val(entry.get("entry_zone")), "inline": True},
            {"name": "🛑 Stop",                "value": _val(entry.get("stop_loss")),  "inline": True},
            {"name": "🎯 TP1",                 "value": _val(entry.get("target1")),    "inline": True},
            {"name": "🎯 TP2",                 "value": _val(entry.get("target2")),    "inline": True},
            {"name": "💯 Confidence Score",    "value": conf_score,                   "inline": True},
            {"name": "📈 VWAP",                "value": _val(entry.get("vwap_position")), "inline": True},
            {"name": "🧱 Zone",                "value": _val(entry.get("supply_demand_zone")), "inline": True},
            {"name": "📉 Volatility",          "value": _val(vol_disp),              "inline": True},
            {"name": "🗓️ Session",            "value": session_line,                "inline": False},
            analysis_field,
            {"name": "📝 Setup Notes",         "value": notes_text,                  "inline": False},
            {"name": "💬 Why the Trade Qualifies", "value": why_text,                 "inline": False},
            {"name": "➡️ Next Step",           "value": next_step,                   "inline": False},
        ],
        "footer": {"text": footer_text},
    }

    # ── Additive: 📋 Trade Management block (Feature 1). Built defensively so a
    # missing plan simply omits the field and never breaks the existing card. ──
    try:
        mgmt_lines = []
        if entry.get("target3") not in (None, ""):
            mgmt_lines.append(f"🎯 TP3: {entry['target3']}")
        if entry.get("be_level") not in (None, ""):
            mgmt_lines.append(f"🟰 Move stop to BE: {entry['be_level']}")
        if entry.get("partial_level") not in (None, ""):
            mgmt_lines.append(f"💰 Partial: {entry['partial_level']}")
        if entry.get("runner_target") not in (None, ""):
            mgmt_lines.append(f"🏃 Runner: {entry['runner_target']}")
        rp, rwp, rrn = entry.get("risk_points"), entry.get("reward_points"), entry.get("rr_num")
        if rp is not None and rwp is not None:
            rr_txt = f" · R:R {rrn}R" if rrn is not None else ""
            mgmt_lines.append(f"⚖️ Risk: {rp} pts · Reward: {rwp} pts{rr_txt}")
        if entry.get("atr_pts") is not None:
            mgmt_lines.append(
                f"📏 ATR Stop: {entry.get('atr_pts')} pts × {entry.get('atr_multiplier')} "
                f"→ {entry.get('stop_distance_ticks')} ticks "
                f"(${entry.get('risk_dollars_per_contract')}/contract)")
        if entry.get("max_invalidation"):
            mgmt_lines.append(f"🚫 Invalidation: {entry['max_invalidation']}")
        if mgmt_lines:
            embed["fields"].append({"name": "📋 Trade Management",
                                    "value": "\n".join(mgmt_lines)[:1024],
                                    "inline": False})
    except Exception as exc:
        logger.error("Trade-management card block error: %s", exc)

    # Attach the chart screenshot when a validated public URL is present.
    shot = entry.get("screenshot_url")
    if shot:
        embed["image"] = {"url": shot}

    return embed


def send_live_ready_card(entry, ticker="", notify=False):
    """Post the clean trade-card to the LIVE alert channel when a setup is READY.
    Routed per-instrument via _discord_url() (MNQ → MNQ channel, else default).

    notify=True prepends DISCORD_ALERT_MENTION (e.g. @everyone) so the message
    pings phones set to "Only @mentions". Used ONLY for the instant first post of
    a fresh setup; the 5-min re-post loop calls with notify=False so a standing
    READY setup buzzes the phone once, not every interval."""
    url = _discord_url(ticker or entry.get("symbol", ""))
    if not url:
        logger.warning("DISCORD_WEBHOOK_URL not set — live ready card skipped")
        return
    footer = f"Live Signal · {entry.get('symbol') or ticker or '—'}"
    # Record the send time per-instrument so the periodic loop throttles against it
    # (prevents an instant card and a periodic card landing seconds apart).
    LAST_LIVE_CARD_AT[instrument_of(ticker or entry.get("symbol", ""))] = datetime.now(timezone.utc)
    # Additive: record the READY send time per-instrument for the Diagnostics
    # readyAlertTime / waitedForCandleClose fields (display-only; never gates).
    LAST_READY_SENT_AT[instrument_of(ticker or entry.get("symbol", ""))] = datetime.now(timezone.utc)
    # Build + post inside the guard so a render failure (e.g. an unforeseen new
    # card field) can never raise into the caller — the alert path stays fail-open.
    embed = None
    try:
        embed = _build_trade_card_embed(entry, footer)
        payload = {"embeds": [embed]}
        if notify and DISCORD_ALERT_MENTION:
            # The single phone-pinging message: prepend the mention and allow it
            # so a "Only @mentions" device buzzes exactly on a fresh READY setup.
            payload["content"] = DISCORD_ALERT_MENTION
            payload["allowed_mentions"] = {"parse": ["everyone", "users", "roles"]}
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code not in (200, 204):
            logger.warning("Live ready card post failed: %s %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.error("Live ready card build/post error: %s", exc)
    # Optionally mirror 🔥 A+ setups to a dedicated channel (additive — the card
    # already posted to the normal channel above, so this never blocks/relocates
    # Possible/Strong/A+ alerts). Skipped entirely when no A+ channel is set.
    try:
        if (embed is not None and entry.get("trade_strength") == "A+ Setup"
                and DISCORD_APLUS_WEBHOOK_URL and DISCORD_APLUS_WEBHOOK_URL != url):
            requests.post(DISCORD_APLUS_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
    except Exception as exc:
        logger.error("A+ channel mirror error: %s", exc)
    # ── Additive: register the setup with the trade-management watcher and store
    # the latest READY snapshot (for /why). Both fail-open so a hiccup here can
    # never block the alert that already posted above. ──
    try:
        _register_managed_trade(entry, ticker)
    except Exception as exc:
        logger.error("Managed-trade register error: %s", exc)
    try:
        LAST_READY_BY_TICKER[instrument_of(ticker or entry.get("symbol", ""))] = entry
    except Exception as exc:
        logger.error("LAST_READY snapshot error: %s", exc)


# ── Tiered WATCH/ARMED early alerts (SCALP) ───────────────────────────────────
# Per-instrument throttle state: the last tier level we dispatched/observed and
# when the last WATCH/ARMED post went out. A level fires once on TRANSITION (the
# level changed) and then at most once per WATCH_ARMED_COOLDOWN_SEC while it
# persists, so a standing WATCH/ARMED never spams on every webhook.
LAST_TIER_LEVEL = {}   # instrument -> last alert_level seen ("WATCH"/"ARMED"/"READY")
LAST_TIER_AT    = {}   # instrument -> datetime (UTC) of last WATCH/ARMED post attempt

# ── EARLY pre-READY alert runtime state (additive, display-only) ──────────────
LAST_EARLY_ANCHOR  = {}  # (instrument, direction) -> structure-anchor ISO already EARLY-alerted (per-setup dedupe)
LAST_EARLY_AT      = {}  # (instrument, direction) -> datetime (UTC) of last EARLY post (cooldown guard)
EARLY_EVENT_TIMES  = {}  # instrument -> latest event/alert timestamps (ISO) for the Diagnostics page
LAST_READY_SENT_AT = {}  # instrument -> datetime (UTC) the READY card last fired (Diagnostics readyAlertTime)


def _tiered_alert_url(inst):
    """Resolve the Discord channel for WATCH/ARMED early alerts. DEFAULT routes to
    the journal channel so the main signal channel stays READY-only; switchable via
    TIERED_ALERT_CHANNEL = journal (default) | main | none (none disables posting).
    Falls back to the instrument's main channel if the journal URL is unset."""
    channel = os.environ.get("TIERED_ALERT_CHANNEL", "journal").strip().lower()
    if channel == "none":
        return None
    if channel == "main":
        return _discord_url(inst)
    return DISCORD_JOURNAL_WEBHOOK_URL or _discord_url(inst)


def _build_tiered_embed(a, inst, level):
    """Compact WATCH/ARMED early-alert embed. DISPLAY-ONLY — it announces that price
    has reached a fresh trade-side zone (WATCH) and is gathering confirmations
    (ARMED) BEFORE a full READY, so the strict trade card stays the single READY
    signal. Everything shown is read from the analysis `a` (never fabricated)."""
    cand   = a.get("gate_candidate") or "—"
    gd     = a.get("gate_debug") or {}
    price  = a.get("current_price")
    zone   = a.get("nearest_demand") if cand == "Long" else a.get("nearest_supply")
    confs  = int(gd.get("confirmations_passed") or 0)
    needed = int(gd.get("confirmations_needed") or 0)
    edge   = a.get("edge_score", 0)
    grade  = a.get("edge_grade", "")
    side_word = "demand" if cand == "Long" else "supply"
    title_emoji, color = {
        "WATCH":           ("👀 WATCH", 0xF1C40F),
        "ARMED":           ("🎯 ARMED", 0xE67E22),
        "WATCH FOR ENTRY": ("⚡ WATCH FOR ENTRY", 0x3498DB),
    }.get(level, ("👀 WATCH", 0xF1C40F))
    # What still blocks READY (the gate's own failed conditions), conflict aside.
    missing = [m for m in (a.get("strict_missing") or []) if m != "conflicting_structure"]
    fields = [
        {"name": "Setup",         "value": f"{cand} · at {side_word} zone", "inline": True},
        {"name": "Edge",          "value": f"{edge}/100{(' · ' + grade) if grade else ''}", "inline": True},
        {"name": "Confirmations", "value": f"{confs}/{needed}", "inline": True},
    ]
    if price is not None:
        fields.append({"name": "Price", "value": f"{price:,.2f}", "inline": True})
    if zone:
        fields.append({"name": f"{side_word.title()} zone", "value": f"{zone:,.2f}", "inline": True})
    if missing:
        fields.append({"name": "Still needs", "value": ", ".join(missing[:4]), "inline": False})
    # Per-direction gate diagnostics (real numbers; display-only) so the early
    # alert carries the same transparency as the READY card.
    _dv = lambda v: v if v not in (None, "") else "—"
    fields.append({
        "name": "Diagnostics",
        "value": (f"L {_dv(a.get('long_score'))} · S {_dv(a.get('short_score'))} · "
                  f"Gap {_dv(a.get('conflict_gap'))} · Edge {edge} · "
                  f"ATR {_dv(a.get('current_atr'))} · Vol× {_dv(a.get('volatility_multiplier'))}"),
        "inline": False,
    })
    tail = {
        "WATCH FOR ENTRY": "confirmations in — waiting on one decisive trigger (BOS/CHOCH/rejection candle/VWAP) to go READY.",
        "ARMED":           "gathering confirmations.",
    }.get(level, "watching for confirmation.")
    return {
        "title":       f"{title_emoji} · {inst}",
        "description": f"Price is at a fresh {side_word} zone — {tail}",
        "color":       color,
        "fields":      fields,
        "footer":      {"text": f"Early Signal · {inst}"},
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    }


def _post_tiered_embed(url, embed, inst, level):
    """Slow-task worker body: POST a prebuilt WATCH/ARMED embed to Discord. Runs OFF
    the webhook worker (via _enqueue_slow) so a slow or timing-out Discord call can
    never delay the decision, the READY card, the journal enqueue, or the next
    webhook evaluation. Best-effort — logs and swallows any failure."""
    try:
        resp = requests.post(url, json={"embeds": [embed]}, timeout=10)
        if resp.status_code not in (200, 204):
            logger.warning("Tiered %s alert post failed (%s): %s %s",
                           level, inst, resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.error("Tiered %s alert post error (%s): %s", level, inst, exc)


def _maybe_send_tiered_alert(a, record):
    """Fire a WATCH/ARMED early alert (SCALP only, ENABLE_TIERED_ALERTS) for the
    analysis `a`. Throttled per instrument: a level posts on transition (changed
    from the last level) OR after WATCH_ARMED_COOLDOWN_SEC while it persists.
    READY is NOT posted here (the live card owns it) but is recorded so a later
    retreat to ARMED/WATCH counts as a fresh transition.

    The throttle state and embed are computed synchronously, but the Discord POST
    is OFFLOADED to the slow-task worker, so this never blocks the webhook worker.
    Returns True iff a WATCH/ARMED embed was enqueued for posting. Fail-open: never
    raises into the webhook tail."""
    if not cfg("ENABLE_TIERED_ALERTS"):
        return False
    level = a.get("alert_level")
    inst  = instrument_of(record.get("ticker") or record.get("instrument")
                          or a.get("active_ticker") or "")
    if not inst:
        return False

    # READY: record (so a later ARMED/WATCH is a transition) but let the live card post it.
    if level == "READY":
        LAST_TIER_LEVEL[inst] = "READY"
        return False
    # No tier: forget the standing level so the next early alert fires immediately.
    if level not in ("WATCH", "ARMED", "WATCH FOR ENTRY"):
        LAST_TIER_LEVEL.pop(inst, None)
        LAST_TIER_AT.pop(inst, None)
        return False

    now           = datetime.now(timezone.utc)
    last_at       = LAST_TIER_AT.get(inst)
    cooldown      = int(cfg("WATCH_ARMED_COOLDOWN_SEC"))
    is_transition = (level != LAST_TIER_LEVEL.get(inst))
    cooled_down   = (last_at is None or (now - last_at).total_seconds() >= cooldown)
    if not (is_transition or cooled_down):
        return False

    # Record the attempt up-front (level + time) so a transient post failure can't
    # turn into a per-webhook retry storm; the next attempt waits for the cooldown.
    LAST_TIER_LEVEL[inst] = level
    LAST_TIER_AT[inst]    = now
    url = _tiered_alert_url(inst)
    if not url:
        return False
    # Build the embed synchronously (fast, in-memory) but hand the Discord POST to
    # the slow-task worker so a slow/timing-out call can't block this worker.
    try:
        embed = _build_tiered_embed(a, inst, level)
    except Exception as exc:
        logger.error("Tiered alert build error (%s/%s): %s", inst, level, exc)
        return False
    _enqueue_slow(lambda u=url, e=embed, i=inst, lv=level: _post_tiered_embed(u, e, i, lv))
    return True


# ── EARLY intrabar pre-READY alerts (additive, DISPLAY-ONLY) ──────────────────
def _early_latest_ts(alert_history, alert_type, inst, ticker_scoped, cutoff):
    """Most recent UTC timestamp of `alert_type` for `inst` at/after `cutoff`, using
    the SAME instrument-scoping as evaluate_strict_setup._latest_ts (un-prefixed
    CHOCH/BOS/HH.. are scoped by the instrument resolved at ingestion; sweep types
    embed the instrument in their name). None if absent. Read-only."""
    latest = None
    for a in alert_history:
        if a.get("alert_type") != alert_type:
            continue
        if not ticker_scoped:
            a_inst = (a.get("instrument")
                      or _instrument_from_text(a.get("ticker"))
                      or _instrument_from_text(a.get("alert_type")))
            if a_inst != inst:
                continue
        try:
            ts = datetime.fromisoformat(a["timestamp"])
        except (KeyError, ValueError):
            continue
        if ts < cutoff:
            continue
        if latest is None or ts > latest:
            latest = ts
    return latest


def _early_event_times(inst, alert_history):
    """Scan ALERT_HISTORY (within EARLY_WINDOW_MIN) for the EARLY building blocks of
    each direction: a liquidity sweep + a structure shift. 'displacement' maps to
    impulsive structure (BOS / HH-HL-LH-LL) and 'choch' to CHOCH; 'structure' is
    the latest of the two. event_start is the first of (sweep, structure). All
    times are server-ingestion timestamps (no bar time is in the payload). Pure /
    read-only — never feeds the gate."""
    cutoff = now_utc() - timedelta(minutes=EARLY_WINDOW_MIN)
    def lt(t, scoped=False):
        return _early_latest_ts(alert_history, t, inst, scoped, cutoff)
    def _max(*xs):
        xs = [x for x in xs if x]
        return max(xs) if xs else None
    def _min(*xs):
        xs = [x for x in xs if x]
        return min(xs) if xs else None
    bear_sweep   = lt(f"{inst} BEARISH SWEEP", True)
    choch_sup    = lt("CHOCH SUPPLY")
    short_disp   = _max(lt("BOS SUPPLY"), lt("LH"), lt("LL"))
    short_struct = _max(choch_sup, short_disp)
    bull_sweep   = lt(f"{inst} BULLISH SWEEP", True)
    choch_dem    = lt("CHOCH DEMAND")
    long_disp    = _max(lt("BOS DEMAND"), lt("HH"), lt("HL"))
    long_struct  = _max(choch_dem, long_disp)
    return {
        "Short": {"sweep": bear_sweep, "choch": choch_sup, "displacement": short_disp,
                  "structure": short_struct, "event_start": _min(bear_sweep, short_struct)},
        "Long":  {"sweep": bull_sweep, "choch": choch_dem, "displacement": long_disp,
                  "structure": long_struct, "event_start": _min(bull_sweep, long_struct)},
    }


def _early_alert_url(inst):
    """Resolve the Discord channel for EARLY alerts. EARLY_ALERT_CHANNEL =
    main (default, the live signal channel) | journal | none (disables)."""
    if EARLY_ALERT_CHANNEL == "none":
        return None
    if EARLY_ALERT_CHANNEL == "journal":
        return DISCORD_JOURNAL_WEBHOOK_URL or _discord_url(inst)
    return _discord_url(inst)


def _build_early_embed(a, inst, direction, t):
    """Compact ⚡ EARLY LONG/SHORT embed. DISPLAY-ONLY — announces an early,
    UNCONFIRMED entry (sweep + structure shift) that fired before the candle close,
    with the confirmed READY card still to follow. Everything is read from the
    analysis `a` / event times `t`, never fabricated."""
    price     = a.get("current_price")
    side_word = "supply" if direction == "Short" else "demand"
    zone      = a.get("nearest_supply") if direction == "Short" else a.get("nearest_demand")
    bias_word = "bearish" if direction == "Short" else "bullish"
    title, color = (("⚡ EARLY SHORT", 0xE74C3C) if direction == "Short"
                    else ("⚡ EARLY LONG", 0x2ECC71))
    def et(ts):
        return fmt_et(ts, "%H:%M:%S ET") if ts else "—"
    fields = [
        {"name": "Trigger", "value": f"Liquidity sweep + {bias_word} structure (CHOCH/displacement)", "inline": False},
    ]
    if price is not None:
        fields.append({"name": "Price", "value": f"{price:,.2f}", "inline": True})
    if zone:
        fields.append({"name": f"{side_word.title()} zone", "value": f"{zone:,.2f}", "inline": True})
    fields.append({"name": "Sweep",     "value": et(t.get("sweep")),     "inline": True})
    fields.append({"name": "Structure", "value": et(t.get("structure")), "inline": True})
    # Per-direction gate diagnostics (real numbers; display-only).
    _dv = lambda v: v if v not in (None, "") else "—"
    fields.append({
        "name": "Diagnostics",
        "value": (f"L {_dv(a.get('long_score'))} · S {_dv(a.get('short_score'))} · "
                  f"Gap {_dv(a.get('conflict_gap'))} · Edge {_dv(a.get('edge_score'))} · "
                  f"ATR {_dv(a.get('current_atr'))} · Vol× {_dv(a.get('volatility_multiplier'))}"),
        "inline": False,
    })
    return {
        "title":       f"{title} · {inst}",
        "description": "Early, *unconfirmed* entry — fired before the candle close. "
                       "The confirmed READY card follows if it holds.",
        "color":       color,
        "fields":      fields,
        "footer":      {"text": f"Early Signal · {inst} · not a confirmed READY"},
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    }


def _post_early_embed(url, embed, inst, direction, ping):
    """Slow-task worker body: POST a prebuilt EARLY embed to Discord, OFF the
    webhook worker (via _enqueue_slow) so a slow/timing-out call can never delay the
    decision, the READY card, or the next webhook. Best-effort — swallows failures."""
    try:
        payload = {"embeds": [embed]}
        if ping and DISCORD_ALERT_MENTION:
            payload["content"] = DISCORD_ALERT_MENTION
            payload["allowed_mentions"] = {"parse": ["everyone", "users", "roles"]}
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code not in (200, 204):
            logger.warning("EARLY %s alert post failed (%s): %s %s",
                           direction, inst, resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.error("EARLY %s alert post error (%s): %s", direction, inst, exc)


def _store_early_times(inst, direction, times):
    """Persist the latest EARLY event timestamps (ISO) per instrument for the
    Diagnostics page. Preserves earlyAlertTime (set only when EARLY actually fires)."""
    if not inst:
        return
    slot = EARLY_EVENT_TIMES.setdefault(inst, {})
    if direction is None:
        slot["direction"]        = None
        slot["eventStartTime"]   = None
        slot["sweepTime"]        = None
        slot["chochTime"]        = None
        slot["displacementTime"] = None
        return
    t = times[direction]
    def iso(x):
        return x.isoformat() if x else None
    slot["direction"]        = direction
    slot["eventStartTime"]   = iso(t.get("event_start"))
    slot["sweepTime"]        = iso(t.get("sweep"))
    slot["chochTime"]        = iso(t.get("choch"))
    slot["displacementTime"] = iso(t.get("displacement"))


def _maybe_dispatch_early_alert(a, record):
    """Fire an ⚡ EARLY LONG/SHORT the instant a sweep + structure shift appear
    together, BEFORE the strict candle-close confirmation. PURELY ADDITIVE and
    fail-open: never touches evaluate_strict_setup, the READY verdict/score, SWING
    parity, the journal, or managed trades. Fires ONCE per active setup (per-
    direction dedupe that resets when the setup goes inactive). Skipped when the
    verdict is already READY (the confirmed card owns it), the zone is broken, or a
    trade is active. Returns True iff an EARLY embed was enqueued. Refreshes
    EARLY_EVENT_TIMES (for the Diagnostics page) on every call."""
    if not EARLY_ALERTS_ENABLED:
        return False
    if (a or {}).get("market_open") is False:
        return False   # market closed — EARLY heads-up is paused with the gate
    inst = instrument_of(record.get("ticker") or record.get("instrument")
                         or a.get("active_ticker") or "")
    if not inst:
        return False
    times = _early_event_times(inst, ALERT_HISTORY)
    active = [d for d in ("Short", "Long")
              if times[d]["sweep"] and times[d]["structure"]]
    chosen = active[0] if len(active) == 1 else None  # ambiguous (both) → stand aside
    _store_early_times(inst, chosen, times)
    if not active:
        # Setup fully inactive → reset per-setup dedupe so the next clean setup
        # re-fires, and clear the stale early-alert stamp for the Diagnostics row.
        LAST_EARLY_ANCHOR.pop((inst, "Short"), None)
        LAST_EARLY_ANCHOR.pop((inst, "Long"), None)
        EARLY_EVENT_TIMES.get(inst, {}).pop("earlyAlertTime", None)
        return False
    if chosen is None:
        # Ambiguous (both sides active) → stand aside, but PRESERVE the dedupe
        # anchors: a side that already EARLY-fired must not be re-armed and re-fire
        # when the ambiguity later resolves back to that same side in-window.
        return False
    key = (inst, chosen)
    # An actionable verdict (full READY *or* EARLY READY) already owns the signal —
    # never post the intrabar ⚡EARLY teaser at/after it; mark dedupe so a late EARLY
    # can't fire after the verdict card for this setup.
    if is_actionable(a.get("verdict")):
        LAST_EARLY_ANCHOR[key] = "ready"
        return False
    # Honour the same hard invalidations the gate uses (display-side only).
    if a.get("zone_broken_active") or ACTIVE_TRADE:
        return False
    if LAST_EARLY_ANCHOR.get(key):     # already EARLY-alerted this active setup
        return False
    now = datetime.now(timezone.utc)
    last_at = LAST_EARLY_AT.get(key)
    if last_at and (now - last_at).total_seconds() < EARLY_ALERT_COOLDOWN_SEC:
        return False
    url = _early_alert_url(inst)
    if not url:
        return False
    # Build the embed BEFORE committing any dedupe / diagnostics state, so a build
    # failure can't leave the setup stuck-deduped or falsely stamp earlyAlertTime.
    try:
        embed = _build_early_embed(a, inst, chosen, times[chosen])
    except Exception as exc:
        logger.error("EARLY alert build error (%s/%s): %s", inst, chosen, exc)
        return False
    anchor = times[chosen]["structure"]
    LAST_EARLY_ANCHOR[key] = anchor.isoformat() if anchor else now.isoformat()
    LAST_EARLY_AT[key]     = now
    EARLY_EVENT_TIMES.setdefault(inst, {})["earlyAlertTime"] = now.isoformat()
    _enqueue_slow(lambda u=url, e=embed, i=inst, d=chosen, p=EARLY_ALERT_PING:
                  _post_early_embed(u, e, i, d, p))
    logger.info("EARLY %s alert enqueued (%s) — sweep+structure before confirmation", chosen, inst)
    return True


def _trade_ready_loop():
    """Every TRADE_READY_INTERVAL seconds, re-evaluate each instrument and re-post
    the clean trade-card to the live alert channel while a setup stays READY.

    This is the recurring "update every 5 min" companion to the instant alert
    fired from the webhook. It posts only on READY verdicts and stays silent
    while a trade is already active (lifecycle alerts cover that case).
    """
    try:
        if not ACTIVE_TRADE:
            now = datetime.now(timezone.utc)
            for inst in ("MGC", "MNQ"):
                # Throttle: skip if a card (instant or periodic) was sent for this
                # instrument within the last TRADE_READY_INTERVAL seconds.
                last = LAST_LIVE_CARD_AT.get(inst)
                if last and (now - last).total_seconds() < TRADE_READY_INTERVAL:
                    continue
                try:
                    a = full_analysis(ticker_override=inst)
                except Exception as exc:
                    logger.error("trade-ready loop analysis error (%s): %s", inst, exc)
                    continue
                # Re-check ACTIVE_TRADE just before sending (it may have changed
                # while full_analysis ran).
                if not ACTIVE_TRADE and is_actionable(a.get("verdict")):
                    entry = _build_card_entry(a, ticker=f"{inst}1!")
                    send_live_ready_card(entry, inst)
    except Exception as exc:  # never let the loop die
        logger.warning("trade-ready loop error: %s", exc)
    finally:
        threading.Timer(TRADE_READY_INTERVAL, _trade_ready_loop).start()


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
            # Performance analytics: fire once per entry on a terminal outcome
            # (Win / Loss / Breakeven). Intermediate states (Pending / T1 Hit)
            # do not trigger. Deduped via entry["analytics_posted"].
            state = _outcome_state(new_outcome, entry.get("pnl_dollars"))
            if state in ("win", "loss", "breakeven") and not entry.get("analytics_posted"):
                entry["analytics_posted"] = True
                post_performance_stats(
                    reason=f"After {entry.get('symbol','—')} "
                           f"{entry.get('direction','—')} closed: {new_outcome}"
                )
            return entry
    return None


# ───────────────────────────────────────────────────────────────────────────
# Trade-management watcher (Features 1-3, fully additive).
#
# When a setup goes READY and its clean card posts, the setup is registered as a
# *managed trade* keyed by (instrument, direction, entry-zone low, UTC date). A
# background pass — piggybacking the VWAP auto-fetch loop — pulls the latest
# 1-minute bar (high/low/close) from the same public feed and walks the trade
# through its plan: TP1 → breakeven → partial → TP2 → TP3, or stop/invalidation.
# Each level fires a Discord update exactly once; the terminal exit posts an
# outcome card and writes Result / R / MFE / MAE back to the journal entry.
#
# This NEVER touches the manual ACTIVE_TRADE flow, the READY gate, throttling,
# or the existing cards. Every entry point is wrapped in try/except so a feed
# hiccup can never block an alert.
# ───────────────────────────────────────────────────────────────────────────

def _managed_trade_key(instrument, direction, entry_lo):
    """Dedup key — same setup on the same UTC day maps to one managed trade."""
    try:
        lo_key = round(float(entry_lo), 0)
    except (TypeError, ValueError):
        lo_key = 0.0
    return (instrument, direction, lo_key,
            datetime.now(timezone.utc).strftime("%Y-%m-%d"))


def _register_managed_trade(entry, ticker=""):
    """Register (or refresh) a READY setup with the trade-management watcher.

    Idempotent: a repost of the same setup refreshes the plan levels but keeps
    any events already sent and the running MFE/MAE. A closed trade is never
    re-opened by a later repost on the same key/day.
    """
    mp = entry.get("management_plan")
    if not mp:
        return None  # no numeric plan (e.g. legacy/no-plan entry) — nothing to track

    instrument = entry.get("instrument") or instrument_of(ticker or entry.get("symbol", ""))
    direction  = entry.get("direction") or mp.get("direction") or "Long"
    key = _managed_trade_key(instrument, direction, mp.get("entry_lo"))

    # Light housekeeping: drop closed trades from previous UTC days.
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for k in [k for k, v in MANAGED_TRADES_BY_KEY.items()
              if v.get("closed") and k[3] != today]:
        MANAGED_TRADES_BY_KEY.pop(k, None)

    existing = MANAGED_TRADES_BY_KEY.get(key)
    if existing is not None:
        if not existing.get("closed"):
            # Refresh plan numbers, preserve progress (events/MFE/MAE/be_active).
            existing.update({k: mp.get(k) for k in
                             ("entry", "entry_lo", "entry_hi", "stop", "tp1", "tp2",
                              "tp3", "be_level", "partial", "runner", "risk_points",
                              "point_value")})
        return existing

    mt = {
        "key": key, "instrument": instrument, "direction": direction,
        "symbol": entry.get("symbol") or instrument,
        "entry": mp.get("entry"), "entry_lo": mp.get("entry_lo"), "entry_hi": mp.get("entry_hi"),
        "stop": mp.get("stop"), "tp1": mp.get("tp1"), "tp2": mp.get("tp2"), "tp3": mp.get("tp3"),
        "be_level": mp.get("be_level"), "partial": mp.get("partial"), "runner": mp.get("runner"),
        "risk_points": mp.get("risk_points"), "point_value": mp.get("point_value"),
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "events_sent": set(), "updates": [],
        "mfe": 0.0, "mae": 0.0, "be_active": False, "closed": False,
        "trade_strength": entry.get("trade_strength"),
        "edge_score": entry.get("edge_score"),
        "journal_id": entry.get("id"),
    }
    MANAGED_TRADES_BY_KEY[key] = mt
    logger.info("Managed trade registered: %s %s entry≈%s", instrument, direction, mp.get("entry"))
    return mt


def _fetch_latest_bar(instrument):
    """Return {'high','low','close'} of the most recent 1-minute bar, or None.

    Uses the same public feed as the VWAP fetch (MGC≈GC=F, MNQ≈NQ=F). Best-effort
    — any error returns None so the watcher simply skips this cycle.
    """
    symbol = VWAP_FEED_SYMBOL.get(instrument)
    if not symbol:
        return None
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    try:
        resp = requests.get(url, params={"interval": "1m", "range": "1d"},
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        if resp.status_code != 200:
            return None
        result = resp.json()["chart"]["result"][0]
        quote  = result["indicators"]["quote"][0]
        highs, lows, closes = quote["high"], quote["low"], quote["close"]
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
        logger.warning("Latest-bar fetch failed for %s: %s", instrument, exc)
        return None
    for i in range(len(closes) - 1, -1, -1):
        if highs[i] is not None and lows[i] is not None and closes[i] is not None:
            return {"high": float(highs[i]), "low": float(lows[i]), "close": float(closes[i])}
    return None


def _watch_managed_trades():
    """Evaluate every open managed trade against the latest bar (one fetch per
    instrument per cycle). Called from the VWAP auto-fetch loop."""
    active = [mt for mt in MANAGED_TRADES_BY_KEY.values() if not mt.get("closed")]
    if not active:
        return
    bars = {}
    for inst in {mt["instrument"] for mt in active}:
        bars[inst] = _fetch_latest_bar(inst)
    for mt in active:
        bar = bars.get(mt["instrument"])
        if not bar:
            continue
        try:
            _evaluate_managed_trade_levels(mt, bar)
        except Exception as exc:
            logger.error("Managed-trade eval error (%s): %s", mt.get("key"), exc)


def _evaluate_managed_trade_levels(mt, bar):
    """Walk one managed trade through its plan using a single OHLC bar.

    Terminal precedence is conservative: the stop / breakeven exit is checked
    *before* targets so an ambiguous bar resolves against the trade. Each level
    fires its Discord update once (deduped via mt['events_sent'])."""
    if mt.get("closed"):
        return
    high, low = bar["high"], bar["low"]
    entry   = mt.get("entry")
    is_long = mt["direction"] == "Long"
    sent    = mt["events_sent"]

    # ── Running MFE / MAE (price points, never negative) ──
    if entry is not None:
        if is_long:
            fav, adv = high - entry, entry - low
        else:
            fav, adv = entry - low, high - entry
        mt["mfe"] = round(max(mt["mfe"], fav, 0.0), 4)
        mt["mae"] = round(max(mt["mae"], adv, 0.0), 4)

    # Effective stop: original until TP1, then breakeven (entry) afterwards.
    eff_stop = entry if (mt.get("be_active") and entry is not None) else mt.get("stop")

    # ── 1) Terminal stop / breakeven exit (checked first; conservative) ──
    if eff_stop is not None:
        stop_breached = (low <= eff_stop) if is_long else (high >= eff_stop)
        if stop_breached:
            if mt.get("be_active"):
                _close_managed_trade(mt, "Breakeven",
                                     "Breakeven (stop moved to BE after TP1)", eff_stop)
            else:
                _close_managed_trade(mt, "Loss", "Loss (stop hit)", eff_stop)
            return

    # ── 2) Targets in plan order ──
    def _reached(level):
        if level is None:
            return False
        return (high >= level) if is_long else (low <= level)

    if "TP1" not in sent and _reached(mt.get("tp1")):
        sent.add("TP1")
        mt["be_active"] = True
        _send_management_update(mt, "🎯 TP1 hit",
                                f"Target 1 reached at `{mt.get('tp1')}`. Stop moves to breakeven.")

    if "PARTIAL" not in sent and _reached(mt.get("partial")):
        sent.add("PARTIAL")
        _send_management_update(mt, "💰 Partial zone",
                                f"Partial profit zone reached at `{mt.get('partial')}`.")

    if "TP2" not in sent and _reached(mt.get("tp2")):
        sent.add("TP2")
        _send_management_update(mt, "🎯 TP2 hit",
                                f"Target 2 reached at `{mt.get('tp2')}`. Trail the runner toward TP3.")

    if "TP3" not in sent and _reached(mt.get("tp3")):
        sent.add("TP3")
        _close_managed_trade(mt, "Win", "Win (TP3 / runner target)", mt.get("tp3"))
        return


def _close_managed_trade(mt, outcome, result_label, exit_price):
    """Finalise a managed trade: compute R / MFE / MAE / PnL, post the outcome
    card, and write the result back to the linked journal entry."""
    if mt.get("closed"):
        return
    mt["closed"]       = True
    mt["outcome"]      = outcome
    mt["result_label"] = result_label
    mt["exit_price"]   = exit_price
    mt["closed_at"]    = datetime.now(timezone.utc).isoformat()

    entry   = mt.get("entry")
    risk    = mt.get("risk_points") or 0.0
    pv      = mt.get("point_value") or 0.0
    is_long = mt["direction"] == "Long"

    if entry is not None and exit_price is not None:
        pnl_points = (exit_price - entry) if is_long else (entry - exit_price)
    else:
        pnl_points = 0.0
    mt["pnl_points"]  = round(pnl_points, 4)
    mt["pnl_dollars"] = round(pnl_points * pv, 2)
    mt["r_multiple"]  = round(pnl_points / risk, 2) if risk else 0.0
    mt["mfe_r"]       = round(mt["mfe"] / risk, 2) if risk else 0.0
    mt["mae_r"]       = round(mt["mae"] / risk, 2) if risk else 0.0

    _send_outcome_update(mt)
    _apply_outcome_to_journal(mt)


def _send_management_update(mt, title, detail):
    """Record + post a single trade-management update to the instrument channel."""
    mt.setdefault("updates", []).append(f"{title} — {detail}")
    url = _discord_url(mt.get("symbol") or mt.get("instrument"))
    if not url:
        return
    content = (f"**{mt.get('instrument')} {mt.get('direction')} — Trade Management**\n"
               f"{title}\n{detail}")
    try:
        requests.post(url, json={"content": content[:1900]}, timeout=5)
    except Exception as exc:
        logger.error("Management update post error: %s", exc)


def _send_outcome_update(mt):
    """Post the terminal-outcome card to the instrument channel and journal."""
    o     = mt.get("outcome", "—")
    emoji = {"Win": "✅", "Loss": "❌", "Breakeven": "➖"}.get(o, "•")
    color = {"Win": 0x2ECC71, "Loss": 0xE74C3C, "Breakeven": 0xF1C40F}.get(o, 0x95A5A6)
    embed = {
        "title": f"{emoji} Trade Closed — {mt.get('result_label', o)}",
        "color": color,
        "fields": [
            {"name": "Instrument", "value": str(mt.get("instrument", "—")), "inline": True},
            {"name": "Direction",  "value": str(mt.get("direction", "—")),  "inline": True},
            {"name": "Result",     "value": str(o),                          "inline": True},
            {"name": "Entry",      "value": str(mt.get("entry", "—")),       "inline": True},
            {"name": "Exit",       "value": str(mt.get("exit_price", "—")),  "inline": True},
            {"name": "R Multiple", "value": f"{mt.get('r_multiple', 0)}R",   "inline": True},
            {"name": "MFE",        "value": f"{mt.get('mfe_r', 0)}R",        "inline": True},
            {"name": "MAE",        "value": f"{mt.get('mae_r', 0)}R",        "inline": True},
            {"name": "PnL (1 contract)", "value": f"${mt.get('pnl_dollars', 0):,.2f}", "inline": True},
        ],
        "footer": {"text": "Trade-management outcome · auto-tracked"},
    }
    updates = mt.get("updates") or []
    if updates:
        embed["fields"].append({"name": "Management Path",
                                "value": "\n".join(f"• {u}" for u in updates)[:1024],
                                "inline": False})
    for url in {_discord_url(mt.get("symbol") or mt.get("instrument")), DISCORD_JOURNAL_WEBHOOK_URL}:
        if not url:
            continue
        try:
            requests.post(url, json={"embeds": [embed]}, timeout=10)
        except Exception as exc:
            logger.error("Outcome update post error: %s", exc)


def _apply_outcome_to_journal(mt):
    """Write the management outcome onto its linked journal entry (additive keys),
    and set the entry's outcome/pnl so analytics + reports can count it. Never
    overwrites an outcome already decided by the manual flow."""
    jid    = mt.get("journal_id")
    target = None
    if jid is not None:
        for e in JOURNAL:
            if e.get("id") == jid:
                target = e
                break
    if target is None:  # fall back to the most-recent dedup match
        for e in JOURNAL:
            if (instrument_of(e.get("symbol", "")) == mt.get("instrument")
                    and e.get("direction") == mt.get("direction")):
                target = e
                break
    if target is None:
        return
    target["mgmt_outcome"]       = mt.get("outcome")
    target["mgmt_result_label"]  = mt.get("result_label")
    target["r_multiple"]         = mt.get("r_multiple")
    target["mfe_r"]              = mt.get("mfe_r")
    target["mae_r"]              = mt.get("mae_r")
    target["management_updates"] = list(mt.get("updates") or [])
    target["mgmt_closed_at"]     = mt.get("closed_at")
    # Only set the canonical outcome if the manual flow hasn't already decided it.
    if _outcome_state(target.get("outcome"), target.get("pnl_dollars")) is None:
        target["outcome"]     = mt.get("outcome")
        target["pnl_dollars"] = mt.get("pnl_dollars")
    logger.info("Journal #%s management outcome → %s (%sR)",
                target.get("id"), mt.get("outcome"), mt.get("r_multiple"))


# ---------------------------------------------------------------------------
# Additive upgrade helpers — AI notes, structure block, screenshot, analytics.
# All grounded in real full_analysis() data; none alter scoring or the webhook
# path. build_setup_notes/build_trade_thesis are isolated so they can later be
# swapped for an LLM without touching callers.
# ---------------------------------------------------------------------------

# Private/loopback host prefixes rejected for screenshot URLs (SSRF-safe; the
# URL is only ever handed to Discord, never fetched server-side).
_PRIVATE_HOST_RE = re.compile(
    r'^(localhost|127\.|0\.0\.0\.0|10\.|192\.168\.|169\.254\.|'
    r'172\.(1[6-9]|2[0-9]|3[0-1])\.)', re.I,
)

PERF_CATEGORIES = ("BOS Demand", "BOS Supply", "CHOCH Bullish", "CHOCH Bearish",
                   "VWAP Reclaim", "VWAP Rejection")


def extract_screenshot_url(record):
    """Return a public http(s) chart/screenshot URL from a webhook payload, or None.

    Accepts several common field names. Rejects non-http(s) schemes and
    private/loopback hosts. Never fetches the URL — only a validated public URL
    is passed through to Discord's embed image.
    """
    if not isinstance(record, dict):
        return None
    raw = (record.get("screenshot") or record.get("screenshot_url")
           or record.get("chart_url") or record.get("chart_image")
           or record.get("chart") or record.get("image"))
    if not raw:
        return None
    url = str(raw).strip()
    # Ignore the legacy placeholder string; cap length (Discord image URL limit).
    if not url or url.startswith("[") or len(url) > 2048:
        return None
    try:
        p = urlparse(url)
    except Exception:
        return None
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    if _PRIVATE_HOST_RE.match(p.hostname):
        return None
    return url


def _fmt_lvl(v):
    """Format a numeric price level as a 2-dp string, or None if not numeric."""
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return None


def build_structure_fields(a, direction):
    """Return (bos_status, choch_status, vwap_position, supply_demand_zone) strings
    grounded in the full_analysis() result `a`. VWAP side is derived by comparing
    current price to the VWAP value (vwap_status is freshness, not direction)."""
    lpt = a.get("last_price_by_type") or {}
    if direction == "Long":
        bos_lvl   = _fmt_lvl(lpt.get("BOS DEMAND"))
        choch_lvl = _fmt_lvl(lpt.get("CHOCH DEMAND"))
        zone_lvl  = _fmt_lvl(a.get("nearest_demand"))
        bos_status   = f"BOS Demand @ {bos_lvl}" if bos_lvl else "None"
        choch_status = f"Bullish CHOCH @ {choch_lvl}" if choch_lvl else "None"
        zone_word    = "Demand"
    else:
        bos_lvl   = _fmt_lvl(lpt.get("BOS SUPPLY"))
        choch_lvl = _fmt_lvl(lpt.get("CHOCH SUPPLY"))
        zone_lvl  = _fmt_lvl(a.get("nearest_supply"))
        bos_status   = f"BOS Supply @ {bos_lvl}" if bos_lvl else "None"
        choch_status = f"Bearish CHOCH @ {choch_lvl}" if choch_lvl else "None"
        zone_word    = "Supply"

    # _fmt_lvl validates numerics; float() on its output is then always safe.
    vval_f  = _fmt_lvl(a.get("vwap_value"))
    price_f = _fmt_lvl(a.get("current_price"))
    if vval_f and price_f:
        side = "Above" if float(price_f) >= float(vval_f) else "Below"
        vwap_position = f"{side} VWAP ({vval_f})"
    else:
        vwap_position = "—"

    if a.get("zone_broken_active"):
        sd_zone = f"{zone_word} zone broken"
    elif a.get("zone_mitigated_near"):
        sd_zone = f"{zone_word} zone consumed"
    elif zone_lvl:
        sd_zone = f"{zone_word} intact @ {zone_lvl}"
    else:
        # No nearest zone level on this side — do NOT claim "intact". That string
        # both mislabels the Zone display and trips the Edge Score's zone_active
        # check, fabricating a "Demand/Supply Zone Active" credit with no zone.
        sd_zone = "—"

    return bos_status, choch_status, vwap_position, sd_zone


def build_setup_notes(a, entry):
    """Deterministic, fact-grounded "AI Analysis" for a setup. Every line is built
    from the real confluences in `a`/`entry`, so it can never hallucinate. Kept in
    one place so it can be swapped for an LLM later without touching callers."""
    direction = entry.get("direction", "Long")
    conf         = a.get("confluences") or {}
    bos_status   = entry.get("bos_status")   or "None"
    choch_status = entry.get("choch_status") or "None"
    vwap_pos     = str(entry.get("vwap_position") or "").lower()
    t1           = entry.get("target1")
    # Each note line is gated on a REAL confluence so the notes can never invent a
    # signal the setup didn't actually have.
    has_sweep    = bool(conf.get("liquidity_sweep") or conf.get("sweep") or a.get("liquidity_sweep"))
    zone_mitig   = bool(conf.get("zone_mitigated"))
    zone_intact  = not (a.get("zone_broken_active") or a.get("zone_mitigated_near"))
    has_struct   = (choch_status != "None") or (bos_status != "None")
    lines = []
    if direction == "Long":
        if has_sweep:
            lines.append("Price swept liquidity below recent lows.")
        if zone_mitig:
            lines.append("Demand zone was mitigated and respected.")
        else:
            zone_lvl = _fmt_lvl(a.get("nearest_demand"))
            if zone_lvl:
                lines.append(f"Price reacted at demand near {zone_lvl} and is holding bid.")
        if has_struct:
            lines.append("Bullish CHOCH/BOS supports upside structure.")
        if vwap_pos.startswith("above"):
            lines.append("VWAP confluence supports continuation.")
        if zone_intact and not zone_mitig and zone_lvl:
            lines.append("Demand zone remains intact.")
        lines.append(f"Looking for continuation toward {t1}."
                     if t1 not in (None, "") else
                     "Looking for continuation toward next resistance.")
    else:
        if has_sweep:
            lines.append("Price swept liquidity above recent highs.")
        if zone_mitig:
            lines.append("Supply zone was mitigated and respected.")
        else:
            zone_lvl = _fmt_lvl(a.get("nearest_supply"))
            if zone_lvl:
                lines.append(f"Price rejected supply near {zone_lvl}.")
        if has_struct:
            lines.append("Bearish CHOCH/BOS supports downside structure.")
        if vwap_pos.startswith("below"):
            lines.append("VWAP confluence supports continuation.")
        if zone_intact and not zone_mitig and zone_lvl:
            lines.append("Supply zone remains respected.")
        lines.append(f"Looking for continuation toward {t1}."
                     if t1 not in (None, "") else
                     "Looking for continuation toward next support.")
    if not lines:
        lines.append(entry.get("why_qualifies") or "Setup conditions met.")
    return "\n".join(lines)[:1000]


def build_trade_thesis(a, entry):
    """One-line trade thesis grounded in the setup direction and target."""
    direction = entry.get("direction", "Long")
    sym       = entry.get("symbol", "—")
    t1        = entry.get("target1")
    tgt       = f", targeting {t1}" if t1 not in (None, "") else ""
    if direction == "Long":
        return f"Long {sym}: demand reclaim with bullish CHOCH above VWAP{tgt}."[:240]
    return f"Short {sym}: supply rejection with bearish CHOCH below VWAP{tgt}."[:240]


def classify_setup_categories(entry):
    """Tag a setup with performance categories from its structure + VWAP position."""
    cats = []
    direction    = entry.get("direction", "Long")
    bos_status   = str(entry.get("bos_status") or "")
    choch_status = str(entry.get("choch_status") or "")
    vwap_pos     = str(entry.get("vwap_position") or "").lower()
    if direction == "Long":
        if bos_status not in ("", "None"):   cats.append("BOS Demand")
        if choch_status not in ("", "None"): cats.append("CHOCH Bullish")
        if vwap_pos.startswith("above"):     cats.append("VWAP Reclaim")
    else:
        if bos_status not in ("", "None"):   cats.append("BOS Supply")
        if choch_status not in ("", "None"): cats.append("CHOCH Bearish")
        if vwap_pos.startswith("below"):     cats.append("VWAP Rejection")
    return cats


def _grade_for_score(score):
    """Map a 0-100 Edge Score to a Trade Grade band.

    Bands (Edge Score → Grade): 95-100 A+, 90-94 A, 85-89 B, 80-84 C, below 80
    WAIT. The grade mirrors the READY gate's Edge ≥ 80 threshold — a READY setup
    always scores ≥ 80 (grade C or better); anything below 80 reads "WAIT"."""
    s = score or 0
    if s >= 95: return "A+"
    if s >= 90: return "A"
    if s >= 85: return "B"
    if s >= 80: return "C"
    # A valid READY in a looser mode (SCALP, actionable floor 35 / full 50) is still
    # a real trade — grade it C rather than the "WAIT" floor so the card's grade never
    # contradicts its READY/EARLY READY verdict. SWING (threshold 80) is unaffected:
    # s>=80 already returned above.
    if s >= cfg("EDGE_READY_THRESHOLD"): return "C"
    return "WAIT"


# ── Trade-strength classification (display / journal / analytics) ──────────────
# A READY setup (the strict gate passed) is sub-classified PURELY by its Edge
# Score: 75-89 → Possible Trade, 90-94 → Strong Trade, 95-100 → A+ Setup. This never affects the
# READY gate itself (evaluate_strict_setup); it only labels an already-READY
# trade. A score below 75 is not a READY strength and returns None.
def _trade_strength_from_score(score):
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    if s >= 95:
        return "A+ Setup"
    if s >= 90:
        return "Strong Trade"
    if s >= 75:
        return "Possible Trade"
    return None


def _score_tier(score):
    """Conviction tier from the Edge Score (display-only) — the user-facing band
    naming. HIGH CONVICTION >= 70, READY 50-69, EARLY READY 35-49, else None.
    Distinct from the operational alert_level ladder (WATCH/ARMED/WATCH FOR ENTRY/
    READY): a tier names the STRENGTH of an Edge Score, not the dispatch state, and
    never gates a trade. The bands line up with the SCALP gate floors (actionable 35,
    full 50); in SWING (threshold 80) a READY always lands in HIGH CONVICTION."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    if s >= 70:
        return "HIGH CONVICTION"
    if s >= 50:
        return "READY"
    if s >= 35:
        return "EARLY READY"
    return None


def _decision_support(a):
    """Decision-support header for the live trade card + dashboard gauge. EVERY
    field is derived from REAL analysis data — nothing is fabricated. `a` may be a
    full_analysis result OR a card-entry dict (both carry edge_score,
    conviction_tier, verdict, a volatility dict, a preferred-session flag and a
    trade plan), so it reads everything defensively with .get().

      quality        ← conviction tier: HIGH CONVICTION→HIGH, READY→MODERATE,
                       EARLY READY→SPECULATIVE, else LOW
      probability    ← the Edge Score (the SAME number shown as EdgeScore)
      risk           ← volatility regime: Normal→Low, Caution→Moderate,
                       Block→High, unavailable→Unknown
      reward         ← trade-plan T2 R:R: >=3 Excellent / >=2 Good / >=1.5 Fair / else Poor
      trade_window   ← preferred-session flag
      recommendation ← tier + verdict: full READY enter / EARLY READY aggressive-
                       enter-or-wait / else stand aside
    """
    a = a or {}
    edge    = int(a.get("edge_score") or 0)
    tier    = a.get("conviction_tier") or _score_tier(edge)
    verdict = a.get("verdict") or ""
    vol     = a.get("volatility") or {}
    regime  = (vol.get("regime") or "NA").upper()

    quality = {"HIGH CONVICTION": "HIGH", "READY": "MODERATE",
               "EARLY READY": "SPECULATIVE"}.get(tier, "LOW")

    if vol.get("status") != "ok":
        risk = "Unknown"
    elif vol.get("blocked"):
        risk = "High"
    elif vol.get("caution"):
        risk = "Moderate"
    elif regime == "NORMAL":
        risk = "Low"
    else:
        risk = "Moderate"

    rr = a.get("rr_num")
    if rr is None:
        rr = (a.get("trade_plan") or {}).get("rr_num")
    try:
        rr_f = float(rr)
    except (TypeError, ValueError):
        rr_f = None
    if rr_f is None:
        reward = "—"
    elif rr_f >= 3:
        reward = f"Excellent ({rr_f:.1f}R)"
    elif rr_f >= 2:
        reward = f"Good ({rr_f:.1f}R)"
    elif rr_f >= 1.5:
        reward = f"Fair ({rr_f:.1f}R)"
    else:
        reward = f"Poor ({rr_f:.1f}R)"

    pref = a.get("session_preferred")
    if pref is None:
        pref = (a.get("session") or {}).get("preferred")
    trade_window = "Preferred window" if pref else "Off-hours"

    if is_full_ready(verdict):
        recommendation = f"ENTER {ready_direction(verdict).upper()} — full conviction setup."
    elif is_early_ready(verdict):
        recommendation = (f"EARLY {ready_direction(verdict).upper()} — enter aggressive at "
                          "reduced size, or wait for the confirmation candle.")
    else:
        recommendation = "STAND ASIDE — no qualified setup."

    return {
        "quality":        quality,
        "probability":    edge,
        "risk":           risk,
        "reward":         reward,
        "trade_window":   trade_window,
        "recommendation": recommendation,
    }


def _strength_display(strength):
    """Bold, color-coded Discord label for a trade strength."""
    return {"A+ Setup":       "🔥 A+ SETUP",
            "Strong Trade":   "🟢 STRONG TRADE",
            "Possible Trade": "🟡 POSSIBLE TRADE"}.get(strength, "🟡 POSSIBLE TRADE")


def _edge_score_for_entry(entry):
    """Authoritative Edge Score for a stored journal entry. Prefers the
    transparent breakdown score, then the stored edge_score, then the strict
    gate score, then the legacy edge score — so manual/legacy entries still
    rank in recap and analytics."""
    eb = entry.get("edge_breakdown") or {}
    for v in (eb.get("score"), entry.get("edge_score"),
              entry.get("strict_score"), entry.get("legacy_edge_score")):
        try:
            if v is not None:
                return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


def _display_edge_score(entry):
    """Edge Score for DISPLAY ONLY (recaps / weekly reports). Returns the
    transparent score — the breakdown score, or a stored edge_score that is
    known-transparent (new-format entries also carry edge_grade/edge_breakdown).
    Legacy/manual entries that only have a bias-derived edge_score return "—" so
    the legacy number is never surfaced. Use _edge_score_for_entry (not this) for
    internal ranking, where a legacy fallback is acceptable to order old entries."""
    eb = entry.get("edge_breakdown") or {}
    if eb.get("score") is not None:
        return eb["score"]
    if entry.get("edge_grade") is not None and entry.get("edge_score") is not None:
        return entry["edge_score"]
    return "—"


def _entry_trade_strength(entry):
    """Trade strength for a stored entry: the explicit field if present, else
    derived from its Edge Score (only when ≥75, i.e. an actual READY trade)."""
    st = entry.get("trade_strength")
    if st in ("Possible Trade", "Strong Trade", "A+ Setup"):
        return st
    return _trade_strength_from_score(_edge_score_for_entry(entry))


def compute_edge_breakdown(a, entry):
    """Display Edge Score / Grade / Reasons / Risk for a setup.

    Computed from the SAME pure additive helper (compute_trade_edge_components)
    the READY gate uses, reading the confluences full_analysis() already produced,
    so the displayed Edge Score and the gate score can never diverge. Six additive
    components (max 100): zone mitigation+reaction (+25), VWAP (+20), structure
    (+20), liquidity sweep (+15), confirmation candle (+10), preferred session
    (+10). Risk lines are INFORMATIONAL warnings only — they do NOT reduce the
    score. Hard blockers (zone broken / consumed) force the score to 0.

    Returns {"score": int, "grade": str,
             "score_breakdown": [{"label": str, "points": int}],
             "risk_adjustments": [{"label": str, "points": None}],
             "reasons": [str], "risks": [str]}.  (reasons/risks kept for
    backward compatibility — they mirror the breakdown/risk labels.)
    """
    direction = entry.get("direction", "Long")
    is_long   = direction != "Short"
    conf      = a.get("confluences") or {}
    sess      = a.get("session") or get_session_state()

    # The six additive components come straight from the confluences the READY
    # gate already evaluated, scored by the SAME helper the gate uses, so the
    # displayed Edge Score equals the gate score. `zone_mitigated` carries the
    # zone-VALID signal (mitigation + same-direction reaction).
    has_sweep = bool(conf.get("liquidity_sweep") or conf.get("sweep") or a.get("liquidity_sweep"))
    signals = {
        "zone_valid":          bool(conf.get("zone_mitigated")),
        "vwap_confirmed":      bool(conf.get("vwap")),
        "structure_confirmed": bool(conf.get("structure_confirmed")),
        "liquidity_sweep":     has_sweep,
        "confirmation_candle": bool(conf.get("confirmation_candle")),
        "preferred_session":   bool(sess.get("preferred")),
    }
    vol_adj = int((a.get("volatility") or {}).get("score_adj") or 0)
    score, raw_breakdown = compute_trade_edge_components(signals, vol_adj)

    # Direction-aware display labels for the generic component names.
    relabel = {
        "Zone Mitigated":         "Demand Zone Reaction"  if is_long else "Supply Zone Reaction",
        "VWAP Confirmation":      "VWAP Reclaim"          if is_long else "VWAP Rejection",
        "Structure Confirmation": "Bullish Structure"     if is_long else "Bearish Structure",
        "Volatility":             "Calm Market" if vol_adj > 0 else "Extreme Volatility",
    }
    breakdown = [{"label": relabel.get(it["label"], it["label"]), "points": it["points"]}
                 for it in raw_breakdown]

    # ── Risk lines — INFORMATIONAL warnings only; they do NOT reduce the Edge
    #    Score (the score is the pure additive sum above). Shown as bullet flags. ─
    risk_adj = []
    def _risk(label):
        risk_adj.append({"label": label, "points": None})

    risk_label = str(a.get("risk_label") or "")
    price = a.get("current_price")
    try:
        near_pct = float(cfg("NEAR_PCT"))
    except Exception:
        near_pct = 0.004
    near_opposite = False
    try:
        if price is not None:
            price = float(price)
            if is_long:
                sup = a.get("nearest_supply")
                near_opposite = sup is not None and 0 <= (float(sup) - price) <= price * near_pct * 1.5
            else:
                dem = a.get("nearest_demand")
                near_opposite = dem is not None and 0 <= (price - float(dem)) <= price * near_pct * 1.5
    except (TypeError, ValueError):
        near_opposite = False

    if is_long and (near_opposite or risk_label in ("Testing Supply", "Approaching Supply")):
        _risk("Nearby Resistance")
    if not is_long and (near_opposite or risk_label in ("Testing Demand", "Approaching Demand")):
        _risk("Nearby Support")
    if a.get("overextended") or risk_label == "Overextended":
        _risk("Overextended from level")
    if risk_label == "Choppy":
        _risk("Choppy conditions")
    # Volatility regime flags the card with an informational risk line. In SCALP a
    # BLOCK regime (Wild/Dead) now reaches here too (it no longer gates) — flag it.
    vol = a.get("volatility") or {}
    if vol.get("status") == "ok":
        _vreg = vol.get("regime")
        if _vreg == "HIGH_CAUTION":
            _risk("Elevated volatility")
        elif _vreg == "QUIET_CAUTION":
            _risk("Thin / quiet volatility")
        elif _vreg == "HIGH_BLOCK":
            _risk("Extreme volatility")
        elif _vreg == "QUIET_BLOCK":
            _risk("Dead / illiquid market")

    # Dedup by label, preserving first occurrence.
    def _dedup(items):
        seen, out = set(), []
        for it in items:
            if it["label"] in seen:
                continue
            seen.add(it["label"])
            out.append(it)
        return out
    breakdown = _dedup(breakdown)
    risk_adj  = _dedup(risk_adj)

    # Hard blockers override everything: a broken structure or a consumed
    # (mitigated-near) zone is not tradeable, so its Edge Score is 0 regardless of
    # any residual confluences still present in `a`. A single decisive risk line
    # explains the zero so the breakdown stays self-consistent.
    if a.get("zone_broken_active") or a.get("zone_mitigated_near"):
        blocker = ("Structure invalidated — zone broken"
                   if a.get("zone_broken_active") else "Zone consumed — invalid entry")
        breakdown = []
        risk_adj  = [{"label": blocker, "points": None}]
        score     = 0

    return {
        "score": score,
        "grade": _grade_for_score(score),
        "score_breakdown": breakdown,
        "risk_adjustments": risk_adj,
        # Backward-compatible plain-label lists (mirror the structured items).
        "reasons": [it["label"] for it in breakdown],
        "risks":   [it["label"] for it in risk_adj],
    }


def _analysis_edge_breakdown(a):
    """THE single Edge Score computation for a full_analysis() result `a`.

    Used by full_analysis (to attach the unified score) and reused by
    _build_card_entry, so the card, journal, /why, recaps and dashboard can never
    diverge. Direction follows the strict gate (or, for WAIT, the leading
    confluence direction) so the confluence-based signals line up with is_long
    inside compute_edge_breakdown."""
    conf = a.get("confluences") or {}
    tp   = a.get("trade_plan") or {}
    bias = a.get("bias")
    direction = (a.get("strict_direction") or conf.get("direction") or tp.get("direction")
                 or ("Long" if bias == "Bullish" else "Short" if bias == "Bearish" else "Long"))
    bos_s, choch_s, vwap_pos, sd_zone = build_structure_fields(a, direction)
    edge_entry = {
        "direction":          direction,
        "bos_status":         bos_s,
        "choch_status":       choch_s,
        "vwap_position":      vwap_pos,
        "supply_demand_zone": sd_zone,
        "strict_label":       a.get("strict_label"),
        "verdict":            a.get("verdict"),
    }
    return compute_edge_breakdown(a, edge_entry)


def _render_edge_block_field(eb):
    """Render the transparent Edge Score / Grade / Score Breakdown / Risk
    Adjustments block as one embed field, with per-item point values."""
    score     = eb.get("score", 0)
    grade     = eb.get("grade", "—")
    breakdown = eb.get("score_breakdown")
    risk_adj  = eb.get("risk_adjustments")
    # Fallback for legacy/plain-label breakdowns that carry no points.
    if breakdown is None:
        breakdown = [{"label": r, "points": None} for r in (eb.get("reasons") or [])]
    if risk_adj is None:
        risk_adj = [{"label": r, "points": None} for r in (eb.get("risks") or [])]

    def _line(it):
        pts = it.get("points")
        if pts is None:
            return f"• {it['label']}"
        return f"{'+' if pts >= 0 else ''}{pts} {it['label']}"

    lines  = [f"**Edge Score: {score} / 100**", f"**Grade: {grade}**", "", "**Score Breakdown:**"]
    lines += [_line(it) for it in breakdown] if breakdown else ["—"]
    lines += ["", "**Risk Adjustments:**"]
    lines += [_line(it) for it in risk_adj] if risk_adj else ["None"]
    return {"name": "⚡ Edge Score", "value": "\n".join(lines)[:1024], "inline": False}


def _outcome_state(outcome, pnl=None):
    """Map a journal outcome string (+optional pnl) to 'win'/'loss'/'breakeven'/None.

    'None' means still open (Pending / T1 Hit). A Win or Loss with ~zero realized
    P&L is reclassified as a breakeven (e.g. stop hit at break-even after T1)."""
    o  = str(outcome or "")
    ol = o.lower()
    if "breakeven" in ol or ol.strip() == "be" or ol.strip().startswith("be "):
        return "breakeven"
    if o.startswith("Win") or "win" in ol:
        st = "win"
    elif o.startswith("Loss") or "loss" in ol:
        st = "loss"
    else:
        return None
    try:
        if pnl is not None and abs(float(pnl)) < 1e-6:
            return "breakeven"
    except (TypeError, ValueError):
        pass
    return st


def _realized_r(entry, state):
    """Conservative realized-R proxy from the outcome (inferred, not exact)."""
    if state == "breakeven":
        return 0.0
    if state == "loss":
        return -1.0
    o = str(entry.get("outcome", "")).lower()
    try:
        rr = float(entry.get("rr"))
    except (TypeError, ValueError):
        rr = 2.0
    if "t1" in o and "t2" not in o:
        return 1.0
    return rr


def compute_performance_stats(entries=None):
    """Derive performance analytics from closed JOURNAL entries (in-memory only)."""
    entries = JOURNAL if entries is None else entries
    wins = losses = breakevens = 0
    gross_win = gross_loss = 0.0
    r_values = []
    cat = {c: {"win": 0, "loss": 0} for c in PERF_CATEGORIES}
    strength_keys = ("Possible Trade", "Strong Trade", "A+ Setup")
    strength = {k: {"win": 0, "loss": 0, "breakeven": 0,
                    "gross_win": 0.0, "gross_loss": 0.0, "r": []}
                for k in strength_keys}

    for e in entries:
        pnl   = e.get("pnl_dollars")
        state = _outcome_state(e.get("outcome"), pnl)
        if state is None:
            continue
        if state == "win":
            wins += 1
            if pnl is not None:
                gross_win += max(0.0, float(pnl))
        elif state == "loss":
            losses += 1
            if pnl is not None:
                gross_loss += abs(min(0.0, float(pnl)))
        else:
            breakevens += 1

        r_values.append(_realized_r(e, state))

        # Split by trade strength (Possible vs Strong).
        st = _entry_trade_strength(e)
        if st in strength:
            b = strength[st]
            if state == "win":
                b["win"] += 1
                if pnl is not None:
                    b["gross_win"] += max(0.0, float(pnl))
            elif state == "loss":
                b["loss"] += 1
                if pnl is not None:
                    b["gross_loss"] += abs(min(0.0, float(pnl)))
            else:
                b["breakeven"] += 1
            b["r"].append(_realized_r(e, state))

        if state in ("win", "loss"):
            for c in (e.get("setup_categories") or classify_setup_categories(e)):
                if c in cat:
                    cat[c][state] += 1

    decided = wins + losses
    win_rate = (wins / decided * 100.0) if decided else None
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None
    avg_r = (sum(r_values) / len(r_values)) if r_values else None

    by_strength = {}
    for k, b in strength.items():
        dec = b["win"] + b["loss"]
        by_strength[k] = {
            "wins": b["win"], "losses": b["loss"], "breakevens": b["breakeven"],
            "decided": dec, "closed": dec + b["breakeven"],
            "win_rate": (b["win"] / dec * 100.0) if dec else None,
            "avg_r": (sum(b["r"]) / len(b["r"])) if b["r"] else None,
            "gross_win": round(b["gross_win"], 2), "gross_loss": round(b["gross_loss"], 2),
            "profit_factor": (b["gross_win"] / b["gross_loss"]) if b["gross_loss"] > 0 else None,
        }

    return {
        "wins": wins, "losses": losses, "breakevens": breakevens,
        "decided": decided, "closed": wins + losses + breakevens,
        "win_rate": win_rate,
        "gross_win": round(gross_win, 2), "gross_loss": round(gross_loss, 2),
        "profit_factor": profit_factor, "avg_r": avg_r,
        "categories": cat,
        "by_strength": by_strength,
    }


def post_performance_stats(reason=""):
    """Compute and post the performance-analytics embed to the journal channel."""
    if not DISCORD_JOURNAL_WEBHOOK_URL:
        return
    s = compute_performance_stats()
    if s["closed"] == 0:
        return
    wr   = f"{s['win_rate']:.0f}%" if s["win_rate"] is not None else "—"
    if s["profit_factor"] is not None:
        pf = f"{s['profit_factor']:.2f}"
    else:
        pf = "∞" if s["gross_win"] > 0 else "—"
    avgr = f"{s['avg_r']:+.2f}R" if s["avg_r"] is not None else "—"

    cat_lines = []
    for c in PERF_CATEGORIES:
        w, l = s["categories"][c]["win"], s["categories"][c]["loss"]
        if w + l > 0:
            cat_lines.append(f"{c}: {w}W / {l}L ({w / (w + l) * 100:.0f}%)")
    cat_text = "\n".join(cat_lines) or "No categorized results yet."

    def _fmt_strength(k):
        b = s.get("by_strength", {}).get(k)
        if not b or b.get("closed", 0) == 0:
            return f"{k}: —"
        wr_b = f"{b['win_rate']:.0f}%" if b.get("win_rate") is not None else "—"
        ar_b = f"{b['avg_r']:+.2f}R" if b.get("avg_r") is not None else "—"
        if b.get("profit_factor") is not None:
            pf_b = f"{b['profit_factor']:.2f}"
        else:
            pf_b = "∞" if b.get("gross_win", 0) > 0 else "—"
        return (f"**{k}**: {b['wins']}W / {b['losses']}L / {b['breakevens']}BE\n"
                f"WR {wr_b} · Avg {ar_b} · PF {pf_b}")
    strength_text = "\n".join(_fmt_strength(k) for k in ("Possible Trade", "Strong Trade", "A+ Setup"))

    embed = {
        "color":  0x5865F2,
        "author": {"name": f"{BOT_NAME} · Performance"},
        "title":  "📈 Performance Analytics",
        "description": reason or "Updated after a closed trade.",
        "fields": [
            {"name": "Wins ✅",        "value": str(s["wins"]),       "inline": True},
            {"name": "Losses ❌",      "value": str(s["losses"]),     "inline": True},
            {"name": "Breakeven ⚖️",   "value": str(s["breakevens"]), "inline": True},
            {"name": "Win Rate",       "value": wr,                   "inline": True},
            {"name": "Profit Factor",  "value": pf,                   "inline": True},
            {"name": "Avg R",          "value": avgr,                 "inline": True},
            {"name": "🎯 By Trade Strength", "value": strength_text[:1024], "inline": False},
            {"name": "📊 By Setup Type", "value": cat_text[:1024],    "inline": False},
        ],
        "footer": {"text": "Session stats · in-memory, resets on restart"},
    }
    try:
        requests.post(DISCORD_JOURNAL_WEBHOOK_URL, json={"embeds": [embed]}, timeout=5)
        logger.info("Performance stats posted: %dW/%dL/%dBE",
                    s["wins"], s["losses"], s["breakevens"])
    except Exception as exc:
        logger.warning("Performance stats post failed: %s", exc)


def _build_card_entry(a, ticker=None, record=None):
    """Build the trade-card dict (single source of truth for both the journal
    entry and the live alert card) from a full_analysis() result `a`.

    `record` (incoming webhook) preserves the exact ticker (e.g. "MNQ1!");
    `ticker` is used by the periodic loop where no webhook record exists.
    """
    record     = record or {}
    tp         = a.get("trade_plan") or {}
    direction  = a.get("strict_direction") or tp.get("direction") or "Long"
    inst       = (tp.get("instrument") or a.get("active_ticker")
                  or record.get("instrument")
                  or instrument_of(record.get("ticker") or ticker or record.get("alert_type", "")))
    # Preserve exact ticker (e.g. "MNQ1!") when supplied, else the normalized symbol.
    symbol     = record.get("ticker") or ticker or inst
    entry_zone = tp.get("entry_zone")

    # BOS / CHOCH levels from the most recent same-side structure alerts.
    lpt = a.get("last_price_by_type") or {}
    if direction == "Long":
        bos_level, choch_level = lpt.get("BOS DEMAND"), lpt.get("CHOCH DEMAND")
        bos_type,  choch_type  = "Demand", "Bullish"
    else:
        bos_level, choch_level = lpt.get("BOS SUPPLY"), lpt.get("CHOCH SUPPLY")
        bos_type,  choch_type  = "Supply", "Bearish"

    # Additive structure block + screenshot (grounded in `a` / webhook record).
    with _timed("aiNotesMs"):
        bos_status, choch_status, vwap_position, sd_zone = build_structure_fields(a, direction)
    with _timed("screenshotMs"):
        screenshot_url = extract_screenshot_url(record)

    entry = {
        "datetime":         datetime.now(timezone.utc).isoformat(),
        "symbol":           symbol,
        "instrument":       inst,
        "direction":        direction,
        "setup_stage":      a.get("setup_stage", "Trade Ready"),
        "strict_label":     a.get("strict_label", "WAIT"),
        "strict_score":     a.get("strict_score", 0),
        "verdict":          a.get("verdict", "WAIT"),
        "entry_zone":       entry_zone,
        "stop_loss":        tp.get("stop_loss"),
        "target1":          tp.get("target1"),
        "target2":          tp.get("target2"),
        "rr":               tp.get("rr"),
        # ── Additive trade-management plan (Feature 1) ──
        "target3":          tp.get("target3"),
        "be_level":         tp.get("be_level"),
        "partial_level":    tp.get("partial_level"),
        "runner_target":    tp.get("runner_target"),
        "risk_points":      tp.get("risk_points"),
        "reward_points":    tp.get("reward_points"),
        "rr_num":           tp.get("rr_num"),
        "max_invalidation": tp.get("max_invalidation"),
        "management_plan":  tp.get("management"),
        # ── Additive ATR dynamic-stop metadata (single source = the trade plan) ──
        "atr_pts":                   tp.get("atr_pts"),
        "atr_multiplier":            tp.get("atr_multiplier"),
        "atr_stop":                  tp.get("atr_stop"),
        "structure_stop":            tp.get("structure_stop"),
        "calculated_stop":           tp.get("calculated_stop"),
        "min_stop_ticks":            tp.get("min_stop_ticks"),
        "tick_size":                 tp.get("tick_size"),
        "stop_distance_ticks":       tp.get("stop_distance_ticks"),
        "risk_dollars_per_contract": tp.get("risk_dollars_per_contract"),
        "volatility_regime":         tp.get("volatility_regime"),
        "volatility_label":          tp.get("volatility_label"),
        "min_floor_applied":         tp.get("min_floor_applied"),
        "bos_type":         bos_type,
        "choch_type":       choch_type,
        "bos_level":        bos_level,
        "choch_level":      choch_level,
        "why_qualifies":    a.get("strict_reason") or a.get("why", "—"),
        "bias":             a.get("bias", "—"),
        "confidence":       f"{a.get('confidence', 0)}%",
        "edge_score":       a.get("edge_score", 0),
        "market_structure": a.get("structure_label", "—"),
        "risk_zone":        a.get("risk_label", "—"),
        "reasoning_chain":  a.get("reasoning_chain") or [],
        "why":              a.get("why", "—"),
        # ── Additive fields (do not remove; consumers use .get() defaults) ──
        "bos_status":         bos_status,
        "choch_status":       choch_status,
        "vwap_position":      vwap_position,
        "supply_demand_zone": sd_zone,
        "screenshot_url":     screenshot_url,
        "volatility":         a.get("volatility"),
    }
    entry["setup_categories"] = classify_setup_categories(entry)
    with _timed("aiNotesMs"):
        entry["setup_notes"]  = build_setup_notes(a, entry)
        entry["trade_thesis"] = build_trade_thesis(a, entry)
    # Session focus + next step, carried onto the card, /why and the journal so a
    # READY is always tagged with the window it fired in and what to do next.
    sess = a.get("session") or get_session_state()
    entry["session_preferred"] = bool(sess.get("preferred"))
    entry["session_bonus"]     = int(sess.get("bonus", 0))
    entry["session_window"]    = sess.get("window", "—")
    entry["next_step"]         = a.get("stage_next_step") or entry.get("why_qualifies") or "—"
    # Transparent Edge Score is the authoritative READY-trade score: it drives the
    # Possible/Strong strength label, the card/journal display, recap and analytics.
    # The legacy bias-derived edge score is preserved for backward compatibility.
    # Reuse the unified Edge Score computed once in full_analysis so the card,
    # journal, /why, recap and dashboard can never diverge; only recompute for a
    # bare/legacy `a` that never passed through full_analysis.
    eb = a.get("edge_breakdown") or compute_edge_breakdown(a, entry)
    entry["edge_breakdown"]    = eb
    entry["legacy_edge_score"] = a.get("legacy_edge_score", entry.get("edge_score", 0))
    entry["edge_score"]        = eb.get("score", entry.get("edge_score", 0))
    entry["edge_grade"]        = eb.get("grade")
    entry["score_breakdown"]   = eb.get("score_breakdown", [])
    entry["risk_adjustments"]  = eb.get("risk_adjustments", [])
    entry["trade_strength"]    = _trade_strength_from_score(entry["edge_score"]) or entry.get("strict_label")
    entry["a_plus"]            = (entry["trade_strength"] == "A+ Setup")
    # Conviction tier (score band) for the READY card label — reuse the value
    # full_analysis already computed; recompute only for a bare/legacy `a`.
    entry["conviction_tier"]   = a.get("conviction_tier") or _score_tier(entry["edge_score"])
    # Gate diagnostics + decision-support header — reuse the values full_analysis
    # already computed (recompute the header only for a bare/legacy `a`).
    entry["long_score"]            = a.get("long_score")
    entry["short_score"]           = a.get("short_score")
    entry["conflict_gap"]          = a.get("conflict_gap")
    entry["dominant_direction"]    = a.get("dominant_direction")
    entry["current_atr"]           = a.get("current_atr")
    entry["volatility_multiplier"] = a.get("volatility_multiplier")
    entry["ready_reason"]          = a.get("ready_reason") or a.get("strict_reason")
    entry["rejected_reasons"]      = a.get("rejected_reasons") or a.get("strict_missing")
    entry["alert_diagnostics"]     = a.get("alert_diagnostics")
    entry["decision_support"]      = a.get("decision_support") or _decision_support(entry)
    return entry


def _build_why_explanation(entry):
    """Render a plain-language explanation of WHY a setup qualifies (Feature 6).

    Works off a card-entry dict (the same shape stored in LAST_READY_BY_TICKER
    and built by _build_card_entry), so it has one consistent input shape. Reads
    everything with .get so a partial/legacy entry can never crash the endpoint.
    Surfaces: direction, edge, strength, the 4 passed gate conditions, bonus
    confluences, risks, invalidation, and concrete improvements toward A+."""
    direction = entry.get("direction", "—")
    edge      = entry.get("edge_score", 0)
    strength  = (entry.get("trade_strength")
                 or _trade_strength_from_score(edge)
                 or entry.get("strict_label", "—"))
    grade     = entry.get("edge_grade", "—")
    is_long   = direction == "Long"

    breakdown   = entry.get("score_breakdown") or []
    risks       = entry.get("risk_adjustments") or []
    passed      = [it.get("label") for it in breakdown if it.get("label")]
    risk_labels = [it.get("label") for it in risks if it.get("label")]

    gate_set = {
        "BOS Demand" if is_long else "BOS Supply",
        "Bullish CHOCH" if is_long else "Bearish CHOCH",
        "Confirmation Candle",
        "VWAP Reclaim" if is_long else "VWAP Rejection",
    }
    gate_passed = [p for p in passed if p in gate_set]
    confluences = [p for p in passed if p not in gate_set]

    # Concrete paths toward an A+ (95+) score: missing bonus confluences and any
    # active risk adjustments to resolve. Only shown when not already A+.
    improvements = []
    if edge < 95:
        possible_bonus = [
            "Liquidity Sweep",
            "Confirmed Zone Reaction",
            "Demand Zone Active" if is_long else "Supply Zone Active",
            "Bullish Trend" if is_long else "Bearish Trend",
            "High Confidence",
        ]
        improvements += [f"Add confluence: {b}" for b in possible_bonus if b not in passed]
        improvements += [f"Resolve risk: {r}" for r in risk_labels]

    return {
        "direction":        direction,
        "edge_score":       edge,
        "edge_grade":       grade,
        "trade_strength":   strength,
        "verdict":          entry.get("verdict", entry.get("strict_label", "—")),
        "thesis":           entry.get("trade_thesis", "—"),
        "why_qualifies":    entry.get("why_qualifies", entry.get("why", "—")),
        "setup_notes":      entry.get("setup_notes", "—"),
        "session_preferred": entry.get("session_preferred"),
        "session_bonus":     entry.get("session_bonus"),
        "session_window":    entry.get("session_window"),
        "next_step":         entry.get("next_step", entry.get("stage_next_step", "—")),
        "passed_conditions": gate_passed,
        "confluences":      confluences,
        "risks":            risk_labels,
        "entry_zone":       entry.get("entry_zone"),
        "stop_loss":        entry.get("stop_loss"),
        "targets":          [entry.get("target1"), entry.get("target2"), entry.get("target3")],
        "invalidation":     entry.get("max_invalidation") or entry.get("invalidation") or "—",
        "improvements":     improvements,
    }


def create_journal_entry(record, a, sizing, post_discord=True):
    """Auto-journal every Possible/Strong Trade recommendation, skipping duplicates.

    post_discord=False stores the entry in-memory only and skips the (slow)
    journal-channel Discord embed, so the webhook worker can send the trade alert
    first and offload that embed to the slow-task worker."""
    global JOURNAL, JOURNAL_KEYS

    strict_label = a.get("strict_label", "WAIT")
    if strict_label not in ("Strong Trade", "Possible Trade"):
        return None

    entry      = _build_card_entry(a, record=record)
    ticker     = entry["symbol"]
    direction  = entry["direction"]
    entry_zone = entry["entry_zone"]

    # Dedup key: instrument + direction + entry-zone low rounded to nearest integer.
    try:
        zone_key = round(float(str(entry_zone).split("–")[0]), 0) if entry_zone else 0.0
    except (TypeError, ValueError):
        zone_key = 0.0
    dedup_key = (instrument_of(ticker), direction, zone_key)

    if dedup_key in JOURNAL_KEYS:
        logger.info("Journal dedup skip: %s %s @ %.0f", ticker, direction, zone_key)
        return None

    with _timed("journalWriteMs"):
        JOURNAL_KEYS.add(dedup_key)

        entry["id"]      = len(JOURNAL) + 1
        entry["outcome"] = "Pending"
        # Chart screenshot: use a validated public URL when the alert carried one,
        # else keep the legacy placeholder string and continue (no failure).
        if entry.get("screenshot_url"):
            entry["screenshot"] = entry["screenshot_url"]
        else:
            entry["screenshot"] = "[ Screenshot placeholder — add URL or image link ]"
            logger.info("Screenshot unavailable for journal #%d — continuing", entry["id"])

        JOURNAL.insert(0, entry)
        if len(JOURNAL) > 500:
            JOURNAL.pop()

    if post_discord:
        send_journal_discord_embed(entry)
    logger.info("Journal entry #%d created: %s %s %s @ %s",
                entry["id"], ticker, strict_label, direction, entry_zone)
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
_DATA_ONLY_TYPES = {"MGC VWAP", "MNQ VWAP"}


def _handle_command_alert(normalized, data, parsed_price, resolved_inst):
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
            a  = full_analysis(current_price_override=parsed_price, ticker_override=resolved_inst)
            tp = a["trade_plan"]
            if not tp.get("trade_plan") or not tp.get("entry_zone"):
                return jsonify({"status": "error",
                                "reason": "No ready setup to enter — fill in Entry / Stop / T1 / T2, or wait for a valid Long Ready / Short Ready setup."}), 400
            try:
                if entry is None:
                    zone = str(tp["entry_zone"])
                    if "–" in zone:
                        lo_s, hi_s = zone.split("–")
                        entry = (float(lo_s) + float(hi_s)) / 2
                    else:
                        entry = float(zone)
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
        symbol    = resolved_inst

        # Record the trade for local tracking. _ENTER_LOCK serialises the
        # assignment so two concurrent ENTERs can't race on ACTIVE_TRADE.
        with _ENTER_LOCK:
            ACTIVE_TRADE = {
                "direction":   direction,
                "entry_price": entry,
                "stop_loss":   stop,
                "target1":     t1,
                "target2":     t2,
                "contracts":   contracts,
                "profile":     profile,
                "symbol":      symbol,
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

# ---------------------------------------------------------------------------
# Per-webhook gate diagnostics
# ---------------------------------------------------------------------------
# Every scored webhook produces a full PASS/FAIL breakdown of each READY gate so
# the operator can see EXACTLY why a setup is WAIT. Entries are kept in a ring
# buffer (viewable, owner-only, at /diagnostics) and mirrored best-effort to an
# on-disk log. Recording is fail-open — diagnostics must never crash the worker.
GATE_DIAGNOSTICS = deque(maxlen=400)
_DIAG_LOCK       = threading.Lock()
DIAG_LOG_PATH    = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "gate_diagnostics.log")
# Cap the on-disk mirror so a write-on-every-webhook file can never grow without
# bound on a long-running VM. When the live file exceeds the cap it is rotated to
# a single ".1" backup, bounding total disk use to ~2× the cap. The /diagnostics
# ring buffer above is the owner-facing surface; the file is a best-effort mirror.
DIAG_LOG_MAX_BYTES = 2 * 1024 * 1024   # 2 MB per file (≈ a few thousand entries)


def _record_diagnostic(text):
    try:
        GATE_DIAGNOSTICS.append(text)
    except Exception:
        pass
    try:
        with _DIAG_LOCK:
            try:
                if os.path.getsize(DIAG_LOG_PATH) >= DIAG_LOG_MAX_BYTES:
                    os.replace(DIAG_LOG_PATH, DIAG_LOG_PATH + ".1")
            except FileNotFoundError:
                pass
            with open(DIAG_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(text.rstrip("\n") + "\n\n")
    except Exception:
        pass


def _pf(flag):
    return "PASS" if flag else "FAIL"


def _vol_diag_summary(gd, vol):
    """One-line volatility status for the diagnostic header."""
    vol = vol or {}
    if gd.get("volatility_block"):
        return "BLOCK (holds setup at WAIT)"
    if vol.get("status") == "ok":
        return vol.get("decision") or "ok"
    return "unavailable (fail-open, no score effect)"


def _vol_diag_detail(vol):
    """Indented volatility fields for the diagnostic: currentATR, baselineATR,
    volatilityMultiplier, volatilityThreshold, volatilityDecision."""
    vol = vol or {}
    if vol.get("status") != "ok":
        return ["    (volatility reading unavailable - fail-open, no effect)"]

    def _n(x):
        try:
            return "%.2f" % float(x)
        except (TypeError, ValueError):
            return "—"
    adj = vol.get("score_adj", 0) or 0
    adj_str = ("+%d" % adj) if adj > 0 else ("%d" % adj)
    return [
        "    currentATR ........... %s pts" % _n(vol.get("atr_pts")),
        "    baselineATR .......... %s pts" % _n(vol.get("baseline_pts")),
        "    volatilityMultiplier . %sx" % _n(vol.get("ratio")),
        "    volatilityThreshold .. elevated >= %sx | extreme >= %sx" % (
            _n(vol.get("threshold_elevated")), _n(vol.get("threshold_extreme"))),
        "    volatilityDecision ... %s (Edge %s)" % (vol.get("label", "—"), adj_str),
    ]


def _stop_diag_lines(tp):
    """Human-readable ATR dynamic-stop breakdown for the owner-only /diagnostics
    text view. `tp` is a build_strict_trade_plan result dict; lines are emitted
    only when ATR stop metadata is present (additive / display-only — never used
    by the trade decision)."""
    tp = tp or {}

    def _n(x):
        if x is None:
            return "—"
        try:
            return "%g" % float(x)
        except (TypeError, ValueError):
            return str(x)
    return [
        "    currentATR ........... %s pts" % _n(tp.get("atr_pts")),
        "    atrMultiplier ........ %sx" % _n(tp.get("atr_multiplier")),
        "    nearestDemand ........ %s" % _n(tp.get("nearest_demand")),
        "    nearestSupply ........ %s" % _n(tp.get("nearest_supply")),
        "    structureStop ........ %s" % _n(tp.get("structure_stop")),
        "    atrStop .............. %s" % _n(tp.get("atr_stop")),
        "    minStopTicks ......... %s%s" % (
            _n(tp.get("min_stop_ticks")),
            " (FLOOR APPLIED)" if tp.get("min_floor_applied") else ""),
        "    finalStop ............ %s" % _n(tp.get("stop_loss")),
        "    stopDistanceTicks .... %s" % _n(tp.get("stop_distance_ticks")),
        "    riskPerContract ...... $%s" % _n(tp.get("risk_dollars_per_contract")),
        "    riskReward ........... %s" % _n(tp.get("rr")),
    ]


def format_gate_diagnostic(symbol, trigger, candidate, gd, verdict,
                           extra_blockers=None, vol=None):
    """Human-readable per-gate PASS/FAIL block for one scored webhook. `gd` is the
    candidate direction's gate_debug from evaluate_strict_setup (expanded above).
    Structure passes on ANY ONE of BOS/CHOCH/swing; the individual rows are shown
    for visibility while "Structure (any one)" + "Blocked by" reflect the real
    gate, so the breakdown never misreports the cause of a WAIT."""
    gd    = gd or {}
    side  = "Supply" if candidate == "Short" else "Demand"
    ready = is_actionable(verdict)
    cmp_  = "<" if candidate == "Short" else ">"
    fails = list(gd.get("failed_conditions") or [])
    for b in (extra_blockers or []):
        if b and b not in fails:
            fails.append(b)
    lines = [
        "──────── GATE DIAGNOSTIC ────────",
        "%s | %s | Trigger: %s" % (
            symbol, fmt_et(now_utc(), "%Y-%m-%d %H:%M:%S ET"), trigger),
        "Candidate direction: %s" % (candidate.upper() if candidate else "NONE"),
        "",
        "  BOS (%s) .............. %s" % (side, _pf(gd.get("bos"))),
        "  CHOCH (%s) ............ %s" % (side, _pf(gd.get("choch"))),
        "  Swing (HH/HL or LH/LL)  %s" % _pf(gd.get("swing")),
        "  Structure (any one) ... %s" % _pf(gd.get("structure_confirmed")),
        "  VWAP (price %s VWAP) ... %s" % (cmp_, _pf(gd.get("vwap_confirmed"))),
        "  %s zone present ...... %s" % (side, _pf(gd.get("zone_present"))),
        "  Zone Mitigated ........ %s" % _pf(gd.get("zone_mitigated")),
        "  Reaction (candle/sweep) %s" % _pf(gd.get("reaction")),
        "  Zone valid (mit+react)  %s" % _pf(gd.get("zone_valid")),
        "  Trading Session (bonus) %s" % _pf(gd.get("session_pref")),
        "  Conflict .............. %s" % ("YES" if gd.get("conflicting_structure") else "no"),
        "  Volatility ............ %s" % _vol_diag_summary(gd, vol),
    ]
    lines.extend(_vol_diag_detail(vol))
    lines.extend([
        "  Edge Score ............ %d / %d" % (gd.get("edge_score", 0), gd.get("ready_threshold", EDGE_READY_THRESHOLD)),
        "",
        "  FINAL READY DECISION: %s" % ("PASS" if ready else "FAIL"),
    ])
    if ready:
        lines.append("  Result: %s" % verdict)
    else:
        lines.append("  Result: WAIT")
        lines.append("  Blocked by: %s" % (", ".join(fails) if fails else "confluence"))
    lines.append("──────── END DIAGNOSTIC ─────────")
    return "\n".join(lines)


def _record_eval_metrics(a, webhook_received_at, eval_started_at, eval_finished_at,
                         eval_duration_ms, alert_sent_at, instrument, tiered_sent=False,
                         trigger="webhook", signal_type=None, is_duplicate=False,
                         signal_cooldown_remaining_ms=None):
    """Append one per-evaluation timing + volatility record to EVAL_METRICS (the
    live Diagnostics page / /eval-metrics read this). Best-effort — it must never
    raise into the worker. Phase timings come from the thread-local accumulator
    populated by the _timed() blocks during this evaluation; a phase that did not
    run for this alert (e.g. no journal write on a WAIT) is left None.

    Also records the gate breakdown (edge / zone / confirmations / blockers), the
    tiered alert_level, and the alert-dispatch + cooldown context so the live
    Diagnostics page can show exactly why a setup did or did not alert."""
    t   = _eval_timing_get() or {}
    vol = (a or {}).get("volatility") or {}
    gd  = (a or {}).get("gate_debug") or {}
    total_delay = None
    if webhook_received_at is not None and alert_sent_at is not None:
        total_delay = round((alert_sent_at - webhook_received_at).total_seconds() * 1000.0, 3)

    # ── Alert dispatch + cooldown context (best-effort, for diagnostics) ─────────
    alert_level = (a or {}).get("alert_level")
    verdict     = (a or {}).get("verdict") or ""
    inst_key    = instrument_of(instrument) if instrument else None
    alert_sent  = bool(alert_sent_at) or bool(tiered_sent)
    # Which throttle governs this level: READY re-posts on TRADE_READY_INTERVAL vs
    # LAST_LIVE_CARD_AT; the early tiers (WATCH / ARMED / WATCH FOR ENTRY) on
    # WATCH_ARMED_COOLDOWN_SEC vs LAST_TIER_AT.
    last_alert_at, cooldown_ms = None, None
    if alert_level == "READY":
        last_alert_at = LAST_LIVE_CARD_AT.get(inst_key) if inst_key else None
        cooldown_ms   = TRADE_READY_INTERVAL * 1000
    elif alert_level in ("WATCH", "ARMED", "WATCH FOR ENTRY"):
        last_alert_at = LAST_TIER_AT.get(inst_key) if inst_key else None
        cooldown_ms   = int(cfg("WATCH_ARMED_COOLDOWN_SEC")) * 1000
    cooldown_remaining_ms = None
    if last_alert_at is not None and cooldown_ms is not None:
        elapsed_ms = (datetime.now(timezone.utc) - last_alert_at).total_seconds() * 1000.0
        cooldown_remaining_ms = round(max(0.0, cooldown_ms - elapsed_ms), 1)
    suppressed = bool(alert_level and not alert_sent
                      and cooldown_remaining_ms is not None and cooldown_remaining_ms > 0)
    blockers = ", ".join(gd.get("failed_conditions") or [])
    if not blockers:
        blockers = "READY" if is_actionable(verdict) else "-"

    # ── EARLY pre-READY timing (additive diagnostics; never affects the gate) ────
    et_slot         = dict(EARLY_EVENT_TIMES.get(inst_key) or {}) if inst_key else {}
    early_at_iso    = et_slot.get("earlyAlertTime")
    event_start_iso = et_slot.get("eventStartTime")
    event_start_dt  = None
    if event_start_iso:
        try:
            event_start_dt = datetime.fromisoformat(event_start_iso)
        except (ValueError, TypeError):
            event_start_dt = None
    # LAST_READY_SENT_AT is per-instrument and persists across setups. Attribute a
    # READY send to THIS event row ONLY when the current verdict is READY, or the
    # recorded READY happened at/after this event's start — otherwise it's a stale
    # READY from an earlier setup and must not pair with a fresh event.
    ready_dt         = LAST_READY_SENT_AT.get(inst_key) if inst_key else None
    ready_is_current = bool(ready_dt) and (
        is_actionable(verdict)
        or (event_start_dt is not None and ready_dt >= event_start_dt))
    ready_at_iso    = ready_dt.isoformat() if ready_is_current else None
    first_alert_iso = early_at_iso or ready_at_iso   # EARLY if it fired, else READY
    alert_delay_seconds = None
    try:
        if event_start_iso and first_alert_iso:
            delay = (datetime.fromisoformat(first_alert_iso)
                     - datetime.fromisoformat(event_start_iso)).total_seconds()
            if delay >= 0:                       # guard against stale/negative pairs
                alert_delay_seconds = round(delay, 1)
    except (ValueError, TypeError):
        alert_delay_seconds = None
    # False when EARLY caught it first; True when the current verdict is the
    # candle-close READY with no EARLY preceding it; None otherwise (incl. later
    # WAIT re-evaluations after a prior READY, so a stale READY can't read as True).
    if early_at_iso:
        waited_for_candle_close = False
    elif is_actionable(verdict):
        waited_for_candle_close = True
    else:
        waited_for_candle_close = None

    # ── Direction / setup-type / decision + processing-latency aliases (additive,
    # for the per-row latency view). direction prefers the verdict side, then the
    # gate candidate; setupType is the inbound alert_type when known, else the
    # structural stage; totalLatencyMs is webhook->finished (full processing
    # latency) and falls back to the in-eval duration for heartbeat rows. ──
    _candidate = gd.get("candidate") or (a or {}).get("gate_candidate")
    if verdict.startswith("LONG"):
        direction = "Long"
    elif verdict.startswith("SHORT"):
        direction = "Short"
    elif _candidate in ("Long", "Short"):
        direction = _candidate
    else:
        direction = None
    setup_type = signal_type or (a or {}).get("setup_stage") or (a or {}).get("structure_class")
    _eval_tp = (a or {}).get("trade_plan") or {}
    if not isinstance(_eval_tp, dict):
        _eval_tp = {}
    if webhook_received_at is not None and eval_finished_at is not None:
        total_latency_ms = round((eval_finished_at - webhook_received_at).total_seconds() * 1000.0, 3)
    else:
        total_latency_ms = eval_duration_ms

    record = {
        "webhookReceivedAt":    webhook_received_at.isoformat() if webhook_received_at else None,
        "evaluationStartedAt":  eval_started_at.isoformat() if eval_started_at else None,
        "evaluationFinishedAt": eval_finished_at.isoformat() if eval_finished_at else None,
        "evaluationDurationMs": eval_duration_ms,
        "indicatorCalcMs":      t.get("indicatorCalcMs"),
        "volatilityCalcMs":     t.get("volatilityCalcMs"),
        "scoringMs":            t.get("scoringMs"),
        "aiNotesMs":            t.get("aiNotesMs"),
        "screenshotMs":         t.get("screenshotMs"),
        "journalWriteMs":       t.get("journalWriteMs"),
        "alertSentAt":          alert_sent_at.isoformat() if alert_sent_at else None,
        "totalAlertDelayMs":    total_delay,
        # ── Volatility reading used by this evaluation ──
        "currentATR":           vol.get("atr_pts"),
        "baselineATR":          vol.get("baseline_pts"),
        "volatilityMultiplier": vol.get("ratio"),
        "volatilityThreshold":  vol.get("threshold_extreme"),
        "volatilityDecision":   vol.get("decision"),
        # ── ATR dynamic-stop breakdown (single source = this eval's trade plan) ──
        "atrMultiplier":          _eval_tp.get("atr_multiplier"),
        "nearestDemand":          _eval_tp.get("nearest_demand"),
        "nearestSupply":          _eval_tp.get("nearest_supply"),
        "structureStop":          _eval_tp.get("structure_stop"),
        "atrStop":                _eval_tp.get("atr_stop"),
        "minStopTicks":           _eval_tp.get("min_stop_ticks"),
        "stopDistanceTicks":      _eval_tp.get("stop_distance_ticks"),
        "finalStop":              _eval_tp.get("stop_loss"),
        "riskDollarsPerContract": _eval_tp.get("risk_dollars_per_contract"),
        "riskReward":             _eval_tp.get("rr"),
        "minFloorApplied":        _eval_tp.get("min_floor_applied"),
        # ── Context ──
        "instrument":           instrument,
        "verdict":              (a or {}).get("verdict"),
        # ── Per-signal latency view (additive aliases) ──
        "direction":            direction,
        "setupType":            setup_type,
        "decision":             (a or {}).get("verdict"),
        "alertReceivedTime":    webhook_received_at.isoformat() if webhook_received_at else None,
        "processingStartTime":  eval_started_at.isoformat() if eval_started_at else None,
        "processingEndTime":    eval_finished_at.isoformat() if eval_finished_at else None,
        "totalLatencyMs":       total_latency_ms,
        "isDuplicate":          bool(is_duplicate),
        "signalCooldownRemainingMs": signal_cooldown_remaining_ms,
        # ── Gate / decision breakdown ──
        "alertLevel":           alert_level,
        "edgeScore":            gd.get("edge_score"),
        "edgeOk":               gd.get("edge_ok"),
        "zoneValid":            gd.get("zone_valid"),
        "confirmationsPassed":  gd.get("confirmations_passed"),
        "confirmationsNeeded":  gd.get("confirmations_needed"),
        "readyThreshold":       gd.get("ready_threshold"),
        "gateBlockers":         blockers,
        # ── Expanded WAIT diagnostics (additive; surfaced on the Diagnostics page) ──
        "trigger":              trigger,
        "vwapConfirmed":        gd.get("vwap_confirmed"),
        "structureConfirmed":   gd.get("structure_confirmed"),
        "candleConfirmed":      gd.get("candle_confirmed"),
        "liquidityConfirmed":   gd.get("liquidity_sweep"),
        "volatilityConfirmed":  (None if gd.get("volatility_block") is None
                                 else (not gd.get("volatility_block"))),
        "confidenceScore":      (a or {}).get("confidence"),
        "waitReason":           (a or {}).get("strict_reason") or (a or {}).get("reason"),
        # ── Alert dispatch + cooldown ──
        "alertSent":            alert_sent,
        "webhookSent":          bool(alert_sent_at),
        "tieredSent":           bool(tiered_sent),
        "lastAlertAt":          last_alert_at.isoformat() if last_alert_at else None,
        "cooldownMs":           cooldown_ms,
        "cooldownRemainingMs":  cooldown_remaining_ms,
        "suppressedByCooldown": suppressed,
        # ── EARLY pre-READY timing (additive) ──
        "eventStartTime":       event_start_iso,
        "sweepTime":            et_slot.get("sweepTime"),
        "chochTime":            et_slot.get("chochTime"),
        "displacementTime":     et_slot.get("displacementTime"),
        "earlyAlertTime":       early_at_iso,
        "readyAlertTime":       ready_at_iso,
        "alertDelaySeconds":    alert_delay_seconds,
        "waitedForCandleClose": waited_for_candle_close,
    }
    with EVAL_METRICS_LOCK:
        EVAL_METRICS.append(record)

    # ── Cumulative counters (separate lock; NEVER nested in EVAL_METRICS_LOCK) ──
    # Best-effort, like the rest of this function — counter math must never raise
    # into the worker / heartbeat loop.
    try:
        verdict_str = (a or {}).get("verdict") or ""
        is_ready    = is_actionable(verdict_str)
        if (a or {}).get("market_open") is False:
            # Market closed — evaluations are paused. Never tally a quiet tape as
            # an evaluation or a failed/rejected signal (the row is still recorded
            # above for diagnostics; only the counters are skipped).
            return
        with COUNTERS_LOCK:
            COUNTERS["evaluations_run"] += 1
            if alert_sent:
                COUNTERS["alerts_sent"] += 1
            # Webhook (non-duplicate) signal funnel: how many inbound signals
            # passed every filter (READY) vs were rejected (WAIT). Heartbeat
            # re-evals and duplicate repeats are excluded so this tracks real
            # inbound TradingView signals only.
            if trigger == "webhook" and not is_duplicate:
                if is_ready:
                    COUNTERS["signals_passed_filters"] += 1
                else:
                    COUNTERS["signals_rejected"] += 1
            # Count a fresh setup once per non-READY -> READY transition per
            # instrument (NOT on every heartbeat re-eval of a still-READY setup).
            prev_ready = _READY_STATE_BY_INST.get(inst_key, False)
            if is_ready and not prev_ready:
                COUNTERS["ready_setups_detected"] += 1
            if inst_key is not None:
                _READY_STATE_BY_INST[inst_key] = is_ready
            # Tally each failed gate on a WAIT verdict so the operator can see the
            # dominant reasons setups don't fire.
            if not is_ready:
                for _fc in (gd.get("failed_conditions") or []):
                    COUNTERS["wait_reasons_breakdown"][_fc] = \
                        COUNTERS["wait_reasons_breakdown"].get(_fc, 0) + 1
                # Canonical, broken-out rejection reasons (req 6). Scoped to genuine
                # inbound signals (webhook, non-duplicate) so this cleanly decomposes
                # the signals_rejected funnel bucket; duplicates are tallied as
                # cooldown_duplicate in the webhook() dedup path instead. Counted at
                # the CONDITION level (not just the active hard gate) so SCALP
                # confirmation gaps (vwap/structure/candle) are visible even when the
                # raw wait_reasons_breakdown lumps them into "confirmations(N<M)".
                # session_filter is deliberately NEVER bumped: the trading session is
                # a +10 bonus, never a gate, so it can never reject a setup.
                if trigger == "webhook" and not is_duplicate:
                    _rr = COUNTERS["rejection_reasons"]
                    def _bump_rr(_k):
                        _rr[_k] = _rr.get(_k, 0) + 1
                    if not gd.get("zone_valid"):
                        _bump_rr("zone_valid")
                    if gd.get("vwap_confirmed") is False:
                        _bump_rr("vwap_confirmed")
                    if gd.get("structure_confirmed") is False:
                        _bump_rr("structure_confirmed")
                    if gd.get("candle_confirmed") is False:
                        _bump_rr("candle_confirmed")
                    if gd.get("volatility_block"):
                        _bump_rr("volatility_block")
                    if not gd.get("edge_ok"):
                        _bump_rr("edge_score_low")
                    if gd.get("conflicting_structure"):
                        _bump_rr("conflicting_structure")
    except Exception as exc:
        logger.error("Counter update failed (eval still recorded): %s", exc)


def _update_setup_state(inst, a, dispatched_ready=False, is_duplicate=False):
    """Derive a per-instrument setup lifecycle state AFTER an evaluation. Pure
    display/diagnostics — it is NEVER read by full_analysis or the gate, so it can
    never alter a READY/WAIT verdict. States: FORMING -> READY -> ACTIVE, plus
    INVALIDATED / EXPIRED. A READY->ACTIVE move requires a live READY card actually
    dispatched on THIS eval (dispatched_ready), not merely a READY verdict (so a
    heartbeat re-eval never promotes to ACTIVE). Live states are STICKY: a transient
    heartbeat/duplicate re-eval never downgrades them, so the 1-min TradingView
    repeats can't flap READY<->FORMING. A live state leaves only via an upgrade,
    INVALIDATED (zone broken/mitigated or an opposite-direction READY), or EXPIRED
    (held longer than SETUP_STATE_TTL_SEC)."""
    inst_key = instrument_of(inst) if inst else None
    if not inst_key:
        return
    a         = a or {}
    verdict   = a.get("verdict") or ""
    ready     = is_actionable(verdict)
    invalid   = bool(a.get("zone_broken_active") or a.get("zone_mitigated_near"))
    candidate = a.get("gate_candidate")
    if verdict.startswith("LONG"):
        cur_dir = "Long"
    elif verdict.startswith("SHORT"):
        cur_dir = "Short"
    elif candidate in ("Long", "Short"):
        cur_dir = candidate
    else:
        cur_dir = None
    now = now_utc()

    with STATE_LOCK:
        prev       = SETUP_STATE.get(inst_key) or {}
        prev_state = prev.get("state")
        prev_dir   = prev.get("direction")
        prev_since = prev.get("since_dt") or now
        live_prev  = prev_state in ("FORMING", "READY", "ACTIVE")
        opposite_ready = bool(ready and cur_dir and prev_dir and cur_dir != prev_dir)

        # A duplicate repeat of the SAME signal must never downgrade a live state —
        # it only refreshes liveness (it can still invalidate/expire below).
        if is_duplicate and live_prev and not invalid and not opposite_ready:
            base, base_dir = prev_state, prev_dir
        elif invalid or opposite_ready:
            base, base_dir = "INVALIDATED", (cur_dir if opposite_ready else prev_dir)
        elif ready:
            base     = "ACTIVE" if (dispatched_ready or prev_state == "ACTIVE") else "READY"
            base_dir = cur_dir or prev_dir
        elif cur_dir in ("Long", "Short"):
            base, base_dir = "FORMING", cur_dir
        else:
            base, base_dir = None, None

        # Stickiness: a live state is never downgraded by a transient re-eval. Only
        # an upgrade (higher rank), INVALIDATED, or EXPIRED can move it; a lower or
        # empty base is ignored while the prior live state still stands.
        _RANK = {"FORMING": 1, "READY": 2, "ACTIVE": 3}
        if live_prev and base != "INVALIDATED":
            if base in _RANK and prev_state in _RANK and _RANK[base] < _RANK[prev_state]:
                base, base_dir = prev_state, prev_dir
            elif base is None:
                base, base_dir = prev_state, prev_dir

        # since: reset when the state label changes; otherwise carry it forward.
        since_dt = now if base != prev_state else prev_since
        # Expiry: a live state held longer than the TTL ages out to EXPIRED.
        if (base in ("FORMING", "READY", "ACTIVE")
                and (now - since_dt).total_seconds() > SETUP_STATE_TTL_SEC):
            base, since_dt = "EXPIRED", now

        SETUP_STATE[inst_key] = {
            "state":          base,
            "direction":      base_dir,
            "since_dt":       since_dt,
            "since":          since_dt.isoformat(),
            "last_update_dt": now,
            "last_update":    now.isoformat(),
        }


def _process_webhook_alert(record, parsed_price, resolved_inst, normalized,
                           account_size, risk_pct, profile_name,
                           webhook_received_at=None, is_duplicate=False,
                           cooldown_remaining_ms=None):
    """Heavy webhook tail (analysis + journaling + Discord) run OFF the request
    thread so TradingView gets a fast ack and never times out. All state this job
    reads (ALERT_HISTORY, zone flags, CURRENT_PRICE/VWAP) is committed
    synchronously before the job is queued; the per-alert price is passed in
    explicitly so late processing still scores against the correct price.

    Ordering is latency-critical: the READY/WARN/WAIT decision (full_analysis) is
    scored and TIMED first, the trade alert is sent BEFORE any journaling, and the
    journal-channel embed is offloaded to the slow-task worker — so nothing slow
    ever delays the decision or the alert. Per-evaluation timings + the volatility
    reading are recorded to EVAL_METRICS for the Diagnostics page."""
    global ACTIVE_TRADE

    _eval_timing_begin()
    eval_started_at = now_utc()
    _eval_t0 = time.perf_counter()
    a = full_analysis(current_price_override=parsed_price, ticker_override=resolved_inst)
    eval_finished_at = now_utc()
    eval_duration_ms = round((time.perf_counter() - _eval_t0) * 1000.0, 3)
    alert_sent_at = None
    tiered_sent   = False

    # ── Unconfirmed zone mitigation → consumed-zone notice, skip the trade
    # engine. A CONFIRMED bullish mitigation reaction sets zone_mitigated_near
    # False (handled as a LONG above) and falls through to the READY-card path. ──
    if a.get("zone_mitigated_near"):
        _mz_price = a.get("mitigated_zone_price")
        send_zone_mitigated_message(record, _mz_price)
        logger.info("Zone mitigated (unconfirmed) — %s — scoring skipped", normalized)
        _record_diagnostic(
            "%s | %s | Trigger: %s\nResult: WAIT\nBlocked by: zone_consumed "
            "(zone already reacted — scoring skipped)" % (
                resolved_inst, fmt_et(now_utc(), "%Y-%m-%d %H:%M:%S ET"), normalized))
        _record_eval_metrics(a, webhook_received_at, eval_started_at,
                             eval_finished_at, eval_duration_ms, alert_sent_at,
                             resolved_inst, tiered_sent=tiered_sent,
                             signal_type=normalized, is_duplicate=is_duplicate,
                             signal_cooldown_remaining_ms=cooldown_remaining_ms)
        try:
            _update_setup_state(resolved_inst, a, dispatched_ready=False,
                                is_duplicate=is_duplicate)
        except Exception as exc:
            logger.error("Setup-state update failed (zone-mitigated path): %s", exc)
        return

    # ── EARLY intrabar pre-READY alert (additive, fail-open). Fired HERE — before
    # the journal/READY-card path below — so an early, unconfirmed sweep+structure
    # heads-up precedes the confirmed READY in time. It never alters the verdict,
    # the journal, or managed trades. ──
    try:
        _maybe_dispatch_early_alert(a, record)
    except Exception as exc:
        logger.error("EARLY alert dispatch error: %s", exc)

    # BOS-only ("Attempt") entries trade at reduced size (mode-dependent).
    _risk_mult = (cfg("RISK_MULT_ATTEMPT")
                  if a.get("structure_class") in ("Bullish Attempt", "Bearish Attempt")
                  else 1.0)
    sizing = calculate_position_sizing(a["trade_plan"], account_size, risk_pct * _risk_mult, profile_name)

    # ── Active trade: check events (T1 / T2 / Stop lifecycle alerts) ──
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

    # ── Trading Journal + live alert ───────────────────────────────────────────
    # The main alert channel now receives the same clean trade-card as the
    # journal, but ONLY when a brand-new setup is READY. create_journal_entry()
    # returns the entry just once per setup (deduped), so this fires the instant
    # alert exactly once on the triggering webhook; _trade_ready_loop() then
    # re-posts the card every 5 min while the setup stays READY.
    # Fail-open: journaling/enrichment + the live card must never crash the worker.
    # Dedup + build the card + store the journal IN-MEMORY only (fast). A failure
    # here must not crash the worker or block the alert below.
    try:
        journal_entry = create_journal_entry(record, a, sizing, post_discord=False)
    except Exception as exc:
        journal_entry = None
        logger.error("Journal build/store error (alert still attempted): %s", exc)

    # Trade alert FIRST — the instant a brand-new setup is READY, BEFORE any
    # Discord journal post, so the alert is never queued behind slower work. Its
    # error handling is isolated so a live-card failure can't suppress the journal
    # embed offloaded below.
    if (journal_entry and not ACTIVE_TRADE
            and is_actionable(a.get("verdict"))):
        try:
            send_live_ready_card(journal_entry,
                                 record.get("ticker") or record.get("instrument")
                                 or journal_entry.get("instrument"),
                                 notify=True)
            alert_sent_at = now_utc()
        except Exception as exc:
            logger.error("Live-card send error (alert path): %s", exc)

    # Tiered WATCH/ARMED early alert (SCALP only). Mutually exclusive with the READY
    # card above (alert_level is READY vs WATCH/ARMED) and throttled per instrument.
    # The throttle state is recorded synchronously but the Discord POST is offloaded
    # to the slow-task worker, so it can never delay the READY card, the journal
    # enqueue below, or the next webhook evaluation. Fail-open.
    try:
        tiered_sent = _maybe_send_tiered_alert(a, record)
    except Exception as exc:
        logger.error("Tiered alert dispatch error: %s", exc)

    # Offload the journal-channel embed (the only slow piece left here), regardless
    # of the live-card outcome, so the decision worker is free immediately.
    if journal_entry:
        _enqueue_slow(lambda e=journal_entry: send_journal_discord_embed(e))

    _gd = a.get("gate_debug") or {}
    if is_actionable(a["verdict"]):
        _gate_str = "READY"
    elif _gd.get("conflicting_structure"):
        _gate_str = "conflict"
    else:
        _gate_str = "zone=%s vwap=%s struct=%s edge=%d%s" % (
            "Y" if _gd.get("zone_valid") else "N",
            "Y" if _gd.get("vwap_confirmed") else "N",
            "Y" if _gd.get("structure_confirmed") else "N",
            _gd.get("edge_score", 0),
            "" if _gd.get("edge_ok") else ("<%d" % _gd.get("ready_threshold", EDGE_READY_THRESHOLD)),
        )
        _vol = a.get("volatility") or {}
        if _gd.get("volatility_block"):
            _gate_str += " vol=BLOCK"
        elif _vol.get("status") == "ok" and _vol.get("score_adj"):
            _gate_str += " volAdj=%+d" % _vol.get("score_adj")
    logger.info(
        "Alert: %s | %s (%d/10) | %d%% | Edge %d | %s → %s | Struct: %s | Risk: %s | Gate: %s",
        normalized, a["bias"], a["strength"], a["confidence"], a["edge_score"],
        a["recommendation"], a["verdict"], a["structure_class"], a["risk_label"], _gate_str,
    )

    # ── Full per-gate diagnostic — logged AND persisted to the owner-viewable
    # /diagnostics buffer so it's clear EXACTLY which gate holds a setup at WAIT.
    # Hard blockers applied after the gate (zone broken) are surfaced explicitly
    # since gate_debug itself is not recomputed by those overrides. ──
    _extra = []
    if a.get("zone_broken_active"):
        _extra.append("zone_broken")
    _candidate = a.get("gate_candidate") or ready_direction(a.get("verdict"))
    try:
        _diag = format_gate_diagnostic(resolved_inst, normalized, _candidate, _gd,
                                       a["verdict"], extra_blockers=_extra,
                                       vol=a.get("volatility"))
        _tp_diag = a.get("trade_plan")
        if isinstance(_tp_diag, dict) and _tp_diag.get("atr_pts") is not None:
            _diag += "\n  ATR dynamic stop —\n" + "\n".join(_stop_diag_lines(_tp_diag))
        logger.info("Gate diagnostic —\n%s", _diag)
        _record_diagnostic(_diag)
    except Exception as exc:
        logger.error("Gate diagnostic formatting failed (alert still recorded): %s", exc)

    _record_eval_metrics(a, webhook_received_at, eval_started_at,
                         eval_finished_at, eval_duration_ms, alert_sent_at,
                         resolved_inst, tiered_sent=tiered_sent,
                         signal_type=normalized, is_duplicate=is_duplicate,
                         signal_cooldown_remaining_ms=cooldown_remaining_ms)

    # ── Structured per-webhook latency line (additive diagnostics) ──────────────
    try:
        logger.info(
            "Webhook latency | sym=%s dir=%s setup=%s decision=%s dup=%s "
            "alertReceivedTime=%s processingStartTime=%s processingEndTime=%s "
            "evalMs=%s totalMs=%s",
            resolved_inst, (a.get("gate_candidate") or "-"), normalized,
            a.get("verdict"), is_duplicate,
            (webhook_received_at.isoformat() if webhook_received_at else "-"),
            (eval_started_at.isoformat() if eval_started_at else "-"),
            (eval_finished_at.isoformat() if eval_finished_at else "-"),
            eval_duration_ms,
            (round((eval_finished_at - webhook_received_at).total_seconds() * 1000.0, 1)
             if webhook_received_at else eval_duration_ms))
    except Exception as exc:
        logger.error("Latency log formatting failed: %s", exc)

    # ── Persistent setup-state lifecycle (additive, DISPLAY-ONLY) ───────────────
    # Derived AFTER the verdict + dispatch so it never feeds back into the gate. A
    # READY->ACTIVE transition requires that a live READY card was actually sent on
    # THIS evaluation (alert_sent_at), not merely a READY verdict.
    try:
        _update_setup_state(resolved_inst, a,
                            dispatched_ready=bool(alert_sent_at),
                            is_duplicate=is_duplicate)
    except Exception as exc:
        logger.error("Setup-state update failed: %s", exc)


# ── Asynchronous webhook processing ──────────────────────────────────────────
# TradingView aborts a webhook if the server does not respond quickly ("request
# took too long and timed out"). The analysis itself is in-memory/fast, but the
# Discord POSTs in the tail (journal + live card, each up to 5-10 s) can stack
# and blow past that timeout. So the request thread does only the fast, in-memory
# state commit (record + zone flags + price/VWAP) and then hands the heavy tail
# to a single background worker, returning an immediate ack. The worker is
# serialized (one job at a time, FIFO) so concurrent alerts (e.g. MGC + MNQ on
# the same bar) never race on shared state.
_WEBHOOK_JOBS          = queue.Queue()
_WEBHOOK_WORKER_LOCK   = threading.Lock()
_WEBHOOK_WORKER_THREAD = None


def _webhook_worker():
    while True:
        job = _WEBHOOK_JOBS.get()
        try:
            _process_webhook_alert(**job)
        except Exception as exc:
            logger.error("Webhook background processing failed: %s", exc)
        finally:
            _WEBHOOK_JOBS.task_done()


def _ensure_webhook_worker():
    """Start the background worker on first use (idempotent, thread-safe). Lazy
    start keeps module import side-effect-free and works regardless of how Flask
    is launched (python app.py or a WSGI server)."""
    global _WEBHOOK_WORKER_THREAD
    if _WEBHOOK_WORKER_THREAD is not None and _WEBHOOK_WORKER_THREAD.is_alive():
        return
    with _WEBHOOK_WORKER_LOCK:
        if _WEBHOOK_WORKER_THREAD is not None and _WEBHOOK_WORKER_THREAD.is_alive():
            return
        _WEBHOOK_WORKER_THREAD = threading.Thread(
            target=_webhook_worker, name="webhook-worker", daemon=True)
        _WEBHOOK_WORKER_THREAD.start()
        logger.info("Webhook background worker started")


# ── Slow-task worker ─────────────────────────────────────────────────────────
# Everything that is NOT the trade decision or the trade alert (the journal-
# channel Discord embed, etc.) runs here, OFF the decision worker, so a slow or
# timing-out Discord POST can never delay the next alert's READY/WARN/WAIT
# decision or the trade alert itself. Single FIFO thread, so it preserves the
# same no-race guarantee on shared JOURNAL state that the webhook worker relies
# on (the in-memory store still happens on the decision worker; only the embed
# is offloaded).
_SLOW_TASKS         = queue.Queue()
_SLOW_WORKER_LOCK   = threading.Lock()
_SLOW_WORKER_THREAD = None


def _slow_task_worker():
    while True:
        fn = _SLOW_TASKS.get()
        try:
            fn()
        except Exception as exc:
            logger.error("Slow task failed: %s", exc)
        finally:
            _SLOW_TASKS.task_done()


def _ensure_slow_worker():
    """Start the slow-task worker on first use (idempotent, thread-safe)."""
    global _SLOW_WORKER_THREAD
    if _SLOW_WORKER_THREAD is not None and _SLOW_WORKER_THREAD.is_alive():
        return
    with _SLOW_WORKER_LOCK:
        if _SLOW_WORKER_THREAD is not None and _SLOW_WORKER_THREAD.is_alive():
            return
        _SLOW_WORKER_THREAD = threading.Thread(
            target=_slow_task_worker, name="slow-task-worker", daemon=True)
        _SLOW_WORKER_THREAD.start()
        logger.info("Slow-task background worker started")


def _enqueue_slow(fn):
    _ensure_slow_worker()
    _SLOW_TASKS.put(fn)


# ── Heartbeat market re-evaluation loop (additive, DIAGNOSTIC-ONLY) ───────────
# Periodically re-scores every instrument so the Diagnostics page shows a live,
# continuously-updated read (counters, gate signals, WAIT reasons) instead of only
# the infrequent TradingView webhooks. It calls full_analysis + _record_eval_metrics
# ONLY — never a Discord / journal / EARLY / tiered / live-card path — so it can
# never alter a READY/WAIT verdict or post a duplicate alert. Fail-open per
# instrument, and strictly less frequent than the existing /status polling that
# already calls full_analysis, so it adds no new concurrency risk.
_HEARTBEAT_INSTRUMENTS = ("MGC", "MNQ")


def _run_heartbeat_evaluations():
    # Market closed → pause the diagnostic re-eval entirely: no full_analysis, no
    # eval metrics, no setup-state churn. A quiet (closed) tape must never be
    # re-scored into a stream of "failed" WAITs the counters then tally.
    if not market_session_status()["open"]:
        return
    for inst in _HEARTBEAT_INSTRUMENTS:
        try:
            _eval_timing_begin()          # per-instrument so phase timings don't bleed
            _t0     = time.perf_counter()
            started = now_utc()
            a       = full_analysis(ticker_override=inst)
            finished = now_utc()
            dur_ms  = round((time.perf_counter() - _t0) * 1000.0, 3)
            _record_eval_metrics(a, None, started, finished, dur_ms, None, inst,
                                 tiered_sent=False, trigger="heartbeat")
            try:                       # display-only lifecycle refresh (never dispatches)
                _update_setup_state(inst, a, dispatched_ready=False, is_duplicate=False)
            except Exception as exc:
                logger.error("Heartbeat setup-state update failed for %s: %s", inst, exc)
        except Exception as exc:
            logger.error("Heartbeat evaluation failed for %s: %s", inst, exc)


def _heartbeat_eval_loop():
    """Run a heartbeat evaluation pass now, then reschedule every
    EVAL_HEARTBEAT_INTERVAL seconds. Diagnostic-only; safe on dev and prod."""
    try:
        if EVAL_HEARTBEAT_ENABLED:
            _run_heartbeat_evaluations()
    except Exception as exc:
        logger.error("Heartbeat eval loop error: %s", exc)
    finally:
        threading.Timer(EVAL_HEARTBEAT_INTERVAL, _heartbeat_eval_loop).start()


@app.route("/webhook", methods=["POST"])
def webhook():
    global CURRENT_PRICE, ACTIVE_TRADE, ZONE_BROKEN_AT, ZONE_MITIGATED_FLAG, LAST_WEBHOOK_AT

    webhook_received_at = now_utc()   # T0 for the webhook->alert delay metric
    LAST_WEBHOOK_AT = webhook_received_at   # any inbound POST, before any early return
    with COUNTERS_LOCK:
        COUNTERS["webhooks_received"] += 1
        _WEBHOOK_TS.append(webhook_received_at)   # rolling last-hour window

    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        data = {}

    raw_body = request.get_data(as_text=True)
    if not data and raw_body:
        data = {"alert_type": raw_body.strip()}

    # ── Compatibility shim: alternate VWAP alert schema ──────────────────────
    # Some TradingView VWAP alerts post {"signal":"VWAP_BULLISH","symbol":"MNQ1!",
    # "instrument":"MGC",...} instead of the canonical
    # {"alert_type":"MNQ VWAP","ticker":"MNQ1!",...}. Map them onto the existing
    # data-only VWAP type so they are ingested (price refresh + ack) instead of
    # dropped as "unrecognized". The chart `symbol` is the authoritative
    # instrument (it is the literal {{ticker}} and matches the price level); the
    # free-text `instrument` field is only a fallback and is ignored when it
    # disagrees. No `vwap` value is invented, so the auto-fetched VWAP is never
    # corrupted — these alerts simply refresh the per-instrument price.
    if not (data.get("alert_type") or data.get("message") or data.get("text")):
        _sig = str(data.get("signal") or "").strip().upper()
        if _sig.startswith("VWAP"):
            _vw_inst = (_instrument_from_text(data.get("symbol"))
                        or _instrument_from_text(data.get("ticker"))
                        or _instrument_from_text(data.get("instrument")))
            if _vw_inst:
                _inst_field = _instrument_from_text(data.get("instrument"))
                if _inst_field is not None and _inst_field != _vw_inst:
                    logger.warning("VWAP alert symbol=%r disagrees with instrument=%r; "
                                   "using %s (chart symbol is authoritative)",
                                   data.get("symbol"), data.get("instrument"), _vw_inst)
                data["alert_type"] = f"{_vw_inst} VWAP"
                data.setdefault("ticker", data.get("symbol") or _vw_inst)
                logger.info("Normalized alternate VWAP alert (signal=%r) → %s",
                            _sig, data["alert_type"])

    alert_type = (data.get("alert_type") or data.get("message") or data.get("text") or "")
    normalized = alert_type.strip().upper()

    if normalized not in ALERT_TYPES:
        logger.warning("Unrecognized alert type: %r", alert_type)
        _record_diagnostic("%s | IGNORED — unrecognized/empty alert: %r" % (
            fmt_et(now_utc(), "%Y-%m-%d %H:%M:%S ET"), alert_type))
        return jsonify({"status": "ignored", "reason": "unrecognized alert type", "received": alert_type}), 200

    # ── Authoritative instrument resolution (ticker-first; never silently MGC) ──
    # The payload `ticker` field decides the instrument. The alert title is used
    # only when no ticker is present, and unresolvable/contradictory alerts are
    # rejected rather than misattributed (default-MGC) to the wrong instrument.
    res = resolve_instrument(data.get("ticker"), normalized)
    if not res["ok"]:
        logger.warning("Ticker resolution rejected %r (ticker=%r): %s",
                       normalized, data.get("ticker"), res["error"])
        _record_diagnostic("%s | REJECTED — %s — ticker resolution failed: %s" % (
            fmt_et(now_utc(), "%Y-%m-%d %H:%M:%S ET"), normalized, res["error"]))
        return jsonify({
            "status":     "error",
            "reason":     "ticker resolution failed",
            "detail":     res["error"],
            "alert_type": normalized,
            "ticker":     data.get("ticker"),
        }), 400
    resolved_inst = res["instrument"]
    if res["source"] == "title":
        logger.warning("Alert %r has no ticker field — resolved %s from the title; "
                       "add a `ticker` field to this TradingView alert.",
                       normalized, resolved_inst)

    raw_price = data.get("price")
    try:
        parsed_price = float(raw_price) if raw_price is not None else None
    except (ValueError, TypeError):
        parsed_price = None

    if parsed_price is not None:
        CURRENT_PRICE = parsed_price
        CURRENT_PRICE_BY_TICKER[resolved_inst] = parsed_price
        CURRENT_PRICE_TS_BY_TICKER[resolved_inst] = now_utc().isoformat()

    # ── VWAP ingestion (required input for the strict price-vs-VWAP filter) ──
    raw_vwap = data.get("vwap")
    if raw_vwap is not None:
        try:
            vwap_val = float(raw_vwap)
        except (ValueError, TypeError):
            vwap_val = None
        if vwap_val is not None:
            vwap_key = resolved_inst
            VWAP_BY_TICKER[vwap_key] = {"value": vwap_val, "ts": now_utc().isoformat(),
                                        "source": "chart"}

    # ── Data-only VWAP push — store already updated above; ack without scoring ──
    if normalized in _DATA_ONLY_TYPES:
        _vk = resolved_inst
        _vv, _vs = get_vwap(_vk)
        logger.info("VWAP update: %s = %s (%s)", _vk, _vv, _vs)
        _record_diagnostic("%s | %s — VWAP update = %s (%s) — data only, no scoring" % (
            fmt_et(now_utc(), "%Y-%m-%d %H:%M:%S ET"), _vk, _vv, _vs))
        return jsonify({
            "status":      "vwap_updated",
            "ticker":      _vk,
            "vwap":        _vv,
            "vwap_status": _vs,
            "price":       parsed_price if parsed_price is not None else CURRENT_PRICE,
        }), 200

    # ── Command types: ENTER / CLOSE sent via TradingView webhook ─────────────
    #    Per user choice, the webhook stays OPEN (no shared-secret) so existing
    #    TradingView ENTER/CLOSE alerts work unmodified. Trade entry/exit is also
    #    available via the password-protected dashboard (/enter, /close).
    #    NOTE: this path is intentionally unauthenticated — anyone who knows the
    #    webhook URL can trigger ENTER/CLOSE here. The user accepted this tradeoff
    #    in favour of keeping their TradingView automation working as-is.
    if normalized in _COMMAND_TYPES:
        _resp = _handle_command_alert(normalized, data, parsed_price, resolved_inst)
        if _resp is not None:
            return _resp

    record = {
        "alert_type":        normalized,
        "ticker":            data.get("ticker"),
        "instrument":        resolved_inst,
        "instrument_source": res["source"],
        "price":             parsed_price,
        "timestamp":         now_utc().isoformat(),
        "raw":               data,
    }
    global LAST_ALERT_AT
    LAST_ALERT_AT = datetime.now(timezone.utc)
    ALERT_HISTORY.append(record)

    # ── Zone event side-effects ──
    _zone_neutral = ("MGC ZONE BROKEN", "MNQ ZONE BROKEN", "MGC ZONE MITIGATED", "MNQ ZONE MITIGATED")
    if normalized in ("MGC ZONE BROKEN", "MNQ ZONE BROKEN"):
        _zb_instrument = _instrument_from_text(normalized)
        if parsed_price is not None:
            _handle_zone_broken(parsed_price, _zb_instrument)
        else:
            ZONE_BROKEN_AT = {"price": None, "alerts_since": 0, "instrument": _zb_instrument}
    elif normalized in ("MGC ZONE MITIGATED", "MNQ ZONE MITIGATED"):
        if parsed_price is not None:
            _handle_zone_mitigated(parsed_price)
    elif ZONE_BROKEN_AT is not None and normalized not in _zone_neutral:
        # Count expiry only on same-instrument alerts so the other instrument's
        # activity can't prematurely clear this instrument's broken-zone blocker.
        # Untagged (legacy) breaks expire on any alert (global fallback).
        _zb_inst = ZONE_BROKEN_AT.get("instrument")
        if _zb_inst is None or _zb_inst == resolved_inst:
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

    # ── Inbound signal de-dup (additive, DIAGNOSTIC only) ────────────────────────
    # Flag a repeat of the SAME (instrument, alert_type) seen within the cooldown so
    # the operator can SEE how many duplicates the 1-min TradingView repeats produce.
    # This NEVER skips the enqueue or full_analysis, and NEVER suppresses a dispatch
    # — the authoritative, zone-aware non-duplication already lives downstream. A
    # direction flip or different setup is a different key, so it's never a duplicate.
    is_duplicate          = False
    cooldown_remaining_ms = None
    try:
        _dedup_inst   = instrument_of(resolved_inst)
        _cooldown_sec = signal_dedup_cooldown_sec(_dedup_inst)
        sig_key = (_dedup_inst, normalized)
        with DEDUP_LOCK:
            _prev_seen = SIGNAL_DEDUP.get(sig_key)
            if _prev_seen is not None:
                _elapsed = (webhook_received_at - _prev_seen).total_seconds()
                if 0 <= _elapsed < _cooldown_sec:
                    is_duplicate          = True
                    cooldown_remaining_ms = round(
                        (_cooldown_sec - _elapsed) * 1000.0, 1)
            SIGNAL_DEDUP[sig_key] = webhook_received_at
        if is_duplicate:
            with COUNTERS_LOCK:
                COUNTERS["duplicates_ignored"] += 1
                COUNTERS["wait_reasons_breakdown"]["cooldown_duplicate"] = \
                    COUNTERS["wait_reasons_breakdown"].get("cooldown_duplicate", 0) + 1
                COUNTERS["rejection_reasons"]["cooldown_duplicate"] = \
                    COUNTERS["rejection_reasons"].get("cooldown_duplicate", 0) + 1
                _DUP_TS.append(webhook_received_at)
    except Exception as exc:
        logger.error("Signal dedup check failed (alert still processed): %s", exc)

    # ── Hand the heavy tail (analysis + journaling + Discord) to the background
    # worker and ack immediately so TradingView never times out. Every piece of
    # state the job needs has been committed synchronously above (ALERT_HISTORY,
    # zone flags, price/VWAP); the per-alert price is passed explicitly. ──
    _ensure_webhook_worker()
    _WEBHOOK_JOBS.put({
        "record":              record,
        "parsed_price":        parsed_price,
        "resolved_inst":       resolved_inst,
        "normalized":          normalized,
        "account_size":        account_size,
        "risk_pct":            risk_pct,
        "profile_name":        profile_name,
        "webhook_received_at": webhook_received_at,
        "is_duplicate":        is_duplicate,
        "cooldown_remaining_ms": cooldown_remaining_ms,
    })

    return jsonify({
        "status":       "accepted",
        "alert_type":   normalized,
        "ticker":       record.get("ticker"),
        "instrument":   resolved_inst,
        "queued":       True,
        "total_alerts": len(ALERT_HISTORY),
    }), 200


@app.route("/alerts", methods=["GET"])
def get_alerts():
    return jsonify({"alerts": list(ALERT_HISTORY), "count": len(ALERT_HISTORY)}), 200


@app.route("/diagnostics", methods=["GET"])
def get_diagnostics():
    """Owner-only (dashboard password, enforced by the Express proxy) plain-text
    view of the most recent per-webhook gate diagnostics — newest first. `?n=`
    limits the count (default 60). Shows EXACTLY why each alert was WAIT/READY."""
    try:
        n = int(request.args.get("n", 60))
    except (ValueError, TypeError):
        n = 60
    n = max(1, min(n, GATE_DIAGNOSTICS.maxlen))
    entries = list(GATE_DIAGNOSTICS)[-n:][::-1]
    header = ("Gate diagnostics — %d shown of %d buffered (newest first).\n"
              "Add ?n=N to show more (max %d). Each webhook the bot scores appears "
              "here with a per-gate PASS/FAIL breakdown.\n" % (
                  len(entries), len(GATE_DIAGNOSTICS), GATE_DIAGNOSTICS.maxlen))
    body = "\n\n".join(entries) if entries else "No webhooks received yet."
    return Response(header + "\n" + body + "\n", mimetype="text/plain")


# ── Live Diagnostics page ────────────────────────────────────────────────────
# Static HTML (no server-side templating: no %-formatting, no triple-quote
# collisions). All data is fetched client-side from /api/eval-metrics every 1s.
DIAGNOSTICS_LIVE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Diagnostics - AI Trading Partner</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#0a0a0f;color:#e8e8f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:16px}
  a.back{color:#a0a8ff;text-decoration:none;font-size:13px}
  h1{font-size:18px;font-weight:700;color:#a0a8ff;letter-spacing:.5px;margin:8px 0 2px}
  .sub{font-size:12px;color:#666;margin-bottom:16px}
  .cards{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:18px}
  .card{background:#12121e;border:1px solid #1e1e32;border-radius:12px;padding:12px 14px;min-width:140px;flex:1}
  .card .k{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:#666;margin-bottom:6px}
  .card .v{font-size:20px;font-weight:800}
  .card .v.sm{font-size:13px;font-weight:600;white-space:normal;word-break:break-word;line-height:1.3}
  .good{color:#22c55e}.warn{color:#f59e0b}.bad{color:#ef4444}.muted{color:#888}
  .wrap{overflow-x:auto;border:1px solid #1e1e32;border-radius:12px}
  table{border-collapse:collapse;width:100%;font-size:12px;white-space:nowrap}
  th,td{padding:8px 10px;text-align:right;border-bottom:1px solid #16162a}
  th{position:sticky;top:0;background:#16162a;color:#a0a8ff;font-size:10px;text-transform:uppercase;letter-spacing:.5px;text-align:right}
  th:nth-child(-n+3),td:nth-child(-n+3){text-align:left}
  tbody tr:hover{background:#12121e}
  .v-ready{color:#22c55e;font-weight:700}.v-wait{color:#888}
  .empty{padding:24px;text-align:center;color:#666}
</style>
</head>
<body>
<a class="back" href="/api/dashboard">&larr; Back to dashboard</a>
<h1>Diagnostics</h1>
<div class="sub">Per-evaluation timing &amp; volatility for the last <span id="cap">100</span> scored alerts &middot; auto-refresh 1s &middot; <span id="updated" class="muted"></span></div>
<div class="cards" id="summary"></div>
<div class="wrap">
<table>
<thead><tr>
<th>Webhook Recv (ET)</th><th>Instr</th><th>Verdict</th>
<th>Dir</th><th>Setup</th><th>Dup</th>
<th>Eval Start</th><th>Eval Finish</th>
<th>Eval ms</th><th>Total ms</th><th>Indicator ms</th><th>Volatility ms</th><th>Scoring ms</th>
<th>AI Notes ms</th><th>Screenshot ms</th><th>Journal ms</th>
<th>Alert Sent</th><th>Alert Delay ms</th>
<th>ATR</th><th>Base ATR</th><th>Mult</th><th>Threshold</th><th>Vol Decision</th>
<th>Alert Level</th><th>Edge</th><th>Confirms</th><th>Gate Blockers</th>
<th>Sent?</th><th>Cooldown Left ms</th><th>Suppressed</th>
<th>Event Start</th><th>Sweep</th><th>CHOCH</th><th>Displacement</th>
<th>Early Alert</th><th>Ready Alert</th><th>Delay s</th><th>Waited Close</th>
<th>Trigger</th><th>Zone</th><th>VWAP</th><th>Struct</th><th>Candle</th><th>Liq</th><th>Vol</th><th>Conf</th><th>Wait Reason</th>
</tr></thead>
<tbody id="rows"><tr><td class="empty" colspan="47">Loading...</td></tr></tbody>
</table>
</div>
<script>
var BASE='/api';
function ms(v){return (v===null||v===undefined)?'-':Number(v).toFixed(1);}
function num(v){return (v===null||v===undefined)?'-':Number(v).toFixed(2);}
function intOrDash(v){return (v===null||v===undefined)?'-':Number(v).toFixed(0);}
function confs(e){
  var p=e.confirmationsPassed,n=e.confirmationsNeeded;
  if(p===null||p===undefined) return '-';
  return p+'/'+((n===null||n===undefined)?'-':n);
}
function etTime(iso){
  if(!iso) return '-';
  var dt=new Date(iso);
  if(isNaN(dt.getTime())) return '-';
  var t=dt.toLocaleTimeString('en-US',{hour12:false,timeZone:'America/New_York'});
  return t+'.'+String(dt.getMilliseconds()).padStart(3,'0');
}
function delayClass(v){
  if(v===null||v===undefined) return 'muted';
  if(v<1000) return 'good';
  if(v<3000) return 'warn';
  return 'bad';
}
function card(k,v,cls){return '<div class="card"><div class="k">'+k+'</div><div class="v '+(cls||'')+'">'+v+'</div></div>';}
function setupStateTxt(s){
  if(!s) return '-';
  var ks=Object.keys(s); if(!ks.length) return '-';
  return ks.map(function(k){
    var v=s[k]||{}; var st=v.state||'-'; var d=v.direction?(' '+v.direction):'';
    return k+': '+st+d;
  }).join(' \u00b7 ');
}
function setupStateCls(s){
  if(!s) return 'muted';
  var vals=Object.keys(s).map(function(k){return (s[k]||{}).state;});
  if(vals.indexOf('ACTIVE')>=0||vals.indexOf('READY')>=0) return 'good';
  if(vals.indexOf('INVALIDATED')>=0) return 'warn';
  if(vals.indexOf('FORMING')>=0) return '';
  return 'muted';
}
function yn(v){return (v===true)?'<span class="good">Y</span>':((v===false)?'<span class="bad">N</span>':'<span class="muted">-</span>');}
function pct(v){return (v===null||v===undefined)?'-':Number(v).toFixed(0)+'%';}
function fmtAgo(sec){
  if(sec===null||sec===undefined) return 'never';
  if(sec<60) return Math.round(sec)+'s ago';
  if(sec<3600) return Math.round(sec/60)+'m ago';
  return Math.round(sec/3600)+'h ago';
}
function showError(msg){
  var u=document.getElementById('updated');
  if(u){ u.className='bad'; u.textContent='\u26a0 '+msg+' \u2014 retrying every second'; }
  var rows=document.getElementById('rows');
  if(rows && rows.querySelector('.empty')){
    rows.innerHTML='<tr><td class="empty" colspan="47">'+msg+' \u2014 retrying every second.</td></tr>';
  }
}
async function refresh(){
  var data;
  try{
    var r=await fetch(BASE+'/eval-metrics',{headers:{'Content-Type':'application/json'},cache:'no-store'});
    if(!r.ok){ showError('Server returned HTTP '+r.status); return; }
    data=await r.json();
  }catch(e){ showError('Cannot reach the metrics endpoint ('+((e&&e.message)||'network error')+')'); return; }
  var evals=data.evaluations||[];
  document.getElementById('cap').textContent=data.max||100;
  var u=document.getElementById('updated');
  u.className='muted';
  u.textContent='updated '+new Date().toLocaleTimeString('en-US',{hour12:false});
  var st=data.stats||{};
  var c=st.counters||{};
  var sess=st.session||{};
  var wrb=c.wait_reasons_breakdown||{};
  var wrbTop=Object.keys(wrb).sort(function(a,b){return wrb[b]-wrb[a];}).slice(0,5)
              .map(function(k){return k+' ('+wrb[k]+')';}).join(', ')||'-';
  var rr=c.rejection_reasons||{};
  var rrLabel={zone_valid:'zone_valid',vwap_confirmed:'vwap_confirmed',
    structure_confirmed:'structure_confirmed',candle_confirmed:'candle_confirmed',
    volatility_block:'volatility_block',edge_score_low:'edge_score',
    conflicting_structure:'conflict',cooldown_duplicate:'cooldown_dup',
    session_filter:'session(bonus,n/a)'};
  var rrKeys=Object.keys(rrLabel).sort(function(a,b){return (rr[b]||0)-(rr[a]||0);});
  var rrTxt=rrKeys.map(function(k){return rrLabel[k]+' ('+(rr[k]||0)+')';}).join(', ');
  var sum=document.getElementById('summary');
  var e=evals.length?evals[0]:null;
  var ready=!!(e&&(e.verdict||'').indexOf('READY')>=0);
  var sinceTxt=fmtAgo(st.timeSinceLastWebhookSec);
  var staleWh=(st.timeSinceLastWebhookSec==null||st.timeSinceLastWebhookSec>900);
  sum.innerHTML=
    card('Latest verdict', e?(e.verdict||'-'):'-', ready?'good':'muted')+
    card('Latest WAIT reason', e?(ready?'\u2014 ready \u2014':(e.waitReason||e.gateBlockers||'-')):'-', 'sm '+(ready?'good':'warn'))+
    card('Webhooks received', (c.webhooks_received!=null?c.webhooks_received:'-'), 'muted')+
    card('Evaluations run', (c.evaluations_run!=null?c.evaluations_run:'-'), 'muted')+
    card('READY setups', (c.ready_setups_detected!=null?c.ready_setups_detected:'-'), (c.ready_setups_detected>0?'good':'muted'))+
    card('Alerts sent', (c.alerts_sent!=null?c.alerts_sent:'-'), (c.alerts_sent>0?'good':'muted'))+
    card('Last webhook', sinceTxt, (staleWh?'warn':'good'))+
    card('Eval frequency', (st.evaluationFrequencySeconds!=null?('every '+st.evaluationFrequencySeconds+'s'):'-'), (st.evalHeartbeatEnabled?'good':'bad'))+
    card('Signals passed', (st.signalsPassedFilters!=null?st.signalsPassedFilters:'-'), (st.signalsPassedFilters>0?'good':'muted'))+
    card('Signals rejected', (st.signalsRejected!=null?st.signalsRejected:'-'), 'muted')+
    card('Duplicates ignored', (st.duplicatesIgnored!=null?st.duplicatesIgnored:'-')+(st.duplicatesIgnoredLastHour!=null?(' ('+st.duplicatesIgnoredLastHour+'/hr)'):''), (st.duplicatesIgnored>0?'warn':'muted'))+
    card('Alerts last hour', (st.alertsReceivedLastHour!=null?st.alertsReceivedLastHour:'-'), 'muted')+
    card('Avg processing', (st.averageProcessingTimeMs!=null?(Number(st.averageProcessingTimeMs).toFixed(1)+' ms'):'-'), 'muted')+
    card('Queue length', (st.currentQueueLength!=null?st.currentQueueLength:'-'), (st.currentQueueLength>5?'warn':'muted'))+
    card('Setup states', setupStateTxt(st.setupStates), 'sm '+(setupStateCls(st.setupStates)))+
    card('Dedup cooldown', (st.signalDedupCooldownSec!=null?(st.signalDedupCooldownSec+'s'):'-'), 'sm muted')+
    card('Session', (sess.window||'-'), 'sm '+(sess.preferred?'good':'muted'))+
    card('Top WAIT reasons', wrbTop, 'sm muted')+
    card('Rejection reasons (condition gaps)', rrTxt, 'sm muted');
  var rows=document.getElementById('rows');
  if(!evals.length){
    rows.innerHTML='<tr><td class="empty" colspan="47">No evaluations yet - waiting for the next webhook.</td></tr>';
    return;
  }
  rows.innerHTML=evals.map(function(e){
    var vc=(e.verdict||'').indexOf('READY')>=0?'v-ready':'v-wait';
    return '<tr>'+
      '<td>'+etTime(e.webhookReceivedAt)+'</td>'+
      '<td>'+(e.instrument||'-')+'</td>'+
      '<td class="'+vc+'">'+(e.verdict||'-')+'</td>'+
      '<td>'+(e.direction||'-')+'</td>'+
      '<td style="text-align:left">'+(e.setupType||'-')+'</td>'+
      '<td class="'+(e.isDuplicate?'warn':'muted')+'">'+(e.isDuplicate?'dup':'-')+'</td>'+
      '<td>'+etTime(e.evaluationStartedAt)+'</td>'+
      '<td>'+etTime(e.evaluationFinishedAt)+'</td>'+
      '<td>'+ms(e.evaluationDurationMs)+'</td>'+
      '<td>'+ms(e.totalLatencyMs)+'</td>'+
      '<td>'+ms(e.indicatorCalcMs)+'</td>'+
      '<td>'+ms(e.volatilityCalcMs)+'</td>'+
      '<td>'+ms(e.scoringMs)+'</td>'+
      '<td>'+ms(e.aiNotesMs)+'</td>'+
      '<td>'+ms(e.screenshotMs)+'</td>'+
      '<td>'+ms(e.journalWriteMs)+'</td>'+
      '<td>'+etTime(e.alertSentAt)+'</td>'+
      '<td class="'+delayClass(e.totalAlertDelayMs)+'">'+ms(e.totalAlertDelayMs)+'</td>'+
      '<td>'+num(e.currentATR)+'</td>'+
      '<td>'+num(e.baselineATR)+'</td>'+
      '<td>'+num(e.volatilityMultiplier)+'</td>'+
      '<td>'+num(e.volatilityThreshold)+'</td>'+
      '<td style="text-align:left">'+(e.volatilityDecision||'-')+'</td>'+
      '<td class="'+(e.alertLevel==='READY'?'v-ready':(e.alertLevel?'warn':'muted'))+'">'+(e.alertLevel||'-')+'</td>'+
      '<td>'+intOrDash(e.edgeScore)+'</td>'+
      '<td>'+confs(e)+'</td>'+
      '<td style="text-align:left">'+(e.gateBlockers||'-')+'</td>'+
      '<td class="'+(e.alertSent?'good':'muted')+'">'+(e.alertSent?'yes':'no')+'</td>'+
      '<td>'+ms(e.cooldownRemainingMs)+'</td>'+
      '<td class="'+(e.suppressedByCooldown?'warn':'muted')+'">'+(e.suppressedByCooldown?'yes':'-')+'</td>'+
      '<td>'+etTime(e.eventStartTime)+'</td>'+
      '<td>'+etTime(e.sweepTime)+'</td>'+
      '<td>'+etTime(e.chochTime)+'</td>'+
      '<td>'+etTime(e.displacementTime)+'</td>'+
      '<td>'+etTime(e.earlyAlertTime)+'</td>'+
      '<td>'+etTime(e.readyAlertTime)+'</td>'+
      '<td>'+(e.alertDelaySeconds==null?'-':Number(e.alertDelaySeconds).toFixed(1))+'</td>'+
      '<td class="'+(e.waitedForCandleClose===true?'bad':(e.waitedForCandleClose===false?'good':'muted'))+'">'+(e.waitedForCandleClose===true?'yes':(e.waitedForCandleClose===false?'no':'-'))+'</td>'+
      '<td class="'+(e.trigger==='heartbeat'?'muted':'')+'">'+(e.trigger||'webhook')+'</td>'+
      '<td>'+yn(e.zoneValid)+'</td>'+
      '<td>'+yn(e.vwapConfirmed)+'</td>'+
      '<td>'+yn(e.structureConfirmed)+'</td>'+
      '<td>'+yn(e.candleConfirmed)+'</td>'+
      '<td>'+yn(e.liquidityConfirmed)+'</td>'+
      '<td>'+yn(e.volatilityConfirmed)+'</td>'+
      '<td>'+pct(e.confidenceScore)+'</td>'+
      '<td style="text-align:left">'+(e.waitReason||'-')+'</td>'+
    '</tr>';
  }).join('');
}
refresh();
setInterval(refresh,1000);
</script>
</body>
</html>"""


@app.route("/eval-metrics", methods=["GET"])
def get_eval_metrics():
    """Owner-only JSON feed of the last EVAL_METRICS_MAX scored evaluations
    (newest first). Each record carries the per-phase timings, the webhook->alert
    delay and the volatility reading. Powers the live Diagnostics page. The "stats"
    block carries the cumulative counters, current session, last-webhook timing and
    the heartbeat evaluation cadence."""
    _now = now_utc()
    _hour_ago = _now - timedelta(hours=1)
    with EVAL_METRICS_LOCK:
        snapshot = list(EVAL_METRICS)
    with COUNTERS_LOCK:
        counters = {
            "webhooks_received":      COUNTERS["webhooks_received"],
            "evaluations_run":        COUNTERS["evaluations_run"],
            "ready_setups_detected":  COUNTERS["ready_setups_detected"],
            "alerts_sent":            COUNTERS["alerts_sent"],
            "duplicates_ignored":     COUNTERS["duplicates_ignored"],
            "signals_passed_filters": COUNTERS["signals_passed_filters"],
            "signals_rejected":       COUNTERS["signals_rejected"],
            "wait_reasons_breakdown": dict(COUNTERS["wait_reasons_breakdown"]),
            # Canonical rejection reasons (req 6): always expose all keys (0 default)
            # so the operator sees the full checklist, incl. session_filter pinned at
            # 0 (bonus, never a gate). conflicting_structure is surfaced too.
            "rejection_reasons":      {
                _k: COUNTERS["rejection_reasons"].get(_k, 0)
                for _k in REJECTION_REASON_KEYS
            },
        }
        # Trim + count the rolling last-hour timestamp windows under the same lock.
        while _WEBHOOK_TS and _WEBHOOK_TS[0] < _hour_ago:
            _WEBHOOK_TS.popleft()
        while _DUP_TS and _DUP_TS[0] < _hour_ago:
            _DUP_TS.popleft()
        alerts_received_last_hour    = len(_WEBHOOK_TS)
        duplicates_ignored_last_hour = len(_DUP_TS)
    # Average processing latency over the recorded window (durations are stationary,
    # so the capped EVAL_METRICS window is representative even when heartbeat-heavy).
    _durs = [r.get("evaluationDurationMs") for r in snapshot
             if isinstance(r.get("evaluationDurationMs"), (int, float))]
    avg_processing_ms = round(sum(_durs) / len(_durs), 3) if _durs else None
    # Per-instrument setup-state snapshot (display-only; strip internal datetimes).
    with STATE_LOCK:
        setup_states = {
            k: {"state": v.get("state"), "direction": v.get("direction"),
                "since": v.get("since"), "lastUpdate": v.get("last_update")}
            for k, v in SETUP_STATE.items()
        }
    last_wh   = LAST_WEBHOOK_AT
    since_sec = round((_now - last_wh).total_seconds(), 1) if last_wh else None
    stats = {
        "counters":                   counters,
        "session":                    get_session_state(),
        "lastWebhookReceived":        last_wh.isoformat() if last_wh else None,
        "timeSinceLastWebhookSec":    since_sec,
        "evaluationFrequencySeconds": EVAL_HEARTBEAT_INTERVAL,
        "evalHeartbeatEnabled":       EVAL_HEARTBEAT_ENABLED,
        # ── 1-min-repeat handling diagnostics (additive) ──
        "signalsReceived":            counters["webhooks_received"],
        "signalsPassedFilters":       counters["signals_passed_filters"],
        "signalsRejected":            counters["signals_rejected"],
        "duplicatesIgnored":          counters["duplicates_ignored"],
        "alertsReceivedLastHour":     alerts_received_last_hour,
        "duplicatesIgnoredLastHour":  duplicates_ignored_last_hour,
        "averageProcessingTimeMs":    avg_processing_ms,
        "currentQueueLength":         _WEBHOOK_JOBS.qsize(),
        "signalDedupCooldownSec":     SIGNAL_DEDUP_COOLDOWN_SEC,
        "signalDedupCooldownMNQSec":  signal_dedup_cooldown_sec("MNQ"),
        "signalDedupCooldownMGCSec":  signal_dedup_cooldown_sec("MGC"),
        "setupStateTtlSec":           SETUP_STATE_TTL_SEC,
        "setupStates":                setup_states,
    }
    return jsonify({
        "evaluations": snapshot[::-1],
        "count":       len(snapshot),
        "max":         EVAL_METRICS_MAX,
        "stats":       stats,
    }), 200


@app.route("/diagnostics-live", methods=["GET"])
def diagnostics_live():
    """Owner-only (dashboard password, enforced by the Express proxy) live
    Diagnostics page: per-evaluation timing + volatility metrics for the last
    100 scored alerts, auto-refreshing every second from /eval-metrics."""
    return Response(DIAGNOSTICS_LIVE_HTML, mimetype="text/html")


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
        "LONG EARLY READY":      "Trade Ready",
        "SHORT EARLY READY":     "Trade Ready",
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
        "bos_type":         str(data.get("bos_type") or ("Demand" if direction == "Long" else "Supply")),
        "choch_type":       str(data.get("choch_type") or ("Bullish" if direction == "Long" else "Bearish")),
        "bos_level":        data.get("bos_level") or data.get("bos"),
        "choch_level":      data.get("choch_level") or data.get("choch"),
        "strict_score":     data.get("strict_score") or data.get("score"),
        "why_qualifies":    str(data.get("why_qualifies") or data.get("why") or data.get("next_step") or "—"),
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

    # ── Additive fields (parity with auto-journal entries) ──
    entry["screenshot_url"] = extract_screenshot_url(data)
    if entry["screenshot_url"]:
        entry["screenshot"] = entry["screenshot_url"]
    entry["vwap_position"]      = str(data.get("vwap_position") or data.get("vwap") or "—")
    entry["supply_demand_zone"] = str(data.get("supply_demand_zone") or data.get("zone") or "—")
    entry["bos_status"] = str(data.get("bos_status") or (
        f"BOS {entry['bos_type']} @ {entry['bos_level']}" if entry.get("bos_level") else "None"))
    entry["choch_status"] = str(data.get("choch_status") or (
        f"{entry['choch_type']} CHOCH @ {entry['choch_level']}" if entry.get("choch_level") else "None"))
    entry["setup_categories"] = classify_setup_categories(entry)
    entry["setup_notes"]  = str(data.get("setup_notes") or entry["why_qualifies"] or "—")
    entry["trade_thesis"] = str(data.get("trade_thesis") or build_trade_thesis({}, entry))

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
    CURRENT_PRICE_BY_TICKER.clear()
    CURRENT_PRICE_TS_BY_TICKER.clear()
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
    _sw = cfg("SCORE_WINDOW_MIN")
    bullish, bearish, counts = score_alerts(alerts_in_window(_sw)) if _sw else calculate_scores()
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
    # The dashboard's MGC/MNQ tab passes ?ticker= so the view follows the selected
    # instrument; ignore junk values and fall back to the active instrument.
    _raw = (request.args.get("ticker") or "").upper()
    _tk  = instrument_of(_raw) if ("MGC" in _raw or "MNQ" in _raw) else None
    a = full_analysis(ticker_override=_tk)
    windows = {}
    for label, minutes in TIME_WINDOWS.items():
        w_counts, w_total = window_summary(minutes)
        w_bull, w_bear, _ = score_alerts(alerts_in_window(minutes))
        windows[label] = {"alert_counts": w_counts, "total": w_total,
                          "bullish_score": w_bull, "bearish_score": w_bear}
    return jsonify({
        "status":              "running",
        "version":             "11.0",
        "trading_mode":        TRADING_MODE,
        "verdict":             a["verdict"],
        "strict_label":        a.get("strict_label"),
        "strict_score":        a.get("strict_score"),
        "strict_direction":    a.get("strict_direction"),
        "strict_reason":       a.get("strict_reason"),
        "strict_missing":      a.get("strict_missing"),
        "gate_debug":          a.get("gate_debug"),
        "confluences":         a.get("confluences"),
        "directions":          a.get("directions"),
        "vwap_value":          a.get("vwap_value"),
        "vwap_status":         a.get("vwap_status"),
        "vwap_source":         (VWAP_BY_TICKER.get(a.get("active_ticker")) or {}).get("source"),
        "active_ticker":       a.get("active_ticker"),
        "volatility":          a.get("volatility"),
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
        "edge_grade":          a.get("edge_grade"),
        "edge_breakdown":      a.get("edge_breakdown"),
        "conviction_tier":     a.get("conviction_tier"),
        "long_score":          a.get("long_score"),
        "short_score":         a.get("short_score"),
        "conflict_gap":        a.get("conflict_gap"),
        "dominant_direction":  a.get("dominant_direction"),
        "current_atr":         a.get("current_atr"),
        "volatility_multiplier": a.get("volatility_multiplier"),
        "ready_reason":        a.get("ready_reason"),
        "rejected_reasons":    a.get("rejected_reasons"),
        "alert_diagnostics":   a.get("alert_diagnostics"),
        "decision_support":    a.get("decision_support"),
        "alert_level":         a.get("alert_level"),
        "session_preferred":   (a.get("session") or {}).get("preferred"),
        "session_bonus":       (a.get("session") or {}).get("bonus"),
        "session_window":      (a.get("session") or {}).get("window"),
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
        "display_price":       a.get("display_price"),
        "price_source":        a.get("price_source"),
        "nearest_supply":      a["nearest_supply"],
        "nearest_demand":      a["nearest_demand"],
        "longs_allowed":       a["plan"]["longs_allowed"],
        "shorts_allowed":      a["plan"]["shorts_allowed"],
        "action":              a["plan"]["action"],
        "warning":             a["plan"]["warning"],
        "alert_counts":        a["counts"],
        "windows":             windows,
        "total_alerts_stored": len(ALERT_HISTORY),
        "market_open":         a.get("market_open"),
        "market_status":       a.get("market_status"),
        "next_open":           a.get("next_open"),
        "market_reason":       a.get("market_reason"),
        "last_valid_price":    a.get("last_valid_price"),
        "last_valid_time":     a.get("last_valid_time"),
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
        if not tp.get("trade_plan") or not tp.get("entry_zone"):
            return jsonify({"status": "error", "reason": "No active trade plan. Send entry/stop/t1/t2 or trigger a Short Ready / Long Ready setup first."}), 400
        try:
            zone = str(tp["entry_zone"])
            if "–" in zone:
                lo_s, hi_s = zone.split("–")
                entry     = (float(lo_s) + float(hi_s)) / 2
            else:
                entry     = float(zone)
            stop      = float(tp["stop_loss"])
            t1        = float(tp["target1"])
            t2        = float(tp["target2"])
            direction = str(tp["direction"])
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify({"status": "error", "reason": str(exc)}), 400
        profile   = str(data.get("profile", DEFAULT_PROFILE))
        acct_size = ACCOUNT_PROFILES.get(profile, {}).get("account_size", DEFAULT_ACCOUNT_SIZE)
        risk_pct  = ACCOUNT_PROFILES.get(profile, {}).get("risk_pct", DEFAULT_RISK_PCT)
        # BOS-only ("Attempt") entries trade at reduced size (mode-dependent).
        _risk_mult = (cfg("RISK_MULT_ATTEMPT")
                      if a.get("structure_class") in ("Bullish Attempt", "Bearish Attempt")
                      else 1.0)
        sz        = calculate_position_sizing(tp, acct_size, risk_pct * _risk_mult, profile)
        contracts = int(sz.get("contracts", 1)) if sz else 1

    symbol = instrument_of(profile)

    # Record the trade for local tracking. _ENTER_LOCK serialises the
    # assignment so two concurrent ENTERs can't race on ACTIVE_TRADE.
    with _ENTER_LOCK:
        ACTIVE_TRADE = {
            "direction":   direction,
            "entry_price": entry,
            "stop_loss":   stop,
            "target1":     t1,
            "target2":     t2,
            "contracts":   contracts,
            "profile":     profile,
            "symbol":      symbol,
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


@app.route("/weekly", methods=["GET", "POST"])
def weekly_trigger():
    """Manually trigger / preview the weekly performance report.

    POST posts the embed to Discord and returns the computed stats. GET only
    computes and returns the stats (no Discord post) so the report can be
    previewed without spamming channels. Optional ?days=N overrides the window.
    """
    try:
        days = int(request.args.get("days", WEEKLY_REPORT_DAYS))
    except (TypeError, ValueError):
        days = WEEKLY_REPORT_DAYS
    if request.method == "POST":
        stats = _send_weekly_report(days)
        return jsonify({"status": "sent", "stats": stats}), 200
    return jsonify({"status": "preview", "stats": _compute_weekly_stats(days)}), 200


@app.route("/why", methods=["GET"])
@app.route("/why/<ticker>", methods=["GET"])
def why_endpoint(ticker=None):
    """Explain WHY the current/last READY setup qualifies (Feature 6).

    Prefers the stored READY snapshot (LAST_READY_BY_TICKER); if none exists yet,
    falls back to a fresh full_analysis() for the instrument, read defensively.
    Optional ?ticker=MGC|MNQ or /why/<ticker>; defaults to the active instrument.
    """
    raw        = ticker or request.args.get("ticker") or _active_ticker() or "MGC"
    instrument = instrument_of(raw)
    snapshot   = LAST_READY_BY_TICKER.get(instrument)
    source     = "snapshot"
    entry      = snapshot

    if entry is None:
        source = "live"
        try:
            a = full_analysis(ticker_override=instrument)
            entry = _build_card_entry(a, ticker=instrument)
        except Exception as exc:
            logger.error("/why fallback analysis error: %s", exc)
            return jsonify({"ticker": instrument, "instrument": instrument,
                            "source": "live", "error": "analysis unavailable"}), 200

    try:
        explanation = _build_why_explanation(entry)
    except Exception as exc:
        logger.error("/why render error: %s", exc)
        return jsonify({"ticker": instrument, "instrument": instrument,
                        "source": source, "error": "explanation unavailable"}), 200

    explanation["ticker"]     = entry.get("symbol", instrument)
    explanation["instrument"] = instrument
    explanation["source"]     = source
    return jsonify(explanation), 200


@app.route("/dashboard", methods=["GET"])
def dashboard():
    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>AI Trading Partner</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#0a0a0f;color:#e8e8f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh;padding:16px}
  h1{font-size:18px;font-weight:700;text-align:center;padding:12px 0 18px;color:#a0a8ff;letter-spacing:.5px}
  /* Status card */
  #status-card{background:#12121e;border:1px solid #1e1e32;border-radius:16px;padding:18px;margin-bottom:18px;min-height:100px}
  #status-label{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#555;margin-bottom:8px}
  #trade-info{font-size:22px;font-weight:700}
  #trade-detail{font-size:13px;color:#888;margin-top:6px;line-height:1.6}
  #pnl{font-size:26px;font-weight:800;margin-top:8px}
  .pnl-pos{color:#22c55e} .pnl-neg{color:#ef4444} .pnl-flat{color:#888}
  /* Tabs */
  .tabs{display:flex;gap:8px;margin-bottom:14px}
  .tab{flex:1;padding:12px;border-radius:12px;border:2px solid #1e1e32;background:#12121e;color:#888;font-size:15px;font-weight:600;cursor:pointer;text-align:center;transition:all .15s}
  .tab.active{border-color:#a0a8ff;color:#a0a8ff;background:#16162a}
  /* Direction toggle */
  .dir-row{display:flex;gap:8px;margin-bottom:16px}
  .dir-btn{flex:1;padding:14px;border-radius:12px;border:2px solid #1e1e32;background:#12121e;font-size:16px;font-weight:700;cursor:pointer;transition:all .15s;color:#888}
  .dir-btn.long.active{border-color:#22c55e;color:#22c55e;background:#0d1f14}
  .dir-btn.short.active{border-color:#ef4444;color:#ef4444;background:#1f0d0d}
  .dir-btn .rec-tag{display:none;font-size:10px;font-weight:800;letter-spacing:.5px;opacity:.9;margin-left:6px}
  .dir-btn.long.rec{box-shadow:0 0 12px rgba(34,197,94,.55);border-color:#22c55e}
  .dir-btn.short.rec{box-shadow:0 0 12px rgba(239,68,68,.55);border-color:#ef4444}
  .dir-btn.rec .rec-tag{display:inline}
  /* Overrides */
  details{margin-bottom:16px}
  summary{font-size:13px;color:#555;cursor:pointer;padding:6px 0;user-select:none;list-style:none}
  summary::-webkit-details-marker{display:none}
  summary::before{content:'▶ ';font-size:10px}
  details[open] summary::before{content:'▼ '}
  .fields{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}
  .field{display:flex;flex-direction:column;gap:4px}
  .field label{font-size:11px;color:#555;text-transform:uppercase;letter-spacing:.5px}
  .field input{background:#0d0d18;border:1px solid #1e1e32;border-radius:8px;color:#e8e8f0;font-size:15px;padding:10px 12px;width:100%;outline:none}
  .field input:focus{border-color:#a0a8ff}
  /* Buttons */
  .btn{width:100%;padding:18px;border-radius:14px;border:none;font-size:18px;font-weight:800;cursor:pointer;margin-bottom:10px;transition:all .1s;letter-spacing:.3px}
  .btn:active{transform:scale(.97)}
  .btn-enter{background:#22c55e;color:#fff}
  .btn-enter.short{background:#ef4444}
  .btn-close{background:#f59e0b;color:#000}
  .btn-be{background:#1e1e32;color:#a0a8ff;border:2px solid #a0a8ff;font-size:15px;padding:14px}
  .btn-eod{background:#1e1e32;color:#888;border:1px solid #2a2a40;font-size:13px;padding:12px;margin-top:6px}
  .btn:disabled{opacity:.4;cursor:not-allowed}
  /* Recommendation card */
  #rec-card{background:#12121e;border:1px solid #1e1e32;border-radius:16px;padding:18px;margin-bottom:18px;transition:border-color .3s}
  .rec-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
  #rec-label{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#555}
  .rec-badge{font-size:13px;font-weight:800;padding:5px 12px;border-radius:20px;background:#1e1e32;color:#888;letter-spacing:.5px}
  .rec-badge.ready-long{background:#0d1f14;color:#22c55e;border:1px solid #22c55e}
  .rec-badge.ready-short{background:#1f0d0d;color:#ef4444;border:1px solid #ef4444}
  .rec-badge.early-long{background:#10180d;color:#a3e635;border:1px solid #4d7c0f}
  .rec-badge.early-short{background:#180d10;color:#fb923c;border:1px solid #7c4d0f}
  .rec-badge.wait{background:#1f1a0d;color:#f59e0b;border:1px solid #5a4a1a}
  #rec-meta{font-size:12px;color:#888;margin-bottom:12px;line-height:1.6}
  .rec-gauge{position:relative;width:220px;margin:2px auto 14px;text-align:center}
  .gauge-svg{width:220px;height:auto;display:block;transition:filter .3s}
  .gauge-center{position:absolute;top:50px;left:0;right:0;text-align:center;pointer-events:none}
  .gauge-prob{font-size:22px;font-weight:800;line-height:1}
  .gauge-conf{font-size:10px;color:#9aa0b5;margin-top:3px;letter-spacing:.6px;text-transform:uppercase}
  .gauge-dir{font-size:12px;font-weight:700;margin-top:3px}
  .gauge-scores{font-size:11px;color:#9aa0b5;font-family:ui-monospace,monospace;margin-top:6px;line-height:1.5}
  #g-needle{transition:transform .5s cubic-bezier(.34,1.56,.64,1)}
  .rec-gauge.glow .gauge-svg{filter:drop-shadow(0 0 9px rgba(34,197,94,.8))}
  .rec-score-wrap{height:8px;background:#0d0d18;border-radius:6px;overflow:hidden;margin-bottom:4px}
  #rec-score-bar{height:100%;width:0;background:#f59e0b;border-radius:6px;transition:width .4s,background .4s}
  #rec-score-num{font-size:11px;color:#666;text-align:right;margin-bottom:12px}
  .rec-checklist{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px}
  .rec-item{display:flex;align-items:center;gap:8px;font-size:13px;padding:8px 10px;border-radius:8px;background:#0d0d18}
  .rec-item .ck{font-size:13px;width:16px;text-align:center}
  .rec-item.ok{color:#cfe9d6} .rec-item.no{color:#6a6a7a}
  .rec-plan{font-size:13px;color:#bbb;line-height:1.8;background:#0d0d18;border-radius:10px;padding:12px;margin-bottom:10px;display:none}
  .rec-plan b{color:#e8e8f0}
  .rec-reason{font-size:12px;color:#888;line-height:1.5;font-style:italic}
  .btn-apply{background:#16162a;color:#a0a8ff;border:2px solid #a0a8ff;font-size:14px;padding:13px;margin-top:12px;margin-bottom:0}
  /* Diagnostics modules */
  .mod{background:#12121e;border:1px solid #1e1e32;border-radius:16px;padding:16px;margin-bottom:14px}
  .mod-h{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#6b7280;margin-bottom:12px;font-weight:700}
  .gauge-wrap{position:relative;width:100%;max-width:320px;margin:0 auto}
  .mgauge-center{position:absolute;left:0;right:0;bottom:24%;text-align:center;pointer-events:none}
  .mgauge-prob{font-size:21px;font-weight:800;line-height:1;letter-spacing:0}
  .gauge-sub{font-size:10px;color:#9aa0b4;margin-top:3px;font-weight:600;letter-spacing:.3px}
  .gauge-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:14px}
  .gstat{background:#0d0d18;border-radius:10px;padding:9px 6px;text-align:center}
  .gstat .l{font-size:9px;text-transform:uppercase;letter-spacing:.6px;color:#6b7280}
  .gstat .v{font-size:15px;font-weight:800;margin-top:3px}
  .sg-row{margin-bottom:10px}
  .sg-row:last-child{margin-bottom:0}
  .sg-top{display:flex;justify-content:space-between;font-size:12px;margin-bottom:5px;color:#cfd2e0;font-weight:600}
  .sg-track{height:14px;background:#0d0d18;border-radius:8px;overflow:hidden}
  .sg-fill{height:100%;border-radius:8px;transition:width .4s}
  .sg-fill.long{background:linear-gradient(90deg,#15803d,#22c55e)}
  .sg-fill.short{background:linear-gradient(90deg,#991b1b,#ef4444)}
  .ai-ck{display:grid;grid-template-columns:1fr 1fr;gap:8px}
  .ai-item{display:flex;align-items:center;gap:8px;font-size:12px;padding:8px 10px;border-radius:8px;background:#0d0d18;color:#9aa0b4}
  .ai-item .ic{width:16px;text-align:center;font-weight:800}
  .ai-item.ok{color:#cfe9d6} .ai-item.ok .ic{color:#22c55e}
  .ai-item.no .ic{color:#ef4444}
  .cd-big{font-size:30px;font-weight:800;text-align:center;letter-spacing:-1px}
  .cd-sub{font-size:12px;color:#9aa0b4;text-align:center;margin-top:4px}
  .cd-track{height:10px;background:#0d0d18;border-radius:6px;overflow:hidden;margin-top:12px}
  .cd-fill{height:100%;border-radius:6px;transition:width .4s,background .4s}
  .wn-item{display:flex;align-items:flex-start;gap:8px;font-size:13px;color:#e7c0c0;background:#1a0f0f;border:1px solid #3a1b1b;border-radius:8px;padding:9px 11px;margin-bottom:8px;line-height:1.4}
  .wn-item:last-child{margin-bottom:0}
  .wn-ok{font-size:13px;color:#cfe9d6;background:#0d1f14;border:1px solid #1b3a26;border-radius:8px;padding:11px;line-height:1.5}
  /* Toast */
  #toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1e1e32;color:#e8e8f0;padding:12px 24px;border-radius:10px;font-size:14px;opacity:0;transition:opacity .3s;pointer-events:none;white-space:nowrap}
  #toast.show{opacity:1}
  #refresh-dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:#22c55e;margin-right:6px;animation:pulse 2s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
  #last-updated{font-size:12px;color:#6b7280;margin:-6px 0 16px 14px}
  #last-updated.stale{color:#f59e0b}
  /* Sensitivity (trading mode) toggle */
  #mode-row{display:flex;align-items:center;gap:10px;margin-bottom:18px}
  #mode-cap{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:#555;white-space:nowrap}
  .mode-seg{display:flex;flex:1;background:#0d0d18;border:1px solid #1e1e32;border-radius:12px;padding:4px;gap:4px}
  .mode-btn{flex:1;text-align:center;font-size:12px;font-weight:700;padding:9px 6px;border-radius:9px;color:#6a6a7a;cursor:pointer;transition:all .15s;letter-spacing:.3px}
  #mode-scalp.active{background:#3a2a0d;color:#f5b342;box-shadow:inset 0 0 0 1px #5a4a1a}
  #mode-swing.active{background:#16203a;color:#9ec5ff;box-shadow:inset 0 0 0 1px #2a3a5a}
</style>
</head>
<body>
<h1><span id="refresh-dot"></span>🤖 AI Trading Partner</h1>
<div id="last-updated">Last updated —</div>

<!-- Sensitivity (trading mode) -->
<div id="mode-row">
  <span id="mode-cap">Sensitivity</span>
  <div class="mode-seg">
    <div class="mode-btn" id="mode-scalp" onclick="setMode('SCALP')">SCALP · Sensitive</div>
    <div class="mode-btn" id="mode-swing" onclick="setMode('SWING')">SWING · Strict</div>
  </div>
</div>

<!-- Status card -->
<div id="status-card">
  <div id="status-label">Current Status</div>
  <div id="trade-info">Loading…</div>
  <div id="trade-detail"></div>
  <div id="pnl"></div>
</div>

<!-- Live recommendation -->
<div id="rec-card">
  <div class="rec-head">
    <span id="rec-label">Live Recommendation</span>
    <span id="rec-badge" class="rec-badge wait">…</span>
  </div>
  <div id="rec-meta"></div>
  <!-- Trade Probability Gauge (speedometer / RPM style) — fed by /status fields -->
  <div id="rec-gauge" class="rec-gauge">
    <svg viewBox="0 0 200 116" class="gauge-svg" aria-hidden="true">
      <path id="g-band-red"    fill="none" stroke="#ef4444" stroke-width="16"></path>
      <path id="g-band-yellow" fill="none" stroke="#eab308" stroke-width="16"></path>
      <path id="g-band-green"  fill="none" stroke="#22c55e" stroke-width="16"></path>
      <g id="g-needle">
        <line x1="100" y1="100" x2="100" y2="36" stroke="#e8e8f0" stroke-width="3" stroke-linecap="round"></line>
      </g>
      <circle cx="100" cy="100" r="6" fill="#e8e8f0"></circle>
    </svg>
    <div class="gauge-center">
      <div id="g-prob" class="gauge-prob">—</div>
      <div id="g-conf" class="gauge-conf">—</div>
      <div id="g-dir"  class="gauge-dir">—</div>
    </div>
    <div id="g-scores" class="gauge-scores"></div>
  </div>
  <div class="rec-score-wrap"><div id="rec-score-bar"></div></div>
  <div id="rec-score-num"></div>
  <div id="rec-checklist" class="rec-checklist"></div>
  <div id="rec-plan" class="rec-plan"></div>
  <div id="rec-reason" class="rec-reason"></div>
  <button class="btn btn-apply" id="btn-apply" style="display:none" onclick="applyRec()">⬇️ Use This Setup</button>
</div>

<!-- Diagnostics modules (feed off alert_diagnostics) -->
<div class="mod" id="mod-prob">
  <div class="mod-h">⏱ Trade Probability Meter</div>
  <div class="gauge-wrap">
    <svg id="gauge-svg" viewBox="0 0 220 132" style="width:100%;display:block"></svg>
    <div class="mgauge-center">
      <div class="mgauge-prob" id="gauge-prob">—</div>
      <div class="gauge-sub" id="gauge-sub"></div>
    </div>
  </div>
  <div class="gauge-stats">
    <div class="gstat"><div class="l">Long</div><div class="v" id="gs-long" style="color:#22c55e">—</div></div>
    <div class="gstat"><div class="l">Short</div><div class="v" id="gs-short" style="color:#ef4444">—</div></div>
    <div class="gstat"><div class="l">Edge Δ</div><div class="v" id="gs-gap" style="color:#e8e8f0">—</div></div>
    <div class="gstat"><div class="l">Dominant</div><div class="v" id="gs-dom" style="color:#a0a8ff">—</div></div>
  </div>
</div>

<div class="mod" id="mod-scores">
  <div class="mod-h">⚖️ Long vs Short Score</div>
  <div class="sg-row">
    <div class="sg-top"><span>📈 Long</span><span id="sg-long-n">0</span></div>
    <div class="sg-track"><div class="sg-fill long" id="sg-long-f" style="width:0%"></div></div>
  </div>
  <div class="sg-row">
    <div class="sg-top"><span>📉 Short</span><span id="sg-short-n">0</span></div>
    <div class="sg-track"><div class="sg-fill short" id="sg-short-f" style="width:0%"></div></div>
  </div>
</div>

<div class="mod" id="mod-checklist">
  <div class="mod-h">🤖 AI Trade Checklist</div>
  <div class="ai-ck" id="ai-ck"></div>
</div>

<div class="mod" id="mod-countdown">
  <div class="mod-h">🎯 Setup Countdown</div>
  <div class="cd-big" id="cd-big">—</div>
  <div class="cd-sub" id="cd-sub"></div>
  <div class="cd-track"><div class="cd-fill" id="cd-fill" style="width:0%"></div></div>
</div>

<div class="mod" id="mod-whynot">
  <div class="mod-h">🚦 Why Not Ready</div>
  <div id="wn-body"></div>
</div>

<!-- Symbol tabs -->
<div class="tabs">
  <div class="tab active" onclick="setSymbol('MGC')">MGC (Gold)</div>
  <div class="tab" onclick="setSymbol('MNQ')">MNQ (Nasdaq)</div>
</div>

<!-- VWAP is fetched automatically; manual entry just overrides it temporarily -->
<details class="vwap-set">
  <summary>📌 VWAP for <span id="vwap-sym">MGC</span> updates automatically — tap to override</summary>
  <div class="fields">
    <div class="field"><label>VWAP value</label><input id="f-vwap" type="number" step="0.01" placeholder="auto — type to override"></div>
    <div class="field"><label>Current price (optional)</label><input id="f-vwap-price" type="number" step="0.1" placeholder="optional"></div>
  </div>
  <button class="btn" style="background:#16203a;color:#9ec5ff;border:1px solid #2a3a5a" onclick="setVwap()">Override VWAP</button>
  <div style="font-size:11px;color:#6b7280;margin-top:6px">A manual value holds for ~10 min, then auto resumes.</div>
</details>

<!-- Direction -->
<div class="dir-row">
  <div class="dir-btn long active" onclick="setDir('Long')">📈 LONG<span class="rec-tag">✓ READY</span></div>
  <div class="dir-btn short" onclick="setDir('Short')">📉 SHORT<span class="rec-tag">✓ READY</span></div>
</div>

<!-- Optional overrides -->
<details>
  <summary>Override entry levels (optional)</summary>
  <div class="fields">
    <div class="field"><label>Entry Price</label><input id="f-entry" type="number" step="0.1" placeholder="auto"></div>
    <div class="field"><label>Stop Loss</label><input id="f-stop" type="number" step="0.1" placeholder="auto"></div>
    <div class="field"><label>Target 1</label><input id="f-t1" type="number" step="0.1" placeholder="auto"></div>
    <div class="field"><label>Target 2</label><input id="f-t2" type="number" step="0.1" placeholder="auto"></div>
    <div class="field"><label>Contracts</label><input id="f-contracts" type="number" min="1" placeholder="1"></div>
  </div>
</details>

<!-- Action buttons -->
<button class="btn btn-enter" id="btn-enter" onclick="enterTrade()">📈 ENTER LONG</button>
<button class="btn btn-close" id="btn-close" style="display:none" onclick="closeTrade()">🏁 CLOSE TRADE</button>
<button class="btn btn-be" id="btn-be" style="display:none" onclick="breakeven()">⚖️ Move Stop to Breakeven</button>
<button class="btn btn-eod" onclick="sendEod()">📊 Send EOD Summary Now</button>
<a class="btn btn-eod" href="/api/diagnostics-live" target="_blank" rel="noopener" style="display:block;text-align:center;text-decoration:none">🩺 Diagnostics (live metrics)</a>

<div id="toast"></div>

<script>
const BASE = '/api';
let sym = 'MGC', dir = 'Long', activeTrade = null;

function setSymbol(s) {
  sym = s;
  document.querySelectorAll('.tab').forEach((t,i)=>t.classList.toggle('active', (i===0&&s==='MGC')||(i===1&&s==='MNQ')));
  const vs = document.getElementById('vwap-sym');
  if (vs) vs.textContent = s;
  updateEnterBtn();
  refreshRec();   // switch the displayed analysis to the selected instrument now
}
function setDir(d) {
  dir = d;
  document.querySelectorAll('.dir-btn').forEach(b=>b.classList.remove('active'));
  document.querySelector('.dir-btn.'+(d==='Long'?'long':'short')).classList.add('active');
  updateEnterBtn();
  // instant: re-render the meter AND the analysis panel for the selected side (no refetch)
  if (lastRec) renderGauge(lastRec);
  renderDirView();
}
function updateEnterBtn() {
  const b = document.getElementById('btn-enter');
  b.textContent = dir==='Long' ? '📈 ENTER LONG' : '📉 ENTER SHORT';
  b.className = 'btn btn-enter' + (dir==='Short'?' short':'');
}
function toast(msg, ok=true) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.background = ok ? '#0d1f14' : '#1f0d0d';
  t.style.borderLeft = '3px solid ' + (ok ? '#22c55e' : '#ef4444');
  t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'), 2800);
}

async function api(path, body=null) {
  const opts = { method: body ? 'POST' : 'GET', headers: {'Content-Type':'application/json'} };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(BASE+path, opts);
  return r.json();
}

async function enterTrade() {
  const body = {
    alert_type: sym+' ENTER',
    ticker: sym+'1!',
    direction: dir,
    profile: sym+' Standard',
  };
  const e = document.getElementById('f-entry').value;
  const s = document.getElementById('f-stop').value;
  const t1 = document.getElementById('f-t1').value;
  const t2 = document.getElementById('f-t2').value;
  const c  = document.getElementById('f-contracts').value;
  if (e)  body.entry     = parseFloat(e);
  if (s)  body.stop      = parseFloat(s);
  if (t1) body.t1        = parseFloat(t1);
  if (t2) body.t2        = parseFloat(t2);
  if (c)  body.contracts = parseInt(c);
  try {
    const d = await api('/enter', body);
    if (d.status === 'entered') {
      toast('✅ Trade entered!'); refresh();
    }
    else toast('Error: '+(d.reason||d.status), false);
  } catch(err) { toast('Request failed', false); }
}

async function setVwap() {
  const v = document.getElementById('f-vwap').value;
  const p = document.getElementById('f-vwap-price').value;
  if (!v) { toast('Enter a VWAP value first', false); return; }
  const body = { alert_type: sym+' VWAP', ticker: sym+'1!', vwap: parseFloat(v) };
  if (p) body.price = parseFloat(p);
  try {
    const d = await api('/webhook', body);
    if (d.status === 'vwap_updated') { toast('✅ VWAP set for '+sym); refresh(); }
    else toast('Error: '+(d.reason||d.status), false);
  } catch(err) { toast('Request failed', false); }
}

async function closeTrade() {
  try {
    const d = await api('/close', {});
    if (d.status === 'closed') { toast('🏁 Trade closed'); refresh(); }
    else toast('Error: '+(d.reason||d.status), false);
  } catch(err) { toast('Request failed', false); }
}

async function breakeven() {
  try {
    const d = await api('/breakeven', {});
    if (d.status === 'breakeven_set') { toast('⚖️ Stop moved to breakeven'); refresh(); }
    else toast('Error: '+(d.reason||d.status), false);
  } catch(err) { toast('Request failed', false); }
}

async function sendEod() {
  try {
    await api('/eod', {});
    toast('📊 EOD summary sent to Discord');
  } catch(err) { toast('Request failed', false); }
}

function paintMode(m) {
  const sc = document.getElementById('mode-scalp');
  const sw = document.getElementById('mode-swing');
  if (!sc || !sw) return;
  sc.classList.toggle('active', m === 'SCALP');
  sw.classList.toggle('active', m === 'SWING');
}
async function loadMode() {
  try { const d = await api('/mode'); if (d.trading_mode) paintMode(d.trading_mode); } catch(e) {}
}
async function setMode(m) {
  try {
    const d = await api('/mode', { mode: m });
    if (d.trading_mode) {
      paintMode(d.trading_mode);
      toast(m === 'SCALP' ? '⚡ SCALP — more sensitive' : '🎯 SWING — stricter');
      refreshRec();
    } else toast('Error: '+(d.reason||d.status), false);
  } catch(e) { toast('Request failed', false); }
}

let lastRec = null;
function ckItem(label, ok) {
  return '<div class="rec-item '+(ok?'ok':'no')+'"><span class="ck">'+(ok?'✅':'⬜')+'</span>'+label+'</div>';
}

// ── Verdict helpers (mirror the Python is_full_ready / is_early_ready / is_actionable
//    / ready_direction so EARLY READY is treated as actionable but labelled). ──
function jsIsFullReady(v){ return v==='LONG READY' || v==='SHORT READY'; }
function jsIsEarlyReady(v){ return v==='LONG EARLY READY' || v==='SHORT EARLY READY'; }
function jsIsActionable(v){ return jsIsFullReady(v) || jsIsEarlyReady(v); }
function jsReadyDir(v){
  if(!jsIsActionable(v)) return null;
  return /LONG/.test(v) ? 'Long' : (/SHORT/.test(v) ? 'Short' : null);
}
// Score → quality band, mirroring the Python _score_tier + _decision_support quality
// mapping so a per-side gauge reads the same wording as the authoritative header.
function jsTierForScore(s){
  s = Number(s)||0;
  if(s>=70) return 'HIGH CONVICTION';
  if(s>=50) return 'READY';
  if(s>=35) return 'EARLY READY';
  return null;
}
function jsQualityForScore(s){
  return ({'HIGH CONVICTION':'HIGH','READY':'MODERATE','EARLY READY':'SPECULATIVE'})[jsTierForScore(s)] || 'LOW';
}

// ── Trade Probability Gauge (speedometer) ────────────────────────────────────
// Semi-circle 0-100 with a needle, red 0-40 / yellow 41-69 / green 70-100 bands,
// and a deep-green glow on a HIGH-QUALITY full READY. Reads /status fields only.
function gaugeArc(v0, v1, r){
  var cx=100, cy=100;
  var a0=Math.PI*(1 - v0/100), a1=Math.PI*(1 - v1/100);
  var x0=(cx + r*Math.cos(a0)).toFixed(2), y0=(cy - r*Math.sin(a0)).toFixed(2);
  var x1=(cx + r*Math.cos(a1)).toFixed(2), y1=(cy - r*Math.sin(a1)).toFixed(2);
  return 'M '+x0+' '+y0+' A '+r+' '+r+' 0 0 1 '+x1+' '+y1;
}
var gaugeBandsDrawn=false;
function drawGaugeBands(){
  if(gaugeBandsDrawn) return;
  var R=80;
  var r=document.getElementById('g-band-red');
  var y=document.getElementById('g-band-yellow');
  var g=document.getElementById('g-band-green');
  if(!r||!y||!g) return;
  r.setAttribute('d', gaugeArc(0,40,R));
  y.setAttribute('d', gaugeArc(40,70,R));
  g.setAttribute('d', gaugeArc(70,100,R));
  gaugeBandsDrawn=true;
}
function renderGauge(d){
  var wrap=document.getElementById('rec-gauge');
  if(!wrap) return;
  drawGaugeBands();
  var ds=d.decision_support||{};
  // Direction-aware: the meter reflects the SELECTED side (the Long/Short toggle).
  // The favored side's per-side Edge equals the authoritative Edge (parity), so the
  // dominant side reads exactly like the system header; the other side shows its OWN
  // (typically lower) Edge instead of mirroring it. Falls back to the authoritative
  // probability when per-side data is absent (older server / market closed).
  var blk = (d.directions && d.directions[dir]) ? d.directions[dir] : null;
  var prob = (blk && blk.edge_score!=null) ? blk.edge_score
           : (ds.probability!=null ? ds.probability : (d.edge_score!=null ? d.edge_score : 0));
  prob = Math.max(0, Math.min(100, Number(prob)||0));
  // Needle: value 0 -> -90deg (left), 50 -> 0deg (up), 100 -> +90deg (right).
  var needle=document.getElementById('g-needle');
  var deg=(prob/100)*180-90;
  if(needle) needle.setAttribute('transform','rotate('+deg.toFixed(1)+' 100 100)');
  var col = prob>=70?'#22c55e':(prob>=41?'#eab308':'#ef4444');
  var probEl=document.getElementById('g-prob');
  if(probEl){ probEl.textContent=Math.round(prob)+'%'; probEl.style.color=col; }
  // Confidence label = the SELECTED side's quality band (derived from its Edge so it
  // can't contradict the needle); favored side matches the header by parity. Falls
  // back to authoritative quality / conviction tier when per-side data is absent.
  var quality = (blk && blk.edge_score!=null) ? jsQualityForScore(blk.edge_score)
              : (ds.quality || d.conviction_tier || '—');
  var confEl=document.getElementById('g-conf');
  if(confEl) confEl.textContent = quality;
  // Direction indicator = the side you're viewing (the meter is per-side now).
  var dirTxt='—', dirCol='#9aa0b5';
  if(dir==='Long'){ dirTxt='📈 LONG'; dirCol='#22c55e'; }
  else if(dir==='Short'){ dirTxt='📉 SHORT'; dirCol='#ef4444'; }
  var dirEl=document.getElementById('g-dir');
  if(dirEl){ dirEl.textContent=dirTxt; dirEl.style.color=dirCol; }
  // Long / Short / Edge-difference / Dominant row — system context (both sides at
  // once), independent of the selected toggle.
  var ls=d.long_score, ss=d.short_score;
  var diff=(ls!=null&&ss!=null)?Math.abs(ls-ss):(d.conflict_gap!=null?d.conflict_gap:null);
  var _n=function(x){ return x!=null?x:'—'; };
  var sc=document.getElementById('g-scores');
  if(sc) sc.innerHTML='Long <b style="color:#e8e8f0">'+_n(ls)+'</b> · Short <b style="color:#e8e8f0">'+_n(ss)
    +'</b><br>Δ Edge <b style="color:#e8e8f0">'+_n(diff)+'</b> · Dom <b style="color:#e8e8f0">'+_n(d.dominant_direction)+'</b>';
  // Deep-green glow only when VIEWING the actionable side at high conviction (full
  // READY + the ready direction + green band) — never on the non-favored side.
  var hi = (quality==='HIGH');
  wrap.classList.toggle('glow', !!(hi && jsIsFullReady(d.verdict) && jsReadyDir(d.verdict)===dir && prob>=70));
}

// ── Diagnostics modules (5) — all DISPLAY-ONLY, fed by alert_diagnostics ──────
// Verdict helpers: reuse the authoritative ones already defined above.
function isFullReady(v){ return jsIsFullReady(v); }
function isEarlyReady(v){ return jsIsEarlyReady(v); }
function isReadyVerdict(v){ return jsIsActionable(v); }
function readySide(v){ return jsReadyDir(v); }
function _modTxt(id,val){ const e=document.getElementById(id); if(e) e.textContent=val; }
function _modW(id,val){ const e=document.getElementById(id); if(e) e.style.width=Math.max(0,Math.min(100,Number(val)||0))+'%'; }
function _modEsc(s){ return String(s).replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function _gPolar(r,deg){ const a=deg*Math.PI/180; return [110 + r*Math.cos(a), 110 - r*Math.sin(a)]; }
function _gArc(r,vStart,vEnd){
  const d0=180-1.8*vStart, d1=180-1.8*vEnd;
  const p0=_gPolar(r,d0), p1=_gPolar(r,d1);
  const large=Math.abs(d1-d0)>180?1:0;
  return 'M'+p0[0].toFixed(2)+' '+p0[1].toFixed(2)+' A'+r+' '+r+' 0 '+large+' 1 '+p1[0].toFixed(2)+' '+p1[1].toFixed(2);
}
function gaugeColor(v,prob){
  if (isFullReady(v))  return prob>=90 ? '#15803d' : '#22c55e';   // HQ deep-green / READY green
  if (isEarlyReady(v)) return '#eab308';                          // EARLY yellow
  return prob<40 ? '#ef4444' : '#eab308';                         // WAIT red/yellow
}
function renderModules(d){
  if (!d) return;
  const diag   = d.alert_diagnostics || {};
  const v      = d.verdict || 'WAIT';
  const prob   = Math.max(0, Math.min(100, Number(diag.edge_score!=null?diag.edge_score:(d.edge_score||0))));
  const longS  = Number(diag.long_score||0);
  const shortS = Number(diag.short_score||0);
  const gap    = Number(diag.conflict_gap!=null?diag.conflict_gap:Math.abs(longS-shortS));
  const dom    = diag.dominant_direction || (longS>shortS?'Long':shortS>longS?'Short':'Neutral');
  const col    = gaugeColor(v, prob);
  const hq     = isFullReady(v) && prob>=90;

  // ── Module 1: speedometer / RPM gauge ──
  const svg = document.getElementById('gauge-svg');
  if (svg){
    let s = '<defs><filter id="gglow" x="-60%" y="-60%" width="220%" height="220%">'
          + '<feGaussianBlur stdDeviation="3.5" result="b"/><feMerge><feMergeNode in="b"/>'
          + '<feMergeNode in="SourceGraphic"/></feMerge></filter></defs>';
    s += '<path d="'+_gArc(82,0,40)+'" stroke="#ef4444" stroke-width="16" fill="none" stroke-linecap="round" opacity="0.9"/>';
    s += '<path d="'+_gArc(82,41,69)+'" stroke="#eab308" stroke-width="16" fill="none" opacity="0.9"/>';
    s += '<path d="'+_gArc(82,70,100)+'" stroke="#22c55e" stroke-width="16" fill="none" stroke-linecap="round" opacity="0.9"/>';
    [0,25,50,75,100].forEach(function(t){
      const p=_gPolar(100, 180-1.8*t);
      s += '<text x="'+p[0].toFixed(1)+'" y="'+(p[1]+3).toFixed(1)+'" fill="#6b7280" font-size="9" '
         + 'text-anchor="middle" font-family="ui-monospace,monospace">'+t+'</text>';
    });
    const n=_gPolar(72, 180-1.8*prob);
    s += '<line x1="110" y1="110" x2="'+n[0].toFixed(2)+'" y2="'+n[1].toFixed(2)+'" stroke="'+col+'" '
       + 'stroke-width="3.5" stroke-linecap="round"'+(hq?' filter="url(#gglow)"':'')+'/>';
    s += '<circle cx="110" cy="110" r="6.5" fill="'+col+'"'+(hq?' filter="url(#gglow)"':'')+'/>';
    s += '<circle cx="110" cy="110" r="2.5" fill="#0d0d18"/>';
    svg.innerHTML = s;
  }
  const probEl=document.getElementById('gauge-prob');
  if (probEl){ probEl.textContent=Math.round(prob)+'%'; probEl.style.color=col; }
  const subEl=document.getElementById('gauge-sub');
  if (subEl){
    const conf = prob>=70 ? 'HIGH' : prob>=41 ? 'MOD' : 'LOW';
    const dirTxt = readySide(v) || (dom!=='Neutral'?dom:'—');
    const stateTxt = isFullReady(v) ? (prob>=90?'HIGH QUALITY READY':'READY')
                   : isEarlyReady(v) ? 'EARLY READY' : 'WAIT';
    subEl.innerHTML = '<b style="color:'+col+'">'+stateTxt+'</b> · '+conf+' confidence · '+dirTxt;
  }
  _modTxt('gs-long', longS); _modTxt('gs-short', shortS); _modTxt('gs-gap', gap);
  const domEl=document.getElementById('gs-dom');
  if (domEl){ domEl.textContent=dom; domEl.style.color = dom==='Long'?'#22c55e':dom==='Short'?'#ef4444':'#6b7280'; }

  // ── Module 2: Long vs Short score bars ──
  _modW('sg-long-f', longS);  _modTxt('sg-long-n', longS);
  _modW('sg-short-f', shortS); _modTxt('sg-short-n', shortS);

  // ── Module 3: AI Trade Checklist (gate booleans of the dominant side) ──
  const aiGd = (d.directions && dom!=='Neutral' && d.directions[dom] && d.directions[dom].gate_debug)
             ? d.directions[dom].gate_debug : (d.gate_debug || {});
  const reqd = Number(diag.required_score!=null?diag.required_score:(aiGd.ready_threshold!=null?aiGd.ready_threshold:50));
  const edgeNow = Number(aiGd.edge_score!=null?aiGd.edge_score:prob);
  const items = [
    ['Trade-side zone valid', !!aiGd.zone_valid],
    ['Structure (BOS/CHOCH)', !!aiGd.structure_confirmed],
    ['VWAP confirming', !!aiGd.vwap_confirmed],
    ['Confirmation candle', !!aiGd.candle_confirmed],
    ['Liquidity sweep', !!aiGd.liquidity_sweep],
    ['Edge \u2265 '+reqd, edgeNow>=reqd]
  ];
  const aiEl=document.getElementById('ai-ck');
  if (aiEl) aiEl.innerHTML = items.map(function(it){
    return '<div class="ai-item '+(it[1]?'ok':'no')+'"><span class="ic">'+(it[1]?'\u2713':'\u2717')
         + '</span><span>'+it[0]+'</span></div>';
  }).join('');

  // ── Module 4: Setup Countdown (Edge points to the next trigger) ──
  const cur = Number(diag.current_score!=null?diag.current_score:prob);
  const cdBig=document.getElementById('cd-big'), cdSub=document.getElementById('cd-sub'), cdFill=document.getElementById('cd-fill');
  if (cdBig){
    const pct = Math.max(0, Math.min(100, reqd>0 ? cur/reqd*100 : 0));
    if (isFullReady(v)){
      cdBig.textContent='READY'; cdBig.style.color='#22c55e';
      if (cdSub) cdSub.textContent='Setup is live — trade plan available';
      if (cdFill){ cdFill.style.width='100%'; cdFill.style.background='#22c55e'; }
    } else if (isEarlyReady(v)){
      const togo=Math.max(0, reqd-cur);
      cdBig.textContent='+'+togo+' pts'; cdBig.style.color='#eab308';
      if (cdSub) cdSub.textContent='Early entry active — '+togo+' Edge pts to full READY ('+reqd+')';
      if (cdFill){ cdFill.style.width=pct+'%'; cdFill.style.background='#eab308'; }
    } else {
      const togo=Math.max(0, reqd-cur);
      cdBig.textContent='+'+togo+' pts'; cdBig.style.color='#f59e0b';
      if (cdSub) cdSub.textContent=togo+' Edge pts to trigger (need '+reqd+', now '+cur+')';
      if (cdFill){ cdFill.style.width=pct+'%'; cdFill.style.background = cur>=reqd?'#22c55e':'#f59e0b'; }
    }
  }

  // ── Module 5: Why Not Ready ──
  const wn=document.getElementById('wn-body');
  if (wn){
    if (isReadyVerdict(v)){
      wn.innerHTML = '<div class="wn-ok">\u2705 <b>'+(isFullReady(v)?'All systems go':'Early entry active')+'</b>'
                   + (diag.ready_reason ? '<br>'+_modEsc(diag.ready_reason) : '') + '</div>';
    } else {
      const rr = diag.rejected_reasons || [];
      wn.innerHTML = rr.length
        ? rr.map(function(r){ return '<div class="wn-item">\u26d4 <span>'+_modEsc(r)+'</span></div>'; }).join('')
        : '<div class="wn-ok">No blocking gate reasons reported.</div>';
    }
  }
}

async function refreshRec() {
  try {
    const d = await api('/status?ticker='+encodeURIComponent(sym));
    lastRec = d;
    if (d.trading_mode) paintMode(d.trading_mode);
    const badge = document.getElementById('rec-badge');
    const meta  = document.getElementById('rec-meta');
    const card  = document.getElementById('rec-card');

    // ── Authoritative header (the system's single call) — toggle-independent ──
    const v = d.verdict || 'WAIT';

    // Market closed (CME/COMEX hours) — show a paused banner instead of WAIT, and
    // skip the live-setup rendering below. Live alerts resume on the next open.
    if (d.market_open === false) {
      badge.textContent = 'MARKET CLOSED';
      badge.className = 'rec-badge wait';
      card.style.borderColor = '#3a3a52';
      const inst = d.active_ticker ? String(d.active_ticker).replace('1!','') : 'MNQ/MGC';
      const no = d.next_open ? ' &nbsp;·&nbsp; Next open: <b style="color:#e8e8f0">'+d.next_open+'</b>' : '';
      const lv = (d.last_valid_price!=null)
        ? '<br>Last valid data: <b style="color:#e8e8f0">'+d.last_valid_price+'</b>'
          + (d.last_valid_time ? ' <span style="color:#6b7280;font-size:11px">('+d.last_valid_time+')</span>' : '')
        : '';
      meta.innerHTML = '<b style="color:#a0a8ff">'+inst+'</b> &nbsp;·&nbsp; '
        + '<b style="color:#f59e0b">🌙 Live alerts paused</b>' + no + lv;
      const lb = document.querySelector('.dir-btn.long');
      const sb = document.querySelector('.dir-btn.short');
      if (lb) lb.classList.remove('rec');
      if (sb) sb.classList.remove('rec');
      renderGauge(d);
      renderModules(d);
      renderDirView();
      markUpdated();
      return;
    }

    badge.textContent = v;
    badge.className = 'rec-badge ' + (
      jsIsFullReady(v) ? (v==='LONG READY'?'ready-long':'ready-short')
      : jsIsEarlyReady(v) ? (v==='LONG EARLY READY'?'early-long':'early-short')
      : 'wait');
    card.style.borderColor =
      v==='LONG READY'?'#1b3a26':v==='SHORT READY'?'#3a1b1b'
      : v==='LONG EARLY READY'?'#26301b':v==='SHORT EARLY READY'?'#301b1b'
      : '#1e1e32';

    const inst  = d.active_ticker ? String(d.active_ticker).replace('1!','') : '—';
    const _pval = d.display_price!=null ? d.display_price
                : (d.current_price!=null ? d.current_price : null);
    const price = _pval!=null ? _pval : '—';
    const psrc  = d.price_source==='auto'  ? ' <span style="color:#6b7280;font-size:11px">(auto)</span>'
                : d.price_source==='stale' ? ' <span style="color:#6b7280;font-size:11px">(stale)</span>' : '';
    const vsrc  = d.vwap_source==='chart' ? ' <span style="color:#6b7280;font-size:11px">(manual)</span>'
                : d.vwap_source==='auto'  ? ' <span style="color:#6b7280;font-size:11px">(auto)</span>' : '';
    const vwap  = (d.vwap_status==='ok' && d.vwap_value!=null)
      ? Number(d.vwap_value).toFixed(1) + vsrc
      : 'n/a ('+(d.vwap_status||'—')+')';
    const sess = d.session_preferred
      ? '<span style="color:#22c55e">● Preferred Session (+'+(d.session_bonus!=null?d.session_bonus:10)+')</span>'
      : '<span style="color:#6b7280">○ Off-session</span>';
    const vol = d.volatility || {};
    let volTxt = '';
    if (vol.status === 'ok') {
      const vc = vol.blocked ? '#ef4444' : vol.caution ? '#f59e0b' : '#22c55e';
      const vr = vol.ratio!=null ? ' <span style="color:#6b7280;font-size:11px">'+Number(vol.ratio).toFixed(2)+'×</span>' : '';
      volTxt = ' &nbsp;·&nbsp; Vol <b style="color:'+vc+'">'+(vol.label||'—')+'</b>'+vr;
    }
    // Operational early-warning level (only meaningful pre-READY) + conviction tier
    // (score band). Display-only — these never change the verdict above.
    let lvlTxt = '';
    if (!jsIsActionable(v) && d.alert_level) {
      const lc = d.alert_level==='WATCH FOR ENTRY' ? '#3b82f6'
               : d.alert_level==='ARMED' ? '#e67e22' : '#eab308';
      lvlTxt = ' &nbsp;·&nbsp; <b style="color:'+lc+'">'+d.alert_level+'</b>';
    }
    let convTxt = '';
    if (d.conviction_tier) {
      const cc = d.conviction_tier==='HIGH CONVICTION' ? '#22c55e'
               : d.conviction_tier==='READY' ? '#3b82f6'
               : d.conviction_tier==='EARLY READY' ? '#a3e635' : '#eab308';
      convTxt = ' &nbsp;·&nbsp; <span style="color:#6b7280;font-size:11px">Tier</span> <b style="color:'+cc+'">'+d.conviction_tier+'</b>';
    }
    meta.innerHTML = '<b style="color:#a0a8ff">'+inst+'</b> &nbsp;·&nbsp; Price <b style="color:#e8e8f0">'+price+'</b>'+psrc+' &nbsp;·&nbsp; VWAP <b style="color:#e8e8f0">'+vwap+'</b> &nbsp;·&nbsp; '+sess+volTxt+lvlTxt+convTxt;

    // Trade probability gauge — fed by the authoritative /status fields.
    renderGauge(d);
    renderModules(d);

    // Mark the recommended toggle button (the system's actionable side, incl. EARLY
    // READY) — display-only hint, no auto-switch.
    const readyDir = jsReadyDir(v);
    const lb = document.querySelector('.dir-btn.long');
    const sb = document.querySelector('.dir-btn.short');
    if (lb) lb.classList.toggle('rec', readyDir==='Long');
    if (sb) sb.classList.toggle('rec', readyDir==='Short');

    // Per-direction body driven by the current toggle.
    renderDirView();
    markUpdated();
  } catch(e) {}
}

// Render the checklist + edge score + plan/reason for the CURRENTLY SELECTED
// direction (the Long/Short toggle). Reads lastRec.directions[dir]; falls back to
// the authoritative favored block if directions is missing (older server).
function renderDirView() {
  if (!lastRec) return;
  const d = lastRec;
  const bar    = document.getElementById('rec-score-bar');
  const num    = document.getElementById('rec-score-num');
  const list   = document.getElementById('rec-checklist');
  const planEl = document.getElementById('rec-plan');
  const reason = document.getElementById('rec-reason');
  const apply  = document.getElementById('btn-apply');
  if (!bar) return;

  // Market closed — paused, neutral body (no checklist / score / plan / apply).
  if (d.market_open === false) {
    const no = d.next_open ? '<br>Next open: <b style="color:#e8e8f0">'+d.next_open+'</b>' : '';
    const lv = (d.last_valid_price!=null)
      ? '<br>Last valid data: <b style="color:#e8e8f0">'+d.last_valid_price+'</b>'
        + (d.last_valid_time ? ' <span style="color:#6b7280;font-size:11px">('+d.last_valid_time+')</span>' : '')
      : '';
    if (list) list.innerHTML = '<div style="color:#6b7280;font-size:13px;line-height:1.6">'
      + '🌙 <b style="color:#f59e0b">MARKET CLOSED</b> — MNQ/MGC live alerts paused.'
      + no + lv + '</div>';
    bar.style.width = '0%';
    if (num) num.textContent = 'Paused';
    if (planEl) planEl.style.display = 'none';
    if (apply)  apply.style.display = 'none';
    if (reason) reason.textContent = d.strict_reason || 'Market closed — live alerts paused.';
    return;
  }

  const isShort = dir === 'Short';
  const blk = (d.directions && d.directions[dir]) ? d.directions[dir] : null;

  // Checklist for the selected side. The Edge threshold is mode-dependent (SCALP 55
  // / SWING 80), so read it from gate_debug rather than hard-coding "80".
  const ck = (blk && blk.checklist) ? blk.checklist : (d.confluences || {});
  const _thr = (blk && blk.gate_debug && blk.gate_debug.ready_threshold!=null)
             ? blk.gate_debug.ready_threshold : 80;
  list.innerHTML =
    ckItem((isShort?'Supply':'Demand')+' zone mitigated + reaction', !!ck.zone) +
    ckItem('Structure '+(isShort?'(CHOCH/BOS/LH-LL)':'(CHOCH/BOS/HH-HL)'), !!ck.structure) +
    ckItem('Price '+(isShort?'< ':'> ')+'VWAP', !!ck.vwap) +
    ckItem('Edge Score \u2265 '+_thr, !!ck.edge);

  // Raw gate debug — exact per-condition booleans driving the verdict.
  const gd = (blk && blk.gate_debug) ? blk.gate_debug : null;
  if (gd) {
    const yn = v => v ? '\u2713' : '\u2717';
    const fails = (gd.failed_conditions && gd.failed_conditions.length)
                ? gd.failed_conditions.join(', ') : 'none';
    list.innerHTML +=
      '<div style="margin-top:8px;padding-top:6px;border-top:1px solid #1e1e32;' +
      'font-size:11px;line-height:1.5;color:#6b7280;font-family:ui-monospace,monospace">' +
      'debug · zone_valid '+yn(gd.zone_valid)+' · vwap_confirmed '+yn(gd.vwap_confirmed)+
      ' · structure_confirmed '+yn(gd.structure_confirmed)+' · candle_confirmed '+yn(gd.candle_confirmed)+
      '<br>edge_score '+(gd.edge_score!=null?gd.edge_score:0)+'/100 · failed_conditions: '+fails+'</div>';
  }

  // Per-direction Edge Score.
  const score = blk && blk.edge_score!=null ? blk.edge_score
              : (d.edge_score!=null ? d.edge_score : 0);
  const grade = blk && blk.edge_grade ? ' · ' + blk.edge_grade
              : (d.edge_grade ? ' · ' + d.edge_grade : '');
  const label = blk ? (blk.label || 'WAIT') : (d.strict_label || 'WAIT');
  const met   = blk && blk.met!=null ? ' · ' + blk.met + '/4' : '';
  bar.style.width = score + '%';
  bar.style.background = score>=90 ? '#22c55e' : score>=75 ? '#a0a8ff' : '#f59e0b';
  num.textContent = dir + ' Edge ' + score + '/100' + grade + ' · ' + label + met;

  // Trade plan + Apply only when the SELECTED side is the system's actionable side
  // (full READY or EARLY READY).
  const readyDir = jsReadyDir(d.verdict);
  const tp = d.trade_plan || {};
  if (readyDir === dir && tp.trade_plan) {
    planEl.style.display = 'block';
    planEl.innerHTML =
      'Entry <b>'+tp.entry_zone+'</b> &nbsp;·&nbsp; Stop <b>'+tp.stop_loss+'</b><br>' +
      'T1 <b>'+tp.target1+'</b> &nbsp;·&nbsp; T2 <b>'+tp.target2+'</b> &nbsp;·&nbsp; R:R <b>'+(tp.rr!=null?tp.rr:'—')+'</b>' +
      (tp.atr_pts!=null
        ? '<br>ATR <b>'+tp.atr_pts+'</b> × <b>'+tp.atr_multiplier+'</b> &nbsp;·&nbsp; Stop <b>'+tp.stop_distance_ticks+'</b> ticks &nbsp;·&nbsp; Risk <b>$'+tp.risk_dollars_per_contract+'</b>/ct'
        : '');
    apply.style.display = 'block';
  } else {
    planEl.style.display = 'none';
    apply.style.display = 'none';
  }

  // Reason for the selected side; nudge to the ready side when viewing the other.
  let txt = (blk && blk.reason) ? blk.reason : (d.strict_reason || '');
  if (readyDir && readyDir !== dir) {
    txt = '↪ System says ' + d.verdict + ' — switch to ' + readyDir + ' for the live setup.  ' + txt;
  }
  reason.textContent = txt;
}

function applyRec() {
  if (!lastRec) return;
  const tp = lastRec.trade_plan || {};
  const dirReady = jsReadyDir(lastRec.verdict);
  if (!dirReady || !tp.trade_plan) { toast('No ready setup to apply', false); return; }
  const inst = (lastRec.active_ticker||'').toString().replace('1!','');
  if (inst==='MGC' || inst==='MNQ') setSymbol(inst);
  setDir(dirReady);
  if (tp.entry_zone) {
    const parts = String(tp.entry_zone).split('–');
    if (parts.length===2) {
      const mid = (parseFloat(parts[0]) + parseFloat(parts[1])) / 2;
      if (!isNaN(mid)) document.getElementById('f-entry').value = mid.toFixed(1);
    }
  }
  if (tp.stop_loss!=null) document.getElementById('f-stop').value = tp.stop_loss;
  if (tp.target1!=null)   document.getElementById('f-t1').value   = tp.target1;
  if (tp.target2!=null)   document.getElementById('f-t2').value   = tp.target2;
  document.querySelector('details').open = true;
  toast('⬇️ Setup applied — review & ENTER');
}

let lastUpdateTs = 0;
function markUpdated() {
  lastUpdateTs = Date.now();
  const el = document.getElementById('last-updated');
  if (!el) return;
  const t = new Date();
  const p = n => String(n).padStart(2,'0');
  el.textContent = 'Last updated ' + p(t.getHours()) + ':' + p(t.getMinutes()) + ':' + p(t.getSeconds());
  el.classList.remove('stale');
}
function checkStale() {
  const el = document.getElementById('last-updated');
  if (!el || !lastUpdateTs) return;
  const secs = Math.floor((Date.now() - lastUpdateTs) / 1000);
  if (secs > 12) {
    el.classList.add('stale');
    el.textContent = '⚠ Not responding — last update ' + secs + 's ago';
  }
}

async function refresh() {
  try {
    const d = await api('/trade');
    const card = document.getElementById('status-card');
    const info = document.getElementById('trade-info');
    const detail = document.getElementById('trade-detail');
    const pnl = document.getElementById('pnl');
    const btnClose = document.getElementById('btn-close');
    const btnBe = document.getElementById('btn-be');

    if (d.status === 'no_active_trade') {
      activeTrade = null;
      card.style.borderColor = '#1e1e32';
      info.textContent = 'No Active Trade';
      info.style.color = '#555';
      detail.textContent = '';
      pnl.textContent = '';
      btnClose.style.display = 'none';
      btnBe.style.display = 'none';
    } else {
      activeTrade = d;
      const isLong = d.direction === 'Long';
      card.style.borderColor = isLong ? '#22c55e' : '#ef4444';
      info.style.color = isLong ? '#22c55e' : '#ef4444';
      info.textContent = (isLong?'📈':'📉') + ' ' + d.direction + ' — ' + (d.profile||'').split(' ')[0];
      detail.innerHTML =
        'Entry <b>'+d.entry_price+'</b> &nbsp;·&nbsp; Stop <b>'+d.stop_loss+'</b><br>' +
        'T1 <b>'+d.target1+'</b> &nbsp;·&nbsp; T2 <b>'+d.target2+'</b> &nbsp;·&nbsp; '+d.contracts+'x';
      if (d.pnl_dollars !== undefined) {
        const v = d.pnl_dollars;
        const s = v >= 0 ? '+$'+Math.abs(v).toFixed(0) : '-$'+Math.abs(v).toFixed(0);
        pnl.textContent = s;
        pnl.className = 'pnl ' + (v > 0 ? 'pnl-pos' : v < 0 ? 'pnl-neg' : 'pnl-flat');
      }
      btnClose.style.display = 'block';
      btnBe.style.display = d.t1_hit ? 'block' : 'none';
    }
    markUpdated();
  } catch(e) {}
}

// Poll every 3 seconds
refresh(); refreshRec(); loadMode();
setInterval(() => { refresh(); refreshRec(); }, 3000);
setInterval(checkStale, 2000);
</script>
</body>
</html>"""
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/ping", methods=["GET", "POST", "HEAD"])
def ping():
    # Simple health/test endpoint — no alert logic. Used by UptimeRobot and for testing.
    return jsonify({"status": "ok", "trading_mode": TRADING_MODE}), 200


@app.route("/mode", methods=["GET", "POST"])
def mode():
    """Read or switch the active trading mode (SCALP / SWING) at runtime."""
    global TRADING_MODE
    if request.method == "POST":
        data      = request.get_json(silent=True) or {}
        requested = str(data.get("mode", "")).upper()
        if requested not in MODES:
            return jsonify({"status": "error",
                            "reason": f"Unknown mode {requested!r}. Use one of {list(MODES)}."}), 400
        TRADING_MODE = requested
        logger.info("Trading mode switched to %s", TRADING_MODE)
    return jsonify({
        "status":          "ok",
        "trading_mode":    TRADING_MODE,
        "available_modes": list(MODES),
        "thresholds":      MODES[TRADING_MODE],
    }), 200


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service":      "TradingView Webhook Server",
        "version":      "11.0",
        "trading_mode": TRADING_MODE,
        "alert_types":  list(ALERT_TYPES.keys()),
        "endpoints":   {
            "POST /webhook":   "Receive TradingView alerts",
            "GET /alerts":     "View last 100 stored alerts",
            "GET /price":      "Price context, levels, structure, and risk zone",
            "GET /status":     "Full analysis with verdict and reasoning chain",
            "GET|POST /mode":  "Read or switch trading mode (SCALP / SWING)",
            "POST /enter":     "Open an active trade (uses current trade plan or explicit params)",
            "POST /breakeven": "Move stop loss to entry price",
            "POST /close":     "Close the active trade manually",
            "GET /trade":      "Show active trade status and live PnL",
        },
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    _ensure_webhook_worker()                       # background webhook processor (fast ack to TradingView)
    threading.Timer(0, _vwap_autofetch_loop).start()  # auto-fetch VWAP now, then every VWAP_FETCH_INTERVAL (no Discord posting)
    threading.Timer(0, _price_autofetch_loop).start()  # DISPLAY-ONLY price now, then every PRICE_FETCH_INTERVAL (never feeds the gate)
    if EVAL_HEARTBEAT_ENABLED:
        threading.Timer(0, _heartbeat_eval_loop).start()  # periodic market re-eval (diagnostics-only; no Discord) — runs on dev + prod
    # Unconditional, time-based Discord senders run on the LIVE (prod) instance only.
    # In dev they would double-post to the shared live channel — see DISCORD_LIVE_ENABLED.
    if DISCORD_LIVE_ENABLED:
        threading.Timer(0, _heartbeat_loop).start()   # fire immediately, then every HEARTBEAT_INTERVAL
        threading.Timer(TRADE_READY_INTERVAL, _trade_ready_loop).start()  # re-post READY card every 5 min
        _schedule_eod()                               # schedule daily EOD summary
        _schedule_weekly_report()                     # schedule weekly report (Fri after close)
    else:
        logger.info(
            "DISCORD_LIVE_ENABLED=False (dev instance) — heartbeat / trade-ready / EOD / "
            "weekly Discord schedulers disabled so this instance can't double-post to the "
            "live channel. Set DISCORD_LIVE=1 to enable."
        )
    app.run(host="0.0.0.0", port=port, debug=False)
