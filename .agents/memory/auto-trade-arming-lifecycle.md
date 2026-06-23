---
name: Auto-trade arming lifecycle & "bot not taking trades" diagnosis
description: Why the bot stops auto-entering after a republish, how to diagnose it, and the explicit decision to keep arming non-persistent.
---

# Auto-trade arming lifecycle

AUTO-TRADE is a per-instrument arm switch (MGC/MNQ). The armed state lives in the
in-memory server-side `AUTO_TRADE` dict — it is NOT persisted, so it resets to OFF
on every restart/republish. Two independent gates must both hold for an auto entry:
1. `auto_trade_enabled(inst)` is True (operator armed it on the dashboard), AND
2. live broker orders only fire on the published/live instance
   (`is_live_instance` / `DISCORD_LIVE_ENABLED`); the dev preview blocks live
   auto-sends even when armed (paper/manual_only are allowed anywhere).

**Diagnosis pattern — "bot not taking trades / setups go READY but nothing fires":**
- First check `GET /auto-trade` → the `enabled` map. All-false = not armed; this is
  the usual cause, *especially right after a publish* (republish resets it OFF).
- Confirm the operator is on the PUBLISHED app, not the dev preview (`is_live_instance`
  false on dev).
- The dashboard bell rings ONLY on an actual entry (an `ACTIVE_TRADE` opening), never
  on a bare READY verdict — so "bell but no broker order" still points at the
  execution path (non-live instance / non-sending mode), not the alert layer.
- Other auto gates if armed+live: full READY only (EARLY never auto-fires), 20
  entries/instrument/day cap, and the $100/trade risk ceiling skips over-cap plans.

**Decision (2026-06-23): keep arming NON-persistent — manual re-arm after each
republish.** The user was offered persistence (resume auto-arm on boot) and
explicitly declined for safety.
**Why:** auto-arming on boot would resume placing REAL-money orders with no human
in the loop after a crash/redeploy. Do NOT add arming persistence without
re-confirming with the user.
