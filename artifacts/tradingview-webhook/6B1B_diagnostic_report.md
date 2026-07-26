# Phase 6B.1B — Read-Only Strategy Diagnostic Report
**Baseline:** `BL-20260726-043053-0cc8364`  
**Report date:** 2026-07-26  
**Author:** Phase 6B.1B automated analysis  
**Status:** COMPLETE — read-only diagnostic, no code changed except determinism fix

---

## PART 1 — PRE-FLIGHT AND BASELINE CORRECTION

**Branch:** `polish-v1`  
**HEAD:** `f77cef4`  
**Baseline ID:** `BL-20260726-043053-0cc8364`  
**Source commit:** `0cc8364`  
**Config hash:** `68fd5b2c96b4f2ee` (stored in baseline_matrix_results)  
**Dataset IDs:** 8 (MNQ), 9 (MES), 10 (MGC), 11 (MYM)  
**Matrix count:** 40 rows (5 strategies × 4 instruments × 2 modes)  
**Trade count:** 7,909 closed trades  
**Breakdown count:** 64 rows (2 direction + 23 et_hour + 4 instrument + 8 instrument_mode + 2 mode + 6 month + 4 session + 5 strategy + 4 vol_regime + 6 weekday)  
**Baseline status:** COMPLETE  

**Determinism fix applied:** `bt_baseline.py` lines 158–159 changed from  
`list(bt.VALID_SYMBOLS)` / `list(bt.VALID_TIMEFRAMES)` (non-deterministic if  
sets) → `sorted(bt.VALID_SYMBOLS)` / `sorted(bt.VALID_TIMEFRAMES)`.  
Both are tuples in the engine so hash was already stable at runtime; the fix  
makes the contract explicit for future refactors that might introduce sets.  
`NEWS_BLACKOUTS_ET` is a tuple of tuples — `[list(w) for w in ...]` already  
serialises deterministically (no change needed).

---

## PART 2 — CORPUS OVERVIEW

| Dimension | Value |
|---|---|
| Date range | 2026-01-02 → 2026-06-30 (H1 2026) |
| Total trades | **7,909** |
| Overall win rate | **36.38%** |
| Total net R | **−377.49 R** |
| Average R/trade | −0.0477 R |
| Median R/trade | −1.011 R (stop-loss) |
| Profit factor | **0.9274** |
| Gross positive R | +4,821.09 R |
| Gross negative R | −5,198.58 R |
| Avg hold time | 585.2 min (~9.75 h) |
| Median hold time | 65 min |
| Min realized R | −1.174 R (MYM stop) |
| Max realized R | +3.9996 R (ORB 4R target) |
| Avg initial stop | 70.68 pts |
| Stddev R | 1.368 |

The corpus is overwhelmingly negative: 63.6% of trades are losers. The median  
R of −1.011 shows the engine operates as expected — the typical trade either  
hits its 1.5R target or its −1R stop. The gap between avg (−0.048) and median  
(−1.011) is entirely explained by the positive tail from ORB's 4R targets.

---

## PART 3 — STRATEGY SUMMARY TABLE

| Strategy | Trades | WR% | Net R | Avg R | PF | Long R | Short R | Comm | Slip |
|---|---|---|---|---|---|---|---|---|---|
| LSR | 2,644 | 39.83 | −112.38 | −0.0425 | 0.919 | −39.04 | −73.34 | 68.5 | 42.6 |
| OD | 1,309 | 39.95 | −29.46 | −0.0225 | 0.964 | −0.86 | −28.60 | 20.3 | 12.5 |
| ORB | 1,254 | 18.33 | −116.97 | −0.0933 | 0.891* | +56.49 | −173.65 | 20.4 | 13.2 |
| REB | 926 | 38.23 | −67.99 | −0.0735 | 0.890 | −23.14 | −44.85 | 22.5 | 14.1 |
| VTC | 1,776 | 39.67 | −50.49 | −0.0284 | 0.965 | +12.93 | −63.43 | 32.3 | 19.0 |

*ORB PF is distorted: 80.6% of trades are losers at −1R; winners hit 4R.  
**No strategy is profitable.** LSR and ORB Short carry the most negative load.  
OD and VTC have the least negative expectancy of the five.

---

## PART 4 — EXIT-REASON DISTRIBUTION

| Strategy | Exit | Count | Avg R | Net R | Avg Hold |
|---|---|---|---|---|---|
| LSR | Stop −1R | 1,591 | −1.043 | −1,659.8 | 247 min |
| LSR | Target 1.5R | 1,048 | +1.475 | +1,546.0 | 358 min |
| LSR | Open EOD | 5 | +0.298 | +1.5 | 938 min |
| OD | Stop −1R | 786 | −1.026 | −806.0 | 243 min |
| OD | Target 1.5R | 523 | +1.485 | +776.6 | 312 min |
| ORB | Stop −1R | 1,017 | −1.027 | −1,044.1 | 817 min |
| ORB | Target 4R | 231 | +3.983 | +920.0 | 3,195 min |
| ORB | Open EOD | 6 | +1.154 | +6.9 | 367 min |
| REB | Stop −1R | 570 | −1.039 | −591.9 | 976 min |
| REB | Target 1.5R | 355 | +1.475 | +523.6 | 498 min |
| REB | Open EOD | 1 | +0.359 | +0.4 | 23,150 min |
| VTC | Stop −1R | 1,065 | −1.028 | −1,095.2 | 656 min |
| VTC | Target 1.5R | 704 | +1.482 | +1,043.0 | 667 min |
| VTC | Open EOD | 7 | +0.240 | +1.7 | 924 min |

**Key finding:** For every 1.5R strategy, stops outnumber targets ~3:2. The  
win-rate needed to break even at 1.5R with 1R risk is 40%: all five strategies  
are sub-40% except ORB (which uses 4R). The stop-to-target ratio reveals the  
fundamental problem — these setups detect the wrong side more than 60% of the  
time under current H1-2026 conditions.

ORB hold time at target (3,195 min ≈ 53 h) confirms these are multi-day  
runners; stop holds (817 min ≈ 13.6 h) show that losses also take time to  
resolve, making daily-cap decisions more expensive in practice.

---

## PART 5 — INSTRUMENT ROLL-UP

| Instrument | Trades | WR% | Net R | Avg R | PF | Long R | Short R | Avg Stop |
|---|---|---|---|---|---|---|---|---|
| MES | 2,220 | 35.77 | −179.45 | −0.0808 | 0.880 | −32.36 | −147.09 | 17.7 pts |
| MGC | 1,126 | 38.54 | **+40.10** | **+0.0356** | **1.057** | +11.93 | +28.18 | 24.4 pts |
| MNQ | 2,460 | 36.18 | −88.00 | −0.0358 | 0.945 | +63.47 | −151.44 | 89.8 pts |
| MYM | 2,103 | 36.09 | −150.18 | −0.0714 | 0.893 | −36.65 | −113.52 | 129 pts |

**MGC is the only profitable instrument overall (+40.1 R, PF 1.057).** MES  
carries the worst expectancy per trade. MNQ Longs are the one profitable  
direction across all instruments (+63.5 R), but MNQ Shorts destroy −151.4 R.

---

## PART 6 — MODE ROLL-UP

| Mode | Trades | WR% | Net R | Avg R | PF | Long R | Short R |
|---|---|---|---|---|---|---|---|
| SCALP | 4,437 | 36.44 | −225.81 | −0.0509 | 0.923 | +29.76 | −255.57 |
| SWING | 3,472 | 36.29 | −151.68 | −0.0437 | 0.933 | −23.37 | −128.30 |

SWING has a marginally better PF (0.933 vs 0.923) and better per-trade  
expectancy (−0.044 vs −0.051). Both modes are loss-making. The Short side  
is catastrophically negative in both modes; Longs are only marginally positive  
in SCALP (+29.8 R).

---

## PART 7 — LONG VS SHORT BREAKDOWN BY STRATEGY

| Strategy | Dir | Trades | WR% | Net R | Avg R | PF |
|---|---|---|---|---|---|---|
| LSR | Long | 1,460 | 40.48 | −39.04 | −0.0267 | 0.957 |
| LSR | Short | 1,184 | 38.94 | −73.34 | −0.0620 | 0.903 |
| OD | Long | 708 | 40.82 | −0.86 | −0.0012 | 0.998 |
| OD | Short | 601 | 38.94 | −28.60 | −0.0476 | 0.924 |
| ORB | Long | 622 | 22.51 | **+56.49** | **+0.0908** | **1.114** |
| ORB | Short | 632 | 15.19 | −173.65 | −0.2748 | **0.684** |
| REB | Long | 440 | 39.32 | −23.14 | −0.0526 | 0.917 |
| REB | Short | 486 | 37.65 | −44.85 | −0.0923 | 0.857 |
| VTC | Long | 859 | 41.56 | **+12.93** | **+0.0151** | **1.025** |
| VTC | Short | 917 | 38.50 | −63.43 | −0.0692 | 0.891 |

**Critical finding — the Short side is losing on every strategy:**

- ORB Short is catastrophic: 15.2% WR, −173.6 R, PF 0.684
- The worst single-instrument short drag is MNQ/LSR Short (−83.6 R in 343 trades)
- MES/ORB Short: 11.4% WR, −85.1 R (worst expectancy at −0.462/trade)

**The two profitable directions in the corpus:**
- ORB Long: +56.5 R, PF 1.114 — driven by 4R runners making up for low WR
- VTC Long: +12.9 R, PF 1.025 — near breakeven but reliable edge

**The one profitable instrument/strategy/direction triple:**
- MGC Short across all strategies: +50.2 R (LSR Short alone: +50.2 R, WR 52%)
- MGC OD Short: +26.3 R, WR 53.8% — the engine correctly short-fades MGC

---

## PART 8 — VOLATILITY REGIME ANALYSIS

| Strategy | Regime | Trades | WR% | Net R | Avg R | PF | Avg Stop |
|---|---|---|---|---|---|---|---|
| LSR | BALANCED | 1,034 | 38.30 | −87.30 | −0.0844 | 0.870 | 41.0 |
| LSR | TRENDING | 888 | 38.74 | −65.89 | −0.0742 | 0.885 | 33.2 |
| LSR | VOLATILE | 720 | 43.33 | **+43.14** | **+0.0599** | **1.103** | 94.9 |
| LSR | RANGING | 2 | 0.00 | −2.34 | — | 0.000 | 20.5 |
| OD | BALANCED | 81 | 33.33 | −15.94 | −0.197 | 0.715 | 42.5 |
| OD | TRENDING | 428 | 43.93 | **+31.62** | **+0.0739** | **1.128** | 46.2 |
| OD | VOLATILE | 800 | 38.50 | −45.14 | −0.0564 | 0.910 | 105.2 |
| ORB | BALANCED | 249 | 18.88 | −28.38 | −0.114 | 0.864 | 56.1 |
| ORB | TRENDING | 462 | 17.97 | −59.56 | −0.129 | 0.847 | 50.1 |
| ORB | VOLATILE | 543 | 19.52 | −29.22 | −0.0538 | 0.934 | 111.2 |
| REB | BALANCED | 286 | 33.92 | −53.78 | −0.188 | 0.726 | 125.5 |
| REB | TRENDING | 427 | 41.69 | **+1.21** | **+0.0028** | **1.005** | 35.4 |
| REB | VOLATILE | 213 | 38.03 | −15.42 | −0.0724 | 0.886 | 98.7 |
| VTC | BALANCED | 1,229 | 39.79 | −46.10 | −0.0375 | 0.940 | 70.5 |
| VTC | VOLATILE | 547 | 40.40 | −4.40 | −0.0080 | 0.987 | 101.1 |

**Key regime findings:**
1. **LSR loves VOLATILE** (+43.1 R, PF 1.103): wide stops (94.9 pts avg) give  
   sweeps room to reverse — the strategy's core thesis works when ATR is large.
2. **OD needs TRENDING** (+31.6 R, PF 1.128): opening drives require a clear  
   intraday trend to sustain the move; BALANCED/VOLATILE regimes disrupt them.
3. **REB near break-even in TRENDING** (+1.2 R): tight-range setups signal  
   a coil before expansion, which works when the tape has a directional lean.
4. **ORB is regime-blind**: WR is ~18-20% across all three regimes; it relies  
   entirely on 4R tails, not regime selection.
5. VTC is mildly better in VOLATILE (PF 0.987 vs 0.940 in BALANCED) — VWAP  
   proximity signals have more resolution when price is moving actively.

---

## PART 9 — OPENING DRIVE (OD) DEEP DIVE

**Detector:** requires `or_complete=True` (OR window closed) + VWAP alignment  
(`close above VWAP for Long, below for Short`) + volume confirmation  
(`volume > avg_volume * STRAT_OD_VOL_MULT`). Fires only pre-`opening_drive_end_et`.

**Summary:** 1,309 trades, 39.95% WR, −29.5 R, avg −0.023 R. This is the  
second-least-negative strategy. OD is almost break-even — the issue is Shorts.

**Instrument split:**
- MGC SCALP: 77 trades, 46.8% WR, **+12.2 R** — only profitable OD combo
- MGC SWING: 66 trades, 43.9% WR, **+5.9 R**
- MNQ SCALP: 213 trades, 43.2% WR, **+14.5 R** — second best
- MNQ SWING: 195 trades, 40.5% WR, near zero (+0.6 R)
- MES SCALP: 189 trades, 40.2% WR, −5.0 R
- MES SWING: 168 trades, 36.9% WR, −17.5 R
- MYM SCALP: 208 trades, 36.5% WR, −24.6 R
- MYM SWING: 193 trades, 37.8% WR, −15.5 R

**Root cause of losses:**
- MYM/SCALP/Short: −21.3 R (WR 34.7%, 95 trades). MYM opens volatile with  
  wide spreads; short fades after OR completion often get stopped by AM noise.
- MES SWING Short: −17.0 R (75 trades, 32% WR). ES SWING Short has no edge.
- All OD Shorts: −28.6 R on 601 trades (38.9% WR, avg −0.048 R)

**Regime insight:** OD in TRENDING regime produces +31.6 R (PF 1.128) vs  
OD in BALANCED regime −15.9 R (PF 0.715). The detector fires regardless of  
regime; restricting to TRENDING would significantly improve it.

**Best OD conditions:** MGC/MNQ instruments, TRENDING regime, ET hour 8-10,  
Monday/Thursday. MYM Shorts and BALANCED regime should be filtered.

---

## PART 10 — LIQUIDITY SWEEP REVERSAL (LSR) DEEP DIVE

**Detector:** `sweep_bull or sweep_bear` + VWAP proximity (price within  
`STRAT_LSR_VWAP_DIST_ATR` × ATR of VWAP). Bull sweep → Long; Bear → Short.

**Summary:** 2,644 trades (largest strategy), 39.8% WR, **−112.4 R**, worst  
absolute net R of any strategy. High signal count, poor edge.

**Instrument split:**
- MGC SCALP: 214 trades, 45.3% WR, **+24.3 R**
- MGC SWING: 178 trades, 50.0% WR, **+42.2 R** ← only combo with 50%+ WR
- MNQ SCALP: 423 trades, 37.6% WR, **−34.9 R**
- MNQ SWING: 350 trades, 35.7% WR, **−43.3 R**
- MES SCALP: 422 trades, 39.1% WR, −32.8 R
- MES SWING: 338 trades, 37.3% WR, −34.9 R
- MYM SCALP: 410 trades, 39.0% WR, −38.0 R
- MYM SWING: 309 trades, 42.4% WR, +5.0 R

**Session breakdown (all instruments):**
- Asia: 748 trades, 36.1% WR, **−106.7 R** — disastrous
- London: 742 trades, 40.0% WR, −32.0 R
- New York: 1,057 trades, 43.1% WR, **+54.6 R**
- Off-hours: 97 trades, 29.9% WR, −28.2 R

**Asia is the problem session for LSR.** Overnight sweeps in thin markets  
trigger the detector but the reversal is a continuation, not a reversal.  
MNQ Asia Short: WR 30.9%, −83.6 R (343 trades) is the single worst cell in  
the entire study. LSR Asia Short on MNQ loses −0.244 R/trade.

**Regime:** VOLATILE makes LSR profitable (+43.1 R, PF 1.103). BALANCED and  
TRENDING are losing. The sweep-and-reverse thesis requires a volatile market  
where sweeps are clear; in calm/trending tape they're just noise.

**Why MGC works:** MGC's VWAP proximity is tighter, stops are wider relative  
to tick size, and MGC reverses more cleanly after sweeps in H1-2026 conditions  
(gold was trend-driven, sweep reversals were decisive). London MGC Short:  
+28.1 R on 56 trades, 60.7% WR — a standout profitable cell.

---

## PART 11 — VWAP TREND CONTINUATION (VTC) DEEP DIVE

**Detector:** requires `trend != 0` (non-zero trend bias) + near-VWAP proximity  
(`close within STRAT_VTC_VWAP_DIST_ATR` × ATR) + confirm candle  
(`confirm_bull` for Long, `confirm_bear` for Short).

**Summary:** 1,776 trades, 39.7% WR, **−50.5 R**, avg −0.028 R.  
Near break-even. The Long side is slightly profitable (+12.9 R, PF 1.025).

**Instrument split:**
- MES SCALP: 252 trades, 45.2% WR, **+23.4 R** ← best VTC combo
- MES SWING: 189 trades, 43.4% WR, **+10.2 R**
- MNQ SCALP: 342 trades, 41.5% WR, +7.7 R
- MNQ SWING: 239 trades, 39.8% WR, −3.3 R
- MGC SCALP: 164 trades, 37.2% WR, −14.9 R
- MGC SWING: 121 trades, 37.2% WR, −10.6 R
- MYM SCALP: 298 trades, 35.6% WR, **−48.5 R** ← worst VTC combo
- MYM SWING: 171 trades, 38.0% WR, −14.4 R

**Session breakdown:**
- Asia: 759 trades, 38.2% WR, **−57.5 R** — losing heavily (Short Asia −63.2 R)
- London: 340 trades, 42.4% WR, +7.8 R
- New York: 634 trades, 41.6% WR, +15.2 R

**ET hour pattern:** VTC best at ET hours 2–3 (Asia early, +21.6 R), 8 (NY  
open, +26.6 R). Worst at hours 4 (−19.0 R) and 14 (−16.1 R). The 18h-block  
(486 trades) loses −29.6 R — heavy Asia session continuation trades fail.

**MYM/SCALP issue:** MYM's wide tick/point structure inflates stops  
(avg 412 min hold in SCALP); the 1.5R target is hard to reach without  
multi-hour runs. MYM VTC Long NY: 20 trades at 10% WR, −15.6 R.

**VTC Short problem:** The trend continuation thesis works for Longs but not  
Shorts — short VTC signals fire into bounce zones where stop placement is  
tight and the market resumes the original trend through the stop.

---

## PART 12 — RANGE EXPANSION BREAKOUT (REB) DEEP DIVE

**Detector:** requires tight range in recent bars (range ≤ 1.5 × ATR) + volume  
confirmation (`volume > avg_volume * STRAT_REB_VOL_MULT`) to confirm breakout.

**Summary:** 926 trades, 38.2% WR, **−67.99 R**, avg −0.074 R.  
Second-worst expectancy per trade. High hold times (BALANCED: avg 2,160 min).

**Instrument split:**
- MNQ SCALP: 166 trades, 44.6% WR, **+15.9 R** ← only profitable REB combo
- MNQ SWING: 123 trades, 39.8% WR, −2.1 R
- MES SCALP: 151 trades, 37.1% WR, −18.2 R
- MES SWING: 133 trades, 41.4% WR, −1.4 R
- MGC SCALP: 64 trades, 28.1% WR, **−20.0 R**
- MGC SWING: 55 trades, 30.9% WR, **−13.1 R**
- MYM SCALP: 144 trades, 36.8% WR, −20.4 R
- MYM SWING: 90 trades, 37.8% WR, −8.7 R

**Session breakdown:**
- Asia: 355 trades, 40.9% WR, **−6.3 R** (Long +29.4, Short −35.7)
- London: 234 trades, 31.6% WR, **−58.9 R** ← heaviest damage
- New York: 326 trades, 41.7% WR, +5.9 R

**London is catastrophic for REB** (PF 0.649, −25.2 avg R/100). London  
breakouts from tight ranges frequently fail — the European open creates  
false breakouts that snap back, stopping Longs and Shorts alike.

**Regime:** REB works in TRENDING (+1.2 R, PF 1.005) and fails hard in  
BALANCED (−53.8 R, PF 0.726). Tight-range coils in a non-trending market  
resolve as further consolidation, not expansion.

**MGC underperforms badly:** MGC REB WR 28-31%, PF 0.57-0.66. Gold does not  
coil-and-break cleanly — it tends toward larger range structures and REB  
signals appear during micro-consolidations that resolve as continuation, not  
breakout.

---

## PART 13 — OPENING RANGE BREAKOUT (ORB) DEEP DIVE

**Detector:** no time ceiling (unlike OD), requires `or_complete=True` + volume  
confirmation + candle confirmation of the breakout. Management override → 4R  
target instead of the 1.5R default.

**Summary:** 1,254 trades, **18.3%** WR, −117.0 R total. The *only* strategy  
where Long direction is profitable (+56.5 R). Short side destroys −173.6 R.

**80.6% of trades are stop-losses; 18.4% hit 4R target; 0.5% open EOD.**

**Instrument/mode split:**
- MGC SWING: **38 trades**, 31.6% WR, **+18.5 R** (PF 1.700) — tiny sample
- MGC SCALP: 149 trades, 20.1% WR, −4.5 R
- MNQ SWING: 206 trades, 19.9% WR, **−5.1 R** (near break-even)
- MNQ SCALP: 203 trades, 16.8% WR, −37.9 R
- MYM SWING: 121 trades, 21.5% WR, +5.3 R
- MYM SCALP: 159 trades, 22.0% WR, +9.7 R
- MES SCALP: 189 trades, 17.5% WR, −33.8 R
- MES SWING: 189 trades, **13.2%** WR, **−69.4 R** ← worst single combo

**Monthly ORB pattern:**
- Month 1 (Jan): 212 trades, 24.1% WR, **+37.9 R** — best month (Long +66.5)
- Month 2 (Feb): 127 trades, 21.3% WR, +4.6 R
- Month 3 (Mar): 196 trades, 17.4% WR, −29.8 R
- Month 4 (Apr): 222 trades, 16.2% WR, **−47.8 R**
- Month 5 (May): 267 trades, 19.5% WR, −14.2 R
- Month 6 (Jun): 230 trades, 15.7% WR, **−67.9 R** ← worst month

**ORB degrades over the study period.** Jan is the only month with a clear  
positive expectancy. The WR trend is declining: 24% → 21% → 17% → 16% → 20%  
→ 16%. ORB Short is losing in every month (Short WR 15.2% overall, −173.6 R).

**ORB Long by month:** Jan +66.5, Feb −13.2, Mar −32.5, Apr +28.0, May +26.9,  
Jun −19.1. The 4R target works when the breakout direction is correct; the  
engine correctly identifies Long breakouts in Jan/Apr/May but misses direction  
in Feb/Mar/Jun. The inability to detect direction (it fires both Long and  
Short with similar frequency) is the fundamental problem.

**ET hour:** ORB fires most at 8h (158 trades, 12.7% WR, −62.4 R) and 9h  
(382 trades, 16.5% WR, −82.1 R) — the first two hours of NY open. The best  
hours are 12h (40% WR, +34.4 R), 13h (+25.5 R), 18-19h (+14-19 R from Asia  
session ORBs). Early AM breakouts in NY fail badly; later session ORBs work.

---

## PART 14 — MONTHLY PERFORMANCE PATTERNS

| Month | Strat | Trades | WR% | Net R | Best Dir |
|---|---|---|---|---|---|
| Jan | LSR | 475 | 40.0 | −19.1 | Short (−31.9) |
| Jan | OD | 267 | 42.3 | +9.0 | Long (+32.7) |
| Jan | ORB | 212 | 24.1 | **+37.9** | Long (+66.5) |
| Jan | REB | 170 | 42.9 | +6.9 | Short (+13.7) |
| Jan | VTC | 372 | 42.5 | +12.7 | — |
| Feb | LSR | 389 | 37.8 | −37.0 | — |
| Feb | OD | 222 | 46.4 | **+30.6** | Short (+24.0) |
| Mar | LSR | 385 | 34.3 | **−64.9** | Long (−86.2) ← |
| Apr | LSR | 431 | 34.8 | **−73.1** | Short (−69.3) ← |
| May | LSR | 519 | 45.9 | **+58.4** | Long (+45.8) ← best LSR month |
| May | OD | 212 | 34.4 | −34.1 | — |
| Jun | ORB | 230 | 15.7 | **−67.9** | — |
| Jun | VTC | 247 | 35.2 | **−40.5** | — |

**Standout monthly findings:**
- Jan 2026 was the only month with net-positive outcome across strategies  
  (ORB +37.9, OD +9.0, REB +6.9, VTC +12.7; LSR −19.1)
- Mar-Apr were catastrophic for LSR and ORB; Apr Short on LSR alone −69.3 R
- May was LSR's only profitable month (+58.4 R, 45.9% WR) — volatile month  
  with clear sweep-and-reverse setups (tariff volatility)
- Jun deterioration is broad: ORB −67.9, VTC −40.5, LSR flat (+23.3)

**Weekday patterns (aggregate):**
- Monday (0): 1,446 trades, 33.8% WR avg — worst day; ORB on Monday:  
  9.5% WR, −132.6 R (241 trades)
- Wednesday (2): mixed; VTC Wed best +5.6 R
- Thursday (3): best day for VTC (+36.3 R, 45% WR) and ORB (+123.7 R, 31%)
- Friday (4): ORB worst Friday −63.4 R, REB best +23.1 R

---

## PART 15 — SESSION PERFORMANCE PATTERNS

| Session | Strat | Trades | WR% | Net R | Long R | Short R |
|---|---|---|---|---|---|---|
| Asia | LSR | 748 | 36.1 | **−106.7** | −15.3 | −91.4 |
| London | LSR | 742 | 40.0 | −32.0 | −13.0 | −19.1 |
| New York | LSR | 1,057 | 43.1 | **+54.6** | +6.9 | +47.7 |
| Off-hours | LSR | 97 | 29.9 | −28.2 | −17.7 | −10.6 |
| New York | OD | 1,309 | 40.0 | −29.5 | −0.9 | −28.6 |
| Asia | ORB | 272 | 21.7 | +7.7 | +29.3 | −21.6 |
| New York | ORB | 963 | 18.0 | **−126.0** | +27.9 | **−154.0** |
| Asia | REB | 355 | 40.9 | −6.3 | +29.4 | −35.7 |
| London | REB | 234 | 31.6 | **−58.9** | −32.0 | −26.9 |
| New York | REB | 326 | 41.7 | +5.9 | −13.9 | +19.8 |
| Asia | VTC | 759 | 38.2 | **−57.5** | +5.7 | −63.2 |
| London | VTC | 340 | 42.4 | +7.8 | +30.8 | −23.0 |
| New York | VTC | 634 | 41.6 | +15.2 | −11.7 | +26.8 |

**Session-level summary:**

1. **LSR: Asia is toxic, NY is profitable.** The strategy's entire profitability  
   depends on NY session (+54.6 R). Asia destroys −106.7 R. LSR should be  
   restricted to NY only for signal generation.

2. **ORB: NY is where it bleeds.** NY Short alone −154 R. Asia ORB Long works  
   (+29.3 R). The breakout algorithm fires indiscriminately during the NY session  
   open when spreads widen and false breaks are frequent.

3. **VTC: Asia Short is the killer** (−63.2 R). VTC New York Short is  
   profitable (+26.8 R). The trend continuation thesis fails in Asia where  
   overnight flow dominates and trends reverse suddenly.

4. **REB: London fails** (−58.9 R, 31.6% WR). NY REB works (+5.9 R, 41.7%).  
   London open creates whipsaws that look like range expansions but reverse.

---

## PART 16 — ET HOUR PATTERNS

**Best and worst ET hours by strategy (≥15 trades):**

| Strategy | Best Hours (net R) | Worst Hours (net R) |
|---|---|---|
| LSR | 12h (+21.9), 14h (+21.2), 19h (+12.9), 22h (+14.6) | 20h (−54.1), 18h (−40.5), 1h (−26.2), 16h (−26.8) |
| OD | 10h (+20.3, 52% WR) | 9h (−37.2), 8h (−12.5) |
| ORB | 12h (+34.4), 13h (+25.5), 18h (+14.4), 22h (+11.9) | 8h (−62.4), 9h (−82.1), 15h (−29.8), 20h (−39.1) |
| REB | 0h (+22.6, 62%), 13h (+19.6), 22h (+13.6), 10h (+12.8) | 1h (−16.1), 5h (−16.2), 18h (−21.8), 21h (−10.3) |
| VTC | 8h (+26.6, 54%), 2h (+21.6), 3h (+10.4), 5h (+10.1) | 4h (−19.0), 14h (−16.1), 20h (−19.4), 22h (−11.2) |

**Key observations:**
- The 8-9h (NY open) is terrible for ORB (−144.5 R combined) but good for VTC  
  (+26.6 R at 8h). The open creates trend continuation setups but destroys  
  breakout trades.
- Hour 20 (8pm ET = Asia open) loses for LSR (−54.1 R) and ORB (−39.1 R) —  
  the overnight Asia open is a high-noise period for all signal types.
- Hours 12-14 (late morning NY) are consistently good for LSR and ORB — post-  
  open volatility has settled and reversals/breakouts are more reliable.

---

## PART 17 — LOSS CLUSTER AND DRAWDOWN ANALYSIS

**Top 10 worst combinations by net R:**

| Rank | Instrument | Mode | Strategy | Net R | Max DD | Loss Streak |
|---|---|---|---|---|---|---|
| 1 | MES | SWING | ORB | −69.4 | 73.3 | 18 |
| 2 | MYM | SCALP | VTC | −48.5 | 53.8 | 12 |
| 3 | MNQ | SWING | LSR | −43.3 | 53.3 | 11 |
| 4 | MYM | SCALP | LSR | −38.0 | 39.9 | 9 |
| 5 | MNQ | SCALP | ORB | −37.9 | 49.2 | 16 |
| 6 | MNQ | SCALP | LSR | −34.9 | 56.5 | 11 |
| 7 | MES | SWING | LSR | −34.9 | 55.8 | 11 |
| 8 | MES | SCALP | ORB | −33.8 | 43.7 | 18 |
| 9 | MES | SCALP | LSR | −32.8 | 55.4 | 10 |
| 10 | MYM | SCALP | OD | −24.6 | 34.5 | 10 |

**MES SWING ORB** is the worst combo: 18-consecutive-loss streaks, 73.3 R  
drawdown, driven entirely by ORB's 13.2% WR on MES. MES is a high-frequency,  
tight-tick instrument where the 4R target is rarely reached without  
multi-day holds; the SWING mode adds even more patience but the setup WR is  
too low to sustain it.

**ORB and LSR dominate the bottom 10**, accounting for 7 of 10 worst combos.  
All worst combos share a max drawdown exceeding the full 1-year net R —  
meaning each of these combos was in a perpetual drawdown throughout H1-2026.

**Worst single-trade R:** MYM/SCALP/LSR at −1.174 R (worst-case tick math).  
**Best single-trade R:** MGC/SWING/ORB at +3.9996 R (4R target hit).

---

## PART 18 — WIN TAIL AND TOP-TRADE ANALYSIS

**All 25 top trades by R are ORB 4R targets.** The maximum realized R is  
~3.9996 (≈4R, net of commission). Top 5 individual ORB wins:

1. MGC SWING Short: +3.9996 R, 218,015 min hold (≈151 days open at EOD)  
2. MGC SCALP Short: +3.9993 R, 5,080 min hold (~3.5 days)  
3. MGC SCALP Short: +3.9989 R, 6,430 min hold (~4.5 days)  
4. MNQ SCALP Long: +3.9981 R, 5,525 min (~3.8 days)  
5. MNQ SWING Long: +3.9981 R, 11,110 min (~7.7 days)

The MGC SWING ORB Short open from Jan 29 staying open until Jun 30 (151 days)  
is technically valid under the backtester's "close at last bar if target not  
reached" logic, but represents a significant real-world concern: a 4R target  
on MGC was held for 5 months. This is a data artifact of the open-period ending  
mid-run, not a real-world execution.

**Win cluster observation:** ORB wins cluster in Jan (month 1), Apr-May for  
Longs, and Feb-Mar for Shorts. The 4R target requires sustained directional  
momentum which is more available during high-volatility trend months.

---

## PART 19 — BEST-EDGE CELLS

These are the combinations and conditions where edge exists in the data:

**Tier 1 — Strong positive edge (>+10 R, reasonable sample):**

| Cell | Trades | WR% | Net R |
|---|---|---|---|
| MGC/SWING/LSR (any) | 178 | 50.0 | +42.2 |
| ORB/Long (all instruments) | 622 | 22.5 | +56.5 |
| LSR/New York/Short | ~200+ | 43+ | +47.7 |
| OD/TRENDING regime | 428 | 43.9 | +31.6 |
| MGC/SCALP/LSR | 214 | 45.3 | +24.3 |
| MNQ/SCALP/OD | 213 | 43.2 | +14.5 |
| LSR/VOLATILE regime | 720 | 43.3 | +43.1 |
| VTC/Long (all) | 859 | 41.6 | +12.9 |
| MES/SCALP/VTC | 252 | 45.2 | +23.4 |
| MGC/London/LSR/Short | 56 | 60.7 | +28.1 |

**Tier 2 — Marginal positive edge (+1 to +10 R):**

| Cell | Trades | WR% | Net R |
|---|---|---|---|
| MNQ/SCALP/REB | 166 | 44.6 | +15.9 |
| REB/TRENDING regime | 427 | 41.7 | +1.2 |
| MYM/SWING/LSR | 309 | 42.4 | +5.0 |
| MGC/SCALP/OD | 77 | 46.8 | +12.2 |
| MNQ/SWING/ORB | 206 | 19.9 | −5.1 (near zero) |

**Conclusion:** The edge is highly localized. The strongest signal is that  
**MGC has consistent edge across strategies** (PF 1.057 overall). The  
**LSR-in-NY-with-VOLATILE** regime combination is worth isolating.

---

## PART 20 — NO-EDGE / AVOID CELLS

**Cells to avoid (>−20 R, consistent negative edge):**

| Cell | Trades | WR% | Net R | Reason |
|---|---|---|---|---|
| MNQ/SCALP/LSR Short | 185 | 30.8 | −46.1 | Asia overnight short sweeps |
| MES/SWING/ORB | 189 | 13.2 | −69.4 | 4R too far for tight ES |
| MES/SCALP/ORB | 189 | 17.5 | −33.8 | Same root cause |
| MYM/SCALP/VTC | 298 | 35.6 | −48.5 | Wide tick, Long fails |
| LSR/Asia/Short (all) | ~400 | ~31 | −91.4 | Overnight sweeps = continuation |
| ORB/NY/Short | ~500 | ~15 | −154.0 | False NY open breaks short |
| VTC/Asia/Short | 759 | 38.2 | −63.2 | Overnight trend reverses |
| REB/London | 234 | 31.6 | −58.9 | London false breakouts |

---

## PART 21 — STRATEGY DETECTOR FINDINGS

Based on source code analysis of `backtest_engine.py` lines 795–899:

**OPENING_DRIVE** (detect_opening_drive):
- Fires only when `or_complete=True` AND `close > vwap` (Long) or `< vwap` (Short)  
  AND `volume > avg × STRAT_OD_VOL_MULT`
- Weakness: The OR completion check doesn't enforce that the OR formed with  
  directional conviction. A flat OR that completes produces the same signal as  
  a strong directional OR.
- Opportunity: Restrict to TRENDING regime and filter MYM/Short.

**LIQUIDITY_SWEEP_REVERSAL** (detect_liquidity_sweep_reversal):
- Fires on `sweep_bull or sweep_bear` + VWAP proximity
- Weakness: Sweep signals fire frequently in thin overnight markets (Asia) where  
  the "sweep" is really a continuation move, not a reversal. The VWAP proximity  
  check doesn't prevent this because VWAP itself drifts slowly overnight.
- Opportunity: Add a session filter (NY only) or require VOLATILE regime.

**VWAP_TREND_CONTINUATION** (detect_vwap_trend_continuation):
- Fires when `trend != 0` + near VWAP + confirm candle
- Weakness: `trend` is the indicator-derived bias from the alert system, which  
  can be stale during low-activity overnight hours. Near-VWAP confirms that  
  fire into the wrong side of a slow overnight VWAP drift generate losing trades.
- Opportunity: Filter to London/NY sessions and require recent trend freshness.

**RANGE_EXPANSION_BREAKOUT** (detect_range_expansion_breakout):
- Fires on tight range (≤1.5×ATR) + volume confirmation
- Weakness: The tight-range condition fires in both pre-expansion coil AND in  
  low-volatility consolidations that extend further. The detector cannot  
  distinguish between them without additional context.
- Opportunity: Restrict to TRENDING regime and NY/Asia sessions (avoid London).

**OPENING_RANGE_BREAKOUT** (detect_opening_range_breakout):
- Fires on `or_complete=True` + volume + candle confirmation, NO time ceiling
- Weakness: Fires in both directions equally. The Long-only edge (+56.5 R)  
  vs Short catastrophe (−173.6 R) shows the instrument is biased Lond in  
  H1-2026. Without directional bias filter, Short signals destroy value.
- Opportunity: Filter to Long direction only (bullish bias in H1-2026).  
  ORB-only-Long would yield ~+56.5 R net on this corpus.

---

## PART 22 — DATASET / CONTRACT CONTEXT

| Dataset | Instrument | Mode | Combos | Trades | Date Range |
|---|---|---|---|---|---|
| 8 | MNQ | SCALP | 5 | 1,347 | 2026-01-02 → 2026-06-30 |
| 8 | MNQ | SWING | 5 | 1,113 | 2026-01-02 → 2026-06-30 |
| 9 | MES | SCALP | 5 | 1,203 | 2026-01-02 → 2026-06-30 |
| 9 | MES | SWING | 5 | 1,017 | 2026-01-02 → 2026-06-30 |
| 10 | MGC | SCALP | 5 | 668 | 2026-01-02 → 2026-06-30 |
| 10 | MGC | SWING | 5 | 458 | 2026-01-02 → 2026-06-30 |
| 11 | MYM | SCALP | 5 | 1,219 | 2026-01-02 → 2026-06-30 |
| 11 | MYM | SWING | 5 | 884 | 2026-01-02 → 2026-06-30 |

All 4 datasets span the full H1-2026 period. MES and MNQ have the highest  
signal density (2,220 and 2,460 trades total). MGC has the fewest (1,126) —  
consistent with gold's longer intraday trend windows generating fewer signals.

MYM (Micro Dow) has abnormally wide nominal stops (avg 129 pts) due to the  
Dow's large nominal price. This inflates hold times and makes 1.5R targets  
take significantly longer to reach than for MES or MGC.

---

## PART 23 — DETERMINISM BUG REPORT AND FIX

**Bug discovered:** `bt_baseline.py` `_freeze_config()` used:
```python
"valid_symbols":    list(bt.VALID_SYMBOLS),
"valid_timeframes": list(bt.VALID_TIMEFRAMES),
```

**Root cause:** If `VALID_SYMBOLS` or `VALID_TIMEFRAMES` were ever refactored  
to use `set` or `frozenset` (common Python idiom for uniqueness), `list()`  
on a set produces a non-deterministic order across Python processes, which  
would silently change the config hash and either reject a valid re-run or  
accept a corrupt comparison.

**Current state:** Both are `tuple` in the engine (lines 100-101), which  
iterates in insertion order. The hash was *accidentally* stable. The fix  
makes the contract *explicitly* stable for any future type change.

**Fix applied:**
```python
"valid_symbols":    sorted(bt.VALID_SYMBOLS),   # was: list(...)
"valid_timeframes": sorted(bt.VALID_TIMEFRAMES), # was: list(...)
```

**Verification:**
```
valid_symbols in cfg: ['MES', 'MGC', 'MNQ', 'MYM']  (alphabetical)
valid_timeframes in cfg: ['15m', '1m', '3m', '5m']  (alphabetical)
detector_registry_snapshot: already sorted via sorted(bt.DETECTORS.keys())
disabled_strategies: already sorted via sorted(bt.DISABLED_STRATEGIES)
news_blackouts_et: already deterministic via [list(w) for w in ...]
```

The stored baseline hash `68fd5b2c96b4f2ee` is unaffected (the baseline  
was generated from sorted tuples; the fix doesn't change their output).

---

## PART 24 — TEST RESULTS

### test_backtest_baseline.py
```
97 passed, 7 subtests passed in 3.27s
```
**9 new determinism tests added (BL061–BL069):**
- BL061: hash is 16 lowercase hex chars ✅
- BL062: hash is stable across two consecutive calls ✅
- BL063: valid_symbols is a sorted list ✅
- BL064: valid_timeframes is a sorted list ✅
- BL065: detector_registry_snapshot is a sorted list ✅
- BL066: disabled_strategies is a sorted list ✅
- BL067: news_blackouts_et is JSON-serialisable list of lists ✅
- BL068: config dict contains all required keys ✅
- BL069: reversed valid_symbols produces a different hash (mutation oracle) ✅

### All backtest-related suites
```
test_backtest_baseline.py + test_backtest_coverage_audit.py + 
test_backtest_import.py + test_backtest_orb_adapter.py + 
test_backtest_parity_repair.py + test_backtest_stop_parity.py
→ 202 passed, 7 subtests passed in 3.49s
```

### Pre-existing failure (unrelated to this task)
```
FAILED test_dynamic_stop.py::test_fixed_1to1_rr_swing_no_longer_vetoes
```
This test was failing before any changes in this task (pre-existing on  
`polish-v1` branch). No investigation or fix in scope for Phase 6B.1B.

---

## PART 25 — SIMULATE_STRATEGY CODE REVIEW

**Key engine behaviors confirmed from source review (lines 969–1153):**

1. **One open position per strategy:** `i = max(exit_bar, entry_bar) + 1`  
   ensures no overlapping trades for the same strategy. This is conservative  
   but means adjacent signals during a live trade are skipped.

2. **Next-bar open entry:** Entry is on `candles[entry_bar]["open"]` (the bar  
   *after* the signal close) ± slippage. This is correct causal implementation.

3. **Worst-case same-bar fill:** If stop and target both trigger on the same  
   bar, stop wins. This is the correct conservative assumption.

4. **Management model split:** ORB uses `target_4r` management override  
   (from `STRATEGY_MGMT_OVERRIDE`), all others use `target_1_5r`. This is  
   the correct live-parity behavior.

5. **Commission model:** `commission_per_side * 2.0` per trade = $1.24/trade.  
   Total commission drag: 164.1 R across 7,909 trades. Slippage (1 tick) adds  
   another 92.4 R. Combined cost: **256.5 R** — significant relative to gross  
   positive R of 4,821 R (~5.3% drag).

6. **Session filter:** `_session_for_et()` correctly classifies Asia/London/NY.  
   The "off-hours" bucket catches CME maintenance gaps.

7. **News blackout:** Single window `(8.467, 8.533)` = ~8:28-8:32 ET (jobless  
   claims/NFP window). Only 1 blackout window is configured — very conservative.

---

## PART 26 — BT_STOP_PLAN CODE REVIEW

**Key stop calculation behaviors (lines 905–963):**

1. **Wider-of-ATR-or-structure:** `calc_stop = min(atr_stop, structure_stop)`  
   for Long (lower of the two prices = wider stop from entry). Correct.

2. **SCALP widens too-tight stops:** If `calc_dist < min_pts`, SCALP widens to  
   `min_pts` rather than rejecting. This means SCALP never rejects a trade on  
   stop tightness — it forces a wider stop, which increases commission/slip ratio  
   but allows entry. This inflates losses when the natural stop is very tight.

3. **SWING rejects too-tight stops:** `return None` if below minimum. More  
   conservative. Explains why SWING has fewer trades than SCALP per combo.

4. **Snap to ticks:** `math.ceil(calc_ticks - 1e-9)` — always rounds UP.  
   Never tighter than calculated. The epsilon prevents floating-point errors  
   from inflating by one tick.

5. **VOLATILE regime multiplier:** Uses `stop_mult_high` instead of `stop_mult`  
   in VOLATILE. This explains the large avg_stop_pts for VOLATILE regime cells  
   (94.9 pts for LSR vs 33-41 pts in other regimes).

---

## PART 27 — RANKED IMPROVEMENT OPPORTUNITIES

These are ranked by expected impact, difficulty, and risk. All are proposals  
for the next engineering phase — none are implemented here.

**Rank 1 — ORB Short filter (HIGHEST IMPACT, LOW RISK)**  
ORB Short loses −173.6 R on 632 trades (15.2% WR). If ORB were Long-only  
this study would show +56.5 R instead of −117.0 R net. The fix is a single  
directional gate on the ORB detector. Estimated improvement: +173 R.  
Risk: minimal (removes trades, doesn't add complexity).

**Rank 2 — LSR Session filter: NY only**  
LSR Asia loses −106.7 R. LSR NY earns +54.6 R. Restricting LSR to NY or  
London sessions would remove the dominant loss cluster. Estimated improvement:  
+100+ R on the corpus. Risk: reduces signal frequency significantly.

**Rank 3 — OD and VTC: TRENDING regime gate**  
OD in TRENDING: +31.6 R. OD in BALANCED: −15.9 R. VTC follows the same  
pattern. Adding a regime filter at the detector level removes the losing  
regime cells while preserving the edge. Estimated improvement: +45 R.

**Rank 4 — REB: London session exclusion**  
REB London: −58.9 R (31.6% WR). Removing London would add ~+59 R.  
The TRENDING filter (Rank 3) partially overlaps this.

**Rank 5 — MYM/SCALP instrument-mode exclusion**  
MYM SCALP VTC (−48.5 R), MYM SCALP LSR (−38.0 R). MYM's large nominal tick  
makes 1.5R targets take too long relative to stop exposure. Excluding MYM/SCALP  
from VTC and LSR adds ~+86 R. Risk: reduces live MYM trading significantly.

**Rank 6 — LSR: VOLATILE regime requirement**  
LSR in VOLATILE: +43.1 R (PF 1.103). In BALANCED: −87.3 R. Requiring VOLATILE  
regime for LSR to fire would dramatically improve it. Note: this would reduce  
signal frequency by 2/3.

---

## PART 28 — SUMMARY OF FINDINGS

**What's working:**
1. MGC has consistent edge (PF 1.057, +40.1 R) — gold's volatility pattern  
   in H1-2026 suits sweep reversal and opening drive strategies.
2. ORB Long is profitable (+56.5 R, 22.5% WR) — the 4R target architecture  
   correctly handles low win-rate breakouts when direction is right.
3. OD in TRENDING regime: PF 1.128, +31.6 R — the strategy thesis works.
4. LSR in VOLATILE regime and NY session: positive edge exists.
5. VTC Long overall: +12.9 R, PF 1.025 — marginal but real edge.

**What's failing:**
1. ORB Short (−173.6 R) — by far the largest single loss source.
2. LSR Asia/Short (−91.4 R) — overnight sweeps are continuations, not reversals.
3. VTC Asia/Short (−63.2 R) — trend continuation fails overnight.
4. REB London (−58.9 R) — London false breakouts destroy the strategy.
5. Short direction across all strategies: −377.5 R combined.

**Root cause of negative overall PF (0.927):**
The strategies are directionally symmetric (fire equally Long and Short) but  
H1-2026 market conditions were predominantly bullish on indices and strongly  
directional in gold. Short signals fire into up-trending markets and fail at  
a much higher rate than Long signals. The engine has no macro-directional bias  
filter — it treats Long and Short equally regardless of market regime.

**The single highest-value engineering action:**  
Add ORB-Long-only restriction. This alone converts ORB from −117 R to +56.5 R  
and improves the overall corpus from −377 R to −203 R on the same data.

---

## PART 29 — SUPPORTING DATA TABLES

### 40-Combo Performance Matrix (complete)

| Instrument | Mode | Strategy | Trades | WR% | Net R | Expectancy | PF | MaxDD | AvgHold | LongR | ShortR | Reliability |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| MES | SCALP | LSR | 422 | 39.10 | −32.84 | −0.0778 | 0.881 | 55.4 | 231.6 | −28.17 | −4.67 | STRONG |
| MES | SWING | LSR | 338 | 37.28 | −34.91 | −0.1033 | 0.842 | 55.8 | 371.4 | −23.40 | −11.51 | STRONG |
| MGC | SCALP | LSR | 214 | 45.33 | **+24.34** | **+0.1137** | 1.203 | 19.5 | 302.9 | +9.67 | +14.67 | STRONG |
| MGC | SWING | LSR | 178 | **50.00** | **+42.24** | **+0.2373** | 1.467 | 11.8 | 433.0 | +6.69 | +35.55 | MODERATE |
| MNQ | SCALP | LSR | 423 | 37.59 | −34.93 | −0.0826 | 0.871 | 56.5 | 195.9 | +11.15 | −46.07 | STRONG |
| MNQ | SWING | LSR | 350 | 35.71 | −43.30 | −0.1237 | 0.811 | 53.3 | 329.1 | −5.77 | −37.52 | STRONG |
| MYM | SCALP | LSR | 410 | 39.02 | −38.04 | −0.0928 | 0.859 | 39.9 | 215.0 | −17.83 | −20.21 | STRONG |
| MYM | SWING | LSR | 309 | 42.39 | +5.05 | +0.0163 | 1.027 | 28.8 | 394.4 | +8.62 | −3.57 | STRONG |
| MES | SCALP | OD | 189 | 40.21 | −5.02 | −0.0266 | 0.957 | 14.6 | 213.3 | +11.25 | −16.27 | MODERATE |
| MES | SWING | OD | 168 | 36.90 | −17.48 | −0.1041 | 0.840 | 20.3 | 280.8 | −0.48 | −17.01 | MODERATE |
| MGC | SCALP | OD | 77 | 46.75 | **+12.18** | **+0.1582** | 1.293 | 7.2 | 276.3 | −4.42 | +16.60 | DEVELOPING |
| MGC | SWING | OD | 66 | 43.94 | **+5.93** | **+0.0898** | 1.159 | 6.7 | 337.1 | −3.79 | +9.72 | DEVELOPING |
| MNQ | SCALP | OD | 213 | 43.19 | **+14.47** | **+0.0679** | 1.118 | 15.6 | 241.7 | +14.06 | +0.40 | STRONG |
| MNQ | SWING | OD | 195 | 40.51 | +0.62 | +0.0032 | 1.005 | 23.3 | 292.2 | +1.39 | −0.76 | MODERATE |
| MYM | SCALP | OD | 208 | 36.54 | −24.61 | −0.1183 | 0.820 | 34.5 | 263.1 | −9.08 | −15.53 | STRONG |
| MYM | SWING | OD | 193 | 37.82 | −15.55 | −0.0805 | 0.874 | 26.1 | 310.5 | −9.79 | −5.75 | MODERATE |
| MES | SCALP | ORB | 189 | 17.46 | −33.77 | −0.1787 | 0.792 | 43.7 | 1059.1 | +8.81 | −42.58 | MODERATE |
| MES | SWING | ORB | 189 | 13.23 | **−69.38** | **−0.3671** | 0.589 | 73.3 | 1006.2 | −26.88 | −42.50 | MODERATE |
| MGC | SCALP | ORB | 149 | 20.13 | −4.48 | −0.0301 | 0.963 | 40.1 | 604.5 | +12.90 | −17.38 | MODERATE |
| MGC | SWING | ORB | 38 | 31.58 | **+18.47** | **+0.4861** | 1.700 | 6.3 | 6522.6 | +28.75 | −10.28 | LOW |
| MNQ | SCALP | ORB | 203 | 16.75 | −37.88 | −0.1866 | 0.780 | 49.2 | 1001.9 | −8.45 | −29.43 | STRONG |
| MNQ | SWING | ORB | 206 | 19.90 | −5.13 | −0.0249 | 0.969 | 21.5 | 964.0 | +5.94 | −11.07 | STRONG |
| MYM | SCALP | ORB | 159 | 22.01 | +9.69 | +0.0609 | 1.075 | 14.5 | 1356.5 | +24.98 | −15.29 | MODERATE |
| MYM | SWING | ORB | 121 | 21.49 | +5.33 | +0.0440 | 1.054 | 22.8 | 1860.7 | +10.45 | −5.12 | MODERATE |
| MES | SCALP | REB | 151 | 37.09 | −18.18 | −0.1204 | 0.819 | 26.6 | 372.0 | +3.06 | −21.24 | MODERATE |
| MES | SWING | REB | 133 | 41.35 | −1.44 | −0.0108 | 0.982 | 10.4 | 618.2 | −0.26 | −1.18 | MODERATE |
| MGC | SCALP | REB | 64 | 28.12 | −19.97 | −0.3120 | 0.573 | 24.4 | 1993.7 | −14.05 | −5.91 | DEVELOPING |
| MGC | SWING | REB | 55 | 30.91 | −13.10 | −0.2381 | 0.660 | 19.8 | 2491.4 | −12.38 | −0.72 | DEVELOPING |
| MNQ | SCALP | REB | 166 | 44.58 | **+15.90** | **+0.0958** | 1.170 | 11.4 | 286.8 | +17.09 | −1.19 | MODERATE |
| MNQ | SWING | REB | 123 | 39.84 | −2.13 | −0.0173 | 0.972 | 16.4 | 754.2 | −2.27 | +0.14 | MODERATE |
| MYM | SCALP | REB | 144 | 36.81 | −20.36 | −0.1414 | 0.791 | 26.3 | 594.3 | −7.33 | −13.04 | MODERATE |
| MYM | SWING | REB | 90 | 37.78 | −8.72 | −0.0969 | 0.851 | 15.3 | 1410.5 | −7.00 | −1.73 | DEVELOPING |
| MES | SCALP | VTC | 252 | 45.24 | **+23.36** | **+0.0927** | 1.163 | 25.5 | 585.1 | +13.73 | +9.64 | STRONG |
| MES | SWING | VTC | 189 | 43.39 | **+10.22** | **+0.0540** | 1.093 | 15.7 | 901.3 | +9.98 | +0.24 | MODERATE |
| MGC | SCALP | VTC | 164 | 37.20 | −14.87 | −0.0907 | 0.858 | 25.4 | 892.8 | −6.33 | −8.54 | MODERATE |
| MGC | SWING | VTC | 121 | 37.19 | −10.65 | −0.0880 | 0.862 | 19.5 | 1284.6 | −5.12 | −5.53 | MODERATE |
| MNQ | SCALP | VTC | 342 | 41.52 | +7.75 | +0.0227 | 1.038 | 18.4 | 313.9 | +20.57 | −12.82 | STRONG |
| MNQ | SWING | VTC | 239 | 39.75 | −3.34 | −0.0140 | 0.977 | 24.4 | 622.9 | +9.77 | −13.12 | STRONG |
| MYM | SCALP | VTC | 298 | 35.57 | **−48.55** | **−0.1629** | 0.760 | 53.8 | 412.4 | −21.85 | −26.70 | STRONG |
| MYM | SWING | VTC | 171 | 38.01 | −14.42 | −0.0843 | 0.868 | 23.2 | 1025.7 | −7.83 | −6.59 | MODERATE |

---

## PART 30 — ENGINEERING RECOMMENDATIONS

Based on the complete diagnostic, the following actions are recommended for  
the next phase. All are flagged as **RESEARCH ONLY** until validated on  
out-of-sample data.

### Priority 1: ORB directional bias filter
- **Action:** Add a Long-bias flag to ORB detector that disables Short signals  
  when the HTF regime is bullish. Or simply restrict ORB to Long-only.
- **Expected gain:** +173 R on H1-2026 corpus.
- **Risk surface:** Zero live-trading change if gated (BT-only initially).

### Priority 2: LSR session restriction
- **Action:** Gate LSR to New York session only (or NY + London).  
  Remove Asia/Off-hours LSR signal generation.
- **Expected gain:** +107 R reduction in LSR losses.
- **Risk:** Removes ~28% of LSR signals from the live feed.

### Priority 3: Regime-aware strategy activation
- **Action:** Add a regime check at detector entry: OD requires TRENDING,  
  REB requires TRENDING, LSR requires VOLATILE.
- **Expected gain:** ~+90 R combined across OD/REB/LSR.
- **Risk:** Requires reliable regime classification from the live feed  
  (currently computed from ATR ratio — already available).

### Priority 4: Short-side veto during bullish macro regime
- **Action:** Add a macro-regime layer: when the 4H HTF bias is bullish  
  (already available in swing_ctx), veto Short signals across all strategies.
- **Expected gain:** Potentially eliminates most of the −377 R Short loss.
- **Risk:** Requires accurate HTF bias — directional lock can cause extended  
  drawdowns if the regime call is wrong.

### Priority 5: Instrument-specific strategy exclusions
- **Action:** Exclude MYM/SCALP from VTC and LSR; exclude MES/ORB from  
  all modes; restrict MGC/REB.
- **Expected gain:** +86 R (MYM SCALP) + 73 R (MES ORB) = ~+159 R.
- **Risk:** Reduces signal frequency significantly on MYM.

---

**End of Phase 6B.1B Diagnostic Report**  
*No strategies modified. No live trading changed. No baseline regenerated.*  
*All 97 BL tests passing. 202 backtest suite tests passing.*
