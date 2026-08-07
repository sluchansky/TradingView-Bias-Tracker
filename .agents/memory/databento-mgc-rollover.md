---
name: Databento MGC contract rollover fix
description: MGC Databento subscription must follow TradingView's early volume-roll, not the calendar front-month; env var override available.
---

# Databento MGC Contract Rollover

## Rule
MGC.c.0 (Databento front-month by calendar) lags behind TradingView's MGC1! volume roll by several weeks. When TradingView rolls to the next active contract, Databento's continuous `MGC.c.0` stays on the near-month until it fully expires — causing a price gap and near-zero bar count.

## How to apply
- When you see: few MGC bars (0–2) AND price gap >$30/oz between Databento close and TradingView webhook price → rollover mismatch.
- Fix: change `DATABENTO_MGC_SYMBOL` env var to `MGC.c.1` (second month). The var is read at startup in `databento_brain.py` (`_DB_MGC_SYMBOL`).
- After the near-month expires and Databento's continuous rolls: revert `DATABENTO_MGC_SYMBOL` to `MGC.c.0` (or remove it to restore the default).

## Current state (Aug 2026)
- `MGC.c.1` = MGCV6 (October 2026) — currently correct, matches TradingView MGC1!
- `MGC.c.0` = MGCQ6 (August 2026) — near-expiry, almost no volume

## Rollover schedule (COMEX Micro Gold — every 2 months)
Active months: Feb, Apr, Jun, Aug, Oct, Dec
Typical volume-roll: 3–4 weeks before last trading day of near-month.

**Why:** COMEX gold volume migrates to the next active month well before calendar expiry. Databento's continuous contract follows OI, not just calendar, but still lags traders' volume move by days-to-weeks.
