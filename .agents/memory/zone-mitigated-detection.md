---
name: Zone Mitigated detection (per-instrument state + SCALP TTL)
description: Mitigation state is per-instrument (MITIGATED_*_BY_TICKER) with a mode TTL; a mitigated zone is tradeable only when paired with a same-direction reaction.
---

# Zone Mitigated detection

A *mitigated* demand/supply zone that **reacts** is a high-conviction signal:
"mitigated demand reacting" → GO LONG only when ALL hold — this instrument's
mitigation flag is armed, price is near a mitigated **demand** price for THIS
instrument, there is a bullish reaction (5m confirmation candle / zone-confirmed /
liquidity sweep), and price is above VWAP. A **broken** zone still blocks; an
**unconfirmed** touch still WAITs.

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
- A structure-reset alert clears only `resolved_inst`'s flag (not the other side).
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

**Money-safe:** TTL only REMOVES a block. `zone_valid_* = has_mitigated_* AND
reaction_*` and SCALP still hard-requires the zone gate, so an expired mitigation
makes `zone_valid_*` False → READY still fails. Expiry can never create a READY.

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
(zone_mitigated_near WAIT) and make `has_mitigated_*`→`zone_valid_*` HARDER (more
conservative). Same argument as the TTL: never fabricates a READY (structure + edge
+ reaction still gate). A genuinely new zone >tol from a consumed level is no longer
falsely treated as consumed; price sitting on the zone still correctly blocks.

**How to apply:** keep consumed-zone proximity in absolute points per instrument;
if real MNQ zones routinely span wider than tol, raise `MNQ_MITIG_TOL_PTS` (no code
change) rather than reverting to a percentage. Note: the LIVE deployed app runs its
own copy — a code change here needs a republish to take effect in production.
