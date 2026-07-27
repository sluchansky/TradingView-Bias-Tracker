---
name: Databento signal → immediate bar scan
description: _databento_structure_trigger now also spawns _databento_bar_scan on non-duplicate signals; dual-sim observer accepts "databento_scan" source
---

## Rule
`_databento_structure_trigger` enqueues a webhook job AND spawns `_databento_bar_scan`
in a daemon thread on non-duplicate signals, so READY setups fire within seconds of a
BOS/CHOCH/sweep instead of waiting up to 60s for the next 1m bar close.

**Why:** User saw 8 READY setups on dashboard but zero auto-trades. Bar scan is the
auto-execute path; signals only ran through the webhook worker queue.

**How to apply:**
- The bar scan is spawned only when `not is_duplicate` (cooldown not active)
- AUTO_FIRED_KEYS dedup prevents double auto-execute between the two paths
- `_maybe_observe_dual_mode_sim` accepts `source not in ("webhook", "databento_scan")`
  so both paths feed the dual shadow simulator

## Clear Fired Keys
`POST /clear-fired-keys` clears AUTO_FIRED_KEYS only (not alert history/price/zones).
Use after a redeploy so same-day restored keys don't block re-entry on valid setups.
Button lives on the Auto-Trade Settings page under "Auto-Fire Dedup".
