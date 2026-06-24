---
name: SCALP/SWING goldens are time-of-day flaky (session bonus)
description: Why a uniform +10 edge_score drift in the scalp/swing goldens is environmental, not a regression, and how to prove it.
---

# Golden flakiness from the live session bonus

The `scalp_golden.py` harness (driven by `check_scalp_golden.sh`,
`check_swing_flagoff_golden.sh`) calls `evaluate_strict_setup` with `session=None`
and does **not** pin the clock. The Session component of the Edge Score (+10) is
derived from the live time-of-day, so the snapshot's `edge_score`/`score` floats by
±10 depending on when the baseline vs the current run happened.

**Symptom:** goldens fail with a *uniform* `score 15→25` and `edge_score(15<THRESH)→(25<THRESH)`
across every `empty_history` fixture (SCALP threshold 50, SWING-flagoff 80). Uniform
+10 everywhere = session bonus, not a logic change.

**Why:** baselines are captured at one moment; re-running in a different session
window flips the +10. parity (`check_parity.sh`) is clock-independent and stays green.

**How to apply / prove it before blaming your change:** extract committed HEAD app.py
(`git show HEAD:artifacts/tradingview-webhook/app.py` into a temp dir), point the
harness `sys.path` at that copy, and diff. If HEAD shows the *same* drift vs baseline
AND HEAD's output is byte-identical to your working-tree output, your change is
golden-neutral and the failure is environmental. Do **not** rebaseline to "fix" it —
that masks the flakiness (and SWING-flagoff is meant to be a frozen invariant). The
real fix (if ever wanted) is to pin the clock/session in the harness.

Display-only dashboard edits (HTML/CSS/JS, equity-curve serialization) never touch
`evaluate_strict_setup`/`build_strict_trade_plan`, so they cannot move these scores.
