---
name: Published smoke prerequisites
description: Caveats for proving a security boundary on the real published host rather than only in local tests.
---

Published deployment metadata is not sufficient evidence for a live smoke or a shareable link: a deployment may be marked public with a successful build while its public host times out or serves an older revision.

**Why:** Production routing and the active revision are outside the local test process. A passing unit test or metadata health flag cannot prove that the public proxy serves the hardened route.

**How to apply:** Before sharing or smoke-testing a security-sensitive public route, probe a versioned read-only marker through the public HTTPS boundary. Use a trusted canonical origin or verified proxy host metadata, no credentials, an absolute deadline, and a small response cap. Treat missing markers as stale and transport/current-service failures as unavailable; never mint or present a link after either result.