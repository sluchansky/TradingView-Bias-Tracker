# Code & Systems Review — TradingView Webhook Bot

_Date: 2026-06-25. Read-only audit. No code was changed._

**Scope:** `artifacts/tradingview-webhook/app.py` (27,331 lines), the Express `/api`
proxy (`api-server`), the auth/CSRF layer, and supporting artifacts. Method: 5
parallel focused audits (money path, security, concurrency, display-vs-money-path
discipline, robustness/tech-debt), then hand-verification of every Critical/High
flag against the source.

**Bottom line:** No critical defects found. The two scariest machine-flagged items
(webhook `ENTER` "bypasses all safety"; CVD/volume gates "fail open") are
intentional, documented design — verified below.

---

## Verified intentional — NO action needed (flagged by audit, cleared on inspection)

1. **Webhook `ENTER` does not bypass the money path** — `_handle_command_alert`
   (~L17410) only calls `set_active_trade()` + posts a Discord message. It never
   calls `execute_trade_gateway`/broker. Local tracking + Discord only, by design
   (TradingView can't send the dashboard password). No real order is sent, so there
   is nothing to bypass.
2. **CVD gate fail-open** (`evaluate_strict_setup` ~L5945) and **Volume gate
   fail-open** (~L5974) are deliberate: when the feed is silent the check no-ops so
   SWING stays byte-identical and a dead feed can't paralyze the bot. CVD becomes a
   hard veto only under `GATE_CVD_HARD` (SWING); in SCALP it's a soft Edge penalty.
   A real risk-tradeoff to be aware of, but a chosen design, not a bug.
3. **`source="dual_tf"` skips the Edge `is_actionable` check** — intentional; it has
   its own readiness logic and shares every downstream money-safety gate.

---

## HIGH — worth reviewing

**H1. Unlocked shared `*_BY_TICKER` state across threads (compound-read
inconsistency).** `CURRENT_PRICE_BY_TICKER`, `VWAP_BY_TICKER`, `CVD_BY_TICKER`,
`VOLUME_SPIKE_BY_TICKER`, `HTF_STATE_BY_INST` are written by the webhook thread and
read by the worker with no lock. The GIL keeps single dict ops safe, but not:
- multi-key reads that must agree (a price and its timestamp read in two steps),
- the CVD 2-candle read-modify-write (~L19745) — concurrent same-ticker webhooks can
  lose an increment or flip state,
- `ZONE_BROKEN_AT` mutation (~L4658) without a lock.

Low probability at current webhook rates, but it's the gate's input data, so it's the
highest-value thing to harden. Suggest one lightweight lock (or atomic snapshot dict)
around the per-ticker state bundle.

---

## MEDIUM

**M1. `/traderspost` runs on the Flask request thread; one-position check is outside
the send lock.** The duplicate-send guard is inside `_TRADERSPOST_LOCK`, but the
`active_trade_for(instrument)` one-position check (~L20934) sits before the lock, and
the manual route does not take `_AUTO_EXEC_LOCK` (only `_maybe_auto_execute` does).
Narrow TOCTOU: a manual dashboard ENTER + a worker auto-trade for the same instrument,
within the same window and producing different dedup keys, could both pass. Identical
orders are still caught by the duplicate-send slot. Recommend pulling the active-trade
check inside the same lock that guards the send.

**M2. SCALP stacking aggregate exposure is bounded only by the daily count by
default.** Per-trade risk is hard-capped ($100/trade via `_risk_capped_contracts`,
`maxContracts`), and `maxOpenTrades` can cap simultaneous positions — but it defaults
to `None` = legacy unlimited. So with defaults, multiple different SCALP setups on one
asset can be open at once, bounded by `maxTradesPerDay` and per-trade risk, not by
concurrent count. Confirm that's the intended risk posture, or set a default
`maxOpenTrades`.

**M3. `_redact` is too narrow.** It masks only keys literally named
`password`/`token` (~L29-34). Free-form bodies are logged (first 500 chars), so a
secret in any other field (webhook URL, `api_key`, `secret`) would hit logs. Real
exposure is low (TradingView payloads don't carry secrets), but widening the key list
/ allowlisting log paths is cheap defense-in-depth.

**M4. Webhook worker has no self-heal/alert on a persistent error.** `_webhook_worker`
(~L18418) catches per-iteration exceptions and logs, but a recurring exception would
silently stall trade processing with no heartbeat/alert. Consider a watchdog or
"stuck worker" notification.

**M5. Fire-once key drift risk.** `_auto_setup_key` (~L1830) and the Fast-Entry key
(~L19292) are constructed separately, both depending on `zone_low` rounding. They must
stay identical or a fast early-entry and the HTF-ready auto could double-enter. They
agree today; centralizing into one shared helper would remove the drift hazard.

---

## LOW / housekeeping

- **L1. No internal Flask auth** — all auth is at the Express edge; direct Flask-port
  access = full control. Mitigated by topology (port isn't public); a defense-in-depth
  note.
- **L2. CSRF is origin/referer-only** (no token). Standard and acceptable; noted for
  completeness.
- **L3. Possible unhandled exceptions on the worker path** — `a["trade_plan"]` assumes
  a well-formed plan; `parsed_price=None` could reach downstream arithmetic. Verify
  defensive guards exist.
- **L4. Hardcoded tuning constants** — `SESSION_BONUS_POINTS=10`, `EDGE_SCORE_MAX=110`,
  TTLs, `TIME_WINDOWS` are module-level; tuning requires a deploy. Consider env-config.
- **L5. Very large functions** — `full_analysis`, `_process_webhook_alert`, the
  `/webhook` handler, the dashboard route are each hundreds of lines (high cyclomatic
  complexity → hard to test safely).
- **L6. 258 broad `except Exception`** — mostly the intentional fail-open pattern
  (good), but a few `except Exception: pass` on instrument-resolution/HTF paths could
  mask real logic errors. No bare `except:` anywhere (good).

---

## What's solid

Server-authoritative sizing (ignores client size, rounds down); confirm-before-track
(only tracks on broker `sent`/`simulated`); gateway fails closed on unknown
instrument, emergency stop, daily-loss, max-open; duplicate-send guard under lock with
slot-release on failure; per-asset safety controls fail-closed; lock discipline
(SAFETY_LOCK never nested under AUTO_TRADE_LOCK); 14 golden/smoke guards protecting
parity; no bare excepts.
