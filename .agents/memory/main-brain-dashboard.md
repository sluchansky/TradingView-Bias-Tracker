---
name: Main Brain dashboard consolidation
description: How the trading dashboard's analyst-family panels were consolidated into ONE display-only "Main Brain" command center, and the non-obvious hiding/layout gotchas.
---

# Main Brain dashboard consolidation

The cluttered analyst-family dashboard boxes were folded into ONE display-only
"Main Brain" command center: a status badge + 4 brain-view lists
(Market/Strategy/Risk/Trade Manager) + a client-side scrolling plain-English
narration, all driven by a server-computed `result["main_brain"]` block.

**Rule: this whole feature is display-only.** It consumes the already-assembled
full_analysis blocks (analyst/debate/pro/entry-quality/volatility/edge/verdict),
never recomputes, never touches gate/sizing/dedupe/broker/traderspost. The
top-level key must be on the `/status` curated whitelist or it is `None` on the
wire. Goldens stay byte-identical.

## Hiding redundant panels — two traps

**Trap 1: render JS re-shows statically-hidden panels.** The 5 analyst panels are
written with inline `style="display:none"` BUT their render functions set
`mod.style.display=''` whenever their engine is enabled (i.e. during market
hours), so a static `display:none` is NOT enough. Hide them with a
`.mb-hidden{display:none !important}` CLASS instead — a stylesheet `!important`
declaration beats an inline non-`!important` `style.display` assignment, so the
render JS can stay intact and the panel still stays hidden.
**Why:** leaving render JS untouched keeps the change minimal/display-only; the
`!important` is what actually wins against the inline toggle.
**How to apply:** add `mb-hidden` to the container's class list; keep the render
function as-is. Would only fail if code later removed the class/rule or used
`style.setProperty('display','block','important')` (nothing does).

**Trap 2: `mod-report` id is DUPLICATED.** There are TWO `id="mod-report"`
panels — Unified Analyst Report (hide) and Performance Report (KEEP). An
`#mod-report` CSS/id selector would hit BOTH. Hiding via a CLASS on the specific
Unified Analyst Report container avoids clobbering Performance Report. Never use
an id selector to hide one of a duplicated id.

## Layout version reset

The per-device collapse/reorder layer (`dashCollapsed`/`dashOrder` in
localStorage) sorts any panel NOT in a saved `dashOrder` to the bottom (index
`1e9`). A newly-added panel (Main Brain) would therefore sink to the bottom for
existing users. Fix: a one-time reset keyed by a `dashLayoutVer` marker — if the
stored version != current `VER`, clear both keys once and write the new version,
so everyone falls back to default DOM order (Main Brain on top) exactly once;
later manual reorders persist again.
**How to apply:** when you add/remove a panel that should change default order,
bump `VER` deliberately.

## Main Brain is INTERACTIVE (chat lives inside the panel)

The Main Brain panel embeds an "Ask the brain" chat (`mb-chat-*` ids, `mbAsk`/
`mbChatSend`/`mbChatRender`, own `mbChatHistory`) + the owner's quick-action
buttons. It does NOT have its own backend — it reuses the existing read-only
`/assistant` endpoint (see `ai-assistant-chat.md`). New ids/fns deliberately do
NOT reuse the legacy assistant panel's `ai-*` ids (that panel is hidden via
`mb-hidden` but still in the DOM, so reusing its ids would collide).
**Why:** the grounded Q&A capability already existed inside the now-hidden
assistant panel; surfacing it in Main Brain is the partner experience.
**How to apply:** the chat is rendered ONCE on init (`mbChatRender()` in the load
sequence), NEVER from the 3s poll/`renderMainBrain` — the poll touches only the
specific child ids (badge/summary/lists/feed/foot), never the chat log, so chat
state survives polls. Keep model output rendered via `textContent` (XSS-safe).

## Command-center upgrade (mission / bias / cases / What-Changed / MANAGING)

The panel was extended into a command center, still STRICTLY display-only. New
`compute_main_brain` keys are ALL derived from already-assembled reads (never
recomputed): `mission[]`+`mission_progress` (booleans from vwap side / zone
presence / structure_label / cvd_state / entry_quality), `confidence_pct`,
`long_bias_pct`/`short_bias_pct` (lean from `result["directions"][L/S].edge_score`),
`trade_quality`, `risk_level`, `bull_case[]`/`bear_case[]` (from analyst), and
`management_read`. EVERY new key must be mirrored in BOTH `_main_brain_neutral()`
and the main return, or a fail-open/closed-market poll yields `undefined` on the
wire. The "What-Changed" feed is a CLIENT-side diff of the server `signals{}`
snapshot — append a line ONLY on a transition; never re-emit on an unchanged poll.

## management_read re-entrancy + mutation rule (the sharp edge)

`compute_main_brain` runs INSIDE `full_analysis`. `management_read` is built by
calling `compute_manual_trade_management(mirror, analysis=result)`. TWO things
make this safe and BOTH are mandatory:
1. **Pass `analysis=result`** — that helper calls `full_analysis()` only when
   `analysis is None`; omitting it would recurse full_analysis→compute_main_brain
   →full_analysis.
2. **Pass a fresh COPY mirror dict**, never the object from `active_trade_for()` —
   the helper writes `min_r`/`max_r` back onto the dict it's handed; mutating the
   live `ACTIVE_TRADES_BY_INST` entry would corrupt active-trade state.
The whole block is wrapped in its own try/except → `management_read=None`, inside
the outer fail-open that returns `_main_brain_neutral()`.
**Why:** display-only must never alter live trade state or risk a state-dependent
500 on the single-return path.

## Manual Trade Management Mode (manual position priority + timeline)

The Main Brain can manage a USER-entered trade, not just the bot's own position.
`_newest_manual_trade_for(inst)` (newest OPEN manual trade, returns a COPY) takes
PRIORITY over `active_trade_for(inst)`; `pos_is_manual` then makes the mirror use
the user's real reason/entry_time/id/targets, and `management_read["origin"]` is
`manual|bot`. Same COPY+`analysis=result` re-entrancy rules as above apply — the
mirror is always freshly built so the helper's `min_r/max_r` writeback never
touches the stored manual trade either.

`compute_manual_trade_management` exposes a canonical `out["action"]`
(HOLD/REDUCE/TAKE PARTIAL/MOVE STOP TO BE/EXIT, MONITOR on the early `unavailable`
return) + `out["invalidated"]`, derived PURELY from the existing recommendation
booleans — `recommendation`/`recommendation_reason` stay byte-stable so the legacy
mtCard is untouched. The UI colors a pill off `action`/`invalidated` (`mbActColor`).

**Timeline is a single-writer in-payload log, NOT a new table.** Events live in
`trade["timeline"]` (cap 80) with fire-once dedupe via `trade["_tl_flags"]`;
`_update_manual_trade_timeline(trade,mgmt)->bool` is the ONLY advancer and runs on
the stored object under `MANUAL_TRADES_LOCK` in GET /manual-trade (persist only on
change). POST seeds the "entered" event. `management_read["timeline"]` is attached
ONLY for a manual position. No DDL (INSERT/SELECT convention).
**Why:** keeps the copilot log durable in the existing JSONB row, money-path-free.

## Known limitation (intentional, not a bug)

The market-closed early return in `compute_main_brain` runs BEFORE the
active-position check, so a position HELD through the daily halt / weekend shows
WAIT (closed-market neutral) instead of MANAGING/PAUSED. Left as-is to avoid
touching the closed-market neutralising override. To change: move the
active-position management branch ahead of the market-closed early return (and
lean on compute_manual_trade_management's own `market_open=False`→PAUSED path).
