---
name: Discord read-only viewer feasibility
description: Why a "view other people's Discord channels in the dashboard" feature is mostly infeasible, and the one compliant workaround.
---

# Discord read-only channel viewer — feasibility ceiling

A request to "show/monitor Discord channels inside the dashboard" runs into a hard
Discord platform limit, not an implementation gap.

**The rule:** the only compliant read path is a **bot token** (`Authorization: Bot …`
→ `GET /channels/{id}/messages`). A bot can read **only** channels in servers where
the bot has been added with View Channel + Read Message History. It **cannot** read:
- channels in **third-party servers** the user merely subscribes to (no admin there → can't add the bot),
- the user's **DMs**.

Dead ends (do not re-attempt):
- The Replit **Discord connector** is OAuth2/bearer-based; Discord does **not** let
  OAuth bearer tokens read guild message history.
- **User/"self" tokens** would read everything the user sees but are **banned** by
  Discord ToS — never use them.
- Existing `DISCORD_*_WEBHOOK_URL` secrets are **send-only**; webhooks can't read.

**The one workaround:** Discord's **Follow** feature. If a third-party service posts
in an **Announcement (News) channel**, the user can Follow it into a channel in
**their own** server, where the bot then *can* read it. Only viable if the source is
an announcement channel.

**Why this matters:** A user asked for this; their target channels were in other
people's servers, so the only honest answer was that it can't work. They opted to
**drop the build** (Jun 2026). A read-only `DISCORD_BOT_TOKEN` secret + a bot named
"Dashboard Viewer" were created during scoping and left in place, unused — no app.py
/ proxy / auth code was written.

**How to apply:** Before scoping any "view Discord in dashboard" work, first confirm
the target channels live in a server the user can add a bot to (or are announcement
channels they can Follow into their own server). If not, say so up front instead of
building.
