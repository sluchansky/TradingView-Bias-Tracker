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
- **Conflict check keys on structure only (VWAP-independent).** Both-sides-confirmed → WAIT, score 0.
  **Why:** if the conflict test also required VWAP it becomes unreachable (price can't be both above
  and below VWAP), so simplifying it to use the VWAP-gated booleans silently breaks the stand-aside rule.
- **VWAP equality → WAIT.** Gate uses strict inequalities (price > VWAP long, price < VWAP short).
- **Score base is 75 on a full gate pass** (so any pass is at least "Possible Trade", 90+ = Strong).
  WAIT path score = round(present/4 × 70) ≤ 70, always < 75. Keep these ranges in sync with the labels.
- **Journal gates solely on `strict_label` ∈ (Strong Trade, Possible Trade); WAIT must never journal.**
  Dedup key is (instrument, direction, rounded zone_low); `JOURNAL_KEYS` is in-memory only (no expiry
  until restart) — same zone later in the day won't re-journal. Intended.
- **Zone-broken and zone-mitigated branches must reset the full strict payload** (strict_label=WAIT,
  strict_score=0, no-plan trade_plan), or `/status` shows "WAIT" alongside a stale score/plan.

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
