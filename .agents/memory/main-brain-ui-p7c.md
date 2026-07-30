---
name: Main Brain Operator Console (Phase 7C)
description: React page at /main-brain; read-only polling console; auth + normalizer architecture; test file locations
---

## Architecture

- Page: `artifacts/home/src/pages/MainBrain.tsx`
- Normalizer (pure, testable): `artifacts/home/src/lib/mainBrainNormalizer.ts`
  - Exports: `safeStr`, `safeNum`, `extractAvail`, `mapStrategyResult`, `normalizeMainBrainPayload`
  - Zero React dependencies — runs with `npx tsx`
- `normalizeMainBrainPayload()` is the single bridge between live payload and all panels
- Auth: localStorage `brain_auth`, 7s poll, Basic Auth header
- Pre-existing TS errors on `Sentinel`/`MobileHome` — not regressions

## Test suites

| File | Runner | Checks | Covers |
|------|--------|--------|--------|
| `test_phase7b_main_brain_route.py` | python3 | 56 | Route + builder |
| `test_phase7c_main_brain_ui.py` | python3 | 103 | UI contracts |
| `test_phase7c1_main_brain_population.py` | python3 | 123 | Population wiring |
| `test_phase7c3_transparency_contracts.py` | python3 | 66 | Backend passthrough + truthfulness |
| `test_phase7c3_normalizer.ts` | npx tsx (from artifacts/home/) | 214 | Normalizer contract |

## Phase 7C.2 transparency fields (verdict)

`_mb_verdict` passes through four extra fields from `edge_breakdown`:
- `edge_components` — list of `{key, label, points, present}` for ✓/✗ display
- `score_breakdown` — list of items that contributed points
- `failed_confirmations` — labels of absent components
- `risks` — risk flag strings

**Defect fixed in 7C.3**: error fallback dict now includes all four keys (they were missing, causing KeyError if the error path was triggered).

## Normalizer key mappings (Phase 7C.1)

- `active_trades` bare array → `{available, trades}` wrapper
- `alerts` bare array → `{available, items}` with `ts→timestamp`, `ticker→instrument` (strips `1!`)
- `left_brain.thesis.*` flattened to `lb.*`; `thesis.strength` → `momentum`
- `market.session.status` → `market.session_status`; `selected_instrument` → `instrument`
- `market_state.regime` (object `{regime, reason}`) → string extracted
- `strategy_scanner.selected` → `selected_strategy`; `ranked_strategies` → `strategies`
- `strategy_scanner.strategy_key` → `key` alias; `label` → `name` alias
- `verdict.grade` → `edge_grade` alias
- `raw.voice` (dict `{narration, headline}`) → string extracted as `main_brain.voice`
- `availability.*` `{available:bool}` objects → plain booleans
- `availability.timeline` → `availability.decision_timeline`
- `journal.summary.*` flattened to `today_*`; `win_rate` scaled ×100; `recent_trades` → `recent_closed`
- Timeline events: `ts` → `timestamp`, `label` → `event_label`

## _mb_system_status reads from globals (not result dict)

`db_ready` ← `LEARNING_DB_ENABLED` global  
`databento_ready` ← `os.environ["DATABENTO_ENABLED"]`  
`broker_ready` ← `os.environ["TRADERSPOST_WEBHOOK_URL"]`

Tests must use `mock.patch.dict(os.environ, ...)` to control env values.

**Why:** The function was written to expose runtime state, not to transform a result dict. Tests that pass a `result` dict with `database_ready=False` will still see the global value.
