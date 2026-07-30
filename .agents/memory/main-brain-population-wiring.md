---
name: Main Brain population wiring (Phase 7C.1)
description: Normalizer that bridges /main-brain payload schema mismatches to the UI-expected paths; backend additions; key schema quirks.
---

## Rule
`normalizeMainBrainPayload()` in `MainBrain.tsx` is the SINGLE bridge between the raw `/main-brain` payload and every panel in the console. Any new field consumed by a panel must be mapped there, not hardcoded in the panel.

**Why:** The backend schema uses canonical naming conventions (e.g. `database_ready`, `ranked_strategies`, `ts`) that differ from the UI-expected names (`db_ready`, `strategies`, `timestamp`). Fixing panel-by-panel creates drift; one normalizer = one audit point.

## Key schema quirks (as of 2026-07-30)

| Field | Raw payload shape | After normalize |
|---|---|---|
| `active_trades` | bare `[]` at top level | `{available:true, trades:[]}` |
| `alerts` | bare `[]` at top level | `{available:true, items:[...]}` |
| `market.session_status` | `market.session.status` | flattened |
| `market.instrument` | `market.selected_instrument` | renamed |
| `market_state.regime` | `{regime:"TRENDING", reason:"..."}` (object) | extract `.regime` string |
| `left_brain.*` (direction/confidence/narrative/status/strength) | all nested under `left_brain.thesis.*` | flattened; `strength→momentum` |
| `verdict.edge_grade` | `verdict.grade` | alias added |
| `strategy_scanner.selected_strategy` | `sc.selected` | renamed |
| `strategy_scanner.strategies` | `sc.ranked_strategies` | renamed |
| `strategy_scanner.trade_plan` | no wrapper; `entry/stop/targets/risk_reward` at sc level | wrapper built |
| strategy items: `key/name/readiness/mode_compatible` | `strategy_key/label/result/"skipped"/"no_signal"/"ready"/eligible` | mapped |
| `journal.today_count/win_rate/avg_r` | `journal.summary.total_trades/win_rate(0-1)/avg_r` | flattened; win_rate×100 |
| `journal.recent_closed` | `journal.recent_trades`; items use `symbol`/`strategy` | renamed |
| `system_status.db_ready/databento_ready/broker_ready` | `database_ready/databento_connected/broker_url_configured` | **backend aliases added** |
| `performance.trade_count` | `performance.sample` | alias |
| `availability.*` | `{available:bool}` objects | extract to plain bool; `timeline` key → `decision_timeline` |
| timeline events | `ts`/`label`/`event_type` | renamed to `timestamp`/`event_label`/`source` |
| `main_brain.voice` | `raw.voice` (dict `{narration, headline, ...}`) | extract `narration` then `headline` |
| `coach.weight_updated` | boolean `true/false` | display as YES/NO (NOT fmtTs) |

## Backend additions (additive-only, Phase 7C.1)
- `raw.voice` at top level: reads `result["main_brain_voice"]` (already computed in full_analysis) — no new computation
- `execution_gateway.gateway_status`: `"SENT"` if `last_sent_at` non-null, else `"IDLE"`
- `system_status.db_ready/databento_ready/broker_ready`: canonical aliases for the UI

**How to apply:** Any future backend field consumed by the console must be added both to the builder AND to `normalizeMainBrainPayload()`. Any future UI panel field must be traced back through the normalizer to verify the canonical source.
