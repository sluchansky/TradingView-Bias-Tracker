---
name: Native Journal read API (Phase 7K-A.2)
description: 3 Flask routes, proxy whitelist, JNativeTradesTab component, and source selector in JTradesTab for reading native_journal rows.
---

## What was built

**Backend** (`app.py`):
- `GET /journal/native-trades` — paginated list (limit/offset, 9 filters, returns `{ok, db_ready, total, limit, offset, filters, trades[]}`)
- `GET /journal/native-trades/<trade_id>` — full detail by UUID (404 if not found)
- `GET /journal/native-counts` — source counts `{native, tradzella, legacy}`, fail-open

All three return 503 with `db_ready: false` when `NJ_DB_READY` is False.

**Proxy whitelist** (`artifacts/api-server/src/routes/flask-proxy.ts`):
- Added `/journal/native-trades`, `/journal/native-trades/:id`, `/journal/native-counts` to `BOT1_ROUTES`.

**Frontend** (`MainBrain.tsx`):
- `NJTrade` / `NJTradeDetail` interfaces (after existing JBatch interface).
- `jLifecycleBadge(status)` — 10 statuses; blue=active/submitted, gray=CLOSED, red=REJECTED/CANCELED, amber=UNKNOWN/NEEDS_REVIEW.
- `JNativeTradesTab` — self-contained component with filter bar, 11-col table, lifecycle badges, pagination, and a fixed-position detail drawer.
- Modified `JTradesTab` to add source selector (NATIVE / TRADZELLA / LEGACY) at the top + live counts from `/api/journal/native-counts`. Default source = `native`. When native: renders `<JNativeTradesTab />`; otherwise keeps existing behaviour wrapped in `<React.Fragment>`.

**Tests** (`test_native_journal_api.py`): 25 tests covering auth/db_ready, pagination bounds, 7 filter types, 404, secrets, empty result, counts endpoint.

## Critical pitfalls

**React.Fragment, not bare `<>`**: The existing detail-pane in `JTradesTab` already uses a `<>...</>` fragment internally. Wrapping the outer `{jSrc !== 'native'}` conditional block with a bare `<>` caused Babel to pair that `<>` with the inner detail pane's `</>`, leaving the outer block's closer unmatched → parse error. Fix: use `<React.Fragment>...</React.Fragment>` as the outer wrapper.

**Why:** Babel's JSX parser matches `<>` with the nearest syntactically valid `</>` — it doesn't scope by nesting depth the way a full context-free parser would when fragments are deeply nested.

**How to apply:** Any time you add a conditional fragment wrapper (`&&` or ternary) around a large existing JSX block that itself contains fragment children, use `<React.Fragment>` explicitly.

## Prod status at time of writing

- `native_journal` table exists in **dev** DB only. Schema is in `db_native_journal_schema.sql`.
- Prod table applied at next Publish (DDL blocked on prod replica).
- The UI degrades to a "NATIVE JOURNAL UNAVAILABLE" message when `NJ_DB_READY = False` (which is the state in prod until Publish).
- 0 rows in dev native_journal — empty state is tested and shown correctly.
