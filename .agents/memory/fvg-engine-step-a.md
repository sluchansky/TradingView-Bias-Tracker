---
name: FVG Engine Step A
description: Fair Value Gap / IFVG all-day scanner — architecture, wiring, and key invariants for Step A (shadow/display-only).
---

# FVG Engine — Step A (Shadow/Display-Only)

## What was built
- `fvg_engine.py`: standalone FVG/IFVG lifecycle engine called on every Databento 1m bar close.
- `fvg_zones` DB table: created via DB tool (no DDL in app.py — same pattern as all other tables).
- App.py wiring: `_fvg_bar_close` callback registered via `_DATABENTO_BRAIN.register_bar_close_callback`; FVG seam at `full_analysis` end; `fvg_summary` key in `build_main_brain_payload`; `fvg_zones` key in `/main-brain/chart`; boot probe after `_boot_native_journal_table`.
- Flask routes: `GET /fvg/zones`, `GET /fvg/summary` — no `@_owner_required` (auth via Express proxy, consistent with `volatility_intelligence_endpoint`).
- Express proxy whitelist: `/fvg/zones`, `/fvg/summary` in `BOT1_ROUTES` in `flask-proxy.ts`.
- React panel: `FVGScannerPanel` component reads `p.fvg_summary`; placed in Analysis tab after `VolatilityIntelligencePanel`.
- Tests: `test_fvg_engine.py` — 61 tests covering detection, lifecycle, IFVG, API, ranking, safety.

## Key engine invariants

**Safety contract:** NEVER modifies gate verdicts, edge scores, position sizes, or execution. Fail-open everywhere.

**Detection (3-candle pattern):**
- Bullish FVG: `bar[-3].high < bar[-1].low` AND displacement candle body ≥ 1.2×ATR AND candle is bullish
- Bearish FVG: `bar[-3].low > bar[-1].high` AND displacement ≥ 1.2×ATR AND candle is bearish
- Gap must be ≥ FVG_MIN_SIZE_ATR (default 0.08×ATR)
- Deduplication by `bar[-1].ts` — same anchor ts = same gap

**Critical bug fixed during implementation:**
- `status` local variable in `_update_zone_lifecycle` is NOT refreshed when `zone["status"]` changes within the same bar. The HOLDING check MUST use `zone["status"]` (not `status`) or zones that go ACTIVE→TOUCHED→MITIGATED in one bar never get the HOLDING check applied.
- Newly created zones must skip lifecycle update on their birth bar (use `new_zone_ids` set) or the creating bar touches its own zone immediately.

**IFVG direction semantics:**
- `ifvg_direction="BEARISH"`: failed BULLISH FVG → price broke DOWN. Retest = price rallies BACK UP → `bar.high >= zone.lower`
- `ifvg_direction="BULLISH"`: failed BEARISH FVG → price burst UP. Retest = price drops BACK DOWN → `bar.low <= zone.upper`
- NOTE: these are OPPOSITE to what you'd expect from the label — read the code comments carefully.

**ATR for test fixtures:**
- Warmup bars must have `range = atr_size` (not `2×atr_size`) or displacement ratio is 0.7 < threshold 1.2
- Use: `make_bar(i, 2000.0, 2000.0 + atr_size/2, 2000.0 - atr_size/2, 2000.0)`

**`@_owner_required` decorator:**
- NOT defined at line ~75400 where the FVG routes were inserted. Remove it — Express proxy handles auth.
- Consistent with `volatility_intelligence_endpoint` which also has no `@_owner_required`.

## Step B (not yet built)
Full sequence states (FVG_CREATED → RETURN_PENDING → FVG_TOUCHED → HOLD_PENDING → HOLD_CONFIRMED → STRUCTURE_PENDING → MOMENTUM_PENDING → ENTRY_WINDOW → READY), entry window, stop/target calculation, and Discord/dashboard alerting.
