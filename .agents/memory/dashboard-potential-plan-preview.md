---
name: Dashboard potential-plan preview
description: The forming-setup trade-level preview on the dashboard is display-only and must never touch the broker/money path.
---

# Dashboard "potential plan" preview

As soon as a directional setup is FORMING (structure confirmed, but not yet full
READY), the dashboard previews would-be entry/stop/TP/R:R per side. This is a
preview, NOT a tradeable plan.

**Rule:** The preview lives in `directions[*].potential_plan` (computed by the same
`build_strict_trade_plan` with the SAME args as the READY path). The money path —
`liveEligible`, `applyRec`, `sendOrder`, `buildOrderText`, the Apply/Copy/Send
buttons — must key ONLY off the actionable `verdict` (`jsReadyDir`) + the top-level
`trade_plan`. It must NEVER read `potential_plan`. The potential branch in
`renderDirView()` keeps those buttons hidden.

**Why:** Top-level `trade_plan` is only populated on an actionable verdict; reusing
it (or wiring the preview into the broker actions) for a non-READY state would let a
forming setup fire a REAL Tradovate order. The whole point of a separate field is to
keep money-moving actions impossible until READY.

**How to apply:** Any change that surfaces forming-setup levels must (1) keep them in
a separate display field, (2) gate compute on `current_price is not None` +
`market["open"]` + not conflict/blockers + `gate_debug.structure_confirmed` (so
VWAP/anchor-only states don't spam previews), (3) default the field to None on every
directions block for reader parity, and (4) guard the frontend on `pp && pp.trade_plan`
so a no-plan dict (trade_plan:False) or None hides cleanly. Never broaden top-level
`trade_plan` for non-READY states.

**Zone-anchored numbers look "frozen" by design — NOT a bug.** entry_zone is the setup
zone bounds and targets are fixed offsets, so the preview numbers barely move while
price drifts. A client-side display-only variant (`_liveAnchoredPotential` in
`renderDirView`'s POTENTIAL branch) re-anchors entry/stop/T1/T2 to the LIVE price on a
30s heartbeat so they visibly track the market, PRESERVING the server plan's geometry
(signed offset from entry mid; rr/atr/ticks/risk copied untouched). It returns a COPY
(`shown`) used ONLY for `planRow` display — never reuse `shown` in the READY/Apply/Send
paths. If asked to make preview numbers "update", this is the lever; do it in JS, never
by re-anchoring the server `potential_plan` (that's zone-authoritative).
