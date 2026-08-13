# AI Trading Partner — Operator Manual

> **Audience:** The single operator running this system live.  
> **Scope:** Day-to-day operation. This is not a setup or deployment guide.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Instruments & Sessions](#2-instruments--sessions)
3. [Daily Startup Checklist](#3-daily-startup-checklist)
4. [Dashboard Navigation](#4-dashboard-navigation)
5. [Reading the Brain](#5-reading-the-brain)
6. [Edge Score & Setup Quality](#6-edge-score--setup-quality)
7. [Trading Mode: SCALP](#7-trading-mode-scalp)
8. [Trading Mode: SWING](#8-trading-mode-swing)
9. [Trading Mode: INTRADAY TREND](#9-trading-mode-intraday-trend)
10. [Taking a Trade — Manual](#10-taking-a-trade--manual)
11. [Taking a Trade — Auto-Fire](#11-taking-a-trade--auto-fire)
12. [Managing an Open Position](#12-managing-an-open-position)
13. [Alerts & Discord](#13-alerts--discord)
14. [Research & Learning Panels](#14-research--learning-panels)
15. [Session Close Checklist](#15-session-close-checklist)
16. [Modes & Switches Reference](#16-modes--switches-reference)
17. [Troubleshooting](#17-troubleshooting)

---

## 1. System Overview

This is an always-on, AI-assisted futures trading system. It does not trade on its own by default — you, the operator, retain final control. Here is the full signal path:

```
TradingView Pine Scripts
        │
        ▼  (webhooks: structure, zones, sweeps, CVD, volume, FVGs)
Flask Webhook Server  (:8000)
        │  evaluates every alert through ~15 gates
        ▼
 Verdict: WAIT / EARLY / READY
        │
        ├──► Discord alert (operator notified)
        │
        └──► Execution Gateway
                 │
                 ├── Manual path: you click ENTER / LONG / SHORT
                 └── Auto path:  fires only when armed + setup is READY
```

**Three things that must all be true before any live order goes to the broker:**

| Condition | Where to set it |
|-----------|----------------|
| Execution **enabled** | Execution tab → ▶ ENABLE EXECUTION |
| System **armed** (auto only) | Execution tab → ARM AUTO-FIRE |
| Setup verdict is **READY** | Computed automatically |

### The Three Trading Modes

The system has three distinct operating modes. Each has its own signal thresholds, risk parameters, instruments, and trade management philosophy. Only one mode is active at a time.

| Mode | Character | Instruments | R:R | Min Edge |
|------|-----------|-------------|-----|----------|
| **SCALP** | High-frequency intraday, 1:1 quick captures | All 4 (MGC, MNQ, MES, MYM) | 1:1 | 60 (EARLY at 50) |
| **SWING** | Stricter, multi-hour or overnight holds | All 4 | 1:3 | 80 |
| **INTRADAY TREND** | Session-based large moves, MNQ only, ghost/shadow | MNQ only | ≥2R | Native pipeline (no edge floor) |

Switch the active mode from the **Mode** row in the dashboard (SCALP · Sensitive / SWING · Strict / INTRADAY · Session).

---

## 2. Instruments & Sessions

| Symbol | Full Name | Exchange | Session |
|--------|-----------|----------|---------|
| **MGC** | Micro Gold | COMEX | Nearly 24h; pauses 17:00–18:00 ET daily |
| **MNQ** | Micro Nasdaq-100 | CME | Nearly 24h; pauses 17:00–18:00 ET daily |
| **MES** | Micro S&P 500 | CME | Nearly 24h; pauses 17:00–18:00 ET daily |
| **MYM** | Micro Dow | CME | Nearly 24h; pauses 17:00–18:00 ET daily |

**Best intraday windows (US):**
- **Primary:** 09:30–11:00 ET (opening session bonus active, highest volume)
- **Secondary:** 13:30–15:30 ET (afternoon momentum window)
- **Avoid:** 12:00–13:30 ET (lunch chop, low conviction), 15:30–16:00 ET (erratic close)
- **Overnight:** System watches but setups are lower quality without volume; Asia Long entries require an even higher edge score (≥85) to fire

The system is aware of market hours and will suppress signals during the CME/COMEX daily halt (17:00–18:00 ET) and on US market holidays.

---

## 3. Daily Startup Checklist

Do this before the open. Takes ~2 minutes.

### Step 1 — Verify the server is up
Open the dashboard. The top status bar should show:
- 🟢 **LIVE DATA** — Databento feed is connected
- A timestamp that is recent (within the last few seconds)

If it shows OFFLINE or the timestamp is stale, the Flask server may be restarting. Wait 60 seconds and refresh.

### Step 2 — Select your trading mode
Use the **Mode** selector row in the dashboard to choose SCALP, SWING, or INTRADAY TREND. The mode persists across restarts — it is saved to the database. Verify it shows the mode you intend for today's session.

> ⚠️ INTRADAY TREND is **MNQ-only** and currently operates in **ghost/shadow mode** (it records and scores setups but does not send live broker orders). If you select INTRADAY TREND, MGC/MES/MYM will show WAIT — this is expected.

### Step 3 — Enable Execution
Navigate to the **Execution** tab.

Click **▶ ENABLE EXECUTION**. You will be prompted to type a confirmation phrase. Once confirmed, the button turns green and shows ENABLED.

> ⚠️ Execution resets to **disabled** on every server restart. You must re-enable it each session.

### Step 4 — Verify TradingView webhooks are flowing
Look at the **Market Strip** at the top of Main Brain. It should show:
- Price updating for each instrument
- Session = ACTIVE (during trading hours)

If price is frozen, check that your TradingView alerts are still active and the webhook URL points to your published app.

### Step 5 — Check the thesis
On the **Main Brain** page, each instrument's **Left Brain** panel should show a direction (BULLISH / BEARISH / NEUTRAL) with a confidence level. If it shows "No data yet" or the age is >2 hours old during an active session, the structure alerts may have stopped firing from TradingView.

### Step 6 — (Optional) Arm Auto-Fire
Only if you want the system to send trades without your click. See [Section 11](#11-taking-a-trade--auto-fire).

---

## 4. Dashboard Navigation

The dashboard is a single-page app at your production URL. Use the top navigation:

| Tab | What's Here |
|-----|-------------|
| **Main Brain** | Primary trading view — thesis, edge score, candidate setup, one-click entry |
| **Analysis** | Deep dive: FVG scanner, volatility, MTF trend, market intelligence breakdown |
| **Scanner** | Cleanest Trade Available — ranks all instruments/modes by score right now |
| **Trade Desk** | Active position management, close controls, arm/disarm |
| **Execution** | Enable/disable execution, arm auto-fire, execution mode selector |
| **Journal** | Trade history, review workflow, coaching drill-down |
| **Coach** | Learning engine weights, sample counts, strategy performance |
| **Alerts** | Alert history, mute controls per instrument |

### Switching Instruments
Main Brain shows one instrument at a time. Use the instrument selector tabs (MGC / MNQ / MES / MYM) near the top to switch. Each instrument has its own independent thesis, edge score, and candidate setup.

### Mode Selector
The **Mode** row (SCALP · Sensitive / SWING · Strict / INTRADAY · Session) is visible at the top of the dashboard. Click any option to switch the active mode globally. The switch is immediate and persisted.

---

## 5. Reading the Brain

The Main Brain page is your primary trading interface. Here is how to read it top to bottom.

### 5.1 Market Strip
The colored bar at the top shows at a glance:

```
[SCALP] [MNQ $20,140.50] [SESSION: ACTIVE] [LIVE DATA ●] [P&L: +$42] [DAILY CAP: OK]
```

- **Mode badge** (SCALP / SWING / INTRADAY): the current scoring profile
- **Price**: last price from Databento (real-time)
- **SESSION**: ACTIVE / CLOSED — the system gates trades when markets are closed
- **LIVE DATA**: green = Databento connected, red = feed offline
- **P&L / Daily Cap**: today's realized P&L vs your max daily loss limit

### 5.2 Left Brain (Thesis)
The thesis is the structural bias — which way the market is trending based on bar-close analysis.

| Display | Meaning |
|---------|---------|
| **BULLISH** / **BEARISH** | Active directional bias with supporting evidence |
| **NEUTRAL** | No clear structure yet, or genuinely conflicting signals |
| "No data yet" | Left Brain has received data but no bar has closed, or feed is new |
| Age indicator | How old the thesis is — treat anything >30 min cautiously in fast markets |

The thesis updates only when bars **close**, not tick-by-tick. MNQ/MES/MYM update frequently; MGC updates more slowly overnight (~1 bar per overnight session on COMEX — this is normal).

### 5.3 Brain State Pill
A colored pill near the thesis shows the system's combined judgment:

| Color / Label | Meaning |
|---------------|---------|
| 🟢 **READY** | All gates pass — a setup is available right now |
| 🟡 **EARLY** | Setup is forming but not yet confirmed (SCALP only; score 50–74) |
| 🔵 **WAIT** | Conditions not met — see the blocker list below it |
| ⚫ **MANAGING** | You have an open trade being tracked |
| 🔴 **CLOSED** | Market is closed for this instrument |

### 5.4 Edge Score
A number from 0 to 110 (displayed as a gauge). This is the system's confidence in the current setup.

| Score | Grade | Meaning |
|-------|-------|---------|
| 85–110 | **A+** | Highest quality — all confirmations present |
| 70–84 | **A** | Strong setup |
| 50–69 | **B** | Acceptable — system may still trigger at 60+ in SCALP mode |
| 0–49 | **WAIT** | Below threshold — do not take this trade |

Below the gauge you will see the **component breakdown**: which of the 7 scored signals are contributing (BOS/CHoCH, VWAP, Sweep, Volume, CVD, Session). A zero score on Zone means no supply/demand zone is active — this effectively caps the total score.

### 5.5 Blockers
If the verdict is WAIT, a list of blockers explains why:
- **No structure** — no BOS or CHoCH has fired recently
- **VWAP not confirmed** — price is on the wrong side of VWAP
- **Zone mitigated** — the supply/demand zone has been violated
- **Volatility too high/low** — ATR outside acceptable range
- **CVD conflict** — cumulative delta disagrees with the direction (hard veto)
- **Daily cap reached** — max daily loss or trade count hit; no more trades today
- **INTRADAY TREND specific**: time restriction, setup family unrecognised, confirmation incomplete, structural stop invalid

### 5.6 Trade Candidate
If a setup is active (EARLY or READY), the candidate panel shows:
- **Entry zone**: price range to enter
- **Stop loss**: where the trade is wrong
- **TP1 / TP2**: first and second targets
- **R:R**: risk-to-reward ratio
- **Strategy**: which pattern triggered this (e.g., Liquidity Sweep Reversal, Opening Range Breakout, Trend Pullback)

---

## 6. Edge Score & Setup Quality

### Scoring Components (max 110)

| Component | Max Points | What Triggers It |
|-----------|-----------|-----------------|
| BOS / CHoCH (20-min) | 20 | Break of structure or change of character |
| CHoCH confirmation | 20 | Secondary structure confirmation |
| VWAP | 15 | Price on correct side of VWAP |
| Liquidity Sweep | 15 | Stop-hunt sweep before reversal |
| Volume | 15 | Relative volume ≥ ~1.5× average |
| CVD | 15 | Cumulative delta confirms direction |
| Session Bonus | 10 | Trade is in the primary session window (09:30–11:00 ET) |

**Critical rules:**
- **CVD is a hard filter.** If CVD disagrees with your direction, the trade is blocked regardless of how high the score is. This cannot be overridden from the dashboard.
- **Zone score is 0 when no supply/demand zone is active.** Because CHoCH credit partly depends on zone presence, a missing zone effectively caps the total score at ~50, keeping the trade in WAIT.
- The score can exceed 100 in exceptional conditions (CVD agreement bonus can push to 110 max).

---

## 7. Trading Mode: SCALP

SCALP is the high-frequency, sensitive profile designed for quick intraday captures at a 1:1 risk-to-reward ratio. It has the lowest signal threshold of the three modes, making it most likely to trigger but also requiring more active position management.

### 7.1 When to Use SCALP

- You are trading during active US hours (09:30–15:30 ET) when volume and volatility are present
- You want the most trading opportunities per session
- You are comfortable with a 1:1 R:R and exiting at first target
- Markets are trending clearly on the intraday timeframe
- You are watching the dashboard actively and can manage trades in real-time

**Do NOT use SCALP** when:
- You want to hold overnight — the short TRADE_READY_INTERVAL (2 min) and 1:1 R:R are not suited for multi-hour holds
- Markets are in a choppy, low-volatility lunch grind (12:00–13:30 ET)

### 7.2 SCALP Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| R:R target | **1:1** (TP1 = 1R) | First target closes the trade; runner optional |
| Minimum edge to fire READY | **60** | Gate opens at 60; below that is WAIT |
| EARLY tier threshold | **50–59** | Setup visible but not yet confirmed |
| EARLY size | **Half size** (flag-gated) | If SCALP half-size flag is on, EARLY fires at 50% contracts |
| Max risk per trade | **$200** (default) | Env `MAX_RISK_DOLLARS_PER_TRADE` overrides |
| TRADE_READY_INTERVAL | **120s** (2 min) | How often the system re-evaluates and can re-post a READY card |
| Max losses per day | **3** (default) | Safety cap — env `SAFETY_MAX_LOSSES_PER_DAY` overrides |
| Zone requirement | Soft (demotes only) | A mitigated zone demotes to WAIT but does not hard-block |
| HTF bias check | Optional | Not required by default; DUAL_TF_ENGINE flag adds a 2-confirm requirement |
| Dynamic exits | Yes (flag-gated ON) | TP1, TP2, runner, delayed breakeven — see §7.4 |

### 7.3 SCALP Gate Sequence

For a SCALP trade to reach READY, the following must all pass:

1. **Structure confirmed** — at least one BOS or CHoCH has fired for this instrument and direction (any of BOS, CHOCH, HH, HL, LH, LL)
2. **Zone active** — a supply (Short) or demand (Long) zone is present and not fully mitigated
3. **VWAP side confirmed** — price is on the directionally correct side of VWAP (Long: above; Short: below)
4. **CVD agrees** — cumulative delta is not in hard conflict with the direction
5. **Edge score ≥ 60** — the sum of all signal components meets the minimum
6. **Volatility in range** — ATR-ratio is within the acceptable band (extreme ratio >3.0 demotes to WAIT)
7. **Market session is open** — not in the 17:00–18:00 ET halt, not a holiday, not a weekend
8. **Daily cap not reached** — today's realized loss count is below the max-losses-per-day cap

If any gate fails, the verdict is WAIT and the blockers panel explains which one.

### 7.4 SCALP Dynamic Exits

SCALP mode uses a tiered exit structure that replaces the simple 1:1 exit:

| Exit Level | Trigger | What Happens |
|------------|---------|--------------|
| **TP1** | Price reaches 1R | Primary target — broker TP1 fills here; local tracking updates |
| **TP2** | Price reaches 2R | Secondary target |
| **Runner** | Edge score ≥ 75 at entry | A third contract (runner) is sized in; trails for larger capture |
| **Breakeven stop** | After TP1 fills | Stop is moved to entry level (delayed BE) — you can no longer lose on this trade |

The runner is only offered when Edge ≥ 75 at entry time (the "SCALP_RUNNER_MIN_EDGE"). If Edge is 60–74, only TP1 and TP2 are active.

### 7.5 SCALP — EARLY Tier

When the setup scores 50–59 and passes structure+zone gates but not the full 60 threshold, the verdict shows **EARLY** (⚡). This is a forming setup that the system is watching but not yet executing.

- EARLY setups are **announced** via the dashboard and Discord (teaser alert, not a full trade card)
- EARLY setups do **not** trigger auto-fire at full size; if the SCALP half-size flag is on, they can auto-fire at half size
- EARLY never triggers for SWING or INTRADAY TREND — only SCALP has this tier

### 7.6 SCALP — Asia Hours Rule

During the Asia session (roughly 18:00–00:00 ET / overnight), Long entries require a **higher edge score of ≥ 85** (vs. the normal 60). This asymmetry exists because historical data shows Asia-session Long setups have a significantly lower win rate. Short entries during Asia are not subject to this elevated bar.

### 7.7 SCALP — Dual TF Engine (optional)

When the `DUAL_TF_ENGINE` flag is enabled (OFF by default), SCALP adds a second confirmation gate:
- Before READY, the system must see **≥ 2 distinct confirms** (CVD, sweep, or volume — not VWAP or Delta) within the last 10 seconds
- This dramatically reduces false triggers in choppy conditions
- To enable: set env `DUAL_TF_ENGINE=1` and republish

---

## 8. Trading Mode: SWING

SWING is the strict, high-conviction profile designed for multi-hour or overnight position holds. It requires a full higher-timeframe context before entering and targets a 1:3 risk-to-reward. You will take fewer trades but with stronger setups.

### 8.1 When to Use SWING

- You are willing to hold a position for hours or overnight
- You want the highest-quality setups only (fewer, better)
- The market has a clear multi-hour directional bias visible on 1H/4H charts
- You do not want to watch the screen constantly — SWING's slower re-check cadence (5 min) gives you more time between checks
- You are targeting 3× your risk on each trade

**Do NOT use SWING** when:
- Markets are choppy or range-bound on the hourly chart — the HTF bias requirement will keep you out, which is correct
- You need intraday entries and exits within the same session

### 8.2 SWING Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| R:R target | **1:3** | TP1 at 1R, TP2 at 3R |
| Minimum edge to fire READY | **80** | Much stricter than SCALP |
| EARLY tier | Not available | SWING only has WAIT or READY |
| Max risk per trade | **$500** (default) | Env `MAX_RISK_DOLLARS_PER_TRADE` overrides |
| ATR stop multiplier | **2.5× ATR** (normal) / **3.0× ATR** (high volatility) | Stop is wider than SCALP |
| TRADE_READY_INTERVAL | **300s** (5 min) | Re-check cadence is slower; fits holds |
| Max losses per day | **3** (default, shared with SCALP) | |
| Zone requirement | **Hard gate** — zone + VWAP + structure all required | All three must pass; none are "demote only" |
| HTF bias check | Required | 1H, 4H, and Daily bias must align (SWING_HTF_ENABLED) |
| Dynamic exits | No | Simple TP1/TP2 structure |

### 8.3 SWING Gate Sequence

SWING has a stricter gate sequence than SCALP. All of the following must pass:

1. **Structure confirmed** — BOS or CHoCH must have fired, with no opposite structure in conflict
2. **Zone active and unmitigated** — supply/demand zone must be present; mitigation is a hard block (not a demotion like in SCALP)
3. **VWAP side confirmed** — price must be on the directionally correct side of VWAP
4. **CVD agrees** — hard veto still applies
5. **HTF trend aligned** — the 1H, 4H, and Daily higher-timeframe bias (computed from Databento bars) must agree with the direction
6. **Edge score ≥ 80** — stricter floor
7. **Volatility in range** — ATR-ratio within acceptable band; extreme volatility hard-blocks (not just demotes)
8. **Market session open** — same halt/holiday awareness as SCALP
9. **Daily cap not reached** — same safety cap

### 8.4 SWING HTF Bias

The higher-timeframe engine (SWING_HTF) auto-computes directional bias from Databento bar data:

| Timeframe | Source | Stale after |
|-----------|--------|------------|
| 1H | 1-hour bars from Databento | 2 hours |
| 4H | Resampled from 1H bars | 6 hours |
| Daily | Daily bars from Databento | 36 hours |

If any HTF timeframe is stale (no data received within its stale window), the HTF gate uses a 20-minute grace period before blocking. After the grace window expires, SWING will show WAIT with "HTF stale" as a blocker.

You can manually push HTF bias updates via TradingView Pine webhook (see the `INTRADAY_TREND` webhook push feature in the pending feature list), or let the system auto-compute it from Databento bars.

### 8.5 SWING — Stop Loss Sizing

SWING uses an ATR-based stop rather than a structural stop:
- **Normal conditions**: stop = 2.5 × ATR (measured from the entry candle)
- **High volatility conditions**: stop = 3.0 × ATR (when the volatility gate detects elevated ATR-ratio)

At a $500 max risk and a typical MNQ ATR of ~30 pts, this translates to roughly 3–5 contracts per trade depending on market conditions.

> ⚠️ If you raise `MAX_RISK_DOLLARS_PER_TRADE` above $500, ensure your broker's daily loss limits still accommodate the new sizing. The safety controls use the per-trade cap but the daily loss cap is in realized-dollar terms.

### 8.6 SWING — Multi-Hour Hold Management

Once you are in a SWING trade, the system's advisory loop provides guidance every 5 minutes:
- **Thesis VALID**: the directional structure is intact — hold per plan
- **Thesis CONFLICTED**: an opposite structure has appeared but is not confirmed — consider tightening your stop
- **Thesis INVALID**: confirmed opposite BOS/CHoCH — the trade has reversed; the system will suggest closing

The system does NOT move your stop or TP at the broker automatically unless the **Live Runner** flag is enabled (default off). You must manage the position manually or use Auto-Exit (see §12.4).

---

## 9. Trading Mode: INTRADAY TREND

INTRADAY TREND is a specialized mode designed to capture large intraday directional moves — the kind of 50–150+ point MNQ runs that develop within a single session. It combines session-aware timing, structural key levels, and a setup-family confirmation model.

> **Important**: INTRADAY TREND is currently in **ghost/shadow mode**. It scores, logs, and analyzes setups in real-time and writes them to the ghost observations database, but it does **not** send live broker orders. Use it to observe and learn the system before live execution is enabled.

> **MNQ-only**: INTRADAY TREND only fires for MNQ. If this mode is active, MGC, MES, and MYM will all show WAIT — this is by design, not a malfunction.

### 9.1 When to Use INTRADAY TREND

- You are trading MNQ specifically
- You want to catch the large, high-conviction directional moves (opening drive, session continuation)
- You are comfortable with session-aware timing discipline (no entries after 14:30 ET; flat by 15:55 ET)
- You accept a hard 2-trade daily cap

**Do NOT use INTRADAY TREND** when:
- You want to trade MGC, MES, or MYM
- You need more than 2 setups per day
- You want live broker orders (currently shadow-only)
- You are trading after 14:30 ET

### 9.2 INTRADAY TREND Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| R:R target | **≥ 2.0** (minimum) | Target selected from the nearest real session level ≥ 2R away; no manufactured targets |
| Edge score gate | **None** | IT does not use an edge score floor. Verdict comes entirely from the native IT pipeline (family → confirmation → stop → chase → target → cap). Edge score is still computed and displayed for reference. |
| Instruments | **MNQ only** | Hard gate — all other symbols blocked |
| Max risk per trade | **$500** | Env `MAX_RISK_DOLLARS_PER_TRADE` overrides |
| Last new entry time | **15:15 ET** | No new entries at or after this time; env `INTRADAY_NEW_ENTRY_CUTOFF_ET` overrides |
| Force flat time | **15:55 ET** | All open ghost positions force-closed |
| Daily trade cap | **2 trades** | Env `MAX_INTRADAY_TREND_TRADES_PER_DAY` overrides |
| Stop type | **Structural stop** | Sourced directly from `it_ctx` key levels — never ATR-based |
| Stop validity | **Hard gate** | Invalid or unavailable stop = hard block; ATR sanity bounds 0.3×–4× applied |
| Chase gate | **1.5× ATR** | Entry blocked when price has moved > 1.5× ATR from the entry zone anchor |
| Session cap check | **Fail-closed** | If DB is down and count can't be verified, entries are blocked |

### 9.3 INTRADAY TREND Sessions

The engine classifies every moment into a trading session. Each session has different characteristics that affect entry quality:

| Session | ET Hours | Character |
|---------|----------|-----------|
| **ASIA** | ~18:00–00:00 (overnight) | Thin volume; low-conviction moves |
| **LONDON** | 00:00–07:00 | European flow; moderate volatility |
| **US_PREMARKET** | 07:00–09:30 | Consolidation before the open; VWAP anchoring |
| **NY_OPEN** | 09:30–11:00 | Highest-quality window; opening drive setups |
| **NY_AM** | 11:00–13:00 | Continuation or reversal of opening move |
| **NY_PM** | 13:00–15:55 | Afternoon trend or fade; entries close at 14:30 |

The session label is shown in the **INTRADAY TREND Diagnostics** panel on the Analysis tab (visible when this mode is active).

### 9.4 Session Key Levels

The engine auto-computes key structural levels from Databento bar history every session:

| Level | Definition | Use |
|-------|-----------|-----|
| Overnight High / Low | Range formed during ASIA session | Breakout reference for NY_OPEN setups |
| Asia High / Low | Same as overnight for this engine | Structural stop anchor for ASIA-session setups |
| London High / Low | Range formed during LONDON session | Key for US_PREMARKET and early NY_OPEN |
| Opening Range High / Low | First 30 minutes of NY session (09:30–10:00) | Primary reference level for TREND_PULLBACK and BREAKOUT_RETEST |

These levels are displayed in the INTRADAY TREND diagnostics panel. When price is interacting with one of these levels, the entry location quality will be **KEY_LEVEL** or **ZONE_CONFLUENCE** — the strongest entries. A price that is in the middle of a range (not near any key level) will be classified as **MID_RANGE** and blocked.

### 9.5 Setup Families

INTRADAY TREND requires the system to identify a recognised **setup family** before an entry is possible. There are exactly three families:

#### LIQUIDITY_SWEEP_REVERSAL
- **Pattern**: Price sweeps a key session level (overnight high/low, OR high/low), takes out stops, then reverses strongly
- **What you see**: Price spikes through the level, stalls, then a BOS/CHoCH fires in the opposite direction
- **Best context**: Post-sweep of overnight high → BOS lower → Short setup; Post-sweep of OR low → BOS higher → Long setup
- **Confirmation required**: Sweep detected + directional BOS/CHoCH confirmed + VWAP not violated by close
- **Character**: High-conviction entries; historically the sharpest reversals happen here because trapped participants are forced to cover

#### BREAKOUT_RETEST
- **Pattern**: Price breaks out of a key range (OR, overnight range, London range), pulls back to retest the broken level, and holds
- **What you see**: Strong expansion bar through the key level, followed by a controlled pullback that does not breach the level, then a continuation bar
- **Best context**: NY_OPEN or NY_AM session; breakout must be on above-average volume
- **Confirmation required**: Initial breakout + retest without close below the key level + BOS in the breakout direction + volume confirmation
- **Character**: Trend-following entries that ride the primary session move; require more patience as the retest can take 10–20 minutes

#### TREND_PULLBACK
- **Pattern**: An established intraday trend is in place; price pulls back to a structural level (VWAP, prior session high/low, or OR level) without breaking the trend
- **What you see**: A clear sequence of HH/HL (uptrend) or LH/LL (downtrend) on the intraday chart; price dips to VWAP or OR level and bounces with volume
- **Best context**: Mid-session (NY_AM); primary trend direction should agree with the 1H bias
- **Confirmation required**: Established trend (≥2 HH/HL or LH/LL confirmed) + pullback to key level + bounce bar + CVD confirms
- **Character**: Higher-probability in a trending day; works poorly on news reversal days or days with big morning reversals

If none of these three families are detected, the status shows **WAITING_FOR_SETUP** and no entry is possible regardless of edge score.

### 9.6 INTRADAY TREND Gate Sequence

> **Architecture note (August 2026):** INTRADAY TREND now runs its own native gate pipeline as the sole READY/WAIT authority. It no longer inherits or passes through the SWING strict gate (edge ≥ 85, zone valid, VWAP confirmed, structure confirmed). The SWING strict result is still computed in the background as shadow data for analytics, but it has zero execution authority for IT. SCALP and SWING are completely unchanged.

The full gate sequence is strictly IT-native and completely separate from SWING:

1. **MNQ-only** — hard block immediately if instrument is not MNQ
2. **Time restriction** — no entries at or after 15:15 ET; no entries at or after 15:55 ET (force-flat period)
3. **Location quality** — price must be at or near a key session level (KEY_LEVEL or ZONE_CONFLUENCE); MID_RANGE entries are blocked
4. **Setup family recognised** — must be one of the three families above; UNKNOWN or no family = WAITING_FOR_SETUP
5. **Confirmation sequence complete** — all required signals for the detected family must be confirmed (not just started)
6. **Structural stop valid** — the computed stop must be finite, positive, on the correct side of price, and within ATR sanity bounds (0.3×–4× ATR); a bad or unavailable stop is a hard block
7. **Chase gate** — entry blocked when the current price has moved more than 1.5× ATR away from the intended entry zone anchor (demand/supply zone or VWAP); prevents chasing after the move has already happened
8. **Target available** — at least one real session level must be ≥ 2R away in the trade direction; no qualifying level = blocked with `IT_INSUFFICIENT_RR`
9. **Daily trade cap not reached** — if the DB cannot be reached to verify today's count, the gate fails closed (this is deliberate — uncertainty about the cap is treated as cap-reached)

### 9.7 INTRADAY TREND — Structural Stop

Unlike SCALP/SWING which use ATR-based stops, INTRADAY TREND computes a **structural stop** exclusively from session key levels in `it_ctx`. ATR is never used to manufacture a stop:
- **Long entry**: stop below the most recent key support level (overnight low, OR low, or pullback swing low)
- **Short entry**: stop above the most recent key resistance level (overnight high, OR high, or pullback swing high)

This structural stop is validated before any entry is accepted:
- Must be a finite number (not None or zero)
- Must be on the correct side of current price (below price for Long; above for Short)
- Must fall within ATR sanity bounds: minimum 0.3× ATR, maximum 4× ATR — stops outside this window suggest a data error

If these conditions are not met, the blocker shows **IT_STRUCTURE_FAIL** and no entry fires. The system will never fall back to an ATR-estimated stop or assume a default risk distance — no valid structural stop = no trade.

### 9.7a Setup Expiration

Each setup family has a built-in expiration window after which the setup is considered stale:

| Family | Expiration |
|--------|-----------|
| LIQUIDITY_SWEEP_REVERSAL | **30 minutes** |
| BREAKOUT_RETEST | **45 minutes** |
| TREND_PULLBACK | **60 minutes** |

Once a setup expires, the `expires_at` timestamp passes and the engine looks for a fresh setup. This prevents the system from holding a "stale" setup context and entering on a move that has already played out.

### 9.8 EOD Force-Flat (15:55 ET)

All open INTRADAY TREND ghost positions are automatically force-closed at 15:55 ET. This is a hard rule that prevents holding overnight in a mode designed purely for intraday moves.

- The watchdog fires before 15:55 ET and logs all closures
- If the DB is temporarily down when the watchdog fires, the failure is logged as **CRITICAL** and the closure is retried on the next heartbeat cycle (every ~30s)
- Pending force-close failures are visible in the INTRADAY TREND diagnostics panel as `force_close_pending: true`
- Once the DB reconnects, the pending closure completes automatically

### 9.9 INTRADAY TREND Diagnostics Panel

On the **Analysis** tab (with INTRADAY TREND mode active), a dedicated panel shows:

| Field | What It Means |
|-------|--------------|
| Session | Current session label (NY_OPEN, NY_AM, etc.) |
| Location Quality | KEY_LEVEL / ZONE_CONFLUENCE / MID_RANGE |
| Setup Family | Which family is active (or UNKNOWN/WAITING_FOR_SETUP) |
| Confirmation Complete | Whether all family-specific confirmation steps have fired |
| Structural Stop Pts | Distance to the computed structural stop in MNQ points |
| Structural Stop Valid | Whether the stop passed all validity checks |
| Recommended Contracts | Contract count from the structural stop + $500 risk |
| Daily Trade Count | Trades taken today vs. the cap |
| Time OK | Whether new entries are permitted (before 14:30 ET) |
| Force Close Pending | Whether an EOD watchdog failure is outstanding |

### 9.10 Ghost/Shadow Observations

While in ghost mode, every INTRADAY TREND setup that would have been a trade entry is recorded in the `ghost_observations` database table. Each observation tracks:
- Entry price, stop, target at signal time
- Max favorable / adverse excursion (how far the trade went in your favor / against you)
- Whether the trade would have touched 1R, 2R, or 3R at its best point
- Whether a "premature exit" would have occurred (trade hit 1.5R+ but exited below 0.5× of that)
- Session label and setup family

This data feeds the **Research Health** panel and will eventually inform whether INTRADAY TREND is ready to graduate to live execution.

---

## 10. Taking a Trade — Manual

This is the recommended approach for most sessions (SCALP and SWING modes).

> For INTRADAY TREND in the current build, manual entry is recorded as a ghost observation but does not send a live broker order.

### Method A: Main Brain ENTER Button

1. Wait for the verdict to show **READY** (green pill)
2. Verify the candidate panel shows the setup you want (entry, stop, TP, direction)
3. The instrument tab at the top should match what you intend to trade
4. Click **ENTER** (or the **LONG** / **SHORT** quick buttons if shown)
5. A confirmation dialog shows the full order details: symbol, direction, quantity, stop, TP
6. Click **CONFIRM** to send to your broker

The order goes immediately to TradersPost → Tradovate. You will see a toast notification confirming sent or showing an error.

> **No arm session required for manual entry.** You only need execution **enabled**.

### Method B: Trade Desk Quick Buttons

1. Navigate to the **Trade Desk** tab
2. Use the **LONG** / **SHORT** buttons at the top of the desk
3. These bypass the READY gate — you can take a discretionary trade even when the system says WAIT
4. The broker bracket (stop + TP) is still auto-computed from current ATR

> Method B uses live ATR-derived stops. Check the candidate panel on Main Brain first to understand the risk before using quick buttons.

### What Happens After You Click

```
Your click
    │
    ▼
Execution gateway (safety checks: daily cap, session hours, position already open?)
    │
    ▼
Broker payload built (entry, stop, TP1, TP2, size)
    │
    ▼
Pre-send audit (required fields checked; invalid payload rejected before it leaves)
    │
    ▼
TradersPost → Tradovate (live order)
    │
    ▼
Trade logged locally + Discord alert sent
```

---

## 11. Taking a Trade — Auto-Fire

When armed, the system sends live orders automatically when the verdict reaches READY. You do not need to click.

> Auto-fire in INTRADAY TREND mode records ghost observations but does not place live orders in the current build.

### Arming the System

1. Go to the **Execution** tab
2. Ensure execution is already **enabled**
3. Click **ARM AUTO-FIRE**
4. Type the confirmation phrase exactly as shown
5. Set your session parameters:
   - **Max trades this session** (recommended: 2–3 for SCALP; 1–2 for SWING)
   - **Max session loss** (hard stop in dollars)
6. Click **CONFIRM ARM**

The arm state is shown in the header. It resets to **disarmed** every time the server restarts.

### What Auto-Fire Does

- Watches for READY verdicts from the webhook
- Fires **once per zone** — if the same zone re-triggers after a stop, it re-arms automatically
- Respects the session trade count and session loss cap
- Respects the daily loss cap (shared with manual trades)
- **SCALP**: EARLY setups (score 50–59) can fire at **half size** if the half-size flag is on
- **SWING**: only fires on full READY (score ≥ 80); no EARLY tier

### Auto-Fire and Mode-Specific Behavior

| Mode | Auto-Fire behavior |
|------|-------------------|
| SCALP | Fires on READY (≥ 60) and optionally EARLY (50–59) at half size |
| SWING | Fires on READY (≥ 80) only; requires `SWING_AUTO_EXEC_ENABLED=1` flag |
| INTRADAY TREND | Currently ghost-mode only; auto-fire records observations, no live orders |

### Disarming

Click **DISARM** in the Execution tab or Trade Desk at any time. Any in-flight order that has already been sent will not be recalled — disarm prevents future fires only.

---

## 12. Managing an Open Position

Once a trade is live, it appears in the **Trade Desk** tab under **Active Positions**.

### 12.1 What the System Tracks

| Field | Description |
|-------|-------------|
| Entry price | Where the trade was filled (from broker confirmation) |
| Stop level | Hard stop — if price hits this, thesis is invalidated |
| TP1 / TP2 | First and second targets |
| Min R / Max R | Worst and best R-multiple seen so far |
| Thesis status | VALID / CONFLICTED / INVALID — updates live |

### 12.2 Closing a Position

**From Trade Desk:**
- Click **■ CLOSE POSITION** next to the trade — this sends a flat/close order to TradersPost
- Click **Clear tracking** to remove a trade from local tracking without sending a close order (use this if you closed manually at the broker)

**From Main Brain:**
- If the MANAGING state is active, a close button appears on the candidate panel

### 12.3 Manual Trade Management Advisory

The system provides guidance while you hold:
- **Thesis VALID** → hold per plan; thesis direction intact
- **Thesis CONFLICTED** → an opposite structure has appeared but is not confirmed; consider reducing size or tightening stop manually at the broker
- **Thesis INVALID** → confirmed opposite BOS/CHoCH; the structure has reversed against you; the system will suggest closing

The system does **not** move your stop or TP at the broker automatically unless the Live Runner flag is enabled (default off).

### 12.4 Auto-Exit

If **Auto-Exit** is armed, the system will send a close order automatically when the thesis flips to **CONFIRMED INVALID** (opposite BOS/CHoCH confirmed). This fires only on thesis reversal, not on stop-hit.

- Off by default; resets on restart
- Only closes the system's own position (checks position was opened in this session by the bot)
- Fire-once per trade — will not double-close

### 12.5 SCALP — Dynamic Exit Management

When SCALP dynamic exits are active (flag on by default in SCALP mode):
- After **TP1 fills** at 1R, the system updates local tracking and the stop is moved to breakeven (delayed BE)
- After **TP2 fills** at 2R, only the runner (if active) remains open
- The **runner** trails until the trade is closed manually or the thesis invalidates
- You can stop managing a trade (click "Stop managing") to disconnect local tracking without closing at the broker

---

## 13. Alerts & Discord

### Dashboard Bell
A bell notification fires (and plays a sound) when a new READY setup becomes active. The bell is per-device — muting on your phone does not mute on your desktop.

### Discord Alerts
Three channels receive alerts:

| Channel | Content |
|---------|---------|
| **Main** | READY setup cards with full trade plan |
| **MNQ** | MNQ-specific signals |
| **MGC** | MGC-specific signals |

Alert cards include: direction, edge score, entry/stop/TP, strategy, and key confirmations present/missing.

### Alert Types by Mode

| Mode | What gets a Discord card |
|------|--------------------------|
| SCALP | EARLY teaser (⚡), full READY card, stop-hit, trade-close |
| SWING | READY card (no EARLY), stop-hit, trade-close, 15-min hold loop |
| INTRADAY TREND | Ghost observation opened/closed (separate from main trade alerts) |

### Muting an Instrument
If you want to stop Discord alerts for a specific instrument without disabling the whole system:
1. Go to the **Alerts** tab
2. Toggle mute for the instrument

Mute is **server-side** (affects all devices) but **resets on server restart**.

### Phone Notifications (Discord @mentions)

The system already sends a phone buzz for every fresh READY setup — no extra code needed. The mechanism is a Discord `@everyone` ping that is attached only to the first post of a new setup. The 5-minute re-posts are silent (no ping), so a standing READY setup rings your phone **once**, not on every interval.

**To receive these as phone notifications:**
1. Install Discord on your phone and join the server where your signal channel lives.
2. Long-press the signal channel → **Notifications** → select **Only @mentions**.

With "Only @mentions" set, only the `@everyone` ping on a fresh READY setup will buzz your phone. All other traffic in the channel (WATCH alerts, re-posts, journal embeds) arrives silently.

**To test without waiting for a real setup:**
- Dashboard → any instrument → click the **Test alert** button, or
- Send `POST /api/notify-test` — this fires a real `@everyone` Discord push and returns `{ sent, reason }`.

> **Note:** The test alert sends even when the instrument is muted (it's an explicit test), but muted status is reported in the response.

### Opening Bell
At 09:30 ET, the system plays an audible opening bell and shows a notification. This is for awareness only — it does not automatically arm or change anything.

---

## 14. Research & Learning Panels

These panels are **display only** — they inform you but do not affect live trading unless explicitly noted.

### Coach Tab
Shows the learning engine's current beliefs about each strategy:

| Column | Meaning |
|--------|---------|
| Strategy | The pattern name |
| Win Rate | Historical win rate from past trades |
| Weight | How much the system favors this strategy (0.65×–1.35×) |
| Sample Count | Trades used to compute the weight |

Weight only affects scoring when the **Learning Score Influence** flag is on. At < 25 samples, the strategy uses neutral weight (1.0×). The weight can adjust the edge score by up to ±15 points.

### Research Health Panel (Analysis Tab)
Shows the live research simulator status:
- Which research strategies are being simulated on the live feed
- How many sim trades each has accumulated
- Whether the recording loop is active

If the recording loop appears stopped during market hours, this is the "silent failure" to watch for — the system stops accumulating new sim data without any obvious dashboard indicator until you look here.

### Left Brain Observations
The Left Brain accumulates bar-by-bar observations. The **Observations** section (Analysis tab) shows the raw feed. If the count stops growing during market hours, the Databento feed has gone quiet.

### INTRADAY TREND Ghost Observations
The **ghost_observations** table records every IT setup that would have been a trade. After a few weeks of data accumulation, you can review:
- How many setups per family (Liquidity Sweep, Breakout Retest, Trend Pullback) are occurring
- What percentage would have reached 1R, 2R, 3R
- Which sessions produce the best results
- Whether management premature exits are a pattern

This data will inform the decision to graduate INTRADAY TREND from shadow mode to live execution.

---

## 15. Session Close Checklist

At the end of your trading day:

1. **Close or flatten any open positions** at the broker
2. In Trade Desk, click **Clear tracking** for any tracked trades you've closed manually
3. **Disarm** auto-fire if armed (Execution tab)
4. **Disable execution** (optional — it resets on next restart anyway)
5. Review the **Journal** tab — any trades from today will appear; mark them as reviewed

You do not need to stop the server. It runs 24/7 and will continue watching overnight. Discord alerts for overnight setups will fire if your TradingView alerts fire — mute the instrument Discord channels if you don't want overnight pings.

> For INTRADAY TREND: the EOD watchdog automatically flattens any open ghost positions at 15:55 ET. You do not need to do this manually.

---

## 16. Modes & Switches Reference

### Trading Mode Comparison

| | SCALP | SWING | INTRADAY TREND |
|---|-------|-------|----------------|
| **R:R** | 1:1 (runner up to 3R) | 1:3 | 1:3 |
| **Min edge** | 60 | 80 | 80 |
| **EARLY tier** | 50–59 (half size) | None | None |
| **Max risk/trade** | $200 | $500 | $500 |
| **Re-check cadence** | 2 min | 5 min | N/A (ghost) |
| **Instruments** | All 4 | All 4 | MNQ only |
| **Stop type** | ATR-based | ATR-based (2.5–3.0×) | Structural (session levels) |
| **Zone gate** | Soft (demote) | Hard (block) | Hard (block) |
| **HTF required** | No (optional flag) | Yes | No (native pipeline) |
| **Daily cap** | 3 losses | 3 losses | 2 entries |
| **Time limit** | None | None | Last entry 14:30 ET; flat 15:55 ET |
| **Live orders** | ✅ Yes | ✅ Yes | ❌ Ghost/shadow only |

### Execution Mode

| Mode | Effect |
|------|--------|
| **manual_only** | The ENTER/LONG/SHORT buttons show the plan but do not send anything. Use for practice. |
| **paper** | Sends a simulated order locally; nothing goes to the broker. |
| **traderspost** | Live mode — orders go to TradersPost → Tradovate. |

Switch via: server environment variable `EXECUTION_MODE` (requires republish to change permanently; in-session toggle available in Execution tab for paper↔live).

### Prop Firm Protection
When enabled, an additional pre-send check enforces prop-firm daily loss and drawdown limits. The system will return a 409 and block the order if you would breach the prop limit. Toggle in Execution tab. Default: OFF.

### Key Flags (require republish to change)

| Flag | Default | Effect |
|------|---------|--------|
| `DUAL_TF_ENGINE` | OFF | SCALP only: requires 2 distinct confirms (CVD/sweep/volume) within 10s before READY |
| `SWING_AUTO_EXEC_ENABLED` | OFF | Allows auto-fire in SWING mode |
| `LEARNING_SCORE_INFLUENCE_ENABLED` | OFF | Learning weights adjust edge score (±15 pts) |
| `MICRO_SCALP_MODE_ENABLED` | OFF | Sweep→trap→trigger micro entries (SCALP sub-mode) |
| `BOT_TRAINING_MODE_ENABLED` | OFF | Stage 1-3: simulates only; Stage 4: goes live |
| `PROP_PROTECTION_ENABLED` | OFF | Pre-send prop firm limit guard |
| `SWING_HTF_ENABLED` | ON (in SWING/IT) | Higher-timeframe bias required; env `=0` kills everywhere |
| `MAX_INTRADAY_TREND_TRADES_PER_DAY` | 2 | IT daily entry cap (env override) |
| `IT_LAST_NEW_ENTRY_TIME` | 14:30 | IT last entry time ET (env override, HH:MM format) |
| `IT_FORCE_FLAT_TIME` | 15:55 | IT EOD force-flat time ET (env override) |

---

## 17. Troubleshooting

### "System disarmed before transmission" on LONG/SHORT click
- You need to enable execution first. Go to **Execution** tab → **▶ ENABLE EXECUTION**.
- The arm session is only required for **auto-fire**, not manual clicks.

### Edge score is zero / verdict always WAIT
- Check if a Supply/Demand zone is active. Zone score is 0 when no zone is in range — this caps the total score and blocks CHOCH credit.
- Go to **Analysis** tab → FVG Scanner to see active zones.
- Check **gate_debug** in the diagnostics (owner-only `/api/diagnostics`) for the specific failing gate.

### No alerts / no signals firing
- The most common cause is no structure alert from TradingView. Structure = any one of CHOCH, BOS, HH, HL, LH, LL. If TradingView alert conditions have expired or the webhook failed, no setups will form.
- Check the **Alerts** tab — if the last alert is hours old during market hours, TradingView webhooks have stopped.

### VWAP says "stale" or "not confirmed"
- VWAP is auto-fetched from Yahoo Finance for GC=F (MGC) and NQ=F (MNQ). If the fetch fails, the system uses a grace window and then blocks VWAP-dependent gates.
- You can push a manual VWAP via the VWAP input in the Analysis tab. The manual value wins for a grace window, then auto-fetch resumes.

### Left Brain thesis is very old / stale
- MGC typically gets ~1 bar per overnight session. This is normal — COMEX gold is sparse overnight.
- MNQ/MES/MYM should update every few minutes during US hours. If stale, check Databento feed.

### Auto-fire didn't fire on a READY signal
- Check: was execution enabled at the time?
- Check: was the system armed? (ARM resets on restart)
- Check: was the session trade count already at max?
- Check: was the daily loss cap already hit?
- Check: if `BOT_TRAINING_MODE_ENABLED=1`, Stage < 4 suppresses all live sends.
- Check (SWING only): is `SWING_AUTO_EXEC_ENABLED=1` set? Without this flag, SWING auto-fire is blocked.

### INTRADAY TREND shows WAIT on all instruments except MNQ
- This is correct behavior. INTRADAY TREND is MNQ-only. MGC, MES, MYM will all show WAIT with a blocker message "INTRADAY TREND is MNQ-only" — the system is working correctly.

### INTRADAY TREND shows WAITING_FOR_SETUP
- No recognised setup family has been detected yet. The system is watching for LIQUIDITY_SWEEP_REVERSAL, BREAKOUT_RETEST, or TREND_PULLBACK patterns to develop. This is normal early in the session or during a choppy/sideways day.
- Check the INTRADAY TREND diagnostics panel for `setup_family` field and `confirmation_missing` list.

### INTRADAY TREND shows BLOCKED_INVALID_STOP
- The structural stop computed from session key levels was invalid (None, zero, or on the wrong side of price). This usually means the key level data is incomplete — either the opening range hasn't formed yet (before 10:00 ET) or there were insufficient bars during the session.
- Wait for more bars to accumulate, or check that the Databento feed is streaming MNQ data normally.

### INTRADAY TREND shows BLOCKED_DAILY_COUNT_UNAVAILABLE
- The system could not reach the database to check today's trade count. Because the cap cannot be verified, entries are blocked as a safety measure. This resolves automatically when the DB connection is restored.
- Check the server logs for database connectivity errors.

### INTRADAY TREND entries blocked after 14:30 ET
- This is intentional. After 14:30 ET, no new INTRADAY TREND entries are permitted (insufficient time for a large intraday move). The `force_close_pending` state at 15:55 ET will close any open ghost positions.

### Chart/price went silent
- A second browser tab can sometimes interrupt the Databento stream. Close all but one tab.
- If Databento disconnects, the server logs an error and attempts reconnect automatically. The status bar will show OFFLINE.

### Deployment build failed
- If the build phase completes but the deploy shows "waiting for deployment to be ready" and times out, this is a transient VM cold-start issue on the platform — not a code problem. Retry the publish.
- If the build phase itself fails, check the build logs in the Publishing pane for the specific error.

### Execution shows enabled but orders aren't going through
- Verify `EXECUTION_MODE=traderspost` is set (not `paper` or `manual_only`).
- Verify the TradersPost webhook URL and password are set in your production secrets.
- A TradersPost HTTP 400 with "invalid payload" means the URL/password is valid but the payload has an issue. Check `TRADERSPOST_TICKER_MNQ` / `TRADERSPOST_TICKER_MGC` env vars match your TradersPost ticker names exactly.

### SWING mode keeps blocking with "HTF stale"
- The 1H/4H/Daily bias data is stale. Check that Databento is streaming MNQ/MGC bars (the HTF engine uses real bar data, not webhooks).
- If the feed has been down for >2 hours, HTF stale will block SWING entries even if all other gates pass.
- During reconnect, the 20-minute grace window allows SWING to continue briefly while HTF data refreshes.

---

*Last updated: August 2026. Changes this revision: INTRADAY TREND now uses its own native gate pipeline — the SWING strict gate (edge ≥ 85 / zone / VWAP / structure) no longer applies to IT; IT gate sequence and parameters table updated accordingly. Phone notification setup (Discord @mentions) added to §13. SCALP max risk raised from $100 → $200.*
