---
name: Volatility monitor gate
description: per-instrument volatility layer in the webhook app — fail-open contract, two-tier flag/hold behavior, and invariants any scoring/gate change must keep
---

A volatility layer sits between market data and the strict gate in `artifacts/tradingview-webhook/app.py`.

- **Metric:** recent 1m ATR (mean of last ~14 true ranges) ÷ session-typical range (median of all session TRs). The *ratio* is self-normalising, so one threshold set works for both MGC and MNQ despite very different absolute point sizes. Reads from the same Yahoo 1m feed as VWAP.
- **FAIL-OPEN (critical):** missing / stale / error / partial-bar volatility must NEVER block a trade — it resolves to `status != ok`, `blocked=False`, `caution=False`.
  - **Why:** this is the OPPOSITE of VWAP, which fail-CLOSES (blocks) because price-vs-VWAP is a *required* gate condition. Volatility is an *extra* safety gate; blocking every setup on a transient feed hiccup is unacceptable for a live trader.
- **STRICTLY per-instrument:** state is keyed by instrument with NO global fallback, and reads use the analyzed instrument only.
  - **Why:** prevents the recurring MGC↔MNQ bleed bug class (see zone-mitigated-detection).
- **Mode-aware gate vs modifier — the central rule (`VOL_HARD_GATE` in `MODES`):**
  - **SWING (`VOL_HARD_GATE=True`):** classic hard gate. A BLOCK regime (Wild/too-volatile = `HIGH_BLOCK`, or Dead/too-quiet = `QUIET_BLOCK`) forces the gates false so an otherwise-READY setup becomes WAIT. `_vol_regime_score_adj` returns **0** in this mode, so volatility never touches the Edge Score (no double-counting: it gates instead).
  - **SCALP (`VOL_HARD_GATE=False`):** volatility is **NOT a gate AND does NOT change the Edge Score** — it is currently **display/diagnostic ONLY**. `_vol_regime_score_adj` still returns Normal `+10` / Elevated CAUTION `0` / Extreme BLOCK `−10`, but that `score_adj` is consumed ONLY by `_vol_diag_detail` (the `volatilityDecision … (Edge ±N)` line) and the terse gate log (`volAdj=±N`); it is NEVER passed to `_edge_modifiers()` / `compute_trade_edge_components()`. The local `vol_adj = int(vol.get("score_adj") or 0)` in `evaluate_strict_setup` is assigned but unused (dead code). So in SCALP, volatility neither blocks nor docks a setup — the displayed `volAdj` / `Edge -N` is cosmetic. **Do not assume that number affects the gate; it does not — verify the wiring before trusting it.**
  - **Why:** the user wanted SCALP to keep trading through fast markets — volatility should shade conviction, not veto setups — while SWING keeps the protective veto.
- **Single source for gate + display:** `full_analysis()` calls `get_volatility()` ONCE and stores it on `result["volatility"]`; the SAME dict reaches `evaluate_strict_setup` (in SWING `blocked` hard-gates; `score_adj` is read only for the log). The Edge Score for BOTH gate and display is built by `compute_trade_edge_components(signals, modifiers)` where `modifiers` = the SOFT list (location / CVD-conflict / cooldown) — volatility is NOT among them, and `compute_trade_edge_components` takes no vol argument. Never wire volatility into the Edge without an explicit money-path decision (it would change trade frequency).
- The Edge Score is the additive components + soft modifiers, clamped to `[0, EDGE_SCORE_MAX]`; volatility is NOT part of that sum (see above). There is no 75-floor (removed app-wide).
- BLOCK / WAIT is SILENT by the user's explicit choice: WAIT never journals or posts a card/ping, so the dashboard is the only surface that shows a held setup. Do NOT add a "held" Discord notice unless the user asks.
- Thresholds are mode-aware via `cfg()` (`VOL_HIGH_CAUTION` = elevated, `VOL_HIGH_BLOCK` = extreme; SCALP 1.6/2.5, SWING looser).
- **Diagnostic display:** `format_gate_diagnostic`/`_vol_diag_detail` render currentATR (`atr_pts`), baselineATR (`baseline_pts`), volatilityMultiplier (`ratio`), volatilityThreshold (elevated/extreme), volatilityDecision (`label` + Edge adj). The terse Alert log appends `vol=BLOCK` (SWING gate) or `volAdj=±N` (SCALP modifier).

**How to apply:** any future change to the strict gate or Edge Score must preserve fail-open + per-instrument isolation, and must keep the SWING-gate / SCALP-modifier split driven by `VOL_HARD_GATE` (never let SCALP veto on volatility, never let SWING double-count it in the score). Never make volatility fail-closed, and never introduce a global "current volatility" value.
