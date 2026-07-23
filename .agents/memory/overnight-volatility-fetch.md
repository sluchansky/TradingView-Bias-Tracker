---
name: Overnight volatility fetch (ES/NQ bar scarcity)
description: Why MES/MNQ ATR shows "unavailable" overnight and how it was fixed.
---

## The rule
`VOL_MIN_BARS` must stay ≤ the minimum bars Yahoo Finance returns for the slowest
CME equity-index session.  As of 2026-07, `range=1d interval=1m` for ES=F/NQ=F
delivers only **~12 bars** overnight (00:00–06:00 ET), while GC=F/YM=F deliver ~21.
Setting the minimum above ~12 silently breaks MES/MNQ ATR overnight.

## Why
Yahoo's `1d` range clips differently per exchange session.  ES/NQ are equity-index
futures (CME); GC is COMEX gold; YM is CBOT Dow — each has a different session-start
boundary in Yahoo's back-end, so bar counts diverge in the overnight window.

## How to apply
- `VOL_MIN_BARS = 5` — safe floor; `trs[-14:]` gracefully uses all available bars.
- `VOLATILITY_MAX_AGE_MIN = 60` — keeps last-good ATR valid through brief data gaps.
- `_fetch_intraday_quote` 45 s TTL cache — VWAP + volatility loops share one HTTP
  request per symbol per tick; avoids Yahoo rate-limiting the second same-symbol call
  which can return a sparser response.
- The failure is SILENT: `len(trs) < VOL_MIN_BARS` returns `None,None,None` before
  `_log_feed_status` is called, so no WARNING appears in the log.  Diagnose by
  testing `_fetch_intraday_quote` live and counting `valid_highs`.
