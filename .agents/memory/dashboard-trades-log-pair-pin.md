---
name: Today's Trades per-panel pair pin
description: The trades log can be pinned to a non-main instrument via its own /status fetch; gotchas for the poll clobber and the async fetch race.
---

- The "Today's Trades" log has a per-panel pair switcher (JS `ttInst`) INDEPENDENT of the main instrument tab (`sym`): pin it to any pair to review that pair's closed trades without changing the main analysis view. Display-only — never touches the gate/money path. Default `ttInst=null` = follow the main tab (no behavior change until used).
- It reuses `/status?ticker=X` (the same endpoint the main tab uses); that response carries `equity_curve_today` for the requested instrument. There is NO dedicated peer endpoint — pinning to a non-main pair costs one extra /status fetch per poll tick (accepted "2× /status" tradeoff, only while pinned).
- Gotcha 1 (poll clobber): the 3s poll calls `renderTodaysTrades(d)` with the MAIN payload. When pinned to a different pair it MUST early-return into the override fetch instead of rendering main data, or the log flickers back to the main pair every tick.
- Gotcha 2 (async race): the override fetch is async; snapshot the selection (`const reqInst=ttInst`) BEFORE the await and only render if `ttInst===reqInst`, labeling with `reqInst` — otherwise a fast pair-switch renders one pair's data under another pair's label. Re-fire the fetch in `finally` if the selection changed mid-flight (an in-flight guard alone would drop the new pick until the next tick).
- **Why:** the same single triple-quoted dashboard HTML string means any per-panel override has to coexist with the global poll; without the early-return + snapshot it silently shows wrong/stale data rather than erroring.
- **How to apply:** any new per-panel instrument override on this dashboard should copy this pattern (early-return from the main-poll renderer + snapshot-before-await), and label rendered data by the instrument it was actually fetched for, not the live selection.
