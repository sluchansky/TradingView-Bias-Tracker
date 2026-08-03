---
name: Test-trade GHOST_ONLY trap
description: Phase-6 test fixture rows in strategy_trades triggered GHOST_ONLY for MGC/MNQ SCALP, silently rerouting all live orders to paper.
---

## What Happened

6 rows inserted during Phase-6 integration testing (managed_key prefix `test_p6_`, mode=display, trading_mode=SCALP, identical timestamps 2026-07-30 12:00) remained in the production `strategy_trades` table.

The learning eligibility SQL uses `trading_mode` (not `mode`), so these rows counted as real SCALP samples.  n=1–49 range → `GHOST_ONLY` (by design: "some data but not enough").  The LRE gate in `_execute_trade_gateway_inner` saw GHOST_ONLY → `mode = "paper"` → **all live MGC and MNQ SCALP orders silently routed to paper, never reaching TradersPost**.

## Fix Applied

`_boot_purge_test_trades()` added before `_recompute_learning` in the boot sequence.  Deletes WHERE `managed_key LIKE 'test_p6_%'` (no-op after first run).  Requires a publish to reach production; after that, the recompute sees n=0 → fail-open LIVE_ELIGIBLE.

**Why:** n=0 is deliberately fail-open (bootstrap new instruments freely).  n=1–49 is GHOST_ONLY to prevent going live on minimal history.  Test rows cross the n=0 boundary and permanently lock the instrument until deleted or 50 real trades accumulate.

## How to Apply

- Any future `test_p6_*` / `test_p7_*` prefix rows MUST be deleted from `strategy_trades` before publish.
- Check production with: `SELECT COUNT(*) FROM strategy_trades WHERE managed_key LIKE 'test_%'`
- If GHOST_ONLY is unexpectedly active for an instrument with few real trades, check for test fixture rows first.
