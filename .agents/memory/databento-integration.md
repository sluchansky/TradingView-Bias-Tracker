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
