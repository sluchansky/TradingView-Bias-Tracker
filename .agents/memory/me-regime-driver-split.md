---
name: ME regime vs primary_driver split
description: Market Environment Phase 1A two-field architecture; regime (condition) and primary_driver (cause) are independent fields; how they combine into risk_state.
---

## The Rule
`regime` and `primary_driver` are independent fields. Never put a cause (geopolitical, Fed, inflation) into `regime`.

- `regime` — "What is the market DOING?" → RISK_ON / RISK_OFF / MIXED / NEUTRAL / UNKNOWN
- `primary_driver` — "WHY is it doing that?" → GEOPOLITICAL_ESCALATION / FED_DRIVEN / INFLATIONARY / ECONOMIC_DATA / EARNINGS / NONE
- `risk_state` — derived from BOTH: RISK_OFF + GEOPOLITICAL → SHOCK; RISK_OFF alone → DEFENSIVE; RISK_ON → AGGRESSIVE/BALANCED

**Why:** The old architecture put GEOPOLITICAL as a regime value that competed with RISK_OFF in a priority chain. This meant a market could never be RISK_OFF with a non-geo cause, or RISK_ON while geo news was active. The result was "GEOPOLITICAL" and "RISK_OFF" were mutually exclusive when they should be orthogonal.

**How to apply:**
- The regime block (step 5 in `_compute_market_env_inner`) reads ONLY price/CVD signals — never news categories.
- The driver block (step 6) reads ONLY `news_category` — never market signals.
- `risk_state` (step 7) combines them.
- `_me_futures_preference` uses `_risk_off_like = (regime=="RISK_OFF" or primary_driver=="GEOPOLITICAL_ESCALATION")` to score gold/equity directionality.
- Adding a new driver: add it to `_DRIVER_MAP` in step 6. No other block needs to change.
- `dominant_theme` reads from regime first, then falls back to driver for nuance (e.g. RISK_OFF + GEO → theme=GEOPOLITICAL_ESCALATION).

## Test invariants
- RISK_ON market + geo news → regime=RISK_ON, driver=GEOPOLITICAL_ESCALATION, risk_state=BALANCED (not SHOCK)
- RISK_OFF market + geo news → regime=RISK_OFF, driver=GEOPOLITICAL_ESCALATION, risk_state=SHOCK
- RISK_OFF market + no news → regime=RISK_OFF, driver=NONE, risk_state=DEFENSIVE
- Schema: `primary_driver` must be present in both the live snapshot AND the error-fallback dict.
