---
name: Professional Review layer
description: Pre-READY pro-trader grading layer with two per-instrument models and a default-OFF money-path veto, living at the full_analysis level (above the strict-gate goldens).
---

# Professional Review layer

A pre-READY "graded like a pro" layer computed inside `full_analysis` (the assembled
result), NOT inside the strict gate. Two models (SCALP, SWING) are scored every pass
from one shared read; the ACTIVE model is selected PER INSTRUMENT. Personal "prime
windows" are per-instrument and clock-driven (MGC 8–10pm ET, MNQ/index 9–11am ET) and
are DISPLAY-ONLY — they feed session_quality, never the gate.

## Two independent flags (keep them separate)
- engine_enabled: default ON — the display brain. Fail-OPEN; a compute error must yield
  a NEUTRAL block whose `active.veto_would_fire` is False (never a synthetic veto).
- gate_enabled (the VETO): default OFF; a runtime override (`set_pro_review_gate`) wins
  over the env seed and RESETS to None on restart — fail-safe bias toward NOT
  interfering with live trading after a republish.

## Veto contract (money path)
Fires ONLY when `_pro_review_gate_enabled()` AND `active.veto_would_fire` AND the verdict
is actionable. It can ONLY DEMOTE actionable→WAIT, never promote/force. On demotion it
must update EVERY authoritative field in lockstep or a stale actionable field leaks
downstream: verdict, strict_label, strict_reason, trade_plan (False), directions
Long/Short ready=False, alert_diagnostics.rejected_reasons, and a recomputed
decision_support. This mirrors the analyst-veto pattern.
**Why:** the goldens snapshot only build_strict_trade_plan + evaluate_strict_setup, so
this whole layer is invisible to them — flag-OFF therefore stays byte-identical, and the
veto needs its OWN behavioral smoke instead (`.local/state/pro_review_smoke.py`, run via
`.local/state/check_pro_review.sh`; could not be registered as a workflow — cap of 10).
**How to apply:** any new actionable-derived field added downstream of the veto must
also be reset inside the demotion block. The `/pro-review` POST gate toggle must parse
gate_enabled STRICTLY (real bool / recognised true/false tokens only; ambiguous or null
→ IGNORED, gate unchanged) so a stray JSON string like "false" can never arm the live
veto.

## Scoring gotcha (selective by design)
`veto_would_fire = (professional_decision != "TAKE")`, and efficiency < 80 always adds a
hard_fail — so the pro is intentionally picky: a setup needs eff≥80 + grade≠D + not
over-extended + room to TP1 to earn TAKE. A poor/over-extended setup grades D and would
veto. That selectivity is the feature, not a bug.
