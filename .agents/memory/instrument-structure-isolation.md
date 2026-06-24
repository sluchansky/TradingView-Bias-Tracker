---
name: Per-instrument structure isolation (no cross-instrument leak)
description: A suspected cross-instrument BOS/CHOCH structure leak (e.g. MNQ structure showing on MES) was DISPROVEN; how to diagnose apparent leaks and what's a red herring.
---

# Per-instrument structure isolation

A reported "cross-instrument structure leak" (a brand-new instrument like MES appearing
to show another instrument's BOS/CHOCH before it had sent any of its own) was
investigated as an AUTHORIZED money-path change and **disproven**. **No code change was
made** — the filter is already correct.

**The invariant:** all three structure-reading paths apply the *identical* per-instrument
filter `a_inst = alert.get("instrument") or _instrument_from_text(ticker) or
_instrument_from_text(alert_type); if a_inst != inst: continue`:
1. `get_price_context(inst)` → DISPLAY structure (`structure_label`/`structure_class` via
   `get_market_structure` in `full_analysis`).
2. `evaluate_strict_setup._latest_ts` (`inst = instrument_of(ticker)`) → the
   AUTHORITATIVE money-path gate (`structure_long/short` → `gate_debug.structure_confirmed`
   → the `Gate: ... struct=Y/N` log field).
3. `_strategy_signal_snapshot(inst)` → strategy engine + the SCALP setup-quality (S2) veto.

**Why it LOOKS like a leak (red herrings):**
- The display `Struct: <label>` is DECOUPLED from the gate `struct=Y/N` (observed:
  `struct=N` while label said `Reversal`). The label can come from the instrument's OWN
  *older* CHOCH (outside the log window you're staring at), and the global `bias (n/10)`
  is mixed-instrument display-only. None of these are the money-path signal.
- `gate_candidate` defaults to a direction (e.g. "Long") even with ZERO structure — not a
  trade, just a default candidate.

**How to diagnose (proven approach):** import `app.py` directly (import-safe — all thread
`.start()` are under `if __name__=="__main__"` or inside funcs) and REPLAY the real webhook
sequence (pull bodies from deployment logs; attribute via `resolve_instrument`) into
`ALERT_HISTORY`. `get_price_context` has NO time window so it's reliable as-is; the gate's
`recent` window is clock-relative, so **rebase record timestamps to `now`** before asserting
`gate_debug.structure_confirmed`. `event:`-schema sweep/CVD/VWAP webhooks (no `alert_type`)
are rejected at ingest ("Unrecognized alert type: ''") → never stored → ignore them.

**Regression guard:** `.local/state/instrument_isolation_smoke.py` (wrapper
`check_instrument_isolation.sh`, workflow `instrument_isolation`) asserts MES sees zero
structure on all 3 paths while MGC/MNQ see only their own. It is mode-independent AND
clock-independent (unlike the session-bonus-flaky scalp/swing goldens), so a failure is a
REAL regression, not drift.
