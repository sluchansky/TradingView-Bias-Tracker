---
name: Adding a futures instrument — the lockstep surfaces & two non-obvious invariants
description: Where a new instrument must be wired (live + backtest + tests + UI + Pine), and two cross-file constraints that aren't visible from any single file.
---

# Adding a futures contract spans many surfaces — wire them in lockstep

To find every site, grep an existing symbol (e.g. `MNQ`) across the repo. A new
instrument must be added to ALL of:
- Live registry in `app.py` (`INSTRUMENT_SPECS`) — the money-path source of truth.
- `backtest_engine.py`: `BT_SPECS`, `VALID_SYMBOLS`, `SYMBOL_ALIASES`,
  `_synthetic_candles` per-symbol scale, and `_self_test` coverage.
- Tests: `test_backtest_stop_parity.py`, `test_dynamic_stop.py`.
- Backtest UI in `app.py` (the Symbol `<option>` dropdown + helper text — static HTML).
- `artifacts/home/src/pages/Home.tsx` instrument cards (display only).
- The repo-owned Pine webhook scripts (see `pine-webhook-source-scripts.md`) — they
  auto-detect the instrument and default unknowns to MGC.

**Why:** missing one surface = silent partial support (works live but backtest can't
parse it, or the UI can't select it, or TradingView webhooks misroute to MGC).

## Invariant 1 — backtest specs must match live specs (stop parity)
`BT_SPECS` tick/point/min-stop/buffer values MUST be byte-identical to the live
`INSTRUMENT_SPECS`, or live↔backtest stop/target geometry diverges. There is a
dedicated `test_backtest_stop_parity.py` guarding this; extend it for every new
instrument (long/short SCALP floor-widen + SWING hard-reject paths).

## Invariant 2 — overlapping price bands → fail-closed detection
MES's plausible price band overlaps MGC, and MYM's overlaps MNQ. So price-only
auto-detect intentionally **refuses** ("fits multiple instruments") when >1
instrument matches the median price; **filename detection (or the explicit
dropdown) is the primary path**. Do NOT "fix" the refusal to force a guess — a
silent wrong-symbol backtest is worse than an honest "ambiguous, pick a symbol".

## Goldens are your additive-only proof
Pure instrument-add work leaves `scalp_golden`/`swing_flagoff`/`parity`/
`instrument_isolation` byte-identical (new symbols don't change MGC/MNQ scoring).
If a golden moves on an instrument-add, you touched shared logic by accident —
investigate, don't rebaseline.
