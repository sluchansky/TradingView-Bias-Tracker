---
name: Analyst Reasoning Engine
description: Professional-analyst layer (Context→Thesis→Evidence→Risk→Verdict) over EXISTING signals; display-first, optional fail-closed veto. Invariants any change must keep.
---

# Analyst Reasoning Engine

A professional-analyst layer that reasons over the **already-assembled** `full_analysis`
result (alerts/strategies read as EVIDENCE, never as commands) and emits an independent
verdict (READY LONG/SHORT / WAIT / NO TRADE) that can disagree with the gate.

**Rule:** the engine lives ENTIRELY on top of `full_analysis`' assembled `result`, AFTER
the authoritative strict verdict is decided. It must never feed `build_strict_trade_plan`
/ `evaluate_strict_setup`.
**Why:** the 4 goldens call those strict funcs directly, so keeping the analyst above them
makes the layer auto byte-identical (all 4 goldens stay green for free).
**How to apply:** add analyst logic only after the strict result exists; never inside the
strict path.

## Display-first / veto invariants
- `_analyst_engine_enabled()` defaults **ON** (display); `_analyst_gate_enabled()`
  (env `ANALYST_GATE_ENABLED`) defaults **OFF** (money-path veto).
- The veto, when ON, may ONLY **demote** an `is_actionable()` gate verdict to WAIT — it
  must NEVER promote/force a trade. On fire it drops `trade_plan`, sets both direction
  cards to WAIT, appends "Analyst veto" to `alert_diagnostics.rejected_reasons`, and
  recomputes `decision_support` (it was computed earlier from the pre-veto plan and would
  otherwise stay stale).
- `veto_would_fire = is_actionable(gate) AND not agrees` — computed even when the flag is
  OFF (display), but only acted on when the flag is ON.
- Fail-open: caller wraps `compute_analyst_reasoning` in try/except → `_analyst_neutral_block`.

## Parity / serialization gotchas
- Single-return-path: `result["analyst"]` MUST exist on BOTH the market-open branch and
  the market-closed override (closed sets a neutral stub) or hard-indexed consumers 500.
- `/status` is a curated whitelist — `"analyst"` had to be added explicitly or it's None
  on the wire despite being computed (see curated-endpoint-serialization).
- **R:R quirk:** trade plans carry `rr` as a DISPLAY string ("1:1", "1:4",
  "T1 .. / T2 .."); the number is in `rr_num`. The analyst risk check reads `rr_num`
  first, then parses the string ("1:X" ratio, else first number). `float(rr)` on the
  display string raises → phantom "no acceptable R:R" → analyst over-WAITs and (if veto
  ON) over-vetoes valid setups. (Same rr/rr_num split as fixed-1to1-rr.)

## Scope
- FVG / Order Blocks ARE now tracked as **analyst-only display evidence** (alert side
  `analyst`, score 0, members of `ANALYST_TYPES`): sourced from `pine/fvg_ob.pine`, stored
  in `ALERT_HISTORY`, read via `_recent_smc_signals(inst)` (recency-windowed
  `_SMC_RECENCY_MIN`, per-instrument, fail-open, `continue`-on-stale so robust to
  out-of-order deque entries) and folded into the bull/bear evidence stacks,
  `what_needs_next`, and `professionals_watching` — NEVER into the gate's `_confluences`
  or any scoring/level set.
- The engine reasons over supply/demand zones, VWAP, structure, sweeps, CVD, volume, HTF
  AND FVG/OB.
- Evidence weights `_ANALYST_W` (choch/bos 20, vwap/sweep/cvd/volume 15, zone/session/htf
  10, fvg/ob 10); thresholds READY≥70 / margin≥10 / nothing<25.

## Display-only ingestion (the "analyst" alert side) — money-path invariant
- A non-scoring display-only alert side is NOT safe just because it's excluded from
  scoring + level sets. Recognized alerts otherwise fall through the SHARED `/webhook`
  body: price stores (`CURRENT_PRICE*`), `_update_intraday_tracker`, zone-broken expiry
  (`ZONE_BROKEN_AT["alerts_since"]`), and the `_WEBHOOK_JOBS` worker (which runs
  `full_analysis`/journal/Discord/auto-exec). So a display-only alert could mutate a gate
  input or BE THE TRIGGERING webhook that dispatches an already-ready setup.
- **Rule:** `ANALYST_TYPES` must SHORT-CIRCUIT early in `/webhook` — after ticker/price
  parsing, BEFORE the price-store block — appending only the `ALERT_HISTORY` record
  (+ `LAST_ALERT_AT` liveness) and returning `analyst_signal_stored`. It reaches the
  analyst engine on the NEXT `full_analysis`/`/status` poll, not by enqueuing its own job.
- **Why:** confirmed by architect review; without the short-circuit FVG/OB violated
  display-only and could auto-execute. Guarded by `check_fvg_ob.sh` test 6 (asserts no
  enqueue + no gate-input mutation).
- `LAST_ALERT_AT` is dashboard-liveness only (not a gate/sizing/exec input); it is hoisted
  into `/webhook`'s top-level `global` so both the short-circuit and the normal path can
  assign it without a "assigned before global declaration" SyntaxError.

## Pro-analyst display blocks (market_phase / analyst_outlook / entry_probability)
- `result["analyst"]` now carries three extra DISPLAY-ONLY blocks plus `game_plan.next_opportunity`:
  `market_phase` (8 phases in `_ANALYST_PHASES` + confidence + reasons + signals),
  `analyst_outlook` (intent/control/liquidity/next-entry/invalidation/continuation-vs-reversal
  + multi-line `wait_reasoning`), and `entry_probability` (current EQ score + projected scenarios).
  Each has a `*_neutral()` twin and is mirrored into `_analyst_neutral_block`; new keys must be
  added to BOTH twins or the single-return-path / hard-indexed-consumer 500 invariant breaks.
- `entry_probability` is a NEUTRAL placeholder inside `compute_analyst_reasoning`'s return and is
  OVERWRITTEN post-hoc in `full_analysis` (right after the memory_review overwrite) because it
  CONSUMES `result["entry_quality"]`, which is computed AFTER the analyst runs. It only reads that
  block (improvements/projected_high/better_entry_zone) — never recomputes EQ.
- **Mode-correct ATR wiring gotcha (regressed once):** the Market-Phase VWAP-extension signal
  needs the right ATR per mode, sourced via the single helper `_analyst_phase_atr(mode, swing_ctx, vol)`:
  SCALP = `vol["atr_pts"]` (1m), SWING = `swing_ctx` HTF ATR `atr_1h`→`atr_4h`→`atr_daily`
  (NOT `atr`/`atr_pts`/`htf_atr` — those keys don't exist in `compute_swing_context`'s schema, so
  reading them silently disabled the extension on every real SWING run). Helper is pure/fail-open,
  returns a positive float or None (extension "unavailable", never the wrong-TF ATR). Guarded by
  `analyst_pro_smoke.py` section 6; whole feature guarded by `check_analyst_pro.sh` (not a workflow —
  workflow slots are full at 11/10 — run it directly like the other check scripts).
