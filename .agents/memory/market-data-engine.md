---
name: Market Data Engine
description: Multi-phase plan to add real futures market data alongside TradingView alerts. Phase 1 done; Phase 2 pending Databento API key.
---

## Phase status
| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Data Feed Status display panel + Phase 5 staleness gate | DONE |
| 2 | Databento live tick feed | BLOCKED — needs DATABENTO_API_KEY |
| 3 | MarketDataProvider abstraction layer | Follows Phase 2 |
| 4 | Keep execution unchanged | Invariant (always) |
| 5 | Stale data → WAIT safety gate | Infrastructure DONE (default OFF); arms after Phase 2 live |

## Phase 1 — what was built
- `compute_data_feed_status(instrument=None)` — reads CURRENT_PRICE_TS_BY_TICKER,
  AUTO_PRICE_BY_TICKER, ALERT_HISTORY → per-instrument freshness (LIVE/STALE/OFFLINE)
- Whitelisted in `/status` as `"data_feed"`
- Dashboard panel `mod-data-feed` with `renderDataFeed(d)` JS in render loop
- DATA MODE: ALERT-ONLY badge

## Phase 5 — data staleness gate (default OFF)
- `DATA_STALENESS_GATE_ENABLED=1` arms it (env flag)
- `DATA_STALE_THRESHOLD_MINS` (default 15) — age threshold for alert price
- `data_stale_brake` — fail-open demote-only READY→WAIT
- Only fires when BOTH alert price AND auto-fetch price are stale + market open
- Wired into `_dir_block` alongside `scalp_vol_brake`

## Phase 2 — Databento plan (pending)
- `databento` package available: `pip install databento` (v0.81.0 in index)
- Dataset: `GLBX.MDP3` (CME Globex) for MNQ, MES, MYM; `XNAS.ITCH` for equities
- MGC is on COMEX which is also under GLBX.MDP3
- Symbol format: continuous front-month e.g. `MNQM5` or continuous `MNQ.c.0`
- Live streaming via `databento.Live` client (WebSocket)
- Needs: `DATABENTO_API_KEY` secret

## Key invariants for Phase 2 implementation
- Execution path (TradersPost → Tradovate) must NOT change
- Live data is ONLY for: display, faster entries, stale detection, learning
- Databento price feeds AUTO_PRICE_BY_TICKER (display-only layer) — never CURRENT_PRICE_BY_TICKER (alert-only gate layer)
- Phase 5 gate activates automatically once Databento provides fresh data
- New data provider must be fail-open: Databento down → ALERT-ONLY mode gracefully

## Needed symbols (Databento)
- MNQ → `MNQ.c.0` (Micro E-mini Nasdaq, continuous, GLBX)
- MES → `MES.c.0` (Micro E-mini S&P, GLBX)
- MGC → `MGC.c.0` (Micro Gold, GLBX/COMEX)
- MYM → `MYM.c.0` (Micro Dow, CBOT = GLBX)
