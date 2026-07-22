---
name: Brain Contract JS migration (Phase 3A)
description: How dashboard render functions bind to data.brain.* — the two helper functions, what was migrated, and what to do when adding new fields.
---

## The rule
All operator-facing dashboard render functions must read from `data.brain.*` only.
No individual `d.verdict`, `d.edge_score`, `d.trade_plan`, `d.active_ticker`, etc. reads are allowed in render functions — they go through `getBrain(d)` which returns the contract or a structurally-identical legacy fallback.

## Entry points (the only two)
```js
function getBrain(d)           // returns d.brain or buildLegacyFallback(d)
function buildLegacyFallback(d) // mirrors brain schema from flat d.* fields; logs a warning
```
Both live just before `function renderGauge(d){` in the dashboard `<script>`.

## Contract keys consumed by render functions
- `bk.decision.verdict` — authoritative verdict string
- `bk.decision.is_ready` — boolean
- `bk.decision.direction` — "Long" / "Short" / null (replaces jsReadyDir(d.verdict))
- `bk.decision.next_action` — maps from stage_next_step
- `bk.score.value` — operator-facing edge score number
- `bk.score.max` — always 110
- `bk.score.grade` — "A+" / "A" / "B" / "" (maps from edge_grade / conviction_tier)
- `bk.instrument` — canonical symbol, 1! already stripped
- `bk.trade_plan` — null when WAIT, object when actionable
- `bk.freshness.price_last_valid_at` — maps from last_valid_time
- `bk.reasons.top` — array ≤3, maps from why[] / strict_reason
- `bk.supporting_diagnostics` — full confidence_governor dict (dict(_gov) in Python)

## Python side
`_build_brain_contract(a, generated_at)` at ~line 39850.
`supporting_diagnostics = dict(_gov)` — the full confidence_governor object, NOT just 2 keys.
All `cg.*` reads in ADC (final_confidence_score, ready, confidence_components) still work.

## Migrated render functions (Phase 3A)
renderGauge, renderModules, _avExtract, renderMBAvatar, refreshRec,
renderAiDecisionCenter, renderBLPanels, renderDirView, renderChartPreview,
renderTodaysTrades, renderMainBrain (symKey only), applyRec, pickCleanestSetup.

## What NOT to migrate
- Developer diagnostic sub-blocks that read `ea.edge_score`, `gd.edge_score`, `aiGd.edge_score` — these are alert_diagnostics/gate_debug fields, intentionally kept.
- `buildLegacyFallback` itself — reads `d.*` by design.
- Controls panels (renderControlsGroups, swing strategy label) — administrative/config fields, not decision fields.

## Adding a new operator-facing field
1. Add the key to `_build_brain_contract` in app.py.
2. Add it to `buildLegacyFallback` in the dashboard `<script>` (mirrors the schema).
3. Read it via `bk.your_field` in the render function.
4. Add a test in `test_brain_dashboard.py`.

**Why:** Scatters of `d.verdict` / `d.edge_score` across 10+ render functions made it impossible to change the server contract atomically. A single `getBrain()` accessor + legacy fallback means all renders stay consistent even during a rolling deploy.
