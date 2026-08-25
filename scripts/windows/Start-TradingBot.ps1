[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [switch]$EnableDatabento,
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
}
foreach ($entry in $safeDefaults.GetEnumerator()) {
    if (-not [Environment]::GetEnvironmentVariable($entry.Key, "Process")) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
    }
}

# Market data is opt-in even for a local dashboard. Requiring both the
# explicit switch and a configured provider key prevents an accidental .env
# edit from turning the feed on. This never changes the execution safeguards.
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

[Environment]::SetEnvironmentVariable("PORT", "$Port", "Process")
Push-Location (Join-Path $repoRoot "artifacts\tradingview-webhook")
try {
    & $python "app.py"
}
finally {
    Pop-Location
}