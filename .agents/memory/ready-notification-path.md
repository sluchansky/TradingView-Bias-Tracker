---
name: READY notification path (dashboard bell + phone push)
description: The READY-setup alert path is already built and on by default; "no bell" is usually WAIT setups, not a bug. How to verify.
---

The dashboard bell AND the phone push for a READY setup are ALREADY built and ON
by default — when asked to "notify me of a READY trade", do NOT build new alert
plumbing; verify the existing path instead.

- Dashboard (tab open): maybeReadyAlert() (1s poll) + scanEdgeBells() (3s poll)
  ring on actionable / edge>=80; browser Notification via notifyReady(). Audio
  needs one user gesture (pointerdown/keydown) to unlock.
- Phone: send_live_ready_card(..., notify=True) on a FRESH READY prepends
  DISCORD_ALERT_MENTION (defaults "@everyone") + allowed_mentions -> pings a phone
  set to "Only @mentions". The re-post loop uses notify=False (no spam).

**Why quiet != broken:** if every setup is WAIT there is nothing to ring on, and
the phone also depends on the user's own Discord app / channel notification
settings. The MAIN deployed bot has DISCORD_LIVE_ENABLED=True (REPLIT_DEPLOYMENT)
and the READY card isn't gated by it anyway; the prod-log lines "ANALYSIS_ONLY
mode ENABLED" / "DISCORD_LIVE_ENABLED=False (dev instance)" come from the SECONDARY
analysis-only bot2 (api2, port 8001), NOT the live sender.

**Verify on demand:** owner-only POST /notify-test fires a real @everyone Discord
push + returns {sent, reason, muted_note}; the dashboard "Test alert" button also
rings the local bell + browser notification. Pure notification test — no gate /
scoring / sizing / journal / broker. Sends even when the instrument is muted (it's
an explicit test) but reports muted_note.

**Secret rule:** the send exception path must log type(exc).__name__ and return a
GENERIC reason — never str(exc), which a requests/urllib3 error can fill with the
webhook URL's bearer token (`/api/webhooks/<id>/<token>`), leaking it to logs+API.
