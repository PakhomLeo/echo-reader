[CmdletBinding()]
param(
    [string]$LibraryRoot,
    [string]$RuntimeRoot,
    [ValidateRange(1, 65535)]
    [int]$ImportPort = 3100
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

function Get-EnvironmentValue([string]$Name) {
    $item = Get-Item -LiteralPath "Env:$Name" -ErrorAction SilentlyContinue
    if ($null -eq $item) { return $null }
    return $item.Value
}

function Restore-EnvironmentValue([string]$Name, $Value) {
    if ($null -eq $Value) {
        Remove-Item -LiteralPath "Env:$Name" -ErrorAction SilentlyContinue
    }
    else {
        Set-Item -LiteralPath "Env:$Name" -Value $Value
    }
}

$previousLibraryRoot = Get-EnvironmentValue 'LIBRARY_ROOT'
$previousImportApiUrl = Get-EnvironmentValue 'IMPORT_API_URL'
$importProcess = $null

$misePath = (Get-Command mise.exe -CommandType Application -ErrorAction Stop).Path
function Quote-ProcessArgument([string]$Value) {
    if ($Value.Contains('"')) { throw 'Paths and URLs containing a quote are not supported.' }
    return '"' + $Value + '"'
}

try {
    $env:LIBRARY_ROOT = $LibraryRoot
    $env:IMPORT_API_URL = "http://127.0.0.1:$ImportPort"
    $serviceArguments = @(
        'exec', '--', 'uv', 'run', '--extra', 'local-asr', 'echo-reader-service',
        '--host', '127.0.0.1',
        '--port', [string]$ImportPort,
        '--library-root', (Quote-ProcessArgument $LibraryRoot),
        '--runtime-root', (Quote-ProcessArgument $RuntimeRoot),
        '--echo-url', 'http://127.0.0.1:3000'
    )
    $importProcess = Start-Process `
        -FilePath $misePath `
        -ArgumentList $serviceArguments `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -PassThru

    $healthUrl = "http://127.0.0.1:$ImportPort/api/v1/health"
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($importProcess.HasExited) {
            throw "Import service exited during startup with code $($importProcess.ExitCode)"
        }
        try {
            $health = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 1
            if ($health.status -eq 'ok') {
                $ready = $true
                break
            }
        }
        catch {
            Start-Sleep -Milliseconds 250
        }
    }
    if (-not $ready) {
        throw "Import service did not become healthy at $healthUrl"
    }

    Push-Location (Join-Path $projectRoot 'components\echo\frontend')
    try {
        & mise exec -c 'bun run build'
        if ($LASTEXITCODE -ne 0) { throw "Echo frontend build failed with exit code $LASTEXITCODE" }
    }
    finally {
        Pop-Location
    }

    Push-Location (Join-Path $projectRoot 'components\echo\backend')
    try {
        & mise exec -c 'cargo run --release'
        if ($LASTEXITCODE -ne 0) { throw "Echo backend failed with exit code $LASTEXITCODE" }
    }
    finally {
        Pop-Location
    }
}
finally {
    if ($null -ne $importProcess -and -not $importProcess.HasExited) {
        # Kill only the exact process tree created above; never search by process name.
        $importProcess.Kill($true)
        $importProcess.WaitForExit()
    }
    if ($null -ne $importProcess) {
        $importProcess.Dispose()
    }
    Restore-EnvironmentValue 'LIBRARY_ROOT' $previousLibraryRoot
    Restore-EnvironmentValue 'IMPORT_API_URL' $previousImportApiUrl
}
