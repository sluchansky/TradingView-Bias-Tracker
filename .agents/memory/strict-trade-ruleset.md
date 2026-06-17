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
- **READY = zone_valid AND vwap_confirmed AND structure_confirmed AND not conflicting AND Edge≥80**,
  evaluated PER DIRECTION (candidate side chosen by which side of VWAP price sits on). A pre-existing
  **volatility BLOCK** also holds READY→WAIT (fail-open: only a hard BLOCK regime — see
  `volatility-monitor-gate.md`) — intentional safety, NOT part of the user's 5-term formula; keep it.
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
  score and the shown Edge Score can never diverge. NO 75-floor anymore; a READY is always ≥80 by
  construction. Session is a pure additive component (NEVER blocks, no longer READY-gated).
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
