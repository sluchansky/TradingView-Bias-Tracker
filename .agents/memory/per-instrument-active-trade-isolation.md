---
name: Per-instrument ACTIVE_TRADES_BY_INST isolation
description: How open-position tracking is per-instrument (not a single global), the fail-closed ticker-resolution rule for management endpoints, and why ALERT_HISTORY stays shared+widened instead of per-instrument.
---

# Per-instrument open-position isolation

The webhook app tracks **one open position per instrument** in `ACTIVE_TRADES_BY_INST`
(guarded by `ACTIVE_TRADES_LOCK`, an `RLock`), via helpers `active_trade_for(inst)` /
`set_active_trade(inst, trade, overwrite=)` / `clear_active_trade(inst, opened_at=)` /
`any_active_trade()` / `active_trade_count()`. There is NO single global `ACTIVE_TRADE`
any more. Purpose: one asset's open position / cooldown / re-arm must never block another
asset's evaluation, alerts, or auto-execute.

## Fail-closed ticker resolution (money-path rule)
**Rule:** `_resolve_active_trade(ticker)` returns `(inst, trade, error)`. An EXPLICIT but
unknown/ambiguous ticker is FAIL-CLOSED — it returns an error string and callers
(`/close`, `/breakeven`, `/trade`) must return HTTP 400 BEFORE the no-active-trade check.
Only an ABSENT/empty ticker falls back to `any_active_trade()` (legacy single-position
dashboard behaviour).

**Why:** an earlier version treated a bad/typo ticker the same as "no ticker" and fell
back to the most-recent open trade — so `/close {ticker:"BAD"}` could close the WRONG
instrument's live position. A typo on a management endpoint must never act on an unrelated
trade.

**How to apply:** any new management endpoint that targets a position by ticker must use
`_resolve_active_trade` and reject `error` first. Never silently default an unrecognized
ticker to a real instrument or to "the only open trade".

## Compare-and-clear
The price watcher clears a closed slot with `clear_active_trade(inst, opened_at=<that
trade's opened_at>)` so a newer re-entry on the same instrument isn't deleted by a stale
STOP_HIT/T1 handler. Keep the opened_at compare-and-clear whenever clearing from a
deferred/worker path.

## SCALP stacking vs the gateway write
Gateway auto-exec guard is `if active_trade_for(inst) and not allow_stack: return`. With
SCALP `allow_stack=True` the guard is bypassed (broker-side stacking allowed) and the
local write uses `set_active_trade(inst, trade, overwrite=False)` — i.e. only the FIRST
local slot per instrument is tracked even though multiple broker entries may exist. This
matches the prior single-global behaviour (one tracked slot, broker-side stacking); it is
NOT local management of multiple same-instrument stacks.

## ALERT_HISTORY: shared + widened, NOT per-instrument
`ALERT_HISTORY` was widened `maxlen 100 → 1000` so a high-frequency asset can't evict a
quiet asset's recent alerts. It is deliberately kept a SINGLE shared deque.

**Why not per-instrument:** the legacy score (`calculate_scores` / `score_alerts`) is
computed across ALL instruments' alerts in the deque — `full_analysis(ticker_override=X)`
scores the whole window regardless of X. Splitting into per-instrument deques would change
that mixed-instrument score and therefore BREAK MGC byte-identity.

**Why widening is byte-identical for MGC:** every consumer is bounded, so a deeper buffer
only ever retains MORE out-of-window history (filtered out), never less:
- SCALP scoring is time-windowed (`SCORE_WINDOW_MIN=20`).
- The READY gate (`evaluate_strict_setup`) reads a time-window (`STAGE_WINDOW_MIN`) or the
  last 8 (`list(...)[-8:]`).
- EARLY scanners are time-windowed (`EARLY_WINDOW_MIN`).
- The ONE unbounded scan — `calculate_scores` (SWING path, `SCORE_WINDOW_MIN=None`) — is
  explicitly capped at the original `[-100:]`, so its score is unchanged.

**How to apply:** if you add a new ALERT_HISTORY consumer, make it time-windowed or
explicitly capped; never add an unbounded full-deque scan whose result depends on maxlen.
