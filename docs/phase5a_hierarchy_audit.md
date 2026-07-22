# Phase 5A — Brain Decision Hierarchy Audit and Contract Design

**Date:** 2026-07-22  
**Scope:** Audit and design only. No code was modified.  
**Source file:** `artifacts/tradingview-webhook/app.py` (~61 K lines)

---

## Table of Contents

1. [Current Decision-Order Diagram](#1-current-decision-order-diagram)
2. [Exact Functions at Every Level](#2-exact-functions-at-every-level)
3. [Verdict Enum and Creation Paths](#3-verdict-enum-and-creation-paths)
4. [Strategy Eligibility Matrix](#4-strategy-eligibility-matrix)
5. [Hard-Block vs Scoring-Component Matrix](#5-hard-block-vs-scoring-component-matrix)
6. [Risk-Approval Matrix](#6-risk-approval-matrix)
7. [Execution-Plan Ordering](#7-execution-plan-ordering)
8. [Active-Trade State Machine](#8-active-trade-state-machine)
9. [Conflicts and Accidental Precedence](#9-conflicts-and-accidental-precedence)
10. [Proposed Canonical Hierarchy](#10-proposed-canonical-hierarchy)
11. [Proposed Canonical Decision States](#11-proposed-canonical-decision-states)
12. [Proposed Decision-Trace Schema](#12-proposed-decision-trace-schema)
13. [Scenario Table](#13-scenario-table)
14. [Migration Plan](#14-migration-plan)
15. [Test Plan](#15-test-plan)
16. [Code Modification Confirmation](#16-code-modification-confirmation)

---

## 1. Current Decision-Order Diagram

The current system has **no single orchestrating function** that runs the full hierarchy in order. Decision logic is spread across `full_analysis()`, `evaluate_strict_setup()`, `execute_trade_gateway()`, and numerous helper layers that run at different call sites. The effective order of operations, reconstructed from the code, is:

```
WEBHOOK INGESTED
      │
      ▼
[A] INSTRUMENT RESOLUTION
      │  _instrument_from_text()
      │  alert_type_for()
      │  Fail-closed: unknown instrument → logged, not processed
      │
      ▼
[B] MARKET SESSION CHECK
      │  market_session_status(now)
      │  If market closed → full_analysis() sets closed-override block, returns early
      │  All keys still present (closed-override key parity)
      │
      ▼
[C] DATA INGESTION AND STATE UPDATE
      │  Update: CVD_BY_TICKER, VWAP_BY_TICKER, ZONE_STATE_BY_INST,
      │           ALERT_HISTORY (deque, list() snapshot safe)
      │           STRUCTURE_BY_INST, HTF_STATE_BY_INST (SWING only)
      │  Auto-fetch VWAP via yfinance (GC=F proxy for MGC, NQ=F for MNQ)
      │  Persist: market_state_cache (Postgres) for CVD/dedup/ALERT_HISTORY
      │
      ▼
[D] PRICE AND DATA FRESHNESS CHECKS (partially gated, partially display-only)
      │  _me_instrument_obs(): price < 10 min, VWAP < 120 min
      │  compute_main_brain_bus(): price_fresh (<60s for scalp)
      │  _htf_tf_view(): 1H/4H/1D bar age vs SWING_HTF_*_STALE_MIN
      │  alert_stale check: signal age > threshold → demote READY→WAIT
      │  _compute_market_env_inner(): freshness_count coverage (<50% → INSUFFICIENT)
      │  NOTE: No unified DataIntegrity gate object. Checks are distributed.
      │
      ▼
[E] MARKET STATE CLASSIFICATION
      │  calculate_bias(): bullish/bearish/choppy from alert score gap
      │  get_market_structure() + classify_structure(): Bullish/Bearish/Range/Breakout
      │  get_volatility(): ATR ratio → NORMAL/QUIET_CAUTION/HIGH_CAUTION/BLOCK
      │  market_session_status(): session window + preferred session bonus
      │  compute_swing_context(): 1H/4H/Daily HTF bias (SWING only, flag-gated)
      │  CVD_BY_TICKER: directional flow
      │  Cross-market: _compute_cross_market_alignment() (display+notify only)
      │  NOTE: No canonical MarketState object. Results are separate dict keys.
      │
      ▼
[F] STRATEGY SELECTION (implicit, not explicit)
      │  TRADING_MODE env (SCALP | SWING | MICRO_SCALP) → cfg() dispatcher
      │  Swing Mode V2 (SWING_MODE_V2_ENABLED, default OFF)
      │  Dual-TF Engine (DUAL_TF_ENGINE, default OFF, SCALP only)
      │  Fast Entry Trigger (FAST_ENTRY_TRIGGER, default OFF, SCALP only)
      │  ORB: _compute_orb_engine() (display+advisory only, no auto-execute)
      │  Micro Scalp: micro_scalp.py ghost ledger (LIVE arm via gateway)
      │  Swing Strategy Library (SWING_STRATEGY_FILTER_ENABLED, default OFF)
      │  MI Strategy Filter (MI_STRATEGY_FILTER_ENABLED, default ON)
      │  NOTE: No explicit eligibility ranking. Mode is set globally, not per-call.
      │
      ▼
[G] SETUP EVALUATION — evaluate_strict_setup()
      │  1. Zone gate: trade-side zone mitigated + reaction required
      │  2. VWAP gate: price on correct side
      │  3. Structure gate: fresh BOS/CHOCH/HH-HL/LH-LL in direction
      │  4. CVD hard filter: direction-opposing CVD → block (fail-open if missing)
      │  5. Volatility: SWING hard-block; SCALP score-adjust + vol brake
      │  6. Edge score: compute_trade_edge_components() → 0-110
      │  7. Score floor: FULL_READY floor (default 50+) or EARLY floor (SCALP only)
      │  8. Cooldown: active cooldown → WAIT
      │  9. MI Structure Fallback (SCALP, default ON): Directional Confidence
      │     can satisfy structure gate if CVD agrees and is fresh
      │
      ▼
[H] THESIS HYSTERESIS LAYER — _apply_thesis()
      │  Confidence 0-100 with inertia (_THESIS_MAX_CONF_DROP cap on drop)
      │  HOLD_READY_THRESHOLD: maintains READY if briefly flickering
      │  Hard invalidation: zone_broken, struct_lost → confidence→0, COOLDOWN
      │  Generates: thesis state, confidence, alert_level
      │
      ▼
[I] SAFETY VETO LAYERS (demote-only, each applied sequentially)
      │  a. Trend Brake (_trend_brake_reason): fights both HTF + VWAP → WAIT
      │  b. Volatility Hard Gate (SWING VOL_HARD_GATE): extreme ATR → WAIT
      │  c. Swing Strategy Filter (_apply_swing_strategy_filter): strategy mismatch → WAIT
      │  d. MI Strategy Filter (_mi_strategy_filter_veto): unambiguous opposing MI → WAIT
      │  e. Structure Reversal Demote (STRUCTURE_REVERSAL_DEMOTE_ENABLED): fresh
      │     opposite BOS/CHOCH > CONFLICT_WINDOW_MIN → stale side nulled
      │  f. Entry Quality Veto (ENTRY_QUALITY_GATE_ENABLED, default ON):
      │     location score <70 AND edge <90 → WAIT
      │  g. Learning Engine Veto (flag-gated, default OFF): loss streak → WAIT
      │  h. Analyst Veto (compute_analyst_reasoning): AI disagrees → WAIT
      │  i. Pro Review Veto (compute_pro_review): strategy oversight → WAIT
      │  j. Trade Debate Veto (trade_debate): Bull/Bear/Judge final_verdict → WAIT
      │  k. Advisor Auto-trade Review Gate (ADVISOR_REVIEW_GATE, default OFF): WAIT
      │  l. Bot Training Mode (TRAINING_MODE_ENABLED): stage <4 → suppress send
      │
      ▼
[J] VERDICT ASSEMBLY
      │  Final verdict: LONG READY / SHORT READY / LONG EARLY READY /
      │                 SHORT EARLY READY / WAIT / NO TRADE
      │  is_actionable(): True iff READY (any direction/tier)
      │  DPv2 shadow (DECISION_PIPELINE_V2_ENABLED, default OFF):
      │    parallel shadow pipeline; CAN_CHANGE_VERDICT default OFF
      │
      ▼
[K] DISPLAY AND ANALYST LAYERS (all display-only, after verdict)
      │  compute_main_brain() — 7-key cognitive layer
      │  compute_analyst_reasoning() — analyst report (may veto if flag ON)
      │  compute_trade_debate() — Bull/Bear/Judge
      │  compute_pro_review() — strategy-specific grader
      │  compute_governor() — learning-based nudge
      │  compute_shared_trade_memory() — similar-history lookup
      │  compute_unified_analyst_report() — consolidated display block
      │  Unified Learning Brain / Playbook Selector (display-only)
      │  Scalp Strategy Advisory (display-only, all 16 research strategies)
      │  Breakout Mode Advisory (display-only, 09:30 ORB)
      │
      ▼
[L] TRADE PLAN CREATION — build_strict_trade_plan() / generate_trade_plan()
      │  Entry zone, stop, TP1, TP2, R:R, contracts
      │  Plan exists: always computed when opportunity detected
      │  A potential_plan exists even in WAIT/FORMING states (display-only)
      │  Executable plan: requires READY verdict
      │
      ▼
[M] BRAIN CONTRACT ASSEMBLY — getBrain() / buildLegacyFallback()
      │  Publishes d.brain to dashboard
      │  Keys: decision.{verdict,next_action,is_ready}, score.{value,grade},
      │        reasons.top, instrument, freshness
      │
      ▼
[N] ALERT AND EXECUTION GATE
      │  Auto-execute: _enqueue_auto_trade() if armed + is_actionable
      │  Manual ENTER: /traderspost endpoint → execute_trade_gateway()
      │
      ▼
[O] EXECUTION GATEWAY — execute_trade_gateway()
      │  Re-validates: market open, emergency_disabled, daily_loss_cap
      │  Prop guard: evaluate_prop_guard() (hard-block if enabled)
      │  Duplicate guard: AUTO_FIRED_KEYS fingerprint
      │  Loss streak / Asia floor / correlated cooldown (auto only)
      │  Sends to broker via adapt_traderspost() (or paper/manual)
      │
      ▼
[P] POST-EXECUTION TRACKING
      │  set_active_trade() → persists to open_trades (Postgres)
      │  _register_managed_trade() → MANAGED_TRADES_BY_KEY lifecycle
      │  _persist_swing_thesis() for SWING
      │  Discord journal card, analytics sidecar
```

**Key structural problem:** Steps D through I are not a clean linear sequence. Several veto layers (I-a through I-l) run inside `full_analysis()` but are interleaved with score computation (G), not applied strictly after. The trade plan (L) is computed before the Brain Contract (M) but the Brain Contract is what the dashboard shows—creating a risk that plan and display are momentarily inconsistent.

---

## 2. Exact Functions at Every Level

### Level 0 — Data Integrity

| Check | Function / Line | Failure Behavior |
|-------|----------------|-----------------|
| Instrument resolution | `_instrument_from_text()` | Fail-closed: returns None → alert dropped |
| Alert type mapping | `alert_type_for()` | Unknown type → logged, not processed |
| Price freshness | `_me_instrument_obs()` | `data_available: False` → dashboard muted |
| VWAP freshness | `_me_instrument_obs()` | VWAP stale flag; gate never trades on stale VWAP |
| Price fresh for scoring | `compute_main_brain_bus()` | `price_fresh` bool; display-only consequence |
| HTF bar staleness | `_htf_tf_view(rec, tf)` | `compute_swing_context` → `stale: True, complete: False` |
| Signal age (alert_stale) | `evaluate_strict_setup()` lines 7648-7666 | Demotes READY → WAIT |
| Market-env coverage | `_compute_market_env_inner()` | Coverage <50% → `INSUFFICIENT` classification |
| VWAP status in BCR | `brain_conflict_resolver()` Priority 1 | Hard veto if VWAP is "stale" or "unavailable" |
| Session availability | `market_session_status(now)` | Closed → full_analysis closed-override block |
| Cross-instrument guard | `_instrument_from_text()` + per-inst stores | Per-instrument state stores prevent leakage |
| Null/malformed fields | Stable schema pattern (`compute_swing_context`) | Always returns same keys with `null`/`unknown` |

**Failure behaviors by type:**
- **Blocks analysis:** Market closed (closed-override short-circuits full_analysis)
- **Blocks execution only:** Stale VWAP (BCR hard veto), VWAP freshness gate
- **Reduces score:** Signal age staleness (demotes to WAIT)
- **Display-only:** price_fresh flag, market-env coverage

**Missing:** There is no unified DataIntegrity state object. Freshness checks are scattered across 8+ functions with no single place that can answer "is data valid?" before the evaluation begins.

### Level 1 — Market State

| Component | Function | Update Frequency | Instrument Scoped |
|-----------|----------|-----------------|-------------------|
| Direction/Bias | `calculate_bias()` | Per webhook | Per-instrument (ALERT_HISTORY) |
| Market structure | `get_market_structure()` + `classify_structure()` | Per structure alert | Per-instrument (STRUCTURE_BY_INST) |
| Volatility regime | `get_volatility(ticker)` | Per price update | Per-instrument |
| Session state | `market_session_status(now)` | Real-time | Global (CME/COMEX shared calendar) |
| Preferred session | `get_session_state()` | Real-time | Global |
| VWAP context | `get_vwap(ticker)` | Per VWAP alert / auto-fetch | Per-instrument |
| CVD direction | `CVD_BY_TICKER` store | Per CVD alert | Per-instrument |
| HTF bias (SWING) | `compute_swing_context()` | Per HTF alert / `_refresh_htf_if_due()` | Per-instrument |
| Cross-market | `_compute_cross_market_alignment()` | Per price update | Global (MNQ/MES/MYM) |
| Event risk | `ForexFactory news` scraper | Periodic | Global |
| Trend bias (1H/4H) | `_trend_brake_reason()` | Per call | Per-instrument |

**Authoritative source:** No single canonical MarketState object. Each component lives in its own store (`CVD_BY_TICKER`, `STRUCTURE_BY_INST`, `HTF_STATE_BY_INST`, etc.).

**Duplicated sources:**
- Bias is computed from ALERT_HISTORY score gap (general) AND from STRUCTURE_BY_INST (structural) AND from HTF_STATE_BY_INST (swing). Three different bias signals that can disagree.
- CVD direction stored in `CVD_BY_TICKER` AND ingested again into `full_analysis` alert_diagnostics block.
- Volatility: `get_volatility()` returns raw ATR data; `_trend_brake_reason()` independently fetches swing_ctx ATR.

**Conflicting classifications:**
- Alert score bias = Bullish but structure = Bearish → "Mixed Alerts" → WAIT
- HTF 1H bias = Bull but 4H bias = Bear → `aligned_long = False` → trend brake fires

**Whether state currently blocks or demotes:**
- Volatility BLOCK state: hard-blocks SWING (via `VOL_HARD_GATE`); score-adjusts SCALP
- CVD conflict: hard-blocks direction if present and opposing
- HTF stale: SWING fails closed (WAIT); SCALP unaffected
- Cross-market misalignment: display+notify only; never blocks

### Level 2 — Strategy Layer

See [Section 4 — Strategy Eligibility Matrix](#4-strategy-eligibility-matrix) for full details.

### Level 3 — Setup Evaluation

See [Section 5 — Hard-Block vs Scoring-Component Matrix](#5-hard-block-vs-scoring-component-matrix) for full details.

**Edge score ordering (current):**

```
1. compute_trade_edge_components()     ← raw boolean flags → score
2. _analysis_edge_breakdown()          ← bridge: full_analysis result → edge_entry
3. compute_edge_breakdown()            ← applies modifiers, grades
4. evaluate_strict_setup()             ← applies gates + floors + early-floor
5. _apply_thesis()                     ← hysteresis layer on top
6. Safety vetoes (a-l)                 ← demote-only after score is set
```

### Level 4 — Decision

See [Section 3 — Verdict Enum](#3-verdict-enum-and-creation-paths).

### Level 5 — Risk Approval

See [Section 6 — Risk-Approval Matrix](#6-risk-approval-matrix).

### Level 6 — Execution Plan

See [Section 7 — Execution-Plan Ordering](#7-execution-plan-ordering).

### Level 7 — Active Trade Management

See [Section 8 — Active-Trade State Machine](#8-active-trade-state-machine).

---

## 3. Verdict Enum and Creation Paths

### All Current Verdict Values

| Verdict | Actionable | Created By | Conditions |
|---------|-----------|-----------|------------|
| `LONG READY` | ✅ | `evaluate_strict_setup()` | All gates pass, score ≥ FULL floor, bias Long |
| `SHORT READY` | ✅ | `evaluate_strict_setup()` | All gates pass, score ≥ FULL floor, bias Short |
| `LONG EARLY READY` | ✅ (half-size) | `evaluate_strict_setup()` | SCALP only: score ≥ EARLY floor but < FULL floor, bias Long |
| `SHORT EARLY READY` | ✅ (half-size) | `evaluate_strict_setup()` | SCALP only: score ≥ EARLY floor but < FULL floor, bias Short |
| `WAIT` | ❌ | `evaluate_strict_setup()` + any veto | Default when gates fail or any veto fires |
| `WATCH` | ❌ | `full_analysis()` informational | Setup forming but gates not yet met |
| `SETUP BUILDING` | ❌ | display/informational only | No distinct code path; label in UI |
| `BLOCK` | ❌ | `brain_conflict_resolver()` | Internal BCR hard-veto label |
| `ALLOW` | ❌ | `brain_conflict_resolver()` | Internal BCR pass label |
| `NO TRADE` | ❌ | `market_session_status()` / analyst fail | Market closed or engine unavailable |
| `INSUFFICIENT_DATA` | ❌ | DPv2 shadow only | Environmental/regime data missing in shadow |

**`is_actionable(verdict)` returns True only for:** `LONG READY`, `SHORT READY`, `LONG EARLY READY`, `SHORT EARLY READY`

### Precedence Rules (Current — Implicit)

```
NO TRADE (market closed override)           ← highest precedence
    │
WAIT (from any hard gate failure)
    │
WAIT (from any veto layer a-l)
    │
LONG/SHORT EARLY READY (SCALP, score in EARLY band)
    │
LONG/SHORT READY (all gates + full floor)   ← only if all above pass
```

**Problems:**
- Precedence is implicit: there is no enum with explicit ordinal values.
- `BLOCK` and `ALLOW` from the BCR are internal labels; they do not correspond to operator-visible verdicts.
- `EARLY READY` and `READY` carry direction in the string (`LONG`/`SHORT`), which makes programmatic comparison fragile.
- `WATCH` and `SETUP BUILDING` are not produced by a distinct code path; they appear as UI labels derived from incomplete gate passes.
- The DPv2 shadow produces `INSUFFICIENT_DATA` but this never reaches the operator-facing Brain Contract when `CAN_CHANGE_VERDICT = False`.

---

## 4. Strategy Eligibility Matrix

| Strategy ID | Enable Function / Flag | Mode | Session Req | Volatility Req | Structure Req | Hard Blocks | Parallel w/ Others | Conflict Resolution | Status |
|-------------|----------------------|------|-------------|---------------|---------------|-------------|-------------------|--------------------|----|
| **SCALP** | `TRADING_MODE=SCALP` (env) | Primary | Any (bonus in prime) | Score-adjust; vol brake at ATR>2.0x | Structure gate (can be satisfied by MI fallback) | Zone gate, VWAP gate, CVD veto | No — mode is global | Mode selected globally; only one active | **Production** |
| **SWING** | `TRADING_MODE=SWING` (env) | Primary | Any | Hard-block at ATR extreme (`VOL_HARD_GATE`) | Hard structure gate (no fallback) | Zone gate, VWAP gate, CVD veto, HTF stale | No — mode is global | Mode selected globally | **Production** |
| **MICRO SCALP** | `MICRO_SCALP_MODE` (TRADING_MODE value) | Primary | Any | Not audited separately | Sweep→trap→trigger engine | Ghost ledger always; LIVE arm via gateway | No — replaces SCALP | N/A | **Production (experimental)** |
| **DUAL-TF ENGINE** | `DUAL_TF_ENGINE=1` (env, default OFF) | SCALP overlay | Any | Inherits SCALP limits | 1m bias + ≥2 confirms (CVD/sweep/volume) within 10s | VWAP/DELTA never count as confirms; no entry trigger alone | Overlays SCALP | Requires SCALP mode; overrides base SCALP entry logic | **Experimental (default OFF)** |
| **FAST ENTRY** | `FAST_ENTRY_TRIGGER=1` (env, default OFF) | SCALP overlay | Any | Inherits SCALP | Requires valid/aligned HTF setup already | Never creates/overrides; requires SCALP, not DUAL_TF | Overlays SCALP | Shares FULL-READY auto-fire key; prevents double-enter | **Experimental (default OFF)** |
| **ORB / Breakout Mode** | `BREAKOUT_MODE_ENABLED` (env, default OFF) | Advisory overlay | 09:30 ET only | Not gated | ORB-specific levels | Phase-D auto-execute deliberately NOT built | Advisory only | No conflict with base mode | **Display-only (default OFF)** |
| **SWING MODE V2** | `SWING_MODE_V2_ENABLED` (env, default OFF) | SWING alternative | Any | Not separately gated | 9-category HTF scorer | SCANNING→READY lifecycle | No — replaces SWING logic | N/A | **Experimental (default OFF)** |
| **SWING STRATEGY LIBRARY** | `SWING_STRATEGY_FILTER_ENABLED` (env, default OFF) | SWING filter | Any | N/A | Inherits SWING | Strategy mismatch → WAIT (demote-only) | Applies over SWING | Operator-selected in-memory | **Experimental (default OFF)** |
| **MI STRATEGY FILTER** | `MI_STRATEGY_FILTER_ENABLED` (env, default ON) | SCALP money-path | Any | N/A | N/A | Unambiguous opposing market state → WAIT | Demote-only over SCALP | Ambiguous/None → fail-open | **Production (default ON)** |
| **LIQUIDITY SWEEP FOCUS** | `LIQUIDITY_SWEEP_FOCUS_ENABLED` (env, default OFF) | Advisory | Any | N/A | N/A | None | Display/advisory only | N/A | **Display-only (default OFF)** |
| **SCALP RESEARCH / SIM** | `SCALP_RESEARCH_ENABLED` | Research | Any | N/A | N/A | Walled off from money path | Research only | N/A | **Research/display-only** |
| **BACKTEST** | `/backtest/*` owner-only | Research | N/A | N/A | N/A | Walled off from money path | N/A | N/A | **Research-only** |
| **MANUAL DESK** | `MANUAL_ORDER_ENABLED` (default OFF) | Manual override | Any | N/A | Bypasses gates | Server-built ATR bracket; single-slot | Bypasses gate | Unconditional entry | **Production flag-gated (default OFF)** |
| **USER-APPROVED PREVIEW** | `USER_APPROVED_PREVIEW_TAKE` (default OFF) | Manual override | Any | Max_open local gate | FORMING skip (but re-runs mode-correct vetoes) | Re-runs SCALP/SWING vetoes + flatness gate | Bypasses READY wait | PREVIEW_MAX_CONTRACTS=1 | **Production flag-gated (default OFF)** |

**Proposed eligibility result per strategy (design only):**

```
ELIGIBLE   — mode matches, session valid, no hard blocks
DEMOTED    — eligible but one or more soft conditions reduce confidence
BLOCKED    — hard gate or veto prevents READY; reason required
DISABLED   — flag is OFF or mode doesn't match
EXPERIMENTAL — flag is ON but shadow/display-only, cannot produce live execution
```

**Strategy ranking (proposed):**
1. Active trade management always outranks new-entry analysis for same instrument
2. Production modes (SCALP/SWING/MICRO_SCALP) selected by TRADING_MODE; rank = 1
3. Overlays (DUAL_TF, FAST_ENTRY) rank = 2, applied over the selected mode
4. Demote-only filters (MI_STRATEGY_FILTER, SWING_STRATEGY_LIBRARY) rank = 3
5. Advisory/display (ORB, LIQUIDITY_SWEEP_FOCUS, Scalp Research) rank = 0 (no execution)

---

## 5. Hard-Block vs Scoring-Component Matrix

### EDGE_COMPONENTS (Pure Score Additions)

| Component | Label | Points | Type | Notes |
|-----------|-------|--------|------|-------|
| `bos_confirmed` | BOS Confirmed | +20 | Score component | Any BOS in trade direction within window |
| `choch_confirmed` | CHOCH Confirmed | +20 | Score component | Change of Character in direction |
| `vwap_confirmed` | VWAP Confirmation | +15 | Score component | Price above (Long) / below (Short) VWAP |
| `liquidity_sweep` | Liquidity Sweep | +15 | Score component | Recent sweep in direction |
| `volume_confirmed` | Volume | +15 | Score component | Spike within 20min OR RVOL ≥ threshold |
| `cvd_confirmed` | CVD Agreement | +15 | Score component | CVD delta agrees with direction |
| `preferred_session` | Session Bonus | +10 | Score component | Prime session window (09:30–11:30 ET) |
| **Max total** | | **110** | | |

### Grade Thresholds

| Grade | Score Range | Operator Label |
|-------|-------------|----------------|
| A+ | ≥ 85 | Premium Setup |
| A | ≥ 70 | Strong Trade |
| B | ≥ 50 | Possible Trade |
| WAIT | < 50 | Stand Aside |

### Hard Gates (Block READY Regardless of Score)

| Gate | Flag | SCALP Behavior | SWING Behavior | Fail Mode |
|------|------|---------------|----------------|-----------|
| Zone Gate | `GATE_REQUIRE_ZONE` | **Demoted** (zone required in SWING only; SCALP: zone scores 0 but doesn't gate) | Hard block | WAIT |
| VWAP Gate | `GATE_REQUIRE_VWAP` | Hard block | Hard block | WAIT |
| Structure Gate | `GATE_REQUIRE_STRUCTURE` | Hard block (MI fallback can satisfy) | Hard block (no fallback) | WAIT |
| CVD Hard Veto | `GATE_CVD_HARD` | Hard block (fail-open if no data) | Hard block (fail-open if no data) | WAIT |
| Volatility Hard Gate | `VOL_HARD_GATE` | Score-adjust only (vol brake = demote if ATR>2.0x) | Hard block if ATR extreme | WAIT |
| Alert Staleness | Inline in `evaluate_strict_setup` | Demote | Demote | WAIT |
| Zone Mitigated/Broken | `zone_broken` | Hard block (score forced to 0) | Hard block | WAIT |
| Cooldown Active | `cooldown_active` | Hard block | Hard block | WAIT |
| Market Closed | `market_session_status` | Hard block (closed-override) | Hard block | NO TRADE |

### Score Adjustments (Not Gates)

| Modifier | Points | Context |
|----------|--------|---------|
| Learning score influence (flag-gated) | ±15 | Bounded; only when base score > 0 |
| CVD conflict penalty (soft, non-SWING) | −10 | When CVD is opposing but not hard-gated |
| Asia floor requirement | Effective +20 threshold | Auto-trade only (requires ≥70 in Asia session) |
| Volatility score (SCALP) | ±10 | NORMAL +10, Extreme −10 |
| Competing zone proximity | Penalty via edge_modifiers | Diagnostic label |
| Entry quality veto | Demotes to WAIT if score<70 AND edge<90 | Not a score adjust; veto-only |

### Contradictions Found

1. **Zone gate asymmetry:** Zone "scores 0" in SCALP if broken/mitigated (hard force), but zone not required in SCALP gate (GATE_REQUIRE_ZONE=False). A setup can be SCALP READY with zero zone score—but zone-broken forces score to zero, making this unreachable. The interaction is correct but opaque.

2. **CVD double-application:** CVD contributes +15 to edge score (component) AND is a hard gate (CVD_HARD_GATE). A trade can earn CVD score points (+15) in one mode and be hard-blocked by CVD in another, using the same indicator value. No single place documents both roles.

3. **Volume shown as failed but not blocking:** The Volume component shows "WAIT (no volume)" in alert_diagnostics, but volume is fail-open in SCALP (volume absence = 0 points, not a gate). Dashboard may show "Volume: ❌" while trade is READY.

4. **EARLY tier score ambiguity (SCALP):** EARLY requires sweep+structure but score may be below FULL floor. The score is computed the same way for both tiers; the tier is determined by the floor, not by which components fired. The same score can be EARLY or WAIT depending only on threshold configuration.

5. **Learning influence applied after gate:** Learning ±15 is applied inside `_analysis_edge_breakdown` but AFTER the strict gates in `evaluate_strict_setup`. A learning boost could lift a below-floor score to READY even when it shouldn't affect the gate outcome. Per memory: this is bounded and intentional, but the ordering is not documented in code.

---

## 6. Risk-Approval Matrix

### Phase 1: READY Verdict Gates (Pre-Execution, Run in `evaluate_strict_setup()`)

| Check | Source | Hard-Block | Runs Before READY | Appears in Brain | Manual Bypass |
|-------|--------|-----------|-------------------|-----------------|--------------|
| Active position (same instrument) | `ACTIVE_TRADES_BY_INST` | ✅ | ✅ (inside eval) | ❌ | ❌ |
| Cooldown | `cooldown_active` flag | ✅ | ✅ | `strict_reason` | ❌ |
| VWAP alignment | `get_vwap()` | ✅ | ✅ | Gate pills | ❌ |
| Structure presence | `STRUCTURE_BY_INST` | ✅ | ✅ | Gate pills | MI fallback only |
| Zone validity | `ZONE_STATE_BY_INST` | ✅ (SWING) | ✅ | Gate pills | ❌ |
| CVD direction | `CVD_BY_TICKER` | ✅ (fail-open) | ✅ | `alert_diagnostics` | ❌ |
| Volatility (SWING) | `get_volatility()` | ✅ | ✅ | Volatility monitor | ❌ |
| Min R:R | `SWING_MIN_RR` config | ✅ | During plan build | trade_plan | ❌ |
| Market session | `market_session_status()` | ✅ | ✅ | NO TRADE verdict | ❌ |

### Phase 2: Execution Gateway Checks (Run in `execute_trade_gateway()`, After READY)

| Check | Source | Hard-Block | Warning Only | Runs After READY | Manual Bypass |
|-------|--------|-----------|-------------|-----------------|--------------|
| Emergency disable | `emergency_disabled(inst)` | ✅ | ❌ | ✅ | ❌ |
| Daily loss cap | `max_daily_loss(inst)` | ✅ | ❌ | ✅ | ❌ |
| Max contracts | `TRADERSPOST_MAX_CONTRACTS` | ✅ | ❌ | ✅ | ❌ |
| Prop firm guard | `evaluate_prop_guard()` | ✅ (if enabled) | ❌ | ✅ | ❌ |
| Duplicate trade | `_TRADERSPOST_LAST` fingerprint | ✅ | ❌ | ✅ | ❌ |
| Position sizing (0 contracts) | `_risk_capped_contracts()` | ✅ | ❌ | ✅ | ❌ |
| Loss streak (auto only) | `DIRSTREAK_LOSS_COUNT` | ✅ | ❌ | ✅ (auto only) | ✅ (manual) |
| Asia floor (auto only) | Edge score ≥ 70 | ✅ | ❌ | ✅ (auto only) | ✅ (manual) |
| Bot Training Mode stage | `TRAINING_MODE_ENABLED` | ✅ (stage<4) | ❌ | ✅ | ✅ (stage≥4) |
| Market open (re-check) | `market_session_status()` | ✅ | ❌ | ✅ | ❌ |

### Prop Firm Protection Details (`evaluate_prop_guard()`)

| Rule | Block Type |
|------|-----------|
| Allowed instruments | Hard block |
| Max contracts per order | Hard block |
| Aggregate open contracts | Hard block |
| Daily loss limit | Hard block |
| Trailing drawdown (EOD) | Hard block |
| Static drawdown floor | Hard block |
| Trading hours (ET window) | Hard block |
| News proximity | Variable (warn or block) |
| Overnight/weekend hold | Hard block |

**Proposed Risk Approval result (design only):**

```python
RiskApproval = {
    "state": "APPROVED" | "APPROVED_WITH_WARNING" | "BLOCKED" | "NOT_EVALUATED",
    "blockers": [str],         # list of block reasons
    "warnings": [str],         # non-fatal concerns
    "max_contracts": int,
    "approved_risk": float,    # dollars
    "account_state": str,      # "NORMAL" | "LOSS_STREAK" | "DRAWDOWN_WARN"
    "generated_at": float      # epoch
}
```

---

## 7. Execution-Plan Ordering

### Current Order of Plan Creation

```
full_analysis() invoked (webhook or heartbeat)
    │
    ├─ generate_trade_plan()        ← always runs when opportunity detected
    │   Entry zone, stop, TP1/TP2, R:R
    │   A `potential_plan` exists even in WAIT/FORMING (display)
    │
    ├─ evaluate_strict_setup()      ← determines READY/WAIT after plan exists
    │   Plan is pre-computed; gates use plan values (e.g., SWING_MIN_RR check)
    │
    ├─ build_strict_trade_plan()    ← finalizes plan for READY verdict
    │   Uses same entry/stop/TP logic; applies strict sizing
    │
    └─ trade_plan published in result dict
           ├─ If READY: plan is "executable" (entry/stop/TP all set)
           └─ If WAIT:  plan is typically None or cleared
                        (test_k_trade_plan_absent_when_wait asserts this)
```

### Identified Problems

1. **Plan pre-computed before gates:** `generate_trade_plan()` runs before `evaluate_strict_setup()`. The plan exists momentarily for WAIT setups before being cleared. This is safe currently but creates a window where plan != verdict.

2. **ORB retarget (1:4) changes plan after READY:** The ORB engine's `engine.ready` flag can trigger a sanctioned 1:4 retarget on an already-READY setup. This is the only case where a non-primary-strategy layer mutates the trade plan.

3. **Plan direction vs Brain direction:** Brain Contract `brain.decision.verdict` carries direction (`LONG READY`). Trade plan also carries direction. If a late veto changes the verdict but the plan dict is already published, there is a brief window of mismatch. Currently mitigated by the single return path (full_analysis returns once with the final assembled dict).

4. **Paper managed-trade same-bar fill guard:** Paper watcher must skip exit-eval on bars opened at/before entry_epoch. If entry_epoch is misaligned, the paper trade "instantly fills" off pre-entry price data.

### Proposed Rule (Design Only)

> A plan may be informational while SETUP_FORMING, but an executable plan must require both `READY` decision state and `Risk Approval = APPROVED`. The plan creation step should move to after the decision state is produced, not before.

---

## 8. Active-Trade State Machine

### Current State Stores

| Store | Scope | Persistence | Purpose |
|-------|-------|-------------|---------|
| `ACTIVE_TRADES_BY_INST` | 1 slot per instrument | DB (`open_trades` table) | Tracks bot's own open position |
| `MANAGED_TRADES_BY_KEY` | N slots per (inst, dir, zone, date) | In-memory + `swing_theses` for SWING | Paper/live managed lifecycle |
| `THESIS_BY_INST` | 1 per instrument | `swing_theses` table | Confidence hysteresis |
| `MANUAL_TRADES` | N slots | DB (INSERT/SELECT) | User-entered advisory positions |

### Managed Trade Lifecycle States

```
FORMING ──────────────────────────────────────────────────┐
    │  (setup building, not yet confirmed)                 │
    ▼                                                      │
READY ─── (broker sent, waiting fill)                     │
    │                                                      │
    ▼                                                      │
ACTIVE ─── (confirmed fill, tracking)                     │
    │                                                      │
    ├─── TP1 hit ──→ HOLD (partial exit, BE armed)        │
    │                  │                                   │
    │                  ├─── TP2 hit ──→ trailing/runner    │
    │                  │                    │              │
    │                  │                    └──→ CLOSED    │
    │                  │                                   │
    │                  └─── Stop hit ──→ CLOSED (Loss)    │
    │                                                      │
    ├─── Stop hit ──→ CLOSED (Loss)                       │
    │                                                      │
    ├─── Thesis INVALIDATED ──→ COOLDOWN ──────────────────┘
    │                              │
    │                              └──→ new entry eligible after cooldown
    │
    └─── Manual /stop-managing ──→ CLOSED
```

### Thesis Invalidation Triggers

| Trigger | Function | Result |
|---------|----------|--------|
| Zone broken | `zone_broken` flag in `_apply_thesis_inner()` | confidence → 0, status = INVALIDATED |
| Structure lost | `struct_lost` flag | confidence → 0, status = INVALIDATED |
| Opposite confirmed | `opposite_confirmed` | Auto Early Exit armed (if armed) |
| Stop breached | `stop_breached` | Advisory EXIT recommendation |
| Confidence slow drain | `_THESIS_MAX_CONF_DROP` cap | Gradual; HOLD_READY_THRESHOLD prevents flash |

### New-Signal Suppression While Active

- In `webhook()`: `if ACTIVE_TRADES_BY_INST.get(inst): # don't shadow an open position` → new setups suppressed
- In auto-execute path: `ACTIVE_TRADES_BY_INST` presence prevents additional entries for that instrument
- SCALP `allow_stack` flag: can bypass this for stacked entries; bounded by `daily cap`
- Cross-instrument: suppression is per-instrument only; MNQ active does not suppress MGC

### Whether Management Uses Same Brain

- **Advisory management** (`compute_manual_trade_management()`): uses a **copy** of the active trade data; MUST NOT pass a reference (function mutates min_r/max_r fields)
- **Bot mirror** (ACTIVE_TRADES_BY_INST display in advisor box): COPY required
- **Managed trade watcher** (`_watch_managed_trades()`): operates independently of the analysis Brain; reads raw price bars, not `full_analysis` output

### Proposed Canonical Management States (Design Only)

```
ENTRY_PENDING   ← order sent, fill not confirmed
ACTIVE          ← fill confirmed, tracking
HOLD            ← partial exit taken (TP1), stop at BE
SCALE           ← adding to position (allow_stack path)
MOVE_TO_BREAKEVEN ← TP1 hit, moving stop to entry
TRAIL           ← runner leg, dynamic stop trailing
REDUCE          ← partial reduction before full exit
EXIT            ← winding down (thesis invalid or manual)
CLOSED          ← final state; no further updates
```

---

## 9. Conflicts and Accidental Precedence

### Conflict Matrix

| Conflict | Current Behavior | Current Winner | Explicit? | Recommended Winner |
|----------|-----------------|----------------|-----------|-------------------|
| Alert bias (Bullish) vs structure (Bearish) | "Mixed Alerts" → WAIT | Structure wins | ✅ Explicit in `decision_engine` | Structure (correct) |
| Edge score high (85) but VWAP gate fails | WAIT regardless of score | Gate wins | ✅ Explicit in `evaluate_strict_setup` | Gate (correct) |
| CVD component score (+15) AND CVD hard veto | Both can apply in same mode | CVD hard veto wins | ⚠️ Partially explicit; same indicator used twice | Clarify: hard veto should zero the component if it fires |
| READY verdict but prop-risk blocked | READY shown; gateway blocks at send | Prop guard wins | ✅ Explicit at gateway | Prop guard (correct; but READY should not be shown to operator without risk context) |
| READY verdict but trade plan missing | Theoretically possible (plan cleared on WAIT, gap if plan creation fails) | Plan creation failure → WAIT | ⚠️ Not fully explicit; depends on exception path | Decision should not be READY without a valid plan |
| Plan direction vs Brain direction | Single return path makes this safe normally | Simultaneous | ✅ Single dict prevents mismatch | Must remain one-shot assembly |
| Active long trade + new short setup | New setup suppressed for same instrument | Active trade wins | ✅ Explicit (ACTIVE_TRADES_BY_INST check) | Active trade (correct) |
| Manual ENTER during WAIT | Gateway allows; re-validates server-side | Manual wins (if PREVIEW/MANUAL_DESK flag ON) | ⚠️ Flag-dependent; unclear to operator | Require explicit "override acknowledged" signal |
| Confidence governor vs edge score | Both computed; governor is display-only (±nudge) | Edge score authoritative | ✅ Explicit (governor is display-only) | Edge score (correct) |
| Stale price while cached READY exists | READY may persist until next poll cycle (3s) | Cache wins until evicted | ⚠️ TTL-based; not instant | Stale data should demote immediately (requires data-integrity gate) |
| Different strategies producing opposite directions | Not possible currently (mode is global) | Global mode wins | ✅ Global mode enforces single strategy | After multi-strategy: strategy with higher priority + evidence |
| SCALP vs SWING conflict | Not possible (TRADING_MODE is singleton) | TRADING_MODE wins | ✅ Explicit | Keep as singleton; multi-strategy is Phase 5B+ |
| HTF bias (Bull 1H) vs HTF (Bear 4H) | `aligned_long = False` → trend brake fires | Most conservative (brake) | ✅ Explicit in `compute_swing_context` | Conservative (correct) |
| Analyst veto vs pro review veto (both fire) | Both demote to WAIT; same outcome | Both produce WAIT | ✅ Both produce WAIT; order irrelevant | Fine as-is; report all blockers |
| Fast entry signal vs strict confirmation | Fast entry sharpens timing only; does not override | Strict gates still required | ✅ Explicit (fast entry is timing-only) | Correct |
| Learning veto vs edge score | Learning can demote READY→WAIT; does not promote | Learning veto wins if flag ON | ✅ Explicit (demote-only) | Correct |
| DPv2 shadow vs live verdict | Shadow verdict ignored when `CAN_CHANGE_VERDICT=False` | Live verdict wins | ✅ Explicit flag | Correct while flags are OFF |
| Experimental auto-execute (EARLY tier) vs strict | EARLY fires at half-size for auto | EARLY tier allowed in auto | ✅ Explicit (`EARLY_READY` included in `is_actionable`) | Review whether EARLY auto-execute is appropriate for live |

---

## 10. Proposed Canonical Hierarchy

The following is the recommended deterministic evaluation sequence. This is a design proposal only; current code does not implement this sequence.

```
Step 0: DATA INTEGRITY CHECK
─────────────────────────────────────────────────────────────────────
  Input: instrument identifier, incoming alert payload
  Functions to consolidate:
    _instrument_from_text()     → instrument valid?
    market_session_status()     → session open?
    _me_instrument_obs()        → price fresh? VWAP fresh?
    _htf_tf_view()              → HTF bars fresh? (SWING)
    alert age check             → signal not stale?
  Output: DataIntegrity { state: VALID | DEGRADED | STALE | INVALID, blockers: [] }
  Rule: INVALID stops the pipeline. DEGRADED/STALE may continue with restrictions.

Step 1: ACTIVE TRADE RESOLUTION
─────────────────────────────────────────────────────────────────────
  Input: instrument
  Functions to consolidate:
    ACTIVE_TRADES_BY_INST.get(inst)   → is there an active position?
    MANAGED_TRADES_BY_KEY             → is there a managed trade?
    THESIS_BY_INST                    → what is the current thesis state?
  Output: ActiveTradeState { state, managed_state, thesis_confidence }
  Rule: If ACTIVE → enter management path, skip new-entry analysis.
        Return decision = MANAGE with management plan, not new entry.

Step 2: MARKET STATE CLASSIFICATION
─────────────────────────────────────────────────────────────────────
  Input: all per-instrument state stores
  Functions to consolidate into one call:
    calculate_bias()
    get_market_structure() + classify_structure()
    get_volatility()
    get_vwap()
    CVD_BY_TICKER
    compute_swing_context()     (SWING only)
    market_session_status()
  Output: MarketState {
    regime,           ← RISK_ON | RISK_OFF | MIXED | NEUTRAL | UNKNOWN
    direction,        ← BULLISH | BEARISH | CHOPPY | UNKNOWN
    volatility_state, ← NORMAL | QUIET_CAUTION | HIGH_CAUTION | QUIET_BLOCK | HIGH_BLOCK
    liquidity_state,  ← NORMAL | THIN | CLOSED
    session_state,    ← PRIME | REGULAR | EXTENDED | CLOSED
    structure_state,  ← BULLISH | BEARISH | RANGE | BREAKOUT | UNKNOWN
    event_risk,       ← NONE | LOW | HIGH (from ForexFactory)
    vwap_direction,   ← ABOVE | BELOW | UNKNOWN
    cvd_direction,    ← BULLISH | BEARISH | UNKNOWN
    htf_aligned,      ← bool (SWING only)
    freshness,        ← epoch
    primary_evidence, ← [] of supporting signals
    primary_risks     ← [] of conflicting signals
  }

Step 3: STRATEGY ELIGIBILITY
─────────────────────────────────────────────────────────────────────
  Input: MarketState, TRADING_MODE, feature flags
  Evaluate each registered strategy against market state:
    SCALP:         eligible when session open, volatility not BLOCK, mode=SCALP
    SWING:         eligible when HTF aligned, volatility not BLOCK, mode=SWING
    MICRO_SCALP:   eligible when mode=MICRO_SCALP
    DUAL_TF:       eligible when DUAL_TF_ENGINE=ON AND mode=SCALP
    FAST_ENTRY:    eligible when FAST_ENTRY_TRIGGER=ON AND mode=SCALP AND HTF aligned
    MANUAL_DESK:   eligible when MANUAL_ORDER_ENABLED=ON
    SWING_V2:      eligible when SWING_MODE_V2_ENABLED=ON AND mode=SWING
  Output: per-strategy { id, status, priority, blockers, score }
  Rule: Only one primary mode eligible at a time (TRADING_MODE is global).
        Overlay strategies (DUAL_TF, FAST_ENTRY) eligible alongside primary.
        Demote-only filters applied after eligibility, not before.

Step 4: SETUP EVALUATION
─────────────────────────────────────────────────────────────────────
  Input: MarketState, selected strategy, alert history
  Functions:
    compute_trade_edge_components()   → raw component scores
    evaluate_strict_setup()           → gates + floors
    _apply_thesis()                   → hysteresis
  Apply in order:
    a. Hard prerequisites (zone, VWAP, structure, CVD, session)
    b. Hard blockers (zone broken, cooldown, volatility extreme)
    c. Score computation (BOS+CHOCH+VWAP+Sweep+Volume+CVD+Session = 0-110)
    d. Score adjustments (learning ±15, only if base score > 0)
    e. Grade and floor check (≥50 WAIT, ≥50 EARLY, ≥FULL_FLOOR READY)
    f. Thesis hysteresis application
  Output: SetupEval { score, max_score, grade, hard_gates, failed_gates }

Step 5: SAFETY VETOES (Demote-Only Layer)
─────────────────────────────────────────────────────────────────────
  Apply in explicit priority order (lowest to highest priority = last wins):
    1. Structure Reversal Demote (STRUCTURE_REVERSAL_DEMOTE_ENABLED)
    2. Volatility Brake (SCALP_VOL_BRAKE_ENABLED)
    3. Trend Brake (_trend_brake_reason)
    4. MI Strategy Filter (_mi_strategy_filter_veto)
    5. Swing Strategy Library Filter (_apply_swing_strategy_filter)
    6. Entry Quality Veto (ENTRY_QUALITY_GATE_ENABLED)
    7. Learning Engine Veto (flag-gated)
    8. Advisor Auto-Trade Review Gate (flag-gated)
    9. Analyst Veto (compute_analyst_reasoning)
    10. Pro Review Veto (compute_pro_review)
    11. Trade Debate Veto (compute_trade_debate)
    12. Bot Training Mode (TRAINING_MODE_ENABLED)
  Rule: Each layer may only demote (READY → WAIT). None may promote.
        All blockers must be recorded for display.

Step 6: DECISION STATE
─────────────────────────────────────────────────────────────────────
  Combine Steps 0-5 into canonical decision:
    INVALID_DATA    ← Step 0 returned INVALID
    MANAGE          ← Step 1 found active trade
    NO_STRATEGY     ← Step 3 found no eligible strategy
    MONITOR         ← Step 4 score below all floors OR no setup forming
    SETUP_FORMING   ← Step 4 partial gates passed; score building
    READY           ← all gates + floor passed + no vetoes
    BLOCKED_BY_RISK ← READY but awaiting risk approval
  Direction: separate field (LONG | SHORT | null)
  Output: Decision { state, direction, reason, next_action }

Step 7: RISK APPROVAL
─────────────────────────────────────────────────────────────────────
  Only runs when Decision.state == READY
  Checks (in order):
    1. Market session (re-verify)
    2. Emergency disable
    3. Daily loss cap
    4. Prop firm protection (if enabled)
    5. Position sizing (≥1 contract)
    6. Duplicate trade fingerprint
    7. Loss streak / Asia floor (auto-trade path only)
  Output: RiskApproval { state, blockers, warnings, max_contracts, account_state }

Step 8: EXECUTION PLAN
─────────────────────────────────────────────────────────────────────
  Only builds executable plan when:
    Decision.state == READY AND RiskApproval.state == APPROVED
  Informational (potential_plan) allowed at SETUP_FORMING.
  Functions:
    build_strict_trade_plan()
    calculate_position_sizing()
    adapt_traderspost()     (at send time only, not at plan-creation time)
  Output: ExecutionPlan { available, executable, entry, stop, tp1, tp2, rr, contracts }

Step 9: BRAIN CONTRACT PUBLICATION
─────────────────────────────────────────────────────────────────────
  Assemble once, publish once. No re-computation at this step.
  Fields: brain.decision, brain.score, brain.reasons, brain.instrument,
          brain.freshness, brain.risk_approval (new), brain.execution_plan (new)

Step 10: ALERT AND EXECUTION PERMISSION
─────────────────────────────────────────────────────────────────────
  Allow Discord READY alert only when: Decision.state == READY
  Allow auto-execute only when: READY + APPROVED + arm is ON
  Allow manual ENTER only when: READY + APPROVED (or manual-desk override)
  Allow management actions always when MANAGE state is active
```

---

## 11. Proposed Canonical Decision States

```python
class DecisionState:
    INVALID_DATA   = "INVALID_DATA"    # Data integrity failed; no decision possible
    NO_STRATEGY    = "NO_STRATEGY"     # No strategy is eligible for current market state
    MONITOR        = "MONITOR"         # No actionable setup; watching
    SETUP_FORMING  = "SETUP_FORMING"   # Directional intent present; missing ≥1 prerequisite
    READY          = "READY"           # All gates + floor passed; no vetoes
    BLOCKED_BY_RISK= "BLOCKED_BY_RISK" # READY setup blocked by risk approval layer
    ACTIVE_TRADE   = "ACTIVE_TRADE"    # Instrument has an open position (management mode)
    MANAGE         = "MANAGE"          # Active trade; management decisions only
    EXIT           = "EXIT"            # Exit order in progress

class Direction:
    LONG  = "LONG"
    SHORT = "SHORT"
    NONE  = None
```

**Explicit precedence (highest wins):**

```
1. INVALID_DATA        ← outranks everything; no partial decisions
2. ACTIVE_TRADE/MANAGE ← outranks new-entry analysis for same instrument
3. BLOCKED_BY_RISK     ← READY setup exists but risk gate blocked execution
4. READY               ← eligible strategy + valid setup + no vetoes + risk approved
5. SETUP_FORMING       ← directional intent; ≥1 prerequisite missing
6. NO_STRATEGY         ← no strategy eligible for market conditions
7. MONITOR             ← default; no setup forming
8. EXIT                ← terminal action in progress
```

**Backward compatibility mapping (current → proposed):**

| Current Verdict | Proposed State | Direction | Notes |
|-----------------|---------------|-----------|-------|
| `LONG READY` | `READY` | `LONG` | Direction separated |
| `SHORT READY` | `READY` | `SHORT` | Direction separated |
| `LONG EARLY READY` | `READY` | `LONG` | Score tier = setup detail |
| `SHORT EARLY READY` | `READY` | `SHORT` | Score tier = setup detail |
| `WAIT` | `MONITOR` or `SETUP_FORMING` | depends | Reason determines which |
| `WATCH` | `SETUP_FORMING` | depends | |
| `NO TRADE` | `INVALID_DATA` or `MONITOR` | `NONE` | Market closed → INVALID_DATA |
| `BLOCK` (BCR internal) | `BLOCKED_BY_RISK` | | Surfaced to operator |
| `INSUFFICIENT_DATA` (DPv2) | `INVALID_DATA` | | |

---

## 12. Proposed Decision-Trace Schema

The following schema is proposed for future internal use. **It must not be added to the production `/status` response yet.** It is a diagnostic/engineering tool.

```json
{
  "decision_trace": {
    "instrument": "MGC",
    "generated_at": 1753230000.0,

    "data_integrity": {
      "state": "VALID | DEGRADED | STALE | INVALID",
      "blockers": ["price stale: 90s", "VWAP unavailable"]
    },

    "market_state": {
      "regime": "RISK_ON | RISK_OFF | MIXED | NEUTRAL | UNKNOWN",
      "direction": "BULLISH | BEARISH | CHOPPY | UNKNOWN",
      "volatility": "NORMAL | QUIET_CAUTION | HIGH_CAUTION | QUIET_BLOCK | HIGH_BLOCK",
      "liquidity": "NORMAL | THIN | CLOSED",
      "session": "PRIME | REGULAR | EXTENDED | CLOSED",
      "structure": "BULLISH | BEARISH | RANGE | BREAKOUT | UNKNOWN",
      "event_risk": "NONE | LOW | HIGH",
      "vwap_direction": "ABOVE | BELOW | UNKNOWN",
      "cvd_direction": "BULLISH | BEARISH | UNKNOWN",
      "htf_aligned": true,
      "freshness": 1753229950.0,
      "primary_evidence": ["BOS DEMAND @ 3020.5", "CVD Bullish", "Price above VWAP"],
      "primary_risks": ["Volatility HIGH_CAUTION", "Asia session"]
    },

    "strategies": [
      {
        "id": "SCALP",
        "status": "ELIGIBLE | DEMOTED | BLOCKED | DISABLED | EXPERIMENTAL",
        "priority": 1,
        "blockers": [],
        "score": null
      },
      {
        "id": "DUAL_TF",
        "status": "DISABLED",
        "priority": 2,
        "blockers": ["DUAL_TF_ENGINE flag OFF"],
        "score": null
      }
    ],

    "selected_strategy": {
      "id": "SCALP",
      "reason": "TRADING_MODE=SCALP"
    },

    "setup": {
      "score": 75,
      "max_score": 110,
      "grade": "A",
      "hard_gates": {
        "zone": "PASS | FAIL | N/A",
        "vwap": "PASS | FAIL",
        "structure": "PASS | FAIL",
        "cvd": "PASS | FAIL | UNKNOWN"
      },
      "failed_gates": ["zone"],
      "score_components": {
        "bos": 20, "choch": 20, "vwap": 15,
        "sweep": 15, "volume": 0, "cvd": 15, "session": 10
      },
      "score_adjustments": [
        {"reason": "learning_boost", "delta": 5}
      ]
    },

    "vetoes_applied": [
      {
        "layer": "TREND_BRAKE",
        "fired": false,
        "reason": null
      },
      {
        "layer": "ANALYST_VETO",
        "fired": true,
        "reason": "Low R:R given ATR extension"
      }
    ],

    "decision": {
      "state": "READY | MONITOR | SETUP_FORMING | INVALID_DATA | ...",
      "direction": "LONG | SHORT | null",
      "reason": "All gates passed. Score 75/110 (A). Analyst veto fired.",
      "next_action": "Wait for analyst veto to clear or reconfigure"
    },

    "risk_approval": {
      "state": "APPROVED | APPROVED_WITH_WARNING | BLOCKED | NOT_EVALUATED",
      "blockers": [],
      "warnings": ["Asia session: reduced size recommended"],
      "max_contracts": 2,
      "approved_risk": 250.0,
      "account_state": "NORMAL",
      "generated_at": 1753230000.0
    },

    "execution_plan": {
      "available": true,
      "executable": true,
      "entry": 3022.5,
      "stop": 3018.0,
      "tp1": 3029.5,
      "tp2": 3036.0,
      "rr": 2.4,
      "contracts": 1
    },

    "active_trade": {
      "state": "NONE | ENTRY_PENDING | ACTIVE | HOLD | MANAGE | EXIT | CLOSED"
    }
  }
}
```

**Field mapping to current Brain Contract:**

| decision_trace field | Already in Brain Contract | Location |
|---------------------|--------------------------|---------|
| `decision.state` | Partially (`verdict` string) | `brain.decision.verdict` |
| `decision.direction` | Embedded in verdict string | Extract from `brain.decision.verdict` |
| `setup.score` | ✅ | `brain.score.value` |
| `setup.grade` | ✅ | `brain.score.grade` |
| `decision.next_action` | ✅ | `brain.decision.next_action` |
| `data_integrity` | ❌ | Not in Brain Contract |
| `market_state` (structured) | ❌ | Scattered: `d.volatility`, `d.bias`, etc. |
| `strategies` | ❌ | Not in Brain Contract |
| `vetoes_applied` | Partial (`strict_reason`) | `d.strict_reason` |
| `risk_approval` | ❌ | Not in Brain Contract |
| `execution_plan.executable` | Partial (`trade_plan` present = executable) | `d.trade_plan` |
| `active_trade.state` | Partial (`has_active_trade`) | `d.has_active_trade` |

**Fields for diagnostics vs operator Brain Contract:**

- `data_integrity`, `strategies`, `vetoes_applied`, `risk_approval.account_state` → diagnostics (owner-only `/diagnostics-live`)
- `decision.state/direction/reason/next_action`, `setup.score/grade`, `execution_plan.available/executable`, `active_trade.state` → operator Brain Contract

---

## 13. Scenario Table

| # | Scenario | Market State | Strategy | Setup State | Decision State | Risk Approval | Execution | Operator Explanation |
|---|----------|-------------|----------|-------------|----------------|--------------|-----------|---------------------|
| 1 | Fresh data, no directional setup | Regime: NEUTRAL, Bias: CHOPPY | SCALP ELIGIBLE | No BOS/CHOCH; score 0 | MONITOR | NOT_EVALUATED | ❌ | "Watching markets. No directional structure yet." |
| 2 | Bullish trend, long strategy eligible, structure missing | Regime: RISK_ON, Bias: BULLISH | SCALP ELIGIBLE | Structure gate FAIL; partial score | SETUP_FORMING | NOT_EVALUATED | ❌ | "Bullish setup building. Waiting for BOS/CHOCH confirmation." |
| 3 | Bullish trend, long setup fully confirmed | All gates pass; score 85 | SCALP ELIGIBLE | All gates PASS; A+ setup | READY LONG | APPROVED | ✅ | "LONG READY. A+ setup (85/110). All gates confirmed." |
| 4 | Fully confirmed setup, prop-risk blocked | Same as #3 | SCALP ELIGIBLE | All gates PASS; score 85 | BLOCKED_BY_RISK | BLOCKED (prop guard) | ❌ | "READY but blocked. Prop firm daily limit reached." |
| 5 | Edge score high (90) but hard structure gate failed | Score would be 90; no BOS/CHOCH | SCALP ELIGIBLE | Structure gate FAIL | SETUP_FORMING | NOT_EVALUATED | ❌ | "High score but structure gate failed. Need BOS or CHOCH." |
| 6 | Low score (40) but all hard gates passed | All gates PASS; score 40 | SCALP ELIGIBLE | Below floor | MONITOR | NOT_EVALUATED | ❌ | "Setup conditions met but insufficient edge (40/110). Waiting." |
| 7 | Extreme volatility (ATR > 3.0x) | VOL: HIGH_BLOCK | SWING BLOCKED | Vol hard gate | MONITOR (SWING) or SETUP_FORMING (SCALP vol brake) | NOT_EVALUATED | ❌ | "Extreme volatility. Market conditions unsuitable for entry." |
| 8 | Breakout regime, countertrend strategy | Regime: BREAKOUT, Bias: BULLISH | SCALP ELIGIBLE, MI filter fires | MI_STRATEGY_FILTER blocks short | MONITOR (for Short) | NOT_EVALUATED | ❌ | "Market in breakout. Short against trend blocked by MI filter." |
| 9 | Two eligible strategies agree long | Only one primary (TRADING_MODE) | SCALP primary + FAST_ENTRY overlay | Both confirm Long; score 85 | READY LONG | APPROVED | ✅ | "LONG READY. Fast entry timing layer aligned." |
| 10 | Two eligible strategies conflict | Not possible currently (global TRADING_MODE) | Single mode | N/A | N/A | N/A | N/A | "Not currently possible: single mode active. Proposed: highest priority eligible strategy wins." |
| 11 | Active long trade, new short setup appears | ACTIVE_TRADE for MGC exists | Short setup evaluates for MGC | New setup suppressed | MANAGE (for MGC) | NOT_EVALUATED | ❌ | "Managing existing long position. New short suppressed until closed." |
| 12 | Stale price while cached READY exists | Price >60s old; VWAP stale | Any | Stale data → Data integrity DEGRADED | INVALID_DATA (proposed) / currently: may linger as READY until next poll | NOT_EVALUATED | ❌ | "Data stale. Last confirmed setup expired. Refresh required." (currently: READY may persist 3s) |
| 13 | Trade plan missing while setup otherwise READY | Plan creation failed (exception path) | Any | Gates pass; plan absent | Currently: depends on exception handling; READY without plan is possible edge case | N/A | ❌ | Proposed: READY requires valid plan. Currently: not fully guarded. |
| 14 | Manual market order during WAIT | WAIT verdict | Any | WAIT | WAIT (unless MANUAL_DESK flag ON) | BLOCKED (normal) / APPROVED (manual_desk) | ✅ (manual_desk only) | "Manual desk override active. Order placed outside strict gate." |
| 15 | Null or malformed indicator data | CVD = None; vol = None | Any | Fail-open for CVD/vol (score 0, not blocked); null schema for HTF | MONITOR (typically) | NOT_EVALUATED | ❌ | "Some indicators unavailable. Scoring conservatively." |
| 16 | Non-active instrument snapshot | Ticker not in registry | N/A | Fail-closed (_instrument_from_text = None) | INVALID_DATA | NOT_EVALUATED | ❌ | "Unknown instrument. No analysis available." |
| 17 | SWING setup vs SCALP setup conflict | Not currently possible (global mode) | Single mode | N/A | N/A | N/A | N/A | "Not applicable: single mode. Proposed: TRADING_MODE determines which evaluates." |
| 18 | Experimental fast-entry signal without strict confirmation | Fast entry flag ON, but HTF not aligned | FAST_ENTRY overlay | Timing layer requires valid/aligned HTF; if HTF absent → fast entry deferred | SETUP_FORMING or MONITOR | NOT_EVALUATED | ❌ | "Fast entry signal detected but HTF alignment not confirmed. Waiting." |

---

## 14. Migration Plan

Migration must be divided into safe phases, each independently deployable and reversible. No phase changes live behavior without its own feature flag.

### Phase 5B — DataIntegrity Gate Object (Safe)

**Scope:** Create a named DataIntegrity result from existing checks. No behavior change.

Steps:
1. Add `_compute_data_integrity(inst)` that consolidates existing freshness calls
2. Return `{state, blockers}` — same logic as today, new wrapper
3. Attach to `full_analysis` result as `result["data_integrity"]` (new key; goldens must be rebased)
4. Add to `/status` whitelist; display in `/diagnostics-live`
5. Flag gate: `DATA_INTEGRITY_GATE_ENABLED` (default OFF)
6. When ON: INVALID state short-circuits full_analysis before step E
7. Goldens: flag-OFF byte-identical; flag-ON gets own golden

### Phase 5C — Canonical MarketState Object (Safe)

**Scope:** Aggregate the 8+ scattered market-state fields into one object. No behavior change.

Steps:
1. Add `_compute_market_state(inst)` that calls existing functions and returns the proposed schema
2. Attach as `result["market_state"]` (new key)
3. Whitelist in `/status`; display on dashboard (display-only initially)
4. No behavioral change to gates or scoring
5. Goldens rebased (new key appears)

### Phase 5D — Strategy Eligibility Layer (Display-Only First)

**Scope:** Produce explicit per-strategy eligibility results. Display-only initially.

Steps:
1. Add `_evaluate_strategy_eligibility(market_state, flags)` returning the eligibility matrix
2. Attach as `result["strategy_eligibility"]` (new key)
3. Display on owner-only diagnostics
4. Flag gate: `STRATEGY_ELIGIBILITY_ENABLED` (default OFF)
5. When ON (shadow): compute eligibility but don't change verdict
6. Goldens: flag-OFF byte-identical

### Phase 5E — Decision State Enum (Backward-Compatible)

**Scope:** Add `decision.state` and `decision.direction` as separate fields alongside current `verdict` string.

Steps:
1. Map current verdict strings to proposed DecisionState enum
2. Publish as `result["decision_state"]` and `result["decision_direction"]`
3. Keep `verdict` string unchanged for backward compatibility
4. Whitelist in Brain Contract as `brain.decision.state` and `brain.decision.direction`
5. All existing consumers continue using `verdict` string
6. Goldens rebased (new keys)

### Phase 5F — Risk Approval Layer (Display-Only)

**Scope:** Surface risk-approval results without changing execution behavior.

Steps:
1. Add `_compute_risk_approval(inst, verdict, plan)` consolidating existing gateway checks
2. Attach as `result["risk_approval"]` (new key)
3. Display on dashboard when READY: show blockers/warnings before user presses ENTER
4. Execution gateway remains authoritative (unchanged)
5. Flag gate: `RISK_APPROVAL_DISPLAY_ENABLED` (default OFF)

### Phase 5G — Decision Trace (Owner-Only Diagnostics)

**Scope:** Produce full decision_trace for diagnostics. Not in Brain Contract.

Steps:
1. Assemble `result["decision_trace"]` from all previous phase outputs
2. Available only at `/diagnostics-live` (owner-only)
3. Never in `/status` public response
4. Goldens: decision_trace absent in golden (owner-only path)

### Phase 5H — Full Hierarchy Enforcement (Breaking Change; Future)

**Scope:** Reorder full_analysis to match proposed canonical hierarchy. This is a live-behavior change.

Prerequisites: Phases 5B–5G complete and validated in production.

Steps:
1. Implement orchestrator function running Steps 0–10 in order
2. Replace `full_analysis()` entry point with orchestrator
3. Validate all goldens pass
4. Run full test suite
5. Shadow for ≥1 week with `CAN_CHANGE_VERDICT=False` before enabling

---

## 15. Test Plan

### Reusable Existing Tests

| Test File | Coverage | Reuse Strategy |
|-----------|---------|----------------|
| `test_brain_contract.py` | Brain Contract field presence | Extend with `decision.state` + `direction` fields |
| `test_brain_dashboard.py` | Dashboard rendering from Brain | Extend with new `decision.state` values |
| `test_dpv2_phase1b.py`, `test_dpv2_phase2.py`, `test_dpv2_phase3.py` | DPv2 shadow pipeline | Extend for Phase 5E mapping |
| `test_confidence_integrity.py` | Confidence/hysteresis | Extend for DataIntegrity gate |
| `test_thesis_hysteresis.py` | Thesis state machine | Extend for active-trade precedence |
| `test_scalp_zone_gate.py` | Zone gate behavior | Extend with proposed zone gate rules |
| `test_market_env.py` | Market environment checks | Extend with MarketState object schema |
| `test_persistence.py` | DB persistence layer | Extend for active trade state persistence |
| `validate_dpv2_production.py` | Production DPv2 validation | Extend for Phase 5D eligibility layer |
| `.local/state/check_parity.sh` et al. | Golden parity | Must re-baseline after each new key added |

### New Tests Required

#### Suite A: DataIntegrity (Phase 5B)

```
A1: VALID state when all data fresh
A2: STALE when price > staleness threshold
A3: STALE when VWAP > staleness threshold
A4: STALE when HTF bar stale (SWING)
A5: DEGRADED when ≥1 non-critical check fails
A6: INVALID when instrument unknown
A7: INVALID blocks full_analysis pipeline (flag ON)
A8: DEGRADED does not block pipeline
A9: Flag OFF → byte-identical to current golden
A10: DataIntegrity blockers appear in diagnostics
```

#### Suite B: MarketState (Phase 5C)

```
B1: MarketState schema always complete (no missing keys)
B2: regime=RISK_ON when bias Bullish + structure Bullish
B3: regime=RISK_OFF when bias Bearish + structure Bearish
B4: volatility_state maps correctly from ATR ratio
B5: session_state = PRIME in 09:30-11:30 ET
B6: session_state = CLOSED on weekend
B7: cvd_direction = UNKNOWN when no CVD data
B8: htf_aligned=False when 1H Bull, 4H Bear
B9: event_risk = HIGH during ForexFactory high-impact window
B10: MarketState freshness = epoch of most recent component
```

#### Suite C: Strategy Eligibility (Phase 5D)

```
C1: SCALP ELIGIBLE when TRADING_MODE=SCALP and session open
C2: SWING BLOCKED when HTF stale (SWING mode)
C3: DUAL_TF DISABLED when flag OFF
C4: DUAL_TF ELIGIBLE when flag ON and mode=SCALP
C5: FAST_ENTRY DISABLED when DUAL_TF also ON (mutually exclusive)
C6: MANUAL_DESK DISABLED when flag OFF
C7: ORB EXPERIMENTAL regardless of flag (no live execution)
C8: Eligibility result always has id, status, priority, blockers
C9: Only one PRIMARY mode ELIGIBLE at a time
C10: Demote-only filters appear in vetoes_applied, not in strategy.status
```

#### Suite D: Decision State Enum (Phase 5E)

```
D1: LONG READY maps to state=READY, direction=LONG
D2: SHORT READY maps to state=READY, direction=SHORT
D3: LONG EARLY READY maps to state=READY, direction=LONG, tier=EARLY
D4: WAIT from gate fail maps to state=MONITOR
D5: WAIT from veto maps to state=SETUP_FORMING + veto reason
D6: NO TRADE (market closed) maps to state=INVALID_DATA
D7: INVALID_DATA outranks READY (simultaneous)
D8: ACTIVE_TRADE outranks READY for same instrument
D9: BLOCKED_BY_RISK shows when READY + prop guard blocks
D10: verdict string unchanged alongside new fields (backward compat)
```

#### Suite E: Risk Approval (Phase 5F)

```
E1: APPROVED when daily loss cap not reached
E2: BLOCKED when daily loss cap exceeded
E3: BLOCKED when emergency_disabled = True
E4: BLOCKED when position sizing yields 0 contracts
E5: BLOCKED when prop guard fires (any rule)
E6: APPROVED_WITH_WARNING when Asia session active
E7: NOT_EVALUATED when Decision.state != READY
E8: Risk approval runs after verdict, not before
E9: Manual ENTER passes even when auto-path BLOCKED (loss streak)
E10: max_contracts is clamped to TRADERSPOST_MAX_CONTRACTS
```

#### Suite F: Precedence and Isolation

```
F1: INVALID_DATA beats READY (simultaneous conditions)
F2: ACTIVE_TRADE beats READY for same instrument
F3: ACTIVE_TRADE for MGC does not suppress MNQ new entry
F4: BLOCKED_BY_RISK does not suppress new analysis; only blocks execution
F5: Hard gate failure beats high edge score
F6: CVD hard veto beats CVD score component (no double-count)
F7: Veto layers apply in documented order (A→L in current; 1→12 proposed)
F8: Last veto to fire is the blocking reason in strict_reason
F9: Non-actionable verdict never triggers Discord READY alert
F10: Non-actionable verdict never triggers auto-execute
```

#### Suite G: Decision Trace Schema (Phase 5G)

```
G1: decision_trace schema matches proposed shape
G2: All required keys present in every scenario
G3: generated_at is monotonically increasing
G4: decision_trace absent from /status response
G5: decision_trace available at /diagnostics-live (owner only)
G6: No field in decision_trace drives execution (display/diagnostics only)
G7: decision_trace.decision.state matches result["decision_state"]
G8: decision_trace accurate for all 18 scenario table cases
```

#### Suite H: Active Trade Precedence

```
H1: New entry suppressed when ACTIVE_TRADES_BY_INST[inst] exists
H2: Management state computed even when verdict=WAIT
H3: Auto early exit fires only on opposite_confirmed (not stop_breached)
H4: COOLDOWN prevents new entry after invalidation
H5: After CLOSED, new entry eligible for same instrument
H6: MANAGED_TRADES close persistence: is_swing thesis persisted
H7: Managed trade same-bar fill guard: no instant fill on entry bar
H8: Active trade state per-instrument (MGC active != MNQ active)
```

---

## 16. Code Modification Confirmation

**No code was modified during this audit.**

All findings are based on read-only analysis of:
- `artifacts/tradingview-webhook/app.py`
- `artifacts/tradingview-webhook/backtest_engine.py`
- `artifacts/tradingview-webhook/micro_scalp.py`
- `artifacts/tradingview-webhook/scalp_live_sim.py`
- `artifacts/tradingview-webhook/scalp_research.py`
- `artifacts/tradingview-webhook/tradezella_engine.py`
- All test files in `artifacts/tradingview-webhook/test_*.py`
- `docs/code-audit-2026-06-25.md`
- Persistent memory (`.agents/memory/MEMORY.md` and topic files)

No changes were made to:
- Scoring or thresholds
- Strategy eligibility
- Gates
- Execution logic
- Sizing
- Alerts
- Database behavior
- Trade management
- Dashboard layout
- Brain Contract fields
- Databento integration
- Broker integrations
- Any `.py`, `.ts`, `.tsx`, `.html`, or configuration file

The four golden tests (`parity`, `scalp_golden`, `dual_sim`, `breakout_mode`) remain passing and were not affected by this task.
