[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$BundleRoot,

    [string]$RunRoot
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

$allowedRuntimeRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'out\.runtime'))
$BundleRoot = [IO.Path]::GetFullPath($BundleRoot)
if ([string]::IsNullOrWhiteSpace($RunRoot)) {
    $RunRoot = Join-Path $allowedRuntimeRoot 'package-smoke'
}
$RunRoot = Assert-SafeRecursiveRemovalTarget $allowedRuntimeRoot $RunRoot 'RunRoot'

$executable = Join-Path $BundleRoot 'EchoReader.exe'
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Standalone bundle executable was not found: $executable"
}

$startupTimeout = [TimeSpan]::FromSeconds(120)
$requestTimeout = [TimeSpan]::FromSeconds(10)
$shutdownTimeout = [TimeSpan]::FromSeconds(45)
$bridgePort = 19876

function Assert-Equal($Actual, $Expected, [string]$Label) {
    if ($Actual -ne $Expected) {
        throw "$Label mismatch"
    }
}

function Assert-IsolatedPath([string]$Actual, [string]$Expected, [string]$Label) {
    $actualFull = [IO.Path]::GetFullPath($Actual)
    $expectedFull = [IO.Path]::GetFullPath($Expected)
    if (-not $actualFull.Equals($expectedFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label escaped the isolated run root"
    }
}

function Assert-BridgePortAvailable {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $bridgePort)
    $listener.ExclusiveAddressUse = $true
    try {
        $listener.Start()
    }
    catch [Net.Sockets.SocketException] {
        throw "SessionBridge port 127.0.0.1:$bridgePort is occupied. Stop its owner before running the package smoke test; this script will not stop it."
    }
    finally {
        $listener.Stop()
    }
}

function Get-LoopbackBaseUri([string]$Value) {
    $uri = $null
    if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$uri) -or
        $uri.Scheme -ne 'http' -or
        $uri.IsDefaultPort -or
        -not [string]::IsNullOrEmpty($uri.UserInfo) -or
        $uri.AbsolutePath -ne '/' -or
        -not [string]::IsNullOrEmpty($uri.Query) -or
        -not [string]::IsNullOrEmpty($uri.Fragment)) {
        throw 'instance.json contains an invalid application URL'
    }
    $isLoopback = $uri.Host.Equals('localhost', [StringComparison]::OrdinalIgnoreCase)
    if (-not $isLoopback) {
        $address = $null
        $isLoopback = [Net.IPAddress]::TryParse($uri.Host, [ref]$address) -and
            [Net.IPAddress]::IsLoopback($address)
    }
    if (-not $isLoopback) {
        throw 'instance.json application URL is not loopback-only'
    }
    return $uri.AbsoluteUri.TrimEnd('/')
}

function Invoke-JsonRequest(
    [Net.Http.HttpClient]$Client,
    [string]$Method,
    [string]$Url,
    [int]$ExpectedStatus
) {
    $request = [Net.Http.HttpRequestMessage]::new(
        [Net.Http.HttpMethod]::new($Method),
        $Url
    )
    try {
        if ($Method -eq 'POST') {
            $request.Content = [Net.Http.StringContent]::new('', [Text.Encoding]::UTF8, 'application/json')
        }
        $response = $Client.Send($request)
        try {
            if ([int]$response.StatusCode -ne $ExpectedStatus) {
                throw "$Method request to $Url returned HTTP $([int]$response.StatusCode); expected $ExpectedStatus"
            }
            $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            try {
                return $body | ConvertFrom-Json
            }
            catch {
                throw "$Method request to $Url did not return valid JSON"
            }
        }
        finally {
            $response.Dispose()
        }
    }
    finally {
        $request.Dispose()
    }
}

function Start-IsolatedHost([hashtable]$Roots) {
    $environment = @{
        ECHO_READER_LIBRARY_ROOT = $Roots.library
        ECHO_READER_STATE_ROOT = $Roots.state
        ECHO_READER_CACHE_ROOT = $Roots.cache
        ECHO_READER_LOGS_ROOT = $Roots.logs
        ECHO_READER_TMP_ROOT = $Roots.tmp
        ECHO_READER_CONFIG_ROOT = $Roots.config
    }
    $saved = @{}
    foreach ($name in $environment.Keys) {
        $item = Get-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        $saved[$name] = if ($null -eq $item) { $null } else { $item.Value }
        Set-Item -LiteralPath "Env:$name" -Value $environment[$name]
    }
    try {
        return Start-Process -FilePath $executable -ArgumentList '--no-browser' `
            -WorkingDirectory $BundleRoot -PassThru
    }
    finally {
        foreach ($name in $saved.Keys) {
            if ($null -eq $saved[$name]) {
                Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
            }
            else {
                Set-Item -LiteralPath "Env:$name" -Value $saved[$name]
            }
        }
    }
}

function Wait-ForInstance([Diagnostics.Process]$Process, [string]$InstanceFile) {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed -lt $startupTimeout) {
        $Process.Refresh()
        if ($Process.HasExited) {
            throw "EchoReader.exe exited during startup with code $($Process.ExitCode)"
        }
        if (Test-Path -LiteralPath $InstanceFile -PathType Leaf) {
            try {
                $record = Get-Content -LiteralPath $InstanceFile -Raw -Encoding utf8 | ConvertFrom-Json
                if ($record.schema_version -eq 1 -and
                    $record.state -eq 'ready' -and
                    $record.pid -is [ValueType] -and
                    [int64]$record.pid -eq $Process.Id -and
                    $record.boot_id -is [string] -and
                    $record.boot_id.Length -ge 16) {
                    $baseUrl = Get-LoopbackBaseUri $record.url
                    $launchUri = $null
                    if (-not [Uri]::TryCreate([string]$record.launch_url, [UriKind]::Absolute, [ref]$launchUri) -or
                        -not $launchUri.GetLeftPart([UriPartial]::Authority).Equals(
                            $baseUrl, [StringComparison]::OrdinalIgnoreCase
                        ) -or
                        $launchUri.AbsolutePath -notmatch '^/session/[A-Za-z0-9_-]{32,}$' -or
                        -not [string]::IsNullOrEmpty($launchUri.Query) -or
                        -not [string]::IsNullOrEmpty($launchUri.Fragment)) {
                        throw 'instance.json contains an invalid session launch URL'
                    }
                    return [pscustomobject]@{
                        Process = $Process
                        BaseUrl = $baseUrl
                        LaunchUrl = $launchUri.AbsoluteUri
                        BootId = $record.boot_id
                    }
                }
            }
            catch [System.Management.Automation.RuntimeException] {
                # The host replaces instance.json atomically, but antivirus and indexing
                # can briefly delay a complete read. Retry until the hard deadline.
            }
        }
        Start-Sleep -Milliseconds 100
    }
    throw "EchoReader.exe did not publish a valid instance.json within $($startupTimeout.TotalSeconds) seconds"
}

function Assert-RunningApi($Instance, [hashtable]$Roots, [Net.Http.HttpClient]$Client) {
    $health = Invoke-JsonRequest $Client 'GET' "$($Instance.BaseUrl)/health" 200
    Assert-Equal $health.status 'ok' 'health status'
    Assert-Equal $health.boot_id $Instance.BootId 'health boot_id'

    $anonymousHandler = [Net.Http.HttpClientHandler]::new()
    $anonymousHandler.UseProxy = $false
    $anonymousClient = [Net.Http.HttpClient]::new($anonymousHandler)
    try {
        $anonymous = $anonymousClient.GetAsync(
            "$($Instance.BaseUrl)/api/v1/items"
        ).GetAwaiter().GetResult()
        try {
            if ([int]$anonymous.StatusCode -ne 401) {
                throw 'Unauthenticated public API request was not rejected'
            }
        }
        finally {
            $anonymous.Dispose()
        }
    }
    finally {
        $anonymousClient.Dispose()
        $anonymousHandler.Dispose()
    }

    $bootstrap = $Client.GetAsync($Instance.LaunchUrl).GetAwaiter().GetResult()
    try {
        if (-not $bootstrap.IsSuccessStatusCode -or
            $bootstrap.RequestMessage.RequestUri.AbsolutePath -ne '/') {
            throw 'Session bootstrap did not set the browser session and redirect to /'
        }
    }
    finally {
        $bootstrap.Dispose()
    }

    $config = Invoke-JsonRequest $Client 'GET' "$($Instance.BaseUrl)/api/v1/app-config" 200
    Assert-IsolatedPath $config.library_root $Roots.library 'library_root'
    Assert-IsolatedPath $config.state_root $Roots.state 'state_root'
    Assert-IsolatedPath $config.cache_root $Roots.cache 'cache_root'
    Assert-IsolatedPath $config.logs_root $Roots.logs 'logs_root'
    Assert-IsolatedPath $config.tmp_root $Roots.tmp 'tmp_root'
    Assert-IsolatedPath $config.config_root $Roots.config 'config_root'
    Assert-Equal $config.install_mode $true 'install_mode'

    $items = Invoke-JsonRequest $Client 'GET' "$($Instance.BaseUrl)/api/v1/items" 200
    if ($null -eq $items.items -or @($items.items).Count -ne 1 -or $items.items[0].id -ne 'package-smoke-item') {
        throw 'items response did not contain the isolated durable smoke item'
    }
    return ($items | ConvertTo-Json -Depth 20 -Compress)
}

function Stop-IsolatedHost($Instance, [Net.Http.HttpClient]$Client) {
    $response = Invoke-JsonRequest $Client 'POST' "$($Instance.BaseUrl)/api/v1/app/shutdown" 202
    Assert-Equal $response.status 'shutting_down' 'shutdown status'
    if (-not $Instance.Process.WaitForExit([int]$shutdownTimeout.TotalMilliseconds)) {
        throw "EchoReader.exe PID $($Instance.Process.Id) did not exit within $($shutdownTimeout.TotalSeconds) seconds"
    }
}

function Stop-OwnedProcessTree([Diagnostics.Process]$Process) {
    if ($null -eq $Process) {
        return
    }
    $Process.Refresh()
    if (-not $Process.HasExited) {
        & taskkill.exe /PID $Process.Id /T /F 2>$null | Out-Null
        [void]$Process.WaitForExit(10000)
    }
}

function Wait-ForPidExit([int]$ProcessId, [TimeSpan]$Timeout, [string]$Label) {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed -lt $Timeout) {
        if ($null -eq (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
            return
        }
        Start-Sleep -Milliseconds 100
    }
    throw "$Label PID $ProcessId did not exit within $($Timeout.TotalSeconds) seconds"
}

Assert-BridgePortAvailable

$workerSelfTest = Start-Process -FilePath $executable -ArgumentList '--self-test-worker' `
    -WorkingDirectory $BundleRoot -WindowStyle Hidden -Wait -PassThru
if ($workerSelfTest.ExitCode -ne 0) {
    throw "Frozen worker self-test failed with exit code $($workerSelfTest.ExitCode)"
}

if (Test-Path -LiteralPath $RunRoot) {
    $safeRunRoot = Assert-SafeRecursiveRemovalTarget $allowedRuntimeRoot $RunRoot 'RunRoot'
    Remove-Item -LiteralPath $safeRunRoot -Recurse -Force
}
$roots = @{
    library = Join-Path $RunRoot 'library'
    state = Join-Path $RunRoot 'state'
    cache = Join-Path $RunRoot 'cache'
    logs = Join-Path $RunRoot 'logs'
    tmp = Join-Path $RunRoot 'tmp'
    config = Join-Path $RunRoot 'config'
}
New-Item -ItemType Directory -Path @($roots.Values) -Force | Out-Null

$markerValue = 'echo-reader-package-smoke-v1'
foreach ($entry in $roots.GetEnumerator()) {
    Set-Content -LiteralPath (Join-Path $entry.Value 'package-smoke.marker') `
        -Value "$markerValue/$($entry.Key)" -Encoding utf8NoBOM -NoNewline
}
$itemRoot = Join-Path $roots.library 'package-smoke-item'
New-Item -ItemType Directory -Path $itemRoot -Force | Out-Null
@{
    schema_version = 1
    id = 'package-smoke-item'
    created_at = '2026-01-01T00:00:00Z'
    updated_at = '2026-01-01T00:00:00Z'
    source = @{
        type = 'article'
        url = 'https://example.invalid/package-smoke'
        title = 'Standalone package smoke item'
        author = 'Echo Reader smoke test'
        published_at = '2026-01-01T00:00:00Z'
    }
    article = @{ path = 'article.md'; content_source = 'content.json' }
    transcript = @{ path = 'transcript.json'; language = 'und' }
    primary_media = $null
    media_items = @()
} | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $itemRoot 'manifest.json') -Encoding utf8NoBOM

$handler = [Net.Http.HttpClientHandler]::new()
$handler.UseProxy = $false
$client = [Net.Http.HttpClient]::new($handler)
$client.Timeout = $requestTimeout
$activeProcess = $null
$orphanReaderPid = $null
try {
    $instanceFile = Join-Path $roots.state 'instance.json'

    $activeProcess = Start-IsolatedHost $roots
    $first = Wait-ForInstance $activeProcess $instanceFile
    $firstItems = Assert-RunningApi $first $roots $client
    Stop-IsolatedHost $first $client
    $activeProcess = $null

    $database = Join-Path $roots.state 'imports.db'
    if (-not (Test-Path -LiteralPath $database -PathType Leaf) -or (Get-Item -LiteralPath $database).Length -le 0) {
        throw 'The durable imports database was not created in the isolated state root'
    }
    foreach ($entry in $roots.GetEnumerator()) {
        $marker = Get-Content -LiteralPath (Join-Path $entry.Value 'package-smoke.marker') -Raw -Encoding utf8
        Assert-Equal $marker "$markerValue/$($entry.Key)" "$($entry.Key) marker"
    }

    Assert-BridgePortAvailable
    $activeProcess = Start-IsolatedHost $roots
    $second = Wait-ForInstance $activeProcess $instanceFile
    if ($second.BootId -eq $first.BootId) {
        throw 'Restart reused the previous boot_id'
    }
    $secondItems = Assert-RunningApi $second $roots $client
    Assert-Equal $secondItems $firstItems 'durable items response after restart'
    foreach ($entry in $roots.GetEnumerator()) {
        $marker = Get-Content -LiteralPath (Join-Path $entry.Value 'package-smoke.marker') -Raw -Encoding utf8
        Assert-Equal $marker "$markerValue/$($entry.Key)" "$($entry.Key) marker after restart"
    }
    Stop-IsolatedHost $second $client
    $activeProcess = $null

    Assert-BridgePortAvailable
    $activeProcess = Start-IsolatedHost $roots
    $third = Wait-ForInstance $activeProcess $instanceFile
    [void](Assert-RunningApi $third $roots $client)
    $readerChildren = @(
        Get-CimInstance Win32_Process -Filter "ParentProcessId=$($activeProcess.Id)" |
            Where-Object Name -eq 'echo-backend.exe'
    )
    if ($readerChildren.Count -ne 1) {
        throw 'The desktop host did not own exactly one echo-backend.exe child'
    }
    $orphanReaderPid = [int]$readerChildren[0].ProcessId
    Stop-Process -Id $activeProcess.Id -Force
    [void]$activeProcess.WaitForExit(10000)
    $activeProcess = $null
    Wait-ForPidExit $orphanReaderPid ([TimeSpan]::FromSeconds(15)) 'Reader parent-death watchdog'
    $orphanReaderPid = $null
    Assert-BridgePortAvailable

    $activeProcess = Start-IsolatedHost $roots
    $fourth = Wait-ForInstance $activeProcess $instanceFile
    if ($fourth.BootId -eq $third.BootId) {
        throw 'Restart after host termination reused the previous boot_id'
    }
    [void](Assert-RunningApi $fourth $roots $client)
    Stop-IsolatedHost $fourth $client
    $activeProcess = $null

    Write-Output "Standalone package smoke test passed: $BundleRoot"
    Write-Output "Isolated runtime retained at: $RunRoot"
}
finally {
    Stop-OwnedProcessTree $activeProcess
    if ($null -ne $orphanReaderPid) {
        Stop-Process -Id $orphanReaderPid -Force -ErrorAction SilentlyContinue
    }
    $client.Dispose()
    $handler.Dispose()
}
