---
name: Phase 6B.1 Baseline Engine
description: How the bt_baseline.py engine works, DB schema quirks, first official baseline ID, and known tooling issues
---

## First Official Baseline
- ID: `BL-20260726-043053-0cc8364`  (NOT `042830` — first attempt was deleted; 042830 does not exist in DB)
- Config hash: `68fd5b2c96b4f2ee`
- 40 combos (4 inst × 2 modes × 5 strategies), 7,909 trades, 64 breakdowns
- Dataset IDs: 8=MNQ, 9=MES, 10=MGC, 11=MYM (all 5m, Jan1–Jun30 2026, Databento)
- Best by net R: MGC/SWING/LIQUIDITY_SWEEP_REVERSAL (+42.24R, WR=50%, n=178)
- Overall: −377.5R net, 36.38% WR, PF 0.9274; MGC only profitable instrument (+40.1R)
- readiness: BASELINE_ANALYSIS_USABLE_WITH_WARNINGS

## bt_baseline.py key functions
- `generate_baseline([8,9,10,11])` — full matrix runner; returns ok/baseline_id/summary/per_combo/rankings
- `_jdump(obj)` — JSONB-safe serializer: handles float inf/nan AND set/frozenset (converts to sorted list)
- `_freeze_config(commit)` — deterministic config snapshot; BT_SPECS may contain frozensets → _jdump handles them
- `_run_combination(inst, mode, strategy, candles)` — 4 args; takes pre-loaded candles (NOT ds_id+cfg); caller must call `_load_candles(ds_id, conn)` first
- `_extended_metrics(trades, inst)` — 30+ fields including streaks, hold stats, direction split, reliability label
- `_build_trade_records(...)` → tuple rows for batch INSERT; mfe_r/mae_r always None (unsupported)
- `_compute_breakdowns(combos)` → 10 breakdown types: instrument/mode/strategy/direction/session/et_hour/weekday/month/volatility_regime/instrument_mode

## DB Schema quirks
- `baseline_trades.initial_risk_r` — added after initial CREATE; migration in `bt_baseline_migrations.sql` (idempotent ALTER TABLE IF NOT EXISTS)
- CRITICAL: bt_baseline.py must contain NO DDL strings — test_BL055b_baseline_no_ddl does a static source scan; put all migrations in bt_baseline_migrations.sql instead
- Deletion order: trades → breakdowns → matrix_results → configs (FK constraints)
- `baseline_matrix_results` uses `completed_trades` column (NOT `trade_count`)
- Detail route returns `matrix_results` key (not `per_combo` — that's only in generate_baseline return)
- `backtest_datasets` columns: id, symbol, timeframe, source_label, source_tz, original_filename, sha256, row_count, gap_count, first_ts, last_ts, uploaded_at — no quality_classification/is_real_data/etc.

## Config hash drift (KNOWN OPEN ITEM)
- `_freeze_config` uses `list(bt.VALID_SYMBOLS)` and `list(bt.VALID_TIMEFRAMES)` — iterates Python sets non-deterministically across processes
- Hash drifts between runs: stored `68fd5b2c96b4f2ee` ≠ recomputed `4c18b4492442e5d4` even with identical logic
- FIX before next baseline generation: change to `sorted(bt.VALID_SYMBOLS)` and `sorted(bt.VALID_TIMEFRAMES)`
- backtest_engine.py: byte-identical to 0cc8364 (0 diff lines) — simulation logic unchanged; config hash drift is tooling only

## API routes (all owner-gated, NOT in OPEN_PATHS)
- GET `/backtest/baselines` — list with summary
- GET `/backtest/baselines/<id>` — full detail (matrix_results key has 40 rows)
- GET `/backtest/baselines/<id>/trades?instrument=&strategy=...` — filtered trade records
- GET `/backtest/baselines/<id>/breakdowns` — aggregated breakdowns
- POST `/backtest/baselines/generate` — generate new baseline (body: {dataset_ids: [8,9,10,11]})

## Key findings from first baseline
- Short-side structurally disadvantaged (−432R) vs long (+54R) in Jan–Jun 2026 uptrend period
- Thursday is extreme outlier weekday (+165R); Monday worst (−247R)
- ORB dominated by single 151-day MGC/SWING/ORB short trade — remove it and ORB is negative overall
- Strategy performance is highly instrument-dependent (LSR: MGC +67R vs MES −68R)
- Cost drag: −265R total (commission −164R + slippage −101R) across 40 combos
- MYM SCALP minimum-stop stops exit at exactly −1.174R (deterministic cost model artifact)
- 2 ORB entries before 08:00 ET exist — flag for investigation (opening_range_start_et=8.0, builds 30min)

**Why:** Baseline is immutable research artifact; INSERT-only, no DDL in bt_baseline.py, never touches money path.
**How to apply:** Future baselines compare against BL-20260726-043053-0cc8364. Fix _freeze_config set ordering before next generation run.
