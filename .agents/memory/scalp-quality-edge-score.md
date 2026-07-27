---
name: compute_scalp_quality does NOT read edge_score
description: edge_score is accepted as a parameter but never consumed inside the function body; safe reuse of gate-path result at display site.
---

## Finding
`compute_scalp_quality(direction, current_price, vwap_value, vwap_status, nearest_supply, nearest_demand, plan, edge_score, inst, atr_pts)` accepts `edge_score` but NEVER reads it in the function body. Verified by grep of lines 8480–8830 in app.py.

## Consequence
The two calls to `compute_scalp_quality` in `full_analysis`:
1. Gate/veto path (line ~22958) — uses legacy `edge_score` from `calculate_edge_score`
2. Display path (line ~24162) — uses `result.get("edge_score")` (authoritative 0-110 score)

These pass different `edge_score` values BUT produce IDENTICAL output because `edge_score` is never read. Safe to reuse call-1 result at call-2 site.

## Dedup implementation
`_lb_sq_no_veto` cache variable in `full_analysis`:
- Set when call-1 runs without a veto: `_lb_sq_no_veto = _sq`
- Reused at call-2 site if `_lb_sq_no_veto is not None and _sq_dir is not None and _sq_dir == strict_direction`
- Direction guard: if any override between call-1 and call-2 changed the direction (analyst/pro review), fall through to a fresh compute

**Why:** Avoids redundant arithmetic+geometry in the display path when the gate path already computed the same result.

**How to apply:** If `compute_scalp_quality` is ever modified to READ `edge_score`, the dedup branch at `_lb_sq_no_veto is not None` MUST be removed or the cache must carry `edge_score` alongside it.
