---
name: SWING HTF data layer (auto-compute)
description: How the SWING higher-timeframe (1H/4H/Daily) context is sourced, gated, and consumed; invariants for the SWING overhaul.
---

# SWING HTF context — auto-compute data layer

The SWING-mode overhaul makes SWING genuinely different from SCALP via higher-
timeframe (1H/4H/Daily) confirmation. The data layer that feeds it is
**display-only** and **fail-OPEN**; the money gate that reads it (the P4 entry
veto + target geometry) is the thing that fails **CLOSED**. Keep that split.

## Master gate
- Single helper `_swing_htf_enabled(mode=None)` = the mode (given ARG else global
  TRADING_MODE) is SWING **and** cfg("SWING_HTF_ENABLED") **and** env kill-switch
  `SWING_HTF_ENABLED` != "0".
- **Gotcha (cost test-debugging time):** it keys off the PASSED `mode` arg, so
  `_swing_htf_enabled("SWING")` returns True even when global `TRADING_MODE`
  defaults to SCALP (dev/tests). Any unit test calling `_dynamic_stop_plan(...,
  mode="SWING")` therefore exercises FLAG-ON SWING geometry (2.25× stops), NOT
  legacy — the flag-off legacy path is only reachable via the `SWING_HTF_ENABLED=0`
  kill-switch (what the flag-off golden sets).
- EVERY piece of new SWING behavior is gated on this. SCALP and flag-off SWING
  must be byte-identical to legacy — proven by the goldens (see below).

## Where it lives / how it runs
- State store: module-global `HTF_STATE_BY_INST` keyed by instrument →
  `{ "1H": {...}, "4H": {...}, "1D": {...} }`. Each TF record carries
  `bias/confidence/atr/bars/ts/source` (+ `levels` on 1D).
- Refresh is **folded into `_vwap_autofetch_loop`** (NOT a separate Timer) via
  `_refresh_htf_if_due()`, throttled to `HTF_FETCH_INTERVAL` (300s) with a
  monotonic-clock guard, and is a no-op unless `_swing_htf_enabled()`. So SCALP
  / dev (which defaults to SCALP, no TRADING_MODE in workflow) never fetches HTF.
- **Why fold into the VWAP loop, not a new timer:** one less thread to reason
  about + the loop already proved fail-open. Each block (VWAP / volatility / HTF
  / watcher) is in its own try/except so HTF can never disrupt the others.

## Data sourcing (same public Yahoo feed as VWAP, via `VWAP_FEED_SYMBOL`)
- 1H = Yahoo 60m bars (range 1mo). 4H = **resampled** from the 60m bars into
  fixed UTC-epoch buckets of `HTF_4H_BUCKET_SEC` (= 4*3600), NOT a separate
  fetch. Daily = 1d bars (range 6mo).
- Bias = fast/slow EMA separation (`HTF_EMA_FAST` 8 / `HTF_EMA_SLOW` 21),
  neutral-banded by 10% of ATR so tiny gaps read "neutral"; confidence =
  |ema gap| / ATR clamped to 1.0.
- Daily key levels (`_daily_key_levels`): prior **completed** day's H/L/C (the
  LAST daily bar is the in-progress session, so prior = `bars[-2]`), plus recent
  swing highs/lows as pivots of half-width `HTF_SWING_PIVOT_K` (2), excluding the
  in-progress bar, dedup'd newest-first, capped at 5 each.

## The public read: `compute_swing_context(instrument, price)`
- PURE + FAIL-OPEN (never raises; wraps body in try/except → warning). Returns a
  **STABLE schema** regardless of data availability so dashboard, /status, and
  the gate all see identical keys.
- Key fields: `bias_1h/4h/daily`, `conf_*`, `atr_*`, `aligned_long`/
  `aligned_short` (1H&4H agree), `daily_levels`, `nearest_support`/
  `nearest_resistance`, `daily_level_nearby` (closer of S/R + `within_tolerance`
  flag using `SWING_PULLBACK_ZONE_ATR` * daily ATR), `freshness` (per-TF view w/
  age_min + stale), `complete`, `stale`, `as_of`.
- `complete` = all 3 TFs present AND fresh AND biases decided. `stale` = any
  required TF stale/missing. Per-TF staleness from `SWING_HTF_{1H,4H,1D}_STALE_MIN`
  (120/360/2160). The P4 money gate treats not-complete/stale as a **veto**.
- Attached to `full_analysis` result as `result["swing_context"]` ONLY under
  `_swing_htf_enabled()` (else key absent → byte-identical). Uses
  `instrument_of(active_ticker)` + `result["current_price"]`.

## Chart-push grace (forward-compat for P3 inbound HTF alerts)
- `_htf_chart_override_active(instrument, tf)` returns True if a `source=="chart"`
  record is within `SWING_HTF_GRACE_MIN` (20). The auto-writer skips a TF while a
  fresh chart push owns it (mirrors the VWAP chart-wins-then-auto-resumes pattern).
  No chart pushes exist until P3, so it's always False today — written now so the
  writer is grace-aware from the start.

## Inbound HTF chart pushes (P3, display-only overlay)
- `_ingest_htf_overlay(data, inst, normalized)` (called in /webhook after the RVOL
  ingest, fail-open) writes a `source="chart"` overlay into HTF_STATE_BY_INST ONLY
  when the alert EXPLICITLY tags a higher-timeframe field (`timeframe`/`interval`/
  `tf`) that `_normalize_htf_timeframe` maps to 1H/4H/1D. Sub-hourly (1m/5m/…) /
  missing / unrecognised → None → no write. **Why:** the 1m ALERT_HISTORY must
  never masquerade as HTF authority.
- Bias comes from `_htf_bias_from_alert`: explicit `bias`/`direction`/`trend`/`side`
  field wins, else the alert type's own `side` (bullish/bearish) or HH/HL↔LH/LL.
  Operator chart push = confidence 1.0.
- The overlay MERGES onto the prior record (so a 1D bias/level push PRESERVES the
  auto-computed daily `levels`); a pushed level lands in `levels["chart_levels"]`
  (also consumed by `_nearest_levels`). The auto-writer skips a TF while
  `_htf_chart_override_active` (chart + within SWING_HTF_GRACE_MIN), then resumes
  and rebuilds from scratch (chart_levels naturally drop) — same as VWAP.
- SCALP / flag-off: `_ingest_htf_overlay` returns before any write (byte-identical).

## P4 money path: entry veto + 1:4 (≥SWING_MIN_RR) target geometry
- One read, many consumers: `full_analysis` computes the swing context ONCE
  (flag-on only, try/except→None) and threads the SAME object into the main plan
  builder, the preview builder, the entry veto, and the display attach. Never
  recompute per-consumer — drift between "what the gate saw" and "what the
  dashboard shows" is the failure mode this avoids.
- Entry veto `_swing_entry_veto_reasons(ctx, plan, direction)` is **fail-CLOSED**
  (missing ctx/plan → veto) and lives at the full_analysis seam right AFTER the
  SCALP veto block, mirroring it. Vetoed → WAIT + precise reason + drop plan.
- Target geometry: `build_strict_trade_plan(..., swing_context=...)`. Flag-on
  SWING replaces fixed-1:1 with `_swing_rr_target()` — it scans ALL `daily_levels`
  for the NEAREST opposing level (resistance for Long / support for Short) whose
  tick-SNAPPED reward is ≥ `SWING_MIN_RR` × risk (now **4.0** → 1:4), derives
  reward/`rr_num`/`rr` from the SNAPPED price (so display == broker), and fails
  SAFE to None → no_plan. REJECTS (no_plan) when context is missing, VWAP-only,
  wrong-side, or no qualifying level exists. Paired with the WIDE stop (2.25× ATR),
  a 1:4 target needs a daily level ~9× ATR out, so **fewer, higher-quality SWING
  setups are expected — "quiet" is correct, not broken** (read `swing_diagnostics`
  to tell them apart). SCALP/flag-off keep fixed-1:1 byte-identical.
- **ORB-override hazard (non-obvious, cost a review cycle):** the one sanctioned
  exception to 1:1 — `_apply_orb_target_override` (rewrites a ready ORB plan to
  ~1:4) — is mode-agnostic and runs AFTER the SWING veto, so it WILL clobber a
  confirmed SWING ≥2R target unless it early-returns under `_swing_htf_enabled()`.
  **Why it matters:** any future post-veto target rewriter has the same hazard —
  if it can reach a flag-on SWING plan it must guard with `_swing_htf_enabled()`.
  Regression-test it with an ACTIONABLE verdict (e.g. `"LONG READY"`, not bare
  `"READY"` — `is_actionable` rejects the latter and the rewriter short-circuits
  before the guard, giving a false-green test).

## P5 money path: per-trade thesis + multi-day Postgres persistence
- The `swing_thesis` object + its DB persistence are **local-tracking/DISPLAY
  ONLY** — they must NEVER feed the gate, sizing, dedupe, or the broker money
  path. Same rule as the adaptive-learning engine: INSERT/SELECT only, NO in-app
  DDL (table `swing_theses` created via the database tool in dev + Publish
  schema-diff in prod), reads behind a `*_DB_READY` no-DDL probe, writes offloaded
  to `_enqueue_slow` on an autocommit conn, all FAIL-OPEN.
- **Restart-safety lesson (cost a review cycle):** persisting on *register* is not
  enough. A managed trade that closes must ALSO re-persist so its row flips
  `closed=true`, because rehydrate selects `WHERE closed=FALSE`. Persist at BOTH
  ends — register AND `_close_managed_trade` (gated `if mt.get("is_swing")`,
  fail-open, after `closed=True`/R are finalized). Same rule for P6: persist after
  every review decision and terminal transition, or boot resurrects stale state.
- Rehydration is **INERT**: `_load_swing_theses_from_db` populates
  `MANAGED_TRADES_BY_KEY` directly (set/tuple restored from list, tagged
  `source="postgres"`), never calling register/live-card/journal/broker.
- Cross-UTC-day dedup: `_managed_trade_key` embeds today's date, so a multi-day
  hold reposted on a later day would mint a new key + duplicate. `_find_open_swing_
  managed_trade` reuses the existing OPEN trade within ~1 risk-unit. It MUST filter
  `is_swing` so a SWING repost never reuses a SCALP/legacy managed trade.
- Daily housekeeping pop only removes `closed AND k[date]!=today`, so OPEN
  multi-day SWING trades already survive — the missing piece was the close-path
  re-persist above, not the pop.
- `is_swing` is stamped ONLY in the flag-on SWING register branch, so every P5
  close/dedup/persist hook gated on it is byte-identical for SCALP/legacy.

## P6 money path: 15-min thesis review lifecycle
- `_evaluate_managed_trade_levels` routes SWING trades out FIRST via
  `_swing_lifecycle_enabled(mt)` (`is_swing` + `_swing_htf_enabled()`), placed AFTER
  the shared MFE/MAE update but BEFORE `_scalp_dynamic_lifecycle_enabled`, so SCALP /
  legacy / flag-off fall through byte-identical. `_evaluate_swing_managed_levels` honors
  the terminal stop EVERY bar (before TP; an ambiguous bar resolves against the trade),
  banks the single broker TP immediately, then runs the discretionary review at most once
  per `SWING_REVIEW_INTERVAL_MIN` (15) window.
- Decision precedence EXIT>REDUCE>MOVE_STOP>HOLD (`_derive_swing_review_decision`):
  EXIT = full 1H+4H bias flip; REDUCE = trade-side align broken OR daily opposes (once via
  `swing_reduced`, only cur_r>=0); MOVE_STOP = cur_r>=1 & stop beyond entry (once via
  `swing_stop_moved`, trails stop→entry). ctx None/incomplete/stale → HOLD (fail-OPEN; a
  feed hiccup must never force a spurious discretionary EXIT — the hard stop is separate).
- **LIVE vs LOCAL** (`_apply_swing_review_decision`): stop-move + EXIT-close mutate LOCAL
  state ONLY when `execution_is_live(resolve_execution_mode())` is False; when live the broker
  holds ONE bracket so MOVE_STOP/EXIT become Discord ADVISORY only (`swing_exit_advised` fires
  once) — never book exposure the broker did not take (mirrors SCALP S4). HOLD is silent. Every
  decision still `_schedule_next_swing_review` + `_persist_swing_thesis`; EXIT non-live routes
  through `_close_managed_trade` (self-persists the closed row, P5 close path).
- The once-only review flags (`swing_reduced`/`swing_stop_moved`/`swing_exit_advised`) MUST be in
  the `_swing_mt_snapshot` whitelist or they reset on restart and re-fire.
- **Smoke gotcha:** this repl resolves to a LIVE provider (`traderspost`) because
  `TRADERSPOST_WEBHOOK_URL` is set, so `execution_is_live()` defaults True. Behavioral smokes that
  assert the LOCAL-mutation branch MUST monkeypatch `app.execution_is_live` deterministically
  (False for local, True for advisory) or they fail against ambient broker config. Close-path
  persistence is covered by `swing_close_e2e.py` (real dev Postgres, synchronous `_enqueue_slow`,
  throwaway key DELETEd in `finally`).

## Goldens / harness gotchas
- `golden_swing_baseline.json` = flag-ON baseline, **rebaselined per phase** as
  SWING behavior intentionally changes. The harness passes NO `swing_context`, so
  flag-on plan scenarios correctly resolve to `no_plan` (fail-closed) — that is
  the expected flag-on baseline, NOT a bug.
- `golden_swing_flagoff_baseline.json` = **immutable** flag-off == legacy baseline.

## P7 — DISPLAY-ONLY swing_diagnostics (/status + dashboard)
- ONE nested `swing_diagnostics` block built by `_swing_diag_block(a)`: fail-OPEN (junk/None →
  disabled-shaped dict, never raises), `enabled=_swing_htf_enabled()` so it is INERT
  (`enabled:False`) in SCALP / flag-off. Mirrors `a["swing_context"]` HTF + the open SWING thesis
  found by iterating `MANAGED_TRADES_BY_KEY` for `is_swing` AND not-closed AND
  `instrument==active_ticker` (instrument-SCOPED — the per-instrument view, not a global pick).
- `reason_to_exit` = recorded thesis.reason when status INVALID, else an invalidation-level string;
  `reason_to_hold` = thesis.reason. These are DISPLAY mirrors of P5/P6 state, never a new source.
- Subject to the two standing dashboard rules: curated `/status` is KEY-WHITELISTED (the new key
  must be added to the route dict, which it is) and the dashboard HTML is a plain triple-quoted
  string → render JS uses literal glyphs/em-dashes, NO backslash escapes. Verify by `node --check`
  on the SERVED `/dashboard` inline `<script>` (NOT `/` — dashboard lives at `/dashboard`).
- SCALP `/status` can only ever show `enabled:False`; the enabled=True path (bias/thesis fields,
  instrument-scoping, INVALID reason, fail-open) is covered by the P7 block in `swing_smokes.py`.

## Invariants for any SWING change
- SCALP byte-identical (`check_scalp_golden.sh`), registry parity
  (`check_parity.sh`), and flag-off SWING == legacy baseline
  (`check_swing_flagoff_golden.sh`) must ALL stay green after every edit.
- MGC/MNQ/MES/MYM get HTF together (driven off `VWAP_FEED_SYMBOL`).
- Display layer fail-OPEN; money path (P4+) fail-CLOSED on stale/incomplete HTF.
