---
name: Curated endpoint serialization
description: Why adding a key to full_analysis() is not enough to surface it on the dashboard — JSON endpoints whitelist keys.
---

# /status (and peers) return a curated subset, not the whole full_analysis dict

The read endpoints that the dashboard polls do NOT `jsonify(full_analysis(...))`
wholesale. They build an explicit dict and copy through only the keys they list. So a
new key added to `full_analysis()`'s return is computed correctly but is invisible on
the wire until it is ALSO added to each consuming endpoint's curated dict.

**Why:** this exact gap caused a long false-debug — a new `display_price` was being
populated internally (confirmed by logs) yet `/status` kept returning `None`, because
the value was never copied into the `/status` response dict. Time was lost chasing
threads/timing/Yahoo before realizing the endpoint just didn't serialize it.

**How to apply:** when you add a field meant to reach the dashboard, grep the route
handlers (e.g. `/status`, and any other curated read endpoints) and add the key to
each curated response dict — don't assume the endpoint returns the full analysis. To
diagnose "value present internally but `None` in the API response," check the route's
explicit dict before suspecting the producer, threads, or the data source.

**Exception — nested diagnostics ride free.** `alert_diagnostics` (and `gate_debug`) are
copied through to `/status` as whole nested dicts, so NEW diagnostic sub-keys added
inside them (e.g. `score_breakdown`, `components`, `volume_state`, `location_ok`,
`ready_blockers`, `raw_score`/`max_score`/`cap_applied`) surface automatically WITHOUT a
per-route edit. Prefer putting new per-setup diagnostics inside `alert_diagnostics`
rather than as loose top-level `/status` keys — fewer serialization gaps.
