---
name: Scalping Strategy Research Engine
description: research/display-only scalp-strategy lab walled off from the live money path; invariants any change must keep
---

The Scalp Research Engine (`scalp_research.py` + `/scalp-research` route + dashboard "Research" tab) researches/simulates/ranks candidate scalp strategies against historical candle datasets. It is DISPLAY/RESEARCH-ONLY and must never touch the live money path.

**Why:** new strategies are unproven; the whole point is to study them WITHOUT risking real fills. It mirrors the backtest_engine wall.

**How to apply — invariants any change MUST preserve:**
- Separate `RESEARCH_DETECTORS` / `STRATEGY_LIBRARY` registry. NEVER register research detectors into `backtest_engine.DETECTORS` / `STRATEGY_DEFS` / `STRATEGY_ORDER`, the live engine, gate, auto-trade, sizing, or `/traderspost`. `bt.*` pure helpers are reused READ-ONLY; do NOT edit backtest_engine.py (goldens stay byte-identical).
- `live_status` ∈ {watch, simulation, recommended} ONLY — never an actionable/live value. Promotion is advisory TEXT; actual manual promotion is outside this feature.
- No DDL in app.py — probe + INSERT/SELECT/UPSERT only (tables `scalp_strategy_library` / `scalp_strategy_research` created out-of-band, like the learning engine).
- Recompute is throttled + single-flight + gated to the live instance (DISCORD_LIVE_ENABLED) and NEVER runs in the webhook/gate path. GET is display-only and never triggers a recompute; POST starts a BACKGROUND recompute and returns immediately, so the dashboard must poll GET until `generated_at` changes (not load once).
- Honest labels: unsupported strategies stay `data_pending` / `detector_pending` with NO fabricated stats. Live strategies appear in the ranking labelled `is_live`.
- Owner-only: `/scalp-research` is whitelisted in `flask-proxy.ts` but NOT in dashboard-auth `OPEN_PATHS`. Unauth proxied GET returns 401 (same as `/api/backtest/datasets`). Dashboard renders research data with textContent only (no innerHTML → no XSS).
- The view dict keys (`library`/`tested`/`best`/`worst`/`promotions`/`counts`/`datasets`/`combined.*`, where combined has total_trades/win_rate(0-100)/avg_r/net_r/profit_factor/max_drawdown_r/best_session) ARE the dashboard JS contract — a new field needs adding to BOTH the Python `_build_view` and the JS render or cells go blank.
