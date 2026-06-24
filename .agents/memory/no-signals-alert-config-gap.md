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
`BOS SUPPLY`, `BOS DEMAND`, plus the swing-structure alerts `HH`, `HL` (bullish) and `LH`, `LL`
(bearish). CRITICAL: ALL of these are **shared / un-prefixed** (no MGC/MNQ in the name) and
therefore **REQUIRE a `ticker` field** to resolve the instrument — without ticker they're
rejected as unresolvable. Zone alerts, by contrast, embed the instrument in the name (e.g.
`MNQ NEW SUPPLY ZONE`) and don't strictly need ticker.

**Full READY recipe (per side):** structure_confirmed (ANY ONE of CHOCH/BOS/HH/HL for long,
CHOCH/BOS/LH/LL for short) + zone_valid (zone MITIGATED + a same-direction reaction) + price on
the correct side of VWAP + Edge Score ≥ 80, with no opposing-structure conflict. Some structure
signal is the gate that must open first; a zone + VWAP alone can never reach READY.

**Diagnose the new per-gate WAIT debug:** the scored "Alert:" log line ends with a `Gate:` field
(e.g. `Gate: zone=N vwap=Y struct=N edge=20<80`) and `/status` carries `gate_debug` (now incl.
`candle_confirmed` + `failed_conditions`) + a plain-English `strict_reason` naming the failed
gate(s). Use these to see exactly which gate is holding the setup before blaming structure/config.

**Two non-code blockers seen live, both UPSTREAM of the gate — check them FIRST:**
1. **Deploy gap.** Loosening the gate in the repo does NOTHING until the Reserved VM is
   re-published. Tell-tale: prod "Alert:" lines still use the OLD format (`… | Struct: … |
   Risk: …`) with NO `Gate:` field. Always confirm the new code is actually live before debugging.
2. **Input-mix gap.** The chart was sending ONLY `… CONFIRMATION` (every 5m bar) + occasional
   `BOS`; ZERO `CHOCH`, `ZONE MITIGATED`, or `SWEEP` ever arrived. `zone_valid` is a HARD gate
   that requires a `ZONE MITIGATED` alert (+ a reaction), so with none sent it can NEVER be TRUE
   → READY impossible regardless of code. Quantify by grepping prod logs per alert type (a regex
   for `CHOCH|ZONE MITIGATED|SWEEP|READY` returning "No deployment logs found" = none arriving)
   before tuning any threshold.

**If structure truly is the gap, the fix is on the user's TradingView side**, not in app.py:
create the structure (and reaction) alerts with the exact alert_type strings + a `ticker` field.
NOTE: the gate was deliberately LOOSENED (structure ANY-ONE, not BOS+CHOCH-both) per an explicit
user request — do not silently re-tighten it. Also note: a junk `{{strategy.order.comment}}` alert (Pine strategy placeholder
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

**Indicator DEFAULT plain-text messages are dropped — map them to canonical JSON.** The user's
zone/volume/CVD indicators ship human-readable default alert messages that match NO alert_type and
are silently dropped (200 + `Unrecognized alert type`). The structure (BOS/CHOCH) + confirmation +
VWAP alerts were sent as proper JSON and worked, masking that the *rest* never registered. Confirm
by grepping prod logs for `Unrecognized alert type` — it lists every dropped phrase verbatim.
Phrase → canonical alert_type seen live:
- `Bullish/Bearish Zero Cross on <SYM>` **and** `Bullish/Bearish Divergence on <SYM>` = the **CVD**
  indicator (it fires BOTH event kinds). Per user choice, both bullish events → `CVD BULLISH`, both
  bearish → `CVD BEARISH`. Unprefixed → need a `ticker`. No numeric value travels in the text, and
  that's fine: CVD magnitude is display-only; only the bullish/bearish STATE is the (hard) filter.
- `New Supply/Demand Zone (BOS/CHOCH) confirmed on <SYM>` → `<INST> NEW SUPPLY/DEMAND ZONE`
  (use `… SUPPLY/DEMAND ZONE CONFIRMED`, score 2, only if a *separate* validated-zone event exists).
- `Price has broken through a confirmed zone on <SYM>` → `<INST> ZONE BROKEN`.
- `Price has mitigated a confirmed zone on <SYM>` → `<INST> ZONE MITIGATED`.
- `Volume Crossing Up Threshold (Red)` → `VOLUME SPIKE` — **carries NO ticker/symbol at all**.
  BULLETPROOF FIX: use the PREFIXED form `{"alert_type":"MGC VOLUME SPIKE"}` (or `MNQ …`) on the
  matching chart — VOLUME SPIKE ingestion needs NEITHER ticker NOR price (it only stamps a freshness
  ts in `VOLUME_SPIKE_BY_TICKER`), so NO placeholders are required. (Unprefixed `VOLUME SPIKE` also
  works but then needs `"ticker":"{{ticker}}"`.) Direction-agnostic, so a "(Green)" variant maps to
  the same type.
- **Literal-placeholder gotcha (confirmed live):** a VOLUME alert fired for hours as
  `{"alert_type":"VOLUME SPIKE","ticker":"{{ticker}}","price":{{close}}}` with `{{ticker}}`/`{{close}}`
  UNSUBSTITUTED — `"price":{{close}}` unquoted is invalid JSON → whole body dropped as alert_type →
  "Unrecognized alert type". The SAME chart's SWEEP alert DID substitute (`"price":4149.9`), so
  placeholders work per-alert; the cure is to recreate the broken alert like the working one, or just
  drop placeholders (prefixed VOLUME SPIKE needs none).
Zone types are **prefixed-only** (`MGC …`/`MNQ …`) and must hardcode the instrument to match the chart
(TradingView can't build it from `{{ticker}}`). VOLUME SPIKE has BOTH prefixed and unprefixed forms;
`CVD …` is unprefixed and self-resolves from `ticker`. `ZONE MITIGATED` is prefixed AND needs a real
`"price":"{{close}}"`: the +0.3% (`MITIGATED_TOLERANCE_PCT`) proximity check populates
`MITIGATED_PRICES`, so a price-less mitigation can NEVER open `zone_valid` even if it registers.

**SCALP "no trades fire" with structure PRESENT = missing confluence, edge caps ~45<50 (recurring).**
Distinct from "0 signals / Struct: Undefined": here structure DID arrive, so the scored line reads
`struct=Y zone=Tested vwap=Y edge=45<50` — yet still WAIT. Cause: only `… CONFIRMATION` + occasional
`BOS`/`… ZONE` arrive; ZERO `BULLISH/BEARISH SWEEP`, `CVD BULLISH/BEARISH`, `VOLUME SPIKE`. Edge then
tops out at structure(BOS/CHOCH +20) + VWAP(+15) + Session(+10) = ~45, below the SCALP 50 READY
threshold (volAdj +10 is a separate SCALP modifier, NOT folded into the gate `edge=` number). A SINGLE
confluence alert (+15 each) tips it over. Symptom the user reports as "instrument X never fires": the
near-miss instruments (MES/MYM seen at edge 35–45 w/ struct=Y) are actually the CLOSEST to firing, not
the most broken — MGC/MNQ scored LOWER (10–25) in the same window. Webhooks ARE received/attributed/200;
the gap is purely the missing confluence alerts on TradingView. Prove it: `SWEEP|CVD|VOLUME|RVOL` grep of
prod logs → "No deployment logs found" (none arriving) AND no `Unrecognized alert type` (so not a
bad-schema rejection — genuinely not sent). Recognized confluence types: `BULLISH/BEARISH SWEEP`,
`CVD BULLISH/BEARISH` (also `CVD_…`), `VOLUME SPIKE` (also `VOLUME_SPIKE`). Secondary: auto-trade arming
resets OFF every publish — re-arm the instrument too, else a future READY still won't auto-execute (moot
until edge clears 50). NOT a code bug; do not tune thresholds to compensate.

**Indicator ON the chart ≠ alert CREATED — coverage is per-indicator-per-chart (recurring).** A
TradingView indicator placed on a chart (visible markers + status line) sends NOTHING to the webhook
until you separately create an Alert for it (Condition = that indicator → "Any alert() function call" →
Notifications → Webhook URL). Users routinely add all the indicators to every chart but only create the
alert for ONE (commonly Confirmation Candle), so an instrument sits at `Edge 15 | struct=N` forever.
Full coverage = 5 indicators (Confirmation, Market Structure BOS/CHOCH, Liquidity Sweep, Volume Spike,
CVD) × 4 charts = **20 alerts** — flag the user's TradingView plan alert cap (Essential 20 / Plus 100 /
Premium 400) as the likely reason only a subset exist. DIAGNOSE with a per-instrument coverage matrix:
grep prod `INCOMING.*<TYPE>` (one query each for `BULLISH SWEEP|BEARISH SWEEP`, `VOLUME SPIKE`, `CVD`,
`CHOCH|BOS|HH|HL|LH|LL`) and tabulate which alert_type prefixes arrive for which ticker. Typical finding
is a PATCHWORK — no chart has the full set (e.g. SWEEP only on MGC, CVD only on MNQ, VOLUME SPIKE on
none) — so the fix is "create the missing alerts", instrument-by-instrument, not a code/threshold change.

**Status-line instrument label is the *Manual instrument* INPUT value, NOT what's sent — red herring.**
The prefixed Pine scripts display their `manualPrefix` input in the TV status line REGARDLESS of whether
"Auto-detect instrument from symbol" is checked, so a MYM1! chart can show "…MES" while auto-detect is
correctly sending `MYM`. Never diagnose auto-detect from the status line (bools like useAuto aren't even
shown there) — diagnose from the PREFIX on arriving `INCOMING POST` bodies in prod logs. Confirmed live:
MYM1! chart showing "MES" was emitting correct `{"alert_type":"MYM BULLISH CONFIRMATION","ticker":"MYM1!"}`.
(If the prefix and ticker ever DISAGREE — e.g. manual really was MES on a MYM chart — `resolve_instrument`
rejects it as a contradiction, ok:False, dropped with 200; that's a separate failure from "no alert created".)

**RESOLVED / confirmed live:** after the user recreated the two broken alerts, prod began receiving the
canonical forms `{"alert_type":"MGC ZONE MITIGATED","price":"4170.4"}` and `{"alert_type":"MNQ VOLUME SPIKE"}`
with ZERO `Unrecognized alert type` warnings, and the scored line flipped to **`Gate: zone=Y …`** for the
first time (was always `zone=N`). So the zone HARD gate now opens. Lesson for future "0 signals": once the
config gap is closed, remaining WAITs are LEGITIMATE alignment, not config — e.g. MGC showed
`zone=Y vwap=N struct=Y edge=35<70`: the gate is just waiting for VWAP side + edge≥threshold to coincide
with the (transient) zone-mitigation window. Don't re-investigate as a config bug; the pieces simply have
to line up on the SAME instrument at the SAME time. (Note: VOLUME SPIKE fired on MNQ while the mitigation
was on MGC, so a clean same-instrument +volume edge bump wasn't captured in that window.)
