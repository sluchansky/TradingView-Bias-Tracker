---
name: Zone presence must come from data, not display strings
description: Why Edge Score / confluence "zone active" must be grounded in nearest_demand/nearest_supply, never parsed from the human-readable Zone string.
---

The Zone display string (`supply_demand_zone` from `build_structure_fields`)
defaulted to `"{Demand|Supply} intact"` whenever the zone was not broken/consumed —
**even with no nearest zone level at all**. The Edge Score then derived
`zone_active = "intact" in supply_demand_zone`, so an empty system (no alerts,
`nearest_demand`/`nearest_supply` null) got a phantom "Demand Zone Active" +5 and a
"Demand intact" Zone field. `build_setup_notes` had the same flaw.

**Rule:** Scoring/confluence presence must be grounded in the actual analysis data
(`nearest_demand` for long, `nearest_supply` for short), NOT inferred by substring-
matching a string built for humans. Display strings should also stay honest — emit
"—" / "None" when there is no zone, never a default "intact".

**Why:** Display strings are formatted for readability and carry default words;
treating them as a data source fabricates signals the system never received. This
surfaced as a user-visible "that doesn't seem right" phantom on the dashboard.

**How to apply:** Any new Edge Score reason, confluence, or AI-note line must gate
on the real boolean/numeric in the `full_analysis()` result, and require the side-
appropriate zone level to exist before crediting a zone. Keep MGC/MNQ symmetric
(long→demand, short→supply).
