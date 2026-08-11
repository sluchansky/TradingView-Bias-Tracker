---
name: Signal source ownership — CVD, RVOL, FVG, BOS/CHOCH, Sweeps
description: Canonical writer rules for all dual-source signals; guard pattern; dedup engine; classification table.
---

# Signal Source Ownership (Duplicate Input Consolidation)

## Classification table

| Signal | Classification | Canonical writer | Status |
|---|---|---|---|
| CVD | DUAL_SOURCE → **DATABENTO_ONLY** (guarded) | Databento | ✅ Phase 1 done |
| RVOL | DUAL_SOURCE → **DATABENTO_ONLY** (guarded) | Databento | ✅ Phase 1 done |
| FVG | **DATABENTO_ONLY** (already clean) | Databento | ✅ No fix needed |
| BOS/CHOCH/HH/HL/LH/LL | DUAL_SOURCE → DEDUP ENGINE | Databento canonical | ✅ Phase 2 done |
| Sweeps | DUAL_SOURCE → DEDUP ENGINE | Databento canonical | ✅ Phase 3 done |
| VWAP | DUAL_SOURCE | Databento (by recency) | 🔜 Next phase |
| Zones | LEGACY_ONLY | TV only | No Databento zone writer exists |

## The guard: `_databento_is_canonical(record, max_stale_secs=300)`

Defined in app.py after `now_utc()` at ~line 3990.

- Returns True when `record['source'] == 'databento'` AND `record['ts']` is < 300 seconds old.
- Fails OPEN: missing source, wrong source, or stale ts → returns False → TV may write.
- 300 s = ~5 completed 1m bars (Databento writes every bar close).

## CVD guard wiring

In the TV CVD webhook handler (app.py ~line 53712):
- `_tv_cvd_blocked = _databento_is_canonical(CVD_BY_TICKER.get(resolved_inst) or {})`
- If blocked: return `{"status": "cvd_tv_shadow", "canonical": "databento"}` — no state write.
- If not blocked: write as before, now with `"source": "tradingview"` tag added.

## RVOL guard wiring

In the generic RVOL field ingestion block (app.py ~line 53669):
- `if _databento_is_canonical(RVOL_BY_TICKER.get(resolved_inst) or {}): skip write`
- Else: write with `"source": "tradingview"` tag added.

## FVG: already clean (no fix needed)

- `FVG_ZONES_BY_INST` is exclusively written by `fvg_engine.process_bar_close()` via Databento bar-close callback.
- TV FVG alerts (BULLISH FVG / BEARISH FVG) go through the analyst branch → ALERT_HISTORY only.

## BOS / CHOCH / HH / HL / LH / LL dedup (structure_dedup.py — Phase 2)

New file: `structure_dedup.py` — standalone module, no circular imports.
Singleton: `STRUCTURE_DEDUP = StructureDedup()` imported by app.py and databento_brain.py.

**Two call sites:**
- `databento_brain._inject_alert()` → snapshot history BEFORE append → append → call `on_databento_event(record, snapshot)` which retroactively sets `canonical=False` on matching TV entries already in history.
- `app.py` TV webhook append path (before ALERT_HISTORY.append) → `on_tv_event(record, list(ALERT_HISTORY))` → sets `source='tradingview'`, `canonical=True/False`, `duplicate_of=<db_ts>`.

## {inst} BULLISH/BEARISH SWEEP dedup (structure_dedup.py — Phase 3)

`SWEEP_TYPES` frozenset in `structure_dedup.py` covers all 8 prefixed types (4 instruments × 2 directions: MGC/MNQ/MES/MYM × BULLISH/BEARISH SWEEP).

The same `StructureDedup` engine handles both structure and sweep events — identical logic, separate per-family metric dicts. `on_tv_event` / `on_databento_event` dispatch by checking `a_type in STRUCTURE_TYPES` vs `a_type in SWEEP_TYPES`.

**Match criteria (same for both families):**
- same `alert_type` + same `instrument` + |Δt| < 90s + |Δprice| < 10 ticks
- Tick sizes: MGC=0.10, MNQ/MES=0.25, MYM=1.00
- Price check fail-open (skipped if either side missing price)

**Sweep consumers with `if a.get("canonical") is False: continue` filter:**
- `_latest_ts()` in `evaluate_strict_setup` (gate) ✅ Phase 2
- `latest()` in `_strategy_signal_snapshot` (gate) ✅ Phase 2
- `_alert_source()` in edge diagnostics (display) ✅ Phase 2
- `_recent_sweep()` in Liquidity Focus overlay ✅ Phase 3 (newly added)
- `_early_latest_ts()` in EARLY timing path ✅ Phase 3 (newly added)

**Metrics route:** `GET /structure-dedup-metrics` (owner-only) now returns:
```json
{
  "structure": { "tv_events_received": N, "databento_events_produced": N, ... },
  "sweep":     { "tv_events_received": N, "databento_events_produced": N, ... }
}
```
Legacy `.metrics` property on the class aggregates both families as flat dict (backward compat).

**Fast-entry bridge events:** Tagged `canonical=True` explicitly; never passed through dedup.

**Chart endpoint:** Shows ALL entries including shadows for audit; adds `canonical` field to output dict.

## Tests

- `test_structure_dedup.py` — 54 tests (structure dedup, Phase 2); metrics tests updated to use `m["structure"]` namespace.
- `test_sweep_dedup.py` — 52 tests (sweep dedup, Phase 3): taxonomy, TV/DB alone, DB-first→TV-shadow, TV-first→DB-retroactive-demote, time/price tolerance, per-instrument tick scaling, missing-price fail-open, conflict detection, cross-instrument isolation, canonical filter semantics, LSR-no-duplicate, chart audit, metrics independence, structure regression.
- Combined: 685 targeted tests pass, all 4 smokes OK.

## What's NOT in scope yet (next phases)

- **VWAP reclaim/rejection:** TV writes source='chart'; Databento overwrites on each bar close (recency wins, no write-exclusion). Next candidate for full dedup treatment.
- **Zones:** TV-only, no Databento zone writer.
