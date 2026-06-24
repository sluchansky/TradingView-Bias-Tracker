---
name: Trade Debate Engine
description: Bull-vs-Bear-vs-Judge layer that mirrors the pro_review architecture; its money-path invariants and the final_verdict semantics that an earlier build got wrong.
---

# Trade Debate Engine

A pre-READY internal Bull-vs-Bear debate judged by a Decision Judge. Built to mirror
the Professional Review (`pro_review`) layer EXACTLY.

- Lives at the `full_analysis` level (computed AFTER the pro_review block, from the
  FINAL assembled result), NOT inside the strict gate — so scalp/swing_flagoff/parity
  goldens stay byte-identical. The strict gate does NOT snapshot this layer; its guard
  is `check_trade_debate.sh` (`trade_debate_smoke.py`), the same way pro_review has its
  own smoke.
- DISPLAY-on by default (`_trade_debate_engine_enabled` True). Money-path WAIT veto is
  behind a default-OFF flag (`_trade_debate_gate_enabled`, runtime `set_trade_debate_gate`
  override wins over the env seed, RESETS on restart) + dashboard toggle + `/trade-debate`
  GET/POST (strict bool parse, owner-only via the Express proxy whitelist).
- Bull/Bear confidence is DERIVED from the per-direction Edge cards
  (`result["directions"]["Long"/"Short"]["edge_score"]`); reasons / strongest_evidence
  / biggest_risk come from `edge_breakdown` (reasons / score_breakdown / risks). NEVER
  fabricated.
- Judge: `confidence_gap=|bull-bear|`; `too_balanced = gap < TRADE_DEBATE_MIN_GAP (15)`;
  `edge_significant = gap > TRADE_DEBATE_DECISIVE_GAP (20)`; `decisive = edge_significant
  and winning_side != Balanced`.

## final_verdict invariant (an earlier build got this WRONG)

`judge.final_verdict == "TAKE"` **iff** `(actionable and decisive and winner_aligned)`,
which is identical to `(actionable and not veto_would_fire)`. Every other case —
non-actionable base verdict, balanced, weak 15-20 edge, OR a decisive winner pointing
the OPPOSITE way to `ready_direction(verdict)` — reports `WAIT` (the winning side /
evidence is still shown).

**Why:** the first cut used `"TAKE" if decisive else "WAIT"`, which would display
"Judge: TAKE" on a READY long whose debate actually favored Short. That is a feature-
semantics failure (misleads the operator while the veto is OFF), even though the armed
veto already demoted it. The veto and the display verdict must agree.

**How to apply:** any change to the judge must keep `final_verdict==TAKE` equivalent to
`not veto_would_fire` (on an actionable verdict). `veto_would_fire = actionable and not
(decisive and winner_aligned)` — it can ONLY demote actionable→WAIT, never promote.

## Precedence & safety

- Precedence is Analyst → Pro Review → Debate. The debate veto chains AFTER pro_review;
  if pro_review already demoted, `is_actionable` is False so the debate no-ops.
- Veto demotion side effects mirror pro_review: verdict / strict_label / strict_reason,
  `trade_plan(False)`, directions[*] ready=False/label=WAIT, alert_diagnostics
  rejected_reasons += "Trade Debate veto", recomputed decision_support,
  judge.final_verdict=WAIT.
- FAIL-OPEN: a compute exception yields `_trade_debate_neutral_block` (veto_would_fire
  False, full schema) so it can never crash analysis or demote.
- Closed-override path sets the neutral block too (single-return / hard-index parity).
- Dashboard module (`mod-debate`) writes all dynamic strings via `textContent` (no
  `escapeHtml` helper exists in the dashboard) to neutralize webhook-derived reason text.
