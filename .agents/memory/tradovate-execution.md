---
name: Tradovate live execution
description: Safety invariants and design decisions for the live broker execution path in the TradingView webhook app.
---

# Tradovate live order execution

`artifacts/tradingview-webhook` gained real broker execution (a separate
`tradovate.py`, direct Tradovate REST, only `requests`). It stays a tracking-only
journal unless execution is explicitly enabled. These are the durable invariants —
any future change to the execution path must preserve them.

## Safety invariants (do not regress)

- **The public webhook must never auto-fire a real order.** Every broker action
  requires a configured server-side execution secret AND a constant-time match on
  the request; there is no "manual flag" fallback. Opening additionally requires
  the runtime execution toggle ON. A raw TradingView alert carries no secret, so
  it can score/journal but never place/flatten/modify.
  **Why:** the webhook URL is public; auto-trading from an unauthenticated POST is
  the worst-case failure for this app.

- **A real position must always be closeable.** CLOSE/breakeven on a broker-backed
  trade requires authorization (the secret) but NOT the toggle — otherwise toggling
  execution off would strand a live position. The broker op must actually succeed
  before any local state changes; on failure return an error and keep the trade.
  Tracking-only trades stay local-only.
  **Why:** mutating local state first made the UI claim "closed" while the real
  position was still open.

- **Fail closed, never fake success.** Any broker uncertainty surfaces as an
  explicit failure, never an `ok`: a full order rejection, an inability to list or
  cancel working orders during flatten, or an inability to verify the open position
  before liquidating. `ok:True` only after the broker confirms.
  **Why:** a surviving working stop on a "flat" account can reopen a position; a
  position-query error mistaken for "flat" skips liquidation on a CLOSE.

- **Safe defaults + reversibility.** Execution OFF by default, env defaults to
  DEMO, the runtime toggle is in-memory and reverts to the env default on restart,
  and enabling refuses without both credentials and the execution secret. Disabling
  is always allowed (the fail-safe direction).

- **Partial fills are kept, not hidden.** A partial bracket fill keeps the trade so
  the operator can flatten it, but still returns an error, and the recorded size is
  the actually-placed quantity, not the requested one.

- **ENTER is a process-level critical section.** The duplicate-check → place →
  record sequence is serialized by one app-level lock across BOTH entry paths
  (webhook command + dashboard route), so two concurrent ENTERs can never both open
  a position.

- **Never log secrets.** The request-body logger redacts credentials/exec-secret;
  the broker module never logs its auth request body.

## Design choices worth keeping consistent

- Market entry + OCO bracket; for ≥2 contracts the size splits across two targets
  (more to T1). Prices must be tick-rounded (Tradovate rejects off-tick) and size is
  clamped to a max-contracts cap.
- Front-month contract and account are resolved on demand and cached; the auth token
  is cached with locking and backoff on the broker's anti-bot challenges.
- The dashboard polls a status endpoint; the live self-test only runs on first use
  or when forced, to avoid a broker round-trip on every poll. A successful ENTER
  must surface the broker order IDs in the active-trade card — persist them via the
  trade payload so they survive refresh/polling, not just a one-shot toast.

## Go-live (surface before enabling)

Inert until the 6 Tradovate secrets AND the execution secret exist. Without the
execution secret nothing can be enabled or fired (fail-closed). Keep env on DEMO and
do one full demo round-trip (enter → breakeven → close, verifying the OCO orders
appear and are cancelled) before considering the real-money env.

**Go-live can be blocked by the broker, not the app.** Some account types (e.g.
prop-firm accounts) do not permit API trading, so no Tradovate API Key (cid/sec) can
be created for them and the live path cannot be turned on. The auth payload requires
username/password PLUS an API Key (cid + sec), appId, deviceId; a login alone is not
enough. If the configured account is not API-capable, do not re-attempt go-live until
an API-capable Tradovate account is supplied.
**Why:** avoid re-requesting credentials that a non-API-capable account type
structurally cannot provide.
