[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$FlaskPort = 8000,

    [ValidateRange(1, 65535)]
    [int]$ApiPort = 8080,

    [ValidateRange(1, 65535)]
    [int]$DashboardPort = 24319,

    [switch]$EnableDatabento,
    [switch]$EnableVisualBrain,
    [switch]$SkipEnvFile,
    [switch]$NoBrowser,
    # Automation-only: return after all local services pass their readiness checks.
    [switch]$ExitAfterReady
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$flaskRoot = Join-Path $repoRoot "artifacts\tradingview-webhook"
$apiRoot = Join-Path $repoRoot "artifacts\api-server"
$homeRoot = Join-Path $repoRoot "artifacts\home"
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$apiEntry = Join-Path $apiRoot "dist\windows-local-proxy.mjs"

if ($FlaskPort -ne 8000) {
    throw "FlaskPort must remain 8000 so the Express /api proxy reaches the Databento process that owns the live chart bars."
}
if ($ApiPort -eq $DashboardPort) {
    throw "ApiPort and DashboardPort must be different."
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Missing .venv. Follow WINDOWS_HOSTING.md to create the Python environment first."
}
if (-not (Test-Path -LiteralPath (Join-Path $repoRoot "node_modules") -PathType Container)) {
    throw "Missing node_modules. Run pnpm install --frozen-lockfile from the repository root first."
}

$pnpmCommand = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
if (-not $pnpmCommand) {
    $pnpmCommand = Get-Command pnpm -ErrorAction SilentlyContinue
}
if (-not $pnpmCommand) {
    throw "pnpm is required for the existing Express and React services. Enable Corepack or install pnpm first."
}
$nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $nodeCommand) {
    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
}
if (-not $nodeCommand) {
    throw "Node.js LTS is required for the existing Express proxy and React dashboard."
}

if (-not $SkipEnvFile) {
    . (Join-Path $PSScriptRoot "Import-LocalEnv.ps1")
    Import-LocalEnv -Path (Join-Path $repoRoot ".env")
}

function Get-ProcessEnv([string]$Name) {
    return [Environment]::GetEnvironmentVariable($Name, "Process")
}

function Set-SafeDefault([string]$Name, [string]$Value) {
    $current = Get-ProcessEnv $Name
    if ([string]::IsNullOrWhiteSpace($current)) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    } elseif ($current -ne $Value) {
        throw "Refusing to start: $Name must be $Value for this safe Windows dashboard launcher."
    }
}

# These are deliberately enforced for every process this launcher starts.
# Databento is the only optional live-data switch; it never enables execution.
Set-SafeDefault "EXECUTION_MODE" "disabled"
Set-SafeDefault "MANUAL_ORDER_ENABLED" "0"
Set-SafeDefault "LIVE_RUNNER_ENABLED" "0"
Set-SafeDefault "DISCORD_LIVE" "0"
Set-SafeDefault "DISCORD_LIVE_ENABLED" "0"
# Flask derives its live Discord gate from REPLIT_DEPLOYMENT OR DISCORD_LIVE.
# A copied Replit environment must never turn this local launcher into a sender.
Set-SafeDefault "REPLIT_DEPLOYMENT" "0"
Set-SafeDefault "CENTRAL_GHOST_COORDINATOR_FANOUT_ENABLED" "0"
if ($EnableVisualBrain) {
    # Explicit opt-in only. Visual Brain is advisory/observation-only; the
    # launcher still forces execution, manual orders, live runner, Discord,
    # benchmark duplication, and coordinator fan-out off.
    [Environment]::SetEnvironmentVariable("VISUAL_BRAIN_ENABLED", "1", "Process")
} else {
    Set-SafeDefault "VISUAL_BRAIN_ENABLED" "0"
}
Set-SafeDefault "VISUAL_BRAIN_BENCHMARK_ENABLED" "0"
Set-SafeDefault "VISUAL_BRAIN_BENCHMARK_CANDIDATE_ENABLED" "0"
if ($EnableDatabento) {
    [Environment]::SetEnvironmentVariable("DATABENTO_ENABLED", "1", "Process")
} elseif ([string]::IsNullOrWhiteSpace((Get-ProcessEnv "DATABENTO_ENABLED"))) {
    [Environment]::SetEnvironmentVariable("DATABENTO_ENABLED", "0", "Process")
}

function Test-LocalPort([int]$Port) {
    try {
        $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
        return $null -ne $connection
    } catch {
        # Get-NetTCPConnection is not present on some PowerShell installations.
        try {
            $probe = Test-NetConnection -ComputerName 127.0.0.1 -Port $Port -WarningAction SilentlyContinue
            return [bool]$probe.TcpTestSucceeded
        } catch {
            return $false
        }
    }
}

function Get-Json([string]$Uri, [hashtable]$Headers = @{}) {
    return Invoke-RestMethod -Uri $Uri -Method Get -Headers $Headers -TimeoutSec 10
}

function Wait-Response([string]$Uri, [string]$Label, [int]$TimeoutSeconds = 120) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            return Invoke-WebRequest -Uri $Uri -Method Get -UseBasicParsing -TimeoutSec 10
        } catch {
            Start-Sleep -Seconds 2
        }
    } while ((Get-Date) -lt $deadline)
    throw "$Label did not become ready at $Uri."
}

function Wait-Http([string]$Uri, [string]$Label, [hashtable]$Headers = @{}, [int]$TimeoutSeconds = 120) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            return Get-Json $Uri $Headers
        } catch {
            Start-Sleep -Seconds 2
        }
    } while ((Get-Date) -lt $deadline)
    throw "$Label did not become ready at $Uri."
}

function Stop-ProcessTree($Process, [string]$Label) {
    if ($null -eq $Process -or $Process.HasExited) {
        return
    }
    Write-Host "Stopping $Label (PID $($Process.Id))..."
    try {
        & taskkill.exe /PID $Process.Id /T /F *> $null
    } catch {
        try { Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue } catch {}
    }
}

function Assert-LiveChart($Chart, [string]$Label) {
    if ($Chart.enabled -ne $true) {
        throw "$Label chart is disabled. Use -EnableDatabento or set DATABENTO_ENABLED=1 intentionally in the local environment."
    }
    if ($Chart.connection.connected -ne $true) {
        throw "$Label chart is not connected to Databento."
    }
    if ([int]$Chart.bar_count_1m -le 0 -or @($Chart.bars).Count -le 0) {
        throw "$Label chart has no completed MNQ bars."
    }
    if ([string]::IsNullOrWhiteSpace([string]$Chart.connection.last_ts)) {
        throw "$Label chart has no current event timestamp."
    }
    $eventAge = ((Get-Date).ToUniversalTime() - [DateTime]::Parse([string]$Chart.connection.last_ts).ToUniversalTime()).TotalSeconds
    if ($eventAge -gt 45) {
        throw "$Label chart event timestamp is stale ($([math]::Round($eventAge, 1)) seconds old)."
    }
}

function Wait-LiveChartParity([string]$FlaskUri, [string]$ProxyUri, [hashtable]$Headers, [int]$TimeoutSeconds = 180) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastFailure = "chart data has not arrived yet"
    do {
        try {
            $direct = Get-Json $FlaskUri
            $proxied = Get-Json $ProxyUri $Headers
            Assert-LiveChart $direct "Direct Flask"
            Assert-LiveChart $proxied "Browser-facing /api"

            $directLast = @($direct.bars)[-1]
            $proxiedLast = @($proxied.bars)[-1]
            if (
                [string]$directLast.ts -eq [string]$proxiedLast.ts -and
                [string]$directLast.close -eq [string]$proxiedLast.close -and
                [int]$direct.bar_count_1m -eq [int]$proxied.bar_count_1m
            ) {
                return
            }
            $lastFailure = "direct Flask and browser-facing /api MNQ bars differ"
        } catch {
            $lastFailure = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw "Fresh MNQ chart parity was not established within $TimeoutSeconds seconds: $lastFailure"
}

$started = @()
try {
    if (Test-LocalPort $FlaskPort) {
        throw "FlaskPort $FlaskPort is already in use. Stop the existing process before launching so this dashboard owns the exact safe Databento process and chart cache."
    }
    $env:PORT = "$FlaskPort"
    $flaskProcess = Start-Process `
        -FilePath $python `
        -ArgumentList @("app.py") `
        -WorkingDirectory $flaskRoot `
        -PassThru
    $started += [PSCustomObject]@{ Process = $flaskProcess; Label = "Flask" }
    Write-Host "Started Flask on http://127.0.0.1:$FlaskPort (PID $($flaskProcess.Id))."

    [void](Wait-Http "http://127.0.0.1:$FlaskPort/ping" "Flask")

    # Build the existing Express source before starting it so the local proxy
    # cannot silently use an old dist bundle that lacks the chart route.
    Push-Location $repoRoot
    try {
        & $pnpmCommand.Source --filter @workspace/api-server run build
        if ($LASTEXITCODE -ne 0) {
            throw "The existing Express API server build failed."
        }
    } finally {
        Pop-Location
    }
    if (-not (Test-Path -LiteralPath $apiEntry -PathType Leaf)) {
        throw "Express build completed without producing $apiEntry."
    }

    if (Test-LocalPort $ApiPort) {
        throw "ApiPort $ApiPort is already in use. Stop the existing process before launching so /api cannot target a stale or different proxy."
    }
    $env:PORT = "$ApiPort"
    $env:NODE_ENV = "development"
    $apiProcess = Start-Process `
        -FilePath $nodeCommand.Source `
        -ArgumentList @("--enable-source-maps", $apiEntry) `
        -WorkingDirectory $apiRoot `
        -PassThru
    $started += [PSCustomObject]@{ Process = $apiProcess; Label = "Express API proxy" }
    Write-Host "Started Express proxy on http://127.0.0.1:$ApiPort (PID $($apiProcess.Id))."
    [void](Wait-Http "http://127.0.0.1:$ApiPort/api/ping" "Express API proxy")

    if (Test-LocalPort $DashboardPort) {
        throw "DashboardPort $DashboardPort is already in use. Stop the existing dashboard or choose a different local dashboard port."
    }
    $env:PORT = "$DashboardPort"
    $env:BASE_PATH = "/"
    $env:LOCAL_API_PROXY = "1"
    $env:LOCAL_API_PROXY_TARGET = "http://127.0.0.1:$ApiPort"
    $env:LOCAL_DASHBOARD_HOST = "127.0.0.1"
    $homeProcess = Start-Process `
        -FilePath $pnpmCommand.Source `
        -ArgumentList @("--filter", "@workspace/home", "run", "dev") `
        -WorkingDirectory $homeRoot `
        -PassThru
    $started += [PSCustomObject]@{ Process = $homeProcess; Label = "React dashboard" }
    [void](Wait-Response "http://127.0.0.1:$DashboardPort/" "React dashboard")

    # Verify the actual two chart routes against the same Flask instance. The
    # proxy check uses the configured dashboard credential but never prints it.
    if ($EnableDatabento -or (Get-ProcessEnv "DATABENTO_ENABLED") -eq "1") {
        $headers = @{}
        $password = Get-ProcessEnv "DASHBOARD_PASSWORD"
        if (-not [string]::IsNullOrWhiteSpace($password)) {
            $username = Get-ProcessEnv "DASHBOARD_USERNAME"
            if ([string]::IsNullOrWhiteSpace($username)) { $username = "admin" }
            $rawCredential = "{0}:{1}" -f $username, $password
            $encodedCredential = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($rawCredential))
            $headers["Authorization"] = "Basic $encodedCredential"
        }
        Wait-LiveChartParity `
            "http://127.0.0.1:$FlaskPort/main-brain/chart?instrument=MNQ&timeframe=1m&limit=60" `
            "http://127.0.0.1:$ApiPort/api/main-brain/chart?instrument=MNQ&timeframe=1m&limit=60" `
            $headers
        Write-Host "Verified fresh MNQ chart parity: direct Flask and /api have matching nonzero bars."
    } else {
        Write-Host "Databento is disabled by default; chart parity validation will run when started with -EnableDatabento."
    }

    $dashboardUrl = "http://127.0.0.1:$DashboardPort/"
    Write-Host ""
    Write-Host "Windows dashboard ready: $dashboardUrl"
    Write-Host "Express chart proxy:      http://127.0.0.1:$ApiPort/api/main-brain/chart"
    Write-Host "Flask chart source:       http://127.0.0.1:$FlaskPort/main-brain/chart"
    Write-Host "Visual Brain:             $(if ($EnableVisualBrain) { 'enabled by explicit switch (advisory only)' } else { 'disabled' })"
    Write-Host "Keep this PowerShell window open. Press Ctrl+C to stop processes started by this launcher."
    if (-not $NoBrowser) {
        Start-Process $dashboardUrl | Out-Null
    }
    if ($ExitAfterReady) {
        return
    }
    while ($true) {
        foreach ($item in $started) {
            if ($item.Process.HasExited) {
                throw "$($item.Label) exited unexpectedly with code $($item.Process.ExitCode)."
            }
        }
        Start-Sleep -Seconds 2
    }
} finally {
    for ($index = $started.Count - 1; $index -ge 0; $index--) {
        Stop-ProcessTree $started[$index].Process $started[$index].Label
    }
}
