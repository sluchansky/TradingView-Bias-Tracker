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

The current branch is `polish-v1`. Verify the commit shown by `git log -1`
matches the reviewed production baseline before starting the bot.

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
EXECUTION_MODE=manual_only
DATABENTO_ENABLED=0
DISCORD_LIVE=0
TRAINING_MODE_ENABLED=1
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