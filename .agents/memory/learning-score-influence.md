---
name: Learning influences live scoring
description: Master-flag-gated bounded learning adjustment of the authoritative Edge Score (the SECOND money-path effect of learning); where it must fold and the hard-block gotcha.
---

# Learning influences live scoring (Edge Score adjustment)

Owner-armed master flag (`_learning_score_gate_enabled` / `set_learning_score_gate`,
`/learning-score`, env seed `LEARNING_SCORE_INFLUENCE`, resets OFF on restart). When ON,
the learning engine's bounded per-strategy weight adjusts the authoritative Edge Score
**up AND down**, hard-capped `±LEARNING_SCORE_MAX_DELTA` (15), clamped to
`[0, EDGE_SCORE_MAX]`, BEFORE the READY gate. This is a SEPARATE money-path effect from
the Learning Engine v2 demote-only veto — keep them from double-counting.

## Where the delta MUST be folded (non-obvious)
The gate (`evaluate_strict_setup._edge_for`) applies the delta to its own strict score for
the DECISION and stamps `gate_debug` (`edge_score_base`, `learning_score_delta`,
`learning_weight`, ...). But `full_analysis` recomputes `result["edge_score"]` from the
*raw* breakdown via `_analysis_edge_breakdown`, and that value feeds the **Entry-Quality
override** (`compute_entry_quality` reads `result.get("edge_score")`), the conviction tier,
both per-direction cards, and the card/journal Edge block.
**Why:** folding only at the gate call site leaves all those consumers on a stale RAW
score → a real money-path leak (the EQ demote-veto evaluating the un-adjusted number).
**How to apply:** fold inside `_analysis_edge_breakdown` (the single canonical Edge Score
fn; 2 call sites, both in full_analysis; `_build_card_entry` reuses `result["edge_breakdown"]`).
Read the delta from the SAME `gate_debug` the fn already uses, clamp, recompute grade via
`_grade_for_score`, append a "Learning Adjustment" breakdown line.

## Hard-block resurrection gotcha (the bug that caused a NO-GO)
`compute_edge_breakdown` force-zeros a zone-broken/consumed SWING setup (only when
`GATE_REQUIRE_ZONE`). But `gate_debug["learning_score_delta"]` is derived from the gate's
ADDITIVE base, where zone no longer scores, so it can be POSITIVE on that exact setup.
Unguarded, the fold turns `0 → 0 + (up to +15)`, resurrecting a hard-blocked Edge Score.
**Guard:** fold only `if _ls_delta and eb["score"] > 0` — a 0 stays 0 (any hard-block
reason), while a negative delta can still push a low-but-positive score toward 0.
A genuinely-zero-component setup has gate base ~0 ⇒ delta 0, so the guard never suppresses
a legitimate fold.

## Byte-identity / verification
OFF (delta 0 or `gate_debug` absent) ⇒ no mutation ⇒ legacy byte-identical. The 4 OFF
goldens call the strict funcs directly and can't see the fold, so there is a dedicated ON
golden (`learning_score_golden.py` `breakdown_fold`) asserting fold+clamp+grade, the
no-op cases, AND a mode-independent hard-block case (stub `compute_edge_breakdown` to a
0-score shape, fold +15, assert score stays 0 / no Learning Adjustment line).
