---
name: Strategy scan coverage (Phase 6)
description: 3-system strategy architecture audit; scoring loop behavior; diagnostics pattern for display-only coverage tracking.
---

## Three separate strategy systems (29 total definitions)

| System | Count | Location | Purpose |
|---|---|---|---|
| Main Engine | 5 | `app.py` STRATEGY_DEFS/PRIORITY/SCORERS | Active strategy selection (display-driven; ORB 1:4 is the only money-path effect) |
| Swing Library | 5 | `app.py` SWING_STRATEGY_DEFS | Operator-selected demote-only SWING filter; flag-gated OFF |
| Scalp Research | 19 = 16 live + 3 pending | `scalp_live_sim.py` LIVE_SIM_DETECTORS + PENDING_KEYS | Paper-sim display/advisory; fully walled off from money path |

## Key finding: main engine evaluation loop

All 5 main-engine scorers are called **every cycle** — the loop has no per-strategy skip gate.
The only eligibility exception is `OPENING_DRIVE`: its scorer runs but `eligible=False` is set when
`ctx["in_opening_window"]` is False, preventing it from reaching `fully_met` / being selected.

**Why this matters:** a future change that adds an early-exit, feature-flag skip, or mode-filter to
a scorer would make `evaluated_count < eligible_count` — the `unevaluated_eligible` metric in
STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER will surface this as a warning on the dashboard panel.

## Diagnostics pattern (STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER)

- Module-level dict: `ticker → last-scan snapshot` (bounded, one entry per ticker, no history)
- Populated by `_build_strategy_scan_diag(ticker, inst, evals, active_key, engine, scan_t)`
  called before BOTH `return engine` paths in `compute_strategy_engine` (inside the outer try-block)
- On scorer exception the outer try/except fires → `_build_strategy_scan_diag` is NOT called →
  diagnostics remain stale (or empty). This is intentional: diagnostics only reflect clean scans.
- Skip-reason taxonomy: `outside_session` (OPENING_DRIVE); others reserved for future use.
- Result states: `selected`, `candidate`, `no_signal`, `skipped`.

**Why:** Diagnostics are in a parallel dict (not injected into the engine result) so existing
consumers (full_analysis, /status, etc.) can't be broken by a diagnostics build failure.

## How to apply

- Adding a new main-engine strategy: add to STRATEGY_DEFS + STRATEGY_PRIORITY + STRATEGY_SCORERS.
  `_build_strategy_scan_diag` picks it up automatically via iteration over STRATEGY_PRIORITY.
- Adding an eligibility gate (mode/instrument/session): set `eligible=False` in the evals dict
  entry for that strategy key. The diag helper reads `ev.get("eligible", True)` automatically.
- The scalp research PENDING_KEYS are intentionally absent from LIVE_SIM_DETECTORS — test
  `test_inv_scalp_research_pending_keys_excluded_from_live` guards this invariant.
