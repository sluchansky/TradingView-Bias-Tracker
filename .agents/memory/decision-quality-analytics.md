---
name: Decision Quality analytics (Phase 5F / 5F.1 / 5F.1B)
description: DB-backed decision snapshot system — capture at READY, resolve at trade close, report component win rates and recommendations. Phase 5F.1 repaired two lifecycle bugs; Phase 5F.1B repaired SC2/SC3/SC4 association safety bugs.
---

## Phase 5F.1B repairs (July 2026) — SC2 / SC3 / SC4

**SC2 — unsafe first-match fallback** (`_resolve_decision_snapshot`)
Old code: if exact fingerprint missed, scanned all pending entries for any
matching `inst` (and optionally `direction`), taking the first hit. This
could attribute a Win/Loss to the wrong setup.
Fix: 3-level resolution hierarchy:
  - L1: exact `snapshot_key` stored on the trade (`mt["_dq_snapshot_key"]`)
  - L2: exact fingerprint reconstruction (inst + direction + _dq_mode)
  - L3: unmatched closure — increment `_DQ_UNMATCHED_CLOSURES`, log, return; NO DB write
First-match fallback is fully removed.

**SC3 — WAIT abandons an active-trade snapshot**
Old code: `full_analysis` WAIT branch called `_dq_abandon_setup(fp)` unconditionally.
If a trade is already open (e.g. SWING holding across sessions), the WAIT heartbeat
clears the pending DQ entry → trade close becomes an unmatched closure.
Fix: `if not _dq_has_active_trade(inst, direction): _dq_abandon_setup(fp)`.
When a trade is open the pending entry is preserved.

**SC4 — 240-min TTL expires active SWING trades**
Old code: `_dq_expire_old_entries()` evicted any entry older than 240 min.
SWING trades held overnight exceed this window.
Fix: before expiring, call `_dq_has_active_trade(entry["inst"], entry["direction"])`.
If active: increment `_DQ_PRESERVED_ACTIVE`, continue. If not: expire as before,
increment `_DQ_EXPIRED_UNTRADED`.

**SC1 (documented-only, not repaired)** — fingerprint is still `inst::direction::mode`
only; two distinct untraded setups on the same lane share a fingerprint. Future fix
would add strategy_key + rounded entry + stop to the fingerprint.

## New helpers (Phase 5F.1B)

- `_dq_has_active_trade(inst, direction)` — reads `ACTIVE_TRADES_BY_INST` under
  `ACTIVE_TRADES_LOCK`; returns False on exception; conservatively True when
  direction is empty.
- `_dq_attach_to_trade(inst, trade)` — called from `set_active_trade` after
  `_persist_active_trade`; looks up `_DQ_PENDING_BY_SETUP.get(fp)` and injects
  `_dq_snapshot_key`, `_dq_fingerprint`, `_dq_mode` onto the trade dict in-place.
  Boot-restore path uses direct assignment (no `set_active_trade`) so no attach
  there — that's intentional (no pending entry exists at boot).

## Lifecycle counters (Phase 5F.1B)

All five are module-level ints, reset in `teardown_module` and `_reset_dq_all()`:
- `_DQ_UNMATCHED_CLOSURES` — closures that found no match (L3 path).
- `_DQ_EXPIRED_UNTRADED`   — untraded candidates past TTL that were evicted.
- `_DQ_PRESERVED_ACTIVE`   — candidates skipped by expiry because trade is open.
- `_DQ_RESOLVED_COUNT`     — successful L1 or L2 resolutions.
- `_DQ_ABANDONED_COUNT`    — WAIT-path abandons (no active trade guard passed).

## Phase 5F.1 repairs (original — July 2026)

1. **DQ_DB_READY=False blocking bug** — pop always happens before DB call.
2. **WAIT-verdict stale-key bug** — WAIT path calls `_dq_abandon_setup(fp)` (now
   guarded by `_dq_has_active_trade` per SC3 fix above).

## Current data structure

`_DQ_PENDING_BY_SETUP` (dict):
- **Key**: fingerprint `f"{inst}::{direction}::{mode}"` via `_dq_fingerprint()`.
- **Value**: `{"snapshot_key": str, "inst": str, "direction": str, "mode": str, "created_at": datetime}`.

Supporting state:
- `_DQ_SETUP_TTL_MIN = 240` — TTL for orphaned (untraded) pending entries.
- `_DQ_MAX_PENDING = 100` — cap to prevent unbounded growth.

## Key helpers

- `_dq_fingerprint(inst, direction, mode)` — canonical fp builder.
- `_dq_expire_old_entries()` — called at capture time; active-trade entries exempt.
- `_dq_abandon_setup(fp)` — clears entry on WAIT when no active trade; increments
  `_DQ_ABANDONED_COUNT`.

## Snapshot capture dedup

`_capture_decision_snapshot` checks `fp in _DQ_PENDING_BY_SETUP`.
If present → skip INSERT (heartbeat dedup). If absent → INSERT and set entry.

## Resolve (pop-before-DB invariant)

Entry is always popped BEFORE the DB UPDATE attempt:
- DB failure → entry still cleared (no permanent block).
- DQ_DB_READY=False → entry still cleared.
- L3 unmatched → counter incremented, safe no-op, no DB write.

## Hook locations

- **Capture**: in `full_analysis` after TFA record-ready block (is_actionable gate).
- **Attach**: in `set_active_trade` after `_persist_active_trade`.
- **Resolve**: in managed-trade closure path after TFA complete block.
- **Abandon (WAIT)**: in `full_analysis` WAIT branch, guarded by `_dq_has_active_trade`.
- **Boot probe**: after `_check_market_state_cache_db_ready()`.

## Component performance computation

Resolves in Python after SELECT (JSONB may be returned as str → always `json.loads()`
if isinstance str). Component "present" = `points > 0`. Min 5 samples before surfacing.

## Invariants

- `DQ_DB_READY=False` → all functions are no-ops (byte-identical to baseline).
- No in-app DDL — table created via database tool (`decision_snapshots`).
- UPDATE only on `WHERE snapshot_key=%s AND outcome IS NULL`.
- Only bot-executed managed-trade closes get outcomes attached.
- Never touches the money path, gate, sizing, or any execution flag.

## Test file

`test_decision_quality.py` — 103 tests (73 existing + 30 new `test_1b_*` Section 10).
Key test helpers: `_fp()`, `_pending_entry()`, `_clear_pending()`, `_reset_dq_all()`.
Imports: `_dq_expire_old_entries`, `_dq_has_active_trade`, `_dq_attach_to_trade` added.
`teardown_module` resets all 5 lifecycle counters.
