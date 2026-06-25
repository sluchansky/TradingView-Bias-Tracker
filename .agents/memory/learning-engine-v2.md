---
name: Learning Engine v2
description: Confidence Governor + Professional Memory + flag-gated demotion layered on the Adaptive Learning Engine; what keeps it display-only and fail-open.
---

# Learning Engine v2 (Confidence Governor + Memory + flag-gated demotion)

Built ON TOP of the existing Adaptive Learning Engine (Postgres `strategy_trades`,
`_record_strategy_trade` / `_capture_learning_ctx` / `_recompute_learning`, `_learning_engine_view`).
Five additive pieces: enriched per-trade record, every-25-trades performance report,
a transparent **Confidence Governor**, a **Professional Memory Engine** (similar-trades lookup),
and a flag-gated **demotion** that can ONLY downgrade READY→WAIT.

## Hard invariants (why the layer is safe)
- **DISPLAY-ONLY until the demotion flag is armed.** Everything (governor, memory, report,
  enriched records) is observability; the only money-path effect is the demotion, and it is
  off by default. **Why:** this is a LIVE auto-trader; an unarmed analytics layer must never
  change a verdict. **How to apply:** any new learning field is display-first; touching the
  money path requires an explicit, flag-gated decision like the demotion hook.
- **Demote-only.** Never upgrade WAIT→READY, never create a trade, never change
  entry/stop/targets/size, never override hard risk. Every demotion is logged with a reason.
- **FAIL-OPEN.** psycopg2 optional; no data ⇒ governor returns a neutral block with
  `veto_would_fire=False` (no veto). Stats not `ready` ⇒ no adjustments, no demotion.
- **NO in-app DDL** (INSERT/SELECT only) — same convention as the rest of the engine.
- **Similar-trades lookup reads the in-memory rolling cache (`MEMORY_TRADES`), NEVER queries
  the DB inside `full_analysis`.** The DB reads happen only in `_recompute_learning`, which
  swaps `GOVERNOR_STATS` / `MEMORY_TRADES` under `LEARNING_LOCK`.

## The veto decision
`compute_confidence_governor` produces `veto_would_fire = (gate ON) and (verdict actionable)
and (final_confidence_score < READY threshold)`. **The governor reads RAW historical
win-rates** (strategy ±10 / session ±5 / regime ±5 / grade ±8 / recent ±5, each 0 under
`min_sample`) and is DISTINCT from the existing ±15 strategy weight — do not let the two
double-count. Final confidence = base Edge Score + bounded explained adjustments, clamped 0–100.

## Gate plumbing (mirror of trade_debate)
`_LEARNING_GATE_OVERRIDE` / `_learning_gate_enabled()` / `set_learning_gate()` and the
`/learning` GET/POST endpoint mirror the `_trade_debate_gate` pattern exactly. The gate is
**in-memory and non-persistent** — it resets to the env seed (OFF) on every restart/republish,
intentionally, like the other engine gates. `/learning` must be on the Express `/api` proxy
whitelist or it 404s before reaching Flask.

## Why the goldens stay byte-identical while OFF
The goldens call the strict funcs (`evaluate_strict_setup` / `build_strict_trade_plan`)
DIRECTLY, not `full_analysis`. All v2 logic lives in/after the `full_analysis` governor seam,
so display changes never touch the goldens. The demotion hook is the only money-path change,
and its guard (`market open and gate ON and veto_would_fire and is_actionable`) is dead while
the flag is OFF. **How to apply:** to smoke the demotion end-to-end you must drive
`full_analysis` (mock the two strict funcs to a READY + force market open + vetoing governor);
the goldens alone can never exercise it.

## Curated-endpoint rule (recurring footgun)
A new learning field must be added in THREE places or it's `None` on the wire / invisible:
the `full_analysis` result, the `/status` whitelist (it serializes a key allowlist, not the
whole dict), and the dashboard render. `_build_card_entry` carries the governor/memory blocks
for journal+card parity.
