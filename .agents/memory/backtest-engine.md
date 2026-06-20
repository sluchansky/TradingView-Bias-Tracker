---
name: Read-only backtesting engine
description: Invariants any change to the /backtest/* engine (backtest_engine.py + app.py routes + Express scoping) must preserve.
---

# Read-only backtesting engine

A research-only strategy backtester lives in `backtest_engine.py` (pure, no app
imports) + `/backtest/*` Flask routes in `app.py` + Express proxy plumbing. It
replays historical CSV candles through Python re-implementations of the
indicators/strategies. It is deliberately walled off from the live money path.

## Money-path isolation (hard, non-negotiable)
The engine must NEVER call `evaluate_strict_setup()` / `full_analysis()` (they read
live globals), mutate any live global, post Discord, write `strategy_trades`, or hit
the broker/traderspost. Stop math is a COPIED pure helper, not a refactor of the
live one. Strategy weights are read-only — backtesting never auto-tunes them.
**Why:** this is a live trading app; a backtest that touched live state could move
real money or corrupt live signals.

## "INSERT/SELECT only" — what it actually means here
The stated constraint is shorthand for "no runtime DDL + never mutate live/money
tables." The backtest's OWN isolated tables legitimately use UPDATE (async run
status/progress on `backtest_runs`) and DELETE (`DELETE /backtest/datasets/<id>`,
an explicitly-specified route). Those are part of the approved design, not a
violation. DDL stays external (database tool in dev, Publish diff in prod).
**How to apply:** don't "fix" the run-status UPDATE or dataset DELETE into
insert-only; do keep CREATE/ALTER/DROP out of app runtime.

## No look-ahead / worst-case fills
Indicators are causal (only bars up to & incl. current; pivots confirmed after
`PIVOT_RIGHT` bars). Entry is ONLY at next-bar-open after a close-confirmed signal.
Same-bar collisions resolve WORST-CASE, stop first. This includes the runner: the
bar where TP1 fills, if that same bar also revisits breakeven, the runner is closed
at BE on that bar (don't optimistically carry it to the next bar). See `_walk_trade`.

## Express raw-body scoping (DoS surface)
Large request bodies are needed by exactly ONE endpoint: `/api/backtest/upload`.
Scope the big `express.raw` limit to that path only and keep a tight global cap
(1mb) for every other `/api` path. A global multi-MB cap buffers large
unauthenticated bodies before auth runs — an availability/DoS surface.
**Why:** auth lives inside the `/api` router (runs after body parsing); a global
big buffer can't be gated by it. Note `app.use("/api/backtest/upload", dashboardAuth)`
would mis-fire because mount-relative `req.path` becomes "/" which is in OPEN_PATHS.

## Auth
All `/backtest/*` routes are owner-only: present in the `flask-proxy.ts` whitelist,
absent from `dashboard-auth.ts` OPEN_PATHS (`/`, `/ping`, `/webhook` only).

## Honesty
Signal-agreement panel must report "unavailable" when no live signals were captured
(no forward-capture log yet) rather than fabricating agreement numbers. Engine is an
*approximation* of the TV indicators (BOS/CHOCH/zones reconstructed via pivot SMC);
confirmation candle is per-bar, exact only for 5m datasets.
