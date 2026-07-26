---
name: Phase 6B.1 Baseline Engine
description: How the bt_baseline.py engine works, DB schema quirks, and the first official baseline ID
---

## First Official Baseline
- ID: `BL-20260726-043053-0cc8364`
- Config hash: `68fd5b2c96b4f2ee`
- 40 combos (4 inst × 2 modes × 5 strategies), 7,909 trades, 64 breakdowns
- Best by net R: MGC/SWING/LIQUIDITY_SWEEP_REVERSAL (+42.24R, WR=50%, n=178)

## bt_baseline.py key functions
- `generate_baseline([8,9,10,11])` — full matrix runner; returns ok/baseline_id/summary/per_combo/rankings
- `_jdump(obj)` — JSONB-safe serializer: handles float inf/nan AND set/frozenset (converts to sorted list)
- `_freeze_config(commit)` — deterministic config snapshot; BT_SPECS may contain frozensets → _jdump handles them
- `_run_combination(inst, mode, strategy, candles)` — runs one cell; filters trades to requested strategy only
- `_extended_metrics(trades, inst)` — 30+ fields including streaks, hold stats, direction split, reliability label
- `_build_trade_records(...)` → tuple rows for batch INSERT; mfe_r/mae_r always None (unsupported)
- `_compute_breakdowns(combos)` → 10 breakdown types: instrument/mode/strategy/direction/session/et_hour/weekday/month/volatility_regime/instrument_mode

## DB Schema quirks
- `baseline_trades` was missing `initial_risk_r` column on creation; added via ALTER TABLE
- Deletion order: trades → breakdowns → matrix_results → configs (FK constraints)
- `baseline_matrix_results` uses `completed_trades` column (NOT `trade_count`)
- Detail route returns `matrix_results` key (not `per_combo` — that's only in generate_baseline return)

## API routes (all owner-gated, NOT in OPEN_PATHS)
- GET `/backtest/baselines` — list with summary
- GET `/backtest/baselines/<id>` — full detail (matrix_results key has 40 rows)
- GET `/backtest/baselines/<id>/trades?instrument=&strategy=...` — filtered trade records
- GET `/backtest/baselines/<id>/breakdowns` — aggregated breakdowns
- POST `/backtest/baselines/generate` — generate new baseline (body: {dataset_ids: [8,9,10,11]})

**Why:** Baseline is immutable research artifact; INSERT-only, no DDL in bt_baseline.py, never touches money path.
**How to apply:** Future phases can compare new baselines against BL-20260726-043053-0cc8364 to detect drift.
