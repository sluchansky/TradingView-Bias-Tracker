[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$FlaskPort = 8000,
    [ValidateRange(1, 65535)]
    [int]$ApiPort = 8080,
    [ValidateRange(1, 65535)]
    [int]$UiPort = 5173,
    [switch]$EnableDatabento,
    [switch]$NoBrowser,
    [switch]$SkipEnvFile
)

$ErrorActionPreference = "Stop"

if ($FlaskPort -eq $ApiPort -or $FlaskPort -eq $UiPort -or $ApiPort -eq $UiPort) {
    throw "FlaskPort, ApiPort, and UiPort must be different."
}

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$botScript = Join-Path $PSScriptRoot "Start-TradingBot.ps1"
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$apiEntry = Join-Path $repoRoot "artifacts\api-server\dist\windows-local-proxy.mjs"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Missing .venv. Follow WINDOWS_HOSTING.md to create the Python environment first."
}

$pnpmCommand = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
if (-not $pnpmCommand) {
    $pnpmCommand = Get-Command pnpm -ErrorAction SilentlyContinue
}
if (-not $pnpmCommand) {
    throw "pnpm is required for the local Express proxy and React dashboard."
}
$pnpm = $pnpmCommand.Source
$nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $nodeCommand) {
    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
}
if (-not $nodeCommand) {
    throw "Node.js LTS is required for the local Express proxy and React dashboard."
}

if (-not $SkipEnvFile) {
    . (Join-Path $PSScriptRoot "Import-LocalEnv.ps1")
    Import-LocalEnv -Path (Join-Path $repoRoot ".env")
}

if ([Environment]::GetEnvironmentVariable("REPLIT_DEPLOYMENT", "Process") -eq "1") {
    throw "Refusing to start: REPLIT_DEPLOYMENT=1 can enable Discord delivery on this local launcher."
}

$safeDefaults = @{
    "EXECUTION_MODE" = "disabled"
    "DATABENTO_ENABLED" = "0"
    "DISCORD_LIVE" = "0"
    "TRAINING_MODE_ENABLED" = "1"
    "MANUAL_ORDER_ENABLED" = "0"
    "LIVE_RUNNER_ENABLED" = "0"
    "VISUAL_BRAIN_ENABLED" = "0"
    "VISUAL_BRAIN_BENCHMARK_ENABLED" = "0"
    "VISUAL_BRAIN_BENCHMARK_CANDIDATE_ENABLED" = "0"
}
foreach ($entry in $safeDefaults.GetEnumerator()) {
    if (-not [Environment]::GetEnvironmentVariable($entry.Key, "Process")) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
    }
}

if ($EnableDatabento) {
    if (-not [Environment]::GetEnvironmentVariable("DATABENTO_API_KEY", "Process")) {
        throw "Refusing to start with -EnableDatabento: DATABENTO_API_KEY is not configured."
    }
    [Environment]::SetEnvironmentVariable("DATABENTO_ENABLED", "1", "Process")
}

foreach ($entry in $safeDefaults.GetEnumerator()) {
    $expected = $entry.Value
    if ($EnableDatabento -and $entry.Key -eq "DATABENTO_ENABLED") {
        $expected = "1"
    }
    if ([Environment]::GetEnvironmentVariable($entry.Key, "Process") -ne $expected) {
        throw "Refusing to start: $($entry.Key) must be $expected for this safe launcher."
    }
}

function Restore-EnvironmentValue {
    param([string]$Name, [AllowNull()][string]$Value)
    if ($null -eq $Value) {
        Remove-Item "Env:$Name" -ErrorAction SilentlyContinue
    } else {
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

function Wait-ForHttp {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Service,
        [int]$TimeoutSeconds = 90
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                Write-Host "[dashboard] $Service is responding at $Url"
                return
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    } while ((Get-Date) -lt $deadline)
    throw "Timed out waiting for $Service at $Url."
}

function Assert-PortAvailable {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$Service
    )
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($listener) {
        throw "$Service port $Port is already in use (PID $($listener.OwningProcess)). Stop the old local dashboard before starting a new one."
    }
}

function Stop-ProcessTree {
    param([System.Diagnostics.Process]$Child)
    if ($null -eq $Child) {
        return
    }
    try {
        if (-not $Child.HasExited) {
            # /T is essential: pnpm and PowerShell are wrappers whose Node/Python
            # descendants otherwise survive a Ctrl+C and can impersonate a later
            # dashboard launch on the same local ports.
            & taskkill.exe /PID $Child.Id /T /F | Out-Null
            $Child.WaitForExit(5000)
        }
    } catch {
        Write-Warning "Unable to stop child process tree $($Child.Id): $($_.Exception.Message)"
    }
}

$bot = $null
$api = $null
$ui = $null
try {
    Assert-PortAvailable $FlaskPort "Flask"
    Assert-PortAvailable $ApiPort "Express"
    Assert-PortAvailable $UiPort "React dashboard"

    # Build the Express bundle before starting the long-lived child. This avoids
    # the Unix-only `export ... &&` dev script and works in Windows PowerShell.
    Push-Location $repoRoot
    try {
        & $pnpm "--filter" "@workspace/api-server" "run" "build"
        if ($LASTEXITCODE -ne 0) {
            throw "Express proxy build failed with exit code $LASTEXITCODE."
        }
    } finally {
        Pop-Location
    }
    if (-not (Test-Path -LiteralPath $apiEntry -PathType Leaf)) {
        throw "Express proxy build completed without producing $apiEntry."
    }

    $botArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$botScript`" -Port $FlaskPort -SkipEnvFile"
    if ($EnableDatabento) {
        $botArgs += " -EnableDatabento"
    }
    $bot = Start-Process -FilePath "powershell.exe" -ArgumentList $botArgs `
        -WorkingDirectory $repoRoot -PassThru

    $oldPort = [Environment]::GetEnvironmentVariable("PORT", "Process")
    $oldFlaskPort = [Environment]::GetEnvironmentVariable("FLASK_PORT", "Process")
    [Environment]::SetEnvironmentVariable("PORT", "$ApiPort", "Process")
    [Environment]::SetEnvironmentVariable("FLASK_PORT", "$FlaskPort", "Process")
    try {
        # Use the focused proxy-only bundle so the Windows chart topology neither
        # initializes artifact services nor mutates any database state.
        $api = Start-Process -FilePath $nodeCommand.Source `
            -ArgumentList @("--enable-source-maps", $apiEntry) `
            -WorkingDirectory $repoRoot -PassThru
    } finally {
        Restore-EnvironmentValue "PORT" $oldPort
        Restore-EnvironmentValue "FLASK_PORT" $oldFlaskPort
    }

    $oldUiPort = [Environment]::GetEnvironmentVariable("PORT", "Process")
    $oldProxyTarget = [Environment]::GetEnvironmentVariable("LOCAL_API_PROXY_TARGET", "Process")
    $oldBasePath = [Environment]::GetEnvironmentVariable("BASE_PATH", "Process")
    [Environment]::SetEnvironmentVariable("PORT", "$UiPort", "Process")
    [Environment]::SetEnvironmentVariable("LOCAL_API_PROXY_TARGET", "http://127.0.0.1:$ApiPort", "Process")
    [Environment]::SetEnvironmentVariable("BASE_PATH", "/", "Process")
    try {
        $ui = Start-Process -FilePath $pnpm `
            -ArgumentList "--filter @workspace/home run dev" `
            -WorkingDirectory $repoRoot -PassThru
    } finally {
        Restore-EnvironmentValue "PORT" $oldUiPort
        Restore-EnvironmentValue "LOCAL_API_PROXY_TARGET" $oldProxyTarget
        Restore-EnvironmentValue "BASE_PATH" $oldBasePath
    }

    Wait-ForHttp "http://127.0.0.1:$FlaskPort/ping" "Flask"
    Wait-ForHttp "http://127.0.0.1:$ApiPort/api/ping" "Express /api proxy"
    Wait-ForHttp "http://127.0.0.1:$UiPort/" "React dashboard"

    $dashboardUrl = "http://127.0.0.1:$UiPort/"
    Write-Host ""
    Write-Host "[dashboard] Flask :$FlaskPort -> Express /api :$ApiPort -> Vite UI :$UiPort"
    Write-Host "[dashboard] Open $dashboardUrl"
    Write-Host "[dashboard] Databento: $(if ($EnableDatabento) { 'enabled by explicit switch' } else { 'disabled' })"
    Write-Host "[dashboard] Execution, manual orders, live runner, and Discord delivery remain disabled."
    Write-Host "[dashboard] Press Ctrl+C to stop all three processes."
    if (-not $NoBrowser) {
        Start-Process $dashboardUrl | Out-Null
    }

    while (-not $ui.HasExited) {
        if ($bot.HasExited -or $api.HasExited) {
            throw "A required dashboard service exited. Check its child PowerShell/Node window for details."
        }
        Start-Sleep -Seconds 2
    }
} finally {
    foreach ($child in @($ui, $api, $bot)) {
        Stop-ProcessTree $child
    }
}