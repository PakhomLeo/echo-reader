[CmdletBinding()]
param(
    [string]$Version = '0.1.0',
    [string]$OutputRoot,
    [string]$IsccPath,
    [switch]$SkipInstaller
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

function Assert-SafeRecursiveRemovalTarget(
    [string]$AllowedRoot,
    [string]$Target,
    [string]$Label
) {
    $allowedFull = [IO.Path]::GetFullPath($AllowedRoot).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $targetFull = [IO.Path]::GetFullPath($Target)
    $relative = [IO.Path]::GetRelativePath($allowedFull, $targetFull)
    if ([IO.Path]::IsPathRooted($relative) -or
        $relative -eq '.' -or
        $relative -eq '..' -or
        $relative.StartsWith("..$([IO.Path]::DirectorySeparatorChar)", [StringComparison]::Ordinal) -or
        $relative.StartsWith("..$([IO.Path]::AltDirectorySeparatorChar)", [StringComparison]::Ordinal)) {
        throw "$Label must be a child of $allowedFull"
    }

    $current = $allowedFull
    $item = Get-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
    if ($null -ne $item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "$Label crosses a reparse point and cannot be removed recursively: $current"
    }
    $segments = $relative -split '[\\/]'
    foreach ($segment in $segments) {
        $current = Join-Path $current $segment
        $item = Get-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
        if ($null -ne $item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "$Label crosses a reparse point and cannot be removed recursively: $current"
        }
    }
    return $targetFull
}

function Assert-NoCredentialResidue([string]$Root, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        throw "$Label does not exist: $Root"
    }
    $matches = @(Get-ChildItem -LiteralPath $Root -Recurse -File | Where-Object {
        $_.Name -ieq 'secrets.toml' -or
        $_.Name -imatch '^.*cookies.*\.txt$' -or
        $_.Name -imatch '^.*\.cookies$' -or
        (($_.Name -ieq '.env' -or $_.Name -ilike '.env.*') -and
            $_.Name -ine '.env.example') -or
        $_.Extension -imatch '^\.(key|p12|pfx)$' -or
        ($_.Extension -ieq '.pem' -and (Select-String -LiteralPath $_.FullName `
            -Pattern '-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----' -Quiet))
    })
    if ($matches.Count -gt 0) {
        $relative = $matches | ForEach-Object {
            [IO.Path]::GetRelativePath($Root, $_.FullName).Replace('\','/')
        }
        throw "$Label contains credential-bearing files that must not be packaged: $($relative -join ', ')"
    }
}

if ($Version -notmatch '^\d+\.\d+\.\d+([-.][0-9A-Za-z.-]+)?$') {
    throw "Version must be a semantic version, got: $Version"
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $projectRoot "out\package\$Version"
}
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
$allowedOutputRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'out\package'))
$OutputRoot = Assert-SafeRecursiveRemovalTarget $allowedOutputRoot $OutputRoot 'Package output'
$medialabPackageRoot = Join-Path $projectRoot 'components\medialab\src\medialab'
Assert-NoCredentialResidue $medialabPackageRoot 'MediaLab package source'
if (Test-Path -LiteralPath $OutputRoot) {
    $safeOutputRoot = Assert-SafeRecursiveRemovalTarget $allowedOutputRoot $OutputRoot 'Package output'
    Remove-Item -LiteralPath $safeOutputRoot -Recurse -Force
}

$artifactRoot = Join-Path $OutputRoot 'artifacts'
$bundleRoot = Join-Path $OutputRoot 'bundle'
$frozenRoot = Join-Path $OutputRoot 'pyinstaller-dist'
$workRoot = Join-Path $OutputRoot 'pyinstaller-work'
$specRoot = Join-Path $OutputRoot 'spec'
$cargoRoot = Join-Path $OutputRoot 'cargo-target'
New-Item -ItemType Directory -Path $artifactRoot,$bundleRoot,$frozenRoot,$workRoot,$specRoot,$cargoRoot -Force | Out-Null

function Invoke-Checked([string]$Label, [scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

$uvPath = (& mise which uv).Trim()
$bunPath = (& mise which bun).Trim()
$cargoPath = (& mise which cargo).Trim()
$ffmpegPath = (& mise which ffmpeg).Trim()
$ffprobePath = (& mise which ffprobe).Trim()
$denoPath = (& mise which deno).Trim()

Push-Location $projectRoot
try {
    Invoke-Checked 'uv sync' { & $uvPath sync --frozen --all-packages --extra local-asr --group package }

    Push-Location (Join-Path $projectRoot 'components\echo\frontend')
    try {
        Invoke-Checked 'frontend install' { & $bunPath install --frozen-lockfile }
        Invoke-Checked 'frontend build' { & $bunPath run build }
    }
    finally {
        Pop-Location
    }

    Push-Location (Join-Path $projectRoot 'components\echo\backend')
    try {
        $env:CARGO_TARGET_DIR = $cargoRoot
        Invoke-Checked 'Echo release build' { & $cargoPath build --release --locked }
    }
    finally {
        Remove-Item Env:CARGO_TARGET_DIR -ErrorAction SilentlyContinue
        Pop-Location
    }

    $pyinstallerArguments = @(
        'run', '--group', 'package', '--extra', 'local-asr', 'pyinstaller',
        '--noconfirm', '--clean', '--onedir', '--windowed', '--name', 'EchoReader',
        '--distpath', $frozenRoot, '--workpath', $workRoot,
        '--specpath', $specRoot,
        '--paths', (Join-Path $projectRoot 'src'),
        '--paths', (Join-Path $projectRoot 'components\medialab\src'),
        '--collect-all', 'medialab',
        '--collect-all', 'yt_dlp_plugins',
        '--collect-all', 'yt_dlp',
        '--collect-all', 'yt_dlp_ejs',
        '--collect-all', 'curl_cffi',
        '--collect-all', 'huggingface_hub',
        '--collect-all', 'oss2',
        '--collect-all', 'playwright',
        '--collect-all', 'openvino',
        '--collect-all', 'openvino_genai',
        '--collect-all', 'sherpa_onnx',
        '--collect-all', 'websocket_server',
        '--collect-all', 'websocket',
        (Join-Path $projectRoot 'installer\desktop_entry.py')
    )
    Invoke-Checked 'Python desktop host freeze' { & $uvPath @pyinstallerArguments }

    Copy-Item -Path (Join-Path $frozenRoot 'EchoReader\*') -Destination $bundleRoot -Recurse
    Copy-Item -LiteralPath (Join-Path $cargoRoot 'release\echo-backend.exe') -Destination (Join-Path $bundleRoot 'echo-backend.exe')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'components\echo\frontend\dist') -Destination (Join-Path $bundleRoot 'web') -Recurse
    Copy-Item -LiteralPath (Join-Path $projectRoot 'components\medialab\browser-extension') -Destination (Join-Path $bundleRoot 'browser-extension') -Recurse
    New-Item -ItemType Directory -Path (Join-Path $bundleRoot 'tools') -Force | Out-Null
    Copy-Item -LiteralPath $ffmpegPath,$ffprobePath,$denoPath -Destination (Join-Path $bundleRoot 'tools')
    $templatesRoot = Join-Path $bundleRoot 'config-templates'
    New-Item -ItemType Directory -Path $templatesRoot -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $projectRoot 'components\medialab\src\medialab\config\secrets.toml.example') -Destination $templatesRoot
    $licensesRoot = Join-Path $bundleRoot 'licenses'
    New-Item -ItemType Directory -Path $licensesRoot -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $projectRoot 'components\echo\THIRD_PARTY_LICENSES.md') -Destination (Join-Path $licensesRoot 'ECHO_DEPENDENCIES.md')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'installer\licenses\DENO_LICENSE.txt') -Destination $licensesRoot
    $ffmpegLicense = Get-ChildItem -LiteralPath (Split-Path (Split-Path $ffmpegPath -Parent) -Parent) -Recurse -File -Filter 'LICENSE.txt' | Select-Object -First 1
    if ($null -eq $ffmpegLicense) { throw 'Bundled FFmpeg license was not found' }
    Copy-Item -LiteralPath $ffmpegLicense.FullName -Destination (Join-Path $licensesRoot 'FFMPEG_LICENSE.txt')
    $sitePackages = Join-Path $projectRoot '.venv\Lib\site-packages'
    $pyinstallerLicense = Get-ChildItem -LiteralPath $sitePackages -Directory -Filter 'pyinstaller-*.dist-info' | ForEach-Object { Join-Path $_.FullName 'licenses\COPYING.txt' } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    $hooksLicense = Get-ChildItem -LiteralPath $sitePackages -Directory -Filter 'pyinstaller_hooks_contrib-*.dist-info' | ForEach-Object { Join-Path $_.FullName 'licenses\LICENSE' } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $pyinstallerLicense -or -not $hooksLicense) { throw 'PyInstaller license files were not found' }
    Copy-Item -LiteralPath $pyinstallerLicense -Destination (Join-Path $licensesRoot 'PYINSTALLER_COPYING.txt')
    Copy-Item -LiteralPath $hooksLicense -Destination (Join-Path $licensesRoot 'PYINSTALLER_HOOKS_LICENSE.txt')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'installer\README-INSTALLED.md') -Destination (Join-Path $bundleRoot 'README.md')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'README.md') -Destination (Join-Path $bundleRoot 'DEVELOPMENT.md')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'THIRD_PARTY_NOTICES.md') -Destination $bundleRoot

    Assert-NoCredentialResidue $bundleRoot 'Assembled bundle'

    $manifest = Get-ChildItem -LiteralPath $bundleRoot -File -Recurse | Sort-Object FullName | ForEach-Object {
        [pscustomobject]@{
            path = [IO.Path]::GetRelativePath($bundleRoot, $_.FullName).Replace('\','/')
            size = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    [pscustomobject]@{
        schema_version = 1
        version = $Version
        platform = 'windows-x86_64'
        files = @($manifest)
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $bundleRoot 'bundle-manifest.json') -Encoding utf8NoBOM

    & (Join-Path $projectRoot 'scripts\test-package.ps1') -BundleRoot $bundleRoot

    $zipPath = Join-Path $artifactRoot "EchoReader-$Version-win-x64-standalone.zip"
    Compress-Archive -Path (Join-Path $bundleRoot '*') -DestinationPath $zipPath -CompressionLevel Optimal

    if (-not $SkipInstaller) {
        if ([string]::IsNullOrWhiteSpace($IsccPath)) {
            $candidates = @(
                (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 7\ISCC.exe'),
                (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
                (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 7\ISCC.exe'),
                (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
            )
            $IsccPath = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        }
        if ([string]::IsNullOrWhiteSpace($IsccPath) -or -not (Test-Path -LiteralPath $IsccPath)) {
            throw 'Inno Setup compiler was not found. Install JRSoftware.InnoSetup or pass -IsccPath; use -SkipInstaller only for bundle diagnostics.'
        }
        Invoke-Checked 'Inno Setup' {
            & $IsccPath "/DAppVersion=$Version" "/DBundleDir=$bundleRoot" "/DOutputDir=$artifactRoot" (Join-Path $projectRoot 'installer\EchoReader.iss')
        }
    }

    Write-Output "Bundle: $bundleRoot"
    Write-Output "Artifacts: $artifactRoot"
}
finally {
    Pop-Location
}
