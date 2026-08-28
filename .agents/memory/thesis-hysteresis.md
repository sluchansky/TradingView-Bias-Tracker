---
name: Persistent market thesis continuity
description: Mode-scoped thesis continuity is evidence-epoch idempotent and never upgrades a blocked strict WAIT into an actionable verdict.
---

# Persistent Market Thesis Continuity

Maintain exactly one persistent thesis for each instrument and canonical mode.
SCALP and INTRADAY_TREND never share continuity state; SWING is only a historical
alias for INTRADAY_TREND.

Confidence may change only once per material evidence epoch. Repeated heartbeat
or dashboard evaluation of the same completed bar is a true no-op.

Continuity is demote-only. A blocked strict WAIT always remains non-actionable,
even when the thesis stays active and the entry is merely paused.

Opposite candidate structure cannot flip FORMING, READY, or ACTIVE state.
Confirmed opposite structure starts a paused replacement thesis, and a newer
evidence epoch is required before the opposite direction can become actionable.

Fresh safety, stale-data, market-close, stop/zone, and confirmed-reversal
evidence bypass continuity immediately. Unexpired invalidation cooldown remains
binding across heartbeat evaluations and restart restoration.

Persist the stable reason, originating structure context, explicit invalidation
conditions, evidence epoch, and mode with the thesis.

After restart, persisted thesis direction and confidence may be restored for
continuity, but entry authority must be forced to paused/WAIT until one fresh
strict evaluation succeeds. Restored snapshots must not influence enforced
thesis alignment before that evaluation.

**Why:** Evaluation-count hysteresis changed confidence every few seconds, mixed
SCALP and intraday state, and could visibly contradict strict entry authority.

**How to apply:** Future thesis consumers must key by instrument and canonical
mode, preserve exact evidence identity and append-only transitions, and never
use continuity or research context to promote a strict WAIT.
