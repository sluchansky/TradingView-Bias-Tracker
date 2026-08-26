---
name: Visual Brain SDK transport
description: Dependency-compatible, proxy-isolated transport for the observation-only OpenAI client.
---

Visual Brain must construct its OpenAI client with an explicit
`httpx.Client(verify=True, trust_env=False, timeout=20)`, passing that
transport only to the observer's `OpenAI` instance. Pin `openai==3.3.1` and
`httpx==0.28.1` as direct dependencies.

**Why:** The Windows home-PC probe succeeds with those exact versions and an
explicit top-level HTTPX client, while the OpenAI SDK default client raises
`APIConnectionError`. Disabling environment inheritance prevents malformed
Windows proxy variables from redirecting observer calls while explicit
verification preserves normal TLS certificate checks.

**How to apply:** Keep this transport local to Visual Brain calls; never mutate
proxy environment variables or global client defaults. Preserve the configured
OpenAI API key and base URL, retain the observer's fail-open behavior, and log
only dependency versions plus the transport policy at enabled startup.