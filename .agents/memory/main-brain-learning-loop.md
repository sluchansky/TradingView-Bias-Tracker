---
name: Main Brain Performance Review & Learning Loop
description: Display-only event-capture + outcome-review + deterministic-lessons subsystem on the trading dashboard; its invariants and the fail-open lock discipline.
---

The Main Brain learning loop is STRICTLY observability/display-only: it captures trade
& rejected-setup events into the `main_brain_events` table, resolves WAIT
"would-have-worked" hypotheses via a background price-sampling resolver, computes review
aggregates (win rate, avg R, best/worst setup_type, top rejection/loss reason, best/worst
ET entry hour, WAIT accuracy), and surfaces deterministic lessons. It NEVER touches the
strict READY gate, sizing, dedupe, or /traderspost.

**Why display-only is golden-safe by construction:** the 4 goldens drive
build_strict_trade_plan / evaluate_strict_setup directly with synthetic inputs — they
never call full_analysis / compute_main_brain, so any review/lesson/snapshot change is
golden-safe. Still confirm with the 4 checks every phase.

**Persistence discipline:** INSERT (ON CONFLICT idempotency_key DO NOTHING) + SELECT + a
status UPDATE on pending-hypothesis resolve; NO in-app DDL (table created via DB tool in
dev / Publish schema-diff in prod, like the rest of the learning engine). A readiness
probe sets the DB-ready flag; everything fail-opens to "no history" when the table/DB is
absent.

**Lock discipline (the load-bearing rule):** the review-cache recompute must do ALL DB
connect/query/aggregation with NO review lock held. Use a SEPARATE non-blocking guard
lock (acquire(blocking=False)) so only one recompute runs at a time, build the new
snapshot into a LOCAL dict, and hold the review lock only for the single atomic global
rebind. Reader paths (review snapshot + lessons) acquire with a short timeout and fall
back to a lock-free read (the rebind is atomic under the GIL, so a reader can copy the
current dict without tearing).
**Why:** an earlier version held the review lock across the DB query; with
connect_timeout≈5s + statement_timeout≈8s, a slow/down learning DB would block every
/status and full_analysis reader on a display-only cache read — a fail-open violation
caught in code review.
**How to apply:** any new cached-from-DB display layer on this stack must keep DB I/O
outside the lock that request paths read under.

**Vocab coupling:** setup_type must match across event-writers and the lessons reader —
the regime source (result["market_regime"] / ctx.regime||ctx.strategy) is the setup_type;
mode is TRADING_MODE; the lessons index is keyed "INST|MODE|SETUP" capped per bucket. A
mismatch silently yields zero lessons (not an error).

**Serialization & render:** both /status whitelists pass the whole main_brain block, so
nested performance_review + lessons flow through with NO whitelist edits. Frontend render
is DOM/textContent only, guards None/empty subkeys (all None on an empty DB), BMP emoji
glyphs only (no \\u surrogate escapes → UTF-8-encode 500), no raw backslash escapes in
the triple-quoted dashboard JS (a stray \\n there is a whole-script SyntaxError).
