---
name: INTRADAY_TREND native gate routing
description: IT bypasses the inherited SWING strict gate; uses its own pipeline as sole READY/WAIT authority. SWING result is shadow-only.
---

## Rule
For `INTRADAY_TREND`, `full_analysis` no longer gates IT execution on the inherited SWING strict label (edge≥85 / zone_valid / vwap_confirmed / structure_confirmed). The IT-native pipeline is authoritative:

`compute_intraday_trend_context` → `build_intraday_trade_plan` → `_it_entry_veto_reasons` → verdict

The SWING strict result is captured in `_it_legacy_strict` (and attached to `intraday_trend_context["legacy_strict_verdict"]`) for shadow analytics only — no execution authority.

**Why:** `MODES["INTRADAY_TREND"] = dict(MODES["SWING"])` caused IT to inherit EDGE_READY_THRESHOLD=85 + zone/vwap/structure prerequisites before the IT-native gates ever got to decide. The IT pipeline has its own hard gates (family, confirmation, structural stop, ATR bounds, chase, RR, daily cap) that are more appropriate for intraday trading.

**How to apply:**
- Two surgical edits in `full_analysis` (around lines 28981 and 28992 in pre-edit numbering):
  1. `_it_ctx` pre-compute: removed `strict_label in ("Strong Trade", "Possible Trade")` guard — now computes whenever `TRADING_MODE == "INTRADAY_TREND" and strict_direction`.
  2. Added `if TRADING_MODE == "INTRADAY_TREND" and strict_direction:` branch BEFORE the SCALP/SWING `elif strict_label in (...)` branch. IT builds its trade plan and sets verdict here; SCALP/SWING never enter this branch.
- `_it_legacy_strict` dict is attached to `intraday_trend_context["legacy_strict_verdict"]` at the display block.
- Every IT-native hard gate is unchanged (veto layer + plan builder apply them).
- SCALP/SWING paths are byte-identical (the IT branch has a TRADING_MODE guard; goldens confirm it).

## Tests
`tests/test_intraday_trend_native_gate.py` — 43 tests covering spec Tests A–M + extras.
All 259 IT tests pass. All goldens (parity / scalp_golden / dual_sim / breakout_mode) clean.

## Do NOT
- Change the 85 threshold for SWING.
- Let `_it_legacy_strict` affect the verdict in any way.
- Add the EARLY READY band to IT (IT uses plain LONG/SHORT READY only).
- Add `evaluate_strict_setup` gating back to the IT pre-compute.
