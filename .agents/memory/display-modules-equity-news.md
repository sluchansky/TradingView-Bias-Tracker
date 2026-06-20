---
name: Dashboard display modules — equity curve & news filter
description: The two newest /status-fed dashboard modules, why they are display-only, and the one design line future work must not cross.
---

# Equity curve (today) & News filter modules

Both are ADDITIVE, DISPLAY-ONLY modules fed through the existing `/status` (no new
Flask routes), rendered alongside the strategy/learning modules on the dashboard.

**Equity curve (today):** cumulative-R of trades CLOSED today in ET, from real
`strategy_trades` rows. There is NO backfill — it is empty until trades close today,
which is the correct/honest state, not a bug. The server helper is read-only,
short-cached under a lock, and FAIL-OPEN (returns an honest empty/unavailable payload,
never 500). Rendered as an inline SVG.

**News filter:** real ForexFactory economic-calendar feed
(`ff_calendar_thisweek.json`, public, no auth), USD High-impact only, cached
server-side with a single-flight daemon refresh; `/status` reads cache only and never
blocks; FAIL-OPEN. A `within_window` flag is computed for DISPLAY. On a quiet day
(e.g. all of the week's high-impact events already past) `high_impact_count` of 0 is
honest, not a parse failure.

**The line that must NOT be crossed — news is DISPLAY-ONLY.**
**Why:** the user explicitly scoped news as informational only; wiring it into the
strict gate is a SEPARATE money-path decision that must be confirmed first.
**How to apply:** if asked to "block trades around news", treat it as a money-path
change — confirm with the user, and keep the existing display path intact. Nothing in
the gate / sizing / dedupe path may read `news_filter`.

**Threading invariant:** both keys (`equity_curve_today`, `news_filter`) live in BOTH
`full_analysis` returns (open + closed-override) and are whitelisted in the `/status`
dict — see `full-analysis-return-parity.md` and `curated-endpoint-serialization.md`.
