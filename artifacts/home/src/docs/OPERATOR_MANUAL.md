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
7. [Taking a Trade — Manual](#7-taking-a-trade--manual)
8. [Taking a Trade — Auto-Fire](#8-taking-a-trade--auto-fire)
9. [Managing an Open Position](#9-managing-an-open-position)
10. [Alerts & Discord](#10-alerts--discord)
11. [Research & Learning Panels](#11-research--learning-panels)
12. [Session Close Checklist](#12-session-close-checklist)
13. [Modes & Switches Reference](#13-modes--switches-reference)
14. [Troubleshooting](#14-troubleshooting)

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

---

## 2. Instruments & Sessions

| Symbol | Full Name | Exchange | Session |
|--------|-----------|----------|---------|
| **MGC** | Micro Gold | COMEX | Nearly 24h; pauses 17:00–18:00 ET daily |
| **MNQ** | Micro Nasdaq-100 | CME | Nearly 24h; pauses 17:00–18:00 ET daily |
| **MES** | Micro S&P 500 | CME | Nearly 24h; pauses 17:00–18:00 ET daily |
| **MYM** | Micro Dow | CME | Nearly 24h; pauses 17:00–18:00 ET daily |

**Best intraday windows (US):**
- **Primary:** 09:30–11:00 ET (opening session bonus active)
- **Secondary:** 13:30–15:30 ET (afternoon momentum)
- **Avoid:** 12:00–13:30 ET (lunch chop), 15:30–16:00 ET (erratic close)
- **Overnight:** System watches but setups are lower quality without volume

The system is aware of market hours and will suppress signals during the CME/COMEX daily halt and on US market holidays.

---

## 3. Daily Startup Checklist

Do this before the open. Takes ~2 minutes.

### Step 1 — Verify the server is up
Open the dashboard. The top status bar should show:
- 🟢 **LIVE DATA** — Databento feed is connected
- A timestamp that is recent (within the last few seconds)

If it shows OFFLINE or the timestamp is stale, the Flask server may be restarting. Wait 60 seconds and refresh.

### Step 2 — Enable Execution
Navigate to the **Execution** tab.

Click **▶ ENABLE EXECUTION**. You will be prompted to type a confirmation phrase. Once confirmed, the button turns green and shows ENABLED.

> ⚠️ Execution resets to **disabled** on every server restart. You must re-enable it each session.

### Step 3 — Verify TradingView webhooks are flowing
Look at the **Market Strip** at the top of Main Brain. It should show:
- Price updating for each instrument
- Session = ACTIVE (during trading hours)

If price is frozen, check that your TradingView alerts are still active and the webhook URL points to your published app.

### Step 4 — Check the thesis
On the **Main Brain** page, each instrument's **Left Brain** panel should show a direction (BULLISH / BEARISH / NEUTRAL) with a confidence level. If it shows "No data yet" or the age is >2 hours old during active session, the structure alerts may have stopped firing from TradingView.

### Step 5 — (Optional) Arm Auto-Fire
Only if you want the system to send trades without your click. See [Section 8](#8-taking-a-trade--auto-fire).

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

---

## 5. Reading the Brain

The Main Brain page is your primary trading interface. Here is how to read it top to bottom.

### 5.1 Market Strip
The colored bar at the top shows at a glance:

```
[SCALP] [MGC $2,847.30] [SESSION: ACTIVE] [LIVE DATA ●] [P&L: +$42] [DAILY CAP: OK]
```

- **Mode badge** (SCALP / SWING): the current scoring profile
- **Price**: last price from Databento (real-time)
- **SESSION**: ACTIVE / CLOSED — the system gates trades when markets are closed
- **LIVE DATA**: green = Databento connected, red = feed offline
- **P&L / Daily Cap**: today's realized P&L vs your max daily loss limit

### 5.2 Left Brain (Thesis)
The thesis is the structural bias — which way the market is trending based on Bar-close analysis.

| Display | Meaning |
|---------|---------|
| **BULLISH** / **BEARISH** | Active directional bias with supporting evidence |
| **NEUTRAL** | No clear structure yet, or conflicting signals |
| "No data yet" | Left Brain has received data but no bar has closed, or feed is new |
| Age indicator | How old the thesis is — treat anything >30 min cautiously in fast markets |

The thesis updates only when bars **close**, not tick-by-tick. MNQ/MES/MYM update frequently; MGC updates more slowly overnight.

### 5.3 Brain State Pill
A colored pill near the thesis shows the system's combined judgment:

| Color / Label | Meaning |
|---------------|---------|
| 🟢 **READY** | All gates pass — a setup is available right now |
| 🟡 **EARLY** | Setup is forming but not yet confirmed (score 50–74) |
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

Below the gauge you will see the **component breakdown**: which of the 7 scored signals are contributing (BOS/CHoCH, VWAP, Sweep, Volume, CVD, Session). A zero score on Zone means no supply/demand zone is active.

### 5.5 Blockers
If the verdict is WAIT, a list of blockers explains why:
- **No structure** — no BOS or CHoCH has fired recently
- **VWAP not confirmed** — price is on the wrong side of VWAP
- **Zone mitigated** — the supply/demand zone has been violated
- **Volatility too high/low** — ATR outside acceptable range
- **CVD conflict** — cumulative delta disagrees with the direction
- **Daily cap reached** — max daily loss hit; no more trades today

### 5.6 Trade Candidate
If a setup is active (EARLY or READY), the candidate panel shows:
- **Entry zone**: price range to enter
- **Stop loss**: where the trade is wrong
- **TP1 / TP2**: first and second targets
- **R:R**: risk-to-reward ratio (SCALP targets 1:1; SWING targets 1:3)
- **Strategy**: which pattern triggered this (e.g., Liquidity Sweep Reversal, Opening Range Breakout)

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
| Session Bonus | 10 | Trade is in the primary session window |

**Important:** CVD is a **hard filter**, not just points. If CVD disagrees with your direction, the trade is blocked even if everything else is perfect.

### SCALP vs SWING Modes

The system scores and gates differently depending on the active mode:

| | SCALP | SWING |
|---|-------|-------|
| R:R target | 1:1 (TP1 = 1R) | 1:3 |
| Zone requirement | Yes, but mitigated zone only demotes | Zone + VWAP + structure all required |
| Minimum edge to fire | 60 (EARLY eligible at 50) | 80 |
| HTF bias check | Optional | Required |

To change modes, use the **Mode** selector in the Execution tab.

---

## 7. Taking a Trade — Manual

This is the recommended approach for most sessions.

### Method A: Main Brain ENTER Button

1. Wait for the verdict to show **READY** (green pill)
2. Verify the candidate panel shows the setup you want
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
Execution gateway (safety checks: daily cap, hours, position already open?)
    │
    ▼
Broker payload built (entry, stop, TP1, TP2, size)
    │
    ▼
TradersPost → Tradovate (live order)
    │
    ▼
Trade logged locally + Discord alert sent
```

---

## 8. Taking a Trade — Auto-Fire

When armed, the system sends live orders automatically when the verdict reaches READY. You do not need to click.

### Arming the System

1. Go to the **Execution** tab
2. Ensure execution is already **enabled**
3. Click **ARM AUTO-FIRE**
4. Type the confirmation phrase exactly as shown
5. Set your session parameters:
   - **Max trades this session** (recommended: 2–3)
   - **Max session loss** (hard stop in dollars)
6. Click **CONFIRM ARM**

The arm state is shown in the header. It resets to **disarmed** every time the server restarts.

### What Auto-Fire Does

- Watches for READY verdicts from the webhook
- Fires **once per zone** — if the same zone re-triggers after a stop, it re-arms automatically
- Respects the session trade count and session loss cap
- Respects the daily loss cap (shared with manual trades)
- EARLY setups (score 50–59) fire at **half size** if the SCALP half-size flag is on

### Disarming

Click **DISARM** in the Execution tab or Trade Desk at any time. Any in-flight order that has already been sent will not be recalled — disarm prevents future fires only.

---

## 9. Managing an Open Position

Once a trade is live, it appears in the **Trade Desk** tab under **Active Positions**.

### What the System Tracks

| Field | Description |
|-------|-------------|
| Entry price | Where the trade was filled (from broker confirmation) |
| Stop level | Hard stop — if price hits this, thesis is invalidated |
| TP1 / TP2 | First and second targets |
| Min R / Max R | Worst and best R-multiple seen so far |
| Thesis status | VALID / CONFLICTED / INVALID — updates live |

### Closing a Position

**From Trade Desk:**
- Click **■ CLOSE POSITION** next to the trade — this sends a flat/close order to TradersPost
- Click **Clear tracking** to remove a trade from local tracking without sending a close order (use this if you closed manually at the broker)

**From Main Brain:**
- If the MANAGING state is active, a close button appears on the candidate panel

### Manual Trade Management

The system provides advisory guidance while you hold:
- **Thesis VALID** → hold per plan
- **Thesis CONFLICTED** → consider reducing size or tightening stop
- **Thesis INVALID** → thesis has reversed; the system will suggest closing

The system does **not** move your stop or TP at the broker automatically unless the Live Runner flag is enabled (default off).

### Auto-Exit

If **Auto-Exit** is armed, the system will send a close order automatically when the thesis flips to CONFIRMED INVALID (opposite BOS/CHoCH confirmed). This is off by default and resets on restart.

---

## 10. Alerts & Discord

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

### Muting an Instrument
If you want to stop Discord alerts for a specific instrument without disabling the whole system:
1. Go to the **Alerts** tab
2. Toggle mute for the instrument

Mute is **server-side** (affects all devices) but **resets on server restart**.

### Opening Bell
At 09:30 ET, the system plays an audible opening bell and shows a notification. This is for awareness only — it does not automatically arm or change anything.

---

## 11. Research & Learning Panels

These panels are **display only** — they inform you but do not affect live trading unless explicitly noted.

### Coach Tab
Shows the learning engine's current beliefs about each strategy:

| Column | Meaning |
|--------|---------|
| Strategy | The pattern name |
| Win Rate | Historical win rate from past trades |
| Weight | How much the system favors this strategy (0.65×–1.35×) |
| Sample Count | Trades used to compute the weight |

Weight only affects scoring when the **Learning Score Influence** flag is on. At < 25 samples, the strategy uses neutral weight (1.0×).

### Research Health Panel (Analysis Tab)
Shows the live research simulator status:
- Which research strategies are being simulated on the live feed
- How many sim trades each has accumulated
- Whether the recording loop is active

### Left Brain Observations
The Left Brain accumulates bar-by-bar observations. The **Observations** section (Analysis tab) shows the raw feed. If the count stops growing during market hours, the Databento feed has gone quiet.

---

## 12. Session Close Checklist

At the end of your trading day:

1. **Close or flatten any open positions** at the broker
2. In Trade Desk, click **Clear tracking** for any tracked trades you've closed manually
3. **Disarm** auto-fire if armed (Execution tab)
4. **Disable execution** (optional — it resets on next restart anyway)
5. Review the **Journal** tab — any trades from today will appear; mark them as reviewed

You do not need to stop the server. It runs 24/7 and will continue watching overnight. Discord alerts for overnight setups will fire if your alerts fire — mute the instrument Discord channels if you don't want overnight pings.

---

## 13. Modes & Switches Reference

### Trading Mode
| Mode | When to Use |
|------|-------------|
| **SCALP** | Intraday, quick 1:1 trades, high-frequency signal environment |
| **SWING** | Multi-hour or overnight holds, stricter gates, 1:3 R:R |

Switch via: Execution tab → Mode selector.

### Execution Mode
| Mode | Effect |
|------|--------|
| **manual_only** | The ENTER/LONG/SHORT buttons show the plan but do not send anything. Use for practice. |
| **paper** | Sends a simulated order locally; nothing goes to the broker. |
| **traderspost** | Live mode — orders go to TradersPost → Tradovate. |

Switch via: server environment variable `EXECUTION_MODE` (requires republish to change permanently; in-session toggle available in Execution tab for paper↔live).

### Prop Firm Protection
When enabled, an additional pre-send check enforces prop-firm daily loss and drawdown limits. The system will return a 409 and block the order if you would breach the prop limit. Toggle in Execution tab.

### Key Flags (require republish to change)
| Flag | Default | Effect |
|------|---------|--------|
| `DUAL_TF_ENGINE` | OFF | Requires two timeframe confirmations before READY |
| `SWING_AUTO_EXEC_ENABLED` | OFF | Allows auto-fire in SWING mode |
| `LEARNING_SCORE_INFLUENCE_ENABLED` | OFF | Learning weights adjust edge score (±15 pts) |
| `MICRO_SCALP_MODE_ENABLED` | OFF | Sweep→trap→trigger micro entries |
| `BOT_TRAINING_MODE_ENABLED` | OFF | Stage 1-3: simulates only; Stage 4: goes live |
| `PROP_PROTECTION_ENABLED` | OFF | Pre-send prop firm limit guard |

---

## 14. Troubleshooting

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

---

*Last updated: August 2026. This manual covers the system as currently deployed.*
