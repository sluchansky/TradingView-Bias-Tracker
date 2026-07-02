---
name: Liquidity Sweep Focus overlay
description: Flag-gated DISPLAY/ADVISORY-ONLY Main-Brain sweep-state overlay — how it stays walled off from the money path, and the sweep-reading gotchas.
---

# Liquidity Sweep Focus (DISPLAY/ADVISORY-ONLY)

A flag-gated overlay (`LIQUIDITY_SWEEP_FOCUS_ENABLED`, default OFF) that reports a
liquidity-sweep STATE + a plain-English trader read + an ADVISORY confidence delta,
nested at `result["main_brain"]["liquidity_focus"]`. States: NO SWEEP / SWEEP FORMING /
SWEEP CONFIRMED / SWEEP FAILED / CONTINUATION THROUGH LIQUIDITY.

**The advisory delta (+10 confirmed+reclaim, +10 sweep-into-opposing-zone+rejection,
-10 failed/continuation, 0 none) is DISPLAY-ONLY** — it has zero consumers outside the
compute fn and the dashboard render JS. It must NEVER feed the Edge Score, the gate,
strategy votes, risk, or execution.

**Why:** this is a "trader's read" advisory the operator asked for, not a signal. Any
leak into scoring/gate would silently change live entries. Delta stacks are additive and
spec-literal: FORMING+into_opposing can yield +10 without a CONFIRMED state — intended.

**How to apply (invariants any change must keep):**
- Nest in `main_brain` ONLY. Never attach a top-level `result` key (a stray top-level
  key is the classic FVG/OB-style leak into /webhook side-effects). Both attach paths
  (live + fail-open neutral) nest.
- `compute_liquidity_sweep_focus(result)` READS `result` and writes only to its own
  `out` dict — never mutate the input (copy `nearby` via `dict(nearby, ...)`).
- Flag OFF ⇒ the compute call itself is skipped (guarded at the seam), so no key, no
  side effects, byte-identical `full_analysis`/`main_brain`. Goldens pin the flag =0;
  the flag-ON behavior is covered by a dedicated smoke, NOT the goldens.
- Read sweeps by RE-SCANNING `ALERT_HISTORY` with an aware-UTC recency cutoff
  (`LIQ_SWEEP_RECENCY_MIN`, default 20m) — never trust stale `last_price_by_type` key
  *presence* as "a sweep is happening now". ALERT_HISTORY timestamps are aware
  (`now_utc().isoformat()`), so the cutoff compare is safe.
- Instrument-SCOPE the scan: match the instrument-PREFIXED alert_type
  (`f"{inst} BULLISH/BEARISH SWEEP"`) AND `a_inst == inst`. Bare un-prefixed
  "BULLISH SWEEP" (dual-tf side) must never match, and no MGC/MNQ/MES/MYM bleed.
- CVD unknown ⇒ FORMING (never CONFIRMED). No-reclaim splits FAILED vs CONTINUATION on
  whether CVD agrees with the break direction.
- Dashboard render is textContent-only (no innerHTML) and hides when the key is absent;
  node --check the SERVED `/dashboard` <script> (py_compile can't catch inline-JS errors).
