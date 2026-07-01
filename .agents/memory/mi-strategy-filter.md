---
name: MI adaptive strategy filter
description: The demote-only SCALP money-path veto that blocks an actionable setup fighting an unambiguous market state, plus the MI-flag default-ON / golden-pinning rules.
---

# MI adaptive strategy filter (MI_STRATEGY_FILTER_ENABLED)

Demote-only money-path veto: blocks an already-actionable setup whose strict
DIRECTION fights an UNAMBIGUOUS market state (e.g. a Short into TREND_UP). It is
DIRECTION-based, NOT per-strategy-key. Never creates, upgrades, or re-targets a
trade — only READY→WAIT.

## Fires only when ALL hold
Seam sits above the strict gate, right after the entry_quality veto:
`MI_STRATEGY_FILTER_ENABLED and MARKET_INTELLIGENCE_ENABLED and mode=="SCALP" and is_actionable(verdict)`.
Anything else → no-op. Fail-OPEN on any error / missing input (no veto).

## Favored-side mapping (`_mi_state_favored_side`)
TREND_UP→long, TREND_DOWN→short; BREAKOUT / PULLBACK parse the label direction
("up"/"down", "uptrend"/"downtrend"). EVERYTHING else — RANGE, COMPRESSION,
EXPANSION, LIQUIDITY_HUNT, REVERSAL, UNKNOWN, missing/None — returns None =
ambiguous = NO veto. The filter only bites in a clearly one-directional regime, and
only against a counter-trend entry into that regime.

## Display permissions (`_mi_strategy_permissions`)
When the filter is INACTIVE (flag off) the display shows the favored side only and
emits NO red "blocked" chip — the UI must never imply a block that isn't actually
armed. Ambiguous state → empty allowed/blocked + "No directional constraint".

**Why:** the goal is to stop the bot auto-shorting a clearly-bullish tape (and vice
versa) while never inventing trades or touching any risk/safety gate. Direction-vs-
state is a cheap, robust proxy that reuses the existing MI market-state read.

## Gotchas
- `is_actionable()` matches the LITERAL verdicts "LONG READY" / "SHORT READY" /
  "LONG EARLY READY" / "SHORT EARLY READY" (FULL_READY_VERDICTS / EARLY_READY_VERDICTS),
  NOT bare "READY"/"EARLY" — fixtures that use "READY" silently never trigger.
- All three MI flags are DEFAULT-ON via `_env_flag_on(name)` (its `default_on`
  defaults True); env NAME=0/false/no/off is the kill-switch (user rollout choice C).
- Golden pinning: the SCALP golden MUST pin all three MI flags =0 (SCALP is where the
  fallback + filter act). The SWING golden does NOT pin them and still passes because
  the entire MI money path is SCALP-only — do NOT "fix" it by adding a pin, or it
  would snapshot the wrong (MI-off) config vs the live MI-on SWING behaviour. The
  frozen legacy baseline is check_swing_flagoff_golden.sh, which does pin them.
- ON-path guards: check_mi_strategy_filter.sh (45 assertions) + check_mi_fallback.sh
  (19). Goldens run flag-off, so these smokes are the only ON-path coverage.
