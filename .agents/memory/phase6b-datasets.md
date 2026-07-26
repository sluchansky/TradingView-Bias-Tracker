---
name: Phase 6B historical OHLCV datasets
description: Databento acquisition quirks, dataset IDs, and run_backtest calling convention for Phase 6B baseline work.
---

## Dataset IDs (backtest_datasets table, 2026-01-01→2026-06-30, 5m)

| Instrument | ID | Rows   | Gaps | Status |
|------------|-----|--------|------|--------|
| MNQ        |  8  | 34,863 | 128  | READY  |
| MES        |  9  | 34,863 | 128  | READY  |
| MGC        | 10  | 18,929 | 433  | READY  |
| MYM        | 11  | 34,729 | 152  | READY  |

Also dataset_id=7: MNQ 5-day sample (1,080 bars, 2026-07-14→2026-07-17).

## Acquisition quirks

**stype_out must NOT be set when stype_in="continuous"** — Databento returns 422 if you pass `stype_out="continuous"` alongside `stype_in="continuous"`. Omit stype_out entirely (default is instrument_id).

**MYM needs chunked download** — 170K 1-minute bars in one request exceeds the 2-minute bash timeout. Download in two 3-month chunks (Jan→Apr, Apr→Jul), concatenate DataFrames in memory with `pd.concat().sort_index().drop_duplicates()`, then resample and import as a single dataset. MNQ/MES/MYM all have ~170K 1m bars; MGC has ~91K (COMEX has fewer trading hours).

**Reduced-quality days warning is benign** — Databento emits a `BentoWarning` for Feb 14/21/28 and some April days marked "missing/degraded". These are CME holiday or data-quality flags; the gap_count reflects them correctly (128/152 gaps for equity index futures, 433 for gold). Do not re-download to avoid them.

## run_backtest calling convention

```python
# CORRECT — takes (candles: list[dict], params: dict)
res = run_backtest(candles, {
    "symbol":            "MNQ",      # instrument ticker
    "mode":              "SCALP",    # or "SWING"
    "strategies":        list(STRATEGY_ORDER),
    "management":        BT_MGMT_LEGACY,  # "partial_tp3"
    "news_blackouts_et": [],         # empty = no blackout filter
})
# Result keys: ok, symbol, mode, bars, strategies{}, ranking[], trades[], total_trades

# WRONG — no positional kwargs, no `instrument=` param
run_backtest(candles=..., instrument=inst, strategy=strat, mode=mode)
```

**Why:** `run_backtest` signature is `def run_backtest(candles, params)` where params is a plain dict. It was not designed for per-strategy single-call invocation; pass the full strategy list and read `res["strategies"]` for per-strategy metrics.

## Smoke test interpretation

0 trades on a 5-day window is expected (indicators need warm-up bars). The 6-month datasets reliably generate trades: MNQ=197, MES=446, MGC=56, MYM=770. The smoke test goal is no-crash, not a specific trade count.

## Phase gate

ALL 4 instruments READY → Phase 6B full baseline run may proceed.
