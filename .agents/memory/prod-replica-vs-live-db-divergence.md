---
name: Prod replica can diverge from the live deployment's DB
description: Diagnosing "deployed app logs 'relation X does not exist' while environment:production shows X exists" — the read-replica and the running deployment can be different databases; trust the app's own logs.
---

# Prod replica vs. the live deployment's actual database

`executeSql({ environment: "production" })` queries a READ-REPLICA of the "production
database" Replit has on record. That replica can show tables **with data** that the
CURRENTLY-RUNNING deployment **cannot see** — the deployment logs `relation "..." does
not exist` at the same moment the replica returns hundreds of rows.

**Why:** the replica reflects the schema/data as of a prior deploy/DB state and "may
have outdated schemas" (per the database skill). The live deployment can be bound to a
different / freshly-provisioned / empty production database than the one the replica
mirrors. They are NOT guaranteed to be the same physical DB. Dev is a third, separate
DB again (this project: dev tables exist but are empty; prod replica has the real data).

**How to apply / diagnose:**
- Ground truth for "what the deployed app sees" = the DEPLOYMENT LOGS
  (`fetch_deployment_logs`), NOT the replica. A persistent, current `relation ... does
  not exist` means the live app's DB genuinely lacks the table right now, even if the
  replica says otherwise.
- This app's boot readiness probes gate features on `*_DB_READY`; when the live DB is
  unreadable, `db_ready` goes False → training gate fail-closes to Stage 1 with 0
  counts and records nothing (looks exactly like "panel not updating"), the equity
  curve query warns, etc. — all downstream of the one DB-visibility problem.
- Supported fix is the Publish flow (re-publish re-syncs dev→prod schema + redeploys);
  NEVER hand-migrate prod (no DDL/scripts against the prod URL — see the
  database-migrations-on-publish reference). If "does not exist" PERSISTS after a
  re-publish, the deployment's production-DB BINDING is the culprit (points at a
  different/empty DB than where the data lives) — a Deployment/Database settings issue
  for the user, not a code fix.
- Correlate timestamps: if the last successful writes (max(ts)/max(created_at)) predate
  a suspected culprit deploy, that deploy did NOT cause the break. Here writes stopped
  ~mid-day UTC well before a later unrelated publish.
