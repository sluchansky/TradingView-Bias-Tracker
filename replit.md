# AI Trading Partner — TradingView Webhook Server

An autonomous futures trading bot that receives TradingView alerts, scores each setup through a multi-layer analysis engine, executes trades via a configurable broker gateway, and surfaces everything in a real-time operator dashboard.

## Run & Operate

### Development
- `python artifacts/tradingview-webhook/app.py` — start the Flask trading engine (port 8000)
- `pnpm --filter @workspace/api-server run dev` — start the Express API proxy (port 5000)
- `pnpm --filter @workspace/home run dev` — start the operator dashboard (React/Vite)

### Test suite
```bash
cd artifacts/tradingview-webhook
python -m pytest tests/ -q            # Phase 8 suite (profitability, DC, GRE, MTF…)
python -m pytest test_*.py -q         # all root-level tests
python -m pytest -q                   # everything
```

### Smoke checks (run these before declaring any change done)
```bash
bash .local/state/check_parity.sh          # PARITY OK — registry/resolver must be byte-identical
bash .local/state/check_scalp_golden.sh    # SCALP GOLDEN OK — byte-identical to baseline
bash .local/state/check_dual_sim.sh        # DUAL-SIM SMOKE OK + node --check on served <script>
bash .local/state/check_breakout_mode.sh   # BREAKOUT MODE SMOKE OK + node --check on served <script>
```
All four must pass before any change is considered complete.

### Database
Managed via Replit's built-in PostgreSQL. App boots with a no-DDL readiness probe; new tables must be created via the database tool (dev) or a re-publish schema diff (prod). The app never runs DDL at boot.

### PostgreSQL durability and republish safety
PostgreSQL is persistent external state. Trading history, research evidence,
ghost/coordinator observations, journals, strategy records, and persisted
operator state live in PostgreSQL and must survive a process restart,
republish, or redeploy. The checkout, Python process memory, runtime caches,
logs, and temporary files are ephemeral and must never be treated as the
database.

The development webhook workflow and `scripts/prod-start.sh` run the
read-only `scripts/persistence_guard.py` before starting services. It checks
that boot schema SQL is non-destructive/idempotent and that automatic SQL
migrations contain no data-writing statements, then reconnects to the
configured non-template PostgreSQL database using SELECT-only access. The
guard itself never creates a database, changes a schema, writes rows, or
prints a connection string. It deliberately does not block normal,
event-driven application persistence such as live research observations.
Run the source-only portion before publishing:

```bash
python scripts/persistence_guard.py --source-only --source-root .
```

Before clicking Publish, confirm the deployment still points at the existing
PostgreSQL database, review the Publish schema diff, and do **not** choose any
overwrite-data option. A normal republish reconnects to the existing database;
it does not restore from the checkout or recreate PostgreSQL. Schema changes
must be additive and reviewed through the Publish flow.

The guard does not disable normal event-driven research persistence,
intentional operator CRUD routes, test-fixture cleanup, or an explicitly run
backup restore against a separately created test database. Those are separate
application or operator actions, not destructive migration/startup
initialization.
Never run `push-force`, `pg_restore`, or a destructive SQL command against the
live development or production database as part of publishing.

### Deployment
Single Reserved VM deployment. The `api-server` prod build supervises Flask + Express. The `home` artifact serves the operator dashboard at `/`. Deploy via Replit Publish UI (not `pnpm run build` alone).

## Stack

- **Trading engine:** Python 3 / Flask (`artifacts/tradingview-webhook/app.py`)
- **API proxy:** Express 5 / Node.js 24 (`artifacts/api-server`)
- **Operator dashboard:** React + Vite (`artifacts/home`)
- **Market data:** Databento (live bars, trades) + Alpha Vantage (VIX)
- **Broker gateway:** TradersPost / PickMyTrade (configurable via `EXECUTION_MODE` env var)
- **Database:** PostgreSQL (Replit managed) — Drizzle ORM for the Node layer; raw psycopg2 for Python
- **Alerts source:** TradingView Pine scripts (webhook POST to `/webhook`)
- **Notifications:** Discord webhooks (per-channel: MNQ, MGC, journal, general)
- **Instruments:** MGC (Micro Gold), MNQ (Micro Nasdaq), MES (Micro S&P), MYM (Micro Dow)
- **Trading modes:** SCALP, SWING (env var `TRADING_MODE`; durable mode change requires re-publish)

## Where things live

| Thing | Location |
|---|---|
| **Main trading engine** | `artifacts/tradingview-webhook/app.py` (all Flask routes, signal scoring, gate logic, broker gateway) |
| **Decision Contract** | `artifacts/tradingview-webhook/decision_contract.py` — shadow lifecycle state machine |
| **Ghost Research Engine** | `artifacts/tradingview-webhook/ghost_research_engine.py` — ORB shadow experiment platform |
| **Profitability Engine** | `artifacts/tradingview-webhook/profitability_engine.py` — ghost observation → net-R accounting |
| **FVG Engine (Step A)** | `artifacts/tradingview-webhook/fvg_engine.py` — all-day FVG/IFVG scanner (shadow/display) |
| **FVG Sequence Engine (Step B)** | `artifacts/tradingview-webhook/fvg_sequence_engine.py` — shadow state machine for FVG entry candidates |
| **ORB Engine** | `artifacts/tradingview-webhook/orb_engine.py` — 09:30 ET Opening Range Breakout (shadow) |
| **Canonical Market State** | `artifacts/tradingview-webhook/canonical_market_state.py` — shadow Databento VWAP/ATR/structure |
| **Left Brain / Market Intelligence** | `artifacts/tradingview-webhook/left_brain_market_intelligence.py` |
| **Databento data layer** | `artifacts/tradingview-webhook/databento_brain.py` |
| **Volatility Intelligence** | `artifacts/tradingview-webhook/volatility_intelligence.py` — VIX via Alpha Vantage |
| **Edge Ledger** | `artifacts/tradingview-webhook/edge_ledger.py` — frozen-signal accounting (Phase 8A) |
| **Scalp Research** | `artifacts/tradingview-webhook/scalp_research.py` — research/display-only strategy lab |
| **Scalp Live Sim** | `artifacts/tradingview-webhook/scalp_live_sim.py` — paper sim on live stream |
| **Native Journal** | `artifacts/tradingview-webhook/` (helpers inside app.py: `_nj_*`) |
| **TradeZella engine** | `artifacts/tradingview-webhook/tradezella_engine.py` + auto-seed script |
| **Backtest engine** | `artifacts/tradingview-webhook/backtest_engine.py` + `bt_acquire.py`, `bt_baseline.py` |
| **Trading Academy** | Inside `app.py` — `/academy/*` routes; `#view-academy` dashboard tab |
| **Express proxy whitelist** | `artifacts/api-server/src/routes/flask-proxy.ts` |
| **Dashboard auth / open paths** | `artifacts/api-server/src/middleware/dashboard-auth.ts` (`OPEN_PATHS`) |
| **Operator dashboard** | `artifacts/home/` (React/Vite; talks to Express proxy at `/api/*`) |
| **Smoke scripts** | `.local/state/check_*.sh` |
| **Phase 8+ test suite** | `artifacts/tradingview-webhook/tests/` |
| **All other tests** | `artifacts/tradingview-webhook/test_*.py` (root level) |

## Architecture decisions

**One Flask app, one gateway.** All trading logic — scoring, gates, broker sends, journal, persistence — lives inside `artifacts/tradingview-webhook/app.py`. Express is only a reverse proxy and auth layer. Never bypass it; never add trading logic to Express.

**Shadow-first for all new research systems.** Every new engine (Decision Contract, GRE, FVG, CanonicalState, MTF, Profitability, Edge Ledger) is SHADOW-ONLY when first deployed. It observes and logs but never gates, sizes, or triggers broker sends. Promotion to a live gate requires explicit operator approval and a new phase.

**Fail-open for observability, fail-closed for money paths.** Research/display layers catch their own exceptions and return safe defaults. Execution gateway, prop guard, safety lock, and daily-loss cap are fail-CLOSED — an exception blocks the trade, never silently passes it.

**App runs no DDL.** `app.py` contains zero `CREATE TABLE` statements. All schema changes go through the Replit database tool (dev) then a re-publish schema diff (prod). Boot does a no-DDL readiness probe and sets a `*_DB_READY` flag; features gate on that flag before touching the table.

**Single execution gateway.** All live broker POSTs go through one function (`_send_broker_order` → `_traderspost_order`). `EXECUTION_MODE` selects `manual_only | paper | traderspost | pickmytrade`. Paper and manual_only modes never send or dedupe. Every mode preserves fail-closed money invariants.

**SCALP vs SWING are full scoring profiles.** They share the same code path but read from `cfg()` for thresholds, ATR multipliers, edge bands, and gate behavior. Any scoring change must keep invariants for both modes and pass parity + scalp-golden smokes.

**Arm state is non-persistent.** The auto-trade arm resets OFF on every restart and re-publish. This is intentional — the operator must explicitly re-arm after a deploy. There is no "auto-re-arm" path.

**Per-instrument isolation throughout.** `ACTIVE_TRADES_BY_INST` (one slot per instrument, RLock), `ALERT_HISTORY` (shared deque, iterated via `list()` snapshot), `ALERT_HISTORY_BY_INST` for throttle. Every gate, zone check, and structure read is instrument-scoped. Cross-instrument leaks are actively tested.

## Product

The operator dashboard (Brain UI) shows:
- **Brain Hero / Orb Halo** — live conviction state with directional indicator
- **Left Brain panels** — per-instrument signal breakdown (VWAP, structure, zones, CVD, volume)
- **Right Brain** — bar-close scanner, strategy scan, setup status
- **Main Brain** — cognitive overlay: debate (Bull/Bear/Judge), governor, memory review, game plan
- **Live Chart** — Databento tick stream with FVG zone overlays and VWAP bands
- **Trade Management** — active position guidance (HOLD / TAKE PARTIAL / MOVE STOP / CONSIDER EXIT)
- **Research Health** — Ghost Research Engine status, event feed, live sim metrics
- **Journal** — native trade journal with coaching drill-down, TradeZella import, eligibility tracking
- **Academy** — learning-only knowledge module (sources → AI-extracted lessons → strategy cards)

The operator can ENTER trades manually from the dashboard, arm/disarm the auto-execute engine, mute per-instrument alerts, switch focus between instruments, and adjust per-asset safety overrides — all without leaving the dashboard.

## User preferences

- **Completion standard:** every session's work must produce zero ILLEGAL TRANSITION or WARNING log lines from changed code paths, all four smokes must pass, and a production verification report must confirm the fix before the session closes.
- **Shadow-first rule:** new engines are always SHADOW_ONLY at first deploy; live gating requires a separate explicit phase.
- **Byte-identical baseline:** parity and golden smokes compare against a stored baseline; any change that alters behavior for the untouched mode (e.g. SWING change breaking SCALP golden) is a blocker.
- **No speculative transitions:** DC LEGAL_TRANSITIONS only gets new pairs when actual production behavior demonstrates they occur — not because they seem theoretically possible.
- **`node --check` on every served `<script>`:** JS-in-Python triple-quoted strings can silently produce malformed JS (backslash escapes, astral emoji). The dual-sim and breakout smokes run `node --check` on the served dashboard `<script>` after every change.

## Gotchas

- **Parity + four smokes must ALL pass before any change is complete.** Run `check_parity.sh`, `check_scalp_golden.sh`, `check_dual_sim.sh`, `check_breakout_mode.sh` after every non-trivial edit. A failing smoke means you broke the byte-identical baseline for one mode even if your target mode looks fine.

- **Flask routes must be added to the Express proxy whitelist or they 404.** The file is `artifacts/api-server/src/routes/flask-proxy.ts`. Every new `/route` in `app.py` needs a matching entry there. Debug 404s by curling `$REPLIT_DEV_DOMAIN/api/your-route` directly.

- **Express must forward the raw body for TradingView webhooks.** TradingView sends `Content-Type: text/plain`. If `express.json()` parses the body first, the proxy body is empty and all evals produce "0 evaluations." The proxy buffers raw bytes and forwards the client's original content-type.

- **Dashboard auth lives in Express, not Flask.** `OPEN_PATHS` in `dashboard-auth.ts` controls what bypasses the password gate. Never lock `/`, `/ping`, `/webhook`, `/healthz`. Webhook routes (`/webhook/enter`, `/webhook/close`) MUST stay open — TradingView cannot send a password.

- **JS-in-Python triple-quote escape trap.** Any `\n`, `\t`, or astral emoji inside a Python `"""..."""` string that gets served as `<script>` content will produce a literal newline / UTF-8 crash. Always use `\\n` inside those strings. `py_compile` won't catch it — run `node --check` on the *served* script (the smokes do this automatically).

- **`compute_manual_trade_management` mutates its input.** It writes `min_r` and `max_r` onto the dict passed to it. Any caller that passes the live `ACTIVE_TRADES_BY_INST` entry directly will corrupt the active trade. Always pass `dict(trade)` (a shallow copy).

- **Decision Contract is SHADOW-ONLY.** `decision_contract.py` observes every execution path but never gates broker transmission. `LEGAL_TRANSITIONS` only gains new pairs when production logs confirm the transition occurs — never speculatively. `OBSERVING → WAIT` was added in Phase 3.1 after confirmed log spam. The rule: if `validate_transition` logs `ILLEGAL TRANSITION`, audit first, then add the pair with a comment explaining why it is legitimate.

- **GRE `dc_registry_fn` must be a lambda, not a direct reference.** The Decision Registry is created after the GRE is instantiated. Pass `dc_registry_fn=lambda: globals().get("_DECISION_REGISTRY")` so GRE looks up the registry at call time, not at boot.

- **`arm_state` must include `execution_enabled=True` and `configured_mode` for `observe_full_analysis` to reach READY/EXECUTABLE.** `armed=False` → READY. `armed=True` + `execution_enabled=True` → EXECUTABLE. Missing either key routes to `BLOCKED_EXECUTION_MODE` in the DC record.

- **The `strategy_trades` table stores raw TradingView symbols** (`MGC1!`) but the dashboard reads canonical names (`MGC`). Any per-symbol query must canonicalize via `_instrument_from_text`.

- **Active trade persistence gap on managed-trade close.** Closing a managed trade must call `_persist_swing_thesis()` for `is_swing` trades, or they resurrect as OPEN on boot. `/stop-managing` flushes all three local position stores.

- **Prod replica ≠ live deployment DB.** Running `executeSql(environment='production')` can show tables that the running deployment logs as "relation does not exist." Trust the deployment logs. Fix divergences by re-publishing, not by hand-migrating prod.

- **`ALERT_HISTORY` deque must be read via `list()` snapshot.** The deque is shared and lock-free under the GIL. Readers must do `for item in list(ALERT_HISTORY):` — never iterate the live deque. Adding a lock is the wrong fix.

- **Trading Academy is learning-only — never wire it into live trading.** The `/academy/*` module is fully walled off from the gate/scoring/auto-execute/broker/sizing path. Setting a strategy `APPROVED`/`active` records intent only; it does NOT place trades. Academy routes are owner-only; never add them to `OPEN_PATHS`.

- **Advisory overlays (Stalk Mode + Active Trade Thinking) are display-only.** Flag-gated (`STALK_MODE_ENABLED` / `ACTIVE_THINKING_ENABLED`, default ON). Active Thinking must pass a `dict(trade)` copy and never touch `ACTIVE_TRADES_BY_INST`. The strict goldens don't exercise overlay-ON; run `bash .local/state/check_stalk_active.sh` after any change here.

- **VWAP auto-fetch resumes after a grace window.** A manual VWAP push wins a short grace window, then auto-fetch resumes. The gate never trades on a stale VWAP; staleness is tracked per-instrument with a strict freshness window.

- **Same-state DC observations are a no-op.** `_observe_full_analysis_inner` only writes a new `DecisionTransition` row when `current.state != can_state`. WAIT→WAIT and OBSERVING→OBSERVING are both in `LEGAL_TRANSITIONS` as self-transitions but create no history row.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details.
- Memory index: `.agents/memory/MEMORY.md` — per-topic durable notes accumulated across sessions.
- Pine script sources for webhooks (confirmation, sweep, volume, structure, CVD, zones, FVG/OB) are tracked in `.agents/memory/pine-webhook-source-scripts.md` — adding a new contract requires editing those scripts, not just `app.py`.
