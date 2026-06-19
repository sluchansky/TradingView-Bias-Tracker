---
name: Strict trade recommendation ruleset
description: Non-obvious constraints behind the MNQ/MGC strict checklist verdict in the TradingView webhook app — what future changes must preserve.
---

# Strict trade recommendation ruleset (artifacts/tradingview-webhook/app.py)

The strict checklist evaluator is the **authoritative** verdict source inside `full_analysis`
(`evaluate_strict_setup` → `build_strict_trade_plan`). It replaced the old confidence-gate /
verdict-mapping block. Any future change to the final LONG/SHORT/WAIT decision must flow through
the strict path, not by re-adding a parallel confidence gate.

## Constraints any change must keep
- **The READY gate is MODE-TUNABLE (per-`cfg()` keys); the old "byte-for-byte unchanged" invariant
  was EXPLICITLY OVERRIDDEN for SCALP by the user.** `_is_ready` reads `gate_debug` flags:
  structure (hard) AND location (hard) AND volume_ok (fail-open) AND Edge≥`EDGE_READY_THRESHOLD`
  AND not-conflict AND not-vol_block; VWAP and zone are gated only when their `GATE_REQUIRE_*` is on.
  - **SCALP READY = Edge≥70 AND structure AND location(near VWAP OR active trade-side zone) AND
    volume_ok(FAIL-OPEN).** `MIN_CONFIRMATIONS`=0. `GATE_REQUIRE_VWAP/ZONE`=False (VWAP still scores
    +15; zone is folded into the location gate, no longer a hard block). `GATE_REQUIRE_LOCATION/
    STRUCTURE`=True. **No EARLY band:** `EDGE_FULL_READY_THRESHOLD`==`EDGE_READY_THRESHOLD`==70, so a
    setup is READY at Edge≥70 (A+ DISPLAY at ≥85) or it WAITs — the EARLY READY verdict no longer fires
    in SCALP. **Do not "re-tighten" thinking this is a bug.**
  - **SWING** (`GATE_REQUIRE_ZONE/VWAP/STRUCTURE`=True, `GATE_REQUIRE_LOCATION`=False, threshold 80)
    reduces EXACTLY to the historical `zone AND vwap AND structure AND Edge≥80` gate — **must stay so.**
  - **Volume is FAIL-OPEN:** `volume_ok` = volume_confirmed OR no volume data present — a missing feed
    NEVER blocks; only data-present-but-unconfirmed blocks. Volume also scores +15 (spike OR RVOL≥thr).
  - When location/structure fails the SCALP READY `reason` must NOT claim "zone reaction"/"near VWAP";
    gate on the real `gate_debug` flags. Classify READY verdicts ONLY via the verdict helpers
    (`is_full_ready` etc.), never a literal `"LONG READY"` compare — see trading-mode-scalp-swing.md.
- A pre-existing **volatility BLOCK** also holds READY→WAIT (fail-open: only a hard BLOCK regime — see
  `volatility-monitor-gate.md`) — intentional safety; keep it.
- **alert_level vs conviction_tier are TWO SEPARATE fields by design — never merge them.** `alert_level`
  is the OPERATIONAL early-warning ladder (WATCH < ARMED < WATCH FOR ENTRY < READY); `conviction_tier`
  is the SCORE BAND (`_score_tier`: 50-69 READY / 70+ HIGH CONVICTION; a 35-49 EARLY band exists in code
  but SCALP's floor==full==70 means it is not reached). "WATCH FOR ENTRY" = in-zone AND confirmations≥
  needed but gate not yet full-READY. Both display-only, never gate.
- **structure_confirmed (the GATE flag) = ANY ONE of** CHOCH/BOS in direction, HH/HL (long), LH/LL
  (short). It is NOT "BOS AND CHOCH" — that older both-required rule was the #1 cause of always-WAIT.
  **Scoring nuance:** only BOS (+20) and CHOCH (+20) CREDIT the Edge Score; HH/HL/LH/LL satisfy the
  structure GATE but score 0 — so a swing-only setup can pass the structure gate yet sit below the
  Edge≥70 floor and still WAIT.
- **zone_valid = trade-side zone MITIGATED + a same-direction REACTION** (5m confirmation candle OR
  zone-confirmed OR liquidity sweep). Mitigation alone is the old "consumed/stand-aside" state.
- **Conflict is recency-aware & structure-only (VWAP-independent):** opposing long/short structure
  timestamps within `CONFLICT_WINDOW_MIN` (10 min) → WAIT both sides, score 0. A STALE opposite
  structure must NOT block. **Why:** if conflict also required VWAP it is unreachable (price can't be
  both sides of VWAP); the old "any opposite in window" rule over-blocked.
- **VWAP equality → WAIT.** Gate uses strict inequalities (price > VWAP long, price < VWAP short).
- **Edge Score is the single additive helper `compute_trade_edge_components(signals)`** (BOS20 +
  CHOCH20 + VWAP15 + Sweep15 + Volume15 + CVD15 + Session10, **max 110**) shared by the gate AND the
  display layer — gate score and shown Edge Score can never diverge. NO floor; in SWING a READY is
  Edge≥80 by construction, in SCALP Edge≥70. `zone_valid` and `confirmation_candle` NO LONGER score
  (zone is a location input + SWING hard gate; candle is dropped); RVOL/volatility modifiers were
  removed from the sum (RVOL feeds Volume; volatility is informational + a SWING hard gate). Session is
  a pure additive component (NEVER blocks). See edge-score-card-block.md for the full model + tiers/grades.
- **`build_strict_trade_plan` anchors on the trade-side zone when present, else falls back to VWAP**
  (param `vwap=`, single caller passes `vwap=vwap_value`). The SCALP location gate accepts "near VWAP
  OR zone", so a near-VWAP setup with NO zone MUST still form a plan — otherwise the verdict silently
  downgrades to WAIT and the OR-location path is dead. Fails closed only when neither a zone nor VWAP
  exists. Real-money/TradersPost path is unaffected (it recomputes; prices come from the same plan).
- **Every WAIT names the failed gate(s)** via `reason`/`missing`/`gate_debug` (also on `/status` and the
  scored "Alert:" log `Gate:` field). Keep this per-gate debug surface when touching the gate.
- **Journal gates solely on `strict_label` ∈ (Strong Trade, Possible Trade); WAIT must never journal.**
  `strict_label` stays in {Strong Trade, Possible Trade, WAIT} — the "A+ Setup" tier is a DISPLAY
  `trade_strength` only and must NEVER become `strict_label`. While the market is CLOSED the override
  sets `strict_label`='WAIT' (NOT 'MARKET CLOSED') so the journaling label never escapes the allowed
  set; the closed state surfaces via `verdict`/`market_status`/`strict_reason` (dashboard badge keys on
  `market_open`/`verdict`, not `strict_label`).
  Dedup key is (instrument, direction, rounded zone_low); `JOURNAL_KEYS` is in-memory only (no expiry
  until restart) — same zone later in the day won't re-journal. Intended.
- **Zone-broken and zone-mitigated-near branches still reset the full strict payload** (strict_label=
  WAIT, strict_score=0, no-plan trade_plan); the display Edge Score is hard-zeroed by these blockers
  while `gate_debug.edge_score` reports raw components — expected, the blocker is the override.

## Instrument resolution contract (ticker is authoritative)
`webhook()` resolves the instrument once via `resolve_instrument(ticker, alert_type)` and stores it on
the alert record (`record["instrument"]`). The payload `ticker` field is the source of truth; the alert
title is consulted **only** when no ticker is present. Unresolvable/contradictory alerts are **rejected
with HTTP 400, never silently defaulted to MGC**:
- shared BOS/CHOCH with no ticker (instrument named nowhere) → reject
- unrecognized ticker (neither MGC nor MNQ) → reject
- ticker vs title-prefix mismatch (e.g. `MGC VWAP` + `ticker: MNQ1!`) → reject

Instrument-prefixed alerts that omit the ticker still resolve from the title (logged WARNING) — this is
the lenient choice; a stricter "every alert must carry a ticker" policy would reject those too.
`instrument_of()` is the **lenient legacy** normalizer (empty/unknown → MGC) — display/fallback only,
never for webhook attribution. All instrument-scoped consumers (`_has`, `_active_ticker`,
`get_price_context`, card build, live-card routing) prefer `record["instrument"]`.
**Why:** an MNQ alert that omitted `ticker` previously stored VWAP / counted structure under MGC, and a
shared BOS/CHOCH with a falsy ticker used to match BOTH instruments.
