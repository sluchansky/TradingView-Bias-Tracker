---
name: Market State Cache persistence
description: market_state_cache table design, freshness windows, safety invariants, write points, and test file for boot-restart state persistence.
---

# Market State Cache Persistence

## The rule
`market_state_cache` (PK on `key VARCHAR(255)`, JSONB `data`, `schema_version INT`, `updated_at TIMESTAMPTZ`) persists critical in-memory market state across restarts/republishes. Each restore is gated by a freshness window — stale rows are silently skipped.

## Classification
- **MUST PERSIST** — `_TRADERSPOST_LAST` (TradersPost dedup, 2h window), `AUTO_FIRED_KEYS` (same ET-date check, 24h)
- **MAY RESTORE IF FRESH** — CVD committed state (60min), volume spike (20min), ALERT_HISTORY snapshot (30min)
- **NOT RESTORED** — `_READY_STATE_BY_INST` (new post-restart market evidence required)
- **NEVER** — restored state never calls TradersPost/broker/Discord/journal/evaluation

## Key functions (all in app.py before `_check_manual_trade_db_ready`)
- `_check_market_state_cache_db_ready()` — probes table, sets `MARKET_STATE_CACHE_DB_READY`; FAIL-OPEN
- `_save_market_state(key, data)` — UPSERT; called outside locks; FAIL-OPEN
- `_load_market_state(key, max_age_sec)` — SELECT + age gate; FAIL-OPEN
- `_restore_market_state_from_db()` — boot restore across all 5 state types; INERT
- `_persist_auto_fired_key(key)` — snapshots full AUTO_FIRED_KEYS set after each add
- `_alert_history_snapshot_loop()` — 60s daemon; sleeps first, then loops forever

## Write points in app.py
- **CVD**: after `CVD_BY_TICKER[inst] = {...}` (key `"cvd::<inst>"`)
- **Volume spike**: after `VOLUME_SPIKE_BY_TICKER[inst] = {"ts": ...}` (key `"volume_spike::<inst>"`)
- **TradersPost dedup**: after `_TRADERSPOST_LAST[instrument] = (fp, now)`, OUTSIDE `_TRADERSPOST_LOCK` (key `"traderspost_last::<inst>"`)
- **AUTO_FIRED_KEYS**: after each `AUTO_FIRED_KEYS.add(...)` (3 call sites: dual_tf, scalp auto-dispatch, fast-entry), OUTSIDE `AUTO_TRADE_LOCK`

## Boot sequence (in `__main__`)
```
_check_market_state_cache_db_ready()   # after _load_safety_overrides_from_db
_restore_market_state_from_db()        # after above
_ensure_webhook_worker()
threading.Thread(target=_alert_history_snapshot_loop, daemon=True).start()  # inside LEARNING_DB_ENABLED guard
```

## Test file
`artifacts/tradingview-webhook/test_persistence.py` — 11 tests (10 required + schema_version bonus). Uses real PostgreSQL via DATABASE_URL. Each test writes its own rows and cleans up in `finally`.

**Why:**
A production republish wipes all in-memory state. Without persistence, `_TRADERSPOST_LAST` resets → duplicate broker orders possible in the cooldown window; `AUTO_FIRED_KEYS` resets → fire-once auto-trades can re-fire on the same setup immediately after republish.

**How to apply:**
- Adding a new in-memory state that must survive restart: add `_save_market_state("key::<inst>", data)` at its write point, add a restore block in `_restore_market_state_from_db()`, add a freshness constant `_MSC_*_MAX_AGE_SEC`.
- NEVER call `_save_market_state` inside a shared lock (e.g. `_TRADERSPOST_LOCK`, `AUTO_TRADE_LOCK`) — call it right after the `with` block exits.
- The `market_state_cache` table must be created via the database tool (dev) or a Publish schema-diff (prod) — no in-app DDL.
