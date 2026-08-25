[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [switch]$SkipEnvFile
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Missing .venv. Follow WINDOWS_HOSTING.md to create the Python environment first."
}

if (-not $SkipEnvFile) {
    . (Join-Path $PSScriptRoot "Import-LocalEnv.ps1")
    Import-LocalEnv -Path (Join-Path $repoRoot ".env")
}

$safeDefaults = @{
    "EXECUTION_MODE" = "disabled"
    "DATABENTO_ENABLED" = "0"
    "DISCORD_LIVE" = "0"
    "TRAINING_MODE_ENABLED" = "1"
    "MANUAL_ORDER_ENABLED" = "0"
    "LIVE_RUNNER_ENABLED" = "0"
}
foreach ($entry in $safeDefaults.GetEnumerator()) {
    if (-not [Environment]::GetEnvironmentVariable($entry.Key, "Process")) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
    }
}

foreach ($entry in $safeDefaults.GetEnumerator()) {
    if ([Environment]::GetEnvironmentVariable($entry.Key, "Process") -ne $entry.Value) {
        throw "Refusing to start: $($entry.Key) must be $($entry.Value) for this safe launcher."
    }
}

[Environment]::SetEnvironmentVariable("PORT", "$Port", "Process")
Push-Location (Join-Path $repoRoot "artifacts\tradingview-webhook")
try {
    & $python "app.py"
}
finally {
    Pop-Location
}