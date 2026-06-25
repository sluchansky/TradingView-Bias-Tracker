---
name: Fast Entry Trigger (seconds-timing layer)
description: Conservative DISPLAY-FIRST 1s/5s entry-timing layer in the TradingView bot — invariants and the gotchas any change to it must preserve.
---

# Fast Entry Trigger

A DISPLAY-FIRST seconds (1s/5s) layer that may only SHARPEN entry TIMING on an
HTF strict setup that is already valid/nearly-valid AND aligned. It NEVER creates a
trade alone and NEVER overrides a bad setup. Two env flags, both default OFF:
`FAST_ENTRY_TRIGGER` (display/state/ingestion) and `FAST_ENTRY_MONEY` (money path).

## Invariants any change must keep
- **`FAST_ENTRY_MONEY` requires `FAST_ENTRY_TRIGGER` + SCALP + NOT `DUAL_TF_ENGINE`.**
  **Why:** the dual-TF engine is the other seconds engine; making them mutually
  exclusive is the guard against two seconds engines double-firing the same zone.
  **How to apply:** the only money-enable predicate is `_fast_entry_money_enabled()`
  — never re-derive that condition inline.
- **The money path REUSES the already-computed `trade_plan`; it never derives a plan
  from seconds state.** The seconds layer decides *timing only*, never price levels.
- **The fast early-entry fire-once key MUST equal the legacy FULL-READY auto key**
  (the `_auto_setup_key` = instrument/direction/zone). **Why:** a fast early entry
  and the later HTF-READY auto must collapse to ONE entry per zone — different keys
  would double-enter. **How to apply:** if you touch the legacy auto key shape,
  update the fast path in lockstep.
- **HTF gate reads ONLY existing strict output** (directions[dir].gate_debug /
  edge_score, dominant_direction). It never recomputes or bypasses the gate.
- **Display-first + fail-open:** every off/closed/error path returns the inert
  neutral block (`early_entry_allowed=False`), so it can never promote on failure.
- **Neutral↔real schema parity:** the real aggregator output and the neutral block
  must have an identical key set (the 9 user-facing fields + the `direction`/`htf`/
  `enabled`/`money_enabled`/`available` twins). The /status route whitelists the
  block, so a new field must be added to BOTH or it's None on the wire.
- **SWING + flags-OFF are byte-identical** (scalp/swing/swing-flagoff goldens).

## Gotchas
- **The strict-gate goldens do NOT cover this layer** — it lives in `full_analysis`,
  *above* the strict funcs the goldens snapshot. Its dedicated guard is its own
  behavioral smoke (display-first, gate-on-setup, never-on-misaligned/bad-HTF,
  money-off-dormant, fail-open), NOT the goldens.
- **Adding the seconds micro alert types bumps the registry count**, so the parity
  baseline (`scalp_baseline_parity.json`) had to be RE-BLESSED. These are additive
  `side:"fast"` score:0 types with nothing in the MGC/MNQ resolver, so re-blessing
  was correct (not a regression). General rule: a pure additive registry change
  legitimately fails `parity` until the baseline is regenerated — confirm the diff
  is additive + score-neutral before re-blessing, don't just rebaseline blindly.
- **The seconds source is repo-owned Pine** (`pine/fast_entry.pine`, auto-detects
  MGC/MNQ/MES/MYM, MGC fallback) — adding a contract means editing it too.
