---
name: Unified Analyst Report
description: The display-only thesis layer that consumes (never recomputes) the other analyst engines; its invariants and the open-position journal loop.
---

# Unified Analyst Report

A single "executive thesis" layer (`compute_analyst_report`) built at the SINGLE
`full_analysis` return path, AFTER every verdict override (open/vetoed/closed all
land there). It only CONSUMES already-assembled `result` blocks —
`analyst`, `trade_debate`, `confidence_governor`, `trade_memory`,
`trade_memory_context`, `volatility`, `strategy_engine`, `news_filter`,
`directions` — and never recomputes any engine. Surfaced as `result["analyst_report"]`,
whitelisted onto `/status`, rendered by the `mod-report` dashboard panel
(`renderReportMode`).

**Rule:** This layer is DISPLAY/NOTIFY-ONLY. It must never touch the gate,
scoring, sizing, dedupe, or execution, and is wrapped in its own try/except that
degrades to a neutral stable-schema block. Any change to it must keep the goldens
byte-identical.
**Why:** It is purely downstream of the decided result; a bug here breaking the
money path would be inexcusable. Goldens don't call `full_analysis`, so a correct
display-only change is automatically golden-safe — if a golden moves, you leaked
into the decision path.
**How to apply:** Read the consumed blocks by key (they are the contract). If any
upstream engine renames its schema, this consumer must be updated in lockstep.
Confidence components map names `current_setup/live_market/ai_reasoning/historical`.

**Open-position journal loop (`_analyst_report_loop`):** the OPEN-position companion
to `_trade_ready_loop` (which fires while FLAT). Posts a fresh thesis to the JOURNAL
channel every `ANALYST_REPORT_INTERVAL` (env, default 900s) while `active_trade_for(inst)`
is set; market-open gated, mute-aware, per-instrument throttled, arms `LAST_SENT`
only on a 2xx send. Started at boot ONLY under `DISCORD_LIVE_ENABLED` — like every
other time-based Discord sender, so dev never double-posts to the shared live channel.

**Two non-obvious gotchas:**
- The prev-confidence store re-baselines ONLY on `|Δ| >= eps` or after a TTL — otherwise
  the high-frequency `/status` poll resets the delta to ~0 every few seconds and the
  change narrative is always "steady". Keep that guard.
- Early-exit "CVD flips" trigger must fire when `cvd_state == adverse` (bearish for a
  Long), NOT `!=`. The first cut had it inverted (warned on favorable flow); display-only
  but misleading. Mind directional polarity in all early-exit/management hints.
