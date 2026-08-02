---
name: Native Journal Phase B — Management Timeline
description: Management event capture, lifecycle linkage, outcome persistence, 5-section UI drawer, and test coverage for Phase 7K-B.
---

## What Phase B adds (all fail-open, additive)

### New frozensets (app.py, near _NJ_TERMINAL_OUTCOMES)
- `_NJ_VALID_EVENT_TYPES` (21 types) — validated on every `_nj_append_management_event` call; unknown types are logged and silently dropped.
- `_NJ_OVERRIDE_REASONS` (7 codes) — validated at `/close` route for manual override reason_code.

### Extended `_nj_append_management_event`
- 5 new params: `event_id`, `reason_code`, `price`, `quantity`, `metadata`.
- Canonical 12-field event shape (adds `event_id`, `price`, `quantity`, `reason_code`, `metadata`).
- Dedup path: when `event_id` is supplied, uses `NOT EXISTS` guard so idempotent re-callers cannot double-append.
- Invalid `event_type` → logger.debug + return (never raises).

### Updated `_nj_set_outcome`
- New `actual_exit` param merges into outcome block.
- **Idempotency guard**: rows already in `_NJ_TERMINAL_OUTCOMES` are silently skipped.
- SELECT now fetches 5 cols: `planned_entry, planned_stop, direction, created_at, lifecycle_status`.
- Computes `duration_seconds` from row's own `created_at`.
- Tracks `data_completeness.missing_fields` for `actual_exit/net_pnl/realized_r`.
- Uses `POSITION_CLOSED` event type (was `STATUS_CHANGE`).
- POSITION_CLOSED event has deterministic `event_id = f"{iid}:POSITION_CLOSED"`.
- SQL now has `AND lifecycle_status NOT IN ('CLOSED','REJECTED','CANCELED')` idempotency guard.

### `_capture_send_time_snapshot`
- Now **returns** the `internal_trade_id` string (or `None` on failure). Callers that don't use the return value are byte-identical.

### New helpers
- `_nj_pos_opened_events(iid, direction, entry, stop, t1, t2, contracts, source)` — transitions lifecycle to ACTIVE + appends STOP_PLACED + TARGET_PLACED (all idempotent via fixed event_id) + merges avg_entry into execution block.
- `_nj_link_managed_trade(inst, direction, iid)` — attaches iid to matching open MT in-memory dict (MANAGED_TRADES_BY_KEY). Pure in-memory, no DB.

### Phase B test mock fix (test_native_journal_phase_a.py)
- `_mock_db_row` now returns 5 values: `(planned_entry, planned_stop, direction, None, "ACTIVE")`.
- `None` for `created_at` skips duration calc; `"ACTIVE"` passes idempotency guard.

## Wire points (10 total)

| Wire point | Event appended | Notes |
|---|---|---|
| Paper-dynamic path (`_execute_auto_trade`) | pos-opened + link | After `_record_corr_entry`, before `return True` |
| Active trade slot (`_execute_auto_trade`) | pos-opened + link | After `set_active_trade`; attaches iid to `_trade` dict |
| `/breakeven` route | BREAK_EVEN_MOVE | operator, automated=False |
| `_maybe_move_be_to_entry` | BREAK_EVEN_MOVE | system_auto, reason_code=TP1_BREAK_EVEN, idempotent event_id |
| `_apply_swing_review_decision` MOVE_STOP | STOP_MOVED | system_auto, captures old stop |
| `/close` route | MANUAL_EXIT | operator, automated=False, reason_code validated, metadata={has_manual_override:True} |
| `_auto_exit_fire` | THESIS_INVALIDATION_EXIT | before `_update_journal_outcome` |
| Webhook T1/T2 hit | TARGET_HIT | before `_update_journal_outcome` |
| `_evaluate_dynamic_managed_levels` TP1 | PARTIAL_EXIT + PARTIALLY_CLOSED lifecycle | system_auto |
| `_close_managed_trade` | POSITION_CLOSED (via `_nj_set_outcome`) | **Critical gap fixed** — managed trade closes now close the NJ row |

## Detail endpoint enrichment (`nj_trade_detail`)
Server-derived fields added to response:
- `event_count`, `first_event_at`, `last_event_at`
- `has_manual_override` (any event where automated=False)
- `duration_seconds` (created_at → updated_at when CLOSED; None otherwise)
- `outcome_complete`, `missing_outcome_fields`
- `management_events` sorted chronologically

## UI (JNativeTradesTab in MainBrain.tsx)
Drawer expanded from 3 sections to 5 + header badge:
1. **Header** — adds MANUAL OVERRIDE amber badge + event count
2. **Trade Replay banner** — realized_r / net_pnl / exit price / duration with color-coded outcome (hidden when no data and not CLOSED)
3. **Planned by System** — unchanged
4. **Actual Execution** — richer (avg fill, source_mode, MISSING FILL DATA state)
5. **Management Timeline** — vertical event list with dot/line, type color, AUTO/MANUAL badges, old→new value, price, reason; empty state = "NO MANAGEMENT EVENTS YET"
6. **Final Outcome** — OUTCOME PENDING / PARTIAL EXECUTION DATA states; MAE/MFE/R/PnL/followed_plan
7. **Record & Learning** — adds first_event_at / last_event_at rows

New interfaces: `NJManagementEvent` (12-field canonical shape); `NJTradeDetail` extended with Phase B derived fields.

## Tests
- `test_native_journal_phase_b.py`: **56 tests**, 0 failures
- `test_native_journal_phase_a.py`: **61 tests**, 0 failures (after mock fix)
- `test_native_journal_api.py`: **25 tests**, 0 failures

**Why:** the critical gap before Phase B was that `_close_managed_trade` called `_apply_outcome_to_journal` (legacy) which did NOT call `_nj_close_by_instrument`, leaving managed trade NJ rows permanently SUBMITTED. Phase B closes the NJ row from `_close_managed_trade` directly.
