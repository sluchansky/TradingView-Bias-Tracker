---
name: USER_APPROVED_PREVIEW manual preview-take
description: The manual "TAKE THIS TRADE" money path for FORMING (pre-READY) preview setups and its non-obvious safety invariants.
---

Manual owner-only button lets the operator take a FORMING preview setup (verdict
POTENTIAL LONG/SHORT, pre-READY) via the configured TradersPost gateway using the
server-side `potential_plan` (never a client-supplied price). Endpoint `/take-preview`
(owner-only; whitelisted in `BOT1_ROUTES`, NOT in `OPEN_PATHS`). Gateway branch keys on
`source == "user_approved_preview"`. Flag `USER_APPROVED_PREVIEW_ENABLED` default OFF;
`PREVIEW_MAX_CONTRACTS` default 1 (applied AFTER the normal risk/per-asset caps).

**Why the extra gates exist (both were architect BLOCKERS):**

1. **Non-shared entry vetoes must be RE-RUN in the preview branch.** The SCALP/SWING
   entry vetoes inside `full_analysis` only run `if is_actionable(verdict)`, so a
   FORMING/POTENTIAL preview SKIPS them entirely. The preview branch therefore re-runs
   the mode-correct veto before committing `direction = pv_dir`:
   - SCALP (guarded by `_scalp_dynamic_enabled()`): `compute_scalp_quality` +
     `_scalp_entry_veto_reasons` (same pattern as dual_tf / fast_entry).
   - SWING (guarded by `_swing_htf_enabled()`): `compute_swing_context(inst, price)` +
     `_swing_entry_veto_reasons(ctx, plan, dir)`.
   - Both wrapped fail-CLOSED (any exception → 409), mode-gated (mutually exclusive).

2. **Unconditional local-flatness gate.** `max_open_trades()` is env-reversible to None
   (the shared gateway cap can be disabled), so relying on it alone could overwrite an
   open slot (`set_active_trade(..., overwrite=True)`). Right after the flag check the
   branch returns 409 when `active_trade_for(instrument) is not None`. With the default
   per-asset cap (1) the SHARED gateway check blocks FIRST; the local gate is the
   last-line-of-defense when the cap is disabled.

**How to apply / gotchas:**
- Counted SEPARATELY by tagging the tracked position `source="USER_APPROVED_PREVIEW"`
  (no DDL, no new table). `strategy_trades` still only INSERTs via the existing close path.
- Send-before-track: only `sent`/`simulated`/`manual_required` create the tracked trade;
  rejects create no phantom trade. Duplicate-send reservation still guards double sends.
- Known non-blocking race (architect-acknowledged): flat-check → set_active_trade is not
  atomic. Acceptable for an owner-only two-confirm button; if ever hardened, use an
  instrument-scoped lock or `overwrite=False` with an explicit "sent-but-tracking-conflict".
- `entry_zone` in `potential_plan` uses an EN-DASH (U+2013) the gateway zone parser expects.
- Goldens stay byte-identical because all of this lives inside the preview branch (flag OFF,
  never reached by the SCALP/SWING/dual/breakout goldens); coverage is a dedicated smoke.
