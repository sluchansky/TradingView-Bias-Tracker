---
name: Structure-reversal demote
description: SCALP-only money-path layer that nulls a STALE opposing structure's credit when a fresh opposite reversal is clearly newer, so the dominant direction flips.
---

# Structure-reversal demote (STRUCTURE_REVERSAL_DEMOTE_ENABLED)

A single flag-gated block inside `evaluate_strict_setup`, placed right AFTER the
HH/HL/LH/LL swing-timestamp reads and BEFORE `structure_long/short` are computed. When
a FRESH opposite-direction structure event (BOS/CHOCH/swing) is CLEARLY newer than the
other side's newest structure event, it NULLs the OLDER side's structure timestamps +
`has_bos_*/has_choch_*` flags + that side's anchored `has_bull_confirm/has_bear_confirm`,
and sets `structure_demoted` = None/"demand"/"supply".

**Rule / invariants:**
- Guard is `STRUCTURE_REVERSAL_DEMOTE_ENABLED and not bool(cfg("VOL_HARD_GATE"))` —
  SCALP-only (mirrors the SCALP_VOL_BRAKE guard). SWING is byte-identical.
- Trigger = BOTH sides carry structure AND `abs(newest_dem_ts - newest_sup_ts) >
  CONFLICT_WINDOW_MIN*60`. It uses the SAME `CONFLICT_WINDOW_MIN` (10 min) as
  `opposing_present`/`true_conflict`, so there is NO dead zone: gap ≤ window stays owned
  by the existing conflict logic, gap > window is owned by the demote. Do not give the
  demote its own separate window constant or the two will overlap/gap.
- DEMOTE-ONLY / fail-safe: the block ONLY assigns None/False. It never sets a flag True,
  never adds credit to the fresh side, never creates/loosens a trade. The fresh side
  must still independently pass every gate.
- `structure_demoted = None` is written UNCONDITIONALLY (outside the `if`) so the nested
  `_gate_debug` closure never NameErrors; the gate_debug stamp is itself flag-gated
  (`**({"structure_demoted": ...} if FLAG else {})`) so OFF gate_debug is byte-identical.

**Why:** a stale opposite BOS/CHOCH kept feeding the losing side +20 structure credit
long after a fresh reversal, so the dominant direction flipped too slowly. User approved
"option 2": let a fresh opposite structure demote the stale side so direction flips on
the reversal. Consequence to remember: ONE fresh opposite CHOCH/BOS >10 min newer FULLY
wipes the stale side, so direction can flip on a single structure event — intended, and
bounded because it only removes eligibility.

**How to apply:** default OFF → byte-identical (all goldens green). Enabled LIVE via env
`STRUCTURE_REVERSAL_DEMOTE_ENABLED=1` on the deployment + republish (SCALP mode). Flag-ON
behavior is guarded by its own smoke (`check_structure_reversal_demote.sh`), NOT the
goldens (which pin it OFF). Watch `gate_debug.structure_demoted` on the first live
reversals to confirm real-tape behavior. Any edit to structure timestamps/flags in
`evaluate_strict_setup` must keep this single-write-point consistent — every downstream
consumer (structure_long/short, _signals edge, _confirmations, _gate_debug,
opposing_present, reaction_long/short) reads the nulled vars from this one point.
