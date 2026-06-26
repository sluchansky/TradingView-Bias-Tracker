---
name: Paper managed-trade same-bar fill guard
description: Why the paper managed-trade watcher must not evaluate exits on the entry bar, and why entry_epoch stays at registration time.
---

- Rule: the paper/local managed-trade watcher (_watch_managed_trades) must NOT evaluate exit levels against any bar whose start epoch <= the trade's entry_epoch. Otherwise a freshly-opened paper trade can "instantly fill" its stop/TP off the high/low of the bar it entered on (or an earlier bar) — extremes that predate the entry.
- entry_epoch is set ONCE at managed-trade registration (_register_managed_trade). That registration runs inside send_live_ready_card on the SAME /webhook request as the paper auto-exec/tag (_tag_dynamic_paper_managed_trade), milliseconds apart — so registration ≈ actual paper fill.
- **Why:** moving entry_epoch to the exact tag/fill time was considered and rejected. The registration→fill gap is sub-millisecond within one request, so the theoretical "bar-boundary straddle" window is negligible, and re-touching the money-path-adjacent paper lifecycle adds more risk than it removes.
- Guard is FAIL-OPEN: if either bar_start or entry_epoch is missing it does NOT skip (never silently freezes a trade). It only ever skips evaluation (conservative toward NOT closing early); it can never force a close.
- Live/broker path untouched: the broker still sends a single TP; the dynamic multi-leg lifecycle is local/paper only.
- **How to apply:** any change to managed-trade exit evaluation must preserve the "skip bars at/before entry" guard and its fail-open nature; don't tie entry_epoch to anything later than registration without re-checking the registration-vs-fill timing first.
