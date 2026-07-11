---
name: Verdict Board
description: Main Brain layer that classifies every measurement into Supports/Opposes/Missing/Vetoes — one plain-English sentence each; wired at mb_out["verdict_board"].
---

## Rule

`compute_verdict_board(result, observations, conflict_resolver)` classifies every important signal into exactly one of four plain-English buckets. The operator must never need to combine two panels to understand the decision.

**Four buckets:**
- `supports` — evidence that favors entering right now
- `opposes` — evidence working against the trade
- `missing` — condition not yet present (would tip the balance if it arrives)
- `vetoes` — hard block; entry cannot happen regardless of other signals

**Classification sources (in order):**
1. `conflict_resolver.hard_vetoes` → vetoes
2. `result.edge_score` vs `EDGE_READY_THRESHOLD` → supports (≥thr) / missing (≥65% thr) / opposes (<65%)
3. 11 specialist observations by `obs["observation"]` (the obs_code) and `obs["source"]`:
   - Direction-aware obs_codes use `result.strict_direction` to route to supports vs opposes
   - Unknown direction → missing
4. `conflict_resolver.soft_disagreements` → opposes
5. `result.strict_reason` when not READY → missing as "Gate is waiting: ..."
6. `result.market_open == False` → missing (if not already in vetoes from BCR)

**Direction-aware helper `_dir(long_favored, long_text, short_text)`:**
- long_favored=True: long→supports, short→opposes
- long_favored=False: short→supports, long→opposes
- dir_known=False: → missing

**Return schema:**
```python
{
  "available":  bool,
  "supports":   [str, ...],
  "opposes":    [str, ...],
  "missing":    [str, ...],
  "vetoes":     [str, ...],
  "direction":  "long" | "short" | None,
  "summary":    str,   # counts-based summary sentence
  "reason":     "ok",
}
```

**Neutral:** `_vb_neutral(reason)` — available=False, empty lists.

## Why

The user's principle: every important fact must end in one of four categories. The operator should never need to combine two panels to understand the decision. This makes it possible to build a single dashboard panel that shows the complete trade decision in plain English.

## How to apply

- New specialist added to `_mb_build_structured_observations`: add a matching `elif src == "new_source":` block in `compute_verdict_board` classifying each obs_code to the right bucket.
- New hard condition to surface: add it to the conflict resolver (BCR) as a hard veto — it flows through to verdict_board.vetoes automatically.
- New soft signal: add it to BCR as a soft disagreement — flows to verdict_board.opposes automatically.
- The verdict board is purely downstream — it NEVER feeds the gate, sizing, or money path. Keep it that way.
- Consume via `result["main_brain"]["verdict_board"]`.
