---
name: No-signals = upstream alert-config gap
description: Diagnosing a live "0 signals / nothing to trade" symptom on the TradingView webhook app when alerts ARE arriving but none are tradeable.
---

# "No signals" on the live webhook is usually an upstream alert-config gap, not a deploy bug

When the user reports "0 signals / still nothing" on the deployed webhook app, the deployment
is usually fine. The dominant root cause is that TradingView is only sending **neutral /
structure** alert types, which by design never produce a trade card.

**How to diagnose (do this FIRST, before touching code):**
Pull prod logs for `INCOMING POST /webhook` and read the `alert_type` in each BODY.
If you only see ZONE MITIGATED / ZONE BROKEN / VWAP (+ the junk `{{strategy.order.comment}}`),
the engine has nothing to score → correct silence, not a bug.

**Which alert types score vs. skip (ALERT_RULES + webhook flow in app.py):**
- Score-eligible *setup* alerts (engine builds a trade from these): `NEW SUPPLY ZONE`,
  `NEW DEMAND ZONE`, `SUPPLY ZONE CONFIRMED`, `DEMAND ZONE CONFIRMED`,
  `BULLISH CONFIRMATION`, `BEARISH CONFIRMATION`, plus `CHOCH`/`BOS` structure events.
- `ZONE MITIGATED` → sets `zone_mitigated_near` → logs "scoring skipped", returns WAIT
  ("zone consumed / no longer valid"). By design, NOT a bug.
- `ZONE BROKEN` → structure reset (neutral, score 0).
- `MGC VWAP` / `MNQ VWAP` → in `_DATA_ONLY_TYPES`, price refresh only, no scoring.

**Why:** the engine only posts an Edge Score / trade-card when `full_analysis` yields an
actual setup, which requires the directional building blocks above. A stream of neutral
events alone can never form a setup, so the dashboard/Discord stay empty.

**How to apply:** confirm via prod logs which `alert_type`s are actually arriving before
assuming a code/deploy fault. If setup alerts are absent, the fix is on the user's
TradingView side (create the missing alerts), NOT in app.py. Also set expectation: even
once setup alerts flow, a choppy market correctly scores WAIT (e.g. a real NEW SUPPLY ZONE
test scored "Choppy, Edge 14, WAIT") — signals are intentionally sparse, fired only on a
real edge.
