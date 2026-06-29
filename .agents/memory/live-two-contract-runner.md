---
name: LIVE 2-contract runner + trade-mgmt analytics suite
description: Why the LIVE split-runner arming UI is safe by construction, the operator prereq, and the OFF==today guarantees for the 6-feature TradeZella-driven trade-management upgrade.
---

# LIVE 2-contract runner (Option B) + trade-management analytics

A flag-gated, default-OFF suite added on top of the single-order money path:
trade-management analytics (MFE/MAE, commission, oversized-loss), session-quality
grade, bot hold score, a paper conditional runner, and the LIVE 2-contract runner.
All analytics/advisory layers fail-OPEN and never touch gate/scoring/sizing/dedupe;
the money path stays fail-CLOSED. OFF == byte-identical (the 4 scoring goldens prove it).

## The arming UI is safe by construction — two independent layers
The LIVE split has a **two-layer arm**, and neither layer is the money path:
- `LIVE_RUNNER_ENABLED` — env bool, the engine switch.
- `LIVE_RUNNER_ARMED` — in-memory owner bool flipped by the owner-only `/live-runner`
  POST; **resets OFF on every restart/publish** (fail-safe toward single-order).

**The ONLY path to a real split is the gateway** (`execute_trade_gateway` →
`_live_runner_eligible` → `_execute_live_two_leg_entry`). `_live_runner_eligible`
requires ALL of: env enabled + armed + execution mode == traderspost +
`DISCORD_LIVE_ENABLED` (live/published instance) + setup contracts ≥ 2.
**Why:** so a dashboard arm toggle can never place an order by itself — arming while
the engine is disabled is inert (`eligible` stays false). `/live-runner` only flips the
bool under `LIVE_RUNNER_LOCK` and returns `live_runner_status_view()`; it never calls
`_send_broker_order`/the two-leg entry.
**How to apply:** never let the UI/endpoint short-circuit `_live_runner_eligible`; if
you add a new "arm"-style control, keep it a flag the gateway re-checks, not an action.

## Operator prerequisite (must be surfaced, easy to miss)
The live instance's TradersPost strategy must have **"Use signal quantity" ON**, or the
broker ignores the per-leg quantity and the split sizing is wrong. This is surfaced in
the live-runner panel note and the arm confirm dialog — keep it there.

## Leg ordering & failure handling (durable safety design)
The two-leg entry is **primary-first**: the primary leg (stop + 1R TP) sends with the
dedupe slot; the runner leg (stop only, no TP) fires **only after the primary lands
2xx** (with `release_slot=None` so it can't double-reserve). **Any runner-leg non-2xx
disarms `LIVE_RUNNER_ARMED`** so a half-filled split can't silently repeat. The reduce
path (`_execute_live_runner_reduce`) is fail-closed + idempotent via a reduce-state
machine; `reduce_mode` defaults to "manual" (alert only, no auto broker exit). The
watcher reads RAW stores only and fails open to the broker's resting stop.
**Why:** a partial split (primary filled, runner failed) is the dangerous state; disarm
+ idempotent reduce keep it from compounding. **How to apply:** preserve the ordering
and the disarm-on-failure if you touch `_execute_live_two_leg_entry`.

## Runner tracked separately from the open-position slot (Option A)
The live runner leg lives in a gateway-owned store (`LIVE_RUNNERS_BY_INST`), **not** in
`ACTIVE_TRADES_BY_INST`. **Why:** the per-instrument single-position tracker must stay
one-slot-per-instrument; mixing the runner into it would corrupt that accounting and the
paper lifecycle. Paper conditional runner is display/journal only — never a broker send.

## Dashboard surfaces are pure /status consumers
The 4 panels (trade-mgmt, session-quality, bot-hold, live-runner) are driven purely by
the 3s `/status` poll + the toggle's POST response — no bootstrap loader. Each panel
**hides itself** when its flag/data is absent, so default-OFF == invisible. Owner-only
endpoints (`/live-runner`) must be in the Express `flask-proxy.ts` whitelist (else 404)
but NOT in dashboard-auth `OPEN_PATHS`.

## Verification recipe (do not skip)
`py_compile` does NOT catch errors in the inline dashboard JS (it's a Python triple-
quoted string). Always `node --check` the **served** `<script>` (extract via
`app.test_client().get('/dashboard')`), plus the 4 goldens + the feature smokes.
