---
name: 9:30 ET Breakout Mode
description: Display-only opening-range breakout advisory — what it is, its invariants, and the dedicated 09:30 OR that must stay separate from the 08:00 engine.
---

# 9:30 ET Breakout Mode (display-only advisory)

A professional opening-range breakout ADVISORY at the 09:30 ET equity cash open:
builds a dedicated OR, classifies upside/downside break **or** liquidity-sweep
reversal, confirms (close-beyond / volume-RVOL / VWAP side / BOS-CHOCH / no S-D
wall / not-overextended), grades quality 0–100, targets a dynamic 1:2–1:5 R:R with
a stop on the opposite OR edge, lists failure rules + trade-management steps, and
renders a dashboard panel. NO money path in this phase (advisory only).

## Hard invariants (do not regress)
- **Flag-gated, default OFF** via `BREAKOUT_MODE_ENABLED = _env_flag_on(..., default_on=False)`.
  When OFF: `compute_breakout_mode()` returns **None** (not a neutral block), both
  wiring sites are guarded by `if BREAKOUT_MODE_ENABLED:` so `result["breakout_mode"]`
  is genuinely **absent**, and `_update_breakout_or_tracker()` is a hard no-op. This is
  why the 4 strict goldens + pycompile stay byte-identical — the OFF default adds nothing.
- **Dedicated 09:30 OR**: `BREAKOUT_OR_BY_TICKER` / `_update_breakout_or_tracker` /
  `_breakout_or` are a SEPARATE tracker from the 08:00 strategy-engine OR
  (`OPENING_RANGE_START_ET=8.0`, `INTRADAY_BY_TICKER`). **Never retime the 08:00
  engine to get a 9:30 range** — that mutates the goldens. Two ranges coexist on purpose.
- **Closed-override key parity**: `_breakout_neutral_block()` must have the EXACT same
  key set as a populated `compute_breakout_mode()` return; the closed-market path mirrors
  the key. Smoke asserts `set(neutral) == set(populated)`.
- **Display-only**: only sets `result["breakout_mode"]` + feeds the OR tracker; never
  touches verdict / directions / trade_plan / sizing / dedupe / broker.
- `/status` whitelists `breakout_mode` (curated dict — a new field is None on the wire
  unless added there too).
- Dashboard panel is a normal **visible** `.mod` (`id=mod-breakout`, NOT `mb-hidden`);
  `renderBreakoutMode` toggles `style.display` like the dual-sim panel (hidden when the
  block is absent / flag OFF). Real 🚀 glyph (astral emoji as a real char is fine; the
  `\uXXXX` surrogate-escape form 500s at UTF-8 encode).

## Plumbing notes
- The webhook tick feed calls `_update_breakout_or_tracker(resolved_inst, parsed_price)`
  right after `_update_intraday_tracker` (both fail-open, flag-guarded).
- Reuses `_strategy_signal_snapshot(inst)` for structure_long/short, volume_spike_fresh,
  rvol_value; reads `cfg("RVOL_CONFIRM_THRESHOLD")` / `cfg("VOL_HIGH_CAUTION")`.
- Fully FAIL-OPEN: any error in compute degrades to `_breakout_neutral_block(...)`.
- Smoke: `.local/state/check_breakout_mode.sh` (drives now_utc + snapshot monkeypatch);
  registered as the `breakout_mode` validation step.

## Out of scope here
- **Phase D auto-execute** (`BREAKOUT_AUTO_EXECUTE_ENABLED`) was deliberately NOT built —
  it is a separate gated money-path layer requiring its own architect review and explicit
  user sign-off before/after. Do not bolt auto-execute onto the advisory without that.
