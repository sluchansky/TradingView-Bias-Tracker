---
name: Databento MGC overnight silence + partial-flush fix
description: MGC (COMEX Micro Gold) has zero Databento trades overnight (11 PM–1 AM ET). The partial-bar flush fix closes stale partials for low-volume instruments at session open.
---

# Databento MGC overnight silence + partial-flush fix

## Rule / invariants

- **Overnight silence is upstream, not a bug.** Databento historical probes can confirm zero MGC records between 03:00–05:00 UTC (11 PM–1 AM ET). The "Databento bars received: 0" display can be accurate even while other instruments trade.
- **US session has gaps.** MGC historical probe (July 30, 13:30–16:00 UTC): 903 records in 137 unique minutes, max gap = 4 minutes. MNQ has no gaps.
- **Partial-flush fix** (`PARTIAL_STALE_S=70, PARTIAL_FLUSH_INTERVAL_S=30`): a daemon thread flushes any partial bar whose minute is >70s in the past. This closes real-trade partials that never get a next-minute trade. No synthetic bars created.
- **Thread-safety**: `_partial_lock` (threading.Lock) wraps the read-clear-close sequence in both `_tick_bar` and `_flush_stale_partials`. `_on_bar_close` is always called OUTSIDE the lock.
- **Flush timer lifecycle**: started with `stop_event` inside `_run_feed`, stopped in `finally` block on disconnect. New timer on every reconnect.
- **Bounded-depth telemetry**: high-water depth must come from the bounded queue itself, not a per-instrument counter that still includes a just-dequeued in-flight record.

**Why:** Without the flush, MGC bars during a 4-minute trade gap would not close until the next trade arrived, leaving the Left Brain bar count at 0 even with real data accumulated.

**How to apply:**
- When diagnosing MGC "0 bars" at night: check the time. If it's overnight (11 PM–5 AM ET), the silence is upstream/genuine. Wait for US session.
- Probe the historical API with the currently configured continuous symbol to distinguish upstream silence from an application issue. `end` must be ≤ the available end timestamp or Databento returns `data_end_after_available_end`.
- `DATABENTO_STATUS["instruments"]` key for MGC will be absent during genuine overnight silence (never populated until first `_on_bar_close`). This is expected.
- After the fix, the log line `DatabentoBrain: flushed stale partial bar for MGC (bar_ts=HH:MM, age=Xs)` at INFO level confirms flush fired.

## Live log signatures

Healthy overnight (zero trades expected):
```
DatabentoBrain: connected ✓  streaming [configured continuous symbols]
DatabentoBrain: id→inst 42002887 → MGC (native=MGCQ6)
DatabentoBrain: symbology map ready — {42002887: 'MGC', ...}
[NO further MGC lines until US session opens]
```

US session open with flush fix:
```
DatabentoBrain ▶ MGC  MGC BULLISH SWEEP @ 4105.2000
DatabentoBrain: flushed stale partial bar for MGC (bar_ts=13:31, age=75s)
```
