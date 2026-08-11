---
name: Decision Contract boot flags
description: Two separate DC_DB_READY flags must BOTH be set, or persistence silently skips.
---

# Decision Contract — Two DB-ready flags

## The rule
`DecisionRegistry` has TWO independent DB-ready flags that must both be True for persistence to work:

1. `DC_DB_READY` — module-level global in `app.py`, set by `_check_dc_db_ready()` at boot.
   - Controls the `observe_full_analysis` / `observe_orb_state` call sites in app.py.
2. `DecisionRegistry.DC_DB_READY` — class variable on the registry, set by `registry.boot()`.
   - Controls the daemon-thread persist inside `_persist_record()` (decision_contract.py line 856).

If `registry.boot()` is not called, flag #2 stays False and ALL persistence silently skips (caught by `except Exception: logger.debug(...)`). The in-memory state still works, but nothing writes to DB.

**Why:** The two flags are intentionally separate so the module global can be set before the registry is constructed, and so tests can construct the registry without a live DB. But in production boot, BOTH must be set.

**How to apply:** In app.py, always call `_DECISION_REGISTRY.boot()` immediately after the registry constructor. The `boot()` call also restores active records from DB.

## Route bug (also found during Phase 3)
`registry.get_all_states()` returns `Dict[str, Optional[Dict]]` (keyed by instrument), NOT a list.
Iterating it directly gives string keys → `.get()` crashes. Convert first:
```python
records_list = [v for v in registry.get_all_states().values() if v is not None]
```
The correct history method is `registry.get_history(inst, limit=N)` — there is no `get_transitions()`.
