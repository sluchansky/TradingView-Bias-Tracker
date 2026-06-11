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

## VWAP misattribution gotcha (external constraint)
`webhook()` keys VWAP per instrument via `instrument_of(ticker or alert_type)`. Shared BOS/CHOCH
alerts carry **no** instrument prefix in their name, so they rely on the payload's `ticker` field.
**TradingView alert templates must always include `ticker`** — an MNQ alert that sends `vwap` but
omits `ticker` will store its VWAP under MGC (instrument_of default). This is a data-source contract,
not a code bug.
