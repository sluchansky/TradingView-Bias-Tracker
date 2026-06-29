---
name: LIVE 2-contract runner + trade-mgmt analytics suite
description: Why the LIVE split-runner arming UI is safe by construction, the operator prereq, the OFF==today guarantees for the 6-feature TradeZella-driven trade-management upgrade, and the be_2r runner-management style (contract 1 banks 1R / runner to BE+2R).
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

## Runner-management style: trail (default) vs be_2r
`LIVE_RUNNER_RUNNER_MODE` env (sanitized to {"trail","be_2r"}, default **"trail"** =
byte-identical legacy stop-only-no-TP runner). **"be_2r"** = "contract 1 banks 1R,
runner to break-even after 1R, contract 2 runs to 2R":
- Both `be_arm_price` (entry±1R) and `runner_target` (entry±2R) come from
  `_live_runner_be_2r_levels(entry, stop, direction)` — **derived from live entry/stop
  ONLY, never the plan target**. Returns `(None,None)` on risk≤0 → silently degrade to
  trail (so be_2r can never send a bad TP).
- **Primary leg TP is OVERRIDDEN to `be_arm_price` (1R), NOT the plan `t1`.** Why: the
  sanctioned ORB 1:4 override can make `t1`=4R, which would stop contract 1 from banking
  at 1R while the runner BE still arms at 1R — a real correctness bug the architect
  caught. The override also re-stamps `primary_plan["takeProfit"]` and the local `t1`
  used by the notify/return/record so display matches the wire.
- Runner leg carries a **real broker-resident 2R take-profit** + its original broker
  stop (the hard backstops). `_runner_exit_signal` **delegates to
  `_runner_exit_signal_be_2r` when `runner_mode=="be_2r"`**: that path does ONLY a
  mandatory market-closed flatten + a stateless synthetic BE (high-water reaches
  `be_arm_price` → flatten if price returns to entry). **No VWAP/CVD/ATR/time-stop
  trailing** in be_2r — the runner is meant to ride to 2R or BE.
- The synthetic BE only **auto-sends** when `LIVE_RUNNER_REDUCE_MODE=="exit"`; otherwise
  it's alert-only and the broker's resting 2R TP + original stop manage the runner.
**Operator prereqs for full auto BE+2R:** `LIVE_RUNNER_ENABLED=1`,
`LIVE_RUNNER_RUNNER_MODE=be_2r`, `LIVE_RUNNER_REDUCE_MODE=exit`, `LIVE_RUNNER_QTY=1`,
TradersPost "Use signal quantity" ON, then arm via the dashboard.
**How to apply:** if you touch the entry, keep the primary-TP=1R override AND the
be_2r→levels degrade-to-trail guard; new runner-mode logic must not add a trailing
signal to the be_2r exit path.

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
