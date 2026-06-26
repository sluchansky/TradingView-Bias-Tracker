---
name: Manual Trade Manager bot mirror & mutation gotcha
description: The advisory monitor box also displays the bot's own open positions; the management compute mutates its input dict, so any live-trade mirror must pass a copy.
---

# Manual Trade Manager — bot-position mirror

The "Manual Trade Manager" advisory box (`MANUAL_TRADES`, `GET /manual-trade`,
`compute_manual_trade_management`) ALSO displays the bot's own OPEN positions
(`ACTIVE_TRADES_BY_INST`) so a bot-sent order shows up there automatically — not
just in the top status card. Built by `_bot_active_trade_monitor_items()` at GET
request time, rows tagged `origin:"bot"` / `advisory_mirror:True`; UI shows a
"🤖 BOT" badge and hides "Stop monitoring". Manual rows are tagged
`origin:"manual"`.

**Rule:** the mirror is DISPLAY-ONLY. It must never write to MANUAL_TRADES,
journal, learning, dedupe, or any broker/execution path, and it builds each row
from a THROWAWAY copy of the live trade (never the `ACTIVE_TRADES_BY_INST`
object). Rows derive from `active_trade_snapshot()` each poll, so they appear
while open and vanish on `clear_active_trade()` — no open/close hooks, no second
store. FAIL-OPEN (per-instrument + whole-merge try/except).

**Why (the non-obvious bit):** `compute_manual_trade_management` MUTATES the
trade dict it is handed (writes `min_r`/`max_r` back onto it). Handing it the
live `ACTIVE_TRADE` object would corrupt money-path state. Always pass a copy.

**How to apply:** any future "show a live position somewhere else" feature must
(1) copy the dict before calling the management compute, and (2) stay off every
money path. Verify with an offline import test asserting the source dict's keys
are unchanged (`ORIG_UNMUTATED`).

**Known gap:** SCALP paper-dynamic trades live in `MANAGED_TRADES_BY_KEY` and
skip `ACTIVE_TRADE`, so they are NOT mirrored here yet (acceptable; mirror them
the same copy-first way if needed).
