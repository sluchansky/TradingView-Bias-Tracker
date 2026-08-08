---
name: FVG/IFVG Sequence Engine (Step B)
description: Shadow state machine turning FVG zone lifecycle events into trade candidates. Architecture, wiring, and safety invariants for Step B.
---

# FVG/IFVG Sequence Engine — Step B

## What it is
`fvg_sequence_engine.py` — a deterministic shadow state machine that tracks each FVG/IFVG zone through a multi-step sequence until it either reaches `SHADOW_READY` or expires/invalidates. **Shadow/display-only throughout** — no gate, scoring, sizing, TradersPost, or execution changes.

## Two setup families
- `FVG_CONTINUATION` ("FVG_CONTINUATION"): Plain FVG touch → hold → structure → momentum → entry
- `IFVG_REVERSAL` ("IFVG_REVERSAL"): Failed FVG → IFVG inversion → retest → hold → structure → momentum → entry

## State machine constants (module-level strings)
Continuation path: `SC_RETURN_PENDING → SC_TOUCHED → SC_HOLD_PENDING → SC_HOLD_CONFIRMED → SC_STRUCTURE_PENDING → SC_MOMENTUM_PENDING → SC_ENTRY_WINDOW → SC_SHADOW_READY`
Reversal prefix: `SC_INVERTED → SC_RETEST_PENDING → SC_RETESTED` then shared hold→structure→momentum→entry tail.
Terminal: `TERMINAL_SEQ_STATES = frozenset({SC_SHADOW_READY, SC_EXPIRED, SC_INVALIDATED})`

## Key design decisions
- **Structure events** filtered by timestamp: must be AFTER `touch_at` (continuation) or `retest_at` (reversal). Alert types: `BOS DEMAND`/`CHOCH DEMAND` = bullish; `BOS SUPPLY`/`CHOCH SUPPLY` = bearish.
- **Momentum**: 5 named checks (displacement, close_strength, CVD direction, volume expansion, movement from zone). Requires 3 of 5. Never opaque.
- **Entry window**: `ENTRY_AVAILABLE` (<60s, <1 ATR from zone), `ENTRY_LATE` (60-120s), `ENTRY_CHASING` (≥1 ATR away — fires BEFORE target-consumed check), `ENTRY_EXPIRED` (≥120s). Only PRIMARY + ENTRY_AVAILABLE reaches SHADOW_READY.
- **IFVG hold** detected from bar data (not zone status, since fvg_engine marks IFVG zones terminal at ST_RETESTED). Bullish: `close > zone_upper`; Bearish: `close < zone_lower`.
- **Primary election**: per-instrument, per-direction; most-advanced state then highest `rank_score` wins. Secondaries tracked but never reach SHADOW_READY.
- **Shadow plan**: entry=close, stop=zone_edge±0.2×ATR, targets 1R/2R/3R. Always: `shadow_only=True, production_ready=False, execution_eligible=False`.
- **CHASING fires before target-consumed**: price ≥1 ATR from zone always classifies as CHASING even if target is 75%+ consumed. This is by design (ordering in `_classify_entry_window`).

## DB table
`fvg_shadow_sequences` — already created via `executeSql` (3 indexes). `check_fvg_seq_db_ready()` probed at boot (after FVG engine probe). Fail-open: sequences tracked in-memory if table missing.

## App.py wiring points
- `_fvg_bar_close()` → calls `fvg_sequence_engine.process_bar_close(inst, bars, zones, cvd=..., alert_history=list(ALERT_HISTORY))` after `fvg_engine.process_bar_close`
- `full_analysis` seam → `result["fvg_sequences"] = _fse_mod.get_all_summary()` (after fvg_summary injection, ~line 27739+10)
- `build_main_brain_payload` → `"fvg_sequences": (result or {}).get("fvg_sequences")` (after fvg_summary, ~line 25913+1)
- `/main-brain/chart` endpoint → `"fvg_sequences": _fvg_seq_chart_data_safe(inst)` (after fvg_zones key)
- Helper: `_fvg_seq_chart_data_safe(inst)` → calls `fse.get_chart_data(inst)`, fail-open []
- Flask route: `GET /fvg/sequences` — no `@_owner_required` (same pattern as /volatility-intelligence; Express auth sufficient)

## Proxy whitelist
`/fvg/sequences` added to `BOT1_ROUTES` in `artifacts/api-server/src/routes/flask-proxy.ts`.

## Frontend wiring
- **`MainBrain.tsx` `FVGScannerPanel`**: upgraded to show Step A zone cards + Step B primary sequence cards (SeqCard component). Reads `p.fvg_summary` (Step A) + `p.fvg_sequences` (Step B). Per-sequence: zone bounds, step progress bar, momentum count, entry window label, next-required-event hint, shadow plan snippet, explain-why toggle.
- **`LiveMarketChart.tsx`**: Added `FvgZoneOverlay` + `FvgSequenceOverlay` interfaces to `ChartResponse`. New `showFvg` toggle (default OFF). `fvgLinesRef` cleans up price lines. FVG price lines: upper+lower per zone, color by direction/status (SHADOW_READY=vivid, IFVG=pink, normal=indigo/rose). CHASING lines → solid, IFVG → large-dashed, normal → dashed.

## Public API (fvg_sequence_engine.py)
- `process_bar_close(inst, bars, zones, cvd=None, alert_history=None)` — main entry point
- `get_sequences(inst, include_terminal=False)` → list of dicts
- `get_summary(inst)` → `{instrument, total, active, primary_sequences, all_sequences, ...}`
- `get_all_summary()` → `{inst: summary_dict, ...}`
- `get_chart_data(inst)` → list of chart-overlay dicts with zone_lower/upper/direction/setup_family/current_state
- `_classify_entry_window(seq, bar, atr, now)` → `{label, ...}` — public for testing
- `_build_shadow_plan(seq, bar, atr)` → shadow plan dict — public for testing
- `check_fvg_seq_db_ready()` — boot probe
- `reset_all()` — test helper, clears SEQUENCES_BY_INST

## Test coverage
`test_fvg_sequence_engine.py` — 55 tests; all pass. Covers: FVG_CONTINUATION (bull+bear), IFVG_REVERSAL (bull+bear), sequence safety/isolation, entry window classification, shadow safety proofs (no gate mutation via source inspection), UI/chart data contracts, regressions (Step A still 61/61 tests).

**Why:** "target consumed" was removed as an expiry trigger because CHASING (≥1 ATR from zone) always fires first for realistic bar closes; keeping the check in would require price to be both near the zone AND past target, which is geometrically impossible for typical 1R targets. CHASING is the correct expiry mechanism when price is too far extended.
