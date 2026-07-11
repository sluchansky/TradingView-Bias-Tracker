---
name: Operator Mode UI
description: Home artifact root (/) rewritten as conversational AI Trading Partner with auth, streaming narration, and chat.
---

## Architecture
- **File:** `artifacts/home/src/pages/Home.tsx` (full rewrite)
- **Route:** `/` (home artifact, base path `/`)
- **Auth:** localStorage-persisted Basic Auth (`brain_auth` key); 401 → LoginOverlay → re-stores; same pattern as Cockpit
- **Data source:** polls `/api/status?ticker=MGC|MNQ` every 5s with `credentials:'include'` + Basic Auth header
- **Narration:** `data.main_brain_voice.narration` (from `compute_main_brain_voice`) → character-streamed via `useStream` hook (14ms/char)
- **Chat:** POST `/api/assistant` with `{question, ticker}` → returns `{ok, answer}`; BrainBubble streams each response
- **Engineering Mode:** `<a href="/api/dashboard">` — no route change needed

## Key invariants
- `OPEN_PATHS` in Express auth = only `/`, `/ping`, `/webhook` → `/api/status` requires Basic Auth
- `compute_main_brain_voice` already wired at full_analysis line 20804, exposed in `/status` at line 34948
- `/assistant` is owner-only (not in OPEN_PATHS) — same auth header needed
- `useTicker` rotates micro-thoughts every 3s; derived from `mb.signals`, `mb.learning_memory`
- Status orb color: READY=green (#4ade80), MANAGING=blue (#60a5fa), BUILDING=amber (#fbbf24), WATCHING=gray (#3f3f46)

**Why:** A minimal conversational front-end that surfaces the existing Brain narration and Q&A without touching the money path or dashboard.
