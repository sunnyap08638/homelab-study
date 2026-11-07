param(
    [Parameter(Mandatory)][ipaddress]$ListenAddress,
    [Parameter(Mandatory)][ipaddress]$UbuntuAddress,
    [ValidateRange(60, 300)][int]$IdleSeconds = 180,
    [switch]$EnableShutdown
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http

function Test-DesktopSession {
    return [bool]((Get-CimInstance Win32_ComputerSystem).UserName -or
        (Get-Process explorer -ErrorAction SilentlyContinue))
}

function Send-SessionState($response, [string]$state, [int]$seconds = 0, [bool]$canShutdown = $false) {
    $body = [Text.Encoding]::UTF8.GetBytes((@{
        state = $state; seconds = $seconds; canShutdown = $canShutdown
    } | ConvertTo-Json -Compress))
    $response.ContentType = 'application/json'
    $response.ContentLength64 = $body.Length
    $response.OutputStream.Write($body, 0, $body.Length)
}

function Stop-Worker {
    if (Test-DesktopSession) { return $false }
    & shutdown.exe /s /t 0 /d p:0:0 /c 'HomeLab worker shutdown' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Windows rejected the worker shutdown.' }
    return $true
}

function Invoke-Ollama($context) {
    $request = $context.Request
    $response = $context.Response
    $path = $request.Url.AbsolutePath
    if ($request.RemoteEndPoint.Address -ne $UbuntuAddress) {
        $response.StatusCode = 403
        return
    }
    if (($request.HttpMethod -eq 'GET' -and $path -in @('/api/tags', '/api/ps')) -or
        ($request.HttpMethod -eq 'POST' -and $path -eq '/api/chat')) {
        $upstream = [System.Net.Http.HttpRequestMessage]::new(
            [System.Net.Http.HttpMethod]::new($request.HttpMethod), "http://127.0.0.1:11435$path")
    } else {
        $response.StatusCode = 404
        return
    }
    try {
        if ($path -eq '/api/chat') {
            if ($request.ContentLength64 -le 0 -or $request.ContentLength64 -gt 3MB) {
                $response.StatusCode = 413
                return
            }
            $upstream.Content = [System.Net.Http.StreamContent]::new($request.InputStream)
            $upstream.Content.Headers.ContentLength = $request.ContentLength64
            $upstream.Content.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::new('application/json')
        }
        $reply = $client.SendAsync($upstream).GetAwaiter().GetResult()
        try {
            $body = $reply.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
            $response.StatusCode = [int]$reply.StatusCode
            $response.ContentType = 'application/json'
            $response.ContentLength64 = $body.Length
            $response.OutputStream.Write($body, 0, $body.Length)
        } finally {
            $reply.Dispose()
        }
    } finally {
        $upstream.Dispose()
    }
}

$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.UseProxy = $false
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds(180)
$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://${ListenAddress}:11434/")
$listener.IgnoreWriteExceptions = $true
$claimUntil = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.AddMinutes(3)
$canClaimBoot = $EnableShutdown -and (Get-Date) -lt $claimUntil -and !(Test-DesktopSession)
$shutdownAt = $null
$sessionMinutes = 30
$manualSession = $false
$listener.Start()
Write-Output "Gateway listening on $ListenAddress for $UbuntuAddress. Shutdown enabled: $EnableShutdown. Idle seconds: $IdleSeconds."

try {
    $nextRequest = $listener.GetContextAsync()
    while ($true) {
        if ($canClaimBoot -and ((!$shutdownAt -and (Get-Date) -ge $claimUntil) -or (Test-DesktopSession))) {
            $canClaimBoot = $false
            $shutdownAt = $null
        }
        if (!$nextRequest.Wait(2000)) {
            if ($shutdownAt -and (Get-Date) -ge $shutdownAt -and !$nextRequest.IsCompleted) {
                if (!(Test-DesktopSession)) {
                    Write-Output 'Worker idle after Wake-on-LAN. Shutting down Windows.'
                    Stop-Worker | Out-Null
                }
                $shutdownAt = $null
                $canClaimBoot = $false
            }
            continue
        }
        $context = $nextRequest.GetAwaiter().GetResult()
        $request = $context.Request
        $fromUbuntu = $request.RemoteEndPoint.Address -eq $UbuntuAddress
        $isChat = $request.HttpMethod -eq 'POST' -and $request.Url.AbsolutePath -eq '/api/chat'
        $path = $request.Url.AbsolutePath
        $powerOff = $false
        if ($canClaimBoot -and !$shutdownAt -and $fromUbuntu -and
            $request.HttpMethod -eq 'GET' -and $request.Url.AbsolutePath -eq '/api/tags' -and
            $request.Headers['X-Mneme-Wake'] -eq '1') {
            Write-Output 'Accepted wake claim from Mneme.'
            $shutdownAt = (Get-Date).AddSeconds($IdleSeconds)
        }
        try {
            if (!$path.StartsWith('/mneme/session', [StringComparison]::Ordinal)) {
                Invoke-Ollama $context
            } elseif (!$fromUbuntu) {
                $context.Response.StatusCode = 403
            } elseif ($request.HttpMethod -eq 'GET' -and $path -eq '/mneme/session') {
                $canShutdown = $EnableShutdown -and !(Test-DesktopSession)
                if (!$shutdownAt) {
                    Send-SessionState $context.Response 'unmanaged' 0 $canShutdown
                } else {
                    $state = if ($manualSession) { 'active' } else { 'idle' }
                    $seconds = [Math]::Max(0, [Math]::Ceiling(($shutdownAt - (Get-Date)).TotalSeconds))
                    Send-SessionState $context.Response $state $seconds $canShutdown
                }
            } elseif ($request.HttpMethod -eq 'POST' -and $path -eq '/mneme/session/shutdown') {
                if ($EnableShutdown -and (Stop-Worker)) {
                    $shutdownAt = $null
                    $canClaimBoot = $false
                    Send-SessionState $context.Response 'stopping'
                } else {
                    $context.Response.StatusCode = 409
                }
            } elseif ($request.HttpMethod -eq 'POST' -and $path -in @('/mneme/session/start', '/mneme/session/end')) {
                if (!$shutdownAt -or (Test-DesktopSession)) {
                    $context.Response.StatusCode = 409
                } elseif ($path -eq '/mneme/session/start') {
                    $manualSession = $true
                    $shutdownAt = (Get-Date).AddMinutes($sessionMinutes)
                    Send-SessionState $context.Response 'active' ($sessionMinutes * 60)
                } else {
                    Send-SessionState $context.Response 'stopping'
                    $powerOff = $true
                }
            } else {
                $context.Response.StatusCode = 404
            }
        } catch {
            $context.Response.StatusCode = 503
            Write-Warning "Ollama request failed: $($_.Exception.GetBaseException().Message)"
        } finally {
            $context.Response.Close()
        }
        if ($powerOff) {
            Write-Output 'Mneme ended the generation session. Shutting down Windows.'
            Stop-Worker | Out-Null
            $shutdownAt = $null
            $canClaimBoot = $false
        }
        if ($shutdownAt -and $fromUbuntu -and $isChat) {
            $shutdownAt = if ($manualSession) { (Get-Date).AddMinutes($sessionMinutes) } else { (Get-Date).AddSeconds($IdleSeconds) }
        }
        $nextRequest = $listener.GetContextAsync()
    }
} finally {
    $listener.Close()
    $client.Dispose()
}
