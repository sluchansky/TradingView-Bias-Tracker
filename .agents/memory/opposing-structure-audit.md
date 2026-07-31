---
name: Opposing-structure conflict rule audit
description: Complete trace of the 10-minute opposing-structure suppression rule in evaluate_strict_setup, with all edge-case findings from Phase 7H.
---

## The Rule (evaluate_strict_setup, app.py ~line 6935)

```
CONFLICT_WINDOW_MIN = 10 min (600 s)

opposing_present = bool(
    long_struct_ts AND short_struct_ts
    AND abs(long_struct_ts - short_struct_ts) <= 600 s)

true_conflict:
  SCALP (CONFLICT_SCORE_AWARE=True):  opposing_present AND conflict_gap <= CONFLICT_WAIT_GAP(10)
  SWING (CONFLICT_SCORE_AWARE=False): opposing_present (always blocks)

Effect: true_conflict → label=WAIT, score=0, direction=None  [HARD BLOCK]
```

## Timestamp Source
Server ingestion time only (`now_utc().isoformat()` at webhook receipt).  
NOT TradingView candle close time, NOT Databento bar timestamp.  
The 600-second gap is measured between the two SIDES' most-recent ingestion timestamps.

## Key Behavioral Facts (all verified by tests)

1. **Gap is between the two sides, not absolute age from now.**  
   A bearish CHOCH at 11 min ago + bullish BOS at 3 min ago → gap = 8 min ≤ 10 min → still `opposing_present`.

2. **Duplicate events refresh the timer.**  
   `_latest_ts()` picks the most-recent timestamp per alert type. A Pine script emitting BOS/CHOCH every bar keeps the block alive indefinitely. `_audit_event_duplicates()` is diagnostic-only; it does NOT prevent ALERT_HISTORY from receiving duplicates.

3. **No supersession or invalidation tracking.**  
   A fresh same-direction BOS does NOT supersede or invalidate the opposing CHOCH. Both events remain in ALERT_HISTORY until they age out of `STAGE_WINDOW_MIN` (SCALP: 30 min; SWING: last 8 alerts). Price reclaim has no effect.

4. **Instrument scoping is correct.**  
   `_latest_ts()` applies `a_inst != inst` filter. MGC structure never blocks MNQ and vice versa.

5. **Missing timestamps fail safe.**  
   `_latest_ts()` skips events with malformed/absent timestamps (`continue` on ValueError). They default to "absent", never to "recent".

## What Was Added (Phase 7H, no trading logic changed)

- `_build_opp_struct()` closure in `evaluate_strict_setup`: computes read-only diagnostic dict, added to `_ret()` payload as `opposing_structure`.
- `_mb_verdict()`: forwards `r.get("opposing_structure")` to the verdict block (both success and error paths).
- `_mb_decision_timeline()`: adds `STRUCTURE_EVENT` entries from `ALERT_HISTORY` (BOS/CHOCH/HH/HL/LH/LL, instrument-filtered, last 10 real events).
- `MainBrain.tsx VerdictPanel`: BLOCKING ALERT section (red, with age / window remaining / candidate direction / effect / source) or NO HARD ALERT BLOCK (green) when `opposing_structure` is present in the verdict.
- `test_opposing_structure.py`: 27 checks across 8 cases (A-H).

## Effect Classification in opposing_structure.effect
- `NONE` — no opposing structure detected
- `OBSERVED` — opposing structure exists but not blocking (SCALP dominant side clear)
- `SCORE_AWARE_BLOCK` — SCALP conflict where both sides are balanced (gap ≤ 10 Edge pts)
- `HARD_BLOCK` — SWING always-on block
