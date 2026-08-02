---
name: Native Journal Phase A
description: Key implementation lessons from the native_journal table and Phase A backend helpers.
---

## The rule

All NJ helpers use `json.dumps(...)` — **never** `_jj.dumps(...)`. `_jj` is a function-local alias in app.py (defined inside `_record_arm_audit`), not a module-level import. Using `_jj` in module-level helpers causes a silent `NameError` caught by the fail-open try/except, making the entire DB write a no-op with no visible error.

## Table

`native_journal` — provisioned out-of-band via `db_native_journal_schema.sql`. App does NO DDL. Boot probe: `_boot_native_journal_table()` → sets `NJ_DB_READY`. Column `internal_trade_id UUID UNIQUE` is the FK to `internal_trade_snapshots`.

## Every helper needs its own NJ_DB_READY guard

`_nj_close_by_instrument` delegates to `_nj_find_open_by_instrument`, but if the `NJ_DB_READY` guard is only in the delegate (not the caller), tests that mock the delegate will still see the call happen. Rule: every public NJ helper must have `if not NJ_DB_READY: return` at the top.

## Wiring

- `_capture_send_time_snapshot` calls `_nj_create_from_snapshot(snap)` after `_persist_trade_snapshot(snap)` — fail-open inside the same try/except.
- `_update_journal_outcome` calls `_nj_close_by_instrument(...)` only when `state in ("win", "loss", "breakeven")` — same block as `post_performance_stats`, gated by `analytics_posted`.

## R calculation invariant

`_nj_set_outcome` always reads `planned_entry` and `planned_stop` from the DB row (immutable planned columns) to compute `realized_r`. It never uses a moved stop. This is intentional — BE moves must not inflate the R number.

## Test sentinel pattern

When mocking DB rows that may have nullable JSONB columns, use a sentinel (`_UNSET = object()`) in `_set_row` helpers to distinguish "caller explicitly passed None" from "caller didn't pass, use default". Without it, `execution=None` triggers the default substitution and the NULL-column test silently tests the happy path instead.

**Why:** The standard `if param is None: param = default` pattern in test helpers conflicts with tests that want to assert behavior when the column is actually NULL.

**How to apply:** Any test helper that provides defaults for JSONB/nullable columns should use `_UNSET = object()` and check `if param is _UNSET: param = default`.

## Phase sequence

- Phase A ✅: schema, snapshot→journal creation, lifecycle transitions, outcome with R calc, eligibility gating, 61 tests green.
- Phase B: management timeline hooks (stop moves, BE moves, emergency flatten), close calculations from managed-trade paths, trade detail UI.
- Phase C: review workflow, override comparison, screenshots.
- Phase D: analytics, calendar, playbook reading from native_journal.
- Phase E: Tradzella enrichment, bulk migration.
