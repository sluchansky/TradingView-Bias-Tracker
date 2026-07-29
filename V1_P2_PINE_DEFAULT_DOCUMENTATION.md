# V1-P2-005: Pine Default to MGC — Open Conflict Resolution

**Task:** V1-P2-005  
**Status:** RESOLVED — documented below  
**Open Conflict:** TD-014 (Pine/TradingView payload schema)  
**Resolution type:** Documentation only — no code change to gate behavior  
**Date:** 2026-07-29

---

## Summary

Pine scripts that auto-detect instruments from the alert title (when no explicit
`ticker` field is present) default to MGC when the instrument cannot be resolved.
This is an accepted, documented behavior for V1.

---

## Two Resolution Paths in the Codebase

The application has two distinct instrument resolution functions with different behaviors:

### 1. `instrument_of(ticker)` — Lenient (display/legacy)

```
instrument_of(ticker):
    "Normalize any raw ticker to an enabled instrument.
     Anything that does not match a non-default enabled asset's alias
     (including None/empty/unknown) silently becomes the DEFAULT instrument (MGC)."
```

- Used by: display surfaces, legacy fallbacks, alert history readers
- Behavior: unresolvable input → MGC (silent default)
- Handles: `"MGC1!"` → `"MGC"`, `"MNQ1!"` → `"MNQ"`, `""` → `"MGC"`, `"UNKNOWN"` → `"MGC"`

### 2. `resolve_instrument(ticker_field, alert_type)` — Strict (money path)

```
resolve_instrument(ticker_field, alert_type):
    "Authoritative instrument resolution for an incoming TradingView alert.
     The payload `ticker` field is the source of truth.
     Nothing silently defaults to MGC — unresolvable alerts are rejected."
```

- Used by: `/webhook` endpoint (the gate's entry point for all TradingView alerts)
- Behavior: unresolvable input → `ok=False`, `instrument=None`, alert rejected
- Never silently defaults to MGC in the money path

---

## How Pine Scripts Auto-Detect Instruments

Pine scripts in the repository include:
- `pine_scripts/confirmation_alerts.pine`
- `pine_scripts/zone_alerts.pine`
- `pine_scripts/structure_alerts.pine`
- etc.

These scripts auto-detect the instrument from the current chart symbol and include
it in the `ticker` field of the webhook payload. When a Pine script is running on
an MNQ chart, the payload includes `ticker: "MNQ1!"`. When on an MGC chart,
`ticker: "MGC1!"` (or similar continuous contract notation).

**The default behavior documented here occurs only when:**
1. A Pine script runs on a symbol that is NOT one of the 4 registered instruments
2. AND the alert uses title-based detection (legacy path) rather than explicit `ticker`

In this case, `instrument_of()` silently defaults to MGC. This is the lenient
normalizer and is only used for display/legacy paths. The strict gate path via
`resolve_instrument()` would reject the alert entirely.

---

## Why This Is Accepted for V1

| Aspect | Assessment |
|---|---|
| Gate impact | NONE — `resolve_instrument()` (strict) is used for all gate decisions. An unknown ticker is rejected, not defaulted to MGC. |
| Risk | LOW — A Pine script on an unregistered symbol does not silently take a trade. The alert is rejected at the webhook. |
| Display impact | LOW — `instrument_of()` defaulting to MGC means diagnostic displays may show MGC data for unrecognized tickers. This is benign. |
| Frequency | LOW — Pine scripts are repo-owned and authored to run on the 4 registered instruments only. An accidental MGC default is rare. |

---

## TD-014 Resolution

| TD-014 Field | Value |
|---|---|
| **Issue** | Pine scripts that auto-detect instruments default to MGC when the symbol is unrecognized. This is an undocumented behavior gap relative to Implementation Principle 14. |
| **Planned resolution** | V1-P2-005 (documentation). No code change to gate behavior. |
| **Resolved?** | YES — this document is the V1-P2-005 output. |
| **Code change required?** | NO — gate already uses `resolve_instrument()` (strict, rejects unknowns). |
| **WARN log** | The TD-014 entry mentions "Add WARN log when the default fires." A WARN is already present in `resolve_instrument()` when a ticker is rejected. The lenient `instrument_of()` default to MGC is intentional by design and adding a WARN there would be noisy in normal display rendering. **Decision: No WARN log change for V1. Deferred to a display-focused post-V1 cleanup.** |

---

## Acceptance Criteria Status

| Criterion | Status |
|---|---|
| TD-014 open conflict resolved | ✅ RESOLVED — documented above |
| No code change to gate behavior | ✅ CONFIRMED — resolve_instrument() unchanged |
| No code change to Pine webhook handling | ✅ CONFIRMED — webhook entry unchanged |
| All 4 instruments still resolve correctly | ✅ CONFIRMED — see V1-P2-002 test |
| resolve_instrument() rejects unknown tickers | ✅ CONFIRMED — see test_resolve_instrument_rejects_unknown_tickers |

---

## Reference

- `instrument_of()` — app.py line ~1627
- `resolve_instrument()` — app.py line ~1682
- ALERT_TYPES registry — app.py line ~1468 (boot assertion enforces ASSETS == ALERT_TYPES instruments)
- TD-014 — IMPLEMENTATION_ROADMAP_V1.md §11 open conflicts, line 1762
- V1-P2-005 task card — IMPLEMENTATION_ROADMAP_V1.md line 1971
