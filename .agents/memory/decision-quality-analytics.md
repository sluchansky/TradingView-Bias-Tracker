---
name: Decision Quality analytics (Phase 5F)
description: DB-backed decision snapshot system — capture at READY, resolve at trade close, report component win rates and recommendations.
---

## Key design decisions

**Snapshot capture dedup**: `_DQ_PENDING_BY_INST[inst]` stores the last snapshot_key per instrument. `_capture_decision_snapshot` skips if `_existing.startswith(f"{inst}::{direction}::")` — prevents per-heartbeat duplicate rows for a stable READY setup.

**Why:** full_analysis runs every ~3s on the heartbeat. Without dedup, each stable READY verdict generates hundreds of rows per session.

**How to apply:** When the direction OR instrument changes, the startswith check fails → new snapshot captures. When trade closes and `_DQ_PENDING_BY_INST.pop(inst)` clears the slot, the next READY for that inst also captures fresh.

## Hook locations

- **Capture**: in `full_analysis` immediately after the TFA record-ready block (`logger.debug("TFA record-ready fail-open: %s", _tfa_exc)`).
- **Resolve**: in the managed-trade closure path immediately after TFA complete block (`logger.debug("TFA complete fail-open: %s", _tfa_exc)`).
- **Boot probe call**: after `_check_market_state_cache_db_ready()` in the boot section (~line 62860).

## Component performance computation

Resolves to Python after SELECT (JSONB returned as string by psycopg2 in some configs → always call `json.loads()` if isinstance str). Component "present" = `points > 0` in components JSONB array. Absent = not in present set for that row. Min 5 samples before surfacing delta.

## Invariants

- `DQ_DB_READY=False` at boot → all three functions are no-ops (byte-identical to baseline).
- No in-app DDL — table created via database tool (`decision_snapshots`).
- UPDATE only on `WHERE snapshot_key=%s AND outcome IS NULL` — never overwrites a resolved snapshot.
- Only managed-trade closes (bot-executed + ENTER) get outcomes attached. Manual journal entries via `_update_journal_outcome` do NOT get resolved (acceptable gap — analytics still valid for bot-traded setups).
