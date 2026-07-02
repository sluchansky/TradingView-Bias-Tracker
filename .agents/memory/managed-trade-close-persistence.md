---
name: Managed-trade close must persist the SWING closed flag
description: Any path that stops/closes a managed trade in memory must persist the closed flag for SWING theses, or the trade resurrects as OPEN on the next boot.
---

# Managed-trade close/stop must persist the SWING closed flag

Any code path that marks an entry in `MANAGED_TRADES_BY_KEY` closed
(`mt["closed"] = True`) MUST, for `is_swing` trades, also call
`_persist_swing_thesis(mt)` right after setting the flag.

**Why:** SWING theses are persisted to the `swing_theses` table and rehydrated on
boot by the loader that selects `WHERE closed = FALSE`. An in-memory-only
`closed=True` is lost on the next restart/republish, so the trade comes back as
OPEN / "managing" — reproducing the "I closed it but the bot still shows it
managing" complaint. `_close_managed_trade` already guards this in its tail; new
close paths tend to forget it. SCALP / legacy trades carry no `is_swing`, so the
branch is a no-op for them (keeps goldens byte-identical).

**How to apply:** Whenever you add a new close / stop / flush / invalidate path
for managed trades, mirror the `_close_managed_trade` tail:
`if mt.get("is_swing"): try: _persist_swing_thesis(mt) except Exception: logger.warning(...)`.
`_persist_swing_thesis` snapshots synchronously then offloads the DB upsert to the
slow worker, so it is safe to call from a request thread and is fail-open.

**Local position-tracking flush surfaces:** the bot tracks an open position in
THREE stores that a "stop managing" action must all clear —
`ACTIVE_TRADES_BY_INST` (`clear_active_trade`, under `ACTIVE_TRADES_LOCK`),
`MANAGED_TRADES_BY_KEY` (mark closed, do NOT pop — the watcher iterates it
lock-free; day-change housekeeping pops later), and `MANUAL_TRADES` (pop under
`MANUAL_TRADES_LOCK` + persist closed). The owner-only `/stop-managing` endpoint
flushes all three and is TRACKING-ONLY (never sends a broker order); it is
whitelisted in `BOT1_ROUTES` only and is not in dashboard-auth `OPEN_PATHS`.
