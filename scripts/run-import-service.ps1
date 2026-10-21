[CmdletBinding()]
param(
    [string]$HostAddress = '127.0.0.1',
    [ValidateRange(1, 65535)]
    [int]$Port = 3100,
    [string]$LibraryRoot,
    [string]$RuntimeRoot,
    [string]$EchoUrl = 'http://127.0.0.1:3000'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($LibraryRoot)) {
    $LibraryRoot = Join-Path $projectRoot 'content'
}
if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
    $RuntimeRoot = Join-Path $projectRoot '.runtime'
}

$LibraryRoot = [IO.Path]::GetFullPath($LibraryRoot)
$RuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot)
New-Item -ItemType Directory -Path $LibraryRoot -Force | Out-Null
New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null

Push-Location $projectRoot
try {
    # Quote the separator so PowerShell passes it through to mise.
    & mise exec '--' uv run --extra local-asr echo-reader-service `
        --host $HostAddress `
        --port $Port `
        --library-root $LibraryRoot `
        --runtime-root $RuntimeRoot `
        --echo-url $EchoUrl
    if ($LASTEXITCODE -ne 0) {
        throw "Echo Reader import service failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
