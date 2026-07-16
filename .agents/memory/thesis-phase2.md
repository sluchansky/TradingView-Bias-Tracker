---
name: Thesis Phase 2 dashboard + alerts
description: Phase 2 of the thesis hysteresis engine — timeline, Discord alerts, DB persistence, /thesis routes, dashboard panel
---

# Thesis Phase 2 — Live Market Thesis Dashboard

## Rule
`_thesis_post_update(inst, prev_snap, new_snap)` is the sole Phase 2 hook wired
into `_apply_thesis` outer wrapper (after the inner call updates `THESIS_BY_INST`).
It orchestrates:
1. `_record_thesis_event` — writes to `THESIS_TIMELINE_BY_INST[inst]` deque(maxlen=25)
   **only** when status changed OR abs(confidence_delta) >= 5
2. `_maybe_send_thesis_notification` — Discord alert gated on
   `_THESIS_DISCORD_ALERTS_ENABLED` + `_should_notify_thesis` (table + direction-flip check)
3. `_persist_thesis_state` — upserts `hysteresis_thesis` table, gated on `THESIS_DB_READY`

**Why:** All Phase 2 effects are display/notification only — fail-open and never touch the
gate or money path. Timeline skips trivial changes to avoid noise.

## How to apply
- Adding a new notification trigger: extend the `_NOTIFY_TRANSITIONS` set in `_should_notify_thesis`
- Adding a new timeline field: extend the event dict in `_record_thesis_event`
- The `/thesis` and `/thesis/<inst>/history` routes are owner-only (NOT in OPEN_PATHS); proxy-whitelisted in `flask-proxy.ts`
- Dashboard panel: `#mod-thesis` with `.mod-h` + `.mod-c`; in advanced-gate `:not()` allowlist so visible in core mode
- `THESIS_DB_READY` is probed at boot inside `if LEARNING_DB_ENABLED:` block (no-DDL probe)
- `_restore_thesis_states` skips rows older than `THESIS_RESTORE_MAX_AGE_MS` (default 8 h)

## Emoji / escape gotcha
Trade card embed uses `"\u26a0"` (single backslash in Python source = ⚠)
and `"\U0001F9E0"` (8-digit = 🧠). Double-backslash `"\\u..."` → literal text, not emoji.
Dashboard JS uses HTML entities (e.g. `&#x1F9E0;`) to avoid the cockpit-mode `\n`-in-triple-quoted-string bug.

## Tests
`test_thesis_phase2.py` — 17 tests covering timeline, notify dedup, fail-open paths, routes,
DB skip, _ms_to_human, _ev_label, Discord flag. Runs alongside Phase 1 (33 total).
