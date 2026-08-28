---
name: Gateway safety test isolation
description: Keep gateway contract tests deterministic when independent safety gates depend on ambient time or process state.
---

Gateway contract tests for one response boundary must control unrelated time/session gates and run in fresh processes.

**Why:** Ambient session rules or state left by another suite can reject a fixture before it reaches the contract being tested, producing failures that depend on run time or collection order.

**How to apply:** Freeze time or construct fixtures that explicitly satisfy unrelated guards, and isolate suites by process. Never weaken application safety gates to make a contract test pass.