---
name: Advisor auto-trade review gate
description: The opt-in "Advisor" toggle that lets the analyst block an AUTO-trade it disagrees with — money-path gate, fail-closed-when-ON, default-OFF design and the reviewed-marker invariant behind it.
---

# Advisor auto-trade review gate

The Advisor is an **opt-in GLOBAL money-path gate on AUTO execution only**. When ON,
the Analyst Reasoning engine reviews every about-to-fire READY setup and can BLOCK
the auto entry; when OFF the analyst module is hidden and auto-trades fire on the
normal base gate (today's behavior). It is consulted at the single auto chokepoint
`_maybe_auto_execute` (after cooldown, before the exec lock), gated `if _advisor_enabled():`.

## Invariants (any change must keep these)
- **Default OFF + resets OFF on restart** (in-memory flag, like AUTO_TRADE). The whole
  advisor block is skipped when OFF → the auto path stays byte-identical → goldens
  unaffected. Behavior only changes when the operator toggles ON.
- **Never touches** the base gate, scoring, manual ENTER, or the journal. It only
  decides whether an *already-READY* setup is allowed to AUTO-execute. **Manual ENTER
  is never advisor-blocked.**
- **FAIL-CLOSED when ON.** `_advisor_blocks_auto_trade(inst)` allows ONLY when a real
  review ran AND did not veto. It BLOCKS on: explicit disagreement (`veto_would_fire`),
  a not-real review (`reviewed` falsy), a missing analyst block, or any `full_analysis`
  exception. Recomputes `full_analysis(ticker_override=inst)` at fire-time (latest
  analysis is the authoritative last-second review) — computed OUTSIDE `_AUTO_EXEC_LOCK`.

## Why the `reviewed` marker exists (non-obvious)
`engine_enabled` = `_analyst_engine_enabled()` in BOTH the normal analyst return AND
the `_analyst_neutral_block` fallback, so it CANNOT distinguish a genuine review from a
fail-open neutral fallback (engine env ON but the inner analyst compute was skipped/
errored). The fallback also hardcodes `veto_would_fire=False`. So a naive
"block only on veto, else allow" let **Advisor-ON allow an UNREVIEWED auto-trade** when
the engine was off or the analysis fell back (architect caught this).

Fix: an explicit boolean **`reviewed`** — `True` only on the real compute path,
`False` in `_analyst_neutral_block`. The gate keys off `reviewed`, not `engine_enabled`.
`reviewed` must stay in **both** analyst return paths (the single-return-path /
hard-indexed-consumer parity rule; adding to only one branch reintroduces the leak).

## Surfaces
- Flag + helpers `_advisor_enabled()/_set_advisor_enabled()` (near AUTO_FIRED_KEYS).
- `/advisor` GET/POST (owner-only, NOT in OPEN_PATHS) — must be in the Express `/api`
  proxy whitelist (`flask-proxy.ts`) or it 404s before reaching Flask.
- `/status` exposes top-level `advisor_enabled`; dashboard header pill + analyst module
  visibility key off it.
- **Blocked-trade log (display-only).** Advisor block decisions are recorded AFTER the
  decision into a bounded in-memory log (clears on restart like the toggle), exposed via
  `/status` and shown in the analyst module — so the recording is best-effort, must never
  raise, and never feeds back into the auto-exec decision or money path. Visible only while
  Advisor is ON (the analyst module hides when OFF). Its lock must stay standalone (never
  nested under the auto-exec lock).
- Smoke: `.local/state/check_advisor.sh` (+ `advisor_smoke.py`) proves the fail-closed
  contract (veto→block, clean→allow, not-reviewed/missing/error→block). NOT registered
  as a workflow — the project is at the 10-workflow cap; run it via bash.
