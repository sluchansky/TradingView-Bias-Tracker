---
name: Micro Scalp Mode
description: Separate toggle-gated liquidity-moment engine (sweep→trap→absorption→micro trigger) — display + GHOST trades only, never the money path.
---

# Micro Scalp Mode (GHOST ONLY)

A SEPARATE engine from SCALP/SWING: hunts liquidity sweeps of prior highs/lows,
waits for the failed follow-through (trapped side), optional absorption read, then
a micro trigger (fresh aligned micro CHOCH/delta-flip/VWAP-reclaim webhook →
derived VWAP reclaim/reject from the price trail → close back inside range).
Verdict TAKE/WAIT/NO TRADE. MNQ geometry: TP1 +5 / TP2 +10 pts, stop behind the
sweep extreme CLAMPED 3–8 pts, BE only after TP1.

**Rules that must hold:**
- **Never a money path.** A TAKE only opens a GHOST row in
  `micro_scalp_ghost_trades` (webhook-source-only observer, one open ghost per
  instrument). No gate/edge/auto-execute/broker read touches the block.
- **Toggle OFF ⇒ key ABSENT.** `main_brain.micro_scalp` is only attached when the
  in-memory toggle is ON (owner-only `/micro-scalp`, resets OFF on restart).
  Flag-OFF is byte-identical — goldens stay green because the key never exists.
- **Pure engine lives in `micro_scalp.py`** (imports nothing from app.py); app.py
  only builds the read-only ctx and stores. Smoke: `.local/state/check_micro_scalp.sh`.
- **Ghost watcher starts UNCONDITIONALLY at boot** and self-gates INSIDE the loop
  on DB-ready + DISCORD_LIVE_ENABLED. Do NOT re-add a boot-time `if DB_READY`
  gate: the lazy probe in POST /micro-scalp can flip DB-ready at runtime and a
  boot-gated timer would leave open ghosts unresolved until restart.

**Why:** operator wanted a fast 5–10 pt liquidity-moment brain to WATCH (and
prove via ghost stats) without risking the live SCALP/SWING bot.

**How to apply:** any change here must keep toggle-OFF byte-identity (attach seam
is the ONLY writer), keep the observer webhook-source-only, and keep all DB work
INSERT/SELECT/UPDATE on its own table (no DDL — prod table arrives via Publish
schema-diff).

**Gotchas learned:**
- `/status` has a 3s payload cache (`STATUS_CACHE_TTL_SEC`) — a toggle can look
  "ignored" for up to ~3s; smokes must zero the TTL, not poll-and-pray.
- Engine ladder: at trigger-TTL freshness, "price back inside the range" alone
  fires the reclaim trigger → TAKE; a trapped-but-WAIT test vector must park
  price BETWEEN range_high and the swept level.
- A sweep anchor further than stop_max from entry gets CLAMPED to stop_max — a
  "stop behind the sweep" assertion only holds for near-sweep entries.
