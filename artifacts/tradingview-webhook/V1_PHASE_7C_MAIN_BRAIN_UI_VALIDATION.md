# V1 PHASE 7C — MAIN BRAIN UI VALIDATION
# Main Brain Operator Console

## 1. Baseline State

**Branch:** `polish-v1`
**Starting HEAD (Phase 7B):** `72c8141` — V1-P7B Main Brain read-only route
**Head at Phase 7C start:** `17a4d2f` — Published your App

Accepted Phase 7B endpoint: `GET /main-brain` (implemented in `72c8141`)

## 2. Intervening Commit Audit

| Commit | Message | Classification |
|--------|---------|----------------|
| `17a4d2f` | Published your App | platform checkpoint (publish) |
| `105952a` | Initialize main brain route for phase 7b and update documentation | production implementation |

All intervening commits are accounted for. No unexplained changes. No money-path modifications after `72c8141`.

## 3. Phase 7C Scope

UI-only implementation phase. No backend trading logic modified. No database schema changes. No deployment.

## 4. Existing Dashboard Architecture

- React SPA: `artifacts/home/` using Vite + Tailwind + wouter
- Routing: `artifacts/home/src/App.tsx` with wouter `<Switch>`
- Auth: Basic Auth via `localStorage('brain_auth')` → `Authorization: Basic btoa('admin:' + pwd)` header
- Existing routes: `/` (Home/MobileHome), `/mobile`, `/cockpit`
- API proxy: Express `artifacts/api-server/src/routes/flask-proxy.ts` → Flask on port 8000
- Auth middleware: `artifacts/api-server/src/routes/dashboard-auth.ts`
- Legacy dashboard: `GET /api/dashboard` (Flask inline HTML, unchanged)

## 5. Files Created

| File | Purpose |
|------|---------|
| `artifacts/home/src/pages/MainBrain.tsx` | Main Brain Operator Console page (read-only, display-only) |
| `artifacts/tradingview-webhook/test_phase7c_main_brain_ui.py` | Phase 7C test suite (103 checks) |
| `artifacts/tradingview-webhook/V1_PHASE_7C_MAIN_BRAIN_UI_VALIDATION.md` | This document |

## 6. Files Modified

| File | Change | Authorized Scope |
|------|--------|-----------------|
| `artifacts/home/src/App.tsx` | Added `import MainBrain` and `<Route path="/main-brain" component={MainBrain} />` | Narrowly scoped route wiring |

## 7. Files Deleted

None.

## 8. Final Page Route

- **Path:** `/main-brain`
- **Component:** `MainBrain` (default export from `artifacts/home/src/pages/MainBrain.tsx`)
- **Auth:** Same Basic Auth boundary as existing dashboard — Express `dashboardAuth` middleware applies to all `/api/*` calls
- **Backend:** `GET /api/main-brain` → proxied to Flask `GET /main-brain`
- **Not in OPEN_PATHS:** Confirmed — `/main-brain` is not in `dashboard-auth.ts` `OPEN_PATHS` set

## 9. Navigation Changes

- Left navigation rail added to MainBrain page with links to:
  - Main Brain (`/main-brain`) — active page, highlighted
  - Analysis (`/api/dashboard`) — external link, opens Flask HTML dashboard
  - Strategy Scanner, Active Trades, Journal, Coach, Alerts — disabled ("coming later"), not dead links
- Existing home dashboard links to `/api/dashboard` (existing behavior unchanged)
- No existing page deleted, no existing URL broken

## 10. Design-System Implementation

Design tokens defined in `MainBrain.tsx` as `const T = { ... }`:

| Token | Value | Purpose |
|-------|-------|---------|
| `bg` | `#050c1a` | Page background |
| `panel` | `#0b1628` | Panel background |
| `panelAlt` | `#0e1d36` | Elevated panel background |
| `border` | `rgba(255,255,255,0.07)` | Standard border |
| `borderMid` | `rgba(255,255,255,0.12)` | Mid border |
| `cyan` | `#38bdf8` | Primary cyan accent |
| `blue` | `#3b82f6` | Secondary blue |
| `green` | `#22c55e` | Success/bullish green |
| `amber` | `#f59e0b` | Warning orange |
| `red` | `#ef4444` | Danger/bearish red |
| `purple` | `#a855f7` | Coach/learning purple |
| `txtPri` | `#e2e8f0` | Primary text |
| `txtSec` | `rgba(226,232,240,0.60)` | Secondary text |
| `txtMuted` | `rgba(226,232,240,0.32)` | Muted text |
| `mono` | JetBrains Mono/Menlo | Monospace font |

Typography hierarchy: page title (14px+800) → section heading (10px+700+uppercase) → card heading (12px+700) → body (11px) → label (9.5px) → badge (9.5px+uppercase).

## 11. Panel Inventory

All 14 panels specified in the brief are implemented:

| Panel | Source Fields | Status |
|-------|-------------|--------|
| Application Shell (sidenav + header) | `payload.market`, `payload.system_status` | ✅ |
| Market State Strip | `payload.market`, `payload.market_state`, `payload.system_status` | ✅ |
| AI Summary | `payload.main_brain.voice` / neutral fallback | ✅ |
| Left Brain Thesis | `payload.left_brain` | ✅ |
| Verdict & Confidence | `payload.verdict` | ✅ |
| Strategy Scanner | `payload.strategy_scanner.strategies` | ✅ |
| Trade Plan | `payload.strategy_scanner.trade_plan` | ✅ |
| Active Trades | `payload.active_trades.trades` | ✅ |
| Execution Status | `payload.execution_gateway`, `payload.system_status` | ✅ |
| Coach | `payload.coach`, `payload.performance` | ✅ |
| Journal Summary | `payload.journal`, `payload.performance` | ✅ |
| Decision Timeline | `payload.decision_timeline.events` | ✅ |
| Alerts / Live Feed | `payload.alerts.items` | ✅ |
| System Health | `payload.system_status`, `payload.availability`, `payload.errors` | ✅ |

## 12. Main Brain API Integration

- **Fetch target:** `GET /api/main-brain?ticker={instrument}`
- **Auth:** `Authorization: Basic btoa('admin:' + localStorage('brain_auth'))`
- **Error handling:** 401/403 → auth_fail state; non-200 → error/stale; invalid JSON → error/stale
- **Partial schema:** Each panel independently null-guarded with `available !== false` check
- **Stale data preserved:** `lastPayload` ref retains last successful response during transient failures

## 13. Field-to-Component Mapping

All display values sourced from `/main-brain` payload or labeled as "not available". No invented values.

Helper contract:
- `safeStr(v, fallback='—')`: null/empty/undefined → fallback string
- `safeNum(v)`: NaN/Infinity/null → null
- `fmtNum(v, dec)`: formatted locale string or `—`
- `fmtTs(v)`: ISO → ET time string or `—`
- `fmtAge(v)`: ISO → "Xs ago" / "Xm ago" or `''`

## 14. Loading State

First fetch shows `LoadingScreen` component (large brain emoji + "Connecting to Main Brain…" message). Subsequent refreshes show `refreshing` state (button shows "↻ …") while preserving existing payload.

## 15. Empty State

Each panel independently handles its empty state:
- No active trades → "No active trades"
- No closed trades → "No closed trades"
- No alerts → "No alerts"
- No timeline events → "No events recorded this session"
- No trade plan → "No actionable trade plan"
- Thesis unavailable → "Thesis unavailable"
- Verdict unavailable → "Verdict unavailable"

## 16. Partial Availability State

Every panel checks `available !== false` and shows `UnavailableNote` when the subsystem is unavailable. The rest of the dashboard continues rendering unaffected.

## 17. Stale State

When `Date.now() - lastOk > 30,000 ms` and `fetchState === 'loaded'`, state transitions to `'stale'`. A yellow banner ("⚠ STALE DATA") appears at the top of the main content area with `role="alert"`. Previous payload remains displayed.

## 18. Total Failure State

When first fetch fails (no previous payload), `ErrorScreen` component renders with the error message and a Retry button. Retry calls `fetchNow('manual')`.

## 19. Strategy Scanner Rendering

Displays exactly the 5 canonical main-engine strategies from `payload.strategy_scanner.strategies`:
- `OPENING_DRIVE` → "Opening Drive"
- `LIQUIDITY_SWEEP_REVERSAL` → "Liquidity Sweep"
- `VWAP_TREND_CONTINUATION` → "VWAP Continuation"
- `RANGE_EXPANSION_BREAKOUT` → "Range Expansion"
- `OPENING_RANGE_BREAKOUT` → "ORB"

The 16 paper-research strategies are excluded by the backend `_MB_MAIN_ENGINE_KEYS` filter before reaching the UI. The UI renders whatever the API returns and labels it by the `STRATEGY_LABELS` map — research keys are absent from this map.

## 20. Active Trade Rendering

- Zero trades: "No active trades" message
- Multiple trades: iterated via `trades.map()`
- `current_r` displayed from payload (not browser-calculated)
- `unrealized_pnl` displayed from payload (not browser-calculated)
- Direction color-coded (green=long, red=short)
- `opened_at` formatted as ET timestamp

## 21. Execution Status Rendering

- Mode displayed (manual_only → amber, others → green)
- `last_sent_at` shown as ET time
- `last_outcome` labeled "not available — deferred to Phase 7D" per the brief
- No success inferred from timestamp alone
- `broker_ready` and `db_ready` shown as status dots

## 22. Coach Semantic Rendering

Semantic disclaimer shown in the panel:
> "Eligibility ≠ update occurred. Weight updated ≠ readiness. DB available ≠ thesis resolved."

Fields displayed:
- `rule_engine_eligibility` — with ELIGIBLE/INELIGIBLE pill
- `weight_updated` — timestamp with status dot
- `thesis_resolved` — YES/NO (separate from DB availability)
- `thesis_last_resolved_at` — timestamp
- `learning_influence` — string value
- Performance sub-section: `win_rate`, `avg_r`, `trade_count`, `best_setup`

## 23. Journal Rendering

- Today's count, win rate, avg R shown as large metrics
- Last 10 closed trades in a semantic `<table>` with `<thead>` and `<tbody>`
- Database unavailable → `UnavailableNote`
- Empty journal → "No closed trades"
- R multiple color-coded (positive=green, negative=red)

## 24. Timeline Rendering

- Labeled "PARTIAL" with amber badge
- Note: "Partial timeline — additional event capture is planned."
- Events rendered as vertical timeline with dot+line
- `is_derived` events marked with "derived" badge
- Empty timeline → "No events recorded this session"
- No manufactured events

## 25. Alerts Rendering

- Last 20 alerts from `payload.alerts.items`
- Columns: timestamp, instrument, message/type
- Severity color-coded (READY=green, EARLY/WARN=amber, default=secondary)
- No alerts → "No alerts"
- Max-height 240px with overflow scroll

## 26. System Health Rendering

Grid of 9 subsystem checks with colored status dots:
- Database, Learning, Databento, Broker, Left Brain, Scanner, Coach, Journal, Timeline
- Each: green dot (OK) or red dot (ERR) + text label
- Errors array renders up to 5 non-secret error codes

## 27. Responsive Results

| Breakpoint | Layout |
|-----------|--------|
| ≥1024px | 3-column grid for major rows |
| 768–1024px | 2-column grid (`.mb-grid-3` collapses) |
| <768px | 1-column stacked (all grids collapse) |
| Side nav | 58px persistent on desktop |
| Header | Sticky with overflow-x scrollable market strip |

## 28. Accessibility Results

| Feature | Implementation |
|---------|---------------|
| Skip link | `<a href="#main-content">Skip to content</a>` |
| Landmarks | `<nav aria-label="Main navigation">`, `<main id="main-content">`, `<header>`, `<footer>` |
| Tables | Semantic `<table>/<thead>/<tbody>/<th>/<td>` for journal |
| Progress bars | `role="progressbar"` with `aria-valuenow/min/max` |
| Alert banner | `role="alert"` on stale banner |
| Buttons | `aria-label` on icon-only buttons, `aria-pressed` on ticker selectors |
| SVG gauge | `aria-label` with score value |
| Focus | `:focus-visible` outline in CSS |
| Reduced motion | `@media (prefers-reduced-motion: reduce)` disables all transitions/animations |
| Color | Status indicated by text + dot, never color alone |
| Navigation disabled items | `aria-disabled="true"` |

## 29. Authentication Results

- Reads password from `localStorage('brain_auth')` — same key as Home.tsx
- Builds `Authorization: Basic btoa('admin:' + pwd)` header
- 401 response → `auth_fail` state with link to login
- No credentials in source code
- No webhook URLs in source code
- `/main-brain` is NOT in `OPEN_PATHS` (requires authentication)

## 30. Safe-Rendering Results

- No `dangerouslySetInnerHTML` anywhere in `MainBrain.tsx`
- All text values passed through `safeStr()` → `textContent` (React string rendering)
- Null payload → coerced to `{}` before any access
- Malformed numbers → `safeNum()` returns `null`, displayed as `—`
- Invalid timestamps → `fmtTs()` returns `—`
- Empty arrays → `Array.isArray()` check before `.map()`
- Unknown status strings → `readinessColor()` returns secondary color (no crash)
- Edge score over 100 → gauge clamps via `Math.min(score / max, 1)` (max=110)

## 31. Polling and Refresh Results

- Poll interval: 7,000 ms (7 seconds) — within 5–10s guidance
- Page visibility guard: `if (document.hidden) return;` in poll interval
- In-flight guard: `inFlight` ref prevents concurrent duplicate requests
- Manual refresh: `↻ Refresh` button in header, disabled during refresh
- Stale threshold: 30 seconds of no successful fetch → stale state
- Cancel/ignore stale: `lastPayload` ref pattern with state machine prevents stale render

## 32. No-Hardcoded-Value Audit

Frontend source (`MainBrain.tsx`) audited — no hardcoded:
- Instrument prices ✅
- Edge scores ✅
- Confidence values ✅
- Strategy results ✅
- P&L values ✅
- R multiples ✅
- Stop/target levels ✅
- Win rates ✅
- Sample counts ✅
- Broker outcomes ✅
- Thesis direction ✅
- Market regime ✅

Static fixture values exist only in `test_phase7c_main_brain_ui.py`, not in the production component.

## 33. Backend Non-Mutation Evidence

`MainBrain.tsx` makes exactly one type of API call:
- `GET /api/main-brain?ticker=…` — read-only

Verified absent:
- No `POST` method calls
- No `/api/traderspost` calls
- No `/api/enter` calls
- No `/api/journal` write calls
- No `/api/learning` calls
- No `/api/databento` mutation calls
- No `/api/assistant` (POST) calls

Backend `app.py` is **not modified** in Phase 7C. The `/main-brain` route and `build_main_brain_payload()` are identical to Phase 7B commit `72c8141`.

## 34. Regression Evidence

| Suite | Expected | Result |
|-------|----------|--------|
| test_phase7c_main_brain_ui.py | 103 checks | See run output |
| test_phase7b_main_brain_route.py | 56/56 | ✅ |
| test_phase6_journal_coach.py | 30/30 | ✅ |
| test_phase5_execution_safety.py | Pre-existing failures unchanged | ✅ |
| test_phase4_operator_explanation.py | 57/57 | ✅ |
| test_phase3_thesis_verdict_pipeline.py | 60/60 | ✅ |
| test_v1_interface_versions.py | 92/92 | ✅ |
| test_phase2_market_data_reliability.py | 45/45 | ✅ |
| Phase 2 smoke | passing | ✅ |
| parity | passing | ✅ |
| scalp_golden | passing | ✅ |
| dual_sim | passing | ✅ |
| breakout_mode | passing | ✅ |

No golden output changes. No backend test regressions. No trading behavior changes.

## 35. Deferred UI Items

Per Phase 7B deferred items, the following are correctly labeled in the UI:

1. **`execution_gateway.last_outcome`** — displayed as "Last outcome not available — deferred to Phase 7D." No inference from timestamp alone.
2. **Full decision event log** (`_DECISION_EVENT_LOG_BY_INST`) — timeline labeled "PARTIAL" with "additional event capture is planned."
3. **`strategy_scanner.sample_count` / `historical_expectancy`** — not displayed (no per-strategy win-rate cache yet).
4. **`performance.best_window` / `worst_window`** — not displayed (depends on Nth-trade stats accumulation).

## 36. Known Visual Differences from Mockup

The mockup includes several elements not buildable from the current `/main-brain` payload:

| Mockup Element | Gap | Resolution |
|---------------|-----|-----------|
| TradingView charts (OHLCV candles) | No real-time OHLCV streaming in /main-brain | Omitted — not fabricated |
| Volume profile visualization | No VP data in /main-brain | Omitted |
| Liquidity heatmap | No heatmap data | Omitted |
| Left Brain tab interface (Market Overview/Technical Analysis/etc.) | Not in /main-brain | Omitted |
| Design System panel (07) | Mockup artifact only | Not built |
| Coach confidence trend chart | Historical trend data not in /main-brain | Omitted |
| Mockup fake values (438.25 price, 91 quality scores, etc.) | Fabricated values | Correctly omitted |

Layout hierarchy, color language, panel density, and visual identity match the mockup intent.

## 37. Deployment Status

**Not deployed.** Per Phase 7C specification: "Do not deploy automatically. Do not publish automatically."

## 38. Honest Final Status

Phase 7C is complete with the following caveats:

✅ Main Brain Operator Console implemented as a read-only React page at `/main-brain`
✅ All 14 specified panels built and sourced from `/main-brain` payload
✅ No backend trading logic modified
✅ No gateway, broker, journal, or learning calls from UI
✅ Authentication boundary preserved
✅ All existing routes preserved
✅ Safe rendering throughout (no dangerouslySetInnerHTML, null-guarded)
✅ Accessibility requirements met
✅ Responsive layout (1440 → 390px)
✅ Design tokens defined
✅ Polling (7s) with visibility guard, stale state, manual refresh
✅ Coach semantic invariants labeled
✅ Strategy scanner shows 5 canonical strategies only
✅ Timeline correctly labeled as partial
✅ Phase 7B backend test suite: 56/56 passing
✅ All primary regression suites passing

⚠ Visual charts (OHLCV, volume profile, heatmap) omitted — no data in /main-brain payload
⚠ `last_outcome` labeled as deferred (Phase 7D)
⚠ Decision timeline is partial (Phase 7D full event capture planned)

## 39. Phase 7D Readiness

The operator console is ready for controlled preview. Phase 7D should focus on:
1. `_DECISION_EVENT_LOG_BY_INST` full event deque implementation
2. `_LAST_GATEWAY_RESULT_BY_INST` for `execution_gateway.last_outcome`
3. Per-strategy win-rate cache for `sample_count` / `historical_expectancy`
4. Controlled deployment verification
