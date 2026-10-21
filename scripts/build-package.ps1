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

# GPL-3.0 text-to-speech dependencies (eSpeak NG via espeakng-loader, phonemizer-fork) and the
# kokoro-onnx package that pulls them in are optional, user-installed extras. They must never be
# shipped in the bundle, installer or any sidecar.
# PyInstaller excludes them explicitly below and the assembled bundle is checked afterwards.

function Assert-NoGplTtsDependency([string]$Root, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        throw "$Label does not exist: $Root"
    }
    $found = @(Get-ChildItem -LiteralPath $Root -Recurse -Force | Where-Object {
        $_.Name -imatch '(espeak|phonemizer|kokoro_onnx|kokoro-onnx)'
    })
    if ($found.Count -gt 0) {
        $relative = $found | ForEach-Object {
            [IO.Path]::GetRelativePath($Root, $_.FullName).Replace('\','/')
        }
        throw "$Label contains optional GPL-3.0 TTS dependencies that must not be distributed: $($relative -join ', ')"
    }
}

# Returns the files next to ffmpeg that contain FFTW (GPL-2.0-or-later) code, found by the
# 'fftw_' symbol prefix or the 'fftw-3.x' version string. BtbN's "lgpl" builds link FFTW
# statically through chromaprint (-DFFT_LIB=fftw3), so --enable-gpl alone does not catch it.
# Scans the ffmpeg executable, ffprobe next to it and every DLL in the same folder, in chunks.
function Find-FftwInFfmpeg([string]$FfmpegPath) {
    $folder = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($FfmpegPath))
    $candidates = @(Get-Item -LiteralPath $FfmpegPath)
    $candidates += @(Get-ChildItem -LiteralPath $folder -File | Where-Object {
        $_.Name -imatch '^ffprobe(\.exe)?$' -or $_.Extension -ieq '.dll'
    })
    $markers = @('fftw_', 'fftw-3')
    $latin1 = [Text.Encoding]::GetEncoding(28591)
    $chunkSize = 4MB
    $hits = @()
    foreach ($file in ($candidates | Sort-Object FullName -Unique)) {
        $found = @()
        $tail = ''
        $buffer = New-Object byte[] $chunkSize
        $stream = [IO.File]::OpenRead($file.FullName)
        try {
            while (($read = $stream.Read($buffer, 0, $chunkSize)) -gt 0) {
                # Keep the end of the previous chunk so a marker split across chunks is still found.
                $text = $tail + $latin1.GetString($buffer, 0, $read)
                foreach ($marker in $markers) {
                    if ($found -notcontains $marker -and $text.IndexOf($marker, [StringComparison]::Ordinal) -ge 0) {
                        $found += $marker
                    }
                }
                if ($found.Count -eq $markers.Count) { break }
                $tail = $text.Substring([Math]::Max(0, $text.Length - 8))
            }
        } finally {
            $stream.Dispose()
        }
        if ($found.Count -gt 0) { $hits += "$($file.Name) ($($found -join ', '))" }
    }
    return $hits
}

# FFmpeg must be an LGPL build: no --enable-gpl / --enable-nonfree, no libx264/libx265 and no
# FFTW. The BtbN "lgpl" build pinned in mise.toml fails the FFTW check (chromaprint); a custom
# build without chromaprint is planned, see docs/HANDOFF.md. The Windows build is the shared
# variant so the FFmpeg libraries ship as replaceable DLLs next to ffmpeg.exe.
function Assert-LgplFfmpeg([string]$FfmpegPath) {
    $versionText = (& $FfmpegPath -hide_banner -version) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw "ffmpeg -version failed for $FfmpegPath" }
    foreach ($flag in @('--enable-gpl', '--enable-nonfree', '--enable-libx264', '--enable-libx265')) {
        if ($versionText.Contains($flag)) { throw "Bundled FFmpeg must be an LGPL build, but it was configured with $flag" }
    }
    $encoders = (& $FfmpegPath -hide_banner -encoders) -join "`n"
    if ($encoders -match '\blibx26[45]\b') { throw 'Bundled FFmpeg exposes GPL-only H.264/H.265 encoders' }
    if ($encoders -notmatch '\blibopenh264\b') { throw 'Bundled FFmpeg lacks libopenh264, which playback transcoding needs' }
    $fftwHits = @(Find-FftwInFfmpeg $FfmpegPath)
    if ($fftwHits.Count -gt 0) {
        throw ("Bundled FFmpeg contains FFTW, which is GPL-2.0-or-later, so it is not an LGPL build: " +
            "$($fftwHits -join '; '). BtbN 'lgpl' builds link FFTW through chromaprint; use a custom " +
            "build configured without chromaprint (--disable-chromaprint) before shipping the bundle.")
    }
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
        '--exclude-module', 'kokoro_onnx',
        '--exclude-module', 'phonemizer',
        '--exclude-module', 'espeakng_loader',
        (Join-Path $projectRoot 'installer\desktop_entry.py')
    )
    Invoke-Checked 'Python desktop host freeze' { & $uvPath @pyinstallerArguments }

    Copy-Item -Path (Join-Path $frozenRoot 'EchoReader\*') -Destination $bundleRoot -Recurse
    Copy-Item -LiteralPath (Join-Path $cargoRoot 'release\echo-backend.exe') -Destination (Join-Path $bundleRoot 'echo-backend.exe')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'components\echo\frontend\dist') -Destination (Join-Path $bundleRoot 'web') -Recurse
    Copy-Item -LiteralPath (Join-Path $projectRoot 'components\medialab\browser-extension') -Destination (Join-Path $bundleRoot 'browser-extension') -Recurse
    New-Item -ItemType Directory -Path (Join-Path $bundleRoot 'tools') -Force | Out-Null
    Assert-LgplFfmpeg $ffmpegPath
    Copy-Item -LiteralPath $ffmpegPath,$ffprobePath,$denoPath -Destination (Join-Path $bundleRoot 'tools')
    # LGPL shared build: ship the FFmpeg libraries as separate, user-replaceable DLLs.
    $ffmpegLibraries = Get-ChildItem -LiteralPath (Split-Path $ffmpegPath -Parent) -File -Filter '*.dll' | Where-Object { $_.Name -match '^(avcodec|avdevice|avfilter|avformat|avutil|swresample|swscale)-\d+\.dll$' }
    if (@($ffmpegLibraries).Count -lt 7) { throw 'Expected the seven FFmpeg LGPL shared libraries next to ffmpeg.exe' }
    Copy-Item -LiteralPath $ffmpegLibraries.FullName -Destination (Join-Path $bundleRoot 'tools')
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
    Copy-Item -LiteralPath (Join-Path $projectRoot 'installer\licenses\FFMPEG_SOURCE.txt') -Destination $licensesRoot
    $sitePackages = Join-Path $projectRoot '.venv\Lib\site-packages'
    $pyinstallerLicense = Get-ChildItem -LiteralPath $sitePackages -Directory -Filter 'pyinstaller-*.dist-info' | ForEach-Object { Join-Path $_.FullName 'licenses\COPYING.txt' } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    $hooksLicense = Get-ChildItem -LiteralPath $sitePackages -Directory -Filter 'pyinstaller_hooks_contrib-*.dist-info' | ForEach-Object { Join-Path $_.FullName 'licenses\LICENSE' } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $pyinstallerLicense -or -not $hooksLicense) { throw 'PyInstaller license files were not found' }
    Copy-Item -LiteralPath $pyinstallerLicense -Destination (Join-Path $licensesRoot 'PYINSTALLER_COPYING.txt')
    Copy-Item -LiteralPath $hooksLicense -Destination (Join-Path $licensesRoot 'PYINSTALLER_HOOKS_LICENSE.txt')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'installer\README-INSTALLED.md') -Destination (Join-Path $bundleRoot 'README.md')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'README.md') -Destination (Join-Path $bundleRoot 'DEVELOPMENT.md')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'THIRD_PARTY_NOTICES.md') -Destination $bundleRoot
    Copy-Item -LiteralPath (Join-Path $projectRoot 'LICENSE') -Destination (Join-Path $bundleRoot 'LICENSE.txt')

    Assert-NoCredentialResidue $bundleRoot 'Assembled bundle'
    Assert-NoGplTtsDependency $bundleRoot 'Assembled bundle'

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
