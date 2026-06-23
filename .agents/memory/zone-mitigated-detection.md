---
name: Zone Mitigated detection (per-instrument state + SCALP TTL)
description: Mitigation state is per-instrument (MITIGATED_*_BY_TICKER) with a mode TTL; a mitigated zone is tradeable only when paired with a same-direction reaction.
---

# Zone Mitigated detection

A *mitigated* demand/supply zone that **reacts** is a high-conviction signal:
"mitigated demand reacting" → GO LONG only when ALL hold — this instrument's
mitigation flag is armed, price is near a mitigated **demand** price for THIS
instrument, there is a bullish reaction (5m confirmation candle / zone-confirmed /
liquidity sweep), and price is above VWAP. A **broken** zone still blocks and an
**unconfirmed** touch still WAITs **only in SWING** (`cfg("GATE_REQUIRE_ZONE")` on);
in SCALP the zone is fully demoted and neither blocks (see "SCALP full demotion" below).

**Zone-valid is the tradeable mitigation:** `evaluate_strict_setup` computes
`zone_valid_long/short = has_mitigated_* AND reaction_*` and exposes it as
`confluences.zone_mitigated` — exactly what the +25 Zone Edge component credits.
Mitigation alone (no reaction) is NOT a signal; it is the old "consumed /
stand-aside" state. Bare every-5m CONFIRMATION webhooks must NOT count as the
reaction (see confirmation-candle-every-bar.md) or false-READY returns.

## State is PER INSTRUMENT, not global (the fix for the MNQ stuck-scoring bug)

Mitigation state lives in two **per-instrument dicts** keyed by `instrument_of()`:
`MITIGATED_PRICES_BY_TICKER` ({inst: [{"price","ts"}, …] cap 10 each}) and
`MITIGATED_FLAG_BY_TICKER` ({inst: bool}). They are mutated **in place** (never
rebound) so no function needs a `global` declaration for them.

- `_handle_zone_mitigated(price, ticker)` appends + arms only that instrument.
- `is_near_mitigated_zone(price, ticker)` checks only that instrument's list.
- A structure-reset alert clears only `resolved_inst`'s flag (not the other side), and
  ONLY when `cfg("GATE_REQUIRE_ZONE")` is on — SCALP keeps the mitigation state so its
  zoneState diagnostics stay accurate (the flag no longer drives any SCALP block).
- Consumers pass the active instrument: strict gate uses its local `inst`;
  `full_analysis` uses `_mit_inst = instrument_of(active_ticker)`.

**Why this matters (the bug):** the old design used a single global bool
`ZONE_MITIGATED_FLAG` + single global capped list `MITIGATED_PRICES` with NO
instrument identity. MGC floods `ZONE MITIGATED` alerts every few minutes, which
kept the global flag armed for MNQ and dominated the shared 10-slot list. MNQ's
own mitigation near price + no qualifying reaction → MNQ's `zone_mitigated_near`
stuck True forever → every MNQ confirmation dropped with "Zone mitigated
(unconfirmed) — scoring skipped". MGC scored fine (asymmetry). Price proximity
(0.3%) alone prevented 4k↔30k price-scale bleed but the global FLAG still bled.

## Mode-tuned TTL (un-sticks a stale consumed zone)

`is_near_mitigated_zone` prunes entries older than `cfg("MITIGATED_TTL_MIN")`
before the proximity check (None = no expiry; unparseable ts = fail-open / kept):
- **SCALP = 30 min** (== STAGE_WINDOW_MIN): a consumed zone stops blocking after
  30 min so MNQ can't be permanently stuck.
- **SWING = None**: no expiry — historical lifecycle preserved. The only
  SWING-visible change from this fix is instrument isolation (a correctness fix).

**Money-safe:** TTL only REMOVES a block, and in SWING `zone_valid_* = has_mitigated_*
AND reaction_*`, so an expired mitigation makes `zone_valid_*` False → SWING READY still
fails; expiry can never CREATE a SWING READY. (In SCALP the zone is non-blocking, so TTL
is purely a diagnostics / zoneState concern — the SCALP READY decision ignores it.)

**How to apply:** any new mitigation-derived signal must read the per-instrument
dicts (gate the flag with this-instrument proximity), never reintroduce a global
flag, and must not treat bare confirmations as reactions.

## Proximity is an INSTRUMENT-SCALED absolute points band (not a flat %)

`is_near_mitigated_zone` compares `abs(price - ref) <= tol` where
`tol = spec.mitig_tol_pts` (per-instrument absolute POINTS), falling back to
`abs(ref) * MITIGATED_TOLERANCE_PCT` (0.3%) only for an instrument without a spec.
Defaults are env-overridable: `MNQ_MITIG_TOL_PTS=15.0`, `MGC_MITIG_TOL_PTS=12.0`
(via `_spec_float_env`, mirroring `min_stop_ticks`).

**Why:** a flat 0.3% does not scale across price levels — ~92 pts on MNQ@30k
(> 4x its 20-pt tp1) vs ~12.6 pts on MGC@4.2k. On a choppy structureless MNQ night
one mitigated zone's ~92-pt band swallowed the whole session's ~27-pt range, so
EVERY MNQ evaluation read "near a consumed zone" → constant "scoring skipped". MGC
@12 ≈ its old 12.6 (intentionally unchanged — least surprise; only MNQ was broken).

**Money-safe:** tightening only NARROWS the band → can only REMOVE over-blocking
(the SWING `zone_mitigated_near` WAIT) and make `has_mitigated_*`→`zone_valid_*` HARDER
(more conservative). Same argument as the TTL: never fabricates a SWING READY (structure
+ edge + reaction still gate). A genuinely new zone >tol from a consumed level is no
longer falsely treated as consumed; in SWING price sitting on the zone still correctly
blocks (in SCALP it never blocked).

**How to apply:** keep consumed-zone proximity in absolute points per instrument;
if real MNQ zones routinely span wider than tol, raise `MNQ_MITIG_TOL_PTS` (no code
change) rather than reverting to a percentage. Note: the LIVE deployed app runs its
own copy — a code change here needs a republish to take effect in production.

## SCALP full demotion — zone is non-blocking at EVERY site

`GATE_REQUIRE_ZONE`=False in SCALP demotes the zone from the READY gate, but the gate flag
is NOT the only place a consumed/broken zone can force WAIT/skip/mute. Demoting ONLY the gate
leaves the money path still zone-blocked. ALL of these independent zone short-circuits must
be guarded with `cfg("GATE_REQUIRE_ZONE")` (live in SWING, no-op in SCALP):
- `full_analysis` zone_broken / zone_mitigated_near OVERRIDES that reset the strict payload
  to WAIT / score 0 and hard-zero the display Edge Score.
- the per-direction (direction-card) zone blockers.
- the webhook consumed-zone SHORT-CIRCUIT in `_process_webhook_alert`: it sends the "zone
  mitigated (unconfirmed)" notice and `return`s BEFORE dispatch — un-guarded, this silently
  suppressed live SCALP alerts/trades even with the gate flag already demoted.
- `_update_setup_state`'s `invalid` (consumed/broken zone → INVALIDATED): un-guarded, a
  freshly-dispatched SCALP READY flips straight to INVALIDATED in the display.
- the structure-reset mitigation-flag clear (above).
- the DISPLAY-ONLY teaser alerts: the tiered WATCH/ARMED/WATCH-FOR-ENTRY `alert_level` ladder
  (it muted on a consumed/broken zone) and `_maybe_dispatch_early_alert` (it muted on a broken
  zone). These are non-actionable heads-ups, NOT the money path — but a demoted zone must not
  mute them in SCALP either, so they are cfg-guarded too (an ACTIVE_TRADE still suppresses the
  EARLY teaser in EVERY mode; the tiered ladder still only fires near an existing zone).

**Why:** the live PROD symptom was "no alerts/trades fire on a real prop account". Flipping
the gate flag alone did NOT fix it because the webhook short-circuit and the full_analysis
override still blocked on the consumed zone — a demoted gate LEAKS unless every block site is
cfg-guarded. A gate flag and a downstream payload-override / short-circuit are DIFFERENT sites.
**How to apply:** when demoting ANY gate (zone / vwap / cvd / vol) for one mode, grep for
EVERY read of its underlying flag (`zone_broken_active`, `zone_mitigated_near`, …) across
full_analysis + the webhook tail + setup-state + the direction cards + the teaser dispatchers
and cfg-guard each one. The SWING money path must stay byte-for-byte unchanged (every guard
reduces to the old behaviour when `cfg("GATE_REQUIRE_ZONE")` is True).
