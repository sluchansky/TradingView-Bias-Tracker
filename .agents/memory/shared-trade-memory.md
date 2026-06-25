---
name: Shared Trade Memory engine
description: find_similar_trades is the single source of truth for similar-trade history feeding BOTH the Confidence Governor and the Analyst; how the 4-lens governor and non-domination floor keep history non-dominating.
---

# Shared Trade Memory engine

`find_similar_trades(result, ticker)` is the ONE source of truth for "what happened
on similar past trades". It reads the in-memory `MEMORY_TRADES` cache only (never the
DB at request time) and returns a STABLE numeric dict. Three consumers must read the
SAME context object so they can never disagree on the wire:

- `compute_confidence_governor` (Historical lens)
- `compute_trade_memory` (presenter; kept its old output schema)
- `build_analyst_memory_review` → `result["analyst"]["memory_review"]` (6 Q&A strings)

`full_analysis` computes `_memory_ctx` ONCE (after pro_review/trade_debate), stores
it on `result["trade_memory_context"]` (internal, not serialized), and passes the
same object into all three. **If you add a 4th consumer, pass it this same ctx — do
not recompute, or governor and analyst will drift.**

## Similarity rules
- Symbol is the hard core (same instrument only).
- A *known* opposite trading mode (SCALP vs SWING) is excluded; legacy NULL-mode rows
  still pass (back-compat).
- Scored dims = mode/direction/strategy/session/regime/volatility/grade; a row is a
  match only at `>= GOV_SIM_MIN_SCORE` (3) shared dims, and the engine is neutral
  (fail-open) below `GOV_SIM_MIN_MATCHES` (5) accepted rows.
- Each accepted row is weighted by recency tier (rank<=25=1.0, 26-125=0.5, else 0.2)
  × version factor (`STRATEGY_VERSION` current=1.0, prior=0.2). Bump `STRATEGY_VERSION`
  by hand whenever trade logic changes so stale-logic trades down-weight to 0.2.
- `matched_on` is accumulated ONLY from accepted rows (merge per-row dims after the
  min-score check), so the explanation never advertises a dim no comparable trade
  actually shared.

## Non-domination invariant (money-path critical)
Governor is 4 labeled lenses (each 0..100): **Current setup** is the ANCHOR
(base edge + grade tilt); **Live market**, **AI reasoning**, **Historical** can each
only NUDGE within a cap (`GOV_LIVE_CAP=8`, `GOV_AI_CAP=8`, `GOV_HIST_CAP=12`).
final = anchor + capped nudges. FLOOR: if `current_confidence >= threshold`, final is
floored to `>= threshold`. `veto_would_fire` requires BOTH `final < thr` AND
`current < thr`, so **history alone can never veto a strong live setup**.

**Why:** the governor's only money-path effect is the default-OFF learning veto
(`_learning_gate_enabled()`), which can only DEMOTE actionable→WAIT. A strong current
setup must survive weak/contradictory history; the floor + dual `< thr` condition
guarantee it.

## Goldens
scalp / swing-flagoff / parity goldens stay byte-identical because governor, trade
memory, and analyst.memory_review output is display-only and NOT part of the golden
subset — a pure presenter/scoring change here should never move them. If a golden
moves after touching this engine, you leaked into the gate/scoring path.
