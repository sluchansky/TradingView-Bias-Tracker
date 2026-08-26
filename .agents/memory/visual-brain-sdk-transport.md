---
name: Visual Brain SDK transport
description: Dependency-compatible, proxy-isolated transport for the observation-only OpenAI client.
---

Visual Brain must construct its OpenAI client with the SDK-provided
`DefaultHttpxClient(verify=True, trust_env=False)`, passing that transport only
to the observer's `OpenAI` instance.

**Why:** The installed OpenAI SDK can provide an HTTPX-compatible client through
its bundled `httpx2` dependency without exposing a top-level `httpx` package.
Using a direct `httpx` import breaks the Visual Brain test/runtime environment.
Disabling environment inheritance prevents malformed Windows proxy variables
from redirecting observer calls while explicit verification preserves normal TLS
certificate checks.

**How to apply:** Keep this transport local to Visual Brain calls; never mutate
proxy environment variables or global client defaults. Preserve the configured
OpenAI API key and base URL, and retain the observer's fail-open behavior.