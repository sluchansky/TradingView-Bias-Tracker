"""
DatabentoBrain — real-time market data engine for the trading bot.

Subscribes to Databento GLBX.MDP3 live feed (MGC / MNQ / MES / MYM),
builds 1-minute OHLCV bars, and continuously injects computed indicators
into app.py's shared state stores so the existing full_analysis / heartbeat
eval loop picks up fresh data with zero changes to the analysis engine.

State stores written (passed by reference from app.py):
    CURRENT_PRICE_BY_TICKER    ← live price on every trade tick
    CURRENT_PRICE_TS_BY_TICKER ← timestamp of above
    AUTO_PRICE_BY_TICKER       ← session VWAP + ATR (replaces Yahoo Finance)
    CVD_BY_TICKER              ← cumulative volume delta from trade tape
    RVOL_BY_TICKER             ← relative volume vs session rolling avg
    VOLUME_SPIKE_BY_TICKER     ← set when RVOL >= VOL_SPIKE_MULT (2.0×)
    ALERT_HISTORY              ← synthetic BOS / CHOCH structure alerts

TradingView webhook alerts continue to work alongside — zone confirms,
FVG/OB, and manual confirmations all supplement the Databento feed.
When DATABENTO_ENABLED=0 (default) this module is a complete no-op and
the system is byte-identical to the original.

Public surface (imported by app.py):
    DatabentoBrain          — main class; call .start() once at boot
    DATABENTO_BARS_BY_INST  — {inst: deque[bar_dict]}  for dashboard chart
    get_top_of_book_snapshot — fresh MBP-1 best-bid/best-ask snapshot per instrument
    DATABENTO_STATUS        — health / telemetry dict for /databento-status
"""
from __future__ import annotations

import logging
import math
import os
import queue
import time
import threading
import uuid
from collections import deque
from datetime import datetime, timezone, timedelta, date as _date
from typing import Any

logger = logging.getLogger(__name__)

# ── Instrument ↔ Databento continuous-contract symbol ────────────────────────
# MGC rolls earlier than the calendar front-month: TradingView MGC1! typically
# moves to the next active month (c.1) several weeks before Databento's c.0
# catches up.  Set DATABENTO_MGC_SYMBOL=MGC.c.0 to revert once Databento's
# continuous contract has rolled past the current delivery month.
_DB_MGC_SYMBOL = os.environ.get("DATABENTO_MGC_SYMBOL", "MGC.c.1")
DB_SYMBOLS: dict[str, str] = {
    "MGC": _DB_MGC_SYMBOL,
    "MNQ": "MNQ.c.0",
    "MES": "MES.c.0",
    "MYM": "MYM.c.0",
}
DB_DATASET = "GLBX.MDP3"
# MBP-1 supplies the best bid/ask and displayed size at each side of the book.
# Leave a kill switch for an exchange/feed incident without disrupting the trades
# subscription that powers price, CVD, bars, and VWAP.
DATABENTO_MBP1_ENABLED = os.environ.get("DATABENTO_MBP1_ENABLED", "1") == "1"
TOP_OF_BOOK_STALE_S = max(1.0, float(os.environ.get("TOP_OF_BOOK_STALE_S", "5")))
TOP_OF_BOOK_HISTORY_S = max(60.0, float(os.environ.get("TOP_OF_BOOK_HISTORY_S", "300")))
TOP_OF_BOOK_HISTORY_SAMPLE_S = max(0.25, float(os.environ.get("TOP_OF_BOOK_HISTORY_SAMPLE_S", "1")))
# The live iterator must never be held hostage by an expensive bar-close callback.
# Keep intake bounded and preserve stream order with one worker.  A dropped record
# makes the affected instrument unavailable for fresh-only consumers until reconnect.
RECORD_QUEUE_MAX = max(64, int(os.environ.get("DATABENTO_RECORD_QUEUE_MAX", "4096")))
RECORD_QUEUE_DELAY_S = max(1.0, float(os.environ.get("DATABENTO_RECORD_QUEUE_DELAY_S", "5")))
RECORD_QUEUE_STALE_S = max(RECORD_QUEUE_DELAY_S, float(os.environ.get("DATABENTO_RECORD_QUEUE_STALE_S", "120")))
# Native structure needs enough *closed* bars to confirm pivots, sweeps,
# confirmation volume, ATR, and RVOL without inventing a single candle.
STRUCTURE_WARMUP_BARS = max(
    32, int(os.environ.get("DATABENTO_STRUCTURE_WARMUP_BARS", "60"))
)
STRUCTURE_WARMUP_MAX_AGE_HOURS = max(
    6.0, float(os.environ.get("DATABENTO_STRUCTURE_WARMUP_MAX_AGE_HOURS", "72"))
)

# ── Public stores (read by Flask routes and the dashboard chart) ──────────────
# Each bar entry: {ts, open, high, low, close, volume, vwap?, atr?}
DATABENTO_BARS_BY_INST: dict[str, deque] = {
    inst: deque(maxlen=200) for inst in DB_SYMBOLS
}

# Shadow-only structure provenance.  This is intentionally separate from
# ALERT_HISTORY: structure consumers never read it, and losing it on restart is
# safe because it is an operator audit surface rather than trading evidence.
STRUCTURE_PROVENANCE_MAX = max(
    25, int(os.environ.get("STRUCTURE_PROVENANCE_MAX", "500"))
)
_STRUCTURE_PROVENANCE_BY_INST: dict[str, deque] = {
    inst: deque(maxlen=STRUCTURE_PROVENANCE_MAX) for inst in DB_SYMBOLS
}
_STRUCTURE_PROVENANCE_LOCK = threading.RLock()


def clear_structure_provenance() -> None:
    """Clear the in-memory shadow trace (primarily useful for test isolation)."""
    with _STRUCTURE_PROVENANCE_LOCK:
        for records in _STRUCTURE_PROVENANCE_BY_INST.values():
            records.clear()


def get_structure_provenance(
    instrument: str | None = None,
    *,
    limit: int = 50,
) -> list[dict[str, Any]] | dict[str, list[dict[str, Any]]]:
    """Return copied, bounded structure traces for read-only diagnostics."""
    safe_limit = max(1, min(int(limit), STRUCTURE_PROVENANCE_MAX))
    inst = str(instrument or "").upper().strip()
    with _STRUCTURE_PROVENANCE_LOCK:
        if inst:
            return [dict(item) for item in list(
                _STRUCTURE_PROVENANCE_BY_INST.get(inst, ())
            )[-safe_limit:]]
        return {
            key: [dict(item) for item in list(records)[-safe_limit:]]
            for key, records in _STRUCTURE_PROVENANCE_BY_INST.items()
        }


def get_latest_structure_provenance_id(instrument: str) -> str | None:
    """Return the current bar's opaque trace ID for callback correlation."""
    inst = str(instrument or "").upper().strip()
    with _STRUCTURE_PROVENANCE_LOCK:
        records = _STRUCTURE_PROVENANCE_BY_INST.get(inst)
        if not records:
            return None
        trace_id = records[-1].get("trace_id")
        return str(trace_id) if trace_id is not None else None


def annotate_structure_provenance(
    instrument: str,
    trace_id: str | None,
    *,
    structure_cycle: dict[str, Any] | None = None,
    gate_result: dict[str, Any] | None = None,
) -> bool:
    """Attach authoritative analysis output to one exact detector trace.

    This function only enriches a diagnostic copy.  It does not return anything
    used by the gate, scorer, strategy layer, or execution gateway.
    """
    inst = str(instrument or "").upper().strip()
    if not inst or not trace_id:
        return False
    with _STRUCTURE_PROVENANCE_LOCK:
        records = _STRUCTURE_PROVENANCE_BY_INST.get(inst)
        if not records:
            return False
        for record in reversed(records):
            if record.get("trace_id") != trace_id:
                continue
            if structure_cycle is not None:
                record["resolved_structure_cycle"] = dict(structure_cycle)
            if gate_result is not None:
                record["structure_gate"] = dict(gate_result)
            record["analysis_attached"] = bool(
                structure_cycle is not None or gate_result is not None
            )
            return True
    return False


def _append_structure_provenance(inst: str, record: dict[str, Any]) -> None:
    """Append a JSON-safe diagnostic record without affecting live state."""
    with _STRUCTURE_PROVENANCE_LOCK:
        _STRUCTURE_PROVENANCE_BY_INST.setdefault(
            inst, deque(maxlen=STRUCTURE_PROVENANCE_MAX)
        ).append(dict(record))

# Current in-progress (partial) 1m bar per instrument.
# Snapshot updated after every tick inside _partial_lock so Flask routes
# can read a consistent copy without acquiring the lock themselves.
# Each entry: {ts, open, high, low, close, volume} or None when no bar is open.
# Display-only — never read by full_analysis or the gate.
DATABENTO_PARTIAL_BY_INST: dict[str, Any] = {
    inst: None for inst in DB_SYMBOLS
}

# The mutable snapshots stay behind this lock. Readers must use
# get_top_of_book_snapshot(), which returns a copy only while the snapshot is
# fresh. This prevents an old book from quietly affecting Order Flow after a
# disconnect, reconnect, or quiet period.
DATABENTO_TOP_OF_BOOK_BY_INST: dict[str, dict[str, Any] | None] = {
    inst: None for inst in DB_SYMBOLS
}
_TOP_OF_BOOK_HISTORY_BY_INST: dict[str, deque] = {
    inst: deque(maxlen=int(TOP_OF_BOOK_HISTORY_S / TOP_OF_BOOK_HISTORY_SAMPLE_S) + 10)
    for inst in DB_SYMBOLS
}
_TOP_OF_BOOK_LAST_HISTORY_AT: dict[str, float] = {inst: 0.0 for inst in DB_SYMBOLS}
_TOP_OF_BOOK_LOCK = threading.RLock()


def clear_top_of_book_snapshots() -> None:
    """Discard all MBP-1 state, including on a live-feed reconnect."""
    with _TOP_OF_BOOK_LOCK:
        for inst in DATABENTO_TOP_OF_BOOK_BY_INST:
            DATABENTO_TOP_OF_BOOK_BY_INST[inst] = None
            _TOP_OF_BOOK_HISTORY_BY_INST[inst].clear()
            _TOP_OF_BOOK_LAST_HISTORY_AT[inst] = 0.0


def get_top_of_book_snapshot(
    inst: str,
    *,
    now_epoch: float | None = None,
    max_age_s: float | None = None,
) -> dict[str, Any] | None:
    """Return a copied, fresh top-of-book snapshot or None.

    The book is informational/fail-open: an absent or stale quote must look the
    same as unavailable data to the Order Flow engine.
    """
    if not inst:
        return None
    now_epoch = time.time() if now_epoch is None else float(now_epoch)
    max_age_s = TOP_OF_BOOK_STALE_S if max_age_s is None else float(max_age_s)
    with _TOP_OF_BOOK_LOCK:
        snapshot = DATABENTO_TOP_OF_BOOK_BY_INST.get(inst)
        if not isinstance(snapshot, dict):
            return None
        received_at = snapshot.get("_received_at")
        try:
            age_s = max(0.0, now_epoch - float(received_at))
        except (TypeError, ValueError):
            return None
        if age_s > max_age_s:
            return None
        out = {key: value for key, value in snapshot.items() if key != "_received_at"}
    out["age_s"] = round(age_s, 3)
    return out


def get_top_of_book_display(inst: str, *, now_epoch: float | None = None) -> dict[str, Any]:
    """Return a UI-safe MBP-1 view without ever serving an expired quote.

    This is intentionally separate from ``get_top_of_book_snapshot``: consumers
    of a display need to distinguish a feed that has gone stale from one that has
    not produced a quote yet, while neither condition may expose old bid/ask
    sizes.  It is advisory-only and does not participate in any trade decision.
    """
    empty = {
        "available": False,
        "state": "UNAVAILABLE",
        "instrument": inst,
        "bid_size": None,
        "ask_size": None,
        "imbalance": None,
        "updated_at": None,
        "age_s": None,
        "history": [],
        "cumulative_pressure": None,
        "average_imbalance": None,
        "history_samples": 0,
    }
    if not inst:
        return empty

    now_epoch = time.time() if now_epoch is None else float(now_epoch)
    with _TOP_OF_BOOK_LOCK:
        snapshot = DATABENTO_TOP_OF_BOOK_BY_INST.get(inst)
        if not isinstance(snapshot, dict):
            return empty
        received_at = snapshot.get("_received_at")
        try:
            age_s = max(0.0, now_epoch - float(received_at))
            bid_size = int(snapshot.get("bid_size"))
            ask_size = int(snapshot.get("ask_size"))
            bid_price = float(snapshot.get("bid_price"))
            ask_price = float(snapshot.get("ask_price"))
        except (TypeError, ValueError):
            return empty

        if age_s > TOP_OF_BOOK_STALE_S:
            state = {
                **empty,
                "state": "STALE",
                "age_s": round(age_s, 3),
                "updated_at": snapshot.get("updated_at"),
            }
            # Historical imbalance is explicitly timestamped and never pretends
            # to be the current quote. Keep it available so a brief feed pause
            # shows what pressure was doing immediately beforehand.
            history = [
                dict(point) for point in _TOP_OF_BOOK_HISTORY_BY_INST.get(inst, ())
                if now_epoch - float(point.get("epoch", 0)) <= TOP_OF_BOOK_HISTORY_S
            ]
            state["history"] = [{key: value for key, value in p.items() if key != "epoch"} for p in history]
            state["history_samples"] = len(history)
            if history:
                values = [float(p["imbalance"]) for p in history]
                state["cumulative_pressure"] = round(sum(values), 4)
                state["average_imbalance"] = round(sum(values) / len(values), 4)
            return state
        if bid_size <= 0 or ask_size <= 0 or bid_price <= 0 or ask_price <= bid_price:
            return empty

        total = bid_size + ask_size
        if total <= 0:
            return empty
        history = [
            dict(point) for point in _TOP_OF_BOOK_HISTORY_BY_INST.get(inst, ())
            if now_epoch - float(point.get("epoch", 0)) <= TOP_OF_BOOK_HISTORY_S
        ]
        history_wire = [{key: value for key, value in p.items() if key != "epoch"} for p in history]
        values = [float(p["imbalance"]) for p in history]
        return {
            "available": True,
            "state": "LIVE",
            "instrument": inst,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "imbalance": round((bid_size - ask_size) / total, 4),
            "updated_at": snapshot.get("updated_at"),
            "age_s": round(age_s, 3),
            "history": history_wire,
            "cumulative_pressure": round(sum(values), 4) if values else None,
            "average_imbalance": round(sum(values) / len(values), 4) if values else None,
            "history_samples": len(history),
        }


DATABENTO_STATUS: dict[str, Any] = {
    "connected":   False,
    "reconnects":  0,
    "last_ts":     None,
    "error":       None,
    "instruments": {},
    "order_book": {
        "schema":      "mbp-1",
        "enabled":     DATABENTO_MBP1_ENABLED,
        "subscription": "pending" if DATABENTO_MBP1_ENABLED else "disabled",
        "last_update": None,
        "updates":     0,
    },
    "queue": {
        "max_depth":       RECORD_QUEUE_MAX,
        "depth":           0,
        "enqueued":        0,
        "processed":       0,
        "dropped":         0,
        "unsupported":     0,
        "worker":          "stopped",
        "reset_at":        None,
    },
    "structure_warmup": {
        inst: {
            "state": "WARMING_UP",
            "bars": 0,
            "seeded_closed_bar_count": 0,
            "observed_closed_bar_count": 0,
            "required_bars": STRUCTURE_WARMUP_BARS,
            "reason": "startup_history_pending",
            "completion_reason": None,
            "failure_reason": None,
            "warmup_started_at": None,
            "warmup_completed_at": None,
            "warmup_duration_ms": None,
            "source_timestamp": None,
            "newest_historical_source_timestamp": None,
        }
        for inst in DB_SYMBOLS
    },
}


def get_databento_status_snapshot() -> dict[str, Any]:
    """Return a JSON-safe copy of live telemetry without exposing mutable stores."""
    status = dict(DATABENTO_STATUS)
    status["queue"] = dict(DATABENTO_STATUS.get("queue") or {})
    status["order_book"] = dict(DATABENTO_STATUS.get("order_book") or {})
    status["structure_warmup"] = {
        inst: dict(values or {})
        for inst, values in (DATABENTO_STATUS.get("structure_warmup") or {}).items()
    }
    status["instruments"] = {
        inst: {
            **dict(values or {}),
            "queue": dict((values or {}).get("queue") or {}),
        }
        for inst, values in (DATABENTO_STATUS.get("instruments") or {}).items()
    }
    return status


# ─────────────────────────────────────────────────────────────────────────────

class DatabentoBrain:
    """
    Connects to Databento live feed and continuously:

    • Builds 1-minute OHLCV bars from individual trade records
    • Computes session VWAP, ATR(14), CVD, and RVOL per instrument
    • Detects swing-pivot BOS / CHOCH → injects into ALERT_HISTORY
    • Updates all shared state stores so full_analysis always has current data

    TV webhook alerts keep working normally — they layer on top as supplemental
    signals (zone confirms, FVG / OB, manual entries, etc.).
    """

    # ── Indicator parameters ──────────────────────────────────────────────────
    ATR_PERIOD      = 14      # bars for ATR calculation
    SWING_N         = 5       # pivot bars each side (confirmed after n bars)
    SWEEP_N         = 10      # prior bars scanned for sweep high/low range
    CONFIRM_N          = 10   # bars for confirmation volume baseline
    CONFIRM_BODY_RATIO = 0.65 # close must be in top/bottom 65% of bar range (strong close)
    CONFIRM_VOL_MULT   = 1.5  # volume must be >= 1.5x rolling avg to qualify
    CONFIRM_COOLDOWN_MIN = 15 # min minutes between same-direction confirmations
    RVOL_LOOKBACK   = 20      # bars used for rolling avg volume baseline
    VOL_SPIKE_MULT  = 2.0     # RVOL ≥ this → volume spike record
    RECONNECT_DELAY = 10      # seconds to wait between reconnect attempts
    MAX_BARS        = 300     # max completed bars kept in memory per instrument

    # ATR baselines for vol-regime classification (match INSTRUMENT_SPECS)
    _ATR_BASELINES: dict[str, float] = {
        "MGC": 1.0,
        "MNQ": 10.0,
        "MES": 2.5,
        "MYM": 35.0,
    }

    def __init__(
        self,
        *,
        alert_history,
        cvd_by_ticker,
        rvol_by_ticker,
        auto_price_by_ticker,
        current_price_by_ticker,
        current_price_ts_by_ticker,
        volume_spike_by_ticker,
        volatility_by_ticker=None,
        vwap_by_ticker=None,
        vwap_override_grace_min: int = 10,
        now_utc_fn=None,
    ):
        # Shared state stores from app.py (passed by reference — writes are visible
        # immediately to every thread reading from these dicts / the deque).
        self._ah    = alert_history
        self._cvd   = cvd_by_ticker
        self._rvol  = rvol_by_ticker
        self._ap    = auto_price_by_ticker
        self._cp    = current_price_by_ticker
        self._cp_ts = current_price_ts_by_ticker
        self._vs    = volume_spike_by_ticker
        self._vol   = volatility_by_ticker   # VOLATILITY_BY_TICKER — populated per bar close
        self._vwap  = vwap_by_ticker         # VWAP_BY_TICKER — gate VWAP, with grace window
        self._vwap_grace = vwap_override_grace_min
        self._now   = now_utc_fn or (lambda: datetime.now(timezone.utc))

        # Reverse map: continuous-contract symbol → bot instrument key
        # e.g. "MGC.c.0" → "MGC"
        self._sym_to_inst: dict[str, str] = {v: k for k, v in DB_SYMBOLS.items()}

        # instrument_id → bot instrument key, populated at runtime from the
        # SymbolMappingMsg records Databento sends before any trades.
        # e.g. 42002887 → "MGC"  (IDs change each rollover — never hardcode them)
        self._id_to_inst: dict[int, str] = {}

        # Unknown instrument_ids seen — logged once each so they don't spam.
        self._unknown_ids_warned: set = set()

        # Per-instrument working state (all keyed by bot instrument: MGC/MNQ/…)
        self._bars:        dict[str, list]        = {i: [] for i in DB_SYMBOLS}
        self._partial:     dict[str, Any]         = {i: None for i in DB_SYMBOLS}
        # Lock protecting _partial read-modify-close sequences so the periodic
        # stale-bar flush thread and the trade feed thread cannot double-close
        # the same partial bar (or race on its assignment).
        self._partial_lock: threading.Lock        = threading.Lock()
        self._pv_sum:      dict[str, float]       = {i: 0.0 for i in DB_SYMBOLS}
        self._v_sum:       dict[str, float]       = {i: 0.0 for i in DB_SYMBOLS}
        self._cvd_acc:     dict[str, float]       = {i: 0.0 for i in DB_SYMBOLS}
        self._last_bos:     dict[str, Any]         = {i: None for i in DB_SYMBOLS}
        self._last_sweep:   dict[str, Any]         = {i: None for i in DB_SYMBOLS}
        self._last_confirm: dict[str, Any]         = {i: None for i in DB_SYMBOLS}
        self._active_bar_source_ts: dict[str, float | None] = {i: None for i in DB_SYMBOLS}
        self._trend:        dict[str, str | None]  = {i: None for i in DB_SYMBOLS}
        # HH/HL/LH/LL: last confirmed swing high/low used for label sequencing
        self._prev_sh:      dict[str, float | None] = {i: None for i in DB_SYMBOLS}
        self._prev_sl:      dict[str, float | None] = {i: None for i in DB_SYMBOLS}
        self._session_day: dict[str, Any]         = {i: None for i in DB_SYMBOLS}
        self._warmup_state: dict[str, dict[str, Any]] = {
            i: dict((DATABENTO_STATUS.get("structure_warmup") or {}).get(i) or {})
            for i in DB_SYMBOLS
        }
        self._warmup_started_monotonic: dict[str, float | None] = {
            i: None for i in DB_SYMBOLS
        }
        self._warmup_started_at: dict[str, str | None] = {
            i: None for i in DB_SYMBOLS
        }
        self._warmup_lock = threading.RLock()
        self._suppress_replay_signals = False
        # Bar-close callbacks: called after every completed 1m bar per instrument.
        # Registered by app.py to trigger proactive scanning without polling.
        self._bar_close_callbacks: list = []
        # Structure-signal callbacks: called for every BOS/CHOCH/CONFIRMATION alert.
        # Registered by app.py to enqueue a scored webhook analysis without a TV hit.
        self._structure_signal_callbacks: list = []
        # Tick callbacks: called for every raw trade record (sub-second cadence).
        # Registered by app.py to forward live ticks to SSE subscribers for the
        # real-time dashboard chart.  Must return quickly — runs on the ordered
        # dispatcher worker and must not recreate feed backpressure.
        self._tick_callbacks: list = []
        # The feed iterator is producer-only.  One worker preserves the ordering
        # that the old inline path had while isolating socket intake from detectors,
        # chart callbacks, and analysis fan-out.
        self._dispatch_lock = threading.RLock()
        self._record_process_lock = threading.RLock()
        self._record_queue: queue.Queue | None = None
        self._record_worker: threading.Thread | None = None
        self._record_worker_stop: threading.Event | None = None
        self._dispatch_generation = 0
        self._active_dispatch_generation = 0
        self._queue_depth_by_inst: dict[str, int] = {i: 0 for i in DB_SYMBOLS}
        self._queue_enqueued_by_inst: dict[str, int] = {i: 0 for i in DB_SYMBOLS}
        self._queue_processed_by_inst: dict[str, int] = {i: 0 for i in DB_SYMBOLS}
        self._queue_dropped_by_inst: dict[str, int] = {i: 0 for i in DB_SYMBOLS}
        self._queue_unsupported_by_inst: dict[str, int] = {i: 0 for i in DB_SYMBOLS}
        self._queue_unsupported_global = 0
        self._last_enqueued_at: dict[str, float | None] = {i: None for i in DB_SYMBOLS}
        self._last_enqueued_event: dict[str, float | None] = {i: None for i in DB_SYMBOLS}
        self._last_processed_at: dict[str, float | None] = {i: None for i in DB_SYMBOLS}
        self._last_processed_event: dict[str, float | None] = {i: None for i in DB_SYMBOLS}

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Warm native state from closed history, then launch the live feed.

        The live socket is deliberately not subscribed until replay finishes:
        this prevents a startup history replay from racing the ordered record
        dispatcher or overflowing its bounded queue.
        """
        t = threading.Thread(
            target=self._warmup_then_connect,
            daemon=True,
            name="databento-brain",
        )
        t.start()
        logger.info("DatabentoBrain: started — watching instruments: %s",
                    list(DB_SYMBOLS.keys()))

    def _set_warmup_state(self, inst: str, state: str, *, bars: int = 0,
                          observed_bars: int | None = None, reason: str | None = None,
                          source_ts: float | None = None) -> None:
        now_wall = datetime.now(timezone.utc).isoformat()
        with self._warmup_lock:
            if state == "WARMING_UP" and self._warmup_started_monotonic.get(inst) is None:
                self._warmup_started_monotonic[inst] = time.monotonic()
                self._warmup_started_at[inst] = now_wall
            started = self._warmup_started_monotonic.get(inst)
            terminal = state in ("READY", "UNAVAILABLE")
            duration_ms = (
                round(max(0.0, time.monotonic() - started) * 1000.0, 3)
                if terminal and started is not None else None
            )
            source_iso = self._iso_epoch(source_ts)
            payload = {
                "state": state,
                "bars": int(bars),
                "seeded_closed_bar_count": int(bars),
                "observed_closed_bar_count": int(
                    bars if observed_bars is None else observed_bars
                ),
                "required_bars": STRUCTURE_WARMUP_BARS,
                "reason": reason,
                "completion_reason": (
                    "sufficient_valid_closed_history" if state == "READY"
                    else None
                ),
                "failure_reason": reason if state == "UNAVAILABLE" else None,
                "warmup_started_at": self._warmup_started_at.get(inst),
                "warmup_completed_at": now_wall if terminal else None,
                "warmup_duration_ms": duration_ms,
                "source_timestamp": source_iso,
                "newest_historical_source_timestamp": source_iso,
            }
            self._warmup_state[inst] = payload
            DATABENTO_STATUS.setdefault("structure_warmup", {})[inst] = dict(payload)

    @staticmethod
    def _history_epoch(value: Any) -> float | None:
        try:
            if hasattr(value, "timestamp"):
                return float(value.timestamp())
            raw = float(value)
            return raw / 1_000_000_000 if raw > 10_000_000_000 else raw
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    def _history_rows(self, store: Any) -> list[dict[str, Any]]:
        """Convert Databento OHLCV rows to strictly valid source-time bars."""
        rows: list[dict[str, Any]] = []
        try:
            frame = store.to_df()
            iterator = frame.iterrows()
            for ts, row in iterator:
                rows.append({
                    "ts": self._history_epoch(ts),
                    "open": float(row.get("open")),
                    "high": float(row.get("high")),
                    "low": float(row.get("low")),
                    "close": float(row.get("close")),
                    "volume": int(row.get("volume", 0)),
                    "buy_volume": 0,
                    "sell_volume": 0,
                })
        except Exception:
            for row in store:
                rows.append({
                    "ts": self._history_epoch(getattr(row, "ts_event", None)),
                    "open": float(getattr(row, "open")),
                    "high": float(getattr(row, "high")),
                    "low": float(getattr(row, "low")),
                    "close": float(getattr(row, "close")),
                    "volume": int(getattr(row, "volume", 0)),
                    "buy_volume": 0,
                    "sell_volume": 0,
                })
        return rows

    def _validated_history(self, rows: list[dict[str, Any]], now: float) -> tuple[list[dict[str, Any]], str | None]:
        valid: list[dict[str, Any]] = []
        last_ts = None
        # Preserve source order: reordering an invalid response would fabricate
        # a chronology and make a malformed warm-up look trustworthy.
        for bar in rows:
            try:
                ts = float(bar.get("ts"))
                open_ = float(bar["open"])
                high = float(bar["high"])
                low = float(bar["low"])
                close = float(bar["close"])
                volume = float(bar["volume"])
                if (not all(math.isfinite(v) for v in (ts, open_, high, low, close, volume))
                        or ts > now - 60 or ts <= 0 or high < low or volume < 0
                        or not (low <= open_ <= high) or not (low <= close <= high)
                        or (last_ts is not None and ts <= last_ts)):
                    return [], "malformed_or_non_monotonic_history"
            except (KeyError, TypeError, ValueError):
                return [], "malformed_history"
            last_ts = ts
            valid.append({
                **bar,
                "ts": ts,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": int(volume),
            })
        if not valid:
            return [], "empty_history"
        if now - valid[-1]["ts"] > STRUCTURE_WARMUP_MAX_AGE_HOURS * 3600:
            return [], "history_too_stale"
        if len(valid) < STRUCTURE_WARMUP_BARS:
            return valid, "insufficient_closed_history"
        return valid[-min(self.MAX_BARS, STRUCTURE_WARMUP_BARS):], None

    def _warmup_then_connect(self) -> None:
        # Allow app.py to register its callbacks before replay.  Replay suppresses
        # outputs regardless, but this keeps lifecycle ordering deterministic.
        time.sleep(0.1)
        for inst in DB_SYMBOLS:
            self._set_warmup_state(inst, "WARMING_UP", reason="fetching_closed_history")
        try:
            import databento as db  # noqa: PLC0415
            api_key = os.environ.get("DATABENTO_API_KEY", "").strip()
            if not api_key:
                for inst in DB_SYMBOLS:
                    self._set_warmup_state(inst, "UNAVAILABLE", reason="missing_api_key")
                return self._reconnect_loop()
            historical = db.Historical(key=api_key)
            # Historical data can lag live.  We only use it to reconstruct the
            # native detector state, never to claim that price/CVD is fresh.
            end = datetime.now(timezone.utc) - timedelta(hours=5)
            start = end - timedelta(hours=12)
            for inst, symbol in DB_SYMBOLS.items():
                try:
                    store = historical.timeseries.get_range(
                        dataset=DB_DATASET, symbols=[symbol], schema="ohlcv-1m",
                        stype_in="continuous", start=start.strftime("%Y-%m-%dT%H:%M"),
                        end=end.strftime("%Y-%m-%dT%H:%M"),
                    )
                    bars, reason = self._validated_history(
                        self._history_rows(store), time.time()
                    )
                    if reason:
                        self._set_warmup_state(
                            inst, "UNAVAILABLE", bars=0, observed_bars=len(bars),
                            reason=reason,
                        )
                        continue
                    self._suppress_replay_signals = True
                    try:
                        for bar in bars:
                            self._on_bar_close(inst, bar, replay=True)
                    finally:
                        self._suppress_replay_signals = False
                    self._set_warmup_state(
                        inst, "READY", bars=len(bars), reason=None, source_ts=bars[-1]["ts"]
                    )
                    logger.info(
                        "DatabentoBrain: structure warm-up READY for %s "
                        "(seeded_closed_bar_count=%d newest_source=%s duration_ms=%s)",
                        inst,
                        len(bars),
                        self._iso_epoch(bars[-1]["ts"]),
                        (DATABENTO_STATUS.get("structure_warmup", {}).get(inst, {})
                         .get("warmup_duration_ms")),
                    )
                except Exception as exc:
                    logger.warning("DatabentoBrain: structure warm-up failed for %s: %s", inst, exc)
                    self._set_warmup_state(inst, "UNAVAILABLE", reason="history_fetch_failed")
        except Exception as exc:
            logger.warning("DatabentoBrain: structure warm-up unavailable: %s", exc)
            for inst in DB_SYMBOLS:
                self._set_warmup_state(inst, "UNAVAILABLE", reason="history_client_failed")
        self._reconnect_loop()

    def register_bar_close_callback(self, fn) -> None:
        """Register a callable(inst: str, price: float) invoked after each bar close.
        Called from the ordered record worker — fn must be fast or dispatch its own thread."""
        self._bar_close_callbacks.append(fn)

    def register_structure_signal_callback(self, fn) -> None:
        """Register a callable(inst: str, alert_type: str, price: float) invoked
        whenever _inject_alert fires a BOS, CHOCH, or CONFIRMATION alert.
        Called from the ordered record worker — fn must return quickly."""
        self._structure_signal_callbacks.append(fn)

    def register_tick_callback(self, fn) -> None:
        """Register a callable(inst, ts_s, price, volume, side) invoked for
        every individual Databento trade record.  Runs on the ordered worker —
        fn must return immediately (enqueue or discard; never block or sleep)."""
        self._tick_callbacks.append(fn)

    # ── Bounded record dispatch / telemetry ───────────────────────────────────

    @staticmethod
    def _event_epoch(rec: Any) -> float | None:
        """Return the source event epoch, not the local processing time."""
        try:
            raw = getattr(rec, "ts_event", None)
            if raw is None:
                return None
            value = float(raw)
            return value / 1_000_000_000 if value > 10_000_000_000 else value
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _iso_epoch(value: float | None) -> str | None:
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError, OverflowError):
            return None

    def _queue_state_for(self, inst: str) -> dict[str, Any]:
        """Build a copied, current queue health view while holding _dispatch_lock."""
        now = time.time()
        depth = self._queue_depth_by_inst.get(inst, 0)
        last_enqueued_event = self._last_enqueued_event.get(inst)
        last_processed_event = self._last_processed_event.get(inst)
        processing_lag = None
        if last_enqueued_event is not None:
            processing_lag = max(
                0.0,
                last_enqueued_event - (last_processed_event if last_processed_event is not None else last_enqueued_event),
            )
        event_age = max(0.0, now - last_processed_event) if last_processed_event is not None else None
        dropped = self._queue_dropped_by_inst.get(inst, 0)
        worker_alive = bool(self._record_worker and self._record_worker.is_alive())
        if dropped:
            freshness = "UNAVAILABLE"
            reason = "records_dropped"
        elif last_processed_event is None:
            freshness = "UNAVAILABLE"
            reason = "no_processed_records"
        elif event_age is not None and event_age > RECORD_QUEUE_STALE_S:
            freshness = "STALE"
            reason = "source_event_stale"
        elif depth > 0 or (processing_lag is not None and processing_lag > RECORD_QUEUE_DELAY_S):
            freshness = "DELAYED"
            reason = "queue_backlog"
        else:
            freshness = "FRESH"
            reason = None
        return {
            "queue_depth": depth,
            "enqueued": self._queue_enqueued_by_inst.get(inst, 0),
            "processed": self._queue_processed_by_inst.get(inst, 0),
            "dropped": dropped,
            "unsupported": self._queue_unsupported_by_inst.get(inst, 0),
            "last_enqueued_at": self._iso_epoch(self._last_enqueued_at.get(inst)),
            "newest_enqueued_timestamp": self._iso_epoch(last_enqueued_event),
            "last_processed_at": self._iso_epoch(self._last_processed_at.get(inst)),
            "newest_processed_timestamp": self._iso_epoch(last_processed_event),
            "processing_lag_s": round(processing_lag, 3) if processing_lag is not None else None,
            "source_event_age_s": round(event_age, 3) if event_age is not None else None,
            "freshness": freshness,
            "unavailable_reason": reason,
            "worker_alive": worker_alive,
        }

    def _publish_queue_telemetry(self, inst: str | None = None) -> None:
        """Publish additive health snapshots; this never affects signal scoring."""
        with self._dispatch_lock:
            instruments = (inst,) if inst in DB_SYMBOLS else tuple(DB_SYMBOLS)
            queue_state = DATABENTO_STATUS["queue"]
            queue_state.update({
                "max_depth": RECORD_QUEUE_MAX,
                "depth": sum(self._queue_depth_by_inst.values()),
                "enqueued": sum(self._queue_enqueued_by_inst.values()),
                "processed": sum(self._queue_processed_by_inst.values()),
                "dropped": sum(self._queue_dropped_by_inst.values()),
                "unsupported": sum(self._queue_unsupported_by_inst.values()) + self._queue_unsupported_global,
                "worker": "running" if self._record_worker and self._record_worker.is_alive() else "stopped",
            })
            for key in instruments:
                existing = dict(DATABENTO_STATUS["instruments"].get(key) or {})
                existing["queue"] = self._queue_state_for(key)
                DATABENTO_STATUS["instruments"][key] = existing

    def _start_record_dispatcher(self) -> None:
        """Start a fresh, bounded ordered dispatcher for one feed connection."""
        with self._dispatch_lock:
            if self._record_worker is not None and self._record_worker.is_alive():
                # Never run two state-mutating consumers across reconnects.
                raise RuntimeError("previous Databento record worker is still running")
            self._dispatch_generation += 1
            generation = self._dispatch_generation
            self._active_dispatch_generation = generation
            self._record_queue = queue.Queue(maxsize=RECORD_QUEUE_MAX)
            self._record_worker_stop = threading.Event()
            self._queue_depth_by_inst = {i: 0 for i in DB_SYMBOLS}
            self._queue_enqueued_by_inst = {i: 0 for i in DB_SYMBOLS}
            self._queue_processed_by_inst = {i: 0 for i in DB_SYMBOLS}
            self._queue_dropped_by_inst = {i: 0 for i in DB_SYMBOLS}
            self._queue_unsupported_by_inst = {i: 0 for i in DB_SYMBOLS}
            self._queue_unsupported_global = 0
            self._last_enqueued_at = {i: None for i in DB_SYMBOLS}
            self._last_enqueued_event = {i: None for i in DB_SYMBOLS}
            self._last_processed_at = {i: None for i in DB_SYMBOLS}
            self._last_processed_event = {i: None for i in DB_SYMBOLS}
            DATABENTO_STATUS["queue"]["reset_at"] = datetime.now(timezone.utc).isoformat()
            worker = threading.Thread(
                target=self._record_worker_loop,
                args=(self._record_queue, self._record_worker_stop, generation),
                daemon=True,
                name="databento-record-worker",
            )
            self._record_worker = worker
            worker.start()
        self._publish_queue_telemetry()

    def _stop_record_dispatcher(self) -> None:
        """Stop one session before reconnecting; never overlap state consumers."""
        with self._dispatch_lock:
            worker = self._record_worker
            stop = self._record_worker_stop
            record_queue = self._record_queue
            if stop is not None:
                stop.set()
            while record_queue is not None:
                try:
                    _, inst, _, _ = record_queue.get_nowait()
                except queue.Empty:
                    break
                if inst in DB_SYMBOLS:
                    self._queue_depth_by_inst[inst] = max(0, self._queue_depth_by_inst[inst] - 1)
                    self._queue_dropped_by_inst[inst] += 1
                record_queue.task_done()
            DATABENTO_STATUS["queue"]["worker"] = "stopping"
        # Serialize shutdown against the actual record handler. If a callback is
        # currently running, wait for that one already-started record to finish
        # before fencing the session; no subsequent queued record can enter.
        with self._record_process_lock:
            self._active_dispatch_generation = 0
        if worker is not None:
            warned = False
            while worker.is_alive():
                worker.join(timeout=5)
                if worker.is_alive() and not warned:
                    warned = True
                    logger.warning(
                        "DatabentoBrain: waiting for in-flight record handler before reconnect"
                    )
        self._publish_queue_telemetry()

    def _dispatch_record(self, rec: Any) -> None:
        """Non-blocking feed-thread intake; overflow is explicit, never silent."""
        kind = "mbp1" if self._is_mbp1_record(rec) else "trade" if self._is_trade_record(rec) else "unsupported"
        inst = self._instrument_for_record(rec) if kind != "unsupported" else None
        if kind == "unsupported":
            with self._dispatch_lock:
                self._queue_unsupported_global += 1
            self._publish_queue_telemetry()
            return
        event_epoch = self._event_epoch(rec)
        now = time.time()
        unsupported = False
        with self._dispatch_lock:
            if inst not in DB_SYMBOLS:
                # Preserve prior unsupported behavior (ignored record) while exposing it.
                if inst:
                    self._queue_unsupported_by_inst[inst] = self._queue_unsupported_by_inst.get(inst, 0) + 1
                else:
                    self._queue_unsupported_global += 1
                unsupported = True
            else:
                self._last_enqueued_at[inst] = now
                if event_epoch is not None:
                    prior = self._last_enqueued_event[inst]
                    self._last_enqueued_event[inst] = max(prior or event_epoch, event_epoch)
                record_queue = self._record_queue
                if record_queue is None:
                    self._queue_dropped_by_inst[inst] += 1
                    unsupported = True
                else:
                    try:
                        record_queue.put_nowait((rec, inst, kind, event_epoch))
                    except queue.Full:
                        self._queue_dropped_by_inst[inst] += 1
                        if self._queue_dropped_by_inst[inst] == 1:
                            logger.warning(
                                "DatabentoBrain: bounded record queue saturated; dropping new %s records for %s",
                                kind, inst,
                            )
                        unsupported = True
                    else:
                        self._queue_depth_by_inst[inst] += 1
                        self._queue_enqueued_by_inst[inst] += 1
        self._publish_queue_telemetry(inst)
        if unsupported:
            return

    def _record_worker_loop(
        self,
        record_queue: queue.Queue,
        stop: threading.Event,
        generation: int,
    ) -> None:
        """Consume records in stream order without ever blocking the live iterator."""
        while not stop.is_set():
            try:
                rec, inst, kind, event_epoch = record_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            with self._dispatch_lock:
                # Queue depth means records still waiting; the one now executing
                # is deliberately excluded so it can never exceed max_depth.
                self._queue_depth_by_inst[inst] = max(0, self._queue_depth_by_inst[inst] - 1)
            try:
                with self._record_process_lock:
                    # Fence a disconnected or superseded session before it can
                    # mutate bars, indicators, structure, or callbacks.
                    if stop.is_set() or generation != self._active_dispatch_generation:
                        continue
                    if kind == "mbp1":
                        self._on_mbp1(rec)
                    else:
                        self._on_trade(rec)
                    with self._dispatch_lock:
                        self._queue_processed_by_inst[inst] += 1
                        self._last_processed_at[inst] = time.time()
                        if event_epoch is not None:
                            prior = self._last_processed_event[inst]
                            self._last_processed_event[inst] = max(prior or event_epoch, event_epoch)
            except Exception as exc:
                logger.debug("DatabentoBrain record-worker error: %s", exc)
            finally:
                record_queue.task_done()
                self._publish_queue_telemetry(inst)

    # ── HTTP symbology pre-fetch ──────────────────────────────────────────────

    def _prefetch_id_map_http(self, db_module: Any, api_key: str) -> None:
        """
        Pre-populate _id_to_inst via Databento's REST symbology.resolve endpoint.

        The live feed for continuous-contract subscriptions (stype_in="continuous")
        never sends SymbolMappingMsg records, so client.symbology_map stays empty
        for the entire session. TradeMsg also has no `symbol` field, so every
        symbol-string fallback in _on_trade resolves to "". This method resolves
        instrument_id values up-front from the HTTP API so the map is ready before
        the first tick arrives. Fail-open: any exception is logged and the live
        feed continues — the add_callback path handles any rollover mid-session.
        """
        try:
            hist = db_module.Historical(key=api_key)
            today = datetime.now(timezone.utc).date().isoformat()
            result = hist.symbology.resolve(
                dataset=DB_DATASET,
                symbols=list(DB_SYMBOLS.values()),
                stype_in="continuous",
                stype_out="instrument_id",
                start_date=today,
            )
            # Response shape: {"result": {"MGC.c.0": [{"d0":…,"d1":…,"s":"12345"}], …}}
            mappings: dict = result.get("result", {})
            found = 0
            for cont_sym, intervals in mappings.items():
                inst = self._sym_to_inst.get(cont_sym)
                if inst is None:
                    continue
                for interval in (intervals or []):
                    iid_str = (
                        interval.get("s") if isinstance(interval, dict) else str(interval)
                    )
                    if not iid_str or iid_str in ("0", "None", ""):
                        continue
                    try:
                        iid = int(iid_str)
                        self._id_to_inst[iid] = inst
                        found += 1
                        logger.info(
                            "DatabentoBrain: pre-fetched id→inst %s → %s (sym=%s)",
                            iid, inst, cont_sym,
                        )
                    except (ValueError, TypeError):
                        pass
            if found:
                logger.info(
                    "DatabentoBrain: symbology pre-fetch complete — %d id(s) mapped: %s",
                    found, self._id_to_inst,
                )
            else:
                logger.warning(
                    "DatabentoBrain: symbology pre-fetch returned no mappings "
                    "(raw result=%s); instrument resolution will rely on add_callback",
                    mappings,
                )
        except Exception as exc:
            logger.warning(
                "DatabentoBrain: symbology pre-fetch failed (%s); "
                "instrument resolution will rely on add_callback",
                exc,
            )

    # ── Reconnect loop ────────────────────────────────────────────────────────

    def _reconnect_loop(self) -> None:
        """Outer loop that re-establishes the feed after any error."""
        while True:
            try:
                self._run_feed()
            except Exception as exc:
                DATABENTO_STATUS["connected"] = False
                DATABENTO_STATUS["error"]     = str(exc)
                DATABENTO_STATUS["reconnects"] += 1
                logger.warning(
                    "DatabentoBrain: feed error — %s  (reconnect in %ds)",
                    exc, self.RECONNECT_DELAY,
                )
            time.sleep(self.RECONNECT_DELAY)

    def _run_feed(self) -> None:
        """Connect to Databento and stream records until an exception is raised."""
        try:
            import databento as db  # noqa: PLC0415 — lazy import (flag-gated)
        except ImportError:
            logger.error(
                "DatabentoBrain: 'databento' package not installed. "
                "Install it with:  pip install databento"
            )
            time.sleep(60)
            return

        api_key = os.environ.get("DATABENTO_API_KEY", "").strip()
        if not api_key:
            logger.warning(
                "DatabentoBrain: DATABENTO_API_KEY is not set — "
                "standing by (will retry every %ds when key is added).",
                self.RECONNECT_DELAY,
            )
            time.sleep(self.RECONNECT_DELAY)
            return

        # Reset per-session state so reconnects don't carry stale instrument_ids
        # (exchange IDs change on contract rollover). A prior connection's book
        # must also never be reused after a reconnect.
        self._id_to_inst = {}
        self._unknown_ids_warned = set()   # re-warn after each reconnect/re-prefetch
        clear_top_of_book_snapshots()
        DATABENTO_STATUS["order_book"].update({
            "enabled":      DATABENTO_MBP1_ENABLED,
            "subscription": "pending" if DATABENTO_MBP1_ENABLED else "disabled",
            "last_update":  None,
        })

        logger.info("DatabentoBrain: connecting to %s …", DB_DATASET)
        DATABENTO_STATUS["error"] = None

        client = db.Live(key=api_key)
        client.subscribe(
            dataset=DB_DATASET,
            schema="trades",
            symbols=list(DB_SYMBOLS.values()),
            stype_in="continuous",
        )
        if DATABENTO_MBP1_ENABLED:
            try:
                # Databento allows multiple schema subscriptions on one Live
                # session. Keep the trade tape intact; MBP-1 is an additive
                # top-of-book stream used only by Order Flow.
                client.subscribe(
                    dataset=DB_DATASET,
                    schema="mbp-1",
                    symbols=list(DB_SYMBOLS.values()),
                    stype_in="continuous",
                )
                DATABENTO_STATUS["order_book"]["subscription"] = "active"
                logger.info(
                    "DatabentoBrain: MBP-1 top-of-book subscription active for %s",
                    list(DB_SYMBOLS.values()),
                )
            except Exception as exc:
                # Fail open: a depth entitlement/feed problem must not take down
                # the established trades subscription or any existing indicators.
                DATABENTO_STATUS["order_book"]["subscription"] = "unavailable"
                logger.warning(
                    "DatabentoBrain: MBP-1 subscription unavailable; "
                    "continuing with trades only (%s)",
                    exc,
                )
        self._start_record_dispatcher()
        DATABENTO_STATUS["connected"] = True
        logger.info(
            "DatabentoBrain: connected ✓  streaming %s", list(DB_SYMBOLS.values())
        )

        # ── Pre-fetch instrument_id map via HTTP API ──────────────────────────
        # The Databento live feed for continuous-contract subscriptions does NOT
        # send SymbolMappingMsg records, so client.symbology_map stays permanently
        # empty. TradeMsg also has no `symbol` field. We resolve instrument_id
        # values up-front via the REST symbology.resolve endpoint instead.
        self._prefetch_id_map_http(db, api_key)

        # ── add_callback: catch any SymbolMappingMsg that arrives at runtime ──
        # Handles contract rollovers mid-session where instrument_ids change.
        def _symmap_callback(rec) -> None:
            try:
                import databento_dbn as _dbn  # noqa: PLC0415
                if not isinstance(rec, _dbn.SymbolMappingMsg):
                    return
                iid    = rec.instrument_id
                native = getattr(rec, "stype_out_symbol", "") or ""
                if not native:
                    native = getattr(rec, "stype_in_symbol", "") or ""
                for root in DB_SYMBOLS:
                    if native.startswith(root):
                        if self._id_to_inst.get(iid) != root:
                            self._id_to_inst[iid] = root
                            logger.info(
                                "DatabentoBrain: live id→inst %s → %s (native=%s)",
                                iid, root, native,
                            )
                        break
            except Exception:
                pass

        client.add_callback(_symmap_callback)

        # ── Periodic stale-partial flush (low-volume bar-close fix) ─────────
        # Closes bars that accumulated real trades but whose minute has already
        # elapsed without a subsequent trade arriving (e.g. MGC overnight).
        _flush_stop = threading.Event()
        self._start_partial_flush_timer(_flush_stop)

        # The session carries both TradeMsg and MBP1Msg records. The instrument
        # map is built concurrently above and will be ready before either is used.
        try:
            for record in client:
                self._dispatch_record(record)
        finally:
            # Disconnect first: no in-flight or callback-triggered execution can
            # pass the existing Databento health boundary while shutdown drains.
            DATABENTO_STATUS["connected"] = False
            # Stop the flush timer whether the feed exits cleanly or on error.
            _flush_stop.set()
            # Do not replay backlog from a dead connection after reconnect.  Any
            # discarded records make the affected instrument explicitly unavailable.
            self._stop_record_dispatcher()

        logger.warning("DatabentoBrain: feed closed by server — reconnecting …")

    # ── Shared record/instrument helpers ───────────────────────────────────────

    @staticmethod
    def _is_mbp1_record(rec: Any) -> bool:
        """True for Databento MBP-1 records without importing DBN types eagerly."""
        return (
            rec.__class__.__name__ in ("MBP1Msg", "CMBP1Msg")
            or (hasattr(rec, "bid_sz_00") and hasattr(rec, "ask_sz_00"))
        )

    @staticmethod
    def _is_trade_record(rec: Any) -> bool:
        """True for the trade tape records consumed by _on_trade()."""
        return hasattr(rec, "price") and hasattr(rec, "size") and not (
            hasattr(rec, "bid_sz_00") or hasattr(rec, "ask_sz_00")
        )

    def _instrument_for_record(self, rec: Any) -> str | None:
        """Resolve a live Databento record to MGC/MNQ/MES/MYM, fail-open."""
        iid = getattr(rec, "instrument_id", None)
        inst = self._id_to_inst.get(iid) if iid is not None else None
        if inst is None:
            sym = getattr(rec, "symbol", None) or ""
            inst = self._sym_to_inst.get(sym)
        if inst is None:
            sym = getattr(rec, "symbol", None) or ""
            for root in DB_SYMBOLS:
                if sym.startswith(root):
                    inst = root
                    break
        if inst is None and iid is not None and iid != 0 and iid not in self._unknown_ids_warned:
            self._unknown_ids_warned.add(iid)
            logger.warning(
                "DatabentoBrain: unrecognized instrument_id=%s (sym=%r) — "
                "record dropped. Known ids: %s. This may indicate a contract "
                "rollover where the id changed mid-session.",
                iid, getattr(rec, "symbol", None) or "", list(self._id_to_inst.keys()),
            )
        return inst

    # ── MBP-1 top-of-book handler ─────────────────────────────────────────────

    @staticmethod
    def _mbp1_level_zero(rec: Any) -> tuple[Any, Any, Any, Any]:
        """Read level zero across supported DBN Python representations."""
        bid_px = getattr(rec, "bid_px_00", None)
        ask_px = getattr(rec, "ask_px_00", None)
        bid_sz = getattr(rec, "bid_sz_00", None)
        ask_sz = getattr(rec, "ask_sz_00", None)
        if None not in (bid_px, ask_px, bid_sz, ask_sz):
            return bid_px, ask_px, bid_sz, ask_sz
        levels = getattr(rec, "levels", None) or []
        if levels:
            level = levels[0]
            return (
                getattr(level, "bid_px", getattr(level, "bid_px_00", None)),
                getattr(level, "ask_px", getattr(level, "ask_px_00", None)),
                getattr(level, "bid_sz", getattr(level, "bid_sz_00", None)),
                getattr(level, "ask_sz", getattr(level, "ask_sz_00", None)),
            )
        return bid_px, ask_px, bid_sz, ask_sz

    def _on_mbp1(self, rec: Any) -> None:
        """Store a valid, current MBP-1 best bid/ask quote for one instrument."""
        try:
            inst = self._instrument_for_record(rec)
            if inst is None:
                return
            bid_px_raw, ask_px_raw, bid_sz_raw, ask_sz_raw = self._mbp1_level_zero(rec)
            bid_px = float(bid_px_raw) / 1_000_000_000
            ask_px = float(ask_px_raw) / 1_000_000_000
            bid_sz = int(bid_sz_raw)
            ask_sz = int(ask_sz_raw)
            # Ignore crossed, empty, or malformed levels. The prior valid quote
            # remains available only until its regular stale timeout elapses.
            if bid_px <= 0 or ask_px <= bid_px or bid_sz <= 0 or ask_sz <= 0:
                return
            processed_at = time.time()
            event_epoch = self._event_epoch(rec)
            # A quote delayed in the dispatcher must not regain freshness simply
            # because it was processed moments ago.
            received_at = event_epoch if event_epoch is not None else processed_at
            event_ts = getattr(rec, "ts_event", None)
            snapshot = {
                "available": True,
                "instrument": inst,
                "bid_price": round(bid_px, 8),
                "ask_price": round(ask_px, 8),
                "bid_size": bid_sz,
                "ask_size": ask_sz,
                "ts_event": event_ts,
                "updated_at": self._iso_epoch(received_at) or datetime.now(timezone.utc).isoformat(),
                "processed_at": self._iso_epoch(processed_at),
                "_received_at": received_at,
            }
            with _TOP_OF_BOOK_LOCK:
                DATABENTO_TOP_OF_BOOK_BY_INST[inst] = snapshot
                last_history_at = _TOP_OF_BOOK_LAST_HISTORY_AT.get(inst, 0.0)
                if received_at - last_history_at >= TOP_OF_BOOK_HISTORY_SAMPLE_S:
                    total = bid_sz + ask_sz
                    _TOP_OF_BOOK_HISTORY_BY_INST[inst].append({
                        "t": snapshot["updated_at"],
                        "imbalance": round((bid_sz - ask_sz) / total, 4),
                        "epoch": received_at,
                    })
                    _TOP_OF_BOOK_LAST_HISTORY_AT[inst] = received_at
            book_status = DATABENTO_STATUS["order_book"]
            book_status["last_update"] = snapshot["updated_at"]
            book_status["updates"] = int(book_status.get("updates") or 0) + 1
            DATABENTO_STATUS["instruments"].setdefault(inst, {})["order_book"] = {
                "bid_size": bid_sz,
                "ask_size": ask_sz,
                "updated_at": snapshot["updated_at"],
            }
        except Exception as exc:
            logger.debug("DatabentoBrain _on_mbp1 error: %s", exc)

    # ── Trade record handler ──────────────────────────────────────────────────

    def _on_trade(self, rec) -> None:
        try:
            # Shared resolution also maps the companion MBP-1 stream to the
            # exact same canonical instrument keys.
            inst = self._instrument_for_record(rec)
            if inst is None:
                return

            # Databento uses nanosecond epoch integers for timestamps and
            # fixed-point price integers (divide by 1e9 to get float USD/index).
            ts_s  = rec.ts_event / 1_000_000_000
            price = rec.price   / 1_000_000_000
            size  = int(rec.size)
            side  = getattr(rec, "side", None)  # 'A'=buy aggressor, 'B'=sell, 'N'=?

            processed_iso = datetime.now(timezone.utc).isoformat()
            event_iso = self._iso_epoch(ts_s) or processed_iso
            # Keep the legacy timestamp as local processing time for connection
            # liveness, but source-derived stores carry their actual event time.
            DATABENTO_STATUS["last_ts"] = processed_iso
            DATABENTO_STATUS["last_event_ts"] = event_iso

            # ── Live price (sub-second resolution) ──
            self._cp[inst]    = price
            # Freshness must describe the market event, not when an overloaded
            # consumer eventually got around to handling it.
            self._cp_ts[inst] = event_iso

            # ── CVD accumulation (bid/ask aggression) ──
            if side == "A":        # buyer hit the ask — demand pressure
                self._cvd_acc[inst] += size
            elif side == "B":      # seller hit the bid — supply pressure
                self._cvd_acc[inst] -= size
            # side == 'N' (unknown) — neutral, skip

            # ── Session VWAP accumulators ──
            self._pv_sum[inst] += price * size
            self._v_sum[inst]  += size

            # ── Bar builder ──
            bar_minute = int(ts_s // 60) * 60
            self._check_session_reset(inst, bar_minute)
            self._tick_bar(inst, bar_minute, price, size, side=side)

            # ── Live tick broadcast (SSE chart feed) ──
            # Fire AFTER _tick_bar so DATABENTO_PARTIAL_BY_INST already reflects
            # this tick.  Callbacks must return immediately — they run on the feed thread.
            if self._tick_callbacks:
                for _cb in self._tick_callbacks:
                    try:
                        _cb(inst, ts_s, price, size, side)
                    except Exception:
                        pass

        except Exception as exc:
            logger.debug("DatabentoBrain _on_trade error: %s", exc)

    # ── Session reset (VWAP + CVD) ────────────────────────────────────────────

    def _check_session_reset(self, inst: str, bar_ts: int) -> None:
        """
        Reset VWAP and CVD accumulators at the start of each CME session.
        CME Globex opens at 18:00 ET (23:00 UTC) Sunday–Friday.
        For simplicity we reset when the UTC date changes — within ≤23h of
        the true session boundary which is sufficient for intraday VWAP.
        """
        dt  = datetime.utcfromtimestamp(bar_ts)
        day = dt.date()
        if self._session_day[inst] != day:
            if self._session_day[inst] is not None:
                logger.info(
                    "DatabentoBrain: session reset for %s (%s → %s)",
                    inst, self._session_day[inst], day,
                )
            self._pv_sum[inst]    = 0.0
            self._v_sum[inst]     = 0.0
            self._cvd_acc[inst]   = 0.0
            self._session_day[inst] = day

    # ── Bar builder ───────────────────────────────────────────────────────────

    def _tick_bar(
        self, inst: str, bar_minute: int, price: float, size: int,
        side: "str | None" = None,
    ) -> None:
        # Hold the lock only for the read-modify of _partial so the periodic
        # flush thread cannot read a stale pointer or double-close the same bar.
        #
        # Order Flow V1: buy_volume / sell_volume are accumulated per bar from
        # the Databento trade side field (A = buy aggressor, B = sell aggressor).
        # Side N (unknown) adds to total volume only (same as before).
        with self._partial_lock:
            p = self._partial[inst]
            if p is None or p["ts"] != bar_minute:
                to_close = p           # capture old partial (may be None)
                self._partial[inst] = {
                    "ts":          bar_minute,
                    "open":        price,
                    "high":        price,
                    "low":         price,
                    "close":       price,
                    "volume":      size,
                    "buy_volume":  size if side == "A" else 0,
                    "sell_volume": size if side == "B" else 0,
                }
            else:
                to_close = None        # same minute — just update in place
                if price > p["high"]: p["high"] = price
                if price < p["low"]:  p["low"]  = price
                p["close"]   = price
                p["volume"] += size
                if side == "A":
                    p["buy_volume"]  = p.get("buy_volume",  0) + size
                elif side == "B":
                    p["sell_volume"] = p.get("sell_volume", 0) + size
            # Publish a display snapshot so Flask routes can read the partial
            # bar without acquiring this lock.  dict() creates a frozen copy
            # while we're still inside the lock — consistent and lock-free for readers.
            DATABENTO_PARTIAL_BY_INST[inst] = (
                dict(self._partial[inst]) if self._partial[inst] is not None else None
            )
        # Call _on_bar_close OUTSIDE the lock — it runs detectors and callbacks
        # that take meaningful time and must not block the flush thread.
        if to_close is not None:
            self._on_bar_close(inst, to_close)

    # ── Stale partial-bar flush (low-volume bar-close fix) ───────────────────
    # Problem: _tick_bar only closes a bar when the NEXT trade arrives in a
    # different minute.  Low-volume instruments (MGC overnight / early session)
    # can go several minutes between trades.  The partial bar from minute N
    # accumulates real trades but never calls _on_bar_close until minute N+k,
    # so the Left Brain bar count stays at zero even though real data exists.
    #
    # Fix: a background timer thread wakes every PARTIAL_FLUSH_INTERVAL_S seconds
    # and promotes any partial bar whose minute is already ≥ PARTIAL_STALE_S
    # seconds in the past.  Bars are only closed — never synthesised.
    # Thread-safety: the same _partial_lock used by _tick_bar prevents a race
    # between the flush thread and the trade feed thread.

    PARTIAL_FLUSH_INTERVAL_S: int = 30   # how often the flush thread wakes
    PARTIAL_STALE_S:          int = 70   # seconds past the bar-minute → flush
    #   70s = 60s (full minute elapsed) + 10s clock-skew / late-arriving records

    def _flush_stale_partials(self) -> None:
        """Close any partial bars whose minute has already passed.

        Called from the flush timer thread every PARTIAL_FLUSH_INTERVAL_S seconds.
        No bar is synthesised — only real accumulated ticks are finalised.
        """
        now_unix = time.time()
        to_flush: list[tuple[str, dict]] = []
        with self._partial_lock:
            for inst in list(DB_SYMBOLS):
                p = self._partial[inst]
                if p is not None and (now_unix - p["ts"]) > self.PARTIAL_STALE_S:
                    # Clear inside the lock so _tick_bar won't double-close it.
                    self._partial[inst] = None
                    to_flush.append((inst, p))
        # Fire _on_bar_close outside the lock (runs detectors + callbacks).
        for inst, p in to_flush:
            try:
                self._on_bar_close(inst, p)
                logger.info(
                    "DatabentoBrain: flushed stale partial bar for %s "
                    "(bar_ts=%s, age=%.0fs)",
                    inst,
                    datetime.utcfromtimestamp(p["ts"]).strftime("%H:%M"),
                    now_unix - p["ts"],
                )
            except Exception as exc:
                logger.warning(
                    "DatabentoBrain: partial-flush error for %s: %s", inst, exc
                )

    def _start_partial_flush_timer(self, stop_event: threading.Event) -> None:
        """Start a daemon thread that calls _flush_stale_partials periodically.

        The thread exits cleanly when stop_event is set (i.e., when _run_feed
        returns or raises).
        """
        def _loop() -> None:
            while not stop_event.wait(self.PARTIAL_FLUSH_INTERVAL_S):
                try:
                    self._flush_stale_partials()
                except Exception as exc:
                    logger.debug("DatabentoBrain flush-loop error: %s", exc)

        t = threading.Thread(target=_loop, daemon=True, name="db-partial-flush")
        t.start()

    # ── Bar close — compute indicators and inject into shared state ───────────

    def _on_bar_close(self, inst: str, bar: dict, *, replay: bool = False) -> None:
        bars = self._bars[inst]
        bars.append(bar)
        if len(bars) > self.MAX_BARS:
            del bars[0]

        processed_iso = datetime.now(timezone.utc).isoformat()
        # A completed bar is source-time data.  Using wall-clock processing time
        # here would make a queued replay look current to VWAP/CVD/RVOL consumers.
        source_iso = self._iso_epoch(bar.get("ts")) or processed_iso
        self._active_bar_source_ts[inst] = bar.get("ts")

        # Session VWAP — accumulated from live trade ticks (_on_trade).
        # On boot, historical bars arrive before any live trades, so _v_sum is 0.
        # Fall back to the bar's typical price so get_vwap() never sees "missing"
        # from the very first bar; replaced by real accumulation within ~1 minute.
        if self._v_sum[inst] > 0:
            vwap = self._pv_sum[inst] / self._v_sum[inst]
        elif bar.get("volume", 0) > 0:
            vwap = (bar["high"] + bar["low"] + bar["close"]) / 3.0
        else:
            vwap = None

        # ATR(14) from completed bars
        atr = self._calc_atr(bars)

        # RVOL (relative volume vs rolling avg of recent bars)
        rvol = self._calc_rvol(bars)

        # CVD state
        cvd_val   = self._cvd_acc[inst]
        cvd_state = ("bullish" if cvd_val > 0
                     else "bearish" if cvd_val < 0 else None)

        # ── AUTO_PRICE_BY_TICKER (same schema as Yahoo Finance push) ─────────
        if vwap is not None:
            entry: dict[str, Any] = {
                "vwap":        vwap,
                "vwap_status": "ok",
                "ts":          source_iso,
                "processed_at": processed_iso,
                "source":      "databento",
            }
            if atr is not None:
                entry["atr_pts"]    = atr
                entry["vol_regime"] = self._vol_regime(atr, inst)
            self._ap[inst] = entry

        # ── CVD_BY_TICKER ─────────────────────────────────────────────────────
        self._cvd[inst] = {
            "state":     cvd_state,
            "value":     cvd_val,
            "direction": "rising" if cvd_val > 0 else "falling",
            "ts":        source_iso,
            "processed_at": processed_iso,
            "source":    "databento",
        }

        # ── RVOL_BY_TICKER + VOLUME_SPIKE_BY_TICKER ───────────────────────────
        if rvol is not None:
            self._rvol[inst] = {
                "value":  rvol,
                "ts":     source_iso,
                "processed_at": processed_iso,
                "source": "databento",
            }
            if rvol >= self.VOL_SPIKE_MULT:
                self._vs[inst] = {
                    "ts": source_iso,
                    "processed_at": processed_iso,
                    "source": "databento",
                }

        # ── Public bar store (dashboard live chart) ───────────────────────────
        # Order Flow V1: include per-bar buy/sell volume and the session CVD
        # snapshot at the moment this bar closed.  Older bars that were recorded
        # before this code was deployed will lack these keys — order_flow_engine
        # detects the absence and returns available=False, reason=bars_pre_v1.
        pub: dict[str, Any] = {
            "ts":           bar["ts"],
            "open":         bar["open"],
            "high":         bar["high"],
            "low":          bar["low"],
            "close":        bar["close"],
            "volume":       bar["volume"],
            "buy_volume":   bar.get("buy_volume",  0),
            "sell_volume":  bar.get("sell_volume", 0),
            "cvd_snapshot": self._cvd_acc[inst],   # session cumulative at bar close
        }
        if vwap is not None: pub["vwap"] = vwap
        if atr  is not None: pub["atr"]  = atr
        DATABENTO_BARS_BY_INST[inst].append(pub)

        # ── VOLATILITY_BY_TICKER (replaces Yahoo Finance volatility fetch) ────
        # Compute ATR ratio = recent_atr / baseline_atr so get_volatility() can
        # classify the regime. baseline = per-instrument typical ATR from config.
        if atr is not None and self._vol is not None:
            baseline = self._ATR_BASELINES.get(inst, atr)
            ratio    = round(atr / baseline, 4) if baseline > 0 else 1.0
            self._vol[inst] = {
                "atr_pts":     round(atr, 4),
                "baseline_pts": round(baseline, 4),
                "ratio":       ratio,
                # This is a source-time bar observation; processed_iso would
                # falsely make historical replay look current.
                "ts":          source_iso,
            }

        # ── VWAP_BY_TICKER (gate VWAP — replaces Yahoo Finance auto-refresh) ──
        # get_vwap() reads VWAP_BY_TICKER for all gate decisions. Databento is
        # the PRIMARY source — it always writes at every bar close.
        #
        # Freshness-aware precedence rule (Part 1A VWAP fix):
        #   • Databento ALWAYS writes when it has a value — never blocked by TV.
        #   • TV/chart pushes land in CHART_VWAP_BY_TICKER (a parallel store
        #     maintained by app.py) and ALSO still update VWAP_BY_TICKER, but
        #     Databento will overwrite them within the next completed 1m bar (~60 s).
        #   • The old 10-minute grace window was inverted: it blocked the fresher
        #     Databento value in favour of a potentially stale TV push.  Removed.
        #   • Diagnostic fields (source, db_ts, chart_ts) are written so
        #     get_vwap_diagnostics() can explain the current authoritative value.
        if vwap is not None and self._vwap is not None:
            self._vwap[inst] = {
                "value":   round(vwap, 4),
                "ts":      source_iso,
                "processed_at": processed_iso,
                "source":  "databento",
                "db_ts":   source_iso,
            }

        # ── Telemetry ─────────────────────────────────────────────────────────
        telemetry = dict(DATABENTO_STATUS["instruments"].get(inst) or {})
        telemetry.update({
            "bars":  len(bars),
            "vwap":  round(vwap, 4) if vwap  is not None else None,
            "atr":   round(atr,  4) if atr   is not None else None,
            "cvd":   round(cvd_val, 1),
            "rvol":  round(rvol, 2)  if rvol  is not None else None,
            "price": bar["close"],
            "last_bar_source_timestamp": source_iso,
            "last_bar_processed_at": processed_iso,
        })
        DATABENTO_STATUS["instruments"][inst] = telemetry

        # ── Structure detection (BOS / CHOCH → ALERT_HISTORY) ────────────────
        self._detect_structure(inst, bars)
        # ── Sweep detection (BULLISH/BEARISH SWEEP → ALERT_HISTORY) ──────────
        self._detect_sweep(inst, bars)
        # ── Confirmation candle (BULLISH/BEARISH CONFIRMATION → ALERT_HISTORY) ─
        self._detect_confirmation(inst, bars)
        # ── Bar-close callbacks (proactive scanner hook) ───────────────────────
        # Called AFTER all detectors so every fresh signal is already in
        # ALERT_HISTORY before the scan evaluates the setup.
        # Historical replay reconstructs native detector state only.  It must
        # never fan out a startup callback as though the last closed historical
        # bar were a newly actionable live event.
        if not replay:
            for _cb in self._bar_close_callbacks:
                try:
                    _cb(inst, bars[-1]["close"])
                except Exception:
                    pass

    # ── Structure detection ───────────────────────────────────────────────────

    def _detect_structure(self, inst: str, bars: list) -> None:
        """
        Swing-pivot structure detector.

        Looks at the bar at index [-SWING_N-1]: it has SWING_N completed bars
        after it, so we can confirm whether it was a swing high or low.

        HH / LH — confirmed swing high compared to the prior swing high:
                   HH (Higher High) if the new pivot high > previous; LH if lower.
        HL / LL — confirmed swing low compared to the prior swing low:
                   HL (Higher Low) if the new pivot low > previous; LL if lower.

        BOS DEMAND  — close breaks above a confirmed swing high (bullish continuation)
        BOS SUPPLY  — close breaks below a confirmed swing low  (bearish continuation)
        CHOCH DEMAND — first bullish BOS after a bearish trend (bullish reversal)
        CHOCH SUPPLY — first bearish BOS after a bullish trend (bearish reversal)

        HH/HL/LH/LL are emitted when a pivot is CONFIRMED (regardless of whether
        close has broken above/below it). BOS/CHOCH require the additional break.
        Each label fires once per unique pivot level (deduped via _prev_sh/_prev_sl).
        Each BOS/CHOCH level is injected once only (deduped via _last_bos).
        """
        n = self.SWING_N
        required = n * 2 + 2
        now_bar = bars[-1] if bars else {}
        now_ts = now_bar.get("ts") if isinstance(now_bar, dict) else None

        def _iso(ts: Any) -> str | None:
            try:
                return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
            except (TypeError, ValueError, OSError, OverflowError):
                return None

        trace: dict[str, Any] = {
            "schema": "mnq_structure_provenance.v1",
            "shadow_only": True,
            "instrument": inst,
            "source": "databento",
            "trace_id": uuid.uuid4().hex,
            "evaluation_ts": _iso(now_ts),
            "bar_ts": now_ts,
            "history": {
                "available": len(bars) >= required,
                "bars": len(bars),
                "required": required,
                "reason": (
                    None if len(bars) >= required
                    else "insufficient_completed_bars_for_pivot_confirmation"
                ),
            },
            "pivot": {
                "side": None,
                "level": None,
                "timestamp": None,
                "age_bars": None,
                "age_seconds": None,
            },
            "confirmation_progress": {
                "left_bars": None,
                "right_bars": None,
                "left_required": n,
                "right_required": n,
            },
            "prior_trend": self._trend.get(inst),
            "tested_break": None,
            "raw_decisions": [],
            "dedupe": [],
            "resolved_structure_cycle": None,
            "structure_gate": None,
            "analysis_attached": False,
        }
        if len(bars) < required:
            trace["raw_decisions"].append({
                "alert_type": None,
                "candidate": None,
                "decision": "unavailable",
                "reason": "insufficient_completed_bars_for_pivot_confirmation",
            })
            _append_structure_provenance(inst, trace)
            return

        pi     = len(bars) - n - 1
        pivot  = bars[pi]
        window = range(max(0, pi - n), min(len(bars), pi + n + 1))

        is_sh = all(pivot["high"] >= bars[j]["high"] for j in window if j != pi)
        is_sl = all(pivot["low"]  <= bars[j]["low"]  for j in window if j != pi)

        pivot_sides = [
            side for side, confirmed in (("high", is_sh), ("low", is_sl))
            if confirmed
        ]
        pivot_ts = pivot.get("ts")
        try:
            pivot_age_seconds = max(0.0, float(now_ts) - float(pivot_ts))
        except (TypeError, ValueError):
            pivot_age_seconds = None
        trace["pivot"].update({
            "side": "+".join(pivot_sides) if pivot_sides else "none",
            "level": (
                pivot.get("high") if is_sh
                else pivot.get("low") if is_sl
                else None
            ),
            "timestamp": _iso(pivot_ts),
            "age_bars": len(bars) - 1 - pi,
            "age_seconds": (
                round(pivot_age_seconds, 3)
                if pivot_age_seconds is not None else None
            ),
        })
        trace["confirmation_progress"].update({
            "left_bars": min(n, pi),
            "right_bars": min(n, len(bars) - pi - 1),
        })
        if not pivot_sides:
            trace["raw_decisions"].append({
                "candidate": None,
                "decision": "reject",
                "reason": "candidate_bar_is_not_a_confirmed_swing_pivot",
            })

        close    = bars[-1]["close"]
        last_bos = self._last_bos[inst] or {}

        def _break_decision(side: str, level: float, broken: bool) -> None:
            side_label = "demand" if side == "high" else "supply"
            expected_type = (
                ("CHOCH " if trace["prior_trend"] == "bear" else "BOS ")
                + side_label.upper()
            )
            trace["tested_break"] = {
                "side": side_label,
                "alert_type": expected_type,
                "level": level,
                "close": close,
                "relation": "above" if side == "high" else "below",
                "broken": bool(broken),
            }
            if broken:
                duplicate = (
                    last_bos.get("type") in (
                        f"BOS {side_label.upper()}",
                        f"CHOCH {side_label.upper()}",
                    )
                    and abs(last_bos.get("level", 0) - level) < 0.01
                )
                trace["dedupe"].append({
                    "candidate": side_label,
                    "outcome": "duplicate" if duplicate else "new_level",
                    "last_type": last_bos.get("type"),
                    "last_level": last_bos.get("level"),
                })
                trace["raw_decisions"].append({
                    "alert_type": expected_type,
                    "candidate": side_label,
                    "decision": "reject" if duplicate else "accept",
                    "reason": (
                        "same_break_level_already_emitted"
                        if duplicate else "confirmed_pivot_break"
                    ),
                })
            else:
                trace["raw_decisions"].append({
                    "alert_type": expected_type,
                    "candidate": side_label,
                    "decision": "reject",
                    "reason": (
                        "close_not_above_confirmed_swing_high"
                        if side == "high"
                        else "close_not_below_confirmed_swing_low"
                    ),
                    "pivot_level": level,
                    "close": close,
                })

        # ── HH / LH — swing-high sequence labels ─────────────────────────────
        if is_sh:
            sh = pivot["high"]
            prev_sh = self._prev_sh[inst]
            if prev_sh is None or abs(sh - prev_sh) > sh * 0.001:
                if prev_sh is not None:
                    self._inject_alert(inst, "HH" if sh > prev_sh else "LH", sh)
                self._prev_sh[inst] = sh

        # ── HL / LL — swing-low sequence labels ──────────────────────────────
        if is_sl:
            sl = pivot["low"]
            prev_sl = self._prev_sl[inst]
            if prev_sl is None or abs(sl - prev_sl) > sl * 0.001:
                if prev_sl is not None:
                    self._inject_alert(inst, "HL" if sl > prev_sl else "LL", sl)
                self._prev_sl[inst] = sl

        # ── BOS / CHOCH — close-based structure break ─────────────────────────
        if is_sh:
            _break_decision("high", pivot["high"], close > pivot["high"])
        if is_sh and close > pivot["high"]:
            if not (last_bos.get("type") in ("BOS DEMAND", "CHOCH DEMAND")
                    and abs(last_bos.get("level", 0) - pivot["high"]) < 0.01):
                atype = ("CHOCH DEMAND"
                         if self._trend[inst] == "bear" else "BOS DEMAND")
                self._inject_alert(inst, atype, close)
                self._last_bos[inst] = {"type": atype, "level": pivot["high"]}
                self._trend[inst]    = "bull"

        if is_sl:
            _break_decision("low", pivot["low"], close < pivot["low"])
        if is_sl and close < pivot["low"]:
            if not (last_bos.get("type") in ("BOS SUPPLY", "CHOCH SUPPLY")
                    and abs(last_bos.get("level", 0) - pivot["low"]) < 0.01):
                atype = ("CHOCH SUPPLY"
                         if self._trend[inst] == "bull" else "BOS SUPPLY")
                self._inject_alert(inst, atype, close)
                self._last_bos[inst] = {"type": atype, "level": pivot["low"]}
                self._trend[inst]    = "bear"
        _append_structure_provenance(inst, trace)

    # Alert types that warrant a full scored analysis pass (registered callbacks).
    # HH/HL/LH/LL are informational (edge-score only, no hard-gate effect).
    # Sweeps are already sent by TradingView; triggering here would double-fire.
    _STRUCTURE_CB_TYPES = frozenset({
        "BOS DEMAND", "BOS SUPPLY", "CHOCH DEMAND", "CHOCH SUPPLY",
    })

    def _inject_alert(self, inst: str, alert_type: str, price: float) -> None:
        """Append a synthetic structure alert to the shared ALERT_HISTORY deque."""
        if self._suppress_replay_signals:
            # Detector transitions still update their own per-instrument state
            # during replay; external evidence and execution-trigger callbacks
            # wait for a genuinely live closed bar.
            return
        source_timestamp = (
            self._iso_epoch(self._active_bar_source_ts.get(inst))
            or datetime.now(timezone.utc).isoformat()
        )
        record = {
            "alert_type":        alert_type,
            "ticker":            inst + "1!",
            "instrument":        inst,
            "instrument_source": "databento",
            "source":            "databento",   # explicit feed-source tag
            "canonical":         True,           # Databento events are always canonical
            "price":             float(price),
            "timestamp":         source_timestamp,
            "raw":               {"source": "databento_brain"},
        }
        # Snapshot history BEFORE appending so on_databento_event can retroactively
        # demote any TV entries that were marked canonical=True while Databento
        # hadn't fired yet for the same logical bar-close event.
        _history_snapshot = list(self._ah)
        self._ah.append(record)
        try:
            from structure_dedup import STRUCTURE_DEDUP as _SD  # noqa: PLC0415
            _SD.on_databento_event(record, _history_snapshot)
        except Exception:
            pass
        logger.info("DatabentoBrain ▶ %s  %s @ %.4f", inst, alert_type, price)
        # Notify structure-signal callbacks for BOS/CHOCH and CONFIRMATION events
        # so app.py can enqueue a scored analysis without waiting for a TV webhook.
        _is_scoring = alert_type in self._STRUCTURE_CB_TYPES
        _is_confirm = (alert_type.endswith("BULLISH CONFIRMATION")
                       or alert_type.endswith("BEARISH CONFIRMATION"))
        if (_is_scoring or _is_confirm) and self._structure_signal_callbacks:
            for _cb in self._structure_signal_callbacks:
                try:
                    _cb(inst, alert_type, float(price))
                except Exception as _cbe:
                    logger.error("structure_signal_callback error: %s", _cbe)

    # ── Sweep detection ───────────────────────────────────────────────────────

    def _detect_sweep(self, inst: str, bars: list) -> None:
        """
        Liquidity sweep detector — injects "{inst} BULLISH SWEEP" or
        "{inst} BEARISH SWEEP" into ALERT_HISTORY for the Sweep15 Edge Score
        component (ticker_scoped=True lookup in _latest_ts).

        BULLISH SWEEP — current bar's low undercuts the prior SWEEP_N-bar low
                        but the bar CLOSES back above it (lows swept, bulls win).

        BEARISH SWEEP — current bar's high exceeds the prior SWEEP_N-bar high
                        but the bar CLOSES back below it (highs swept, bears win).

        Deduped per instrument: same direction at the same price level (within
        0.1 %) is only injected once per episode.
        """
        n = self.SWEEP_N
        if len(bars) < n + 2:
            return

        cur      = bars[-1]
        lookback = bars[-(n + 1):-1]      # n bars immediately before current

        prior_high = max(b["high"] for b in lookback)
        prior_low  = min(b["low"]  for b in lookback)

        last = self._last_sweep[inst] or {}

        # ── Bullish sweep: wick below prior low, close above it ───────────────
        if cur["low"] < prior_low and cur["close"] > prior_low:
            if not (last.get("side") == "bull"
                    and abs(last.get("level", 0) - prior_low) <= prior_low * 0.001):
                atype = f"{inst} BULLISH SWEEP"
                self._inject_alert(inst, atype, cur["close"])
                self._last_sweep[inst] = {"side": "bull", "level": prior_low}
                # Bootstrap the confirmation path when no BOS/CHOCH has been
                # confirmed yet.  Setting trend + a provisional _last_bos lets
                # _detect_confirmation fire on the NEXT strong-close bar and
                # inject HL → live gate's _latest_ts("HL") satisfies
                # structure_gate_long immediately.  A real BOS/CHOCH from
                # _detect_structure always overwrites this provisional state.
                if self._last_bos[inst] is None:
                    self._trend[inst]    = "bull"
                    self._last_bos[inst] = {"type": "SWEEP", "level": prior_low}

        # ── Bearish sweep: wick above prior high, close below it ──────────────
        elif cur["high"] > prior_high and cur["close"] < prior_high:
            if not (last.get("side") == "bear"
                    and abs(last.get("level", 0) - prior_high) <= prior_high * 0.001):
                atype = f"{inst} BEARISH SWEEP"
                self._inject_alert(inst, atype, cur["close"])
                self._last_sweep[inst] = {"side": "bear", "level": prior_high}
                if self._last_bos[inst] is None:
                    self._trend[inst]    = "bear"
                    self._last_bos[inst] = {"type": "SWEEP", "level": prior_high}

    # ── Confirmation candle detection ─────────────────────────────────────────

    def _detect_confirmation(self, inst: str, bars: list) -> None:
        """
        Confirmation candle detector — injects "{inst} BULLISH CONFIRMATION" or
        "{inst} BEARISH CONFIRMATION" into ALERT_HISTORY for the gate's
        has_bull_confirm / has_bear_confirm flags (ticker_scoped=True lookup).

        A confirmation fires when ALL of the following are true:
          1. A BOS or CHOCH has already been detected in that direction
             (_trend[inst] is "bull" or "bear" and _last_bos[inst] is set).
          2. The current bar shows DIRECTIONAL COMMITMENT:
               • Strong close: close is in the top 65% of bar range (bull)
                 or bottom 65% (bear), indicating controlled momentum, OR
               • Engulfing: the bar's body fully engulfs the prior bar's body
                 in the opposite direction (classic reversal confirmation).
          3. Above-average volume: current bar volume >= 1.5x the 10-bar
             rolling average, confirming real participation.
          4. Direction-aware cooldown: same-direction confirmation has not
             fired within CONFIRM_COOLDOWN_MIN minutes. Resets automatically
             when trend direction flips (new BOS/CHOCH in the other direction).

        Because _detect_structure runs before _detect_confirmation in
        _on_bar_close, the inject timestamp of the confirmation is always
        >= the inject timestamp of the structure alert, satisfying the gate's
        _after_anchor check naturally.
        """
        n = self.CONFIRM_N
        if len(bars) < n + 2:
            return

        trend    = self._trend[inst]
        last_bos = self._last_bos[inst]
        if trend not in ("bull", "bear") or last_bos is None:
            return

        cur  = bars[-1]
        prev = bars[-2]

        # ── Above-average volume filter ────────────────────────────────────────
        avg_vol = sum(b["volume"] for b in bars[-(n + 1):-1]) / n
        if avg_vol <= 0 or cur["volume"] < avg_vol * self.CONFIRM_VOL_MULT:
            return

        # ── Directional commitment: strong close OR engulfing ──────────────────
        bar_range = cur["high"] - cur["low"]
        if bar_range <= 0:
            return
        close_ratio = (cur["close"] - cur["low"]) / bar_range

        # Engulfing: current body completely contains the prior bar's body in
        # the opposite direction (prior bearish → current bullish engulf; vice versa)
        bull_engulf = (prev["close"] < prev["open"]         # prior bar bearish
                       and cur["close"] > prev["open"]      # cur close above prior open
                       and cur["open"] <= prev["close"])    # cur open at/below prior close
        bear_engulf = (prev["close"] > prev["open"]         # prior bar bullish
                       and cur["close"] < prev["open"]      # cur close below prior open
                       and cur["open"] >= prev["close"])    # cur open at/above prior close

        last_conf = self._last_confirm[inst] or {}
        side      = "bull" if trend == "bull" else "bear"

        # ── Direction-aware cooldown ───────────────────────────────────────────
        # Suppress re-firing the same direction within CONFIRM_COOLDOWN_MIN.
        # Resets automatically when trend flips (last_conf["side"] differs).
        if last_conf.get("side") == side:
            last_ts_str = last_conf.get("ts")
            if last_ts_str:
                try:
                    age_min = (
                        datetime.now(timezone.utc)
                        - datetime.fromisoformat(last_ts_str)
                    ).total_seconds() / 60.0
                    if age_min < self.CONFIRM_COOLDOWN_MIN:
                        return
                except Exception:
                    pass

        if trend == "bull":
            strong_close = close_ratio >= self.CONFIRM_BODY_RATIO
            if not (strong_close or bull_engulf):
                return
            self._inject_alert(inst, f"{inst} BULLISH CONFIRMATION", cur["close"])
            # Bridge the SWING_N look-ahead gap: a confirmed strong-close bullish
            # bar after a detected bullish trend IS a higher-low structure signal.
            # Satisfies _latest_ts("HL") in evaluate_strict_setup so the live gate
            # sees real-time Databento confirmation instead of waiting 5 more bars
            # for the full pivot BOS/CHOCH to be confirmed.  "HL" is NOT in
            # _STRUCTURE_CB_TYPES so no scoring callback is fired — only the
            # ALERT_HISTORY deque entry is written.
            self._inject_alert(inst, "HL", cur["close"])
            self._last_confirm[inst] = {
                "side": "bull",
                "ts":   datetime.now(timezone.utc).isoformat(),
            }

        else:  # trend == "bear"
            strong_close = close_ratio <= (1.0 - self.CONFIRM_BODY_RATIO)
            if not (strong_close or bear_engulf):
                return
            self._inject_alert(inst, f"{inst} BEARISH CONFIRMATION", cur["close"])
            # Bridge: a confirmed strong-close bearish bar is a lower-high signal.
            self._inject_alert(inst, "LH", cur["close"])
            self._last_confirm[inst] = {
                "side": "bear",
                "ts":   datetime.now(timezone.utc).isoformat(),
            }

    # ── Indicator helpers ─────────────────────────────────────────────────────

    def _calc_atr(self, bars: list) -> float | None:
        if len(bars) < 2:
            return None
        trs = []
        for i in range(1, len(bars)):
            h, lo, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
            trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
        if not trs:
            return None
        recent = trs[-self.ATR_PERIOD:]
        return sum(recent) / len(recent)

    def _calc_rvol(self, bars: list) -> float | None:
        if len(bars) < 2:
            return None
        hist = [b["volume"] for b in bars[-(self.RVOL_LOOKBACK + 1):-1]]
        if not hist:
            return None
        avg = sum(hist) / len(hist)
        return bars[-1]["volume"] / avg if avg > 0 else None

    def _vol_regime(self, atr: float, inst: str) -> str:
        base = self._ATR_BASELINES.get(inst, 1.0)
        r    = atr / base if base > 0 else 1.0
        if r > 3.0: return "extreme"
        if r > 1.8: return "elevated"
        if r < 0.5: return "low"
        return "normal"
