# AI Trading Partner
# Version 1.0 — Product Specification
**July 2026 | NO CODE | NO REFACTOR | NO IMPLEMENTATION**

---

## Table of Contents

1. [User Journey — The Ideal Trading Day](#task-1--user-journey)
2. [Screen Definitions](#task-2--screen-definitions)
3. [Role Responsibilities](#task-3--role-responsibilities)
4. [Information Hierarchy](#task-4--information-hierarchy)
5. [Operator Experience Design](#task-5--operator-experience)
6. [Version 1 Feature Selection](#task-6--version-1-feature-selection)
7. [Priority Matrix](#task-7--priority-matrix)
8. [Version 1 Definition](#task-8--version-1-definition)

---

---

# TASK 1 — USER JOURNEY

## The Ideal Trading Day

---

### STEP 1: Opening the Application

**What the trader sees:**

The main screen opens with a single commanding view. The trader does not face a wall of panels. They see:

- A clear **session status** indicator — whether the market is currently open, in a halt, or closed for the day.
- A **market health summary** — one or two sentences from the Main Brain describing the current market condition in plain English. Not a list of numbers. A statement: "The market opened bullish this morning, has been trending above the daily VWAP, and structure remains intact on both instruments."
- The **best current setup** highlighted automatically — the platform has already determined which instrument (MGC or MNQ) has the highest probability right now and selected that tab.
- An **avatar greeting** — a brief welcome that confirms whether conditions are good, quiet, or uncertain today. "Good morning. MGC is setting up well above VWAP. MNQ is noisy — I'd focus on MGC first."
- The **time until next major event** — if there is a high-impact economic release within 90 minutes, a subtle but visible countdown is present.

**Which subsystem is responsible:**
- Market Session Awareness (open/halt/closed)
- Main Brain Cognitive Layer (voice narration, opening statement)
- Avatar Intelligence Engine (greeting, focus recommendation)
- ForexFactory News Feed (upcoming event countdown)
- Dashboard auto-landing (best-setup selection)

**What decisions are being made:**
- No trade decisions yet. The trader is orienting.
- The platform is deciding which instrument to surface first and whether conditions warrant attention or patience.

**What should be explained:**
- If the market is in a halt (17:00–18:00 ET), explain what a halt is and when it ends.
- If the market opened and conditions are immediately chaotic (no VWAP, no structure), the avatar says so explicitly. "We don't have a clean read yet. I'd give it 30 minutes before forming a view."

---

### STEP 2: Pre-Market Preparation

**What the trader sees:**

Before the primary session opens (or at the start of their trading window), the trader sees a **pre-session briefing view**. This is not a separate screen — it is the same main view with a "preparing" state:

- **Left Brain Thesis** — what is the market narrative right now? "Bullish bias above 2,650 VWAP. Structure is building demand on the daily. Invalidation: a daily close below 2,640."
- **Higher Timeframe Summary** — the 1H, 4H, and Daily direction labels for the active instrument. Displayed as directional arrows or color labels, not raw numbers. "Daily: Bullish. 4H: Bullish. 1H: Neutral."
- **Economic Calendar** — any high-impact events today, with times converted to the trader's session window. "CPI at 8:30 ET — high impact. Avoid trading 5 minutes before and 2 minutes after."
- **Strategy Playbook** — which 2–3 strategies are best suited to today's conditions based on the thesis and structure. "Today favors Demand Zone Retest and VWAP Reclaim setups. Avoid counter-trend fades."
- **Instrument Focus** — if one instrument is cleaner than the other, the platform recommends which to prioritize and why.

**Which subsystem is responsible:**
- Left Brain Thesis Engine (narrative, invalidation, timeline)
- SWING HTF Data Layer (1H/4H/Daily bias labels)
- ForexFactory News Feed (events with times)
- Playbook Selector from Unified Learning Brain (strategy recommendations)
- Cross-Market Index Alignment (instrument comparison)

**What decisions are being made:**
- Which instrument to focus on today
- Which strategy type to look for
- Whether today is a trading day at all (high-impact news = smaller size or skip)

**What should be explained:**
- What "invalidation" means: "If price does this specific thing, everything I've just told you about the bullish thesis is wrong. That's the line."
- What "HTF bias" means: "The 4-hour chart has been making higher highs and higher lows. That's a bullish trend on a timeframe that matters."

---

### STEP 3: Watching the Market

**What the trader sees:**

The market is open. The trader is in observation mode. The screen shows:

- **Real-time price and VWAP** for the active instrument — is price above or below VWAP? Is it expanding away or contracting back?
- **Structure status** — is a BOS or CHOCH present? "Structure confirmed: Change of Character to the upside (CHOCH). This means buyers have taken control."
- **CVD direction** — is cumulative volume delta confirming the direction? "CVD is bullish. Buyers are committing at these prices."
- **Edge Score building** — even if the setup is not READY, the trader can see which components are currently scoring and which are missing. "You have CHOCH (+20) and VWAP (+15). Missing: Sweep (+15) and Volume (+15)."
- **Left Brain MI panel** — the market intelligence summary: direction, strength, momentum. "Bullish. Strong. Accelerating." Or "Neutral. Weak. Stalling."
- **"Nothing to do" state** — when nothing is forming, the platform says so clearly and tells the trader what to watch for. "Waiting for a sweep of the 2,648 lows or a VWAP reclaim above 2,651. Either would start building the setup."

**Which subsystem is responsible:**
- VWAP Engine (real-time price context)
- Market Structure Detection (BOS/CHOCH state)
- CVD / Delta Engine (directional confirmation)
- Edge Score Engine (component-by-component scoring)
- Left Brain Market Intelligence (direction/strength/momentum)
- Analyst Reasoning Engine (what to watch for)

**What decisions are being made:**
- Stay or leave: is this worth watching, or should the trader step away?
- Instrument switching: is the other instrument cleaner right now?

**What should be explained:**
- What a CHOCH is: "Change of Character. Previously, the market was making lower highs. It just made a higher high. That's the first sign of a directional shift."
- What CVD means: "Cumulative Volume Delta. It tracks whether the volume coming in is from buyers or sellers. Right now, more volume is from buyers — that confirms the bullish bias."

---

### STEP 4: Opportunity Developing

**What the trader sees:**

Something is starting to form. The trader receives an **EARLY alert** — a notification that a setup is building but not yet READY:

- A visible indicator (⚡ EARLY) appears on the active instrument tab, drawing attention without demanding action.
- The **potential plan preview** becomes visible: "If this becomes a full setup, the plan would be: Entry near 2,649, Stop at 2,644.50, Target at 2,659. Risk: ~$140 per contract."
- The platform explains what is still missing: "Waiting for: Volume confirmation (+15). Everything else is in place."
- The **Main Brain** begins synthesizing: "Bull analyst sees a textbook demand zone retest above the daily VWAP. Bear analyst notes the volume is light so far. Judge: leaning bullish but not yet decisive."
- The avatar becomes more active: "This looks like it's building. I'm watching the volume on the next candle."

**Which subsystem is responsible:**
- EARLY Pre-Ready Alert (⚡ EARLY tier)
- Potential-Plan Preview (forming-setup entry/stop/TP)
- Edge Score Engine (what's missing)
- Trade Debate Engine (Bull/Bear/Judge synthesis)
- Avatar Intelligence Engine (proactive observation)

**What decisions are being made:**
- Prepare or ignore: is this setup worth preparing for, or does the thesis conflict with it?
- Size consideration: if this becomes READY, is there a reason to trade smaller?

**What should be explained:**
- What EARLY means: "This is not a trade signal. It means the conditions are starting to align. Think of it as 'start paying attention.' A full signal requires one more confirmation."
- What the potential plan is: "This is what the trade would look like IF it becomes valid. The exact numbers may shift slightly. This is so you can think about sizing and risk before it becomes urgent."

---

### STEP 5: Trade Decision

**What the trader sees:**

The full READY verdict arrives. This is the most important moment in the platform. The trader sees:

- A **READY badge** on the instrument — clear, unmissable, with the grade (A+ / A / B).
- The **Main Brain verdict** in plain English: "Take this trade. All systems aligned. MGC long above 2,649 — demand zone, VWAP confirmed, structure in, CVD bullish. This is the setup."
- The **trade plan**: Entry price, stop level, take-profit target(s). Dollar risk at 1 contract. Recommended size.
- The **full analyst context** (available but not in the way): Bull case, Bear case, Judge verdict, risk grade, game plan.
- The **action button**: ENTER — one clear button. If auto-trade is armed, the platform states it will execute automatically. If not, the trader presses ENTER to send the order.
- The **Long/Short toggle** if the trader wants to see both sides before deciding.
- Any **active veto** clearly labeled: "The Analyst is concerned about extended ATR. This is flagged but not blocking the trade."

**Which subsystem is responsible:**
- Strict Gate (READY verdict)
- Edge Score Engine (grade, alert level)
- Main Brain Cognitive Layer (plain-English synthesis)
- Analyst Reasoning Engine (game plan, risk, veto display)
- Trade Debate Engine (Bull/Bear/Judge)
- Execution Gateway (ENTER action, auto-trade)
- Trade Plan builder (entry/stop/target)

**What decisions are being made:**
- Take the trade or pass
- Size: how many contracts
- Auto vs manual execution

**What should be explained:**
- What each gate means: "READY means Zone ✓, VWAP ✓, Structure ✓, CVD ✓. All four boxes are checked."
- What the grade means: "A+ means 85 or higher out of 110. This is a high-quality setup. B means 50–69 — the setup is valid but thinner."
- What a veto means: "The Analyst flagged a concern. This is advisory — the trade is still valid. The flag means there is a risk worth knowing about, not that you must skip."
- What the stop means: "If price reaches 2,644.50, the trade's premise is wrong and it exits automatically. That's the maximum risk per contract."

---

### STEP 6: Active Trade

**What the trader sees:**

A trade is open. The screen shifts into **active trade mode**:

- The active instrument tab shows an **OPEN indicator** — a persistent badge that the trade is live.
- The **Right Brain advisory panel** is now visible with real-time recommendations: "Currently: Hold. Price is moving toward target 1. No action needed." Or: "CAUTION: Price approaching stop. Consider partial exit if this level breaks."
- **Live P&L** in R and in dollars: "+0.45R / +$63 per contract."
- **Trade management controls**: Move stop to breakeven (one button), take partial profit, close trade.
- The **thesis validity indicator**: "Trade thesis valid. Price remains above VWAP. Structure intact." Or: "Thesis invalidated — price has broken back below entry VWAP. Consider closing."
- The **timer** showing how long the trade has been open.
- If the trade is managed automatically (SCALP dynamic exits), the platform shows which targets have been hit: "TP1 hit — partial exit complete. Runner active. Stop moved to breakeven."

**Which subsystem is responsible:**
- Active Trade Persistence (open trade state)
- Right Brain Trade Management (advisory: trail/hold/exit)
- SCALP Dynamic Exits (TP1/TP2/runner management)
- Manual Trade Manager (advisory for hand-managed trades)
- P&L computation (R and dollar tracking)
- Advisory Overlays — Active Thinking (in-trade reasoning)

**What decisions are being made:**
- Hold or exit early
- Take partial profit or let it run
- Whether the thesis is still valid

**What should be explained:**
- What "thesis valid" means: "The reason we entered this trade is still true. Price is still above VWAP, structure is intact, and nothing has changed that would negate the original idea."
- What "thesis invalidated" means: "The condition that made this a good trade is no longer true. This does not automatically mean you are losing — but it means the reason to hold is gone."
- What breakeven means: "We're moving the stop to where you entered. Best case: you still make money. Worst case: you scratch the trade with no loss."

---

### STEP 7: Trade Completion

**What the trader sees:**

The trade closes — either by hitting a target, stopping out, or manual close. The platform immediately:

- Shows a **trade outcome card**: "MGC Long — Closed. Result: +1.2R / +$168. Entry: 2,649. Exit: 2,657. Duration: 22 minutes. Grade at entry: A."
- The **journal** is automatically updated — a Discord embed fires to the journal channel with the full card.
- A brief **explanation of what happened**: "The trade reached Target 1 (1:1). The runner was still active when structure reversed — the system exited the runner at 2,655.50 rather than the planned 2,659. That's normal in choppy conditions after the first target."
- The **learning capture**: "Thesis Tracker has recorded this setup. The pattern — CHOCH + demand zone + VWAP above — now has 7 samples in memory. Win rate on this pattern: 71%."
- The platform returns to **watching mode** — the OPEN badge clears, the interface relaxes back to observation state.
- If this was a loss: "Stop hit at 2,644.50. Result: -1R / -$140. What happened: structure broke before volume confirmation arrived. The gate was valid at entry — this is within normal variance."

**Which subsystem is responsible:**
- Journal System (trade card, Discord embed)
- Adaptive Learning Engine (outcome recording, pattern capture)
- Thesis Tracker (snapshot resolution, lesson)
- Trade Management Analytics Sidecar (MFE/MAE, slippage)
- Shared Trade Memory (similar-trade pattern update)

**What decisions are being made:**
- Review the trade now or move on
- Were there any process errors (entered without a gate condition)?

**What should be explained:**
- What "1.2R" means: "You risked 1 unit and made 1.2 of that unit back. If your risk was $140, you made $168."
- What the learning capture means: "The platform is building a memory of setups exactly like this one. Over time, it will know whether this specific pattern has been working or not, and it will factor that into future scoring."
- On a losing trade: distinguish between a good process / bad outcome (acceptable) and a bad process (entry without proper conditions).

---

### STEP 8: End-of-Day Review

**What the trader sees:**

The session ends (or the trader chooses to review). The platform presents an **EOD summary view**:

- **Today's performance**: Total trades, wins/losses, total R, total P&L in dollars. Equity curve for the day.
- **Today's best trade**: What went right, what aligned well, what the grade was.
- **Today's hardest trade**: If there was a loss or a forced exit — what happened, why, what the gate said at the time.
- **Coaching note**: One clear observation from the learning engine. Not a lecture. One thing: "You took 3 trades today. 2 were A-grade. 1 was B-grade (the 11:30 trade). The B-grade trade was the only loss. Pattern: your B setups have a 38% win rate vs 74% for A and A+. Something to keep in mind."
- **Thesis review**: What the Left Brain thesis said at open, what it said at close, and whether it was correct. "The bullish thesis held for 3 hours, then invalidated after the 11:15 structure break."
- **Tomorrow's watch**: Any conditions or levels the platform flags as important for tomorrow's preparation.

**Which subsystem is responsible:**
- Journal System (Today's Trades panel, EOD Discord report)
- Adaptive Learning Engine (win rate by grade, coaching note)
- Thesis Tracker (thesis arc for the day, lessons)
- Trade Failure Analyzer (root cause on losing trades)
- Equity Curve (today's P&L chart)

**What decisions are being made:**
- What to study or review before tomorrow
- Whether today's process was sound regardless of outcome

**What should be explained:**
- What "process" means: "A process review asks: did you follow the rules? A READY signal with a proper gate is a good process trade regardless of whether it won or lost. A trade taken without the gate conditions met is a bad process trade — even if it won."
- What the coaching note means: "This is based on your actual history with this platform. It's not a general rule — it's specific to how you have been trading."

---

---

# TASK 2 — SCREEN DEFINITIONS

---

## Screen 1: Operator Mode (Primary Trading View)

**Purpose:**
The main command center. The screen a trader has open during market hours. Every decision a trader makes while trading happens here.

**Primary questions it answers:**
1. Should I be in a trade right now?
2. If yes, what is the setup and the plan?
3. If I am in a trade, how is it going and what should I do?
4. What is the market doing and why?

**Required panels:**

| Panel | Description |
|---|---|
| **Market Brain** | The single most important element. Shows the current verdict (WAIT / READY / OPEN), the edge grade (A+/A/B), and a one-sentence plain-English summary of what the Brain sees right now. |
| **Active Trade Status** | When a trade is open: shows entry, stop, target, current P&L in R and dollars, thesis validity. Hidden when no trade is open. |
| **Trade Action** | The ENTER button (when READY), the management controls (when OPEN: move stop, close), and the execution mode indicator (manual / paper / live). |
| **Price + VWAP Context** | Current price, VWAP value, whether price is above or below. One directional label. |
| **Setup Status** | What gate conditions are currently met. Which are missing. Why it is WAIT if it is WAIT. |
| **Instrument Tabs** | MGC / MNQ tabs. Tab indicator shows READY (green pulse) or OPEN (persistent indicator) per instrument. |
| **Market Session** | Open / Halt / Closed with time to next state change. |
| **Avatar** | A brief, contextual observation from the trading partner. Updates on meaningful state changes. |

**Optional panels (collapsed by default):**
- Edge Score breakdown (component-by-component)
- Trade Debate (Bull / Bear / Judge)
- Left Brain Thesis (narrative, invalidation, timeline)
- Economic Calendar (upcoming events)
- Today's Trades log
- Strategy Advisory (what the 16 research strategies are saying)
- Cross-market alignment (MNQ / MES / MYM agreement)

**Dependencies:**
- All analysis engines (Strict Gate, Edge Score, Main Brain)
- Active trade persistence
- Execution gateway (for ENTER action)
- Market session awareness
- Discord journal (for notifications)

---

## Screen 2: Engineering View (Full Analysis Dashboard)

**Purpose:**
The deep-inspection view. Not used during active trading decision-making. Used when the operator wants to understand exactly why the platform said what it said, or when diagnosing a problem.

**Primary questions it answers:**
1. Exactly why did the gate say WAIT?
2. What is each individual component scoring?
3. What are all the analyst layers saying?
4. What did the Left Brain observe over the last hour?
5. Is the system working correctly?

**Required panels:**
- Per-gate diagnostics (PASS/FAIL for every gate condition)
- Alert history feed (recent webhooks with type, instrument, time)
- VWAP diagnostics (source, age, freshness)
- ATR and volatility monitor (ratio, brake state)
- All analyst layers (Analyst, Debate, Professional Review, Main Brain full)
- Left Brain Intelligence (MI direction/strength/confidence)
- Left Brain Thesis (full narrative including timeline and playbook reasoning)
- Strategy scan (all 29 candidates, active strategy, scores)
- Auto-trade arming controls
- Per-asset safety controls (kill switch, limits)
- Bot Training Mode status
- Prop Firm Protection status

**Optional panels:**
- Eval metrics (request counts, WAIT rate, gate pass rates)
- Decision trace (per-instrument full decision log)
- Cross-market panel (MNQ/MES/MYM agreement detail)
- Databento feed status
- Right Brain trade management advisory (full detail)
- Dual-TF convergence state (when flag on)

**Dependencies:**
- All subsystems (this view is the full system view)
- Owner authentication required

---

## Screen 3: Research & Backtest Lab

**Purpose:**
Offline research. A place to explore strategy performance, upload historical data, and run optimization sweeps. Never connected to the live trading path.

**Primary questions it answers:**
1. How has this strategy performed historically?
2. Which management style (hold, partial exit, runner) produces the best outcome?
3. What is the optimal stop size for this instrument?
4. Which of the 16 scalp strategies has been performing best on live paper simulation?

**Required panels:**
- Backtest file upload (CSV → run → results)
- Backtest results display (trades, equity curve, Sharpe, max drawdown, best combo)
- Baseline comparison (current params vs optimal params from sweep)
- Scalp Research Engine (16 strategies, live paper sim, win rates, status)
- Scalp Strategy Advisory (ranked candidates with reasoning votes)
- Analysis Bot toggle (enable/disable parallel analysis-only instance)

**Optional panels:**
- Raw strategy×management matrix (full optimization grid)
- Dataset management (uploaded files, date ranges)
- Dual-sim ghost trade log

**Dependencies:**
- Backtest Engine (offline, no live path)
- Scalp Research Engine (live stream observer only)
- Baseline Engine
- Analysis Bot instance

---

## Screen 4: Coach & Academy

**Purpose:**
Learning. Review past performance. Understand why trades worked or failed. Study the playbook.

**Primary questions it answers:**
1. Why do my B-grade trades lose more often?
2. What have I been doing wrong lately?
3. What does "CHOCH" mean and how do I use it?
4. What did the platform learn about me this week?

**Required panels:**
- Academy knowledge library (strategies, rules, management guidelines)
- Academy Ask (LLM Q&A against the curriculum — "explain a demand zone in plain language")
- Trade Idea Review (type a hypothetical trade, get a scored grade)
- Decision Quality Summary (process quality score trend over time)
- Trade Failure Analysis (patterns in losing trades, root cause categories)
- Thesis Tracker history (setups that were snapshots → outcomes → lessons)
- Weekly Learning Report (per-strategy win rates, hours with highest edge)

**Optional panels:**
- TradeZella history review (imported personal journal analysis)
- Bot Training Mode progress indicator (current stage, what Stage N+1 requires)
- Per-strategy win-rate table

**Dependencies:**
- Academy Knowledge Module
- Trade Idea Review engine
- Decision Quality Analytics
- Trade Failure Analyzer
- Thesis Tracker
- Adaptive Learning Engine (win-rate tables)
- TradeZella import

---

## Screen 5: Journal & Performance

**Purpose:**
The permanent trade record. Today's trades and historical performance.

**Primary questions it answers:**
1. What trades did I take today?
2. How is my equity curve looking?
3. What was my best streak? Worst drawdown?
4. What did the Discord journal channel post today?

**Required panels:**
- Today's Trades log (per instrument, with entry/exit/R/grade)
- Equity curve (today's session)
- EOD performance summary (total trades, R, dollar P&L, win rate)
- Discord journal feed summary (what was sent to the journal channel)

**Optional panels:**
- Trade management analytics detail (MFE/MAE, slippage per trade)
- Simulation realism overlay toggle (shows commission-adjusted "real" performance vs raw)
- Strategy performance by instrument and hour

**Dependencies:**
- Journal System
- strategy_trades table
- Trade Management Analytics Sidecar
- Equity curve data

---

---

# TASK 3 — ROLE RESPONSIBILITIES

Each role owns its domain exclusively. Nothing spans two roles unless unavoidable, in which case the overlap is called out.

---

## Trading Desktop

**Owns:**
- The visual interface and layout (navigation, panels, themes, collapse/reorder)
- Market session state (open / halt / closed / holiday)
- Instrument selection and tab switching (MGC / MNQ / MES / MYM)
- Real-time price and VWAP display
- ForexFactory economic calendar display
- Cross-market index alignment display
- Equity curve (today)
- Today's Trades log panel
- Staleness / last-updated clock
- View-only share link (for observers)
- Authentication gate (Basic Auth)
- Theme (glass / retro)
- Panel layout persistence (localStorage)

**Does NOT own:**
- Why the market is doing what it is doing (Expert)
- What to do about the market (Partner)
- What the trade means for learning (Coach / Journal)
- Whether to send an order (Manager)

---

## Trading Expert

**Owns:**
- All raw market data ingestion: VWAP, ATR, CVD, Volume, Sweeps, Structure, Zones, FVG/OB
- Left Brain Market Intelligence (direction, strength, momentum, confidence)
- Left Brain Thesis (narrative, invalidation, timeline, OUTLOOK_SHIFT)
- Strict Gate decision (READY/WAIT verdict, per-gate PASS/FAIL)
- Edge Score and Grade (0–110, A+/A/B/WAIT, component breakdown)
- Multi-Strategy Engine (regime → strategy selection, 29 strategies)
- ORB / Breakout Mode advisory
- SWING HTF Data Layer (1H/4H/Daily bias and levels)
- Swing Mode V2 (9-category HTF scorer)
- Dual-TF Engine (1m bias + 5s convergence)
- Fast Entry Trigger (1s/5s timing overlay)
- Entry Quality Location Engine (0–100 location score)
- Trend Brake and Structure-Reversal Demote
- MI Adaptive Strategy Filter (SCALP demote-only veto)
- Databento live feed (bar-close precision, flag-gated)

**Does NOT own:**
- What the data means for the trader personally (Partner)
- Whether to execute (Manager)
- Whether this pattern has worked before (Coach / Journal)

---

## Trading Partner

**Owns:**
- All AI reasoning layers: Analyst, Trade Debate (Bull/Bear/Judge), Professional Review
- Main Brain synthesis (verdict narrative, conflict resolver, verdict board)
- Main Brain voice (plain-English narration)
- Avatar persona (proactive observations, greetings, explain-simply mode)
- VRM 3D Avatar display
- AI Assistant chat (contextual Q&A grounded on live snapshot)
- Stalk + Active Thinking advisory overlays (pre-entry + in-trade)
- Unified Analyst Report (consolidated thesis, 15-min update loop)
- Brain Conflict Resolver (why the engines disagree)
- Verdict Board (plain-English 4-bucket classifier)
- Per-direction Long/Short toggle (bull/bear case view)
- Potential-Plan Preview (forming-setup levels before READY)

**Does NOT own:**
- The raw data or gate decision (Expert)
- Trade execution or management (Manager)
- Trade history or learning (Coach / Journal)

*Note: The Partner consumes the Expert's outputs and translates them for the operator. The Partner never recomputes what the Expert already computed.*

---

## Trading Coach

**Owns:**
- Academy Knowledge Module (curriculum, strategy rules, management rules)
- Trade Idea Review (grade a hypothetical trade)
- Trade Failure Analyzer (root cause analysis of losses)
- Decision Quality Analytics (process quality scoring over time)
- Thesis Tracker (outcome-based pattern memory and lessons)
- Bot Training Mode progression (4-stage staged → live, with coaching at each stage)
- Backtest Engine (historical strategy testing)
- Baseline Engine (strategy×management optimization research)
- Scalp Research Engine (16-strategy live paper-sim lab)
- Per-gate diagnostics education (explaining what each gate means in plain language)
- TradeZella import and historical review

**Does NOT own:**
- Live verdict or real-time decisions (Expert / Partner)
- Trade execution (Manager)
- Trade records for ongoing tracking (Journal)

*Note: The Coach uses historical data and offline research. It never influences the live gate or sizing.*

---

## Trading Journal

**Owns:**
- Live trade cards (Discord embeds: entry, rationale, grade, plan)
- EARLY alert notification (⚡ pre-READY advisory)
- A+ Channel filtering (high-conviction setups)
- Trade-taken bell (audio notification on entry)
- Analyst Report Discord embeds (journal channel, 15-min loop)
- EOD performance report (Discord)
- strategy_trades DB table (permanent trade records)
- Trade Management Analytics Sidecar (MFE/MAE, commission, slippage records)
- Weekly Learning Report (performance summary)
- Screenshots forwarded to Discord on READY

**Does NOT own:**
- Analysis of what went wrong (Coach)
- Live active trade management (Manager)
- The raw verdict that triggers the journal (Expert)

---

## Trading Manager

**Owns:**
- Execution Gateway (manual / paper / traderspost / pickmytrade routing)
- Auto-trade arming and disarming (per-instrument, in-memory)
- SCALP Dynamic Exits (TP1/TP2/runner + delayed BE)
- Live 2-Contract Runner (flag-gated)
- Manual Trade Manager (advisory for hand-entered positions)
- Manual Desk Order (operator override, flag-gated)
- Per-asset safety controls (kill switch, position limits)
- Prop Firm Protection Guard (daily loss limit hard stop)
- Active Trade Persistence (write-through to DB, boot restore)
- Right Brain Trade Management advisory (trail/hold/exit recommendations)
- Auto Early-Exit (armed watcher for confirmed-invalid thesis)
- Advisor Auto-Trade Review Gate (opt-in pre-trade approval)
- USER_APPROVED_PREVIEW Take (operator-approved forming setups)
- Opposite-side reversal buffer (send spacing for TradersPost)
- Broker Payload Pre-Send Guard (audit log + required-field check)

**Does NOT own:**
- The verdict that triggers execution (Expert via Strict Gate)
- The reasoning or narration around the trade (Partner)
- The record of the trade after it closes (Journal)

---

---

# TASK 4 — INFORMATION HIERARCHY

---

## CRITICAL — Surface Immediately, Always Visible

These pieces of information must be visible without any action from the operator. They cannot be collapsed, hidden, or scroll-buried.

| Information | Why Critical |
|---|---|
| **READY / WAIT / OPEN verdict** | The core decision. The entire platform converges to this. |
| **Active trade status (open/closed)** | An open trade demands the operator's attention. They must know at a glance. |
| **Daily loss limit status (Prop Protection)** | If the kill switch is approaching, that is the most important thing on screen. |
| **Market session status (open/halt/closed)** | Trading during a halt is a catastrophic mistake. |
| **Auto-trade arm state** | If the bot is armed and about to execute, the operator must know. |
| **READY grade (A+ / A / B)** | Quality of the current signal. Drives sizing. |

---

## IMPORTANT — Visible by Default, Collapsible

These should be visible when the operator opens the platform. They can be collapsed but should default to open.

| Information | Why Important |
|---|---|
| **Edge Score (0–110) with component breakdown** | Tells the operator why the grade is what it is. |
| **Strict reason (why WAIT)** | If it is WAIT, the operator must know what is missing without guessing. |
| **Trade plan (entry / stop / target / R)** | The plan that governs any live or forming trade. |
| **Market thesis (plain-English statement)** | The market's directional story. Frames every decision. |
| **CVD direction** | Hard-fail input that can block or confirm a trade. |
| **VWAP value and freshness** | VWAP is in 3 gate conditions. Its staleness matters. |
| **Economic events (today)** | High-impact news can override every analysis. |
| **Main Brain voice narration** | The platform's plain-English synthesis. One sentence. |
| **Structure state (CHOCH/BOS present)** | Core gate condition. Visible means the operator understands the current market character. |

---

## REFERENCE — Available One Level Down

These panels should be accessible from the main view with one interaction (expand, tab, click) but do not need to be visible at all times.

| Information | Why Reference |
|---|---|
| **Left Brain Thesis (full narrative, invalidation, timeline)** | Important for context but not actionable in the moment. |
| **HTF bias summary (1H/4H/Daily)** | Confirms direction but does not change the trade plan. |
| **Trade Debate (Bull/Bear/Judge full detail)** | Useful for conviction but should not interrupt the decision moment. |
| **Strategy scan (all 29 candidates)** | Context about which strategy is active. Not needed every second. |
| **Cross-market alignment** | Confluence context. Rarely decision-critical in isolation. |
| **Right Brain advisory (trail/hold/exit)** | Useful but the operator acts on it, not reacts instantly. |
| **Active trade P&L detail (MFE/MAE, slippage)** | Useful after close, not during. |
| **Today's Trades log** | Performance review. Not needed during active watching. |
| **ForexFactory calendar detail** | The event countdown is CRITICAL. The full calendar detail is REFERENCE. |

---

## DIAGNOSTIC — Deep Inspection, Available via Engineering View

These should not appear in the primary trading view under any circumstances. They belong to the Engineering View only. They are for debugging, not for decision-making.

| Information | Why Diagnostic |
|---|---|
| **Per-gate PASS/FAIL detail** | Gate outcomes are surfaced as "strict_reason." The per-gate debug table is for engineers. |
| **Eval metrics (request counts, WAIT rate)** | System health monitoring. Not useful during trading. |
| **Databento feed status (bar precision, connection)** | Infrastructure health. Useful only when something is broken. |
| **Alert history feed (raw webhook log)** | Full ingestion record. Only needed when debugging why a signal was missed. |
| **ATR volatility ratio (exact numbers)** | The effect (volatility brake) is surfaced. The raw ratio is not. |
| **VWAP diagnostics (source, age in ms)** | The freshness indicator is surfaced. The raw diagnostics are not. |
| **Left Brain observation buffer detail** | Research data. Not a trading tool. |
| **Decision Pipeline V2 shadow log** | Internal shadow system. Not operator-facing. |
| **Analysis Bot status (/api2)** | Infrastructure. Not operator-relevant during trading. |
| **Flask health, DB probe status** | System health only. |

---

---

# TASK 5 — OPERATOR EXPERIENCE

**Design Principle:**
Every decision the platform makes must be explainable in the same sentence structure: *What happened → Why → What changed → What is missing → What would invalidate it → Recommended action.*

This section defines the communication contract for every major decision state.

---

## State: WAIT — Gate Not Met

**What happened:**
"The setup is not ready to trade yet."

**Why:**
"One or more of the required conditions is not yet confirmed. The platform needs [Zone / VWAP / Structure] before it considers this a valid entry."

**What changed:**
"Nothing has changed — or — [specific event] just updated the state. For example: 'VWAP position just confirmed after price reclaimed 2,651.'"

**What is missing:**
"Currently missing: [list, one per line]:
— Structure confirmation (no BOS or CHOCH yet)
— VWAP confirmation (price is below VWAP)
These are the specific conditions that need to change for a READY signal."

**What would invalidate it:**
"If price drops below 2,640, the demand zone is likely compromised and the setup is off."

**Recommended action:**
"Wait and watch. If a CHOCH appears above 2,649 with volume, the setup completes."

---

## State: READY — Full Signal

**What happened:**
"All conditions are met. This is a valid setup."

**Why:**
"Zone confirmed (demand zone active at 2,648–2,651). VWAP confirmed (price above 2,651 VWAP). Structure confirmed (CHOCH at 2,652). CVD bullish. Edge Score: 90/110. Grade: A+."

**What changed:**
"The last confirmation was the structure CHOCH that arrived 2 minutes ago."

**What is missing:**
"Nothing required is missing. The Volume component is not scoring (+15 would bring the score to 105) but it is not a gate requirement."

**What would invalidate it:**
"A price break below 2,648 (the zone) would invalidate the setup. A CHOCH to the downside would change the structure view."

**Recommended action:**
"Enter long near 2,649. Stop at 2,644.50. Target 1 at 2,659. Risk per contract: $140."

---

## State: EARLY — Setup Building

**What happened:**
"The setup is starting to form but is not complete."

**Why:**
"Sweep and structure are present, which are early confirmation signals. The gate is not yet met — at least one required condition is still missing."

**What changed:**
"A liquidity sweep just appeared below 2,646. This is the first signal that the demand zone is being tested."

**What is missing:**
"The gate still needs: VWAP confirmation (price must sustain above 2,651). Everything else is in place."

**What would invalidate it:**
"If price sweeps below 2,642 and fails to recover, the demand zone has been compromised."

**Recommended action:**
"Do not enter yet. Watch for price to hold above 2,651 VWAP on the next candle close. If it does, this becomes a READY setup."

---

## State: OPEN — Trade Active

**What happened:**
"You are in a trade. [Instrument] Long entered at [price]. Stop at [stop]. Target at [target]."

**Why the trade is still valid:**
"The original reason for the trade — demand zone + VWAP + structure — remains intact. Price has not violated any of these conditions."

**What has changed since entry:**
"Price moved from 2,649 to 2,654 (+5 pts / +0.35R). Target 1 at 2,659 is getting closer."

**What would invalidate the thesis:**
"A close below 2,648 (the zone) or a confirmed CHOCH to the downside would make the original reason for this trade invalid."

**Recommended action:**
"Hold. No action required. The Right Brain recommends trailing the stop to 2,648 if price reaches 2,656."

---

## State: THESIS INVALIDATED — While in a Trade

**What happened:**
"The market just did something that changes the original reason for this trade."

**Why:**
"A Change of Character (CHOCH) to the downside appeared at 2,651. This means the market structure has flipped from the bullish view that supported the entry."

**What changed:**
"The structure condition that the trade was based on is no longer intact. Price is also approaching the entry VWAP level from above."

**What is missing:**
"Nothing is 'missing' — but the conditions that made this trade valid are no longer present."

**What would re-validate it:**
"If price reclaims 2,652 with a new CHOCH to the upside within the next 10 minutes, the thesis could be re-established."

**Recommended action:**
"Consider closing or reducing size. The thesis is no longer supported. Risk: if you stay in, you are now holding a position without a confirmed reason."

---

## State: VETO ACTIVE — Analyst Concern

**What happened:**
"The setup is READY but the Analyst has flagged a concern."

**Why:**
"[Specific reason, e.g.]: The Analyst notes that price is 1.8× the normal daily range extended from VWAP. At this extension, the probability of a reversal before the target is higher than normal."

**What this means for the trade:**
"The gate says READY. The Analyst says the timing is risky. Both are shown to you so you can decide. The veto does not block the trade — it informs your decision."

**Recommended action:**
"If you take the trade, consider using a tighter stop or a smaller size. The Analyst's concern is about the risk/reward at this specific entry point, not about the setup quality overall."

---

## State: WAIT — Market Closed

**What happened:**
"The market is currently closed."

**Why:**
"CME futures markets are in their daily halt period (17:00–18:00 ET). This is a scheduled break every trading day, not a disruption."

**What changed:**
"Nothing — this is a scheduled closure. All signals and setups are on pause until the market reopens."

**What would change this:**
"The market reopens at 18:00 ET (6:00 PM Eastern Time). The platform will resume normal analysis at that time."

**Recommended action:**
"No action. Alerts are suppressed during market close. Use this time to review today's trades or prepare for the evening session."

---

---

# TASK 6 — VERSION 1 FEATURE SELECTION

---

## Feature Selection Labels

- **KEEP** — Present, working, belongs in V1 without change.
- **MERGE** — Two or more things that should be presented as one experience.
- **HIDE** — Exists and works but is not appropriate for the primary operator view. Available in Engineering View or advanced settings.
- **REMOVE** — Not appropriate for V1. Either too early, orphaned, or contradicts the product vision.

---

## Version 1 Feature Selection by Subsystem

| # | Subsystem | Label | Justification |
|---|---|---|---|
| 1 | Webhook Ingestion Layer | KEEP | Foundation. Every trade decision originates here. Not operator-visible but non-negotiable. |
| 2 | Instrument Registry & Resolver | KEEP | Foundation. Critical infrastructure. Not operator-visible. |
| 3 | VWAP Engine | KEEP | Core gate input. Freshness indicator is operator-visible. Critical. |
| 4 | ATR / Volatility Monitor | KEEP | Powers stop placement, sizing, and the volatility brake. |
| 5 | Market Structure Detection (BOS/CHOCH) | KEEP | Core gate input. Structure label is one of the most important things a trader sees. |
| 6 | CVD / Delta Engine | KEEP | Hard veto in the gate AND an edge component. Non-negotiable. |
| 7 | Volume / RVOL | KEEP | Edge component. Operator-visible in the edge breakdown. |
| 8 | Supply/Demand Zone Engine | KEEP | Core gate input for SWING. Demote for SCALP. Non-negotiable. |
| 9 | Liquidity Sweep Detector | KEEP | Edge component and EARLY alert trigger. Core to the setup flow. |
| 10 | FVG / OB Analyst Evidence | HIDE | Analyst-level context. Not needed in the primary operator view. Available in Engineering View under Analyst detail. |
| 11 | Databento Live Feed | KEEP | Flag-gated. When active, improves ATR accuracy. Status shown in Engineering View only. |
| 12 | Left Brain Market Intelligence | KEEP | The most legible market context layer. The "what is the market doing" answer. |
| 13 | Left Brain Thesis Engine | KEEP | The persistent narrative. Critical for context in pre-market and during watching. |
| 14 | Left Brain Observation Buffer | HIDE | Rich backend data, zero V1 operator value as a panel. Available via Engineering View for diagnostics. |
| 15 | Strict Gate / Decision Engine | KEEP | THE gate. The READY/WAIT verdict. Non-negotiable. |
| 16 | Edge Score Engine | KEEP | Transparent scoring. The operator needs to understand why A+ vs B. Non-negotiable. |
| 17 | Multi-Strategy Engine | MERGE | Merge with the Main Brain "what strategy is active today" display. The full 29-strategy detail is HIDE (Engineering View). |
| 18 | Breakout Mode (ORB) | KEEP | The 9:30 ORB is a distinct and important setup type. Displayed as a dedicated advisory panel, flag-gated, default OFF until Phase-D is ready. |
| 19 | SWING HTF Data Layer | KEEP | Critical for SWING mode. HTF direction labels are REFERENCE-level information in the operator view. |
| 20 | Swing Mode V2 Engine | KEEP | Flag-gated. When SWING mode active, this is the scoring engine. Default OFF until fully validated by the operator. |
| 21 | Analyst Reasoning Engine | KEEP | Critical Partner layer. Game plan and veto. Must be present in V1 but placed in the collapsible analyst panel, not the primary view. |
| 22 | Trade Debate Engine | KEEP | Bull/Bear/Judge is the most legible form of the debate. Keep but place in collapsible panel. Veto default OFF in V1. |
| 23 | Main Brain Cognitive Layer | KEEP | The single voice of synthesis. The orb, the grade, the narration. This IS the product. |
| 24 | Avatar Intelligence Engine | KEEP | Plain-language partner. The greeting, the "hey, look at this" observations, the explain-simply mode. Must be present. Deep memory wiring is DEFER. |
| 25 | Decision Pipeline V2 (Shadow) | HIDE | Zero operator value in V1. All flags OFF. Available in Engineering View log if needed. Remove from dashboard entirely. |
| 26 | Adaptive Learning Engine | KEEP | The edge score modifier and the learning report are core to the Coach experience. Keep but present simply: "This pattern has a 71% win rate over 32 trades." |
| 27 | Unified Learning Brain | MERGE | Merge with the Learning panel in Coach view. PER_MODE_STATS and playbook selector appear as one unified "What the system has learned" panel. |
| 28 | Shared Trade Memory Engine | KEEP | The similar-trade memory context is one of the most human moments in the platform. "I've seen this setup 14 times before" is a powerful coaching and partner element. |
| 29 | Thesis Tracker | KEEP | Snapshot + resolve + lesson. Presents in Coach view as "What happened to setups like this one in the past." |
| 30 | Trade Failure Analyzer | KEEP | Core Coach feature. Root-cause panel for losing trades. Present in Coach view. Not in primary trading view. |
| 31 | Decision Quality Analytics | KEEP | Keep the backend. Surface as a simple trend line in Coach view: "Your process quality has improved 3 weeks in a row." Detailed drill-down available but not required. |
| 32 | Right Brain Trade Management | KEEP | Advisory panel when a trade is OPEN. The trail/hold/exit recommendations are the core in-trade coaching experience. Phase-D auto-execution is DEFER. |
| 33 | Execution Gateway | KEEP | Non-negotiable. All execution flows through here. |
| 34 | Auto-Trade Arming System | KEEP | Core automation feature. Arm/disarm controls must be visible in the primary operator view. |
| 35 | SCALP Dynamic Exits | KEEP | Multi-target management is the standard for V1 SCALP trades. |
| 36 | Live 2-Contract Runner | KEEP | Flag-gated. Available for operators who want it. Not default. Present as an opt-in in Manager settings. |
| 37 | Manual Trade Manager | KEEP | Operators trade manually too. The advisory overlay for hand-entered positions is a V1 requirement. |
| 38 | Manual Desk Order | HIDE | Useful power-user feature. Available via Engineering View / advanced controls. Not in primary view. |
| 39 | Bot Training Mode | KEEP | The staged progression is the V1 onboarding experience. Stage 1–3 suggest-only, Stage 4 live. Present as a clear status indicator in Manager view. |
| 40 | Prop Firm Protection Guard | KEEP | Default OFF but essential for prop traders. Simple on/off in safety controls. Status visible in Manager view. |
| 41 | Per-Asset Safety Controls | KEEP | Kill switch and position limits. Essential risk management. Simple interface in Manager view. |
| 42 | Dual-TF Engine | HIDE | Power-user SCALP feature. Flag-gated. Available but not in the primary operator view. Engineering View / advanced settings. |
| 43 | Fast Entry Trigger | KEEP | The structure bridge (SWEEP_RECLAIM → inject structure) is always active and essential. The money-path timing component is flag-gated. Display the bridge as part of the standard setup flow. |
| 44 | Micro Scalp Mode | HIDE | Ultra-short-duration mode. Ghost ledger is research. Not a V1 primary feature. Available as a flag in Engineering View. |
| 45 | Scalp Research Engine | KEEP | Core to the Research & Backtest screen. Provides live strategy performance data that directly informs operator decisions about which strategies to prioritize. |
| 46 | Scalp Strategy Advisory | MERGE | Merge with the Main Brain "what the strategies are saying" panel. Present as one ranked-candidate list, not a separate panel. |
| 47 | Backtest Engine | KEEP | Core research tool. Essential for validating strategy changes before going live. |
| 48 | Baseline Engine | KEEP | Pairs with Backtest. Provides the optimization comparison that makes backtesting actionable. |
| 49 | Journal System | KEEP | Trade cards, Discord posts, strategy_trades DB. The permanent record. Non-negotiable. |
| 50 | Trade Idea Review | KEEP | Simple, powerful coaching tool. Type a trade, get a grade. Accessible from Coach view. |
| 51 | Market State Cache | KEEP | Infrastructure. Essential for cold-start correctness. Not operator-visible. |
| 52 | Active Trade Persistence | KEEP | Infrastructure. Essential for restart safety. Not operator-visible as a feature. |
| 53 | Database Layer | KEEP | Foundation. Not operator-visible. |
| 54 | Express API Server | KEEP | Foundation. Security gateway. Not operator-visible. |
| 55 | React Dashboard (home artifact) | KEEP | IS the product. |
| 56 | AI Assistant Chat | KEEP | "Ask the platform a question" is a key partner interaction. Must be present and accessible. Ideally triggered from the main view with one click. |
| 57 | Academy Knowledge Module | KEEP | The curriculum, the Q&A. Core Coach screen content. |
| 58 | Market Session Awareness | KEEP | Foundation. Session status is CRITICAL information. |
| 59 | Cross-Market Index Alignment | KEEP | Collapse by default. Available as a REFERENCE panel. Discord alignment alerts are valuable. |
| 60 | ForexFactory News Feed | KEEP | Event countdown is CRITICAL. Full calendar is REFERENCE. Both stay in V1. |
| 61 | TradeZella Integration | KEEP | Enriches shared trade memory with personal history. Import is a setup step. Display in Coach view. |
| 62 | Diagnostics & Observability | HIDE | Engineering View only. Not in the primary operator view. |
| 63 | Analysis Bot (Second Instance) | HIDE | Research infrastructure. Present in Engineering View. Not operator-facing in V1. |
| 64 | Pine Script Sources | KEEP | Foundation. Without these, no signals arrive. Not operator-visible as a UI element. |

---

### Summary Counts

| Label | Count |
|---|---|
| KEEP | 48 |
| MERGE | 4 |
| HIDE | 11 |
| REMOVE | 0 |

**Note on REMOVE:** No subsystems are removed in V1. The platform has invested heavily in each of these and none are actively harmful. Features labeled HIDE are available via Engineering View or advanced settings — they are not surfaced in the primary operator experience but are not discarded. Nothing is removed.

---

---

# TASK 7 — PRIORITY MATRIX

**Scoring dimensions:**
- **Business Value (1–10):** Impact on the commercial proposition of the product
- **Operator Value (1–10):** Direct value to the trader during their trading day
- **Technical Maturity (1–10):** How complete, tested, and stable the implementation is
- **Integration Level (1–10):** How deeply connected to other subsystems

**V1 Priority** = (Business Value × 1.5) + (Operator Value × 2.0) + (Technical Maturity × 1.0) + (Integration Level × 0.5)

Sorted highest V1 Priority first.

---

| Rank | Subsystem | BV | OV | TM | IL | V1 Priority | Label |
|---|---|---|---|---|---|---|---|
| 1 | Strict Gate / Decision Engine | 10 | 10 | 10 | 10 | 60.0 | KEEP |
| 2 | Execution Gateway | 10 | 10 | 10 | 10 | 60.0 | KEEP |
| 3 | Edge Score Engine | 10 | 10 | 10 | 9 | 59.5 | KEEP |
| 4 | Main Brain Cognitive Layer | 10 | 10 | 9 | 10 | 58.5 | KEEP |
| 5 | Webhook Ingestion Layer | 10 | 7 | 10 | 10 | 54.5 | KEEP |
| 6 | VWAP Engine | 9 | 9 | 10 | 9 | 54.0 | KEEP |
| 7 | Market Structure Detection | 9 | 9 | 10 | 9 | 54.0 | KEEP |
| 8 | Journal System | 9 | 9 | 10 | 8 | 53.5 | KEEP |
| 9 | Auto-Trade Arming System | 9 | 9 | 10 | 8 | 53.5 | KEEP |
| 10 | React Dashboard (home artifact) | 10 | 10 | 9 | 7 | 53.5 | KEEP |
| 11 | Left Brain Market Intelligence | 8 | 9 | 10 | 9 | 53.0 | KEEP |
| 12 | CVD / Delta Engine | 9 | 8 | 10 | 9 | 52.5 | KEEP |
| 13 | Left Brain Thesis Engine | 8 | 9 | 10 | 8 | 52.5 | KEEP |
| 14 | Analyst Reasoning Engine | 9 | 8 | 9 | 9 | 51.5 | KEEP |
| 15 | Database Layer | 10 | 4 | 10 | 10 | 51.0 | KEEP |
| 16 | Active Trade Persistence | 9 | 8 | 10 | 8 | 51.0 | KEEP |
| 17 | Express API Server | 10 | 3 | 10 | 10 | 51.0 | KEEP |
| 18 | Market Session Awareness | 8 | 9 | 10 | 8 | 51.0 | KEEP |
| 19 | Supply/Demand Zone Engine | 8 | 8 | 10 | 9 | 50.5 | KEEP |
| 20 | Instrument Registry & Resolver | 9 | 5 | 10 | 10 | 50.0 | KEEP |
| 21 | ATR / Volatility Monitor | 8 | 8 | 10 | 8 | 50.0 | KEEP |
| 22 | SCALP Dynamic Exits | 9 | 8 | 9 | 8 | 49.5 | KEEP |
| 23 | Per-Asset Safety Controls | 9 | 7 | 10 | 7 | 49.0 | KEEP |
| 24 | Pine Script Sources | 10 | 5 | 9 | 10 | 49.0 | KEEP |
| 25 | Market State Cache | 8 | 6 | 10 | 9 | 48.0 | KEEP |
| 26 | Liquidity Sweep Detector | 7 | 8 | 10 | 8 | 47.5 | KEEP |
| 27 | Adaptive Learning Engine | 8 | 8 | 9 | 8 | 47.5 | KEEP |
| 28 | Avatar Intelligence Engine | 7 | 9 | 7 | 7 | 46.5 | KEEP |
| 29 | Trade Debate Engine | 7 | 8 | 9 | 8 | 46.5 | KEEP |
| 30 | Right Brain Trade Management | 8 | 8 | 7 | 8 | 45.5 | KEEP |
| 31 | AI Assistant Chat | 7 | 8 | 9 | 7 | 45.5 | KEEP |
| 32 | Volume / RVOL | 6 | 8 | 10 | 7 | 44.0 | KEEP |
| 33 | ForexFactory News Feed | 6 | 8 | 10 | 5 | 43.5 | KEEP |
| 34 | Manual Trade Manager | 7 | 7 | 9 | 6 | 42.0 | KEEP |
| 35 | Academy Knowledge Module | 7 | 7 | 9 | 5 | 41.5 | KEEP |
| 36 | Bot Training Mode | 8 | 7 | 9 | 6 | 41.5 | KEEP |
| 37 | Shared Trade Memory Engine | 7 | 7 | 9 | 7 | 41.5 | KEEP |
| 38 | Fast Entry Trigger | 7 | 7 | 9 | 7 | 41.5 | KEEP |
| 39 | SWING HTF Data Layer | 7 | 7 | 9 | 7 | 41.5 | KEEP |
| 40 | Multi-Strategy Engine (merged) | 7 | 7 | 9 | 7 | 41.5 | MERGE |
| 41 | Prop Firm Protection Guard | 8 | 6 | 9 | 5 | 40.5 | KEEP |
| 42 | Thesis Tracker | 7 | 7 | 7 | 7 | 40.0 | KEEP |
| 43 | Trade Failure Analyzer | 7 | 7 | 7 | 7 | 40.0 | KEEP |
| 44 | Scalp Research Engine | 6 | 7 | 9 | 6 | 40.0 | KEEP |
| 45 | Backtest Engine | 6 | 7 | 9 | 6 | 40.0 | KEEP |
| 46 | Unified Learning Brain (merged) | 6 | 7 | 8 | 7 | 39.5 | MERGE |
| 47 | SWING Mode V2 Engine | 6 | 7 | 7 | 7 | 38.5 | KEEP |
| 48 | Baseline Engine | 6 | 6 | 9 | 6 | 38.0 | KEEP |
| 49 | Breakout Mode (ORB) | 6 | 6 | 7 | 6 | 36.5 | KEEP |
| 50 | Decision Quality Analytics | 6 | 6 | 7 | 6 | 36.5 | KEEP |
| 51 | Trade Idea Review | 6 | 6 | 9 | 4 | 36.0 | KEEP |
| 52 | Cross-Market Index Alignment | 5 | 6 | 9 | 5 | 35.5 | KEEP |
| 53 | Scalp Strategy Advisory (merged) | 5 | 6 | 8 | 6 | 35.0 | MERGE |
| 54 | TradeZella Integration | 5 | 6 | 9 | 5 | 35.0 | KEEP |
| 55 | Live 2-Contract Runner | 6 | 5 | 7 | 6 | 34.0 | KEEP |
| 56 | FVG / OB Analyst Evidence | 5 | 5 | 9 | 6 | 33.5 | HIDE |
| 57 | Entry Quality Location Engine | 5 | 5 | 8 | 6 | 33.0 | KEEP |
| 58 | Databento Live Feed | 5 | 4 | 8 | 5 | 31.5 | KEEP |
| 59 | Diagnostics & Observability | 5 | 2 | 9 | 7 | 26.5 | HIDE |
| 60 | Manual Desk Order | 4 | 4 | 7 | 4 | 26.0 | HIDE |
| 61 | Dual-TF Engine | 4 | 5 | 7 | 5 | 25.5 | HIDE |
| 62 | Analysis Bot (Second Instance) | 4 | 2 | 8 | 5 | 22.5 | HIDE |
| 63 | Micro Scalp Mode | 3 | 4 | 6 | 4 | 20.5 | HIDE |
| 64 | Decision Pipeline V2 (Shadow) | 3 | 1 | 5 | 4 | 14.5 | HIDE |
| — | Left Brain Observation Buffer | 3 | 2 | 9 | 3 | 19.5 | HIDE |

---

---

# TASK 8 — VERSION 1 DEFINITION

## The AI Trading Partner — Version 1.0

---

### What Version 1 Is

Version 1 of AI Trading Partner is a professional trading desktop, journal, coach, partner, and market expert — in a single application — designed for a trader who knows the basics and wants to trade better.

It is not a signal service. It is not a black box. It does not hide what it is doing.

It is an expert partner that watches the market alongside the operator, explains everything it sees, makes a clear recommendation, executes when told to, manages the trade while it is open, records everything when it closes, and teaches the operator what to do differently next time.

Every capability that is live, tested, and directly useful to an operator during their trading day is included. Nothing that is purely diagnostic, research-only, or not yet ready for a live user is shown in the primary view.

---

### What a Trader Experiences

When someone opens AI Trading Partner, they immediately see six things without scrolling or clicking:

**1. What the market is doing**

The market thesis is on screen. One sentence. "MGC is in a bullish structure above VWAP. The demand zone at 2,648 has been defended twice this session. Thesis: long setups are valid while price holds above 2,651."

This is not a list of indicators. It is a statement. The trader can understand it without knowing what VWAP or CHOCH means — the platform defines terms when it uses them.

**2. Why**

Directly beneath the thesis: "Why: Price held the daily VWAP after a bearish open. Buyers stepped in at the demand zone with increasing volume. A Change of Character (shift from lower highs to higher highs) confirmed at 10:15 ET."

**3. What opportunities exist**

The best current setup is surfaced automatically. If MGC is forming a setup and MNQ is quiet, MGC is selected. The setup state is shown: "READY — Grade A+" or "Building — waiting for volume confirmation (+15 points needed)." The specific conditions that are met and the one or two that are missing are listed plainly.

**4. What action is recommended**

A single recommended action with reasoning. If there is a setup: "Enter long near 2,649. Stop at 2,644.50. Target at 2,659. Risk: $140 per contract. This is a high-quality setup — all conditions are confirmed."

If there is no setup: "Wait. The structure condition has not been met yet. Watch for a CHOCH above 2,651 to trigger the setup."

If the trader should not be trading right now: "High-impact news in 8 minutes (FOMC). The platform recommends waiting until the initial reaction settles (approximately 14 minutes after release)."

**5. How active trades are performing**

When a trade is open, the active trade panel is the most prominent element on screen. Current P&L in R and in dollars. Whether the trade thesis is still valid. What the platform recommends doing right now. What level would signal that the trade should be exited early.

This disappears when there is no open trade. The operator is never distracted by phantom trade management panels when there is nothing to manage.

**6. What they learned today**

At the end of the session — or at any time via the Coach screen — the operator sees a simple performance card. Trades taken, grades, results, and one coaching observation drawn from their actual history. Not a generic tip. A specific observation based on how they have been trading.

---

### The Five Screens of Version 1

**Operator Mode** is where the trader spends 90% of their time. It has one job: tell the trader what to do right now and let them do it. Everything that is not relevant to the current moment is collapsed or absent.

**Engineering View** is where the operator goes when something unexpected happens and they need to understand why. Every component, every gate, every score is visible here. It is not a trading screen — it is a diagnostic screen.

**Research & Backtest Lab** is where the operator tests ideas without risk. Upload history, run a backtest, review what worked. The platform's 16 live research strategies are also visible here — the operator can see which ones are performing well on the live stream right now.

**Coach & Academy** is where the operator learns. Study the curriculum, grade a hypothetical trade, review what went wrong in recent losses, track the thesis patterns that have been most reliable. The platform teaches while the trader is not trading.

**Journal & Performance** is the permanent record. Today's trades, the equity curve, and a simple performance summary. What was sent to Discord. What the results look like net of commission and slippage (the realistic picture, not the optimistic one).

---

### The Six Roles in Version 1

Version 1 is clear about which of the platform's six roles is doing what at any moment.

**The Desktop** gives the trader a clean, professional workspace. It does not overwhelm. Panels collapse. The most important information is always visible. The information that is not currently relevant is out of the way.

**The Expert** does the technical analysis. It watches every signal that arrives from TradingView, maintains the market state, runs the gate, scores the edge, and publishes a READY or WAIT verdict. The operator does not need to know how this works — but if they want to know, the gate conditions are labeled, explained, and visible.

**The Partner** translates the Expert's output into human language. It is the voice of the platform. It synthesizes the Bull case, the Bear case, the analyst's game plan, the market thesis, and the memory of similar past setups into a single coherent statement. It speaks with the operator, not at them.

**The Coach** helps the operator improve over time. It uses the record of everything that has happened to identify patterns: which setup types have worked, which hours are best, what the trader's most common mistake is. It does not evaluate performance in the moment — it reviews the record after the fact and offers one clear observation at a time.

**The Journal** keeps the permanent record without requiring any manual entry. Every READY setup fires a trade card to Discord. Every trade close is recorded. At the end of the day, the EOD report is automatic. The operator can annotate if they want to — but they do not have to.

**The Manager** handles execution. When the setup is READY and the operator presses ENTER, the Manager routes the order, arms the exit targets, watches the position, and closes when the plan says to close. When the trader wants full autonomy, the Manager executes silently in the background. When the trader wants oversight, the Manager surfaces every decision before making it.

---

### The V1 Contract

Version 1 of AI Trading Partner commits to the following experience:

The operator opens the application and within 10 seconds knows: whether the market is open, what the current thesis is, whether there is a setup forming, and what the recommended action is.

Every WAIT state names the specific reason. No unexplained waiting.

Every READY state includes the plan: entry, stop, target, R, size.

Every active trade shows: whether the thesis is still valid, current P&L, and what to do right now.

Every closed trade shows: what happened, whether it was within normal variance, and one learning note.

The platform teaches without lecturing. Every technical term it uses is defined in place.

The platform never hides a concern. Vetoes are visible. Flags are labeled. Disagreements between analysis layers are shown, not suppressed.

The platform never makes a decision without being asked to. ENTER requires operator confirmation unless auto-trade is explicitly armed. Arming is in-memory and resets on every restart.

---

### What Version 1 Is Not

Version 1 does not include:

- Voice output from the avatar
- Proactive push notifications to a mobile device
- A calendar heatmap view of monthly P&L
- Chart screenshots auto-captured at entry
- Real broker balance feed integration
- Prop firm account setup UI (available via API but no configuration screen in V1)
- Decision Pipeline V2 live stages (all five stages run in shadow mode; no operator interaction)
- Micro Scalp Mode in the primary operator view (available via Engineering View flag)
- Dual-TF Engine in the primary operator view (available via Engineering View flag)

These are not failures. They are the defined scope of V1. Each is documented in the gap analysis with a clear path to inclusion in V2.

---

### The One-Sentence Description

**AI Trading Partner V1 is a professional trading platform that watches the market alongside you, tells you exactly what is forming and why, executes when you say go, manages the trade while it is live, records everything when it closes, and teaches you what to do better — in plain language, every step of the way.**

---

*Version 1.0 Product Specification — AI Trading Partner*
*Produced July 2026 — NO CODE | NO REFACTOR | NO IMPLEMENTATION*
*This document is the official Version 1 product definition.*
