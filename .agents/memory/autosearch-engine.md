---
name: AutoSearch engine
description: Karpathy-style iterative hypothesis training loop — generation, scoring, ghost validation, manual-only promotion. Display/research only, walled off from money path.
---

## What it is
Research-only loop: generate trade hypotheses → score historically → ghost-validate forward → manual promote to Main Brain.

## DB tables (created out-of-band via DB tool, no DDL in app.py)
- `autosearch_hypotheses` — hypothesis registry with status lifecycle
- `autosearch_historical_scores` — per-hypothesis historical score snapshots
- `autosearch_ghost_samples` — forward ghost validation samples

## Status lifecycle
`testing` → (historical score passes threshold) → `ghost_validating` → (≥20 ghost samples) → `validated` or `rejected` → (manual owner POST) → `promoted`

Thresholds: edge_lift ≥ +4pp, exp_lift ≥ +0.05R, ghost win-rate ≥ 45%, ghost expectancy ≥ 0.

## Key functions
- `_check_autosearch_db_ready()` — boot probe, no DDL
- `_as_generate_hypotheses()` — enumerates instrument × direction × session × regime × grade × mode combos from `strategy_trades` + `micro_scalp_ghost_trades`
- `_as_score_one(hyp_key, conn)` — historical scoring vs baseline; auto-advances to ghost_validating when passing
- `_as_run_scoring_pass()` — single-flight scoring over all testing hyps
- `_as_observe_close(mt)` — ghost observer hooked in `_close_managed_trade` (fail-open, non-blocking)
- `_as_evaluate_ghost(hyp_key, conn)` — evaluates ghost samples after each new observation
- `_as_rebuild_cache()` / `_as_get_cache()` — display cache

## Flask routes (all owner-only, all in BOT1_ROUTES whitelist)
- `GET /autosearch` — cached view
- `POST /autosearch/generate` — auto-generate + kick off scoring
- `POST /autosearch/rescore` — background rescore pass
- `POST /autosearch/add` — add manual hypothesis
- `POST /autosearch/promote/<hyp_key>` — MANUAL-ONLY; requires status='validated'
- `POST /autosearch/reject/<hyp_key>` — manual reject

## Dashboard
Panel `mod-autosearch` with tabs Active/Validated/Promoted/Rejected.
Separate 120s fetch poll (`_asFetch()`) — NOT part of /status hot path.
`renderAutoSearchSummary()` called from `renderMainBrainCognitive()`.

## Critical JS gotcha
**Why:** `\'` inside a Python triple-quoted string (`"""..."""`) is interpreted by Python as just `'` (the backslash is consumed), so what reaches the served JS is a bare `'` that breaks the string delimiter.

**How to apply:** onclick handlers that pass a JS string value must use `data-hk` HTML attributes instead:
```javascript
// WRONG: produces bare ' in JS (SyntaxError)
html += '<button onclick="asPromote(\'' + key + '\')">';

// CORRECT: data attribute, no string escaping needed
html += '<button data-hk="' + esc(key) + '" onclick="asPromote(this.dataset.hk)">';
```

## Safety invariants
- Never touches gate / sizing / dedupe / broker / strategy_trades (read-only on that table)
- INSERT/SELECT only on its 3 own tables
- AUTOSEARCH_ENABLED default OFF → flag-OFF == byte-identical (ghost observer self-guards with `if not AUTOSEARCH_DB_READY: return`)
- Auto-promote is impossible: promote endpoint hard-requires `status='validated'`
