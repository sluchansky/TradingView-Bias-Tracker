---
name: Per-gate webhook diagnostics
description: The /diagnostics surface that explains WHY the engine is WAIT, and the invariants any change to it must keep.
---

# Per-gate webhook diagnostics ("why is it WAIT?")

When the bot is "permanently WAIT" / "no signals", the authoritative answer is the
per-gate PASS/FAIL breakdown produced for **every scored webhook**, not guesswork.

- **Surface:** Flask `GET /diagnostics` (text/plain, newest-first, `?n=`) backed by an
  in-memory ring buffer (`GATE_DIAGNOSTICS`, bounded). Owner-only — it is behind the
  dashboard password (whitelisted in the Express `/api` proxy but NOT in OPEN_PATHS).
  Public URL: `/api/diagnostics`. There is also a best-effort on-disk mirror
  (`gate_diagnostics.log`), but on the Reserved VM that file is **not user-reachable**
  (no SSH) — the endpoint + stdout are the readable surfaces.

## Invariants any change must keep
- **Observability only.** Recording must NEVER alter a gate decision. All diagnostic
  writes are fail-open (wrapped so they can't crash the worker thread).
- **"Blocked by" reflects the REAL `failed_conditions`**, where the structure gate =
  ANY ONE of BOS / CHOCH / swing. The individual rows (BOS, CHOCH, Swing) are shown
  separately for visibility. **Do not "fix" a row that shows FAIL when
  `Structure (any one)` passes** — a single failing structure row is expected.
- **On-disk mirror is size-capped** (single `.1` rotation, fail-open). Any
  write-on-every-webhook file here must stay bounded.

## Known non-bug that looks like a bug
The diagnostic shows ONLY the **candidate direction** (single side, by design — matches
the operator's mental model). In a no-VWAP context (e.g. dev/local with no auto VWAP),
candidate selection can default to the side **opposite** the triggering alert
(e.g. a `BOS DEMAND` alert showing `Candidate: SHORT` with all supply rows FAIL).
**Why:** candidate = side of price vs VWAP; with no usable VWAP it falls back to
structure/default. This is pre-existing engine candidate-selection behavior, NOT a
diagnostics bug. Prod auto-fetches VWAP (see vwap-auto-fetch.md), so the candidate is
meaningful there. If a future request needs both sides, surface Long+Short — it's a
small additive change to the formatter, still observability-only.
