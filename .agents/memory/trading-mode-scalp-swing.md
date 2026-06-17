---
name: SCALP/SWING trading mode
description: Why the TradingView webhook has two sensitivity profiles and the invariants any future scoring change must preserve.
---

# SCALP vs SWING mode (tradingview-webhook/app.py)

A `MODES` dict + `TRADING_MODE` env (default **SCALP**) + `cfg(key)` accessor gate every
sensitivity threshold. Never reintroduce module-level scoring constants — read them through
`cfg()` so both profiles stay consistent.

**Why:** The user got *constant WAIT* during fast markets because the old (swing-only)
thresholds were too strict for scalping. SCALP loosens bias/distance/confidence thresholds,
windows scoring to recent alerts, and lets BOS-only "Attempt" setups become tradable.

**Invariants to preserve on any future scoring/decision change:**
- **SWING must stay behaviorally identical to the pre-mode code.** It keeps the old conf tiers,
  `MIN_TOTAL_SCORE=0`, full-history scoring, last-5 stage recency, and `ATTEMPT_TRADABLE=False`
  (Attempts → WAIT). If a SWING regression test stops returning WAIT on a bare Attempt, you broke it.
- **Attempts are reduced-risk.** BOS-only entries size at `RISK_MULT_ATTEMPT` (0.5 in SCALP).
  This guard must be applied at **every** sizing call site that can open an Attempt trade —
  both the `/webhook` embed site AND the `/enter` auto-plan path. Forgetting one lets a trade
  open at full size while the embed shows half — they must agree.
- **Attempts never reach top conviction.** Decision logic maps Attempt → trend_class but caps the
  recommendation at BIAS/TRADE, never STRONG/HIGH CONVICTION. `MIN_TOTAL_SCORE` is a confluence
  floor so a single BOS can't alone hit the TRADE tier.
- **Volatility is gate-vs-modifier by mode (`VOL_HARD_GATE`).** SWING hard-gates on a BLOCK regime
  (WAIT) and adds 0 to the Edge Score; SCALP never gates on volatility and instead folds it into the
  score (Normal +10 / Elevated 0 / Extreme −10). See volatility-monitor-gate for the full contract.
  Any new safety layer added in one mode must declare its mode behavior the same way (a `cfg()` key),
  never a hardcoded gate.

**MGC vs MNQ symmetry trap:** zone-confirmed / confirmation checks in `get_setup_stage` must match
**both** instruments' alert strings. A past bug hardcoded only the `MGC ...` variants, so MNQ could
never advance past "Setup Forming". Any per-instrument string match here needs both symbols.

**Verification without spamming Discord:** import `app` as a module, populate `app.ALERT_HISTORY`
(records: alert_type/ticker/price/timestamp-ISO) + `app.CURRENT_PRICE`, toggle `app.TRADING_MODE`,
and call `app.full_analysis()` directly. Discord only fires on a real `/webhook` POST, so direct
calls are side-effect-free. The final verdict is decided in `full_analysis` (trade_plan + setup_stage),
which overrides `decision_engine`.
