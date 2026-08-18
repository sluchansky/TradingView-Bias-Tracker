---
name: SCALP Feedback Loop Repair
description: Root causes and fixes for the 6 SCALP measurement pipeline failures; schema patch, test file, new API routes.
---

## Root Causes Fixed

### Phase 1 — ghost_observe_setup never called on webhook READY verdicts
`_ghost_observe_setup()` was only called from the Databento bar-close scan path (~line 32235). Webhook-triggered READY verdicts (all 85 in the 7-day window) never reached it. Fix: added a fail-open call in `_process_webhook_alert()` right after `eval_finished_at = now_utc()`, wrapped in try/except, gated on `is_actionable(a.get("verdict"))`.

### Phase 2 — ALLOWED records stuck as PENDING forever (no geometry)
The outcome watcher's WHERE clause requires `entry_price IS NOT NULL AND stop_price IS NOT NULL AND target1_price IS NOT NULL`. SCALP ALLOWED records often have no trade_plan geometry. Fix: when gate_verdict is ALLOWED/EARLY_ALLOWED and geometry is absent, synthesise ATR bracket (same as BLOCKED counterfactual logic). New geometry_source label: `LIVE_PLAN_ATR_FILL` to distinguish from `LIVE_PLAN` (real geometry) and `ATR_FALLBACK` (blocked counterfactual).

### Phase 4 — vwap_value captured from wrong key
`gate_effectiveness._extract()` used `result.get("vwap")` — wrong key. The scalar VWAP price in full_analysis is `result["vwap_value"]`. Fix applied at both IT-native and SCALP/SWING code paths: `result.get("vwap_value") or result.get("vwap")`.

### Phase 4 — trend_alignment always None
`result.get("trend_alignment")` returns None; the value lives at `result.get("swing_context", {}).get("trend_alignment")` (only populated when `_swing_htf_enabled()`). Fix: fall back to swing_context.

### Phase 5 — strategy always "SCALP" (not sub-strategy)
`_extract_strategy()` returned the bare mode name for SCALP/SWING. The actual sub-strategy (`LIQUIDITY_SWEEP_REVERSAL`, `VWAP_PULLBACK_CONTINUATION`, etc.) lives at `result["strategy_engine"]["active_key"]`. Fix applied with ORB/IT precedence preserved; fallback to mode name when key is absent or "None" string.

### Phase 7 — shadow cohort classification
New `_classify_shadow_cohort()` assigns one of three research cohorts to BLOCKED SCALP records:
- `EDGE35_OTHER_GATES_PASS` — edge (≥30 score) is the SOLE blocker; all others passed
- `VOLUME_ONLY_BLOCK_1030_1200` — volume is the sole blocker in the 10:30–12:00 ET window
- `SHORT_CVD_ONLY_BLOCK` — Short direction, CVD is the sole blocker
ALLOWED records never classified. Non-SCALP modes never classified.

### Phase 9 — session_bucket
New `_et_session_bucket()` produces a canonical ET time-of-day label (9 buckets) stored at record time so all reports agree.

## Schema Patch
`db_scalp_feedback_schema_patch.sql` adds to `gate_audit_log`:
- `geometry_source TEXT` (already existed in live DB)
- `shadow_cohort TEXT`
- `session_bucket TEXT`

**CRITICAL**: When applying via the executeSql callback, split and run each ALTER TABLE statement individually. Do NOT split the entire file by `;` and filter by `!s.startsWith('--')` — comment blocks before each ALTER get grouped with it and filtered out, silently skipping the ALTER.

## ghost_observations timestamp column
The `ghost_observations` table uses `signal_time` for its timestamp, NOT `ts`. Any query filtering by recency must use `signal_time >= NOW() - INTERVAL '...'`.

## New API Routes (Phase 10)
- `GET /gate-effectiveness/scalp-feedback-health` — pipeline health snapshot (breakdown, vwap coverage, strategy identity coverage, ghost_webhook_7d)
- `GET /gate-effectiveness/shadow-cohorts?days=N` — per-cohort win-rate analytics
Both registered in proxy whitelist at `artifacts/api-server/src/routes/flask-proxy.ts`.

## Test File
`artifacts/tradingview-webhook/tests/test_scalp_feedback_loop.py` — 52 tests, Phases 2/3/4/5/7/9/12.

## INSERT param count after changes
`gate_audit_log` INSERT now has **39 params**: 37 original + geometry_source + shadow_cohort + session_bucket.

**Why:** Kept as explicit count in test_shadow_cohort_in_insert_params so future column additions are caught immediately by test failures.
