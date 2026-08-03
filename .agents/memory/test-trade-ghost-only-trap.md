---
name: Test-trade GHOST_ONLY trap
description: Test fixture rows in strategy_trades trigger GHOST_ONLY for an instrument, silently rerouting live orders to paper. Fix is a SQL filter in the LRE eligibility queries, not a boot-time DELETE.
---

## What Happened

Rows inserted during integration testing (managed_key prefix `test_*`, e.g. `test_p6_`, trading_mode=SCALP) remained in `strategy_trades`.  The learning eligibility SQL counts them as real samples.  n=1–49 range → `GHOST_ONLY`.  The LRE gate in `_execute_trade_gateway_inner` sees GHOST_ONLY → routes to paper → **live orders silently never reach TradersPost**.

## Fix Applied

All three eligibility SQL queries in `_recompute_learning_eligibility` exclude test rows at query time:

```sql
AND (managed_key IS NULL OR managed_key NOT LIKE 'test_%%')
```

The `%%` escaping is required — psycopg2 treats a bare `%` as a parameter placeholder even with no parameter tuple, raising `tuple index out of range`.

**Why SQL filter instead of boot DELETE:** operator wants test trades preserved across republishes for inspection/replay. The SQL filter makes them invisible to GHOST_ONLY counting without destroying the rows.

**Why:** n=0 is deliberately fail-open (bootstrap new instruments freely). n=1–49 is GHOST_ONLY to prevent going live on minimal history. Test rows cross the n=0 boundary and permanently lock the instrument until 50 real trades accumulate — or the filter excludes them.

## How to Apply

- Any row with `managed_key LIKE 'test_%'` is automatically excluded from GHOST_ONLY counting.
- If GHOST_ONLY is unexpectedly active for an instrument with few real trades, check for test fixture rows: `SELECT COUNT(*) FROM strategy_trades WHERE managed_key LIKE 'test_%'`
- The `_boot_purge_test_trades()` function still exists in app.py but is NOT called at boot — do not re-add it to the boot sequence.
- If adding new LRE eligibility queries in `_recompute_learning_eligibility`, always include the `NOT LIKE 'test_%%'` filter on all three SELECT blocks.
