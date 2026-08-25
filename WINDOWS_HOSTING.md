# Windows 11 hosting runbook

This document prepares the current TradingView Bias Tracker production source
for a Windows 11 home host. It intentionally does **not** change trading logic,
strategies, gates, thresholds, execution behavior, database contents, or live
settings.

## What is included

The production bot source is primarily:

- `artifacts/tradingview-webhook/` — Flask webhook server, market-data engine,
  dashboard routes, research modules, and tests.
- `artifacts/tradingview-webhook/requirements.txt` — Python dependencies.
- `artifacts/home/` — optional React/Vite operator frontend.
- `artifacts/api-server/` — the existing Express proxy and artifact API service.
  It is required when the React dashboard is run locally because the dashboard's
  `/api/*` requests must reach the same Flask process as the Databento feed.

The validated source branch is `replit-dev`. For a recovery, check out the
specific reviewed commit recorded with the backup/validation record, then verify
the worktree is clean before starting the bot.

## 1. Install prerequisites

Install these from their official installers:

1. Git for Windows: <https://git-scm.com/download/win>
2. Python 3.11 or newer: <https://www.python.org/downloads/windows/>
   Enable **Add python.exe to PATH** during installation.
3. Node.js LTS: <https://nodejs.org/en/download> for the Express proxy or React
   frontend.
4. PostgreSQL client/server only if using a local PostgreSQL database.
   The application expects PostgreSQL, not SQLite.

Confirm PowerShell sees them:

```powershell
git --version
python --version
node --version       # only needed for the optional frontend
```

## 2. Clone the repository

```powershell
git clone https://github.com/sluchansky/TradingView-Bias-Tracker.git
cd TradingView-Bias-Tracker
git fetch origin
git checkout replit-dev
git pull --ff-only
git status --short
git rev-parse HEAD
```

Do not copy the Replit `.env`, database dumps, caches, logs, or API keys into
the repository. Create a new local `.env` from `.env.example`.

## 3. Create the Python environment

From the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r artifacts\tradingview-webhook\requirements.txt
```

If PowerShell blocks activation for the current user, run this once in an
elevated or permitted PowerShell session:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

The visual-analysis module can use Playwright. Install Chromium only if that
feature is enabled and required:

```powershell
python -m playwright install chromium
```

## 4. Create the local environment file

```powershell
Copy-Item .env.example .env
notepad .env
```

Set real values only in the local `.env` or Windows user/system environment;
never commit it. At minimum, set:

- `DASHBOARD_PASSWORD`
- `SESSION_SECRET`
- `DATABENTO_API_KEY` when market data is intentionally enabled
- `DATABASE_URL` when PostgreSQL persistence is intentionally configured

Keep these safety values for the first boot:

```text
EXECUTION_MODE=disabled
DATABENTO_ENABLED=0
DISCORD_LIVE=0
TRAINING_MODE_ENABLED=1
MANUAL_ORDER_ENABLED=0
LIVE_RUNNER_ENABLED=0
```

Before enabling market data or any execution route, verify the corresponding
provider account, database schema, and risk controls separately. This runbook
does not enable live trading.

PowerShell does not automatically load `.env` files into child processes. The
current Flask app reads `os.environ`, so either use a dotenv loader that is
approved for the host, or set the variables in the process environment before
starting:

```powershell
Get-Content .env | Where-Object { $_ -and $_ -notmatch '^\s*#' } |
  ForEach-Object {
    $name, $value = $_ -split '=', 2
    [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), 'Process')
  }
```

For secrets containing `=` or more complex quoting, set them explicitly with
`$env:NAME = "value"` instead of using the simple loader above. The repository
also includes `scripts\windows\Start-TradingBot.ps1`, whose parser safely splits
only the first `=` and never prints values.

## 5. Start the backend

```powershell
.\scripts\windows\Start-TradingBot.ps1
```

The launcher loads a local `.env` when present, defaults every safety setting to
the first-boot values above, and refuses to start if a local file attempts to
enable execution, manual orders, live runner, Databento, or Discord delivery.
The default local port is 8000. To choose it explicitly:

```powershell
.\scripts\windows\Start-TradingBot.ps1 -Port 8000
```

The process must remain running in that PowerShell window. TradingView webhook
URLs must point to a secure, externally reachable HTTPS reverse proxy in front
of this local service; do not expose Flask directly to the public internet.

The isolated analysis bot is optional. It uses the `analysis_bot` PostgreSQL
schema and has hard outbound broker/Discord suppression. Start it in a separate
window only when its analysis UI is required:

```powershell
.\scripts\windows\Start-AnalysisBot.ps1 -Port 8001
```

## 6. Health checks

Open a second PowerShell window:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/ping
Invoke-WebRequest http://127.0.0.1:8000/status
```

The protected operator dashboard is:

```text
http://127.0.0.1:8000/dashboard
```

Use the local `DASHBOARD_PASSWORD`. If `DATABENTO_ENABLED=0`, a disabled-data
response is expected. Do not interpret that as a broker or strategy failure.

## 7. Optional React frontend

The React frontend is an optional Replit artifact. Its `/api/*` calls normally
go through the Replit Express proxy, and its production configuration is a
static artifact. Running it on Windows without an equivalent reverse proxy
requires additional local routing work; do not assume `pnpm dev` alone will
connect it to Flask.

If the frontend is needed for development:

```powershell
corepack enable
corepack prepare pnpm@latest --activate
pnpm install --frozen-lockfile
pnpm --filter @workspace/home run typecheck
pnpm --filter @workspace/home run build
```

For the React dashboard, the repository now includes one coordinated local
launcher. It keeps the Flask/Databento process on port 8000, builds and starts
the existing Express `/api` proxy on port 8080, and starts Vite on port 24319
with a local `/api` bridge:

```powershell
.\scripts\windows\Start-WindowsDashboard.ps1 -EnableDatabento
```

`-EnableDatabento` is deliberate: it enables market-data ingestion only. The
launcher still enforces `EXECUTION_MODE=disabled`, `MANUAL_ORDER_ENABLED=0`,
`LIVE_RUNNER_ENABLED=0`, `DISCORD_LIVE=0`, `DISCORD_LIVE_ENABLED=0`, and
`REPLIT_DEPLOYMENT=0`, and `CENTRAL_GHOST_COORDINATOR_FANOUT_ENABLED=0`.
Without `-EnableDatabento`, the launcher defaults the feed off and reports the
truthful disabled chart state.
The launcher refuses to adopt any process already listening on ports 8000 or
8080. Stop an existing copy first, then use this command so the launcher owns
the exact safe Flask process and its in-memory chart cache. The local Express
bridge is proxy-only: it reuses the established Flask proxy/auth middleware but
does not import database routes or run migrations. With Databento enabled,
startup verifies that direct Flask and
browser-facing `/api/main-brain/chart` return matching fresh MNQ bars before
opening the browser.

Required for the live-data command:

- `.venv` created as described above
- Node.js LTS and pnpm
- `DATABENTO_API_KEY` and `DASHBOARD_PASSWORD` in the local `.env`
- `DATABENTO_ENABLED=1` may be placed in `.env` instead of using the switch,
  but the switch is the recommended explicit command
- ports 8000, 8080, and 24319 must be free before startup

Use `-NoBrowser` to start the same topology without opening a browser. The
direct chart source is
`http://127.0.0.1:8000/main-brain/chart`; the browser-facing chart is
`http://127.0.0.1:8080/api/main-brain/chart`, and the dashboard is
`http://127.0.0.1:24319/`.

## 8. Stop and restart

Stop the foreground bot with `Ctrl+C`. Restart it with the same command:

```powershell
.\.venv\Scripts\Activate.ps1
cd artifacts\tradingview-webhook
python app.py
```

Do not run multiple copies against the same webhook or execution endpoints.
Confirm the old process has stopped before restarting.

## 9. Windows startup automation

Only after manual startup, health checks, database readiness, and safety
settings are verified:

1. Create a dedicated Windows service account.
2. Store secrets in protected Windows environment variables or a protected
   service secret store, not in the repository.
3. Use Task Scheduler or a Windows service wrapper to run
   `scripts\windows\Start-WindowsDashboard.ps1 -EnableDatabento` when the local
   React dashboard is required. This keeps the Flask, Express, and Vite
   processes on their expected ports and stops only processes it started.
4. Configure **At startup**, **Run whether user is logged on or not**, restart
   on failure, and write logs outside the Git working tree.
5. Test a controlled stop/start and confirm `/ping` before allowing any
   external webhook traffic.

Do not enable automatic live execution as part of startup setup.

## Replit-specific dependencies and blockers

The Flask bot itself can run locally, but the following current features are
Replit-specific or require replacement services:

- `artifacts/api-server` uses Replit artifact routing and may use Replit Object
  Storage sidecars for artifact-dependent features. The local dashboard path
  uses the same Express Flask proxy without requiring Replit artifact routing.
- React `/api/*` requests rely on the Express proxy and dashboard auth edge;
  `Start-WindowsDashboard.ps1` provides the local equivalent path. The direct
  Flask dashboard remains available at `/dashboard`, but it is a separate UI.
- Replit-managed PostgreSQL is not automatically available on Windows.
  `DATABASE_URL` must point to an intentionally provisioned PostgreSQL server.
  A verified custom-format backup restores both the database schema and rows;
  do not run source schema files against an already restored database unless
  applying a separately reviewed upgrade.
- Replit secrets are not transferred by Git. Every provider credential must be
  recreated manually on the Windows host.
- Replit deployment flags such as `REPLIT_DEPLOYMENT` have no Windows
  equivalent and must not be faked to enable live behavior.
- The optional AI/Visual Brain functions use the Replit AI integration variable
  names. On Windows, configure a direct OpenAI-compatible provider URL and key
  explicitly, or leave the related feature flags disabled.
- App/Object Storage used by the Express artifact service is not part of the
  direct Flask runtime. Keep the direct dashboard path or provide an equivalent
  external object-storage service before enabling artifact-dependent features.

Replacing the React/Express proxy or Replit Object Storage is deliberately out
of scope; the local dashboard uses the existing proxy rather than replacing it.

## 10. PostgreSQL evidence backup and restore validation

The repository includes logical-backup tooling in `scripts\backup`. It creates
PostgreSQL custom-format dumps that can be restored on Windows or another
PostgreSQL host. It does not start the application, change a source database,
or invoke an execution route.

## 11. Republish and restart durability guard

PostgreSQL is the persistent external state boundary. The trading database
contains the durable trading, research, ghost/coordinator, journal, strategy,
and operator-state evidence. The Git checkout, process memory, runtime caches,
logs, and temporary files are ephemeral; a republish may replace those without
replacing PostgreSQL.

Before starting a Windows copy or publishing the Replit app, run the
source-only policy check from the repository root:

```powershell
py -3.11 scripts\persistence_guard.py --source-only --source-root .
```

The Replit development workflow and production supervisor also run the full
guard before startup. Its database portion uses SELECT-only access to confirm
the configured `DATABASE_URL` reconnects to an existing non-template
PostgreSQL database with a populated `public` catalog. It does not create a
database, apply a migration, alter a schema, write a row, or expose the
connection string. The source check rejects destructive/non-idempotent boot
schema SQL and data-writing automatic migrations; it intentionally leaves
normal event-driven research observation persistence available after startup.

For every Replit Publish:

1. Confirm the deployment still references the existing PostgreSQL database.
2. Review the proposed Publish schema diff.
3. Approve only additive, reviewed schema changes.
4. Do not select any overwrite-data option.
5. After startup, confirm the persistence guard and evidence-health checks.

The guard protects normal startup/deploy schema code and automatic migrations.
It cannot prevent normal event-driven application persistence, an operator
from explicitly running destructive SQL, the `push-force` command, or
restoring over a live database. Those destructive operations are outside the
safe republish process and must never target the live evidence database.

### Safety rules

- Run the backup command with the correct source database URL available only in
  the process environment. Never paste a connection string into a command,
  script, manifest, terminal transcript, or Git file.
- Use an encrypted destination outside the repository and outside Replit's
  filesystem. The tool refuses output paths within the repository and requires
  an explicit destination acknowledgement. It also refuses to create a final
  backup when launched inside a detected Replit runtime.
- Back up development and production separately. Each has its own schema and
  evidence history.
- The scripts discover the actual schema/table catalog, including
  `public`, `analysis_bot`, and coordinator tables if present. They do not
  assume production and development are identical.

### Exact development backup command

With `DATABASE_URL` already supplied securely to the current process for the
development database:

```powershell
py -3.11 scripts\backup\pg_backup.py --environment development --database-url-env DATABASE_URL --output-dir E:\BiasTrackerBackups\development --confirm-external-destination
```

### Exact production backup command

Run this only in an environment where `DATABASE_URL` has been securely supplied
for the production database:

```powershell
py -3.11 scripts\backup\pg_backup.py --environment production --database-url-env DATABASE_URL --output-dir E:\BiasTrackerBackups\production --confirm-external-destination
```

Each command writes two files outside the repository:

- `bias-tracker-<environment>-<UTC timestamp>.pgdump` — a PostgreSQL custom
  logical backup containing every accessible schema except PostgreSQL catalog,
  temporary, and toast schemas.
- `bias-tracker-<environment>-<UTC timestamp>.manifest.json` — environment,
  UTC timestamp, PostgreSQL version, schema/table catalog, critical evidence
  counts/newest timestamps, backup bytes, and SHA-256 checksum. It never
  contains a connection string or credential.

The critical evidence checks explicitly include P4 authoritative verdict
history; SCALP and INTRADAY_TREND research; generic, coordinator, and canonical
ghost evidence; strategy-lab and simulation records; native/standard journals;
edge and send snapshots; market-state evidence; strategy/backtest data in both
`public` and `analysis_bot`; and safety, training, operator, and academy state.
The dump still captures every accessible non-system schema/table, including a
new durable table that is not yet listed in this compatibility checklist.

### Exact test restore and read-only validation commands

Restore only into a newly created **test** database. Do not restore over a
running production or development database:

```powershell
createdb bias_tracker_restore
pg_restore --no-owner --no-acl --dbname=bias_tracker_restore E:\BiasTrackerBackups\production\bias-tracker-production-YYYYMMDDTHHMMSSZ.pgdump
```

With `DATABASE_URL` securely supplied for `bias_tracker_restore`, validate
without writing to it:

```powershell
py -3.11 scripts\backup\restore_validate.py --environment production --database-url-env DATABASE_URL --backup E:\BiasTrackerBackups\production\bias-tracker-production-YYYYMMDDTHHMMSSZ.pgdump --manifest E:\BiasTrackerBackups\production\bias-tracker-production-YYYYMMDDTHHMMSSZ.manifest.json
```

Validation checks the dump SHA-256, schema/table presence, each critical table's
row count, and newest timestamp where the source table exposes one. A mismatch
returns a non-zero exit code. It uses only read-only `SELECT` queries.

### Disaster-recovery sequence

1. Restore the intended GitHub revision.
2. Restore PostgreSQL into a non-production test database.
3. Run manifest validation and resolve every mismatch.
4. Restore secrets from the approved secret store; never from Git.
5. Start with `EXECUTION_MODE=disabled`, `MANUAL_ORDER_ENABLED=0`, and
   `LIVE_RUNNER_ENABLED=0`.
6. Verify evidence health and market-data connectivity.
7. Reconcile broker positions and account state manually.
8. Obtain explicit operator approval before enabling any execution capability.