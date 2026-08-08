# PROFITABILITY AUDIT — PHASE 0
**Read-only discovery. No code was modified.**
**Date:** 2026-08-08
**Status:** COMPLETE

---

## 1. EXECUTIVE SUMMARY

This audit inspects the trading platform as-built to determine whether it can reliably prove
a durable positive expectancy. The finding is: **the infrastructure is substantial but has
several critical gaps that prevent trustworthy profitability measurement today.**

Key conclusions:

| Area | Status |
|---|---|
| Live execution pipeline | Fully operational |
| Ghost/paper trade infrastructure | Partially exists — two separate, incompatible systems |
| Performance metrics (by strategy × instrument) | Partial — per-strategy win rate only, no R-per-instrument breakdown |
| Trade immutability | **CRITICAL gap** — parameters can drift after signal |
| Commission/slippage modeling in paper trades | **Missing** in both ghost systems |
| Out-of-sample / walk-forward capability | **Does not exist** |
| AI score contamination of performance data | **HIGH** — edge_score written to strategy_trades, used in analytics |
| Strategy promotion / demotion lifecycle | Rudimentary (GHOST_ONLY/LIVE_ELIGIBLE) — not a full promotion model |
| Live vs shadow comparison | **Cannot be done today** — no shared pairing key |

**The platform cannot be used to prove edge today.** It can be upgraded to do so with focused,
surgical additions. The Minimum Build section at the end specifies exactly what is needed.

---

## 2. CURRENT END-TO-END ARCHITECTURE

### 2.1 ASCII Pipeline Diagram

```
DATABENTO (live CME/COMEX feed)
     │ TradeMsg (ns timestamp, fixed-point price, side, size)
     ▼
databento_brain.py  DatabentoBrain._on_trade()
     │ accumulates: CVD, OHLCV minute bars, session VWAP sums, tick callbacks
     │ every 1-min bar close: _on_bar_close()
     │   writes: VWAP_BY_TICKER, CVD_BY_TICKER, VOLATILITY_BY_TICKER (ATR14),
     │           RVOL_BY_TICKER, VOLUME_SPIKE_BY_TICKER, AUTO_PRICE_BY_TICKER
     │   fires:  _detect_structure()  ─┐
     │           _detect_sweep()     ─┼─► ALERT_HISTORY (deque, shared)
     │           _detect_confirmation()─┘
     │   fires bar-close callbacks: _databento_bar_scan(), _fvg_bar_close()
     ▼
app.py  full_analysis(ticker_override)
     │ 1. alerts_in_window + score_alerts  ─► scoring signals
     │ 2. calculate_bias / confidence / trade_quality / edge_score
     │ 3. get_price_context / levels / structure / risk_zone
     │ 4. decision_engine, get_market_direction, get_trade_opportunity
     │ 5. get_vwap, get_volatility (ATR), get_session_state
     │ 6. _resolve_learning_score_influence  ◄── learning weights (±15 pts, display-only flag)
     │ 7. evaluate_strict_setup()  ─► strict_label, gate_debug, strict_reason
     │ 8. build_strict_trade_plan()  ─► trade_plan (entry/stop/target/direction)
     │ 9. Veto chain (in order):
     │      _scalp_entry_veto_reasons / compute_scalp_quality
     │      _swing_entry_veto_reasons
     │      _apply_swing_strategy_filter
     │      _trend_brake_reason
     │      additional display/advisory overlays (shadow-only, never gate)
     │ 10. Final verdict: LONG READY / SHORT READY / WAIT / EARLY
     ▼
app.py  _maybe_auto_execute() [AUTO arm only]
     │ checks: emergency / cooldown / advisor gate / direction / correlation /
     │         daily streak cap / Databento health / ARM gate
     │ under _AUTO_EXEC_LOCK: position/daily cap checks
     ▼
app.py  execute_trade_gateway()
     │ reads EXECUTION_MODE:
     │   disabled      → 409, no trade
     │   manual_only   → plan served, no auto-send
     │   paper         → MANAGED_TRADES_BY_KEY entry (local only, no broker)
     │   traderspost   → managed entry + broker webhook → Tradovate
     │   pickmytrade   → managed entry + broker webhook
     │
     │ GHOST_ONLY check (_check_learning_eligibility):
     │   n=0           → LIVE_ELIGIBLE (first-ever trade for this setup)
     │   1..49         → GHOST_ONLY  (rerouted to paper even if mode=traderspost)
     │   expectancy<0  → GHOST_ONLY
     ▼
MANAGED_TRADES_BY_KEY  (in-memory + open_trades DB table)
     │ managed trade lifecycle: entry → TP1 partial → TP2 / stop → close
     │ paper exits: _paper_watcher_loop (local price comparison)
     │ live exits:  broker webhook confirms fill → _close_managed_trade()
     ▼
app.py  _record_strategy_trade()  [on close]
     │ persists to: strategy_trades (primary learning source)
     │              native_journal (canonical per-trade record)
     │              swing_theses (if SWING managed trade)
     │ frozen at registration: entry, stop, target, strategy_key,
     │                         direction, session, regime, context snapshot
     ▼
Adaptive Learning Engine  (reads strategy_trades post-close)
     │ computes: per-strategy win_rate, expectancy, R avg, hour-bucket weights
     │ updates:  PER_MODE_STATS, learning_weights (0.65–1.35 multiplier)
     │ effect:   edge_score ±15 pts (flag-gated, display-only default)
     │           GHOST_ONLY / LIVE_ELIGIBLE eligibility gate
     ▼
Native Journal  +  Dashboard Analytics  (display-only)
     Learning Rule Engine  (GHOST_ONLY gate on live orders)
```

### 2.2 Stage-by-Stage Table

| Stage | File | Function | Key Inputs | Key Outputs | DB Tables | Affects Live? | Ghost/Paper Mode? |
|---|---|---|---|---|---|---|---|
| Tick ingestion | databento_brain.py | `_on_trade` | TradeMsg | price, CVD accumulation | — | No | N/A |
| Bar close | databento_brain.py | `_on_bar_close` | 1m OHLCV | VWAP, ATR, CVD state, structure alerts | — | No | Yes |
| Market state build | databento_brain.py | `_on_bar_close` | bars | CVD_BY_TICKER, VWAP_BY_TICKER, VOLATILITY_BY_TICKER | — | No | Yes |
| Alert detection | databento_brain.py | `_detect_structure/sweep/confirmation` | bars, price | ALERT_HISTORY entries | — | No | Yes |
| Full analysis | app.py | `full_analysis` | all state stores | verdict, plan, gate_debug | — | No | Yes |
| Strict gate | app.py | `evaluate_strict_setup` | price, VWAP, zones, alerts | strict_label, gate_debug | — | No | Yes |
| Trade plan | app.py | `build_strict_trade_plan` | strict_label, direction | entry, stop, target, rr | — | No | Yes |
| Veto chain | app.py | multiple `_*_veto_*` | plan, context | final verdict, veto reason | — | No | Yes |
| Auto execution | app.py | `_maybe_auto_execute` | verdict, arm state | execute call or abort | — | **Yes** | Paper only |
| Gateway | app.py | `execute_trade_gateway` | verdict, mode, plan | managed trade entry, broker call | open_trades | **Yes** | **Yes (paper path)** |
| Managed lifecycle | app.py | `_paper_watcher_loop` / broker webhook | price vs plan | TP/stop triggers | open_trades | **Yes** | **Yes** |
| Trade close record | app.py | `_record_strategy_trade` | managed trade | strategy_trades row | strategy_trades, native_journal | **Yes** | **Yes** |
| Learning | app.py | adaptive learning engine | strategy_trades | weights, eligibility | learning_weights (implied) | Gate effect | Display-only |

---

## 3. COMPLETE STRATEGY INVENTORY

### 3.1 Live Engine Strategies (STRATEGY_PRIORITY)

These are the strategies eligible for live auto-execution in the main engine.

| Canonical ID | Display Name | File | Detector Function | LONG | SHORT | MGC | MNQ | MES | MYM | Session | Live Eligible | Backtest | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `VWAP_RECLAIM_FAIL` | VWAP Reclaim / Fail | app.py | `detect_vwap_reclaim` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | Any | **Yes** | Yes | Primary scalp/swing trigger |
| `ORDER_BLOCK_REJECTION` | Order Block Rejection | app.py | `detect_order_block_rejection` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | Any | **Yes** | Yes | Requires zone |
| `RANGE_EXPANSION_BREAKOUT` | Range Expansion / Breakout | app.py | `detect_range_expansion_breakout` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | Any | **Yes** | Yes | Consolidation + volume |
| `OPENING_RANGE_BREAKOUT` | Opening Range Breakout (ORB) | app.py | `detect_opening_range_breakout` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | Post-OR | **Yes** | Yes | Session-gated |
| `EXHAUSTION_FADE` | Exhaustion Fade | app.py | `detect_exhaustion_fade` | ✓ (below VWAP) | ✓ (above VWAP) | ✓ | ✓ | ✓ | ✓ | Any | **No** (disabled) | **Disabled** | In DISABLED_STRATEGIES; excluded from backtest; GHOST/research only |

**Note:** `BOS`, `CHOCH`, `SCALP`, `SWING`, `CONTINUATION`, `REVERSAL` are structural conditions and modes — not strategy IDs.

### 3.2 Scalp Research Strategies (scalp_live_sim + scalp_research)

These are **paper/research/display-only** — never in STRATEGY_PRIORITY, never live gate.

| Strategy Key | LONG | SHORT | Session Restriction | Live Status | Backtest | Notes |
|---|---|---|---|---|---|---|
| `vwap_pullback_continuation` | ✓ | ✓ | None | watch | Yes | |
| `vwap_reclaim_fail` | ✓ | ✓ | None | watch | Yes | Research mirror of live strategy |
| `opening_range_breakout` | ✓ | ✓ | OR window | watch | Yes | Research mirror of live ORB |
| `opening_range_fakeout` | ✓ | ✓ | OR window | watch | Yes | |
| `liquidity_sweep_reversal` | ✓ | ✓ | None | watch | Yes | |
| `failed_breakdown_breakout` | ✓ | ✓ | None | watch | Yes | |
| `micro_pullback_scalp` | ✓ | ✓ | None | watch | Yes | |
| `ema_9_20_continuation` | ✓ | ✓ | None | watch | Yes | |
| `fvg_continuation` | ✓ | ✓ | None | watch | Yes | |
| `order_block_rejection` | ✓ | ✓ | None | watch | Yes | Research mirror of live OBR |
| `prior_high_low_sweep` | ✓ | ✓ | None | watch | Yes | |
| `session_high_low_reclaim` | ✓ | ✓ | None | watch | Yes | |
| `volume_climax_reversal` | ✓ | ✓ | None | watch | Yes | |
| `cvd_divergence_scalp` | ✓ | ✓ | None | watch | Yes | |
| `range_edge_mean_reversion` | ✓ | ✓ | None | watch | Yes | |
| `compression_breakout` | ✓ | ✓ | None | watch | Yes | |
| `trendline_break_retest` | ✓ | ✓ | None | watch | **No** | Pending — no detector yet |
| `delta_exhaustion_reversal` | ✓ | ✓ | None | watch | **No** | Pending — no detector yet |
| `news_volatility_fade` | ✓ | ✓ | News events | watch | **No** | Pending — no detector yet |

### 3.3 Summary Count

- **Live-eligible strategies:** 4 (VWAP_RECLAIM_FAIL, ORDER_BLOCK_REJECTION, RANGE_EXPANSION_BREAKOUT, OPENING_RANGE_BREAKOUT)
- **Disabled live strategies:** 1 (EXHAUSTION_FADE)
- **Research/paper-only strategies:** 16 active + 3 pending
- **Total unique strategy IDs across all systems:** 21 active, 24 if pending included
- **Duplicate implementations between live and research:** 3 (vwap_reclaim_fail, opening_range_breakout, order_block_rejection appear in both live engine and scalp research with different detector logic — results are NOT guaranteed to match)

### 3.4 UI vs Code Alignment

The dashboard surface labels ("ORB", "VWAP Reclaim", etc.) match the canonical IDs reasonably well. **Risk:** the 3 strategies that exist in both live engine and scalp research use different detector implementations. There is no mechanism to enforce parity between them.

---

## 4. EXISTING GHOST / PAPER / SIMULATION INFRASTRUCTURE

Two **separate, incompatible** ghost/paper systems currently exist.

### 4.1 System A — Managed Trade Paper Path (Main Engine)

| Property | Detail |
|---|---|
| **Location** | `app.py` — `execute_trade_gateway` (paper branch) + `_paper_watcher_loop` + `_record_strategy_trade` |
| **What creates the record** | `execute_trade_gateway` when `EXECUTION_MODE=paper` or GHOST_ONLY rerouting |
| **Fields frozen at signal time** | entry, stop, target, strategy_key, direction, session, regime, opened_at |
| **Entry determination** | Current live market price at signal moment (market fill assumption) |
| **Stop determination** | ATR-based plan from `build_strict_trade_plan` (frozen at entry) |
| **Target determination** | R:R-based plan from `build_strict_trade_plan` (frozen at entry) |
| **How trade is marked closed** | `_paper_watcher_loop` compares live price vs stop/target; on hit → `_close_managed_trade` |
| **Actual Databento prices used for exit?** | **Yes** — live price feed |
| **Commissions modeled?** | **No** |
| **Slippage modeled?** | **No** (NULL unless broker fill exists) |
| **MAE recorded?** | Yes (from managed trade tracking; forced to 0 on orphan/restart) |
| **MFE recorded?** | Yes (same caveat) |
| **R result recorded?** | Yes — raw R (no cost deduction) |
| **Strategy ID recorded?** | Yes — strategy_key + strategy_version |
| **Instrument recorded?** | Yes |
| **Session recorded?** | Yes |
| **Market context/regime recorded?** | Yes — indicators JSON snapshot at entry |
| **Can ghost run when live risk blocks?** | **Yes** — GHOST_ONLY reroutes live orders to paper silently |
| **Table** | strategy_trades (closed), open_trades (open) |

### 4.2 System B — Scalp Live Sim (Research Engine)

| Property | Detail |
|---|---|
| **Location** | `scalp_live_sim.py` (detectors/geometry) + `app.py` (observer/persistence/watcher) |
| **What creates the record** | App observer on READY signal from research detector |
| **Fields frozen at signal time** | strategy_key, direction, entry_reason, entry, stop, target, risk, rr, fidelity |
| **Entry determination** | Current live context price (market fill assumption) |
| **Stop determination** | Structural zone within 2 ATR, else 1 ATR fallback |
| **Target determination** | 1:1 R (hardcoded geometry) |
| **How trade is marked closed** | Separate watcher, stop-first per-bar comparison |
| **Actual Databento prices used for exit?** | Yes — live bars |
| **Commissions modeled?** | **No** |
| **Slippage modeled?** | **No** |
| **MAE recorded?** | Depends on table schema (not confirmed by audit) |
| **MFE recorded?** | Same |
| **R result recorded?** | Yes — r_multiple stored |
| **Strategy ID recorded?** | Yes — strategy_key |
| **Instrument recorded?** | Yes |
| **Session recorded?** | Unclear from this module alone |
| **Context snapshot recorded?** | fidelity flag recorded; full context unclear |
| **Can ghost run when live risk blocks?** | **Yes** — completely independent of live gateway |
| **Table** | `scalp_strategy_sim_trades` (separate from strategy_trades) |

### 4.3 System C — Backtest Engine

| Property | Detail |
|---|---|
| **Location** | `backtest_engine.py` |
| **Mode** | Historical OHLCV replay — **not live streaming** |
| **Commissions modeled?** | **Yes** (backtest_engine.py:970, 1106-1123) |
| **Slippage modeled?** | Partially (bar fills) |
| **Context snapshot?** | Limited — OHLCV-derived |
| **Use actual Databento prices?** | No — historical bars only |
| **Can run alongside live?** | No — separate offline process |

### 4.4 Critical Infrastructure Gap

**System A and System B cannot be compared.** They write to different tables, use different
exit logic, and have different target assumptions (live R:R vs research 1:1). There is no
shared pairing key between a live execution (System A) and its research shadow (System B)
for the same setup signal.

---

## 5. EXISTING PERFORMANCE MEASUREMENT INFRASTRUCTURE

### 5.1 What Is Currently Calculated

The adaptive learning engine (app.py, ~line 14000+) computes from `strategy_trades`:

| Metric | Calculated? | Grouped By | Notes |
|---|---|---|---|
| Trade count | ✓ | strategy_key | Per-strategy n |
| Wins | ✓ | strategy_key | Outcome = WIN |
| Losses | ✓ | strategy_key | Outcome = LOSS |
| Breakevens | ✓ | strategy_key | Outcome = BREAKEVEN |
| Win rate | ✓ | strategy_key | Raw wins/n |
| Average R (winners) | ✓ | strategy_key | avg r_multiple WHERE win |
| Average R (losers) | ✓ | strategy_key | avg r_multiple WHERE loss |
| Expectancy (R) | ✓ | strategy_key | Computed from above |
| Last-20-trade expectancy | ✓ | strategy_key | Rolling window |
| MAE | ✓ (stored) | strategy_key | Written to strategy_trades |
| MFE | ✓ (stored) | strategy_key | Written to strategy_trades |
| Best-hour buckets | ✓ | strategy_key × hour-of-day | Used to determine best trading hours |
| Commissions | ✗ | — | **Not in ghost/paper R** |
| Slippage (net) | ✗ | — | NULL unless broker fill |
| Profit factor | ✗ | — | Not computed |
| Max drawdown (R) | ✗ | — | Not computed |
| Consecutive losses | ✗ | — | Not computed |
| Net P&L ($) | ✗ | — | Not computed |

### 5.2 Segmentation Capability

| Dimension | Exists? | Notes |
|---|---|---|
| strategy_key | ✓ | Primary grouping |
| Instrument (MGC/MNQ) | **Partial** | Stored in strategy_trades.symbol; SQL queries don't consistently break out per-instrument results |
| Direction (LONG/SHORT) | **Partial** | direction column exists; not a primary grouping in learning |
| Session | **Partial** | session column stored; best-hour bucket available |
| Time of day | ✓ | hour-bucket available |
| Volatility type | **Partial** | volatility_type column in strategy_trades |
| Regime | **Partial** | regime column stored |
| Day of week | ✓ | day_of_week column in strategy_trades |
| Trading mode (SCALP/SWING) | ✓ | trading_mode column |
| LONG/SHORT separately | ✓ | direction column |

**The data is there. The segmentation queries are not.** The learning engine groups by strategy_key
only. To get strategy × instrument breakdowns, new SQL queries would be needed against existing data —
no schema changes required.

### 5.3 PER_MODE_STATS

`PER_MODE_STATS` is a global dict in app.py aggregated by `(instrument, mode)` key. It tracks
recent trade performance per instrument/mode combination in-memory. This is display state, not a
persistent performance ledger.

---

## 6. EXISTING LEARNING / PROMOTION INFRASTRUCTURE

### 6.1 Current State

The system has a **rudimentary two-state promotion model:**

```
LIVE_ELIGIBLE   (n=0, or expectancy positive with sufficient sample)
      ↕
GHOST_ONLY      (n=1..49, or expectancy negative, or last-20 negative)
```

There is **no** PROBATION state, no staged promotion, no explicit demotion audit trail,
no human-approval gate. The transition is automatic and silent.

### 6.2 Key Functions

| Function | File | What It Does |
|---|---|---|
| `_check_learning_eligibility` | app.py ~13874 | Returns GHOST_ONLY or LIVE_ELIGIBLE per strategy+mode |
| `_recompute_learning_rules` | app.py ~13900 | Reads strategy_trades, computes win_rate/expectancy, sets eligibility |
| adaptive learning weight computation | app.py ~14000+ | Computes weights 0.65–1.35 per strategy |
| `_resolve_learning_score_influence` | app.py ~25960 | Applies ±15 pts edge score adjustment (flag-gated, default OFF for gate) |
| Strategy version assignment | app.py ~14955, 15087 | strategy_version field on trade records |
| `diagnose_strategies` | scalp_live_sim.py | Returns per-strategy paper sim stats for dashboard |

### 6.3 What Is Missing

- No PROBATION tier
- No human-approval gate before LIVE_ELIGIBLE
- No time-based holdout (strategies can promote after just 50 trades regardless of time span)
- No statistical significance test (sample-size gate only, no confidence interval)
- No out-of-sample requirement
- No performance monitoring after promotion
- No automatic demotion with audit trail
- No strategy version snapshot (parameter changes mid-lifecycle are silent)

---

## 7. DATABENTO / DATA-PATH FINDINGS

### 7.1 Data Quality

- **Live feed:** Real CME/COMEX Level 1 trades (not quotes; no bid/ask spread model)
- **Latency:** Sub-second delivery; bar close events fire within ~70s of bar end (partial-flush daemon)
- **Partial bars:** Handled by `_partial_flush_daemon` — stale bars (70s without tick) are force-closed as low-volume; this is **correct behavior** for overnight silence

### 7.2 Contract Multipliers (Correctness Check)

| Instrument | point_value | tick_size | Cost per tick | Correct for CME? |
|---|---|---|---|---|
| MGC (Micro Gold) | 10 | 0.1 | **$1.00** | ✓ Yes |
| MNQ (Micro Nasdaq) | 2 | 0.25 | **$0.50** | ✓ Yes |
| MES (Micro S&P) | 5 | 0.25 | **$1.25** | ✓ Yes |
| MYM (Micro Dow) | 0.50 | 1.0 | **$0.50** | ✓ Yes |

All values match CME specifications. **No critical error here.**

### 7.3 Contract Rollover Risk (HIGH)

- MGC uses `MGC.c.1` (front-month continuous, configurable), MNQ uses `MGC.c.0`
- TradingView rolls earlier than Databento (acknowledged in code comments)
- `strategy_trades` stores canonical instrument (`MGC`, `MNQ`) — **not the specific contract**
- No contract-tag column in strategy_trades
- Historical trades before and after a rollover are mixed under the same instrument key
- Price levels are not adjusted; a stop set at $2480 pre-roll may be evaluated against post-roll prices

**Result:** Inter-roll price comparisons in analytics are contaminated. R calculations spanning
a rollover boundary may be incorrect.

### 7.4 Overnight Bar Scarcity

- MGC produces ~0 bars overnight (genuine COMEX silence)
- ES/NQ produce ~12 bars overnight
- ATR14 is computed from available bars; instrument-specific behavior is handled
- `VOL_MIN_BARS` must stay ≤ 12 or MES/MNQ ATR silently breaks

---

## 8. TRADE IMMUTABILITY FINDINGS

### 8.1 What Is Frozen at Signal Time

Fields frozen when `execute_trade_gateway` registers a managed trade:

- `opened_at` (signal timestamp) ✓
- `entry` ✓
- `stop` ✓  
- `target` (TP1, TP2, runner) ✓
- `direction` ✓
- `strategy_key` ✓
- `strategy_version` ✓
- `session` ✓
- `indicators` JSON context snapshot ✓

### 8.2 Mutation Risks Found

| Risk | Severity | Location | Detail |
|---|---|---|---|
| **READY→ACTIVE stop/target modification** | **CRITICAL** | app.py managed trade lifecycle | Stop and target can be modified by trade-management logic after entry (trailing stop, BE move, partial TP). This is intentional for live management but means `_record_strategy_trade` captures the **modified** stop, not the original signal stop. The original plan is not separately preserved. |
| **Strategy parameter drift** | **HIGH** | app.py global thresholds | If score thresholds or ATR multipliers change while a ghost trade is open, the exit watcher uses the new parameters (ATR recalculates each bar). The original entry ATR is not frozen. |
| **MAE/MFE forced to 0 on restart** | **MEDIUM** | app.py ~13816-13867 | On server restart, open managed trades that were in-progress lose their MAE/MFE history; orphan handler sets both to 0. This understates risk on stopped trades. |
| **GHOST_ONLY eligibility can flip mid-trade** | **MEDIUM** | app.py learning engine | A trade opened as LIVE_ELIGIBLE can be re-evaluated; if eligibility changes, the next trade is affected but the current one is not. Low risk for current implementation. |
| **strategy_version is logic version, not parameter snapshot** | **HIGH** | app.py ~14955 | `strategy_version` tracks code version, not the exact threshold values used. If `ORB_CONFIDENCE_THRESHOLD` changes from 65→70 mid-deployment, both pre/post trades get the same version tag. |
| **No dedicated "original_stop" / "original_entry" field** | **HIGH** | strategy_trades schema | After trailing-stop moves, BE moves, or manual management, the final stop in the record is not the original. Backtesting the strategy as-signaled is impossible from this record alone. |

**Verdict: The system does not support immutable ghost trade records as required for edge proof.**

---

## 9. LOOK-AHEAD / DATA-LEAKAGE FINDINGS

### 9.1 Partial Bar During Live Evaluation

| Issue | Severity | Detail |
|---|---|---|
| `full_analysis` can be called mid-bar | **MEDIUM** | The heartbeat and API poll both call full_analysis while the current minute bar is still forming. ATR is computed from completed bars (safe). CVD and price reflect the current partial bar (correct — this is what traders see). Structure signals are only injected on bar close. **No look-ahead here; live behavior is intentional.** |
| Scalp live sim entry price | **MEDIUM** | Entry is the current live context price at signal moment, not the next bar's open. In a real market this is achievable with a market order but assumes zero latency. **Overstates fill quality.** |
| Backtest bar-fill assumption | **LOW** | Backtest uses OHLCV bar data; fills are assumed at signal-bar close or next-bar open (needs per-strategy inspection). In fast markets, actual fills lag this. |

### 9.2 Structural Look-Ahead Risks

| Issue | Severity | Detail |
|---|---|---|
| ALERT_HISTORY re-scored on every `full_analysis` call | **LOW** | Alerts are historical facts; re-scoring applies the current scoring function to old alerts. If the scoring function changes, historical READY signals would be reclassified. This is subtle parameter drift, not look-ahead bias in the traditional sense. |
| Backtest structure detection | **MEDIUM** | `backtest_engine.py` reconstructs BOS/CHOCH from OHLCV; the exact detection algorithm may differ from the live detector. A strategy validated in backtest may behave differently live. **Backtest ≠ Live structural signals.** |
| AI / LLM scoring in context snapshot | See §12 | Edge score (AI-influenced) is stored in the trade record at entry; if analytics later use edge_score as a predictor of outcomes, this is circular — the AI scored the setup, the setup resolved, and now the AI score is used to weight future setups. |

---

## 10. COST / SLIPPAGE REALISM FINDINGS

| Item | System A (Managed Paper) | System B (Scalp Live Sim) | Backtest |
|---|---|---|---|
| Commission modeled | **No** | **No** | Yes |
| Slippage modeled | **No** | **No** | Partial |
| Bid/ask spread modeled | **No** | **No** | No |
| Entry at market price | Yes (live price) | Yes (live price) | Bar close / next open |
| Exit at Databento prices | Yes | Yes | Bar prices |

**For MNQ at 1 contract:**
- Commission (Tradovate): ~$1.49/side = ~$2.98/round-trip
- At MNQ tick value $0.50: commission = ~5.96 ticks ≈ ~1.5 pts per round-trip
- 1R trade with 10-pt stop → commission drag ≈ **0.15R per trade**
- Ghost/paper R numbers are overstated by ~0.15R for MNQ, more for other instruments

**For MGC at 1 contract:**
- Commission: ~$1.49/side = ~$2.98/round-trip
- At MGC tick value $1.00: commission = ~3 ticks ≈ ~0.3 pts per round-trip
- Typical MGC stop ~$3-5 → commission drag ≈ **0.06-0.10R per trade**

**Verdict:** Ghost/paper profitability numbers are materially overstated. A strategy appearing
to be +0.10R expectancy could be zero or negative after realistic costs.

---

## 11. STRATEGY × INSTRUMENT MEASUREMENT CAPABILITY

**Current state:** The data exists to measure strategy × instrument, but the queries don't.

`strategy_trades` has both `strategy` (or `strategy_key`) and `symbol`/`instrument` columns.
A simple SQL GROUP BY `strategy_key, instrument` would produce per-cell statistics.

**Gap:** The learning engine groups by strategy_key only. ORB × MGC and ORB × MNQ are not
treated as separate experiments. If ORB is unprofitable on MGC but profitable on MNQ, the
system learns a blended average and potentially promotes or demotes the wrong cell.

**Required change to fix:** Add instrument as a second dimension to the learning key. This
is a SQL query change, not a schema change.

---

## 12. SESSION / REGIME MEASUREMENT CAPABILITY

`strategy_trades` stores:
- `session` (e.g., LONDON, NY, ASIA)
- `regime` (trend/range label)
- `day_of_week`
- `volatility_type`
- `trading_mode` (SCALP/SWING)
- `hour` bucket (best-hour computed from opened_at)

**Segmentation by session/regime is possible today with SQL queries.** The adaptive learning
engine uses hour buckets but does not segment by session or regime for eligibility decisions.

---

## 13. OUT-OF-SAMPLE CAPABILITY

**Does not exist.**

There is no mechanism for:
- Designating a time period as "training" vs "validation"
- Freezing a strategy version and evaluating it on subsequent unseen trades
- Walk-forward testing
- Holdout samples
- Strategy parameter versioning (parameter snapshots, not just logic version tags)

All learning is in-sample — the same trades used to compute performance weights are the
trades that may be influencing the setups that generated those trades (through edge_score
influence on the gate, which is flag-gated but could be enabled).

**This is the most significant structural gap for proving durable edge.**

---

## 14. LIVE VS SHADOW COMPARISON CAPABILITY

**Cannot be done today.**

To compare a theoretical ghost result vs an actual live result for the same setup, you need
a shared pairing key. Currently:

- System A (Managed Paper) and live trades share `managed_key` and `journal_id` — but only
  within System A. A GHOST_ONLY paper trade and its corresponding "would-have-been live" result
  cannot be compared because one of them was never executed.
- System B (Scalp Live Sim) has no foreign key to System A or to any live trade.
- There is no "shadow_of_trade_id" field in any table.

**Required to fix:** A pairing key injected at signal time into both the ghost record and
any corresponding live record. This requires no schema change to live trades — only a new
column in ghost tables.

---

## 15. CRITICAL PROFITABILITY-TRUST DEFECTS

### CRITICAL

| # | Finding | Impact |
|---|---|---|
| C1 | **Original stop/target not frozen** — managed trade lifecycle modifies stop (trailing, BE), and _record_strategy_trade captures the modified value. Ghost trade exit parameters do not reflect the original signal. | All paper R calculations are unreliable as a measure of the *strategy signal's* edge; they measure the execution manager's performance instead. |
| C2 | **No out-of-sample capability** — all learning is in-sample. Sample size ≥ 50 is not proof of edge; it is overfitting evidence on N=50 trades. | Cannot prove edge is durable; any positive expectancy could be in-sample noise. |
| C3 | **Commission/slippage missing from all ghost systems** — raw R is stored, not net R. | A +0.1R gross expectancy strategy is likely breakeven or negative after costs. Promotion using raw R = promoting losing strategies. |

### HIGH

| # | Finding | Impact |
|---|---|---|
| H1 | **AI score contamination** — `edge_score` (AI/ML influenced) stored in `strategy_trades`; analytics compute avg edge score by outcome. If edge score influence on the gate is enabled, the learning feedback loop uses AI-scored-then-traded results to weight future AI scores. | Circular: AI grades setups, setups execute, grades "validated" by outcomes. Not an objective edge test. |
| H2 | **Contract rollover contaminates history** — canonical symbol (`MGC`) used as key; no contract tag; price series mix across rolls | R calculations for trades spanning a rollover may use wrong price context. Any stop set against a pre-roll price level is evaluated post-roll. |
| H3 | **Survivorship bias in strategy reporting** — EXHAUSTION_FADE explicitly disabled in backtest; only enabled strategies appear in aggregate stats | Aggregate performance is inflated by removing losing strategies post-hoc. |
| H4 | **Strategy parameter drift during open trades** — ATR multipliers, score thresholds can change while a ghost trade is open | Exit conditions for ghost trades depend on current parameters, not entry parameters. Backtesting is unreliable without parameter snapshots. |
| H5 | **Scalp live sim and main engine use different detectors for the same strategy name** — `vwap_reclaim_fail`, `opening_range_breakout`, `order_block_rejection` exist in both with independent logic | Research validation of a strategy does not validate the live version. |

### MEDIUM

| # | Finding | Impact |
|---|---|---|
| M1 | **MAE/MFE reset to 0 on restart** — orphan handler forces MAE/MFE=0 for in-progress trades that survived a restart | Drawdown statistics for stopped trades are understated. |
| M2 | **Backtest ≠ live structural signals** — BOS/CHOCH reconstruction in backtest_engine differs from live Databento detector | Backtest validation does not reliably predict live behavior. |
| M3 | **Unresolved ghost trades on feed loss** — scalp sim watcher has no startup force-close | Ghost trades can stay open indefinitely; if excluded from stats, win rate is overstated. |
| M4 | **Mixed UTC/ET storage** — minor timezone inconsistency | Session boundary attribution may be wrong for trades near session-change hour. |
| M5 | **Market-order fill assumption** — ghost entry at current price, not next-bar open | In fast markets (ORB, sweep) the actual fill would be several ticks worse. |

### LOW

| # | Finding | Impact |
|---|---|---|
| L1 | **No bid/ask spread model** | Minor — relevant mainly for wide-spread instruments |
| L2 | **Duplicate signal prevention only in scalp sim** | Low risk in managed path due to per-instrument single-slot design |
| L3 | **Contract multipliers are correct** — no error found | None |

---

## 16. REUSABLE COMPONENTS

These already exist and should be used rather than rebuilt:

| Component | Location | What It Provides |
|---|---|---|
| `strategy_trades` table | PostgreSQL | Closed-trade record with strategy, instrument, direction, session, R, MAE, MFE, regime, context |
| `native_journal` table | PostgreSQL | Canonical per-trade record with open/close timestamps |
| `open_trades` table | PostgreSQL | Active trade persistence across restarts |
| `_record_strategy_trade` | app.py ~13611 | Full trade close recorder — just needs original_stop/original_entry fields added |
| Managed trade lifecycle | app.py | GHOST_ONLY rerouting already exists — ghost trades already run independent of live blocks |
| `scalp_strategy_sim_trades` | PostgreSQL | Research paper-sim ledger — needs schema extension |
| Adaptive learning engine | app.py ~14000+ | Win rate, expectancy, rolling windows — queries need instrument as 2nd dimension |
| `strategy_version` field | strategy_trades | Logic version attribution already works |
| Context snapshot (indicators JSON) | strategy_trades | ATR, session, regime, CVD direction, VWAP side already captured |
| `day_of_week`, `session`, `volatility_type`, `regime` columns | strategy_trades | Segmentation dimensions already stored |
| GHOST_ONLY / LIVE_ELIGIBLE gate | app.py ~13874 | Two-state promotion already functional |
| `backtest_engine.py` commission modeling | backtest_engine.py | Commission math already correct — can be ported to ghost system |

---

## 17. MISSING COMPONENTS

| Component | Priority | Notes |
|---|---|---|
| **`original_entry` / `original_stop` / `original_target` frozen fields** | CRITICAL | Must be captured before any trade management modifies the plan |
| **Commission deduction in ghost/paper R** | CRITICAL | Port from backtest_engine.py; trivial math |
| **Out-of-sample / holdout designation** | CRITICAL | Time-period flag on strategy_trades to mark training vs validation trades |
| **Ghost record pairing key** | HIGH | A `shadow_of_signal_id` or `experiment_id` to link ghost → live for the same setup |
| **Strategy parameter snapshot** | HIGH | Freeze the actual threshold values (not just version tag) at trade entry |
| **Net R field** (gross R − commission R) | HIGH | Separate from raw r_multiple |
| **Strategy × instrument learning key** | HIGH | SQL query change to adaptive engine — no schema change |
| **Walk-forward / holdout infrastructure** | HIGH | Period tagging on strategy_trades |
| **Profit factor, max drawdown, consecutive losses** | MEDIUM | Additional SQL aggregations on existing data |
| **Automatic demotion with audit trail** | MEDIUM | Extend GHOST_ONLY gate with time-stamped reason |
| **Ghost trade force-close on restart** | MEDIUM | Prevent permanently-open unresolved records |
| **Bid/ask spread model** | LOW | Not critical for proof of concept |

---

## 18. MINIMUM VIABLE EDGE LEDGER DESIGN

Map of proposed fields to existing infrastructure:

| Field | Proposed | Map to Existing | Classification |
|---|---|---|---|
| `experiment_id` | UUID per signal instance | New field | **NEW FIELD REQUIRED** |
| `strategy_id` | canonical strategy key | `strategy_trades.strategy_key` | **REUSE EXISTING** |
| `strategy_version` | logic + parameter version | `strategy_trades.strategy_version` (logic only) | **EXTEND EXISTING** (add param_hash) |
| `instrument` | MGC, MNQ, etc. | `strategy_trades.instrument` | **REUSE EXISTING** |
| `direction` | LONG / SHORT | `strategy_trades.direction` | **REUSE EXISTING** |
| `signal_time` | signal timestamp | `strategy_trades.opened_at` | **REUSE EXISTING** |
| `entry` | **original** entry price | `strategy_trades.entry` (currently modified) | **EXTEND EXISTING** (add original_entry) |
| `stop` | **original** stop price | `strategy_trades.stop` (currently modified) | **EXTEND EXISTING** (add original_stop) |
| `targets` | TP1, TP2 at signal | `strategy_trades.tp1`, `tp2` | **REUSE EXISTING** |
| `risk_pts` | original risk in points | derivable from original_entry − original_stop | **NEW FIELD REQUIRED** (or computed) |
| `session` | session at signal | `strategy_trades.session` | **REUSE EXISTING** |
| `context_snapshot` | ATR, VWAP side, CVD dir, regime | `strategy_trades.indicators` JSON | **REUSE EXISTING** (already has this) |
| `MFE` | max favorable excursion | `strategy_trades.mfe_r` | **REUSE EXISTING** |
| `MAE` | max adverse excursion | `strategy_trades.mae_r` | **REUSE EXISTING** |
| `gross_R` | raw R result | `strategy_trades.r_multiple` | **REUSE EXISTING** |
| `cost_R` | commission + slippage in R units | Not present | **NEW FIELD REQUIRED** |
| `net_R` | gross_R − cost_R | Not present | **NEW FIELD REQUIRED** (or computed) |
| `status` | WIN / LOSS / OPEN / UNRESOLVED | `strategy_trades.outcome` | **REUSE EXISTING** |
| `source` | live / ghost / paper / research | `strategy_trades.mode`? | **EXTEND EXISTING** (add ghost_source enum) |
| `live_trade_id_if_any` | FK to live execution | Not present | **NEW FIELD REQUIRED** |
| `contract_tag` | specific contract (e.g., MGCm26) | Not present | **NEW FIELD REQUIRED** |
| `holdout_period` | training / validation flag | Not present | **NEW FIELD REQUIRED** |
| `param_hash` | hash of live strategy parameters | Not present | **NEW FIELD REQUIRED** |

**Total:** 8 Reuse Existing, 5 Extend Existing, 8 New Fields Required.

The schema extension is surgical — all additions are new columns to existing tables or a
single new `edge_observations` table that extends `strategy_trades` via FK.

---

## 19. PROPOSED PROMOTION / DEMOTION ARCHITECTURE (DESIGN ONLY)

### 19.1 Stage Model

```
RESEARCH (paper-only, unlimited duration)
    │ trigger: operator marks strategy as PROBATION-ready
    │ requires: ≥ 50 ghost observations, net_R > 0 (after cost), profit_factor > 1.2
    ▼
PROBATION (live orders, position size capped at 1 contract)
    │ trigger: statistical gate passes
    │ requires: ≥ 100 live trades, expectancy CI (95%) lower bound > 0,
    │           max drawdown < 5R, no degradation trend in last 30 trades
    ▼
LIVE (full position sizing)
    │ continuous monitoring: rolling 50-trade window
    │ degradation trigger: lower CI bound < 0 for 2 consecutive windows
    ▼
DEMOTION (back to PROBATION or RESEARCH)
    │ reason logged with timestamp, strategy_version snapshot
    │ ghost observations continue in parallel at all times
```

### 19.2 Statistical Approach (Recommended)

**Do not use raw win rate as the promotion criterion.**

Recommended metrics with minimum thresholds:
1. **Sample size:** ≥ 50 per cell (strategy × instrument) — necessary but not sufficient
2. **Net expectancy:** E[net_R] > 0 (after commission + slippage)
3. **Expectancy confidence interval:** Lower bound of 95% bootstrapped CI on E[net_R] > 0
4. **Profit factor:** Σ(winners) / Σ(|losers|) > 1.3
5. **Maximum drawdown:** Never exceed 6R in any 20-trade window during probation
6. **Temporal stability:** Kendall's τ of rolling 10-trade expectancy vs time > −0.3 (not consistently degrading)
7. **Out-of-sample confirmation:** Last 25% of trades not used to establish the threshold — must independently show positive expectancy

**Demotion triggers (any one):**
- Rolling 50-trade net expectancy < −0.05R
- Two consecutive 20-trade windows with negative expectancy
- Drawdown exceeds 8R in any 30-trade window
- Strategy parameter snapshot no longer matches production (silent drift detected)

### 19.3 Ghost Validation Continues After Promotion

Even once LIVE, the system should generate a parallel ghost record for every signal,
using the **original signal parameters** (not managed parameters). This separates:
- Strategy signal quality (ghost R)
- Execution quality (live R)
- Execution drag = ghost_R − live_R

---

## 20. RECOMMENDED IMPLEMENTATION SEQUENCE

**Do not implement all of this at once. Do not build what you don't yet need.**

### Phase 1 — Make Ghost Trades Trustworthy (Minimum for Edge Research)

1. Add `original_entry`, `original_stop`, `original_target` fields to `strategy_trades` (captured at registration, before any management)
2. Add `cost_r` field (commission in R units, computed at close from known contract specs)
3. Add `net_r` field (= r_multiple − cost_r)
4. Add `contract_tag` field (specific contract, e.g. `MGCm26`)
5. Add `holdout_period` enum field (`training` / `validation`) to `strategy_trades`
6. Force-close unresolved open ghost trades on server restart with `outcome=UNRESOLVED`
7. Verify MAE/MFE are correctly preserved across restarts (fix orphan handler)

**Risk:** Zero live behavior change. Additive schema columns only. Phase 1 makes the existing data trustworthy.

### Phase 2 — Strategy × Instrument Learning

1. Add instrument as second dimension to adaptive learning key (`strategy_key + instrument`)
2. Add SQL aggregations for profit factor, max drawdown (in R), consecutive losses
3. Add `ghost_source` field to distinguish System A ghost vs System B research vs live
4. Backfill `holdout_period=training` on all historical strategy_trades rows

**Risk:** Zero live behavior change. Affects only analytics/display.

### Phase 3 — Out-of-Sample Infrastructure

1. Define a "validation window start date" per strategy × instrument cell
2. Tag all new trades after that date as `holdout_period=validation`
3. Dashboard shows separate training vs validation performance
4. Promotion decisions reference validation performance only

**Risk:** Zero live behavior change. Requires operator to designate validation windows.

### Phase 4 — Promotion Lifecycle

1. Add `promotion_status` table (strategy_key, instrument, status, promoted_at, demoted_at, reason)
2. Extend GHOST_ONLY gate to reference promotion_status
3. Implement statistical promotion criteria from §19.2
4. Implement automatic demotion triggers with audit trail
5. Add continuous shadow generation for live strategies

**Risk:** Modifies the GHOST_ONLY gate logic — needs careful testing. Does not affect live execution directly.

---

## CAPABILITY SUMMARY TABLE

| Capability | Exists | Partial | Missing | Trustworthy Today? | Recommended Action |
|---|---|---|---|---|---|
| Live execution pipeline | ✓ | | | ✓ Yes | No change |
| Ghost/paper trade execution | | ✓ | | ✗ No | Fix original_stop/entry fields (Phase 1) |
| Commission modeling in ghost | | | ✓ | ✗ No | Add cost_r field (Phase 1) |
| MAE/MFE recording | | ✓ | | ✗ Partially | Fix restart orphan handler (Phase 1) |
| Strategy × instrument grouping | | ✓ | | ✗ No | Add instrument to learning key (Phase 2) |
| Session/regime segmentation | | ✓ | | ✓ Mostly | Add SQL queries (Phase 2) |
| Out-of-sample capability | | | ✓ | ✗ No | Add holdout_period tagging (Phase 3) |
| Walk-forward testing | | | ✓ | ✗ No | Phase 3 |
| Strategy parameter snapshots | | ✓ | | ✗ No | Add param_hash (Phase 1) |
| Promotion / demotion lifecycle | | ✓ | | ✗ No | Phase 4 |
| Trade immutability | | ✗ | | ✗ No | Phase 1 critical fix |
| Live vs ghost comparison | | | ✓ | ✗ No | Add pairing key (Phase 2) |
| Contract rollover tracking | | | ✓ | ✗ No | Add contract_tag (Phase 1) |
| AI/score isolation from performance | | ✗ | | ✗ No | Use net_r not edge_score as promotion signal |
| Profit factor | | | ✓ | — | SQL aggregation (Phase 2) |
| Max drawdown in R | | | ✓ | — | SQL aggregation (Phase 2) |
| Statistical significance test | | | ✓ | — | Phase 4 |
| Continuous shadow for live strategies | | | ✓ | — | Phase 4 |

---

## TOP 10 PROFITABILITY BLOCKERS

Ranked by how much they prevent proving durable positive expectancy:

1. **[CRITICAL] Original trade parameters are not frozen.** Managed trade stop/target moves are baked into the ghost R. The adaptive learning engine is learning execution manager performance, not strategy signal quality.

2. **[CRITICAL] No out-of-sample infrastructure.** The same 50 trades that trigger LIVE_ELIGIBLE are the same 50 that "proved" the strategy. This is pure in-sample overfitting with a 50-trade threshold.

3. **[CRITICAL] Commissions not deducted from ghost R.** Strategies with +0.1R gross expectancy are likely negative after costs. The promotion gate is comparing gross numbers against a zero threshold — this systematically promotes losing strategies.

4. **[HIGH] AI edge score contamination.** `edge_score` is stored in `strategy_trades` and referenced in win/loss analytics. Since edge score partially influences setups that execute (through the learning weight gate when enabled), the performance data is circularly contaminated.

5. **[HIGH] Strategy × instrument not separated.** ORB on MGC and ORB on MNQ are treated as one experiment. If one is profitable and the other is not, the blended average obscures both facts and may result in wrong promotion/demotion decisions.

6. **[HIGH] Contract rollover contaminates price-based analytics.** Historical R calculations for trades spanning an MGC rollover use mixed price series. Any support/resistance or ATR level is undefined across a rollover boundary.

7. **[HIGH] Survivorship bias in strategy set.** EXHAUSTION_FADE is hard-disabled in backtest. Aggregate performance numbers exclude it. This makes the remaining strategies look better than the full strategy universe.

8. **[HIGH] Backtest signals ≠ live signals.** BOS/CHOCH reconstruction in `backtest_engine.py` differs from the live Databento detector. A strategy that shows edge in backtest has not been validated on the actual signals it would have used live.

9. **[HIGH] No live-vs-shadow comparison.** Cannot measure execution drag (strategy expected X R, execution delivered Y R). Cannot separate strategy quality from broker latency, prop-firm blocks, or duplicate filtering.

10. **[MEDIUM] Unresolved ghost trades contaminate win rate.** Trades that never hit stop or target (restart, feed loss, long hold) have unclear outcomes. If they are excluded from stats, win rate is overstated. If force-closed at last price, results are arbitrary.

---

## MINIMUM BUILD

The smallest implementation to achieve:

> Every valid setup → ghost observation → immutable outcome → net R → grouped expectancy → out-of-sample validation → promotion eligibility → continuous shadow monitoring

**5 additions. No live behavior changes. No new execution paths.**

### Step 1 — Immutable Signal Record (Phase 1)

Schema additions to `strategy_trades`:
- `original_entry NUMERIC` — price at signal generation, before any BE/trail
- `original_stop NUMERIC` — stop at signal generation
- `original_target1 NUMERIC` — TP1 at signal generation
- `cost_r NUMERIC` — commission round-trip in R units (computed at close)
- `net_r NUMERIC` — r_multiple − cost_r
- `contract_tag TEXT` — specific futures contract (e.g., `MGCm26`)
- `param_hash TEXT` — SHA256 of active strategy parameter dict at entry time

Capture `original_entry/stop/target` at the moment `execute_trade_gateway` registers the trade — before `_paper_watcher_loop` or trade management runs. Compute `cost_r` at close using instrument specs already in `INSTRUMENT_SPECS`. Force-close unresolved ghost trades on startup with `outcome=UNRESOLVED`.

**Estimated: ~150 lines of additive changes. Zero live behavior change.**

### Step 2 — Per-Cell Analytics (Phase 2)

Change adaptive learning key from `strategy_key` → `(strategy_key, instrument)`.

Add SQL aggregations for `net_r` (not `r_multiple`) as the performance signal. Add profit factor, max drawdown queries. Add `ghost_source` enum to distinguish the two ghost systems.

**Estimated: ~100 lines. Zero live behavior change.**

### Step 3 — Out-of-Sample (Phase 3)

Add `holdout_period TEXT DEFAULT 'training'` to `strategy_trades`. Backfill all existing rows as `training`. Operator designates a "validation start date" per strategy × instrument cell via a new admin endpoint. All new trades after that date are tagged `validation`. Dashboard shows separate training/validation performance. Promotion criteria reference validation performance only.

**Estimated: ~200 lines, 1 new endpoint, 1 new admin UI control.**

### Step 4 — Statistical Promotion Gate (Phase 4)

Replace the current sample-size-only GHOST_ONLY gate with:

```
net_expectancy_lower_ci_95 > 0.0   (bootstrapped, validation period only)
AND  n_validation >= 25
AND  profit_factor_validation > 1.2
AND  max_drawdown_validation_r < 6.0
```

Add `promotion_status` table (strategy_key, instrument, status, changed_at, reason). Extend GHOST_ONLY logic to reference this table. Add automatic demotion triggers.

**Estimated: ~300 lines, 1 new table, tested carefully before enabling.**

### Step 5 — Continuous Shadow (Phase 4)

For every READY signal that generates a live execution:
1. Also insert a ghost record at signal time with `original_*` fields frozen
2. Resolve the ghost record using the same Databento prices as the live trade
3. Store `live_trade_id` in the ghost record for comparison

Compute and display: `execution_drag_r = ghost_net_r - live_net_r` per strategy × instrument.

**Estimated: ~100 lines, additive only.**

---

*End of PROFITABILITY_AUDIT_PHASE_0.md*
*This document represents findings as of the audit date. No code was modified.*
*Review this document before authorizing any implementation.*
