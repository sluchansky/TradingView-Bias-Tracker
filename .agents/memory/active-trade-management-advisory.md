---
name: Active Trade Management advisory
description: DISPLAY-ONLY open-position monitor on /status; and the shared compute_distances/compute_manual_trade_management None-target2 constraint that any bot-mirror caller must respect.
---

# Active Trade Management advisory (open-position monitor)

DISPLAY/ADVISORY-ONLY layer that, for every OPEN bot position, surfaces a health
score, thesis_status (VALID/WEAKENING/INVALID/PAUSED/UNKNOWN), suggested_action
(HOLD/MOVE STOP/EXIT EARLY/TAKE PARTIAL/MONITOR), hold_reason, exit_warning and a
per-signal `checks` list on the dashboard's 3s `/status` poll.

- Flag `ACTIVE_TRADE_MGMT_ENABLED` (default ON). Block helper returns `None` when OFF
  or no open positions → `/status` key is null → panel hides. Strictly FAIL-OPEN
  (any exception → None), so it can NEVER 500 `/status`.
- It COMBINES, never recomputes: `_bot_hold_score_core` (flag-independent health) +
  `compute_manual_trade_management` on a THROWAWAY mirror COPY. `_compute_bot_hold_score`
  is now just a thin flag-gated wrapper over the core.
- Money-path untouched: never gates/scores/sizes/dedupes/sends/persists. Goldens cover
  scoring/gate (byte-identical); this display key is new and uncovered → guarded by its
  own smoke (`active_trade_mgmt_smoke.py` / `check_active_trade_mgmt.sh`, script-only,
  no workflow — limit is 14/10).

## Mirror-COPY discipline (shared with the Trade Monitor)
`_bot_trade_mgmt_mirror(inst, at)` builds the manual-trade-shaped COPY consumed by both
the Trade Monitor and this advisory. **Always pass the copy, never the live ACTIVE_TRADE**
— `compute_manual_trade_management` writes `min_r`/`max_r` back onto the dict it is handed.

## compute_distances / compute_manual_trade_management assume target1 AND target2
`compute_distances` does `target2 - price` unconditionally, and the mgmt function uses
`trade["target1"]` directly for `t1_hit`. So a trade fed in MUST have non-None target1
AND target2.

**Why:** bot positions can carry `target2=None` (SWING 1:1, or a SCALP before TP2 is
set). The Trade Monitor + this advisory feed bot positions through the mirror, so a
`None - float` TypeError crashed the read (fail-open → the position silently dropped
from the panel). Manual Trade Manager trades always had all targets, which masked it.

**How to apply:** the fix guards `target2` for the DISTANCE math only (falls back to
`target1`) while `t2_hit` still reads the real (possibly None) `target2` → honest
`dist_to_target2=None` / `target2_hit=False`. It is a strict no-op when target2 is
present (manual trades + goldens byte-identical). Any NEW caller that hands a bot/mirror
trade to compute_distances/compute_manual_trade_management must provide a non-None
target2 (or guard) the same way.
