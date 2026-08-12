---
name: INTRADAY_TREND strategy
description: How the INTRADAY_TREND trading mode is implemented — aliasing pattern, wiring points, gate logic, dashboard panel.
---

## Rule
INTRADAY_TREND reuses the full SWING plumbing without duplicating code.

**Why:** The spec said "replace SWING wording" — not build something new.  All risk engine, ghost, execution gateway, and dashboard surfaces are shared.

## Aliasing pattern (MODES dict)
```python
MODES["INTRADAY_TREND"] = dict(MODES["SWING"])
```
Added immediately after the closing `}` of the MODES literal.  SWING entry stays unchanged so historical journal records (trading_mode="SWING") remain readable.

## Gate logic — _it_entry_veto_reasons (FAIL-CLOSED)
Three money-path gates (demote-only, never create trades):
1. MNQ-only — any other instrument → instant veto
2. Time restriction — ENTRY_BLOCKED at 14:30 ET, FORCE_FLAT at 15:55 ET (env: IT_LAST_NEW_ENTRY_TIME, IT_FORCE_FLAT_TIME)
3. Location quality — MID_RANGE (no structural anchor within 1×ATR) → veto; POOR passes through

## full_analysis wiring (3 touch points)
1. `if _swing_htf_enabled(TRADING_MODE) or TRADING_MODE == "INTRADAY_TREND":` — compute swing_ctx (HTF data reuse)
2. IT veto block runs after SWING veto; `_it_ctx = None` init BEFORE the conditional so it's always in scope for #3
3. Lazy result attachment: if `_it_ctx` is None (WAIT path / market closed), compute fresh via `compute_intraday_trend_context`

## Dashboard panel
Reuses `mod-swingdiag` with two sub-divs (`swd-it-content` / `swd-swing-content`).  JS renderer toggles visibility based on `d.trading_mode === 'INTRADAY_TREND'`.  Panel title swapped via `panelTitle.childNodes[0].textContent`.  Both emoji use `\\u{...}` / `\\uXXXX` escapes (JS-in-Python backslash trap).

## /status serialization
`"intraday_trend_diagnostics": _it_diag_block(a)` added immediately after `swing_diagnostics` in the curated whitelist.

## Tests
`artifacts/tradingview-webhook/test_intraday_trend.py` — 82 tests, all passing.
`full_analysis` signature: `full_analysis(current_price_override=None, ticker_override=None, cooldown_active=False)` — no alert_type/direction args; reads from global state.

## How to apply
- To add another IT gate: add a veto entry in `_it_entry_veto_reasons` ONLY.  Never touch the SWING veto.
- To enable live execution: remove the shadow-only guard (currently INTRADAY_TREND_SHADOW intent is enforced at the `_maybe_auto_execute` level via ghost infrastructure).
- To add another IT-eligible instrument: remove or widen the `inst != "MNQ"` check in `_it_entry_veto_reasons`.
