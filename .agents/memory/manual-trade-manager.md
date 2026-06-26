---
name: Manual Trade Manager
description: Advisory-only dashboard module that monitors a manually-entered live position and gives management guidance — never a money path.
---

# Manual Trade Manager (advisory-only)

Owner-only dashboard module: the user logs a position they took by hand and the bot
renders live management guidance (price, unrealized P&L, current R, distances, thesis
status, a HOLD/REDUCE/EXIT/MOVE-STOP rec, what-improves / what-invalidates, next
review). It is a pure DISPLAY/ADVISORY layer.

**Why:** the existing execution gateway can only OPEN sanctioned setups; it cannot
flatten an arbitrary hand-entered position. So there is deliberately NO broker exit
path here — guidance only, the human acts.

**How to apply / invariants when touching it:**
- `/manual-trade` (GET list+guidance / POST create) and `/manual-trade/close` must
  never send a broker/Discord/HTTP POST and never mutate trading state (price globals,
  `ACTIVE_TRADES_BY_INST`, gate/scoring/verdict inputs). Manual state lives in its own
  `MANUAL_TRADES` dict + lock, fully separate from the money path. The only write
  `compute_manual_trade_management` makes is `min_r`/`max_r` back onto the in-memory
  trade dict (for the "poor entry but recovering" read).
- Thesis rule: INVALID only on stop-breach OR a CONFIRMED opposite trend
  (`structure_class` == "Bullish/Bearish Trend"). An opposite *Attempt* (BOS-only),
  VWAP-against, near-stop, or an opposing zone-ahead only WEAKEN. Market-closed
  (`market_open is False`) PAUSES a held position — never invalidates it — UNLESS the
  stop is already breached (that stays INVALID/EXIT). Don't reintroduce the
  `thesis != "INVALID"` guard in the closed-market branch; it leaked INVALID+HOLD on a
  closed-market opposite-trend read.
- No live price OR zero risk → status "unavailable" / thesis UNKNOWN / rec MONITOR.
  Never fabricate guidance.
- Persistence mirrors the learning engine: NO in-app DDL. A SELECT-only readiness
  probe sets the DB-ready flag; runtime writes are INSERT/UPDATE guarded by that flag;
  missing/bad DB fails OPEN to in-memory (monitoring still works). Table is created via
  the database tool (dev) + Publish schema-diff (prod).
- Wiring gotchas: both routes MUST be in the Express `/api` proxy whitelist
  (`flask-proxy.ts`) or they 404, and must stay OWNER-ONLY (NOT in dashboard-auth
  OPEN_PATHS). The before_request logger needs a redaction branch for POST
  /manual-trade (it echoes bodies otherwise). `contracts` must reject non-integral
  values — bare `int(1.5)` silently truncates to 1 and understates P&L.
- Additive only: scalp/swing-flagoff goldens + parity stay byte-identical. Guarded by
  `.local/state/check_manual_trade.sh` (run directly; not a workflow).
