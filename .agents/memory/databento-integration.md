---
name: Databento live feed integration
description: Architecture and activation pattern for the Databento real-time market data feed (Phase 1, flag-gated OFF by default).
---

## Rule
The Databento feed is a flag-gated (default OFF) real-time layer that runs **alongside** TradingView webhook alerts — it never replaces them.  TV alerts continue as supplemental signals.

**Why:** User wants real-time data independent of TradingView chart alerts, but the API key isn't available yet.  Build first, key later.

**How to apply:** 
- Activate: set `DATABENTO_ENABLED=1` + `DATABENTO_API_KEY` secret, then republish.
- The feed injects into the same shared state stores (AUTO_PRICE_BY_TICKER, CVD_BY_TICKER, RVOL_BY_TICKER, CURRENT_PRICE_BY_TICKER, etc.) that `full_analysis` + the heartbeat loop already read — no new consumers needed.
- Routes return `{ok:false, enabled:false}` (HTTP 200) not 404 when flag is OFF.  The dashboard panel uses this to show "○ OFFLINE" rather than erroring.

## Key surfaces (all flag-gated, byte-identical when OFF)
- `databento_brain.py` — `DatabentoBrain` class; streams `GLBX.MDP3` trades schema; symbols: `MGC.c.0`, `MNQ.c.0`, `MES.c.0`, `MYM.c.0`; price = `record.price / 1e9`
- `app.py` — flag (`DATABENTO_ENABLED`), `_DATABENTO_BRAIN` global, two Flask routes (`/databento-bars`, `/databento-status`), startup hook in `__main__`
- `flask-proxy.ts` — both routes added to `BOT1_ROUTES` Express whitelist
- `Home.tsx` — `dbBars` / `dbStatus` state, 5s `useEffect` poll (independent of 3s main poll), collapsible `#db-chart` panel with `CandleChart` + OFFLINE placeholder

## Candle mapping
Databento bar `{ts (unix-s), open, high, low, close, volume}` → `Candle {t: ts*1000, o, h, l, c, vol: Math.min(1, volume/5000)}`.

## Startup log signature (flag OFF)
`INFO:__main__:DatabentoBrain: DATABENTO_ENABLED=0 — feed OFF (byte-identical mode); set DATABENTO_ENABLED=1 + DATABENTO_API_KEY to activate`
