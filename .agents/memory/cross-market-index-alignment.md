---
name: Cross-market index alignment
description: MNQ/MES/MYM directional-agreement layer — strictly display + Discord-notify, never touches the trade decision.
---

# Cross-market index alignment (Nasdaq/S&P/Dow)

A layer that reports whether the three equity-index micros (MNQ=Nasdaq, MES=S&P,
MYM=Dow) agree directionally, surfaced on the dashboard and as a dedicated MES/MYM
Discord "cross-market confirmation" alert.

**Rule:** it is DISPLAY + NOTIFY ONLY. It is NEVER read by the gate / scoring /
sizing / dedupe / broker path. Adding or removing it leaves every trade decision
byte-identical (all four goldens unchanged is the proof).

**Why:** the user chose "Option 1" — show agreement and alert MES/MYM, but never
change whether a trade is taken. Same money-path-untouched discipline as the other
display modules (analyst, equity curve, news).

**How to apply / invariants any change here must keep:**
- Each index's direction comes from its authoritative `bias` via
  `full_analysis(ticker_override=inst)` (Bullish→Long / Bearish→Short / Choppy→None).
  Classification: all-agree=Aligned, 2-of-3 majority=Leaning, else Mixed, <2 enabled=n/a.
  Only **Aligned** ever alerts; Leaning/Mixed/n-a do not.
- A self-rescheduling Timer loop refreshes the snapshot into a lock-guarded global and
  exposes it read-only on `/status` (curated dict). The loop's **refresh runs on
  dev+prod** (the dashboard needs it) but the **Discord SEND is gated on
  `DISCORD_LIVE_ENABLED`** so dev can never post to the shared live channel.
- Notifier: transition + cooldown dedup (same direction within cooldown is suppressed;
  a direction change re-alerts), `_alerts_muted`-respecting, groups targets by
  `_asset_discord_url` so MES+MYM sharing one channel post ONE combined message, and
  only arms the cooldown on a real 2xx (a failed post retries next pass).
- Guarded by the `cross_market` validation (`.local/state/check_cross_market.sh` +
  `test_cross_market.py`): classify, pure-read, /status serialization, notifier
  Aligned-only/dedup/grouping/mute, and dev-no-send gating.
