# PHASE 7 — MAIN BRAIN ROUTING BRIEF
## V1-P7A: Data Routing and Route Inventory

**Status:** Documentation only — no production code changes  
**Date:** 2026-07-30  
**Branch:** polish-v1  
**HEAD at start:** 9986055 (V1-P6 Journal and Coach Separation)  
**Authorized change:** This document only  

---

## 1. Executive Summary

Phase 7A maps every field in the Main Brain visual target to its canonical owner
in the production codebase. The result is a complete routing plan that can be
implemented in a later phase without touching trading logic.

**Key findings:**

- The existing `/status` route already aggregates the majority of needed data.
  A new `/main-brain` route is justified for a structured, versioned payload but
  must read the same underlying sources without duplicating analysis.
- Coach v1 and Manager v1 are production-ready for direct inclusion.
- Expert (full_analysis) supplies verdict, edge score, strategy, and trade plan.
- Left Brain v2 supplies thesis, observations, and market intelligence.
- **5 strategies** are registered in the main STRATEGY_PRIORITY engine; a
  separate scalp research engine covers additional strategies for paper-sim only.
- A "Partner v1" interface is referenced in the controlling brief but **does not
  exist** as a builder function — this is a gap requiring a future decision.
- The Decision Timeline has partial coverage: thesis transitions are tracked
  (THESIS_TIMELINE_BY_INST, maxlen=25/instrument); other event types (order sent,
  trade closed, Journal written) are not collected into a unified timeline store.
- Performance data (win rate, avg R, sample count) is available via
  `compute_main_brain_learning_stats()` from the in-memory performance review cache.
- All controlling documents listed in the instruction brief
  (SYSTEM_ARCHITECTURE_V1.md, IMPLEMENTATION_ROADMAP_V1.md, etc.) are **not
  present** in the repository. This brief relies on direct codebase inspection.

---

## 2. Baseline State

| Item | Value |
|---|---|
| Branch | `polish-v1` |
| HEAD at start | `9986055` — V1-P6 Journal and Coach Separation |
| HEAD at end | `9986055` (unchanged — documentation-only phase) |
| git status | Only untracked: `attached_assets/Pasted--V1-PHASE-7A-...txt` |
| app.py | Unchanged |

**Recent commits:**
```
9986055  V1-P6 Journal and Coach Separation
6e7e5d4  Add 50-sample boundary tests for _recompute_learning_eligibility
6ef2171  Git commit prior to merge
19b5f26  V1-P6 pre-implementation execution brief
3ccc56f  Show thesis resolution history on Coach dashboard panel
91dc5cb  V1-P5 final compatibility audit — no production correction required
```

**Regression status (all pre-existing suites, unchanged by this phase):**
- test_phase6_journal_coach.py: 30/30 ✅
- test_v1_interface_versions.py: 92/92 ✅
- test_phase4_operator_explanation.py: 57/57 ✅
- test_phase3_thesis_verdict_pipeline.py: 60/60 ✅
- test_phase2_market_data_reliability.py: 45/45 ✅
- parity / scalp_golden / dual_sim / breakout_mode: all PASS ✅
- test_phase5_execution_safety.py: 137 OK / 41 pre-existing FAIL

---

## 3. Visual Target

The visual target (Main Brain UI design board) is treated as a layout and
presentation reference. Every field shown in the mockup must be classified
before implementation. No number may be hardcoded.

**Mockup sections identified:**
1. Header (greeting, market status, mode, time)
2. Market State strip (bias, regime, volatility, breadth, session)
3. Left Brain Thesis card (thesis, confidence, drivers, invalidation, age)
4. Verdict gauge (direction, edge score, grade, components, actionability)
5. Strategy Recommendation (selected strategy, entry/stop/targets, competing)
6. Active Trade card (instrument, direction, current R, management state)
7. Execution Status card (last signal, gateway outcome, broker response)
8. Coach card (weight_updated, thesis_resolved, win rate, latest lesson)
9. Decision Timeline (ordered events from analysis to close)
10. Live Feed / Alerts (recent alerts, observations, system warnings)
11. System Status (Databento, DB, broker, learning, AI)
12. Journal summary (recent trades, session totals)
13. Strategy Scanner page (all strategies ranked)

---

## 4. Current Dashboard Architecture

### Entry routes

| Path | Type | Auth | Description |
|---|---|---|---|
| `GET /` | Flask | Express Basic Auth | Operator Mode UI (main dashboard HTML) |
| `GET /dashboard` | Flask | Express Basic Auth | Full dashboard HTML |
| `GET /view` | Express-only | None (view-only link) | Read-only share link |
| `GET /status` | Flask | Express Basic Auth | Primary analysis payload (TTL-cached) |
| `GET /status?ticker=MNQ` | Flask | Express Basic Auth | Per-instrument analysis payload |

### Key analysis routes

| Path | Auth | Description |
|---|---|---|
| `GET /alerts` | Owner | Alert history deque snapshot |
| `GET /diagnostics` | Owner | Per-gate PASS/FAIL diagnostics |
| `GET /strategy-scan-diagnostics` | Owner | Strategy scan per instrument |
| `GET /lb-thesis` | Owner | Left Brain thesis per instrument |
| `GET /lb-thesis-obs` | Owner | Left Brain observations deque |
| `GET /lb-shadow-report` | Owner | Shadow MI report |
| `GET /lb-vwap-authority` | Owner | VWAP authority diagnostics |
| `GET /decision-trace` | Owner | Decision pipeline v2 trace |
| `GET /journal` | Owner | JOURNAL list (recent entries) |
| `GET /trade` | Owner | Active + managed trade snapshot |
| `GET /thesis` | Owner | Per-instrument thesis snapshot |
| `GET /thesis/<inst>/history` | Owner | Thesis timeline events |
| `GET /thesis/stats` | Owner | Thesis summary stats |
| `GET /eval-metrics` | Owner | Evaluation counters |
| `GET /right-brain` | Owner | Right Brain advisory |
| `GET /decision-quality` | Owner | Decision quality analytics |
| `GET /auto-trade-settings` | Owner | Auto-trade arm state map |
| `GET /training/status` | Owner | Bot training mode status |
| `GET /why` / `GET /why/<ticker>` | Owner | Human-readable gate explanation |

### Authentication boundary

Authentication is handled entirely in the **Express proxy** (`artifacts/api-server/`).
Flask has no `@login_required` decorator of its own. "Owner-only" means the route
is **not** in the Express `OPEN_PATHS` bypass list. The following paths bypass auth:
`/ping`, `/webhook`, `/healthz`, `/vrm`, and `/`.

A new `/main-brain` route should follow the same pattern: Express-proxied,
owner-required (not in OPEN_PATHS).

### Current /status payload (existing keys — partial list)

`_build_status_payload()` (line ~44498) serializes over 100 keys including:
`verdict`, `strict_label`, `strict_score`, `strict_direction`, `strict_reason`,
`strict_missing`, `gate_debug`, `edge_score` (via brain contract), `recommendation`,
`reasoning_chain`, `setup_stage`, `trade_plan`, `alert_diagnostics`, `volatility`,
`vwap_value`, `vwap_status`, `directions`, `confluences`, `market_direction`,
`main_brain`, `main_brain_predictions`, `main_brain_voice`, `main_brain_learning_stats`,
`thesis_tracker`, `market_intelligence`, `strategy_engine`, `analyst_report`,
`manager`, `coach`, `learning_engine`, `learning_rule_engine`, `decision_pipeline_v2`,
`breakout_mode`, `swing_v2`, `equity_curve_today`, `news_filter`, `dual_sim`,
`session_preferred`, `session_bonus`, `trade_quality`, `current_price`, `bullish_score`,
`bearish_score`, `market_structure`, `risk_zone`, `nearest_supply`, `nearest_demand`,
`active_zones`, `confidence_timeline`, `market_events_timeline`, `data_feed`.

The `/main-brain` route should consume from `full_analysis()` output (same as `/status`)
plus supplementary sources listed below. It must NOT re-invoke `full_analysis()` for
every sub-section; it calls it once and assembles the payload.

---

## 5. Canonical Interface Inventory

### 5.1 Expert v1 — `full_analysis()`

| Attribute | Value |
|---|---|
| Builder function | `full_analysis(current_price_override, ticker_override, cooldown_active)` |
| Line | ~22982 |
| Route exposure | Called internally by `_build_status_payload()` → exposed via `/status` |
| Primary output keys | `verdict`, `strict_label`, `strict_score`, `strict_direction`, `strict_reason`, `strict_missing`, `gate_debug`, `edge_score`, `edge_breakdown`, `trade_plan`, `recommendation`, `reasoning_chain`, `setup_stage`, `confluences`, `directions`, `learning_score_influence`, `volatility`, `market_direction`, `structure_label`, `risk_label`, `current_price`, `active_ticker`, `quality`, `bullish`, `bearish`, `analyst_report`, `main_brain`, `main_brain_predictions`, `main_brain_voice`, `main_brain_learning_stats`, `strategy_engine`, `manager` (via build_manager_interface), `coach` (via build_coach_interface), `_version` = "v1" |
| Current-state fields | verdict, edge_score, current_price, structure, risk, volatility, directions, confluences |
| Event fields | None (full_analysis is a pure analysis function, not an event recorder) |
| Timestamp fields | generated_at (in brain contract); freshness.price_last_valid_at |
| Absent behavior | Closed-market override runs last; all keys still present (closed_market flag added) |
| Malformed behavior | Each specialist engine is individually fail-open; full_analysis itself has outer try/except that mirrors all keys |
| Mutation protection | full_analysis reads caches only; ACTIVE_TRADES passed as shallow copy through build_manager_interface |
| Existing tests | test_phase3_thesis_verdict_pipeline.py, test_v1_interface_versions.py, all goldens |
| Include in Main Brain | YES — call once, pass result dict to all sub-builders |
| Adapter required | Thin selection: extract sub-keys by section |

### 5.2 Coach v1 — `build_coach_interface()`

| Attribute | Value |
|---|---|
| Builder function | `build_coach_interface(result, instrument=None, mode=None)` |
| Line | ~22873 |
| Route exposure | `result["coach"]` → serialized in `/status` |
| Schema | `weight_updated` (bool), `thesis_resolved` (bool), `thesis_last_resolved_at` (str ISO-8601 \| None), `learning_influence` (float ±15), `rule_engine_eligibility` ("GHOST_ONLY"\|"LIVE_ELIGIBLE"), `_version` = "v1" |
| Current-state fields | All six — reflect in-memory state at query time |
| Event fields | weight_updated (recompute happened), thesis_resolved (resolution happened this session) |
| Timestamp fields | thesis_last_resolved_at |
| Absent behavior | Returns neutral stubs on any internal exception: weight_updated=False, thesis_resolved=False, thesis_last_resolved_at=None, learning_influence=0.0, rule_engine_eligibility="LIVE_ELIGIBLE" |
| Malformed behavior | try/except wraps entire body; neutral stubs returned |
| Mutation protection | Only reads LEARNING_ANALYTICS under lock; LEARNING_ELIGIBILITY read-only; no writes |
| Existing tests | test_v1_interface_versions.py (92 tests), test_phase6_journal_coach.py (30 tests) |
| Include in Main Brain | YES — pass directly as `coach` key |
| Adapter required | NO — schema is already correct |

**Gaps for mockup Coach section:**
- `recent_performance` (win rate, avg R, sample) → available via `compute_main_brain_learning_stats()`; currently in `main_brain_learning_stats` field of `/status` but not nested under `coach`
- `current_advice` / `latest_lesson` → available in `main_brain_voice` (narrative text) and thesis_tracker block
- These need a thin adapter block

### 5.3 Manager v1 — `build_manager_interface()`

| Attribute | Value |
|---|---|
| Builder function | `build_manager_interface(result, instrument=None)` |
| Line | ~22800 |
| Route exposure | `result["manager"]` → serialized in `/status` |
| Schema | `gateway_debug` (dict: per-gate PASS/FAIL), `active_trade` (dict \| None, shallow copy), `managed_trade` (dict \| None, shallow copy), `training_gate` ({"enabled": bool}), `auto_trade_enabled` ({inst: bool}), `_version` = "v1" |
| Current-state fields | All — reflect live ACTIVE_TRADES_BY_INST, MANAGED_TRADES_BY_KEY |
| Event fields | None |
| Timestamp fields | active_trade.opened_at (if trade open) |
| Absent behavior | Neutral stubs on exception |
| Mutation protection | dict() shallow copies prevent mutation of global state |
| Existing tests | test_v1_interface_versions.py |
| Include in Main Brain | YES |
| Adapter required | Thin: expose active_trade fields as Active Trades section |

**Active trade dict structure** (ACTIVE_TRADES_BY_INST[inst]):
`opened_at`, `direction`, `entry` (or `entry_price`), `stop`, `tp1`/`tp2`/`targets`,
`size`/`contracts`, `strategy_key`, `instrument`, `source`, and per-gateway fields.
`current_price`, `unrealized_pnl`, `current_r` must be **derived** (not stored).

### 5.4 Left Brain v2 — `compute_left_brain_thesis()` + MI

| Attribute | Value |
|---|---|
| Builder functions | `compute_left_brain_thesis(inst)`, `compute_left_brain_mi()`, `compute_left_brain_obs()` |
| Route exposure | `/lb-thesis` (GET, owner-only); `/lb-thesis-obs` (GET); `result["market_intelligence"]` in `/status` |
| Schema (thesis) | `direction`, `strength`, `momentum`, `narrative`, `invalidation`, `playbook_reasoning`, `timeline`, `confidence`, `status`, `thesisId`, `lastUpdatedAt`, `reasonCodes` |
| Schema (MI) | `regime`, `primary_driver`, `risk_state`, `conviction`, `directional_confidence`, `is_ambiguous`, `futures_preference` |
| Schema (observations) | `_LB_THESIS_OBS_BY_INST[inst]` deque(maxlen=5000) of bar snapshots |
| Timestamp fields | `lastUpdatedAt` (thesis), `ts` (per observation) |
| Include in Main Brain | YES — `left_brain` key composing thesis + MI |
| Adapter required | YES — restructure from multiple source dicts into a single `left_brain` block |

### 5.5 Partner v1 — DOES NOT EXIST

**Finding:** No `build_partner_interface()` function exists in app.py. The concept
of a "Partner v1" interface is referenced in the V1 instruction brief but was never
implemented as a distinct builder.

**Resolution options:**
1. Treat the combination of `left_brain` + `market_intelligence` as the "Partner" data
2. Formally implement `build_partner_interface()` in a future phase as a thin adapter
3. Document as a gap and proceed with separate `left_brain` and `market_intelligence` keys

**Recommendation:** Option 3 for Phase 7. Add a follow-up task if a unified Partner
adapter is needed for the UI.

### 5.6 Journal v1 — `JOURNAL` list + `/journal` route

| Attribute | Value |
|---|---|
| Builder function | No builder; raw `JOURNAL` list (list of dicts, max 500, newest-first) |
| Route exposure | `GET /journal` (owner-only) |
| Schema (entry) | `id`, `datetime`, `symbol` (raw TV ticker), `direction`, `setup_stage`, `entry_price`, `stop_price`, `targets`, `edge_score`, `verdict`, `strict_score`, `strict_label`, `trade_strength`, `why_qualifies`, `setup_notes`, `session_preferred`, `session_bonus`, `confidence`, `next_step`, `volatility`, `decision_support`, `edge_breakdown`, `r_multiple` (if closed) |
| strategy_trades columns | `managed_key`, `journal_id`, `opened_at`, `closed_at`, `symbol`, `strategy_key`, `strategy`, `market_regime`, `session`, `direction`, `entry`, `stop`, `target`, `result`, `r_multiple`, `hold_minutes`, `confidence`, `quality`, `edge_score`, `mode`, `trading_mode`, `grade` |
| Current-state vs event | JOURNAL = current-session events (rebuilt from Postgres on boot); strategy_trades = persistent closed-trade records |
| Adapter for Main Brain | YES — need to query recent strategy_trades rows for "recent closed trades" in journal section |
| Note | symbol in strategy_trades is raw TV ticker (e.g. "MGC1!"); must canonicalize to "MGC" for display |

### 5.7 Execution Gateway v1 — `execute_trade_gateway()`

| Attribute | Value |
|---|---|
| Builder function | `execute_trade_gateway(instrument, contracts, source)` |
| Route exposure | Called internally by auto-trade/ENTER hooks; exposed via `/traderspost` (POST) |
| Execution modes | "manual_only", "paper", "traderspost", "pickmytrade" |
| Last-send state | `_TRADERSPOST_LAST` dict: `{instrument: (fingerprint, epoch_sent)}` |
| Status output | `{"status": "sent"|"simulated"|"manual_required"|"rejected"|..., "_version": "v1", "outcome": ...}` |
| Include in Main Brain | YES — read-only snapshot of last gateway state |
| Adapter required | YES — compose execution section from _TRADERSPOST_LAST + EXECUTION_MODE + last gateway result |
| Warning | No persistent last-gateway-result store. Only `_TRADERSPOST_LAST` (fingerprint + epoch) persists between calls; the full gateway response is not cached. |

---

## 6. Mockup Field Inventory

### 6.1 Header

| Field | Classification | Canonical Owner | Source |
|---|---|---|---|
| Operator greeting / name | AVAILABLE | `compute_main_brain_voice()` | `result["main_brain_voice"]["greeting"]` |
| AI summary message | AVAILABLE | `compute_main_brain_voice()` | `result["main_brain_voice"]["summary"]` or `narration` |
| Market open/closed | AVAILABLE | `market_session_status()` | `result["market_session"]` (in `/status` as `data_feed`) |
| Current ET time | DERIVABLE | Server clock | `datetime.now(ET_TZ).isoformat()` — derive at payload generation |
| Date | DERIVABLE | Server clock | Same as ET time |
| Trading mode (SCALP/SWING) | AVAILABLE | `TRADING_MODE` env var | `result["trading_mode"]` in `/status` |
| Auto-trade enabled | AVAILABLE | `build_manager_interface()` | `result["manager"]["auto_trade_enabled"][inst]` |
| Paper/live trading mode | AVAILABLE | `resolve_execution_mode()` | `EXECUTION_MODE` env → resolved per call |

### 6.2 Market State

| Field | Classification | Canonical Owner | Source |
|---|---|---|---|
| Overall bias | AVAILABLE | Expert / Left Brain | `result["market_direction"]` or `lb_thesis["direction"]` |
| Market regime | AVAILABLE | Market Intelligence | `result["market_intelligence"]["regime"]` |
| Trend strength | AVAILABLE | Left Brain MI | `result["market_intelligence"]["conviction"]` |
| Volatility state | AVAILABLE | Volatility monitor | `result["volatility"]["status"]` |
| Breadth | PARTIAL | MI + cross-market | `result["market_intelligence"]["directional_confidence"]` — labeled "breadth-like" but not true market breadth |
| Liquidity | PARTIAL | MI futures_preference | `result["market_intelligence"]["futures_preference"]` — qualitative only |
| News impact | AVAILABLE | Market Intelligence | `result["market_intelligence"]["primary_driver"]` + news_filter block |
| Selected instrument | AVAILABLE | Expert | `result["active_ticker"]` |
| Session | AVAILABLE | `market_session_status()` | `result["session"]` |

### 6.3 Left Brain Thesis

| Field | Classification | Canonical Owner | Source |
|---|---|---|---|
| Primary thesis | AVAILABLE | Left Brain v2 | `lb_thesis["narrative"]` |
| Key drivers | AVAILABLE | Left Brain v2 | `lb_thesis["playbook_reasoning"]` |
| Invalidation conditions | AVAILABLE | Left Brain v2 | `lb_thesis["invalidation"]` |
| Thesis confidence | AVAILABLE | Left Brain v2 | `lb_thesis["confidence"]` (int 0-100) |
| Thesis status | AVAILABLE | Left Brain v2 | `lb_thesis["status"]` (NEUTRAL/FORMING_LONG/etc.) |
| Thesis age | DERIVABLE | Left Brain v2 | `now - lb_thesis["lastUpdatedAt"]` — derive in payload builder |
| Thesis generated timestamp | AVAILABLE | Left Brain v2 | `lb_thesis["lastUpdatedAt"]` (ISO-8601 UTC) |
| Thesis last resolved timestamp | AVAILABLE | Coach v1 | `result["coach"]["thesis_last_resolved_at"]` |
| Supporting observations | AVAILABLE | Left Brain Obs | `_LB_THESIS_OBS_BY_INST[inst]` via `/lb-thesis-obs` |

### 6.4 Verdict

| Field | Classification | Canonical Owner | Source |
|---|---|---|---|
| Direction | AVAILABLE | Expert v1 | `result["strict_direction"]` |
| Confidence | AVAILABLE | Expert v1 | `result["strict_score"]` (0-100 equivalent) |
| Edge score | AVAILABLE | Expert v1 | `result["edge_score"]` (0-110) |
| Edge grade | AVAILABLE | Expert v1 | `result["edge_breakdown"]["grade"]` |
| Actionability | AVAILABLE | Expert v1 | `result["verdict"]` → is_actionable derivable |
| Readiness | AVAILABLE | Expert v1 | `result["strict_label"]` ("READY"/"WAIT") |
| Failed conditions | AVAILABLE | Expert v1 | `result["strict_missing"]`, `result["gate_debug"]` |
| Trend component | PARTIAL | Edge breakdown | `result["edge_breakdown"]["components"]["BOS20"]` or `CHOCH20` |
| Momentum component | PARTIAL | Edge breakdown | `result["edge_breakdown"]["components"]["Sweep15"]` |
| Breadth component | PARTIAL | Edge breakdown | `result["edge_breakdown"]["components"]["CVD15"]` |
| Volume component | AVAILABLE | Edge breakdown | `result["edge_breakdown"]["components"]["Volume15"]` |
| Volatility component | AVAILABLE | Volatility monitor | `result["volatility"]["status"]` + vol score in edge |
| Risk/reward | AVAILABLE | Expert v1 | `result["trade_plan"]["rr"]` (from rr_num) |

**Note:** The 7 edge components (BOS20, CHOCH20, VWAP15, Sweep15, Volume15, CVD15, Session10)
sum to max 110. They are NOT named "trend/momentum/breadth/volume/volatility" in the codebase.
The mockup labels must be mapped to actual components — do not fabricate new score breakdowns.

### 6.5 Strategy Recommendation

| Field | Classification | Canonical Owner | Source |
|---|---|---|---|
| Selected strategy | AVAILABLE | Strategy engine | `result["strategy_engine"]["selected"]` or `result["recommendation"]` |
| Selected setup | AVAILABLE UNDER DIFFERENT NAME | Expert v1 | `result["setup_stage"]` or strategy_engine["setup"] |
| Entry | AVAILABLE | Expert v1 | `result["trade_plan"]["entry"]` |
| Stop | AVAILABLE | Expert v1 | `result["trade_plan"]["stop"]` |
| Targets | AVAILABLE | Expert v1 | `result["trade_plan"]["targets"]` or tp1/tp2/tp3 |
| Timeframe | AVAILABLE UNDER DIFFERENT NAME | Expert v1 | `TRADING_MODE` (SCALP=5m, SWING=1H) |
| Risk/reward | AVAILABLE | Expert v1 | `result["trade_plan"]["rr"]` |
| Reason selected | AVAILABLE | Strategy engine | `result["strategy_engine"]["rationale"]` or reasoning_chain |
| Competing strategies | AVAILABLE | Strategy scan diag | `STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER[inst]["strategies"]` |
| Strategy rankings | AVAILABLE | Strategy scan diag | Same source, ordered by score |
| Readiness | AVAILABLE | Expert v1 | `result["strict_label"]` |
| Historical expectancy | AVAILABLE | Learning analytics | `LEARNING_ANALYTICS["top_strategy"]` + per-strategy stats |
| Sample count | AVAILABLE | Learning analytics | `PER_MODE_STATS[mode]["n"]` |
| Learning influence | AVAILABLE | Coach v1 | `result["coach"]["learning_influence"]` |

### 6.6 Active Trades

| Field | Classification | Canonical Owner | Source |
|---|---|---|---|
| Instrument | AVAILABLE | Manager v1 | `result["manager"]["active_trade"]["instrument"]` |
| Direction | AVAILABLE | Manager v1 | `result["manager"]["active_trade"]["direction"]` |
| Strategy | AVAILABLE | Manager v1 | `result["manager"]["active_trade"]["strategy_key"]` |
| Opened time | AVAILABLE | Manager v1 | `result["manager"]["active_trade"]["opened_at"]` |
| Entry | AVAILABLE | Manager v1 | `result["manager"]["active_trade"]["entry"]` |
| Current price | AVAILABLE | Expert v1 | `result["current_price"]` |
| Quantity | AVAILABLE | Manager v1 | `result["manager"]["active_trade"]["contracts"]` or `size` |
| Position size ($) | DERIVABLE | Expert + trade | `contracts × point_value × current_price` — derive in builder |
| Unrealized P&L | DERIVABLE | Expert + trade | `(current_price - entry) × direction_sign × point_value × contracts` |
| Current R | DERIVABLE | Expert + trade | `(current_price - entry) / (entry - stop) × direction_sign` |
| Stop | AVAILABLE | Manager v1 | `result["manager"]["active_trade"]["stop"]` |
| Targets | AVAILABLE | Manager v1 | `result["manager"]["active_trade"]["tp1"]` / `tp2` |
| Management state | AVAILABLE | Manager v1 | `result["manager"]["managed_trade"]` (None if unmanaged) |
| Trade identifier | AVAILABLE | Manager v1 | `result["manager"]["active_trade"]["order_id"]` or source tag |

### 6.7 Execution Status

| Field | Classification | Canonical Owner | Source |
|---|---|---|---|
| Last signal | PARTIAL | Alert history | `ALERT_HISTORY[-1]` — most recent alert dict |
| Instrument | AVAILABLE | Last alert | `ALERT_HISTORY[-1]["ticker"]` |
| Action | PARTIAL | Last gateway | `_TRADERSPOST_LAST[inst]` — fingerprint only; full action not cached |
| Contracts | PARTIAL | Last gateway | Not persisted after gateway call; would need a new last-result cache |
| Gateway status | AVAILABLE | Manager v1 | `result["manager"]["gateway_debug"]` (per-gate PASS/FAIL) |
| Gateway outcome | MISSING | Execution gateway | **No persistent last-gateway-result store.** Only fingerprint+epoch in _TRADERSPOST_LAST. Needs a new `_LAST_GATEWAY_RESULT` dict per instrument |
| Broker response | MISSING | Execution gateway | Same gap — not stored after the HTTP call |
| Broker order ID | MISSING | Broker/TradersPost | Not stored in any in-memory structure after the response |
| Execution mode | AVAILABLE | Gateway | `resolve_execution_mode()` → EXECUTION_MODE env |
| Duplicate suppression | AVAILABLE | Gateway | `_TRADERSPOST_LAST[inst]` — (fingerprint, epoch_sent) |
| Verification required | PARTIAL | Broker payload guard | `broker_verify_required` — present in gateway result dict, not cached |
| Failure reason | MISSING | Execution gateway | Not cached after call |

### 6.8 Coach

| Field | Classification | Canonical Owner | Source |
|---|---|---|---|
| weight_updated | AVAILABLE | Coach v1 | `result["coach"]["weight_updated"]` |
| thesis_resolved | AVAILABLE | Coach v1 | `result["coach"]["thesis_resolved"]` |
| thesis_last_resolved_at | AVAILABLE | Coach v1 | `result["coach"]["thesis_last_resolved_at"]` |
| learning_influence | AVAILABLE | Coach v1 | `result["coach"]["learning_influence"]` |
| rule_engine_eligibility | AVAILABLE | Coach v1 | `result["coach"]["rule_engine_eligibility"]` |
| Recent performance (win rate) | AVAILABLE | Learning stats | `compute_main_brain_learning_stats()["win_rate"]` |
| Average R | AVAILABLE | Learning stats | `compute_main_brain_learning_stats()["avg_r"]` |
| Sample size | AVAILABLE | Learning stats | `compute_main_brain_learning_stats()["sample"]` |
| Current advice | AVAILABLE UNDER DIFFERENT NAME | Main Brain Voice | `result["main_brain_voice"]` narrative/advice text |
| Latest lesson | AVAILABLE UNDER DIFFERENT NAME | Thesis tracker / Learning | `result["thesis_tracker"]["lesson"]` or common_loss pattern |
| Coach version | AVAILABLE | Coach v1 | `result["coach"]["_version"]` = "v1" |

### 6.9 Journal

| Field | Classification | Canonical Owner | Source |
|---|---|---|---|
| Recent closed trades | AVAILABLE | strategy_trades table | SQL: `SELECT … FROM strategy_trades WHERE closed_at IS NOT NULL ORDER BY closed_at DESC LIMIT 10` |
| Per trade: instrument | AVAILABLE | strategy_trades | `symbol` column (must canonicalize from "MGC1!" → "MGC") |
| Per trade: strategy | AVAILABLE | strategy_trades | `strategy_key` column |
| Per trade: direction | AVAILABLE | strategy_trades | `direction` column |
| Per trade: R multiple | AVAILABLE | strategy_trades | `r_multiple` column |
| Per trade: result | AVAILABLE | strategy_trades | `result` column (WIN/LOSS/BE) |
| Coach grade | AVAILABLE UNDER DIFFERENT NAME | strategy_trades | `grade` column (A+/A/B/WAIT) |
| Thesis reference | PARTIAL | strategy_trades | `journal_id` links to JOURNAL entry; no direct thesis_id stored |
| Opened time | AVAILABLE | strategy_trades | `opened_at` column |
| Closed time | AVAILABLE | strategy_trades | `closed_at` column |
| Session summary: total trades | AVAILABLE | JOURNAL list | `len([e for e in JOURNAL if today])` |
| Session summary: win rate | AVAILABLE | JOURNAL + stats | `_trading_stats_today()` |
| Session summary: average R | AVAILABLE | JOURNAL + stats | `_trading_stats_today()["avg_r"]` equivalent |
| Session summary: total R | AVAILABLE | JOURNAL + stats | `_trading_stats_today()["net_r"]` equivalent |

### 6.10 Decision Timeline

See Section 9 (Decision Timeline Audit) for the full event-by-event analysis.

Summary: thesis transitions are **available** (THESIS_TIMELINE_BY_INST, maxlen=25);
all other events are **MISSING** from a unified timeline store.

### 6.11 System Status

| Field | Classification | Canonical Owner | Source |
|---|---|---|---|
| Databento connectivity | AVAILABLE | Databento status | `DATABENTO_ENABLED` + `GET /databento-status` |
| Database health | AVAILABLE | DB readiness flags | `LEARNING_DB_ENABLED`, `ACTIVE_TRADES_DB_READY`, `MARKET_STATE_CACHE_DB_READY` |
| Broker connectivity | PARTIAL | TradersPost probe | `TRADERSPOST_WEBHOOK_URL` presence; no live probe result cached |
| AI subsystem availability | AVAILABLE | Learning analytics | `LEARNING_ANALYTICS["enabled"]` + `["ready"]` |
| Learning availability | AVAILABLE | Learning analytics | `LEARNING_DB_ENABLED` + `LEARNING_ANALYTICS["ready"]` |
| Latest update time | AVAILABLE | Status payload | `_now_ts` in `_build_status_payload` |
| Stale-data status | AVAILABLE | Expert v1 | `result["data_feed"]["freshness"]` or price_age_seconds |
| Authentication status | AVAILABLE | Express proxy | N/A from Flask; Express handles Basic Auth |

---

## 7. Field Provenance Table

Selected critical fields. Full table covers all fields from Sections 6.1–6.11.

| JSON Path | UI Panel | Canonical Owner | Source Function | Source JSON Path | Type | Nullable | Transformation | Side Effects | Failure Fallback |
|---|---|---|---|---|---|---|---|---|---|
| `market.session.open` | Header | market_session_status() | market_session_status() | `["open"]` | bool | No | None | None | false (fail-open) |
| `market.session.name` | Header | market_session_status() | market_session_status() | `["session"]` | str | Yes | None | None | null |
| `market.selected_instrument` | Header | Expert v1 | full_analysis() | `["active_ticker"]` | str | No | None | None | "MGC" |
| `market_state.regime` | Market State | Market Intelligence | result["market_intelligence"] | `["regime"]` | str | Yes | None | None | "UNKNOWN" |
| `market_state.volatility` | Market State | Volatility monitor | result["volatility"] | `["status"]` | str | Yes | None | None | "UNKNOWN" |
| `market_state.bias` | Market State | Expert v1 | result | `["market_direction"]` | str | Yes | None | None | "NEUTRAL" |
| `left_brain.thesis.narrative` | Thesis card | Left Brain v2 | lb_thesis_snapshot(inst) | `["narrative"]` | str | Yes | None | None | null |
| `left_brain.thesis.confidence` | Thesis card | Left Brain v2 | lb_thesis_snapshot(inst) | `["confidence"]` | int | Yes | None | None | 0 |
| `left_brain.thesis.status` | Thesis card | Left Brain v2 | lb_thesis_snapshot(inst) | `["status"]` | str | Yes | None | None | "NEUTRAL" |
| `left_brain.thesis.age_seconds` | Thesis card | Derived | now - lb_thesis["lastUpdatedAt"] | — | int | Yes | Date arithmetic | None | null |
| `verdict.direction` | Verdict gauge | Expert v1 | full_analysis() | `["strict_direction"]` | str | Yes | None | None | "NEUTRAL" |
| `verdict.edge_score` | Verdict gauge | Expert v1 | full_analysis() | `["edge_score"]` | int | No | None | None | 0 |
| `verdict.grade` | Verdict gauge | Expert v1 | full_analysis() | `["edge_breakdown"]["grade"]` | str | Yes | None | None | "WAIT" |
| `verdict.readiness` | Verdict gauge | Expert v1 | full_analysis() | `["strict_label"]` | str | No | None | None | "WAIT" |
| `verdict.failed_conditions` | Verdict gauge | Expert v1 | full_analysis() | `["strict_missing"]` | list | Yes | None | None | [] |
| `verdict.components` | Verdict gauge | Expert v1 | full_analysis() | `["edge_breakdown"]["components"]` | dict | Yes | None | None | {} |
| `strategy.selected` | Strategy card | Strategy engine | result["strategy_engine"] | `["selected"]` | str | Yes | None | None | null |
| `strategy.trade_plan` | Strategy card | Expert v1 | full_analysis() | `["trade_plan"]` | dict | Yes | None | None | {} |
| `strategy.learning_influence` | Strategy card | Coach v1 | build_coach_interface() | `["learning_influence"]` | float | No | None | None | 0.0 |
| `active_trades[0].direction` | Active Trade | Manager v1 | build_manager_interface() | `["active_trade"]["direction"]` | str | Yes | None | None | null |
| `active_trades[0].current_r` | Active Trade | Derived | current_price, entry, stop | — | float | Yes | (price-entry)/(entry-stop) | None | null |
| `active_trades[0].unrealized_pnl` | Active Trade | Derived | current_price, entry, contracts | — | float | Yes | math | None | null |
| `execution_gateway.mode` | Execution card | Gateway | resolve_execution_mode() | — | str | No | None | None | "manual_only" |
| `execution_gateway.last_sent_epoch` | Execution card | _TRADERSPOST_LAST | (fingerprint, epoch) | `[1]` | float | Yes | epoch → ISO | None | null |
| `execution_gateway.last_outcome` | Execution card | **MISSING** | No persistent store | — | str | Yes | — | — | null |
| `coach.weight_updated` | Coach card | Coach v1 | build_coach_interface() | `["weight_updated"]` | bool | No | None | None | false |
| `coach.thesis_last_resolved_at` | Coach card | Coach v1 | build_coach_interface() | `["thesis_last_resolved_at"]` | str\|null | Yes | None | None | null |
| `coach.win_rate` | Coach card | Learning stats | compute_main_brain_learning_stats() | `["win_rate"]` | float | Yes | None | None | null |
| `coach.avg_r` | Coach card | Learning stats | compute_main_brain_learning_stats() | `["avg_r"]` | float | Yes | None | None | null |
| `journal.recent_trades` | Journal | strategy_trades | SQL SELECT | — | list | No | canonicalize symbol | DB read | [] |
| `journal.summary.win_rate` | Journal | JOURNAL + stats | _trading_stats_today() | — | float | Yes | None | None | null |
| `decision_timeline` | Timeline | THESIS_TIMELINE_BY_INST | list(deque) | — | list | No | format event dicts | None | [] |
| `system_status.learning_ready` | System | Learning analytics | LEARNING_ANALYTICS | `["ready"]` | bool | No | None | None | false |
| `system_status.databento_enabled` | System | Databento flag | DATABENTO_ENABLED | — | bool | No | None | None | false |

---

## 8. Strategy Scanner Audit

### Registered strategies (main engine)

The `STRATEGY_PRIORITY` list (line ~9221) defines **5 strategies** in priority order:

| # | Key | Label | Target R | Max Grade | Regimes |
|---|---|---|---|---|---|
| 1 | OPENING_DRIVE | Opening Drive | 2.0 | A+ | TRENDING, VOLATILE, BALANCED |
| 2 | LIQUIDITY_SWEEP_REVERSAL | Liquidity Sweep Reversal | 2.0 | A+ | VOLATILE, RANGING, BALANCED |
| 3 | VWAP_TREND_CONTINUATION | VWAP Trend Continuation | 1.8 | A+ | TRENDING, BALANCED |
| 4 | RANGE_EXPANSION_BREAKOUT | Range Expansion Breakout | 2.0 | A (capped) | RANGING, BALANCED |
| 5 | OPENING_RANGE_BREAKOUT | Opening Range Breakout | 4.0 | A (capped) | TRENDING, VOLATILE, BALANCED |

**Note:** ORB has target_r=4.0 (1:4 R:R) per the ORB strategy.

### Scalp research strategies (paper-sim only)

A separate scalp research engine runs **16 additional strategy configurations**
(live_status ∈ {watch, simulation, recommended}) via `/scalp-research`. These
are **RESEARCH AND PAPER-SIM ONLY** — completely walled off from the money path.
They are not included in the main scanner ranking.

### Scanner coverage per instrument

`STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER[inst]` is updated on every webhook and stores:
- `strategies`: list of scored strategy dicts (all 5, score and rationale)
- `selected`: the chosen strategy key
- `regime`: the detected regime used for selection
- `missing`: conditions preventing a strategy from qualifying
- `timestamp`: snapshot time

All 5 strategies are evaluated for all enabled instruments (MGC, MNQ, MES, MYM).

### Strategies excluded by mode

- ORB (`OPENING_RANGE_BREAKOUT`) has an eligibility gate: only outside session (pre-NY open). During NY session it does not score.
- OPENING_DRIVE is priority-1 but requires opening-drive conditions (early NY session, price expansion).

### Route exposure

`GET /strategy-scan-diagnostics` (owner-only) returns the full snapshot.
For the Main Brain scanner page, this is the canonical source.

### Read-only representation proposed

No selection or ranking logic changes. Expose `STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER[inst]`
as `strategy_scanner.ranked_strategies` in the Main Brain payload. The UI renders
the pre-computed ranking; no re-scoring happens at render time.

---

## 9. Decision Timeline Audit

For each event type, whether it exists as a real event, current-state inference, or not at all:

| Event | Canonical Source | Timestamp | Persistence | Real Event? | Notes |
|---|---|---|---|---|---|
| Market data received | `ALERT_HISTORY` (webhook arrival) | Each entry's timestamp | In-memory deque only | YES — each webhook is an event | maxlen=1000; resets on restart |
| Thesis generated | `THESIS_TIMELINE_BY_INST` | `event["ts"]` | In-memory only | YES — transition recorded | maxlen=25/inst; resets on restart |
| Thesis updated | `THESIS_TIMELINE_BY_INST` | `event["ts"]` | In-memory only | YES | Same deque |
| Thesis resolved | `_THESIS_LAST_RESOLVED_AT` | ISO-8601 UTC | In-memory only | YES — set at resolution | Single timestamp, not a full event |
| Verdict generated | None | None | None | NO — inference only | Verdict is computed on every poll; no event stored |
| Strategy selected | `STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER` | snapshot timestamp | In-memory only | INFERENCE | Reflects last webhook scan |
| Signal generated | `ALERT_HISTORY` last READY entry | Entry timestamp | In-memory deque | YES — READY alert is an event | Not labeled as "signal" event type |
| Gateway decision | None | None | None | NO | Only fingerprint+epoch in _TRADERSPOST_LAST |
| Order sent | `_TRADERSPOST_LAST[inst][1]` (epoch) | epoch_sent | In-memory only | PARTIAL | epoch only; no full order event |
| Order accepted | None | None | None | NO | Broker response not cached |
| Trade managed | `MANAGED_TRADES_BY_KEY` | managed trade metadata | In-memory + DB | PARTIAL | Managed trade dict has timestamps |
| Trade closed | `strategy_trades` DB | `closed_at` column | Postgres | YES — persistent | Best historical record |
| Journal written | `JOURNAL` list | `datetime` field | In-memory (rebuilt from Postgres) | YES | Available but mixed with forming/setup entries |
| Coach updated | `LEARNING_ANALYTICS["updated_at"]` | ISO-8601 UTC | In-memory only | YES — set at recompute | Not per-trade, per-session |

### Gap assessment

A true event-stream timeline does not currently exist. The Decision Timeline panel
in the mockup requires either:

1. **Phase 7B option (recommended):** A `_decision_event_log` deque per instrument
   that receives a single typed event dict from each production seam — without
   changing what those seams do. This is an additive observer, not a logic change.
2. **Phase 7A fallback (current):** Reconstruct a partial timeline from the existing
   snapshots, clearly labeled as "derived" — specifically:
   - Thesis events from THESIS_TIMELINE_BY_INST (real)
   - Last READY alert from ALERT_HISTORY (real)
   - Last gateway epoch from _TRADERSPOST_LAST (real timestamp, partial data)
   - Trade closed from strategy_trades (real, requires DB query)
   
   This fallback is honest but sparse.

**Recommendation:** Implement the full event log as Phase 7B. For Phase 7A, expose
the partial/derived timeline with clear `event_type` and `is_derived: true` flags.

---

## 10. Missing-Field Register

| Field | Panel | Why Missing | Closest Source | Safe to Derive? | New Logic Required? | Recommended Phase | Placeholder |
|---|---|---|---|---|---|---|---|
| Gateway outcome (status) | Execution | _TRADERSPOST_LAST stores only fingerprint+epoch; outcome not cached | execute_trade_gateway() return value | YES — add `_LAST_GATEWAY_RESULT_BY_INST` dict | No logic change, only cache the result | Phase 7B | null |
| Broker response / order ID | Execution | HTTP response not stored after call | execute_trade_gateway() internal | YES — cache sanitized response | No; requires a new write inside gateway | Phase 7B | null |
| Full event timeline | Timeline | No unified event log store | THESIS_TIMELINE_BY_INST + others | PARTIAL (derived) | Yes for complete coverage | Phase 7B | derived partial |
| Verdict generated event | Timeline | Verdict is computed on demand; no event fired | full_analysis() call | NO — would need an event emitter | Additive observer (minimal) | Phase 7B | omit |
| True market breadth | Market State | No index breadth data ingested | cross-market alignment (DISPLAY only) | NO — only 4 instruments | Requires data sourcing | Deferred | MI directional_confidence |
| True market liquidity | Market State | No liquidity data (order book, bid/ask) | futures_preference (qualitative) | NO | Requires data sourcing | Deferred | null |
| Unrealized P&L in $ | Active Trade | Entry/current price available but formula not cached | current_price + trade.entry + contracts | YES — derive in builder | No | Phase 7A payload builder | null |
| Position size in $ | Active Trade | Same as P&L derivation | Same | YES | No | Phase 7A payload builder | null |
| Current R | Active Trade | Same | Same | YES | No | Phase 7A payload builder | null |
| Partner v1 interface | All sections | Builder function does not exist | Left Brain + MI + Expert | N/A — not a data gap, an interface gap | No — use existing builders | Phase 7B if needed | N/A |
| Historical expectancy per strategy | Strategy | LEARNING_ANALYTICS has best/top strategy but not per-strategy win rates | LEARNING_ANALYTICS["ranking"] | PARTIAL | No | Phase 7A | top-strategy only |
| Thesis reference in Journal | Journal | strategy_trades.journal_id links to JOURNAL but no thesis_id | journal_id → entry lookup | YES but requires join | No | Phase 7B | null |
| Broker connectivity probe result | System Status | No live probe result stored | TRADERSPOST_WEBHOOK_URL presence check | PARTIAL (URL presence only) | No | Phase 7A | "configured" or null |

---

## 11. Proposed Main Brain Schema

```json
{
  "_version": "v1",
  "generated_at": "2026-07-30T12:00:00Z",

  "market": {
    "session": {
      "open": true,
      "name": "New York",
      "next_event": null
    },
    "selected_instrument": "MGC",
    "trading_mode": "SCALP",
    "execution_mode": "paper",
    "et_time": "2026-07-30T08:30:00",
    "auto_trade_enabled": {"MGC": true, "MNQ": false, "MES": false, "MYM": false}
  },

  "market_state": {
    "bias": "Bullish",
    "regime": "TRENDING",
    "primary_driver": "FED_DRIVEN",
    "risk_state": "RISK_ON",
    "conviction": "HIGH",
    "volatility_status": "NORMAL",
    "volatility_atr": null,
    "session": "NY",
    "news_impact": "MODERATE",
    "vwap_value": 2450.0,
    "vwap_status": "ok"
  },

  "left_brain": {
    "thesis": {
      "direction": "Long",
      "status": "READY_LONG",
      "narrative": "...",
      "key_drivers": "...",
      "invalidation": "...",
      "confidence": 78,
      "strength": "HIGH",
      "age_seconds": 1800,
      "generated_at": "2026-07-30T10:30:00Z",
      "last_resolved_at": null,
      "thesis_id": "uuid..."
    },
    "intelligence": {
      "regime": "TRENDING",
      "primary_driver": "FED_DRIVEN",
      "risk_state": "RISK_ON",
      "directional_confidence": 72,
      "futures_preference": {}
    },
    "observations_count": 47,
    "available": true
  },

  "verdict": {
    "direction": "Long",
    "readiness": "READY",
    "edge_score": 85,
    "edge_max": 110,
    "grade": "A+",
    "is_actionable": true,
    "confidence_score": 85,
    "strict_reason": null,
    "failed_conditions": [],
    "risk_reward": "1:3",
    "components": {
      "BOS20": 20,
      "CHOCH20": 20,
      "VWAP15": 15,
      "Sweep15": 15,
      "Volume15": 15,
      "CVD15": 0,
      "Session10": 0
    }
  },

  "strategy_scanner": {
    "selected": "LIQUIDITY_SWEEP_REVERSAL",
    "selected_label": "Liquidity Sweep Reversal",
    "entry": 2450.0,
    "stop": 2445.0,
    "targets": [2457.5, 2465.0, 2472.5],
    "risk_reward": "1:3",
    "reason": "...",
    "learning_influence": 5.0,
    "ranked_strategies": [
      {
        "key": "LIQUIDITY_SWEEP_REVERSAL",
        "label": "Liquidity Sweep Reversal",
        "score": 85,
        "selected": true,
        "regime_fit": true,
        "conditions_met": ["structure", "sweep", "vwap"]
      }
    ],
    "sample_count": 47,
    "historical_expectancy": null
  },

  "active_trades": [
    {
      "instrument": "MGC",
      "direction": "Long",
      "strategy_key": "MGC_SCALP_CHOCH_Long",
      "opened_at": "2026-07-30T09:15:00Z",
      "entry": 2450.0,
      "stop": 2445.0,
      "targets": [2457.5],
      "current_price": 2453.0,
      "contracts": 1,
      "current_r": 0.6,
      "unrealized_pnl": 30.0,
      "management_state": "OPEN",
      "order_id": null
    }
  ],

  "manager": {
    "gateway_debug": {},
    "training_gate": {"enabled": false},
    "auto_trade_enabled": {},
    "_version": "v1"
  },

  "execution_gateway": {
    "mode": "paper",
    "last_sent_at": null,
    "last_outcome": null,
    "last_instrument": null,
    "last_action": null,
    "duplicate_window_active": false,
    "_gap": "last_outcome not yet persisted — Phase 7B"
  },

  "coach": {
    "weight_updated": false,
    "thesis_resolved": false,
    "thesis_last_resolved_at": null,
    "learning_influence": 0.0,
    "rule_engine_eligibility": "LIVE_ELIGIBLE",
    "win_rate": null,
    "avg_r": null,
    "sample": 0,
    "losing_pattern": null,
    "best_setup": null,
    "_version": "v1"
  },

  "journal": {
    "recent_trades": [
      {
        "symbol": "MGC",
        "direction": "Long",
        "strategy": "CHOCH",
        "r_multiple": 1.5,
        "result": "WIN",
        "grade": "A",
        "opened_at": "...",
        "closed_at": "...",
        "session": "NY"
      }
    ],
    "summary": {
      "total_trades": 3,
      "wins": 2,
      "losses": 1,
      "win_rate": 0.667,
      "avg_r": 1.0,
      "total_r": 2.0
    },
    "available": true
  },

  "performance": {
    "win_rate": null,
    "avg_r": null,
    "sample": 0,
    "best_setup": null,
    "best_window": null,
    "losing_pattern": null,
    "available": false
  },

  "decision_timeline": [
    {
      "event_type": "THESIS_TRANSITION",
      "ts": "2026-07-30T09:00:00Z",
      "label": "Thesis READY_LONG",
      "details": {"direction": "Long", "confidence": 78},
      "is_derived": false,
      "persisted": false
    },
    {
      "event_type": "READY_SIGNAL",
      "ts": "2026-07-30T09:10:00Z",
      "label": "READY alert",
      "details": {},
      "is_derived": true,
      "persisted": false
    }
  ],

  "alerts": [
    {
      "ts": "...",
      "ticker": "MGC",
      "alert_type": "structure",
      "direction": "Long",
      "verdict": "WAIT"
    }
  ],

  "system_status": {
    "databento_enabled": false,
    "databento_connected": false,
    "database_ready": true,
    "learning_ready": true,
    "active_trades_db_ready": false,
    "broker_url_configured": true,
    "price_fresh": true,
    "price_age_seconds": 30,
    "last_analysis_at": "2026-07-30T09:30:00Z"
  },

  "availability": {
    "left_brain": {"available": true, "stale": false},
    "verdict": {"available": true, "stale": false},
    "strategy_scanner": {"available": true, "stale": false},
    "active_trades": {"available": true},
    "execution_gateway": {"available": true},
    "coach": {"available": true},
    "journal": {"available": true, "error": null},
    "performance": {"available": false, "reason": "insufficient_history"},
    "timeline": {"available": true, "partial": true}
  },

  "errors": []
}
```

---

## 12. Availability and Error Contract

### Fault-isolation rules

1. A failure in the Coach block must not prevent the Verdict from being returned.
2. A failure in the Journal DB query must not prevent any other block from returning.
3. A failure in Left Brain thesis must return `left_brain: null` with `availability.left_brain.available = false`.
4. No subsystem failure may produce an HTTP 500. All exceptions are caught and logged.
5. A stale price does not prevent the payload from being returned; `system_status.price_fresh = false` signals it.

### Availability entry schema

```
"availability": {
  "<section>": {
    "available": bool,       -- false if the section's primary source raised
    "stale":     bool,       -- true if data age exceeds section threshold
    "error":     str | null, -- machine-readable error code (never stack trace)
    "updated_at": str | null -- ISO-8601 UTC of last successful read
  }
}
```

### Required fault-isolation tests (Section 19)

- V1-P7-FI-001: Journal DB unavailable → journal block returns `{"recent_trades": [], "available": false}`, HTTP 200
- V1-P7-FI-002: Left Brain thesis absent → `left_brain.thesis = null`, `availability.left_brain.available = false`
- V1-P7-FI-003: Coach internal exception → neutral stubs, HTTP 200
- V1-P7-FI-004: Strategy scanner empty → `strategy_scanner.ranked_strategies = []`, HTTP 200
- V1-P7-FI-005: full_analysis() raises → fallback Expert block returned, errors list populated, HTTP 200
- V1-P7-FI-006: All subsystems healthy → no entries in `errors` list
- V1-P7-FI-007: Performance data unavailable → `performance.available = false`, not omitted

---

## 13. API Design

### Recommended route

```
GET /main-brain
GET /main-brain?ticker=MNQ
```

Consistent with existing instrument-scoped routes (`/status?ticker=`).

### Authentication

- Express Basic Auth (same as `/status`, `/dashboard`)
- Not in OPEN_PATHS
- Same-origin CSRF protection (XFH/Host proxy-trusted, `same-origin` Referrer Policy)
- View-only `/view` link does NOT expose `/main-brain` (it proxies only specific paths)

### Cache behavior

- TTL cache identical to `/status` mechanism (`STATUS_CACHE_TTL_SEC`)
- Cache keyed on `(mode_override, ticker)` — same pattern as `/status`
- Single-flight builder guard: if rebuild in flight and stale cache exists, serve stale
- Cold-cache + build in flight → HTTP 503 with `{"status": "warming"}`

### Response schema

```
HTTP 200: application/json — full Main Brain payload
HTTP 503: {"status": "warming", "detail": "analysis warming up — retry shortly"}
HTTP 500: should never occur (all subsystems fail-open)
```

### Versioning

`_version: "v1"` in payload root. Breaking schema changes require a new version.

### Polling vs event stream

- **Polling** recommended: consistent with existing `/status` (3-second client poll)
- Event stream (SSE/WebSocket) is a Phase 7C+ consideration requiring architecture work
- Recommended polling interval: 3s on main view, 30s on background tabs

### Payload size

Current `/status` response is large (~50-100KB JSON). The Main Brain payload should:
- Stay under 200KB uncompressed
- Use `availability` flags to omit heavy optional blocks when unavailable
- Support `?lite=true` query param for mobile (omit observations, full timeline, raw gate_debug)

### Timeout

- Server-side: 8s (consistent with existing Flask timeout patterns)
- If full_analysis exceeds timeout, serve cached + `system_status.stale = true`

### Error model

```json
{
  "errors": [
    {
      "source": "journal",
      "code": "db_query_failed",
      "recoverable": true,
      "ts": "2026-07-30T09:30:00Z"
    }
  ]
}
```

---

## 14. Authentication and Security

### Boundary summary

| Layer | Mechanism | Owner |
|---|---|---|
| External → Replit proxy | HTTPS/mTLS | Replit |
| Replit proxy → Express | Internal | Replit |
| Express → Flask | Local HTTP (same host) | api-server artifact |
| Express Auth | Basic Auth (DASHBOARD_PASSWORD) | Express middleware |
| Flask "owner-only" | Path not in OPEN_PATHS → Express blocks unauthenticated | Convention |
| CSRF | XFH/Host proxy-trusted, `same-origin` Referrer Policy | Express |

### `/main-brain` security requirements

1. Must NOT be in OPEN_PATHS (auth required)
2. Must be added to Express proxy `paths` whitelist in `artifact.toml` or it will 404 before reaching Flask
3. Must not expose gateway URL, secrets, broker credentials, or stack traces in any response field
4. `errors[]` must use machine-readable codes only — never raw exception strings

---

## 15. Read-Only and Non-Mutation Contract

The `/main-brain` route builder MUST NOT:

| Prohibited action | Where it could accidentally happen |
|---|---|
| Invoke full_analysis() more than once | Multiple sub-builders each calling it |
| Write to ALERT_HISTORY | Accidentally re-triggering a score event |
| Write to JOURNAL | Accidentally calling create_journal_entry() |
| Call execute_trade_gateway() | Accidentally triggering the execution hook |
| Write to any DB table | No INSERT/UPDATE/DELETE in the route handler |
| Set any module-level flag | e.g. accidentally triggering training mode |
| Update timestamps merely because viewed | e.g. touching thesis or cache update timestamps |
| Call broker HTTP endpoints | Not even a probe |
| Start background threads | Not inside the route handler |

The route handler calls:
- `full_analysis(ticker_override=tk)` — once
- `build_manager_interface(result)` — already called inside full_analysis
- `build_coach_interface(result)` — already called inside full_analysis
- DB reads (SELECT only): recent strategy_trades rows for journal section
- In-memory reads: ALERT_HISTORY list() snapshot, THESIS_TIMELINE_BY_INST list() snapshot, STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER snapshot, compute_main_brain_learning_stats()

All of the above are already called by `_build_status_payload()`. The Main Brain
route should share the same TTL cache as `/status` or use its own with the same
single-flight guard.

---

## 16. UI Component Plan

| Component | Data Dependency | Empty State | Loading State | Stale State | Error State | Desktop | Tablet | Mobile |
|---|---|---|---|---|---|---|---|---|
| AppShell | market.session | — | skeleton | — | error banner | 3-col layout | 2-col | single col |
| NavigationRail | static + active trades count | — | — | — | — | left rail | bottom tabs | bottom tabs |
| HeaderSummary | market, left_brain.thesis, verdict | "Market closed" | pulse | "Data delayed" badge | "Analysis unavailable" | full row | compact | minimal |
| MarketStateStrip | market_state | all nulls shown | shimmer | border turns amber | "MI unavailable" | horizontal strip | 2-row | stacked |
| ThesisCard | left_brain.thesis | "No thesis formed" | skeleton | age badge turns amber >30m | "Left Brain offline" | full card | full card | expandable |
| VerdictGauge | verdict | score=0, WAIT | pulse ring | — | "Analysis error" | circular gauge + components | gauge + table | collapsed score |
| StrategyRecommendation | strategy_scanner | "No setup found" | skeleton | — | "Scanner offline" | card + ranked list | card | accordion |
| ActiveTradeCard | active_trades | "No open positions" | — | — | — | full card | full card | priority card |
| ExecutionCard | execution_gateway | "No recent signal" | — | — | "Gateway offline" | side panel | side panel | collapsed |
| CoachCard | coach | neutral stubs shown | — | — | neutral stubs | card | card | collapsed |
| DecisionTimeline | decision_timeline | "No events yet" | — | "Partial history" | show what exists | right sidebar | drawer | modal |
| LiveFeed | alerts | "No recent alerts" | — | — | — | bottom strip | bottom strip | notification bell |
| SystemStatus | system_status, availability | all green | — | amber indicators | red indicators | floating pill | collapsed | icon-only |
| StrategyScannerPage | strategy_scanner.ranked | "No strategies scored" | table shimmer | — | error row | table | table | card stack |
| LeftBrainPage | left_brain.*, observations | "Insufficient data" | — | age indicator | "Left Brain offline" | 2-col | 1-col | stacked |
| JournalPage | journal | "No trades today" | table shimmer | — | "DB unavailable" | table + chart | table | list |
| CoachPage | coach, performance | empty stats | — | — | neutral stubs | 2-col | 1-col | stacked |
| SettingsPage | market.*, execution | — | — | — | — | form | form | form |

### Interactions

- **ThesisCard**: tap to expand observations drawer
- **VerdictGauge**: tap component to see PASS/FAIL gate detail
- **StrategyRecommendation**: tap ranked strategy to see regime fit details
- **ActiveTradeCard**: tap to open trade management panel (DISPLAY-ONLY — management actions use existing routes)
- **DecisionTimeline**: tap event to see detail modal
- **NavigationRail**: instrument selector switches `?ticker=` param, re-fetches payload
- **HeaderSummary**: auto-trade toggle writes to `/auto-trade` (existing route)

---

## 17. Design System

Extracted from mockup visual target (dark theme, glassmorphism + data-dense):

### Colors

| Token | Value | Use |
|---|---|---|
| `--bg-base` | `#0A0E1A` | Page background |
| `--bg-panel` | `#111827` | Card/panel background |
| `--bg-panel-elevated` | `#1a2236` | Elevated card, modal |
| `--border-subtle` | `rgba(255,255,255,0.08)` | Panel borders |
| `--border-focus` | `rgba(255,255,255,0.2)` | Focus/hover borders |
| `--primary-blue` | `#3B82F6` | Primary actions, links, READY |
| `--success-green` | `#10B981` | WIN, LIVE_ELIGIBLE, open |
| `--warning-orange` | `#F59E0B` | PARTIAL, stale, FORMING |
| `--danger-red` | `#EF4444` | LOSS, GHOST_ONLY, INVALIDATED |
| `--accent-purple` | `#8B5CF6` | AI/Brain elements, Coach |
| `--accent-gold` | `#F59E0B` | A+ grade, edge score high |
| `--text-primary` | `#F9FAFB` | Main text |
| `--text-secondary` | `#9CA3AF` | Labels, metadata |
| `--text-muted` | `#4B5563` | Disabled, placeholder |

### Typography

| Token | Size | Weight | Use |
|---|---|---|---|
| `--text-hero` | 32px / 2rem | 700 | Edge score hero number |
| `--text-title` | 20px / 1.25rem | 600 | Card titles |
| `--text-body` | 14px / 0.875rem | 400 | Body text |
| `--text-label` | 12px / 0.75rem | 500 | Labels, metadata |
| `--text-micro` | 11px / 0.6875rem | 400 | Timestamps, footnotes |
| Font family | Inter, system-ui | — | Primary sans-serif |
| Monospace | JetBrains Mono, monospace | — | Prices, scores, IDs |

### Spacing scale

`4px, 8px, 12px, 16px, 24px, 32px, 48px, 64px`

### Border radius

| Token | Value | Use |
|---|---|---|
| `--radius-sm` | `6px` | Badges, tags |
| `--radius-md` | `10px` | Cards, panels |
| `--radius-lg` | `16px` | Modal, hero elements |
| `--radius-full` | `9999px` | Pills, toggles |

### Shadows / Glows

| Token | Value | Use |
|---|---|---|
| `--shadow-panel` | `0 4px 16px rgba(0,0,0,0.4)` | Panels |
| `--glow-blue` | `0 0 20px rgba(59,130,246,0.3)` | READY state, active |
| `--glow-green` | `0 0 20px rgba(16,185,129,0.3)` | Trade open |
| `--glow-danger` | `0 0 20px rgba(239,68,68,0.3)` | Alert, GHOST_ONLY |

### Status badges

- READY: green background, white text, `--radius-full`
- WAIT: amber background, dark text
- GHOST_ONLY: red background, white text
- FORMING: blue background, white text
- A+: gold border, gold text, transparent bg
- WIN: green dot + text
- LOSS: red dot + text

### Accessibility

- All status communication must use icon + color + text (never color alone)
- Minimum contrast ratio 4.5:1 for body text; 3:1 for large text
- Interactive elements minimum 44×44px touch target

---

## 18. Responsive Plan

| Breakpoint | Token | Layout |
|---|---|---|
| Mobile | `< 640px` | Single column, bottom tab nav, collapsed cards, priority: Verdict + Active Trade |
| Tablet | `640px – 1024px` | 2-column grid, bottom tabs, expandable panels |
| Desktop | `> 1024px` | 3-column layout, left nav rail, all panels visible |
| Wide | `> 1440px` | Same as desktop, wider strategy scanner panel |

### Mobile-specific rules

- All prices and scores use monospace font at 16px minimum (legible at arm's length)
- Auto-trade toggle accessible on mobile (critical operator action)
- Thesis card collapses to direction + confidence + status (3 data points)
- Decision Timeline hidden by default; accessible via swipe/modal
- No horizontal scroll — all tables become card stacks on mobile

### Payload size for mobile

A `?lite=true` mode should omit:
- `left_brain.observations_count` and full observation data
- `decision_timeline` beyond the last 5 events
- `strategy_scanner.ranked_strategies[*].conditions_met` verbose array
- `verdict.components` (show aggregate only)

---

## 19. Validation Plan

### Schema completeness tests

- V1-P7-SCH-001: All required top-level keys present (`_version`, `generated_at`, `market`, `verdict`, `coach`, `manager`, `journal`, `strategy_scanner`, `active_trades`, `execution_gateway`, `left_brain`, `performance`, `decision_timeline`, `alerts`, `system_status`, `availability`, `errors`)
- V1-P7-SCH-002: `_version` = "v1"
- V1-P7-SCH-003: `generated_at` is ISO-8601 UTC string
- V1-P7-SCH-004: `coach._version` = "v1"
- V1-P7-SCH-005: `manager._version` = "v1"
- V1-P7-SCH-006: All fields in provenance table present and correct type
- V1-P7-SCH-007: Serializable to JSON without TypeError

### Read-only / non-mutation tests

- V1-P7-RO-001: Calling `/main-brain` 5× does not change `ALERT_HISTORY` length
- V1-P7-RO-002: Calling `/main-brain` 5× does not change `JOURNAL` length
- V1-P7-RO-003: Calling `/main-brain` 5× does not change `ACTIVE_TRADES_BY_INST`
- V1-P7-RO-004: Calling `/main-brain` 5× does not change `_TRADERSPOST_LAST`
- V1-P7-RO-005: Calling `/main-brain` 5× does not change `LEARNING_ANALYTICS`
- V1-P7-RO-006: `strategy_scanner.ranked_strategies` reflects existing cache — not recomputed

### Fault-isolation tests (see Section 12)

V1-P7-FI-001 through V1-P7-FI-007 as specified.

### Gateway non-invocation test

- V1-P7-GW-001: Mocking `execute_trade_gateway` to raise — verify no call during `/main-brain` request
- V1-P7-GW-002: Mocking `requests.post` — verify no HTTP call during `/main-brain` request

### Serialization test

- V1-P7-SER-001: Response JSON round-trips through `json.loads(json.dumps(payload))` cleanly
- V1-P7-SER-002: No `datetime`, `deque`, `frozenset` objects in response (must all be serialized)

### Authentication test

- V1-P7-AUTH-001: Unauthenticated GET `/main-brain` → HTTP 401 (blocked by Express)
- V1-P7-AUTH-002: Authenticated GET `/main-brain` → HTTP 200

### Rendering tests (deferred to UI phase)

- Desktop, tablet, mobile screenshot comparisons for each component

---

## 20. Regression Contract

Phase 7 implementation must not break:

- All existing test suites (P2, P3, P4, P5, P6, interface versions)
- parity / scalp_golden / dual_sim / breakout_mode goldens (byte-identical)
- py_compile on app.py
- git diff --check on all modified files
- The `/status` route must return identical payload to today (no keys removed)
- The `/dashboard` route must continue to function
- Auto-trade, gateway, and money-path behavior must be unchanged

The new `/main-brain` route is **additive only**. It reads the same data sources as
`/status` and exposes a structured view. Nothing in the analysis or execution path changes.

---

## 21. Recommended Implementation Batches

### Batch 1 — Core payload (Phase 7B)

Build `build_main_brain_payload(instrument)` function:
- Calls `full_analysis()` once
- Assembles market, verdict, strategy_scanner, active_trades sections
- Assembles coach + manager from existing builders (already in full_analysis result)
- Derives current_r, unrealized_pnl, position_size from trade + current_price
- Returns availability block and errors list
- No DB queries in this batch

### Batch 2 — Journal section (Phase 7B)

Add DB SELECT for recent `strategy_trades` rows:
- SELECT last 10 closed trades (closed_at IS NOT NULL ORDER BY closed_at DESC)
- Canonicalize symbol ("MGC1!" → "MGC")
- Compute session summary from JOURNAL in-memory list
- Fail-open: empty `recent_trades: []` on any DB error

### Batch 3 — Left Brain section (Phase 7B)

Compose `left_brain` block from:
- `LB_THESIS_BY_INST[inst]` snapshot
- `result["market_intelligence"]` from full_analysis
- Observation count from `_LB_THESIS_OBS_BY_INST[inst]`
- Thesis age derivation

### Batch 4 — Decision Timeline (Phase 7B)

Build derived timeline from:
- `THESIS_TIMELINE_BY_INST[inst]` (real events, labeled `is_derived: false`)
- Last READY alert from ALERT_HISTORY (labeled `is_derived: true`)
- Last gateway epoch from `_TRADERSPOST_LAST` (labeled `is_derived: true`)
- Cap at 20 events; label all derived events clearly

### Batch 5 — Register route + tests (Phase 7B)

- Add `GET /main-brain` Flask route
- Add to Express proxy paths whitelist (artifact.toml)
- Implement TTL cache with single-flight guard
- Write test_phase7_main_brain.py covering all 20+ tests above

### Batch 6 — Last gateway result cache (Phase 7B)

- Add `_LAST_GATEWAY_RESULT_BY_INST = {}` in-memory dict
- Write result dict to it after every gateway call (no logic change)
- Expose in `execution_gateway` section of Main Brain payload

### Batch 7 — UI implementation (Phase 7C)

Not in Phase 7A or 7B scope. UI components listed in Section 16.

---

## 22. Risk Register

| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| `/main-brain` calls `full_analysis()` twice (cache miss + sub-builder) | HIGH | MEDIUM | Use single call, pass result dict; same TTL cache as /status |
| Journal DB query slow on every request | MEDIUM | HIGH | Cap at 10 rows, add result to TTL cache, fail-open to [] |
| Derived current_r/PnL uses stale current_price | MEDIUM | MEDIUM | Include price_age_seconds in system_status; stale flag |
| Decision timeline looks complete but is derived | MEDIUM | HIGH | Every derived event must carry `is_derived: true` flag |
| Express proxy whitelist not updated → 404 | HIGH | MEDIUM | Document as required step; test explicitly |
| strategy_trades symbol canonicalization misses new instruments | LOW | LOW | Use `_instrument_from_text()` — registry-driven |
| Partner v1 interface expected by UI but missing | MEDIUM | MEDIUM | Use left_brain + market_intelligence keys directly; document gap |
| full_analysis() is expensive; polling at 3s on Main Brain too | MEDIUM | HIGH | Share TTL cache with /status; do not build a separate analysis path |
| Payload size exceeds mobile limits | LOW | MEDIUM | Implement `?lite=true` mode in Phase 7B |
| Missing last-gateway-result store → Execution card always null | LOW | HIGH | Phase 7B Batch 6 adds the cache; label gap explicitly in Phase 7A |

---

## 23. Phase 7 Execution Contract

### Phase 7A (this phase)
**Authorized:** `PHASE_7_MAIN_BRAIN_ROUTING_BRIEF.md` only  
**Prohibited:** app.py, routes, tests, DB, deployment  
**Deliverable:** This document  

### Phase 7B (next phase)
**Authorized:**
- `app.py`: add `build_main_brain_payload()` + `GET /main-brain` route + `_LAST_GATEWAY_RESULT_BY_INST` cache
- `artifacts/api-server/`: add `/main-brain` to proxy paths whitelist
- `test_phase7_main_brain.py`: new test file

**Not authorized:** Scoring, gateway, broker, learning, DB schema changes  
**Constraint:** app.py changes additive only; all existing goldens byte-identical

### Phase 7C (UI phase)
**Authorized:** Frontend components per Section 16  
**Not authorized:** Backend logic changes  
**Constraint:** All data from `/main-brain` API only; no inline data fabrication

---

## 24. Completion Checklist

- [x] Baseline freeze documented (branch, HEAD, git status, recent commits)
- [x] Regression status confirmed
- [x] All @app.route decorators inventoried (~80 routes)
- [x] Canonical Interface Inventory complete (Coach, Manager, Expert, Left Brain, Journal, Gateway, Partner-gap)
- [x] All 12 mockup sections field-mapped
- [x] Field provenance table created
- [x] Strategy scanner audited (5 main + 16 research)
- [x] Decision timeline audited (14 event types classified)
- [x] Missing-field register complete (13 gaps documented)
- [x] Proposed Main Brain schema designed
- [x] Availability and error contract designed
- [x] API design complete (route, auth, cache, versioning, error model)
- [x] Authentication and security boundary documented
- [x] Read-only non-mutation contract specified
- [x] UI component plan (18 components)
- [x] Design system extracted (colors, typography, spacing, radius, shadows, badges)
- [x] Responsive plan (mobile/tablet/desktop breakpoints)
- [x] Validation plan (20+ test specifications)
- [x] Regression contract
- [x] Implementation batches (7 batches)
- [x] Risk register (10 risks)
- [x] Phase 7 execution contract
- [x] Recommendation below

---

## 25. Recommendation: Ready or Not Ready

**Phase 7B implementation is READY TO BEGIN.**

### Evidence

1. Every mockup field has been classified and mapped to a canonical owner or documented as a gap.
2. The proposed payload schema is complete and all existing interfaces can populate it without code changes to their builders.
3. The three significant gaps (Partner v1, last-gateway-result cache, unified event log) are small, additive, and do not require changes to any money-path logic.
4. The implementation batches are sequenced to deliver a working route with no dangerous dependencies.
5. Full_analysis() already produces 90%+ of the needed data; the Main Brain route is primarily a structured wrapper.

### Conditions

- Phase 7B must be a single commit: `V1-P7B Main Brain payload route`
- No existing test may be broken
- All goldens must remain byte-identical
- app.py changes must be additive only
- The route must pass the full validation suite defined in Section 19 before delivery
