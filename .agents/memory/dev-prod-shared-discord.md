---
name: Dev/prod share Discord webhook secrets
description: Why duplicate Discord/external alerts happen and how the webhook bot gates unconditional notifiers to the single live instance.
---

# Dev and prod share env secrets → unconditional notifiers double-fire

In this Replit project the environment secrets (including
`DISCORD_WEBHOOK_URL`, `DISCORD_MNQ_WEBHOOK_URL`, `DISCORD_JOURNAL_WEBHOOK_URL`)
are the **same** in the dev workspace and the published deployment. Both run the
exact same `artifacts/tradingview-webhook/app.py`. So any **unconditional /
time-based external side-effect** runs from BOTH the dev instance and the prod
instance and the user sees every message twice.

**Rule:** Gate unconditional/time-based Discord senders to the LIVE (prod)
instance only. The bot uses `DISCORD_LIVE_ENABLED` =
`REPLIT_DEPLOYMENT == "1"` OR `DISCORD_LIVE == "1"`; the `__main__` block starts
the heartbeat / EOD / weekly / trade-ready re-post schedulers only when that is
true. The webhook worker and VWAP auto-fetch loop stay ungated (they do not post
to Discord on their own) so the dev dashboard keeps working.

**Why:** Confirmed double heartbeats — prod was a single clean 300s chain and the
dev workspace instance added a second offset 300s chain to the same channel.

**How to apply:**
- `REPLIT_DEPLOYMENT` is set to `"1"` only inside a Replit deployment (verified
  unset in the workspace) — it's the reliable prod-vs-dev signal.
- `scripts/prod-start.sh` (the prod entrypoint) exports `DISCORD_LIVE=1` as
  belt-and-suspenders. **Any future prod entrypoint must keep this**, or prod
  goes silent (no heartbeat/EOD/weekly).
- Any NEW unconditional or scheduled external notifier must be gated the same
  way. Webhook-driven sends are intentionally NOT gated because TradingView only
  POSTs alerts to the prod URL, so dev never forwards a real alert.
- **Testing caveat:** because the webhook inline READY card (`send_live_ready_card`
  at the end of the webhook worker) is ungated, manually POSTing a READY-forming
  sequence to the DEV `/webhook` WILL post a real trade card — with `@everyone`
  (`notify=True`) — to the shared live channel. The startup log
  "`DISCORD_LIVE_ENABLED=False … trade-ready … disabled`" refers ONLY to the
  periodic 5-min re-post loop, NOT this first inline post; don't read it as "dev
  won't post READY cards." `send_live_ready_card` logs nothing on success (only on
  failure/skip), and `_register_managed_trade` runs AFTER the Discord POST inside
  it, so a "Managed trade registered" log with no "post failed"/"URL not set"
  warning is strong evidence the card actually posted.
