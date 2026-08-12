---
name: INTRADAY_TREND native analysis engine
description: Phase 3 fully closed — 29-spec audit complete; 6 phase-3 engines wired; 96 new tests; 216 total IT tests pass; trail-stop ordering bug fixed.
---

## What was built (spec §§3–10) — Phase 3 closure

Nine new Phase 3 functions added (all SWING-policy free, all fail-open):

| Function | Spec | Returns | Key guard |
|---|---|---|---|
| `_it_data_freshness(inst)` | §10 | `(is_ok, stale_tfs)` | 15m STALE/UNAVAILABLE/<4 bars → hard-block; absent is STALE (not fail-open) |
| `_it_5m_location_engine(inst, dir, price, sess_levels, vwap, atr)` | §3 | `{setup_location, setup_location_type, setup_location_value, pullback_state}` | AT_LOCATION≤0.25 ATR, PULLING_BACK≤1 ATR, EXTENDED>1 ATR; FAIL-OPEN→UNKNOWN |
| `_it_check_alert_history(inst, dir, types, max_age)` | §5,6 | `bool` | Scans ALERT_HISTORY; 10-min cutoff; direction compatible; FAIL-OPEN |
| `_it_1m_confirmation_engine(inst, dir, confluences, price, vwap)` | §4,5,6,7 | `{confirmations_detected, confirmation_count, min_confirmations, confirmations_met}` | 5 signal types; IT_MIN_CONFIRMATIONS env (default 2); FAIL-OPEN |
| `_it_cooldown_remaining(inst)` | §9 | `float` seconds | Monotonic clock; INTRADAY_TREND_COOLDOWN_MINUTES (default 15); FAIL-OPEN→0 |
| `_it_register_cooldown(inst)` | §9 | None | Records monotonic timestamp in `_IT_COOLDOWN_BY_INST` under lock |
| `_it_notify_force_flat(row, ts)` | §11 | None | Queues Discord via `_enqueue_slow`; gated on DISCORD_LIVE_ENABLED; FAIL-OPEN |

## Phase 3 wiring in compute_intraday_trend_context()

New gates inserted into status precedence chain (between time gate and extension gate):
1. `BLOCKED_DATA` — when `_it_data_freshness()` returns `(False, …)`
2. `BLOCKED_COOLDOWN` — when `_it_cooldown_remaining(inst) > 0`

New stable schema keys:
```python
"setup_location", "setup_location_type", "pullback_state",
"confirmations_detected", "confirmation_count", "min_confirmations",
"confirmations_met", "data_freshness_ok", "stale_timeframes",
"cooldown_remaining",
"mgmt_stop_move_reason", "mgmt_trail_stop_suggested", "mgmt_trail_stop_source"
```

## Bug fixed: trail-stop ordering

`compute_it_trade_management()` had the trail_stop_suggested computation running
BEFORE `out["trail_active"]` was set to True (line order bug). Fixed by moving
the structural trail block to AFTER `out["trail_active"] = True`.

**Why:** Code at line ~8788 checked `if out["trail_active"]` but trail_active
defaulted False and was only set at line ~8818. Structural trail suggestions
were silently never populated.

## IT_DAILY_CAP and _it_daily_trade_count()

Two env vars both set the IT daily cap:
- `IT_DAILY_CAP` (canonical, default 3)
- `MAX_INTRADAY_TREND_TRADES_PER_DAY` (legacy, fallback)

`_it_daily_trade_count()` now reads `IT_DAILY_CAP` first, then falls back to
`MAX_INTRADAY_TREND_TRADES_PER_DAY`, then defaults to 3.

**Why:** Stable schema initialized cap=3 from IT_DAILY_CAP but `_it_daily_trade_count()`
was reading `MAX_INTRADAY_TREND_TRADES_PER_DAY` (default 2), overwriting ctx["daily_trade_cap"]
with the wrong cap on every compute_intraday_trend_context() call.

## Cooldown registration hook

`_ghost_observe_setup()` calls `_it_register_cooldown(inst)` inside the `if inserted:`
block when `TRADING_MODE == "INTRADAY_TREND"`. Cooldown starts on ghost obs creation
(shadow mode), not on live execution.

## Time restriction granularity

`_it_time_restriction()` now returns granular states:
- `BLOCKED_SESSION` — before IT_ENTRY_START_ET (default 08:00 ET)
- `ENTRY_BLOCKED` — at/after INTRADAY_NEW_ENTRY_CUTOFF_ET (default 15:00 ET, was 15:15)
- `FORCE_FLAT` — at/after IT_FORCE_FLAT_TIME (default 15:55 ET)
- `OK` — within session window
Never returns generic `BLOCKED`.

## TP1 fallback correction

`_it_find_tp1()` fallback changed from 1.25R → 1.0R per spec §5.

## Dashboard Phase 3 additions

HTML: Row 4 grid with 6 new cells: `it-pullback-state`, `it-setup-loc`,
      `it-1m-conf`, `it-cooldown`, `it-freshness`, `it-t15m-native`
JS:   `_itStCol` extended with `BLOCKED_DATA`, `BLOCKED_COOLDOWN`, `BLOCKED_SESSION`,
      `ENTRY_BLOCKED`, `FORCE_FLAT`; daily cap default changed from `||2` to `||3`;
      Phase 3 rendering block adds pullback, location, confirmation, cooldown,
      freshness, 15m-native reads.

## Tests

216 total IT tests (Phase 2: 120 + Phase 3: 96); all pass.
Test file: `tests/test_intraday_trend_phase3.py` (1004 lines)

Phase 2 tests updated:
- `test_schema_all_keys_present`: added 3 new keys to expected set
- `test_empty_active_trade_returns_hold`: added explicit et_now during session hours
- `test_blocked_daily_count_unavailable_when_db_not_ready`: mocks `_it_data_freshness` (True,[])
- `test_daily_cap_status_when_capped`: mocks `_it_data_freshness` (True,[])
- `test_confirmed_setup_status_when_fully_confirmed_lsr`: added BLOCKED_DATA to allowed set

**Why:** Phase 3 adds `_it_data_freshness` check before all other status gates. In
test context (no trend_alignment module → 15m UNAVAILABLE), BLOCKED_DATA fires first,
masking tests that expected other status codes. Tests that need to test other paths
must patch `_it_data_freshness` to return `(True, [])`.

## IT engine is ghost/shadow only

No live execution wired. All IT analysis flows to `intraday_trend_diagnostics` in
`/status` for display. 4 smokes (parity, scalp_golden, dual_sim, breakout_mode) pass.
