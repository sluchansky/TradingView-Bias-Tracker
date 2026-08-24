---
name: Verdict history pending-chain recovery
description: Preserve append-only history links when queued persistence retries exhaust.
---

Final-verdict history may only advance a scope's durable chain after the preceding observation is actually written. If a queued head exhausts its retries, cancel its queued descendants, rewind to the last durable predecessor, and let a later analysis create a fresh valid observation.

**Why:** Allowing a successor to persist after its predecessor failed creates an immutable row pointing at a missing previous observation, which cannot be repaired without violating append-only history.

**How to apply:** Keep pending observations scoped and ordered, invalidate a failed suffix before it reaches the database, and test the sequence “head fails, successor is queued, later retry succeeds” under a slow writer.