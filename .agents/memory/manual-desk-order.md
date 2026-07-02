---
name: Manual Desk order
description: The discretionary operator-override money path (/manual-order + source=manual_desk) that fires a REAL market order regardless of setup state.
---

# Manual Desk order (discretionary operator override)

Lets the operator fire a REAL market order on demand from the dashboard **regardless
of setup state** — every Edge verdict can be WAIT and it still fires. Routes through
the ONE audited `execute_trade_gateway` as `source="manual_desk"` (same gateway as
`/traderspost` and the READY auto path), so it inherits every money-path safety.

**Why:** the operator asked (firmly, twice) to send orders by hand while every setup
sat at WAIT, so the existing READY-gated buttons never fired.

## Invariants (any change must preserve)
- **Bypasses ONLY** the `is_actionable` READY check + SCALP/SWING setup-quality
  vetoes. Everything else still runs: market/session/holiday + emergency + daily-loss
  + cooldown + max-open + contract cap all run BEFORE the source branch; absolute risk
  cap, prop guard, training gate, fingerprint dedupe and the fail-closed dispatch all
  run AFTER in the shared tail. `_size_mult = 1.0` for manual_desk.
- **Server-authoritative bracket** (`_build_manual_market_plan`): entry = fresh
  `CURRENT_PRICE_BY_TICKER` (fail-CLOSED if missing OR stale > `PRICE_FRESH_MIN`; NEVER
  `AUTO_PRICE_BY_TICKER`, which is display-only), pure-ATR stop via `_dynamic_stop_plan`
  (no structure) with a min-stop-ticks fallback + side-safety check, target from the
  mode primary R. **Client prices are NEVER trusted** — the browser sends only
  `{ticker, direction, contracts}`.
- **Flag-gated** `MANUAL_ORDER_ENABLED` (default OFF). Flag-OFF is byte-identical (the
  strict goldens run with it off); the ON path is guarded by its own smoke
  (`.local/state/check_manual_order.sh`), NOT the goldens.
- **Single-slot, fail-closed:** an `active_trade_for(instrument)` guard 409s
  unconditionally (mirrors `user_approved_preview`) — never stack/overwrite a tracked
  position. The route starts LOCAL tracking (`source=MANUAL_DESK`) ONLY on
  `sent`/`simulated`, never on `manual_required` — else a phantom position blocks the
  slot on the next real order (send-before-track).
- **Owner-only:** `/manual-order` is in `BOT1_ROUTES` (Express `/api` proxy whitelist)
  and is NOT in `OPEN_PATHS`.
- **Dashboard UI REMOVED (endpoint kept):** the orange `#manual-order-box` div + its
  JS (`sendManualOrder`/`setManualDir`/`manualOrderDir`) were deleted per user request —
  they wanted trade entry to live INSIDE the potential-plan preview box (entry/stop/TP)
  via the `/take-preview` button, not a separate fire-regardless box. The `/manual-order`
  route + `manual_desk` gateway branch remain (double-gated fail-closed 409 when
  `MANUAL_ORDER_ENABLED` off), so re-enabling is UI-only. **Do NOT re-add the box**
  thinking it went missing. `MANUAL_ORDER_ENABLED` env var was removed (→ default OFF).
