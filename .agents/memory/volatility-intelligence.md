---
name: Volatility Intelligence Module
description: VIX-based context layer wired into Left Brain; Phase B-E complete; Phase F (32 tests passing).
---

# Volatility Intelligence Module

**Status**: Phases B–E implemented, Phase F (tests) complete. Default OFF.

**Key files**:
- `artifacts/tradingview-webhook/volatility_intelligence.py` — standalone module (provider, analysis engine, public API)
- `artifacts/tradingview-webhook/db_volatility_intelligence_schema.sql` — DDL (table already created in dev DB)
- `artifacts/tradingview-webhook/test_volatility_intelligence.py` — 32 tests, all passing
- `artifacts/home/src/pages/MainBrain.tsx` — `VolatilityIntelligencePanel` component added before `ThesisPanel` (~line 696)

**Activation**:
Set `VOL_INTELLIGENCE_ENABLED=1` in Replit Secrets/Env to enable. All other flags default safe:
- `VOL_INTELLIGENCE_OBSERVE_ONLY=1` (stay on until paper-trading evidence gathered)
- `VOL_INTELLIGENCE_EXECUTION_INFLUENCE=0`
- `VOL_INTELLIGENCE_SCORE_INFLUENCE=0`

**Provider**: Alpha Vantage (`ALPHA_VANTAGE_API_KEY` secret). Free tier = 25 req/day → set `ALPHA_VANTAGE_FETCH_INTERVAL_SEC=1200` for free tier users. Default 300s (5-min) is fine for paid plans.

**VIX symbol**: `^VIX` via `GLOBAL_QUOTE` endpoint. History via `TIME_SERIES_INTRADAY` (5-min bars).

**Data is always labeled DELAYED** (Alpha Vantage has a delay; never claim real-time).

**app.py insertion points**:
1. `_mb_left_brain()` return dict — adds `volatility_intelligence` key (display-only, fail-open)
2. `full_analysis` display seam (after LEFT_BRAIN_MARKET_INTELLIGENCE block) — adds `result["volatility_intelligence"]`
3. Boot init after DatabentoBrain block — starts background thread when flag ON
4. Flask route `GET /volatility-intelligence` — modeled on `/swing-analysis`

**Express proxy whitelist**: `/volatility-intelligence` added to `flask-proxy.ts`.

**DB table**: `volatility_observations` created in dev DB (needs re-publish to apply in prod).

**Safety contract**:
- `execution_effect` is always `"NONE"` — asserted in `_assert_observe_only()`
- `score_effect` is always `0`
- Module never raises — `get_snapshot()` always returns a well-formed dict
- Missing/stale VIX never crashes scanner or changes any verdict

**Acceleration fix**: acceleration compares absolute slope magnitude, not raw sign.
**Why:** falling-then-decelerating (e.g. -0.7→-0.1) must be DECREASING, not INCREASING.
