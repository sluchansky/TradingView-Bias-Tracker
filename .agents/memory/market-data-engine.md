---
name: Market Data Engine
description: Phase 1-5 build of the live data feed layer. Covers data source choices, yfinance integration approach, and what fields are available.
---

# Market Data Engine

## Phase 1 — Data Feed Status panel (DONE)
- `compute_data_feed_status()` reads CURRENT_PRICE_TS_BY_TICKER + AUTO_PRICE_BY_TICKER + ALERT_HISTORY → per-instrument freshness (LIVE/STALE/OFFLINE)
- Whitelisted in /status as "data_feed"
- `mod-data-feed` HTML panel + `renderDataFeed` JS

## Phase 2 — Enhanced yfinance (DONE)
- **TradingView has NO data API** (even paid plans). Only webhooks.
- **Databento rejected** (costs money).
- **Yahoo Finance v7 quote endpoint is 401** — not usable for bid/ask.
- **Solution**: piggyback on the existing `_fetch_intraday_quote` v8/finance/chart call (already running for VWAP/volatility). The `meta` block in the v8 response contains: `regularMarketVolume`, `regularMarketDayHigh`, `regularMarketDayLow`, `chartPreviousClose`, `regularMarketPrice`.
- **Zero extra HTTP calls** — just added `quote["_meta"] = result.get("meta") or {}` in `_fetch_intraday_quote`.
- `AUTO_PRICE_BY_TICKER` now stores: `{value, ts, source, bar_high, bar_low, bar_close, bar_volume, volume, day_high, day_low, prev_close}`.
- DATA MODE badge: "TV + yfinance · vol/range" when data is fresh + volume present.
- Dashboard shows: price, volume, day range, prev close, % change per instrument.

## Phase 5 — Staleness gate (DONE, default OFF)
- `DATA_STALENESS_GATE_ENABLED` env flag
- `data_stale_brake` demote-only in `_dir_block` alongside `scalp_vol_brake`
- Arms when BOTH alert + auto-fetch price stale + market open

## What yfinance provides for CME futures (v8 chart meta)
- ✅ `regularMarketVolume` — cumulative session volume
- ✅ `regularMarketDayHigh` / `regularMarketDayLow` — day range
- ✅ `chartPreviousClose` — prev session close (more reliable than regularMarketPreviousClose)
- ✅ `regularMarketPrice` — last price (~15s delayed)
- ❌ `bid` / `ask` — always None for futures via this endpoint
- ❌ `regularMarketOpen` — sometimes None

**Why:** The v8/finance/chart endpoint is Yahoo's public chart API that still works without auth. The v7/finance/quote endpoint (which would have bid/ask) now requires auth (HTTP 401).

## Key invariants
- All data is DISPLAY-ONLY — never feeds gate/scoring/sizing/broker path
- `_update_price_auto` calls `_fetch_intraday_quote` (not `_fetch_latest_bar`) to get meta in one call
- `_fetch_latest_bar` still used by managed trade watcher (unchanged)
- Adding new fields to AUTO_PRICE_BY_TICKER is additive — existing consumers only read ["value"] and ["ts"]
