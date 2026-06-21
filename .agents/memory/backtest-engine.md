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

## Research no-trade filters & tradable metric (config-only, never money path)
The engine has research-only knobs: `DISABLED_STRATEGIES` (Exhaustion Fade is
disabled and excluded even when explicitly requested in `strategies=[...]`),
`MAX_TRADES_PER_SESSION` (per strategy, bucketed by `_session_for_et` session-day),
`MIN_TARGET_R`, `NEWS_BLACKOUTS_ET`, and an extreme-volatility skip. All filters are
CAUSAL: evaluated AFTER signal detection but BEFORE next-bar entry, reading only
signal-bar state (`s["et"]`, `s["atr_ratio"]`, `s["session"]`) — never a future bar.
**Design decisions a future agent might second-guess:**
- `min_target_r` and the `risk > tp1d` (stop>target) reject both key off **TP1, not
  TP3** — because 50% scales at TP1 and the runner moves to BE, so TP1's RR is the
  expectancy-critical one.
- `tradable` = raw PF>1 (or `inf` for all-winners) AND avg R>0, computed from the
  **raw numeric pf BEFORE** it's converted to the "∞"/None display string. Never
  compare the display string.
- `run_backtest()` assumes numeric filter params; only the `/backtest/run` Flask
  route clamps them (max_trades 0..100, min_target_r 0..10). If the engine is ever
  exposed beyond that route, clamp inside the engine too.
- Empty-trades metrics dict must carry the same new keys (`tradable=False`,
  `avg_winner_r`/`avg_loser_r=None`, `loss_reasons=[]`) for serialization parity.
- **MGC zeroes out at the default 1.5R min target — this is correct, not a bug.**
  MGC's first target (`tp1=5.0`) equals its minimum stop (`min_stop_ticks 50 ×
  tick 0.1 = 5.0`), so every MGC trade is at best **1.0R at TP1** and can never
  satisfy `min_target_r=1.5` → 0 trades. MNQ passes (tp1=20 vs ~10–13pt stops ≈
  1.5–2R). The dashboard exposes Min Target R / Max Trades-per-session inputs
  (default 1.5 / 3) so MGC can be explored at 1.0R; the form caption states this.
  Don't "fix" by silently lowering the default or by moving the gate to TP3.

## CSV auto-detect (symbol/timeframe) + GC/NQ aliases
`parse_candles_csv` accepts symbol/timeframe = "auto"/None/"" and the upload route
stores the engine-RESOLVED value (returns `detected_symbol`/`detected_timeframe`
flags). `_detect_symbol(filename, med_close)`: **filename is authoritative**, price
scale is fallback ONLY when exactly one instrument's range matches. The price ranges
deliberately OVERLAP (MGC 400–12000, MNQ 3000–60000) so price alone is often
ambiguous — hence filename-first.
**Why aliases:** full-size `GC`/`NQ` tokens map to the micros `MGC`/`MNQ`
(SYMBOL_ALIASES) — the app already treats them as the same scale (VWAP auto-fetch
sources GC=F/NQ=F) and traders export the deeper-volume underlying as a proxy. Match
via `_SYMBOL_TOKEN_RE` (longest-first alternation + `(?<![A-Z])...(?![A-Z])` letter
boundaries) so `MGC1!`→MGC (never also GC), `GC1!`→MGC, and a 2-letter token never
fires inside an unrelated word ("BIGCAP"/"INQUIRY"). A filename naming two distinct
instruments, or a filename-vs-price-scale contradiction, is REJECTED with a clear
400 (the range sanity check is the fail-safe) — never silently mis-classified.
**How to apply:** concrete (non-auto) symbol/timeframe keeps prior strict behavior;
don't loosen the contradiction rejection into a guess. New aliases go in
SYMBOL_ALIASES (regex rebuilds from its keys).
