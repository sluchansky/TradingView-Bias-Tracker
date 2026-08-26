---
name: Scalp live paper-simulation layer
description: How the research scalp strategies are PAPER-simulated on the live market, walled off from the money path, and the non-obvious bugs that bit it.
---

# Scalp live paper-simulation

PAPER-simulates the testable research-library scalp strategies on the LIVE webhook/price
stream to prove winners before manual promotion. **Fully walled off from the money path.**

## Invariants (any violation = real-money risk)
- Behind default-OFF flag `SCALP_LIVE_SIM_ENABLED`; flag OFF must be goldens byte-identical (additive + fail-open). Pure detector module imports nothing from `app.py`.
- Writes to its OWN table ONLY. It must NEVER write `strategy_trades`, call `_record_strategy_trade`/`_close_managed_trade`, touch `MANAGED_TRADES*`, register into `STRATEGY_SCORERS`/priority/control, or hit `/traderspost`/auto-trade, or feed the learning engine (learning has a ±15 money-path effect — a leak there moves real money).
- Observer runs ONLY from the webhook analysis path (rejects `source != "webhook"`, so `/status`/dashboard polls can't open trades); fail-open; never mutates verdict/ready/trade_plan/edge_score.
- Owner-only: `/scalp-research` stays whitelisted in `flask-proxy.ts` but NOT in dashboard-auth `OPEN_PATHS`. Dashboard render is textContent-only. No in-app DDL (table created via DB tool in dev, Publish schema-diff in prod).

## Concurrency / dev-vs-prod
- **Watcher must be gated to the LIVE instance INSIDE its loop**, not only at boot wiring. It self-reschedules forever via `threading.Timer`, so a boot-only `DISCORD_LIVE_ENABLED` gate is not enough — the loop body itself must re-check `DISCORD_LIVE_ENABLED` (dev + prod share secrets → both would otherwise resolve rows).
- The cross-instance "status claim" is the atomic conditional `UPDATE ... WHERE id=%s AND status IN ('open','resolving')` — the first writer (dev OR prod) wins, a concurrent UPDATE matches 0 rows and no-ops. There is no separate `open->resolving` step; the conditional UPDATE *is* the claim.
- The one-open SELECT-then-INSERT guard is safe because webhook processing is single-threaded (one `webhook-worker` thread) and the INSERT is idempotent via `ON CONFLICT (sim_key)`.

## GOTCHA: SHORT R-multiple sign
- R must be measured as `(entry - exit)/risk` for SHORTs (so a stop = negative, a target = positive). Using the LONG formula `(exit - entry)/risk` for shorts **inverts the signs** and silently corrupts win%/avgR/netR/promotion-proof for every short candidate.
- **Why it's dangerous:** `py_compile` + goldens (flag OFF) + an HTTP smoke all pass while this is broken. Only an explicit LONG *and* SHORT outcome test catches it. Always test both directions for any ±R resolver.
- The max-hold "expired" branch already keys off direction correctly; the bug was only in the stop/target resolver.

## Retained-bar resolution boundary
- Watchers evaluate all retained completed bars in chronological order, skipping bars at or before `entry_epoch` (or `opened_at` fallback) to preserve the same-bar guard.
- If a row reaches max hold but retained history starts after its entry, terminalize it as `unresolved` with `market_history_unavailable`/`market_history_truncated`, age, and max-hold metadata. Do not fabricate an expiry from an unobserved interval; unresolved rows stay out of performance and learning evidence.
- **Why:** Databento’s in-memory retention is finite and restarts can leave a paper row older than the available tape; treating the latest close as an observed outcome would poison strategy comparison.
- **How to apply:** Any future paper ledger or backfill must distinguish a real terminal bar from an uncovered history gap, and must expose the gap in watcher health.

## Dashboard read path
- GET `/scalp-research` must DEEP-COPY the cached research collections (tested/library/best/worst/promotions) before augmenting with live-sim stats — otherwise the augmentation mutates the shared cache.
