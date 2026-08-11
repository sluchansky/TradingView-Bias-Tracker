---
name: Ghost Research Engine Phase 2
description: Phase 2 shadow experiment platform — OrbEngine BREAKOUT_DETECTED observer, 10 variant families, bootstrap CI, Monte Carlo drawdown, evidence state machine.
---

## Architecture

**Engine file:** `artifacts/tradingview-webhook/ghost_research_engine.py`  
**Test file:** `artifacts/tradingview-webhook/test_ghost_research_engine.py` (118 tests)  
**DB tables:** `ghost_opportunities`, `ghost_experiments`, `ghost_experiment_results` (created via DB tool; no DDL in app code)

## Integration points in app.py

1. `GRE_DB_READY = False` global (near other `*_DB_READY` flags)
2. `_check_gre_db_ready()` — boot probe, probes all 3 tables, sets `GRE_DB_READY`
3. Boot sequence: `_check_gre_db_ready()` called after `_check_ghost_obs_db_ready()`
4. `_orb_bar_close(inst, price)` — after OrbEngine call, also calls `_gre.on_bar_close(inst, orb_status, price)` via `_ORB_ENGINE.get_instrument_status(inst)`; both wrapped in try/except fail-open
5. GRE boot block after OrbEngine boot: imports `ghost_research_engine`, constructs `GhostResearchEngine(get_db_fn, get_canonical_fn, get_bars_fn, re_event_fn, instruments)`, calls `.boot()`
6. Flask routes (all in proxy whitelist): `/ghost-research/health`, `/ghost-research/candidates`, `/ghost-research/experiments`, `/ghost-research/candidate/<experiment_id>`, `/ghost-research/opportunity/<opportunity_id>`, `/ghost-research/baseline-vs-variant`, `/ghost-research/ready-for-review`
7. `/research-health` response extended with `ghost_research_engine` key from `gre.get_health()`

## 10 variant families

BASELINE, TOUCH, CLOSE_AND_RETEST, BUFFER_PLUS_2, BUFFER_MINUS_2, TP_1R, TP_1_5R, TP_2R, TREND_REQUIRED, CVD_ALIGNED

## Frontend integration

- `globalAlerts.ts`: `RESEARCH_READY_FOR_REVIEW` added to `AlertType`
- `GlobalAlertDock.tsx`: 30s poller for `/ghost-research/ready-for-review`; `ResearchDetail` expand component; `RESEARCH_POLL_MS = 30_000`; sound plays `SCAN_FOUND` on new research alert; dedup via `seenResearchRef` (session-only)

## Key design decisions

- **Trigger:** `BREAKOUT_DETECTED` (pre-filter), not `QUALIFIED` — rejected setups also researched
- **Hook:** No callback in OrbEngine; hook added to `_orb_bar_close()` after engine call
- **Monte Carlo convention:** `p95_dd` = 95th percentile worst case (MORE negative than median)
- **Evidence non-regression:** RETIRED/REJECTED/READY_FOR_REVIEW/VALIDATING protected — guard runs BEFORE sample-count thresholds
- **App code:** Zero DDL; boot does no-DDL probe; `GRE_DB_READY = False` keeps it fully fail-open

**Why:**
- Completely isolated from gate/scoring/execution — NEVER a money path
- All findings surface as `READY_FOR_REVIEW` alerts requiring deliberate human action
- Fail-open at every call site; DB errors log at DEBUG and return gracefully
