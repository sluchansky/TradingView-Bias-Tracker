---
name: Left Brain / Right Brain dual-engine
description: Architecture of the proactive Databento bar-close scanner (Left Brain) and training-mode execution engine (Right Brain) added to app.py.
---

# Left Brain / Right Brain Dual-Engine

## Rule
The Right Brain eval (`_right_brain_eval`) runs on every Databento bar close regardless of `DISCORD_LIVE_ENABLED` — Discord card send is prod-gated inside `_databento_bar_scan`, but the training log and PF gate update on dev too. `_right_brain_loop` is the 60s periodic fallback.

**Why:** Training log is useless if it only runs on the live instance. Dev testing needs to see WOULD_TRADE entries accumulate without firing Discord.

## How to apply
- `_databento_bar_scan`: gate Discord card with `if DISCORD_LIVE_ENABLED:`, then call `_right_brain_eval(inst, a, price)` unconditionally.
- `_right_brain_loop`: no `DISCORD_LIVE_ENABLED` guard; it self-reschedules via `threading.Timer(60, _right_brain_loop).start()` in the `finally` block.
- Boot timer lives inside the `if DISCORD_LIVE_ENABLED:` block (it fires 30s after start so Databento warms up).

## Key invariants
- Training mode (PF < `RIGHT_BRAIN_MIN_PF`, default 1.0): WOULD_TRADE logged, zero gateway calls.
- Live-eligible + armed: fires `_maybe_auto_execute` with `AUTO_FIRED_KEYS` dedup (same as SCALP webhook path).
- `_right_brain_pf()`: weighted average PF across `LEARNING_ANALYTICS` list entries for current mode, requires ≥5 trades per strategy, returns 0.0 on failure.

## Flask auth pattern
Flask routes have NO `@owner_required` decorator — auth is entirely Express-side (Basic Auth via the proxy). Never add an `@owner_required` decorator to a Flask route or it will crash at module load with NameError.

## Dashboard
- Panel `#mod-dual-brain` with `data-cat="primary"` (always visible, not gated by Advanced toggle).
- Polls `/api/right-brain` every 15s (4s initial delay) via `_pollDualBrain()`.
- Left Brain display uses `last_eval[inst]` from the right-brain endpoint (same data source).
- Proxy whitelist: `/right-brain` added to `BOT1_ROUTES` in `artifacts/api-server/src/routes/flask-proxy.ts`.
