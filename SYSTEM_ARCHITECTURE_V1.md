# SYSTEM_ARCHITECTURE_V1.md
## AI Trading Partner — Core Architecture Blueprint
**Version 1.0 — July 2026**
**NO CODE CHANGES | NO API MODIFICATIONS | ARCHITECTURE SPECIFICATION ONLY**

---

## Table of Contents

1. [End-to-End Data Flow](#section-1--end-to-end-data-flow)
2. [Platform State Machine](#section-2--platform-state-machine)
3. [Internal Message Contracts](#section-3--internal-message-contracts)
4. [Responsibility Matrix](#section-4--responsibility-matrix)
5. [Failure Recovery](#section-5--failure-recovery)
6. [Performance Budgets](#section-6--performance-budgets)
7. [Versioned Interfaces](#section-7--versioned-interfaces)
8. [Acceptance Criteria](#section-8--acceptance-criteria)

---

---

# SECTION 1 — End-to-End Data Flow

## Overview

Every market event follows a single logical pipeline from ingestion through post-trade learning. The pipeline is unidirectional: data flows forward. No stage reaches backward to modify an upstream stage's outputs. Each stage produces a defined artifact consumed by the next.

```
Databento / TradingView Pine Scripts
          │
          ▼
  Market Normalization
          │
          ▼
  Feature Extraction
          │
          ▼
  Left Brain (Build Thesis)
          │
          ▼
  Expert (Evaluate Thesis)
          │
          ▼
  Partner (Explain Decision)
          │
          ▼
  Manager (Risk & Execution)
          │
          ▼
  Execution Gateway
          │
          ▼
  Broker
          │
          ▼
  Journal
          │
          ▼
  Coach (Post-Trade Learning)
```

---

## Stage 1: Databento / TradingView Pine Scripts

**Purpose:**
Source of all market signals. TradingView Pine scripts fire structured HTTP webhooks on every qualifying market event. Databento provides a real-time bar-close feed as a secondary source for ATR precision.

**Inputs:**
- Live CME/COMEX futures market data (TradingView subscription)
- Databento CME feed (optional, flag-gated via `DATABENTO_ENABLED`)
- Alert configurations in Pine scripts for: BOS, CHOCH, HH, HL, LH, LL, VWAP push, supply/demand zones, liquidity sweeps, CVD, volume, FVG/OB, SWEEP_RECLAIM, MICRO_CHOCH, DELTA_FLIP, ENTRY_TRIGGER

**Outputs:**
- HTTP POST payloads to `/webhook` endpoint
- Payload fields: `alert_type` (string), `ticker` (symbol), `price` (float), optional extras (e.g., `zone_high`, `zone_low`, `direction`)
- Databento: OHLCV bars on each bar close → injected into ATR and VWAP stores

**Dependencies:**
- TradingView Pro+ subscription
- Pine script repo: `artifacts/tradingview-webhook/pine/` and `artifacts/analysis-bot/pine/`
- `DATABENTO_API_KEY` secret (when Databento enabled)

**Owner:** Infrastructure / External. Not modifiable by application code.

**Failure behavior:**
- TradingView webhook not firing: platform enters a data-silent mode. The Strict Gate continues computing from stale ALERT_HISTORY. Gate freshness windows prevent stale data from producing a READY signal.
- Databento disconnected: ATR computation falls back to yfinance OHLCV. The `get_databento_status()` function reports OFFLINE. No signal is lost — the VWAP/ATR loop continues without bar-close precision.

**Logging requirements:**
- Every inbound webhook payload is logged at DEBUG level (body redacted on sensitive paths).
- Databento connection status changes are logged at INFO level.
- Databento bar-close events are logged at DEBUG level.

**Expected latency:**
- TradingView webhook delivery: 100ms–2s (TradingView-side; not controllable)
- Databento bar-close inject: <100ms after bar close

---

## Stage 2: Market Normalization

**Purpose:**
Transform raw inbound payloads into the platform's canonical instrument, alert-type, and price representation. Reject unrecognized signals at the boundary. Route recognized signals to the correct processing path.

**Inputs:**
- Raw HTTP POST body from TradingView or Databento trigger
- `alert_type` string, `ticker` string, `price` float
- `ALERT_TYPES` registry (known alert type → processing path)
- `INSTRUMENT_SPECS` registry (canonical instrument token → tick size, dollar-per-point)

**Outputs:**
- Normalized `alert_type` (stripped, uppercased)
- Canonical `instrument` token (MGC, MNQ, MES, MYM)
- Validated `price` float
- Routing decision: scored path, data-only path, structure-bridge path, or rejected

**Dependencies:**
- `ALERT_TYPES` registry (prefixed per instrument)
- `resolve_instrument()` / `instrument_of()` / `_instrument_from_text()` resolvers
- `_SHARED_ALERT_TYPES` (non-prefixed signals recognized across all instruments)
- `_is_structure_bridge_type()` check (SWEEP_RECLAIM / MICRO_CHOCH bypass the unrecognized gate)

**Owner:** Webhook Ingestion Layer (Expert subsystem boundary — data only, no analysis)

**Failure behavior:**
- Unrecognized `alert_type` (and not a structure bridge type): logged as "Unrecognized alert type" and immediately returned 200 OK (no further processing). This prevents retries from TradingView while ensuring a clean no-op.
- Instrument resolution failure: defaults to MGC (fail-safe from Pine script auto-detect). Logged at WARN.
- Malformed JSON body: returns 400. Logged at ERROR.
- Duplicate signal detection: payload logged and discarded. 200 OK returned.

**Logging requirements:**
- Every accepted alert: INFO with alert_type, instrument, price, routing decision.
- Every rejected/unrecognized alert: WARN with raw alert_type.
- Every structure-bridge bypass: DEBUG.

**Expected latency:**
- Normalization + routing: <5ms
- Express proxy forwarding overhead: <10ms

---

## Stage 3: Feature Extraction

**Purpose:**
Update all per-instrument in-memory state stores from the normalized signal. Produce the current feature snapshot that all analysis layers read from. This is the only stage that writes to the primary data stores.

**Inputs:**
- Normalized alert_type, instrument, price from Stage 2
- Existing per-instrument stores: `ALERT_HISTORY`, `VWAP_BY_TICKER`, `CVD_BY_INST`, `ZONE_BROKEN_AT`, `HTF_STATE_BY_INST`, `FAST_ENTRY_STATE_BY_TICKER`, `ACTIVE_TRADES_BY_INST`, `DUAL_TF_BIAS_BY_INST`

**Outputs (updated stores):**
- `ALERT_HISTORY` deque: new entry appended (last 100 scored entries, shared across instruments with per-instrument filtering)
- `VWAP_BY_TICKER`: updated from VWAP push alert (manual override) or auto-fetch timer (yfinance / Databento)
- `CVD_BY_INST`: updated from CVD_BULLISH / CVD_BEARISH / CVD_RESET alerts
- `ZONE_BROKEN_AT`: updated on zone broken/mitigated events with per-instrument TTL
- `HTF_STATE_BY_INST`: updated from SWING_EMA_UPDATE webhook (Pine EMA/RSI/MACD/ADX)
- `FAST_ENTRY_STATE_BY_TICKER`: updated from SWEEP_RECLAIM / MICRO_CHOCH / DELTA_FLIP / ENTRY_TRIGGER alerts
- Structure bridge: SWEEP_RECLAIM → injects synthetic LH/HL/CHOCH into `ALERT_HISTORY` via `_fast_entry_record` and `_FE_BRIDGE_LAST` dedup guard

**Dependencies:**
- All in-memory per-instrument stores
- `ALERT_HISTORY` deque (shared, GIL-protected list() snapshot for safe iteration)
- Auto-fetch timer (VWAP: yfinance at 60-second intervals)
- `_LB_MARKET_MEMORY_BY_INST` (maxlen=200, Left Brain observation accumulation)

**Owner:** Feature Extraction is a shared responsibility of Webhook Ingestion Layer (writing) and all consuming analysis layers (reading). No analysis layer writes to these stores — only the ingestion path writes.

**Failure behavior:**
- Store write failure (unexpected exception): logged at ERROR, operation skipped. Platform continues with pre-update state. Never crashes the Flask process.
- ALERT_HISTORY deque mutation during iteration: always iterate a `list()` snapshot (atomic under CPython GIL) to prevent "deque mutated during iteration" runtime errors.
- VWAP auto-fetch failure (yfinance 429 / timeout): VWAP retains its previous value. `vwap_status` freshness field reflects age. Gate will fail on stale VWAP above its freshness threshold.

**Logging requirements:**
- Each store update: DEBUG (instrument, field updated, new value summary).
- Structure bridge injection: DEBUG (source alert, injected synthetic type).
- VWAP auto-fetch failure: WARN with error type and retry timing.

**Expected latency:**
- Per-alert store update: <2ms
- VWAP auto-fetch cycle: background timer, not on critical path

---

## Stage 4: Left Brain (Build Thesis)

**Purpose:**
Synthesize all available feature data into a market-level thesis. Answer: what is the market doing right now, why, where is it going, and when would that view be wrong? This is the platform's macro intelligence layer.

**Inputs:**
- `ALERT_HISTORY` snapshot (last N alerts per instrument)
- `VWAP_BY_TICKER` (current VWAP value and freshness)
- `CVD_BY_INST` (directional commitment)
- Current price
- `_LB_THESIS_BY_INST` (previous thesis for confidence hysteresis)
- `_LB_MARKET_MEMORY_BY_INST` (deque maxlen=200, rolling observation window)

**Outputs:**
- `market_intelligence` block: `direction`, `strength`, `momentum`, `supporting_evidence`, `confidence` (0–100)
- `thesis` block: `narrative` (plain English), `invalidation` (specific condition), `timeline` (expected duration), `playbook_reasoning` (top-3 fit-scored strategies), `outlook_shift` (boolean on significant confidence delta)
- `_LB_THESIS_OBS_BY_INST` observation buffer update (maxlen=5000, minute-precision dedup)
- Optional: OUTLOOK_SHIFT Discord notification (when confidence delta exceeds threshold, gated on `DISCORD_LIVE_ENABLED`)

**Dependencies:**
- `left_brain_market_intelligence.py`: `compute_left_brain_market_intelligence()`, `compute_left_brain_thesis()`
- `_LB_THESIS_BY_INST` store (persistent thesis with hysteresis — reversal requires `prev=None` reset)
- Playbook selector from Unified Learning Brain (top-3 strategy fit scores)

**Owner:** Left Brain (Trading Expert role boundary — data interpretation, not gate decision)

**Failure behavior:**
- `compute_left_brain_market_intelligence()` raises exception: thesis block defaults to a neutral stub (`direction: UNKNOWN`, `confidence: 0`). Expert stage proceeds with neutral MI. Logged at ERROR.
- `compute_left_brain_thesis()` raises exception: thesis retains previous value from `_LB_THESIS_BY_INST`. If no previous value exists, neutral stub used. Logged at ERROR.
- Left Brain is never on the execution critical path. A failed thesis never blocks a trade.

**Logging requirements:**
- Each thesis compute: DEBUG with confidence score and direction.
- OUTLOOK_SHIFT detection: INFO with previous and new confidence.
- Left Brain exception: ERROR with full traceback.

**Expected latency:**
- `compute_left_brain_market_intelligence()`: <50ms
- `compute_left_brain_thesis()`: <50ms
- Total Left Brain stage: <100ms

---

## Stage 5: Expert (Evaluate Thesis)

**Purpose:**
Apply the gate. Evaluate whether current market conditions meet the criteria for a tradeable setup. Produce the authoritative binary verdict (READY / WAIT) and the edge score. All money-path decisions flow through this stage.

**Inputs:**
- Feature snapshot from Stage 3 (ALERT_HISTORY, VWAP, CVD, zones, structure)
- Left Brain thesis and MI block from Stage 4 (for MI adaptive filter veto)
- `TRADING_MODE` (SCALP / SWING / MICRO_SCALP)
- Mode-specific gate configuration from `cfg()`
- Per-instrument historical edge data (learning influence, if flag enabled)

**Outputs:**
- `is_actionable` (boolean): the primary binary verdict
- `verdict` string: "SCALP READY" / "SWING READY" / "WAIT" / "MARKET CLOSED"
- `strict_reason` string: named reason for every WAIT (e.g., "structure_not_confirmed", "vwap_not_confirmed")
- `gate_debug` dict: per-gate PASS/FAIL (zone, vwap, structure, cvd, vol)
- `edge_score` (0–110): transparent quality score
- `grade` string: A+ / A / B / WAIT
- `alert_level` string: READY / EARLY / WATCH
- `edge_breakdown` dict: per-component contribution
- `trade_plan` dict: entry, stop, target(s), risk_r, rr_num
- `alert_diagnostics` dict: CVD state, RVOL, sweep, session bonus

**Dependencies:**
- `evaluate_strict_setup()`: core gate function
- `_analysis_edge_breakdown()`: edge score computation
- EDGE_COMPONENTS: BOS20 / CHOCH20 / VWAP15 / Sweep15 / Volume15 / CVD15 / Session10
- Volatility monitor gate (`get_volatility()`, `VOL_HARD_GATE`)
- Entry Quality Location Engine (`compute_entry_quality()`, score < 70 with edge < 90 → demote)
- MI adaptive strategy filter (SCALP only, demote-only veto on unambiguous MI conflict)
- Market session check (`market_session_status()`) — runs LAST as closed-override
- Learning influence modifier (±15, flag-gated, bounded 0.65–1.35 weight)
- Analyst Reasoning Engine (`compute_analyst_review()`) — produces veto, applied here
- Trade Debate Engine — produces `final_verdict`, veto default OFF

**Owner:** Expert (Trading Expert role — sole authority on READY/WAIT verdict)

**Failure behavior:**
- `evaluate_strict_setup()` exception: verdict defaults to WAIT with `strict_reason: "evaluation_error"`. Logged at ERROR. Platform never produces a READY from an error state.
- Edge score computation failure: `edge_score` defaults to 0, `grade` to WAIT. Logged at ERROR.
- Analyst veto computation failure: analyst veto defaults to False (fail-open for display, fail-closed for any money-path effect). Logged at ERROR.
- Any unhandled exception in full_analysis: single return path catches and returns a safe WAIT result with error key. Never propagates a 500 to the dashboard on signal processing.

**Logging requirements:**
- Every READY verdict: INFO with instrument, grade, edge_score, strict_reason (empty on READY).
- Every WAIT verdict: INFO with instrument, strict_reason.
- Expert exception: ERROR with full traceback.
- Gate debug (per-gate PASS/FAIL): written to `gate_diagnostics.log` at DEBUG.

**Expected latency:**
- `evaluate_strict_setup()`: <20ms
- `_analysis_edge_breakdown()`: <10ms
- Analyst reasoning: <50ms
- Total Expert stage (full_analysis): <200ms target, <500ms maximum

---

## Stage 6: Partner (Explain Decision)

**Purpose:**
Translate the Expert's verdict into a coherent, plain-language explanation for the operator. Synthesize all analyst layers (Analyst, Debate, Professional Review, Main Brain) into a unified voice. Never recompute what the Expert already computed.

**Inputs:**
- Complete Expert output (is_actionable, verdict, strict_reason, grade, edge_score, trade_plan)
- Left Brain thesis and MI block
- Analyst Reasoning Engine output (`analyst` block)
- Trade Debate Engine output (`trade_debate` block: bull_case, bear_case, judge, final_verdict)
- Shared Trade Memory output (`trade_memory` block: similar trades, governor nudges)
- Learning block (strategy weights, per-mode stats)
- Market context (session, news, cross-market alignment)

**Outputs:**
- `main_brain` block: 7 cognitive keys (synthesis, voice, conflict analysis, verdict board, confidence, recommendation, uncertainty)
- `main_brain_voice` string: one-sentence plain-English market narration
- `conflict_resolver` block: 10-priority conflict engine output (why engines disagree)
- `verdict_board` block: 4-bucket plain-English classifier (supports / opposes / missing / vetoes)
- `avatar` observations: mbAvatarObserve hook output (proactive queue events)
- `unified_analyst_report` block: consolidated thesis with 15-min update loop (Discord gated on DISCORD_LIVE_ENABLED)
- `stalk_active` block: Stalk (pre-entry) + Active Thinking (in-trade) advisory overlays

**Dependencies:**
- `_mb_orchestrate()` → `_mb_learning_snapshot()` → `compute_main_brain()` (3-layer rule; never collapse)
- Brain Contract JS rendering (10 render functions in dashboard JS)
- Avatar Intelligence Engine: `mbAvatarObserve(d)` called at end of renderModules
- Unified Analyst Report: consumes (never recomputes) analyst/debate/governor/memory blocks

**Owner:** Partner (Trading Partner role — explanation and synthesis only)

**Failure behavior:**
- `compute_main_brain()` exception: `main_brain` block defaults to neutral stubs. `main_brain_voice` defaults to "Analysis unavailable." Dashboard renders gracefully with fallback values. Logged at ERROR.
- Partner failure never blocks Expert verdict or execution. The READY/WAIT verdict from the Expert is authoritative regardless of Partner state.
- Avatar observation failure: avatar queue returns empty. No proactive message shown. Logged at DEBUG.

**Logging requirements:**
- Main Brain compute cycle: DEBUG with synthesis confidence.
- OUTLOOK_SHIFT triggered partner update: INFO.
- Partner exception: ERROR with full traceback.

**Expected latency:**
- `compute_main_brain()`: <100ms
- Full Partner stage: <150ms
- Avatar observation hook: <20ms (non-blocking)

---

## Stage 7: Manager (Risk & Execution)

**Purpose:**
Apply all risk controls, validate execution eligibility, and route to the Execution Gateway. This stage is the last gatekeeper before any real money moves.

**Inputs:**
- Expert verdict (is_actionable, verdict, trade_plan)
- Auto-trade arm state (`AUTO_TRADE_ENABLED`, per-instrument arm flag)
- Bot Training Mode gate (stage < 4 → suppress)
- Prop Firm Protection (daily loss limit check via `prop_accounts` / `safety_overrides`)
- Per-asset safety controls (kill switch, maxLossesPerDay)
- Advisor auto-trade review gate (opt-in, requires `reviewed` marker in analyst outputs)
- Active trade state (`ACTIVE_TRADES_BY_INST`)
- `AUTO_FIRED_KEYS` dedup store (prevents re-firing on same setup)
- `EXECUTION_MODE` env (manual_only / paper / traderspost / pickmytrade)

**Outputs:**
- Execution decision: `send` | `suppress` | `manual_required` | `rejected`
- Rejection reason (if any): logged and surfaced in `gateway_debug` block
- Broker payload (if send): canonical intent dict → per-provider adapter → provider-specific JSON
- Broker payload audit log: redacted JSON (no URLs or secrets), required-field check result
- Post-send: `ACTIVE_TRADE` written → persisted to `open_trades` (Postgres)
- Post-send: `AUTO_FIRED_KEYS` dedup key registered
- Post-send: SCALP dynamic exits armed (if `SCALP_DYNAMIC_EXITS_ENABLED`)
- Post-send: Live runner armed (if `LIVE_RUNNER_ENABLED`)

**Dependencies:**
- `_check_auto_trade()`: main auto-trade decision function
- `_training_gate()`: Bot Training Mode suppression
- `safety_cfg()`: per-asset safety config resolver (DB → registry → defaults)
- `PROP_LOCK` (never held under money locks)
- `execute_trade_gateway()`: single gateway function (Learning Rule Engine gate: GHOST_ONLY / LIVE_ELIGIBLE)
- `_persist_active_trade()`: called OUTSIDE the instrument lock
- Opposite-side reversal buffer (TradersPost-only, per-instrument send spacing)

**Owner:** Manager (Trading Manager role — sole authority on execution routing and risk controls)

**Failure behavior:**
- Any exception in `_check_auto_trade()`: suppresses execution (fail-closed). Logged at ERROR. `gateway_debug` reflects the failure.
- Prop Firm Protection unavailable (DB error): defaults to BLOCK (fail-closed). Logged at ERROR.
- Per-asset safety config unavailable (DB error): defaults to kill-switch ON (fail-closed). Logged at ERROR.
- Bot Training Mode probe failure: defaults to suppress (fail-closed). Logged at ERROR.
- Learning Rule Engine unavailable: defaults to GHOST_ONLY (no live send). Fail-open for display, fail-closed for execution.

**Logging requirements:**
- Every execution decision: INFO with instrument, direction, outcome (send/suppress/manual/rejected), reason.
- Broker payload audit: INFO (redacted JSON, never URL or secrets).
- Prop firm gate trigger: WARN.
- Kill switch trigger: WARN.
- Manager exception: ERROR with full traceback.

**Expected latency:**
- Risk gate evaluation: <10ms
- Total Manager stage (pre-send): <20ms

---

## Stage 8: Execution Gateway

**Purpose:**
Send the canonical trade intent to the configured broker provider. The Execution Gateway is the only component that communicates with external broker systems.

**Inputs:**
- Canonical intent dict from Manager: `{instrument, direction, action, contracts, stop, target, rr_num}`
- `EXECUTION_MODE`: routes to TradersPost / PickMyTrade / paper / manual_only
- Provider-specific adapter: translates canonical intent to provider JSON schema
- Required-field validation result from Broker Payload Pre-Send Guard

**Outputs:**
- HTTP POST to broker webhook URL (TradersPost or PickMyTrade, if live mode)
- HTTP response code and body (logged, never displayed raw to operator)
- `gateway_result` dict: `{outcome: sent | paper | manual_required, provider, timestamp}`
- Paper trade: local log entry only (no HTTP send, no dedup, no position tracking via broker)
- Manual_only: no send, no log, returns `manual_required`

**Dependencies:**
- `TRADERSPOST_WEBHOOK_URL` secret (TradersPost mode)
- Broker Payload Pre-Send Guard: validates required fields before any HTTP call
- Opposite-side reversal buffer: RESERVE send_at under lock before sleeping (prevents concurrent-send race)
- `_enqueue_slow()`: all non-READY-card Discord posts deferred to slow-task worker

**Owner:** Execution Gateway (Trading Manager role — final provider-facing boundary)

**Failure behavior:**
- HTTP timeout (TradersPost unreachable): local reject (no send). `gateway_result.outcome = "timeout"`. Active trade NOT registered. Logged at ERROR. Operator notified via dashboard `gateway_debug` panel.
- HTTP non-2xx response: local reject. `gateway_result.outcome = "broker_rejected"`. Active trade NOT registered. Logged at ERROR.
- Invalid payload (required-field check fails): local reject before any HTTP call. `gateway_result.outcome = "invalid_payload"`. Logged at ERROR.
- Duplicate execution attempt (`AUTO_FIRED_KEYS` already contains this key): suppressed before reaching gateway. Logged at DEBUG.
- send_at race (concurrent sends): RESERVE under lock before sleeping prevents double-send.

**Logging requirements:**
- Every gateway call attempt: INFO with provider, instrument, direction, contracts.
- HTTP success (2xx): INFO with response code.
- HTTP failure: ERROR with response code, body (redacted).
- Payload validation failure: ERROR with failing fields.
- Duplicate suppression: DEBUG with fired key.

**Expected latency:**
- Payload validation: <5ms
- Broker HTTP round-trip (TradersPost): 100ms–500ms (external; not controllable)
- Paper/manual path: <5ms (local only)

---

## Stage 9: Broker

**Purpose:**
External broker system. Receives the order and routes to the exchange. This stage is outside the platform boundary.

**Inputs:**
- Provider-formatted JSON from Execution Gateway
- Broker authentication (last URL segment or header, managed as secret)

**Outputs:**
- Exchange order confirmation (HTTP 2xx with order ID)
- Fill confirmation (separate — broker pushes via their own mechanism)
- Rejection (HTTP 4xx/5xx)

**Dependencies:**
- TradersPost account and strategy configuration (external)
- PickMyTrade account (external, alternative provider)
- CME/COMEX market hours (broker rejects orders during halt)

**Owner:** External. Not controlled by the platform.

**Failure behavior:**
- Any broker failure is handled at Stage 8 (Execution Gateway). From Stage 9's perspective, failures propagate back as non-2xx HTTP responses.
- The platform does not poll for fill confirmation. Active trade state is registered optimistically on a 2xx response from Stage 8.

**Logging requirements:**
- Handled at Stage 8. Stage 9 itself produces no platform logs.

**Expected latency:**
- Exchange fill: <500ms from broker receipt (market hours, normal conditions)

---

## Stage 10: Journal

**Purpose:**
Create the permanent record of every trade event. Notify all configured channels. Update the strategy_trades table for downstream analytics and learning.

**Inputs:**
- Trade open event (from Manager post-send)
- Trade close event (from active trade lifecycle: stop hit, target hit, manual close, early exit)
- READY alert event (for journal-channel analyst report)
- Active trade P&L (MFE/MAE booleans, commission, slippage from Trade Management Analytics Sidecar)

**Outputs:**
- `strategy_trades` DB row: full trade record (instrument, direction, entry, exit, R, P&L, grade, strategy, source, metadata)
- Discord trade card embed: main channel (READY signal, live card)
- Discord journal embed: journal channel (analyst report, 15-min update loop, gated on DISCORD_LIVE_ENABLED)
- Discord A+ channel embed: high-conviction setups (grade A+ only)
- EOD performance report: Discord (end of session, gated on DISCORD_LIVE_ENABLED)
- Trade-taken bell: audio data URI fired on active trade `opened_at` transition
- Screenshot: passed to Discord embed (never fetched by platform; provided by TradingView webhook)

**Dependencies:**
- `_build_card_entry()`: single source for all journal + trade card content
- `strategy_trades` PostgreSQL table (INSERT/SELECT only — no DDL)
- Discord webhook secrets: `DISCORD_WEBHOOK_URL`, `DISCORD_MNQ_WEBHOOK_URL`, `DISCORD_mgc_WEBHOOK_URL`, `DISCORD_JOURNAL_WEBHOOK_URL`
- `DISCORD_LIVE_ENABLED` flag (dev/prod share secrets → must gate all unconditional sends)
- `_enqueue_slow()`: all Discord embeds except the READY live card deferred to slow-task worker
- Per-instrument throttle: prevents instant + periodic double-post on READY
- `TRADE_READY_INTERVAL`: period for READY card re-post

**Owner:** Journal (Trading Journal role)

**Failure behavior:**
- Discord POST failure (network, rate-limit): logged at WARN. Trade record is still written to DB. Platform continues. Discord is best-effort, not required for correctness.
- `strategy_trades` INSERT failure: logged at ERROR. Trade record may be lost. Platform continues. Does not suppress the trade or block the next signal.
- `_build_card_entry()` exception: logged at ERROR. Fallback minimal card used (instrument + direction only). Discord post still attempted.

**Logging requirements:**
- Trade open journal: INFO with instrument, direction, grade, edge_score.
- Trade close journal: INFO with outcome (win/loss), R, dollar P&L.
- Discord send failure: WARN with channel name and error type.
- DB write failure: ERROR with table name and error.

**Expected latency:**
- DB write: <50ms
- Discord card: <200ms (async, via slow-task worker)
- Journal stage does NOT block the webhook response path

---

## Stage 11: Coach (Post-Trade Learning)

**Purpose:**
Use the completed trade record to improve future performance. Update strategy weights, capture thesis patterns, analyze failure modes, and build the operator's personal performance model.

**Inputs:**
- Completed trade record from Journal (`strategy_trades` row)
- Setup snapshot at entry time (thesis, MI, analyst outputs)
- Trade outcome (win/loss, R, P&L, MFE/MAE)
- `thesis_snapshots` table (for Thesis Tracker resolve cycle)
- `decision_snapshots` table (for Decision Quality update)

**Outputs:**
- `strategy_weights` table: updated per-strategy win-rate weights (bounded 0.65–1.35)
- `PER_MODE_STATS` in-memory store: updated global per-mode aggregates
- `thesis_snapshots` resolve: outcome linked to snapshot → lesson + reflection written to DB
- Trade Failure Analyzer: failure pattern recorded (root cause classification)
- Decision Quality Analytics: process quality snapshot updated
- Learning influence modifier: recomputed (±15 edge score effect for next eligible setup, flag-gated)
- Learning Rule Engine eligibility: recomputed every Nth close (GHOST_ONLY / LIVE_ELIGIBLE)
- 25-trade learning report: generated when threshold crossed

**Dependencies:**
- `LEARNING_ENABLED` flag
- `LEARNING_LOCK` mutex (recompute serialized — never held during DB reads, only during swap)
- `strategy_weights` PostgreSQL table
- `thesis_snapshots` PostgreSQL table
- `decision_snapshots` PostgreSQL table
- `_ns_learning_key("{mode}::{key}")`: namespace isolation (SWING/SCALP/MICRO_SCALP never share a slot)
- Shared Trade Memory: `find_similar_trades()` updated with new record (recency × version weights)

**Owner:** Coach (Trading Coach role — post-trade only, never real-time gate)

**Failure behavior:**
- Learning weight recompute failure: weights retain previous values. Logged at ERROR. Edge score modifier retains previous value.
- `thesis_snapshots` resolve failure: lesson not written. Logged at ERROR. Platform continues.
- Trade Failure Analyzer write failure: failure record not written. Logged at ERROR. Platform continues.
- Coach failure never affects the live gate, edge score display, or execution. It is strictly post-hoc.

**Logging requirements:**
- Weight recompute cycle: INFO with instrument, mode, new weight.
- Thesis resolve: INFO with snapshot_id, outcome, lesson summary.
- Coach exception: ERROR with full traceback.

**Expected latency:**
- Weight recompute: <200ms (serialized, background thread)
- Thesis resolve: <100ms
- Coach stage is fully asynchronous — it never blocks the webhook response

---

---

# SECTION 2 — Platform State Machine

## Overview

The platform operates as a per-instrument state machine. Each instrument (MGC, MNQ, MES, MYM) maintains its own state independently. The machine is non-exclusive: one instrument can be in ACTIVE TRADE while another is OBSERVING.

The states below are logical states derived from the combination of market session, gate verdict, arm state, and active trade presence. They are not stored as an explicit field — they are computed from observable component states.

---

## State: BOOTING

**Purpose:**
The Flask process has started. Critical infrastructure is being initialized before any signals can be processed.

**Entry conditions:**
- Flask process launched (via `prod-start.sh` or development runner)

**Exit conditions:**
- All `*_DB_READY` flags set to True (DB probe passed)
- Market state cache restore complete
- ALERT_HISTORY restored from cache (within freshness window)
- All in-memory stores initialized
- Auto-trade arm state set to OFF (always resets on boot — intentional safety)
- Open trade state restored from `open_trades` table (as INERT)

**Allowed transitions:**
- BOOTING → MARKET CLOSED (if current time is outside market hours)
- BOOTING → WARMING (if market is open but no recent signal history)
- BOOTING → OBSERVING (if market is open and cache restore provided recent history)

**Forbidden transitions:**
- BOOTING → READY (verdict computation not yet valid)
- BOOTING → ARMED (arm state always resets to OFF on boot)
- BOOTING → ACTIVE TRADE (trade state restored as INERT, not ACTIVE)

**Operator UI behavior:**
- Dashboard shows "Initializing..." or a boot status indicator
- All verdict panels show "Loading" or equivalent neutral state
- No READY alerts can fire
- ENTER button is disabled

**Engineering behavior:**
- `*_DB_READY` flags gate all DB-dependent subsystems
- Boot probe runs once (`__main__` only) — not on import
- Market state cache restore loads ALERT_HISTORY, CVD, AUTO_FIRED_KEYS, zone state
- READY state intentionally NOT restored from cache
- Flask zombie-prevention guards active (SIGTERM handler, sys.excepthook, app.run() finally)

---

## State: MARKET CLOSED

**Purpose:**
The market is outside of trading hours. No signals are processed as trading signals. Gate verdicts are suppressed. Existing analysis remains available for review.

**Entry conditions:**
- `market_session_status()` returns closed
- CME/COMEX daily halt (17:00–18:00 ET), weekend, or US exchange holiday

**Exit conditions:**
- `market_session_status()` returns open (next session begins)

**Allowed transitions:**
- MARKET CLOSED → WARMING (on session open, if no recent signal history)
- MARKET CLOSED → OBSERVING (on session open, if warm from cache)
- MARKET CLOSED → BOOTING (if the process restarts while market is closed)

**Forbidden transitions:**
- MARKET CLOSED → READY (closed-override runs LAST in full_analysis; always returns WAIT)
- MARKET CLOSED → ARMED (auto-trade cannot arm during closed session)
- MARKET CLOSED → ENTRY PENDING (no orders during closed session)

**Operator UI behavior:**
- Session status indicator shows "CLOSED" with time to open
- All verdict panels show WAIT with `strict_reason: "market_closed"`
- ENTER button is disabled
- Active trade management controls remain visible if a trade was carried over

**Engineering behavior:**
- Closed-override block runs LAST in `full_analysis` — appended after all other analysis
- Closed-override must mirror all expected keys in the result dict (key parity required)
- Discord alert sends are suppressed during closed session (most alerts are also gated on DISCORD_LIVE_ENABLED)
- Auto-trade arming is not permitted
- Manual Desk Order is not permitted

---

## State: WARMING

**Purpose:**
The market is open but the platform does not yet have sufficient signal history to compute a reliable verdict. The gate may not have all required components populated.

**Entry conditions:**
- Session just opened (boot or overnight session start)
- ALERT_HISTORY has fewer than minimum signals for reliable gate evaluation
- VWAP auto-fetch has not yet returned a value for this session

**Exit conditions:**
- VWAP value present and within freshness window
- At least one structure signal (BOS/CHOCH) in ALERT_HISTORY for the instrument
- CVD state initialized
- Sufficient history for edge scoring

**Allowed transitions:**
- WARMING → OBSERVING (when sufficient history is present)
- WARMING → MARKET CLOSED (if session ends before warm-up completes — rare)

**Forbidden transitions:**
- WARMING → READY (gate cannot be valid without minimum history)
- WARMING → ARMED (arm state must be explicitly set by operator after boot)

**Operator UI behavior:**
- Verdict panel shows WAIT with `strict_reason: "warming_up"` or equivalent
- Edge score shows 0 or suppressed
- Avatar may offer pre-session briefing (thesis from cache, economic events)

**Engineering behavior:**
- No change to gate logic — WARMING is an observed state, not an enforced one
- Gate will naturally produce WAIT until components populate
- VWAP auto-fetch runs on its normal timer — no special warming behavior required

---

## State: OBSERVING

**Purpose:**
Normal operating state. Market is open. Sufficient history exists. Gate is evaluating each incoming signal and producing WAIT verdicts. No setup is actionable yet.

**Entry conditions:**
- Market open
- VWAP present and fresh
- At least one structure signal in ALERT_HISTORY
- Gate verdict is WAIT

**Exit conditions:**
- Gate verdict becomes READY → transition to READY
- Market closes → transition to MARKET CLOSED
- Auto-trade armed by operator → remain in OBSERVING (arm is a control state, not a machine state)

**Allowed transitions:**
- OBSERVING → READY (on READY verdict)
- OBSERVING → MARKET CLOSED (session ends)
- OBSERVING → WARMING (VWAP becomes stale, history drops below threshold — rare)

**Forbidden transitions:**
- OBSERVING → ACTIVE TRADE (cannot have an active trade without going through READY → ARMED → ENTRY PENDING)
- OBSERVING → EXITED (no trade to exit from)

**Operator UI behavior:**
- Verdict panel shows WAIT with named strict_reason
- Edge score shown with current component status
- ENTER button disabled
- Avatar shows observational commentary ("Watching for structure above 2,651")

**Engineering behavior:**
- `full_analysis()` runs on every incoming webhook and every 3-second /status poll
- Dashboard polls /status at 3-second intervals with a client-side tick guard (no overlapping requests)
- ALERT_HISTORY is the live state being continuously updated

---

## State: READY

**Purpose:**
All gate conditions are met. A valid setup is actionable. The operator (or auto-trade system if armed) can enter a trade.

**Entry conditions:**
- `evaluate_strict_setup()` returns `is_actionable: True`
- `verdict` is "SCALP READY" or "SWING READY"
- Market session is open
- CVD not in hard-veto state opposing the direction

**Exit conditions:**
- Operator presses ENTER → ENTRY PENDING
- Auto-trade fires → ENTRY PENDING
- Gate conditions no longer met → OBSERVING
- Market closes → MARKET CLOSED
- `TRADE_READY_INTERVAL` elapses and conditions still met → remains READY (card re-posted)

**Allowed transitions:**
- READY → ENTRY PENDING (operator ENTER or auto-trade)
- READY → OBSERVING (gate conditions no longer met)
- READY → MARKET CLOSED (session ends)

**Forbidden transitions:**
- READY → ACTIVE TRADE (must pass through ENTRY PENDING / Execution Gateway confirmation)
- READY → REVIEWING (no trade to review — must have opened one first)

**Operator UI behavior:**
- READY badge on instrument tab (green pulse)
- Full trade plan visible: entry, stop, target, R, dollar risk
- ENTER button enabled (green)
- Main Brain voice narration updates to "Take this trade" style
- Discord READY card fires to main channel (once per setup, re-fires on interval)
- Trade-taken bell fires if auto-trade is armed and fires automatically

**Engineering behavior:**
- READY card fires BEFORE journal embed (alert before journal ordering)
- `TRADE_READY_INTERVAL` governs re-fire period
- Per-instrument throttle prevents instant + periodic double-post
- `AUTO_FIRED_KEYS` dedup prevents re-entry on same setup key
- EARLY tier: `alert_level = "EARLY"` fires ⚡ EARLY before full READY (sweep + structure, pre-candle-close)

---

## State: ARMED

**Purpose:**
The operator has enabled auto-trade for this instrument. The system will automatically execute when a READY verdict arrives, without requiring operator intervention.

**Entry conditions:**
- Operator explicitly arms the instrument via `/auto-trade` toggle
- `AUTO_TRADE_ENABLED` is True for the instrument
- Market is open

**Exit conditions:**
- Operator disarms the instrument
- Platform restarts (arm state resets to OFF — intentional safety)
- A STOP_HIT event re-arms (WIN does NOT re-arm automatically)

**Allowed transitions:**
- ARMED (+ OBSERVING) → ARMED (+ READY): remains armed, auto-fire pending
- ARMED (+ READY) → ENTRY PENDING: auto-fire triggers
- ARMED → OBSERVING (if disarmed by operator)

**Forbidden transitions:**
- ARMED → any state on boot without explicit operator re-arm

**Operator UI behavior:**
- Auto-trade arm indicator shows "ARMED" per instrument
- ENTER button shows "AUTO" mode indicator
- No additional operator action required for execution once armed

**Engineering behavior:**
- Arm state is in-memory only — never persisted to DB (intentional)
- `_check_auto_trade()` evaluates arm state on every webhook
- SCALP auto-fires on `is_actionable` including EARLY tier (half-size at EARLY)
- Daily cap governs max auto-fires per session
- Advisor review gate (opt-in): requires `reviewed` marker in both analyst outputs before auto-fire

---

## State: ENTRY PENDING

**Purpose:**
An execution attempt is in progress. The Execution Gateway has been called. Awaiting broker confirmation.

**Entry conditions:**
- ENTER pressed (manual) or auto-trade fired
- Manager risk gates all passed
- Execution Gateway call initiated

**Exit conditions:**
- Broker returns 2xx → ACTIVE TRADE
- Broker returns non-2xx / timeout → OBSERVING or READY (execution failed, no position opened)

**Allowed transitions:**
- ENTRY PENDING → ACTIVE TRADE (broker 2xx)
- ENTRY PENDING → READY (broker rejection — setup still valid)
- ENTRY PENDING → OBSERVING (broker rejection — setup conditions degraded by the time rejection returned)

**Forbidden transitions:**
- ENTRY PENDING → ENTRY PENDING (no re-entry while pending — dedup guard)

**Operator UI behavior:**
- ENTER button shows "Sending..." or spinner during pending
- Gateway status panel reflects pending state
- No additional ENTER attempts accepted while pending

**Engineering behavior:**
- Duration of ENTRY PENDING: typically <500ms (broker HTTP round-trip)
- Opposite-side reversal buffer may introduce a pre-send sleep (TradersPost only)
- Broker payload audit logged before HTTP call
- Required-field validation runs before HTTP call (local reject on failure)

---

## State: ACTIVE TRADE

**Purpose:**
A confirmed open position exists. The platform is actively monitoring the trade, providing management guidance, and tracking thesis validity.

**Entry conditions:**
- Broker returned 2xx on execution
- `ACTIVE_TRADES_BY_INST` entry written for the instrument
- `open_trades` Postgres row inserted
- `AUTO_FIRED_KEYS` dedup key registered

**Exit conditions:**
- Trade closes: target hit, stop hit, manual close, or auto early-exit → EXITED

**Allowed transitions:**
- ACTIVE TRADE → MANAGING (immediate; these states overlap — ACTIVE TRADE transitions to MANAGING as soon as dynamic exits are armed)
- ACTIVE TRADE → EXITED (on close event)

**Forbidden transitions:**
- ACTIVE TRADE → READY (for the same instrument — cannot have two simultaneous READY setups on one instrument while in trade)
- ACTIVE TRADE → ARMED for the same instrument (new auto-fire blocked while position open — single ACTIVE_TRADE slot per instrument)

**Operator UI behavior:**
- Active trade panel is the most prominent element
- Live P&L shown in R and in dollars
- Thesis validity indicator ("Trade thesis valid / invalidated")
- Right Brain advisory panel visible ("Hold / Trail / Take Partial / Exit")
- Management controls: Move to BE, Close Trade buttons active

**Engineering behavior:**
- `ACTIVE_TRADES_BY_INST` is per-instrument (one slot per instrument, RLock)
- Write-through: all set/clear operations call `_persist_active_trade()` OUTSIDE the lock
- Boot restores from `open_trades` as INERT (not full ACTIVE) — operator confirmation required before auto-management resumes
- SCALP dynamic exits arm their paper watcher (MANAGED_TRADES_BY_KEY)
- Live runner arms if `LIVE_RUNNER_ENABLED`

---

## State: MANAGING

**Purpose:**
The trade is active and dynamic exit management is running. The platform is actively watching price against TP1/TP2/runner levels and moving the stop per the plan.

**Entry conditions:**
- ACTIVE TRADE state
- SCALP dynamic exits enabled (`SCALP_DYNAMIC_EXITS_ENABLED`)
- OR Right Brain trade management producing actionable recommendations

**Exit conditions:**
- Trade closes → EXITED

**Allowed transitions:**
- MANAGING → EXITED (on close event)

**Forbidden transitions:**
- MANAGING → ACTIVE TRADE (these states overlap; transition is MANAGING → EXITED only)

**Operator UI behavior:**
- Trade management panel shows TP1/TP2/runner levels
- Completed targets show as "Hit" with timestamp
- Stop level updates reflected in real-time
- Right Brain advisory shows most recent recommendation with urgency level

**Engineering behavior:**
- Paper watcher (`MANAGED_TRADES_BY_KEY`) polls on timer
- Same-bar fill guard: skip exit eval on bars opened at/before entry_epoch
- Paper watcher live-gate inside self-rescheduling loop (not outside)
- Live broker: single TP order sent (broker manages runner independently)
- Auto Early-Exit watcher: triggers on `opposite_confirmed` only (not `stop_breached`)

---

## State: EXITED

**Purpose:**
The trade has closed. The outcome is known. Journal records are being written. Learning cycle begins.

**Entry conditions:**
- Stop hit event received
- Target hit event received (from paper watcher, broker callback, or manual close)
- Manual close by operator
- Auto Early-Exit trigger (confirmed-invalid thesis)

**Exit conditions:**
- Journal write complete
- Learning cycle initiated (async)
- Instrument returns to OBSERVING (if market open) or MARKET CLOSED

**Allowed transitions:**
- EXITED → OBSERVING (market open, no new setup yet)
- EXITED → REVIEWING (operator navigates to Coach/Journal view)
- EXITED → MARKET CLOSED (session ended)

**Forbidden transitions:**
- EXITED → ACTIVE TRADE without a new ENTRY PENDING cycle

**Operator UI behavior:**
- Trade outcome card displayed: R result, dollar P&L, grade, duration
- "What happened" explanation from platform
- Learning note (if applicable): "This pattern has a 71% win rate over 32 samples"
- Active trade panel clears; interface returns to observation mode

**Engineering behavior:**
- `_persist_active_trade()` called with cleared state → `open_trades` row updated (closed_at, exit_price, result_r)
- SWING thesis: `_persist_swing_thesis()` called (prevents resurrection as OPEN on boot)
- Journal card fires to Discord (deferred to slow-task worker via `_enqueue_slow()`)
- `strategy_trades` INSERT with full trade record
- Learning cycle begins asynchronously (does not block return to OBSERVING)

---

## State: REVIEWING

**Purpose:**
The operator is in post-trade or end-of-day review mode. No new trading decisions are in progress.

**Entry conditions:**
- Operator navigates to Coach view, Journal view, or EOD summary
- OR EOD auto-trigger at session end

**Exit conditions:**
- Operator returns to Operator Mode
- New trading session begins

**Allowed transitions:**
- REVIEWING → OBSERVING (return to trading)
- REVIEWING → MARKET CLOSED (overnight review)

**Forbidden transitions:**
- REVIEWING → READY (must return to Operator Mode first)

**Operator UI behavior:**
- Coach / Journal / Performance screens active
- Thesis Tracker lessons visible
- Trade Failure patterns visible (if losses occurred)
- Coaching note from learning engine
- EOD Discord report already sent (background)

**Engineering behavior:**
- No gate computation required during REVIEWING
- Learning cycle may still be completing asynchronously
- All DB reads (performance analytics) are SELECT-only

---

---

# SECTION 3 — INTERNAL MESSAGE CONTRACTS

## Overview

The platform's internal communication is not an explicit message bus — state changes propagate through shared in-memory stores and function calls within a single Flask process. This section documents the logical message events: what triggers them, who produces them, who consumes them, and what information they carry.

All messages are synchronous function calls or store updates within the same process unless noted as async (Discord, slow-task worker).

---

## THESIS_CREATED

**Description:** The Left Brain has computed a thesis for the first time for an instrument in this session (no previous thesis in `_LB_THESIS_BY_INST`).

| Field | Detail |
|---|---|
| **Producer** | `compute_left_brain_thesis()` in `left_brain_market_intelligence.py` |
| **Consumer** | Expert stage (MI block), Partner stage (Main Brain synthesis), Dashboard (/status response), Discord (if OUTLOOK_SHIFT) |
| **Required fields** | `instrument`, `direction`, `confidence`, `narrative`, `invalidation`, `timeline`, `playbook_reasoning`, `timestamp` |
| **Optional fields** | `outlook_shift` (boolean), `fit_scores` (top-3 strategy scores) |
| **Delivery expectations** | Synchronous within `full_analysis()` call. Available immediately for all downstream consumers in the same request. |
| **Failure behavior** | If creation fails, `_LB_THESIS_BY_INST[instrument]` remains None. Expert uses neutral MI stub. Logged at ERROR. |

---

## THESIS_UPDATED

**Description:** The Left Brain has recomputed the thesis for an instrument. Confidence or direction has changed from the previous value.

| Field | Detail |
|---|---|
| **Producer** | `compute_left_brain_thesis()` |
| **Consumer** | Expert stage, Partner stage, Dashboard, Discord (if OUTLOOK_SHIFT threshold crossed) |
| **Required fields** | `instrument`, `direction`, `confidence`, `narrative`, `invalidation`, `timeline`, `timestamp`, `prev_confidence`, `prev_direction` |
| **Optional fields** | `outlook_shift` (boolean — True when |Δconfidence| ≥ threshold or direction flips), `fit_scores` |
| **Delivery expectations** | Synchronous within `full_analysis()`. OUTLOOK_SHIFT Discord notification is async via `_enqueue_slow()`. |
| **Failure behavior** | Thesis retains previous value. Logged at ERROR. OUTLOOK_SHIFT notification not sent on failure. |

---

## VERDICT_CHANGED

**Description:** The Expert's gate verdict has changed (WAIT → READY or READY → WAIT) for an instrument.

| Field | Detail |
|---|---|
| **Producer** | `evaluate_strict_setup()` |
| **Consumer** | Partner stage (Main Brain), Manager stage (auto-trade check), Dashboard, Discord (READY card), Journal (READY alert) |
| **Required fields** | `instrument`, `verdict`, `is_actionable`, `strict_reason`, `grade`, `edge_score`, `timestamp` |
| **Optional fields** | `gate_debug` (per-gate PASS/FAIL), `alert_level` (READY/EARLY/WATCH), `trade_plan` |
| **Delivery expectations** | Synchronous within `full_analysis()`. READY → Discord card fires synchronously for the live card; analyst embed deferred to slow-task worker. |
| **Failure behavior** | Verdict defaults to WAIT on exception. Manager never receives a READY from an error state. |

---

## ENTRY_ARMED

**Description:** The operator has enabled auto-trade for an instrument.

| Field | Detail |
|---|---|
| **Producer** | `/auto-trade` endpoint handler |
| **Consumer** | `_check_auto_trade()` (reads arm state), Dashboard (arm indicator), auto-trade arming system |
| **Required fields** | `instrument`, `armed` (boolean), `timestamp`, `operator` |
| **Optional fields** | `mode` (trading mode at arm time) |
| **Delivery expectations** | In-memory state update. No async delivery. Takes effect on next webhook. |
| **Failure behavior** | If arm state write fails, arm defaults to disarmed (fail-safe). Logged at ERROR. |

---

## ENTRY_TRIGGERED

**Description:** An execution attempt has been initiated (operator ENTER or auto-trade fire).

| Field | Detail |
|---|---|
| **Producer** | Operator ENTER action (`/traderspost` handler) or `_check_auto_trade()` |
| **Consumer** | Execution Gateway, Manager risk gates, `AUTO_FIRED_KEYS` dedup, Broker Payload Pre-Send Guard |
| **Required fields** | `instrument`, `direction`, `contracts`, `entry_price`, `stop`, `target`, `source` (manual/auto), `timestamp` |
| **Optional fields** | `rr_num`, `strategy`, `grade`, `edge_score`, `dedup_key` |
| **Delivery expectations** | Synchronous. The entire Manager → Gateway → Broker chain completes before the handler returns. |
| **Failure behavior** | Any gate failure or gateway rejection produces a WAIT result (no position opened). Logged at ERROR. |

---

## TRADE_OPENED

**Description:** A broker confirmation (2xx) has been received and the trade is now active.

| Field | Detail |
|---|---|
| **Producer** | Execution Gateway (post-2xx broker response) |
| **Consumer** | `ACTIVE_TRADES_BY_INST` store, `open_trades` DB table, `AUTO_FIRED_KEYS` dedup, Journal (trade card), SCALP exits (arm watcher), Live runner (arm), Dashboard |
| **Required fields** | `instrument`, `direction`, `contracts`, `entry_price`, `stop`, `target`, `opened_at`, `strategy`, `grade`, `edge_score`, `source` |
| **Optional fields** | `rr_num`, `runner_enabled`, `dynamic_exits_enabled`, `gateway_provider` |
| **Delivery expectations** | Synchronous (store writes, DB INSERT). Journal card is async via `_enqueue_slow()`. |
| **Failure behavior** | If `open_trades` INSERT fails, ACTIVE_TRADE is still set in-memory. Trade functions but boot restore will not see it. Logged at ERROR. |

---

## TRADE_UPDATED

**Description:** An in-trade event has occurred: TP1 hit, stop moved to breakeven, runner armed, or management recommendation changed.

| Field | Detail |
|---|---|
| **Producer** | SCALP dynamic exits watcher, Right Brain Trade Management, Auto Early-Exit watcher |
| **Consumer** | `ACTIVE_TRADES_BY_INST` (updated), `MANAGED_TRADES_BY_KEY` (paper dynamic), Dashboard, Discord (management update via `_enqueue_slow()`) |
| **Required fields** | `instrument`, `event_type` (TP1_HIT / BE_MOVED / RUNNER_ARMED / ADVISORY_CHANGED), `timestamp` |
| **Optional fields** | `new_stop`, `new_target`, `current_r`, `recommendation`, `urgency` |
| **Delivery expectations** | Synchronous store update. Discord update async via slow-task worker. |
| **Failure behavior** | Management update logged at ERROR if exception occurs. Previous state retained. Trade continues. |

---

## TRADE_CLOSED

**Description:** The trade has exited. All close-side records are being written.

| Field | Detail |
|---|---|
| **Producer** | SCALP exits watcher (stop/target hit), Manual close handler, Auto Early-Exit trigger |
| **Consumer** | `ACTIVE_TRADES_BY_INST` (cleared), `open_trades` (updated: closed_at, exit_price, result_r), `strategy_trades` (INSERT), Journal, Coach learning cycle, Trade Management Analytics Sidecar, Thesis Tracker (resolve) |
| **Required fields** | `instrument`, `exit_price`, `result_r`, `dollar_pnl`, `closed_at`, `close_reason` (target/stop/manual/early_exit) |
| **Optional fields** | `mfe_boolean`, `mae_boolean`, `commission`, `slippage`, `duration_minutes` |
| **Delivery expectations** | Store clear and DB update synchronous. Journal Discord and learning cycle async. |
| **Failure behavior** | If `open_trades` update fails, position may reappear on boot. Logged at ERROR. `strategy_trades` INSERT failure: record may be lost (logged at ERROR). Learning cycle proceeds on available data. |

---

## THESIS_INVALIDATED

**Description:** An in-trade thesis invalidation has been detected: a confirmed opposite BOS/CHOCH appeared or price breached the stop condition at the structural level.

| Field | Detail |
|---|---|
| **Producer** | Active Thinking overlay, Auto Early-Exit watcher, Manual Trade Manager thesis checker |
| **Consumer** | Dashboard (thesis validity indicator), Right Brain Trade Management (urgency escalation), Auto Early-Exit (conditional trade close), Discord advisory (via slow-task worker) |
| **Required fields** | `instrument`, `invalidation_type` (opposite_confirmed / stop_breached), `timestamp` |
| **Optional fields** | `invalidating_alert_type`, `invalidating_price`, `current_r` |
| **Delivery expectations** | Synchronous detection. Dashboard update on next /status poll. Discord advisory async. |
| **Failure behavior** | If invalidation detection raises, thesis validity defaults to valid (fail-open — never force-closes a trade on detection error). Logged at ERROR. |

---

## SESSION_CHANGED

**Description:** The market session status has changed (open → closed, closed → open, halt start, halt end).

| Field | Detail |
|---|---|
| **Producer** | `market_session_status()` (detected on each `full_analysis()` call) |
| **Consumer** | Expert stage (closed-override block), Manager (disables auto-trade execution), Dashboard (session indicator), Manual Trade Manager (pauses on close, resumes on open) |
| **Required fields** | `new_status` (open / closed / halt), `timestamp`, `next_change_at` |
| **Optional fields** | `holiday_name` (on full-day close), `half_day` (boolean) |
| **Delivery expectations** | Detected synchronously on each full_analysis call. No explicit event — consumers read session state on each cycle. |
| **Failure behavior** | If session status check fails, defaults to "closed" (fail-safe — never allow trading on session status uncertainty). Logged at ERROR. |

---

## MARKET_STATUS_CHANGED

**Description:** A broader market status change has occurred: volatility spike, cross-market alignment shift, or OUTLOOK_SHIFT from the Left Brain.

| Field | Detail |
|---|---|
| **Producer** | Left Brain (OUTLOOK_SHIFT), Volatility Monitor (extreme ratio), Cross-Market Alignment (transition detected) |
| **Consumer** | Dashboard (market status panels), Discord (alignment alert or outlook shift notify), Partner stage (updated narration), Avatar (proactive observation) |
| **Required fields** | `change_type` (OUTLOOK_SHIFT / VOLATILITY_SPIKE / ALIGNMENT_CHANGED), `instrument`, `timestamp` |
| **Optional fields** | `prev_value`, `new_value`, `severity` (for volatility), `aligned_instruments` (for cross-market) |
| **Delivery expectations** | Discord sends are async via `_enqueue_slow()`. Dashboard reflects on next /status poll. |
| **Failure behavior** | Notification failure logged at WARN. Platform continues. No action blocked. |

---

---

# SECTION 4 — RESPONSIBILITY MATRIX

## Overview

Each role owns its domain exclusively. No role reaches into another role's domain to read raw data, recompute outputs, or make decisions. The ownership boundaries below are hard constraints for all future development.

---

## Left Brain

| Dimension | Detail |
|---|---|
| **Primary Responsibilities** | Compute market intelligence (direction, strength, momentum, confidence). Build and maintain the market thesis (narrative, invalidation, timeline). Detect OUTLOOK_SHIFT. Score strategy fit (playbook selector). Maintain observation buffer. |
| **Consumes** | `ALERT_HISTORY` snapshot, `VWAP_BY_TICKER`, `CVD_BY_INST`, current price, previous thesis from `_LB_THESIS_BY_INST` |
| **Produces** | `market_intelligence` block, `thesis` block, `playbook_reasoning`, `outlook_shift` flag, `_LB_THESIS_OBS_BY_INST` entries |
| **Never Responsible For** | Gate decisions (READY/WAIT), edge scoring, execution routing, trade management, post-trade learning, operator explanation, Discord sends (except OUTLOOK_SHIFT notify) |

---

## Expert

| Dimension | Detail |
|---|---|
| **Primary Responsibilities** | Apply the Strict Gate (zone + VWAP + structure + CVD). Compute the Edge Score. Produce the verdict (READY/WAIT) and grade (A+/A/B). Compute the trade plan (entry, stop, targets). Apply all demote-only vetoes (analyst, entry quality, MI filter, trend brake, structure-reversal). |
| **Consumes** | All Feature Extraction stores (ALERT_HISTORY, VWAP, CVD, zones, structure), Left Brain MI block, learning influence modifier, session status |
| **Produces** | `is_actionable`, `verdict`, `strict_reason`, `gate_debug`, `edge_score`, `grade`, `alert_level`, `trade_plan`, `alert_diagnostics`, `analyst` block, `trade_debate` block |
| **Never Responsible For** | Plain-language explanation, execution routing, trade management, post-trade learning, Discord sends (the READY card is the Journal's responsibility), market thesis narrative |

---

## Partner

| Dimension | Detail |
|---|---|
| **Primary Responsibilities** | Synthesize all analysis layers into a unified plain-English explanation. Produce the Main Brain voice narration. Resolve conflicts between analysis layers. Present the Verdict Board. Maintain the Avatar's proactive observation queue. |
| **Consumes** | Expert full output, Left Brain thesis and MI, analyst block, debate block, trade memory block, learning block, market context (session, news, cross-market) |
| **Produces** | `main_brain` block (7 cognitive keys), `main_brain_voice` narration, `conflict_resolver` output, `verdict_board` output, `avatar` observations, `unified_analyst_report`, `stalk_active` overlays |
| **Never Responsible For** | Gate computation (never recomputes verdict), edge scoring (consumes Expert output only), execution routing, trade management, post-trade learning |

---

## Manager

| Dimension | Detail |
|---|---|
| **Primary Responsibilities** | Apply all risk controls before execution. Route to the correct execution provider. Monitor active trades (dynamic exits, runner). Maintain the auto-trade arm state. Enforce safety controls (kill switch, prop limits, daily loss cap). |
| **Consumes** | Expert verdict and trade plan, auto-trade arm state, Bot Training Mode gate result, Prop Firm Protection result, per-asset safety config, Learning Rule Engine eligibility, `ACTIVE_TRADES_BY_INST` |
| **Produces** | Execution decision (send / suppress / manual_required / rejected), `gateway_debug` block, `ACTIVE_TRADES_BY_INST` updates, broker payload, SCALP exit events, runner events |
| **Never Responsible For** | Verdict computation (consumes Expert output), plain-language explanation (Partner's role), post-trade learning, journal writes, market thesis |

---

## Execution Gateway

| Dimension | Detail |
|---|---|
| **Primary Responsibilities** | Translate canonical trade intent to provider-specific payload. Send the HTTP order to the broker. Return a structured gateway result. Enforce the Broker Payload Pre-Send Guard. Apply the opposite-side reversal buffer. |
| **Consumes** | Canonical intent dict from Manager, `EXECUTION_MODE` env, provider adapter logic, `TRADERSPOST_WEBHOOK_URL` secret |
| **Produces** | HTTP order to broker, `gateway_result` dict, broker audit log entry |
| **Never Responsible For** | Risk gate decisions (Manager's responsibility), trade tracking, journal records, platform state management, learning |

---

## Journal

| Dimension | Detail |
|---|---|
| **Primary Responsibilities** | Create the permanent trade record. Send all Discord notifications. Maintain the `strategy_trades` table. Produce the EOD performance report. Track trade management analytics (MFE/MAE, commission, slippage). |
| **Consumes** | Trade lifecycle events (TRADE_OPENED, TRADE_CLOSED), READY verdict (for trade card), Expert grade and edge score, trade plan, Partner narration (for analyst report embeds), performance analytics queries |
| **Produces** | `strategy_trades` DB rows, Discord trade cards, Discord analyst reports, Discord A+ channel embeds, Discord EOD report, Today's Trades panel data |
| **Never Responsible For** | Gate decisions, execution routing, active trade management, post-trade learning (Coach's responsibility), market analysis |

---

## Coach

| Dimension | Detail |
|---|---|
| **Primary Responsibilities** | Use completed trade records to improve future performance. Update strategy weights. Resolve Thesis Tracker snapshots. Record Trade Failure patterns. Compute Decision Quality scores. Provide the learning influence modifier (edge score ±15). Provide the Learning Rule Engine eligibility gate (GHOST_ONLY / LIVE_ELIGIBLE). |
| **Consumes** | `strategy_trades` table (completed records), setup snapshots (thesis, MI, analyst at entry time), trade outcomes, `thesis_snapshots` table, `decision_snapshots` table |
| **Produces** | `strategy_weights` updates, `PER_MODE_STATS` updates, Thesis Tracker lessons, Trade Failure patterns, Decision Quality trends, learning influence modifier (±15), Learning Rule Engine eligibility, 25-trade learning report |
| **Never Responsible For** | Real-time gate decisions (post-hoc only), execution routing, active trade management, operator explanation, Discord sends (except learning report summary) |

---

---

# SECTION 5 — FAILURE RECOVERY

## Recovery Principles

1. **Fail-closed on money path.** Any uncertainty in a risk gate defaults to blocking execution.
2. **Fail-open on display.** Any uncertainty in a display-only subsystem defaults to neutral / unavailable, never blocking the operator.
3. **No single point of failure stops the platform.** Loss of any one subsystem produces degraded operation, not a crash.
4. **Explicit over silent.** Every failure is logged. Operators are informed when a subsystem is degraded.

---

## Scenario 1: Databento Disconnected

| Field | Detail |
|---|---|
| **Detection** | `DatabentoBrain` connection attempt fails or stream drops. `get_databento_status()` returns OFFLINE. |
| **Recovery** | Platform continues using yfinance for ATR auto-fetch. Databento feed is optional; the core pipeline does not depend on it. No operator intervention required. |
| **Operator notification** | Databento status panel shows "OFFLINE" (Engineering View). No alert to Operator Mode — degradation is transparent in normal conditions. |
| **Logging** | WARN on initial disconnect. INFO on reconnect. |
| **Safe fallback** | yfinance ATR auto-fetch (already running as primary when Databento is off). |
| **Recovery completion** | DatabentoBrain reconnects on next scheduled attempt. `get_databento_status()` returns ONLINE. |

---

## Scenario 2: Market Feed Interruption (TradingView Webhooks Stop)

| Field | Detail |
|---|---|
| **Detection** | No webhooks received for longer than expected. ALERT_HISTORY timestamp freshness falls below threshold. VWAP age exceeds freshness window. |
| **Recovery** | Platform enters data-silent mode. Gate freshness windows naturally produce WAIT verdicts for stale data. No READY signal can fire on stale data. |
| **Operator notification** | VWAP staleness indicator shows age. `vwap_status` reflects "stale". Avatar may note absence of signals. |
| **Logging** | VWAP auto-fetch failure logged at WARN on each failed attempt. |
| **Safe fallback** | WAIT verdict. Gate refuses actionable verdict on stale VWAP. |
| **Recovery completion** | TradingView webhooks resume. VWAP auto-fetch or next VWAP push brings freshness back within window. Gate resumes normal evaluation. |

---

## Scenario 3: Broker Rejection

| Field | Detail |
|---|---|
| **Detection** | Execution Gateway receives non-2xx HTTP response from TradersPost / PickMyTrade. |
| **Recovery** | Execution is aborted. `ACTIVE_TRADES_BY_INST` is NOT updated. `AUTO_FIRED_KEYS` dedup key is NOT registered (allows retry on next READY signal). Gateway result reflects `"broker_rejected"`. |
| **Operator notification** | `gateway_debug` panel shows rejection reason and HTTP status. Dashboard does not show an active trade. |
| **Logging** | ERROR with HTTP status code, response body (redacted), instrument, direction. |
| **Safe fallback** | Platform returns to READY or OBSERVING state depending on whether gate conditions still hold. No position exists. |
| **Recovery completion** | On next READY verdict, gateway can attempt again. No manual operator intervention required unless the broker URL or password is invalid (TradersPost connectivity probe used to diagnose). |

---

## Scenario 4: Execution Timeout

| Field | Detail |
|---|---|
| **Detection** | HTTP request to broker times out (no response within timeout window). |
| **Recovery** | Same as broker rejection. Execution aborted. No position registered. `gateway_debug` reflects `"timeout"`. |
| **Operator notification** | `gateway_debug` panel shows timeout. |
| **Logging** | ERROR with instrument, direction, timeout duration. |
| **Safe fallback** | No position. Platform remains in READY state if conditions hold. |
| **Recovery completion** | Next webhook cycle evaluates fresh. Operator may retry ENTER manually if still READY. |

---

## Scenario 5: Duplicate Execution Attempt

| Field | Detail |
|---|---|
| **Detection** | `AUTO_FIRED_KEYS` dedup store already contains the dedup key for this setup (same instrument + direction + zone key). |
| **Recovery** | Execution suppressed before reaching the gateway. No HTTP call made. |
| **Operator notification** | No notification (this is expected behavior — prevents double-entry on same setup). |
| **Logging** | DEBUG with dedup key that was matched. |
| **Safe fallback** | Platform remains in READY state. No duplicate position. |
| **Recovery completion** | Key clears when: (a) the setup conditions change (new READY from different state), (b) operator uses `/clear-fired-keys`, or (c) platform restarts. |

---

## Scenario 6: Clock Synchronization Issue

| Field | Detail |
|---|---|
| **Detection** | Webhook timestamps arrive out of sequence (future timestamps from TradingView). ALERT_HISTORY age calculations produce anomalous freshness values. |
| **Recovery** | Freshness windows use wall-clock time (`datetime.utcnow()`), not webhook timestamps. The `_audit_event_duplicates` function uses `now_dt` kwarg (1-hour cutoff) for dedup. Out-of-sequence timestamps are treated as stale and filtered by freshness gates. |
| **Operator notification** | VWAP staleness indicator may show unexpected behavior. No explicit alert. |
| **Logging** | DEBUG when a webhook timestamp is outside expected window. |
| **Safe fallback** | Freshness windows produce WAIT on anomalous timestamps. Gate never goes READY on a clock-skewed signal. |
| **Recovery completion** | Normal signals resume and freshness windows return to expected ranges. |

---

## Scenario 7: Database Unavailable

| Field | Detail |
|---|---|
| **Detection** | `*_DB_READY` flags set to False (boot probe fails or runtime query throws). Any DB-dependent subsystem checks its flag before operating. |
| **Recovery** | All DB-dependent features degrade gracefully: learning engine uses in-memory weights only, thesis tracker cannot snapshot, journal writes fail silently (logged), active trade persistence fails (in-memory only). Gate and execution are NOT affected (gate is in-memory). |
| **Operator notification** | Boot diagnostic shows DB unavailable. Learning panel shows "unavailable." Thesis tracker disabled. Coach features show "unavailable." |
| **Logging** | ERROR on initial connection failure. WARN on each subsequent DB operation failure. |
| **Safe fallback** | In-memory operation. All real-time trading functions continue. Analytics and persistence are degraded. |
| **Recovery completion** | When DB connection is re-established, `*_DB_READY` flags return to True. Backfill of missed records is not automatic — records written during outage are lost. |

---

## Scenario 8: Journal Write Failure

| Field | Detail |
|---|---|
| **Detection** | `strategy_trades` INSERT raises exception. Discord POST returns non-2xx. |
| **Recovery** | Journal failure is logged and operation continues. Trade is not blocked or rolled back. Active trade state remains. |
| **Operator notification** | Discord post failure: WARN logged, no visible dashboard alert. DB write failure: ERROR logged, trade may be missing from analytics. |
| **Logging** | WARN for Discord failure. ERROR for DB write failure. |
| **Safe fallback** | Trade continues. Management, tracking, and execution are not affected by journal failure. |
| **Recovery completion** | Next journal write attempt proceeds normally. Lost records cannot be recovered retroactively. |

---

## Scenario 9: Left Brain Unavailable

| Field | Detail |
|---|---|
| **Detection** | `compute_left_brain_market_intelligence()` or `compute_left_brain_thesis()` raises exception. |
| **Recovery** | Neutral stub returned (direction: UNKNOWN, confidence: 0, narrative: "Analysis unavailable"). Expert proceeds with neutral MI. Partner receives neutral thesis. MI adaptive filter defaults to no veto (fail-open). |
| **Operator notification** | Left Brain panel shows "Unavailable." Main Brain voice narration defaults to neutral. |
| **Logging** | ERROR with full traceback. |
| **Safe fallback** | Expert verdict still valid from gate inputs alone. Left Brain failure never blocks a READY signal or execution. |
| **Recovery completion** | Next `full_analysis()` cycle attempts Left Brain compute again. Recovery is automatic. |

---

## Scenario 10: Partner Unavailable

| Field | Detail |
|---|---|
| **Detection** | `compute_main_brain()` raises exception. |
| **Recovery** | Main Brain block defaults to neutral stubs. `main_brain_voice` defaults to "Analysis unavailable." Verdict Board and Conflict Resolver return neutral. Avatar observations return empty. Expert verdict is unaffected. |
| **Operator notification** | Main Brain panel shows "Unavailable." Voice narration shows "Analysis unavailable." |
| **Logging** | ERROR with full traceback. |
| **Safe fallback** | Expert verdict (READY/WAIT) is unaffected. Execution can proceed on gate verdict alone. Partner failure is display-only. |
| **Recovery completion** | Next `full_analysis()` cycle attempts Partner compute again. Automatic recovery. |

---

## Scenario 11: Coach Unavailable

| Field | Detail |
|---|---|
| **Detection** | Learning weight recompute fails. Thesis Tracker resolve fails. Trade Failure Analyzer write fails. |
| **Recovery** | Strategy weights retain previous values. Edge score modifier retains previous value (or defaults to 1.0 if never computed). Post-trade records may be lost. Coaching display shows "Unavailable." |
| **Operator notification** | Coach / Learning panel shows "Unavailable" or last-known data. |
| **Logging** | ERROR on each Coach subsystem failure. |
| **Safe fallback** | Previous weights. In-memory learning state. No gate or execution impact. |
| **Recovery completion** | Next trade close triggers new Coach compute attempt. Automatic recovery on next event. |

---

---

# SECTION 6 — PERFORMANCE BUDGETS

## Critical Path vs. Background Processing

**Critical path:** Any operation that must complete before the webhook HTTP response is returned to TradingView, or before the `/status` response is returned to the dashboard.

**Background processing:** Any operation that runs asynchronously (slow-task worker, timer threads, Discord posts, DB writes not on the response path).

| Stage | Path | Budget |
|---|---|---|
| Webhook receive (Express) | Critical | <10ms |
| Normalization (instrument resolve, alert_type gate) | Critical | <5ms |
| Feature extraction (store updates) | Critical | <10ms |
| Left Brain (MI + Thesis) | Critical | <100ms |
| Expert (Strict Gate + Edge Score) | Critical | <200ms |
| Analyst Reasoning | Critical | <50ms |
| Partner (Main Brain synthesis) | Critical | <150ms |
| Manager (risk gates, pre-send check) | Critical | <20ms |
| **Total decision pipeline** | **Critical** | **<500ms target, <1s maximum** |
| Execution Gateway (pre-send validation) | Critical | <5ms |
| Broker HTTP round-trip (TradersPost) | Critical (blocking send) | <500ms (external; not controllable) |
| Dashboard /status response | Background (poll) | <200ms |
| Discord trade card (READY) | Background (async) | <2s (best effort) |
| Discord journal embed | Background (slow-task worker) | <5s (best effort) |
| strategy_trades DB INSERT | Background | <50ms |
| Learning weight recompute | Background | <200ms |
| Thesis Tracker resolve | Background | <100ms |
| VWAP auto-fetch (yfinance) | Background (60s timer) | <3s per fetch |
| Market State Cache restore (boot) | Boot (once) | <1s |
| Active trade persistence write | Critical (post-execution) | <50ms |

---

## Dashboard Poll Budget

The dashboard polls `/status` every 3 seconds with a client-side tick guard (no overlapping requests).

| Operation | Budget |
|---|---|
| `/status` response assembly (key whitelist) | <50ms |
| Per-instrument full_analysis (if ticker_override) | <200ms |
| Total `/status` end-to-end | <300ms |
| Single-flight TTL cache (prevents poll × inline analysis) | Cache TTL: 2.5s (expires before next poll) |

---

## Boot Budget

| Operation | Budget |
|---|---|
| DB readiness probe | <500ms |
| Market state cache restore | <1s |
| ALERT_HISTORY restore from cache | <200ms |
| Open trades restore (`open_trades` table) | <200ms |
| Flask ready to serve (all guards initialized) | <3s total from process start |

---

## Background Thread Budget

| Thread | Frequency | Budget per run |
|---|---|---|
| VWAP auto-fetch | 60s | <3s |
| Cross-market alignment refresh | 30s | <500ms |
| Left Brain obs dedup cycle | Per full_analysis | <10ms |
| SCALP dynamic exits paper watcher | Per-timer | <50ms |
| Heartbeat eval loop | 60s | <200ms |
| Learning recompute (on trade close) | Per close | <200ms |

---

---

# SECTION 7 — VERSIONED INTERFACES

## Versioning Convention

Each internal interface carries a version identifier in its output contract. Consumers must handle the current version and MAY handle the previous version for a defined backward-compatibility window. Breaking changes require a version bump. Additive changes (new optional fields) do not require a version bump.

---

## Left Brain API

**Purpose:** Provides market intelligence and thesis to all downstream consumers.

**Version:** `v2`

**Input contract:**
```
instrument: str                    # canonical instrument token (MGC, MNQ, MES, MYM)
alert_history_snapshot: list       # list() snapshot of ALERT_HISTORY for the instrument
vwap_value: float | None           # current VWAP value
cvd_state: str                     # "bullish" | "bearish" | "unknown"
price: float                       # current market price
prev_thesis: dict | None           # previous _LB_THESIS_BY_INST entry (for hysteresis)
market_memory: deque               # _LB_MARKET_MEMORY_BY_INST (maxlen=200)
```

**Output contract — market_intelligence block:**
```
direction: str                     # "BULLISH" | "BEARISH" | "NEUTRAL" | "UNKNOWN"
strength: str                      # "STRONG" | "MODERATE" | "WEAK" | "UNKNOWN"
momentum: str                      # "ACCELERATING" | "STABLE" | "DECELERATING" | "UNKNOWN"
confidence: float                  # 0–100
supporting_evidence: list[str]     # plain-language evidence bullets
timestamp: str                     # ISO 8601 UTC
```

**Output contract — thesis block:**
```
direction: str                     # matches market_intelligence.direction
confidence: float                  # 0–100 (with hysteresis applied)
narrative: str                     # plain-English market narrative
invalidation: str                  # specific condition that would negate thesis
timeline: str                      # expected duration ("next 30–60 minutes", etc.)
playbook_reasoning: list[dict]     # top-3 strategies with fit_score and rationale
outlook_shift: bool                # True when |Δconfidence| ≥ threshold or direction flips
top_playbook_fit_score: float      # highest fit score among top-3
vwap_age_ms: int                   # VWAP age at compute time
mi_input_ts: str                   # timestamp of most recent MI input signal
_version: str                      # "v2"
```

**Guaranteed fields:** `direction`, `confidence`, `narrative`, `invalidation`, `timeline`, `_version`

**Optional fields:** `playbook_reasoning`, `outlook_shift`, `top_playbook_fit_score`, `vwap_age_ms`, `mi_input_ts`

**Backward compatibility:** v1 consumers missing optional fields must default gracefully. `_version` allows consumer branching.

---

## Expert Interface

**Purpose:** Provides the authoritative gate verdict, edge score, trade plan, and all derived analysis to all downstream consumers.

**Version:** `v1`

**Input contract:** (implicit — Expert reads directly from shared in-memory stores and the Left Brain output block)

**Output contract — core fields (guaranteed):**
```
is_actionable: bool
verdict: str                       # "SCALP READY" | "SWING READY" | "WAIT" | "MARKET CLOSED"
strict_reason: str                 # named gate failure or "" on READY
gate_debug: dict                   # per-gate PASS/FAIL (zone, vwap, structure, cvd, vol)
edge_score: int                    # 0–110
grade: str                         # "A+" | "A" | "B" | "WAIT"
alert_level: str                   # "READY" | "EARLY" | "WATCH" | "WAIT"
edge_breakdown: dict               # per-component contribution
trade_plan: dict                   # entry, stop, target, risk_r, rr_num
alert_diagnostics: dict            # CVD state, RVOL, sweep, session bonus
analyst: dict                      # Analyst Reasoning Engine output block
trade_debate: dict                 # Trade Debate Engine output block
_version: str                      # "v1"
```

**Optional fields:**
```
conviction_tier: str               # FULL | HALF | WATCH
directions: dict                   # per-direction (long/short) bull/bear case views
potential_plan: dict               # forming-setup preview (EARLY state)
swing_v2: dict                     # Swing Mode V2 output (when flag ON)
breakout_mode: dict | None         # ORB advisory (when flag ON)
dual_tf_ready: bool                # Dual-TF convergence (when flag ON)
```

**Guaranteed fields:** All "core fields" listed above are always present. Missing keys cause downstream 500 errors — new optional fields must be added to ALL code paths simultaneously (including closed-override).

**Backward compatibility:** Adding optional fields is non-breaking. Renaming or removing guaranteed fields is a breaking change requiring version bump.

---

## Partner Interface

**Purpose:** Provides the synthesized cognitive output and plain-English explanation to the dashboard and Discord.

**Version:** `v1`

**Input contract:** Full Expert output block (consumed, never recomputed)

**Output contract — guaranteed fields:**
```
main_brain: dict                   # 7 cognitive keys
main_brain_voice: str              # one-sentence narration
conflict_resolver: dict            # 10-priority conflict analysis
verdict_board: dict                # 4-bucket plain-English classifier
avatar: dict                       # proactive observation queue
unified_analyst_report: dict       # consolidated thesis block
_version: str                      # "v1"
```

**Optional fields:**
```
stalk_active: dict                 # Stalk + Active Thinking overlays (when trade active or forming)
scalp_advisory: dict               # Scalp Strategy Advisory (when flag ON)
dpv2_shadow: dict                  # Decision Pipeline V2 shadow log (when flag ON)
```

**Guaranteed fields:** All core fields always present. Neutral stubs used on failure (never null/missing).

**Backward compatibility:** Consumer Brain Contract JS renders via 10 named render functions — any new top-level key must have a corresponding render function or it will be silently dropped by the JS layer.

---

## Manager Interface

**Purpose:** Provides execution decisions and active trade state to the dashboard and Journal.

**Version:** `v1`

**Input contract:** Expert `is_actionable`, `verdict`, `trade_plan`; arm state, risk gate states

**Output contract — guaranteed fields:**
```
gateway_debug: dict                # execution decision outcome and reason
active_trade: dict | None          # current ACTIVE_TRADES_BY_INST entry (None if no trade)
managed_trade: dict | None         # MANAGED_TRADES_BY_KEY entry (None if no paper trade)
training_gate: dict                # Bot Training Mode gate result
auto_trade_enabled: dict           # per-instrument arm state map
_version: str                      # "v1"
```

**Optional fields:**
```
right_brain: dict                  # RBTM advisory (when RBTM_ENABLED)
runner_state: dict                 # Live runner state (when LIVE_RUNNER_ENABLED)
prop_protection: dict              # Prop Firm guard state (when PROP_PROTECTION_ENABLED)
```

---

## Execution Gateway Interface

**Purpose:** Single broker-facing interface. Translates canonical intent to provider payload.

**Version:** `v1`

**Input contract:**
```
instrument: str                    # canonical instrument token
direction: str                     # "long" | "short"
action: str                        # "buy" | "sell"
contracts: int                     # number of contracts (integer, ≥1)
stop: float                        # stop price
target: float                      # primary target price
rr_num: float                      # risk:reward ratio
strategy: str                      # active strategy name
source: str                        # "auto" | "manual" | "preview" | "manual_desk"
```

**Output contract:**
```
outcome: str                       # "sent" | "paper" | "manual_required" | "rejected" | "timeout" | "invalid_payload"
provider: str                      # "traderspost" | "pickmytrade" | "paper" | "manual_only"
timestamp: str                     # ISO 8601 UTC
gateway_result: dict               # full result including HTTP status (if sent)
```

---

## Journal Interface

**Purpose:** Accepts trade lifecycle events and produces permanent records.

**Version:** `v1`

**Input contract (trade open):**
```
instrument: str
direction: str
contracts: int
entry_price: float
stop: float
target: float
grade: str
edge_score: int
strategy: str
source: str
opened_at: str                     # ISO 8601 UTC
```

**Input contract (trade close — additional fields):**
```
exit_price: float
result_r: float
dollar_pnl: float
closed_at: str                     # ISO 8601 UTC
close_reason: str                  # "target" | "stop" | "manual" | "early_exit"
```

**Output contract:**
```
journal_id: str                    # strategy_trades primary key (on INSERT success)
discord_sent: bool                 # True if main channel Discord post succeeded
journal_sent: bool                 # True if journal channel Discord post succeeded
db_written: bool                   # True if strategy_trades INSERT succeeded
```

---

## Coach Interface

**Purpose:** Accepts completed trade records and produces learning outputs for downstream edge scoring.

**Version:** `v1`

**Input contract:**
```
instrument: str
mode: str                          # "SCALP" | "SWING" | "MICRO_SCALP"
strategy: str
result_r: float
setup_snapshot: dict               # thesis, MI, analyst at entry time
```

**Output contract:**
```
weight_updated: bool               # True if strategy_weights recompute ran
thesis_resolved: bool              # True if thesis_snapshots resolve ran
learning_influence: float          # ±15 modifier for next edge score (0.0 if not yet computed)
rule_engine_eligibility: str       # "GHOST_ONLY" | "LIVE_ELIGIBLE"
```

---

---

# SECTION 8 — ACCEPTANCE CRITERIA

## Overview

These are the measurable completion requirements for Version 1. Every criterion is binary (pass/fail). Version 1 is not complete until all criteria pass.

---

## Category 1: Platform Startup

| # | Criterion | Pass Condition |
|---|---|---|
| 1.1 | Platform starts correctly | Flask process reaches "ready" state within 3 seconds. All `*_DB_READY` flags True. No uncaught exceptions during boot. |
| 1.2 | Market state cache restores | ALERT_HISTORY, CVD, and AUTO_FIRED_KEYS are restored from `market_state_cache` table on boot. Restored values pass their freshness window check. |
| 1.3 | Active trades restore correctly | `open_trades` table entries are loaded as INERT on boot. No phantom position is treated as ACTIVE without operator confirmation. |
| 1.4 | Auto-trade arm resets on boot | All instrument arm states initialize to False (OFF) regardless of previous session state. |
| 1.5 | All instruments initialize | MGC, MNQ, MES, MYM all have initialized in-memory stores after boot. Per-instrument analysis is available for all four. |

---

## Category 2: Operator Mode

| # | Criterion | Pass Condition |
|---|---|---|
| 2.1 | Operator Mode loads successfully | Dashboard renders within 5 seconds of load. Main Brain panel displays. Instrument tabs visible and switchable. No console errors on load. |
| 2.2 | Session status is accurate | Market session indicator correctly shows OPEN / HALT / CLOSED based on current CME/COMEX hours. Transitions correctly at 17:00 ET (halt start) and 18:00 ET (halt end). |
| 2.3 | READY verdict reaches operator | When gate conditions are all met, READY badge appears on the correct instrument tab. Grade shown. Trade plan visible. ENTER button enabled. |
| 2.4 | WAIT verdict explains itself | When gate produces WAIT, `strict_reason` is displayed in plain language. The specific missing condition is named. No unexplained WAIT states. |
| 2.5 | ENTER button respects gate | ENTER is disabled when verdict is WAIT or market is closed. ENTER is enabled when verdict is READY. |
| 2.6 | Active trade panel displays | When a trade is open, the active trade panel shows: entry price, stop, target, current P&L in R and dollars, thesis validity. No trade panel visible when no trade is open. |
| 2.7 | Instrument tab switching works | Switching from MGC to MNQ tab loads MNQ-specific analysis within 3 seconds. Per-instrument VWAP, verdict, and trade plan are correct for the selected instrument. |
| 2.8 | Best setup auto-selected on load | Platform automatically selects the highest-probability instrument on load (actionable > WAIT, then edge_score). Selection does not change if operator has manually selected a tab. |

---

## Category 3: Engineering View

| # | Criterion | Pass Condition |
|---|---|---|
| 3.1 | Engineering View functions | All Engineering View panels load. Per-gate diagnostics show PASS/FAIL for each gate condition. Eval metrics show request counts. |
| 3.2 | Engineering View is isolated from Operator Mode | Operator Mode does not display per-gate PASS/FAIL tables, eval metrics, raw alert history feed, or any DIAGNOSTIC-tier content. These are visible only in Engineering View. |
| 3.3 | Diagnostics require auth | `/diagnostics` and `/diagnostics-live` endpoints require owner authentication. Unauthenticated requests return 401 or redirect to login. |

---

## Category 4: Decision Pipeline

| # | Criterion | Pass Condition |
|---|---|---|
| 4.1 | Decision pipeline completes within budget | A READY webhook to READY verdict takes <500ms end-to-end in normal operating conditions. |
| 4.2 | Gate produces correct verdict | Given a test set of known signals, the gate produces the expected READY or WAIT verdict for each. Parity test (`check_parity.sh`) passes. |
| 4.3 | Edge score components are correct | Given a fixed signal set, each EDGE_COMPONENT contributes its documented points. Total score matches expected value. Scalp golden test (`check_scalp_golden.sh`) passes. |
| 4.4 | WAIT verdict always has a named reason | No READY/WAIT verdict is produced with `strict_reason: ""` when verdict is WAIT. |
| 4.5 | Dual-sim parity holds | Dual-sim test (`check_dual_sim.sh`) passes — parallel analysis produces byte-identical results to sequential analysis. |
| 4.6 | Breakout mode parity holds | Breakout mode test (`check_breakout_mode.sh`) passes — flag-OFF is byte-identical to pre-feature baseline. |
| 4.7 | Structure bridge always active | SWEEP_RECLAIM and MICRO_CHOCH webhooks always inject synthetic structure into ALERT_HISTORY regardless of flag state. Gate correctly reflects injected structure. |

---

## Category 5: Execution Gateway

| # | Criterion | Pass Condition |
|---|---|---|
| 5.1 | Execution gateway functions | In paper mode, sending an ENTER request produces a `gateway_result.outcome: "paper"` with no HTTP call to the broker. |
| 5.2 | No duplicate executions | Sending the same READY setup signal twice does not produce two broker calls. `AUTO_FIRED_KEYS` dedup key prevents re-entry. |
| 5.3 | Broker payload validation fires | A canonical intent missing a required field (e.g., no `ticker`) is rejected locally before any HTTP call. `gateway_result.outcome: "invalid_payload"`. |
| 5.4 | Training Mode suppresses execution at stage < 4 | With `TRAINING_MODE_ENABLED=1` and stage 1–3, auto-trade fires produce `gateway manual_required: None` (no broker call). Stage 4 passes through. |
| 5.5 | Safety kill switch blocks execution | With kill switch active for an instrument, all execution attempts for that instrument are blocked. `gateway_result.outcome: "rejected"`. |

---

## Category 6: Journal

| # | Criterion | Pass Condition |
|---|---|---|
| 6.1 | Journal captures completed trades | After a trade closes, a row exists in `strategy_trades` with correct instrument, direction, entry_price, exit_price, result_r, and closed_at. |
| 6.2 | READY card fires once per setup | A READY verdict produces exactly one Discord card per setup (not per webhook). Re-post occurs only after `TRADE_READY_INTERVAL` elapses. |
| 6.3 | Journal Discord sends are gated | In development (`DISCORD_LIVE_ENABLED=False`), no Discord embeds are sent. In production, embeds fire correctly. |
| 6.4 | Journal failure does not crash platform | A simulated `strategy_trades` INSERT failure produces an ERROR log but does not prevent the next webhook from being processed. |

---

## Category 7: Feed Recovery

| # | Criterion | Pass Condition |
|---|---|---|
| 7.1 | Recovery works after temporary feed interruption | After a simulated 60-second gap in TradingView webhooks, the platform correctly returns WAIT (stale VWAP triggers freshness gate). After feed resumes and VWAP updates, gate evaluates normally. |
| 7.2 | Databento disconnection is transparent | With `DATABENTO_ENABLED=1` and Databento feed dropped, Engineering View shows OFFLINE, platform continues on yfinance ATR, and no READY signal is blocked. |
| 7.3 | Database disconnection degrades gracefully | With simulated DB unavailability, `*_DB_READY` flags go False, Coach features show "Unavailable," but gate verdict and execution continue from in-memory state. |

---

## Category 8: Operator Interface Responsiveness

| # | Criterion | Pass Condition |
|---|---|---|
| 8.1 | Dashboard poll is non-blocking | `/status` endpoint responds within 300ms under normal conditions. Poll does not overlap with previous poll (client tick guard active). |
| 8.2 | Panel collapse persists across refresh | Collapsing a panel in Operator Mode and refreshing the page retains the collapsed state (localStorage persistence). |
| 8.3 | Instrument tab memory persists | The selected instrument tab is retained across page refresh (localStorage persistence). |
| 8.4 | No console errors in Operator Mode | Loading Operator Mode with a valid session produces zero JavaScript console errors. |
| 8.5 | Engineering View does not affect Operator Mode | Actions taken in Engineering View (viewing diagnostics, checking eval metrics) do not alter the data displayed in Operator Mode. |

---

---

*SYSTEM_ARCHITECTURE_V1.md — AI Trading Partner*
*Version 1.0 — July 2026*
*Architecture specification only. This document governs all future development, onboarding, validation, and long-term maintenance.*
*DO NOT modify application code based on this document. DO NOT modify APIs. DO NOT rename files.*
