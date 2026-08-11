"""
09:30 ORB Algorithmic Engine — orb_engine.py
Strategy Version: 1.0.0

Four-instrument independent 09:30 Opening Range Breakout engine.
Runs in SHADOW mode by default — never transmits orders.
Completely independent of the live 08:00 OPENING_RANGE_BREAKOUT strategy.

Architecture:
• OrbEngine singleton manages 4 per-instrument state machines (MGC/MNQ/MES/MYM).
• Receives bar-close callbacks from the existing Databento infrastructure.
• Fail-open: any exception is logged; the instrument transitions to DATA_INVALID;
  live trading is never affected.
• Persistence via Postgres using the app's existing get_db_connection().

Non-negotiable boundaries:
• SHADOW mode → no order transmission; broker gateway is never invoked.
• Does NOT modify the 08:00 intraday state dicts or breakout tracker dicts.
• Does NOT create a second Databento connection.
• Does NOT alter the 08:00 ORB scorer, tracker, or target override.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

try:
    import pytz
    _ET_TZ: Any = pytz.timezone("America/New_York")
except ImportError:
    _ET_TZ = timezone(timedelta(hours=-4))  # EDT fallback

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Version constants
# ─────────────────────────────────────────────────────────────────────────────

ORB_STRATEGY_VERSION = "1.0.0"
ORB_CONFIG_VERSION   = "1.0.0"

_INSTRUMENTS     = ("MGC", "MNQ", "MES", "MYM")
_INDEX_GROUP     = frozenset({"MNQ", "MES", "MYM"})
_METALS_GROUP    = frozenset({"MGC"})
_RANGE_DURATIONS = frozenset({5, 10, 15, 30})


# ─────────────────────────────────────────────────────────────────────────────
# State / mode constants
# ─────────────────────────────────────────────────────────────────────────────

class OrbState:
    """All 32 canonical ORB per-instrument states (spec §6)."""
    DISABLED                 = "DISABLED"
    WAITING_FOR_SESSION      = "WAITING_FOR_SESSION"
    WAITING_FOR_RANGE        = "WAITING_FOR_RANGE"
    BUILDING_RANGE           = "BUILDING_RANGE"
    RANGE_LOCKED             = "RANGE_LOCKED"
    WATCHING_BREAKOUT        = "WATCHING_BREAKOUT"
    BREAKOUT_DETECTED        = "BREAKOUT_DETECTED"
    CONFIRMATION_PENDING     = "CONFIRMATION_PENDING"
    QUALIFIED                = "QUALIFIED"
    BREAKOUT_MISSED          = "BREAKOUT_MISSED"
    RISK_PENDING             = "RISK_PENDING"
    RISK_RESERVED            = "RISK_RESERVED"
    BLOCKED_BY_DATA          = "BLOCKED_BY_DATA"
    BLOCKED_BY_RANGE_WIDTH   = "BLOCKED_BY_RANGE_WIDTH"
    BLOCKED_BY_CONFIRMATION  = "BLOCKED_BY_CONFIRMATION"
    BLOCKED_BY_MAXIMUM_CHASE = "BLOCKED_BY_MAXIMUM_CHASE"
    BLOCKED_BY_INSTRUMENT_RISK = "BLOCKED_BY_INSTRUMENT_RISK"
    BLOCKED_BY_GROUP_RISK    = "BLOCKED_BY_GROUP_RISK"
    BLOCKED_BY_PORTFOLIO_RISK = "BLOCKED_BY_PORTFOLIO_RISK"
    BLOCKED_BY_PROP_RULE     = "BLOCKED_BY_PROP_RULE"
    BLOCKED_BY_DAILY_LOSS    = "BLOCKED_BY_DAILY_LOSS"
    BLOCKED_BY_POSITION_LIMIT = "BLOCKED_BY_POSITION_LIMIT"
    BLOCKED_BY_DUPLICATE_GUARD = "BLOCKED_BY_DUPLICATE_GUARD"
    BLOCKED_BY_EXECUTION_MODE = "BLOCKED_BY_EXECUTION_MODE"
    BLOCKED_BY_ARM_STATE     = "BLOCKED_BY_ARM_STATE"
    BLOCKED_BY_SAFETY_LOCK   = "BLOCKED_BY_SAFETY_LOCK"
    ENTRY_REQUESTED          = "ENTRY_REQUESTED"
    ORDER_ACCEPTED           = "ORDER_ACCEPTED"
    ORDER_REJECTED           = "ORDER_REJECTED"
    POSITION_ACTIVE          = "POSITION_ACTIVE"
    POSITION_MANAGING        = "POSITION_MANAGING"
    COMPLETED                = "COMPLETED"
    EXPIRED                  = "EXPIRED"
    DATA_INVALID             = "DATA_INVALID"
    RECOVERY_REQUIRED        = "RECOVERY_REQUIRED"


class ConfirmationMode:
    TOUCH            = "TOUCH"
    CLOSE_OUTSIDE    = "CLOSE_OUTSIDE"
    CLOSE_AND_RETEST = "CLOSE_AND_RETEST"


class ExecutionMode:
    DISABLED         = "DISABLED"
    SHADOW           = "SHADOW"
    PAPER            = "PAPER"
    LIVE_ALGORITHMIC = "LIVE_ALGORITHMIC"

    _ORDER = [DISABLED, SHADOW, PAPER, LIVE_ALGORITHMIC]

    @classmethod
    def cap(cls, global_mode: str, per_mode: str) -> str:
        """Return the less permissive of two modes (global caps per-instrument)."""
        gi = cls._ORDER.index(global_mode) if global_mode in cls._ORDER else 0
        pi = cls._ORDER.index(per_mode)    if per_mode    in cls._ORDER else 0
        return cls._ORDER[min(gi, pi)]


class CorrelationMode:
    FULL_RISK_EACH           = "FULL_RISK_EACH"
    SHARED_GROUP_BUDGET      = "SHARED_GROUP_BUDGET"
    FIXED_FRACTION_EACH      = "FIXED_FRACTION_EACH"
    DYNAMIC_REMAINING_BUDGET = "DYNAMIC_REMAINING_BUDGET"


class RiskReservationState:
    NONE                    = "NONE"
    PENDING                 = "PENDING"
    RESERVED                = "RESERVED"
    CONVERTED_TO_ACTIVE     = "CONVERTED_TO_ACTIVE"
    RELEASED                = "RELEASED"
    EXPIRED                 = "EXPIRED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


# Per-instrument tick buffers (spec §10, §17)
_DEFAULT_BREAKOUT_BUFFER_TICKS: Dict[str, int] = {"MGC": 2, "MNQ": 4, "MES": 2, "MYM": 4}
_DEFAULT_STOP_BUFFER_TICKS:     Dict[str, int] = {"MGC": 2, "MNQ": 4, "MES": 2, "MYM": 4}


# ─────────────────────────────────────────────────────────────────────────────
# Configuration dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OrbConfig:
    enabled:       bool = False
    global_mode:   str  = ExecutionMode.SHADOW
    strategy_version: str = ORB_STRATEGY_VERSION
    config_version:   str = ORB_CONFIG_VERSION

    # Range timing
    range_duration_min:    int  = 10       # 5 / 10 / 15 / 30
    entry_window_end_et:   str  = "10:30"  # HH:MM ET

    # Per-instrument buffers in ticks
    breakout_buffer_ticks: dict = field(default_factory=lambda: dict(_DEFAULT_BREAKOUT_BUFFER_TICKS))
    stop_buffer_ticks:     dict = field(default_factory=lambda: dict(_DEFAULT_STOP_BUFFER_TICKS))

    # Confirmation
    confirmation_mode:             str  = ConfirmationMode.CLOSE_OUTSIDE
    per_instrument_confirmation:   dict = field(default_factory=dict)

    # Chase protection
    max_chase_pct: float = 0.25  # 25% of OR width

    # Range-width filter
    min_range_width_atr: float = 0.20
    max_range_width_atr: float = 1.50

    # Targets (spec §18)
    tp1_r:               float = 1.0
    tp2_r:               float = 2.0
    single_contract_r:   float = 1.5

    # Portfolio / risk limits (spec §22)
    max_orb_total_risk:        float = 450.0
    max_index_group_risk:      float = 300.0
    max_metals_group_risk:     float = 150.0
    max_risk_per_instrument:   float = 150.0
    max_simultaneous_positions: int  = 4
    max_simultaneous_index:    int   = 3
    max_simultaneous_metals:   int   = 1
    max_trades_per_instrument: int   = 1
    allow_reentry:             bool  = False
    correlation_mode:          str   = CorrelationMode.SHARED_GROUP_BUDGET

    # Per-instrument overrides
    instrument_modes:   dict = field(default_factory=dict)   # {"MGC": "SHADOW", …}
    instrument_enabled: dict = field(default_factory=dict)   # {"MGC": True, …}

    # ── Derived helpers ───────────────────────────────────────────────────────

    def lock_time_minutes(self) -> int:
        """Range-lock time in minutes from midnight ET."""
        return 9 * 60 + 30 + self.range_duration_min

    def range_end_minutes_inclusive(self) -> int:
        """Last inclusive range bar minute from midnight ET."""
        return self.lock_time_minutes() - 1

    def entry_end_minutes(self) -> int:
        parts = self.entry_window_end_et.split(":")
        return int(parts[0]) * 60 + int(parts[1])

    def effective_mode_for(self, inst: str) -> str:
        per = self.instrument_modes.get(inst, self.global_mode)
        return ExecutionMode.cap(self.global_mode, per)

    def is_instrument_enabled(self, inst: str) -> bool:
        return bool(self.instrument_enabled.get(inst, True))

    def breakout_buffer_pts(self, inst: str, tick_size: float) -> float:
        return self.breakout_buffer_ticks.get(inst, 2) * tick_size

    def stop_buffer_pts(self, inst: str, tick_size: float) -> float:
        return self.stop_buffer_ticks.get(inst, 2) * tick_size


# ─────────────────────────────────────────────────────────────────────────────
# Per-instrument state
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OrbInstrumentState:
    instrument:       str
    trading_date:     str = ""
    strategy_version: str = ORB_STRATEGY_VERSION
    config_version:   str = ORB_CONFIG_VERSION
    mode:             str = ExecutionMode.SHADOW
    state:            str = OrbState.DISABLED
    block_reason:     str = ""

    # Range timing (minutes from midnight ET)
    range_duration_min:    int = 10
    range_start_minutes:   int = 570   # 09:30
    lock_minutes:          int = 580   # 09:40 (10-min default)
    entry_end_minutes:     int = 630   # 10:30

    # Opening range (IMMUTABLE once range_locked=True)
    range_locked:        bool            = False
    range_locked_ts:     Optional[str]   = None
    or_high:             Optional[float] = None
    or_low:              Optional[float] = None
    or_midpoint:         Optional[float] = None
    or_width:            Optional[float] = None
    or_valid:            bool            = False
    range_bars_observed: int             = 0
    range_first_bar_ts:  Optional[int]   = None
    range_last_bar_ts:   Optional[int]   = None

    # Breakout levels
    long_breakout_level:  Optional[float] = None
    short_breakout_level: Optional[float] = None
    breakout_direction:   Optional[str]   = None   # "LONG" / "SHORT"
    breakout_bar_ts:      Optional[int]   = None

    # Confirmation
    confirmation_mode:    str            = ConfirmationMode.CLOSE_OUTSIDE
    prior_qual_bar_ts:    Optional[int]  = None    # dedup: last bar that created a candidate
    two_sided_sweep:      bool           = False

    # Max chase
    max_chase_boundary_long:  Optional[float] = None
    max_chase_boundary_short: Optional[float] = None
    max_chase_exceeded_long:  bool            = False
    max_chase_exceeded_short: bool            = False

    # Trade plan
    entry:               Optional[float] = None
    stop:                Optional[float] = None
    tp1:                 Optional[float] = None
    tp2:                 Optional[float] = None
    risk_per_contract:   Optional[float] = None
    contracts_proposed:  int             = 0
    contracts_approved:  int             = 0
    risk_dollars:        float           = 0.0

    # Position
    risk_reservation_state: str   = RiskReservationState.NONE
    daily_trade_count:      int   = 0
    shadow_entries:         list  = field(default_factory=list)

    # Live context (updated on every bar close)
    current_price:  Optional[float] = None
    current_atr:    Optional[float] = None
    current_vwap:   Optional[float] = None
    last_bar_ts:    Optional[int]   = None
    last_bar_close: Optional[float] = None
    last_update:    Optional[str]   = None

    # Timeline ring-buffer (last 200 events)
    timeline_events: list = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Portfolio risk accounting
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _PortfolioRisk:
    active_positions:         int   = 0
    active_index_positions:   int   = 0
    active_metals_positions:  int   = 0
    active_index_risk:        float = 0.0
    active_metals_risk:       float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Contract-sizing helper (mirrors _risk_capped_contracts in app.py)
# ─────────────────────────────────────────────────────────────────────────────

def _orb_contracts(stop_dist: float, point_value: float, max_risk: float) -> tuple:
    """Floor-division position sizing.  Returns (contracts: int, risk_per_contract: float).
    Never returns negative contracts.  Mirrors the logic of _risk_capped_contracts()
    in app.py without creating a circular import."""
    if stop_dist <= 0 or point_value <= 0 or max_risk <= 0:
        return 0, 0.0
    rpc       = float(stop_dist) * float(point_value)
    contracts = int(max_risk / rpc)
    return max(0, contracts), rpc


# ─────────────────────────────────────────────────────────────────────────────
# OrbEngine
# ─────────────────────────────────────────────────────────────────────────────

class OrbEngine:
    """
    Four-instrument 09:30 ORB state-machine engine.

    Initialization (wired inside the Flask application)::

        from databento_brain import DATABENTO_BARS_BY_INST
        _ORB_ENGINE = OrbEngine(
            assets=ASSETS,
            get_db_fn=get_db_connection,
            get_bars_fn=lambda inst: list(DATABENTO_BARS_BY_INST.get(inst, [])),
        )
        _ORB_ENGINE.boot()
        _DATABENTO_BRAIN.register_bar_close_callback(_orb_bar_close)

    Fail-open contract:
    • Any per-instrument exception logs + transitions to DATA_INVALID.
    • One instrument failure never affects others.
    • Production trading path is never touched.
    """

    ORB_DB_READY: bool = False  # set True after ensure_tables() succeeds

    def __init__(
        self,
        assets:       dict,
        get_db_fn:    Any,
        get_bars_fn:  Any,
        now_fn:       Any  = None,
        logger_ref:   Any  = None,
    ):
        self._assets     = assets
        self._get_db     = get_db_fn
        self._get_bars   = get_bars_fn   # get_bars_fn(inst) → list[bar_dict]
        self._now        = now_fn or (lambda: datetime.now(timezone.utc))
        self._log        = logger_ref or logger

        self._config:    OrbConfig = OrbConfig()
        self._state:     Dict[str, OrbInstrumentState] = {}
        self._portfolio: _PortfolioRisk = _PortfolioRisk()
        self._lock       = threading.RLock()
        self._risk_lock  = threading.Lock()

        for inst in _INSTRUMENTS:
            self._state[inst] = OrbInstrumentState(instrument=inst)

    # ─────────────────────────────────────────────────────────────────────────
    # Boot / recovery
    # ─────────────────────────────────────────────────────────────────────────

    def boot(self) -> None:
        """Create tables, restore today's locked ranges, activate instruments."""
        try:
            conn = self._get_db()
            if conn:
                try:
                    self._ensure_tables(conn)
                    OrbEngine.ORB_DB_READY = True
                    self._restore_config(conn)
                    self._restore_today(conn)
                    self._log.info("OrbEngine: boot — DB ready, today's state restored")
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
            else:
                self._log.warning("OrbEngine: boot — DB unavailable; starting in-memory only")
        except Exception as exc:
            self._log.error("OrbEngine: boot error — %s", exc)

        with self._lock:
            for inst in _INSTRUMENTS:
                s   = self._state[inst]
                cfg = self._config
                if s.state != OrbState.DISABLED:
                    continue   # already restored from DB
                if cfg.effective_mode_for(inst) == ExecutionMode.DISABLED:
                    continue
                if not cfg.is_instrument_enabled(inst):
                    continue
                self._activate_instrument(inst)

    def _activate_instrument(self, inst: str) -> None:
        """Initialise per-instrument state for a new/restored session."""
        cfg = self._config
        s   = self._state[inst]
        today = self._now().astimezone(_ET_TZ).date().isoformat()
        s.trading_date      = today
        s.mode              = cfg.effective_mode_for(inst)
        s.strategy_version  = cfg.strategy_version
        s.config_version    = cfg.config_version
        s.range_duration_min    = cfg.range_duration_min
        s.range_start_minutes   = 9 * 60 + 30
        s.lock_minutes          = cfg.lock_time_minutes()
        s.entry_end_minutes     = cfg.entry_end_minutes()
        s.confirmation_mode     = cfg.per_instrument_confirmation.get(inst, cfg.confirmation_mode)
        self._transition(inst, OrbState.WAITING_FOR_SESSION, "activated", {})

    # ─────────────────────────────────────────────────────────────────────────
    # Public entry point (registered as Databento bar-close callback)
    # ─────────────────────────────────────────────────────────────────────────

    def on_bar_close(self, inst: str, price: float) -> None:
        """Process a completed 1-minute Databento bar for one instrument.
        Fail-open: any exception is caught and logged; live trading is unaffected."""
        if inst not in _INSTRUMENTS:
            return
        try:
            bars = self._get_bars(inst)
            if not bars:
                return
            bar = bars[-1]   # the bar just appended (most recently closed)
            self._process(inst, bar)
        except Exception as exc:
            self._log.warning("OrbEngine [%s]: on_bar_close error — %s", inst, exc)
            try:
                with self._lock:
                    s = self._state.get(inst)
                    if s and s.state not in (
                        OrbState.DISABLED, OrbState.DATA_INVALID, OrbState.RECOVERY_REQUIRED
                    ):
                        self._transition(inst, OrbState.DATA_INVALID, str(exc)[:120], {})
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # Core per-instrument processing
    # ─────────────────────────────────────────────────────────────────────────

    def _process(self, inst: str, bar: dict) -> None:
        with self._lock:
            s = self._state[inst]
            if s.state == OrbState.DISABLED:
                return

            bar_ts = bar.get("ts")
            if bar_ts is None:
                return

            bar_et  = datetime.fromtimestamp(int(bar_ts), tz=_ET_TZ)
            bar_min = bar_et.hour * 60 + bar_et.minute
            today   = bar_et.date().isoformat()
            now_iso = self._now().isoformat()

            # ── Day rollover ───────────────────────────────────────────────
            if s.trading_date and s.trading_date != today:
                self._day_rollover(inst, today)
                s = self._state[inst]   # refreshed after rollover

            if not s.trading_date:
                s.trading_date = today

            # ── Update live context ────────────────────────────────────────
            s.last_bar_ts    = int(bar_ts)
            s.last_bar_close = bar.get("close")
            s.current_price  = bar.get("close")
            s.current_atr    = bar.get("atr")
            s.current_vwap   = bar.get("vwap")
            s.last_update    = now_iso

            # ── State machine dispatch ─────────────────────────────────────
            if s.state in (OrbState.WAITING_FOR_SESSION, OrbState.WAITING_FOR_RANGE):
                self._on_pre_range(inst, s, bar, bar_min, now_iso)

            elif s.state == OrbState.BUILDING_RANGE:
                self._on_building_range(inst, s, bar, bar_min, now_iso)

            elif s.state in (OrbState.RANGE_LOCKED, OrbState.WATCHING_BREAKOUT):
                self._on_watching(inst, s, bar, bar_min, now_iso)

            elif s.state == OrbState.BREAKOUT_DETECTED:
                self._on_breakout_detected(inst, s, bar, bar_min, now_iso)

            elif s.state == OrbState.CONFIRMATION_PENDING:
                self._on_confirmation_pending(inst, s, bar, bar_min, now_iso)

            # Persist snapshot asynchronously
            self._persist_state_async(inst)

    # ─────────────────────────────────────────────────────────────────────────
    # State handlers
    # ─────────────────────────────────────────────────────────────────────────

    def _on_pre_range(self, inst, s, bar, bar_min, now_iso):
        if bar_min < s.range_start_minutes:
            return   # too early
        if bar_min < s.lock_minutes:
            # First bar inside the range window
            self._transition(inst, OrbState.BUILDING_RANGE, "first_range_bar", {"bar_min": bar_min})
            self._accumulate(inst, s, bar, now_iso)
        else:
            # Past lock time with no range bars at all
            self._transition(inst, OrbState.BLOCKED_BY_DATA, "no_range_data_before_lock", {})
            self._tl(inst, "BLOCKED_BY_DATA", "No 09:30 bars received before lock time", now_iso)

    def _on_building_range(self, inst, s, bar, bar_min, now_iso):
        if bar_min < s.lock_minutes:
            self._accumulate(inst, s, bar, now_iso)
        else:
            # First post-range bar — lock then check breakout
            self._lock_range(inst, s, now_iso)
            if s.state == OrbState.WATCHING_BREAKOUT:
                self._on_watching(inst, s, bar, bar_min, now_iso)

    def _on_watching(self, inst, s, bar, bar_min, now_iso):
        # Normalise: RANGE_LOCKED immediately advances to WATCHING_BREAKOUT
        if s.state == OrbState.RANGE_LOCKED:
            self._transition(inst, OrbState.WATCHING_BREAKOUT, "watching", {})

        # Entry window expired?
        if bar_min >= s.entry_end_minutes:
            self._transition(inst, OrbState.EXPIRED, "entry_window_expired", {"bar_min": bar_min})
            self._tl(inst, "EXPIRED", f"Entry window closed at {bar_min//60:02d}:{bar_min%60:02d} ET", now_iso)
            return

        if not s.or_valid or s.or_high is None or s.or_low is None:
            return

        close = bar.get("close")
        if close is None:
            return

        # Compute max-chase boundaries once per bar
        or_width = s.or_width or 0.0
        chase    = or_width * self._config.max_chase_pct
        s.max_chase_boundary_long  = round(s.or_high + chase, 6) if s.or_high else None
        s.max_chase_boundary_short = round(s.or_low  - chase, 6) if s.or_low  else None

        cm = s.confirmation_mode

        if cm == ConfirmationMode.TOUCH:
            self._check_touch(inst, s, bar, close, now_iso)
        else:
            self._check_close_outside(inst, s, bar, close, cm, now_iso)

    def _check_touch(self, inst, s, bar, close, now_iso):
        """TOUCH mode: intrabar high/low reaching the breakout level qualifies."""
        bar_high = bar.get("high", close)
        bar_low  = bar.get("low",  close)

        if (s.long_breakout_level is not None
                and bar_high >= s.long_breakout_level
                and bar.get("ts") != s.prior_qual_bar_ts):
            if s.max_chase_boundary_long and close > s.max_chase_boundary_long:
                self._block_chase(inst, s, "LONG", now_iso)
                return
            self._qualify(inst, s, "LONG", bar, now_iso)

        elif (s.short_breakout_level is not None
                and bar_low <= s.short_breakout_level
                and bar.get("ts") != s.prior_qual_bar_ts):
            if s.max_chase_boundary_short and close < s.max_chase_boundary_short:
                self._block_chase(inst, s, "SHORT", now_iso)
                return
            self._qualify(inst, s, "SHORT", bar, now_iso)

    def _check_close_outside(self, inst, s, bar, close, cm, now_iso):
        """CLOSE_OUTSIDE (default) and CLOSE_AND_RETEST first step."""
        bar_open = bar.get("open", close)
        bar_high = bar.get("high", close)
        bar_low  = bar.get("low",  close)
        bar_rng  = bar_high - bar_low

        # Detect two-sided sweep using intrabar high/low (not close): a single bar
        # can wick through both levels while closing in between; checking close on
        # both sides is physically impossible.
        two_side_long  = (s.long_breakout_level  is not None
                          and bar_high >= s.long_breakout_level)
        two_side_short = (s.short_breakout_level is not None
                          and bar_low  <= s.short_breakout_level)
        if two_side_long and two_side_short:
            s.two_sided_sweep = True
            self._tl(inst, "TWO_SIDED_RANGE_SWEEP",
                     "Both sides swept same bar — monitoring", now_iso)
            return

        long_ok = (
            s.long_breakout_level is not None
            and close >= s.long_breakout_level
            and bar.get("ts") != s.prior_qual_bar_ts
        )
        short_ok = (
            s.short_breakout_level is not None
            and close <= s.short_breakout_level
            and bar.get("ts") != s.prior_qual_bar_ts
        )

        if long_ok:
            if s.max_chase_boundary_long and close > s.max_chase_boundary_long:
                self._block_chase(inst, s, "LONG", now_iso)
                return
            # Candle must close in upper 50% of its range (bullish)
            if bar_rng > 1e-9 and close < (bar_low + bar_rng * 0.5):
                return   # bearish close — don't qualify long
            if cm == ConfirmationMode.CLOSE_AND_RETEST:
                self._transition(inst, OrbState.BREAKOUT_DETECTED, "close_outside_long", {})
                s.breakout_direction = "LONG"
                s.breakout_bar_ts    = bar.get("ts")
                self._tl(inst, "BREAKOUT_DETECTED",
                          f"LONG close {close:.4f} ≥ {s.long_breakout_level:.4f} — awaiting retest", now_iso)
            else:
                self._qualify(inst, s, "LONG", bar, now_iso)
            return

        if short_ok:
            if s.max_chase_boundary_short and close < s.max_chase_boundary_short:
                self._block_chase(inst, s, "SHORT", now_iso)
                return
            # Candle must close in lower 50% of its range (bearish)
            if bar_rng > 1e-9 and close > (bar_high - bar_rng * 0.5):
                return   # bullish close — don't qualify short
            if cm == ConfirmationMode.CLOSE_AND_RETEST:
                self._transition(inst, OrbState.BREAKOUT_DETECTED, "close_outside_short", {})
                s.breakout_direction = "SHORT"
                s.breakout_bar_ts    = bar.get("ts")
                self._tl(inst, "BREAKOUT_DETECTED",
                          f"SHORT close {close:.4f} ≤ {s.short_breakout_level:.4f} — awaiting retest", now_iso)
            else:
                self._qualify(inst, s, "SHORT", bar, now_iso)

    def _on_breakout_detected(self, inst, s, bar, bar_min, now_iso):
        """CLOSE_AND_RETEST: waiting for price to pull back to the breakout boundary."""
        if bar_min >= s.entry_end_minutes:
            self._transition(inst, OrbState.EXPIRED, "entry_window_expired", {})
            return
        close = bar.get("close")
        if close is None or s.breakout_direction is None:
            return
        level = s.long_breakout_level if s.breakout_direction == "LONG" else s.short_breakout_level
        if level is None:
            return
        tick = self._tick_size(inst)
        tol  = 3 * tick   # retest must hold within 3 ticks of the level
        if s.breakout_direction == "LONG":
            if abs(close - level) <= tol:
                self._transition(inst, OrbState.CONFIRMATION_PENDING, "retest_started", {})
            elif close < level - tol:
                # Fell back inside range — go back to watching
                self._transition(inst, OrbState.WATCHING_BREAKOUT, "retest_failed_inside_range", {})
        else:
            if abs(close - level) <= tol:
                self._transition(inst, OrbState.CONFIRMATION_PENDING, "retest_started", {})
            elif close > level + tol:
                self._transition(inst, OrbState.WATCHING_BREAKOUT, "retest_failed_inside_range", {})

    def _on_confirmation_pending(self, inst, s, bar, bar_min, now_iso):
        """CLOSE_AND_RETEST: retest bar — check if the level holds."""
        if bar_min >= s.entry_end_minutes:
            self._transition(inst, OrbState.EXPIRED, "entry_window_expired", {})
            return
        close = bar.get("close")
        if close is None or s.breakout_direction is None:
            return
        if s.breakout_direction == "LONG":
            if close >= (s.long_breakout_level or 0):
                self._qualify(inst, s, "LONG", bar, now_iso)
            else:
                self._transition(inst, OrbState.WATCHING_BREAKOUT, "retest_failed", {})
        else:
            if close <= (s.short_breakout_level or float("inf")):
                self._qualify(inst, s, "SHORT", bar, now_iso)
            else:
                self._transition(inst, OrbState.WATCHING_BREAKOUT, "retest_failed", {})

    # ─────────────────────────────────────────────────────────────────────────
    # Range accumulation and locking
    # ─────────────────────────────────────────────────────────────────────────

    def _accumulate(self, inst, s, bar, now_iso):
        h  = bar.get("high")
        lo = bar.get("low")
        ts = bar.get("ts")
        if h is None or lo is None:
            return
        s.or_high = h  if s.or_high is None else max(s.or_high, h)
        s.or_low  = lo if s.or_low  is None else min(s.or_low,  lo)
        s.range_bars_observed += 1
        if s.range_first_bar_ts is None:
            s.range_first_bar_ts = ts
        s.range_last_bar_ts = ts

    def _lock_range(self, inst: str, s: OrbInstrumentState, now_iso: str) -> None:
        """Validate and PERMANENTLY lock the opening range for this session.
        Once locked, or_high / or_low / or_width are immutable (spec §8)."""
        if s.range_locked:
            return   # immutable — ignore re-lock attempt

        if s.or_high is None or s.or_low is None or s.range_bars_observed == 0:
            self._transition(inst, OrbState.BLOCKED_BY_DATA,
                             "no_range_bars", {"bars": s.range_bars_observed})
            self._tl(inst, "BLOCKED_BY_DATA",
                     f"Locked attempted with {s.range_bars_observed} bars — blocked", now_iso)
            return

        if s.or_high <= s.or_low:
            self._transition(inst, OrbState.BLOCKED_BY_DATA, "invalid_high_low",
                             {"high": s.or_high, "low": s.or_low})
            return

        or_width = s.or_high - s.or_low

        # Range-width ATR filter (spec §9) — only when ATR is available
        atr = s.current_atr
        if atr and atr > 0:
            ratio = or_width / atr
            if ratio < self._config.min_range_width_atr:
                self._transition(inst, OrbState.BLOCKED_BY_RANGE_WIDTH, "range_too_narrow",
                                 {"ratio": round(ratio, 4), "min": self._config.min_range_width_atr})
                self._tl(inst, "BLOCKED_BY_RANGE_WIDTH",
                         f"Width {or_width:.4f} / ATR {atr:.4f} = {ratio:.2f} < min {self._config.min_range_width_atr}", now_iso)
                return
            if ratio > self._config.max_range_width_atr:
                self._transition(inst, OrbState.BLOCKED_BY_RANGE_WIDTH, "range_too_wide",
                                 {"ratio": round(ratio, 4), "max": self._config.max_range_width_atr})
                self._tl(inst, "BLOCKED_BY_RANGE_WIDTH",
                         f"Width {or_width:.4f} / ATR {atr:.4f} = {ratio:.2f} > max {self._config.max_range_width_atr}", now_iso)
                return

        # ── LOCK (immutable from here) ──────────────────────────────────────
        tick_size = self._tick_size(inst)
        buf       = self._config.breakout_buffer_pts(inst, tick_size)

        s.or_midpoint          = round((s.or_high + s.or_low) / 2.0, 6)
        s.or_width             = round(or_width, 6)
        s.or_valid             = True
        s.range_locked         = True
        s.range_locked_ts      = now_iso
        s.long_breakout_level  = round(s.or_high + buf, 6)
        s.short_breakout_level = round(s.or_low  - buf, 6)

        self._transition(inst, OrbState.RANGE_LOCKED, "range_locked", {
            "or_high": s.or_high, "or_low": s.or_low,
            "or_width": s.or_width, "bars": s.range_bars_observed,
            "long_level": s.long_breakout_level, "short_level": s.short_breakout_level,
        })
        lock_et_min = s.lock_minutes
        self._tl(inst, "RANGE_LOCKED",
                 f"Range locked at {lock_et_min//60:02d}:{lock_et_min%60:02d} ET — "
                 f"H={s.or_high:.4f}  L={s.or_low:.4f}  W={s.or_width:.4f} | "
                 f"Long>{s.long_breakout_level:.4f}  Short<{s.short_breakout_level:.4f}",
                 now_iso)

    # ─────────────────────────────────────────────────────────────────────────
    # Chase blocking
    # ─────────────────────────────────────────────────────────────────────────

    def _block_chase(self, inst, s, direction, now_iso):
        boundary = s.max_chase_boundary_long if direction == "LONG" else s.max_chase_boundary_short
        if direction == "LONG":
            s.max_chase_exceeded_long = True
        else:
            s.max_chase_exceeded_short = True
        self._transition(inst, OrbState.BREAKOUT_MISSED,
                         f"price_exceeded_maximum_chase_{direction.lower()}", {})
        self._tl(inst, "BLOCKED_BY_MAXIMUM_CHASE",
                 f"{direction} price exceeded max-chase boundary {boundary:.4f} "
                 f"({self._config.max_chase_pct*100:.0f}% of range width)", now_iso)

    # ─────────────────────────────────────────────────────────────────────────
    # Qualification → Shadow entry
    # ─────────────────────────────────────────────────────────────────────────

    def _qualify(self, inst: str, s: OrbInstrumentState,
                 direction: str, bar: dict, now_iso: str) -> None:
        """All confirmation checks passed.  Compute plan, run risk, create shadow entry."""

        # Daily trade limit
        if s.daily_trade_count >= self._config.max_trades_per_instrument:
            self._transition(inst, OrbState.BLOCKED_BY_POSITION_LIMIT,
                             "daily_trade_limit_reached", {"count": s.daily_trade_count})
            return

        # Portfolio risk accounting
        ok, reason = self._check_portfolio_risk(inst)
        if not ok:
            self._transition(inst, OrbState.BLOCKED_BY_GROUP_RISK, reason, {})
            self._tl(inst, "BLOCKED_BY_GROUP_RISK", reason, now_iso)
            return

        # Compute stop and targets
        tick_size = self._tick_size(inst)
        stop_buf  = self._config.stop_buffer_pts(inst, tick_size)
        pv        = self._point_value(inst)

        if direction == "LONG":
            entry = s.long_breakout_level
            stop  = round(s.or_low  - stop_buf, 6) if s.or_low  is not None else None
        else:
            entry = s.short_breakout_level
            stop  = round(s.or_high + stop_buf, 6) if s.or_high is not None else None

        if entry is None or stop is None:
            return

        # Validate stop side
        if direction == "LONG"  and stop >= entry:
            self._transition(inst, OrbState.BLOCKED_BY_DATA, "invalid_stop_long",  {})
            return
        if direction == "SHORT" and stop <= entry:
            self._transition(inst, OrbState.BLOCKED_BY_DATA, "invalid_stop_short", {})
            return

        stop_dist = abs(entry - stop)
        r         = stop_dist
        if direction == "LONG":
            tp1 = round(entry + r * self._config.tp1_r, 6)
            tp2 = round(entry + r * self._config.tp2_r, 6)
        else:
            tp1 = round(entry - r * self._config.tp1_r, 6)
            tp2 = round(entry - r * self._config.tp2_r, 6)

        contracts, rpc = _orb_contracts(stop_dist, pv, self._config.max_risk_per_instrument)
        if contracts == 0:
            self._transition(inst, OrbState.BLOCKED_BY_INSTRUMENT_RISK,
                             "zero_contracts_after_sizing", {"rpc": rpc, "max": self._config.max_risk_per_instrument})
            return

        # Single-contract fallback target (spec §18)
        if contracts == 1:
            single_r = r * self._config.single_contract_r
            tp2 = round(entry + single_r, 6) if direction == "LONG" else round(entry - single_r, 6)

        # Write plan to state
        s.entry             = entry
        s.stop              = stop
        s.tp1               = tp1
        s.tp2               = tp2
        s.risk_per_contract = rpc
        s.contracts_proposed = contracts
        s.contracts_approved = contracts
        s.risk_dollars       = contracts * rpc
        s.breakout_direction = direction
        s.prior_qual_bar_ts  = bar.get("ts")

        self._transition(inst, OrbState.QUALIFIED, f"qualified_{direction.lower()}", {
            "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2, "contracts": contracts,
        })
        self._tl(inst, "QUALIFIED",
                 f"{direction} qualified — entry={entry:.4f}  stop={stop:.4f}  "
                 f"TP1={tp1:.4f}  TP2={tp2:.4f}  {contracts}c  risk=${contracts*rpc:.0f}", now_iso)

        # Reserve portfolio risk (accounting only — SHADOW)
        self._reserve_portfolio_risk(inst, direction, contracts * rpc)
        s.risk_reservation_state = RiskReservationState.RESERVED

        # Create shadow entry record
        shadow = {
            "instrument":        inst,
            "trading_date":      s.trading_date,
            "direction":         direction,
            "strategy_version":  s.strategy_version,
            "config_version":    s.config_version,
            "range_duration_min": s.range_duration_min,
            "or_high":           s.or_high,
            "or_low":            s.or_low,
            "or_width":          s.or_width,
            "or_midpoint":       s.or_midpoint,
            "breakout_level":    entry,
            "confirmation_mode": s.confirmation_mode,
            "entry":             entry,
            "stop":              stop,
            "tp1":               tp1,
            "tp2":               tp2,
            "contracts":         contracts,
            "risk_dollars":      contracts * rpc,
            "shadow_entry_ts":   now_iso,
            "outcome":           "OPEN",
            "mode":              "SHADOW",
        }
        s.shadow_entries.append(shadow)
        s.daily_trade_count += 1

        self._transition(inst, OrbState.POSITION_ACTIVE, "shadow_entry_created", {"mode": "SHADOW"})
        self._tl(inst, "SHADOW_ENTRY_CREATED",
                 f"Shadow {direction} recorded — SHADOW mode — NO ORDER TRANSMITTED", now_iso)
        self._persist_shadow_async(shadow)

    # ─────────────────────────────────────────────────────────────────────────
    # Portfolio risk accounting
    # ─────────────────────────────────────────────────────────────────────────

    def _check_portfolio_risk(self, inst: str) -> tuple:
        with self._risk_lock:
            pr  = self._portfolio
            cfg = self._config
            if pr.active_positions >= cfg.max_simultaneous_positions:
                return False, f"max_simultaneous_positions={cfg.max_simultaneous_positions} reached"
            if inst in _INDEX_GROUP and pr.active_index_positions >= cfg.max_simultaneous_index:
                return False, f"max_simultaneous_index={cfg.max_simultaneous_index} reached"
            if inst in _METALS_GROUP and pr.active_metals_positions >= cfg.max_simultaneous_metals:
                return False, f"max_simultaneous_metals={cfg.max_simultaneous_metals} reached"
            return True, ""

    def _reserve_portfolio_risk(self, inst: str, direction: str, risk_dollars: float) -> None:
        with self._risk_lock:
            pr = self._portfolio
            pr.active_positions += 1
            if inst in _INDEX_GROUP:
                pr.active_index_positions += 1
                pr.active_index_risk      += risk_dollars
            else:
                pr.active_metals_positions += 1
                pr.active_metals_risk      += risk_dollars

    # ─────────────────────────────────────────────────────────────────────────
    # State transition
    # ─────────────────────────────────────────────────────────────────────────

    def _transition(self, inst: str, new_state: str, reason: str, data: dict) -> None:
        s       = self._state[inst]
        prev    = s.state
        s.state = new_state
        s.block_reason = reason if "BLOCKED" in new_state else ""
        self._log.debug("OrbEngine [%s]: %s → %s (%s)", inst, prev, new_state, reason)
        self._persist_transition_async(inst, prev, new_state, reason, data)

    # ─────────────────────────────────────────────────────────────────────────
    # Day rollover
    # ─────────────────────────────────────────────────────────────────────────

    def _day_rollover(self, inst: str, new_date: str) -> None:
        s   = self._state[inst]
        cfg = self._config
        self._log.info("OrbEngine [%s]: day rollover %s → %s", inst, s.trading_date, new_date)
        mode = cfg.effective_mode_for(inst)
        self._state[inst] = OrbInstrumentState(
            instrument       = inst,
            trading_date     = new_date,
            strategy_version = cfg.strategy_version,
            config_version   = cfg.config_version,
            mode             = mode,
            range_duration_min   = cfg.range_duration_min,
            range_start_minutes  = 9 * 60 + 30,
            lock_minutes         = cfg.lock_time_minutes(),
            entry_end_minutes    = cfg.entry_end_minutes(),
            confirmation_mode    = cfg.per_instrument_confirmation.get(inst, cfg.confirmation_mode),
        )
        self._transition(inst, OrbState.WAITING_FOR_SESSION, "day_rollover", {"date": new_date})
        self._tl(inst, "DAY_ROLLOVER", f"New session: {new_date}", self._now().isoformat())

    # ─────────────────────────────────────────────────────────────────────────
    # Timeline helper
    # ─────────────────────────────────────────────────────────────────────────

    def _tl(self, inst: str, event_type: str, msg: str, ts: str) -> None:
        s = self._state.get(inst)
        event = {"ts": ts, "instrument": inst, "event_type": event_type, "message": msg}
        if s:
            s.timeline_events.append(event)
            if len(s.timeline_events) > 200:
                s.timeline_events = s.timeline_events[-100:]
        self._persist_timeline_async(inst, event_type, msg, ts)

    # ─────────────────────────────────────────────────────────────────────────
    # Instrument spec helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _tick_size(self, inst: str) -> float:
        try:
            return float(self._assets[inst]["specs"]["tick_size"])
        except (KeyError, TypeError):
            return 0.25   # safe fallback

    def _point_value(self, inst: str) -> float:
        try:
            return float(self._assets[inst]["specs"]["point_value"])
        except (KeyError, TypeError):
            return 2.0    # safe fallback (MNQ)

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def get_all_status(self) -> dict:
        with self._lock:
            instruments = {inst: self._inst_status(inst) for inst in _INSTRUMENTS}
            return {
                "enabled":          self._config.enabled,
                "global_mode":      self._config.global_mode,
                "strategy_version": ORB_STRATEGY_VERSION,
                "config_version":   ORB_CONFIG_VERSION,
                "db_ready":         OrbEngine.ORB_DB_READY,
                "instruments":      instruments,
                "portfolio":        self._portfolio_status(),
                "config":           self._config_summary(),
            }

    def get_instrument_status(self, inst: str) -> dict:
        with self._lock:
            if inst not in _INSTRUMENTS:
                return {"error": f"unknown instrument: {inst}"}
            return self._inst_status(inst)

    def get_timeline(self, inst: Optional[str] = None, limit: int = 100) -> list:
        with self._lock:
            events: list = []
            for i in (_INSTRUMENTS if inst is None else [inst]):
                s = self._state.get(i)
                if s:
                    events.extend(s.timeline_events)
            events.sort(key=lambda e: e.get("ts", ""), reverse=True)
            return events[:limit]

    def get_config(self) -> dict:
        with self._lock:
            return asdict(self._config)

    def set_config(self, patch: dict) -> dict:
        """Update ORB config. Validates range_duration_min. Thread-safe."""
        with self._lock:
            if "range_duration_min" in patch:
                v = int(patch["range_duration_min"])
                if v not in _RANGE_DURATIONS:
                    raise ValueError(f"range_duration_min must be one of {sorted(_RANGE_DURATIONS)}")
            for k, v in patch.items():
                if hasattr(self._config, k):
                    setattr(self._config, k, v)
            self._save_config_async()
            return asdict(self._config)

    def get_shadow_trades_from_db(self, instrument: Optional[str] = None, limit: int = 100) -> list:
        try:
            conn = self._get_db()
            if not conn:
                return []
            try:
                with conn.cursor() as cur:
                    if instrument:
                        cur.execute("""
                            SELECT id, trading_date, instrument, direction,
                                   entry_price, stop_price, tp1, tp2,
                                   contracts, risk_dollars, outcome, final_r,
                                   shadow_entry_at
                            FROM orb_shadow_trades WHERE instrument=%s
                            ORDER BY shadow_entry_at DESC LIMIT %s
                        """, (instrument, limit))
                    else:
                        cur.execute("""
                            SELECT id, trading_date, instrument, direction,
                                   entry_price, stop_price, tp1, tp2,
                                   contracts, risk_dollars, outcome, final_r,
                                   shadow_entry_at
                            FROM orb_shadow_trades
                            ORDER BY shadow_entry_at DESC LIMIT %s
                        """, (limit,))
                    cols = [d[0] for d in cur.description]
                    return [dict(zip(cols, r)) for r in cur.fetchall()]
            finally:
                conn.close()
        except Exception as exc:
            self._log.debug("OrbEngine: get_shadow_trades_from_db — %s", exc)
            return []

    # ─────────────────────────────────────────────────────────────────────────
    # Status serialisers
    # ─────────────────────────────────────────────────────────────────────────

    def _inst_status(self, inst: str) -> dict:
        s = self._state.get(inst)
        if not s:
            return {"instrument": inst, "state": OrbState.DISABLED}
        return {
            "instrument":           inst,
            "state":                s.state,
            "mode":                 s.mode,
            "block_reason":         s.block_reason,
            "trading_date":         s.trading_date,
            "strategy_version":     s.strategy_version,
            "config_version":       s.config_version,
            # Range
            "range_duration_min":   s.range_duration_min,
            "range_locked":         s.range_locked,
            "range_locked_ts":      s.range_locked_ts,
            "or_high":              s.or_high,
            "or_low":               s.or_low,
            "or_midpoint":          s.or_midpoint,
            "or_width":             s.or_width,
            "or_valid":             s.or_valid,
            "range_bars_observed":  s.range_bars_observed,
            "lock_time_et":         f"{s.lock_minutes//60:02d}:{s.lock_minutes%60:02d}",
            "entry_window_end_et":  f"{s.entry_end_minutes//60:02d}:{s.entry_end_minutes%60:02d}",
            # Breakout
            "long_breakout_level":  s.long_breakout_level,
            "short_breakout_level": s.short_breakout_level,
            "breakout_direction":   s.breakout_direction,
            "confirmation_mode":    s.confirmation_mode,
            "two_sided_sweep":      s.two_sided_sweep,
            # Chase
            "max_chase_boundary_long":  s.max_chase_boundary_long,
            "max_chase_boundary_short": s.max_chase_boundary_short,
            "max_chase_exceeded_long":  s.max_chase_exceeded_long,
            "max_chase_exceeded_short": s.max_chase_exceeded_short,
            # Trade plan
            "entry":            s.entry,
            "stop":             s.stop,
            "tp1":              s.tp1,
            "tp2":              s.tp2,
            "contracts":        s.contracts_approved,
            "risk_dollars":     s.risk_dollars,
            "risk_reservation": s.risk_reservation_state,
            # Position
            "daily_trade_count": s.daily_trade_count,
            "shadow_entries":   s.shadow_entries,
            # Context
            "current_price":    s.current_price,
            "current_atr":      s.current_atr,
            "current_vwap":     s.current_vwap,
            "last_bar_ts":      s.last_bar_ts,
            "last_bar_close":   s.last_bar_close,
            "last_update":      s.last_update,
            "timeline_events":  s.timeline_events[-30:],
        }

    def _portfolio_status(self) -> dict:
        with self._risk_lock:
            pr  = self._portfolio
            cfg = self._config
            total_active = pr.active_index_risk + pr.active_metals_risk
            return {
                "active_positions":        pr.active_positions,
                "max_positions":           cfg.max_simultaneous_positions,
                "active_index_positions":  pr.active_index_positions,
                "max_index_positions":     cfg.max_simultaneous_index,
                "active_metals_positions": pr.active_metals_positions,
                "max_metals_positions":    cfg.max_simultaneous_metals,
                "active_index_risk":       round(pr.active_index_risk,  2),
                "active_metals_risk":      round(pr.active_metals_risk, 2),
                "active_total_risk":       round(total_active, 2),
                "max_index_risk":          cfg.max_index_group_risk,
                "max_metals_risk":         cfg.max_metals_group_risk,
                "max_total_risk":          cfg.max_orb_total_risk,
                "remaining_index_risk":    round(max(0, cfg.max_index_group_risk  - pr.active_index_risk), 2),
                "remaining_metals_risk":   round(max(0, cfg.max_metals_group_risk - pr.active_metals_risk), 2),
                "correlation_mode":        cfg.correlation_mode,
            }

    def _config_summary(self) -> dict:
        cfg = self._config
        return {
            "range_duration_min":      cfg.range_duration_min,
            "lock_time_et":            f"{cfg.lock_time_minutes()//60:02d}:{cfg.lock_time_minutes()%60:02d}",
            "entry_window_end_et":     cfg.entry_window_end_et,
            "confirmation_mode":       cfg.confirmation_mode,
            "max_chase_pct":           cfg.max_chase_pct,
            "breakout_buffer_ticks":   cfg.breakout_buffer_ticks,
            "stop_buffer_ticks":       cfg.stop_buffer_ticks,
            "tp1_r":                   cfg.tp1_r,
            "tp2_r":                   cfg.tp2_r,
            "single_contract_r":       cfg.single_contract_r,
            "max_trades_per_instrument": cfg.max_trades_per_instrument,
            "max_risk_per_instrument": cfg.max_risk_per_instrument,
            "correlation_mode":        cfg.correlation_mode,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # DB persistence
    # ─────────────────────────────────────────────────────────────────────────

    def _ensure_tables(self, conn) -> None:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orb_config (
                    id SERIAL PRIMARY KEY,
                    config_data JSONB NOT NULL DEFAULT '{}',
                    strategy_version TEXT NOT NULL DEFAULT '1.0.0',
                    config_version   TEXT NOT NULL DEFAULT '1.0.0',
                    created_at  TIMESTAMPTZ DEFAULT NOW(),
                    updated_at  TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orb_daily_state (
                    id           SERIAL PRIMARY KEY,
                    trading_date TEXT NOT NULL,
                    instrument   TEXT NOT NULL,
                    strategy_version TEXT NOT NULL DEFAULT '1.0.0',
                    config_version   TEXT NOT NULL DEFAULT '1.0.0',
                    state_data   JSONB NOT NULL DEFAULT '{}',
                    created_at   TIMESTAMPTZ DEFAULT NOW(),
                    updated_at   TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(trading_date, instrument)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orb_state_transitions (
                    id           SERIAL PRIMARY KEY,
                    trading_date TEXT NOT NULL,
                    instrument   TEXT NOT NULL,
                    from_state   TEXT,
                    to_state     TEXT NOT NULL,
                    reason       TEXT,
                    transition_data JSONB DEFAULT '{}',
                    ts           TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orb_shadow_trades (
                    id               SERIAL PRIMARY KEY,
                    trading_date     TEXT NOT NULL,
                    instrument       TEXT NOT NULL,
                    direction        TEXT NOT NULL,
                    strategy_version TEXT NOT NULL DEFAULT '1.0.0',
                    config_version   TEXT NOT NULL DEFAULT '1.0.0',
                    range_duration_min INT,
                    or_high          NUMERIC,
                    or_low           NUMERIC,
                    or_width         NUMERIC,
                    breakout_level   NUMERIC,
                    confirmation_mode TEXT,
                    entry_price      NUMERIC,
                    stop_price       NUMERIC,
                    tp1              NUMERIC,
                    tp2              NUMERIC,
                    contracts        INT,
                    risk_dollars     NUMERIC,
                    outcome          TEXT DEFAULT 'OPEN',
                    exit_price       NUMERIC,
                    exit_reason      TEXT,
                    mfe              NUMERIC,
                    mae              NUMERIC,
                    final_r          NUMERIC,
                    context          JSONB DEFAULT '{}',
                    shadow_entry_at  TIMESTAMPTZ DEFAULT NOW(),
                    created_at       TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orb_timeline_events (
                    id           SERIAL PRIMARY KEY,
                    trading_date TEXT NOT NULL,
                    instrument   TEXT,
                    event_type   TEXT NOT NULL,
                    message      TEXT,
                    ts           TIMESTAMPTZ DEFAULT NOW()
                )
            """)
        conn.commit()

    def _restore_config(self, conn) -> None:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT config_data FROM orb_config ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
            if row:
                data = row[0]
                if isinstance(data, str):
                    data = json.loads(data)
                for k, v in data.items():
                    if hasattr(self._config, k):
                        try:
                            setattr(self._config, k, v)
                        except Exception:
                            pass
        except Exception as exc:
            self._log.debug("OrbEngine: config restore skipped — %s", exc)

    def _restore_today(self, conn) -> None:
        today = self._now().astimezone(_ET_TZ).date().isoformat()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT instrument, state_data
                    FROM orb_daily_state
                    WHERE trading_date = %s
                """, (today,))
                rows = cur.fetchall()
            for row in rows:
                inst, data = row
                if inst not in _INSTRUMENTS:
                    continue
                if isinstance(data, str):
                    data = json.loads(data)
                if not data.get("range_locked"):
                    continue   # only restore if range was locked (immutable)
                s = self._state[inst]
                cfg = self._config
                # Restore the locked range — immutable
                s.or_high             = data.get("or_high")
                s.or_low              = data.get("or_low")
                s.or_midpoint         = data.get("or_midpoint")
                s.or_width            = data.get("or_width")
                s.or_valid            = True
                s.range_locked        = True
                s.range_locked_ts     = data.get("range_locked_ts")
                s.long_breakout_level  = data.get("long_breakout_level")
                s.short_breakout_level = data.get("short_breakout_level")
                s.range_bars_observed  = data.get("range_bars_observed", 0)
                s.daily_trade_count    = data.get("daily_trade_count",   0)
                s.trading_date         = today
                s.mode                 = cfg.effective_mode_for(inst)
                s.range_duration_min   = cfg.range_duration_min
                s.range_start_minutes  = 9 * 60 + 30
                s.lock_minutes         = cfg.lock_time_minutes()
                s.entry_end_minutes    = cfg.entry_end_minutes()
                s.confirmation_mode    = cfg.per_instrument_confirmation.get(inst, cfg.confirmation_mode)
                # Safe recovery point after restart
                s.state = OrbState.WATCHING_BREAKOUT
                self._log.info("OrbEngine [%s]: restored locked range for %s — "
                               "H=%s L=%s; state=WATCHING_BREAKOUT",
                               inst, today, s.or_high, s.or_low)
        except Exception as exc:
            self._log.debug("OrbEngine: today state restore error — %s", exc)

    def _persist_state_async(self, inst: str) -> None:
        if not OrbEngine.ORB_DB_READY:
            return
        s = self._state.get(inst)
        if not s:
            return
        data = {
            "state":               s.state,
            "range_locked":        s.range_locked,
            "range_locked_ts":     s.range_locked_ts,
            "or_high":             s.or_high,
            "or_low":              s.or_low,
            "or_midpoint":         s.or_midpoint,
            "or_width":            s.or_width,
            "long_breakout_level":  s.long_breakout_level,
            "short_breakout_level": s.short_breakout_level,
            "range_bars_observed": s.range_bars_observed,
            "daily_trade_count":   s.daily_trade_count,
            "entry":               s.entry,
            "stop":                s.stop,
            "tp1":                 s.tp1,
            "tp2":                 s.tp2,
            "contracts_approved":  s.contracts_approved,
            "breakout_direction":  s.breakout_direction,
        }
        date = s.trading_date or self._now().astimezone(_ET_TZ).date().isoformat()
        threading.Thread(target=self._persist_state_bg, args=(inst, date, data),
                         daemon=True).start()

    def _persist_state_bg(self, inst: str, date: str, data: dict) -> None:
        try:
            conn = self._get_db()
            if not conn:
                return
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO orb_daily_state
                            (trading_date, instrument, strategy_version, config_version, state_data, updated_at)
                        VALUES (%s, %s, %s, %s, %s::jsonb, NOW())
                        ON CONFLICT (trading_date, instrument)
                        DO UPDATE SET state_data = EXCLUDED.state_data, updated_at = NOW()
                    """, (date, inst, ORB_STRATEGY_VERSION, ORB_CONFIG_VERSION, json.dumps(data)))
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            self._log.debug("OrbEngine: persist_state error [%s]: %s", inst, exc)

    def _persist_transition_async(self, inst: str, from_s: str, to_s: str,
                                  reason: str, data: dict) -> None:
        if not OrbEngine.ORB_DB_READY:
            return
        s = self._state.get(inst)
        date = (s.trading_date if s else None) or self._now().astimezone(_ET_TZ).date().isoformat()
        threading.Thread(target=self._persist_transition_bg,
                         args=(date, inst, from_s, to_s, reason, data),
                         daemon=True).start()

    def _persist_transition_bg(self, date, inst, from_s, to_s, reason, data):
        try:
            conn = self._get_db()
            if not conn:
                return
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO orb_state_transitions
                            (trading_date, instrument, from_state, to_state, reason, transition_data)
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    """, (date, inst, from_s, to_s, reason, json.dumps(data, default=str)))
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            self._log.debug("OrbEngine: persist_transition error: %s", exc)

    def _persist_shadow_async(self, shadow: dict) -> None:
        if not OrbEngine.ORB_DB_READY:
            return
        threading.Thread(target=self._persist_shadow_bg, args=(shadow,), daemon=True).start()

    def _persist_shadow_bg(self, shadow: dict) -> None:
        try:
            conn = self._get_db()
            if not conn:
                return
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO orb_shadow_trades (
                            trading_date, instrument, direction,
                            strategy_version, config_version,
                            range_duration_min, or_high, or_low, or_width,
                            breakout_level, confirmation_mode,
                            entry_price, stop_price, tp1, tp2,
                            contracts, risk_dollars
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (
                        shadow.get("trading_date"),  shadow.get("instrument"),
                        shadow.get("direction"),
                        shadow.get("strategy_version", ORB_STRATEGY_VERSION),
                        shadow.get("config_version",   ORB_CONFIG_VERSION),
                        shadow.get("range_duration_min"), shadow.get("or_high"),
                        shadow.get("or_low"),  shadow.get("or_width"),
                        shadow.get("breakout_level"), shadow.get("confirmation_mode"),
                        shadow.get("entry"), shadow.get("stop"),
                        shadow.get("tp1"),   shadow.get("tp2"),
                        shadow.get("contracts"), shadow.get("risk_dollars"),
                    ))
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            self._log.debug("OrbEngine: persist_shadow error: %s", exc)

    def _persist_timeline_async(self, inst: str, event_type: str,
                                msg: str, ts: str) -> None:
        if not OrbEngine.ORB_DB_READY:
            return
        s = self._state.get(inst)
        date = (s.trading_date if s else None) or self._now().astimezone(_ET_TZ).date().isoformat()
        threading.Thread(target=self._persist_timeline_bg,
                         args=(date, inst, event_type, msg), daemon=True).start()

    def _persist_timeline_bg(self, date, inst, event_type, msg):
        try:
            conn = self._get_db()
            if not conn:
                return
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO orb_timeline_events
                            (trading_date, instrument, event_type, message)
                        VALUES (%s, %s, %s, %s)
                    """, (date, inst, event_type, msg))
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            self._log.debug("OrbEngine: persist_timeline error: %s", exc)

    def _save_config_async(self) -> None:
        if not OrbEngine.ORB_DB_READY:
            return
        data = asdict(self._config)
        threading.Thread(target=self._save_config_bg, args=(data,), daemon=True).start()

    def _save_config_bg(self, data: dict) -> None:
        try:
            conn = self._get_db()
            if not conn:
                return
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO orb_config (config_data, strategy_version, config_version)
                        VALUES (%s::jsonb, %s, %s)
                    """, (json.dumps(data, default=str), ORB_STRATEGY_VERSION, ORB_CONFIG_VERSION))
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            self._log.debug("OrbEngine: save_config error: %s", exc)
