---
name: Journal Coaching Drill-Down
description: Phase 7O.1 — clicking a coaching insight filters the trade log to the exact trades that produced it. JDrillFilter contract, URL sync, _RATING_FIELDS guard.
---

## Rule
Every coaching insight row is now a clickable drill-down that passes a `JDrillFilter`
to the Trade Log tab, filtering to exactly the trades that produced the metric.

## Key design decisions

### Backend: `_RATING_FIELDS` at module level
- Defined as `frozenset` at module level (not inside the function) so tests can
  reference `APP._RATING_FIELDS` directly.
- It is the ONLY param whose value reaches SQL column-name interpolation;
  all other params are fully parameterised.
- Unknown values silently dropped before the query.

### Frontend: `JDrillFilter` 15-field contract
- `label`, `count` — display only (not sent to server)
- `review_status: 'REVIEWED'` — always included from coaching (coaching only counts reviewed trades)
- Coaching filter context (date_from/to, source, instrument, mode) always carried along to preserve count parity
- Specific drill keys: mistake_tag, positive_tag, emotion_tag, followed_plan, strategy, session, rating_field + rating_value

### `JCoachingTab` changes
- Signature: `React.FC<{ onDrill?: (f: JDrillFilter) => void }>`
- `hovSec` / `hovIdx` state drives per-row hover highlight
- `_mkDrill(label, count, extra)` — helper that merges coaching context + specific keys
- `_rowKey(fn)` — keyboard Enter/Space activator
- `_rowSt(sec, i)` — row style with highlight on hover when `onDrill` present
- `_lastCell(sec, i, n, conf, fn)` — shows "VIEW N" button on hover, badge otherwise
- All 7 sections made clickable: mistakes, behaviors, plan, strategy, session, emotion, priority

### `JournalFullPage` changes
- `drillFilter` state + init from `_urlReadJState()`
- Tab init from URL (`_urlReadJState().tab || 'trades'`)
- `popstate` handler syncs tab + drill on browser Back/Forward
- `handleDrill(f)` → pushState so Back returns to coaching
- `handleClearOneDrill(key)` — removes one chip; clears rating_value when rating_field removed
- Clears drill when operator manually clicks a non-trades tab
- Phase label updated to "7O"

**Why:** Count parity requires the same `review_status=REVIEWED` + coaching filter
context to be present in both the coaching query and the drill-down trade list query.

**How to apply:** Any new coaching section that adds a drill-down must:
1. Use `_mkDrill(label, count, { specific_key: value })` — DO NOT omit `review_status`
2. Add `onMouseEnter/Leave` with `setHovSec`/`setHovIdx`
3. Use `_lastCell` for the badge/VIEW toggle
4. Add the filter param to the backend `journal_trades_list` outer_clause
