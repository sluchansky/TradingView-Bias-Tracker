# Dashboard Panel Inventory — Phase 1.2
**File:** `artifacts/tradingview-webhook/app.py`
**Total panels:** 54 in `#view-live` + 5 in `#view-research` + 9 structural layout panels in `#live-layout`
**Date:** July 16, 2026

---

## Legend

- ✅ Yes — data populates from live signals/status
- ⚠️ Conditional — only shows when env flag enabled or position is open
- 🔵 Simulated — functions but on proxy-feed data, not real broker fills
- ❌ No — always shows dashes/empty

---

## OVERVIEW section

| # | Panel name | ID | Purpose | Data source | Functioning | Duplicates |
|---|---|---|---|---|---|---|
| 1 | Main Brain | `mod-brain` | Central AI command center. Avatar, verdict summary, unified learning decision, judge breakdown, bull/bear cases, mission list, live feed, manual trade manager, bot positions, chat. Primary command panel. | `d.main_brain`, `d.main_brain_voice`, `d.confidence_timeline` via `/status` | ✅ Yes | Partially overlapped by right-column cards; intentionally the canonical source |
| 2 | Data Feed | `mod-data-feed` | Current trading mode (SCALP/SWING), per-instrument staleness state, alert rate/hr, staleness gate status, stale warning banner. | `d.market_data_status`, `d.data_feed_status` via `/status` | ✅ Yes | No |
| 3 | Real Account Results | `mod-real-results` | Actual broker P&L, win rate, trade counts from TradeZella/broker CSV import. The only panel showing real fills (not simulated). Two scopes: Today / All imported. | `d.real_account_results` via `/status` | ✅ Yes — shows "no trades imported yet" when empty | No. Explicitly counters the simulated panels below it. |
| 4 | High-Volume Session Windows | `mod-hvsessions` | Configurable high-activity time windows per instrument (MGC/MNQ/MES/MYM). Display-only, never gates. | `d.hv_sessions` via `/status` | ✅ Yes | No |

---

## BRAIN section

| # | Panel name | ID | Purpose | Data source | Functioning | Duplicates |
|---|---|---|---|---|---|---|
| 5 | AI Decision Center | `mod-ai-decision-center` | Tabbed consolidation of all brain engine outputs: Decision / Readiness / Strategy / Entry Quality / Confidence / Memory / Micro Scalp. Top summary shows verdict + bias + active strategy + entry quality inline. | Same `/status` poll as individual panels | ✅ Yes | Intentionally aggregates data from `mod-analyst`, `mod-entryq`, `mod-governor`, `mod-memory`, `mod-debate`, `mod-microscalp`. Designed to replace them as the primary view. |

---

## ANALYSIS section

| # | Panel name | ID | Purpose | Data source | Functioning | Duplicates |
|---|---|---|---|---|---|---|
| 6 | Market Analysis (groups) | `mod-analysis-groups` | Primary analysis view. 5 collapsible sections: Structure & Trend / Liquidity & Volume / VWAP & Key Levels / Volatility & Risk / Strategy Diagnostics. Shows synthesized evidence. | Reads same `d.*` keys as individual analysis panels | ✅ Yes | Intentionally consolidates `mod-cvd`, `mod-mi`, `mod-scalpdiag`, etc. into one summary view |
| 7 | Live Chart Preview | `mod-chartprev` | TradingView iframe embed. Symbol/timeframe picker, FOLLOW mode. View-only — never reads into gate. | TradingView public embed API | ✅ Yes | No |
| 8 | Volume Delta (CVD) & RVOL | `mod-cvd` | CVD state, value, direction, agreement, pending flip, opposing candle count, relative volume, volume state. CVD is the hard directional filter; RVOL is a soft edge modifier. | `d.cvd`, `d.rvol` via `/status` | ✅ Yes | Summarized in `mod-analysis-groups` Liquidity section; this is the detail panel |
| 9 | Market Intelligence | `mod-mi` | Market state classification, Long/Short Directional Confidence scores and breakdown, trend memory + hysteresis, momentum decay, HTF alignment (1H/4H/Daily), allowed/blocked strategies. | `d.market_intelligence` via `/status`. Hidden unless `MI_ENABLED` env flag on. | ⚠️ Conditional | Summarized in `mod-analysis-groups`; detail panel |
| 10 | Fast Entry Trigger | `mod-fastentry` | 1s/5s seconds-level timing layer. 9 micro-indicators: seconds bias, micro-ready, early entry, sweep+reclaim, micro CHOCH, delta flip, micro VWAP, confidence, money-path. | `d.fast_entry` via `/status`. Hidden unless `FAST_ENTRY_TRIGGER` flag on. | ⚠️ Conditional | No |
| 11 | Index Alignment | `mod-xmarket` | Directional agreement across MNQ/MES/MYM. Display + notify only — never gates. | `d.xmarket` via `/status`. Hidden unless cross-market flag on. | ⚠️ Conditional | No |
| 12 | SCALP Diagnostics | `mod-scalpdiag` | Setup quality, room to zone, R:R, initial risk, planned reward, opposing zone, chop, BE moved, exit reason for SCALP dynamic exits. | `d.scalp_diag` via `/status`. Hidden unless SCALP dynamic exits enabled. | ⚠️ Conditional | Summarized in `mod-analysis-groups` Strategy Diagnostics section |
| 13 | SWING Diagnostics | `mod-swingdiag` | 1H/4H bias, daily level, current R, planned RR, next target, invalidation, last review, trade thesis, hold/exit reasons. | `d.swing_diag` via `/status`. Hidden unless SWING + `SWING_HTF_ENABLED`. | ⚠️ Conditional | Summarized in `mod-analysis-groups` |
| 14 | SWING Strategy Filter | `mod-swingstrat` | Dropdown to select a live SWING strategy filter. Demote-only money-path effect: narrows which READY setups are taken. Shows current match status. | `d.swing_strategy` via `/status`. Hidden unless `SWING_STRATEGY_FILTER_ENABLED`. | ⚠️ Conditional | No — only panel with this control |
| 15 | 9:30 Breakout Mode | `mod-breakout` | Opening-range breakout advisory. Dedicated 09:30 OR engine. Entry status, break direction, OR high/low, score breakdown, phase advisory. | `d.breakout_mode` via `/status`. Hidden unless `BREAKOUT_MODE_ENABLED`. | ⚠️ Conditional | No |
| 16 | Swing V2 | `mod-swing-v2` | 9-category 0–100 HTF swing scorer. SCANNING→READY lifecycle. Entry/stop/3-target plan. Score breakdown, hard blocks, next confirmation needed. | `d.swing_v2` via `/status`. Hidden unless `SWING_MODE_V2_ENABLED`. | ⚠️ Conditional | No — separate engine from main SWING |
| 17 | Dual Shadow Simulator | `mod-dual-sim` | Passive paper sim of BOTH SCALP and SWING rulebooks side by side. Compares performance across the two modes. | `d.dual_sim` via `/status`. Hidden unless `DUAL_MODE_SHADOW_SIM_ENABLED`. | ⚠️ Conditional | No |
| 18 | Unified Analyst Report | `mod-report` | Executive synthesis: thesis headline, stance/bias/favored, outcome probability (continuation/range/reversal), confidence, why-it-works vs risks, what improves confidence, early-exit triggers, invalidation. | `d.analyst_report` via `/status`. `mb-hidden` AND `display:none` (double-hidden). | ⚠️ Conditional — only visible when Advanced ON and analyst engine enabled | **Significantly overlaps** `mod-analyst` (which has a superset of this content) and `mod-ai-decision-center` Decision tab |
| 19 | Micro Scalp Brain | `mod-microscalp` | Separate liquidity-moment engine: sweep→trap→absorption→micro trigger. Ghost log always on, LIVE arm optional. Shows phase, sweep direction, volume, absorption, trigger state. | `d.main_brain.micro_scalp` via `/status`. Always visible. | ✅ Yes — shows OFF badge when disabled, data when running | Covered by `mod-ai-decision-center` Micro Scalp tab; this is the detail panel |
| 20 | Analyst Mode | `mod-analyst` | Full professional analyst reasoning: verdict, market story, market phase, professional outlook, entry probability, professional game plan (scenario tree, time horizon, R:R forecast), next opportunity, bull/bear cases, memory review, blocked-by history. Very large panel. | `d.analyst` via `/status`. `mb-hidden` + `display:none`. | ⚠️ Conditional — both Advanced and analyst flag | **Heavily overlaps** `mod-report` (its sub-synthesis), `mod-ai-decision-center` Decision tab, `mod-mb-narrative`. Largest duplication candidate. |
| 21 | Professional Review | `mod-pro` | Pre-READY pro-trader grading with two models (SCALP/SWING) per instrument. Score, pass/fail per criterion, veto toggle. | `d.pro_review` via `/status`. `mb-hidden` + `display:none`. | ⚠️ Conditional | Partially overlaps `mod-ai-decision-center` Readiness tab |
| 22 | Entry Quality | `mod-entryq` | 0–100 location score. Entry grade, bars, raw math, location flags, plain-English meaning, improvement plan. Veto toggle (default ON). | `d.entry_quality` via `/status`. `mb-hidden` + `display:none`. | ⚠️ Conditional | **Duplicates** `mod-ai-decision-center` Entry Quality tab. Detail panel. |
| 23 | Trade Debate | `mod-debate` | Bull vs Bear → Decision Judge. Confidence scores, winning/losing side reasoning, veto toggle. | `d.trade_debate` via `/status`. `mb-hidden` + `display:none`. | ⚠️ Conditional | Overlaps `mod-ai-decision-center` Decision tab; detail panel |
| 24 | Strategy Engine | `mod-strategy` | Market regime, confidence, quality, expected R. Active strategy name, session bias (Asia/London/NY), full strategy list with scores. | `d.strategy_engine` via `/status`. `mb-hidden` (Advanced). | ⚠️ Advanced-only | Partially summarized in `mod-ai-decision-center` Strategy tab; detail panel |
| 25 | Strategy Advisory Reasoning | `mod-scalp-advisory` | DISPLAY-ONLY. Shows all 16 scalp strategies as ranked candidates, 16-vote reasoning roster, consensus, candidate trade cards. | `d.scalp_advisory` via `/status`. Hidden unless `SCALP_ADVISORY_ENABLED`. | ⚠️ Conditional | No — research-advisory layer |
| 26 | Stalk Mode | `mod-stalk-mode` | Pre-entry observation overlay. Summary, grid stats, why-waiting. Advisory only. | `d.stalk_mode` via `/status`. Hidden unless `STALK_MODE_ENABLED`. | ⚠️ Conditional | No |
| 27 | Active Trade Thinking | `mod-active-thinking` | In-trade advisory. 5-verb recommendation, summary, grid stats, warnings, notes. Advisory only. | `d.active_thinking` via `/status`. Hidden unless `ACTIVE_THINKING_ENABLED`. | ⚠️ Conditional | No |
| 28 | Analyst Voice | `mod-mb-voice` | Single headline and narration sentence from the Main Brain's analyst voice. | `d.main_brain_voice` via `/status`. Always visible. | ✅ Yes — shows "Watching the tape…" default | **Duplicates** the narration/caption already displayed inside `mod-brain`. Minor panel with a single line of text. |
| 29 | Forward Odds | `mod-mb-predictions` | Probabilistic event predictions with progress bars. Favored direction, horizon, prediction list with odds%, basis factors. | `d.main_brain_predictions` via `/status`. Always visible. | ✅ Yes — shows "No live projections" when empty | Partially overlaps `mod-ai-decision-center` Decision tab. Unique probability-bar format. |
| 30 | Session Story | `mod-mb-narrative` | 7-section analyst notebook: current bias, market character, current thesis, most likely next move, key memory from today, trade instructions, event timeline. | `d.market_narrative` via `/status`. Always visible. | ✅ Yes — shows "—" per section when no narrative computed | **Significantly overlaps** `mod-brain` (which has the same 4-column grid + mission + cases) and `mod-analyst`. |

---

## JOURNAL section

| # | Panel name | ID | Purpose | Data source | Functioning | Duplicates |
|---|---|---|---|---|---|---|
| 31 | Journal (groups) | `mod-journal-groups` | Primary journal view. 5 collapsible sections: Today / Trade History / Performance / Learning / Simulated & Ghost. | Same `/status` data as individual journal panels | ✅ Yes | Intentionally consolidates `mod-equity`, `mod-trades`, `mod-learning`, etc. |
| 32 | Equity Curve · Today | `mod-equity` | Cumulative R from today's closed trades. SVG line chart, Net R / trade count / wins / losses. SIMULATED (proxy-feed, not real fills). | `d.equity_curve` via `/status` | 🔵 Simulated — accrues live, no backfill | Summarized in `mod-journal-groups` Today section |
| 33 | Today's Trades | `mod-trades` | Per-trade list for selected instrument (MGC/MNQ/MES/MYM). Mini equity chart per instrument, expandable trade cards. SIMULATED. | `d.today_trades` via `/status` | 🔵 Simulated | Summarized in `mod-journal-groups`; pairs with `mod-equity` |
| 34 | News Filter | `mod-news` | High-impact USD events from ForexFactory. Impact level, event name, time (ET), forecast vs actual. Display only — does not block trades. | `d.news` via `/status` (ForexFactory scrape) | ✅ Yes | No |
| 35 | Session Quality | `mod-sessionq` | Today's session report card. Grade (A/B/C/D/F), score, 6 component bars. Display only. | `d.session_quality` via `/status`. Hidden unless `SESSION_QUALITY_ENABLED`. | ⚠️ Conditional | No |
| 36 | Trade Management | `mod-trademgmt` | MFE/MAE booleans, commission, oversized-loss analytics for the most recent closed trade. Session aggregate flags. | `d.trade_mgmt` via `/status`. Hidden unless `TRADE_MGMT` analytics flags on. | ⚠️ Conditional | No |
| 37 | Adaptive Learning | `mod-learning` | Per-strategy analytics from closed trades. Trades logged, top/bottom strategy, recent trend, strategy performance ranking, best hours, best conditions. SIMULATED. | `d.learning_engine` via `/status`. `mb-hidden` (Advanced). | 🔵 Simulated | Summarized in `mod-journal-groups` Learning section; overlaps `mod-rule-engine` in purpose |
| 38 | Market Thesis | `mod-thesis` | Legacy thesis panel. Thesis cards with direction/confidence/structure/price, shadow validation sub-panel with per-instrument stats. | `d.thesis` + `/thesis-stats` endpoint. Always visible. | ✅ Yes — shows "No thesis data yet" when empty | **Overlaps** `mod-mb-thesis` (Thesis Tracker). Two separate thesis systems: this is the legacy hysteresis-gated thesis; `mod-mb-thesis` is the analyst-memory outcome-tracking system. |
| 39 | Trade Idea Review | `mod-review` | Manual trade grader. User enters a hypothetical trade (symbol, direction, entry/stop/targets). Bot grades it with read-only engines. Never places the trade. | `/review-idea` endpoint (owner-only). Always visible. | ✅ Yes | No |
| 40 | Learning Rule Engine | `mod-rule-engine` | Observes every closed trade. Blocks proven-failing setup patterns. Shows per-instrument eligibility (GHOST_ONLY/LIVE_ELIGIBLE), score adjustments (±15 pts max), today's trade labels, blocked patterns. | `d.rule_engine_status` via `/status`. Always visible. | ✅ Yes | No — unique; directly affects live gate eligibility |
| 41 | Trade Memory | `mod-memory` | Similar-trades lookup. Similar trade count, win rate, avg R, avg hold, excursions (MFE/MAE), most common failure, memory read narrative. | `d.trade_memory` via `/status`. `mb-hidden` (Advanced). | ✅ Yes when trades exist | **Duplicates** `mod-ai-decision-center` Memory tab. Detail panel. |
| 42 | Thesis Tracker | `mod-mb-thesis` | Analyst-memory outcome system. Saves a directional thesis snapshot each time AI generates a bias, resolves it 25–75 min later vs actual tape, writes a lesson. Pattern memory, previous thesis outcome, AI reflection, thesis history. | `d.main_brain_thesis` via `/status`. Always visible. | ✅ Yes — empty when no theses saved yet | Shares theme with `mod-thesis` but is a distinct system (analyst memory, not gate hysteresis) |

---

## CONTROLS section

| # | Panel name | ID | Purpose | Data source | Functioning | Duplicates |
|---|---|---|---|---|---|---|
| 43 | Controls Overview (groups) | `mod-controls-groups` | Primary controls status. 6 collapsible sections: Markets / Alerts / Trading Automation / Brain Modes / Display / Integrations. | Reads from `/status` and local state | ✅ Yes | Intentionally consolidates individual control panels |
| 44 | Prop Firm Protection | `mod-prop` | Final gateway guard. Toggle on/off. Apex preset account builder. Rules editor. Recent guard decisions log. Shows headline and per-rule metrics. | `d.prop_firm` via `/status` + `/prop/*` endpoints. Always visible. | ✅ Yes | No |
| 45 | Bot Training Mode | `mod-training` | Staged path to autonomy (Stages 1–4). Stage 4 = real orders; 1–3 = paper only. Paper-graded performance metrics (win rate, PF, expectancy, max DD), promotion readiness checklist. | `/training/status` endpoint. Hidden unless `TRAINING_MODE_ENABLED`. | ⚠️ Conditional | No |
| 46 | Bot Hold Score | `mod-bothold` | Advisory HOLD/SCALE OUT/EXIT conviction scores for OPEN bot positions. Component breakdown bars per position. | `d.bot_hold_score` via `/status`. Hidden unless `BOT_HOLD_SCORE_ENABLED` AND position open. | ⚠️ Conditional | Overlaps `mod-atm` (covers same open positions from a thesis angle) |
| 47 | Active Trade Management | `mod-atm` | Advisory monitor for open bot positions. Health score, thesis status (VALID/WEAKENING/INVALID), hold reason, exit warning, suggested action. | `d.active_trade_mgmt` via `/status`. Hidden unless `ACTIVE_TRADE_MGMT_ENABLED` AND position open. | ⚠️ Conditional | Partially overlaps `mod-bothold`. Both show for open positions but from different angles (ATM = thesis, bothold = conviction score) |
| 48 | LIVE Runner | `mod-liverunner` | Arms a 2-contract split-runner (primary 1R + trailed runner). Eligibility checklist, mode/qty/reduce stats. | `d.live_runner` via `/status`. Hidden unless `LIVE_RUNNER_ENABLED`. | ⚠️ Conditional | No |
| 49 | Auto Early-Exit | `mod-autoexit` | Arms auto-exit on confirmed-invalid thesis for open positions. Non-reversing exit through broker sink. Arm toggle, state, note. | `d.auto_exit` via `/status`. Hidden unless `AUTO_EXIT_ENABLED`. | ⚠️ Conditional | No |
| 50 | Blocked Orders | `mod-exec-reject` | Log of orders rejected locally (invalid payload) before any broker send. Clears on restart. | `d.exec_reject_log` via `/status`. Hidden unless there are blocked orders. | ⚠️ Conditional | No |
| 51 | Broker Send Log | `mod-broker-send-log` | Every attempted POST to TradersPost: signal type, direction, action, sentiment, HTTP status, response. Table format. Clears on restart. | `d.broker_send_log` via `/status`. Hidden unless orders have been sent. | ⚠️ Conditional | No |
| 52 | Confidence Governor | `mod-governor` | Transparent Edge→confidence breakdown. Base edge, final confidence, net adjust, threshold. Adjustment breakdown list. Demote and Score toggles. | `d.confidence_governor` via `/status`. `mb-hidden` (Advanced). | ✅ Yes | **Duplicates** `mod-ai-decision-center` Confidence tab. Detail panel. |
| 53 | Confidence Over Time | `mod-mb-confidence` | Current/High/Low/Trend confidence stats. Mini sparkline bar chart. Why-text. | `d.main_brain_confidence` via `/status`. Always visible (in Controls section). | ✅ Yes | **Overlaps** `mod-governor` and `mod-ai-decision-center` Confidence tab. Sparkline timeline is unique to this panel. |
| 54 | Ask the AI | `mod-assistant` | Read-only Q&A panel. Pre-set quick questions (Why WAIT? / Explain edge score / What's blocking?). Free-text input. Grounded on live full_analysis snapshot. | `/assistant` endpoint. `mb-hidden` (Advanced). | ✅ Yes | **Functionally duplicated** by the chat interface built into `mod-brain` (same `/assistant` backend). Two identical chat interfaces. |

---

## VIEW-RESEARCH panels (separate view, not visible in live view)

| # | Panel name | ID | Purpose | Data source | Functioning | Duplicates |
|---|---|---|---|---|---|---|
| 55 | New Strategies Found | `mod-sr-new` | Research catalog of scalp strategies defined for study. Status advisory only. | `/scalp-research` endpoint | ✅ Yes | No |
| 56 | Strategies Being Tested | `mod-sr-tested` | Strategies simulated on historical candles (out-of-sample research, 1:1 R). | `/scalp-research` endpoint | ✅ Yes | No |
| 57 | Best Performing | `mod-sr-best` | Research + live strategies ranked by net R, best performers. | `/scalp-research` endpoint | ✅ Yes | No |
| 58 | Worst Performing | `mod-sr-worst` | Worst research + live strategies by net R. | `/scalp-research` endpoint | ✅ Yes | No |
| 59 | Promotion Recommendations | `mod-sr-promo` | Advisory-only promotion candidates. Human promotes manually. | `/scalp-research` endpoint | ✅ Yes | No |

---

## Live-layout structural panels (3-column layout, always visible)

These are not collapsible `.mod` cards but persistent layout sections inside `#live-layout`.

| # | Panel name | ID | Purpose | Data source | Functioning | Duplicates |
|---|---|---|---|---|---|---|
| L1 | Verdict Hero | `blh-hero` | Large verdict display. Price, VWAP context, edge bar, AI reasoning box, 5 key observations table, current take, pills, waveform animation, Speak button. | `/status` poll | ✅ Yes | Overlaps `bl-verdict-panel` on the right; hero is the primary display |
| L2 | Left column | `bl-left` | Avatar, Market Context table (trend/momentum/volatility/liquidity), Performance (win rate/avg R:R), Mode toggle (SCALP/SWING), Market tabs (MGC/MNQ/MES/MYM), per-instrument Alerts and Auto-trade toggles. | `/status` + local state | ✅ Yes | No |
| L3 | Verdict card | `bl-verdict-panel` | Compact: verdict, edge grade, edge fill bar, edge score, direction, mode, reason text. | `/status` poll | ✅ Yes | Overlaps `blh-hero`; smaller companion display |
| L4 | Market Structure card | `bl-struct-panel` | BOS, CHOCH, Structure, Zone, Flow (CVD) — 5-row table. | `/status` poll | ✅ Yes | Summarized from `mod-cvd` + `mod-mi`; compact read |
| L5 | Levels to Watch card | `bl-levels-panel` | Supply zone, VWAP, Demand zone — 3-row table. | `/status` poll | ✅ Yes | No dedicated full panel; unique as a quick-glance summary |
| L6 | Trade Plan card | `bl-plan-panel` | Active trade plan: entry/stop/targets/sizing. | `/status` poll (trade_plan key) | ✅ Yes when plan active | No dedicated full panel |
| L7 | Position card | `bl-pos-panel` | Open position summary: instrument, direction, entry, current R, P&L. | `d.active_trade` via `/status` | ✅ Yes when position open | No dedicated full panel |
| L8 | Risk card | `bl-risk-panel` | Prop guard status, daily loss count, max loss limit, session risk. | `d.prop_firm` + `d.risk` via `/status` | ✅ Yes | Partial overlap with `mod-prop` (full prop panel) |
| L9 | Recent Activity strip | `bl-bottom` | Scrolling event feed: alert types, timestamps, source, instrument. | Event stream from webhook worker | ✅ Yes | No |

---

## Duplication summary

The clearest duplication relationships where cleanup is worthwhile:

| Panel | Duplicated by / overlaps with | Recommendation |
|---|---|---|
| `mod-analyst` (Analyst Mode) | `mod-report` (sub-synthesis of it), `mod-ai-decision-center` Decision tab, `mod-mb-narrative` | Candidate for removal; `mod-ai-decision-center` is the replacement |
| `mod-report` (Unified Analyst Report) | `mod-analyst` (superset), `mod-ai-decision-center` Decision tab | Candidate for removal |
| `mod-entryq` (Entry Quality) | `mod-ai-decision-center` Entry Quality tab | Candidate for removal; keep only if more detail needed |
| `mod-governor` (Confidence Governor) | `mod-ai-decision-center` Confidence tab | Candidate for removal; keep only if controls needed |
| `mod-memory` (Trade Memory) | `mod-ai-decision-center` Memory tab | Candidate for removal |
| `mod-assistant` (Ask the AI) | Chat interface inside `mod-brain` (same `/assistant` backend) | **Clear duplicate** — one should be removed |
| `mod-mb-voice` (Analyst Voice) | Caption/narration already inside `mod-brain` | **Clear duplicate** — single line repeated |
| `mod-mb-narrative` (Session Story) | `mod-brain` 4-column grid, mission list, bull/bear cases | Heavy overlap; review which version is more current |
| `mod-mb-confidence` (Confidence Over Time) | `mod-governor`, `mod-ai-decision-center` Confidence tab | Sparkline is unique; the stat block duplicates |
| `mod-bothold` + `mod-atm` | Both cover open positions | Keep both but consider merging into one open-position panel |
| `mod-thesis` (Market Thesis, legacy) | `mod-mb-thesis` (Thesis Tracker) | Different systems, shared theme — annotate clearly |
| `bl-verdict-panel` | `blh-hero` | Intentional: compact companion vs hero display |
| `bl-risk-panel` | `mod-prop` | Intentional: compact read vs full control panel |

---

## Panels that are legitimately conditional (hidden by flag or position state)

These are NOT duplication problems — they are correctly hidden when not applicable:

`mod-mi`, `mod-fastentry`, `mod-xmarket`, `mod-dual-sim`, `mod-swing-v2`, `mod-breakout`,
`mod-scalp-advisory`, `mod-stalk-mode`, `mod-active-thinking`, `mod-swingstrat`,
`mod-scalpdiag`, `mod-swingdiag`, `mod-trademgmt`, `mod-sessionq`, `mod-bothold`,
`mod-atm`, `mod-liverunner`, `mod-autoexit`, `mod-exec-reject`, `mod-broker-send-log`,
`mod-training`
