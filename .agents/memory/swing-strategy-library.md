---
name: Live SWING Strategy Library
description: Operator-selectable swing strategies wired into live SWING trade decisions as a DEMOTE-ONLY filter; invariants any change must keep.
---

# Live SWING Strategy Library (money-path, DEMOTE-ONLY)

Operator picks a swing strategy per-instrument; when the master flag is ON and the
bot is in SWING mode, an already-actionable SWING setup whose pattern does NOT match
the selected strategy is demoted READY->WAIT (drop `trade_plan`, attach a precise
reason). It ONLY narrows which already-READY SWING setups are taken.

**Invariants (any change must preserve):**
- DEMOTE-ONLY: can only turn READY->WAIT, never the reverse; never creates a trade,
  loosens the gate, or alters stops/targets/sizing.
- No selection => NEVER veto (pass-through). This is the demote-only guarantee.
- FAIL-CLOSED: any predicate exception DEMOTES (veto), never passes. There is an
  inner guard in the apply helper AND an outer guard at the full_analysis seam.
- Default OFF => byte-identical. `result["swing_strategy_filter"]` is attached ONLY
  under both `_swing_htf_enabled()` AND `_swing_strategy_filter_enabled()`; absent
  otherwise. Guarded by the flag-OFF goldens (parity/scalp/swing_flagoff + flag-ON
  SWING golden all byte-identical) plus the flag-ON smoke `check_swing_strategy.sh`.

**Why:** it's a live money-path layer; the whole point is to restrict, not expand,
so a leak that promotes or skips fail-closed could take an unintended live trade.

**How to apply:**
- Seam is the full_analysis SWING path, right after the SWING veto; closed-market
  override MUST neutralize the local var to `_swing_strategy_status(...)` so the
  single-return-path keeps key parity (hard-indexed consumers).
- Predicates read ONLY `compute_swing_context` fields (aligned_long/short,
  bias_daily, daily_level_nearby) + direction — never recompute or reach elsewhere.
- Selection is in-memory per-instrument and resets on restart (like auto-trade
  arming) — intentional, NOT persisted; don't add persistence without re-asking.
- `/swing-strategy` is owner-only: NOT in OPEN_PATHS, IS in BOT1_ROUTES proxy
  whitelist; validates instrument (strict `_instrument_from_text`) + strategy key/null.
