---
name: Entry Quality location engine
description: The LOCATION-aware entry scorer (separate from the strict gate), its demote-only veto, and the non-obvious fail-open / parser decisions worth keeping consistent.
---

# Entry Quality location engine

A 0-100 score answering "is THIS a good *location* to enter?" — orthogonal to the
strict IF-valid gate and to Edge. Lives at the single `full_analysis` return path,
computed from the FINAL assembled result. It is the 5th display-first engine layer
alongside analyst / trade_debate / pro_review / learning-v2 and is wired to **byte-mirror
those sibling vetoes**.

## Shape
- Display-first: always computed, written ONLY to `result["entry_quality"]`. When the
  gate flag is OFF (the default) it can never touch verdict / trade_plan / directions /
  execution. Engine ON, veto OFF by default; runtime toggle resets OFF on restart.
- Veto is flag-gated, DEMOTE-ONLY: when armed it can only turn an actionable verdict
  into WAIT (null plan, directions ready=False/WAIT, re-run decision_support,
  entry_quality.final_verdict=WAIT). It can never promote/force a trade.
- `veto_would_fire = (score < ENTRY_QUALITY_MIN_SCORE 70) AND NOT (Edge >= ENTRY_QUALITY_OVERRIDE_EDGE 90)`.
  The Edge>=90 override lets extremely-high-confidence setups through a poor location.

## Non-obvious decisions (don't "fix" these without re-reading the Why)
- **Absence-of-bad sub-scores default to FULL CREDIT 1.0, not neutral 0.70.** The
  "not chasing" and "not at a swing high/low" components return 1.0 when their bad
  condition is absent OR when the reference data (e.g. swing_ctx swing highs/lows) is
  missing. The OTHER components fall back to the 0.70 neutral fraction when their own
  inputs are missing/raise.
  **Why:** for a demote-only veto, maximal fail-open means a data gap must NOT drag the
  total toward the <70 reject. Lowering these to 0.70 would *lower* totals and make
  missing data MORE likely to trip a false veto — the opposite of "a data gap can never
  produce a false reject". Semantically "no known swing nearby" == "clear" == good.
  **How to apply:** keep missing-reference cases for absence-of-bad metrics at 1.0;
  reserve the 0.70 neutral fallback for metrics whose own computation is genuinely
  unavailable.
- **The `/entry-quality` toggle parser is byte-identical to the other 4 engine toggles**
  (real bool passes through; int/float via `bool(...)`; recognised string tokens
  1/true/yes/on, 0/false/no/off; structural junk like null/{}/[]/"maybe" is ignored,
  leaving the gate unchanged).
  **Why:** the 5 engine toggles must stay consistent; tightening ONE in isolation
  (e.g. to reject a float like 0.5) creates cross-engine drift for no real safety gain —
  the dashboard only ever sends real booleans, and arming a demote-only veto from a
  stray value can only make the bot MORE conservative, never force a trade.
  **How to apply:** if you ever change toggle parsing, change all 5 in lockstep.

## ATR-extension math & the mode-correct ATR (the "13 ATR" bug)
- `atr_extension = abs(price - vwap) / ext_atr`. The math was always correct; the bug
  was the **denominator timeframe**: it divided a SESSION-VWAP distance by the **1-minute**
  volatility ATR, so SWING showed implausible readings (e.g. "13 ATR beyond VWAP").
- `ext_atr` is chosen per mode: **SCALP keeps the 1m volatility ATR (== `unit`)**, so SCALP
  scoring stays byte-identical. **SWING uses an HTF ATR (1H → 4H → Daily, in that priority)
  from `swing_ctx`.**
  **Why:** measuring a session-scale distance against a 1-minute ATR mixes timeframes and
  inflates the ratio. The denominator must match the distance's timeframe.
  **How to apply:** if SWING has **no** HTF ATR, set `ext_atr=None` and mark the extension
  *unavailable* (raw atr/atr_extension = None, decision "HTF ATR unavailable"). NEVER fall
  back to the 1m `unit` ATR in SWING — that silently reintroduces the mix. The fail-open then
  flows through the neutral fraction, so a missing HTF ATR can't trip a false veto.
- The raw timeframe label (`raw.atr_timeframe`) is surfaced on the dashboard precisely so a
  timeframe mix can never again be invisible.

## Hard-location cap (good room must NOT override a bad location)
- After the weighted sum, a **DOWN-ONLY** cap forces `score = 69` when `score >= 70` AND a
  hard trap is present (chasing / ATR-extension > 2 / impulse_pct >= 90 / at swing high|low).
  `strong_reject` is the sub-70 "BAD LOCATION" tier (constant `ENTRY_QUALITY_STRONG_REJECT`).
  **Why:** the weighted components can let a great room-to-target / cushion mask a clearly bad
  entry (e.g. overextended + chasing summed ~77). The product requirement is that good room
  can never buy back a bad *location*.
  **How to apply:** the cap may only ever LOWER a score, never raise/force one; it sits below
  the existing demote-only veto, so all veto/flag-gating invariants are unchanged. The TRAP
  fixture in the smoke proves uncapped~77 → capped <70.

## Display-only additions (raw / improvement plan / projection / plain-English)
- `raw` (price/vwap/distance/atr/atr_timeframe/atr_extension/decision), `improvement_plan`,
  `projected_low/high`, and `plain_english` are **display-only** and ride the existing
  `result["entry_quality"]` (already `/status`-whitelisted, no new route).
- `_projected(target)` only lifts the **waitable** components (`_improvable`) up to `target`;
  a completed liquidity sweep is the one component that can't be waited into existence, so it
  is excluded. Projection is clamped monotonic via `max(score, ...)` so it can never read
  below the live score.
  **How to apply:** keep `_improvable` keys in sync with `ENTRY_QUALITY_COMPONENTS` keys and
  with whatever bullets the plan emits — if a plan bullet says "wait for X", X's component
  should be in `_improvable` or the projected range won't reflect that advice.
- The **neutral / no-candidate early return must include every new key** (raw={}, plan=[],
  projected_*, plain_english, strong_reject) or the dashboard JS reads undefined.

## Testing
- The strict-gate goldens snapshot the strict funcs ONLY (not `full_analysis`), so they
  stay byte-identical for this purely additive layer and do NOT cover the veto wiring.
  `.local/state/entry_quality_smoke.py` (run via `check_entry_quality.sh`, SCALP) is the
  dedicated guard for the engine math + endpoint + the full_analysis veto matrix.
- The full_analysis veto matrix is exercised under SCALP only: SWING's `full_analysis`
  runs its own entry-veto layers (`_swing_entry_veto_reasons` + HTF gating) the harness
  does not neutralise, so a synthetic actionable verdict wouldn't survive there. Veto
  WIRING is mode-independent (SCALP proves it); SWING money-path parity is the
  swing-flagoff golden's job. Mirrors the SCALP-only trade_debate / pro_review smokes.
