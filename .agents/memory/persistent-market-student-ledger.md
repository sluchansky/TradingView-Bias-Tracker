---
name: Persistent market-student ledger
description: Durable constraints for the consolidated forecast, evidence, outcome, health, and Strategy Lab research layer.
---

The market-student layer is additive research/display infrastructure only. It must never alter scoring, gates, stops, sizing, risk, execution, broker transmission, or canonical Databento ingestion. Its three forecast lanes have explicit horizons: SCALP in minutes, INTRADAY_TREND at session scale, and SWING across multiple hours or sessions.

**Why:** The project needs one auditable learning ledger without creating a second trading authority or allowing research outages and model drift to change live behavior.

**How to apply:** Freeze hypotheses before resolution; preserve source values alongside normalized R; use exact source identities rather than fuzzy correlation; retain ambiguous outcomes as ambiguous; suppress unchanged WAIT heartbeats; perform persistence off the analysis thread; require validated closed evidence plus human approval for promotion; and keep independent READY notifications explicitly switchable and deduplicated.