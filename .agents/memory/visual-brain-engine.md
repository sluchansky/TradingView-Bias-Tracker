---
name: Visual Brain 2.0
description: Event-driven stateful market observer with completed-bar gating, bounded vision spend, and strict isolation from trading state.
---

# Visual Brain 2.0 — Durable Architectural Decisions

## Completed-bar events own paid-call timing
Live Databento completed bars debounce into one per-instrument local gate evaluation. The original fixed timer remains only as a conservative heartbeat/fallback, and every path remains single-flight.

**Why:** Polling every few minutes can collapse meaningful transitions, while running paid vision on every poll wastes spend. Ownership tokens are required on debounce timers so a canceled stale callback cannot clear or dispatch a newer event generation.

**How to apply:** Register through the existing completed-bar callback, return immediately from market-data intake, coalesce events off-thread, and require a newly completed bar plus a meaningful event or active-market maximum-staleness heartbeat before image/model work.

## Paid-attempt caps reserve the full retry budget
Each observation atomically reserves every allowed model attempt before network work. Attempts settle by reservation identity; unused retries are released, while started attempts with unknown usage remain conservatively estimated.

**Why:** Reserving only one observation while the model can retry lets concurrent instruments exceed spend caps and allows one response to settle another instrument's reservation.

**How to apply:** Keep the reservation ledger at least as large as the maximum configured window cap, distinguish current exposure from next-observation capacity in telemetry, and never let cap state affect anything outside Visual Brain.

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

## Mode-aware assessment contract
Keep the established generic Visual Brain observation compatible and put SCALP, INTRADAY_TREND, and SWING assessments inside its existing `raw_json` payload; do not add a table column just for an advisory model response. New observations require all three assessments, while old rows hydrate to an empty assessment map. Treat every persisted mode field as untrusted at the UI boundary and reject invalid model payloads before persistence. **Why:** model output can evolve or be malformed, and the historical table already contains legacy observations; a schema change would add operational risk without any execution benefit. **How to apply:** this data is display-only and must never feed gating, scoring, sizing, alerts, broker routing, or execution. If a new displayed field is added, validate it on the backend and render it defensively in the dashboard.

**How to apply:** Any future vision-model sub-module should follow the same injection pattern. Never add `import app` inside a helper module that app.py imports — it silently creates a second module instance.
