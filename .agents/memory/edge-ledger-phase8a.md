---
name: Edge Ledger Phase 8A
description: Signal-vs-management accounting layer; frozen original terms; EL_DB_READY flag; 5 integration points; display-only.
---

# Edge Ledger Phase 8A — Signal vs Management Accounting

## Rule
`edge_ledger` table captures frozen signal terms at ghost_observe_setup capture point,
resolves signal outcome via ghost watcher (bar-by-bar with frozen originals), and links
managed outcome via native_journal close. Comparison computed in-DB via SQL CASE.
Display/accounting only — no gate, no learning weight changes, no scoring, no execution.

## Architecture

### Capture point
`_ghost_observe_setup` — immediately after the ghost_obs INSERT succeeds.
`_el_create_entry()` is called with obs_key, result dict, instrument, direction, strategy_key,
entry/stop/targets, estimated cost_r, session, edge_score, and now_utc_ts.

### Signal outcome update
`_ghost_obs_watcher_cycle` — in the `elif status is not None:` branch, after the
`UPDATE ghost_observations` try/except block. `_el_update_signal_outcome_conn()` is called
with the same conn (shared connection) to avoid a second DB round-trip.

### NJ linkage
`_nj_create_from_snapshot` — after `finally: conn.close()`. `_el_try_link_to_journal()`
opens its own connection, matches by instrument+direction within last 10 minutes using a
CTE-based UPDATE (PostgreSQL doesn't support ORDER BY LIMIT in UPDATE directly), and
promotes sample_partition from SHADOW → LIVE.

### Managed outcome update
`_nj_set_outcome` — inside the try block, after `conn.commit()` and before `cur.close()`.
`_el_update_managed_outcome()` opens its own connection and fires two UPDATEs:
(1) managed_* columns, (2) comparison computation (signal_vs_managed_delta_r + reason + management_helped).

### Boot probe
`_check_edge_ledger_db_ready()` — called at boot after `_check_ghost_obs_db_ready()`.
Sets `EL_DB_READY = True` when `edge_ledger` table exists.  Default False = all helpers
are byte-identical no-ops (fail-safe).

### Diagnostics endpoint
`GET /edge-ledger/diagnostics` — owner-only (Express auth), NOT in OPEN_PATHS.
Added to `BOT1_ROUTES` in `artifacts/api-server/src/routes/flask-proxy.ts`.
Also added `/profitability/summary` and `/profitability/observations` to the same whitelist
(they were Flask routes but were missing from the proxy).

## Key files
- `artifacts/tradingview-webhook/db_edge_ledger_schema.sql` — full DDL incl. immutability trigger
- `artifacts/tradingview-webhook/edge_ledger.py` — pure module (no app.py imports)
- `artifacts/tradingview-webhook/tests/test_edge_ledger_phase8a.py` — 46 required + 9 extra tests

## DB schema facts
- Primary key: `edge_id` = `el|{inst}|{direction}|{strategy_short}|{obs_key}` via `build_edge_id()`
- Immutability enforced by `el_immutability_guard` trigger — all `original_*` columns + `instrument/direction/strategy_key/signal_timestamp` are UPDATE-protected at the DB level.
- `ghost_obs_key` → links to ghost_observations; `internal_trade_id` → links to native_journal.
- `sample_partition`: SHADOW (ghost-only) → LIVE (when NJ fires) via `_el_try_link_to_journal`.
- `cost_model_version = 'v1'`; costs always ESTIMATED (never silent zero per spec).

## What NOT changed
- Learning engine weights: unchanged. `edge_ledger_ready_for_learning` is a staging flag only.
- Gate/scoring/sizing/execution: completely untouched.

**Why:** Phase 8A is accounting-only. Learning engine will consume edge_ledger in a future phase.

## Test path fix
Test #46 workspace path: go 4 dirname() levels up from `tests/test_edge_ledger_phase8a.py`
(tests/ → tradingview-webhook/ → artifacts/ → workspace/). Using 3 levels gives `artifacts/`.
