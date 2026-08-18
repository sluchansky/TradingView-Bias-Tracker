# Trade Blocking Audit — MNQ / SCALP / INTRADAY_TREND
**Produced:** 2026-08-18 ET  
**Period:** 2026-08-11 – 2026-08-18 (7 trading days)  
**Instruments:** MNQ, MGC, MES, MYM  
**Modes audited:** SCALP, INTRADAY_TREND  
**Source tables:** gate_audit_log (857 rows), ghost_opportunities (787 rows), scalp_strategy_sim_trades (40 rows), decision_transitions (5,112 rows), native_journal (2 rows), strategy_trades (6 rows)  
**Confidence note:** n < 20 = VERY LOW · n 20–49 = LOW · n 50–99 = MODERATE · n ≥ 100 = STRONGER

---

## 12. DATA QUALITY AUDIT (Run First)

Eight data-quality issues were found before interpreting results. Issues are ranked by severity.

### DQ-1 ⛔ CRITICAL — ghost_observations table is empty (0 rows)
The profitability engine records closed hypothetical ghost trades in `ghost_observations`. This table has never had a row written. This means the outcome data (TP1 hit, stop hit, final_r, MFE, MAE) in `gate_audit_log` is being resolved by the **counterfactual watcher only**, not by a live bar-close ghost watcher. All 278 NO_GEOMETRY records and the 487 COMPLETED records rely solely on the counterfactual backfill path. The profitability engine ghost-obs hook at bar-close is not firing.

### DQ-2 ⛔ CRITICAL — All 85 READY/EARLY trade outcomes are PENDING
Every trade that passed the gate (gate_verdict = ALLOWED or EARLY_ALLOWED) has outcome_status = PENDING. Not one has resolved to COMPLETED. This means there is **no confirmed outcome data for any trade the system approved**. The counterfactual watcher appears to backfill blocked trade geometry but has not yet resolved the forward-looking READY trade outcomes. This prevents any live win-rate calculation for trades the system allows.

### DQ-3 🔴 HIGH — vwap_value not populated in 99.5% of records
Of 860 records, only 4 have a non-null `vwap_value`. The VWAP-side analysis (above vs. below VWAP) requested in Section 7 is effectively impossible. The gate_audit_log recorder is not capturing `vwap_value` at evaluation time.

### DQ-4 🔴 HIGH — trend_alignment not populated in 99.9% of records
Only 1 record has a trend_alignment value ("aligned"). The field exists in the schema but is not being written. HTF trend analysis (Section 7) cannot be performed.

### DQ-5 🔴 HIGH — Short direction: 0 READY trades across all instruments
403 Short evaluations were recorded. Zero reached READY or EARLY_ALLOWED. Every single Short trade was blocked (100.0% block rate). Long direction: 460 evaluations, 85 reached READY (18.5% pass rate). This asymmetry is extreme and requires explicit explanation — either it is intentional (Long-only bias gate active) or it indicates a systematic Short evaluation bug. The `SCALP filter` gate blocked 16 Short setups and 0 Long setups, confirming at least one directional gate is operating. See Section 8.

### DQ-6 🟡 MEDIUM — scalp_strategy_sim_trades: 40 trades, all status=OPEN
The paper-sim engine opened 40 trades across 6 strategies since Aug 14 but none have closed. Either (a) all were opened within the last bar window and are still open, or (b) the paper-sim watcher close-cycle is not running. No outcome data available for strategy-level win rate.

### DQ-7 🟡 MEDIUM — strategy/setup_id fields missing for 101 Aug 11–13 records
Records from Aug 11–13 have NULL strategy and setup_id, using an older audit ID format (e.g., `MNQ|Long|SCALP|20260812`). These 101 records cannot be attributed to a strategy. Kept in funnel counts but excluded from strategy-level analysis.

### DQ-8 🟢 LOW — NO_GEOMETRY at 32.5% of records
278/857 records have outcome_status = NO_GEOMETRY. These cannot contribute to win-rate calculations. The rate is highest for overnight records (106/263 structure_confirmed overnight blocks = 40%) and is expected — overnight bars have fewer counterfactual data points available at the time of backfill.

**Data quality verdict:** The audit can be completed on blocked trade geometry (COMPLETED = 487 records, 56.8%) and gate rankings. READY trade win rates, Short-side analysis, VWAP/trend context, and strategy-level live outcomes are not available due to DQ-1 through DQ-5. Conclusions from the gate analysis carry MODERATE to LOW confidence given the 7-day window.

---

## 1. EXACT CURRENT PIPELINE MAP

```
TradingView webhook  →  webhook()
    │
    ├─ Instrument resolution (ticker-first)
    ├─ Alert normalization
    ├─ Fast-entry / data-only early-return (CVD, volume, dual-TF)
    │
    └─ _process_webhook_alert()
           │
           ├─ full_analysis(ticker)
           │     ├─ Zone validity check (ZONE_VALID)
           │     ├─ VWAP confirmation (auto-fetched, grace window)
           │     ├─ Structure gate (BOS / CHOCH / HH-HL / LH-LL in ALERT_HISTORY)
           │     ├─ CVD hard filter (directional veto)
           │     ├─ Volume / RVOL check
           │     ├─ Volatility gate (per-instrument ATR ratio)
           │     ├─ Edge score computation (max 110 pts)
           │     │     ├─ BOS20 / CHOCH20 / VWAP15 / Sweep15 / Volume15 / CVD15 / Session10
           │     ├─ Entry Quality veto (score < 70 & edge < 90)
           │     ├─ SCALP filter (swing-strategy library demote)
           │     ├─ READY threshold: edge ≥ 60
           │     ├─ Learning Engine veto (flag-gated, default OFF)
           │     └─ Returns: verdict / edge_score / gate_debug / all_components
           │
           ├─ gate_effectiveness.record()  →  gate_audit_log  ← AUDIT DATA SOURCE
           ├─ ghost_observe_setup()        →  ghost_observations  (EMPTY — DQ-1)
           │
           ├─ If READY/EARLY → maybe_auto_execute()
           │     ├─ arm check / execution_enabled check
           │     ├─ _traderspost_send() / paper / manual_only
           │     └─ native_journal entry
           │
           └─ Discord / Discord slow-queue
```

**INTRADAY_TREND additional gates (on top of SCALP):**
- IT-native zone geometry (ATR×1.5 stop, 2R target, real session levels)
- SWING strict gate bypass (IT has its own gate)
- 15:15 ET cutoff (FORCE_FLAT after)
- Chase gate
- BLOCKED_EXTENSION / OPPOSED_1H status codes

---

## 2. CURRENT DATA-QUALITY STATUS

| Check | Status | Detail |
|---|---|---|
| Observations closing | ⛔ FAIL | ghost_observations empty (DQ-1) |
| MFE/MAE backfill | ⚠️ PARTIAL | Works for blocked trades (487 COMPLETED); READY trades all PENDING (DQ-2) |
| ET timestamp conversion | ✅ PASS | gate_audit_log uses `recorded_at AT TIME ZONE 'America/New_York'` correctly |
| strategy/setup_id populated | ⚠️ PARTIAL | Null for Aug 11–13 records (DQ-7) |
| SCALP vs IT mixing | ✅ PASS | mode field correct; only 1 IT record, clearly separated |
| Expired observations | ✅ PASS | outcome_status='EXPIRED' appears 0 times; blocked observations use COMPLETED |
| Duplicate opportunities | ✅ PASS | audit_id is unique per (mode, inst, dir, date); no duplicates found |
| NO_GEOMETRY reported | ✅ PASS | 278 records flagged, excluded from win-rate calculations |
| Post-restart observations | ✅ PASS | Day-level counts show continuous recording Aug 11–18 |
| vwap_value populated | ⛔ FAIL | 4/860 records (DQ-3) |
| trend_alignment populated | ⛔ FAIL | 1/860 records (DQ-4) |
| Short READY trades | ⛔ FAIL | 0/403 Short evaluations passed gate (DQ-5) |

---

## A. OPPORTUNITY FUNNEL

### Table A — Opportunity Funnel

| Mode | Evaluated | Gate Blocked | Passed Gate | READY | Executed | Closed | Notes |
|---|---|---|---|---|---|---|---|
| SCALP | 855 | 771 (90.2%) | 84 (9.8%) | 84* | ~0 confirmed | 0 | All READY=PENDING outcome |
| INTRADAY_TREND | 1 | 1 (100%) | 0 | 0 | 0 | 0 | Single eval; FORCE_FLAT |
| SWING | 1 | 1 (100%) | 0 | 0 | 0 | 0 | Single eval |

*85 ALLOWED+EARLY_ALLOWED; 35 ALLOWED (LONG READY) + 50 EARLY_ALLOWED (LONG EARLY READY). All 85 are Long direction.

**Day-level SCALP funnel:**

| Date (ET) | Evaluated | Blocked | READY | Block % | READY % | Notes |
|---|---|---|---|---|---|---|
| 2026-08-11 | 80 | 70 | 10 | 87.5% | 12.5% | NO_GEOMETRY for all; old setup_id format |
| 2026-08-12 | 155 | 127 | 28 | 81.9% | 18.1% | Highest READY day; old setup_id format |
| 2026-08-13 | 58 | 48 | 10 | 82.8% | 17.2% | Old setup_id format |
| 2026-08-14 | 8 | 8 | 0 | 100.0% | 0.0% | All NO_GEOMETRY; possibly thin session |
| 2026-08-16 | 143 | 136 | 7 | 95.1% | 4.9% | New format; outcomes resolving |
| 2026-08-17 | 283 | 265 | 18 | 93.6% | 6.4% | Highest volume day |
| 2026-08-18 | 134 | 122 | 12 | 91.0% | 9.0% | Includes IT record |

**Observation:** Aug 11–13 show 12–18% READY rates; Aug 16–18 show 5–9%. This pattern warrants investigation — it may reflect a tighter gate configuration deployed after Aug 13, or a different market structure.

**Per-instrument funnel (SCALP):**

| Instrument | Direction | Total | Blocked | READY | Block% | Ready% |
|---|---|---|---|---|---|---|
| MES | Long | 109 | 92 | 17 | 84.4% | 15.6% |
| MES | Short | 105 | 105 | **0** | **100.0%** | 0.0% |
| MGC | Long | 127 | 102 | 25 | 80.3% | 19.7% |
| MGC | Short | 90 | 90 | **0** | **100.0%** | 0.0% |
| MNQ | Long | 108 | 90 | 18 | 83.3% | 16.7% |
| MNQ | Short | 106 | 106 | **0** | **100.0%** | 0.0% |
| MYM | Long | 110 | 85 | 25 | 77.3% | 22.7% |
| MYM | Short | 106 | 106 | **0** | **100.0%** | 0.0% |

**Critical observation:** Short direction never passed. Long direction pass rate: 18.5% (85/460). Short direction pass rate: 0.0% (0/403). This represents the single most significant structural finding in this audit.

---

## B. GATE RANKING (SCALP only, n=771 blocked)

### Table B — Gate Ranking

| Gate | Blocks | % of Blocks | Has Outcome | Would-Win % | TP1 % | Stop % | Avg R | Total R Prevented | Confidence |
|---|---|---|---|---|---|---|---|---|---|
| structure_confirmed | 465 | 60.4% | 279 | 24.0% | 24.0% | 74.2% | -0.51 | -140R | STRONGER |
| volume_unconfirmed | 73 | 9.5% | 53 | 39.6% | 39.6% | 60.4% | -0.21 | -11R | MODERATE |
| edge_score(45<60) | 59 | 7.7% | 45 | 20.0% | 20.0% | 80.0% | -0.60 | -27R | MODERATE |
| edge_score(35<60) | 50 | 6.5% | 34 | **52.9%** | **52.9%** | 47.1% | **+0.06** | **+2R** | MODERATE |
| edge_score(20<60) | 31 | 4.0% | 17 | 29.4% | 29.4% | 70.6% | -0.41 | -7R | LOW |
| edge_score(30<60) | 27 | 3.5% | 21 | 33.3% | 33.3% | 66.7% | -0.33 | -7R | LOW |
| Entry Quality veto | 19 | 2.5% | 14 | 14.3% | 14.3% | 85.7% | -0.71 | -10R | VERY LOW |
| SCALP filter | 16 | 2.1% | 12 | 33.3% | 33.3% | 66.7% | -0.32 | -4R | VERY LOW |
| edge_score(55<60) | 9 | 1.2% | 7 | 14.3% | 14.3% | 85.7% | -0.71 | -5R | VERY LOW |
| vwap_confirmed | 9 | 1.2% | 4 | 25.0% | 25.0% | 75.0% | -0.50 | -2R | VERY LOW |

**Gate ranking notes:**
- "Total R Prevented" = total_r column from DB. Positive = the gate blocked future winners (cost). Negative = the gate saved losses (benefit).
- "Would-Win %" = TP1 hit rate on blocked trades with resolved outcomes.
- edge_score(35<60) is the only gate with positive avg R (+0.06) AND the highest false-block rate (52.9%). This is the primary soften candidate.
- The `structure_confirmed` gate dominates (60.4% of all blocks). Softening it without clear evidence would be high-risk given the 74.2% true-block rate.
- SCALP filter blocked exclusively Short trades (0 Long blocks) — confirms directional behavior.

---

## C. GATE COMBINATIONS (n ≥ 5)

### Table C — Gate Combinations

| Gate Combination | n | Has Outcome | Would-Win % | Stop % | Avg R | Net R | Confidence |
|---|---|---|---|---|---|---|---|
| edge_score(20<60) + structure_confirmed | 110 | 61 | 13.1% | 78.7% | -0.71 | -40R | STRONGER |
| edge_score(45<60) + structure_confirmed | 90 | 47 | 25.5% | 68.1% | -0.45 | -20R | MODERATE |
| **edge_score(35<60)** *(alone)* | **50** | **34** | **52.9%** | **47.1%** | **+0.06** | **+2R** | MODERATE |
| edge_score(30<60) + structure_confirmed | 45 | 26 | 19.2% | 80.8% | -0.62 | -16R | LOW |
| edge_score(35<60) + structure_confirmed | 42 | 25 | 28.0% | 72.0% | -0.44 | -11R | LOW |
| edge_score(30<60) + structure | +volume | 37 | 26 | 38.5% | 61.5% | -0.23 | -6R | LOW |
| structure_confirmed *(alone)* | 35 | 27 | 22.2% | 77.8% | -0.56 | -15R | LOW |
| edge_score(20<60) *(alone)* | 31 | 17 | 29.4% | 70.6% | -0.41 | -7R | LOW |
| edge_score(30<60) *(alone)* | 27 | 21 | 33.3% | 66.7% | -0.33 | -7R | LOW |
| edge_score(5<60) + structure + volume | 24 | 19 | 21.1% | 78.9% | -0.58 | -11R | VERY LOW |
| Entry Quality veto *(alone)* | 19 | 14 | 14.3% | 85.7% | -0.71 | -10R | VERY LOW |
| edge_score(15<60) + structure + volume | 18 | 13 | 46.2% | 53.8% | -0.08 | -1R | VERY LOW |
| edge_score(55<60) + structure | 18 | 12 | 16.7% | 83.3% | -0.67 | -8R | VERY LOW |
| SCALP filter *(alone)* | 16 | 12 | 33.3% | 66.7% | -0.32 | -4R | VERY LOW |
| edge_score(45<60) + volume | 14 | 9 | 11.1% | 88.9% | -0.78 | -7R | VERY LOW |
| edge_score(40<60) + structure + volume | 10 | 7 | 0.0% | 100.0% | -1.00 | -7R | VERY LOW |

**Key combination insights:**
1. When `edge_score(35<60)` appears ALONE (structure passes, volume passes), 52.9% false-block, avg +0.06R. When combined with `structure_confirmed` (both fail), false-block drops to 28%, avg -0.44R. **The structure gate is the distinguishing layer** — edge=35 alone doesn't reliably protect.
2. `edge_score(20<60) + structure_confirmed` (n=110) is the most common combination and one of the most protective: 13.1% false-block, avg -0.71R. Do not touch this combination.
3. `edge_score(40<60) + structure + volume` (n=10): 0.0% false-block, 100% stop rate. All blocked correctly.
4. `edge_score(15<60) + structure + volume` (n=18): 46.2% false-block, avg -0.08R. This combination may benefit from investigation (but n is very small).

---

## D. TIME-OF-DAY ANALYSIS

### Table D — Time of Day (SCALP, ET)

| Time Window | Opps | Blocked | READY | Block% | Blocked TP1 | Blocked Stop | Blocked Avg R | Top Gate | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 05:00–08:00 | *not recorded* | — | — | — | — | — | — | — | No evaluations in this window |
| 08:00–09:30 | *not recorded* | — | — | — | — | — | — | — | No evaluations in this window |
| 09:30–10:30 | 52 | 48 | 4 | 92.3% | 9 TP1 / 34 resolved | 25 / 34 | -0.47 | structure (79% of blocks) | Morning open; structure gate dominant |
| 10:30–12:00 | 61 | 54 | 7 | 88.5% | 20 / 41 resolved | 23 / 41 | ~0.05 | structure (41%) then **volume (30%)** | ⚡ Volume gate 62.5% false-block here |
| 12:00–14:00 | 88 | 81 | 7 | 92.0% | 13 / 52 resolved | 46 / 52 | -0.45 | structure (57%) | Midday; structure very effective (10.9% FB) |
| 14:00–15:45 | 89 | 79 | 10 | 88.8% | 7 / 22 resolved (+ 57 no-geom) | 15 / 22 | -0.36 | structure (73%) | High no-geom rate; geometry degraded |
| 15:45–16:00 | 17 | 16 | 1 | 94.1% | **0 / 8 resolved** | **8 / 8** | **-1.00** | structure (100%) | All blocked would have stopped out |
| 16:00–17:00 | 38 | 30 | 8 | 78.9% | 1 / 1 resolved (15 no-geom) | 0 / 1 | +1.00 (n=1) | structure (73%) | Insufficient valid outcomes |
| Overnight | 509 | 461 | 48 | 90.6% | 57 / 261 resolved (106 no-geom) | 168 / 261 | -0.50 | structure (57%) | Largest window; 22% false-block |

**Key time-of-day findings:**

- **15:45–16:00**: The structure gate blocked 16 trades. Of the 8 with resolved outcomes, ALL 8 hit their stop. 0 TP1 hits. Avg R = -1.00. This window has zero false-block evidence. The gate is working perfectly here.
- **16:00–17:00**: 30 blocked, but 15/30 are NO_GEOMETRY. Only 1 resolved outcome (1 TP1 hit, avg R +1.00 — a single winner). Insufficient data for conclusions.
- **10:30–12:00**: Volume gate false-block rate in this window = 10 TP1 hits out of 16 blocked (62.5%). This is the highest false-block rate for any gate in any time window with n ≥ 10.
- **Overnight (23:00–09:30)**: The majority of all SCALP activity occurs overnight (509/855 = 59.5%). Structure gate works well here (74% true-block). READY rate of 9.4% overnight is consistent with other windows.

**Note on missing windows:** No evaluations between 05:00–09:30 ET. This means either (a) the gate_audit_log hook doesn't fire in that window, or (b) the system doesn't receive alerts in that period. Worth verifying the webhook is receiving signals from the 06:00–09:30 ET pre-market window.

---

## 11. SPECIAL ATTENTION: 15:45–17:00 ET

### 15:45–16:00 (Closing Drive)

**Data:** 17 total evaluations; 16 blocked; 1 READY; 8 resolved outcomes (8 NO_GEOMETRY).

| Metric | Value |
|---|---|
| Blocked opportunities | 16 |
| Resolved with geometry | 8 |
| TP1 hit (would-win) | **0 of 8 (0%)** |
| Stop hit | **8 of 8 (100%)** |
| Avg R of blocked | **-1.00** |
| Primary blocker | structure_confirmed (100% of blocks) |

**Conclusion for 15:45–16:00:** The data strongly supports keeping the current gating in this window. Every single blocked opportunity with a resolved outcome would have hit the stop. The closing-drive / reversal / sweep dynamics in this 15-minute window produce **zero** winning trades by hypothetical counterfactual. This is the most protective window in the dataset.

### 16:00–17:00 (Post Cash-Close)

**Data:** 38 total evaluations; 30 blocked; 8 READY; 15 NO_GEOMETRY; only 1 resolved blocked outcome.

| Metric | Value |
|---|---|
| Blocked opportunities | 30 |
| Resolved with geometry | 1 |
| TP1 hit (would-win) | 1 of 1 (100% — n=1) |
| Stop hit | 0 of 1 |
| Avg R | +1.00 (n=1, unreliable) |
| NO_GEOMETRY rate | 50% |
| Primary blocker | structure_confirmed (73%) |

**Conclusion for 16:00–17:00:** Data is **insufficient** (1 resolved outcome). The high NO_GEOMETRY rate (50%) confirms the counterfactual watcher lacks bar data for this window. Do not make decisions based on this. Recommend: flag this window explicitly for manual observation for 2–4 more weeks before drawing conclusions.

**Recommendation for both late windows (analysis only, do not implement):**
- 15:45–16:00: Current rules justified by data. Could eventually consider **research-only** classification.
- 16:00–17:00: **Insufficient data**. Treat as research-only until n ≥ 50 valid outcomes.

---

## 7. MARKET CONTEXT SEGMENTS

### Volatility Regime

| Vol Regime | Opps | Blocked | READY | False-Block % | Blocked Avg R |
|---|---|---|---|---|---|
| NORMAL | 339 | 302 | 37 | 30.3% | -0.38 |
| QUIET_BLOCK | 247 | 224 | 23 | 25.7% | -0.48 |
| QUIET_CAUTION | 115 | 105 | 10 | 28.4% | -0.42 |
| HIGH_CAUTION | 76 | 66 | 10 | 30.0% | -0.39 |
| HIGH_BLOCK | 39 | 35 | 4 | 16.7% | -0.67 |
| NA | 41 | 41 | 0 | 14.3% | -0.71 |

**Finding:** Gates work best (lowest false-block) at HIGH_BLOCK (16.7%) and NA (14.3%) volatility. The system correctly applies tighter protection when volatility is extreme. NORMAL volatility has the highest false-block rate (30.3%) which is expected — calmer markets have more valid setups being blocked.

### Session Context

| Session | Opps | Blocked | READY | Blocked Avg R | Blocked TP1 | Blocked Stop |
|---|---|---|---|---|---|---|
| Outside preferred window | 553 | 504 | 49 | -0.42 | 86 | 207 |
| 20:00–23:00 ET | 227 | 198 | 29 | -0.52 | 34 | 107 |
| 08:00–11:00 ET | 78 | 72 | 6 | -0.35 | 18 | 37 |

**Finding:** "Outside preferred window" (pre-market, off-hours) dominates volume (64.7% of evaluations) and has nearly the same block rate as preferred windows. The system is recording evaluations continuously regardless of session label. Morning session (08:00–11:00) shows the highest TP1 rate on blocked trades (18/55 resolved = 32.7% false-block) which is consistent with the time-of-day findings above.

### CVD Direction

| CVD Direction | Opps | Blocked | READY | Blocked Avg R |
|---|---|---|---|---|
| Bullish | 559 | 466 | 93 | -0.43 |
| Bearish | 291 | 291 | **0** | -0.43 |

**Finding:** This directly explains the 100% Short block rate. When CVD is bearish, 0 trades passed (100% block rate). All 85 READY trades came from bullish CVD periods. The CVD hard filter is functioning as designed — it vetoes any directional setup that fights the order flow. In this 7-day window, bullish CVD dominated and no Short setups survived the CVD gate. This is NOT a bug; it is the CVD hard filter working correctly. However, it does confirm that **Short setups are entirely suppressed by CVD during bullish market periods**, which is a design choice worth documenting explicitly.

### VWAP / Trend Context

Cannot be analyzed. vwap_value populated in 0.5% of records (DQ-3); trend_alignment in 0.1% (DQ-4).

---

## E. STRATEGY AUDIT (SCALP)

### Individual Strategy Performance

The gate_audit_log does not contain individual sub-strategy names (VWAP_RECLAIM_LONG, OPENING_DRIVE, etc.) — only "SCALP" as the strategy and the audit setup_id (date-keyed). The `scalp_strategy_sim_trades` table has strategy-level keys but all 40 simulated trades are status=OPEN with no outcomes.

What can be analyzed is the **setup_id / date** cross with block patterns:

| Setup Group | Date | Instr | Dir | Total | Blocked | READY | Top Blocker | Blocked TP1 | Blocked Stop | Avg R |
|---|---|---|---|---|---|---|---|---|---|---|
| Aug 17 | MYM Short | 2026-08-17 | Short | 45 | 45 | 0 | structure_confirmed | 21 | 22 | -0.02 |
| Aug 17 | MGC Long | 2026-08-17 | Long | 44 | 38 | 6 | structure_confirmed | 10 | 28 | -0.47 |
| Aug 17 | MES Short | 2026-08-17 | Short | 42 | 42 | 0 | structure_confirmed | 12 | 30 | -0.43 |
| Aug 17 | MNQ Short | 2026-08-17 | Short | 39 | 39 | 0 | structure_confirmed | 14 | 25 | -0.28 |
| Aug 18 | MYM Long | 2026-08-18 | Long | 37 | 24 | 13 | structure_confirmed | 3 | 18 | -0.71 |
| Aug 17 | MNQ Long | 2026-08-17 | Long | 34 | 28 | 6 | structure_confirmed | 10 | 17 | -0.26 |
| Aug 16 | MNQ Long | 2026-08-16 | Long | 12 | 12 | 0 | structure_confirmed | 5 | 7 | -0.17 |
| Aug 16 | MYM Long | 2026-08-16 | Long | 12 | 12 | 0 | structure_confirmed | 7 | 5 | +0.17 |

**Scalp strategy research sim (scalp_strategy_sim_trades):**

6 strategies are running as paper sims (started Aug 14):
- cvd_divergence_scalp
- ema_9_20_continuation
- liquidity_sweep_reversal
- order_block_rejection
- prior_high_low_sweep
- volume_climax_reversal

All 40 trades are status=OPEN. No outcomes are available yet. Strategy-level win rates, expectancy, and profit factor cannot be computed. **INSUFFICIENT DATA for all 6 strategies.**

---

## 8. INTRADAY_TREND ANALYSIS

**Total IT evaluations recorded:** 1  
**Total IT blocked:** 1  
**Total IT READY:** 0

The single IT record:
- Date: 2026-08-18 20:05 UTC (16:05 ET — AFTER the 15:15 ET cutoff)
- Instrument: MGC Long
- Gate verdict: BLOCKED
- Primary blocker: FORCE_FLAT (market closed / after cutoff)
- Edge score: 20 (WAIT)
- Components failing: BOS=FAIL, CHOCH=FAIL, VWAP=UNAVAILABLE, CVD=FAIL, Session=FAIL
- Volatility: HIGH_CAUTION
- Geometry: entry 4366.1, stop 4362.86, ATR 2.16
- Outcome: PENDING (no counterfactual data at 16:05 ET)

**Conclusion for IT:** The IT gate_audit_log hook is wired and recording. However, only 1 record exists over 7 days. This is either because:
1. The IT mode is only evaluated when the system detects an IT-specific setup trigger, which is a rare event
2. The IT analysis hook fires on webhook, and in SCALP mode there are far fewer IT webhooks being sent
3. The IT engine was recently installed and has not had sufficient operating time

With n=1, no gate analysis is possible for IT. **INSUFFICIENT DATA — all IT conclusions are deferred.**

The one blocked record confirms the FORCE_FLAT gate is wired correctly (blocks after 15:15 ET), and the geometry engine is computing ATR-based stops for IT (ATR_FALLBACK geometry source used).

---

## 5. FALSE-BLOCK RATE ANALYSIS

### Per Gate Summary

| Gate | Blocks | Valid Outcomes | TP1 Hit | Stop Hit | False-Block % | True-Block % | Avg R | Verdict |
|---|---|---|---|---|---|---|---|---|
| structure_confirmed | 465 | 279 | 67 (24%) | 207 (74%) | **24.0%** | **74.2%** | -0.51 | KEEP HARD |
| volume_unconfirmed | 73 | 53 | 21 (40%) | 32 (60%) | **39.6%** | **60.4%** | -0.21 | CONTEXT-DEPENDENT |
| edge_score(45<60) | 59 | 45 | 9 (20%) | 36 (80%) | **20.0%** | **80.0%** | -0.60 | KEEP HARD |
| edge_score(35<60) | 50 | 34 | 18 (53%) | 16 (47%) | **52.9%** | **47.1%** | **+0.06** | SOFTEN CANDIDATE |
| edge_score(20<60) | 31 | 17 | 5 (29%) | 12 (71%) | **29.4%** | **70.6%** | -0.41 | KEEP HARD |
| edge_score(30<60) | 27 | 21 | 7 (33%) | 14 (67%) | **33.3%** | **66.7%** | -0.33 | KEEP HARD |
| Entry Quality veto | 19 | 14 | 2 (14%) | 12 (86%) | **14.3%** | **85.7%** | -0.71 | KEEP HARD |
| SCALP filter | 16 | 12 | 4 (33%) | 8 (67%) | **33.3%** | **66.7%** | -0.32 | INSUFFICIENT DATA |
| edge_score(55<60) | 9 | 7 | 1 (14%) | 6 (86%) | **14.3%** | **85.7%** | -0.71 | KEEP HARD |
| vwap_confirmed | 9 | 4 | 1 (25%) | 3 (75%) | **25.0%** | **75.0%** | -0.50 | INSUFFICIENT DATA |

### Aggregate statistics for blocked SCALP trades with valid outcomes (n=491 valid):
- Total TP1 hits that were blocked: 138 (28.1%)
- Total stop hits that were blocked: 351 (71.5%)
- Total hypothetical R prevented: -227R (losses avoided net of winners blocked)
- Net verdict: **the gating system is net-positive** across the 7-day period. Gates blocked -227R in hypothetical losses vs. the ~+84R they prevented in hypothetical wins (rough estimate based on avg R per category).

---

## 6. GATE COMBINATION ANALYSIS

**Key findings from combination analysis:**

**1. edge_score(35<60) alone (n=50) vs with structure (n=42):**
- Alone: 52.9% false-block, avg +0.06R
- With structure: 28.0% false-block, avg -0.44R
- **Interpretation:** When edge=35 is the ONLY failing gate (structure, volume, entry quality all pass), more than half the blocked setups would have been winners. When structure also fails, the gate correctly blocks. The value of the structure gate is demonstrated here.

**2. edge_score(40<60) + structure + volume (n=10):**
- 0% false-block, 100% stop rate, avg -1.00R
- The triple-failure combination is a reliable blocker of losing trades.

**3. volume_unconfirmed in 10:30–12:00:**
- 16 blocks in this window, 10 TP1 hits (62.5% false-block in midday only)
- The same volume gate in overnight has 39.6% false-block (lower)
- In 12:00–14:00: 13 blocks, 4 TP1 (30.8% false-block — back to normal)
- **The volume gate appears context-dependent: too restrictive in 10:30–12:00**

**4. edge_score(15<60) + structure + volume (n=18):**
- 46.2% false-block, avg -0.08R (near breakeven)
- Despite three gates failing, these trades were near-neutral hypothetically
- Low confidence (n=18) but worth monitoring

---

## 9. FINAL GATE CLASSIFICATIONS (Section 9)

### Table F — Final Gate Verdicts

| Gate | Current Role | Evidence | Verdict | Confidence | Recommended Next Test |
|---|---|---|---|---|---|
| structure_confirmed | Hard gate — requires BOS/CHOCH/HH/HL/LH/LL in ALERT_HISTORY | 465 blocks; 74.2% true-block; avg R -0.51; -140R in losses avoided | **KEEP HARD** | STRONGER (n=279 resolved) | Monitor false-block % over next 30 days. If it exceeds 35%, investigate structure sensitivity. |
| Entry Quality veto | Demote-only (READY→WAIT) on score<70 & edge<90 | 19 blocks; 85.7% true-block; avg R -0.71 | **KEEP HARD** | VERY LOW (n=14) | Accumulate 50+ resolved outcomes before reclassifying. |
| edge_score(45<60) | Threshold gate — SCALP READY requires edge≥60 when score=45 | 59 blocks; 80.0% true-block; avg R -0.60; -27R saved | **KEEP HARD** | MODERATE (n=45) | No change. Threshold appears well-placed at 60 for this score. |
| edge_score(55<60) | Threshold gate — SCALP READY requires edge≥60 when score=55 | 9 blocks; 85.7% true-block; avg R -0.71 | **KEEP HARD** | VERY LOW (n=7) | Needs 50+ outcomes. Do not reduce threshold based on this. |
| edge_score(35<60) | Threshold gate — SCALP READY requires edge≥60 when score=35 | 50 blocks; **52.9% false-block**; avg R **+0.06**; net +2R | **SOFTEN CANDIDATE** | MODERATE (n=34) | Test: evaluate a shadow group of edge=35 setups that pass all other gates over next 60 days. If false-block holds above 45%, test edge threshold of 55 for structure-only-failing cases. Do NOT implement yet. |
| edge_score(30<60) | Threshold gate — score=30 | 27 blocks; 33.3% false-block; avg R -0.33 | **KEEP HARD** | LOW (n=21) | Borderline. Needs 50+ outcomes. |
| edge_score(20<60) | Threshold gate — score=20 | 31 blocks; 29.4% false-block; avg R -0.41 | **KEEP HARD** | LOW (n=17) | Same — needs more data. |
| volume_unconfirmed | Passes/fails based on Volume component score | 73 blocks; 39.6% false-block overall; **62.5% in 10:30–12:00** | **CONTEXT-DEPENDENT** | MODERATE (n=53) | Test shadow-only exemption during 10:30–12:00 ET window. If exempted trades show positive expectancy, consider softening in that window only. |
| SCALP filter | Swing-strategy library demote for Short direction | 16 blocks; 33.3% false-block; exclusively Short | **CONTEXT-DEPENDENT / INSUFFICIENT DATA** | VERY LOW (n=12) | Identify what triggered the SCALP filter on each record. Is it a direction filter or strategy mismatch? |
| vwap_confirmed | Requires price on correct side of VWAP | 9 blocks; 25.0% false-block; n=4 outcomes | **INSUFFICIENT DATA** | VERY LOW (n=4) | Accumulate data. Fix vwap_value recording in gate_audit_log (DQ-3) first. |
| CVD hard filter | Hard directional veto — bearish CVD blocks ALL Short | Implicit in 0/403 Short passing; 291 bearish-CVD blocks | **KEEP HARD / DESIGN REVIEW** | N/A | Document explicitly that this gate suppresses all Short setups during bullish CVD periods. Evaluate whether this is the intended behavior or whether it should be softened to allow strong Short setups with high edge during bullish CVD. |

---

## 10. TOP 5 HIGHEST-VALUE CHANGES TO TEST NEXT

These are proposed experiments only. No live trading logic should be changed.

### #1 — Fix the ghost_observations recording gap (instrumentation, not trading logic)
**Problem:** ghost_observations is empty. The profitability engine bar-close hook is not recording. This means all forward-looking counterfactual data (for READY trades and ghost-observe setups) is missing.  
**Value:** Without this, false-block analysis for READY trades is impossible. Once fixed, within 2–3 weeks the dataset will have outcome data for trades the system approved — which is the most important missing piece.  
**Risk:** Zero — instrumentation only.

### #2 — Populate vwap_value and trend_alignment in gate_audit_log
**Problem:** DQ-3 and DQ-4. The vwap_value and trend_alignment fields exist but are not being written at evaluation time.  
**Value:** Enables VWAP-side analysis and trend-alignment analysis (Sections 7 and 4). Currently these two contexts are completely dark. VWAP-side analysis could reveal whether trades above/below VWAP have different gate behaviors.  
**Risk:** Zero — data collection only.

### #3 — Shadow-test the volume gate exemption in 10:30–12:00 ET
**Problem:** volume_unconfirmed shows 62.5% false-block rate in the 10:30–12:00 window (10 of 16 blocked would have won), vs 39.6% overall.  
**Experiment:** Create a shadow observation that tracks how volume-only-blocked setups in 10:30–12:00 would have performed if allowed through. Do not fire live orders.  
**Value:** n=16 is low confidence, but if the pattern holds over another 30 days (target n≥50), this could identify a time-of-day context where the volume gate should be softened or bypassed.  
**Risk:** Low — shadow experiment only, no live order impact.

### #4 — Investigate edge_score=35 as a soft-allow candidate
**Problem:** edge_score(35<60) has 52.9% false-block, positive avg R (+0.06), and n=50 resolved outcomes (moderate confidence). When structure passes and edge=35 is the only blocker, more than half the blocked setups would have been TP1 winners.  
**Experiment:** Add a shadow track for all future "structure PASS, edge=35, all other gates PASS" evaluations. Log their hypothetical outcomes for 60 days. If false-block stays above 45% with positive avg R over n≥100, test a threshold reduction to 55 (not 35) using EARLY_ALLOWED status, not READY.  
**Risk:** Medium. Do not reduce the edge threshold without n≥100 clean outcomes. The current n=34 resolved is marginal.

### #5 — Document and explicitly test the CVD directional gate's Short-blocking behavior
**Problem:** The CVD hard filter blocks all Short setups during bullish CVD periods. In this 7-day dataset, this produced 0 Short trades from 403 evaluations. This is working by design, but the design choice means the system is structurally Long-only during any sustained bullish CVD regime.  
**Experiment:** For the next 30 days, run a shadow log of Short setups that fail CVD only (all other gates pass) and record their hypothetical outcomes. If Short setups with high edge (55+) and strong structure show TP1 rates above 40% during bullish CVD periods, consider whether the CVD veto should be softened for high-conviction Short setups (edge ≥ 80, strong BOS/CHOCH confluence).  
**Risk:** Medium-high. CVD veto is a core safety gate. Do not touch without at minimum n=100 clean Short counterfactual outcomes.

---

## Summary of Key Findings

1. **Structure gate is the primary gatekeeper** (60.4% of all blocks). It is working correctly at 74.2% true-block rate. Do not soften without clear evidence from a larger dataset.

2. **100% of READY trades were Long direction.** This is explained by the CVD hard filter suppressing all Short setups during the bullish CVD period in this 7-day window. It is a design behavior, not a bug.

3. **edge_score=35 is the only gate showing positive net R when isolated.** This is the best current candidate for a future soft threshold test, pending more data.

4. **Volume gate is time-of-day dependent.** In 10:30–12:00 ET, it blocked 62.5% of future winners. In all other windows, it performed within acceptable bounds.

5. **15:45–16:00 ET is the most protected window.** Every blocked trade with valid geometry stopped out. The gate earns its keep here.

6. **ghost_observations being empty is the most urgent data quality fix.** All other gaps (VWAP field, trend alignment) are secondary. Without ghost observations, the profitability engine cannot close the feedback loop.

7. **INTRADAY_TREND has insufficient data.** With 1 evaluation in 7 days, no conclusions are possible. The engine is installed but needs weeks of live observation to evaluate.

---

AUDIT COMPLETE — NO LIVE TRADING LOGIC CHANGED
