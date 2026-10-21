[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

Push-Location $projectRoot
try {
    & mise install
    if ($LASTEXITCODE -ne 0) { throw "mise install failed with exit code $LASTEXITCODE" }

    & mise exec -c 'uv sync --all-packages --extra local-asr'
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed with exit code $LASTEXITCODE" }

    Push-Location (Join-Path $projectRoot 'components\echo\frontend')
    try {
        & mise exec -c 'bun install --frozen-lockfile'
        if ($LASTEXITCODE -ne 0) { throw "bun install failed with exit code $LASTEXITCODE" }
    }
    finally {
        Pop-Location
    }

    New-Item -ItemType Directory -Path (Join-Path $projectRoot 'library'),(Join-Path $projectRoot '.runtime'),(Join-Path $projectRoot 'config') -Force | Out-Null
}
finally {
    Pop-Location
}
