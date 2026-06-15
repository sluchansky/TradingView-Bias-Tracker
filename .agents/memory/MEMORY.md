# Memory Index

- [api-server proxy route whitelist](proxy-route-whitelist.md) — Flask routes must be added to the Express `/api` proxy whitelist or they 404 before reaching Flask; how to debug 404s on this stack.
- [SCALP/SWING trading mode](trading-mode-scalp-swing.md) — webhook scoring has two sensitivity profiles via cfg(); invariants (SWING unchanged, Attempts reduced-size at every sizing site, MGC/MNQ string symmetry) any scoring change must keep.
- [Strict trade ruleset](strict-trade-ruleset.md) — strict 4-condition checklist is the authoritative full_analysis verdict; conflict check must stay VWAP-independent, score base 75, WAIT never journals, alert templates must include `ticker`.
- [full_analysis return-path key parity](full-analysis-return-parity.md) — full_analysis() has 2 return dicts (main + zone-mitigated early return) that must keep identical keys; a missing key is a state-dependent 500 invisible to fresh tests.
- [Tradovate live execution](tradovate-execution.md) — broker order path is gated so the public webhook can never auto-trade; OFF/DEMO defaults, rejection≠success, partial-fill & flatten-cancel safety invariants, never-log-secrets.
- [full_analysis data quirks & card seam](analysis-data-quirks.md) — vwap_status is freshness NOT direction (derive above/below from price vs vwap_value); _build_card_entry is the single source for journal+card; screenshots are passed to Discord never fetched; analytics terminal-only+deduped.
- [Per-instrument dashboard view](per-instrument-dashboard-view.md) — MGC/MNQ tabs switch displayed analysis via /status?ticker → full_analysis(ticker_override); per-instrument price/VWAP/price-context invariants.
- [VWAP auto-fetch](vwap-auto-fetch.md) — VWAP auto-sourced (MGC≈GC=F, MNQ≈NQ=F); chart/manual push wins a grace window then auto resumes; gate never trades on stale VWAP.
- [Live alert trade-card](live-alert-card.md) — clean card is the single alert format for journal + main channel; fires once per READY setup + re-posts every TRADE_READY_INTERVAL; per-instrument throttle prevents instant+periodic double-post.
