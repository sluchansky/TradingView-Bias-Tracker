---
name: Market-session (CME/COMEX) awareness
description: How the LIVE bot pauses when MNQ/MGC futures markets are closed, and the ordering/invariants any change must keep.
---

# Market-session awareness (futures hours pause)

MNQ (CME Globex) and MGC (COMEX) share one schedule: **open Sun 18:00 ET → Fri
17:00 ET**, with a **daily maintenance halt Mon–Thu 17:00–18:00 ET**. A
`market_session_status(now=None)` helper returns `{open, status, next_open(UTC),
next_open_et, reason}`; a `MARKET_HOURS_ENABLED` flag (default true) turns the
whole feature off (always OPEN) for testing or 24/7 venues. Hours are computed in
`America/New_York`, so DST is automatic — never hard-code a UTC offset.

When closed, the spec is: don't count evals/WAIT as failed, show "MARKET CLOSED",
pause READY/ARMED, show next open + last valid price/time, and don't apply
confirmations/edge_score/session penalties.

**Why the closed-override must be LAST in full_analysis:** it lives in the single
final override block, AFTER strict/SWING scoring and AFTER the zone-broken /
zone-mitigated / conflict overrides, so "MARKET CLOSED" wins the displayed verdict
and is never masked by them. It still **appends every market key
unconditionally** (open or closed) so the single-return-path invariant holds
(see full-analysis-return-parity.md) — closed only *neutralizes values*
(verdict/label="MARKET CLOSED", alert_level/conviction_tier=None, edge_score=0,
directions ready=False/WAIT), it never removes keys.

**How to apply:**
- Adding a new override to full_analysis? Keep it BEFORE the market-closed block,
  or the closed banner can be clobbered.
- New market field on the wire? Add it to the `/status` whitelist too
  (curated-endpoint-serialization.md) — it won't serialize otherwise.
- Pause points when closed: `_run_heartbeat_evaluations` early-returns,
  `_record_eval_metrics` skips the COUNTERS funnel (req: don't count as failed),
  `_maybe_dispatch_early_alert` guards `market_open is False`. READY cards +
  tiered alerts pause naturally (verdict not READY, alert_level None).
- **Intentionally NOT paused:** other `_process_webhook_alert` non-trade side
  effects (zone-mitigated consumed-zone notice, ENTER/CLOSE trade-lifecycle).
  Closed markets mean TV rarely fires anyway, and ENTER/CLOSE must stay OPEN
  (dashboard-auth-edge.md). Only add a broad guard there if the user explicitly
  wants total closed-market Discord silence.
- `last_valid_data_for(ticker)` prefers the last *alert* price+ts, falls back to
  the auto-sourced price, so the closed banner shows the real last tape value.

## Exchange-holiday calendar (full-day + early-close half-days)

Holidays live ENTIRELY inside `market_session_status` so the money gate
(/traderspost), /status, heartbeat counters and dashboard banner all inherit them
with no other call-site change. It **reuses the existing `market_open` /
`market_reason` / `next_open` wire keys** — no /status whitelist change needed.

The calendar is computed algorithmically per year (no annual maintenance):
`_nth_weekday`, `_last_weekday`, `_easter` (Gregorian computus → Good Friday),
`_observed` (federal Sat→Fri / Sun→Mon shift), cached in `_HOLIDAY_CACHE`.
- FULL closures: New Year's, Good Friday, Memorial, Juneteenth, Independence,
  Labor, Thanksgiving, Christmas.
- EARLY (13:00 ET) half-days: July 3, day after Thanksgiving, Christmas Eve —
  each SKIPPED if it collides with a full holiday (e.g. when July 4 is a Saturday,
  observance makes July 3 a FULL Independence Day, not an early close) or a weekend.
- `MARKET_HOLIDAYS_EXTRA` env adds/overrides: comma list of `YYYY-MM-DD` (full) or
  `YYYY-MM-DD:early`. Whole feature gated by `MARKET_HOURS_ENABLED`.

**Why `_session_open_at(et)` is the single source of truth:** both the open/closed
decision AND the `_next_session_open` forward scan call it, so they can never
disagree. The next-open scan steps hourly **in UTC** (DST-safe — never add
timedelta to an ET-aware datetime across a DST boundary) and converts each probe
to ET for the wall-clock check.

**Conservative model (intentional):** a FULL holiday closes the entire ET calendar
day INCLUDING its evening session, so after e.g. Memorial Day the banner reports
the next open as the following day 00:00 ET, not the real ~18:00 evening Globex
reopen. This over-pauses by a few evening hours on a holiday — the SAFE direction
for a money gate, and aligned with the user's "full-day closure" choice. EARLY
days open under normal rules until 13:00 ET then close for the rest of the ET day;
the evening session (≥18:00 ET) also closes if the NEXT day is a FULL holiday.

**How to apply:** the weekend ("Weekend close") and daily-halt ("Daily maintenance
break") reason strings are unchanged — preserve them so non-holiday behaviour does
not regress. Any new closure type belongs inside `_session_open_at` (so the scan
stays consistent), not bolted onto the status function separately.
