---
name: Per-asset safety controls
description: How per-asset money-path safety limits layer and the fail-closed/byte-identity rules any change must keep.
---

# Per-asset safety controls (money path)

Per-asset execution limits (emergency kill switch, max trades/day, max contracts,
max open trades, max daily loss, post-loss/win cooldown) layer through ONE resolver:
`safety_cfg(inst, key)` → asset-mode override → asset default → `SAFETY_DEFAULTS`.
Both the manual `/traderspost` path and the auto path flow through the single
`execute_trade_gateway`, so safety is enforced once and authoritatively.

**Why:** adding MES/MYM required per-asset limits without a second divergent broker
path and without changing MGC/MNQ behaviour.

## Invariants any change MUST keep
- **MGC/MNQ byte-identity:** every new control's default is a no-op — emergency
  False, max_daily_loss None (off), cooldown 0, max_open_trades None (legacy
  unlimited), max_trades_per_day = legacy global, max_contracts = legacy global.
  At defaults every new branch must short-circuit so the gateway behaves exactly
  as before. The registry parity harness must stay IDENTICAL vs golden.
- **Fail-CLOSED for unknown/typo instruments:** never default an unknown to MGC on
  the money path. Use strict resolution (`inst if inst in ASSETS else
  _instrument_from_text(inst)`, None on unknown), NEVER lenient `instrument_of()`
  (which defaults unknown→MGC and would turn a typo into a real MGC order). For an
  unknown instrument the limit helpers return the blocking value (max_contracts /
  max_trades_per_day → 0, max_daily_loss → 0.0, emergency_disabled → True).
- **`max_open_trades` / `max_contracts` of 0 is a HARD block**, not "ignored":
  `None` means legacy/unlimited, but a configured `< 1` must reject ALL new orders
  for that asset (fail-closed), in BOTH gateway and auto-exec.
- **Daily-loss cap fails CLOSED:** if realized P&L can't be computed while a cap is
  set, block (409) rather than risk trading past the limit. `_realized_pnl_today`
  uses the same ET trading-day key as `_auto_trade_count_today`, and a malformed
  P&L must raise into the gateway (not be swallowed to 0).
- **Lock order:** `AUTO_TRADE_LOCK` is a plain `Lock` (NOT RLock). `_safety_snapshot`
  must release it before calling helpers that take their own locks
  (`_auto_trade_count_today`, cooldown helpers). `SAFETY_LOCK` must NEVER nest under
  `AUTO_TRADE_LOCK`. Post-outcome cooldown is set OUTSIDE `AUTO_TRADE_LOCK` in the
  price watcher (STOP_HIT→loss, T1/T2→win).

**How to apply:** when touching the gateway, `_maybe_auto_execute`, `/traderspost`,
`/auto-trade`, or the watcher outcome paths, re-run the parity harness (must be
IDENTICAL), the helper unit checks (fail-closed unknown, cooldown arming,
maxOpenTrades=0→block), and the gateway smokes. MES/MYM ship emergency-disabled
until the operator POSTs `/auto-trade {emergencyDisabled:false}` AND arms AUTO.
