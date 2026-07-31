---
name: Cleanest Trade Available button
description: Main Brain feature that scans all instruments × modes and surfaces the best setup. Pure frontend ranking, no backend changes.
---

## Rule
`rankCandidates()` in `src/lib/cleanestTrade.ts` is the single source of truth for ranking.
It is an exact port of `pickCleanestSetup()` from the legacy dashboard JS — do NOT add a second algorithm.

**Why:** The old home page had a working selector; this feature must stay identical to it so the operator sees consistent results across both UIs.

**How to apply:** Any future changes to ranking logic must be made in `cleanestTrade.ts` AND mirrored in the Python port inside `test_cleanest_trade.py`. Run `pytest test_cleanest_trade.py` to verify (29 tests).

## Key files
- `artifacts/home/src/lib/cleanestTrade.ts` — pure ranking utility (no DOM/fetch/React)
- `artifacts/tradingview-webhook/test_cleanest_trade.py` — 29 deterministic Python tests (Cases A–L)
- `artifacts/home/src/pages/MainBrain.tsx` — `CleanestTradeButton`, `CleanestTradeModal`, state + scan in `MainBrain()`

## Canonical algorithm
```
for each candidate:
  act  = 1 if verdict ∈ ACTIONABLE_VERDICTS else 0
  edge = brain.score.value ?? edge_score ?? 0
  better = (cand.act != best.act) ? (cand.act > best.act) : (cand.edge > best.edge)
```
Tie (same act + same edge): first in iteration order wins (MGC_SCALP, MGC_SWING, MNQ_SCALP, ...).

## Scan target
`/api/status?ticker=X&mode=Y` — 8 parallel requests (4 instruments × 2 modes).
Endpoint is in `BOT1_ROUTES` (flask-proxy.ts line 116). Auth via `getAuthHeader()` (same pattern as the main-brain fetch).

## Button placement
Between Market State Strip and section breadcrumb in `MainBrain()` render.
Modal is rendered OUTSIDE the scroll container (before `</style>`) as a fixed-position overlay.

## Money-path isolation
DISPLAY-ONLY. No execution, no gate changes, no backend mutations.
Button scan is triggered on click (on-demand), not on every 7s poll.
