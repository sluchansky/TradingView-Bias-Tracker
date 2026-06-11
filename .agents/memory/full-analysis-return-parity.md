---
name: full_analysis return-path key parity
description: full_analysis() has two return dicts that must keep identical keys; a missing key is a state-dependent 500 invisible to fresh tests.
---

# full_analysis() return-path key parity

`full_analysis()` in `artifacts/tradingview-webhook/app.py` has exactly two
`return dict(...)` statements:
1. the **main path** (normal scoring), and
2. a **zone-mitigated early return** gated on `ZONE_MITIGATED_FLAG and ZONE_BROKEN_AT is None`.

Both must produce the **same set of keys**. Consumers do hard `a["key"]` reads
(notably the `/status` route and the webhook handler), so any key present in the
main path but absent from the early-return path is a latent `KeyError` → HTTP 500.

**Why:** the zone-mitigated branch is reached only after a `… ZONE MITIGATED`
alert arms an **in-memory** flag. A freshly-imported / in-process call hits the
main path and looks fine, so the bug only surfaces on the live server after that
alert — and a workflow restart clears the flag, hiding it again. (`/status` 500'd
on `KeyError: 'stage_direction'`; the webhook never 500'd only because it doesn't
read that key.)

**How to apply:** whenever you add/rename a key in either return dict of
`full_analysis()`, mirror it in the other. To verify parity, AST-diff the keyword
sets of the two `return dict(...)` calls (`early - main` and `main - early` must
both be empty) rather than eyeballing. Consumers using `a.get(...)` (e.g. the
journal builder) are immune; the risk is the hard-indexed readers.
