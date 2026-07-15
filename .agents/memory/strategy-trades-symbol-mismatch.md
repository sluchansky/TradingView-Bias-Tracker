---
name: strategy_trades symbol mismatch (raw vs canonical)
description: Why the dashboard "Today's Trades" / equity curve can silently show empty despite real closed trades — symbol-format drift in strategy_trades.
---

# strategy_trades stores RAW TradingView symbols; per-symbol reads must canonicalize

`strategy_trades.symbol` is written from the trade's raw symbol (`mt.get("symbol")`),
which is the TradingView continuous-contract ticker — e.g. `MGC1!`, `MNQ1!`, `MES1!`,
`MYM1!`. The dashboard tabs and `full_analysis` pass the CANONICAL instrument
(`MGC`/`MNQ`/`MES`/`MYM`). Older rows happened to be stored canonical, so a plain
`WHERE symbol = <canonical>` read worked historically, then silently went empty once
ingestion started persisting the raw `1!` form.

**Symptom:** "Today's trades do not populate" / equity curve empty while the bot is
clearly trading. The trades ARE in the table — the read just doesn't match the stored
symbol. Confirm with a prod read-replica `SELECT symbol, count(*) ... GROUP BY symbol`.

**Fix (applied):** `_record_strategy_trade` now canonicalizes at INSERT with
`_instrument_from_text() or instrument_of()` as fallback — so `MNQ1!` stores as `MNQ`.
The equity curve SELECT filter also has a second clause: if the strict `_instrument_from_text`
fails, it falls back to `instrument_of(row.symbol) == _want` so old `1!` rows are still
matched.

**Rule:** any PER-SYMBOL read of `strategy_trades` must canonicalize both sides. The strict
resolver `_instrument_from_text()` returns None for blank/ambiguous; `instrument_of()` is
lenient and falls back to DEFAULT_INSTRUMENT (MGC) for blank/unknown — only use it when the
symbol is known non-empty and when an MGC fallback is acceptable.

**Blast radius:** as of this writing ONLY the equity-curve read filters by symbol; all
learning-engine / session-analytics reads are GLOBAL (no symbol filter), so the raw-vs-
canonical drift does NOT fragment them. If you add a new per-symbol read, canonicalize it
too.

# get_setup_stage is global + MGC/MNQ-centric (display-only)

`get_setup_stage(current_price, nearest_supply, nearest_demand, bullish, bearish,
alert_history)` has NO instrument param, scans a shared alert window, and its
confirmation/zone alert-type sets are MGC/MNQ-only. Its `stage_next_step` text had a
hardcoded "MGC" that surfaced on the MNQ tab (and in the AI-assistant snapshot) — fixed to
instrument-neutral wording. It is display-only (the gate is instrument-isolated). MES/MYM
stage progression via this function is still imperfect; make it registry-driven if
all-four-instrument stage accuracy ever matters.
