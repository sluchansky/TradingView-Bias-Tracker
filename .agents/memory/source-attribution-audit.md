---
name: Databento Source Attribution Audit
description: Display-only diagnostics identifying which Edge Score component evidence came from Databento vs TradingView. Three new functions + MARKET_INPUT_SOURCE_BY_TICKER global.
---

## Rule
`_audit_event_duplicates` accepts a `now_dt` kwarg (defaults to `datetime.now(timezone.utc)`).
Always pass `now_dt` in unit tests when using pinned timestamps — the function applies a 1-hour
look-back cutoff and silently returns [] if test events are older than 1 hour from real-now.

**Why:** Tests 12 & 13 failed first run because `_NOW = datetime(2025, ...)` events were filtered
by a cutoff computed against real 2026 time. Fixed by adding `now_dt=None` parameter.

## How to apply
- Production callers (full_analysis): pass `now_dt=now_utc()` to keep one consistent timestamp.
- Test callers: always pass `now_dt=_NOW` (or whatever fixed clock the test fixtures use).

## Key invariants
- `MARKET_INPUT_SOURCE_BY_TICKER` is the ONLY new state written by these functions.
- All three functions are FAIL-OPEN (return [] on any exception).
- They appear AFTER `result["learning_score_influence"]` in full_analysis and BEFORE the alert_level block.
- `/status` whitelist keys: `source_attribution`, `source_audit`.
- Test file: `artifacts/tradingview-webhook/test_source_attribution.py` (15 tests).
