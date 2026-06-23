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

**Invariants any change here must keep:**
- DISPLAY-ONLY. It only calls GET `/status` and the existing `setSymbol()` /
  `setDir()` UI selectors. It must never touch the gate, scoring, sizing, journal,
  or any money path.
- Runs ONCE on landing (called once in boot, never in the 3s poll), so it never
  yanks the view away mid-session.
- Never overrides a manual choice: a real tab/direction click sets
  `userPickedSetup=true` (wired into the inline `onclick`s, NOT inside
  `setSymbol`/`setDir`, because those are also called programmatically); the
  function re-checks the flag after its awaits before applying.
- Respects display focus: only instruments passing `instrEnabled` are considered,
  so a focused-out (hidden) tab is never auto-selected.
- Fail-open: any `/status` error or missing data leaves the MGC / Long default.
- There is NO combined/peer endpoint — it fetches `/status?ticker=` once per shown
  instrument (≤2 calls) on boot and compares client-side.
