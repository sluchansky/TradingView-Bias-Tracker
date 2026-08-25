[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8001,
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

[Environment]::SetEnvironmentVariable("ANALYSIS_ONLY", "1", "Process")
[Environment]::SetEnvironmentVariable("EXECUTION_MODE", "manual_only", "Process")
[Environment]::SetEnvironmentVariable("DISCORD_LIVE", "0", "Process")
[Environment]::SetEnvironmentVariable("PORT", "$Port", "Process")
Push-Location (Join-Path $repoRoot "artifacts\analysis-bot")
try {
    & $python "app.py"
}
finally {
    Pop-Location
}