---
name: Main Brain three-layer architecture
description: Orchestration / learning / sole-speaker separation inside compute_main_brain; invariants any future Main Brain work must honour.
---

## Rule

`compute_main_brain` is structured in exactly three layers. Never collapse them.

```
Layer 1  _mb_learning_snapshot()     — shared learning memory (one read of all 4 sources)
Layer 2  _mb_orchestrate()           — reads every engine once → pure structured packet
Layer 3  compute_main_brain()        — thin; calls layers 1+2, then synthesizes once
```

### Layer 1 — Shared Learning Memory (`_mb_learning_snapshot`)

**Single source of truth** for all historical learning signals. The only place that
reads `trade_memory`, `confidence_governor`, `learning_rule_engine`, `learning_engine`.

Returns:
```
available, similar_samples, avg_r, win_rate_pct, most_common_failure,
recommendation, confidence, allow, governor_veto, eligibility,
disabled_setups, le_today, note (one-sentence memory sentence)
```

Neutral: `_mb_learning_snapshot_neutral()` — `available: False`, safe defaults.

### Layer 2 — Orchestration Layer (`_mb_orchestrate`)

Calls every specialist engine reader **exactly once** in this order:
1. `_mb_build_structured_observations` → `observations`
2. `compute_brain_conflict_resolver` → `conflict_resolver`
3. `compute_verdict_board` → `verdict_board`
4. `_mb_learning_snapshot` → `learning_memory`

Returns a pure structured packet — no prose, no scoring, no user-facing text.
Each engine read is individually fail-open. Never raises.

### Layer 3 — Main Brain, sole speaker (`compute_main_brain`)

Calls `_mb_orchestrate()` exactly once. All four engine results come through
the packet. Then:
- Calls `_mb_synthesis_report` (sole author of user-facing narrative)
- Calls legacy `_mb_reconcile` for backward compat (trailing step)
- Returns `mb_out` with all display keys

`mb_out` new key: `"learning_memory"` (wired from `_packet["learning_memory"]`).

## Why

User directive: "Do not merge the engines into one giant function. Create one
orchestration layer that reads every engine, one shared learning memory, and
one Main Brain that alone speaks to the user."

Before this change, `compute_main_brain` had 6+ individual try/except blocks
calling engines inline, and learning sources were read in different ways by
different sub-functions. This broke the "one source of truth" requirement.

## How to apply

- **Adding a new specialist engine**: add it to `_mb_build_structured_observations`
  (a new `_emit_observation` block) — it flows through the orchestration layer
  automatically. Do NOT add a separate call inside `compute_main_brain`.
- **Adding a new learning signal**: add it to `_mb_learning_snapshot`, not inline
  in `compute_main_brain`. All consumers read from `mb_out["learning_memory"]`.
- **New user-facing prose**: add to the synthesis/market_brain/strategy_brain
  section of `compute_main_brain` only — never generate user text in layer 1 or 2.
- **`_main_brain_neutral`**: must include `"learning_memory": None` (already there).
