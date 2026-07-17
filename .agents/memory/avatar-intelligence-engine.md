---
name: Avatar Intelligence Engine v1
description: Proactive AI trading partner layer — event queue, daily greeting, explain-simply mode, memory placeholder.
---

## What was built (Phase 2)

JS-only engine layered on the existing 3s /status poll. Zero new API calls.

### Hook point
`mbAvatarObserve(d)` is called at the end of `renderModules(d)` inside a
`try/catch` so avatar failures never interrupt rendering.

### State tracking
`_avPrev` holds the previous poll snapshot (sk, verdict, edge, opened_at,
has_trade, inst, market_open). Delta detection on every tick.

### Event queue
`_avQueue` + `_avDequeue()` dequeues one item at a time with `_AV_GAP=5000`ms
gaps. Per-type cooldowns in `_avCooldowns` (`_AV_CD` map).

### Events detected (in priority order)
1. `trade_opened` — `opened_at` changed while `has_trade` is true
2. `trade_closed` — `has_trade` went true → false
3. `ready` — `sk` transitioned to READY from non-READY
4. `verdict_change` — `sk` or `verdict` changed (HUNTING/MANAGING/WAITING/BLOCKED)
5. During open trade — no state-change events (avoid distraction)

### Daily greeting
`_avCheckGreeting()` fires once per ET calendar day (approx UTC-5 gate via
`localStorage['mbGreetDate']`). Delayed 2.5s on first tick so rendering completes.

### Text generators (deterministic, no AI calls)
`_avGreetText`, `_avReadyText`, `_avVerdictChangeText` — short, direct phrases.

### Ambient narration (`mbSpeak`)
Updated from no-op: speaks caption text when TTS is on, text changed, 45s
since last ambient speak, and no proactive speech is currently active.

### Voice interrupt
IIFE on script load attaches `keydown` to `#mb-chat-input` → calls `mbStopSpeaking()`.

### Explain-simply mode (backend)
`_assistant_answer()` detects phrases ("explain simply", "simpler", etc.) in
the question and appends a beginner-friendly instruction to `system_primer`.
The `_explain_simple` bool is computed right after question sanitization.

### Memory placeholder
`var mbMemory = { findSimilar, strategyStats, recordOutcome, recentSummary }`
— all return resolved Promises. Interface contract for a future phase.

**Why:**
All proactive code wrapped in try/catch — avatar MUST NEVER interrupt the
trading/alert path. Reuses existing poll data so no new latency is added.
