---
name: SSE tick-stream security hardening
description: Token-based auth, connection limits, enriched events, and diagnostics for /main-brain/tick-stream
---

## Rule
`POST /main-brain/tick-stream-token` issues a 45-second single-use token.
`GET /main-brain/tick-stream?inst=…&token=…` validates the token FIRST (before feature gate or resource allocation).

## Auth ordering (security-critical)
Route check order: instrument → token → feature gate (503) → connection limits → allocate.
Anonymous requests get 401 before DATABENTO_ENABLED is revealed.

## Global scope quirks
`DATABENTO_PARTIAL_BY_INST` is imported lazily inside Flask route functions, never at module level.
Broadcast function uses `globals().get("DATABENTO_PARTIAL_BY_INST") or {}` for safe access.
Tests that inject it must use `monkeypatch.setattr(_app_module, "DATABENTO_PARTIAL_BY_INST", ..., raising=False)`.

**Why:** Without `raising=False`, pytest monkeypatch rejects setting attributes that don't already exist on the module.

## Subscriber store shape
Each entry in `_TICK_SUBSCRIBERS[inst]` is a dict:
`{q: Queue, inst: str, connected_at: float, drops: int, sub_id: str}`

## Diagnostics
`GET /main-brain/tick-stream/diagnostics` — no decorator (Express auth is primary guard);
must NOT appear in OPEN_PATHS; never exposes raw token values.

## Frontend pattern
SSE effect: `POST /api/main-brain/tick-stream-token?inst=...` (with authHeader) → EventSource with `?token=...`.
`generationRef = useRef(0)` incremented on each effect run; stale ticks check `gen !== generationRef.current`.
`sseAuthFailed` state stops reconnecting on 401/403; shows "⚠ AUTH REQUIRED" in StatusStrip.
Fresh token on every reconnect attempt (each token is single-use).

## Test file
`artifacts/tradingview-webhook/test_sse_tick_stream.py` — 43 tests in 11 classes.
Run isolated (not full suite) due to pre-existing full-suite module-state pollution in other test files.
