---
name: SCALP auto-execute trigger timing
description: Why SCALP hands-free auto-entry triggers on the LIVE actionable verdict (EARLY tier included, half-size), not the journal dedup; how it stays "fire once per setup"; and how stacking + re-entry-after-stop-out are layered on without breaking SWING.
---

# SCALP auto-execute trigger timing

**Rule:** SCALP hands-free auto-entry fires on the LIVE ACTIONABLE verdict
(`is_actionable` — EARLY tier Edge 50-59 INCLUDED, not just full READY), decoupled
from the journal dedup object. EARLY entries are half-sized via `RISK_MULT_EARLY`
(0.5, floored at 1 contract by `_risk_capped_contracts`). "Fire exactly once per
setup" is enforced by a dedicated per-setup set `AUTO_FIRED_KEYS` keyed
`(instrument, direction, zone_low)` via `ready_direction` — IDENTICAL for the EARLY
and the later FULL READY of the same zone — so the setup enters once (at the EARLY
point) and the strengthen-to-FULL does NOT double-enter. Recorded ONLY on a
confirmed broker entry. SWING keeps the original journal-gated condition
(`journal_entry and is_actionable`) verbatim — and SWING never emits EARLY, so
`is_actionable == is_full_ready` there (SWING money path unchanged).

**EARLY now auto-fires (operator request "get earlier entries"):** changed from
`is_full_ready` → `is_actionable` at the SCALP trigger AND removed the gateway's
`source=="auto" and is_early_ready` skip backstop. The gateway now sizes EARLY down
(via `_setup_risk_mult`) instead of rejecting it. The auto size is still effectively
1 contract (AUTO_TRADE_CONTRACTS=1, the half-size mult can't go below the 1-contract
floor) — true half-sizing only kicks in if AUTO_TRADE_CONTRACTS rises.

**STACKING + re-entry-after-stop-out (operator request, SCALP-only):** a live
position NO LONGER blocks a new SCALP auto entry. The webhook SCALP block dropped
its `not ACTIVE_TRADE` precondition and calls
`_maybe_auto_execute(inst, allow_stack=True, setup_key=_setup_key)`; the guard
inside is now `if ACTIVE_TRADE and not allow_stack: return False` (SWING passes
`allow_stack=False` → unchanged). Concurrency is bounded by TWO things only:
`AUTO_FIRED_KEYS` (one entry per READY *event* per setup — prevents a
continuously-READY setup from machine-gunning) and the per-day cap
(`AUTO_TRADE_MAX_PER_DAY`, default 20/instrument; `_auto_trade_bump_count` runs on
EVERY confirmed send so the cap is the hard backstop). Re-entry: the tracked
`ACTIVE_TRADE` is tagged `auto_setup_key=setup_key` (added ONLY when `setup_key`
is provided → SCALP only), and the price-based **STOP_HIT** handler discards that
key from `AUTO_FIRED_KEYS` so the setup re-arms and re-enters the instant it is
READY again. The **WIN (T1/T2)** path intentionally does NOT re-arm. Re-arm is
gated `if TRADING_MODE == "SCALP"`.

**Single-ACTIVE_TRADE tracking caveat:** `ACTIVE_TRADE` is still ONE global dict,
so it tracks only ONE position. Stacked broker positions beyond it are untracked
(no dashboard P&L/stop, broker manages real brackets) and CANNOT re-arm their own
setup (only the tracked position's stop-out re-arms). Operator explicitly accepted
this. Re-arm only fires on the app's price-based STOP_HIT detection (needs a
price-bearing webhook crossing the stop) — NOT on a CLOSE webhook or manual close.

**Why:** A SCALP setup routinely fires EARLY READY first (Edge 50-59, half-size),
which CLAIMS the journal dedup slot, then strengthens to FULL READY (Edge >= 60) a
bar or two later — that FULL READY is journal-deduped (`journal_entry=None`). The
original auto trigger was gated on `journal_entry`, so it skipped the FULL READY
entirely — auto entered minutes late, or only when the setup re-formed. Real
complaint: "getting in too late, should sell on the first short ready not 3 min
later." Operator runs AUTO-EXECUTE hands-free on a real prop account. The trigger
was first decoupled from the journal to enter on the first FULL READY; then (this
change) widened to `is_actionable` so it enters on the FIRST EARLY READY of the
setup — the earliest actionable point — at half-size.

**Why the per-setup guard:** the journal dedup is session-persistent (cleared
only by `POST /clear`), so the OLD code got "auto fires once per setup, never
re-enters after the trade closes" for FREE. Decoupling from `journal_entry`
loses that — after a T1 win clears `ACTIVE_TRADE`, the still-lingering FULL-READY
verdict would re-fire (the gateway duplicate-send fingerprint cooldown is only
~60s, so it would NOT catch a re-entry a few minutes later). `AUTO_FIRED_KEYS`
restores it. Marked only on a CONFIRMED send so a transient gateway failure
still retries next webhook; `_maybe_auto_execute` returns True iff the order
reached the broker (sent/simulated) — True even when the local `ACTIVE_TRADE`
tracking write throws (the position is real; must not re-send → verify broker).

**How to apply** — any change to the auto-entry trigger MUST:
1. Keep SWING on the EXACT original journal-gated condition + `allow_stack=False`
   + `setup_key=None` (so no `auto_setup_key` is added to its ACTIVE_TRADE dict)
   + STOP_HIT re-arm gated on SCALP. SWING never emits EARLY (so
   `is_actionable == is_full_ready`) and never populates `AUTO_FIRED_KEYS`, so its
   money path stays behaviorally byte-identical.
2. SCALP auto fires on `is_actionable` (EARLY tier INCLUDED). The old gateway
   `source=="auto" and is_early_ready` skip backstop is REMOVED — do NOT re-add it.
   EARLY must remain half-sized via `_setup_risk_mult` (never blocked outright).
3. Preserve the per-setup once-guard + its confirmed-send-only marking + the
   `/clear` reset (and empty-on-restart, when AUTO also resets OFF). For SCALP,
   the once-guard is now RELEASED on stop-out (re-arm) but NEVER on a win.
4. Split `entry_zone` on the EN-DASH "–" (matches `_journal_dedup_key` and the
   `f"{lo}–{hi}"` trade_plan format) or every same-direction setup collapses to
   `zone_low=0.0` and over-suppresses legitimate distinct setups.
5. Never re-add a blanket `not ACTIVE_TRADE` precondition to the SCALP block — it
   would silently kill stacking + post-stop-out re-entry. The daily cap (not the
   one-position guard) is the runaway backstop for SCALP.
6. Webhook worker is single-threaded, so the `AUTO_FIRED_KEYS` check-then-add is
   safe today; if it ever goes parallel, make check+add atomic or the same setup
   can double-send before the key is recorded.
