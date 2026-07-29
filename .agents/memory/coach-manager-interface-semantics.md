---
name: Coach and Manager Interface v1 semantics
description: Authoritative field sources for build_coach_interface and build_manager_interface; two semantic bugs fixed after initial build.
---

## weight_updated (Coach)
**Rule:** `bool(LEARNING_ANALYTICS.get("updated_at"))` — NOT `LEARNING_ANALYTICS.get("ready")`.

`ready = total_trades > 0` (has trades in DB). `updated_at` is set at line ~12590 in `_recompute_learning()` only when the recompute completes successfully. Absent at boot. Never set by DB availability probes or readiness checks.

**Why:** "ready" and "recompute ran" are independent; a system can have trades (ready=True) without the background recompute having run in this session.

**How to apply:** Any future field that asks "did the learning recompute run?" must check `updated_at`, not `ready`.

## thesis_resolved (Coach)
**Rule:** `False` during ordinary `full_analysis()`.

The ARCH defines `bool` where True = "thesis_snapshots resolve ran." No global "last resolve ran" flag exists. Resolution is per-trade, per-row in `thesis_snapshots` DB. `THESIS_TRACKER_DB_READY` is DB accessibility — never a resolution event.

**Why:** Coach Interface is designed for trade-close context; during `full_analysis()` no resolution occurs. False is the architecture-defined "did not run" value — honest, not invented.

**How to apply:** If a future event source is added (e.g., a global `_THESIS_LAST_RESOLVED_AT` timestamp), update `thesis_resolved` to `bool(_THESIS_LAST_RESOLVED_AT)`.

## Mutability safety (Manager)
**Rule:** Always `dict(trade)` before returning active_trade and managed_trade.

`active_trade_snapshot()` shallow-copies the outer dict but inner trade dicts are shared references. `MANAGED_TRADES_BY_KEY` values are live dicts. Consumers mutating returned dicts would affect global state.

**How to apply:** Any future builder that reads from `ACTIVE_TRADES_BY_INST` or `MANAGED_TRADES_BY_KEY` and returns a dict must shallow-copy before returning.

## Test count reference
After semantic audit: 70 tests (49 structural + 21 semantic). Semantic tests in `test_v1_interface_versions.py` cover: ready≠updated, enabled≠updated, DB-ready≠thesis-resolved, active-thesis≠thesis-resolved, copy isolation, instrument scoping, no-execution side-effects.
