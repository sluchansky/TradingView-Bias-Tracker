---
name: Per-asset safety controls
description: How per-asset money-path safety limits layer and the fail-closed/byte-identity rules any change must keep.
---

# Per-asset safety controls (money path)

Per-asset execution limits (emergency kill switch, max trades/day, max losses/day,
max contracts, max open trades, max daily loss, post-loss/win cooldown) layer through ONE resolver:
`safety_cfg(inst, key)` → RUNTIME override (`SAFETY_RUNTIME`, DB-backed) →
asset-mode override → asset default → `SAFETY_DEFAULTS`.
Both the manual `/traderspost` path and the auto path flow through the single
`execute_trade_gateway`, so safety is enforced once and authoritatively.

**Why:** adding MES/MYM required per-asset limits without a second divergent broker
path and without changing MGC/MNQ behaviour.

## Invariants any change MUST keep
- **Defaults are now TIGHT-PROTECTIVE, not no-ops (live-loss-reduction).** The global
  defaults are `maxTradesPerDay=5` and `maxOpenTrades=1` (was 50 / None-unlimited);
  emergency False, max_daily_loss None, cooldown 0, max_contracts = server ceiling
  stay as before. These are REAL money-path caps — they only stay golden-safe because
  the parity/golden harnesses snapshot the strict funcs (build_strict_trade_plan /
  evaluate_strict_setup) and never exercise the gateway/auto-exec path. If you add a
  NEW control, still make ITS default a no-op so existing-asset behaviour is unchanged
  — the TWO sanctioned exceptions (both user-requested) are the tight defaults above
  and `maxLossesPerDay=5` (2026-07-07): a cap on REALIZED losing trades per ET day
  (wins/breakeven NEVER counted → winners unlimited), nullable (null = unlimited),
  env-reversible via `SAFETY_MAX_LOSSES_PER_DAY` (`none/off` → None). `_losses_today`
  counts JOURNAL closed entries `pnl_dollars < 0` — same source/ET-day key as
  `_realized_pnl_today`, so it inherits the same semantics: only CLOSED trades count
  and the count is the SIM's proxy outcomes, not broker fills.
- **Every cap with a legacy-unlimited meaning must stay env-reversible.** Such caps
  parse via `_env_int_or_none`: `none/off/legacy/unlimited/-1` → `None` (legacy
  unlimited), blank → default, else a non-negative int (`0` = hard block). A plain
  `int(_env_float(...))` can NEVER express None and silently breaks the kill-switch
  contract — don't regress a legacy-unlimited cap back to it.
- **Stacking is belt-and-suspenders.** A live SCALP position is blocked by BOTH
  `DISABLE_STACKING_GATE` (demotes `allow_stack`) AND `maxOpenTrades=1`. Restoring
  legacy stacking needs `DISABLE_STACKING_GATE=0` *and* `SAFETY_MAX_OPEN_TRADES=none`
  — either alone still blocks. Keep both in-code comments saying so.
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
  P&L must raise into the gateway (not be swallowed to 0). `maxLossesPerDay` /
  `_losses_today` mirror this exactly (uncomputable count while a cap is set → 409;
  helper raises, never returns a guess; unknown inst → 0 = any loss trips it).
- **Lock order:** `AUTO_TRADE_LOCK` is a plain `Lock` (NOT RLock). `_safety_snapshot`
  must release it before calling helpers that take their own locks
  (`_auto_trade_count_today`, cooldown helpers). `SAFETY_LOCK` must NEVER nest under
  `AUTO_TRADE_LOCK`. Post-outcome cooldown is set OUTSIDE `AUTO_TRADE_LOCK` in the
  price watcher (STOP_HIT→loss, T1/T2→win).

## Runtime overrides layer (Auto Trade Settings page)
- `SAFETY_RUNTIME` is a per-instrument dict of validated overrides persisted in the
  `safety_overrides` table (INSERT/SELECT only — table created via DB tool in dev,
  Publish schema-diff in prod, NO in-app DDL). Boot load is strictly fail-OPEN
  (DB down → registry values, `SAFETY_LOAD_FAILED` flag for display only).
- **Readers stay lock-free:** writers replace per-instrument dicts wholesale
  (copy-on-write) under `SAFETY_LOCK`; `safety_cfg` does a plain GIL-atomic `.get()`.
  Never mutate a nested dict in place.
- **POST /safety-settings is FULL-REPLACE** `{inst, overrides}` with whitelist +
  fail-closed 400 validation (maxContracts ≤ server ceiling; null only for
  maxOpenTrades/maxDailyLoss/maxLossesPerDay = unlimited). It also resyncs `EMERGENCY_DISABLED`,
  so a save that OMITS `emergencyDisable` clears a set kill switch (fails toward
  trading). The settings page always re-sends the switch from its snapshot —
  raw API clients must do the same. `/auto-trade emergencyDisabled` writes through
  to the table so the kill switch survives restarts.
- Owner-only page `/auto-trade-settings` + `/safety-settings` follow the
  diagnostics-live pattern and must stay on the Express proxy whitelist + behind
  dashboard auth (never in OPEN_PATHS).

**How to apply:** when touching the gateway, `_maybe_auto_execute`, `/traderspost`,
`/auto-trade`, or the watcher outcome paths, re-run the parity harness (must be
IDENTICAL), the helper unit checks (fail-closed unknown, cooldown arming,
maxOpenTrades=0→block), and the gateway smokes. MES/MYM ship emergency-disabled
until the operator POSTs `/auto-trade {emergencyDisabled:false}` AND arms AUTO.

## Data-driven safety layer (in _maybe_auto_execute, FAIL-OPEN)

Three guards added based on 25-trade loss analysis (9W/16L, −7R):

- **Asia Long floor** (`execute_trade_gateway` `else:`-branch): Long entries during
  18:00–02:00 ET require Edge ≥ `ASIA_SESSION_LONG_MIN_EDGE` (85). Standard
  auto/manual-ENTER only — discretionary sources bypass. Returns 409.
- **Correlated index cooldown**: After any MNQ/MES/MYM fires a direction,
  `_CORR_ENTRY_COOLDOWN` suppresses same direction on the other two for
  `CORR_COOLDOWN_SECS` (600 s). Direction pre-resolved from cached `full_analysis`.
  Cooldown set after confirmed send in BOTH Option-C (paper dynamic) and legacy paths.
- **Directional streak pause**: After `DIRSTREAK_LOSS_COUNT` (3) losses in the same
  direction within `DIRSTREAK_WINDOW_SECS` (3600 s), that direction pauses for
  `DIRSTREAK_PAUSE_SECS` (1800 s). `_DIRSTREAK_OUTCOMES` deque fed from
  `_record_strategy_trade` close seam. All three constants are module-level and
  env-reversible by design (they are plain ints — change to `_env_int` if an env
  override path is ever needed).
- **Lock order for new structures**: `_CORR_ENTRY_COOLDOWN` writes under `SAFETY_LOCK`;
  `_DIRSTREAK_LOCK` is independent. Neither ever nests under `AUTO_TRADE_LOCK`.
