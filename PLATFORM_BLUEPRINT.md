# AI Trading Partner — Master Platform Blueprint
**Version 1.0 — July 2026**
**Status: Architecture & Inventory Document — NO CODE CHANGES**

---

## Table of Contents

1. [System Inventory](#system-inventory)
2. [Product Role Mapping](#product-role-mapping)
3. [Feature Map](#feature-map)
4. [Data Flow](#data-flow)
5. [Gap Analysis](#gap-analysis)
6. [Product Roadmap](#product-roadmap)

---

# TASK 1 — SYSTEM INVENTORY

## Runtime Topology

```
[ TradingView Pine Scripts ]
          │ HTTP POST /webhook
          ▼
[ Express API Server :8080 ]  ←── Basic Auth + CSRF + HMAC view-tokens
          │ proxy /api/* → :8000
          │ proxy /api2/* → :8001
          ▼
[ Flask Live Bot :8000 ]          [ Flask Analysis Bot :8001 ]
   app.py (66,794 lines)              analysis-bot/app.py
   DISCORD_LIVE=1                     ANALYSIS_ONLY=1
          │                                    │
          └──── shared PostgreSQL DB ──────────┘
          │
[ React Dashboard / home ]  ←── Vite + React, served static in prod
```

---

## Subsystems

### 1. Webhook Ingestion Layer

| Field | Detail |
|---|---|
| **Purpose** | Single entry point for all TradingView Pine script signals |
| **Responsibilities** | Receive POST /webhook, parse + normalize alert_type, resolve instrument from ticker, route to scoring or data-only path |
| **Inputs** | JSON payloads: `alert_type`, `ticker`, `price`, optional extras |
| **Outputs** | Scoring pipeline, data stores (VWAP/CVD/zone/structure), Discord alerts |
| **Dependencies** | ALERT_TYPES registry, instrument resolver, `_SHARED_ALERT_TYPES` |
| **Status** | ✅ Complete |
| **Dashboard** | Alert feed visible in diagnostics panel |
| **Production Impact** | Every trade decision originates here |
| **Files** | `app.py` lines ~42194–42650+ |

---

### 2. Instrument Registry & Resolver

| Field | Detail |
|---|---|
| **Purpose** | Canonical mapping from TradingView tickers (MGC1!, MNQ1!, etc.) to internal instrument tokens (MGC, MNQ, MES, MYM) |
| **Responsibilities** | `resolve_instrument()`, `instrument_of()`, `_instrument_from_text()`; per-instrument ALERT_TYPES prefixes; INSTRUMENT_SPECS (tick size, dollar-per-point, contract multiplier) |
| **Inputs** | Raw ticker string from webhook payload |
| **Outputs** | Canonical instrument token used throughout all subsystems |
| **Dependencies** | `_ALERT_INSTRUMENTS`, `_PER_INSTRUMENT_ALERT_TEMPLATE`, `INSTRUMENT_SPECS` |
| **Status** | ✅ Complete |
| **Dashboard** | MGC/MNQ/MES/MYM tab switching |
| **Production Impact** | Every cross-instrument calculation depends on correct resolution |
| **Files** | `app.py` lines ~200–780 |

---

### 3. VWAP Engine

| Field | Detail |
|---|---|
| **Purpose** | Maintain a per-instrument VWAP value used by the gate, edge score, and all directional analysis |
| **Responsibilities** | Accept chart/manual VWAP pushes, auto-fetch from yfinance (MGC≈GC=F, MNQ≈NQ=F), grace window on manual override, staleness tracking, VWAP diagnostics endpoint |
| **Inputs** | Pine "VWAP" webhook push, auto-fetch timer |
| **Outputs** | `vwap_value`, `vwap_status` (freshness), `vwap_age_ms`; gate `vwap_confirmed` boolean |
| **Dependencies** | yfinance, `CHART_VWAP_BY_TICKER`, `get_vwap_diagnostics()` |
| **Status** | ✅ Complete |
| **Dashboard** | VWAP value shown in price context panel |
| **Production Impact** | Gate hard-fails on stale VWAP |
| **Files** | `app.py` (VWAP fetch loop, ~VWAP_BY_TICKER stores) |

---

### 4. ATR / Volatility Monitor

| Field | Detail |
|---|---|
| **Purpose** | Per-instrument ATR measurement powering stop placement, position sizing, and volatility gate |
| **Responsibilities** | Compute 1m ATR (SCALP), 1H/4H/Daily ATR (SWING via HTF); SCALP volatility brake (ratio > 3.0 → demote); SWING hard gate (blocked → WAIT); mode-split via `VOL_HARD_GATE` env |
| **Inputs** | Price bars from Databento or OHLCV estimates |
| **Outputs** | `atr_pts`, `vol_ratio`, `volatility` block in full_analysis result |
| **Dependencies** | `get_volatility()`, VOL_MIN_BARS, Databento bar feed |
| **Status** | ✅ Complete |
| **Dashboard** | Volatility panel in diagnostics |
| **Production Impact** | SWING hard-gates on extreme volatility; SCALP brake demotes |
| **Files** | `app.py` (volatility section, `get_volatility`) |

---

### 5. CVD / Delta Engine

| Field | Detail |
|---|---|
| **Purpose** | Track cumulative volume delta to identify institutional buying/selling pressure |
| **Responsibilities** | Receive CVD_BULLISH/CVD_BEARISH webhook events, maintain committed CVD state per instrument, hard-fail veto on disagreement (Long on bearish CVD blocked), +15 edge bonus on agreement |
| **Inputs** | Pine CVD webhook alerts, underscore and spaced variants |
| **Outputs** | `cvd_state` (bullish/bearish/unknown), CVD component in edge score (+15), hard veto |
| **Dependencies** | ALERT_HISTORY, `_CVD_BY_INST` store |
| **Status** | ✅ Complete |
| **Dashboard** | CVD shown in alert_diagnostics block |
| **Production Impact** | Hard veto is a money-path filter |
| **Files** | `app.py` (CVD data ingestion, edge component) |

---

### 6. Volume / RVOL Engine

| Field | Detail |
|---|---|
| **Purpose** | Relative volume spike detection as an edge confirmation signal |
| **Responsibilities** | VOLUME_SPIKE webhook ingestion, RVOL ratio computation, +15 edge component (Volume15) when ≥~1.5×, NOT a standalone modifier |
| **Inputs** | Pine VOLUME_SPIKE alerts |
| **Outputs** | Volume edge component, `rvol` in diagnostics |
| **Dependencies** | ALERT_HISTORY, edge score helper |
| **Status** | ✅ Complete |
| **Dashboard** | Shown in alert_diagnostics |
| **Production Impact** | Edge component only, not a gate |
| **Files** | `app.py` (volume ingestion, EDGE_COMPONENTS) |

---

### 7. Market Structure Engine

| Field | Detail |
|---|---|
| **Purpose** | Detect BOS (Break of Structure), CHOCH (Change of Character), HH/HL/LH/LL swing labels — the primary "structure confirmed" signal for trade entry |
| **Responsibilities** | Accept shared un-prefixed structure alerts (CHOCH SUPPLY/DEMAND, BOS SUPPLY/DEMAND, HH/HL/LH/LL), per-instrument isolation (no cross-leak), expiry of opposing structure, `structure_confirmed` gate boolean |
| **Inputs** | Pine structure alerts, fast-entry bridge (MICRO_CHOCH/SWEEP_RECLAIM → inject LH/HL/CHOCH) |
| **Outputs** | `structure_confirmed`, `bias`, structure-reversal demote, CHOCH20/BOS20 edge components (+3/+2) |
| **Dependencies** | ALERT_HISTORY, `_instrument_from_text`, fast-entry bridge |
| **Status** | ✅ Complete |
| **Dashboard** | Structure label in per-instrument panel |
| **Production Impact** | Required gate component in SWING (80-pt floor); SCALP demotes only |
| **Files** | `app.py` (evaluate_strict_setup, structure sections) |

---

### 8. Supply/Demand Zone Engine

| Field | Detail |
|---|---|
| **Purpose** | Track institutional supply and demand zones for entry context and gate confirmation |
| **Responsibilities** | Per-instrument zone ingestion (NEW SUPPLY/DEMAND ZONE, CONFIRMED variants), zone broken/mitigated detection, SCALP TTL on mitigation, gate boolean `zone_confirmed`; SWING requires zone, SCALP demotes only |
| **Inputs** | Pine zone webhooks (prefixed per instrument) |
| **Outputs** | `zone_confirmed`, zone edge component, `zone_broken_at` TTL |
| **Dependencies** | `ZONE_BROKEN_AT`, per-instrument stores |
| **Status** | ✅ Complete |
| **Dashboard** | Zone state in READY diagnostics |
| **Production Impact** | SWING gate hard-requires zone |
| **Files** | `app.py` (zone ingestion, ZONE_MITIGATED, evaluate_strict_setup) |

---

### 9. Liquidity Sweep Detector

| Field | Detail |
|---|---|
| **Purpose** | Detect stop hunts (liquidity sweeps above highs / below lows) as a setup trigger |
| **Responsibilities** | BULLISH_SWEEP / BEARISH_SWEEP per-instrument prefixed alerts (side="sweep", scored); bare 5s sweep names (side="dual_tf", NOT scored); Sweep15 edge component (+15); sweep focus overlay |
| **Inputs** | Pine sweep webhooks |
| **Outputs** | `sweep_confirmed`, Sweep15 edge component, sweep state in FAST_ENTRY_STATE |
| **Dependencies** | ALERT_HISTORY, DUAL_TF_SWEEP_TYPES, FAST_SWEEP_RECLAIM types |
| **Status** | ✅ Complete |
| **Dashboard** | Sweep shown in alert_diagnostics, Liquidity Sweep Focus overlay |
| **Production Impact** | Edge component; sweep + reclaim triggers structure bridge |
| **Files** | `app.py` (sweep ingestion, EDGE_COMPONENTS) |

---

### 10. FVG / OB Analyst Evidence

| Field | Detail |
|---|---|
| **Purpose** | Fair Value Gaps and Order Blocks as analyst context — DISPLAY-ONLY, never gates a trade |
| **Responsibilities** | Accept BULLISH/BEARISH FVG and OB prefixed alerts (side="analyst"), store in ALERT_HISTORY, surface in Analyst Reasoning Engine as evidence |
| **Inputs** | Pine fvg_ob.pine webhooks |
| **Outputs** | FVG/OB entries in ALERT_HISTORY, analyst evidence block |
| **Dependencies** | ALERT_HISTORY, Analyst Reasoning Engine |
| **Status** | ✅ Complete |
| **Dashboard** | Analyst evidence panel |
| **Production Impact** | Display only — never touches gate or sizing |
| **Files** | `app.py` (FVG/OB ingestion, analyst engine) |

---

### 11. Databento Live Feed

| Field | Detail |
|---|---|
| **Purpose** | High-fidelity real-time market data feed for bar-close scanning and precise ATR |
| **Responsibilities** | DatabentoBrain subscribes to live CME futures feeds, triggers `_databento_bar_scan` on non-duplicate signals, injects data into VWAP/ATR stores, provides `get_databento_status()` |
| **Inputs** | `DATABENTO_ENABLED=1`, `DATABENTO_API_KEY` secret |
| **Outputs** | OHLCV bars, ATR updates, bar-scan triggers; `/databento-status` route |
| **Dependencies** | `databento` pip package, `DATABENTO_ENABLED` flag |
| **Status** | ✅ Complete (flag-gated; requires API key in production) |
| **Dashboard** | OFFLINE/ONLINE status panel |
| **Production Impact** | Enhances ATR accuracy when enabled; graceful degradation when off |
| **Files** | `app.py` (DatabentoBrain class, `_databento_structure_trigger`) |

---

### 12. Left Brain Market Intelligence (MI)

| Field | Detail |
|---|---|
| **Purpose** | Macro market state reasoning — direction, strength, momentum, and what the market is "doing" right now |
| **Responsibilities** | `compute_left_brain_market_intelligence()` reads ALERT_HISTORY + VWAP + CVD, produces `direction`/`strength`/`momentum`/`supporting_evidence`/`confidence`; MI adaptive strategy filter (demote-only veto when setup fights unambiguous state); MI confidence-as-structure fallback (SCALP only, flag-gated) |
| **Inputs** | ALERT_HISTORY, VWAP stores, CVD state, price |
| **Outputs** | MI block in result; SCALP veto; structure fallback; `/lb-vwap-authority`, `/lb-shadow-report` routes |
| **Dependencies** | `left_brain_market_intelligence.py` |
| **Status** | ✅ Complete (display + SCALP money-path veto) |
| **Dashboard** | Left Brain Intelligence panel |
| **Production Impact** | MI adaptive filter is a demote-only SCALP veto (default ON) |
| **Files** | `left_brain_market_intelligence.py`, `app.py` (MI integration) |

---

### 13. Left Brain Thesis Engine

| Field | Detail |
|---|---|
| **Purpose** | Dynamic market thesis — a persistent narrative about what the market is doing, where it is going, and when it will be invalidated |
| **Responsibilities** | `compute_left_brain_thesis()` produces direction/strength/momentum/narrative/invalidation/playbook-reasoning/timeline; persistent thesis with confidence hysteresis (reversal flip requires `prev=None` reset); OUTLOOK_SHIFT event detection; 25-trade learning report |
| **Inputs** | MI output, ALERT_HISTORY, `_LB_MARKET_MEMORY_BY_INST` (maxlen=200) |
| **Outputs** | Thesis block; `/lb-thesis` route; Discord thesis embed (Discord journal channel) |
| **Dependencies** | `left_brain_market_intelligence.py`, `compute_left_brain_thesis()`, `_LB_THESIS_BY_INST` store |
| **Status** | ✅ Complete |
| **Dashboard** | LB Thesis panel with direction/narrative/invalidation/timeline |
| **Production Impact** | Display only; thesis enforcement (Phase 3) can demote |
| **Files** | `left_brain_market_intelligence.py`, `app.py` |

---

### 14. Left Brain Observation Infrastructure

| Field | Detail |
|---|---|
| **Purpose** | Persistent observation buffer for thesis evolution — auditable time-series of MI/thesis snapshots |
| **Responsibilities** | `_LB_THESIS_OBS_BY_INST` deque (maxlen=5000), per-instrument dedup (minute-precision), `top_playbook_fit_score`/`vwap_age_ms`/`mi_input_ts` fields; `/lb-thesis-obs` endpoint with `?inst`, `?limit`, `?summary`, retention metadata |
| **Inputs** | Every thesis compute cycle |
| **Outputs** | Queryable obs buffer; summary distribution stats |
| **Dependencies** | `compute_left_brain_thesis()`, obs dedup key `_LB_THESIS_OBS_LAST_BAR` |
| **Status** | ✅ Complete (Phase 2, July 2026) |
| **Dashboard** | Not yet surfaced on dashboard (backend-only) |
| **Production Impact** | Display only; needs re-publish to reach production |
| **Files** | `app.py` (obs buffer, `/lb-thesis-obs` route) |

---

### 15. Strict Gate / Decision Engine

| Field | Detail |
|---|---|
| **Purpose** | The authoritative READY/WAIT verdict — the single gate that decides whether a setup is actionable |
| **Responsibilities** | `evaluate_strict_setup()` enforces zone + VWAP + structure gates (mode-tunable: SWING requires all three at 80-pt floor; SCALP demotes zone-only); produces `gate_debug` per-gate PASS/FAIL; `strict_reason` for every WAIT; ticker-authoritative resolution |
| **Inputs** | ALERT_HISTORY, VWAP, zone state, structure state, price |
| **Outputs** | `is_actionable`, `verdict`, `strict_reason`, `gate_debug` |
| **Dependencies** | All ingestion subsystems |
| **Status** | ✅ Complete |
| **Dashboard** | Gate diagnostics panel (owner-only `/diagnostics`) |
| **Production Impact** | Primary money-path gate |
| **Files** | `app.py` (`evaluate_strict_setup`) |

---

### 16. Edge Score Engine

| Field | Detail |
|---|---|
| **Purpose** | Transparent 0–110 scoring of setup quality independent of the binary gate |
| **Responsibilities** | EDGE_COMPONENTS: BOS20/CHOCH20/VWAP15/Sweep15/Volume15/CVD15/Session10; grade ≥85=A+/≥70=A/≥50=B/<50=WAIT; Learning influence ±15 (flag-gated); Entry Quality location engine override; session bonus clock-pinned for testing |
| **Inputs** | ALERT_HISTORY, gate components, CVD, RVOL, session time |
| **Outputs** | `edge_score`, `grade`, `edge_breakdown`, `alert_level` |
| **Dependencies** | `_analysis_edge_breakdown()`, EDGE_COMPONENTS |
| **Status** | ✅ Complete |
| **Dashboard** | Edge Score gauge, grade badge |
| **Production Impact** | Score drives conviction tier, sizing, card display |
| **Files** | `app.py` (edge helper, EDGE_COMPONENTS) |

---

### 17. Multi-Strategy Engine

| Field | Detail |
|---|---|
| **Purpose** | Match current market conditions to named trading strategies and select the best-fit regime-appropriate approach |
| **Responsibilities** | 29 strategy definitions across 5 scorers; regime→strategy fixed priority; ORB (9:30 opening range breakout), Exhaustion Fade replaced by ORB in live engine; display mode (show candidates) vs control mode (run global safety); OPENING_DRIVE eligibility gate |
| **Inputs** | full_analysis result, market regime, session time |
| **Outputs** | `strategy_scan` block, `active_strategy`, R:R retarget for ORB (1:4) |
| **Dependencies** | `STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER` |
| **Status** | ✅ Complete |
| **Dashboard** | Strategy scan panel |
| **Production Impact** | ORB triggers 1:4 R:R retarget (money path) |
| **Files** | `app.py` (strategy engine section) |

---

### 18. Breakout Mode (ORB)

| Field | Detail |
|---|---|
| **Purpose** | 9:30 ET opening range breakout advisory on a dedicated engine |
| **Responsibilities** | `compute_breakout_mode()` computes OR from 09:30 candle; flag default OFF; closed-override key parity; Phase-D auto-execute NOT yet built |
| **Inputs** | 09:30 ET candle (OHLCV), price, session |
| **Outputs** | `breakout_mode` block in result (None when flag OFF) |
| **Dependencies** | Market session, OHLCV data |
| **Status** | ⚡ Partially built (advisory only; auto-execute Phase-D not built) |
| **Dashboard** | Breakout mode panel (flag-gated) |
| **Production Impact** | Display only when flag ON; gate untouched |
| **Files** | `app.py` (breakout_mode section) |

---

### 19. SWING HTF Data Layer

| Field | Detail |
|---|---|
| **Purpose** | Higher timeframe (1H/4H/Daily) bias and level computation for SWING mode |
| **Responsibilities** | `_swing_htf_enabled()` master gate; auto-computed 1H/4H (resampled)/Daily bias+levels in `HTF_STATE_BY_INST`; folded into VWAP loop; `compute_swing_context()` stable schema fail-OPEN; SWING EMA/RSI/MACD/ADX via Pine SWING_EMA_UPDATE webhook |
| **Inputs** | Pine SWING_EMA_UPDATE webhook, OHLCV from Databento/auto-fetch |
| **Outputs** | `swing_ctx`, `htf_atr`, `htf_summary`, HTF bias labels |
| **Dependencies** | `SWING_MODE_V2_ENABLED`, yfinance, Databento |
| **Status** | ✅ Complete |
| **Dashboard** | HTF summary in analyst panel |
| **Production Impact** | SWING ATR and structure analysis |
| **Files** | `app.py` (HTF loop, `compute_swing_context`) |

---

### 20. Swing Mode V2 Engine

| Field | Detail |
|---|---|
| **Purpose** | 9-category 0–100 HTF swing scorer with SCANNING→READY lifecycle and multi-target plan |
| **Responsibilities** | Tier-1 HTF data + Tier-2 Pine EMA/RSI/MACD/ADX; entry/stop/3-target plan; `/swing-analysis` route; `/status` key whitelisted |
| **Inputs** | HTF state, Pine EMA data |
| **Outputs** | `swing_v2` block, setup lifecycle, entry plan |
| **Dependencies** | `SWING_MODE_V2_ENABLED` flag |
| **Status** | ⚡ Flag-gated, default OFF in production |
| **Dashboard** | Swing analysis panel |
| **Production Impact** | Gate byte-identical when OFF |
| **Files** | `app.py` (Swing V2 section) |

---

### 21. Analyst Reasoning Engine

| Field | Detail |
|---|---|
| **Purpose** | Professional-grade pre-READY grader that adds market phase analysis, game plan, and demote-only veto |
| **Responsibilities** | Per-instrument SCALP/SWING models + prime windows; `Market-Phase VWAP-ext ATR` using `_analyst_phase_atr`; FVG/OB evidence; game plan (probabilistic plan/scenarios/R:R/conclusion); veto only DEMOTES actionable→WAIT (default display-on, veto default-on); FVG/OB short-circuit in /webhook |
| **Inputs** | full_analysis assembled result, ALERT_HISTORY, ATR, VWAP |
| **Outputs** | `analyst` block including risk/reward assessment, game_plan, veto flag |
| **Dependencies** | `pro_review_layer.py` or inline, ATR mode-correct |
| **Status** | ✅ Complete |
| **Dashboard** | Analyst panel with risk/entry/game plan |
| **Production Impact** | Demote-only veto on money path |
| **Files** | `app.py` (analyst engine, `compute_analyst_review`) |

---

### 22. Trade Debate Engine (Bull/Bear/Judge)

| Field | Detail |
|---|---|
| **Purpose** | Structured devil's advocate — Bull analyst vs Bear analyst, arbitrated by a Judge |
| **Responsibilities** | Mirrors pro_review (full_analysis-level); `final_verdict` = TAKE iff actionable + decisive + aligned; veto only DEMOTES; display default ON, veto default OFF; smoke-guarded |
| **Inputs** | full_analysis result, analyst output |
| **Outputs** | `trade_debate` block with bull_case/bear_case/judge/final_verdict |
| **Dependencies** | `DEBATE_ENABLED` flag |
| **Status** | ✅ Complete (veto default OFF) |
| **Dashboard** | Trade debate panel |
| **Production Impact** | Demote-only when veto enabled |
| **Files** | `app.py` (debate engine section) |

---

### 23. Main Brain Cognitive Layer

| Field | Detail |
|---|---|
| **Purpose** | Top-level cognitive orchestration — synthesizes all analyst/debate/governor/memory inputs into a unified verdict and voice |
| **Responsibilities** | 3-layer rule: `_mb_orchestrate` + `_mb_learning_snapshot` + `compute_main_brain`; 7 display-only keys at full_analysis seam; `main_brain_voice` narration; Brain Contract JS rendering (10 functions); Apple×OpenAI UI design; Brain Conflict Resolver (10-priority display engine); Verdict Board (4-bucket plain-English) |
| **Inputs** | All analyst layers, learning snapshot, market context |
| **Outputs** | `main_brain` block, conflict_resolver, verdict_board, MB voice narration |
| **Dependencies** | All upstream subsystems |
| **Status** | ✅ Complete |
| **Dashboard** | Main Brain hero + orb-halo + 2x2 intel grid + judge panel |
| **Production Impact** | Display only — cognitive seam never gates |
| **Files** | `app.py` (MB orchestrate, `compute_main_brain`) |

---

### 24. Avatar Intelligence Engine

| Field | Detail |
|---|---|
| **Purpose** | Proactive trading partner that observes the session, greets the operator, and explains setups in plain language |
| **Responsibilities** | Proactive event queue; daily greeting; explain-simply mode; `mbAvatarObserve(d)` hook at end of renderModules; `mbMemory` placeholder |
| **Inputs** | Dashboard render cycle, market events |
| **Outputs** | Avatar speech bubble, event queue display |
| **Dependencies** | Main Brain cognitive layer, dashboard JS |
| **Status** | ⚡ Partially built (basic observer; deep memory not wired) |
| **Dashboard** | VRM Avatar panel (home artifact has LordPiggington.vrm) |
| **Production Impact** | Display only |
| **Files** | `app.py` (avatar JS section), `artifacts/home/src/VRMAvatar.tsx` |

---

### 25. Decision Pipeline V2 (Shadow)

| Field | Detail |
|---|---|
| **Purpose** | Shadow 5-stage cognitive pipeline (OBSERVE→INTERPRET→PRIORITIZE→VALIDATE→DECIDE) running in parallel with the live decision path |
| **Responsibilities** | All `CAN_*` flags default OFF; flag-OFF byte-identical; stages logged but never gate; future phases flip flags one-at-a-time |
| **Inputs** | full_analysis result per instrument |
| **Outputs** | `dpv2_shadow` log line per cycle |
| **Dependencies** | `DECISION_PIPELINE_V2_ENABLED` |
| **Status** | ⚡ Shadow only — no live flags enabled yet |
| **Dashboard** | Not surfaced |
| **Production Impact** | Zero — byte-identical |
| **Files** | `app.py` (DPv2 section) |

---

### 26. Adaptive Learning Engine

| Field | Detail |
|---|---|
| **Purpose** | Postgres-backed per-strategy performance analytics that adjusts edge scoring weights over time |
| **Responsibilities** | Per-strategy win rates, best-hours buckets, `strategy_weights` table; bounds 0.65–1.35; never disables a strategy; recompute serialized by mutex; persist after outcome card; learning influence ±15 on edge score (flag-gated); Learning Rule Engine (GHOST_ONLY/LIVE_ELIGIBLE gate) |
| **Inputs** | Trade outcomes written to `strategy_trades` table |
| **Outputs** | `learning` block, edge score ±15 modifier, `learning_rule_engine` eligibility |
| **Dependencies** | PostgreSQL, `LEARNING_ENABLED`, `LEARNING_SCORE_INFLUENCE` |
| **Status** | ✅ Complete |
| **Dashboard** | Learning panel (governor, memory, report) |
| **Production Impact** | Edge score modifier (money path, flag-gated) |
| **Files** | `app.py` (learning engine section) |

---

### 27. Unified Learning Brain

| Field | Detail |
|---|---|
| **Purpose** | Global per-mode performance aggregates feeding playbook selector and cognitive synthesis |
| **Responsibilities** | `PER_MODE_STATS` (inst, mode key); `compute_playbook_selector`; `compute_unified_learning`; display-only cognitive seam; SWING/SCALP/MICRO_SCALP namespace isolation via `_ns_learning_key` |
| **Inputs** | Per-strategy analytics, trade outcomes |
| **Outputs** | `unified_learning` block, playbook recommendations |
| **Dependencies** | Adaptive Learning Engine, DB |
| **Status** | ✅ Complete (display only) |
| **Dashboard** | Unified learning panel |
| **Production Impact** | Display only |
| **Files** | `app.py` (unified learning section) |

---

### 28. Shared Trade Memory Engine

| Field | Detail |
|---|---|
| **Purpose** | Find similar historical trades to current setup and use them to influence analyst reasoning |
| **Responsibilities** | `find_similar_trades()` is the ONE similar-history source; 4-lens governor with capped nudges + non-domination floor; recency×version weights; TradeZella integration (down-weighted `source:"tradezella"`) |
| **Inputs** | `strategy_trades` table, TradeZella import |
| **Outputs** | `trade_memory` block, `governor` nudges |
| **Dependencies** | PostgreSQL, TradeZella import (optional) |
| **Status** | ✅ Complete |
| **Dashboard** | Memory review panel |
| **Production Impact** | Governor nudges are display-only; memory never gates alone |
| **Files** | `app.py` (trade memory section) |

---

### 29. Thesis Tracker

| Field | Detail |
|---|---|
| **Purpose** | Outcome-based analyst memory — snapshot setups, resolve 25–75 minutes later, build pattern memory |
| **Responsibilities** | `thesis_snapshots` table; 25–75 min resolve; lesson + reflection; pattern memory SQL (≥3 samples); `_mb_capture_cognitive` heartbeat hook |
| **Inputs** | Setup snapshots at READY, trade outcomes |
| **Outputs** | Thesis pattern library, reflections in DB |
| **Dependencies** | PostgreSQL, `thesis_snapshots` table |
| **Status** | ⚡ Partially implemented (snapshot + resolve built; UI integration limited) |
| **Dashboard** | Thesis history modal |
| **Production Impact** | Display only |
| **Files** | `app.py` (thesis tracker section) |

---

### 30. Trade Failure Analyzer

| Field | Detail |
|---|---|
| **Purpose** | Root-cause analysis of losing trades |
| **Responsibilities** | `TFA_DB_READY`-gated READY→trigger→outcome recorder; 8 analysis functions; `/failure-analysis` route |
| **Inputs** | Trade outcomes, ALERT_HISTORY snapshots |
| **Outputs** | Failure patterns, root cause classifications |
| **Dependencies** | PostgreSQL, `TFA_DB_READY` flag |
| **Status** | ⚡ Partially built (recording done; analysis display limited) |
| **Dashboard** | Failure analysis panel (owner-only) |
| **Production Impact** | Display only |
| **Files** | `app.py` (failure analyzer) |

---

### 31. Decision Quality Analytics

| Field | Detail |
|---|---|
| **Purpose** | DB-backed snapshot analytics measuring decision process quality independent of outcome |
| **Responsibilities** | DB-backed snapshot analytics; dedup pattern; component win-rate computation; outcome hook location |
| **Inputs** | Trade decisions, outcomes |
| **Outputs** | Quality metrics in DB |
| **Dependencies** | PostgreSQL, `decision_snapshots` table |
| **Status** | ⚡ Backend built; dashboard display limited |
| **Dashboard** | Not prominently surfaced |
| **Production Impact** | Display only |
| **Files** | `app.py` (decision quality section) |

---

### 32. Right Brain Trade Management

| Field | Detail |
|---|---|
| **Purpose** | Post-entry intelligent management advisor — when to trail, when to take profit, when to exit early |
| **Responsibilities** | Phase 6B.2 shadow advisory; `_right_brain_orchestrate()` is sole full_analysis seam; 47 tests (TM001-TM047); `RBTM_VALID_RECOMMENDATIONS` frozenset; near-stop CRITICAL not HIGH; `/right-brain-status` route |
| **Inputs** | Active trade state, price, ATR, P&L |
| **Outputs** | `right_brain` advisory block with recommendation and urgency |
| **Dependencies** | `RBTM_ENABLED` flag, active trade |
| **Status** | ⚡ Shadow advisory only (flag default OFF in dev; env=1 set for production pending deploy) |
| **Dashboard** | Right Brain advisory panel |
| **Production Impact** | Display only at current stage |
| **Files** | `app.py` (RBTM section) |

---

### 33. Execution Gateway

| Field | Detail |
|---|---|
| **Purpose** | Single configurable gateway for all live broker order sends |
| **Responsibilities** | `EXECUTION_MODE` env (manual_only/paper/traderspost/pickmytrade); canonical intent → per-provider adapters; TradersPost payload byte-equivalent; paper/manual never send/dedupe; all fail-closed money invariants; broker payload pre-send guard (audit log + required-field check); opposite-side reversal buffer (spacing delay) |
| **Inputs** | Trade signal from full_analysis, auto-trade arm state |
| **Outputs** | Broker order (or paper/manual log) |
| **Dependencies** | `TRADERSPOST_WEBHOOK_URL`, `EXECUTION_MODE`, `AUTO_TRADE_ENABLED` |
| **Status** | ✅ Complete |
| **Dashboard** | Execution status, gateway mode indicator |
| **Production Impact** | Live money path |
| **Files** | `app.py` (execution gateway section, `/traderspost`) |

---

### 34. Auto-Trade Arming System

| Field | Detail |
|---|---|
| **Purpose** | Per-instrument arm/disarm for autonomous trade execution |
| **Responsibilities** | In-memory arm state (resets on restart intentionally for safety); `/auto-trade` settings; SCALP auto-fires on LIVE is_actionable including EARLY tier (half-size); STOP_HIT re-arms (WIN does NOT); daily cap; auto-trade advisor review gate (opt-in) |
| **Inputs** | Operator arm action, trade outcomes, `AUTO_TRADE_ENABLED` |
| **Outputs** | Automatic order sends when armed + conditions met |
| **Dependencies** | Execution gateway, `AUTO_FIRED_KEYS` dedup |
| **Status** | ✅ Complete |
| **Dashboard** | Auto-trade arm/disarm buttons per instrument |
| **Production Impact** | Live money path |
| **Files** | `app.py` (auto-trade arming, `_check_auto_trade`) |

---

### 35. SCALP Dynamic Exits

| Field | Detail |
|---|---|
| **Purpose** | Multi-target SCALP exit management (TP1/TP2/runner with delayed BE) |
| **Responsibilities** | Replaces simple 1:1 exit; `MANAGED_TRADES_BY_KEY` paper-dynamic tracking; one shared geometry helper; ORB nulls nested keys; delayed BE on runner; paper watcher same-bar fill guard |
| **Inputs** | Active trade, price, ATR |
| **Outputs** | TP levels, BE move trigger, paper tracking |
| **Dependencies** | `SCALP_DYNAMIC_EXITS_ENABLED` |
| **Status** | ✅ Complete |
| **Dashboard** | Trade management panel |
| **Production Impact** | Live SCALP exit management |
| **Files** | `app.py` (managed trades, SCALP exits) |

---

### 36. Live 2-Contract Runner

| Field | Detail |
|---|---|
| **Purpose** | Two-contract entry with primary TP at 1R and a runner at 2R with trailing stop |
| **Responsibilities** | Flag-gated default OFF; arming safe by construction; `RUNNER_MODE` trail/be_2r; primary TP forced to 1R; runner = broker 2R + synthetic BE |
| **Inputs** | Active trade, RUNNER_MODE |
| **Outputs** | Second contract management |
| **Dependencies** | `LIVE_RUNNER_ENABLED` |
| **Status** | ⚡ Flag-gated, default OFF |
| **Dashboard** | Runner status in trade panel |
| **Production Impact** | Live money path when enabled |
| **Files** | `app.py` (live runner section) |

---

### 37. Manual Trade Manager

| Field | Detail |
|---|---|
| **Purpose** | Advisory overlay for hand-entered positions (not bot-initiated) |
| **Responsibilities** | Advisory-only (no broker exit path); thesis INVALID only on stop-breach/confirmed-opposite-trend; market-closed PAUSES not invalidates; INSERT/SELECT fail-open persistence; reject non-integral contracts; mirrors bot's open positions (display-only) |
| **Inputs** | Operator-entered trade details |
| **Outputs** | Management advisory, thesis validity, suggested actions |
| **Dependencies** | `manual_trades` table, `MANUAL_TRADES_LOCK` |
| **Status** | ✅ Complete |
| **Dashboard** | Manual Trade Manager panel |
| **Production Impact** | Display + tracking only |
| **Files** | `app.py` (manual trade manager) |

---

### 38. Manual Desk Order

| Field | Detail |
|---|---|
| **Purpose** | Operator override — fire a real order regardless of gate/setup via discretionary judgment |
| **Responsibilities** | Flag-gated (MANUAL_ORDER_ENABLED default OFF); server-built ATR bracket; fail-closed; single-slot; owner-only |
| **Inputs** | Manual operator POST with instrument/direction |
| **Outputs** | Live broker order |
| **Dependencies** | Execution gateway, `MANUAL_ORDER_ENABLED` |
| **Status** | ⚡ Flag-gated, default OFF |
| **Dashboard** | Manual order button (owner-only) |
| **Production Impact** | Live money path when enabled |
| **Files** | `app.py` (manual desk order) |

---

### 39. Bot Training Mode

| Field | Detail |
|---|---|
| **Purpose** | Staged learning system for configuring the bot safely before it goes fully live |
| **Responsibilities** | 4-stage fail-closed gate; stages 1–3 suggest-only; stage ≥4 passthrough; boot probe is `__main__`-only (lazy probe); flag-OFF byte-identical; suppresses live auto-trades at stage < 4 |
| **Inputs** | `TRAINING_MODE_ENABLED`, `TRAINING_BOOT_STAGE`, `bot_training_state` table |
| **Outputs** | `training_gate` result, suppressed/passed auto-trade |
| **Dependencies** | `BOT_TRAINING_MODE` env, DB table |
| **Status** | ✅ Complete |
| **Dashboard** | Training mode status indicator |
| **Production Impact** | Suppresses all live sends at stage < 4 |
| **Files** | `app.py` (training mode section) |

---

### 40. Prop Firm Protection Guard

| Field | Detail |
|---|---|
| **Purpose** | Hard stop on daily loss limits to protect funded account rules |
| **Responsibilities** | Optional owner-only guard; OFF byte-identical; final gateway layer; live-only 409; fail-closed default-BLOCK; `PROP_LOCK` never under money locks; `prop_accounts` table; maxLossesPerDay=5 |
| **Inputs** | Per-asset safety config, daily P&L |
| **Outputs** | Block/allow trade execution |
| **Dependencies** | `PROP_PROTECTION_ENABLED`, `prop_accounts`, `safety_overrides` tables |
| **Status** | ✅ Complete (optional, default OFF) |
| **Dashboard** | Prop protection status |
| **Production Impact** | Hard block on money path when enabled |
| **Files** | `app.py` (prop guard section) |

---

### 41. Per-Asset Safety Controls

| Field | Detail |
|---|---|
| **Purpose** | Runtime DB-backed overrides for per-instrument risk parameters |
| **Responsibilities** | `safety_cfg` resolves RUNTIME DB→registry→defaults; fail-closed money path; lock-free readers; full-replace POST can clear kill switch; maxLossesPerDay=5 (wins uncounted) |
| **Inputs** | `safety_overrides` DB table, operator POST |
| **Outputs** | Per-instrument kill switch, position limits |
| **Dependencies** | PostgreSQL, `safety_overrides` |
| **Status** | ✅ Complete |
| **Dashboard** | Safety controls panel |
| **Production Impact** | Live kill switch on money path |
| **Files** | `app.py` (safety config section) |

---

### 42. Dual-TF Engine

| Field | Detail |
|---|---|
| **Purpose** | 1m bias + 5s CONVERGENCE for high-precision SCALP entries |
| **Responsibilities** | Flag-gated (DUAL_TF_ENGINE); READY = standing bias + ≥2 distinct aligned confirms (CVD/sweep/volume ONLY, not VWAP/DELTA) within 10s; dual-sim accepts "databento_scan"; `/clear-fired-keys` endpoint |
| **Inputs** | 5s Pine execution alerts (ENTRY_TRIGGER, BULLISH_SWEEP, etc.) |
| **Outputs** | `dual_tf_ready` convergence signal |
| **Dependencies** | `DUAL_TF_ENGINE` flag, `TRADING_MODE=SCALP` |
| **Status** | ⚡ Flag-gated, default OFF |
| **Dashboard** | Dual-TF convergence panel |
| **Production Impact** | Zero when flag OFF (byte-identical) |
| **Files** | `app.py` (dual-TF engine) |

---

### 43. Fast Entry Trigger

| Field | Detail |
|---|---|
| **Purpose** | 1s/5s timing overlay that sharpens entry on already-valid HTF setups |
| **Responsibilities** | Two flags (FAST_ENTRY_TRIGGER + FAST_ENTRY_MONEY); SWEEP_RECLAIM + MICRO_CHOCH always bridged to ALERT_HISTORY (structure bridge); fast-entry state in `FAST_ENTRY_STATE_BY_TICKER`; shares legacy FULL-READY auto fire-once key (no double-enter); `_FE_BRIDGE_LAST` dedup |
| **Inputs** | Pine seconds-level alerts (SWEEP_RECLAIM, DELTA_FLIP, MICRO_CHOCH, MICRO_VWAP) |
| **Outputs** | LH/HL/CHOCH injected into ALERT_HISTORY; fast-entry state for timing |
| **Dependencies** | `FAST_ENTRY_TRIGGER`, `FAST_ENTRY_MONEY` flags |
| **Status** | ✅ Complete (bridge always active; money path flag-gated) |
| **Dashboard** | Fast entry timing panel |
| **Production Impact** | Bridge active unconditionally; money path requires both flags |
| **Files** | `app.py` (fast-entry sections, `_fast_entry_record`, bridge at ~42600) |

---

### 44. Micro Scalp Mode

| Field | Detail |
|---|---|
| **Purpose** | Ultra-short-duration sweep→trap→trigger engine with ghost ledger |
| **Responsibilities** | Ghost ledger always running; separate restart-resetting LIVE arm via shared gateway; SINGLE EXIT (target1==target2); `MICRO_SCALP_ENABLED`; `MICRO_EVENTS_BY_INST` / `MICRO_TRAIL_BY_INST` stores |
| **Inputs** | Micro-scalp signal types, price tick stream |
| **Outputs** | Ghost trades logged, live arm fires real orders |
| **Dependencies** | `MICRO_SCALP_ENABLED`, execution gateway |
| **Status** | ⚡ Flag-gated, default OFF |
| **Dashboard** | Micro scalp ghost ledger panel |
| **Production Impact** | Zero when OFF; live money path when armed |
| **Files** | `app.py` (micro scalp section) |

---

### 45. Scalp Research Engine

| Field | Detail |
|---|---|
| **Purpose** | Research lab for 16 scalp strategies — live paper-simulated and scored but never in the money path |
| **Responsibilities** | 16 strategy detectors in separate registry; `live_status` ∈ {watch, sim, recommended}; live paper-sim on live stream; GET never recomputes; owner-only; walled off from money path |
| **Inputs** | Live webhook stream (observer), ALERT_HISTORY |
| **Outputs** | Strategy rankings, sim P&L, advisor candidates |
| **Dependencies** | `SCALP_RESEARCH_ENABLED`, `scalp_strategy_research` table |
| **Status** | ✅ Complete |
| **Dashboard** | Scalp research panel (owner-only) |
| **Production Impact** | Display only — never gates |
| **Files** | `app.py` (scalp research section), `scalp_live_sim.py` |

---

### 46. Scalp Strategy Advisory

| Field | Detail |
|---|---|
| **Purpose** | DISPLAY-ONLY Main-Brain layer showing all 16 research strategies as ranked candidates with a full 16-vote reasoning roster |
| **Responsibilities** | Votes from `scalp_live_sim.diagnose_strategies`; NEVER money path; flag-OFF byte-identical |
| **Inputs** | Live sim output, market context |
| **Outputs** | `scalp_advisory` block with ranked candidates |
| **Dependencies** | Scalp Research Engine, `scalp_live_sim` |
| **Status** | ✅ Complete (display only) |
| **Dashboard** | Strategy advisory in Main Brain |
| **Production Impact** | Display only |
| **Files** | `app.py` (scalp advisory section) |

---

### 47. Backtest Engine

| Field | Detail |
|---|---|
| **Purpose** | Offline strategy research using historical OHLCV data |
| **Responsibilities** | CSV upload, run_backtest(candles, params_dict), optimize sweep, coverage report; walled off from money path; "INSERT/SELECT only" for own tables; owner-only auth; worst-case fills; R:R mirrors live EDGE_COMPONENTS |
| **Inputs** | OHLCV CSV (MGC/MNQ/MES/MYM), strategy params |
| **Outputs** | Backtest results, optimization matrix, coverage report |
| **Dependencies** | `backtest_engine.py`, `backtest_datasets`/`backtest_candles` tables |
| **Status** | ✅ Complete |
| **Dashboard** | Backtest upload/results panel (owner-only) |
| **Production Impact** | Zero — fully walled off |
| **Files** | `backtest_engine.py`, `app.py` (`/backtest/*` routes) |

---

### 48. Baseline Engine

| Field | Detail |
|---|---|
| **Purpose** | Establish performance baselines across strategy×management parameter combinations |
| **Responsibilities** | `bt_baseline.py`; `_jdump` handles frozenset; `baseline_trades` table; first baseline BL-20260726; detail returns `matrix_results` key |
| **Inputs** | Historical candles, strategy configs |
| **Outputs** | Performance baseline records |
| **Dependencies** | `bt_baseline.py`, PostgreSQL |
| **Status** | ✅ Complete (Phase 6B.1) |
| **Dashboard** | Baseline comparison in backtest panel |
| **Production Impact** | Research only |
| **Files** | `bt_baseline.py`, `app.py` |

---

### 49. Journal System

| Field | Detail |
|---|---|
| **Purpose** | Persistent trade record and Discord notification system |
| **Responsibilities** | `_build_card_entry` is the single source for journal+card; trade-taken bell audio (data URI); screenshots passed to Discord (never fetched); READY live card fires once per setup + re-posts every TRADE_READY_INTERVAL; per-instrument throttle; trade card in main channel + journal in journal channel |
| **Inputs** | Trade events (open, close, READY alert) |
| **Outputs** | Discord embeds, DB records in `strategy_trades` |
| **Dependencies** | Discord webhooks, PostgreSQL |
| **Status** | ✅ Complete |
| **Dashboard** | Journal/trade log panel, Today's Trades |
| **Production Impact** | DISCORD_LIVE_ENABLED gates sends in dev |
| **Files** | `app.py` (journal, `_build_card_entry`) |

---

### 50. Trade Idea Review

| Field | Detail |
|---|---|
| **Purpose** | Let the operator grade a manually-typed hypothetical trade against the same engines the bot uses |
| **Responsibilities** | Owner-only; display only; REUSES read-only engines; manual ticket only (NEVER money path); RR25/market30/entry25/memory10/conviction10 scoring |
| **Inputs** | Manually typed trade description |
| **Outputs** | Graded review with score breakdown |
| **Dependencies** | All analysis engines (read-only) |
| **Status** | ✅ Complete |
| **Dashboard** | Trade Review panel (owner-only) |
| **Production Impact** | Display only |
| **Files** | `app.py` (`/review-idea` route) |

---

### 51. Market State Cache

| Field | Detail |
|---|---|
| **Purpose** | Persist critical in-memory state across VM restarts to prevent cold-start blindness |
| **Responsibilities** | `market_state_cache` table (PK key, JSONB data, schema_version, updated_at); persists CVD/vol-spike/TradersPost-dedup/AUTO_FIRED_KEYS/ALERT_HISTORY across restarts; freshness windows guard each restore; READY state intentionally NOT restored |
| **Inputs** | In-memory state stores |
| **Outputs** | Restored state on boot |
| **Dependencies** | PostgreSQL, `market_state_cache` table |
| **Status** | ✅ Complete |
| **Dashboard** | Boot diagnostics |
| **Production Impact** | Prevents cold-start from zeroing all history |
| **Files** | `app.py` (cache persistence section) |

---

### 52. Active Trade Persistence

| Field | Detail |
|---|---|
| **Purpose** | Survive VM restarts with open trade knowledge intact |
| **Responsibilities** | `ACTIVE_TRADES_BY_INST` write-through to `open_trades` (Postgres); set/clear call `_persist_active_trade` OUTSIDE the lock; boot restores INERT; SWING thesis uses separate `swing_theses` table |
| **Inputs** | Active trade open/close events |
| **Outputs** | Restored trade state on boot |
| **Dependencies** | `open_trades`, `swing_theses` tables |
| **Status** | ✅ Complete |
| **Dashboard** | Active trade panel |
| **Production Impact** | Boot restoration prevents phantom positions |
| **Files** | `app.py` (active trade persistence) |

---

### 53. Database Layer

| Field | Detail |
|---|---|
| **Purpose** | Persistent storage for all trade, analytics, config, and research data |
| **Responsibilities** | PostgreSQL via `psycopg2`; ~40 tables; app runs NO DDL (INSERT/SELECT only); boot readiness probe + `*_DB_READY` flags; schema managed via Replit database tool + publish schema-diff |
| **Inputs** | All subsystems writing records |
| **Outputs** | Persistent records, analytics queries |
| **Dependencies** | `POSTGRES_URL`, Replit PostgreSQL service |
| **Status** | ✅ Complete |
| **Dashboard** | Indirectly (all data-driven panels) |
| **Production Impact** | Foundation of all analytics and persistence |
| **Files** | `app.py` (DB init, all write paths) |

---

### 54. Express API Server

| Field | Detail |
|---|---|
| **Purpose** | Production-hardened HTTP gateway between the internet and the Flask bots |
| **Responsibilities** | HTTP Basic Auth + CSRF protection (`dashboardAuth`); raw body forwarding (TradingView text/plain webhooks); proxy whitelist for all Flask routes; HMAC view-only share links; `/api/healthz` health probe; `/api2` mount for analysis bot |
| **Inputs** | All HTTP traffic |
| **Outputs** | Authenticated + proxied requests to Flask |
| **Dependencies** | Node.js, Express, `artifact.toml` production config |
| **Status** | ✅ Complete |
| **Dashboard** | Auth gateway for all dashboard requests |
| **Production Impact** | Critical infrastructure — all requests flow through here |
| **Files** | `artifacts/api-server/src/` |

---

### 55. React Dashboard (home artifact)

| Field | Detail |
|---|---|
| **Purpose** | Visual operator interface for monitoring and controlling the trading system |
| **Responsibilities** | 5-section live nav (pure-JS show/hide); Cockpit.tsx (main dashboard); Sentinel.tsx (monitoring); VRMAvatar.tsx (3D avatar); 3s polling of `/status`; per-device localStorage for panel collapse/drag/theme; glass vs retro theme |
| **Inputs** | `/status` API polling, operator interactions |
| **Outputs** | Visual display, operator commands via `/api` |
| **Dependencies** | Vite + React + Tailwind + shadcn/ui + @tanstack/react-query |
| **Status** | ✅ Complete |
| **Dashboard** | IS the dashboard |
| **Production Impact** | Operator interface |
| **Files** | `artifacts/home/src/` |

---

### 56. AI Assistant Chat

| Field | Detail |
|---|---|
| **Purpose** | Read-only Q&A about the current market context, grounded on a live full_analysis snapshot |
| **Responsibilities** | Owner-only; `/assistant` route; never touches money path; stays out of OPEN_PATHS; `aiEsc()` for XSS protection; OpenAI via Replit proxy |
| **Inputs** | Operator question, full_analysis snapshot |
| **Outputs** | Contextual market answer |
| **Dependencies** | `AI_INTEGRATIONS_OPENAI_API_KEY`, `AI_INTEGRATIONS_OPENAI_BASE_URL` |
| **Status** | ✅ Complete |
| **Dashboard** | AI chat panel (owner-only) |
| **Production Impact** | Display only |
| **Files** | `app.py` (`/assistant` route) |

---

### 57. Academy Knowledge Module

| Field | Detail |
|---|---|
| **Purpose** | In-app trading education library — approved sources, strategy rules, management guidelines |
| **Responsibilities** | `/academy/*` routes walled off from money path; `academy_sources`/`academy_strategies`/`academy_management_rules` tables; normalizer is fixed dashboard contract; owner-only; `/academy/ask` LLM Q&A against curriculum |
| **Inputs** | Operator-curated content, `/academy/ask` questions |
| **Outputs** | Structured learning content, LLM-grounded answers |
| **Dependencies** | PostgreSQL, OpenAI |
| **Status** | ✅ Complete |
| **Dashboard** | Academy panel (owner-only) |
| **Production Impact** | Zero — walled off |
| **Files** | `app.py` (`/academy/*` routes) |

---

### 58. Market Session Awareness

| Field | Detail |
|---|---|
| **Purpose** | CME/COMEX trading hours + holiday calendar to prevent signals during closed markets |
| **Responsibilities** | MNQ/MGC pause on weekend + daily 17–18 ET halt + US exchange holidays (full-day & ~13:00 ET half-days); `market_session_status()`; closed-override runs LAST in full_analysis |
| **Inputs** | Current UTC time |
| **Outputs** | `market_open` boolean, session label, closed-override block |
| **Dependencies** | Exchange holiday list, `market_session_status()` |
| **Status** | ✅ Complete |
| **Dashboard** | Session status indicator |
| **Production Impact** | Prevents live signals during market closure |
| **Files** | `app.py` (session section) |

---

### 59. Cross-Market Index Alignment

| Field | Detail |
|---|---|
| **Purpose** | Monitor MNQ/MES/MYM directional agreement for confluence context |
| **Responsibilities** | DISPLAY+NOTIFY only (gate untouched); refresh loop dev+prod; Discord send gated on `DISCORD_LIVE_ENABLED`; cooldown dedup; Aligned alerts only; channel-grouped |
| **Inputs** | full_analysis snapshots for all instruments |
| **Outputs** | `cross_market` alignment block, Discord alignment alert |
| **Dependencies** | `CROSS_MARKET_ENABLED`, multiple instrument analysis |
| **Status** | ✅ Complete (display + notify) |
| **Dashboard** | Cross-market panel |
| **Production Impact** | Notify only — gate untouched |
| **Files** | `app.py` (cross-market loop) |

---

### 60. ForexFactory News Feed

| Field | Detail |
|---|---|
| **Purpose** | Economic calendar events on the dashboard for context during scheduled high-impact events |
| **Responsibilities** | `/status`-fed; DISPLAY-ONLY; fetched on interval; NEVER feeds gate |
| **Inputs** | ForexFactory public calendar API |
| **Outputs** | `news` block in status response |
| **Dependencies** | HTTP fetch, `DISCORD_LIVE_ENABLED` schedule |
| **Status** | ✅ Complete (display only) |
| **Dashboard** | News panel in dashboard |
| **Production Impact** | Zero — display only |
| **Files** | `app.py` (news section) |

---

### 61. TradeZella Integration

| Field | Detail |
|---|---|
| **Purpose** | Import personal trade history from TradeZella journal into the shared trade memory |
| **Responsibilities** | Imported journal feeds shared memory as DOWN-WEIGHTED `source:"tradezella"` record; display-only build_reviews presenter; review-only/INSERT-SELECT/owner-only; memory DB read stays OUTSIDE LEARNING_LOCK |
| **Inputs** | TradeZella export file |
| **Outputs** | Historical trade records in shared memory (down-weighted) |
| **Dependencies** | PostgreSQL, `strategy_trades` table |
| **Status** | ✅ Complete |
| **Dashboard** | Trade Zella review panel (owner-only) |
| **Production Impact** | Display only; memory nudges are bounded |
| **Files** | `app.py` (TradeZella section) |

---

### 62. Diagnostics & Observability

| Field | Detail |
|---|---|
| **Purpose** | Real-time system health, per-gate diagnostics, and eval metrics |
| **Responsibilities** | `/diagnostics` per-gate PASS/FAIL + "Blocked by" (owner-only); `/diagnostics-live` live stream; `/eval-metrics` with counters; `_heartbeat_eval_loop` re-eval; EVAL_METRICS_LOCK; request-logger with `_redact()`; Flask zombie-prevention guards (3 os._exit guards) |
| **Inputs** | All scoring + gate evaluations |
| **Outputs** | Diagnostic panels, eval metric counters |
| **Dependencies** | Owner authentication |
| **Status** | ✅ Complete |
| **Dashboard** | Diagnostics panel (owner-only) |
| **Production Impact** | Observability only |
| **Files** | `app.py` (diagnostics, eval metrics sections) |

---

### 63. Analysis Bot (Second Instance)

| Field | Detail |
|---|---|
| **Purpose** | Fail-closed analysis-only mirror of the live bot for research without risk |
| **Responsibilities** | Receives forwarded webhooks via `ANALYSIS_BOT_FORWARD_URL`; ANALYSIS_ONLY=1 suppresses broker sends, Discord, and DB mutations; isolated `analysis_bot` schema; dev bash-spawn is different netns than Express (502 in dev, works in prod); own `backtest_engine.py` and `gate_diagnostics.log` |
| **Inputs** | Forwarded webhook payloads from live bot |
| **Outputs** | Analysis results on port 8001 at `/api2` |
| **Dependencies** | Live bot forwarding, PostgreSQL analysis_bot schema |
| **Status** | ✅ Complete |
| **Dashboard** | Available at `/api2` |
| **Production Impact** | Zero to live trading; research and validation |
| **Files** | `artifacts/analysis-bot/app.py`, `scripts/prod-start.sh` |

---

### 64. Pine Script Sources

| Field | Detail |
|---|---|
| **Purpose** | TradingView indicator scripts that generate all the webhook signals |
| **Responsibilities** | Scripts for: confirmation, sweep, volume, structure, CVD, zones, FVG/OB; auto-detect instrument + default unknowns to MGC; adding a contract requires editing them (not just app.py) |
| **Inputs** | Live market data in TradingView |
| **Outputs** | HTTP POST webhooks to `/webhook` |
| **Dependencies** | TradingView Pro+ subscription, Pine Script |
| **Status** | ✅ Complete (repo-owned) |
| **Dashboard** | N/A — source scripts |
| **Production Impact** | Every signal originates here |
| **Files** | `artifacts/tradingview-webhook/pine/`, `artifacts/analysis-bot/pine/` |

---

---

# TASK 2 — PRODUCT ROLE MAPPING

## Role 1: Trading Desktop

### ALREADY EXISTS
- Unified dashboard with 5-section live navigation
- Per-instrument tab switching (MGC/MNQ/MES/MYM)
- Real-time price display with VWAP context
- Market session status (open/closed/halt indicator)
- ForexFactory economic calendar news feed
- Today's Trades log per instrument
- Panel collapse + drag-reorder (per-device localStorage)
- Glass and retro UI themes
- Dashboard landing on best-probability setup
- Equity curve (today, no backfill)
- Cross-market index alignment panel
- Market health volatility indicator
- Last-updated clock / staleness indicator

### PARTIALLY IMPLEMENTED
- Equity curve: today-only with no historical backfill
- Cross-market: display + Discord notify but not fully integrated into the main verdict UX
- Watchlist: effectively just MGC/MNQ/MES/MYM (no dynamic instrument adding)

### NOT YET BUILT
- Multi-timeframe chart display (embedded TradingView chart widget)
- Dynamic instrument adding beyond the 4 hardcoded contracts
- Full equity curve with weekly/monthly history
- Intraday P&L tracker with running dollar totals
- Portfolio view across all instruments simultaneously
- Custom alert configuration UI (currently Pine-script-managed)

---

## Role 2: Trading Expert

### ALREADY EXISTS
- Left Brain Market Intelligence (direction/strength/momentum/narrative)
- Left Brain Thesis with confidence hysteresis and OUTLOOK_SHIFT detection
- Left Brain Observation buffer (5000-entry time-series)
- Strict Gate with per-gate PASS/FAIL diagnostics
- Edge Score (0–110, EDGE_COMPONENTS, grade A+/A/B/WAIT)
- Multi-Strategy Engine (29 strategies, regime-based selection)
- Analyst Reasoning Engine (professional-grade, game plan, veto)
- Trade Debate Engine (Bull/Bear/Judge)
- Main Brain cognitive synthesis
- SWING HTF Data Layer (1H/4H/Daily bias)
- Swing Mode V2 (9-category HTF scorer)
- Breakout Mode / ORB advisory
- Market structure detection (BOS/CHOCH/HH/HL/LH/LL)
- CVD hard filter (directional veto)
- Volume/RVOL component
- Sweep detection + Liquidity Sweep Focus overlay
- FVG/OB analyst evidence
- Potential-plan preview (forming-setup entry/stop/TP)
- Per-direction dashboard toggle (Long/Short bull/bear view)
- Entry Quality Location Engine (0–100 location scorer)
- MI adaptive strategy filter (SCALP veto)
- MI confidence-as-structure fallback (SCALP, flag-gated)
- Trend brake (HTF anti-trend demote, flag-gated)
- Structure-reversal demote (SCALP, flag-gated)

### PARTIALLY IMPLEMENTED
- Decision Pipeline V2: shadow only, no live stages enabled
- Breakout Mode: advisory only, auto-execute Phase-D not built
- SWING Mode V2: flag-gated, not default-on

### NOT YET BUILT
- Options flow / dark pool data integration
- Seasonality/macro calendar integration
- Multi-asset correlation analysis (beyond MNQ/MES/MYM alignment)
- Volatility surface / VIX context layer
- Fundamental event proximity scoring

---

## Role 3: Trading Partner

### ALREADY EXISTS
- Main Brain voice narration (contextual market commentary)
- Avatar Intelligence Engine (proactive event queue, daily greeting, explain-simply)
- VRM Avatar (3D animated trading partner in React home)
- AI Assistant chat panel (Q&A grounded on live full_analysis snapshot)
- Advisory overlays: Stalk (pre-entry) + Active Thinking (in-trade)
- Advisor confidence scoring with explicit reasoning
- Unified Analyst Report (consolidated thesis, 15-min update loop)
- Brain Conflict Resolver (why engines disagree)
- Verdict Board (plain-English 4-bucket classifier)

### PARTIALLY IMPLEMENTED
- Avatar Intelligence Engine: basic observer, `mbMemory` placeholder not yet wired to real trade memory
- AI Assistant: read-only, no proactive push (only responds to questions)
- Explain-simply mode: implemented but not prominently featured in UX

### NOT YET BUILT
- Voice output (text-to-speech) for the avatar
- Proactive "hey, look at this" market alerts from the partner persona (push to phone)
- Session recap narrative ("here's what happened today")
- Pre-session briefing (what to watch before the open)
- Post-session debrief with lessons learned
- Chat history persistence across sessions

---

## Role 4: Trading Coach

### ALREADY EXISTS
- Academy Knowledge Module (strategies, sources, management rules, LLM Q&A)
- Bot Training Mode (4-stage staged learning system)
- Thesis Tracker (outcome-based analyst memory, lessons + reflections)
- Trade Failure Analyzer (root-cause analysis of losses)
- Decision Quality Analytics (process quality scoring)
- Trade Idea Review (/review-idea — grade a hypothetical trade)
- Per-gate diagnostics (explicit "why WAIT" for every failed gate)
- Strict reason display (named failed gates in WAIT verdict)
- Historical win-rate by strategy/hour
- TradeZella import (review past personal trade history)
- Scalp Research Engine (16-strategy lab with live paper-sim)
- Backtest Engine (historical strategy testing with CSV)

### PARTIALLY IMPLEMENTED
- Thesis Tracker: snapshot + resolve built; coaching output (lesson/reflection) not prominently displayed in UX
- Trade Failure Analyzer: recording done; dashboard display for patterns limited
- Decision Quality: backend built; no dedicated coaching dashboard yet

### NOT YET BUILT
- Daily coaching report (auto-generated "here's what to work on")
- Habit tracking (e.g., "you keep entering without structure 3x/week")
- Structured lesson plans tied to specific weaknesses
- Video/annotated chart attachments to trade reviews
- Goal setting and progress tracking UI
- Peer comparison (anonymized benchmark against similar setups)

---

## Role 5: Trading Journal

### ALREADY EXISTS
- Live trade cards (Discord embeds: entry, rationale, grade, plan)
- Journal Discord channel (Analyst Reports, thesis updates, trade summaries)
- `strategy_trades` table (full trade record with metadata)
- EOD performance report (Discord)
- Trade outcome recording with R:R and P&L
- Today's Trades log panel on dashboard
- Equity curve (today)
- TradeZella import for historical journal enrichment
- Trade management analytics sidecar (MFE/MAE, commission, slippage)
- A+ channel filtering (high-conviction setups in dedicated channel)
- Screenshots forwarded to Discord on READY

### PARTIALLY IMPLEMENTED
- Equity curve: today only, no weekly/monthly view
- Trade management analytics: close-time metrics computed but not richly displayed in journal format
- MFE/MAE booleans: derived, not price-recomputed

### NOT YET BUILT
- In-app journal entry editor (annotate trades with notes/screenshots)
- Calendar view of trades (monthly P&L calendar heatmap)
- Tag/label system for trade categories
- Weekly/monthly performance report in-app (not just Discord)
- Chart screenshot automation (auto-capture at entry)
- Export to CSV/PDF/Excel
- Trade replay (re-run the market context as it was at entry)
- Public shareable journal (anonymized performance)

---

## Role 6: Trading Manager

### ALREADY EXISTS
- Execution Gateway (manual_only/paper/traderspost/pickmytrade modes)
- Auto-trade arming lifecycle (per-instrument, in-memory, safety-reset on restart)
- SCALP dynamic exits (TP1/TP2/runner with delayed BE)
- Live 2-contract runner (flag-gated)
- Manual Trade Manager (advisory overlay for hand-entered positions)
- Manual Desk Order (operator override, flag-gated)
- Per-asset safety controls (kill switch, position limits, maxLossesPerDay=5)
- Prop Firm Protection guard (daily loss limit hard stop)
- Active trade persistence (survives VM restarts)
- Right Brain Trade Management advisory (Phase 6B.2 shadow)
- Auto Early-Exit (armed watcher for confirmed-invalid thesis)
- Bot Training Mode (staged → live progression)
- Opposite-side reversal buffer (TradersPost send spacing)
- TradersPost connectivity probe
- USER_APPROVED_PREVIEW take (operator-approved forming setups)
- Advisor auto-trade review gate (opt-in pre-trade approval)
- View-only share link (watch-only dashboard for co-pilot/observer)

### PARTIALLY IMPLEMENTED
- Right Brain Trade Management: shadow advisory, Phase-D auto-execution not built
- Live 2-contract runner: flag-gated, not default-on
- Manual Desk Order: flag-gated, not default-on
- Auto Early-Exit: armed watcher built, but arm state resets on restart

### NOT YET BUILT
- Position sizing optimizer (dynamic sizing based on account equity + volatility)
- Risk-adjusted P&L tracker (CAGR, Sharpe, max drawdown in-app)
- Multi-account management (live vs paper vs prop simultaneously)
- Automated daily risk report
- Account equity integration (real broker balance feed, not estimated)
- Tax lot management / realized P&L tracking

---

---

# TASK 3 — FEATURE MAP

```
AI Trading Partner
│
├── Trading Desktop
│   ├── Dashboard
│   │   ├── 5-Section Live Navigation
│   │   ├── Per-Instrument Tabs (MGC / MNQ / MES / MYM)
│   │   ├── Glass + Retro Themes
│   │   ├── Panel Collapse + Drag Reorder
│   │   └── Best-Setup Auto-Landing
│   ├── Market Monitoring
│   │   ├── Real-Time Price + VWAP
│   │   ├── Market Session Status (CME/COMEX hours + holidays)
│   │   ├── Volatility Indicator (ATR ratio)
│   │   ├── Cross-Market Index Alignment (MNQ/MES/MYM)
│   │   └── Economic Calendar (ForexFactory)
│   ├── Performance
│   │   ├── Today's Equity Curve
│   │   ├── Today's Trades Log (per instrument)
│   │   └── Simulation Realism Overlay (net of commission+slippage)
│   └── Access & Sharing
│       ├── Basic Auth Gate
│       └── View-Only Share Link (HMAC, expiring)
│
├── Trading Expert
│   ├── Market Data Layer
│   │   ├── VWAP Engine (auto-fetch + manual override + grace window)
│   │   ├── ATR / Volatility Monitor (1m SCALP, HTF SWING)
│   │   ├── CVD / Delta Engine (hard veto + edge component)
│   │   ├── Volume / RVOL (edge component)
│   │   └── Databento Live Feed (flag-gated, bar-close precision)
│   ├── Market Structure
│   │   ├── BOS / CHOCH Detection
│   │   ├── HH / HL / LH / LL Swing Labels
│   │   ├── Supply / Demand Zone Tracking
│   │   ├── Liquidity Sweep Detector
│   │   ├── FVG / OB Evidence (analyst layer, display only)
│   │   └── Fast-Entry Bridge (SWEEP_RECLAIM + MICRO_CHOCH → inject structure)
│   ├── Left Brain Intelligence
│   │   ├── Market Intelligence (direction/strength/momentum/narrative)
│   │   ├── Dynamic Thesis (confidence hysteresis, OUTLOOK_SHIFT)
│   │   ├── Observation Buffer (5000-entry time-series, v2 endpoint)
│   │   ├── Playbook Selector (top-3 ranked strategies for context)
│   │   └── MI Adaptive Strategy Filter (SCALP demote-only veto)
│   ├── Decision Engine
│   │   ├── Strict Gate (zone + VWAP + structure, mode-tunable)
│   │   ├── Edge Score (0–110, 7 components + Session bonus)
│   │   ├── Grade (A+ / A / B / WAIT)
│   │   ├── Entry Quality Location Engine (0–100 location score)
│   │   ├── Trend Brake (HTF anti-trend, flag-gated)
│   │   ├── Structure-Reversal Demote (SCALP, flag-gated)
│   │   └── Per-Gate Diagnostics (explicit PASS/FAIL per gate)
│   ├── Strategy Scanner
│   │   ├── Multi-Strategy Engine (29 strategies, 5 scorers)
│   │   ├── Regime → Strategy Mapping
│   │   ├── ORB (9:30 Opening Range Breakout) with 1:4 R:R retarget
│   │   ├── Breakout Mode Advisory (flag-gated)
│   │   ├── SWING Mode V2 (9-category HTF scorer, flag-gated)
│   │   └── Strategy Scan Coverage Diagnostics
│   ├── Advanced Entry Timing
│   │   ├── Fast Entry Trigger (1s/5s timing overlay)
│   │   ├── Dual-TF Engine (1m bias + 5s convergence, flag-gated)
│   │   └── Micro Scalp Mode (sweep→trap→trigger, flag-gated)
│   └── Higher Timeframe Context
│       ├── SWING HTF Data Layer (1H/4H/Daily)
│       ├── SWING EMA/RSI/MACD/ADX (Pine webhook)
│       └── Potential-Plan Preview (forming-setup levels)
│
├── Trading Partner
│   ├── AI Reasoning Layer
│   │   ├── Analyst Reasoning Engine (professional grader + game plan)
│   │   ├── Trade Debate Engine (Bull/Bear/Judge)
│   │   ├── Main Brain Cognitive Layer (synthesis + voice + UI)
│   │   ├── Brain Conflict Resolver (10-priority, why engines disagree)
│   │   └── Verdict Board (4-bucket plain-English classifier)
│   ├── Advisor Persona
│   │   ├── Avatar Intelligence Engine (proactive observer + greetings)
│   │   ├── VRM 3D Avatar (LordPiggington.vrm in React)
│   │   ├── Explain-Simply Mode
│   │   └── Stalk + Active Thinking Overlays (pre-entry + in-trade)
│   ├── Conversational Interface
│   │   ├── AI Assistant Chat (/assistant, grounded on live snapshot)
│   │   ├── Academy /academy/ask (LLM vs curriculum)
│   │   └── Operator Mode UI (Cockpit/Sentinel, conversational Brain)
│   └── Unified Synthesis
│       ├── Unified Analyst Report (consolidated thesis + 15-min loop)
│       └── Shared Trade Memory (similar historical trades, 4-lens governor)
│
├── Trading Coach
│   ├── Education
│   │   ├── Academy Knowledge Module (sources, strategies, management rules)
│   │   └── Trade Idea Review (/review-idea — grade hypothetical trades)
│   ├── Performance Analysis
│   │   ├── Trade Failure Analyzer (root-cause analysis)
│   │   ├── Decision Quality Analytics (process scoring)
│   │   ├── Thesis Tracker (outcome-based memory, lessons)
│   │   └── Scalp Research Engine (16-strategy live paper-sim lab)
│   ├── Diagnostics
│   │   ├── Per-Gate Diagnostics (explicit why WAIT)
│   │   ├── Strict Reason Display
│   │   └── Backtest Engine (historical strategy testing)
│   ├── Training
│   │   ├── Bot Training Mode (4-stage staged → live)
│   │   └── Baseline Engine (strategy×management optimization)
│   └── Historical Context
│       ├── TradeZella Import (personal history, down-weighted)
│       └── Scalp Strategy Advisory (16-vote reasoning roster)
│
├── Trading Journal
│   ├── Live Alerts
│   │   ├── Trade Cards (Discord: entry, rationale, grade, plan)
│   │   ├── EARLY Alert (⚡ pre-READY advisory)
│   │   ├── A+ Channel (high-conviction filtered feed)
│   │   └── Trade-Taken Bell (audio, data URI)
│   ├── Ongoing Updates
│   │   ├── Analyst Reports (Discord journal channel, 15-min loop)
│   │   ├── Trade Management Updates (periodic in-trade Discord)
│   │   └── Cross-Market Alignment Alerts
│   ├── Trade Records
│   │   ├── strategy_trades DB Table (full record)
│   │   ├── Trade Management Analytics (MFE/MAE, commission, slippage)
│   │   └── Today's Trades Dashboard Panel
│   └── Reporting
│       ├── EOD Performance Report (Discord)
│       ├── Weekly Learning Report
│       └── Equity Curve (today)
│
└── Trading Manager
    ├── Execution
    │   ├── Execution Gateway (manual/paper/traderspost/pickmytrade)
    │   ├── Auto-Trade Arming (per-instrument, in-memory)
    │   ├── TradersPost Integration
    │   ├── Broker Payload Pre-Send Guard
    │   ├── Opposite-Side Reversal Buffer
    │   └── USER_APPROVED_PREVIEW Take
    ├── Position Management
    │   ├── Active Trade Persistence (survives restarts)
    │   ├── SCALP Dynamic Exits (TP1/TP2/runner + delayed BE)
    │   ├── Live 2-Contract Runner (flag-gated)
    │   ├── Manual Trade Manager (advisory for hand-entered)
    │   ├── Manual Desk Order (operator override, flag-gated)
    │   ├── Auto Early-Exit (confirmed-invalid thesis watcher)
    │   └── Right Brain Trade Management (shadow advisory)
    ├── Risk Controls
    │   ├── Per-Asset Safety Controls (kill switch, position limits)
    │   ├── Prop Firm Protection Guard (daily loss limit)
    │   ├── Advisor Auto-Trade Review Gate
    │   └── Bot Training Mode Suppression (< Stage 4)
    └── Infrastructure
        ├── Market State Cache Persistence
        ├── Flask Zombie-Prevention Guards
        └── Analysis Bot (parallel read-only mirror)
```

---

---

# TASK 4 — DATA FLOW

## Complete Logical Pipeline

```
┌─────────────────────────────────────────────────────┐
│               STAGE 0: SIGNAL SOURCES                │
│                                                       │
│  TradingView Pine Scripts                            │
│  ┌──────────────┐ ┌──────────────┐ ┌─────────────┐  │
│  │ Structure    │ │ Zones / FVG  │ │ CVD/Volume  │  │
│  │ BOS/CHOCH/   │ │ Supply/Demand│ │ Sweeps/VWAP │  │
│  │ HH/HL/LH/LL  │ │ OB / FVG     │ │ Delta/Speed │  │
│  └──────┬───────┘ └──────┬───────┘ └──────┬──────┘  │
│         └────────────────┴────────────────┘          │
│                          │ HTTP POST /webhook         │
└──────────────────────────┼──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│               STAGE 1: INGESTION                     │
│                                                       │
│  Express API Server (:8080)                          │
│  • Basic Auth + CSRF check                           │
│  • Raw body forwarding (text/plain safe)             │
│  • Proxy /api/* → Flask :8000                        │
│  • /api/healthz health probe (direct)                │
│                          │                           │
│  Flask webhook() handler                             │
│  • Normalize alert_type (strip/upper)                │
│  • ALERT_TYPES gate (recognized vs unrecognized)     │
│  • Structure bridge (SWEEP_RECLAIM → inject LH/HL)  │
│  • Instrument resolution (ticker-first, fail-closed) │
│                          │                           │
└──────────────────────────┼──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│               STAGE 2: DATA STORES                   │
│                                                       │
│  Per-instrument in-memory stores:                    │
│  • ALERT_HISTORY (deque, shared, last 100 scored)    │
│  • VWAP_BY_TICKER (auto-fetch + manual override)     │
│  • CVD_BY_INST (bullish/bearish/unknown)             │
│  • ZONE_BROKEN_AT (mitigation TTL)                   │
│  • HTF_STATE_BY_INST (1H/4H/Daily bias + EMA data)  │
│  • FAST_ENTRY_STATE_BY_TICKER (seconds micro-events) │
│  • ACTIVE_TRADES_BY_INST (write-through → Postgres)  │
│  • DUAL_TF_BIAS_BY_INST (5s convergence state)       │
│  • SWING_THESES_BY_INST (active SWING thesis)        │
│                                                       │
│  Databento Live Feed (optional):                     │
│  • OHLCV bars → ATR precision                        │
│  • Bar-close scan trigger                            │
│                                                       │
└──────────────────────────┼──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│               STAGE 3: MARKET SNAPSHOT               │
│                                                       │
│  full_analysis(ticker_override) assembles:           │
│  • Current price + VWAP + spread                     │
│  • VWAP status (freshness) + vwap_confirmed          │
│  • ATR + volatility ratio                            │
│  • CVD state + committed direction                   │
│  • Structure state (structure_confirmed, bias)       │
│  • Zone state (zone_confirmed, broken/active)        │
│  • Alert history snapshot (last N alerts)            │
│  • Session status (open/closed/halt)                 │
│  • Market state cache restore (if boot)              │
│                                                       │
└──────────────────────────┼──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│               STAGE 4: LEFT BRAIN                    │
│                                                       │
│  compute_left_brain_market_intelligence()            │
│  • Reads ALERT_HISTORY + VWAP + CVD + price          │
│  • Produces: direction, strength, momentum           │
│  • Produces: supporting_evidence, confidence         │
│  • Detects: OUTLOOK_SHIFT significant changes        │
│                          │                           │
│  compute_left_brain_thesis()                         │
│  • Persistent narrative with confidence hysteresis   │
│  • Playbook reasoning (top-3 fit-scored strategies)  │
│  • Timeline + invalidation conditions                │
│  • Stores to _LB_THESIS_OBS_BY_INST (5000-entry buf) │
│  • Detects: OUTLOOK_SHIFT → Discord notify           │
│                                                       │
│  MI Adaptive Strategy Filter (SCALP veto)            │
│  • Blocks actionable setup fighting unambiguous MI   │
│  • Demote-only, fail-open, all 3 flags default ON    │
│                                                       │
└──────────────────────────┼──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│               STAGE 5: STRICT GATE                   │
│                                                       │
│  evaluate_strict_setup()                             │
│  SWING mode (floor = 80 pts):                        │
│  • zone_confirmed (REQUIRE) — zone must be active    │
│  • vwap_confirmed (REQUIRE) — price side of VWAP     │
│  • structure_confirmed (REQUIRE) — BOS/CHOCH/HH etc  │
│                                                       │
│  SCALP mode:                                         │
│  • zone: DEMOTE ONLY (zone_confirmed=False → WAIT)   │
│  • vwap_confirmed (REQUIRE)                          │
│  • structure_confirmed (REQUIRE)                     │
│                                                       │
│  Both modes:                                         │
│  • CVD hard veto (fights direction → BLOCK)          │
│  • Market closed override (LAST check)               │
│  • Produces: is_actionable, verdict, strict_reason   │
│              gate_debug (per-gate PASS/FAIL)         │
│                                                       │
└──────────────────────────┼──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│               STAGE 6: EDGE SCORE                    │
│                                                       │
│  _analysis_edge_breakdown()                          │
│  EDGE_COMPONENTS (max 110):                          │
│  • BOS20 (+20 if BOS present)                        │
│  • CHOCH20 (+20 if CHOCH present)                    │
│  • VWAP15 (+15 if VWAP confirmed)                    │
│  • Sweep15 (+15 if sweep present)                    │
│  • Volume15 (+15 if RVOL ≥ threshold)                │
│  • CVD15 (+15 if CVD agrees)                         │
│  • Session10 (+10 during prime session hours)        │
│                                                       │
│  Modifiers:                                          │
│  • Learning influence ±15 (flag-gated, bounded)      │
│  • Entry Quality veto override (score<70, edge<90)   │
│                                                       │
│  Output: edge_score, grade (A+/A/B/WAIT)             │
│          alert_level (READY/EARLY/WATCH)             │
│          conviction_tier                             │
│                                                       │
└──────────────────────────┼──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│               STAGE 7: STRATEGY SCAN                 │
│                                                       │
│  Multi-Strategy Engine                               │
│  • Regime → strategy priority mapping                │
│  • 29 strategy definitions, 5 scorers                │
│  • Active strategy selection                         │
│  • ORB: 1:4 R:R retarget (money path)               │
│  • SWING V2: 9-category HTF scorer (flag-gated)      │
│  • Breakout Mode: ORB advisory (flag-gated)          │
│                                                       │
└──────────────────────────┼──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│          STAGE 8: ANALYST REASONING LAYER            │
│                                                       │
│  (All display-first; each adds demote-only veto)     │
│                                                       │
│  Analyst Reasoning Engine                            │
│  • Market phase + ATR extension check                │
│  • FVG/OB evidence integration                       │
│  • Professional game plan (probabilistic scenarios)  │
│  • Veto: demotes actionable→WAIT (default ON)        │
│                                                       │
│  Trade Debate Engine                                 │
│  • Bull analyst + Bear analyst + Judge               │
│  • final_verdict: TAKE iff decisive + aligned        │
│  • Veto: demotes actionable→WAIT (default OFF)       │
│                                                       │
│  Professional Review Layer                           │
│  • Per-instrument SCALP/SWING models                 │
│  • Prime window scoring                              │
│                                                       │
│  Advisor Auto-Trade Review Gate (opt-in)             │
│  • Requires reviewed marker in BOTH analysts         │
│                                                       │
└──────────────────────────┼──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│          STAGE 9: MAIN BRAIN SYNTHESIS               │
│                                                       │
│  _mb_orchestrate() → _mb_learning_snapshot()         │
│  → compute_main_brain()                              │
│                                                       │
│  Inputs consumed (never recomputed):                 │
│  • analyst / debate / governor / memory / vol / news │
│                                                       │
│  Outputs:                                            │
│  • main_brain block (7 cognitive keys)               │
│  • Brain Conflict Resolver (10-priority engine)      │
│  • Verdict Board (4-bucket plain-English)            │
│  • main_brain_voice narration                        │
│  • Avatar observations (mbAvatarObserve)             │
│  • Decision Pipeline V2 shadow log                   │
│                                                       │
└──────────────────────────┼──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│               STAGE 10: LEARNING LAYER               │
│                                                       │
│  Adaptive Learning Engine                            │
│  • Per-strategy win rates from strategy_trades       │
│  • Edge score ±15 modifier (flag-gated)              │
│  • Learning Rule Engine (GHOST_ONLY / LIVE_ELIGIBLE) │
│                                                       │
│  Unified Learning Brain                              │
│  • PER_MODE_STATS global aggregates                  │
│  • Playbook selector recommendations                  │
│                                                       │
│  Shared Trade Memory                                 │
│  • find_similar_trades() — 4-lens governor           │
│  • TradeZella history (down-weighted)                │
│                                                       │
│  Thesis Tracker                                      │
│  • Snapshot → 25–75 min resolve → lesson             │
│                                                       │
└──────────────────────────┼──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│               STAGE 11: DASHBOARD DELIVERY           │
│                                                       │
│  /status endpoint (3s poll by React dashboard)      │
│  • Whitelisted keys only (curated serialization)     │
│  • Per-instrument result via ?ticker=                │
│                                                       │
│  Dashboard renders:                                  │
│  • Main Brain hero (verdict, orb, voice)             │
│  • Per-instrument analysis panels                    │
│  • Trade management controls                         │
│  • Journal / Today's Trades                          │
│  • Research panels (backtest, academy)               │
│                                                       │
│  Discord notifications:                              │
│  • READY trade card (main channel)                   │
│  • Analyst Report (journal channel, 15-min loop)     │
│  • A+ alerts (high-conviction channel)               │
│  • EOD report                                        │
│                                                       │
└──────────────────────────┼──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│               STAGE 12: EXECUTION                    │
│                                                       │
│  Auto-trade check (when armed):                      │
│  Bot Training Mode gate (stage < 4 → suppress)      │
│  Prop Firm Protection (daily loss limit)             │
│  Per-Asset Safety Controls (kill switch)             │
│  Advisor Review Gate (opt-in pre-approval)           │
│  Execution Gateway (EXECUTION_MODE routing)          │
│                          │                           │
│          ┌───────────────┼──────────────────┐        │
│          │               │                  │        │
│          ▼               ▼                  ▼        │
│    TradersPost       Paper Trade        Manual Only  │
│    (live broker)     (local log)        (no send)    │
│                                                       │
│  Post-execution:                                     │
│  • ACTIVE_TRADE written + persisted to open_trades  │
│  • Journal card fired (Discord)                      │
│  • AUTO_FIRED_KEYS dedup registered                  │
│  • SCALP dynamic exits armed (if enabled)            │
│  • Live runner armed (if enabled)                    │
│                                                       │
└─────────────────────────────────────────────────────┘
```

---

---

# TASK 5 — GAP ANALYSIS

## Duplicated Functionality

| Issue | Detail |
|---|---|
| **Duplicate ATR computation** | `_analyst_phase_atr()` selects SCALP vs SWING ATR; the global `get_volatility()` also computes ATR. Two separate code paths; must always use `_analyst_phase_atr` in the analyst context or mode-incorrect ATR leaks in. |
| **Duplicate trade card build** | `_build_card_entry` is the single source BUT several code paths inline similar Discord embed construction for edge cases — these don't use the shared helper. |
| **Duplicate instrument resolution** | `resolve_instrument()`, `instrument_of()`, `_instrument_from_text()` — three functions with overlapping purpose, used inconsistently. Some code uses one, some another; can produce silent misattribution. |
| **Dual similar-history sources** | Before `find_similar_trades()` became the single source, multiple analyst functions computed their own history scans. The consolidation happened but some old pattern still exists in fallback paths. |
| **Structure signal injection at two layers** | Structure bridge (fast-entry) injects LH/HL/CHOCH into ALERT_HISTORY. The regular Pine structure alerts do the same. The two paths are intentionally separate but both update the same store; their interaction under concurrent webhooks is managed but subtle. |
| **CVD check at gate AND edge** | CVD is a hard veto in `evaluate_strict_setup` AND a +15 component in the edge score. A setup where CVD disagrees fails the gate (never reaches score display) but the edge score helper has a CVD component that can never fire positively in a WAIT. The gate check makes the score component display-only moot in practice. |

---

## Disconnected Functionality

| Issue | Detail |
|---|---|
| **Left Brain Observation Buffer → Dashboard** | `/lb-thesis-obs` endpoint exists with rich v2 schema (summary stats, retention, inst/limit filters) but is not connected to any dashboard panel. The time-series observation data is dark. |
| **Decision Pipeline V2 → any action** | All 5 stages (OBSERVE→INTERPRET→PRIORITIZE→VALIDATE→DECIDE) run in shadow mode but all `CAN_*` flags are OFF. The pipeline produces log lines but never influences any verdict. |
| **Decision Quality Analytics → coaching UI** | `decision_snapshots` table is populated on each trade, but there is no in-app UI that reads it and presents coaching insights. |
| **Trade Failure Analyzer → dashboard** | `TFA_DB_READY`-gated recording works, but the `/failure-analysis` route data is owner-only and not surfaced in any coaching-oriented dashboard panel. |
| **Right Brain Trade Management → auto-action** | Phase 6B.2 shadow advisory produces recommendations (trail/take/exit) but Phase-D auto-execution (actually sending the order) was explicitly not built. Advisory sits in result but no auto-action loop reads it. |
| **Thesis Tracker → coaching display** | Snapshot→resolve→lesson cycle runs in the DB but the lesson output has no dedicated coaching panel to display patterns or repeated mistakes. |
| **Baseline Engine → live parameter tuning** | `bt_baseline.py` can identify optimal strategy×management combos, but there is no feedback loop connecting baseline findings back to the live bot's parameters. |
| **Avatar `mbMemory` placeholder** | Avatar Intelligence Engine has a `mbMemory` field in the data contract that was marked as a placeholder. Trade memory exists in the Shared Trade Memory engine but has not been wired to populate the avatar's memory field. |
| **Prop Firm Protection → operator setup UI** | The `/prop-accounts` endpoint manages `prop_accounts` table but there is no dashboard UI to set up or view prop account configurations. |
| **ForexFactory news → pre-trade gate** | News is fetched and displayed. There is no gate or scoring component that uses news proximity (e.g., "avoid trading 5 minutes before a red-impact event"). |

---

## Orphaned Modules

| Module | Issue |
|---|---|
| **`_LAST_DECISION_TRACE`** | `build_legacy_decision_trace()` populates `_LAST_DECISION_TRACE` per instrument and `/decision-trace` route exposes it, but no dashboard panel renders it. It's queryable only by direct API call. |
| **`_SWING_V2_STATE_BY_INST`** | Swing Mode V2 EMA state updated via `SWING_EMA_UPDATE` webhook but `SWING_MODE_V2_ENABLED` is default OFF in production — the state accumulates unused. |
| **`DUAL_SIM_TRADES` table** | The dual-sim watcher has its own `dual_sim_trades` table that logs simulated trades, but there is no reporting or analytics UI consuming this data. |
| **`MICRO_SCALP_GHOST_TRADES` table** | Micro scalp ghost ledger records every simulated micro-scalp but the data is not exposed in any analytics panel. |
| **`bot_training_state` table** | Training mode reads/writes state to DB but there is no dashboard view of historical training stage progression. |
| **`academy_validation_events` table** | Events table in the academy schema exists but is not populated or displayed anywhere currently. |

---

## Hidden Features (exist but not prominently surfaced)

| Feature | Where hidden |
|---|---|
| **Trade Idea Review** | `/review-idea` route exists but is only accessible via direct API call; no prominent dashboard entry point |
| **`/clear-fired-keys`** | Auto-Trade Settings button exists but the endpoint's purpose (clearing dedup keys to re-arm) is not explained in the UI |
| **Cross-market alignment** | Panel exists but is collapsed by default and not part of the main signal flow display |
| **Structure-reversal demote** | Flag-gated default OFF — operator may not know this protection exists |
| **Trend brake** | Flag-gated default OFF — ditto |
| **Opposite-side reversal buffer** | Default 0=OFF; configuration exists but no UI to set it |
| **Auto Early-Exit** | Armed watcher exists but arm resets on restart; no persistent dashboard reminder of its state |
| **MI confidence-as-structure fallback** | SCALP-only flag-gated feature; no UI that explains when it fires |
| **`/lb-thesis-obs` endpoint** | Rich observation buffer with v2 API exists but zero UI |

---

## Dashboard Features with No Backend

| Feature | Issue |
|---|---|
| **Equity curve backfill** | Dashboard renders equity curve for today only; historical equity (weekly/monthly) has no data source. |
| **Phone push notifications** | Dashboard bell fires (it's a local browser notification); a true mobile push notification system (FCM/APNs) does not exist. |
| **Per-panel pin to non-main pair** | Today's Trades log pins to a non-main pair via a separate /status?ticker fetch — but there is no UI affordance explaining this pinning behavior. |

---

## Backend Features with No Dashboard

| Feature | Issue |
|---|---|
| **`/lb-thesis-obs`** | Full v2 endpoint, rich data, no panel |
| **`/decision-trace`** | Queryable but no dashboard widget |
| **`/failure-analysis`** | Data available, no coaching panel |
| **Decision Quality DB** | Populated, no display |
| **Dual-sim ghost trades DB** | Populated, no reporting |
| **Micro-scalp ghost trades DB** | Populated, no reporting |
| **Prop firm account configuration** | DB-backed, no admin UI |
| **`/advisor` route** | Full AI advisor endpoint; not prominently linked from main dashboard |
| **`/diagnostics-live`** | Live stream endpoint; not embedded in dashboard |

---

## Learning Systems Not Connected

| System | Gap |
|---|---|
| **Decision Quality → Edge Score** | Decision quality scores are computed and stored but never feed back into edge scoring or gate thresholds |
| **Trade Failure Patterns → Gate Tuning** | Failure analyzer identifies recurring failure modes but has no path to adjust gate parameters based on patterns |
| **Thesis Tracker patterns → Thesis confidence** | Pattern memory (≥3 samples in DB) exists but the confidence adjustment loop is not wired; thesis doesn't harden/soften based on historical pattern performance |
| **Baseline findings → Live params** | Optimal params found in backtest have no automated feedback loop to live execution parameters |
| **Avatar mbMemory** | Placeholder not populated from Shared Trade Memory |
| **DPv2 stages** | All 5 pipeline stages shadow-only; no mechanism to promote a validated stage to live |

---

## Training Systems Not Connected

| System | Gap |
|---|---|
| **Bot Training Mode → UI coaching** | Stage progression logic exists in DB but there is no dashboard that shows "you are on Stage 2, here's what Stage 3 requires" |
| **Bot Training Mode → Academy** | Training stages and Academy curriculum are completely disconnected — a trader in Stage 1 doesn't see Academy content relevant to their stage |
| **Backtest → Training recommendations** | Backtest results don't feed into training stage promotion criteria |
| **Scalp Research → Strategy activation** | Research engine identifies `recommended` strategies by live performance but there is no pathway to promote them to the live multi-strategy engine |

---

---

# TASK 6 — PRODUCT ROADMAP

## Phase 1: Foundation
*Core infrastructure, data pipeline, and live execution.*

### Completed
- Flask webhook server with full ALERT_TYPES registry
- Instrument registry + resolver (MGC/MNQ/MES/MYM)
- VWAP engine (auto-fetch + manual override + grace window)
- ATR/Volatility Monitor (SCALP 1m + SWING HTF)
- CVD / Volume / Sweep ingestion
- Market Structure detection (BOS/CHOCH/HH/HL/LH/LL)
- Supply/Demand Zone engine
- Strict Gate (zone + VWAP + structure, mode-tunable)
- Edge Score (0–110, 7 components)
- Execution Gateway (manual/paper/TradersPost/PickMyTrade)
- Auto-trade arming (per-instrument, in-memory, safety-reset)
- Active trade persistence (open_trades → Postgres)
- Market State Cache persistence
- Express API proxy (Basic Auth + CSRF + raw body forwarding)
- React dashboard (5-section nav, panel collapse/drag, per-instrument tabs)
- Journal system (trade cards, Discord embeds, EOD report)
- TradingView Pine scripts (structure, zones, CVD, sweeps, FVG/OB)
- Market session awareness (CME/COMEX hours + holidays)

### In Progress
- Databento live feed (pip ready; needs API key in production)

### Future
- WebSocket direct market data (bypass TradingView dependency)
- Dynamic instrument adding beyond the 4 hardcoded contracts

---

## Phase 2: Intelligence
*AI reasoning, thesis, and multi-layer analysis.*

### Completed
- Left Brain Market Intelligence (direction/strength/momentum)
- Left Brain Thesis Engine (confidence hysteresis, OUTLOOK_SHIFT)
- Left Brain Observation Infrastructure (5000-entry buffer, v2 API)
- Analyst Reasoning Engine (game plan, veto, FVG/OB evidence)
- Trade Debate Engine (Bull/Bear/Judge, demote-only veto)
- Main Brain cognitive synthesis (voice, BCR, Verdict Board)
- Multi-Strategy Engine (29 strategies, regime-based)
- Entry Quality Location Engine (0–100 location scorer)
- CVD hard veto
- MI adaptive strategy filter (SCALP demote-only)
- Trend brake (flag-gated)
- Structure-reversal demote (flag-gated)
- SWING HTF Data Layer (1H/4H/Daily)
- Fast-Entry Bridge (SWEEP_RECLAIM + MICRO_CHOCH → structure inject)
- Cross-Market Index Alignment (display + notify)
- ForexFactory news feed (display only)

### In Progress
- Decision Pipeline V2 (shadow stages; no live flags yet)
- Right Brain Trade Management (Phase 6B.2 shadow; Phase-D auto-action not built)
- Left Brain Observation Buffer → Dashboard panel

### Future
- Decision Pipeline V2 live stage promotion (flip CAN_* flags one at a time)
- Right Brain auto-exit execution (Phase-D)
- Options flow / dark pool data integration
- Volatility surface / VIX context layer
- News-proximity gate (avoid trading near high-impact events)
- Seasonality/macro calendar integration

---

## Phase 3: Operator Experience
*Dashboard, visualization, and operator ergonomics.*

### Completed
- 5-section live navigation
- Per-instrument tab switching
- Glass + Retro themes
- Panel collapse + drag reorder (localStorage)
- Dashboard auto-landing on best-probability setup
- Simulation realism overlay (commission + slippage net display)
- Today's Equity Curve
- Today's Trades log (per instrument + pair-pinning)
- Per-direction toggle (Long/Short bull/bear view)
- EARLY alert (⚡ pre-READY advisory)
- Potential-plan preview (forming-setup levels)
- Trade-taken bell (audio, data URI)
- View-only share link (HMAC, expiring, watch-only)
- AI Assistant chat (Q&A grounded on live snapshot)
- Operator Mode UI (Cockpit / Sentinel / Brain)
- A+ alert channel (high-conviction filtered)
- Advisory overlays (Stalk + Active Thinking)
- Avatar Intelligence Engine (proactive events, daily greeting)
- VRM 3D Avatar (LordPiggington.vrm)

### In Progress
- Left Brain Observation Buffer → Dashboard panel (backend done, UI missing)
- Decision Trace viewer
- Failure Analysis coaching panel

### Future
- Embedded TradingView chart widget
- Multi-timeframe side-by-side view
- Voice output (text-to-speech) for the avatar
- Proactive push alerts from the avatar to phone
- Session briefing (pre-market daily prep)
- Session debrief (post-market recap narrative)
- Trade annotation editor (notes + screenshots in-app)
- Monthly P&L calendar heatmap
- Export to CSV/PDF/Excel
- Mobile-optimized dashboard view

---

## Phase 4: Learning
*Feedback loops, performance analytics, and self-improvement.*

### Completed
- Adaptive Learning Engine (strategy weights, ±15 edge modifier)
- Unified Learning Brain (PER_MODE_STATS, playbook selector)
- Shared Trade Memory Engine (4-lens governor, similar-trade lookup)
- Learning Rule Engine (GHOST_ONLY / LIVE_ELIGIBLE gate)
- Backtest Engine (CSV upload, run, optimize, coverage)
- Baseline Engine (strategy×management optimization)
- Scalp Research Engine (16-strategy live paper-sim lab)
- Scalp Strategy Advisory (16-vote reasoning roster)
- TradeZella integration (historical import, down-weighted)
- Trade Idea Review (/review-idea, graded hypothetical trades)
- Academy Knowledge Module (sources, strategies, management rules, LLM Q&A)
- Bot Training Mode (4-stage staged → live)
- Thesis Hysteresis (persistent confidence, reversal-needs-reset)
- Thesis Phase 3 enforcement (shadow gate)

### In Progress
- Thesis Tracker (snapshot + resolve built; coaching output UI missing)
- Trade Failure Analyzer (recording done; coaching panel missing)
- Decision Quality Analytics (DB populated; no coaching dashboard)

### Future
- Decision Quality → Edge Score feedback (process quality influences weighting)
- Trade Failure Patterns → Gate tuning recommendations
- Thesis Tracker patterns → Thesis confidence adjustment
- Baseline findings → Live parameter promotion workflow
- Avatar mbMemory wired to Shared Trade Memory
- DPv2 live stage promotion from validated shadow stages
- Daily auto-generated coaching report ("here's what to work on")
- Habit tracking (pattern detection across operator decisions)
- Goal setting + progress tracking UI
- Weekly pattern library update from Thesis Tracker
- Scalp Research → Strategy promotion to live multi-strategy engine

---

## Phase 5: Automation
*Autonomous execution, risk management, and self-management.*

### Completed
- Auto-trade arming lifecycle (per-instrument, in-memory safety)
- SCALP dynamic exits (TP1/TP2/runner + delayed BE)
- Per-asset safety controls (kill switch, maxLossesPerDay)
- Prop Firm Protection guard (daily loss limit hard stop)
- Auto Early-Exit (confirmed-invalid thesis watcher)
- Advisor auto-trade review gate (opt-in pre-trade approval)
- Bot Training Mode suppression (< Stage 4)
- Opposite-side reversal buffer (TradersPost spacing)
- Broker payload pre-send guard (audit + required-field check)
- TradersPost connectivity probe

### In Progress
- Live 2-contract Runner (flag-gated, default OFF)
- Manual Desk Order (flag-gated, default OFF)
- Right Brain auto-exit Phase-D (not yet built)
- Dual-TF Engine (flag-gated, default OFF)
- Micro Scalp Mode (flag-gated, default OFF)
- Fast Entry Trigger (money path flag-gated)

### Future
- Right Brain auto-exit execution (Phase-D — acting on RBTM recommendations)
- Dynamic position sizing (based on account equity + volatility)
- Multi-account management (live vs paper vs prop simultaneously)
- Automated daily risk report
- Account equity feed from real broker (replace estimated tracking)
- Tax lot management
- Webhook → execution latency monitoring (SLA tracking)

---

## Phase 6: Production
*Stability, observability, and deployment hardening.*

### Completed
- Flask zombie-prevention guards (3 os._exit guards)
- Market State Cache persistence (survives restarts)
- Analysis bot (parallel read-only mirror on :8001)
- Express Basic Auth + CSRF protection
- Raw body forwarding (TradingView text/plain safe)
- Production supervisor (prod-start.sh: Flask + Express + analysis bot)
- Health probe at `/api/healthz`
- Request logger with redaction (`_redact()`)
- DISCORD_LIVE_ENABLED gate (dev↔prod Discord isolation)
- Per-status poll cache (single-flight TTL, prevents 16s inline analysis)
- Curated endpoint serialization (whitelist, not jsonify dump)
- OPEN_PATHS set (/, /ping, /webhook never auth-gated)
- Flask zombie prevention (os._exit guards on SIGTERM + crash)

### In Progress
- Replit registry referrer manifest HTTP 500 (Replit infrastructure, ticket #481442)
- Left Brain obs-infra production deploy (pending publish unblock)

### Future
- Deployment runtime log integration (fetchDeploymentLogs returning errors)
- Prop firm account configuration UI
- Webhook latency monitoring (P50/P95 alert delivery time)
- Dashboard `_LAST_DECISION_TRACE` panel
- Dual-sim + micro-scalp ghost trade analytics UI
- Academy validation events population
- Full equity curve (weekly/monthly, not just today)
- Mobile-first dashboard breakpoints

---

*End of Platform Blueprint v1.0*
*Document produced July 2026 — read-only analysis, no code changes made.*
