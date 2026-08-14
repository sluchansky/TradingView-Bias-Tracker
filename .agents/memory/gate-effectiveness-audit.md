---
name: Gate Effectiveness Audit Phase 8C (unified pipeline)
description: DB schema, analytics functions, Flask routes, and React panel for per-mode gate breakdown (SCALP + INTRADAY_TREND).
---

## DB schema additions (applied dev + schema doc updated)
- `gate_audit_log` now has `strategy TEXT` and `setup_id TEXT` columns
- `setup_id = INST|DIR|MODE|YYYYMMDD` — groups repeated hourly polls of the same directional opportunity
- `strategy` — "SCALP" | "INTRADAY_TREND" | "ORB" | "IT_HYPOTHETICAL" etc.
- Indexes: `idx_gal_mode (mode)`, `idx_gal_setup (setup_id)`
- Back-fill ran (293 rows updated)

## IT hypothetical geometry
- When IT is BLOCKED before a real plan is built (the common NO_GEOMETRY case), gate_effectiveness.py now computes hypothetical entry/stop/target using `current_price + ATR×1.5 (stop) + 2R (target)`
- Strategy label set to `"IT_HYPOTHETICAL"` to distinguish from real plans
- ON CONFLICT promotes `outcome_status = NO_GEOMETRY → PENDING` when geometry arrives on update
- This feeds the existing counterfactual watcher — no new watcher needed

## gate_effectiveness.py new functions
- `_blocker_category(blocker, mode)` — maps any blocker string to 7 audit categories (Zone/location, Structure, Trend alignment, Time/session, Confirmation, Volatility, Other)
- `_extract_strategy(result, mode)` — extracts ORB / BREAKOUT / IT setup_type / mode name
- `get_mode_report(mode)` — per-mode gate breakdown: totals, geometry rate, expectancy, gate categories table, component pass rates
- `get_mode_comparison()` — side-by-side SCALP vs IT (calls get_mode_report for each)
- `get_opportunities(mode, days, instrument)` — deduplicated view (one row per day×inst×dir×mode×blocker)

## Flask routes added (after /gate-effectiveness/saved-losses)
- `GET /gate-effectiveness/mode-report?mode=SCALP|INTRADAY_TREND`
- `GET /gate-effectiveness/mode-comparison`
- `GET /gate-effectiveness/opportunities?mode=&days=7&instrument=`
- All whitelisted in `artifacts/api-server/src/routes/flask-proxy.ts`

## React component
- `GateEffectivenessPanel` added to `artifacts/home/src/pages/MainBrain.tsx`
- Placed after `<ModeOverviewPanel>` in the JSX (before the live chart)
- Collapsible, loads on open, auto-refreshes every 5 min
- Tabs: SCALP / INTRADAY TREND
- Shows: summary chips (observations, geometry rate, evidence, blocked expectancy, gate value)
- Gate breakdown table: Gate | Blocks | % | Unique | Geom | W-Win | W-Lose | Exp.R
- Component pass rates: 8 chips (BOS, CHOCH, VWAP, Sweep, Volume, CVD, Session, Zone)

## Why
- 134 prod IT records were all BLOCKED, all NO_GEOMETRY — hypothetical geometry unlocks counterfactual tracking
- setup_id deduplication prevents poll noise from inflating block counts in the mode report
- Mode-separated analytics answer "which gate is wrong?" per mode independently — prerequisite before any gate threshold change

## Key constraint
- All functions FAIL-OPEN and DISPLAY-ONLY — no gate, execution, or risk path touched
- App.py INSERT-only rule respected: schema changes via DB tool only

## Prod apply
- Need to Publish once so the 3 new Flask routes and schema columns are live in prod
- After publish, IT BLOCKED records will start accumulating hypothetical geometry → watcher resolves → zone_valid counterfactual expectancy becomes available
