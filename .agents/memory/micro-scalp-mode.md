---
name: Micro Scalp Mode
description: Separate toggle-gated liquidity-moment engine (sweep→trap→absorption→micro trigger) — ghost ledger always; a SECOND explicit LIVE arm can fire real orders through the shared gateway.
---

# Micro Scalp Mode (ghost ledger + optional LIVE arm)

A SEPARATE engine from SCALP/SWING: hunts liquidity sweeps of prior highs/lows,
waits for the failed follow-through (trapped side), optional absorption read, then
a micro trigger (fresh aligned micro CHOCH/delta-flip/VWAP-reclaim webhook →
derived VWAP reclaim/reject from the price trail → close back inside range).
Verdict TAKE/WAIT/NO TRADE. MNQ geometry: TP1 +5 / TP2 +10 pts, stop behind the
sweep extreme CLAMPED 3–8 pts, BE only after TP1.

**Two independent switches (both in-memory, both RESET OFF on restart/republish):**
- `enabled` — the mode toggle. ON = brain analyzes + GHOST rows in
  `micro_scalp_ghost_trades` (webhook-source-only observer, one open ghost per
  instrument). Toggle OFF ⇒ `main_brain.micro_scalp` key ABSENT (byte-identical;
  goldens stay green because the key never exists).
- `live` (MICRO_LIVE_ARMED) — a TAKE **additionally** fires a REAL order through
  the SAME `execute_trade_gateway` (`source="micro_scalp"`) via the parametrized
  `_maybe_auto_execute(contracts_override, disarm_cb)`. Arming requires the mode
  ON; mode OFF force-disarms. Ghost keeps logging regardless (live has a ghost
  mirror). Defaults: 1 contract (MICRO_LIVE_CONTRACTS), single tp1 exit
  (MICRO_LIVE_TARGET tp1|tp2), never stacks (MICRO_LIVE_ALLOW_STACK=0).

**Rules that must hold:**
- **SINGLE EXIT: target1 MUST EQUAL target2** in the gateway micro branch. The
  broker order carries ONE takeProfit and check_trade_events frees the slot at
  target1 — a distinct deeper target2 strands an untracked live position and
  (contracts≥2) can route into the two-leg runner split. Smoke pins
  plan.takeProfit == target2.
- **Fire-once key = the ghost INSERT** (idempotent sim_key + per-(inst,dir)
  cooldown). A skipped/blocked gateway call NEVER retries — errs toward not
  trading. The gateway branch re-checks armed + TAKE from its OWN full_analysis
  (fail-closed; never trusts the seam caller).
- **Pure engine lives in `micro_scalp.py`** (imports nothing from app.py); app.py
  only builds the read-only ctx and stores. Smoke: `.local/state/check_micro_scalp.sh`.
- **Ghost watcher starts UNCONDITIONALLY at boot** and self-gates INSIDE the loop
  on DB-ready + DISCORD_LIVE_ENABLED. Do NOT re-add a boot-time `if DB_READY`
  gate: the lazy probe in POST /micro-scalp can flip DB-ready at runtime and a
  boot-gated timer would leave open ghosts unresolved until restart.
- Lock ordering is one-directional: `_AUTO_EXEC_LOCK` → gateway/disarm_cb →
  `MICRO_SCALP_LOCK` (brief flag reads only). Never take `MICRO_SCALP_LOCK`
  around anything that takes the exec/auto locks.

**Why:** operator wanted the fast 5–10 pt liquidity-moment brain to first PROVE
itself via ghost stats, then be armable for real orders with an explicit,
restart-resetting second toggle (same fail-safe convention as the AUTO arm).

**How to apply:** any change here must keep mode-OFF byte-identity (attach seam
is the ONLY writer), keep the observer webhook-source-only, keep the single-exit
rule, and keep all DB work INSERT/SELECT/UPDATE on its own table (no DDL — prod
table arrives via Publish schema-diff).

**Gotchas learned:**
- `/status` has a 3s payload cache (`STATUS_CACHE_TTL_SEC`) — a toggle can look
  "ignored" for up to ~3s; smokes must zero the TTL, not poll-and-pray.
- Engine ladder: at trigger-TTL freshness, "price back inside the range" alone
  fires the reclaim trigger → TAKE; a trapped-but-WAIT test vector must park
  price BETWEEN range_high and the swept level.
- A sweep anchor further than stop_max from entry gets CLAMPED to stop_max — a
  "stop behind the sweep" assertion only holds for near-sweep entries.
- **Smoke tests can send REAL orders if the mode isn't forced correctly**:
  `resolve_execution_mode()` reads module-level `_EXECUTION_MODE_RAW` captured at
  IMPORT — setting `os.environ["EXECUTION_MODE"]` after import is TOO LATE (this
  actually fired one real TradersPost order during testing). Any smoke touching
  the gateway must set `app._EXECUTION_MODE_RAW = "manual_only"` AND hard-stub
  `app._send_broker_order` + `requests.post` (academy tripwire pattern).
