---
name: Zone Mitigated detection & cross-instrument flag bleed
description: ZONE_MITIGATED_FLAG is a GLOBAL in-memory flag; derived signals must be gated on per-instrument price proximity or MGC/MNQ analysis bleeds into each other.
---

# Zone Mitigated detection

A *mitigated* demand/supply zone that **reacts** is a high-conviction signal:
"mitigated demand reacting" → GO LONG only when ALL hold — the mitigation flag is
armed, price is near a mitigated **demand** price for THIS ticker, there is a
bullish CHOCH / reaction confirmation, and price is above VWAP. A **broken** zone
still blocks; an **unconfirmed** touch still WAITs.

## The cross-instrument bleed trap (the real bug, took several passes)

`ZONE_MITIGATED_FLAG` (and the broken/confirmed companions) are **global**
in-memory flags. They are set by `… ZONE MITIGATED` alerts and **cleared by ANY
structure-reset alert** (CHOCH / BOS / NEW ZONE) — including alerts for the
*other* instrument. So a derived signal like `zone_mitigated_near` must be gated
on **per-instrument price proximity**, not on the global flag alone:

    ZONE_MITIGATED_FLAG and (near_sup_mz or near_dem_mz)
        and not zone_broken_active and not mitig_confirmed

If you write `(FLAG or near_sup_mz or near_dem_mz)` the global flag leaks: an MGC
mitigation alert flips MNQ's analysis (and vice-versa). Always AND the global
flag with this-ticker proximity.

**Confirmation display:** the confluences/confirmation line must treat a
mitigation-confirmed long as a confirmation source
(`has_bull_confirm or mitigation_long_confirmed`), otherwise a genuinely
confirmed mitigation entry renders as "unconfirmed" on the card.

**Why:** the global flags are cheap to read but have no instrument identity;
proximity is the only per-instrument anchor. Skipping the AND caused live
MGC↔MNQ cross-talk that fresh single-instrument tests never reproduced.
