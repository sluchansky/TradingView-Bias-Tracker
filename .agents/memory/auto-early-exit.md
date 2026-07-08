---
name: Auto Early-Exit
description: Bot self-flattens its own tracked position when the trade-management advisory confirms the thesis is invalid; arming, trigger, fire-path, and coverage invariants.
---

# Auto Early-Exit (opposite_confirmed flatten)

The bot can flatten its OWN tracked live position when the Active Trade Management
advisory (compute_manual_trade_management on the bot-trade mirror) says the thesis
is CONFIRMED invalid. Owner-armed via `/auto-exit` (owner-only, proxy-whitelisted,
NOT in OPEN_PATHS) with a dashboard `mod-autoexit` panel.

**Rules (all reviewer-approved, don't loosen):**
- Trigger is `invalidated and not stop_breached` — i.e. opposite_confirmed ONLY.
  The STOP_HIT webhook path owns stop bookkeeping; auto-exit must never race it.
- Requires AUTO_EXIT_CONFIRM_READS (2) CONSECUTIVE invalid reads, strikes keyed
  (inst, opened_at) and reset when opened_at changes; read/advisory errors NEVER
  advance strikes (fail-open on noise, fail-closed on money).
- Fire ordering: FIRED-mark FIRST → last-instant `opened_at` identity recheck
  under ACTIVE_TRADES_LOCK (position replaced during the ~60s sweep window would
  otherwise get the NEW broker position flattened) → non-reversing
  adapt_traderspost_reduce `{action:"exit"}` via the audited _send_broker_order →
  `popped = clear_active_trade(inst, opened_at=...)` compare-and-clear → journal +
  Discord ONLY if popped is not None (STOP_HIT winning the race = no double-journal,
  no phantom maxLossesPerDay loss).
- `_send_broker_order` convention: `(None, None)` = 2xx success; any non-None
  result → DISARM + 🛑 manual-flatten Discord alert, keep local tracking.
- Live send requires mode==traderspost AND DISCORD_LIVE_ENABLED (dev never sends);
  paper mode = local tracked close only; manual_only → scan returns early.
- Fire-path Discord cards are DISCORD_LIVE_ENABLED-gated (shared dev/prod secrets).
- AUTO_EXIT_ARMED is in-memory, resets OFF on every restart (same philosophy as
  auto-trade arming — intentionally non-persistent). AUTO_EXIT_ENABLED env kill
  switch (=0) skips even starting the watcher Timer.
- AUTO_EXIT_LOCK is never held across money locks or network calls; strikes/FIRED
  are single-writer inside the watcher thread.

**Known coverage gaps (accepted):**
- Paper-mode path only covers manually-entered /enter positions — paper AUTO
  entries live in MANAGED_TRADES (Option C), not ACTIVE_TRADE, so the paper close
  path never sees them.
- A live SCALP position's MANAGED_TRADES twin survives the ACTIVE_TRADE pop
  (journal is already terminal; /stop-managing cleans up).
