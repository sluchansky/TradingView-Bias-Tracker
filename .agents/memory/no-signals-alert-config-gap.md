---
name: No-signals = the structure (CHOCH/BOS) gate
description: The #1 reason a fully-working TradingView webhook deployment still produces 0 tradeable signals — and how to diagnose it from the scored "Alert:" log line.
---

# "0 signals" on a working deployment is almost always the CHOCH/BOS structure gate

When everything is deployed and alerts ARE arriving and being scored, but the user still
sees "0 signals to trade", the cause is usually the **market-structure gate**, not the
deployment and not a missing zone alert.

`decision_engine()` hard-returns WAIT at its very first gate when
`structure_class == "Undefined"` (or no directional alert score). Market structure is built
**only** from CHOCH/BOS alerts; with none tracked, `get_market_structure()` returns
"Undefined" → every zone setup, no matter how clean, is force-WAITed before it can score.

**Diagnose from the scored log line** — `Alert: <type> | <bias> | Edge N | WAIT → WAIT |
Struct: Undefined | Risk: ...`. `Struct: Undefined` is conclusive proof the engine has
received zero CHOCH/BOS alerts. (Contrast the simpler variant: if you only ever see ZONE
MITIGATED / ZONE BROKEN / VWAP arriving, the user isn't sending directional alerts at all.)

**Recognized structure alert types (ALERT_RULES):** `CHOCH SUPPLY`, `CHOCH DEMAND`,
`BOS SUPPLY`, `BOS DEMAND`. CRITICAL: these are **shared / un-prefixed** (no MGC/MNQ in the
name) and therefore **REQUIRE a `ticker` field** to resolve the instrument — without ticker
they're rejected as unresolvable. Zone alerts, by contrast, embed the instrument in the name
(e.g. `MNQ NEW SUPPLY ZONE`) and don't strictly need ticker.

**Full READY recipe (per side):** BOS + CHOCH (structure) + Zone Confirmed + 5m confirmation
candle (`<INST> BULLISH/BEARISH CONFIRMATION`) + price on the correct side of VWAP. The
CHOCH/BOS structure is the gate that must open first; zones + VWAP alone can never reach READY.

**The fix is on the user's TradingView side**, not in app.py: create CHOCH/BOS (and
confirmation) alerts with the exact alert_type strings + a `ticker` field. Do NOT loosen the
gate in code unless the user explicitly asks — the strict structure-first ruleset is
intentional. Also note: a junk `{{strategy.order.comment}}` alert (Pine strategy placeholder
fired from an indicator) and empty-body alerts are harmless (rejected/200) but are noise; fix
or delete them on the TradingView side.

**TradingView message gotchas:** one JSON object per alert — pasting several objects into one
Message box gives "JSON Parse error". Quote the price placeholder as `"price":"{{close}}"` so
TradingView's editor validates it as JSON; the webhook does `float(price)` so a quoted string
parses fine. Direction→alert_type mapping: bullish CHOCH/BOS → `CHOCH DEMAND` / `BOS DEMAND`;
bearish → `CHOCH SUPPLY` / `BOS SUPPLY`.

**The Message box must be PURE JSON — no leading/trailing prose.** A human label before the JSON
(e.g. `MGC bearish CHOCH. {"alert_type":"CHOCH SUPPLY",...}`) makes the whole body invalid JSON.
The webhook does `get_json(force=True, silent=True)`; on failure it falls back to
`{"alert_type": raw_body.strip()}`, so the entire label+JSON string becomes the "alert_type" →
unrecognized → ignored with a **200** (silent). Symptom: structure stays Undefined even though the
indicator's alerts are clearly firing. This is the most common reason a *correctly-formatted* JSON
still never registers. (Confirmation/zone alerts that DID work had pure-JSON Message boxes — the
contrast is the tell.) Fix is TradingView-side: strip everything outside the `{...}`.
