---
name: Per-direction dashboard toggle (Long/Short)
description: directions{} blocks are additive/display-only; invariants that keep them from corrupting the authoritative verdict/Edge Score.
---

The dashboard Long/Short toggle shows BOTH the bull case and the bear case: each
`full_analysis` result carries `result["directions"]["Long"|"Short"]` with a
per-side checklist {bos,choch,confirmation,vwap}, met count, ready/label/score,
reason, conflict flag, confluences, and per-side edge_score/edge_grade/edge_breakdown.
`evaluate_strict_setup` builds the raw per-side blocks and attaches them to EVERY
return path via a `_ret()` helper; `full_analysis` layers the per-direction edge on
top; `/status` exposes `directions`; the frontend `renderDirView()` renders the
SELECTED toggle side while the badge/meta header stays authoritative.

**The probability gauge (speedometer) is ALSO per-side.** `renderGauge(d)` reads
`d.directions[dir].edge_score` for the needle/percentage and derives its quality label
from that same per-side score (via `jsQualityForScore`, which mirrors the Python
`_score_tier`+`_decision_support` bands: 70 HIGH / 50 MODERATE / 35 SPECULATIVE / else
LOW) — so the meter can't contradict the header on the favored side (parity) yet shows
the other side's lower reading. `setDir()` must call `renderGauge(lastRec)` (not just
`renderDirView()`) or toggling won't move the needle — that omission was the original
"Long and Short show the same meter" bug. The Long/Short/ΔEdge/Dom row + badge/meta
header stay system-wide/toggle-independent; the gauge's direction label shows the
SELECTED side and the deep-green glow requires viewing the actual full-READY side.
In a quiet/balanced market both per-side edges are equal, so an equal meter is CORRECT,
not a regression.

**Invariants any change here must preserve:**
- `directions` is ADDITIVE / display-only. It must NEVER feed back into the
  authoritative `verdict`, `strict_*`, `trade_plan`, journaling, or alert-card state —
  Discord + journal depend on the single authoritative path.
- The favored side (== `result["confluences"]["direction"]`) REUSES
  `result["edge_score"]`/`edge_grade`/`edge_breakdown` verbatim. Do NOT recompute it —
  verbatim reuse is what guarantees favored.edge == authoritative edge_score (parity).
- The non-favored side is scored by `_analysis_edge_breakdown` on a shallow copy with
  `strict_label`/`verdict` forced to "WAIT" so it is never floored at 75.
- A long+short conflict zeros BOTH sides (each block carries `conflict=True`); the
  hard blockers `zone_broken_active` / `zone_mitigated_near` also zero both sides.

**Why:** keeps the display layer from corrupting the authoritative trading decision,
and stops the losing side from falsely showing the 75 READY floor (the edge floor in
compute_edge_breakdown triggers on gate_pass regardless of label, so conflict needs
explicit zeroing).

**How to apply:** when touching scoring or the dashboard, re-run the parity check
(favored.edge == edge_score) and the conflict check (both sides 0, conflict flagged).
Frontend keeps an older-server fallback: if `directions` is absent it renders the
authoritative favored block.
