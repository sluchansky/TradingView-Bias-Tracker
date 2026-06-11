---
name: VWAP auto-fetch
description: How/why the webhook app sources VWAP automatically and the precedence rules any change must preserve.
---

# Automatic VWAP for the strict price-vs-VWAP gate

The webhook app computes session VWAP server-side so the operator never has to type
it. The operator explicitly chose "auto, accepting it may differ slightly from the
chart VWAP" over a one-time TradingView alert setup.

## Durable decisions (keep consistent)

- **Source equivalence:** MGC (micro gold) ≈ GC=F, MNQ (micro Nasdaq) ≈ NQ=F. Micro
  and full contracts trade at the same price level, so their VWAP is interchangeable
  — that is why the public feed uses the full-size symbols.
  **Why:** there is no free reliable micro-futures intraday feed; the full-size one
  gives the same VWAP.

- **VWAP is approximate, not the chart's exact value.** It is computed from 1-minute
  typical-price × volume over the current trading day from a public feed. It can
  differ from TradingView's VWAP (different session anchor) and may occasionally flip
  a near-the-line price-vs-VWAP signal. The operator accepted this tradeoff.

- **Precedence: exact operator value wins briefly, auto keeps it fresh.** A VWAP
  pushed from a TradingView chart or typed manually is tagged `source:"chart"` and is
  honored for a short grace window; within that window the background fetch must NOT
  overwrite it. After the window, auto resumes. A continuously-pushing chart alert
  therefore always wins (each push resets the grace).
  **How to apply:** any change to VWAP sourcing must keep this ordering and must keep
  the staleness guarantee below.

- **The strict gate must never trade on stale VWAP.** `get_vwap` still returns
  `stale`/`missing` past its age window regardless of source; the background loop's
  job is to keep the value fresh, not to bypass the staleness check.

- **Only VWAP is auto-sourced, not current price.** Current price stays driven by the
  live TradingView alerts (more real-time than the public quote). Do not auto-fill
  CURRENT_PRICE from the same feed — it would fight the alert-driven price.

- **The fetch loop must never die or block requests.** It runs on a rescheduling
  timer (mirrors the heartbeat loop), swallows all errors, and a failed fetch leaves
  the previous value untouched rather than clearing it.
