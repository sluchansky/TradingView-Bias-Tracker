"""
Canonical Databento-derived Market State Engine  — SHADOW / OBSERVATION ONLY
=============================================================================
Phase: DATABENTO CANONICAL MARKET STATE

Safety contract
---------------
* All source selectors default to LEGACY.  A missing env var can NEVER
  promote Databento calculations into live trading.
* No component may be LIVE_CANONICAL in this phase.
* Engine failure is isolated per instrument.
* Comparison-store writes are fail-open; a DB error never blocks trading.
* The engine has no write path into READY/WAIT/edge_score/stop/TP/sizing.

Feature flags
-------------
CANONICAL_MARKET_STATE_ENABLED=1   (default 1 — run shadow calculations)
CANONICAL_MARKET_STATE_SHADOW_ONLY=1 (default 1 — never promote to live)
VWAP_SOURCE=legacy         (legacy | databento)
STRUCTURE_SOURCE=legacy    (legacy | databento)
CVD_SOURCE=legacy          (legacy | databento)
SWEEP_SOURCE=legacy        (legacy | databento)
FVG_SOURCE=legacy          (legacy | databento)
ZONE_SOURCE=legacy         (legacy | databento)

Public API
----------
start(databento_brain, cvd_by_ticker, rvol_by_ticker, vwap_by_ticker)
    Boot the engine.  Must be called once at application start.

get_canonical_market_state(instrument) -> dict
get_all_canonical_market_states() -> dict[inst -> dict]
    Thread-safe reads of the current snapshot per instrument.

on_bar_close(inst, close_price)
    Bar-close callback for registration with DatabentoBrain.

record_legacy_comparison(inst, component, legacy_value, db_value, meta=None)
    Persist a source comparison row (fail-open).
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Feature flags ─────────────────────────────────────────────────────────────
# Default to shadow-only.  Missing var → safe default.
CMS_ENABLED = os.environ.get("CANONICAL_MARKET_STATE_ENABLED", "1").strip() == "1"
CMS_SHADOW_ONLY = os.environ.get("CANONICAL_MARKET_STATE_SHADOW_ONLY", "1").strip() == "1"

# Source selectors — all default LEGACY (fail-safe)
VWAP_SOURCE      = os.environ.get("VWAP_SOURCE",      "legacy").strip().lower()
STRUCTURE_SOURCE = os.environ.get("STRUCTURE_SOURCE", "legacy").strip().lower()
CVD_SOURCE       = os.environ.get("CVD_SOURCE",       "legacy").strip().lower()
SWEEP_SOURCE     = os.environ.get("SWEEP_SOURCE",     "legacy").strip().lower()
FVG_SOURCE       = os.environ.get("FVG_SOURCE",       "legacy").strip().lower()
ZONE_SOURCE      = os.environ.get("ZONE_SOURCE",      "legacy").strip().lower()

# ── Constants ─────────────────────────────────────────────────────────────────
INSTRUMENTS            = ("MGC", "MNQ", "MES", "MYM")
ATR_PERIOD             = 14
SWING_LOOKBACK         = 5      # bars each side for pivot detection
STALE_THRESHOLD_S      = 300    # 5 min — component goes STALE after this
WARMUP_BARS            = ATR_PERIOD + SWING_LOOKBACK * 2 + 1
SESSION_RESET_UTC_HOUR = 22     # 22:00 UTC ≈ 6 PM ET (summer, UTC-4)
COMPARISON_RATE_LIMIT_S = 60    # min seconds between comparison log bursts
MAX_SWEEP_HISTORY      = 10
MAX_STRUCTURE_EVENTS   = 50

# ── Component health / promotion states ───────────────────────────────────────
HEALTHY               = "HEALTHY"
STALE                 = "STALE"
INSUFFICIENT_HISTORY  = "INSUFFICIENT_HISTORY"
DATA_UNAVAILABLE      = "DATA_UNAVAILABLE"
CALCULATION_ERROR     = "CALCULATION_ERROR"

SHADOW                = "SHADOW"          # all components start here
VALIDATING            = "VALIDATING"      # comparison data being collected
READY_FOR_PROMOTION   = "READY_FOR_PROMOTION"
LIVE_CANONICAL        = "LIVE_CANONICAL"  # never allowed this phase

# ── Internal data structures ──────────────────────────────────────────────────

@dataclass
class SwingPoint:
    price:     float
    bar_ts:    float   # Unix seconds — bar open timestamp
    direction: str     # "HIGH" | "LOW"
    confirmed: bool = True


@dataclass
class StructureEvent:
    event_type: str    # "BOS" | "CHOCH" | "HH" | "HL" | "LH" | "LL"
    direction:  str    # "BULLISH" | "BEARISH"
    price:      float
    created_at: float  # wall-clock (time.time())
    bar_ts:     float
    timeframe:  str = "1m"

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "direction":  self.direction,
            "price":      self.price,
            "timestamp":  _iso(self.created_at),
            "bar_ts":     self.bar_ts,
            "timeframe":  self.timeframe,
        }


@dataclass
class SweepEvent:
    instrument:     str
    direction:      str    # "BULLISH_SWEEP" | "BEARISH_SWEEP"
    swept_level:    float
    sweep_price:    float
    created_at:     float
    bar_ts:         float
    reclaim_status: str = "RECLAIMED"  # wicked AND closed back — always reclaimed
    event_id:       str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def to_dict(self) -> dict:
        return {
            "direction":      self.direction,
            "swept_level":    self.swept_level,
            "sweep_price":    self.sweep_price,
            "reclaim_status": self.reclaim_status,
            "event_id":       self.event_id,
            "timestamp":      _iso(self.created_at),
            "bar_ts":         self.bar_ts,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _session_start(ts: float) -> float:
    """Return Unix seconds for the start of the CME session that contains ts.

    CME equity-index and micro-gold futures trade Sunday–Friday with a
    daily maintenance window 17:00–18:00 ET.  The trading 'session' that
    VWAP resets to starts at 18:00 ET (22:00 UTC in summer / 23:00 UTC
    in winter).  We use a fixed UTC offset matching the app convention
    (UTC-4) so session resets are stable across DST transitions for the
    purposes of shadow VWAP comparison.
    """
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    # Boundary = SESSION_RESET_UTC_HOUR:00 UTC each calendar day
    boundary = dt.replace(
        hour=SESSION_RESET_UTC_HOUR, minute=0, second=0, microsecond=0
    )
    if dt < boundary:
        boundary -= timedelta(days=1)
    return boundary.timestamp()


def _freshness(last_update: Optional[float]) -> str:
    if last_update is None:
        return DATA_UNAVAILABLE
    age = time.time() - last_update
    return HEALTHY if age < STALE_THRESHOLD_S else STALE


# ── Per-instrument engine ─────────────────────────────────────────────────────

class CanonicalMarketStateEngine:
    """Shadow market-state calculator for one instrument.

    All heavy work happens in on_bar_close().  Reads of get_snapshot() are
    cheap — they return a pre-built dict under the RLock.
    """

    def __init__(self, instrument: str) -> None:
        self.instrument  = instrument
        self._lock       = threading.RLock()

        # Rolling bar buffer — shared across all component calculations
        self._bars: deque = deque(maxlen=max(WARMUP_BARS + 10, 60))

        # ── VWAP state ────────────────────────────────────────────────────────
        self._session_start_ts: Optional[float] = None
        self._pv_sum:  float = 0.0
        self._v_sum:   float = 0.0
        self._vwap:    Optional[float] = None
        self._vwap_updated: Optional[float] = None

        # ── Volume / RVOL ────────────────────────────────────────────────────
        self._session_volume: float = 0.0
        self._vol_history:    deque = deque(maxlen=25)  # recent closed bars
        self._rvol:           Optional[float] = None
        self._vol_regime:     str = "UNKNOWN"

        # ── ATR (Wilder) ─────────────────────────────────────────────────────
        self._tr_history:  deque = deque(maxlen=ATR_PERIOD * 3)
        self._atr:         Optional[float] = None
        self._prev_close:  Optional[float] = None
        self._atr_updated: Optional[float] = None

        # ── Market structure ─────────────────────────────────────────────────
        self._swing_highs:     deque = deque(maxlen=20)
        self._swing_lows:      deque = deque(maxlen=20)
        self._struct_dir:      str = "UNKNOWN"   # BULLISH | BEARISH | UNKNOWN
        self._last_bos:        Optional[StructureEvent] = None
        self._last_choch:      Optional[StructureEvent] = None
        self._struct_events:   deque = deque(maxlen=MAX_STRUCTURE_EVENTS)
        self._struct_updated:  Optional[float] = None

        # ── Sweeps ───────────────────────────────────────────────────────────
        self._sweeps: deque = deque(maxlen=MAX_SWEEP_HISTORY)

        # ── Counters ─────────────────────────────────────────────────────────
        self._bars_received: int = 0
        self._last_price:    Optional[float] = None
        self._last_bar_ts:   Optional[float] = None

    # ── Main entry point ──────────────────────────────────────────────────────

    def on_bar_close(self, bar: Dict[str, Any]) -> None:
        """Process one closed 1m bar.  Called from bar-close callback thread."""
        with self._lock:
            try:
                self._bars.append(bar)
                self._bars_received += 1
                ts = bar.get("ts", time.time())
                self._last_bar_ts = ts
                c = bar.get("close")
                if c is not None:
                    self._last_price = c

                self._calc_vwap(bar, ts)
                self._calc_volume(bar)
                self._calc_atr(bar)
                self._calc_structure(ts)
                self._calc_sweeps(bar, ts)

            except Exception as exc:  # noqa: BLE001
                logger.debug("CMS[%s] on_bar_close error: %s", self.instrument, exc)

    # ── Component calculators ─────────────────────────────────────────────────

    def _calc_vwap(self, bar: dict, ts: float) -> None:
        h  = bar.get("high")
        lo = bar.get("low")
        c  = bar.get("close")
        v  = bar.get("volume") or 0
        if None in (h, lo, c):
            return

        sess = _session_start(ts)
        if sess != self._session_start_ts:
            # New session — reset accumulators
            self._pv_sum = 0.0
            self._v_sum  = 0.0
            self._session_start_ts = sess
            self._session_volume = 0.0

        typical = (h + lo + c) / 3.0
        self._pv_sum += typical * v
        self._v_sum  += v

        if self._v_sum > 0:
            self._vwap = self._pv_sum / self._v_sum
            self._vwap_updated = time.time()

    def _calc_volume(self, bar: dict) -> None:
        v = bar.get("volume") or 0
        self._session_volume += v
        if v:
            self._vol_history.append(v)

        hist = list(self._vol_history)
        if len(hist) >= 2:
            baseline = sum(hist[:-1]) / (len(hist) - 1)
            if baseline > 0:
                self._rvol = round(v / baseline, 3)
                self._vol_regime = (
                    "HIGH"     if self._rvol >= 2.0 else
                    "ELEVATED" if self._rvol >= 1.3 else
                    "NORMAL"
                )

    def _calc_atr(self, bar: dict) -> None:
        h  = bar.get("high")
        lo = bar.get("low")
        c  = bar.get("close")
        if None in (h, lo, c):
            return

        tr = (
            max(h - lo, abs(h - self._prev_close), abs(lo - self._prev_close))
            if self._prev_close is not None else h - lo
        )
        self._tr_history.append(tr)
        self._prev_close = c

        n = len(self._tr_history)
        if n >= ATR_PERIOD:
            if self._atr is None:
                self._atr = sum(list(self._tr_history)[-ATR_PERIOD:]) / ATR_PERIOD
            else:
                self._atr = (self._atr * (ATR_PERIOD - 1) + tr) / ATR_PERIOD
            self._atr_updated = time.time()

    def _calc_structure(self, ts: float) -> None:
        """Deterministic swing-pivot structure detection from closed bars.

        A pivot HIGH is confirmed when its bar.high is strictly greater than
        the SWING_LOOKBACK bars on each side.  Detection fires SWING_LOOKBACK
        bars after the actual pivot bar (we only confirm once right bars exist).

        BOS  = close breaks through last confirmed opposing swing point.
        CHoCH = BOS that reverses the current dominant structure direction.
        """
        bars = list(self._bars)
        n    = len(bars)
        if n < SWING_LOOKBACK * 2 + 1:
            return

        # Pivot candidate is the bar at index -(SWING_LOOKBACK + 1)
        pivot_idx = n - SWING_LOOKBACK - 1
        if pivot_idx < SWING_LOOKBACK:
            return

        pivot     = bars[pivot_idx]
        left_bars = bars[pivot_idx - SWING_LOOKBACK : pivot_idx]
        right_bars= bars[pivot_idx + 1 : pivot_idx + SWING_LOOKBACK + 1]
        p_h  = pivot.get("high",  0)
        p_l  = pivot.get("low",   float("inf"))
        p_ts = pivot.get("ts",    ts)

        # ── Swing HIGH pivot ──────────────────────────────────────────────────
        if (p_h > 0 and
                all(b.get("high", 0) < p_h for b in left_bars) and
                all(b.get("high", 0) < p_h for b in right_bars)):
            if not any(abs(s.bar_ts - p_ts) < 60 for s in self._swing_highs):
                sp = SwingPoint(price=p_h, bar_ts=p_ts, direction="HIGH")
                self._swing_highs.append(sp)
                # Classify HH / LH
                highs = [s.price for s in list(self._swing_highs)[:-1]]
                if highs:
                    evt_type = "HH" if p_h > max(highs) else "LH"
                    self._struct_events.append(
                        StructureEvent(evt_type, "BULLISH" if evt_type == "HH" else "BEARISH",
                                       p_h, time.time(), p_ts)
                    )
                    self._struct_updated = time.time()

        # ── Swing LOW pivot ───────────────────────────────────────────────────
        if (p_l < float("inf") and
                all(b.get("low", float("inf")) > p_l for b in left_bars) and
                all(b.get("low", float("inf")) > p_l for b in right_bars)):
            if not any(abs(s.bar_ts - p_ts) < 60 for s in self._swing_lows):
                sp = SwingPoint(price=p_l, bar_ts=p_ts, direction="LOW")
                self._swing_lows.append(sp)
                # Classify HL / LL
                lows = [s.price for s in list(self._swing_lows)[:-1]]
                if lows:
                    evt_type = "HL" if p_l > min(lows) else "LL"
                    self._struct_events.append(
                        StructureEvent(evt_type, "BULLISH" if evt_type == "HL" else "BEARISH",
                                       p_l, time.time(), p_ts)
                    )
                    self._struct_updated = time.time()

        # ── BOS / CHoCH from current close ────────────────────────────────────
        curr = bars[-1]
        c    = curr.get("close")
        if c is None or not self._swing_highs or not self._swing_lows:
            return

        last_sh = list(self._swing_highs)[-1]
        last_sl = list(self._swing_lows)[-1]

        # Bullish BOS: close above last confirmed swing high
        if c > last_sh.price:
            new_dir = "BULLISH"
            evt = StructureEvent("BOS", "BULLISH", last_sh.price, time.time(), curr.get("ts", ts))
            if self._last_bos is None or abs(self._last_bos.price - last_sh.price) > 0.01:
                self._last_bos = evt
                self._struct_events.append(evt)
                if self._struct_dir == "BEARISH":
                    choch = StructureEvent("CHOCH", "BULLISH", last_sh.price, time.time(), curr.get("ts", ts))
                    self._last_choch = choch
                    self._struct_events.append(choch)
                self._struct_dir = new_dir
                self._struct_updated = time.time()

        # Bearish BOS: close below last confirmed swing low
        elif c < last_sl.price:
            evt = StructureEvent("BOS", "BEARISH", last_sl.price, time.time(), curr.get("ts", ts))
            if self._last_bos is None or abs(self._last_bos.price - last_sl.price) > 0.01:
                self._last_bos = evt
                self._struct_events.append(evt)
                if self._struct_dir == "BULLISH":
                    choch = StructureEvent("CHOCH", "BEARISH", last_sl.price, time.time(), curr.get("ts", ts))
                    self._last_choch = choch
                    self._struct_events.append(choch)
                self._struct_dir = "BEARISH"
                self._struct_updated = time.time()

    def _calc_sweeps(self, bar: dict, ts: float) -> None:
        """Liquidity sweep: wick beyond a recent swing level, close back inside."""
        h  = bar.get("high")
        lo = bar.get("low")
        c  = bar.get("close")
        if None in (h, lo, c):
            return

        if self._swing_highs:
            last_sh = list(self._swing_highs)[-1]
            if h > last_sh.price and c < last_sh.price:
                self._sweeps.append(SweepEvent(
                    instrument=self.instrument, direction="BEARISH_SWEEP",
                    swept_level=last_sh.price, sweep_price=h,
                    created_at=time.time(), bar_ts=ts,
                ))

        if self._swing_lows:
            last_sl = list(self._swing_lows)[-1]
            if lo < last_sl.price and c > last_sl.price:
                self._sweeps.append(SweepEvent(
                    instrument=self.instrument, direction="BULLISH_SWEEP",
                    swept_level=last_sl.price, sweep_price=lo,
                    created_at=time.time(), bar_ts=ts,
                ))

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def get_snapshot(self) -> dict:
        """Return a JSON-serialisable snapshot of current canonical state."""
        with self._lock:
            now        = time.time()
            bars_avail = len(self._bars)
            atr_avail  = len(self._tr_history)
            warm       = bars_avail >= WARMUP_BARS

            # VWAP
            last_price = self._last_price
            vwap_val   = self._vwap
            vwap_health = _freshness(self._vwap_updated) if vwap_val is not None else INSUFFICIENT_HISTORY
            if vwap_val and last_price:
                vwap_dist   = round(last_price - vwap_val, 4)
                vwap_dist_pct = round(vwap_dist / vwap_val * 100, 4)
                vwap_side   = "AT" if abs(vwap_dist) < 0.5 else ("ABOVE" if vwap_dist > 0 else "BELOW")
            else:
                vwap_dist = vwap_dist_pct = None
                vwap_side = "UNKNOWN"

            # ATR
            atr_val    = self._atr
            atr_health = _freshness(self._atr_updated) if atr_val is not None else (
                INSUFFICIENT_HISTORY if atr_avail < ATR_PERIOD else DATA_UNAVAILABLE
            )
            atr_pct    = round(atr_val / last_price * 100, 4) if (atr_val and last_price) else None

            # Structure
            sh_list = list(self._swing_highs)
            sl_list = list(self._swing_lows)
            # Structure detection is inherently slow (needs pivot confirmation on both sides).
            # Unknown direction always means INSUFFICIENT_HISTORY — never DATA_UNAVAILABLE —
            # because it's a signal-arrival problem, not a configuration problem.
            struct_health = HEALTHY if self._struct_dir != "UNKNOWN" else INSUFFICIENT_HISTORY

            # Sweeps
            sweep_list = [s.to_dict() for s in list(self._sweeps)[-5:]]

            return {
                "instrument":   self.instrument,
                "timestamp":    _iso(now),
                "source":       "databento",
                "data_age_ms":  round((now - self._vwap_updated) * 1000) if self._vwap_updated else None,
                "last_price":   last_price,
                "warmup": {
                    "complete":       warm,
                    "bars_required":  WARMUP_BARS,
                    "bars_available": bars_avail,
                },
                "vwap": {
                    "value":        round(vwap_val, 4) if vwap_val else None,
                    "distance":     vwap_dist,
                    "distance_pct": vwap_dist_pct,
                    "side":         vwap_side,
                    "sample_volume":round(self._v_sum, 0),
                    "source":       "databento",
                    "health":       vwap_health,
                    "promotion_status": SHADOW,
                },
                "volume": {
                    "session_cumulative": self._session_volume,
                    "relative_volume":    self._rvol,
                    "regime":             self._vol_regime,
                    "source":             "databento",
                    "health":             HEALTHY if self._rvol is not None else INSUFFICIENT_HISTORY,
                    "promotion_status":   SHADOW,
                },
                "atr": {
                    "value":            round(atr_val, 4) if atr_val else None,
                    "atr_pct":          atr_pct,
                    "source":           "databento",
                    "health":           atr_health,
                    "bars_available":   atr_avail,
                    "bars_required":    ATR_PERIOD,
                    "promotion_status": SHADOW,
                },
                "structure": {
                    "direction":  self._struct_dir,
                    "swing_high": {"price": sh_list[-1].price, "bar_ts": sh_list[-1].bar_ts} if sh_list else None,
                    "swing_low":  {"price": sl_list[-1].price, "bar_ts": sl_list[-1].bar_ts} if sl_list else None,
                    "last_bos":   self._last_bos.to_dict()   if self._last_bos   else None,
                    "last_choch": self._last_choch.to_dict() if self._last_choch else None,
                    "event_count":len(self._struct_events),
                    "source":     "databento",
                    "health":     struct_health,
                    "promotion_status": SHADOW,
                },
                "sweeps": {
                    "recent":           sweep_list,
                    "total_detected":   len(self._sweeps),
                    "source":           "databento",
                    "health":           HEALTHY,
                    "promotion_status": SHADOW,
                },
            }

    # ── Replay / determinism ──────────────────────────────────────────────────

    def reset_for_replay(self) -> None:
        """Clear all state for deterministic replay testing."""
        with self._lock:
            self._bars.clear()
            self._session_start_ts = None
            self._pv_sum = self._v_sum = 0.0
            self._vwap = self._vwap_updated = None
            self._session_volume = 0.0
            self._vol_history.clear()
            self._rvol = None
            self._vol_regime = "UNKNOWN"
            self._tr_history.clear()
            self._atr = self._prev_close = self._atr_updated = None
            self._swing_highs.clear()
            self._swing_lows.clear()
            self._struct_dir = "UNKNOWN"
            self._last_bos = self._last_choch = None
            self._struct_events.clear()
            self._struct_updated = None
            self._sweeps.clear()
            self._bars_received = 0
            self._last_price = self._last_bar_ts = None


# ── Module-level state ────────────────────────────────────────────────────────

_engines: Dict[str, CanonicalMarketStateEngine] = {}
_engines_lock = threading.Lock()
_started       = False

# References to shared state dicts injected at start()
_DATABENTO_BARS: Optional[Dict]  = None
_CVD_BY_TICKER:  Optional[Dict]  = None
_RVOL_BY_TICKER: Optional[Dict]  = None
_VWAP_BY_TICKER: Optional[Dict]  = None   # Yahoo-derived legacy VWAP

# Comparison rate-limiting
_last_comparison_log: Dict[str, float] = {}
_comparison_lock = threading.Lock()

# DB connection getter (injected at boot)
_get_db_fn = None

# ── Boot ──────────────────────────────────────────────────────────────────────

def start(
    databento_bars_by_inst: Dict,
    cvd_by_ticker:          Dict,
    rvol_by_ticker:         Dict,
    vwap_by_ticker:         Optional[Dict] = None,
    get_db_fn=None,
) -> None:
    """Boot the engine.  Call once at application start after DatabentoBrain.start()."""
    global _started, _DATABENTO_BARS, _CVD_BY_TICKER, _RVOL_BY_TICKER, _VWAP_BY_TICKER, _get_db_fn

    if not CMS_ENABLED:
        logger.info("CanonicalMarketState: disabled (CANONICAL_MARKET_STATE_ENABLED=0)")
        return

    _DATABENTO_BARS = databento_bars_by_inst
    _CVD_BY_TICKER  = cvd_by_ticker
    _RVOL_BY_TICKER = rvol_by_ticker
    _VWAP_BY_TICKER = vwap_by_ticker
    _get_db_fn      = get_db_fn

    with _engines_lock:
        for inst in INSTRUMENTS:
            _engines[inst] = CanonicalMarketStateEngine(inst)

    _started = True
    logger.info(
        "CANONICAL_STATE_STARTED instruments=%s shadow_only=%s",
        list(INSTRUMENTS), CMS_SHADOW_ONLY,
    )


# ── Bar-close callback (register with DatabentoBrain) ─────────────────────────

def on_bar_close(inst: str, _close_price: float) -> None:
    """Bar-close callback.  Registered via DatabentoBrain.register_bar_close_callback()."""
    if not _started or _DATABENTO_BARS is None:
        return
    try:
        bars_deque = _DATABENTO_BARS.get(inst)
        if not bars_deque:
            return
        # Read latest closed bar from the public deque
        bar = bars_deque[-1] if bars_deque else None
        if bar is None:
            return

        engine = _engines.get(inst)
        if engine is None:
            return

        engine.on_bar_close(bar)

        # Augment snapshot with external reusable sources
        _inject_external_sources(inst, engine)

        # Check if warmup just completed
        snap = engine.get_snapshot()
        if snap["warmup"]["complete"] and snap["warmup"]["bars_available"] == WARMUP_BARS:
            logger.info("CANONICAL_STATE_WARM instrument=%s bars=%d",
                        inst, snap["warmup"]["bars_available"])

        # Record shadow comparison for VWAP
        _maybe_compare_vwap(inst, engine)

    except Exception as exc:  # noqa: BLE001
        logger.debug("CMS on_bar_close[%s] error: %s", inst, exc)


def _inject_external_sources(inst: str, engine: CanonicalMarketStateEngine) -> None:
    """Pull in CVD/RVOL from shared dicts (already Databento-derived)."""
    # CVD and RVOL are already computed by DatabentoBrain — no duplication
    pass  # snapshots read from _CVD_BY_TICKER / _RVOL_BY_TICKER at read time


# ── Public read API ───────────────────────────────────────────────────────────

def get_canonical_market_state(instrument: str) -> Optional[dict]:
    """Return canonical state snapshot for one instrument.  None if engine not started."""
    engine = _engines.get(instrument)
    if engine is None:
        return None
    snap = engine.get_snapshot()

    # Augment with externally-derived Databento data (CVD, RVOL, trend, FVGs)
    _augment_snapshot(instrument, snap)
    return snap


def get_all_canonical_market_states() -> Dict[str, dict]:
    """Return snapshots for all four instruments."""
    return {inst: get_canonical_market_state(inst) or {} for inst in INSTRUMENTS}


def _augment_snapshot(inst: str, snap: dict) -> None:
    """Attach data from other Databento-derived modules (CVD, RVOL, trend, FVG)."""
    # ── CVD (already Databento-derived in DatabentoBrain) ─────────────────────
    cvd_rec = (_CVD_BY_TICKER or {}).get(inst) or {}
    snap["cvd"] = {
        "value":            cvd_rec.get("value"),
        "direction":        cvd_rec.get("state", "UNKNOWN"),
        "source":           "databento",
        "note":             "Reused from DatabentoBrain accumulator",
        "health":           HEALTHY if cvd_rec.get("value") is not None else DATA_UNAVAILABLE,
        "promotion_status": SHADOW,
    }

    # ── RVOL (already Databento-derived in DatabentoBrain) ────────────────────
    rvol_rec = (_RVOL_BY_TICKER or {}).get(inst) or {}
    if rvol_rec.get("value") is not None:
        snap["volume"]["databento_rvol"] = rvol_rec.get("value")
        snap["volume"]["databento_rvol_source"] = "databento_brain"

    # ── 15m / 4H Trend (trend_alignment module — already Databento-derived) ───
    try:
        import trend_alignment as _ta  # noqa: PLC0415
        mtf = _ta.MTF_STATE_BY_INST.get(inst) or {}
        t15 = mtf.get("trend_15m") or {}
        t4h = mtf.get("trend_4h")  or {}
        t15_dir = (t15.get("direction") or "UNKNOWN").upper()
        t4h_dir = (t4h.get("direction") or "UNKNOWN").upper()

        if t15_dir in ("BULLISH", "BEARISH") and t4h_dir in ("BULLISH", "BEARISH"):
            alignment = "ALIGNED_BULLISH" if t15_dir == t4h_dir == "BULLISH" else (
                "ALIGNED_BEARISH" if t15_dir == t4h_dir == "BEARISH" else "MIXED"
            )
        else:
            alignment = "UNKNOWN"

        snap["trend"] = {
            "trend_15m":       t15_dir,
            "trend_4h":        t4h_dir,
            "trend_alignment": alignment,
            "bars_15m":        t15.get("bars_count", 0),
            "bars_4h":         t4h.get("bars_count", 0),
            "source":          "databento",
            "note":            "Consumed from trend_alignment module",
            "health":          HEALTHY if alignment != "UNKNOWN" else INSUFFICIENT_HISTORY,
            "promotion_status":SHADOW,
        }
    except Exception as exc:  # noqa: BLE001
        snap["trend"] = {"health": CALCULATION_ERROR, "error": str(exc)[:80]}

    # ── FVG zones (fvg_engine — already Databento-derived) ───────────────────
    try:
        from fvg_engine import FVG_ZONES_BY_INST  # noqa: PLC0415
        zones = FVG_ZONES_BY_INST.get(inst) or []
        active_fvgs = [z for z in zones if not z.get("mitigated")]
        snap["fvg_zones"] = {
            "active_count":    len(active_fvgs),
            "total_count":     len(zones),
            "source":          "databento",
            "note":            "Consumed from fvg_engine module",
            "health":          HEALTHY,
            "promotion_status":SHADOW,
        }
    except Exception:  # noqa: BLE001
        snap["fvg_zones"] = {"health": DATA_UNAVAILABLE}

    # ── Legacy VWAP comparison ─────────────────────────────────────────────────
    legacy_vwap = None
    try:
        if _VWAP_BY_TICKER:
            vwap_rec = _VWAP_BY_TICKER.get(inst) or {}
            legacy_vwap = vwap_rec.get("vwap") if isinstance(vwap_rec, dict) else None
    except Exception:  # noqa: BLE001
        pass

    db_vwap = snap.get("vwap", {}).get("value")
    if legacy_vwap is not None and db_vwap is not None:
        diff = round(db_vwap - legacy_vwap, 4)
        snap["vwap_comparison"] = {
            "legacy_vwap":          legacy_vwap,
            "databento_vwap":       db_vwap,
            "absolute_difference":  diff,
            "agreement":            abs(diff) < 1.0,
        }


# ── Shadow comparison store ───────────────────────────────────────────────────

def record_legacy_comparison(
    inst:          str,
    component:     str,
    legacy_value:  Any,
    databento_val: Any,
    meta:          Optional[dict] = None,
) -> None:
    """Persist a source-comparison row.  Fail-open — never blocks trading."""
    if not _started or _get_db_fn is None:
        return
    try:
        import json as _json  # noqa: PLC0415
        db = _get_db_fn()
        if db is None:
            return
        cur = db.cursor()
        now = datetime.now(timezone.utc).isoformat()

        legacy_f  = float(legacy_value)  if legacy_value  is not None else None
        db_f      = float(databento_val) if databento_val is not None else None
        diff      = round(db_f - legacy_f, 6) if (legacy_f is not None and db_f is not None) else None
        agreement = abs(diff) < 1.0 if diff is not None else None

        cur.execute(
            """INSERT INTO market_state_source_comparisons
               (timestamp, instrument, component,
                legacy_value, databento_value,
                agreement, difference_numeric,
                metadata)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (now, inst, component, legacy_f, db_f, agreement, diff,
             _json.dumps(meta or {})),
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("CMS comparison record error (non-critical): %s", exc)


def _maybe_compare_vwap(inst: str, engine: CanonicalMarketStateEngine) -> None:
    """Rate-limited VWAP comparison write."""
    with _comparison_lock:
        key  = f"vwap:{inst}"
        last = _last_comparison_log.get(key, 0)
        if time.time() - last < COMPARISON_RATE_LIMIT_S:
            return
        _last_comparison_log[key] = time.time()

    try:
        if _VWAP_BY_TICKER is None:
            return
        vwap_rec   = (_VWAP_BY_TICKER.get(inst) or {})
        legacy_val = vwap_rec.get("vwap") if isinstance(vwap_rec, dict) else None
        db_val     = engine._vwap  # noqa: SLF001 — same module, OK
        if legacy_val is None or db_val is None:
            return
        diff = abs(db_val - legacy_val)
        if diff > 0.01:
            logger.debug(
                "SOURCE_COMPARISON_MISMATCH inst=%s comp=VWAP legacy=%.4f db=%.4f diff=%.4f",
                inst, legacy_val, db_val, diff,
            )
        record_legacy_comparison(inst, "vwap", legacy_val, db_val)
    except Exception:  # noqa: BLE001
        pass


# ── DB table DDL (called once at boot) ───────────────────────────────────────

def ensure_comparison_table(get_db_fn) -> bool:
    """Create market_state_source_comparisons table if not present.
    Returns True on success, False on error.  Never raises."""
    try:
        db  = get_db_fn()
        cur = db.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS market_state_source_comparisons (
                id                  SERIAL PRIMARY KEY,
                timestamp           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                instrument          VARCHAR(10)  NOT NULL,
                component           VARCHAR(40)  NOT NULL,
                legacy_source       VARCHAR(40)  DEFAULT 'legacy',
                legacy_value        DOUBLE PRECISION,
                databento_value     DOUBLE PRECISION,
                agreement           BOOLEAN,
                difference_numeric  DOUBLE PRECISION,
                difference_ticks    DOUBLE PRECISION,
                difference_seconds  DOUBLE PRECISION,
                legacy_freshness    VARCHAR(20),
                databento_freshness VARCHAR(20),
                metadata            JSONB DEFAULT '{}'::JSONB
            )
        """)
        db.commit()
        logger.info("market_state_source_comparisons table ready")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("market_state_source_comparisons DDL failed (non-critical): %s", exc)
        return False


# ── Deterministic replay API ─────────────────────────────────────────────────

def replay_bars(instrument: str, bars: List[Dict]) -> dict:
    """Feed a list of historical bars through the engine and return snapshot.
    Used for determinism testing — always resets state first.
    Returns the final snapshot after all bars processed."""
    engine = CanonicalMarketStateEngine(instrument)
    for bar in bars:
        engine.on_bar_close(bar)
    return engine.get_snapshot()
