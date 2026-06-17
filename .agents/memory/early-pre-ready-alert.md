---
name: EARLY intrabar pre-READY alert
description: The additive ⚡EARLY LONG/SHORT alert that fires on sweep+structure before candle-close confirmation; invariants any alert/scoring change must preserve.
---

# EARLY intrabar pre-READY alert

The strict READY card intentionally waits for the 5m candle-close confirmation, so it
lands ~5 min after the move starts. EARLY is a separate, **purely additive, display-only**
layer that fires the instant a liquidity sweep **and** a structure shift (CHOCH / BOS /
HH-HL-LH-LL "displacement") appear together in a window — before confirmation. READY is
unchanged and remains the confirmed signal.

**Why:** user wanted to stop entering late on SHORTs without weakening the confirmed gate.

## Hard invariants (any future alert/scoring change must keep these)
- EARLY **never** touches the trading gate: no call into `evaluate_strict_setup`, the
  READY verdict/score, SWING parity, `create_journal_entry`, `_register_managed_trade`,
  or `send_live_ready_card`. It only reads the analysis dict + `ALERT_HISTORY`, mutates
  EARLY-only dicts, and enqueues a Discord post via `_enqueue_slow`. It is wired
  fail-open (try/except) BEFORE the READY card so it can never delay/break the decision.
- **READY owns the signal:** when the verdict is already LONG/SHORT READY, EARLY must not
  post — it marks its dedupe anchor and stands down so a late EARLY can't trail a READY.
- **Fire once per active setup.** Dedupe is per (instrument, direction) anchor that resets
  only when the setup goes **fully inactive** (no side active). **Ambiguity (both sides
  active) must PRESERVE anchors** — clearing them there re-arms an already-fired side and
  double-posts when the ambiguity resolves back in-window. Ambiguous → stand aside.
- Commit dedupe/diagnostics state (anchor, last-at, `earlyAlertTime`) **only after** the
  embed builds successfully; a build failure must not leave the setup stuck-deduped or
  falsely stamp an early-alert time.

## Diagnostics timing semantics (display-only)
- `LAST_READY_SENT_AT` is **per-instrument and persists across setups** — it is NOT
  setup-scoped. Attribute a READY to the current diagnostics row only when the current
  verdict is READY, or the stored READY is at/after this event's start; otherwise it is a
  stale READY and must be ignored (else it produces negative/misleading `alertDelaySeconds`).
- `waitedForCandleClose`: `False` if EARLY caught it; `True` only when the **current**
  verdict is the candle-close READY with no EARLY preceding it; `None` otherwise (incl.
  later WAIT re-evaluations after a prior READY — a stale READY must never read as `True`).

## Config / where it lives
All env-gated (defaults: enabled, main channel, no ping): `EARLY_ALERTS_ENABLED`,
`EARLY_ALERT_CHANNEL` (main|journal|none), `EARLY_ALERT_PING`, `EARLY_WINDOW_MIN`,
`EARLY_ALERT_COOLDOWN_SEC`. Displacement is mapped onto the existing Sweep + CHOCH/BOS
TradingView alerts (no new alert types were added). Behavioral coverage: `.local/test_early.py`.
