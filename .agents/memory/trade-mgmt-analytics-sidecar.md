---
name: Trade-management analytics sidecar
description: Flag-gated DISPLAY/ANALYTICS layer over CLOSED managed trades (MFE/MAE booleans, commission, oversized-loss) — what stays inert when OFF and why.
---

# Trade-management analytics sidecar (TradeZella-driven, Phase 2)

Flag-gated, default-OFF analytics computed only at managed-trade CLOSE. STRICTLY
display/analytics: reads already-finalised `mt` fields, never recomputes the close,
never touches gate / scoring / sizing / dedupe / money path. FAIL-OPEN.

**Invariant — OFF == today exactly.** `_compute_trade_mgmt_metrics(mt)` returns None
(no record, no `mt` mutation) when ALL of TRADE_MGMT_ANALYTICS_ENABLED /
COMMISSION_MODEL_ENABLED / OVERSIZED_LOSS_PROTECTION_ENABLED are OFF. The outcome-card
fields and the `/status` `trade_management` key only appear when `mt["trade_mgmt"]`
exists, so an OFF deployment posts the byte-identical legacy card and a null /status key.
Guarded by goldens (byte-identity) + check_trade_mgmt_math.sh (6 math/inertness checks).

**Outcome booleans are DERIVED, never price-recomputed.** `target_reached_before_reversal`
= tp1_hit OR "Win" in outcome OR exit_reason in (tp1/tp2/runner). `stop_hit_before_target`
= stopped (exit_reason stop/breakeven_stop OR "stop hit" label) AND NOT reached. So a
breakeven-stop AFTER TP1 counts as target-first, a clean stop-out counts as stop-first.

**Commission** is display-only: fees = round-trip-fee × contracts; net = gross − fees;
fee% only when gross>0; warn when >30%. `gross_pnl = pnl_dollars × contracts`
(pnl_dollars is per-1-contract; `contracts` defaults to 1 — not stored on mt today).

**Oversized-loss** fires when realized |R| ≥ OVERSIZED_LOSS_MULT (default 1.5). The
persisted `slippage` here is the DERIVED realized loss beyond the planned 1R stop
((loss_r−1)·risk·pv·contracts), NOT an entry-fill delta (those never flow back) — 0 in
paper because exit==stop. Don't confuse it with `_record_strategy_trade`'s entry-fill
slippage (which is honest-None unless a real fill price exists).

**Persistence:** `_persist_trade_mgmt_metrics` writes the in-memory ring buffer first
then offloads the DB upsert to the slow worker (only if TRADE_MGMT_DB_READY). The ring
buffer stores only `_TRADE_MGMT_COLS` keys — display-only extras (`fee_warn`, `oversized`)
live on `mt["trade_mgmt"]` for the card but are NOT persisted; /status re-derives
`fee_warn` from fee_pct_gross_profit. No in-app DDL (INSERT/SELECT only); tables made via
database tool (dev) + publish schema-diff (prod).
