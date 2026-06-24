---
name: SCALP/SWING goldens — session bonus is now PINNED (was time-of-day flaky)
description: The scalp/swing golden harness used to flip ±10 with the wall-clock (Edge Score session bonus); it now pins a fixed bonus=0. Keep it pinned — don't revert to session=None or rebaseline around it.
---

# Golden flakiness from the live session bonus — FIXED by pinning the harness

**Now (fixed):** `scalp_golden.py` defines `FIXED_SESSION = get_session_state(now=_FIXED_NOW)`
where `_FIXED_NOW` is a fixed UTC instant mapping to **03:00 ET (outside every
`SESSION_WINDOWS` window → bonus 0)**, and passes it as `session=FIXED_SESSION` into
`evaluate_strict_setup`. So the +10 Session Edge component is now deterministic and the
goldens no longer float with the wall-clock. `build_strict_trade_plan` was already
session-independent (only the gate `score` ever drifted). The real session-window logic
still runs against the frozen input, so a regression in `get_session_state` itself would
still surface as a diff. **Do not revert to `session=None`** — that reintroduces the flake.

**History (why this exists):** the harness used to call `evaluate_strict_setup(session=None)`
and not pin the clock. The Session component (+10, `SESSION_BONUS_POINTS`) is a pure
function of live time-of-day (`SESSION_WINDOWS`: 05–08, 08–11, 20–23 ET), so the
snapshot's `edge_score`/`score` floated ±10 depending on WHEN baseline vs current run
happened. Rebaselining during a bonus-ON window then made it go RED in the next bonus-OFF
window (and vice-versa) — a self-inflicted flip-flop.

**Symptom if it ever returns** (someone reverted the pin): goldens fail with a *uniform*
`score 15↔25` / `edge_score(15<THRESH)↔(25<THRESH)` across every `empty_history` fixture
(SCALP threshold 50, SWING-flagoff 80), with **zero** stop/target/risk field changes.
Uniform ±10 everywhere = session bonus, not a logic change. Fix = restore the
`FIXED_SESSION` pin, not a rebaseline. `parity` (`check_parity.sh`) was always
clock-independent and stayed green throughout.

**Proving a real change is golden-neutral:** extract committed HEAD app.py
(`git show HEAD:artifacts/tradingview-webhook/app.py`), point the harness at it, and diff
HEAD output vs working-tree output. Byte-identical (under the pinned session) = your change
is golden-neutral. SWING-flagoff is a frozen legacy invariant — only rebaseline SCALP when
its stop/plan output legitimately changes; never rebaseline to paper over the session bonus.

Display-only dashboard edits (HTML/CSS/JS, equity-curve serialization) never touch
`evaluate_strict_setup`/`build_strict_trade_plan`, so they cannot move these scores.
