---
name: Trading Academy (learning-only knowledge module)
description: The /academy/* knowledge library — what it is, the hard isolation rule, and the invariants any change must keep.
---

# Trading Academy / AI Trading Library

A LEARNING-ONLY knowledge module (sources → AI-extracted lessons → strategy cards →
management rules → validation lifecycle → grounded Q&A). It is research/display, in the
same walled-off class as `/backtest` and `/scalp-research`.

## HARD SAFETY RULE (the whole point)
The Academy must NEVER affect live trading. It is fully walled off from the
gate / scoring / auto-execute / broker / sizing / dedupe / live-management path. Marking a
strategy `APPROVED` / `active` records *intent only* — it does NOT wire anything into live
execution. Actually wiring an approved card into live exec is a SEPARATE, future money-path
task (out of scope here).

**Why:** the user explicitly required isolation as a non-negotiable; an approval flow that
silently became an execution flow is the exact failure mode to prevent.

**How to apply:** any new academy route/helper must not call `_send_broker_order`,
`execute_trade_gateway`, or any dispatch/auto-execute sink, and must not make outbound
HTTP except the one read-only AI proxy call (`_academy_ai_chat`). The dedicated tripwire
smoke (`.local/state/academy_smoke.py`, run via `check_academy.sh`) monkeypatches every
broker sink + `requests.post` to record-and-raise, then exercises every academy route — it
fails if any route reaches the money path. The four strict goldens + parity stay
byte-identical because the Academy never touches the strict gate/scoring path.

## Persistence convention
INSERT/SELECT only, NO in-app DDL (same as the rest of the app). Tables
(`academy_sources`, `academy_strategies`, `academy_management_rules`,
`academy_strategy_sources` M:N, `academy_validation_events` audit) are created out-of-band
in dev via the database tool and in prod via the Publish schema-diff. Boot does a no-DDL
readiness probe that flips `ACADEMY_DB_READY` (fail-open); routes return a 503
"unavailable" JSON when the flag/DB is down.

## Extraction normalizer is a hard contract
`_academy_normalize_extraction` ALWAYS returns the same fixed key set (arrays always
present, scalars default to `""`, enums allowlisted, oversized strings clamped, unknown
`professional_thinking` keys dropped) and is idempotent. The dashboard hard-indexes these
keys, so a drift silently breaks the Academy view. The smoke pins this contract.

## Owner-only surface
Every `/academy/*` route is whitelisted in `artifacts/api-server/src/routes/flask-proxy.ts`
(Express `router.all([...])`, `:id` params supported) and is deliberately NOT in
`dashboard-auth.ts` OPEN_PATHS → reachable through the proxy but auth-protected (curl a
route through the running api-server on :8080 → 401, never 404, never 200). Request bodies
are redacted in the before_request logger (raw transcripts/notes must never hit logs).

## Status enum casing gotcha
`ACADEMY_STATUSES` are UPPERCASE (UNTESTED/BACKTESTING/PAPER_TESTING/APPROVED/REJECTED).
`_academy_status()` upper-cases input; the other enums (lifecycle/applies_to/source_kind)
are lower-cased via `_academy_enum()`. Don't route a status through `_academy_enum` or it
silently fails the allowlist.

## Workflow note
`check_academy.sh` is NOT registered as a Replit workflow — the project is already at the
14/10 workflow cap, so it runs via `bash .local/state/check_academy.sh`. Don't evict a
golden/parity/feature check to register it.
