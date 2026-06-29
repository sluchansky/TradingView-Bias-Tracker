---
name: Simulation realism overlay
description: Why/where the dashboard scoreboard is net-of-cost; the display-only boundary it must never cross.
---

# Simulation realism overlay (SIM_REALISM_*)

The dashboard scoreboard — equity curve, win rate, Today's Trades — is a SIMULATION
off a free price proxy with idealized fills (exits land exactly at target/stop, zero
cost). It is structurally rosier than the real account: 1:1 micros show ~70% wins on
the proxy while the real account loses, because after costs a 1:1 micro is a net loser.

The overlay subtracts an estimated round-trip commission + per-side slippage, expressed
in R against each trade's OWN risk, so the displayed numbers track reality:

    cost_$ per contract = commission_per_side*2 + slippage_ticks*tick*pv*2
    risk_$ per contract = |entry - stop| * pv
    cost_R              = cost_$ / risk_$        (contracts cancel — R is per-contract)
    net_R               = raw_R - cost_R

Default ON; env kill-switch SIM_REALISM_ENABLED=0/off; tunable
SIM_REALISM_COMMISSION_PER_SIDE / SIM_REALISM_SLIPPAGE_TICKS.

**Where:** applied in EXACTLY ONE place — `get_today_equity_curve`, the single source
that feeds both the equity panel and the Today's Trades list via `equity_curve_today`
(re-buckets wins/losses + cum + per-point r; preserves raw_r/raw_result; adds
realism_applied). Existing keys keep flowing through the `/status` whitelist, so no
serialization change is needed.

**Boundary (NEVER cross):** does not touch compute_pnl, journal pnl_dollars, stored
strategy_trades rows, the gate / gateway / sizing / dedupe / broker, or the daily-loss
safety gate — all byte-identical. Real P&L remains the TradeZella "Real Account Results"
panel ONLY. A *net-of-cost daily-loss gate* would be a separate, explicit opt-in flag —
do not fold costs into the money path here.

**Why:** display-only is the architect-approved safe boundary — it fixes the misleading
scoreboard without altering a single money-path number.

**Gotchas:**
- Resolve the instrument STRICTLY via `_instrument_from_text` (returns None on
  unknown/ambiguous), NOT the lenient `instrument_of`/`point_value_for` which default to
  MGC — otherwise an unknown symbol silently borrows MGC's tick/point value. Helper fails
  OPEN (returns raw R) on any missing/invalid entry|stop|risk|instrument.
- Goldens pin SIM_REALISM_ENABLED=0 for insurance even though the overlay is display-only
  and goldens exercise scoring (not the equity display); guarded by check_sim_realism.sh.
