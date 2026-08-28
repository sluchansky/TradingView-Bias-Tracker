---
name: Published smoke prerequisites
description: Caveats for proving a security boundary on the real published host rather than only in local tests.
---

Published deployment metadata is not sufficient evidence for a live smoke: a deployment may be marked public with a successful build while both public hostnames time out, and the active publish may lag behind the current branch.

**Why:** Production routing and the published revision are outside the local test process; a passing unit test cannot prove that the Replit proxy serves the hardened route.

**How to apply:** Before a published security smoke, obtain the production URL from deployment metadata, check a fast health endpoint over HTTPS on every reported public host, and confirm the current code has been published. If the host does not return, report the smoke as blocked instead of treating deployment metadata as a pass.