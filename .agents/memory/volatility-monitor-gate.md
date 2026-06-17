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
  - **SCALP (`VOL_HARD_GATE=False`):** volatility is **NOT a gate** — it is an Edge Score modifier only. `_vol_regime_score_adj`: Normal `+10`, Elevated CAUTION (`HIGH_CAUTION`/`QUIET_CAUTION`) `0`, Extreme BLOCK (`HIGH_BLOCK`/`QUIET_BLOCK`) `−10`. A strong setup can still go READY in high vol; elevated/extreme ATR can never force WAIT on its own. The `−10` is the only effect (it is NOT also a separate `−5` risk line — that older model is gone).
  - **Why:** the user wanted SCALP to keep trading through fast markets — volatility should shade conviction, not veto setups — while SWING keeps the protective veto.
- **Single source for gate + display:** `full_analysis()` calls `get_volatility()` ONCE, passes that dict to `evaluate_strict_setup` (gate reads `score_adj`/`blocked`) AND stores it on `result["volatility"]` (display reads the SAME `score_adj` via `compute_edge_breakdown`). `compute_trade_edge_components(signals, vol_adj)` appends the Volatility line and clamps 0–100. Never recompute the adjustment in two places.
- The vol modifier flows through the additive Edge Score and is clamped 0–100, so `−10` can't go negative and `+10` Normal can't exceed 100. There is no 75-floor (removed app-wide).
- BLOCK / WAIT is SILENT by the user's explicit choice: WAIT never journals or posts a card/ping, so the dashboard is the only surface that shows a held setup. Do NOT add a "held" Discord notice unless the user asks.
- Thresholds are mode-aware via `cfg()` (`VOL_HIGH_CAUTION` = elevated, `VOL_HIGH_BLOCK` = extreme; SCALP 1.6/2.5, SWING looser).
- **Diagnostic display:** `format_gate_diagnostic`/`_vol_diag_detail` render currentATR (`atr_pts`), baselineATR (`baseline_pts`), volatilityMultiplier (`ratio`), volatilityThreshold (elevated/extreme), volatilityDecision (`label` + Edge adj). The terse Alert log appends `vol=BLOCK` (SWING gate) or `volAdj=±N` (SCALP modifier).

**How to apply:** any future change to the strict gate or Edge Score must preserve fail-open + per-instrument isolation, and must keep the SWING-gate / SCALP-modifier split driven by `VOL_HARD_GATE` (never let SCALP veto on volatility, never let SWING double-count it in the score). Never make volatility fail-closed, and never introduce a global "current volatility" value.
