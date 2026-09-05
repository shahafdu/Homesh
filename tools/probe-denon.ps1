<#
.SYNOPSIS
    Discovers Denon/Marantz AVRs on the LAN and probes their HEOS players/zones.

.DESCRIPTION
    Answers the open question in docs/ARCHITECTURE.md section 5.6: does the AVR-X1600H expose
    ZONE2 as a separate HEOS player (allowing two independent network streams), or a single
    shared network player?

    Finds the receiver by SSDP rather than by IP address, so it keeps working when DHCP hands
    the receiver a different address. This mirrors how the shipped agent will locate devices:
    stable identity (USN/UDN) is the key, IP is only a cache. See ARCHITECTURE.md 5.7.

    Talks to two distinct protocols on the same device:
      * HEOS CLI          - TCP 1255, ASCII commands, JSON responses
      * Denon AVR control - TCP 23,   ASCII commands, CR-terminated responses

    Read-only. Sends only status queries; changes nothing on the receiver.

.PARAMETER AvrIp
    Skip discovery and probe this address directly. Optional - discovery runs by default.

.PARAMETER DiscoverOnly
    Run SSDP discovery and stop, without probing.

.EXAMPLE
    .\probe-denon.ps1
    Discovers the receiver automatically, then probes it.

.EXAMPLE
    .\probe-denon.ps1 -AvrIp 192.0.2.42 -OutFile denon-probe.txt
#>
[CmdletBinding()]
param(
    [string]$AvrIp,
    [switch]$DiscoverOnly,

    [int]$HeosPort = 1255,
    [int]$AvrPort  = 23,

    [int]$ConnectTimeoutMs = 5000,
    [int]$ReadWindowMs     = 3000,
    [int]$DiscoveryMs      = 4000,

    [string]$OutFile
)

$ErrorActionPreference = 'Stop'
$transcript = New-Object System.Collections.Generic.List[string]

function Write-Line {
    param([string]$Text, [string]$Color = 'Gray')
    Write-Host $Text -ForegroundColor $Color
    $script:transcript.Add($Text)
}

function Get-LocalIPv4Address {
    # Send the multicast probe from every real interface. On a machine with Wi-Fi, Ethernet and
    # Docker's virtual adapters, letting Windows pick the source interface usually picks wrong.
    Get-WmiObject -Class Win32_NetworkAdapterConfiguration -Filter 'IPEnabled = True' |
        ForEach-Object { $_.IPAddress } |
        Where-Object { $_ -and $_ -match '^\d+\.\d+\.\d+\.\d+$' -and $_ -ne '127.0.0.1' } |
        Select-Object -Unique
}

function Find-DenonDevice {
    <#
        Multicasts an SSDP M-SEARCH and returns discovered Denon devices with their
        stable identity (USN) alongside their current IP.
    #>
    param([int]$TimeoutMs)

    $searchTargets = @(
        'urn:schemas-denon-com:device:ACT-Denon:1',  # HEOS / Denon-specific
        'upnp:rootdevice'                            # fallback: catch anything Denon-ish
    )

    $found = @{}

    foreach ($localIp in (Get-LocalIPv4Address)) {
        foreach ($st in $searchTargets) {

            $request = @(
                'M-SEARCH * HTTP/1.1',
                'HOST: 239.255.255.250:1900',
                'MAN: "ssdp:discover"',
                'MX: 3',
                "ST: $st",
                '', ''
            ) -join "`r`n"

            $udp = $null
            try {
                $localEndpoint = New-Object System.Net.IPEndPoint ([System.Net.IPAddress]::Parse($localIp)), 0
                $udp = New-Object System.Net.Sockets.UdpClient $localEndpoint
                $udp.Client.ReceiveTimeout = 800

                $target  = New-Object System.Net.IPEndPoint ([System.Net.IPAddress]::Parse('239.255.255.250')), 1900
                $payload = [System.Text.Encoding]::ASCII.GetBytes($request)

                # UDP is lossy; a couple of sends materially improves the hit rate.
                [void]$udp.Send($payload, $payload.Length, $target)
                Start-Sleep -Milliseconds 150
                [void]$udp.Send($payload, $payload.Length, $target)

                $deadline = (Get-Date).AddMilliseconds($TimeoutMs)
                while ((Get-Date) -lt $deadline) {
                    try {
                        $remote = New-Object System.Net.IPEndPoint ([System.Net.IPAddress]::Any), 0
                        $data   = $udp.Receive([ref]$remote)
                        $text   = [System.Text.Encoding]::ASCII.GetString($data)

                        $server   = ([regex]::Match($text, '(?im)^SERVER:\s*(.+)$')).Groups[1].Value.Trim()
                        $usn      = ([regex]::Match($text, '(?im)^USN:\s*(.+)$')).Groups[1].Value.Trim()
                        $location = ([regex]::Match($text, '(?im)^LOCATION:\s*(.+)$')).Groups[1].Value.Trim()

                        $isDenon = ($text -match '(?i)denon|marantz|heos|ACT-Denon')
                        if (-not $isDenon) { continue }

                        $ip = $remote.Address.ToString()
                        if (-not $found.ContainsKey($ip)) {
                            $found[$ip] = [pscustomobject]@{
                                IP       = $ip
                                USN      = $usn
                                Server   = $server
                                Location = $location
                            }
                        }
                    }
                    catch [System.Net.Sockets.SocketException] {
                        # Receive timeout with nothing pending; keep waiting until the deadline.
                    }
                }
            }
            catch {
                Write-Verbose "SSDP on $localIp / $st failed: $($_.Exception.Message)"
            }
            finally {
                if ($udp) { $udp.Close() }
            }
        }
    }

    return $found.Values
}

function Invoke-TcpProbe {
    <#
        Opens a TCP connection, sends each command, and drains responses for
        ReadWindowMs after the last write. Returns raw response text.
    #>
    param(
        [string]$Ip,
        [int]$Port,
        [string[]]$Commands,
        [string]$Terminator,
        [int]$ConnectTimeoutMs,
        [int]$ReadWindowMs
    )

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($Ip, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($ConnectTimeoutMs)) {
            throw "Connection to ${Ip}:${Port} timed out after ${ConnectTimeoutMs}ms."
        }
        $client.EndConnect($async)

        $stream = $client.GetStream()
        $stream.ReadTimeout = 500

        $encoding = [System.Text.Encoding]::ASCII
        foreach ($cmd in $Commands) {
            $payload = $encoding.GetBytes($cmd + $Terminator)
            $stream.Write($payload, 0, $payload.Length)
            $stream.Flush()
            # Denon's port-23 protocol drops commands sent back to back.
            Start-Sleep -Milliseconds 300
        }

        $buffer   = New-Object byte[] 8192
        $response = New-Object System.Text.StringBuilder
        $deadline = (Get-Date).AddMilliseconds($ReadWindowMs)

        while ((Get-Date) -lt $deadline) {
            try {
                $read = $stream.Read($buffer, 0, $buffer.Length)
                if ($read -gt 0) { [void]$response.Append($encoding.GetString($buffer, 0, $read)) }
                else { break }   # remote closed
            }
            catch [System.IO.IOException] {
                # Read timeout, nothing pending; keep waiting.
            }
        }

        return $response.ToString()
    }
    finally {
        $client.Close()
    }
}

Write-Line ""
Write-Line "Denon probe" 'Cyan'
Write-Line ("=" * 62) 'Cyan'
Write-Line ("Run at {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Write-Line ""

# ---------------------------------------------------------------------------
# 0. Discovery
# ---------------------------------------------------------------------------
if (-not $AvrIp) {
    Write-Line "[0] SSDP discovery (no IP supplied)" 'Yellow'
    Write-Line ("-" * 62)
    Write-Line "  Searching 239.255.255.250:1900 ..."

    $devices = @(Find-DenonDevice -TimeoutMs $DiscoveryMs)

    if ($devices.Count -eq 0) {
        Write-Line "  Nothing found." 'Red'
        Write-Line "  The receiver may be in standby, on another VLAN, or blocked by the" 'Yellow'
        Write-Line "  Windows firewall. Re-run with -AvrIp 192.0.2.42 to probe directly." 'Yellow'
        Write-Line ""
        if (-not $DiscoverOnly) { return }
    }
    else {
        foreach ($d in $devices) {
            Write-Line "  Found: $($d.IP)" 'Green'
            if ($d.Server)   { Write-Line "    SERVER:   $($d.Server)" }
            if ($d.USN)      { Write-Line "    USN:      $($d.USN)" }
            if ($d.Location) { Write-Line "    LOCATION: $($d.Location)" }
        }
        Write-Line ""
        Write-Line "  => USN is the stable identity. We key on that and treat IP as a cache," 'Green'
        Write-Line "     so a DHCP address change costs nothing." 'Green'
        $AvrIp = $devices[0].IP
    }
    Write-Line ""
}

if ($DiscoverOnly) {
    Write-Line "Discovery complete (-DiscoverOnly)." 'Cyan'
    if ($OutFile) { $transcript -join "`r`n" | Out-File -FilePath $OutFile -Encoding utf8 }
    return
}

Write-Line "Probing $AvrIp" 'Cyan'
Write-Line ""

# ---------------------------------------------------------------------------
# 1. HEOS CLI (port 1255) - the question that matters
# ---------------------------------------------------------------------------
Write-Line "[1] HEOS CLI on port $HeosPort" 'Yellow'
Write-Line ("-" * 62)

$heosCommands = @(
    'heos://system/heart_beat',
    # The key one: does ZONE2 come back as its own player, with its own pid?
    'heos://player/get_players',
    'heos://group/get_groups'
)

try {
    $heos = Invoke-TcpProbe -Ip $AvrIp -Port $HeosPort -Commands $heosCommands `
                            -Terminator "`r`n" -ConnectTimeoutMs $ConnectTimeoutMs `
                            -ReadWindowMs $ReadWindowMs

    if ([string]::IsNullOrWhiteSpace($heos)) {
        Write-Line "  No response. HEOS may be asleep - try playing something first." 'Red'
    }
    else {
        foreach ($line in ($heos -split "`r?`n" | Where-Object { $_.Trim() })) {
            Write-Line "  $line"
        }

        $pids = [regex]::Matches($heos, '"pid"\s*:\s*(-?\d+)') |
                ForEach-Object { $_.Groups[1].Value } |
                Select-Object -Unique

        Write-Line ""
        if ($pids.Count -gt 1) {
            Write-Line "  => $($pids.Count) HEOS players found: $($pids -join ', ')" 'Green'
            Write-Line "     If one is ZONE2, independent per-zone network streaming works." 'Green'
        }
        elseif ($pids.Count -eq 1) {
            Write-Line "  => Exactly 1 HEOS player (pid $pids)." 'Yellow'
            Write-Line "     ZONE2 is NOT independently addressable over HEOS. The zone-1-over-HDMI" 'Yellow'
            Write-Line "     + zone-2-over-network split in ARCHITECTURE.md 5.6 still covers you." 'Yellow'
        }
        else {
            Write-Line "  => No player IDs parsed; see raw output above." 'Red'
        }
    }
}
catch {
    Write-Line "  FAILED: $($_.Exception.Message)" 'Red'
}

Write-Line ""

# ---------------------------------------------------------------------------
# 2. Denon AVR control (port 23) - a different protocol on the same box
# ---------------------------------------------------------------------------
Write-Line "[2] Denon AVR control on port $AvrPort" 'Yellow'
Write-Line ("-" * 62)

# Status queries only. PW=power, ZM=main zone, Z2=zone2, SI=source, MV=volume, MU=mute.
$avrCommands = @('PW?', 'ZM?', 'Z2?', 'SI?', 'MV?', 'MU?')

try {
    $avr = Invoke-TcpProbe -Ip $AvrIp -Port $AvrPort -Commands $avrCommands `
                           -Terminator "`r" -ConnectTimeoutMs $ConnectTimeoutMs `
                           -ReadWindowMs $ReadWindowMs

    if ([string]::IsNullOrWhiteSpace($avr)) {
        Write-Line "  No response. Enable Setup > Network > Network Control > 'Always On'." 'Red'
    }
    else {
        foreach ($line in ($avr -split "`r?`n" | Where-Object { $_.Trim() })) {
            Write-Line "  $line"
        }
        Write-Line ""
        Write-Line "  => Port 23 reachable: we can drive power, zones and volume." 'Green'
    }
}
catch {
    Write-Line "  FAILED: $($_.Exception.Message)" 'Red'
    Write-Line "  Note: port 23 is closed unless Network Control is set to 'Always On'." 'Yellow'
}

Write-Line ""
Write-Line ("=" * 62) 'Cyan'
Write-Line "Probe complete. Paste this output back into the project chat." 'Cyan'
Write-Line ""

if ($OutFile) {
    $transcript -join "`r`n" | Out-File -FilePath $OutFile -Encoding utf8
    Write-Host "Saved to $OutFile" -ForegroundColor Green
}
