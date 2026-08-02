---
name: Native Journal Phase C — Review workflow
description: Operator review layer on native journal: review lifecycle, quality ratings, tags, screenshots backed by App Storage, override assessment, Review Queue tab, review-aware learning eligibility.
---

# Native Journal Phase C — Review Workflow

## Architectural decisions

**Flask route ordering trap:** Flask routes placed in the NJ block (~line 47726) are defined several thousand lines before `_arm_owner_required` (~line 54197). Applying that decorator at the route definition site causes a Python import-time `NameError` that silently breaks all tests that import `app`. **Rule:** Never use `@_arm_owner_required` on routes placed in the Phase A/B/C NJ block. Express-layer auth (`dashboardAuth`) is sufficient for these routes, consistent with the existing NJ GET routes.

**Screenshot upload ownership:** Flask `POST /journal/native-trades/<id>/screenshots` accepts a `storage_key` body field. If exposed directly to clients via the proxy, a client could register arbitrary keys. **Fix:** remove that route from `BOT1_ROUTES` in `flask-proxy.ts`; expose only `POST .../screenshots/upload` via the Express `nj-screenshots.ts` router, which generates a server-side GCS key. Clients never supply the key.

**NJ screenshot flow (Express-native):**
1. `POST /api/journal/native-trades/:id/screenshots/upload` — raw bytes → GCS (key = `nj/attachments/{uuid}.{ext}`) → Flask POST metadata (server-to-server).
2. `GET /api/journal/native-screenshot/:attachment_id` — DB lookup for `storage_key` via JSONB query on `native_journal.screenshots`, then GCS stream.
3. `DELETE /api/journal/native-trades/:id/screenshots/:attachment_id` — DB key lookup → Flask DELETE (removes JSONB entry) → GCS best-effort delete.
Flask DELETE `nj_screenshot_delete` removes only JSONB metadata; GCS cleanup is Express's responsibility.

**`_nj_check_and_set_learning_eligible` 7-col SELECT:** Phase C added `source_label` as the 7th column. Phase A `_set_row()` test helper must return a 7-tuple `(lifecycle, strategy, planned_risk, execution, outcome, review_status, source_label)`.

**Review-aware eligibility by source_label:**
- `EXCLUDED` → blocks always, checked FIRST (before lifecycle).
- `SYSTEM_MANUAL_CONFIRM` / `TRADZELLA_IMPORT` → requires `review_status == REVIEWED`.
- `EXTERNAL_MANUAL` → requires REVIEWED; blocked reason is `attribution_required`.
- `SYSTEM_AUTO` → review optional; UNREVIEWED passes.
- `STATUS_UNKNOWN` review_status → blocked as `unresolved_status` (pre-existing rule).

**Review Queue scope:** Query uses `lifecycle_status NOT IN ('ACTIVE','PENDING','OPENING')` — includes CLOSED, STATUS_UNKNOWN, REJECTED, CANCELED. A CLOSED-only filter omits trades with unresolved outcomes.

**JTradesTab rendering:** The legacy/tradzella content block must use `jSrc === 'tradzella' || jSrc === 'legacy'` — not `jSrc !== 'native'`, which previously caused the legacy block to render under the Queue and Native tabs.

**Queue → Native navigation:** `NJReviewQueueTab.onOpenTrade(id)` sets `pendingNativeId` in JTradesTab and switches `jSrc` to `'native'`. JNativeTradesTab reads `pendingOpenId` prop via `useEffect` and calls `openDetailById(id)` immediately on mount, opening the drawer without requiring the operator to search.

**Why these decisions matter:** The security concern (client-controlled storage keys) is the most critical; any future attachment feature must follow the same Express-generates-key pattern. The rendering-condition bug is subtle because `jSrc !== 'native'` looks correct at a glance but matches 'queue' too.
