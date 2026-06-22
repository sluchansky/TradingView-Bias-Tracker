---
name: Multi-strategy engine (regime → strategy selection)
description: Phase rollout, display-vs-control authority model, and invariants for the strategy engine in artifacts/tradingview-webhook/app.py
---

# Multi-strategy engine

Detects market regime (TRENDING/RANGING/VOLATILE/BALANCED), computes session bias
(Asia/London/NY ET), scores 5 strategies and selects the best-fit by a FIXED priority,
then surfaces confidence%, quality grade, READY reason, missing confirmations, expected R.

## Authority model (the core rule)
- Rollout is gated by env `STRATEGY_ENGINE_MODE = display (default) | control`.
- **display** = engine computed + shown only; the existing strict gate stays the sole
  authority over verdict/directions/trade_plan/edge_score/money path.
- **control** (Phase 2) = selected strategy's readiness feeds the verdict, but EVERY
  global safety control still applies AFTER it (market-open/holiday override, valid stop
  required, risk sizing, dedupe cooldown, session filter, single audited /traderspost
  gateway, fail-closed). Strategies adapt entries; they never touch risk controls.
- **Why:** the engine must be able to ship and be observed live without any risk to the
  money path; control is flipped only after live display-mode validation.

## Strategy priority (fixed; tie-break = priority order)
1 Opening Drive (8:00–10:00 ET ONLY) · 2 Liquidity Sweep Reversal · 3 VWAP Trend
Continuation · 4 Range Expansion Breakout · 5 Opening Range Breakout (ORB).
ORB **replaced Exhaustion Fade** in the LIVE engine; the SEPARATE backtest module keeps its own
Exhaustion Fade detector (different code path — do NOT "fix" that to ORB).
Selection: among fully-met strategies pick the lowest priority number; else highest
completeness with priority tiebreak.

## Invariants any change must keep
- Engine is computed AFTER `result["directions"]` is built and is wrapped in a broad
  fail-open try/except — an engine error must degrade the panel, never break full_analysis.
- The market-closed override path must emit a key-parity `strategy_engine` block
  (same shape as the open path) or hard-indexed /status consumers see None / 500.
  (See full-analysis-return-parity + curated-endpoint-serialization.)
- A new engine field must be added to the /status whitelist dict too, or it's None on the wire.
- Instrument-scoped state only: per-ticker trackers/blockers must not zero the other instrument.
- INTRADAY_BY_TICKER is mutated under INTRADAY_LOCK (incl. the /clear reset); the alert-history
  scan snapshots the deque before iterating (webhook worker mutates it concurrently).

## ORB 1:4 target override — the engine's ONLY money-path effect
Even in `display` mode the engine has ONE sanctioned money-path effect: `_apply_orb_target_override`
retargets a truly-ready ORB plan from 1:1 to 1:4 (full detail in fixed-1to1-rr.md). Gated on
`strategy_engine.ready` (== active strategy `fully_met`) — the fallback `active_key` (highest
completeness when nothing is fully met) must NOT earn 1:4. All other strategies stay fixed 1:1.

## Fidelity caveat
No full OHLC feed: "close outside range" / "rejection candle" / "continuation candle" are
APPROXIMATED from CONFIRMATION alerts + sweeps + price ticks. Confidence is lower when a
close-confirmation is absent; the dashboard panel states this explicitly.
