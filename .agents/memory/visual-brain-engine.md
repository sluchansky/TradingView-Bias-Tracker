---
name: Visual Brain V1
description: MNQ 1-minute stateful market observer — screenshot + vision LLM + structured JSON market state stored in visual_brain_observations table.
---

# Visual Brain V1 — Durable Architectural Decisions

## Dependency injection — never `import app` from sub-modules
`visual_brain.py` receives `db_conn_fn`, `price_store`, and `bars_fn` as parameters to `start()` and `check_vb_db_ready()`. These are injected from `app.py`'s `__main__` globals at boot. **Why:** when `app.py` runs as `__main__`, doing `import app` from any sub-module loads a SECOND copy of the module with empty globals — breaking DB connections, live price stores, and bar history. All other sub-modules in this codebase that touch live app state must follow the same pattern: inject, don't import.

## Single-flight reschedule contract
`_vb_tick()`'s enabled guard lives ABOVE the `try/finally` block. `_schedule_next()` is called exactly once, exclusively in `finally`. Early failure returns must never call `_schedule_next()` themselves. **Why:** a persistent failure (screenshot error, API timeout) that calls `_schedule_next()` before `finally` also calls it doubles active timers every interval — runaway Chromium processes and API cost. The `TestSingleFlightReschedule` suite enforces this on every code path.

## Screenshots are ephemeral — no temp files
Screenshots are captured, analyzed, and discarded in-memory. `screenshot_path` is always stored as NULL. **Why:** `delete=False` temp files written every 60 seconds exhaust local disk silently. Use object storage (via the object-storage skill) if permanent screenshot retention is ever needed.

## Schema file convention
`db_visual_brain_schema.sql` tracks the `visual_brain_observations` DDL alongside the other `db_*.sql` files in this directory. Apply via the DB tool (dev) or publish schema-diff (prod). App code is INSERT/SELECT only — no DDL in `app.py`.

## `check_vb_db_ready()` requires `db_conn_fn` argument
The probe function now takes `db_conn_fn` so the correct `_learning_conn` from `__main__` is used even at boot time (before `start()` is called). The caller in `app.py`'s `_check_vb_db_ready()` must pass it: `_vb_probe.check_vb_db_ready(db_conn_fn=_learning_conn)`.

## Runtime dependencies
`openai`, `playwright`, `Pillow`, and `matplotlib` are declared in `requirements.txt`. The Playwright Chromium binary is installed separately (`playwright install chromium`). Any new deployment that enables `VISUAL_BRAIN_ENABLED=true` must confirm both the package and the binary are present.

**How to apply:** Any future vision-model sub-module should follow the same injection pattern. Never add `import app` inside a helper module that app.py imports — it silently creates a second module instance.
