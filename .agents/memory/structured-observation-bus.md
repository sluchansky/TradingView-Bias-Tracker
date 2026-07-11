---
name: Structured observation bus
description: Architecture for per-specialist machine-readable observations fed into Main Brain
---

## Rule
All Main Brain user-facing prose is synthesized from structured observations emitted per specialist, not from specialist prose directly.

## Schema (_emit_observation)
```
{source, market, mode, observation, direction, confidence (0..1), evidence (≤8 strings), timestamp (ISO UTC)}
```

## Implementation
- `_emit_observation()` helper — defined after `_mb_observe` (voting bus helper)
- `_mb_build_structured_observations(result)` — centralized function; reads 11 specialist blocks from the assembled `result` dict, FAIL-OPEN per engine (try/except per section), returns list of `_emit_observation` dicts
- Called from `compute_main_brain` → `mb_out["observations"]` inside its own fail-open try/except
- `_main_brain_neutral` has `"observations": []` for schema parity
- `/status` needs no whitelist change — main_brain is forwarded wholesale

## Specialists covered (11)
market_intelligence, strategy_engine, stalk_mode, breakout_mode, entry_quality, analyst_reasoning, trade_debate, pro_review, confidence_governor, scalp_quality, liquidity_sweep_focus

## Observation codes per specialist (examples)
- market_intelligence: trending_bullish / trending_bearish / ranging
- strategy_engine: strategy_ready_long / strategy_ready_short / strategy_forming / no_strategy
- stalk_mode: stalking_long / stalking_short / engine_entering / in_trade / idle
- breakout_mode: breakout_long / breakout_short / sweep_reversal_long / sweep_reversal_short / building_range / watching / off
- entry_quality: clean_location / good_location / poor_location / chasing
- analyst_reasoning: bullish_thesis / bearish_thesis / neutral_thesis / veto_active
- trade_debate: decisive_bull / decisive_bear / balanced / veto_active
- pro_review: grade_excellent / grade_good / grade_fair / grade_poor
- confidence_governor: confidence_allow / confidence_caution / confidence_block
- scalp_quality: scalp_pass / scalp_caution / scalp_fail
- liquidity_sweep_focus: sweep_confirmed / sweep_forming / sweep_failed / continuation / no_sweep

## Synthesis layer (_mb_synthesis_report)
Takes `result` + `observations` list, produces HUNTING/READY/MANAGING narrative:
- `status_headline`: MANAGING > READY > BUILDING > INVALIDATED > HUNTING (WAIT+thesis) > WATCHING
- Sections: opening_line, what_happened, what_supports, why_not_entering, what_happens_next, what_cancels, learning_memory, decision, next_action
- Wired at `mb_out["synthesis"]` in its own fail-open try/except inside `compute_main_brain`
- `_mb_synthesis_neutral()` = stable schema; `_main_brain_neutral` has `"synthesis": None`

**Why:** Main Brain must be the sole author of prose; specialists must emit structured facts. Separating the two allows the synthesis logic to evolve without touching 11 specialist engines.

**How to apply:** New specialists — add a try/except block to `_mb_build_structured_observations`, derive an `obs_code`, call `_emit_observation`, append to `obs`. Synthesis section reads from `obs_by_src[source].observation` and the raw `result` block.
