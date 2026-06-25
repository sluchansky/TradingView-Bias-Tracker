---
name: TradeZella integration (review-only)
description: How imported TradeZella journal trades feed the bot as a down-weighted memory source + display-only entry/exit reviews, and the invariants any change must keep.
---

# TradeZella integration — REVIEW-ONLY

External TradeZella CSV journal trades are imported (owner-only `/tradezella/upload` → `tradezella_trades`), analyzed (`tradezella_engine.analyze_journal`), and surfaced two ways. Both are display-only; the live money path (strict gate, scoring, sizing, dedupe, Discord, /traderspost) is never touched and the goldens stay byte-identical.

## Two consumption paths
- **Shared memory (down-weighted source):** `_tz_memory_records()` maps imported trades into the same record shape `find_similar_trades` consumes and appends them to `mem_cache` AFTER the live trades, tagged `source:"tradezella"` with `strategy_version=None`, `mfe_r/mae_r=None`, `grade/regime/volatility=None`. A source-weight factor (`_gov_source_weight`) multiplies the existing recency×version weights so TZ rows are ~0.05 effective when version-unknown — they can never occupy the top recency tier, evict live trades, or dominate. Non-domination floor + dual-(<thr) demote-only veto still protect the money path. Proven by `.local/state/tz_memory_smoke.py` (down-weight effective-sample ~0.30 vs live ~6.0; 75 losing TZ matches cannot veto a strong live setup).
- **Reviews (pure presenter):** `tradezella_engine.build_reviews(analysis)` CONSUMES the already-computed `analyze_journal` dict (no recompute, no raw-trade access, no app import) and returns `{entry_review, exit_review}`, each `{available, flag, verdict, headline, signals, ...}`. Wired fail-open into `GET /tradezella/analysis` as `analysis["reviews"]`. Dashboard `#tz-review` "Bot Review" section renders via `tzRenderReview/tzRenderReviews` using `document.createElement`+`textContent` (no data into innerHTML). Proven by `.local/state/tz_reviews_smoke.py`.

## Memory-load locking — do NOT "fix" it
**Rule:** DB I/O (both the live `mem_rows` SELECT and `_tz_memory_records()`) happens OUTSIDE `LEARNING_LOCK`; only the publication `MEMORY_TRADES[:] = mem_cache` runs UNDER the lock, in `_recompute_learning`.
**Why:** holding `LEARNING_LOCK` across slow DB I/O is the real anti-pattern (blocks request-path readers). The T201 spec said "DB read under LEARNING_LOCK" but the actual invariant that matters is "loaded only during recompute, never via per-request SQL" + "atomic swap under lock." TZ deliberately mirrors the existing live-memory load.
**How to apply:** if a future review flags that the TZ read isn't literally inside the lock, that's expected — leave the read outside, keep only the swap inside. Match the live path; never wrap DB I/O in the lock.

## Invariants any change must keep
- App does INSERT/SELECT only — no runtime DDL (table created out-of-band in dev, Publish schema-diff in prod), like the learning engine.
- Routes are owner-only (not in OPEN_PATHS) and must be on the Express `/api` proxy whitelist.
- `/tradezella/upload` body log is redacted (notes/PnL/PII must not leak to logs).
- Adding more TradeZella influence beyond the down-weighted shared-memory path requires a NEW non-domination smoke before it ships.
