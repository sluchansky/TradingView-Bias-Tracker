---
name: PostgreSQL startup durability guard
description: Boundaries for safe automatic migrations and startup preflight.
---

Automatic SQL migrations may contain only `CREATE TABLE IF NOT EXISTS` and
`CREATE [UNIQUE] INDEX IF NOT EXISTS`. Any other statement must go through an
explicit reviewed database/deployment operation rather than automatic startup.

**Why:** Regex deny-lists miss valid PostgreSQL syntax (quoted identifiers,
less-common `DROP` forms, procedural blocks), which can allow an unintended
data or schema mutation during a republish.

**How to apply:** Extend the TypeScript and Python policy tests in lockstep if
an additional automatic migration form is genuinely required. The startup
probe itself must remain session-read-only. Normal event-driven research
persistence stays outside the migration guard so observation continues after
boot; it must not be reclassified as automatic migration work.