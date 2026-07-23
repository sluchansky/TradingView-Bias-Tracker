---
name: Databento live feed integration
description: Architecture, activation pattern, and instrument-resolution gotchas for the Databento real-time market data feed (flag-gated, default OFF).
---

## Rule
The Databento feed is a flag-gated (default OFF) real-time layer that runs **alongside** TradingView webhook alerts — it never replaces them.  TV alerts continue as supplemental signals.

**Why:** User wants real-time data independent of TradingView chart alerts.

**How to apply:**
- Activate: set `DATABENTO_ENABLED=1` + `DATABENTO_API_KEY` secret, then republish.
- The feed injects into the same shared state stores (AUTO_PRICE_BY_TICKER, CVD_BY_TICKER, RVOL_BY_TICKER, CURRENT_PRICE_BY_TICKER, etc.) that `full_analysis` + the heartbeat loop already read — no new consumers needed.
- Routes return `{ok:false, enabled:false}` (HTTP 200) not 404 when flag is OFF.  The dashboard panel shows "○ OFFLINE" rather than erroring.

## Key surfaces (all flag-gated, byte-identical when OFF)
- `databento_brain.py` — `DatabentoBrain` class; streams `GLBX.MDP3` trades schema; symbols: `MGC.c.0`, `MNQ.c.0`, `MES.c.0`, `MYM.c.0`; price = `record.price / 1e9`
- `app.py` — flag (`DATABENTO_ENABLED`), `_DATABENTO_BRAIN` global, two Flask routes (`/databento-bars`, `/databento-status`), startup hook in `__main__`
- `flask-proxy.ts` — both routes added to `BOT1_ROUTES` Express whitelist
- `Home.tsx` — `dbBars` / `dbStatus` state, 5s `useEffect` poll (independent of 3s main poll), collapsible `#db-chart` panel with `CandleChart` + OFFLINE placeholder

## Instrument resolution (CRITICAL — 3 traps)
Trade records from the Databento Live iterator have THREE resolution traps:

1. **`record.symbol` is ALWAYS empty** on TradeMsg records — only `record.instrument_id` (an integer) is reliable.
2. **`add_callback` does NOT reliably fire for SymbolMappingMsg** in SDK v0.82.0 — even when registered before `start()`. Root cause unclear; may be related to asyncio threading or SDK internals.
3. **The iterator BLOCKS until the first TradeMsg** — so you cannot build the id→inst map inside the `for record in client:` loop when the market is quiet (no trades = no records = forever blocking).

**Working solution:** Launch a background thread that polls `client.symbology_map` (a `dict[int, str]` of `instrument_id → native_symbol` like `{42002887: "MGCQ6"}`). The SDK populates this via its internal `_map_symbol` callback almost immediately after `start()`. Poll every 0.1s; it resolves in < 1s. Then use `self._id_to_inst[instrument_id]` in `_on_trade` for O(1) lookup. Reset `self._id_to_inst = {}` at the top of each `_run_feed` (IDs change on contract rollover).

```python
# Pattern in _run_feed (after subscribe, before for loop):
def _build_id_map() -> None:
    for _ in range(100):   # up to 10s
        smap = client.symbology_map   # {int: "MGCQ6", ...}
        if smap:
            for iid, native_sym in smap.items():
                for root in DB_SYMBOLS:
                    if str(native_sym).startswith(root):
                        self._id_to_inst[iid] = root
                        break
            return
        time.sleep(0.1)

threading.Thread(target=_build_id_map, daemon=True).start()
for record in client:
    self._on_trade(record)
```

## Candle mapping
Databento bar `{ts (unix-s), open, high, low, close, volume}` → `Candle {t: ts*1000, o, h, l, c, vol: Math.min(1, volume/5000)}`.

## Phase 1 activation (DONE — now live in prod)
- `DATABENTO_ENABLED=1` set in shared env; `DATABENTO_API_KEY` secret set; `databento==0.82.0` installed via `uv add`.
- Startup confirmation: `DatabentoBrain: connected ✓  streaming ['MGC.c.0', 'MNQ.c.0', 'MES.c.0', 'MYM.c.0']` + `subscription_ack`.
- prod-start.sh uses `.pythonlibs/bin/python3` — databento must be installed via `uv add` (NOT just in requirements.txt) or the prod container won't have it.

## Phase 2A — Sweep detector (DONE)
- `_detect_sweep(inst, bars)` added to `DatabentoBrain`, called from `_on_bar_close` after `_detect_structure`.
- Fires `"{inst} BULLISH SWEEP"` when current bar's low < prior SWEEP_N-bar low AND close > that low (lows swept, reclaimed).
- Fires `"{inst} BEARISH SWEEP"` when current bar's high > prior SWEEP_N-bar high AND close < that high (highs swept, rejected).
- Alert type is **instrument-prefixed** (`"MGC BULLISH SWEEP"`) to match the `ticker_scoped=True` lookup in `_latest_ts` / `_has`.
- Deduped via `_last_sweep[inst]` — same direction + same level (within 0.1 %) fires only once per episode.
- `SWEEP_N = 10` (10 prior bars = 10-minute lookback window).
- Parity OK, goldens byte-identical.

## What Databento already feeds (no ALERT_HISTORY injection needed)
- **CVD15 Edge Score component**: reads `CVD_BY_TICKER["state"]` directly — Databento updates this every bar close. ✅
- **Volume15 Edge Score component**: reads `RVOL_BY_TICKER` + `VOLUME_SPIKE_BY_TICKER` directly — Databento updates both. ✅
- No CVD_BULLISH/CVD_BEARISH alerts needed in ALERT_HISTORY for gate scoring.

## Phase 2B — Confirmation candle detector (DONE)
- `_detect_confirmation(inst, bars)` added to `DatabentoBrain`; called from `_on_bar_close` AFTER `_detect_sweep`.
- Fires `"{inst} BULLISH CONFIRMATION"` / `"{inst} BEARISH CONFIRMATION"` (ticker_scoped=True).
- Three simultaneous requirements: (1) `_trend[inst]` set by prior BOS/CHOCH; (2) strong close (top/bottom 65% of bar range) OR engulfing vs prior bar; (3) volume >= 1.2× 10-bar rolling avg.
- Deduped via `_last_confirm[inst]` keyed on `_last_bos[inst]["level"]` — fires once per structure episode; resets automatically when a new BOS/CHOCH fires at a different price level.
- `_after_anchor` in gate satisfied naturally: `_detect_structure` runs before `_detect_confirmation`, so inject timestamp of confirmation >= inject timestamp of structure alert.
- Constants: `CONFIRM_N=10`, `CONFIRM_BODY_RATIO=0.65`, `CONFIRM_VOL_MULT=1.2`.
- Parity OK, goldens byte-identical.
- Unlocks the confirmation-candle path inside `reaction_long`/`reaction_short` and `zone_valid_long`/`zone_valid_short` from Databento independently of TradingView.

## Phase 2C — HH/HL/LH/LL swing-structure labels (DONE)
- Integrated into `_detect_structure` (reuses existing pivot detection — DRY).
- Fires `"HH"` / `"HL"` / `"LH"` / `"LL"` (bare, un-prefixed — same format as TradingView Pine scripts).
- Alert record has `instrument` + `ticker` set by `_inject_alert` → `_latest_ts("HH")` filters by `instrument` field → correctly scoped per-instrument in the gate.
- HH/LH: compare new confirmed swing-high to `_prev_sh[inst]`; emit on first non-None prev. HL/LL: same logic via `_prev_sl[inst]`.
- Fires when pivot IS confirmed (is_sh/is_sl), BEFORE the BOS/CHOCH break-check — different semantic: label the pivot's position in the sequence, not that price has broken it.
- Dedup: skip if new pivot within 0.1% of the last one emitted (double-top / noise suppression).
- State: `_prev_sh`, `_prev_sl` per instrument added to `__init__`.
- Effect: `structure_long = has_bos_demand or has_choch_demand or hh_ts or hl_ts` now fully Databento-capable; same for `structure_short` via LH/LL.
- Parity OK, goldens OK, breakout/dual-sim smokes OK.

## Remaining Phase 2 items
- Phase 2D: FVG detector — 3-candle imbalance gap. Complex, keep TV for now.

## Bars only close at minute boundaries
`instruments: {}` and `bars: []` is NORMAL until the first minute boundary after the first trade.  `last_ts` in `/databento-status` being non-null confirms trades are flowing.  `instruments` populates only when `_on_bar_close` fires (when a NEW minute arrives).

## Startup log signature (flag ON, working)
```
DatabentoBrain: connected ✓  streaming ['MGC.c.0', ...]
added symbology mapping MGCQ6 to 42002887   ← SDK internal
DatabentoBrain: id→inst 42002887 → MGC (native=MGCQ6)
DatabentoBrain: symbology map ready — {42002887: 'MGC', ...}
```

## Startup log signature (flag OFF)
`INFO:__main__:DatabentoBrain: DATABENTO_ENABLED=0 — feed OFF (byte-identical mode)`
