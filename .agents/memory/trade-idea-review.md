---
name: Potential Trade Idea Review (display-only)
description: Owner-only dashboard feature that grades a trader's manually-entered trade idea by reusing read-only engines; Option A = manual ticket only, never a money path.
---

# Trade Idea Review — `/review-idea`

Display-only "Potential Trade Idea Review" feature: the trader types a planned
trade (symbol/mode/direction/entry/stop/T1-T3/notes) and the bot grades it like
an analyst. Endpoint `@app.route("/review-idea", POST)` + helper
`_review_trade_idea(payload)`; owner-only (whitelisted in the Express `/api`
proxy, NOT in OPEN_PATHS).

**Option A (hard rule):** the "execute" affordance only returns a manual
`order_ticket` dict with a "place this yourself, nothing sent to a broker" note.
It MUST NEVER call the execution gateway / `/traderspost` / any order placement.

**Reuse, never recompute or mutate the live path:**
- Reads `full_analysis(ticker_override=inst)` and consumes the already-computed
  `result["directions"][dir]` (edge_score / conflict / potential_plan) as the bot
  plan — does not re-run `build_strict_trade_plan`.
- For memory it shallow-copies result, overrides `strict_direction` and a
  *freshly-copied* `strategy_engine` sub-dict to the user's direction, then calls
  `find_similar_trades`. (Shallow copy is safe only because the mutated sub-dict is
  re-copied first.)
- `get_volatility(inst).atr_pts`, `spec_for(inst)["point_value"]`, per-mode risk
  cap. Never mutates TRADING_MODE or global state. Goldens stay byte-identical.

**Scoring is transparent, never fabricated:** RR25 + market30 + entry25 +
memory10 + conviction10 = 100. Verdict: REJECT (max_safe<1 / rr<1.0 /
zone_blocks_t1 / severe direction conflict / total<50); APPROVE (>=75 & entryQ>=70
& rr>=1.5 & max_safe>=1 & no red flags); else MODIFY. rr uses T2's R if a valid T2
is given, else T1. A T2/T3 entered on the wrong side of entry is surfaced as a
visible warning (reasons_against) and R:R falls back to T1 — never silently dropped.

**Logging:** the screenshot/chart-link field can carry a signed token, so
`/review-idea` is exempted in `_log_incoming_request()` (logs byte count only). See
request-logger-redaction.md.
