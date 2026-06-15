# TradingView Webhook Trading Partner — Architecture Report

> **Scope:** `artifacts/tradingview-webhook/app.py` (~5,700 lines), a single-file Flask
> service fronted by a Node/Express API server (`artifacts/api-server`).
> **Nature:** **Alert-driven.** The system holds *no OHLC bar history of its own* for
> decision-making — every market-structure fact arrives as a discrete TradingView alert.
> Live 1-minute OHLC is pulled from Yahoo Finance only for VWAP and for the trade-management
> watcher.
> **Instruments:** Two — **MGC** (Micro Gold) and **MNQ** (Micro Nasdaq).
> Line numbers below are approximate (the file changes as features are added).

---

## 0. System Overview & Data Flow

```
                         ┌─────────────────────────────────────────────┐
  TradingView alert      │  Express API server (artifacts/api-server)   │
  (HTTP POST JSON) ─────▶│  /api/* proxy  →  route whitelist  →  Flask   │
                         └─────────────────────────────────────────────┘
                                              │
                                              ▼
                         ┌─────────────────────────────────────────────┐
                         │  Flask app.py  —  POST /webhook              │
                         │  1. parse + resolve instrument + price/vwap  │
                         │  2. append to ALERT_HISTORY (source of truth)│
                         │  3. update global state (zones, flags, price)│
                         │  4. full_analysis() → verdict + edge + plan  │
                         │  5. if READY → live card + journal + manage  │
                         └─────────────────────────────────────────────┘
                              │                │                 │
                              ▼                ▼                 ▼
                        Discord webhooks   JOURNAL (mem)   MANAGED_TRADES_BY_KEY
                        (per-instrument,   in-memory list   watched by the VWAP
                         A+, journal)      (max 500)        autofetch loop (yfinance)
```

**Background loops (threads):**
- **VWAP autofetch loop** (`_vwap_autofetch_loop`, ~L2321) — every ~60s pulls 1m bars,
  refreshes VWAP, and **piggybacks** the managed-trade watcher.
- **Trade-ready loop / event checker** (`_trade_ready_loop`, `check_trade_events` ~L4595) —
  re-posts READY cards on an interval and tracks the manual `ACTIVE_TRADE`.
- **EOD scheduler** (`_schedule_eod`, ~L530) — daily summary at `EOD_HOUR_UTC` (21:00 UTC).
- **Weekly scheduler** (`_schedule_weekly_report`, ~L751) — Friday post-close report.

The central design rule of the recent upgrade is **additive & fail-open**: the webhook
ingestion path, TradingView alert messages, the READY gate, throttling, the 5-minute repost,
and existing cards are never modified. New features are wrapped in `try/except` so a failure
in scoring, management, or reporting can never stop an existing alert from sending.

---

## 1. TradingView Alerts

- **Transport:** TradingView "webhook" alerts → HTTP `POST` with a JSON (or raw-text) body.
- **Entry point:** Flask route `POST /webhook` (`webhook()`, ~L4428–4667).
- **Public exposure:** Requests reach Flask through the Express proxy. Only routes on the
  proxy whitelist (`artifacts/api-server/src/routes/flask-proxy.ts`) are forwarded — any new
  Flask route must be added there or it 404s before ever reaching Flask.
- **Alert vocabulary** (`ALERT_TYPES`, ~L67–106) — discrete event strings, per instrument:
  - **Zones:** `… NEW SUPPLY ZONE`, `… SUPPLY ZONE CONFIRMED`, `… NEW DEMAND ZONE`,
    `… DEMAND ZONE CONFIRMED`, `… ZONE BROKEN`, `… ZONE MITIGATED`.
  - **Structure:** `… CHOCH SUPPLY`, `… BOS SUPPLY`, `… CHOCH DEMAND`, `… BOS DEMAND`.
  - **Confirmation candles:** `… BULLISH CONFIRMATION`, `… BEARISH CONFIRMATION` (5-minute).
  - **Liquidity:** `… BULLISH SWEEP`, `… BEARISH SWEEP`.
  - **Commands:** `ENTER`, `CLOSE` (manual trade lifecycle).
  - **Data:** `… VWAP` (push an exact chart VWAP value).
- **No new alerts required** by the recent upgrade — every new feature is derived from the
  existing alert stream and from yfinance OHLC.

---

## 2. Webhook Parsing

`webhook()` (~L4429) performs, in strict order (so ingestion never depends on later logic):

1. **Body decode** — `request.get_json(force=True, silent=True)`. If the body is not JSON,
   the raw text is treated as the `alert_type` (~L4433–4440).
2. **Alert-type extraction** — read from `alert_type`, then `message`, then `text` (~L4441).
3. **Instrument resolution** — `resolve_instrument()` (~L193–239):
   - The payload **`ticker` field is authoritative**.
   - Fallback: parse the alert title for an `MGC`/`MNQ` prefix (`instrument_of`).
   - **Reject** with HTTP 400 if the ticker is unrecognizable or contradicts the title
     (~L4453) — prevents cross-instrument contamination.
4. **Price extraction** — `price` field → float (~L4469–4473); updates `CURRENT_PRICE` and
   `CURRENT_PRICE_BY_TICKER`.
5. **VWAP extraction** — optional `vwap` field → stored in `VWAP_BY_TICKER` with
   `source="chart"` (~L4480–4489).
6. **History append** — a normalized record is appended to `ALERT_HISTORY` (~L4521); this is
   the **single source of truth** all analysis reads from.
7. **State flags** — zone-broken / zone-mitigated flags updated (~L4529–4546).
8. **Analysis & routing** — `full_analysis()` runs; if the verdict is READY, the live card,
   journal entry, and managed-trade registration fire (all fail-open).

---

## 3. State Management

All decision state lives in **module-level globals** (the app is single-process; mutated under
the `global` keyword, with `_ENTER_LOCK` guarding trade-entry atomicity). State is **volatile —
nothing persists across a restart.**

| Global | Holds | Mutated by (approx.) |
| --- | --- | --- |
| `ALERT_HISTORY` | `deque(maxlen=100)` of alert records — source of truth | `webhook()` L4521; `clear_alerts()` L4789 |
| `CURRENT_PRICE` | Latest price (any instrument) | `webhook()` L4476 |
| `CURRENT_PRICE_BY_TICKER` | `{MGC,MNQ → float}` latest price | `webhook()` L4477 |
| `VWAP_BY_TICKER` | `{ticker → {value, ts, source}}` | `webhook()` L4488; VWAP loop |
| `ACTIVE_TRADE` | Manual open trade (entry/SL/TP/status) | `_handle_command_alert` L4329/4344; nullified on close/stop |
| `MANAGED_TRADES_BY_KEY` | **(new)** auto-watched managed trades | `_register_managed_trade` (~L3160); closed/popped on terminal exit |
| `LAST_READY_BY_TICKER` | **(new)** last READY card snapshot per instrument | `send_live_ready_card` (~L3052); read by `/why` |
| `ZONE_BROKEN_AT` | `{price, alerts_since}` | `_handle_zone_broken`; expired in `webhook` |
| `ZONE_MITIGATED_FLAG` / `MITIGATED_PRICES` | mitigation armed-state + recent mitigation prices | `_handle_zone_mitigated` L2210; cleared on new structure / clear |
| `JOURNAL` | List of trade records (max 500) | `create_journal_entry` (~L4165) |
| `JOURNAL_KEYS` | Dedupe set `(instrument,direction,zone_key)` | `create_journal_entry`; cleared on `/clear` |
| `LAST_ALERT_AT` | Datetime of last valid alert (heartbeat) | `webhook()` L4520 |
| `LAST_LIVE_CARD_AT` | `{instrument → datetime}` throttle for cards | `send_live_ready_card` |

> **Invariant:** the recent upgrade adds **new** globals (`MANAGED_TRADES_BY_KEY`,
> `LAST_READY_BY_TICKER`) rather than overloading `ACTIVE_TRADE` or `CURRENT_PRICE`, so the
> existing manual-trade semantics are untouched.

---

## 4. BOS (Break of Structure) Logic

- **Source:** discrete alerts `BOS DEMAND` (bullish, ~L82) and `BOS SUPPLY` (bearish, ~L80).
- **Context build:** `get_price_context()` (~L893–929) scans `ALERT_HISTORY` and records the
  latest price for each alert type into `last_price_by_type`.
- **Market-structure synthesis:** `get_market_structure()` (~L947–999) — a standalone `BOS`
  (no accompanying CHOCH) is treated as a **"Bullish/Bearish Attempt"** (weaker than a full
  structural shift) with a base score of 2.
- **Strict gate:** in `evaluate_strict_setup()` (~L2353), `has_bos_demand` / `has_bos_supply`
  are confirmed only if the matching BOS label exists inside the recent alert window
  (~L2402–2403). BOS is **gate condition #1** for a READY verdict.

---

## 5. CHOCH (Change of Character) Logic

- **Source:** `CHOCH DEMAND` (bullish, ~L81, score 3) and `CHOCH SUPPLY` (bearish, ~L79).
- **Directional confirmation** (`get_market_structure`, ~L947–999) uses price relative to the
  CHOCH levels:
  - **Bullish structure** when `price > choch_supply` (both present) or `price >= choch_demand`
    (demand only).
  - **Bearish structure** when `price < choch_demand` or `price <= choch_supply`.
- A CHOCH carries more weight than a bare BOS — together (BOS + CHOCH same side) they
  establish a genuine structural shift rather than an "attempt".
- **Strict gate:** CHOCH is **gate condition #2** — Longs require Bullish CHOCH, Shorts require
  Bearish CHOCH.

---

## 6. VWAP Logic

- **Getter:** `get_vwap(ticker, max_age_min)` (~L2229) returns `(value, status)` where status
  is `ok` / `missing` / `stale`. A value older than `max_age_min` (default 30) is `stale`.
- **Two sources, manual wins a grace window:**
  - **Auto-fetch (yfinance):** `_fetch_vwap_from_market()` (~L2251) pulls 1m bars for the
    proxy symbol (`GC=F` ≈ MGC, `NQ=F` ≈ MNQ) and computes
    `Σ(typical_price × volume) / Σ(volume)` (~L2278–2286). `_update_vwap_auto()` (~L2302)
    refreshes the store.
  - **Manual/chart push:** `… VWAP` alerts (or the `vwap` payload field) set an exact value
    with `source="chart"`/`"manual"`. The auto loop will **not** overwrite a manual/chart value
    while it's within `VWAP_OVERRIDE_GRACE_MIN` (default 10 min, ~L2289); after the grace
    window auto resumes.
- **Status is freshness, not direction.** Above/below VWAP is derived separately in
  `evaluate_strict_setup` (~L2414–2416): `price_above = vwap_ok and price > vwap`,
  `price_below = vwap_ok and price < vwap`.
- **Strict gate #4:** Longs require `price_above`, Shorts require `price_below`. The gate never
  trades on a **stale/missing** VWAP.

---

## 7. Supply / Demand Logic

- **Zone capture:** `get_price_context()` (~L893–929) collects supply prices from
  `SUPPLY_TYPES` alerts and demand prices from bullish zone alerts (**sweeps excluded**).
- **Nearest levels:** `get_nearest_levels()` (~L931–945):
  - **Nearest supply** = lowest supply price above current price (else highest in history).
  - **Nearest demand** = highest demand price below current price (else lowest in history).
- **Proximity ("at supply"/"at demand"):** `get_setup_stage()` (~L2091–2093) computes
  `dist = |zone − price| / price` and compares to `WATCH_PCT` (mode-dependent: ~0.75% SWING,
  ~1.0% SCALP).
- **Confirmed zones:** require a `… ZONE CONFIRMED` alert within a recent window (time-based in
  SCALP mode, last-5-alerts in SWING mode), checked in `get_setup_stage` (~L2078–2081) and
  `evaluate_strict_setup` (~L2408–2409). A confirmed zone reaction is a scoring bonus.

---

## 8. Liquidity Sweep Logic

- **Source:** `… BULLISH SWEEP` / `… BEARISH SWEEP` alerts (~L94–97).
- **Deliberately excluded from zone definition** — in `get_price_context` (~L922–924) sweep
  prices are tracked in `last_price_by_type` but never used to define supply/demand levels (a
  sweep is a stop-run, not a zone).
- **Role:** a recent sweep is added to the `confluences` dict in `evaluate_strict_setup`
  (~L2410–2411, 2487) and contributes a **bonus** to the edge score. It is an *edge factor*,
  not a hard READY gate.

---

## 9. Zone Mitigation Logic

- **Arming:** `_handle_zone_mitigated()` (~L2210) sets `ZONE_MITIGATED_FLAG = True` and appends
  the price to `MITIGATED_PRICES` (last 10).
- **Proximity test:** `is_near_mitigated_zone(price)` (~L2214–2222) → True if price is within
  `MITIGATED_TOLERANCE_PCT` (0.3%) of any mitigated price.
- **`zone_mitigated_near`** (in `full_analysis`, ~L2743–2747): True when the flag is armed AND
  the nearest supply/demand is near a mitigated price AND the zone hasn't since been broken or
  confirmed-reacting.
- **In-place WAIT override** (~L2752–2777): when `zone_mitigated_near`, `full_analysis` forces
  `verdict = WAIT` and replaces `trade_plan` with a no-trade stub, then **falls through to the
  single `return`** (`full_analysis` has exactly one return statement, ~L2782) — the invalidation
  message cites the exact `mitigated_zone_price`.
  - **Exception:** a *mitigation-long-confirmed* retest (mitigated demand + Bullish CHOCH + 5m
    confirmation, price above VWAP) is allowed to proceed (~L2428, `mitig_confirmed` ~L2736).
  - **Shape note:** this override (and the zone-broken override) build a *reduced* `trade_plan`
    stub that omits the new management keys, so every downstream reader must use `.get` on
    trade-plan keys (never index) — a WAIT verdict legitimately lacks `target3`/`be_level`/etc.

---

## 10. Edge Score Calculation

`compute_edge_breakdown()` (~L3662) produces a transparent 0–100 score.

**Single source of truth.** The transparent breakdown score is the **one** Edge
Score shown everywhere a user can see it — `/status`, `/why`, the dashboard
headline bar, Discord alert cards, the journal, daily recaps, and weekly reports
all read the same `edge_score`/`edge_grade`. `full_analysis` computes it once via
`_analysis_edge_breakdown(a)` and attaches `edge_score` (transparent),
`edge_grade`, and `edge_breakdown` to its result; `_build_card_entry` reuses that
same breakdown so the card/journal can never diverge from `/status`.

- **Legacy score is internal-only.** The old bias-derived `calculate_edge_score()`
  is retained solely as `legacy_edge_score` for **ranking fallback** on historical
  entries (`_edge_score_for_entry`). It is **never displayed**. Display paths use
  `_display_edge_score(entry)`, which returns the transparent score or `"—"` when
  only a legacy value exists, so daily/weekly averages count transparent scores only.
- **Confluence-authoritative.** When `confluences` are present, BOS/CHOCH/VWAP
  credit is driven by the confluence flags (`conf.bos`/`conf.choch`/`conf.vwap`),
  not by stale entry display-strings; the display-strings are used only as a
  fallback when no confluences exist (legacy/manual entries).
- **Hard blocker → Edge 0 (instrument-scoped).** If the analyzed instrument's zone
  is broken (`zone_broken_active`) or the entry sits on a consumed level
  (`zone_mitigated_near`), the breakdown is cleared, a single risk line is shown,
  and `score = 0`. `zone_broken_active` is gated by the analyzed instrument
  (`ZONE_BROKEN_AT["instrument"]`) so a broken **MGC** zone never zeros a valid
  **MNQ** setup, and vice versa; untagged (legacy) breaks still apply globally.
- **Floor (READY only):** `floor = 75 if (gate_pass or is_ready) else 0`, then
  `score = max(floor, min(100, raw))` (~L3792). Only a READY setup is floored at **75** (the
  "Possible" threshold). A **non-READY** setup is *not* floored — its score is just the raw sum
  of whatever confluences/penalties are present, often a small value (e.g. `5` for a lone
  "Demand Zone Active" bonus), **not necessarily 0**. This is the value `/why` shows for a
  current WAIT.
- **Gate foundation (the 4 required conditions = 75 pts):**
  | Condition | Points |
  | --- | --- |
  | BOS (Demand/Supply) | 25 |
  | CHOCH (Bullish/Bearish) | 25 |
  | 5m Confirmation Candle | 15 |
  | VWAP (Reclaim/Rejection) | 10 |
- **Bonus confluences (additive):**
  | Confluence | Points |
  | --- | --- |
  | Liquidity Sweep | +8 |
  | Confirmed Zone Reaction (no sweep, not mitigated) | +5 |
  | Demand/Supply Zone Active | +5 |
  | Trend Alignment | +4 |
  | Zone Mitigated (confirmed retest) | +3 |
  | High Confidence (≥ `CONF_HIGH`) | +6 |
  | Elevated Confidence (≥ `CONF_TRADE`) | +3 |
- **Risk adjustments (penalties):** Nearby Resistance/Support −4, Overextended −3, Choppy −3
  (~L3763–3769).
- **Clamp:** `score = max(floor, min(100, raw))` (~L3793).
- The breakdown is returned as `score_breakdown` (list of `{label, points}`) and
  `risk_adjustments`, which feed both the card "Edge Breakdown" block and the `/why` endpoint.

---

## 11. Trade Grading

- **Strength bands** (`_trade_strength_from_score`, ~L3616):
  - **95–100 → "A+ Setup"**, **90–94 → "Strong Trade"**, **75–89 → "Possible Trade"**,
    **< 75 → None** (not READY).
- **Display labels** (`_strength_display`, ~L3630): 🔥 **A+ SETUP** / 🟢 **STRONG TRADE** /
  🟡 **POSSIBLE TRADE**.
- **Letter grades** (`_grade_for_score`, ~L3597): A+ ≥90, A ≥85, A- ≥80, B+ ≥75, B ≥70, B-
  ≥65, C+ ≥60, C ≥55, else D.
- **READY verdict pipeline:**
  1. `evaluate_strict_setup()` (~L2353) must return "Strong Trade" or "Possible Trade" from the
     4-condition checklist (BOS, CHOCH, 5m confirmation, VWAP side).
  2. `build_strict_trade_plan()` (~L2534) must confirm **R:R ≥ 1:2 on Target 2** (~L2577).
     If R:R < 2.0 the verdict is **downgraded to WAIT**.
  3. Only then does `full_analysis` (~L2684) emit **READY**.
- **A+ routing:** an A+ card additionally mirrors to `DISCORD_APLUS_WEBHOOK_URL` when set
  (falls back to the standard channel when unset — never blocks Possible/Strong).

---

## 12. Journal Schema

- **Persistence:** in-memory `JOURNAL` list (max 500) + `JOURNAL_KEYS` dedupe set.
  **Volatile — does not survive a restart.**
- **Builders:** `_build_card_entry(a, ticker, record)` (~L4010) is the **single source** for
  both the card and the journal entry; `create_journal_entry(record, a, sizing)` (~L4165)
  dedupes by `(instrument, direction, rounded entry-zone)`, assigns an `id`, sets
  `outcome="Pending"`, and inserts at the head.
- **Fields (grouped):**
  - **Identity:** `id`, `datetime` (ISO), `symbol`, `instrument`.
  - **Setup:** `direction`, `setup_stage`, `verdict`, `entry_zone`, `stop_loss`,
    `target1`, `target2`, `target3`.
  - **Scores:** `strict_score`, `edge_score` (authoritative), `legacy_edge_score`,
    `confidence`, `edge_grade`.
  - **Reasoning:** `why_qualifies`, `bias`, `market_structure`, `risk_zone`,
    `reasoning_chain`, `setup_notes`, `trade_thesis`.
  - **Structure detail:** `bos_type`, `choch_type`, `bos_level`, `choch_level`, `bos_status`,
    `choch_status`, `vwap_position`, `supply_demand_zone`.
  - **Trade-management plan (new):** `target3`, `be_level`, `partial_level`, `runner_target`,
    `risk_points`, `reward_points`, `rr_num`, `max_invalidation`, `management_plan` (nested).
  - **Outcome / management results (new):** `outcome` (Pending/Win/Loss/Breakeven),
    `pnl_dollars`, `r_multiple`, `mfe_r`, `mae_r`, `management_updates` (list of step strings),
    `mgmt_outcome`, `mgmt_result_label`, `mgmt_closed_at`.
  - **Media:** `screenshot`, `screenshot_url` (validated public link; screenshots are *passed
    through* to Discord, never fetched).

---

## 13. Discord Schema

- **Webhook routing:**
  | URL | Purpose |
  | --- | --- |
  | `DISCORD_WEBHOOK_URL` | Default (MGC) channel; heartbeat; weekly report |
  | `DISCORD_MNQ_WEBHOOK_URL` | MNQ channel (selected via `_discord_url()`) |
  | `DISCORD_JOURNAL_WEBHOOK_URL` | Every journaled trade + terminal outcome cards |
  | `DISCORD_APLUS_WEBHOOK_URL` *(optional)* | Mirror for 🔥 A+ setups only |
- **Embeds:**
  | Card | Function | Notes |
  | --- | --- | --- |
  | Live Ready Card | `send_live_ready_card` (~L3017) → `_build_trade_card_embed` (~L2911) | the single alert format |
  | Management Update | `_send_management_update` (~L3350) | e.g. "🎯 TP1 hit — stop → breakeven" |
  | Outcome Card | `_send_outcome_update` (~L3364) | Result, R-multiple, MFE/MAE, management path |
  | Weekly Report | `_send_weekly_report` (~L665) | Net P&L, Net R, PF, by-strength split |
- **Live card structure** (`_build_trade_card_embed`):
  - **Title:** `📓 {symbol} {emoji} {direction}`.
  - **Color by strength:** A+ `0xFF4500`, Strong `0x2ECC71`, Possible `0xF1C40F`.
  - **Fields:** info grid (instrument/time/direction) · structure grid (BOS@level, CHOCH@level)
    · execution grid (entry zone, stop, TP1, TP2) · edge grid (confidence, VWAP, zone status)
    · Edge Breakdown block · 💬 Why-it-qualifies · 📋 **Trade Management** block (TP3, BE,
    partial, runner, risk/reward points) · image = `screenshot_url` if valid.
- **Throttling (unchanged):** fires once per READY setup, then re-posts every
  `TRADE_READY_INTERVAL`; a per-instrument throttle (`LAST_LIVE_CARD_AT`) prevents an
  instant+periodic double-post.

---

## 14. Analytics Calculations

`JOURNAL` is the source of truth for all metrics; `_outcome_state()` classifies each entry as
win / loss / breakeven.

- **End-of-day** — `_compute_eod_stats()` (~L363): filters to today's ISO date, counts W/L, nets
  P&L (prefers `pnl_dollars`, else parses the outcome string), and picks Best (highest
  `edge_score`) and Worst (most recent loss). Posted by `_send_eod_summary` via `_schedule_eod`
  (~L530) at `EOD_HOUR_UTC` (21:00 UTC).
- **Performance stats** — `compute_performance_stats()` (~L3871): win rate, profit factor
  (gross win ÷ gross loss, "∞" when no losses), average R. `post_performance_stats()` (~L3950)
  splits into three views: **by_strength** (A+/Strong/Possible), **by_instrument** (MGC/MNQ),
  **by_direction** (Long/Short).
- **Weekly** — `_compute_weekly_stats()` (~L582) extends the performance engine with `net_pnl`,
  `net_r`, best/worst instrument & direction, `avg_edge_score`, and the A+/Strong/Possible
  split. `_entry_realized_r()` (~L571) prefers the watcher's recorded `r_multiple`, else a
  computed proxy. `_schedule_weekly_report()` (~L751) fires Friday after close
  (`WEEKLY_REPORT_DOW` / `WEEKLY_REPORT_HOUR_UTC`); `GET/POST /weekly` previews/triggers it.
- **`/why` endpoint** (`why_endpoint`, ~L5147; helper `_build_why_explanation`, ~L4104):
  explains the current/last READY setup — direction, edge, strength, the 4 passed gate
  conditions, bonus confluences, active risks, invalidation, and concrete improvements toward
  A+. Prefers the `LAST_READY_BY_TICKER` snapshot; falls back to a fresh `full_analysis()` read
  defensively (`.get` only). Accepts `?ticker=` or `/why/<ticker>`; defaults to the active
  instrument.

---

## 15. Trade Management Engine

A real-time, OHLC-based watcher that runs **independently of** the manual `ACTIVE_TRADE`, so it
can never disturb manual-trade semantics.

- **Plan extension (additive):** `INSTRUMENT_SPECS` and `build_strict_trade_plan` gain
  `target3`, `be_level`, `partial_level`, `runner_target`, `risk_points`, `reward_points`,
  `rr_num`, `max_invalidation` — new keys only; existing keys unchanged.
- **Registration:** `_register_managed_trade()` (~L3160) is idempotent — keyed by
  `(instrument, direction, entry-zone-low, date)`. Registered **after** the READY card posts; a
  5-minute repost refreshes the trade **without resetting already-sent level events**.
- **Watch loop:** `_watch_managed_trades()` piggybacks `_vwap_autofetch_loop` (~L2321, ~60s),
  pulling the latest 1m bar via `_fetch_latest_bar()` (~L3210). `CURRENT_PRICE` semantics and the
  manual target-hit region are left untouched.
- **Level evaluation:** `_evaluate_managed_trade_levels()` (~L3256):
  - **Conservative precedence** — checks **stop / invalidation before targets** within a single
    ambiguous bar.
  - Tracks **TP1 → TP2 → TP3**, a partial level, and a breakeven flip (`be_active = True` after
    TP1 moves the effective stop to entry).
  - **MFE/MAE** captured from bar high/low (points → R).
  - **Invalidation** = stop hit OR price beyond stop/entry-zone (deliberately **not**
    VWAP-based, to avoid intrabar flips).
  - **Per-level dedupe** prevents repeated messages across reposts.
- **Discord:** `_send_management_update()` (~L3350) on each new level; `_send_outcome_update()`
  (~L3364) on terminal exit (Win/Loss/Breakeven, with R, MFE, MAE, management path).
- **Journal write-back:** `_close_managed_trade()` (~L3320) computes terminal `pnl_dollars` /
  `r_multiple`; `_apply_outcome_to_journal()` (~L3399) writes additive keys (`mgmt_outcome`,
  `r_multiple`, `mfe_r`, `mae_r`, `management_updates`, …) without overwriting manual fields.

---

## 16. Failure Handling

- **Fail-open ingestion order:** `webhook()` records price/VWAP and appends to `ALERT_HISTORY`
  **before** any scoring, sizing, management, or routing — so even if downstream logic throws,
  the alert is still captured.
- **Every new feature is `try/except`-wrapped:** A+ routing, the management watcher, outcome
  write-back, weekly report, and `/why` all swallow their own errors (logged via `logger.error`)
  and return safe defaults. A failure in any of them **cannot** stop an existing alert/card from
  sending.
- **`/why` defensive contract:** reads card-entry/analysis dicts with `.get` only; on snapshot
  miss it falls back to `full_analysis`; on any render error it returns a 200 with an `error`
  field rather than a 500.
- **Single return, shared shape:** `full_analysis()` has exactly one `return` (~L2782); the
  zone-broken and zone-mitigated branches mutate verdict/`trade_plan` *in place* before it, so
  every verdict shares one result shape. The WAIT branches set a reduced `trade_plan` stub, so
  downstream readers must use `.get` on trade-plan keys (a WAIT legitimately lacks the new
  management keys) — indexing would be a state-dependent 500 invisible to fresh tests.
- **Validation rejects bad input:** unknown/contradictory tickers → HTTP 400; stale/missing
  VWAP never trades.
- **Dedupe everywhere:** `JOURNAL_KEYS` prevents duplicate journal entries per setup/day; managed
  trades dedupe by composite key; management level-events dedupe across reposts.
- **Concurrency:** `_ENTER_LOCK` guards trade entry against race conditions between the webhook
  thread and the lifecycle loop.
- **Proxy whitelist:** any Flask route must be registered in the Express `/api` proxy whitelist
  (`flask-proxy.ts`) or it 404s before reaching Flask — the `/weekly` and `/why` routes were
  added there.
- **Volatility caveat:** all state (alerts, journal, managed trades) is in-memory; a restart
  resets history, the journal, and any in-flight managed trades.

---

*Report generated from a static read of `app.py` and the Express proxy. Line numbers are
approximate and intended as navigation hints, not exact references.*
