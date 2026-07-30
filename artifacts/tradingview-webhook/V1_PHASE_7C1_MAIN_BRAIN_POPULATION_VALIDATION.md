# V1 Phase 7C.1 — Main Brain Population Audit & Wiring

**Status:** COMPLETE  
**Date:** 2026-07-30  
**Commit message:** `V1-P7C1 Main Brain population wiring`

---

## Summary

Audited every field on the Main Brain operator console (`/main-brain`) against the live `/main-brain` payload and fixed all schema mismatches. No trading logic was changed. No deployment.

---

## Root Causes Fixed

### Frontend path mismatches (29 fixes) — `MainBrain.tsx`

A single `normalizeMainBrainPayload()` adapter function is called once when data arrives (inside `useMainBrain`). It maps all canonical payload paths to the UI-expected paths without computing trading values or inventing defaults.

| Section | Payload schema | UI expected | Fix |
|---|---|---|---|
| Market | `session.status` (nested) | `session_status` | Flatten |
| Market | `selected_instrument` | `instrument` | Rename |
| Market state | `regime: {regime, reason}` (object) | `regime: string` | Extract `.regime` |
| Left Brain | `thesis.direction` / `.confidence` / `.narrative` / `.status` / `.strength` / `.generated_at` (all nested) | flat `lb.direction` etc. | Flatten thesis |
| Left Brain | `thesis.strength` | `lb.momentum` | Rename |
| Verdict | `grade` | `edge_grade` | Add alias |
| Strategy Scanner | `selected` | `selected_strategy` | Rename |
| Strategy Scanner | `ranked_strategies` | `strategies` | Rename |
| Strategy Scanner | top-level `entry/stop/targets/risk_reward` | `trade_plan{}` | Build wrapper |
| Strategy items | `strategy_key` | `key` | Alias |
| Strategy items | `label` | `name` | Alias |
| Strategy items | `result` (`"ready"/"skipped"/"no_signal"`) | `readiness` (human) | Map |
| Strategy items | `eligible + skip_reason` | `mode_compatible` | Derive |
| Active trades | bare `[]` | `{available, trades:[]}` | Wrap |
| Active trades | `contracts` | `quantity` | Alias |
| Alerts | bare `[]` | `{available, items:[]}` | Wrap |
| Alert items | `ts` | `timestamp` | Rename |
| Alert items | `ticker` (e.g. `MGC1!`) | `instrument` | Rename + strip `1!` |
| Alert items | `alert_type` | `message` | Rename |
| Journal | `recent_trades` | `recent_closed` | Rename |
| Journal | `summary.total_trades` | `today_count` | Flatten |
| Journal | `summary.win_rate` (0–1) | `today_win_rate` (0–100) | Flatten + ×100 |
| Journal | `summary.avg_r` | `today_avg_r` | Flatten |
| Journal items | `symbol` | `instrument` | Rename |
| Journal items | `strategy` | `setup` | Rename |
| System status | `database_ready` | `db_ready` | Added alias **in backend** |
| System status | `databento_connected` | `databento_ready` | Added alias **in backend** |
| System status | `broker_url_configured` | `broker_ready` | Added alias **in backend** |
| Performance | `sample` | `trade_count` | Add alias |
| Availability | `{available: bool}` (nested) | `bool` | Extract |
| Availability | `timeline` key | `decision_timeline` | Rename |
| Timeline events | `ts` | `timestamp` | Rename |
| Timeline events | `label` | `event_label` | Rename |
| Timeline events | `event_type` | `source` | Add alias |
| Main Brain voice | `raw.voice` (dict `{narration, headline}`) | `main_brain.voice` (string) | Extract narration |

### CoachPanel fix
`weight_updated` is a **boolean** (`true`/`false`) in the payload. The previous code passed it to `fmtTs()` (which returned `"—"` for a boolean). Now renders as `YES` / `NO` with the correct status dot.

### SystemHealthPanel fix
`availability.*` values are objects `{available: true}`, not booleans. Previous `!== false` check worked accidentally for truthy objects but is now replaced with the `extractAvail()` helper in the normalizer, giving proper `boolean` values.

---

## Backend Additions (read-only, additive-only)

| Addition | Location | Description |
|---|---|---|
| `voice` field | `build_main_brain_payload()` | Reads `result["main_brain_voice"]` (already computed in `full_analysis`) — no new computation |
| `gateway_status` | `_mb_execution_gateway()` | Derived: `"SENT"` if `last_sent_at` is non-null, else `"IDLE"` |
| `db_ready` alias | `_mb_system_status()` | `bool(LEARNING_DB_ENABLED)` — mirrors `database_ready` |
| `databento_ready` alias | `_mb_system_status()` | Mirrors `databento_connected` |
| `broker_ready` alias | `_mb_system_status()` | Mirrors `broker_url_configured` |

**Money path:** unchanged. All additions are read-only passthroughs or display-only derivations.

---

## Files Changed

| File | Change |
|---|---|
| `artifacts/home/src/pages/MainBrain.tsx` | Added `normalizeMainBrainPayload()` (180 LOC); wired into `useMainBrain`; fixed `CoachPanel` weight_updated display; fixed `SystemHealthPanel` boolean extraction |
| `artifacts/tradingview-webhook/app.py` | `build_main_brain_payload`: added `voice` passthrough; `_mb_execution_gateway`: added `gateway_status`; `_mb_system_status`: added `db_ready`, `databento_ready`, `broker_ready` aliases |
| `artifacts/tradingview-webhook/test_phase7c1_main_brain_population.py` | New test suite (123 checks) |
| `artifacts/tradingview-webhook/V1_PHASE_7C1_MAIN_BRAIN_POPULATION_VALIDATION.md` | This document |

---

## Test Results

```
TOTAL: 123 checks — 123 passed, 0 failed
PASS  all Phase 7C.1 main-brain-population checks passed
```

### Regression suites
```
Phase 7C UI:   103/103  PASS
Phase 7B Route:  56/56  PASS
```

### Test class coverage

| Class | Checks | What it tests |
|---|---|---|
| TC001_BackendPayloadSchema | 001–020 | Live schema fixture matches documented structure (raw payload) |
| TC002_BackendAdditions | 021–029 | New backend fields: voice, gateway_status, db/databento/broker aliases |
| TC003_NormalizerMarket | 030–033 | session_status, instrument, trading_mode, execution_mode |
| TC004_NormalizerMarketState | 034–036 | Regime string extraction from nested object |
| TC005_NormalizerLeftBrain | 037–044 | Thesis flattening including null-thesis safety |
| TC006_NormalizerVerdict | 045–048 | edge_grade alias and score preservation |
| TC007_NormalizerStrategyScanner | 049–060 | selected_strategy, strategies normalization, trade_plan construction, readiness mapping |
| TC008_NormalizerActiveTrades | 061–063 | Bare array → {available, trades} wrap, quantity alias |
| TC009_NormalizerAlerts | 064–068 | Bare array wrap, timestamp/instrument/message/severity renames |
| TC010_NormalizerJournal | 069–076 | summary stats extraction, win_rate ×100, empty journal safety |
| TC011_NormalizerSystemStatus | 077–080 | db_ready, databento_ready, broker_ready, learning_ready |
| TC012_NormalizerPerformance | 081–083 | trade_count alias, zero sample preservation |
| TC013_NormalizerAvailability | 084–088 | Object → boolean extraction, timeline key rename |
| TC014_NormalizerDecisionTimeline | 089–093 | Event timestamp/event_label/source normalization |
| TC015_NormalizerMainBrainVoice | 094–097 | Dict voice narration extraction, string/None/missing safety |
| TC016_EmptyStateBehavior | 098–104 | Null rates not fabricated as 0; empty arrays preserved |
| TC017_NonMutationGuards | 105–109 | No broker/journal writes in builder; original data unchanged |
| TC018_RegressionP7B | 110–116 | Phase 7B contract preserved (deferred markers, mode, version) |
| TC019_SectionFailureIsolation | 117–121 | Bad sections don't crash others |
| TC020_StaleAndAuthStates | 122–123 | generated_at present, availability dict fully populated |

---

## Invariants

1. **Display-only**: normalizer never computes edge scores, entries, stops, or trade decisions.
2. **Fail-open availability**: absent `availability.*` keys resolve to `true` (not `false`).
3. **Null preservation**: `null` rates and null entries are never replaced with `0` or `"—"` by the normalizer — downstream UI components handle display of null.
4. **Boolean coach field**: `weight_updated` is always rendered as YES/NO, never as a timestamp.
5. **No double-count**: `win_rate * 100` conversion happens only once (in normalizer), never in panel components.
6. **Voice extraction order**: dict → `narration` then `headline`; string → as-is; null/missing → null.
7. **Money path unchanged**: `execute_trade_gateway`, journal appends, and broker sends are untouched.
