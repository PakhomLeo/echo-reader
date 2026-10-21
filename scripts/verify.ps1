[CmdletBinding()]
param(
    [switch]$E2E
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

Push-Location $projectRoot
try {
    & mise exec -c 'uv run --extra local-asr python -m pytest tests components\medialab\tests -q'
    if ($LASTEXITCODE -ne 0) { throw "Python tests failed with exit code $LASTEXITCODE" }

    Push-Location (Join-Path $projectRoot 'components\echo\backend')
    try {
        & mise exec -c 'cargo fmt --check'
        if ($LASTEXITCODE -ne 0) { throw "cargo fmt failed with exit code $LASTEXITCODE" }
        & mise exec -c 'cargo clippy --all-targets -- -D warnings'
        if ($LASTEXITCODE -ne 0) { throw "cargo clippy failed with exit code $LASTEXITCODE" }
        & mise exec -c 'cargo test'
        if ($LASTEXITCODE -ne 0) { throw "cargo test failed with exit code $LASTEXITCODE" }
        if ($E2E) {
            & mise exec -c 'cargo build'
            if ($LASTEXITCODE -ne 0) { throw "cargo build failed with exit code $LASTEXITCODE" }
        }
    }
    finally {
        Pop-Location
    }

    Push-Location (Join-Path $projectRoot 'components\echo\frontend')
    try {
        & mise exec -c 'bun run typecheck'
        if ($LASTEXITCODE -ne 0) { throw "frontend typecheck failed with exit code $LASTEXITCODE" }
        & mise exec -c 'bun run lint'
        if ($LASTEXITCODE -ne 0) { throw "frontend lint failed with exit code $LASTEXITCODE" }
        & mise exec -c 'bun run test'
        if ($LASTEXITCODE -ne 0) { throw "frontend tests failed with exit code $LASTEXITCODE" }
        & mise exec -c 'bun run build'
        if ($LASTEXITCODE -ne 0) { throw "frontend build failed with exit code $LASTEXITCODE" }
        if ($E2E) {
            & mise exec -c 'bun run test:e2e'
            if ($LASTEXITCODE -ne 0) { throw "Playwright tests failed with exit code $LASTEXITCODE" }
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    Pop-Location
}
