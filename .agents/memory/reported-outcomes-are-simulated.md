---
name: Reported outcomes are simulated, not real broker fills
description: Why the bot's strategy_trades/journal/learning/dashboard win-rate is a proxy-feed simulation that can diverge wildly from the real broker account.
---

# Reported outcomes are a SIMULATION, not real fills

The bot's `strategy_trades` rows (and everything fed from them — journal, learning
engine, dashboard P&L/win-rate) are **simulated outcomes**, not the broker's real
results.

- Execution is genuinely LIVE when `TRADERSPOST_WEBHOOK_URL` is set and
  `EXECUTION_MODE` is unset (`resolve_execution_mode()` → `traderspost`). Real
  orders go to TradersPost → Tradovate.
- But TradersPost is **send-only**: no fill price ever flows back. `_record_strategy_trade`
  comments this explicitly ("none flows back today") and leaves `slippage` NULL.
- The managed-trade lifecycle decides Win/Loss by watching a **separate public proxy
  feed** (MGC≈`GC=F`, MNQ≈`NQ=F`, etc. via the free VWAP/bar fetch) touch the plan's
  target/stop. So every recorded row exits at *exactly* target (Win) or *exactly*
  stop (Loss), ±1.00R, zero slippage — the tell-tale fingerprint of simulation.

**Why this matters:** the dashboard can show ~70% wins while the real account is deep
red. Observed 2026-06-29: bot DB booked the morning as mostly wins; the TradeZella
export of the same window was 6W/20L, −$365, full of 0-second instant-reversal churn
trades the bot never recorded.

**How to apply:**
- NEVER treat the bot's own DB / dashboard scoreboard as real performance. To judge
  live results, reconcile against the broker (TradeZella / Tradovate).
- The sim-vs-real gap is *amplified* by: tiny SCALP targets (MES 3pts, MGC 3pts) where
  proxy-feed latency alone flips the outcome; overtrading/stacking (every-bar
  CONFIRMATION webhooks + `allow_stack`) which churns the real account far more than
  the idealized sim; SCALP not gating volatility (real fills happen in violent chop the
  sim glides through); and 1:1 R:R on micros being negative-expectancy after
  fees+slippage.
- Any future "the bot is winning" claim sourced from `strategy_trades` is a simulation
  claim until reconciled with real fills.
- The dashboard now makes this explicit: a **Real Account Results** panel
  (`#mod-real-results`) renders the `scoreboard` from `/tradezella/analysis` (real
  broker fills imported via TradeZella CSV), and the equity/today's-trades/learning
  panels carry an amber **SIMULATED** badge. Real P&L = that scoreboard ONLY; don't
  "fix" the simulated panels to look real, and never wire the sim into a money decision.
