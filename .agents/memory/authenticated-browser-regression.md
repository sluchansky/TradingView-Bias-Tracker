---
name: Authenticated browser regression boundary
description: How protected UI regressions verify real authentication without depending on unstable trading or database services.
---

Credential-safe browser regressions should prove the runtime dashboard password against a protected, read-only, no-side-effect edge route. Only after that real authentication succeeds should the test fixture downstream status payloads for deterministic UI assertions.

**Why:** A protected status request can fail because Flask, market data, or PostgreSQL is unavailable even when the credential and Express authentication gate are correct. Coupling the auth proof to those services makes the regression flaky and obscures whether a failure is authentication or application data.

**How to apply:** Keep the credential only in process memory, never log or persist it, require it from the runtime environment, and make the auth-check response contain no credential-derived data. Continue testing the real login form and Authorization header while intercepting only the unstable payload after the edge accepts the request.