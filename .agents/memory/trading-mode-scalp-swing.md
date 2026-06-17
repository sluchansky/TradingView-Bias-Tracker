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
- **The READY gate itself is now mode-tunable** via `GATE_REQUIRE_ZONE/VWAP/STRUCTURE`, `MIN_CONFIRMATIONS`
  and the edge floors (SWING keeps zone/vwap/structure as hard gates @ Edge≥80, single-tier; SCALP demotes
  all three to confirmations and is **two-tier**: an *actionable* floor (EARLY READY) below a *full* READY
  floor). The old "gate byte-for-byte unchanged" rule applies to **SWING only** now — see
  strict-trade-ruleset.md for the full per-mode formula.
- **SCALP readiness is two-tier; EARLY READY still endswith "READY".** SCALP emits `LONG/SHORT EARLY READY`
  for the actionable-but-below-full band so existing dispatch/journal/dashboard treat it as a live setup —
  but it is deliberately labeled. **Why:** any consumer that pattern-matched the verdict string would
  silently mis-handle the new tier. **How to apply:** never compare a verdict to the literal
  `"LONG READY"/"SHORT READY"`; route every check through the shared verdict helpers
  (`is_actionable`/`is_full_ready`/`is_early_ready`/`ready_direction` and their JS twins) so EARLY READY
  is classified consistently. SWING never emits an EARLY tier.
- **Conflict resolution is mode-split.** SWING always stands aside when opposing structure is present on
  both sides (WAIT). SCALP is score-aware: it WAITs only when the two sides are balanced
  (Edge gap ≤ `CONFLICT_WAIT_GAP`); otherwise it commits to the dominant (higher-Edge) side, which still
  must clear readiness/trade-plan plus the zone-broken/zone-mitigated/market-closed overrides. **Why:** a
  clearly dominant directional bias shouldn't be vetoed by a stale opposite structure. Don't let SCALP's
  dominant-side path leak into SWING.
- **Per-instrument de-dup cooldown** (`signal_dedup_cooldown_sec`): only MNQ-resolving tickers get the
  short value; MGC and *every unknown ticker* (instrument_of defaults to MGC) get the longer, more
  conservative value. A global single-knob env override wins for all instruments.

**MGC vs MNQ symmetry trap:** zone-confirmed / confirmation checks in `get_setup_stage` must match
**both** instruments' alert strings. A past bug hardcoded only the `MGC ...` variants, so MNQ could
never advance past "Setup Forming". Any per-instrument string match here needs both symbols.

**Verification without spamming Discord:** import `app` as a module, populate `app.ALERT_HISTORY`
(records: alert_type/ticker/price/timestamp-ISO) + `app.CURRENT_PRICE`, toggle `app.TRADING_MODE`,
and call `app.full_analysis()` directly. Discord only fires on a real `/webhook` POST, so direct
calls are side-effect-free. The final verdict is decided in `full_analysis` (trade_plan + setup_stage),
which overrides `decision_engine`.
