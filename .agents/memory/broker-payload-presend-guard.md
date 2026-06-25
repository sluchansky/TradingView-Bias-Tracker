---
name: Broker payload pre-send guard
description: Money-path validation/audit that runs in execute_trade_gateway right before the broker POST.
---

# Broker payload pre-send guard

Right after the provider payload is built and IMMEDIATELY before the single
`requests.post(send_url, json=payload)` send sink, the execution gateway:
1. Logs the EXACT JSON about to go on the wire, via a redacted copy
   (`_redact_payload_for_log` masks token/account_id/etc). The destination URL
   (which carries the provider secret) is NEVER logged. TradersPost has no
   redacted fields, so its audit log is the verbatim wire JSON.
2. Validates the provider's REQUIRED fields (`_validate_broker_payload`):
   TradersPost = ticker + action; PickMyTrade = symbol + data. Missing / null /
   empty (blank-string) → reject.
3. On reject: `_release_slot()` (nothing was sent, free the dup-guard for retry),
   log, `_record_exec_rejection` + `_record_diagnostic`, return
   `{"status":"error","reason":...,"blocked_fields":[...]}, 400`. No POST happens.

Surface: `_recent_exec_rejections()` is whitelisted into `/status` and rendered by
the display-only `mod-exec-reject` dashboard module; manual ENTER/sendOrder already
toast `reason` on `status:"error"`.

**Why:** the live gateway always populates ticker/action server-side, so this is a
DEFENSIVE guard — the real-world trigger was the connectivity probe sending a
no-ticker/no-action body, which TradersPost rejects with "Both the action and
ticker fields are required". The point is to never emit an invalid order and to
make any block visible instead of silent.

**How to apply:**
- NEVER modify the `payload` object here — a valid order must stay byte-equivalent
  to the legacy `json=payload` send. Validation reads only.
- Fail-closed: serialize the audit JSON best-effort (a serialization error must not
  turn a local block into a 500 that skips the dashboard record / still sends).
- The rejection log is in-memory (clears on restart), display-only, never gates.
- A new provider means extending `_broker_required_fields` with its required keys
  and `_SENSITIVE_PAYLOAD_KEYS` with any secret it puts in the body.
