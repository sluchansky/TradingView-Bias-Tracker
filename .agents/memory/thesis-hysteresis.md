---
name: Persistent market thesis continuity
description: Durable rules for evidence-driven, mode-scoped decision continuity without weakening strict entry authority.
---

# Persistent Market Thesis Continuity

## Rule

Maintain exactly one persistent thesis for each instrument and canonical mode. SCALP and INTRADAY_TREND never share continuity state; SWING is only the historical alias for INTRADAY_TREND.

Continuity is demote-only. A strict WAIT always remains WAIT. Only a strict READY that is consistent with a confirmed thesis may remain READY.

State changes only when material evidence identity changes; repeated heartbeat evaluation of the same evidence is a true no-op.

A reversal is two-step: first record pending opposing evidence, then require a distinct evidence event with confirmed reversal structure. Preserve the prior thesis as explicitly invalidated before the replacement starts forming.

Use lifecycle states NEUTRAL, FORMING, CONFIRMED, WEAKENING, PENDING_REVERSAL, and INVALIDATED. Keep entry status separate from lifecycle status.

Restore durable continuity after restart even when the last evidence is old, but mark that evidence stale rather than silently discarding the thesis or treating stale evidence as a new entry trigger.

**Why:** Evaluation-count hysteresis changed confidence every few seconds, mixed SCALP and intraday state, and could preserve READY after the strict evaluator had returned WAIT. That made the bot appear to change its mind and could contradict strict authority.

**How to apply:** Any future thesis consumer must key reads by instrument and canonical mode, distinguish candidate/thesis/entry/advisory concepts, preserve append-only transitions, and never turn research or continuity context into a READY promotion.
