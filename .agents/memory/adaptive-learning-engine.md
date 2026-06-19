---
name: Adaptive Learning Engine (per-strategy analytics)
description: Display-only Postgres-backed learning layer that scores strategies from closed trades and folds a bounded weight into displayed confidence — invariants any change must keep.
---

# Adaptive Learning Engine

A Postgres-backed layer that logs every CLOSED managed trade with its entry-time
strategy/regime/session/indicator context, then every N closed trades recomputes
per-strategy win rate / profit factor / avg R / best hours / best regime, derives a
BOUNDED per-strategy weight, and folds a CAPPED adjustment into the engine's
*displayed* confidence. Caches live in memory under a lock; `/status` and the
dashboard read the cache, never per-request SQL.

## Invariants any change MUST keep
- **DISPLAY-ONLY money safety.** Learning output (weights, analytics, folded
  confidence) may touch ONLY the strategy-engine display confidence, the cached
  `/status`/dashboard analytics, and post-close telemetry. It must NEVER reach the
  strict READY gate, position sizing, dedupe cooldown, or the `/traderspost`
  execution path. `/traderspost` recomputes `full_analysis` and gates on
  market-open + actionable verdict + `trade_plan` only.
  **Why:** this is real-money-adjacent; a learning bug must never move the gate.
- **Strictly FAIL-OPEN.** psycopg2 is an optional import; the app must boot and
  trade with no DB. Every DB connect/insert/recompute is caught; snapshot capture
  at the `full_analysis` return is wrapped. A DB outage degrades to "no history"
  (weight 1.0), never blocks a trade close or `full_analysis`.
- **Never disable a strategy.** Per-strategy weight is bounded [floor, ceil]
  (0.65–1.35) and returns neutral 1.0 while under-sampled (per-strategy
  `n < MIN_SAMPLE`, currently 20). The confidence fold is capped (±15 pts) and the
  final confidence still clamps 0–100.
- **No DDL in the app.** App does INSERT/SELECT only. Schema is created via the
  database tool in dev and the Publish schema-diff in prod (see
  `database-migrations-on-publish`).
- **Idempotent persistence.** `strategy_trades.managed_key` is UNIQUE and inserts
  use `ON CONFLICT DO NOTHING`, so an idempotent repost can't double-count.

## Non-obvious decisions
- **Best-hours / best-conditions aggregate on `opened_at` (ENTRY hour), not
  `closed_at`.** The whole snapshot is entry-context; bucketing performance by exit
  hour would mislabel which entry windows work. **How to apply:** any new
  time-of-day analytic on this table should use `opened_at AT TIME ZONE
  'America/New_York'`.
- **Recompute is serialized by a dedicated mutex** held across the whole
  read→compute→swap. **Why:** recompute runs on a startup daemon AND on a
  background thread every Nth close; without the mutex a slower/older run could
  overwrite a newer cache snapshot. Keep this lock distinct from the brief
  cache-swap lock.
- **Persistence runs AFTER the user-facing outcome card + journal** in the close
  path, so even the bounded DB latency (connect 5s / statement 8s timeouts) can't
  delay the Discord notification.
- **Curated-endpoint rule applies:** a new learning field must be added to the
  view that serves `/status` AND kept in the dashboard render, or it's `None` on
  the wire / missing in the panel (see `curated-endpoint-serialization`).
