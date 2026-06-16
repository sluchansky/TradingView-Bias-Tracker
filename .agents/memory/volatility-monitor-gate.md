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
- **Two-tier behavior:** CAUTION (mildly out of band) → Edge Score −5 risk line + a visible warning label on the card/dashboard, still tradable. BLOCK (extremely dead or wild) → forces both gates false so an otherwise-READY setup becomes WAIT.
  - A BLOCK is SILENT by the user's explicit choice: WAIT never journals or posts a card/ping, so the dashboard is the only surface that shows a held setup. Do NOT add a "held" Discord notice unless the user asks.
- The −5 CAUTION penalty can be visually masked by the 75 READY-floor on minimum-score setups, but the warning label always shows; this is intentional and consistent with every other risk adjustment living under the same floor.
- Thresholds are mode-aware via `cfg()` (SCALP tighter, SWING looser).

**How to apply:** any future change to the strict gate or Edge Score must preserve fail-open + per-instrument isolation. Never make volatility fail-closed, and never introduce a global "current volatility" value.
