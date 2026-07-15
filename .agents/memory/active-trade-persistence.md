---
name: Active trade persistence via open_trades table
description: How paper/ghost open positions survive restart/republish — the open_trades DB table + write-through in set/clear active trade.
---

# Active trade (ghost/paper) persistence across restarts

`ACTIVE_TRADES_BY_INST` is an in-memory dict. Before this fix, a republish wiped
all open paper/ghost positions — the bot forgot it had an open trade and the managed-trade
watcher would never close/record it.

**Fix:** `open_trades` Postgres table (PRIMARY KEY: `inst`, columns: `inst`, `payload JSONB`,
`opened_at TIMESTAMPTZ`, `updated_at TIMESTAMPTZ DEFAULT now()`).

- `set_active_trade(inst, trade)` calls `_persist_active_trade(inst, trade)` (upsert)
  **after** releasing `ACTIVE_TRADES_LOCK` — never I/O inside the lock.
- `clear_active_trade(inst)` calls `_persist_active_trade(inst, None)` (DELETE)
  **after** releasing `ACTIVE_TRADES_LOCK`, only when a trade was actually popped.
- `_persist_active_trade` offloads to `_enqueue_slow` so it is non-blocking and FAIL-OPEN.
- Boot: `_check_active_trades_db_ready()` + `_load_active_trades_from_db()` run at
  `LEARNING_DB_ENABLED` level (unconditional; NOT gated on `_swing_htf_enabled()`).
  Restore is INERT — populates the dict with no alerts/journal/broker side-effects.

**Why:** auto-trade ARMING is intentionally non-persistent (user declined; see
auto-trade-arming-lifecycle.md) but the POSITION itself (open trade) must persist or
the bot loses track of a real/paper trade on every republish.

**How to apply:** SWING managed trades continue to use the separate `swing_theses` table
(persist via `_persist_swing_thesis`). The `open_trades` table handles SCALP/paper
`ACTIVE_TRADES_BY_INST` entries (non-swing, non-managed-trade-path positions).
`SWING_THESIS_DB_READY` and `ACTIVE_TRADES_DB_READY` are independent flags.
