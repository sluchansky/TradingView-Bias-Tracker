---
name: AI assistant chat panel
description: Read-only dashboard AI Q&A (/assistant) — what it is and the invariants any change must keep.
---

# AI assistant chat panel (/assistant)

A DISPLAY / READ-ONLY chat on the trading dashboard that answers (a) live-setup
questions (why WAIT, explain the edge score, what's blocking a trade) by grounding on
a `full_analysis` snapshot, and (b) general trading / education questions. Powered by
Replit AI Integrations (OpenAI proxy; keys auto-provisioned in env, never handled in
code).

**Invariants any change MUST keep (mirror every other display-only feature):**
- NEVER touch the gate / scoring / auto-execute / broker / sizing / dedupe path or
  mutate state. It only reads (`full_analysis`, env, request JSON) and makes one
  outbound AI call. Goldens / parity / instrument-isolation must stay byte-identical.
- Owner-only: the route is proxied but deliberately NOT in dashboard-auth
  `OPEN_PATHS`, so it inherits Basic Auth + same-origin CSRF. Do not add it there.
- The snapshot is the model's ONLY source of truth for live values (the system primer
  forbids fabrication) and the model must refuse to place / modify trades.
- **XSS:** model output is rendered into `innerHTML` via `aiEsc()` (escapes &, <, >).
  Keep it escaped — if you ever render markdown / links / HTML, run a trusted
  sanitizer first.
- The request logger has a metadata-only branch so questions are never echoed to logs.

**Why:** consistency with the strict money-path isolation every other display-only
engine here follows; an un-escaped model answer or an `OPEN_PATHS` slip are the two
ways this otherwise-safe feature could become a security hole.
