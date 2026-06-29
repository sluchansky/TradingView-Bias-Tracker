---
name: Main Brain cognitive layer
description: Display-only "continuous analyst" upgrade — prediction/voice/confidence-over-time/narrative/events/day-type/learning keys, their persistence + fail-open invariants.
---

# Main Brain Cognitive Upgrade (DISPLAY-ONLY)

Evolves Main Brain from a snapshot processor into a continuous analyst. Seven new
top-level keys on the full_analysis result: `main_brain_predictions`,
`main_brain_voice`, `confidence_timeline`, `market_narrative`,
`market_events_timeline`, `session_day_type`, `main_brain_learning_stats`.

**Rule:** strictly advisory. Nothing here may reach the gate, scoring, sizing,
dedupe, or broker. Live-scoring influence is a SEPARATE dependent task — do not
fold any cognitive output into the money path under this layer.
**Why:** the upgrade was deliberately split so the display half stays byte-identical
on all 4 goldens while the scoring half is reviewed independently.

## Invariants (any change must preserve)
- **Single attach seam.** The 7 keys are attached at the one full_analysis return
  path by EXTENDING the result dict — never reassign an existing verdict/score/plan
  key. The market-closed override runs last and MUST mirror all 7 keys (key parity)
  or they read null whenever the market is closed. See `analysis-data-quirks.md`,
  `market-session-hours.md`.
- **Curated /status whitelist.** Read endpoints whitelist keys (not jsonify of the
  whole result) — the 7 keys were added to the main `/status` route dict. They were
  DELIBERATELY NOT added to the assistant-grounding whitelist (inside
  `_assistant_live_context`) to avoid bloating the AI-assistant prompt. See
  `curated-endpoint-serialization.md`, `ai-assistant-chat.md`.
- **`_mb_cached` can return None.** The 3 DB-backed reads (`compute_confidence_timeline`,
  `compute_market_narrative`, `compute_market_events_timeline`) go through `_mb_cached`,
  which returns None if both producer attempts fail. Callers MUST coerce None to their
  neutral dict, or the advertised stable schema silently breaks. **Why:** architect
  flagged this as a schema-contract gap; the dashboard tolerates null but other
  consumers should not have to.
- **Heartbeat capture is fail-open.** `_mb_capture_cognitive(a)` runs after
  `_update_setup_state(...)` in the heartbeat loop; it persists confidence snapshots +
  detects/records market events and must swallow all exceptions and never feed values
  back into the gate.
- **Lock ordering.** `_MB_DETECT_LOCK` is not held while DB/throttle work runs, and the
  read-cache lock is released before producer SQL — keep it that way (no inversion).

## Persistence
Two tables: `confidence_snapshots`, `market_events`. INSERT/SELECT only — NO in-app
DDL (created via the database tool in dev + publish schema-diff in prod), with no-DDL
boot probes (`CONFIDENCE_SNAPSHOTS_DB_READY` / `MARKET_EVENTS_DB_READY`) mirroring the
other `_check_*_db_ready` probes. Writers enqueue via `_enqueue_slow` INSERTs
(events use ON CONFLICT DO NOTHING). See `db-app-insert-select-only.md`.

## Ops gotcha
`/tmp/logs` boot snapshots are PREVIEWS that can DROP INFO lines — a missing
"<table> table ready" line does NOT mean the probe failed. Verify by invoking the
probe function directly or querying `to_regclass('public.<table>')`, not by grepping
the preview log.
