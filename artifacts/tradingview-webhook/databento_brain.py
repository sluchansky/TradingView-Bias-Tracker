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
    DATABENTO_STATUS        — health / telemetry dict for /databento-status
"""
from __future__ import annotations

import logging
import os
import time
import threading
from collections import deque
from datetime import datetime, timezone, date as _date
from typing import Any

logger = logging.getLogger(__name__)

# ── Instrument ↔ Databento continuous-contract symbol ────────────────────────
DB_SYMBOLS: dict[str, str] = {
    "MGC": "MGC.c.0",
    "MNQ": "MNQ.c.0",
    "MES": "MES.c.0",
    "MYM": "MYM.c.0",
}
DB_DATASET = "GLBX.MDP3"

# ── Public stores (read by Flask routes and the dashboard chart) ──────────────
# Each bar entry: {ts, open, high, low, close, volume, vwap?, atr?}
DATABENTO_BARS_BY_INST: dict[str, deque] = {
    inst: deque(maxlen=200) for inst in DB_SYMBOLS
}

DATABENTO_STATUS: dict[str, Any] = {
    "connected":   False,
    "reconnects":  0,
    "last_ts":     None,
    "error":       None,
    "instruments": {},
}


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
    CONFIRM_VOL_MULT   = 1.2  # volume must be >= 1.2x rolling avg to qualify
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

        # Per-instrument working state (all keyed by bot instrument: MGC/MNQ/…)
        self._bars:        dict[str, list]        = {i: [] for i in DB_SYMBOLS}
        self._partial:     dict[str, Any]         = {i: None for i in DB_SYMBOLS}
        self._pv_sum:      dict[str, float]       = {i: 0.0 for i in DB_SYMBOLS}
        self._v_sum:       dict[str, float]       = {i: 0.0 for i in DB_SYMBOLS}
        self._cvd_acc:     dict[str, float]       = {i: 0.0 for i in DB_SYMBOLS}
        self._last_bos:     dict[str, Any]         = {i: None for i in DB_SYMBOLS}
        self._last_sweep:   dict[str, Any]         = {i: None for i in DB_SYMBOLS}
        self._last_confirm: dict[str, Any]         = {i: None for i in DB_SYMBOLS}
        self._trend:        dict[str, str | None]  = {i: None for i in DB_SYMBOLS}
        self._session_day: dict[str, Any]         = {i: None for i in DB_SYMBOLS}

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Launch the live feed in a background daemon thread (call once at boot)."""
        t = threading.Thread(
            target=self._reconnect_loop,
            daemon=True,
            name="databento-brain",
        )
        t.start()
        logger.info("DatabentoBrain: started — watching instruments: %s",
                    list(DB_SYMBOLS.keys()))

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
        # (exchange IDs change on contract rollover).
        self._id_to_inst = {}

        logger.info("DatabentoBrain: connecting to %s …", DB_DATASET)
        DATABENTO_STATUS["error"] = None

        client = db.Live(key=api_key)
        client.subscribe(
            dataset=DB_DATASET,
            schema="trades",
            symbols=list(DB_SYMBOLS.values()),
            stype_in="continuous",
        )
        DATABENTO_STATUS["connected"] = True
        logger.info(
            "DatabentoBrain: connected ✓  streaming %s", list(DB_SYMBOLS.values())
        )

        # ── Background thread: poll client.symbology_map until populated ─────────
        # The SDK processes SymbolMappingMsg internally (via its own _map_symbol
        # callback) and populates client.symbology_map within ~1s of start().
        # add_callback is unreliable for this — we poll from a side thread.
        DATABENTO_STATUS["_symmap_debug"] = {"polls": 0, "smap": {}, "err": None}

        def _build_id_map() -> None:
            for i in range(100):          # up to 10s; typically < 1s
                try:
                    smap: dict = client.symbology_map
                    DATABENTO_STATUS["_symmap_debug"]["polls"] = i + 1
                    DATABENTO_STATUS["_symmap_debug"]["smap"] = dict(smap)
                    if smap:
                        for iid, native_sym in smap.items():
                            native_str = str(native_sym)
                            for root in DB_SYMBOLS:
                                if native_str.startswith(root):
                                    self._id_to_inst[iid] = root
                                    logger.info(
                                        "DatabentoBrain: id→inst %s → %s"
                                        " (native=%s)", iid, root, native_str,
                                    )
                                    break
                        logger.info(
                            "DatabentoBrain: symbology map ready — %s",
                            self._id_to_inst,
                        )
                        return
                except Exception as _e:
                    DATABENTO_STATUS["_symmap_debug"]["err"] = str(_e)
                    logger.warning("DatabentoBrain: symbology poll error: %s", _e)
                time.sleep(0.1)
            logger.warning(
                "DatabentoBrain: symbology_map empty after 10s — "
                "will fall back to symbol-prefix matching"
            )

        threading.Thread(
            target=_build_id_map, daemon=True, name="db-symmap"
        ).start()

        # Iterator yields only TradeMsg; id→inst map is built concurrently above
        # and will be ready long before the first trade is processed.
        for record in client:
            self._on_trade(record)

        DATABENTO_STATUS["connected"] = False
        logger.warning("DatabentoBrain: feed closed by server — reconnecting …")

    # ── Trade record handler ──────────────────────────────────────────────────

    def _on_trade(self, rec) -> None:
        try:
            # ── Instrument resolution ─────────────────────────────────────────
            # Primary: instrument_id → inst via the map built from SymbolMappingMsg
            iid  = getattr(rec, "instrument_id", None)
            inst = self._id_to_inst.get(iid) if iid is not None else None

            # Fallback 1: continuous-contract symbol string ("MGC.c.0" → "MGC")
            if inst is None:
                sym  = getattr(rec, "symbol", None) or ""
                inst = self._sym_to_inst.get(sym)

            # Fallback 2: prefix-match native symbol (e.g. "MGCQ6") → root
            if inst is None:
                sym = getattr(rec, "symbol", None) or ""
                for root in DB_SYMBOLS:
                    if sym.startswith(root):
                        inst = root
                        break

            if inst is None:
                return

            # Databento uses nanosecond epoch integers for timestamps and
            # fixed-point price integers (divide by 1e9 to get float USD/index).
            ts_s  = rec.ts_event / 1_000_000_000
            price = rec.price   / 1_000_000_000
            size  = int(rec.size)
            side  = getattr(rec, "side", None)  # 'A'=buy aggressor, 'B'=sell, 'N'=?

            DATABENTO_STATUS["last_ts"] = datetime.now(timezone.utc).isoformat()

            # ── Live price (sub-second resolution) ──
            self._cp[inst]    = price
            self._cp_ts[inst] = datetime.now(timezone.utc).isoformat()

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
            self._tick_bar(inst, bar_minute, price, size)

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

    def _tick_bar(self, inst: str, bar_minute: int, price: float, size: int) -> None:
        p = self._partial[inst]
        if p is None or p["ts"] != bar_minute:
            if p is not None:
                self._on_bar_close(inst, p)
            self._partial[inst] = {
                "ts":     bar_minute,
                "open":   price,
                "high":   price,
                "low":    price,
                "close":  price,
                "volume": size,
            }
        else:
            if price > p["high"]: p["high"] = price
            if price < p["low"]:  p["low"]  = price
            p["close"]   = price
            p["volume"] += size

    # ── Bar close — compute indicators and inject into shared state ───────────

    def _on_bar_close(self, inst: str, bar: dict) -> None:
        bars = self._bars[inst]
        bars.append(bar)
        if len(bars) > self.MAX_BARS:
            del bars[0]

        now_iso = datetime.now(timezone.utc).isoformat()

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
                "ts":          now_iso,
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
            "ts":        now_iso,
            "source":    "databento",
        }

        # ── RVOL_BY_TICKER + VOLUME_SPIKE_BY_TICKER ───────────────────────────
        if rvol is not None:
            self._rvol[inst] = {
                "value":  rvol,
                "ts":     now_iso,
                "source": "databento",
            }
            if rvol >= self.VOL_SPIKE_MULT:
                self._vs[inst] = {"ts": now_iso, "source": "databento"}

        # ── Public bar store (dashboard live chart) ───────────────────────────
        pub: dict[str, Any] = {
            "ts":     bar["ts"],
            "open":   bar["open"],
            "high":   bar["high"],
            "low":    bar["low"],
            "close":  bar["close"],
            "volume": bar["volume"],
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
                "ts":          now_iso,
            }

        # ── VWAP_BY_TICKER (gate VWAP — replaces Yahoo Finance auto-refresh) ──
        # get_vwap() reads VWAP_BY_TICKER for all gate decisions. Databento is
        # now the authoritative source when no recent chart/alert push is present.
        # Grace window: a TradingView "chart" or "alert" push (source != "databento")
        # wins for VWAP_OVERRIDE_GRACE_MIN minutes; after that Databento resumes.
        if vwap is not None and self._vwap is not None:
            _write_vwap = True
            existing = self._vwap.get(inst)
            if existing and existing.get("source") in ("chart", "alert"):
                try:
                    from datetime import datetime as _dt, timezone as _tz
                    age_min = (
                        _dt.now(_tz.utc) -
                        _dt.fromisoformat(existing["ts"])
                    ).total_seconds() / 60.0
                    if age_min < self._vwap_grace:
                        _write_vwap = False  # still within grace window
                except Exception:
                    pass  # on parse error, let Databento write
            if _write_vwap:
                self._vwap[inst] = {
                    "value":  round(vwap, 4),
                    "ts":     now_iso,
                    "source": "databento",
                }

        # ── Telemetry ─────────────────────────────────────────────────────────
        DATABENTO_STATUS["instruments"][inst] = {
            "bars":  len(bars),
            "vwap":  round(vwap, 4) if vwap  is not None else None,
            "atr":   round(atr,  4) if atr   is not None else None,
            "cvd":   round(cvd_val, 1),
            "rvol":  round(rvol, 2)  if rvol  is not None else None,
            "price": bar["close"],
        }

        # ── Structure detection (BOS / CHOCH → ALERT_HISTORY) ────────────────
        self._detect_structure(inst, bars)
        # ── Sweep detection (BULLISH/BEARISH SWEEP → ALERT_HISTORY) ──────────
        self._detect_sweep(inst, bars)
        # ── Confirmation candle (BULLISH/BEARISH CONFIRMATION → ALERT_HISTORY) ─
        self._detect_confirmation(inst, bars)

    # ── Structure detection ───────────────────────────────────────────────────

    def _detect_structure(self, inst: str, bars: list) -> None:
        """
        Swing-pivot BOS / CHOCH detector.

        Looks at the bar at index [-SWING_N-1]: it has SWING_N completed bars
        after it, so we can confirm whether it was a swing high or low.

        BOS DEMAND  — close breaks above confirmed swing high (bullish continuation)
        BOS SUPPLY  — close breaks below confirmed swing low  (bearish continuation)
        CHOCH DEMAND — first bullish BOS after a bearish trend (bullish reversal)
        CHOCH SUPPLY — first bearish BOS after a bullish trend (bearish reversal)

        Each structure level is injected once only (deduped via _last_bos).
        """
        n = self.SWING_N
        if len(bars) < n * 2 + 2:
            return

        pi     = len(bars) - n - 1
        pivot  = bars[pi]
        window = range(max(0, pi - n), min(len(bars), pi + n + 1))

        is_sh = all(pivot["high"] >= bars[j]["high"] for j in window if j != pi)
        is_sl = all(pivot["low"]  <= bars[j]["low"]  for j in window if j != pi)

        close    = bars[-1]["close"]
        last_bos = self._last_bos[inst] or {}

        if is_sh and close > pivot["high"]:
            if not (last_bos.get("type") in ("BOS DEMAND", "CHOCH DEMAND")
                    and abs(last_bos.get("level", 0) - pivot["high"]) < 0.01):
                atype = ("CHOCH DEMAND"
                         if self._trend[inst] == "bear" else "BOS DEMAND")
                self._inject_alert(inst, atype, close)
                self._last_bos[inst] = {"type": atype, "level": pivot["high"]}
                self._trend[inst]    = "bull"

        if is_sl and close < pivot["low"]:
            if not (last_bos.get("type") in ("BOS SUPPLY", "CHOCH SUPPLY")
                    and abs(last_bos.get("level", 0) - pivot["low"]) < 0.01):
                atype = ("CHOCH SUPPLY"
                         if self._trend[inst] == "bull" else "BOS SUPPLY")
                self._inject_alert(inst, atype, close)
                self._last_bos[inst] = {"type": atype, "level": pivot["low"]}
                self._trend[inst]    = "bear"

    def _inject_alert(self, inst: str, alert_type: str, price: float) -> None:
        """Append a synthetic structure alert to the shared ALERT_HISTORY deque."""
        record = {
            "alert_type":        alert_type,
            "ticker":            inst + "1!",
            "instrument":        inst,
            "instrument_source": "databento",
            "price":             float(price),
            "timestamp":         datetime.now(timezone.utc).isoformat(),
            "raw":               {"source": "databento_brain"},
        }
        self._ah.append(record)
        logger.info("DatabentoBrain ▶ %s  %s @ %.4f", inst, alert_type, price)

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

        # ── Bearish sweep: wick above prior high, close below it ──────────────
        elif cur["high"] > prior_high and cur["close"] < prior_high:
            if not (last.get("side") == "bear"
                    and abs(last.get("level", 0) - prior_high) <= prior_high * 0.001):
                atype = f"{inst} BEARISH SWEEP"
                self._inject_alert(inst, atype, cur["close"])
                self._last_sweep[inst] = {"side": "bear", "level": prior_high}

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
          3. Above-average volume: current bar volume >= 1.2x the 10-bar
             rolling average, confirming real participation.

        Only fires ONCE per structure episode (deduped by structure level via
        _last_confirm). When a new BOS/CHOCH fires at a different level, the
        dedup clears automatically.

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

        struct_level = last_bos["level"]
        last_conf    = self._last_confirm[inst] or {}

        if trend == "bull":
            strong_close = close_ratio >= self.CONFIRM_BODY_RATIO
            if not (strong_close or bull_engulf):
                return
            if (last_conf.get("side") == "bull"
                    and abs(last_conf.get("level", 0) - struct_level)
                    <= struct_level * 0.001):
                return  # already confirmed this structure episode
            self._inject_alert(inst, f"{inst} BULLISH CONFIRMATION", cur["close"])
            self._last_confirm[inst] = {"side": "bull", "level": struct_level}

        else:  # trend == "bear"
            strong_close = close_ratio <= (1.0 - self.CONFIRM_BODY_RATIO)
            if not (strong_close or bear_engulf):
                return
            if (last_conf.get("side") == "bear"
                    and abs(last_conf.get("level", 0) - struct_level)
                    <= struct_level * 0.001):
                return  # already confirmed this structure episode
            self._inject_alert(inst, f"{inst} BEARISH CONFIRMATION", cur["close"])
            self._last_confirm[inst] = {"side": "bear", "level": struct_level}

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
