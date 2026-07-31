---
name: Directional Symmetry Audit (Phase 7M)
description: Read-only audit of Long vs Short balance across all signal layers; final classification and fixture lessons.
---

## Verdict
**Market-Driven** — code is symmetric. 23 mirrored-pair tests in `test_directional_symmetry_7m.py` confirm no code-driven bias.

## Intentional Asymmetries (documented, non-defects)
1. **Long wins exact tie** (line ~8094/8105): `_cand_key("Long") >= _cand_key("Short")` — legacy default, display-only.
2. **Neutral conflict → Long** (line ~7635): display-only diagnostic default.
3. **Eligibility key `{inst}::{mode}`** has no direction — both Long/Short trades count toward same GHOST_ONLY threshold (intentional).

## Fixture Lessons (save rework)
- `MITIGATED_FLAG_BY_TICKER = {INST: True}` + mocked `is_near_mitigated_zone → (True, 0.001)` marks the zone as **Consumed** → `zone_valid_soft = False`. Use `{INST: False}` for SCALP soft-zone tests so the zone reads "Tested" → `True`.
- `CURRENT_PRICE_TS_BY_TICKER` is keyed by **instrument** (`"MGC"`) not ticker (`"MGC1!"`). Wrong key → data-stale brake fires → WAIT instead of expected result.
- Stale-VWAP + Short signals triggers the Long tie-break (candidate selector with equal _cand_key tuples): test symmetry via `gate_debug.vwap_confirmed`, not raw delta scores.
- `structure_confirmed` gate requires a fresh confirmation candle AFTER the structure event via `_after_anchor`; including both in the fixture at T_CONFIRM > T_STRUCT is necessary but may still hit other SCALP thresholds.

## Files
- `artifacts/tradingview-webhook/test_directional_symmetry_7m.py` — 23 tests
- `artifacts/tradingview-webhook/app.py` — `GET /directional-balance` route (owner-only, fail-open)
- `artifacts/api-server/src/routes/flask-proxy.ts` — `/directional-balance` in `BOT1_ROUTES`
- `artifacts/home/src/pages/MainBrain.tsx` — `JDirectionalTab` + `Direction ↕` tab in Journal

**Why:** Both the test fixture lessons and the documented asymmetries are non-obvious and would cause repeated wasted debugging if not recorded.
