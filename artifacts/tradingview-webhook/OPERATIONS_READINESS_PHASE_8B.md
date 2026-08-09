# OPERATIONS READINESS — Phase 8B
## Pre-Market Validation & Observability

**Status:** Complete  
**Market open:** tomorrow  
**Philosophy:** Visibility only — zero changes to strategy logic, risk, execution, learning, promotion, ghost calculations, or edge calculations.

---

## Files Changed

| File | Change |
|---|---|
| `artifacts/tradingview-webhook/app.py` | +~400 lines: research events ring buffer, 7 hook injections, 2 Flask routes, UI panel HTML, JS functions |
| `artifacts/api-server/src/routes/flask-proxy.ts` | Added `/research-health` and `/research-events` to BOT1_ROUTES proxy whitelist |
| `artifacts/tradingview-webhook/tests/test_phase8b_ops_readiness.py` | New — 31 tests (30 unit + 4 golden subtests) |
| `artifacts/tradingview-webhook/OPERATIONS_READINESS_PHASE_8B.md` | This document |

---

## New Endpoints

### `GET /research-health`
Operations Readiness health snapshot. Returns:
- **Ghost Engine**: `table_ready`, `callback_registered`, `counts` (open/closed/total), `last_created_at`, `duplicate_obs_keys`
- **Edge Ledger**: `table_ready`, `total_rows`, `last_created_at`, `last_updated_at`, `duplicate_edge_ids`, `duplicate_ghost_obs_keys`, `duplicate_journal_links`
- **Databento**: `enabled`
- **First-observation validation**: `obs_key`, `checks` (exactly_one_obs, exactly_one_ledger_row, frozen_values_populated, matching_instrument, matching_strategy), `status` (PASS/FAIL), `failed_checks`
- **Timing metrics**: `signal_to_ghost_ms`, `signal_to_ledger_ms`, `signal_to_resolved_ms` for the most recent closed observation
- **Operator summary**: `ready_for_market` (boolean), `duplicate_event_count`, `event_count`, `error_count`
- **Boot info**: `boot_ts`, `first_obs_key`

Auth: Express proxy (owner-only). NOT in OPEN_PATHS.

### `GET /research-events`
Live event feed from in-memory ring buffer (newest first, max 500).
Query params: `limit` (1-500, default 100), `inst` (e.g. MNQ), `event_type`.

Event types emitted:
| Event | Trigger |
|---|---|
| `ghost_created` | `_ghost_observe_setup` after ghost_obs INSERT succeeds |
| `el_created` | `_el_create_entry` after edge_ledger INSERT succeeds |
| `tp1_hit` | `_ghost_obs_watcher_cycle` TP1 partial path |
| `{status}` (closed/expired/stop_hit) | `_ghost_obs_watcher_cycle` observation close path |
| `journal_linked` | `_el_try_link_to_journal` after NJ link succeeds |
| `managed_outcome_updated` | `_el_update_managed_outcome` after NJ close |

All 7 hook points are FAIL-OPEN: any exception is silently suppressed, never blocking any money path.

Auth: Express proxy (owner-only). NOT in OPEN_PATHS.

---

## New UI

### Research Engine Health Panel (`#mod-research-ops`)
Location: Research tab, between Edge Ledger panel and Real Historical Baseline panel.

Sections rendered by `rehLoad()` / `rehRender()` polling `/research-health` every 30s:

**Section 1 — System Health**
- DATA: Databento enabled status + last event ts
- GHOST ENGINE: Table ready, callback registered, open/closed counts
- EDGE LEDGER: Table ready, total rows, last created/updated timestamps
- COUNTS: Open ghost obs, closed ghost obs, edge ledger rows

**Section 2 — Live Event Feed**
Rendered by `revLoad()` / `revRender()` polling `/research-events`. Newest first, max 100 shown. Filterable by instrument and event type. Click any row to open the Observation Inspector.

**Section 3 — Observation Inspector**
Click any event row with an `obs_key` to inspect the frozen signal terms, current status, MFE/MAE, gross/net R, costs, signal outcome, managed outcome, management delta, exit reason, and journal link. Read-only.

**Section 4 — Duplicate Detection**
Shown as a red-highlighted block when `duplicate_event_count > 0`. Lists all duplicate obs_keys, edge_ids, ghost_obs linkages, and journal links. No automatic repair.

**Section 5 — First Signal Checklist**
Auto-populates when `first_obs_key` is set. Shows PASS/FAIL for each validation check. Highlights failed checks individually.

**Section 6 — Edge Ledger Monitor**
Newest ledger entry, newest closed observation, newest managed outcome, newest journal link, newest management delta, newest sample count.

**Section 7 — Timing Metrics**
`signal_to_ghost_ms`, `signal_to_ledger_ms`, `signal_to_resolved_ms` for the most recently closed observation.

**Section 8 — Error Panel**
`error_count` + any `db_error` fields. No stack traces — readable summaries only.

**Section 9 — Operator Summary Card**
Compact grid: Ghost Engine Running, Edge Ledger Healthy, Today's Ghost Trades, Today's Closed, Research Errors, Duplicate Events, Ready For Market (YES/NO).

---

## New Diagnostics

### Research Events Ring Buffer
```python
_RESEARCH_EVENTS: deque(maxlen=500)   # newest-first (appendleft)
_RESEARCH_BOOT_TS: str                # ISO timestamp of last server restart
_RESEARCH_FIRST_OBS_KEY: str | None   # first ghost obs key since last restart
_RESEARCH_ERROR_COUNT: int            # count of research-path errors
```

`_re_event(event_type, *, inst, strategy, verdict, obs_key, net_r, extra)` — FAIL-OPEN helper. Called from 7 hook points, each with its own try/except.

### Boot log
```
INFO:__main__:EdgeLedger: edge_ledger table ready (Phase 8A)
```
`EL_DB_READY = True` → research-health will report `edge_ledger.table_ready: true`.

---

## Tests

**File:** `tests/test_phase8b_ops_readiness.py`  
**Count:** 31 tests (30 unit + 4 golden regression subtests)

| Class | Tests | What is covered |
|---|---|---|
| `TestEventRingBuffer` | 7 | Newest-first ordering, maxlen 500 cap, wrap behaviour, required fields, fail-open on bad net_r, strategy/key truncation |
| `TestFirstObservationTracking` | 5 | First obs key captured, not overwritten, non-ghost events don't set it, PASS/FAIL validation dicts |
| `TestDuplicateDetection` | 6 | No-dup clean, single dup, multiple dups, ready_for_market=False on dups, True when clean, False when EL not ready |
| `TestHealthCalculations` | 5 | Counts aggregation, timing ms, None handling, event count, error count default |
| `TestObservationInspector` | 4 | Required fields present, frozen entry immutable, journal link field, management delta field |
| `TestEdgeLedgerModuleRegression` | 3 | build_edge_id format, assign_sample_partition, compute_el_diagnostics key names |
| `TestPhase8BRegression` | 1+4 | All 4 golden suites: PARITY, SCALP_GOLDEN, DUAL_SIM, BREAKOUT_MODE |

---

## Regression Results

All 4 golden suites pass unchanged:
```
PARITY OK (registry/resolver identical to baseline)
SCALP GOLDEN OK (byte-identical to baseline)
DUAL-SIM SMOKE OK (+ served dashboard <script> node-check)
BREAKOUT MODE SMOKE OK (+ served dashboard <script> node-check)
```

Phase 8A edge ledger tests: 55/55 pass.

---

## Expected Operator Workflow: Market Open → First Completed Observation

### Pre-market (before 9:30 ET)

1. Open dashboard → Research tab → **Research Engine Health** panel
2. Confirm **Operator Summary**:
   - Ghost Engine: Running ✅
   - Edge Ledger: Healthy ✅
   - Ready For Market: **YES**
3. If any red status → check Error Panel for db_error or duplicate flags

### 9:29 ET — Market opens (ORB window begins)

4. No signal yet. Event feed shows no `ghost_created` events since boot.
5. `Today's Ghost Trades: 0` in Operator Summary.

### ~9:31 ET — First MNQ READY signal

6. Within seconds, Event Feed shows:
   ```
   09:31:02  MNQ  ORB  READY  ghost_created  obs_key: el|obs_MNQ_…
   09:31:02  MNQ  ORB  ——     el_created     edge_id: el|obs_MNQ_…
   ```
7. **First Signal Checklist** auto-runs:
   - Exactly one observation? ✅
   - Exactly one ledger row? ✅
   - Matching strategy? ✅
   - Matching instrument? ✅
   - Frozen values populated? ✅
   - Status: **"First observation validated."**

8. Click the event row → **Observation Inspector** shows:
   - Frozen Entry / Stop / Targets
   - Signal Time
   - Status: `open`
   - MFE/MAE: accumulating
   - Net R: pending

### ~9:36 ET — TP1 Hit (two-leg mode)

9. Event Feed appends:
   ```
   09:36:41  MNQ  ORB  ——  tp1_hit  exit_px: 21150.0
   ```

### ~9:42 ET — Observation Closes

10. Event Feed appends:
    ```
    09:42:15  MNQ  ORB  ——  closed  Net +1.37R
    ```
11. **Timing Metrics** panel updates:
    - `signal_to_ghost_ms`: ~100–500ms (processing latency)
    - `signal_to_ledger_ms`: ~100–600ms
    - `signal_to_resolved_ms`: minutes (bar-resolution time)

12. If a live trade was taken via native_journal:
    ```
    09:42:18  journal_linked  iid: abc12345
    09:42:20  managed_outcome_updated  Net +1.24R
    ```
13. **Edge Ledger Monitor** shows newest comparison: `Management Delta: -0.13R` (management slightly hurt vs signal)

### Throughout the day

14. Event Feed accumulates up to 500 events (ring buffer), newest first.
15. Refresh every 30s automatically; manual refresh via ↻ button.
16. Duplicate Detection remains at 0 (green silence = healthy).
17. Error Panel remains at 0.

---

## Architecture Decisions

- **Ring buffer** (`deque(maxlen=500)`) is in-memory, resets on restart. Designed for same-session observability only — persistent history lives in ghost_observations and edge_ledger tables.
- **All hooks are FAIL-OPEN**: wrapped in try/except, never block the calling function, never touch gate/scoring/execution.
- **No new DB writes from Phase 8B**: all new Phase 8B code is read-only at the DB layer (ring buffer is in-memory; health endpoint only SELECTs).
- **Auto-poll interval**: 30s for health panel, 10s for event feed (configurable in JS).
- **Inspector fetch**: each obs_key click fetches from `/profitability/observations?obs_key=...` (existing endpoint).
