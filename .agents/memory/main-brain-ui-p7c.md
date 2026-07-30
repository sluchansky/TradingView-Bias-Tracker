---
name: Main Brain Operator Console (Phase 7C)
description: React dashboard at /main-brain — read-only display, auth pattern, key TS gotchas, file locations.
---

# Main Brain Operator Console

**Route:** `/main-brain` in `artifacts/home/src/App.tsx` → `artifacts/home/src/pages/MainBrain.tsx`

**Auth:** `localStorage('brain_auth')` → `Authorization: Basic btoa('admin:' + pwd)` — same as Home.tsx.

**Polling:** 7,000 ms constant (`POLL_INTERVAL_MS`). Guards: `document.hidden` skip, `inFlight` ref prevents concurrent calls, 30s stale threshold.

**Data source:** `GET /api/main-brain?ticker=…` — reads only, no POST/mutation calls anywhere.

## Key TypeScript Gotcha

In strict TypeScript, `unknown && <JSX />` resolves to `unknown`, which is not assignable to `ReactNode`. All conditional JSX renders from `Record<string, unknown>` fields must use `!= null &&` not `&&` alone.

```tsx
// WRONG — TS2322: unknown not assignable to ReactNode
{gw.gateway_status && <Badge label={String(gw.gateway_status)} />}

// CORRECT
{gw.gateway_status != null && <Badge label={String(gw.gateway_status)} />}
```

## Pre-existing TS Errors

`MobileHome.tsx` (useRef argument) and `Sentinel.tsx` (canvas ctx null checks) have pre-existing TS errors that are NOT regressions from Phase 7C. Do not attempt to fix them as part of future UI phases unless explicitly asked.

## Design Token Object

`const T = { bg, panel, panelAlt, border, borderMid, cyan, blue, green, amber, red, purple, txtPri, txtSec, txtMuted, mono }` — defined at top of MainBrain.tsx; use these for any visual work on this page.

## Panel List

14 panels: Shell/nav, Header, Market Strip, AI Summary, Thesis, Verdict, Strategy Scanner, Trade Plan, Active Trades, Execution Status, Coach, Journal, Decision Timeline, Alerts, System Health.

## Known Gaps (Phase 7D)

- `execution_gateway.last_outcome` — labeled "not available" in UI, deferred
- Full `_DECISION_EVENT_LOG_BY_INST` event deque — timeline labeled "PARTIAL"
- Per-strategy `sample_count` / `historical_expectancy` — no cache yet

**Why:** Phase 7B builder deferred these; no backend store exists yet. Do not fabricate values.

## Tests

`test_phase7c_main_brain_ui.py` — 103 checks across 20 test classes. Covers: route wiring, auth, proxy whitelist, no hardcoded values, safe rendering, polling/refresh, UI states, strategy count (5 canonical only), active trades, Coach semantics, execution deferral, timeline partial label, accessibility, responsive layout, design tokens, no backend mutation, backend non-regression.
