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
