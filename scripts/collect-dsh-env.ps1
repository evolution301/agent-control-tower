# Collect the three values tower needs to drive live DSH sessions:
#   base_url  - the desktop harness URL on this machine
#   secret    - the base64 HMAC secret the harness bridge uses (may be empty)
#   sessions  - every session whose cwd matches -Workspace, with its title
#
# Run it on the machine that hosts the DSH sessions and paste the printed JSON
# back (or send the written file). Nothing is uploaded anywhere.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\collect-dsh-env.ps1 -Workspace C:\path\to\project
#
# Exit codes: 0 ok, 1 no DSH home, 2 no sessions found for the workspace.

[CmdletBinding()]
param(
    [string]$Workspace = (Get-Location).Path,
    [string]$Out = (Join-Path ([Environment]::GetFolderPath('Desktop')) 'tower-env.json'),
    # Optional: write base_url + secret straight into this tower.toml.
    [string]$ApplyTo = ''
)

$ErrorActionPreference = 'Stop'

$dshHome = Join-Path $env:USERPROFILE '.dsh'
if (-not (Test-Path $dshHome)) {
    Write-Error "no DSH home at $dshHome - is DeepSeek Harness installed for this user?"
    exit 1
}

function Get-BridgeSecret {
    param([string]$Root)
    if ($env:DSH_BRIDGE_SECRET) { return $env:DSH_BRIDGE_SECRET }
    $candidates = @(
        (Join-Path $Root '_dsh-extract\notify_lead.py'),
        (Join-Path $Root '_dsh-extract\bridge.py'),
        (Join-Path $Root '.agents\bridge.py'),
        (Join-Path $env:USERPROFILE '.dsh\bridge.py')
    )
    foreach ($path in $candidates) {
        if (-not (Test-Path $path)) { continue }
        $match = Select-String -Path $path -Pattern 'SECRET_B64\s*=\s*"([^"]+)"' -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($match) { return $match.Matches[0].Groups[1].Value }
    }
    return ''
}

function New-AuthCookie {
    param([string]$Authority, [string]$SecretB64)
    if (-not $SecretB64) { return $null }
    $padded = $SecretB64.Replace('-', '+').Replace('_', '/')
    switch ($padded.Length % 4) {
        2 { $padded += '==' }
        3 { $padded += '=' }
    }
    $secret = [Convert]::FromBase64String($padded)
    $issued = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    $payload = '{"version":1,"authority":"' + $Authority + '","issuedAt":' + $issued + ',"expiresAt":' + ($issued + 2592000000) + '}'
    $body = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($payload)).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    $hmac = [System.Security.Cryptography.HMACSHA256]::new($secret)
    $signatureBytes = $hmac.ComputeHash([Text.Encoding]::ASCII.GetBytes($body))
    $signature = [Convert]::ToBase64String($signatureBytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $nameHash = [Convert]::ToBase64String($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Authority))).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    return "dsh-auth-$nameHash=v1.$body.$signature"
}

function Test-HarnessPort {
    param([int]$Port, [string]$SecretB64)
    $authority = "127.0.0.1:$Port"
    $headers = @{ 'Content-Type' = 'application/json'; 'Accept' = 'application/json'; 'Origin' = "http://$authority" }
    $cookie = New-AuthCookie -Authority $authority -SecretB64 $SecretB64
    if ($cookie) { $headers['Cookie'] = $cookie }
    $envelope = '{"type":"client-request","rpcId":"' + [guid]::NewGuid() + '","method":"session/list","payload":{"args":{"request":{}}}}'
    try {
        $response = Invoke-WebRequest -Uri "http://$authority/api/session/list" -Method POST -Body $envelope `
            -Headers $headers -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        return @{ ok = $true; status = [int]$response.StatusCode }
    } catch {
        $status = 0
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        # 401/403 still identifies the harness: it answered the RPC surface.
        return @{ ok = ($status -eq 401 -or $status -eq 403); status = $status }
    }
}

function Get-DshBaseUrl {
    param([string]$SecretB64)
    $pids = @(Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
    if ($pids.Count -eq 0) { return $null }
    $ports = @(
        Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $pids -contains $_.OwningProcess } |
            Select-Object -ExpandProperty LocalPort -Unique |
            Sort-Object
    )
    $fallback = $null
    foreach ($port in $ports) {
        $probe = Test-HarnessPort -Port $port -SecretB64 $SecretB64
        if ($probe.ok -and $probe.status -ge 200 -and $probe.status -lt 300) {
            return "http://127.0.0.1:$port"
        }
        if ($probe.ok -and -not $fallback) { $fallback = "http://127.0.0.1:$port" }
    }
    return $fallback
}

function Get-SessionIndex {
    $cache = Join-Path $dshHome 'storages\session_projcache\sessions'
    $rows = @()
    if (-not (Test-Path $cache)) { return $rows }
    foreach ($file in Get-ChildItem $cache -Filter '*.json' -ErrorAction SilentlyContinue) {
        try {
            $doc = Get-Content $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        } catch {
            continue
        }
        $record = $doc.record
        if (-not $record -or -not $record.identity) { continue }
        $stem = $file.BaseName
        $sessionId = if ($stem -match '^session-(.+)$') { $stem } else { "session-$stem" }
        $cwd = $record.identity.cwd
        $title = $null
        if ($record.rows -and $record.rows.title) { $title = $record.rows.title.val }
        if (-not $cwd) { continue }
        $cleanTitle = ''
        if ($title) { $cleanTitle = ($title -replace '^\s*#+\s*', '' -replace '\s+', ' ').Trim() }
        $rows += [pscustomobject]@{
            session_id = $sessionId
            cwd        = $cwd
            title      = $cleanTitle
        }
    }
    return $rows
}

$secret = Get-BridgeSecret -Root $Workspace
$baseUrl = Get-DshBaseUrl -SecretB64 $secret
$sessions = Get-SessionIndex
$normalizedWorkspace = [System.IO.Path]::GetFullPath($Workspace).TrimEnd('\')

$matched = @($sessions | Where-Object { $_.cwd.TrimEnd('\') -eq $normalizedWorkspace })
if ($matched.Count -eq 0) {
    $matched = @($sessions | Where-Object { $_.cwd -like "*$normalizedWorkspace*" })
}
if ($matched.Count -eq 0) {
    Write-Warning "no sessions found for $normalizedWorkspace; listing every session instead"
    $matched = @($sessions)
}

$payload = [pscustomobject]@{
    generated_at  = (Get-Date).ToString('s')
    workspace     = $normalizedWorkspace
    base_url      = $baseUrl
    secret        = $secret
    session_count = $matched.Count
    sessions      = @($matched | Sort-Object title | ForEach-Object {
        [pscustomobject]@{ title = $_.title; session_id = $_.session_id }
    })
}

if ($ApplyTo) {
    if (-not (Test-Path $ApplyTo)) {
        Write-Warning "ApplyTo target not found: $ApplyTo"
    } else {
        $text = Get-Content $ApplyTo -Raw -Encoding UTF8
        if ($baseUrl) {
            $text = [regex]::Replace($text, '(?m)^(\s*base_url:\s*)"[^"]*"', '${1}"' + $baseUrl + '"')
        }
        if ($secret) {
            $text = [regex]::Replace($text, '(?m)^(\s*secret:\s*)"[^"]*"', '${1}"' + $secret + '"')
        }
        Set-Content -Path $ApplyTo -Value $text -Encoding UTF8 -NoNewline
        Write-Host "applied base_url + secret to $ApplyTo"
    }
}

$json = $payload | ConvertTo-Json -Depth 6
$json | Set-Content -Path $Out -Encoding UTF8
Write-Host "written: $Out"
Write-Host ''
Write-Host $json
if ($matched.Count -eq 0) { exit 2 }
exit 0
