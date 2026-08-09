---
name: Ops Readiness Phase 8B
description: Research event ring-buffer, 7 fail-open hooks, two Flask routes, dashboard panel — display-only observability for ghost obs + edge ledger.
---

# Operations Readiness Phase 8B

## Rule
`_re_event()` is the single FAIL-OPEN helper that appends to `_RESEARCH_EVENTS` (deque maxlen=500, appendleft → newest-first). It is called from 7 hook points — all wrapped in try/except, never blocking any money path.

## Hook points (all FAIL-OPEN)
1. `_ghost_observe_setup` — after `_el_create_entry()` call → event_type `"ghost_created"`
2. `_el_create_entry` — after logger.debug (inside outer try) → event_type `"el_created"`
3. `_ghost_obs_watcher_cycle` TP1 partial path — after logger.info → event_type `"tp1_hit"`
4. `_ghost_obs_watcher_cycle` close path — after `_el_update_signal_outcome_conn()` → event_type = status string (e.g. `"closed"`)
5. `_el_try_link_to_journal` — after logger.debug (inside try) → event_type `"journal_linked"`
6. `_el_update_managed_outcome` — after logger.debug (inside try) → event_type `"managed_outcome_updated"`
7. *(Note: `_el_update_signal_outcome_conn` has NO separate hook — its event fires via hook #4 in the watcher)*

## Flask routes
- `GET /research-health` — DB queries for ghost_obs/EL counts, duplicate detection, first-obs validation, timing metrics, ready_for_market flag
- `GET /research-events` — returns list(_RESEARCH_EVENTS) slice; query params: limit, inst, event_type

Both in BOT1_ROUTES proxy whitelist. NOT in OPEN_PATHS.

## UI panel
`#mod-research-ops` — Research tab, before `#mod-baseline`. JS functions: `rehLoad()`, `rehRender()`, `revLoad()`, `revRenderEvents()`, `rehInspect(obsKey)`, `rehCloseInspector()`. Auto-polls every 30s.

### JS-in-Python backslash trap (critical lesson)
`\'` inside a Python triple-quoted string → `'` in the output JS (backslash consumed). This breaks JS string literals silently. The dual_sim and breakout_mode golden suites catch this via `node --check` on the served dashboard `<script>`.
**Fix**: Use data-attribute pattern (`data-ok="..."`) + delegated click handler instead of inline `onclick='rehInspect(...)'`. The `data-ok` approach avoids all inline quoting.

## Module-level Optional annotation trap
`_RESEARCH_FIRST_OBS_KEY: Optional[str] = None` at module level fails at import time if `Optional` is not imported at that scope. Fix: remove the type annotation, use a comment instead: `= None  # str | None`.

## Tests
`tests/test_phase8b_ops_readiness.py` — 31 unit tests + 4 golden subtests (35 total).
Key test classes: TestEventRingBuffer, TestFirstObservationTracking, TestDuplicateDetection, TestHealthCalculations, TestObservationInspector, TestEdgeLedgerModuleRegression, TestPhase8BRegression.

## Deliverable
`OPERATIONS_READINESS_PHASE_8B.md` — filed in `artifacts/tradingview-webhook/`.
