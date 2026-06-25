---
name: Analyst professional game-plan
description: The DISPLAY-ONLY pro-analyst trade-plan layer nested under analyst["game_plan"] — what it is, why it's nested, and the invariants any change must keep.
---

# Analyst professional game-plan (`analyst["game_plan"]`)

A DISPLAY-ONLY "think like a professional analyst" layer that turns the Analyst
Market Story into a trade plan: probabilistic plan (thesis + Long%/Short% + Action +
Reason), Next Expected Move (bullets + confidence%), Trade Invalidation list,
Professional Game Plan checklist, Scenario Tree A/B/C (probs sum 100), Dynamic
Confidence Long/Short/Neutral (sum 100), Time Horizon (mode-dependent), Risk vs
Reward Forecast (reward/risk/expected_r/p_tp1/p_tp2), and a one-line AI Conclusion.

## The rule
It is a **nested block under `analyst`, NOT a new top-level `result` block.** Built by
`_analyst_game_plan(ctx)` (pure + fail-open, wrapped in try/except → neutral schema)
from already-computed analyst locals; mirrored with safe defaults by
`_analyst_game_plan_neutral()`.

**Why:** the Analyst engine already owns the Market-Story panel and `/status` passes
the whole `analyst` block wholesale, so nesting here means new keys reach the
dashboard with zero serialization/whitelist edits, and the existing
`analyst_report` (mod-report) stays the separate executive-thesis consumer. A
separate top-level block would have duplicated a panel-level engine and needed a
curated-endpoint whitelist edit.

## How to apply (invariants any change must keep)
- **Display-only:** never read `game_plan` into gate/scoring/sizing/dedupe/execution.
  It lives above the strict goldens, so scalp/swing(-flagoff) goldens stay
  byte-identical — re-run all three after touching it.
- **Schema parity:** every key the dashboard reads must exist in BOTH the neutral
  block and the real return (hard-indexed-consumer invariant); add to both + the
  `_analyst_game_plan_neutral()` contract together.
- **Probabilities:** prob_long+prob_short and dynamic_confidence(long+short+neutral)
  each normalize to 100; scenario A/B/C probs sum to 100 in every branch; Neither /
  no-plan / conflict / chasing degrade coherently and R:R shows "—" rather than
  fabricating geometry.
- **Dashboard JS:** render via textContent/_set/_anFill only; no bare `\n`/`\t`/`\r`
  in the triple-quoted JS string; glyphs must be BMP (no `\\u` surrogate pairs).
- Guarded by its own smoke (`.local/state/check_game_plan.sh`), not the goldens.
