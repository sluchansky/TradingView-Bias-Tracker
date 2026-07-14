---
name: Swing Mode V2 engine
description: Flag-gated HTF swing scoring + lifecycle system; display-only; money path untouched.
---

## Rule
SWING_MODE_V2_ENABLED=1 (default OFF) unlocks the full engine. Flag OFF → key never attached → goldens byte-identical.

**Why:** Pure display/advisory layer that needed zero gate/sizing/execution impact. Default-OFF is the only safe way to ship a large display module without disturbing the 4 goldens.

**How to apply:** Any new Swing V2 sub-feature must: (1) sit behind the same flag, (2) be fail-open (no raise), (3) never touch the money path, (4) add its key to the /status whitelist if the dashboard needs to poll it.

## Data architecture
- **Tier 1** (always live): HTF_STATE_BY_INST, gate_debug, CVD/RVOL stores, zone data, VWAP.  
  Scores 0 honestly for momentum categories when Tier-2 not connected.
- **Tier 2** (Pine script): EMA20/50/200 D/4H/W, ATR-D, RSI-D, MACD-D histogram, ADX-D.  
  Ingested via `SWING_EMA_UPDATE` webhook payload → `SWING_V2_STATE_BY_INST[inst]`.  
  Pine script lives at `artifacts/tradingview-webhook/pine_scripts/swing_v2_ema_momentum.pine`.

## State dicts
- `SWING_V2_STATE_BY_INST`     — per-instrument Pine Tier-2 data + ts
- `SWING_V2_LIFECYCLE_BY_INST` — per-instrument lifecycle state (status/score/grade/reason/ts)

## 9 score categories (100 pts max)
HTF Trend(20) + Market Structure(15) + Entry Location(15) + Momentum(10) + Volume(10) + Correlation(10) + Risk:Reward(10) + Volatility Quality(5) + Event Risk(5)

Grade: ≥75=READY, ≥65=WATCHING, ≥50=WEAK, <50=NO TRADE.

## Lifecycle states
SCANNING → WATCHING → SETUP FORMING → READY (or NO TRADE on hard blocks).

## Hard blocks (always enforced)
- R:R < 2.0
- No BOS/CHOCH (no structural invalidation level)
- Price excessively extended (>1.75 ATR from EMA20-D / VWAP proxy)
- Daily HTF data stale (>36h)

## Routes
- `/swing-analysis?ticker=MNQ` — dedicated per-instrument GET endpoint (auth-gated, not in OPEN_PATHS)
- `/status` — `swing_v2` key whitelisted alongside `breakout_mode`
- `/webhook` — `SWING_EMA_UPDATE` alert type handled before ALERT_TYPES check; returns early

## Dashboard
- Panel: `#mod-swing-v2` (hidden when flag OFF)
- JS function: `renderSwingV2(d)` called after `renderBreakoutMode(d)`
- CSS classes: `.sv2-row`, `.sv2-lbl`, `.sv2-sec`, `.sv2-bd-*`
