---
name: Candidate Preview Panel
description: How the Main Brain Trade Plan panel is wired — what functions produce the preview, field names, and 5-state model.
---

## Rule
`TradePlanPanel` reads from `p.candidate_preview` (NOT `p.strategy_scanner.trade_plan`).
The strategy_scanner block reads `tp.get("entry")` / `tp.get("stop")` which don't exist in
`build_strict_trade_plan` output — those fields are always None, causing the old panel
to always show "No actionable trade plan".

**Why:** `build_strict_trade_plan` returns string-keyed fields (`entry_zone`, `stop_loss`,
`target1`, `rr`, `management.entry`) — not `entry`/`stop`/`targets`. The new
`_mb_candidate_preview()` reads the canonical field names directly.

## Backend functions (app.py)

- `_mb_preview_from_plan(plan, status, direction)` — read-only field extractor from a
  `build_strict_trade_plan` dict. Never re-calculates. Returns the normalized display dict.
- `_mb_candidate_preview(result, errors)` — priority chain: READY → POTENTIAL_Long →
  POTENTIAL_Short → NO_CANDIDATE. Fail-open to UNAVAILABLE on exception.
- Wired in `build_main_brain_payload()` → returned as `"candidate_preview"` key.

## Priority chain
1. READY: `result["strict_label"]` contains "READY" AND `result["trade_plan"]["trade_plan"]` is True
2. POTENTIAL/Long: `result["directions"]["Long"]["potential_plan"]["trade_plan"]` is True
3. POTENTIAL/Short: same for Short
4. NO_CANDIDATE: nothing met

## Key field names (from `build_strict_trade_plan`)
- `entry_zone` (string: "52570.00–52578.00")
- `stop_loss` (string)
- `target1` (string) → mapped to `take_profit` in preview
- `rr` (string: "1:1")
- `risk_points` (float)
- `risk_dollars_per_contract` (float)
- `atr_pts` → mapped to `atr`
- `atr_multiplier` (float)
- `stop_distance_ticks` → mapped to `stop_ticks`
- `stop_valid` (bool)
- `management.entry` (float) → mapped to `preview_price`

## 5 UI states in TradePlanPanel
- READY → green Pill, full plan grid
- POTENTIAL → amber Pill, full plan grid + WAITING FOR list + preview disclaimer
- NO_CANDIDATE → muted "No trade candidate developing"
- UNAVAILABLE → UnavailableNote
- Active trade + POTENTIAL → "FUTURE CANDIDATE" label (not confused with live position)

## Normalizer
`candidate_preview` is passed through unchanged from the backend in
`normalizeMainBrainPayload()` — no transformation needed.

## Test file
`test_candidate_preview.py` — 18 tests (Cases A–K + regressions), all pass.
