"""
Volatility Intelligence Module — Observation-Only, Phase B-D
=============================================================
Adds a VIX-based volatility context layer to the Left Brain.

Feature flags (all default to SAFE / OFF):
  VOL_INTELLIGENCE_ENABLED              = 0  (master gate)
  VOL_INTELLIGENCE_OBSERVE_ONLY         = 1  (must stay 1 until proven)
  VOL_INTELLIGENCE_EXECUTION_INFLUENCE  = 0  (never in Phase B-E)
  VOL_INTELLIGENCE_SCORE_INFLUENCE      = 0  (never in Phase B-E)

Safety contract:
  * NEVER modifies gate verdicts, edge scores, position sizes, or execution.
  * NEVER crashes the scanner on missing/stale/malformed VIX data.
  * Always returns a well-formed dict — callers need no null-checks.
  * All execution-influence paths contain an explicit assertion guard.
  * When disabled, returns _NEUTRAL_SNAPSHOT — byte-identical to flag-OFF.
"""

from __future__ import annotations

import os
import time
import logging
import threading
import requests
from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# ── Feature flags ──────────────────────────────────────────────────────────────

def _vi_flag(name: str, default: bool) -> bool:
    """Read a boolean env flag. Matches the project's _env_flag_on() convention."""
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return default
    return val not in ("0", "false", "no", "off")

VOL_INTELLIGENCE_ENABLED             = _vi_flag("VOL_INTELLIGENCE_ENABLED", False)
VOL_INTELLIGENCE_OBSERVE_ONLY        = _vi_flag("VOL_INTELLIGENCE_OBSERVE_ONLY", True)
VOL_INTELLIGENCE_EXECUTION_INFLUENCE = _vi_flag("VOL_INTELLIGENCE_EXECUTION_INFLUENCE", False)
VOL_INTELLIGENCE_SCORE_INFLUENCE     = _vi_flag("VOL_INTELLIGENCE_SCORE_INFLUENCE", False)

# Safety assertion — must be checked before any future execution-influence code.
def _assert_observe_only(context: str = "") -> None:
    """Raise RuntimeError if execution influence is somehow enabled while observe-only is on."""
    if VOL_INTELLIGENCE_OBSERVE_ONLY and (
        VOL_INTELLIGENCE_EXECUTION_INFLUENCE or VOL_INTELLIGENCE_SCORE_INFLUENCE
    ):
        raise RuntimeError(
            f"Vol-Intelligence safety violation in {context!r}: "
            "EXECUTION_INFLUENCE or SCORE_INFLUENCE enabled while OBSERVE_ONLY=1. "
            "Set OBSERVE_ONLY=0 AND gather paper-trading evidence before enabling influence."
        )

# ── Configuration ──────────────────────────────────────────────────────────────

_AV_SYMBOL          = "^VIX"
_AV_BASE_URL        = "https://www.alphavantage.co/query"
_FETCH_INTERVAL_SEC = int(os.environ.get("ALPHA_VANTAGE_FETCH_INTERVAL_SEC", "3600"))  # 1 hr default — AV free tier = 25 calls/day
_HIST_INTERVAL_SEC  = int(os.environ.get("ALPHA_VANTAGE_HIST_INTERVAL_SEC",  "900"))  # 15 min default
_FRESHNESS_SEC      = int(os.environ.get("VOL_INTELLIGENCE_FRESHNESS_SEC",    "600"))  # 10 min
_MAX_OBS            = 500   # bounded in-memory observation buffer
_MAX_CONSEC_ERRORS  = 10    # suppress repeated log spam

# Regime bands (configurable via env — centralized here, never scattered)
_CALM_MAX    = float(os.environ.get("VIX_CALM_MAX",     "15"))
_NORMAL_MAX  = float(os.environ.get("VIX_NORMAL_MAX",   "20"))
_ELEVATED_MAX= float(os.environ.get("VIX_ELEVATED_MAX", "30"))
# EXTREME = above _ELEVATED_MAX

# ── Data model ────────────────────────────────────────────────────────────────

def _null_vix_record(status: str = "UNAVAILABLE", error: Optional[str] = None) -> Dict[str, Any]:
    return {
        "symbol":         "VIX",
        "source":         "alpha_vantage",
        "price":          None,
        "previous_close": None,
        "change":         None,
        "change_pct":     None,
        "session_open":   None,
        "session_high":   None,
        "session_low":    None,
        "timestamp_utc":  None,
        "age_seconds":    None,
        "is_fresh":       False,
        "is_delayed":     True,   # Alpha Vantage data carries a real-time delay
        "status":         status,
        "error":          error,
    }

_NEUTRAL_SNAPSHOT: Dict[str, Any] = {
    "enabled":           False,
    "observe_only":      True,
    "timestamp_utc":     None,
    "data_status":       "UNAVAILABLE",
    "vix":               _null_vix_record(),
    "direction":         "UNKNOWN",
    "velocity":          "UNKNOWN",
    "acceleration":      "UNKNOWN",
    "session_percentile":None,
    "regime":            "UNKNOWN",
    "risk_tone":         "UNKNOWN",
    "equity_context":    "UNKNOWN",
    "confidence":        0,
    "reasons":           [],
    "warnings":          [],
    "execution_effect":  "NONE",
    "score_effect":      0,
    "instrument_context":{},
    "provider_health":   {"status": "DISABLED"},
}

# Per-instrument relevance
_INSTRUMENT_RELEVANCE: Dict[str, str] = {
    "MNQ": "HIGH",
    "MES": "HIGH",
    "MYM": "MEDIUM_HIGH",
    "MGC": "LOW_TO_MEDIUM",
}

_MGC_INDIRECT_NOTE = (
    "VIX is indirect context for gold — a risk-off spike may support gold as a "
    "safe haven, but VIX alone must not determine gold direction."
)

# ── Provider interface ────────────────────────────────────────────────────────

class VolatilityDataProvider(ABC):
    """Abstract interface — swap providers without touching the analysis engine."""

    @abstractmethod
    def get_latest_vix(self) -> Dict[str, Any]:
        """Return a normalized VIX record (see _null_vix_record schema)."""

    @abstractmethod
    def get_vix_history(self) -> List[Dict[str, Any]]:
        """Return a list of recent VIX price observations [{price, timestamp_utc}]."""

    @abstractmethod
    def get_provider_status(self) -> Dict[str, Any]:
        """Return provider health info."""

# ── Alpha Vantage provider ────────────────────────────────────────────────────

class AlphaVantageProvider(VolatilityDataProvider):
    """
    Fetches VIX from Alpha Vantage.
    Data is ~15-min delayed during market hours and is always labeled DELAYED.
    Fetch interval defaults to 5 min (ALPHA_VANTAGE_FETCH_INTERVAL_SEC).
    Free-tier users (25 req/day) should set the interval to 1200+ seconds.
    """

    def __init__(self) -> None:
        self._api_key: str = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
        self._latest:  Dict[str, Any] = _null_vix_record("UNAVAILABLE", "Not yet fetched")
        self._history: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._last_quote_fetch: float = 0.0
        self._last_hist_fetch:  float = 0.0
        self._consecutive_errors: int = 0
        self._total_errors:  int = 0
        self._reconnect_cnt: int = 0
        self._stale_cnt:     int = 0
        self._last_error:    Optional[str] = None
        self._last_ok_utc:   Optional[str] = None

    # ── Public interface ──────────────────────────────────────────────────────

    def get_latest_vix(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._latest)

    def get_vix_history(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._history)

    def get_provider_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "provider":           "AlphaVantage",
                "symbol":             _AV_SYMBOL,
                "connected":          self._consecutive_errors == 0 and self._last_ok_utc is not None,
                "last_ok_utc":        self._last_ok_utc,
                "last_error":         self._last_error,
                "consecutive_errors": self._consecutive_errors,
                "total_errors":       self._total_errors,
                "reconnect_count":    self._reconnect_cnt,
                "stale_count":        self._stale_cnt,
                "fetch_interval_sec": _FETCH_INTERVAL_SEC,
                "hist_interval_sec":  _HIST_INTERVAL_SEC,
                "api_key_present":    bool(self._api_key),
                "is_delayed":         True,
            }

    # ── Internal fetch ────────────────────────────────────────────────────────

    def maybe_refresh(self) -> None:
        """Called by the background thread; fetches only when intervals have elapsed."""
        now = time.time()
        if not self._api_key:
            with self._lock:
                self._latest = _null_vix_record("UNAVAILABLE", "ALPHA_VANTAGE_API_KEY not set")
            return
        if now - self._last_quote_fetch >= _FETCH_INTERVAL_SEC:
            self._fetch_quote()
            self._last_quote_fetch = now
        if now - self._last_hist_fetch >= _HIST_INTERVAL_SEC:
            self._fetch_history()
            self._last_hist_fetch = now

    def _fetch_quote(self) -> None:
        try:
            resp = requests.get(
                _AV_BASE_URL,
                params={"function": "GLOBAL_QUOTE", "symbol": _AV_SYMBOL, "apikey": self._api_key},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            gq = data.get("Global Quote", {})
            if not gq or "05. price" not in gq:
                note = data.get("Note") or data.get("Information") or "Empty response"
                self._record_error(f"GLOBAL_QUOTE empty — {note[:120]}")
                return

            price         = _safe_float(gq.get("05. price"))
            prev_close    = _safe_float(gq.get("08. previous close") or gq.get("07. previous close"))
            change        = _safe_float(gq.get("09. change") or gq.get("08. change"))
            change_pct_s  = (gq.get("10. change percent") or gq.get("09. change percent") or "").replace("%", "")
            change_pct    = _safe_float(change_pct_s)
            s_open        = _safe_float(gq.get("02. open"))
            s_high        = _safe_float(gq.get("03. high"))
            s_low         = _safe_float(gq.get("04. low"))
            latest_day    = gq.get("07. latest trading day") or gq.get("06. latest trading day")

            now_utc_s = datetime.now(timezone.utc).isoformat()
            rec = {
                "symbol":         "VIX",
                "source":         "alpha_vantage",
                "price":          price,
                "previous_close": prev_close,
                "change":         change,
                "change_pct":     change_pct,
                "session_open":   s_open,
                "session_high":   s_high,
                "session_low":    s_low,
                "timestamp_utc":  now_utc_s,
                "age_seconds":    0.0,
                "is_fresh":       True,
                "is_delayed":     True,
                "status":         "DELAYED" if price is not None else "ERROR",
                "error":          None,
                "latest_trading_day": latest_day,
            }
            with self._lock:
                self._latest = rec
                self._consecutive_errors = 0
                self._last_ok_utc = now_utc_s
            logger.debug("VIX quote fetched: %.2f (%.2f%%)", price or 0, change_pct or 0)
        except Exception as exc:
            self._record_error(str(exc))

    def _fetch_history(self) -> None:
        try:
            resp = requests.get(
                _AV_BASE_URL,
                params={
                    "function":   "TIME_SERIES_INTRADAY",
                    "symbol":     _AV_SYMBOL,
                    "interval":   "5min",
                    "outputsize": "compact",
                    "apikey":     self._api_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            ts_key = "Time Series (5min)"
            series = data.get(ts_key, {})
            if not series:
                return  # quota note or empty — quote fetch handles the error log
            obs = []
            for ts_str, bar in sorted(series.items(), reverse=True)[:36]:  # last 3 hours
                price = _safe_float(bar.get("4. close"))
                if price is not None:
                    obs.append({"price": price, "timestamp_str": ts_str})
            obs.reverse()  # chronological
            with self._lock:
                self._history = obs
        except Exception as exc:
            logger.debug("VIX history fetch failed (non-critical): %s", exc)

    def _record_error(self, msg: str) -> None:
        with self._lock:
            self._consecutive_errors += 1
            self._total_errors += 1
            self._last_error = msg
            self._latest = _null_vix_record("ERROR", msg[:200])
        # Rate-limited logging — only log first + every 5th consecutive error
        if self._consecutive_errors == 1 or self._consecutive_errors % 5 == 0:
            logger.warning("VIX provider error #%d: %s", self._consecutive_errors, msg[:120])
        if self._consecutive_errors > 1:
            self._reconnect_cnt += 1


# ── Background thread ─────────────────────────────────────────────────────────

_provider: Optional[AlphaVantageProvider] = None
_bg_thread: Optional[threading.Thread] = None
_obs_buffer: deque = deque(maxlen=_MAX_OBS)  # timestamped snapshots
_obs_lock = threading.Lock()
_MODULE_STARTED = False


def start(provider: Optional[VolatilityDataProvider] = None) -> None:
    """Start the background refresh thread. Called once at boot when flag is ON."""
    global _provider, _bg_thread, _MODULE_STARTED
    if not VOL_INTELLIGENCE_ENABLED:
        logger.info("VolatilityIntelligence: disabled (VOL_INTELLIGENCE_ENABLED=0)")
        return
    _assert_observe_only("start()")
    _provider = provider or AlphaVantageProvider()
    _bg_thread = threading.Thread(target=_bg_loop, daemon=True, name="vol-intelligence-bg")
    _bg_thread.start()
    _MODULE_STARTED = True
    logger.info(
        "VolatilityIntelligence: started — provider=%s observe_only=%s",
        type(_provider).__name__, VOL_INTELLIGENCE_OBSERVE_ONLY,
    )


def _bg_loop() -> None:
    """Daemon loop: refresh provider data, snapshot analysis into buffer."""
    while True:
        try:
            if isinstance(_provider, AlphaVantageProvider):
                _provider.maybe_refresh()
            snap = _build_snapshot()
            with _obs_lock:
                _obs_buffer.append(snap)
        except Exception as exc:
            logger.debug("VolatilityIntelligence bg loop error: %s", exc)
        time.sleep(30)  # analysis snapshots every 30s; provider fetches internally


# ── Analysis engine ───────────────────────────────────────────────────────────

def _safe_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _build_snapshot() -> Dict[str, Any]:
    """Produce the full volatility-intelligence snapshot from current provider data."""
    _assert_observe_only("_build_snapshot()")
    now_s = datetime.now(timezone.utc).isoformat()

    if _provider is None:
        return {**_NEUTRAL_SNAPSHOT, "enabled": True, "timestamp_utc": now_s}

    vix_rec  = _provider.get_latest_vix()
    history  = _provider.get_vix_history()
    ph       = _provider.get_provider_status()

    price      = vix_rec.get("price")
    s_high     = vix_rec.get("session_high")
    s_low      = vix_rec.get("session_low")
    change_pct = vix_rec.get("change_pct")

    # Freshness
    age_sec = None
    if vix_rec.get("timestamp_utc"):
        try:
            ts = datetime.fromisoformat(vix_rec["timestamp_utc"])
            age_sec = (datetime.now(timezone.utc) - ts).total_seconds()
        except Exception:
            pass
    is_fresh  = age_sec is not None and age_sec < _FRESHNESS_SEC
    data_status = vix_rec.get("status", "UNAVAILABLE")
    if age_sec is not None and age_sec > _FRESHNESS_SEC and data_status not in ("ERROR", "UNAVAILABLE"):
        data_status = "STALE"

    vix_out = {**vix_rec, "age_seconds": round(age_sec, 1) if age_sec is not None else None,
               "is_fresh": is_fresh, "status": data_status}

    if price is None or not is_fresh:
        snap = {
            **_NEUTRAL_SNAPSHOT,
            "enabled":        True,
            "observe_only":   VOL_INTELLIGENCE_OBSERVE_ONLY,
            "timestamp_utc":  now_s,
            "data_status":    data_status,
            "vix":            vix_out,
            "provider_health":ph,
        }
        if not is_fresh and data_status not in ("UNAVAILABLE", "ERROR"):
            snap["warnings"] = ["VIX data is stale — excluded from trading context"]
        return snap

    # ── Regime ───────────────────────────────────────────────────────────────
    regime = (
        "CALM"     if price < _CALM_MAX    else
        "NORMAL"   if price < _NORMAL_MAX  else
        "ELEVATED" if price < _ELEVATED_MAX else
        "EXTREME"
    )

    # ── Direction & velocity (from history) ──────────────────────────────────
    direction    = "UNKNOWN"
    velocity     = "UNKNOWN"
    acceleration = "UNKNOWN"
    reasons: List[str] = []
    change_1m = change_5m = change_15m = change_30m = None

    if history and len(history) >= 2:
        prices = [o["price"] for o in history if o.get("price") is not None]
        if len(prices) >= 2:
            change_5m = prices[-1] - prices[-2]        # most recent 5-min bar change
        if len(prices) >= 4:
            change_15m = prices[-1] - prices[-4]
        if len(prices) >= 7:
            change_30m = prices[-1] - prices[-7]

        if change_5m is not None:
            abs5 = abs(change_5m)
            if abs5 < 0.05:
                direction, velocity = "FLAT",    "SLOW"
            elif abs5 < 0.20:
                direction = "RISING" if change_5m > 0 else "FALLING"
                velocity  = "SLOW"
            elif abs5 < 0.50:
                direction = "RISING" if change_5m > 0 else "FALLING"
                velocity  = "MODERATE"
            else:
                direction = "RISING" if change_5m > 0 else "FALLING"
                velocity  = "FAST"

        # Acceleration: compare absolute magnitude of most-recent vs prior 5-min slope.
        # INCREASING = momentum growing (abs rate of change expanding),
        # DECREASING = momentum shrinking (abs rate of change contracting).
        if len(prices) >= 4:
            slope_recent = prices[-1] - prices[-2]
            slope_prior  = prices[-2] - prices[-3]
            if abs(slope_recent - slope_prior) < 0.02:
                acceleration = "STABLE"
            elif abs(slope_recent) > abs(slope_prior):
                acceleration = "INCREASING"
            else:
                acceleration = "DECREASING"

    # ── Session percentile ───────────────────────────────────────────────────
    session_pct = None
    if s_high is not None and s_low is not None and s_high > s_low:
        session_pct = round(((price - s_low) / (s_high - s_low)) * 100, 1)

    # ── Risk tone ────────────────────────────────────────────────────────────
    risk_tone = "UNKNOWN"
    if regime == "CALM" and direction in ("FALLING", "FLAT"):
        risk_tone = "RISK_ON"
    elif regime == "EXTREME" or (regime == "ELEVATED" and velocity == "FAST" and direction == "RISING"):
        risk_tone = "RISK_OFF_SHOCK"
    elif regime in ("ELEVATED", "NORMAL") and direction == "RISING":
        risk_tone = "RISK_OFF_PRESSURE"
    elif regime in ("CALM", "NORMAL"):
        risk_tone = "NEUTRAL"
    else:
        risk_tone = "NEUTRAL"

    # ── Equity context (what VIX environment means for equity-index longs/shorts)
    equity_context = "UNKNOWN"
    if direction == "RISING" and velocity in ("MODERATE", "FAST"):
        equity_context = "HEADWIND_FOR_LONGS"
    elif direction == "FALLING" and velocity in ("MODERATE", "FAST"):
        equity_context = "TAILWIND_FOR_LONGS"
    elif direction == "FLAT" or velocity == "SLOW":
        equity_context = "MIXED"
    else:
        equity_context = "NEUTRAL"

    # ── Confidence (0-100, based on data quality + signal clarity) ───────────
    confidence = 0
    if is_fresh and price is not None:
        confidence += 40
    if direction != "UNKNOWN":
        confidence += 20
    if velocity != "UNKNOWN":
        confidence += 15
    if session_pct is not None:
        confidence += 15
    if acceleration != "UNKNOWN":
        confidence += 10

    # ── Reasons ──────────────────────────────────────────────────────────────
    reasons = []
    if regime != "UNKNOWN":
        reasons.append(f"VIX regime is {regime} (price={price:.2f})")
    if direction not in ("UNKNOWN", "FLAT") and velocity != "UNKNOWN":
        reasons.append(f"VIX is {direction.lower()} at {velocity.lower()} velocity over the last 5 minutes")
    if change_15m is not None:
        reasons.append(
            f"VIX has {'risen' if change_15m > 0 else 'fallen'} "
            f"{abs(change_15m):.2f} points over the last 15 minutes"
        )
    if acceleration not in ("UNKNOWN", "STABLE") and direction not in ("UNKNOWN", "FLAT"):
        reasons.append(f"Short-term rate of change is {acceleration.lower()}")
    if session_pct is not None:
        pct_label = "above the session midpoint" if session_pct > 50 else "below the session midpoint"
        reasons.append(f"VIX is at the {session_pct:.0f}th session percentile ({pct_label})")

    warnings = []
    if data_status == "DELAYED":
        warnings.append(f"VIX data is delayed (Alpha Vantage; age {int(age_sec or 0)}s)")
    if not VOL_INTELLIGENCE_OBSERVE_ONLY:
        warnings.append("OBSERVE_ONLY is OFF — check safety flags before activating execution influence")

    # ── Per-instrument context ────────────────────────────────────────────────
    inst_ctx: Dict[str, Any] = {}
    for inst, relevance in _INSTRUMENT_RELEVANCE.items():
        if inst == "MGC":
            ctx = {
                "relevance":  relevance,
                "context":    "INDIRECT_ONLY",
                "confidence": max(0, confidence - 43),
                "note":       _MGC_INDIRECT_NOTE,
            }
        else:
            ctx = {
                "relevance":  relevance,
                "context":    equity_context,
                "confidence": confidence,
            }
            if relevance == "MEDIUM_HIGH":
                ctx["confidence"] = max(0, confidence - 14)
        inst_ctx[inst] = ctx

    return {
        "enabled":            True,
        "observe_only":       VOL_INTELLIGENCE_OBSERVE_ONLY,
        "timestamp_utc":      now_s,
        "data_status":        data_status,
        "vix":                vix_out,
        "direction":          direction,
        "velocity":           velocity,
        "acceleration":       acceleration,
        "session_percentile": session_pct,
        "regime":             regime,
        "risk_tone":          risk_tone,
        "equity_context":     equity_context,
        "confidence":         confidence,
        "reasons":            reasons[:3],
        "warnings":           warnings,
        "execution_effect":   "NONE",   # immutable in Phase B-E
        "score_effect":       0,        # immutable in Phase B-E
        "instrument_context": inst_ctx,
        "provider_health":    ph,
        # Rate-of-change details
        "change_5m":  round(change_5m, 3)  if change_5m  is not None else None,
        "change_15m": round(change_15m, 3) if change_15m is not None else None,
        "change_30m": round(change_30m, 3) if change_30m is not None else None,
    }


# ── Public API ────────────────────────────────────────────────────────────────

def get_snapshot() -> Dict[str, Any]:
    """
    Return the most recent analysis snapshot.
    Always returns a well-formed dict. Never raises.
    When disabled → returns _NEUTRAL_SNAPSHOT (enabled=False).
    """
    if not VOL_INTELLIGENCE_ENABLED:
        return _NEUTRAL_SNAPSHOT
    try:
        with _obs_lock:
            if _obs_buffer:
                return dict(_obs_buffer[-1])
    except Exception:
        pass
    # Module enabled but no snapshot yet — build on-demand (safe fallback)
    try:
        return _build_snapshot()
    except Exception as exc:
        logger.debug("vol_intelligence.get_snapshot fallback error: %s", exc)
        return {**_NEUTRAL_SNAPSHOT, "enabled": True,
                "warnings": [f"Snapshot unavailable: {exc}"]}


def get_left_brain_block(inst: str = "") -> Dict[str, Any]:
    """
    Return a compact version of the snapshot suitable for left_brain inclusion.
    Adds provenance fields as required by the spec.
    """
    snap = get_snapshot()
    inst_ctx = snap.get("instrument_context", {}).get(inst, {})
    return {
        "enabled":           snap.get("enabled", False),
        "observe_only":      snap.get("observe_only", True),
        "source":            "alpha_vantage",
        "source_timestamp":  snap.get("vix", {}).get("timestamp_utc"),
        "analysis_timestamp":snap.get("timestamp_utc"),
        "freshness":         snap.get("vix", {}).get("status", "UNAVAILABLE"),
        "is_delayed":        True,
        "regime":            snap.get("regime", "UNKNOWN"),
        "direction":         snap.get("direction", "UNKNOWN"),
        "velocity":          snap.get("velocity", "UNKNOWN"),
        "risk_tone":         snap.get("risk_tone", "UNKNOWN"),
        "equity_context":    snap.get("equity_context", "UNKNOWN"),
        "vix_price":         snap.get("vix", {}).get("price"),
        "vix_change_pct":    snap.get("vix", {}).get("change_pct"),
        "confidence":        snap.get("confidence", 0),
        "instrument_context":inst_ctx,
        "reasons":           snap.get("reasons", []),
        "warnings":          snap.get("warnings", []),
        "execution_effect":  "NONE",
        "score_effect":      0,
    }


def get_history_summary() -> Dict[str, Any]:
    """Return a lightweight summary of recent observation buffer for the API endpoint."""
    with _obs_lock:
        buf = list(_obs_buffer)
    if not buf:
        return {"count": 0, "oldest_utc": None, "newest_utc": None}
    prices = [b.get("vix", {}).get("price") for b in buf if b.get("vix", {}).get("price") is not None]
    return {
        "count":      len(buf),
        "oldest_utc": buf[0].get("timestamp_utc"),
        "newest_utc": buf[-1].get("timestamp_utc"),
        "price_min":  min(prices) if prices else None,
        "price_max":  max(prices) if prices else None,
        "price_mean": round(sum(prices) / len(prices), 2) if prices else None,
    }
