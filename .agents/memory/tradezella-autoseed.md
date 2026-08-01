---
name: TradeZella auto-seed reviews
description: How imported TradeZella trades get auto-reviewed from CSV free-text fields without manual click-through.
---

# TradeZella auto-seed reviews

## The rule
`tradezella_auto_seed.py` is a **pure module** — no app import, no DB access, no money path. It exposes one public function: `auto_seed_review(trade: dict) -> dict`.

The engine extracts structured review data from the `mistake`, `notes`, `outcome`, `r_multiple`, `mfe`, and `mae` fields already present in TradeZella exports.

## Why
Previously, every imported trade landed as `review_status='UNREVIEWED'` and contributed nothing to coaching analytics (which require REVIEWED rows). The auto-seed converts them immediately, so coaching data is populated from day one.

## Key invariant — never overwrite manual reviews
The INSERT in `_persist_tradezella_trades()` uses:
```sql
ON CONFLICT (source, trade_id) DO UPDATE SET ...
WHERE journal_reviews.review_status = 'UNREVIEWED'
```
Manually reviewed trades (`review_status != 'UNREVIEWED'`) are **never touched** by auto-seed, on import OR on reseed.

## How to apply
- On new import: auto-seed runs automatically inside `_persist_tradezella_trades()`; result includes `auto_reviewed` count.
- `/tradezella/reseed-reviews` (POST, owner-only): re-seeds all UNREVIEWED rows for already-imported trades without re-uploading the CSV.
- Proxy whitelist: `"/tradezella/reseed-reviews"` is in `flask-proxy.ts`.
- `journal_import_confirm` response now includes `"auto_reviewed"` field.
- Done screen in the import tab shows a green/amber callout with auto-reviewed count + Re-seed button.

## Deterministic review_status logic
- `review_status = 'REVIEWED'` when: any mistake_tag found OR any emotion found OR followed_plan != NOT_APPLICABLE OR overall_quality is not None.
- Otherwise `'UNREVIEWED'` (no text, no outcome).

## Float safety
`r_multiple` is cast with `float(r_mult)` inside a try/except — TradeZella can send strings.
