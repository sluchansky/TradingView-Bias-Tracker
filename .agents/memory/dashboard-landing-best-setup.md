---
name: Dashboard lands on best-probability setup
description: On page load the operator dashboard auto-selects the instrument+direction with the strongest setup; the ranking rule and its invariants.
---

# Dashboard lands on best-probability setup

On page load, the dashboard auto-picks which instrument tab (MGC/MNQ) and which
direction (Long/Short) to show, via `autoSelectBestSetup()` in the boot block of
the dashboard `<script>`.

**Ranking rule (authoritative):** an *actionable* setup (`jsIsActionable` →
READY / EARLY READY) always beats a non-actionable one; within the same
actionability tier the higher top-level `edge_score` wins. Favored direction =
`jsReadyDir(verdict)` when actionable, else the higher of `long_score` /
`short_score` (Long on tie). Full tie / both WAIT / both edge 0 → falls back to
the MGC / Long default. This mirrors the gate's own notion of "favored side"
(top-level `edge_score` is the favored-side authoritative edge; `dominant_direction`
is Long when `long_score >= short_score`).

**Why this shape:** the user explicitly chose "prefer an actionable setup; if
neither is actionable, show the higher Edge Score." It saves the operator from
manually scanning both tabs to find where the opportunity is.

**30s auto-follow (recurring, separate from landing):** besides the once-on-load
pick, a `setInterval(…, 30000)` re-follows the best setup. `pickCleanestSetup` takes
`(force, actionableOnly)`: the button passes `force=true` (jumps to the best of
*anything*, even all-WAIT), landing passes neither, the 30s timer passes
`(false, true)`. Two calming guards the operator explicitly asked for:
1. **Sticky manual pick** — the timer early-returns while `userPickedSetup` is true,
   so once you click a tab/direction the view is NEVER yanked; only the "Cleanest
   trade" button re-arms it (it sets `userPickedSetup=false` then force-jumps).
2. **Actionable-only** — `actionableOnly` makes the timer switch ONLY to a
   READY/EARLY setup; when everything is WAIT it leaves the view put (no hopping).
**Why:** the "95%" on a card is *confidence*, not the `edge_score`/grade the follower
ranks on, so a high-confidence WAIT kept getting dropped for a higher-Edge or
actionable setup — looked like it "reset to a lower grade." User picked "both fixes."

**Invariants any change here must keep:**
- DISPLAY-ONLY. It only calls GET `/status` and the existing `setSymbol()` /
  `setDir()` UI selectors. It must never touch the gate, scoring, sizing, journal,
  or any money path.
- `setSymbol`/`setDir` must stay flag-neutral (they're called programmatically by
  the follower); only real user clicks set `userPickedSetup=true`, else the sticky
  guard would pin itself after the first auto-switch.
- Never overrides a manual choice: a real tab/direction click sets
  `userPickedSetup=true` (wired into the inline `onclick`s, NOT inside
  `setSymbol`/`setDir`, because those are also called programmatically); the
  function re-checks the flag after its awaits before applying.
- Respects display focus: only instruments passing `instrEnabled` are considered,
  so a focused-out (hidden) tab is never auto-selected.
- Fail-open: any `/status` error or missing data leaves the MGC / Long default.
- There is NO combined/peer endpoint — it fetches `/status?ticker=` once per shown
  instrument (≤2 calls) on boot and compares client-side.
