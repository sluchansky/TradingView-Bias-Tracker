---
name: Right Brain Trade Management v1
description: Phase 6B.2 — shadow-mode display-only trade advisory in the Right Brain; architecture, invariants, and activation notes.
---

## Feature flag
`RIGHT_BRAIN_TRADE_MANAGEMENT_ENABLED` — `default_on=False`.
Activation: set env var to `1` + republish. Flag OFF = zero observable difference (key absent from `full_analysis` result, all goldens byte-identical).

## Architecture
`compute_right_brain_trade_management(result)` reads `active_trade_for(inst)` from the live state store (read-only) and returns one of two states:
- **FLAT** — `state="FLAT"`, recommendation=`NO_ACTIVE_TRADE`
- **ACTIVE_TRADE** — full health/confidence/thesis/exit_pressure/recommendation pipeline

### Sub-components (all fail-open)
1. `_rb_tm_build_snapshot(trade, result, inst)` — read-only trade context
2. `_rb_tm_eval_dimensions(trade, result, snapshot)` — 8 independent dims + `stop_breached`/`near_stop` booleans
3. `_rb_tm_compute_health(snapshot, dims)` — base 50, weighted contributors, clamped 0-100
4. `_rb_tm_compute_confidence(snapshot, dims)` — % of 8 key inputs available
5. `_rb_tm_compute_thesis(trade, result, snapshot, dims)` — drift from entry (IMPROVING/INTACT/STABLE/WEAKENING/BROKEN/UNKNOWN)
6. `_rb_tm_compute_exit_pressure(snapshot, thesis, health, dims)` — accumulator (NONE/LOW/MODERATE/ELEVATED/HIGH/CRITICAL)
7. `_rb_tm_compute_recommendation(...)` — action ∈ `RBTM_VALID_RECOMMENDATIONS` (frozenset, 15 values)

### Invariants (enforced by 42 tests TM001–TM042)
- `affects_execution` is **always False** in every returned dict
- `result` dict is never mutated
- `ACTIVE_TRADES_BY_INST` is never written by this engine
- No broker, TradersPost, dedup, learning, or gate path touched
- `compute_right_brain_trade_management()` never raises; returns `_rb_tm_neutral()` on top-level error

## Dashboard wiring
- HTML: `<div id="rb-tm-body">` inside `#rb-side` (separate from `#rb-body` so `_renderDualBrain` never clobbers it)
- JS: `renderRBTradeMgmt(d)` reads `d.right_brain_trade_management` from 3s /status poll; hooked in `renderModules` via `try{ renderRBTradeMgmt(d); }catch(e){}`
- /status whitelist key: `"right_brain_trade_management"`

## Why
**Why:** Full-analysis hook MUST come after strategy_eligibility and before `return result`. Flag OFF = key absent = byte-identical. The single-return-path rule (see `full-analysis-return-parity.md`) means the hook lives in exactly one place.

## Known near-stop scoring note
`near_stop=True + thesis=WEAKENING + structure=threatened + vwap=failing` → 60 pts → CRITICAL (not just HIGH). This is correct and conservative; TM024 test uses `assertGreaterEqual(ELEVATED)` not a strict HIGH match.
