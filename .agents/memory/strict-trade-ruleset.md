---
name: Strict trade recommendation ruleset
description: Non-obvious constraints behind the MNQ/MGC strict checklist verdict in the TradingView webhook app — what future changes must preserve.
---

# Strict trade recommendation ruleset (artifacts/tradingview-webhook/app.py)

The strict checklist evaluator is the **authoritative** verdict source inside `full_analysis`
(`evaluate_strict_setup` → `build_strict_trade_plan`). It replaced the old confidence-gate /
verdict-mapping block. Any future change to the final LONG/SHORT/WAIT decision must flow through
the strict path, not by re-adding a parallel confidence gate.

## Constraints any change must keep
- **The READY gate is MODE-TUNABLE — the old "byte-for-byte unchanged" invariant was EXPLICITLY
  OVERRIDDEN for SCALP by the user.** `_is_ready` reduces to per-`cfg()` keys: `(not require_zone or
  zone_valid) AND not conflict AND not vol_block AND Edge≥EDGE_READY_THRESHOLD AND (not require_vwap
  or vwap_confirmed) AND (not require_structure or structure_confirmed) AND confirmations≥MIN_CONFIRMATIONS`.
  - **SWING** (`GATE_REQUIRE_ZONE/VWAP/STRUCTURE`=True, `MIN_CONFIRMATIONS`=0, threshold 80) reduces
    EXACTLY to the historical `zone AND vwap AND structure AND Edge≥80` gate — **must stay so.**
  - **SCALP** (all three require_* False, `MIN_CONFIRMATIONS`=2, threshold **55**) demotes zone/vwap/
    structure to *confirmations* (counted toward the ≥2), NOT hard ANDs. A demoted zone STILL scores its
    25pt Edge component — it just no longer hard-blocks. So SCALP can fire READY with `zone_valid` False
    (e.g. Edge 55 from structure20+sweep15+vwap20). **Do not "re-tighten" SCALP thinking this is a bug.**
  - Req5 "auto-upgrade ARMED→READY" needs NO force path: a natural Edge climb (BOS/CHOCH→struct20,
    rejection candle→10, VWAP→20) crossing 55 flips `_is_ready`, which emits the existing trade card.
  - When `zone_valid` is False the SCALP READY `reason` must NOT claim "zone reaction" (gate on
    `gd["zone_valid"]`).
- A pre-existing **volatility BLOCK** also holds READY→WAIT (fail-open: only a hard BLOCK regime — see
  `volatility-monitor-gate.md`) — intentional safety; keep it.
- **alert_level vs conviction_tier are TWO SEPARATE fields by design — never merge them.** `alert_level`
  is the OPERATIONAL early-warning ladder (WATCH < ARMED < WATCH FOR ENTRY < READY); `conviction_tier`
  is the SCORE BAND (`_score_tier`: ARMED 40-54 / READY 55-69 / HIGH CONVICTION 70+). "WATCH FOR ENTRY" =
  in-zone AND confirmations≥needed AND Edge≥50 but gate not yet READY. Both display-only, never gate.
- **structure_confirmed = ANY ONE of** CHOCH/BOS in direction, HH/HL (long), LH/LL (short). It is NOT
  "BOS AND CHOCH" — that older both-required rule was the #1 cause of always-WAIT and was loosened.
- **zone_valid = trade-side zone MITIGATED + a same-direction REACTION** (5m confirmation candle OR
  zone-confirmed OR liquidity sweep). Mitigation alone is the old "consumed/stand-aside" state.
- **Conflict is recency-aware & structure-only (VWAP-independent):** opposing long/short structure
  timestamps within `CONFLICT_WINDOW_MIN` (10 min) → WAIT both sides, score 0. A STALE opposite
  structure must NOT block. **Why:** if conflict also required VWAP it is unreachable (price can't be
  both sides of VWAP); the old "any opposite in window" rule over-blocked.
- **VWAP equality → WAIT.** Gate uses strict inequalities (price > VWAP long, price < VWAP short).
- **Edge Score is the single additive helper `compute_trade_edge_components`** (zone25/vwap20/
  structure20/sweep15/candle10/session10, max 100) shared by the gate AND the display layer — the gate
  score and the shown Edge Score can never diverge. NO 75-floor; in SWING a READY is ≥80 by construction,
  in SCALP ≥55. Session is a pure additive component (NEVER blocks, no longer READY-gated).
- **Every WAIT names the failed gate(s)** via `reason`/`missing`/`gate_debug` (also on `/status` and the
  scored "Alert:" log `Gate:` field). Keep this per-gate debug surface when touching the gate.
- **Journal gates solely on `strict_label` ∈ (Strong Trade, Possible Trade); WAIT must never journal.**
  Dedup key is (instrument, direction, rounded zone_low); `JOURNAL_KEYS` is in-memory only (no expiry
  until restart) — same zone later in the day won't re-journal. Intended.
- **Zone-broken and zone-mitigated-near branches still reset the full strict payload** (strict_label=
  WAIT, strict_score=0, no-plan trade_plan); the display Edge Score is hard-zeroed by these blockers
  while `gate_debug.edge_score` reports raw components — expected, the blocker is the override.

## Instrument resolution contract (ticker is authoritative)
`webhook()` resolves the instrument once via `resolve_instrument(ticker, alert_type)` and stores it on
the alert record (`record["instrument"]`). The payload `ticker` field is the source of truth; the alert
title is consulted **only** when no ticker is present. Unresolvable/contradictory alerts are **rejected
with HTTP 400, never silently defaulted to MGC**:
- shared BOS/CHOCH with no ticker (instrument named nowhere) → reject
- unrecognized ticker (neither MGC nor MNQ) → reject
- ticker vs title-prefix mismatch (e.g. `MGC VWAP` + `ticker: MNQ1!`) → reject

Instrument-prefixed alerts that omit the ticker still resolve from the title (logged WARNING) — this is
the lenient choice; a stricter "every alert must carry a ticker" policy would reject those too.
`instrument_of()` is the **lenient legacy** normalizer (empty/unknown → MGC) — display/fallback only,
never for webhook attribution. All instrument-scoped consumers (`_has`, `_active_ticker`,
`get_price_context`, card build, live-card routing) prefer `record["instrument"]`.
**Why:** an MNQ alert that omitted `ticker` previously stored VWAP / counted structure under MGC, and a
shared BOS/CHOCH with a falsy ticker used to match BOTH instruments.
