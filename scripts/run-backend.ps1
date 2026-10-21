[CmdletBinding()]
param(
    [string]$LibraryRoot,
    [ValidateRange(1, 65535)]
    [int]$Port = 3000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($LibraryRoot)) {
    $LibraryRoot = Join-Path $projectRoot 'content'
}

$LibraryRoot = [IO.Path]::GetFullPath($LibraryRoot)
New-Item -ItemType Directory -Path $LibraryRoot -Force | Out-Null
$env:LIBRARY_ROOT = $LibraryRoot
$env:ECHO_BIND = "127.0.0.1:$Port"

Push-Location (Join-Path $projectRoot 'components\echo\backend')
try {
    & mise exec -c 'cargo run'
    if ($LASTEXITCODE -ne 0) { throw "Echo backend failed with exit code $LASTEXITCODE" }
}
finally {
    Pop-Location
}
