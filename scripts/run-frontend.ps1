[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

Push-Location (Join-Path $projectRoot 'components\echo\frontend')
try {
    & mise exec -c 'bun run dev'
    if ($LASTEXITCODE -ne 0) { throw "Echo frontend failed with exit code $LASTEXITCODE" }
}
finally {
    Pop-Location
}
