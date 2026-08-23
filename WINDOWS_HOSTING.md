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
- `artifacts/api-server/` — Replit-oriented Express proxy and artifact API
  service; it is not required to run the Flask bot directly on Windows.

The current stable branch is `polish-v1`. Backup and recovery tooling is
developed on `replit-dev`; verify the commit shown by `git log -1` matches the
reviewed source you intentionally want to restore before starting the bot.

## 1. Install prerequisites

Install these from their official installers:

1. Git for Windows: <https://git-scm.com/download/win>
2. Python 3.11 or newer: <https://www.python.org/downloads/windows/>
   Enable **Add python.exe to PATH** during installation.
3. Node.js LTS: <https://nodejs.org/en/download> only if the React frontend
   will be run or rebuilt.
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
git checkout polish-v1
git status
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
`$env:NAME = "value"` instead of using the simple loader above.

## 5. Start the backend

```powershell
.\.venv\Scripts\Activate.ps1
cd artifacts\tradingview-webhook
python app.py
```

The default local port is 8000. To choose it explicitly:

```powershell
$env:PORT = "8000"
python app.py
```

The process must remain running in that PowerShell window. TradingView webhook
URLs must point to a secure, externally reachable HTTPS reverse proxy in front
of this local service; do not expose Flask directly to the public internet.

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

The Flask-served `/dashboard` remains the direct Windows-compatible dashboard
until a local proxy is deliberately designed and tested.

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
3. Use Task Scheduler or a Windows service wrapper to run a PowerShell launcher
   that activates `.venv`, sets `PORT`, changes to
   `artifacts\tradingview-webhook`, and runs `python app.py`.
4. Configure **At startup**, **Run whether user is logged on or not**, restart
   on failure, and write logs outside the Git working tree.
5. Test a controlled stop/start and confirm `/ping` before allowing any
   external webhook traffic.

Do not enable automatic live execution as part of startup setup.

## Replit-specific dependencies and blockers

The Flask bot itself can run locally, but the following current features are
Replit-specific or require replacement services:

- `artifacts/api-server` uses Replit artifact routing, the Flask proxy, and
  Replit Object Storage sidecars.
- React `/api/*` requests rely on the Replit Express proxy and dashboard auth
  edge; the direct Flask dashboard does not provide that same frontend routing.
- Replit-managed PostgreSQL is not automatically available on Windows.
  `DATABASE_URL` must point to an intentionally provisioned PostgreSQL server,
  and the existing schema must be migrated deliberately later.
- Replit secrets are not transferred by Git. Every provider credential must be
  recreated manually on the Windows host.
- Replit deployment flags such as `REPLIT_DEPLOYMENT` have no Windows
  equivalent and must not be faked to enable live behavior.

Database migration and replacement of Replit object storage are deliberately
out of scope for this preparation pass.

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

The critical evidence checks include thesis evaluations, decision transitions,
GRE opportunities/experiments/results, SCALP simulations, Visual Brain, gate
audit, dual sim, strategy/backtest data in both schemas, journals, and
operator/safety state.

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