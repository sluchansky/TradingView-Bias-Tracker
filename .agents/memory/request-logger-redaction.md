---
name: request-logger redaction invariant
description: The Flask before_request logger echoes EVERY request body unless the path is on an explicit allowlist; new endpoints with sensitive payloads must opt out.
---

# `_log_incoming_request` logs every body by default

The `@app.before_request` logger in `artifacts/tradingview-webhook/app.py` writes the
full request body to the app log for **every** request, with only a hardcoded
allowlist of exemptions (currently `/tradezella/upload`, `/review-idea`, and the
GET `/trade` + `/status` polls).

`_redact()` only masks JSON keys literally named `password` or `token`. It does
**not** mask:
- signed URLs or query-string tokens embedded inside *other* fields (e.g. a
  `screenshot` / chart-link field containing `?token=...`),
- free-form text, PII, or anything not keyed exactly `password`/`token`.

**Why:** a feature can claim "screenshot URL is never logged" in its own handler
and still leak it, because the global before_request logger runs first and logs
the raw body. A code review caught exactly this on the trade-idea review endpoint.

**How to apply:** any NEW endpoint that accepts a free-form URL, signed link,
secret, or PII in its body must add its own early-return branch in
`_log_incoming_request()` that logs only metadata (method/path/byte count), the
same way `/tradezella/upload` and `/review-idea` do. Do not rely on `_redact()`.
