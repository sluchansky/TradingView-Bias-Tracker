---
name: full_analysis single return path
description: full_analysis() now returns from ONE dict; hard-indexed consumers make any missing key a state-dependent 500. Mirror keys if an early return is ever re-added.
---

# full_analysis() return path

`full_analysis()` in `artifacts/tradingview-webhook/app.py` has exactly **one**
`return dict(...)`. The old **zone-mitigated early return** (gated on the
in-memory mitigation flag) was removed — the zone-mitigated case is now folded
into the single main dict, so there is no longer a second return to keep in sync.

**Why this still matters:** consumers do hard `a["key"]` reads (notably the
`/status` route and the webhook handler), so any key they expect but the dict
omits is a `KeyError` → HTTP 500. Historically the second (zone-mitigated) return
omitted `stage_direction` and `/status` 500'd only *after* a `… ZONE MITIGATED`
alert armed the flag — a freshly-imported / in-process call hit the main path and
looked fine, and a restart cleared the flag and hid it again. State-dependent,
invisible to fresh tests.

**How to apply:** if you ever re-introduce an early return inside
`full_analysis()`, mirror the full key set of the main dict (AST-diff the keyword
sets both ways rather than eyeballing). Consumers using `a.get(...)` (e.g. the
journal builder) are immune; the risk is the hard-indexed readers.
