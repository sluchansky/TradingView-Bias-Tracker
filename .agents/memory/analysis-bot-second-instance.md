---
name: Analysis-only second bot (/api2)
description: Design invariants for the parallel ANALYSIS-ONLY bot, the live-bot webhook forwarder, and the dev-vs-prod hosting/network-namespace constraint.
---

# Analysis-only second bot ("analysis-bot", served at /api2)

A second, separate bot (`artifacts/analysis-bot/`) mirrors the live bot's analysis
engine but must be physically incapable of trading or posting Discord. It is reached
through the Express `/api2` proxy; the live bot (`artifacts/tradingview-webhook/`)
stays at `/api`.

## Invariants — do not break these
- **bot2 is FAIL-CLOSED analysis-only.** Analysis-only mode is the DEFAULT for that
  directory; it is disabled ONLY by an explicit off-value (`0/false/no/off`). A
  missing or typo'd `ANALYSIS_ONLY` must still engage the kill-switch.
  - **Why:** the project-wide broker + Discord secrets are visible to that process,
    so safety cannot depend on an *unset* env. A fail-OPEN default (the original
    `in ("1","true","yes","on")`) once let a typo make it live-capable.
  - **How to apply:** the kill-switch monkeypatches `requests.post` to suppress any
    non-loopback POST, forces `EXECUTION_MODE=manual_only`, and pins the DB
    `search_path` to the `analysis_bot` schema (no `public`). Any new
    outbound/broker/Discord/DB path must sit behind this same guard.
- **The live bot stays byte-identical** except ONE env-gated, default-OFF webhook
  forwarder (`ANALYSIS_BOT_FORWARD_URL`). UNSET => the whole forwarder block in
  `webhook()` is skipped. The 8 goldens/parity checks are the proof and MUST stay
  green with the env unset.
  - The forwarder is a daemon-thread, fully try/except, fire-and-forget mirror of the
    raw `/webhook` body + original content-type; it can never block or alter the
    live 200. It uses Flask's cached `request.get_data()`, so downstream
    `get_json()` still works.
  - **Self-forward guard** rejects loops two ways: direct (loopback host + our own
    `PORT`) AND the proxied Express route (`path == /api/webhook`, which always
    proxies back to the live bot, on ANY host/port incl. the public deploy host).
    Legit targets — `localhost:8001/webhook` (direct) and `/api2/webhook` (via
    Express) — are NOT matched.

## Dev-vs-prod hosting & the network-namespace gotcha (cost hours to diagnose)
- **PROD works:** `scripts/prod-start.sh` launches the live bot, the analysis bot,
  and Express as children of ONE supervisor (shared network namespace), so
  `/api2 -> localhost:8001` behaves exactly like `/api -> localhost:8000`. The
  analysis bot runs in its OWN respawn loop EXCLUDED from `wait -n`, so a bot2 crash
  can never bounce the live bot.
- **DEV may be unable to host bot2 as a new workflow:** if the session's workflow
  counter is frozen, `configureWorkflow` ADD *and* UPDATE can both fail "Workflow
  limit exceeded" even when the real count is under the cap, and `.replit` can't be
  hand-edited. Don't keep retrying — it's a session-level block, not a real limit;
  add the workflow via the UI instead.
- **A bash-spawned process is in a DIFFERENT network namespace than the Express
  workflow**, so Express returns 502 to `localhost:8001` even though
  `curl localhost:8001` from the same bash call returns 200. This is NOT a proxy-code
  bug (the `/api` and `/api2` proxies share one factory).
  - **Verify the forwarder in dev anyway:** run BOTH bots inside a SINGLE bash call
    (same namespace) — a throwaway live bot on a spare port (Discord/broker env
    blanked) forwarding to the analysis bot — and **kill by PID, never by name
    pattern** (the real workflow shares the identical `python app.py` argv, so a
    pattern kill would take down production).
  - **To run `/api2` live in dev:** add the workflow via the UI (no counter bug):
    `ANALYSIS_ONLY=1 PORT=8001 ... python3 artifacts/analysis-bot/app.py`, console
    output, no `waitForPort` (8001 is not a supported preview port).
- **Any cross-bot dashboard link must degrade gracefully** (e.g. the live dashboard's
  "Analysis Bot ->" pill, the analysis dashboard's "Live Bot" pill). A plain
  `<a href>` to the *other* bot 502s in the dev preview because dev runs only ONE bot,
  so the link must `fetch('/api2/ping')` (or `/api/ping`) first and only navigate on a
  2xx, else show a "only runs in the published app" message. Keep the real `href` as a
  no-JS fallback. **Why:** users test in the dev preview and a raw 502 reads as "the
  feature is broken" when it actually works in prod.

## Editing note
Both `app.py` files are huge; the `read` tool caps `tradingview-webhook/app.py` at
~line 20380. Use `sed -n`/`rg` to view or anchor edits beyond that line.

## Keeping the mirror's alert vocabulary in sync (2026-07-08)
- **bot2 is a STALE SNAPSHOT of the live engine — new instruments/alert types on the
  live bot do NOT reach it automatically.** When the live bot gains an instrument
  (MES/MYM) or alert family (FVG/OB), every forwarded alert of that kind is rejected
  by the mirror as "Unrecognized alert type" (prod-log noise; the mirror's second
  opinion silently loses coverage). The live bot is unaffected.
  - **How to apply:** the mirror now uses the live bot's registry-driven pattern
    (`_ALERT_INSTRUMENTS` × `_PER_INSTRUMENT_ALERT_TEMPLATE` + `_per_inst_alert_set`).
    A new instrument = add it to the mirror's `_ALERT_INSTRUMENTS` + `INSTRUMENT_SPECS`
    + `VWAP_FEED_SYMBOL`; the dispatch sets (`_COMMAND_TYPES`, `_DATA_ONLY_TYPES`,
    zone-broken/mitigated, `_STRUCTURE_RESET`) and CVD/volume sets are all registry-
    driven so they follow automatically. Dual-TF/fast vocab deliberately NOT ported
    (mirror has no consumers).
- **Analyst-side (FVG/OB) entries need the get_price_context exclusion** (`t not in
  ANALYST_TYPES` on the demand branch). The classifier's else-branch is
  "everything not supply/sweep = demand", so recognizing a priced analyst alert
  WITHOUT that exclusion silently turns FVG/OB prints into phantom demand levels
  (corrupts nearest_supply/demand → setup stage/plan anchors). The live bot always
  had the exclusion; the snapshot predated it.
