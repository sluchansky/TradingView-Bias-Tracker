---
name: EARLY READY tier, score-aware conflict & alert_diagnostics
description: SCALP-only loosened gate — the EARLY actionable band, score-aware conflict resolution, the alert_diagnostics block feeding /status + Discord + the 5 dashboard modules, and the SWING-parity invariants any change here must keep.
---

# EARLY READY tier + score-aware conflict + alert_diagnostics

All of this is **SCALP-only**. SWING must stay byte-for-byte identical — the knobs
below collapse it back to the historical gate. This is the hard invariant for any
future edit in `evaluate_strict_setup` / `full_analysis`.

## The two-floor tier model (`ready_state`)
`evaluate_strict_setup` returns `ready_state ∈ {"READY","EARLY",""}`, derived AFTER
all non-edge gates pass (zone/vwap/structure where the mode requires them, conflict,
volatility, confirmations-count):
- Edge ≥ `EDGE_FULL_READY_THRESHOLD` → `"READY"` (full setup).
- `EDGE_READY_THRESHOLD` (actionable/EARLY floor) ≤ Edge < full → `"EARLY"` (tradeable, fires a
  labeled live alert + trade plan + journal, verdict `LONG/SHORT EARLY READY`).
- below the actionable floor → `""` → WAIT.
**SCALP now sets BOTH floors to 75 (`EDGE_READY_THRESHOLD == EDGE_FULL_READY_THRESHOLD == 75`),
so the EARLY band is currently COLLAPSED/dormant** — SCALP emits only READY or WAIT; the EARLY
code path is intact but never fires while the floors are equal. SWING is 80/80 (also collapsed),
preserving parity. Both tiers set `_is_ready` True; `_is_full_ready_verdict` is True only for full.
The knobs are the actual `cfg()` names — there is no `EDGE_ACTIONABLE_THRESHOLD`.
**How to apply:** the floors are `cfg()` knobs (`EDGE_READY_THRESHOLD` = actionable/EARLY floor,
`EDGE_FULL_READY_THRESHOLD` = full-READY floor) per MODE — never hardcode a tier threshold; gate_debug
exposes `edge_ok` (≥ready) vs `edge_actionable` (≥actionable) separately. Re-introduce an EARLY band
only by setting the actionable floor BELOW the full floor in a mode's MODES dict.

## Verdict helpers — use them, never literal strings
`FULL_READY_VERDICTS` / `EARLY_READY_VERDICTS` tuples + `_is_ready_verdict` /
`_is_full_ready_verdict` (Python) and `isFullReady`/`isEarlyReady`/`isReadyVerdict`/
`readySide` (dashboard JS). Every consumer (alert_level, embeds, dispatch loop,
intrabar dedupe, candidate fallback, dashboard) routes through these.
**Why:** adding a new ready-like verdict only requires adding it to ONE tuple; a stray
`verdict == "LONG READY"` literal silently skips the EARLY tier. **How to apply:** a new
ready variant ⇒ add to the tuple, do not scatter string compares.

## Score-aware conflict resolution
A both-sides structure clash (`raw_conflict`) is resolved by the two per-direction Edge
scores: SCALP (`CONFLICT_SCORE_AWARE=True`) lets the higher-Edge side run the gate when
`conflict_gap > CONFLICT_TIE_GAP` (10); within the tie gap it stands aside (WAIT).
SWING (`CONFLICT_SCORE_AWARE=False`, gap 0) ALWAYS stands aside — original behavior.
The resolved dominant side overrides the VWAP/ranking candidate choice.
**Why:** a dominant move dragging stale opposite structure was a false WAIT in SCALP.

## alert_diagnostics block (display-only, single source)
`full_analysis` appends `result["alert_diagnostics"]` UNCONDITIONALLY (single-return-path
safe) with long_score/short_score/edge_score/conflict_gap/dominant_direction/current_atr/
volatility_multiplier/ready_reason/rejected_reasons/current_score/required_score PLUS the CVD/RVOL
display fields cvd_state/cvd_value/cvd_direction/cvd_agreement + rvol_value/rvol_adj (see
cvd-rvol-filter). The market-closed override ZEROS it (scores 0, dominant Neutral, CVD/RVOL keys
neutralized, rejected_reasons=["Market closed…"]) — must keep zeroing it or a paused market shows a
stale "why". alert_diagnostics is the WIRE surface for CVD/RVOL display; the top-level result stamps
(cvd_state/cvd_value/etc) are in-process plumbing for edge-breakdown/journal and are NOT whitelisted
on `/status`, so they read None on the wire by design — the dashboard reads `diag.*`, not top-level.
- `required_score` = `EDGE_READY_THRESHOLD` (SCALP 75 / SWING 80) — the dashboard Setup Countdown
  counts toward this floor.
- `rejected_reasons` comes from `_humanize_gate_rejections(gate_debug)` (drops the numeric
  `edge_score(<thr)` line — the Edge vs required already conveys it).
- Wire path: it must be added to the `/status` whitelist dict (curated-endpoint
  serialization rule) AND any peer read endpoint, or it's None on the wire.
- Discord: `_diag_embed_field(src)` renders one compact "🔍 Diagnostics" field; the trade
  card reads `entry["alert_diagnostics"]` (added in `_build_card_entry`), the tiered + early
  embeds read it off the `a` analysis dict. Fail-open (returns None on any error).

## The dashboard modules (display-only, fed by alert_diagnostics)
`renderModules(d)` in the `/dashboard` inline script renders: Trade Probability Meter
(SVG speedometer — semicircle viewBox "0 0 220 132", cx/cy 110, band R=82 sw16, needle
len72, `deg=180-1.8*value`, red0-40/yellow41-69/green70-100; needle/center colored by
`gaugeColor(verdict,prob)`: WAIT red<40 else yellow, EARLY yellow, READY green, HQ
(full+edge≥90) deep-green #15803d + glow filter), Long-vs-Short bars, AI Trade Checklist
(dominant side's gate_debug booleans), Setup Countdown (`required_score-current_score`
points-to-go), Why Not Ready (rejected_reasons list, green when ready), and Volume Delta (CVD) &
RVOL (reads diag.cvd_state/cvd_value/cvd_direction/cvd_agreement + rvol_value/rvol_adj — see
cvd-rvol-filter). `renderModules` is also called in the market-closed early-return branch so paused
state renders.
**How to apply:** these never touch the trade decision — they only read alert_diagnostics
+ `directions[dom].gate_debug`. Dashboard is at `/dashboard` (Flask :8000), proxied via the
api-server Express `/api` mount; the `/api` artifact path is excluded from preview so the
app_preview screenshot tool 401s on it — verify the dashboard via curl localhost:8000.
