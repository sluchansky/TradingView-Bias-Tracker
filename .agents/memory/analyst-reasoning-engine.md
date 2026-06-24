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
- FVG / Order Blocks are **not tracked yet** — the engine reasons over supply/demand
  zones, VWAP, structure, sweeps, CVD, volume & HTF only; it explicitly marks FVG/OB as
  not-tracked rather than inventing detection.
- Evidence weights `_ANALYST_W` (choch/bos 20, vwap/sweep/cvd/volume 15, zone/session/htf
  10); thresholds READY≥70 / margin≥10 / nothing<25.
