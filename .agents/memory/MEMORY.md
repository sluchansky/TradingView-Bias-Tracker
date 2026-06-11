# Memory Index

- [api-server proxy route whitelist](proxy-route-whitelist.md) — Flask routes must be added to the Express `/api` proxy whitelist or they 404 before reaching Flask; how to debug 404s on this stack.
- [SCALP/SWING trading mode](trading-mode-scalp-swing.md) — webhook scoring has two sensitivity profiles via cfg(); invariants (SWING unchanged, Attempts reduced-size at every sizing site, MGC/MNQ string symmetry) any scoring change must keep.
- [Strict trade ruleset](strict-trade-ruleset.md) — strict 4-condition checklist is the authoritative full_analysis verdict; conflict check must stay VWAP-independent, score base 75, WAIT never journals, alert templates must include `ticker`.
- [full_analysis return-path key parity](full-analysis-return-parity.md) — full_analysis() has 2 return dicts (main + zone-mitigated early return) that must keep identical keys; a missing key is a state-dependent 500 invisible to fresh tests.
