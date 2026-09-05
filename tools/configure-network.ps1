<#
.SYNOPSIS
    Detects this machine's LAN address and the receiver's, and writes both to .env.

.DESCRIPTION
    Addresses are configuration, never code. This script derives them and writes
    them to .env, which is not tracked — so the repository keeps the logic and the
    house keeps its addresses.

    Two values are produced:

      DENON_HOST     found by SSDP, so it survives DHCP handing the receiver a
                     new address; re-run this after a lease change.
      LAN_BASE_URL   the address a device on the LAN can fetch media from. The
                     receiver pulls audio itself, so localhost is useless to it.
                     Chosen as the local interface on the same subnet as the
                     receiver, which is the one that can actually route to it.

    Prints masked values. The real ones go only to .env.

.EXAMPLE
    .\tools\configure-network.ps1

.EXAMPLE
    .\tools\configure-network.ps1 -Show      # print the real values too
#>
[CmdletBinding()]
param(
    [int]$Port = 8080,
    [int]$DiscoveryMs = 4000,
    [switch]$Show
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Hide-Address([string]$ip) {
    if (-not $ip) { return '(none)' }
    $p = $ip.Split('.')
    if ($p.Count -ne 4) { return '(masked)' }
    return "$($p[0]).$($p[1]).x.x"
}

function Get-LocalIPv4Address {
    Get-WmiObject -Class Win32_NetworkAdapterConfiguration -Filter 'IPEnabled = True' |
        ForEach-Object { $_.IPAddress } |
        Where-Object { $_ -and $_ -match '^\d+\.\d+\.\d+\.\d+$' -and $_ -ne '127.0.0.1' } |
        Select-Object -Unique
}

# ── Find the receiver ───────────────────────────────────────────────────────

Write-Host "Discovering the receiver over SSDP..." -ForegroundColor Cyan

$denon = $null
foreach ($localIp in (Get-LocalIPv4Address)) {
    $request = @(
        'M-SEARCH * HTTP/1.1',
        'HOST: 239.255.255.250:1900',
        'MAN: "ssdp:discover"',
        'MX: 3',
        'ST: urn:schemas-denon-com:device:ACT-Denon:1',
        '', ''
    ) -join "`r`n"

    $udp = $null
    try {
        $local = New-Object System.Net.IPEndPoint ([System.Net.IPAddress]::Parse($localIp)), 0
        $udp = New-Object System.Net.Sockets.UdpClient $local
        $udp.Client.ReceiveTimeout = 800
        $target = New-Object System.Net.IPEndPoint ([System.Net.IPAddress]::Parse('239.255.255.250')), 1900
        $bytes = [System.Text.Encoding]::ASCII.GetBytes($request)

        [void]$udp.Send($bytes, $bytes.Length, $target)
        Start-Sleep -Milliseconds 150
        [void]$udp.Send($bytes, $bytes.Length, $target)

        $deadline = (Get-Date).AddMilliseconds($DiscoveryMs)
        while ((Get-Date) -lt $deadline -and -not $denon) {
            try {
                $remote = New-Object System.Net.IPEndPoint ([System.Net.IPAddress]::Any), 0
                $data = $udp.Receive([ref]$remote)
                $text = [System.Text.Encoding]::ASCII.GetString($data)
                if ($text -match '(?i)denon|marantz|heos|ACT-Denon') {
                    $denon = $remote.Address.ToString()
                }
            } catch [System.Net.Sockets.SocketException] { }
        }
    } catch {
        Write-Verbose "SSDP from ${localIp}: $($_.Exception.Message)"
    } finally {
        if ($udp) { $udp.Close() }
    }
    if ($denon) { break }
}

if (-not $denon) {
    Write-Host "  no receiver found." -ForegroundColor Yellow
    Write-Host "  It may be on another VLAN, or blocked by the Windows firewall." -ForegroundColor Yellow
    Write-Host "  DENON_HOST is left as it is; set it by hand in .env if you know it." -ForegroundColor Yellow
} else {
    Write-Host "  found receiver at $(Hide-Address $denon)" -ForegroundColor Green
}

# ── Pick the interface that can actually reach it ────────────────────────────

$locals = @(Get-LocalIPv4Address)
$chosen = $null

if ($denon) {
    $prefix = ($denon.Split('.')[0..2]) -join '.'
    # Same /24 as the receiver: the interface that can route to it, rather than a
    # Docker or VPN adapter that happens to be listed first.
    $chosen = $locals | Where-Object { $_.StartsWith("$prefix.") } | Select-Object -First 1
}
if (-not $chosen) {
    # Fall back to whichever interface carries the default route.
    $best = Get-WmiObject Win32_IP4RouteTable |
        Where-Object { $_.Destination -eq '0.0.0.0' } |
        Sort-Object Metric1 | Select-Object -First 1
    if ($best) {
        $chosen = $locals | Where-Object { $_ -eq $best.InterfaceIndex } | Select-Object -First 1
    }
    if (-not $chosen) { $chosen = $locals | Select-Object -First 1 }
}

if (-not $chosen) { throw "Could not determine this machine's LAN address." }
Write-Host "  this machine is $(Hide-Address $chosen)" -ForegroundColor Green

# ── Write to .env ───────────────────────────────────────────────────────────

if (-not (Test-Path .env)) { throw ".env not found. Copy .env.example first." }

$lanBase = "http://${chosen}:$Port"
$lines = Get-Content .env

function Set-EnvValue([string[]]$content, [string]$key, [string]$value) {
    if ($content -match "^$key=") {
        return $content | ForEach-Object { if ($_ -match "^$key=") { "$key=$value" } else { $_ } }
    }
    return $content + "$key=$value"
}

if ($denon) { $lines = Set-EnvValue $lines 'DENON_HOST' $denon }
$lines = Set-EnvValue $lines 'LAN_BASE_URL' $lanBase
Set-Content .env -Value $lines -Encoding utf8

Write-Host ""
Write-Host "Wrote to .env (untracked):" -ForegroundColor Cyan
if ($denon) { Write-Host "  DENON_HOST   = $(Hide-Address $denon)" }
Write-Host "  LAN_BASE_URL = http://$(Hide-Address $chosen):$Port"
if ($Show) {
    Write-Host ""
    Write-Host "Actual values:" -ForegroundColor Yellow
    if ($denon) { Write-Host "  DENON_HOST   = $denon" }
    Write-Host "  LAN_BASE_URL = $lanBase"
}
Write-Host ""
Write-Host "Restart the stack to pick them up:  docker compose up -d api" -ForegroundColor Cyan
