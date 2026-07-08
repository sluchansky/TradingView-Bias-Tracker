---
name: /status poll cache & dashboard poll guard
description: Why prod dashboard froze (poll storm x inline full_analysis) and the display-only single-flight cache + client tick guard that fixes it
---

# /status single-flight cache + dashboard poll guard

**The rule:** GET /status must never run full_analysis inline per poll in prod. It is served
through a single-flight TTL cache (`STATUS_CACHE_TTL_SEC`, default 3.0s, `0` disables → legacy
inline path). One request per key builds; concurrent pollers get the fresh or STALE cached copy
instantly; cold-cache concurrent pollers get a `503 {"status":"warming"}` instead of stacking
builds. Cache key = canonical instrument or `"__active__"`. The lock guards only the two dicts —
never held during a build.

**Why:** Prod froze completely: /api/status took 70–113s per request. A single full_analysis
pass in prod = 16–18s (full ALERT_HISTORY deque + 200+ DB trades; dev is empty so dev = 0.1s —
the dev/prod gap hides this class of bug). 3s polling × per-instrument sweeps × multiple devices
stacked requests faster than they finished → GIL saturation → total freeze.

**How to apply:**
- The money path NEVER reads this cache: webhook worker, watchers, heartbeat, /enter,
  /traderspost all call full_analysis()/their own server-side checks directly. Keep it that way.
- The /status route body lives in `_build_status_payload(_tk)` (returns a dict; route jsonifies).
  New payload keys go in the builder as before; the cache is transparent to them.
- Each build makes a FRESH top-level dict; cached payloads are never mutated after store.
- Client side: the 3s dashboard tick is wrapped in a `_pollBusy` guard (skip, never stack) and
  `api()` has a 15s AbortController timeout so one hung request can't stall all pollers forever.
  Any new poller added to the 3s tick inherits the guard; don't add separate bare setIntervals
  for heavy endpoints.
- Symptom signature if it regresses: prod deployment logs show /api/status 200s with tens-of-
  seconds latency + aborted requests; dashboard "Not responding" banner; dev looks fine.
