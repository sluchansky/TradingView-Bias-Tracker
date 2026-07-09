---
name: Thesis Tracker
description: Outcome-based learning: saves analyst thesis snapshots and resolves them against actual market behavior 25-75 min later.
---

## What it does
Whenever the AI analyst generates a directional thesis (Bullish/Bearish), a snapshot is saved to `thesis_snapshots`.
25-75 minutes later the heartbeat compares the saved thesis to the actual market state and writes:
- `outcome`: SUCCESS / FAILED / PARTIAL / N/A
- `lesson`: human-readable lesson from the failure/success conditions
- `reflection`: 6-question AI self-review

A Pattern Memory SQL query surfaces how many similar past setups (same direction + phase + vwap_side) resolved as wins vs losses over 60 days.

## Key invariants
- **DISPLAY-ONLY**: never touches gate / scoring / sizing / broker
- **Fail-open**: THESIS_TRACKER_DB_READY=False → all functions silently no-op
- **No DDL**: table created via DB tool (dev) + publish schema-diff (prod)
- **Throttle**: at most 1 snapshot per instrument per 25 min (or on direction flip)
- **Outcome guard**: skip save when edge_score ≤ 0 or market_open=False
- **UPDATE is fine**: _resolve_open_theses UPDATE their OWN rows (not read-only)

## Heartbeat hook
`_mb_capture_cognitive(result)` calls:
1. `_save_thesis_snapshot(result, inst, notebook)` — throttled INSERT
2. `_resolve_open_theses(result, inst)` — UPDATE aged rows with outcome

## Full analysis hook
`compute_thesis_tracker(inst, result)` cached 15s via `_mb_cached(("thesis_trk", inst), 15.0, …)`
whitelisted in `/status` as `"thesis_tracker"`.

## Boot probe
`_check_thesis_tracker_db_ready()` called in the `__main__` block after `_check_market_events_db_ready()`.

## Dashboard
- Panel: `mod-mb-thesis` (always-visible `.mod` — remember to add to Advanced allowlist if needed)
- JS: `renderThesisTracker(d)` hooked in the main render loop after `renderMBEvents`
- Reads: `d.thesis_tracker.{snapshots, pattern}`

## Why
The user wanted the AI to "stop repeating mistakes" — market experience vs pure market analysis.
The resolve window (25-75 min) is a deliberate sweet spot: long enough for the setup to play out
or fail, short enough to stay relevant to the same session.
