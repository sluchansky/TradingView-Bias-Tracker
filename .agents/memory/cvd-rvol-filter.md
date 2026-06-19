---
name: CVD hard filter + RVOL soft modifier
description: CVD (Cumulative Volume Delta) is a HARD fail-open veto on trade direction; RVOL is a soft Edge modifier that never gates. How both are ingested, scored, and displayed, and the invariants any change must keep.
---

# CVD (hard, fail-open) + RVOL (soft, never gates)

CVD and RVOL are two independent layers added on top of the existing Edge gate. They feed the
SAME shared Edge helper (`compute_trade_edge_components`) so gate score == display score still holds.

## CVD = HARD directional veto, FAIL-OPEN
- Ingested as new webhook alert types `CVD_BULLISH` / `CVD_BEARISH`, per instrument (ticker in
  payload). Stored in `CVD_BY_TICKER` as state (`"bullish"`/`"bearish"`/None), optional numeric
  value, and a derived `direction` (`"rising"`/`"falling"`/None — from value-vs-prior, None when
  unmeasurable). CVD alerts are DATA-ONLY: score 0, not part of bias/level building; the webhook
  acks `status: cvd_updated` and returns early.
- In `evaluate_strict_setup` it is a hard filter: **reject LONG when CVD is bearish, reject SHORT
  when CVD is bullish.** It contributes a `cvd_ok` boolean to `gates_ok` and a `cvd_conflict_*`
  entry to failed gates when it vetoes.
- **FAIL-OPEN is the core safety rule:** when CVD is unknown for the instrument, `cvd_ok` is True —
  the filter never blocks on missing data. This is why SWING is unchanged today (no CVD feed yet):
  CVD applies to BOTH modes but fail-open makes it a no-op until a feed arrives.
- When CVD CONFIRMS the trade direction it credits a **+10 "CVD Agreement"** Edge component
  (`cvd_confirmed` in `EDGE_COMPONENTS`). Confirmation alone never makes a trade — it is one
  confluence among the others, behind the READY floor.
**Why fail-open, not fail-closed:** a confirmation filter that blocks on absence would silently kill
every trade the moment the feed lags or is unconfigured. The veto only fires on a PRESENT, CONFLICTING reading.
**How to apply:** any future directional "confirmation" signal (delta, order-flow, footprint) should
follow the same shape — hard veto on conflict, fail-open on absence, +N Edge on agreement.
- **Single per-instrument slot = last-writer-wins.** `CVD_BY_TICKER[inst]` holds ONE state, so if the
  user routes multiple directional volume indicators into the CVD types (seen live: a "CVD"
  zero-cross/divergence indicator AND a separate "volume delta" indicator, both sending
  `CVD BULLISH`/`CVD BEARISH`), they overwrite each other and the gate reads whichever fired most
  recently. If they disagree, the committed state no longer flips per alert — an opposite indicator
  must now win 2 DISTINCT 1-min candles (see flip debounce below) before it flips; this is config,
  not a bug. Combining them (e.g. bullish only when BOTH agree, else neutral) is NOT possible via
  alert formatting alone; it needs code (an aggregation layer keyed by source). Diagnose unexpected
  CVD flips by checking how many distinct indicators feed the CVD types before touching the gate logic.

## CVD committed-state flip debounce (2 distinct 1-min candles)
The committed `state` the gate reads only FLIPS after the OPPOSITE direction is signaled on **2 distinct
1-minute candles** — a single opposite alert no longer flips instantly. Per-instrument debounce metadata
lives alongside `state` in `CVD_BY_TICKER[inst]`: `pending_dir` + `opposite_count` + `last_opposite_minute`
(minute bucket = `int(now_utc().timestamp() // 60)`, i.e. SERVER-RECEIPT minute, NOT a chart-candle ts).
- First-ever reading commits immediately (preserves fail-open baseline).
- A signal AGREEING with committed `state` clears the pending flip (`opposite_count` → 0).
- An opposite signal increments `opposite_count` once per NEW minute bucket — same-minute repeats never
  double-count; a skipped minute does NOT reset.
- `opposite_count` reaching 2 flips committed `state` and resets the pending metadata.
**The gate is UNCHANGED:** `evaluate_strict_setup` still reads ONLY committed `state`; `pending_dir`/
`opposite_count` are DISPLAY-ONLY (surfaced as `cvd_pending`/`cvd_opposite_count` in `alert_diagnostics`,
neutralized in the market-closed override like the other CVD fields).
**Why:** a single noisy opposite tick was flipping the hard veto and whipsawing setups; requiring 2
distinct candles debounces transient flips while keeping fail-open. **How to apply:** any change to CVD
flip logic must keep the gate reading committed `state` only — never let the gate read `pending_dir`.
"Distinct candle" = server-receipt minute today; if it must mean chart candle, bucket on a TV-sent
candle timestamp instead.

## RVOL = SOFT Edge modifier, NEVER gates
- Optional `rvol` field parsed off ANY webhook payload into `RVOL_BY_TICKER` (no dedicated alert type).
- `_rvol_adjustment(rvol)`: **+10 if ≥1.5, 0 if missing or 1.0–1.5, −5 if <1.0.** Passed as
  `rvol_adj` into `compute_trade_edge_components` and shown as an "RVOL" breakdown line.
- It NEVER appears in `gates_ok` / failed gates and NEVER forces WAIT — it only nudges the Edge score
  (which can in turn move a borderline setup across the READY floor, but RVOL itself is not a gate).
**Why:** RVOL is a quality signal, not a permission signal; making it a gate would reject valid
structure on a quiet bar.

## Edge ceiling + thresholds that came with this
- `EDGE_SCORE_MAX` raised 100 → 120 (the six confluences sum to 100; CVD +10 and RVOL +10 lift the
  ceiling). Every `/100` display (embeds + dashboard bars) must use `EDGE_SCORE_MAX`, injected into
  the dashboard JS via a `__EDGE_MAX__` placeholder replace at the `/dashboard` return.
- SCALP READY floor 75, A+ at ≥85 (see edge-score-card-block for grade/strength bands). SWING stays
  80/80 byte-for-byte; CVD fail-open + RVOL-never-gates are what keep SWING identical.

## Display path
CVD/RVOL reach the dashboard via `alert_diagnostics` (cvd_state/cvd_value/cvd_direction/
cvd_agreement + rvol_value/rvol_adj) — NOT via top-level `/status` keys (those are whitelisted out;
the top-level result stamps are in-process plumbing only). The market-closed override neutralizes
both the top-level stamps and the alert_diagnostics CVD/RVOL keys, and must keep doing so.
