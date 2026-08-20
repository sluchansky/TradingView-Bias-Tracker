---
name: Order Flow Engine V1
description: Bar-level buy/sell volume accumulation + delta scoring; bounded direction-aware Edge Score confluence; flag-gated default OFF.
---

## What was built

`order_flow_engine.py` computes the directional 0–100 order-flow read. `full_analysis()` snapshots it before the strict gate and applies its bounded score adjustment through the same per-direction modifier path used by the canonical Edge Score.

`databento_brain.py` — `_tick_bar()` now accepts `side` param (A/B/None); accumulates `buy_volume` / `sell_volume` per partial bar. `_on_bar_close()` publishes these plus `cvd_snapshot = self._cvd_acc[inst]` to the public bar in `DATABENTO_BARS_BY_INST`. Bars before V1 deployment lack these fields — `_bars_have_of_fields()` detects absence and returns `available=False, reason=bars_pre_v1`.

`ghost_observations` — 14 new `of_*` columns added via `db_order_flow_schema_patch.sql` (applied dev; needs re-publish for prod). Applied individually via executeSql (not file-split).

## Enablement

- Flag: `ORDER_FLOW_V1_ENABLED=1` (env var, default OFF)
- When flag is OFF, `compute_order_flow()` returns `{available: False, reason: flag_off}` and `result["order_flow"]` key is absent from `full_analysis` (the key is only added when flag is ON)

## Safety contract

- Direction-aware Edge Score adjustment only: score 50 is neutral; bullish flow helps Longs and hurts Shorts, with the reverse for bearish flow.
- Bounded at ±15 points, clamped inside the existing 0–110 Edge Score range.
- Missing, stale, malformed, or unavailable flow is a strict 0-point no-op.
- The exact pre-gate snapshot is reused for result display; do not take a second read later in analysis.
- Hard-zero setups remain zero because the existing zone/structure hard-block applies after signed modifiers.
- The Order Flow engine itself remains pure/fail-open; it does not route orders or alter sizing directly.

## Metric notes

- `cvd_slope` anchor formula: `valid[max(0, len(valid) - 1 - n)]` — n means "n bars back from last", NOT the bar at `len-n` (off-by-one trap)
- Sweep detection in `detect_reversal_sequence()` uses **range spike vs avg_range** (not wick/body ratio) — wick/body blows up for doji bars where body=0, giving infinite ratio → false positives
- `book_imbalance` is always None; no order-book subscription (only trades schema)
- `bid_volume` = sell aggressor (hit the bid), `ask_volume` = buy aggressor (hit the ask)

## DB columns (ghost_observations)

```
of_bar_delta BIGINT
of_delta_ratio NUMERIC(8,4)
of_delta_acceleration BIGINT
of_ask_volume BIGINT
of_bid_volume BIGINT
of_book_imbalance NUMERIC(8,4)   -- always NULL
of_cvd NUMERIC(14,2)
of_cvd_slope NUMERIC(14,2)
of_cvd_divergence TEXT
of_absorption_side TEXT
of_absorption_strength TEXT
of_order_flow_score SMALLINT
of_order_flow_state TEXT
of_reversal_confirmed BOOLEAN
```

## Routes

- `GET /order-flow/status` — live per-instrument OF state; flag-off returns `{enabled: false}`; added to `BOT1_ROUTES` proxy whitelist

## Tests

70 tests across `tests/test_order_flow_v1.py` and the Edge Score integration suite.
