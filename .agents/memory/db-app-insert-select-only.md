---
name: App-side DB convention (INSERT/SELECT only, no DDL)
description: This Flask app never runs DDL in code; tables are created out-of-band. How new tables reach dev and prod.
---

# App does INSERT/SELECT only — never CREATE TABLE in app code

The trading app (`artifacts/tradingview-webhook/app.py`) is FAIL-OPEN against
Postgres and runs **only INSERT/SELECT** at runtime. It must NOT execute DDL
(`CREATE TABLE`, `ALTER`, etc.). At boot it does a no-DDL **readiness probe**
(`SELECT 1 FROM <table> LIMIT 1`) that sets a `*_DB_READY` flag; on failure it
falls back to in-memory only.

**Why:** The established pattern (learning engine / `strategy_trades`) creates
tables out-of-band, not from app code. A boot-time `CREATE TABLE IF NOT EXISTS`
silently disables itself under a no-DDL production role and diverges from the
convention. An architect review flagged exactly this and it was reverted to a
readiness probe.

**How to apply (adding a new table):**
1. Create the table in the **dev** DB with the database tool / `executeSql` DDL.
2. In app code, add only a readiness probe + INSERT/SELECT (no DDL).
3. Prod gets the table via the **Publish schema-diff** (dev→prod), same as the
   learning engine — the dev schema must contain the table before publishing.

Grep signal: there should be **zero** `CREATE TABLE` in `app.py`. If you find
one, it's a convention violation.
