[CmdletBinding()]
param(
    [int]$StartupTimeoutSeconds = 300
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$launcher = Join-Path $PSScriptRoot "Start-WindowsDashboard.ps1"
$ports = @(8000, 8080, 24319)
$healthUri = "http://127.0.0.1:8000/ping"
$runnerTemp = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
$stdoutPath = Join-Path $runnerTemp "windows-dashboard-smoke.stdout.log"
$stderrPath = Join-Path $runnerTemp "windows-dashboard-smoke.stderr.log"

function Set-ProcessEnvironmentValue([string]$Name, [AllowNull()][string]$Value) {
    [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
}

function Test-ListeningPort([int]$Port) {
    try {
        return @(
            Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
        ).Count -gt 0
    } catch {
        return $false
    }
}

function Get-ListeningProcessIds([int]$Port) {
    try {
        return @(
            Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
                Select-Object -ExpandProperty OwningProcess -Unique
        )
    } catch {
        return @()
    }
}

function Stop-PortProcessTrees {
    foreach ($port in $ports) {
        foreach ($processId in @(Get-ListeningProcessIds $port)) {
            if (@($preexistingProcessIds[$port]) -contains [int]$processId) {
                continue
            }
            try {
                & taskkill.exe /PID $processId /T /F *> $null
            } catch {
                # Best-effort cleanup for a failed smoke run. A successful run
                # must not need this path; the launcher owns normal cleanup.
            }
        }
    }
}

$isolatedNames = @(
    "EXECUTION_MODE",
    "DATABENTO_ENABLED",
    "DATABENTO_API_KEY",
    "DISCORD_LIVE",
    "DISCORD_LIVE_ENABLED",
    "DISCORD_WEBHOOK_URL",
    "DISCORD_MNQ_WEBHOOK_URL",
    "DISCORD_mgc_WEBHOOK_URL",
    "DISCORD_JOURNAL_WEBHOOK_URL",
    "REPLIT_DEPLOYMENT",
    "MANUAL_ORDER_ENABLED",
    "LIVE_RUNNER_ENABLED",
    "CENTRAL_GHOST_COORDINATOR_FANOUT_ENABLED",
    "VISUAL_BRAIN_ENABLED",
    "VISUAL_BRAIN_BENCHMARK_ENABLED",
    "VISUAL_BRAIN_BENCHMARK_CANDIDATE_ENABLED",
    "TRAINING_MODE_ENABLED",
    "DATABASE_URL"
)
$previousEnvironment = @{}
foreach ($name in $isolatedNames) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}
$preexistingProcessIds = @{}
foreach ($port in $ports) {
    $preexistingProcessIds[$port] = @(Get-ListeningProcessIds $port)
}

$process = $null
$healthReached = $false
try {
    # This test deliberately does not load .env and clears every input that
    # could reach market data, persistence, execution, or Discord delivery.
    $safeEnvironment = @{
        "EXECUTION_MODE" = "disabled"
        "DATABENTO_ENABLED" = "0"
        "DATABENTO_API_KEY" = ""
        "DISCORD_LIVE" = "0"
        "DISCORD_LIVE_ENABLED" = "0"
        "DISCORD_WEBHOOK_URL" = ""
        "DISCORD_MNQ_WEBHOOK_URL" = ""
        "DISCORD_mgc_WEBHOOK_URL" = ""
        "DISCORD_JOURNAL_WEBHOOK_URL" = ""
        "REPLIT_DEPLOYMENT" = "0"
        "MANUAL_ORDER_ENABLED" = "0"
        "LIVE_RUNNER_ENABLED" = "0"
        "CENTRAL_GHOST_COORDINATOR_FANOUT_ENABLED" = "0"
        "VISUAL_BRAIN_ENABLED" = "0"
        "VISUAL_BRAIN_BENCHMARK_ENABLED" = "0"
        "VISUAL_BRAIN_BENCHMARK_CANDIDATE_ENABLED" = "0"
        "TRAINING_MODE_ENABLED" = "1"
        "DATABASE_URL" = ""
    }
    foreach ($entry in $safeEnvironment.GetEnumerator()) {
        Set-ProcessEnvironmentValue $entry.Key $entry.Value
    }

    Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
    $process = Start-Process `
        -FilePath (Join-Path $PSHOME "pwsh.exe") `
        -ArgumentList @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", $launcher,
            "-SkipEnvFile",
            "-NoBrowser",
            "-ExitAfterReady"
        ) `
        -WorkingDirectory $repoRoot `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru

    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    while (-not $process.HasExited -and (Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri $healthUri -Method Get -TimeoutSec 5
            if ($health.status -ne "ok") {
                throw "Unexpected /ping response status: $($health.status)"
            }
            $healthReached = $true
        } catch {
            # Flask starts before the proxy and dashboard. Keep polling while
            # the supported launcher completes the rest of its topology.
        }
        Start-Sleep -Seconds 1
    }

    if (-not $healthReached) {
        throw "The Windows dashboard launcher did not reach $healthUri within $StartupTimeoutSeconds seconds."
    }
    if (-not $process.HasExited) {
        throw "The Windows dashboard launcher did not complete its smoke run within $StartupTimeoutSeconds seconds."
    }
    if ($process.ExitCode -ne 0) {
        throw "The Windows dashboard launcher exited with code $($process.ExitCode)."
    }

    # ExitAfterReady returns through the launcher's finally block, which owns
    # taskkill /T cleanup for Flask, Express, and Vite. Give Windows a moment
    # to release the listeners before asserting the process tree is gone.
    Start-Sleep -Seconds 2
    $remainingPorts = @($ports | Where-Object { Test-ListeningPort $_ })
    if ($remainingPorts.Count -gt 0) {
        throw "Launcher exited but these owned ports are still listening: $($remainingPorts -join ', ')."
    }

    Write-Host "Windows dashboard smoke passed: /ping reached and launcher-owned process tree stopped cleanly."
} catch {
    if (Test-Path -LiteralPath $stdoutPath) {
        Write-Host "--- launcher stdout ---"
        Get-Content -LiteralPath $stdoutPath
    }
    if (Test-Path -LiteralPath $stderrPath) {
        Write-Host "--- launcher stderr ---"
        Get-Content -LiteralPath $stderrPath
    }
    throw
} finally {
    if ($null -ne $process -and -not $process.HasExited) {
        try {
            & taskkill.exe /PID $process.Id /T /F *> $null
        } catch {}
    }
    Stop-PortProcessTrees
    foreach ($name in $isolatedNames) {
        Set-ProcessEnvironmentValue $name $previousEnvironment[$name]
    }
}