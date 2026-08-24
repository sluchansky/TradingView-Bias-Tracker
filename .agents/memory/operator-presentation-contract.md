---
name: Operator presentation contract
description: Rules for keeping operator-facing verdict, direction, VWAP, and structure copy internally consistent.
---

Operator-facing surfaces must consume one backend-built display projection created only after the authoritative analysis and its display overrides are complete. A directional WAIT is a **candidate**, never an executable direction; only an actionable verdict can populate the actionable direction.

**Why:** Independent “favored” or dominant-direction fields can contradict the strict gate (for example, narrating a Short WAIT as leaning Long), while stale display VWAP text can disagree with the actual price/VWAP relationship.

**How to apply:** Keep the projection out of all money paths. Add new operator wording, verdict labels, narration, Decision Clarity, Waiting For, and Market Structure copy as consumers of the projection rather than recomputing or selecting from secondary analysis blocks. Preserve explicit null actionable direction for WAIT candidates.