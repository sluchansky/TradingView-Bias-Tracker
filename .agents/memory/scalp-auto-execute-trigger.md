---
name: SCALP auto-execute trigger timing
description: Why SCALP hands-free auto-entry triggers on the LIVE full-READY verdict (not the journal dedup), and how it stays "fire once per setup".
---

# SCALP auto-execute trigger timing

**Rule:** SCALP hands-free auto-entry fires on the LIVE full-READY verdict
(`is_full_ready`), decoupled from the journal dedup object. "Fire exactly once
per setup" is enforced by a dedicated per-setup set `AUTO_FIRED_KEYS` keyed
`(instrument, direction, zone_low)` — the SAME identity the journal dedups on —
recorded ONLY on a confirmed broker entry. SWING keeps the original
journal-gated condition (`journal_entry and is_actionable`) verbatim.

**Why:** A SCALP setup routinely fires EARLY READY first (Edge 50-59, half-size,
intentionally NEVER auto-traded). The EARLY READY CLAIMS the journal dedup slot,
so when the setup strengthens to FULL READY (Edge >= 60) a bar or two later that
FULL READY is journal-deduped (`journal_entry=None`). The old auto trigger was
gated on `journal_entry`, so it skipped the FULL READY entirely — auto entered
minutes late, or only when the setup re-formed. Real complaint: "getting in too
late, should sell on the first short ready not 3 min later." Operator runs
AUTO-EXECUTE hands-free on a real prop account and chose "enter on the first
FULL-conviction READY at full size."

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
1. Keep SWING on the EXACT original journal-gated condition. SWING never emits
   EARLY, so `is_actionable == is_full_ready` there and the money path stays
   byte-identical.
2. Keep EARLY out of auto (full conviction only); the gateway also backstops
   `source=="auto" and is_early_ready`.
3. Preserve the per-setup once-guard + its confirmed-send-only marking + the
   `/clear` reset (and empty-on-restart, when AUTO also resets OFF).
4. Split `entry_zone` on the EN-DASH "–" (matches `_journal_dedup_key` and the
   `f"{lo}–{hi}"` trade_plan format) or every same-direction setup collapses to
   `zone_low=0.0` and over-suppresses legitimate distinct setups.
