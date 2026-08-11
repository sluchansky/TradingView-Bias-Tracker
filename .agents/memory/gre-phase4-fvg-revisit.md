---
name: GRE Phase 4 — FVG_REVISIT Research Family
description: Multi-family Ghost Research Engine extension; FVG_REVISIT as Research Family #2 alongside 09:30_ORB.
---

# GRE Phase 4 — FVG_REVISIT Research Family

## What was built
`ghost_research_engine.py` extended with FVG_REVISIT as Research Family #2.  
`app.py` `_fvg_bar_close()` wired to call `_gre.on_fvg_bar_close()` after `_fse.process_bar_close()`.

## Key architecture rules

**Why:** strategy_family and strategy are SEPARATE fields — never overload one as the other.
- `strategy_family = "FVG_REVISIT"` (routing / grouping)
- `strategy = "FVG_RESEARCH_BASELINE_V1"` (specific algorithm within family)
- ORB: `strategy_family = "09:30_ORB"`, `strategy = "09:30_ORB"` (happens to be same, but logically distinct)

**How to apply:** Any new research family needs BOTH fields written explicitly.

## DB schema additions (applied to dev DB)
- `ghost_opportunities`: `strategy_family VARCHAR(64)`, `source_fvg_id VARCHAR(128)`, `research_fvg_id VARCHAR(64)`, `revisit_id VARCHAR(64)` + 5 indexes
- `ghost_experiments`: `strategy_family VARCHAR(64)`
- Backfill: all NULL `strategy_family` rows → `'09:30_ORB'`; ORB `strategy` values preserved

**Why:** Production DB needs these columns applied via re-publish before FVG opportunities can be recorded live.

## Identity fields (deterministic, replay-stable)
- `source_fvg_id`: UUID from fvg_engine (zone.id) — NOT deterministic
- `research_fvg_id`: SHA-256 of `(inst, direction.upper(), bar_ts, upper:.4f, lower:.4f)` → 24 hex — deterministic, independent of source uuid
- `revisit_id`: SHA-256 of `(rfid, revisit_n, revisit_bar_ts, FVG_STRATEGY_NAME)` → 24 hex
- Hash version constant: `_FVG_HASH_VERSION = "V1"` — bump if hashing logic changes

## 10 FVG variants
BASELINE, NEAR_EDGE_ENTRY, MIDPOINT_ENTRY, DEEP_FILL_ENTRY, FIRST_TOUCH_ONLY, SECOND_TOUCH_ALLOWED, TREND_REQUIRED, CVD_ALIGNED, TP_1R, TP_1_5R

## Key implementation details
- `_fvg_inside_prev[rfid]`: bool — was bar inside zone LAST bar? (edge detection)
- `_fvg_revisit_count[rfid]`: int — how many revisit sessions detected today?
- `_fvg_opp_created[f"{rfid}|{n}"]`: str — opp_id to prevent duplicate opportunities
- All 3 dicts restored from DB on boot (query uses `extra_snapshot->>'fvg_revisit_number'`)
- `_fvg_family=True` sentinel on `_open_results` entries → ORB `_process_open_experiments` skips them
- Stop = opposite FVG boundary + 2 ticks; Entry = bar close inside zone (conservative)
- `INVALIDATED_BEFORE_ENTRY` result when zone becomes MITIGATED/FAILED/EXPIRED before entry
- Expiry after `_FVG_MAX_WAITING_BARS = 60` bars without entry

## API changes
- `get_health(family=None)`: per-family counts in `family_breakdown`; `families` key lists both
- `get_candidates(min_samples=10, family=None)`: family WHERE clause; fixed GROUP BY to include `o.strategy_family`
- `get_experiments(instrument=None, variant=None, family=None, limit=100)`: family WHERE clause

## Tests
86 tests in `tests/test_gre_phase4_fvg_revisit.py` — all pass.
All 4 smoke tests pass: PARITY OK, SCALP GOLDEN OK, DUAL-SIM OK, BREAKOUT OK.

## Outstanding
- Dashboard family filter (ALL / 09:30 ORB / FVG REVISIT) on Research tab — not yet built
- Production DB apply needed before live FVG opportunities accumulate
