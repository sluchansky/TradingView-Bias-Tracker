---
name: Decision Quality analytics (Phase 5F / 5F.1)
description: DB-backed decision snapshot system — capture at READY, resolve at trade close, report component win rates and recommendations. Phase 5F.1 repaired two lifecycle bugs.
---

## Phase 5F.1 repairs (July 2026)

Two bugs fixed:

1. **DQ_DB_READY=False blocking bug** — old code did early-return BEFORE popping
   `_DQ_PENDING_BY_INST[inst]`, leaving a stale entry that permanently blocked
   future captures until restart. Fixed: pop always happens first, DB call is
   conditional after.

2. **WAIT-verdict stale-key bug** — heartbeat dedup skipped the INSERT for the
   next READY after a WAIT (no trade taken) because the same-direction key was
   still set. Fixed: `full_analysis` WAIT path now calls `_dq_abandon_setup(fp)`
   to clear the entry, so the next READY is captured fresh.

## Current data structure

`_DQ_PENDING_BY_SETUP` (dict) replaces the old `_DQ_PENDING_BY_INST`.

- **Key**: fingerprint string `f"{inst}::{direction}::{mode}"` via `_dq_fingerprint()`.
- **Value**: `{"snapshot_key": str, "inst": str, "direction": str, "mode": str, "created_at": datetime}`.
- Long/Short and SCALP/SWING are tracked at independent fingerprints — no overwrites.

Supporting state:
- `_DQ_SETUP_TTL_MIN = 240` — TTL for orphaned pending entries (expire helper).
- `_DQ_MAX_PENDING = 100` — cap to prevent unbounded growth.
- `_DQ_UNMATCHED_CLOSURES` (int) — counter of closures that found no matching entry.

## Key helpers

- `_dq_fingerprint(inst, direction, mode)` — canonical fp builder.
- `_dq_expire_old_entries()` — called at capture time; removes entries older than TTL.
- `_dq_abandon_setup(fp)` — called by full_analysis WAIT path; clears entry so next READY is captured fresh.

## Snapshot capture dedup

`_capture_decision_snapshot` checks `fp in _DQ_PENDING_BY_SETUP`. If present, skip INSERT (heartbeat dedup). If absent (first READY, or after close/abandon), INSERT and set entry.

**Why:** full_analysis runs every ~3s. Without dedup, each stable READY verdict generates hundreds of rows per session.

## Resolve (pop-before-DB invariant)

`_resolve_decision_snapshot` pops the entry **before** the DB UPDATE attempt. This means:
- DB failure → entry still cleared (no permanent block).
- DQ_DB_READY=False → entry still cleared (fixed behavior).
- Unmatched closure (no entry found) → increments `_DQ_UNMATCHED_CLOSURES`, safe no-op.

Fallback when `mt.get("direction")` is empty: scans all entries for matching `inst` (backward compat with callers that don't pass direction).

## Report keys

Both return paths of `_build_decision_quality_report()` now include:
- `"pending"` — `len(_DQ_PENDING_BY_SETUP)` at report time.
- `"unmatched_closures"` — count of closures with no matching pending entry.

## Hook locations

- **Capture**: in `full_analysis` immediately after TFA record-ready block.
- **Resolve**: in the managed-trade closure path immediately after TFA complete block.
- **Abandon (WAIT)**: in `full_analysis` WAIT branch, calls `_dq_abandon_setup(fp)`.
- **Boot probe**: after `_check_market_state_cache_db_ready()` in boot section.

## Component performance computation

Resolves to Python after SELECT (JSONB returned as string by psycopg2 in some configs → always call `json.loads()` if isinstance str). Component "present" = `points > 0` in components JSONB array. Absent = not in present set for that row. Min 5 samples before surfacing delta.

## Invariants

- `DQ_DB_READY=False` at boot → all three functions are no-ops (byte-identical to baseline).
- No in-app DDL — table created via database tool (`decision_snapshots`).
- UPDATE only on `WHERE snapshot_key=%s AND outcome IS NULL` — never overwrites a resolved snapshot.
- Only managed-trade closes (bot-executed + ENTER) get outcomes attached.

## Test file

`test_decision_quality.py` — 73 tests (45 existing updated + 30 new `test_5f1_*`).
Key test helpers: `_fp()`, `_pending_entry()`, `_clear_pending()`.
Iterator-exhaustion trap: `_dq_expire_old_entries()` calls `now_utc()` inside capture,
so sequential-capture tests must use separate `with patch(..., return_value=T)` blocks
rather than a `side_effect=iter([...])` iterator.
