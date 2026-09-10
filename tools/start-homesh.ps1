<#
.SYNOPSIS
    Start Homesh, and say where to reach it.

.DESCRIPTION
    Does the whole sequence, in the order that actually works:

      1. Starts Docker Desktop if the engine is not answering, and waits for it
      2. Brings the stack up
      3. Waits for the server to report itself healthy
      4. Re-attaches any granted folder on a removable drive
      5. Prints the addresses to open

    Step 4 is the one that is easy to forget and hard to diagnose. Docker's
    virtual machine is rebuilt every time Docker restarts, and WSL2 attaches
    fixed disks only -- so a folder on an external drive comes back as an empty
    directory rather than as an error. That looks exactly like an empty folder
    and sends you to check the wrong thing. This checks whether each granted
    folder actually has anything in it, and attaches the drive if not.

    Double-click "Start Homesh.cmd" in the Homesh folder rather than running
    this directly; it is the same thing without a terminal.

.PARAMETER Rebuild
    Rebuild the image first. Needed after changing the code; not otherwise, and
    it costs a couple of minutes.

.PARAMETER Stop
    Stop the stack. The database keeps its data; nothing is deleted.

.PARAMETER Status
    Say what is running and where, and stop.

.EXAMPLE
    .\tools\start-homesh.ps1
    Start it.

.EXAMPLE
    .\tools\start-homesh.ps1 -Rebuild
    Start it, building the image first.
#>
[CmdletBinding()]
param(
    [switch] $Rebuild,
    [switch] $Stop,
    [switch] $Status
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\homesh-common.ps1"

# Long enough for a cold Docker Desktop on a mini PC, short enough that a
# genuine failure is reported rather than waited out.
$ENGINE_TIMEOUT = New-TimeSpan -Minutes 4
$HEALTH_TIMEOUT = New-TimeSpan -Minutes 3

function Test-Engine {
    $strict = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $strict
    }
}

function Start-Engine {
    if (Test-Engine) {
        Write-Host 'Docker is running.' -ForegroundColor DarkGray
        return
    }

    # Docker Desktop is a per-user install on this machine, under LOCALAPPDATA
    # rather than Program Files. Both are checked so the script is not wrong on
    # a machine that installed it the other way.
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe",
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    )
    $exe = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $exe) {
        throw "Docker Desktop is not installed where this expected it: $($candidates -join ' or ')"
    }

    Write-Host 'Starting Docker Desktop...' -ForegroundColor Cyan
    Start-Process $exe

    $deadline = (Get-Date).Add($ENGINE_TIMEOUT)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        if (Test-Engine) {
            Write-Host '  engine is up.' -ForegroundColor DarkGray
            return
        }
        Write-Host '  waiting for the engine...' -ForegroundColor DarkGray
    }
    throw 'Docker did not start within four minutes. Open Docker Desktop and see what it says.'
}

function Wait-Healthy {
    <#
        The container's own healthcheck, not a guess at how long it takes.
        Migrations run at startup and a schema change makes that slower, so a
        fixed sleep is either wrong or wasteful.
    #>
    Write-Host 'Waiting for the server...' -ForegroundColor Cyan
    $deadline = (Get-Date).Add($HEALTH_TIMEOUT)
    while ((Get-Date) -lt $deadline) {
        $strict = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $state = (& docker inspect --format '{{.State.Health.Status}}' media_server-api-1 2>$null)
        } finally {
            $ErrorActionPreference = $strict
        }
        if ($state -eq 'healthy') { Write-Host '  healthy.' -ForegroundColor DarkGray; return $true }
        if ($state -eq 'unhealthy') { break }
        Start-Sleep -Seconds 3
    }
    return $false
}

function Repair-RemovableGrants {
    <#
        A granted folder that came up empty, because the drive it lives on was
        not attached to Docker's virtual machine.

        Checked by looking rather than assuming: the folder may be genuinely
        empty, and remounting one that is already fine costs a container
        restart for nothing.
    #>
    $grants = @(Get-HomeshGrants)
    if ($grants.Count -eq 0) { return }

    $repaired = @()
    foreach ($g in $grants) {
        $strict = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $count = (& docker compose exec -T api sh -c "ls /library/$($g.Name) 2>/dev/null | wc -l" 2>$null)
        } finally {
            $ErrorActionPreference = $strict
        }
        $count = "$count".Trim()

        if ($count -match '^\d+$' -and [int]$count -gt 0) { continue }

        # Empty. On a fixed disk that means an empty folder and is none of our
        # business; on a removable one it means Windows never attached it.
        if (Test-HomeshRemovable $g.Dir) {
            Write-Host "  '$($g.Name)' came up empty -- reattaching $($g.Dir)" -ForegroundColor Yellow
            if (Add-HomeshRemovableMount $g.Dir) { $repaired += $g.Name }
        } else {
            Write-Host "  '$($g.Name)' is empty ($($g.Dir))" -ForegroundColor DarkGray
        }
    }

    if ($repaired.Count -gt 0) {
        # Forced, because a bind mount is resolved when the container is
        # created: attaching the drive afterwards does not reach a container
        # that is already running, which looks exactly like the mount failing.
        Write-Host '  recreating the server so it can see them...' -ForegroundColor DarkGray
        Invoke-HomeshCompose @('up', '-d', '--force-recreate', 'api') -Quiet | Out-Null
        Wait-Healthy | Out-Null
    }
}

function Show-Where {
    <#
        The addresses, asked of the server rather than kept in this script.

        It learns its own house address from the requests it answers, so it is
        the only thing that actually knows -- and a hard-coded address in a
        tracked file is the mistake this repository has made before.
    #>
    $strict = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $where = Invoke-RestMethod -Uri 'http://localhost:8080/tv.address' -TimeoutSec 8
    } catch {
        $where = $null
    } finally {
        $ErrorActionPreference = $strict
    }

    Write-Host ''
    Write-Host 'Homesh is running.' -ForegroundColor Green
    Write-Host ''
    # The short form, without the port. It is the one that can be typed on a
    # television remote in a few seconds, and the one to hand somebody.
    $home_ = $null
    if ($where) { $home_ = $where.short; if (-not $home_) { $home_ = $where.lan } }
    if ($home_)                    { Write-Host "  At home     $home_" -ForegroundColor White }
    if ($where -and $where.origin) { Write-Host "  Away        $($where.origin)" -ForegroundColor White }
    Write-Host "  On this PC  http://localhost:8080" -ForegroundColor DarkGray
    if (-not $where) {
        Write-Host "  (the server did not report its address; it may still be starting)" -ForegroundColor Yellow
    }
    Write-Host ''
    if ($home_) {
        Write-Host "  Phone app   $home_/phone" -ForegroundColor DarkGray
        Write-Host "  TV app      $home_/apk" -ForegroundColor DarkGray
    }
}

# ---- status ---------------------------------------------------------------

if ($Status) {
    if (-not (Test-Engine)) { Write-Host 'Docker is not running, so Homesh is not either.'; return }
    Invoke-HomeshCompose @('ps') | Out-Null
    Show-Where
    $grants = @(Get-HomeshGrants)
    if ($grants.Count -gt 0) {
        Write-Host ''
        Write-Host 'Folders Homesh can read, and nothing else on this PC:' -ForegroundColor Cyan
        foreach ($g in $grants) { Write-Host ("  {0,-16} {1}" -f $g.Name, $g.Dir) }
    }
    return
}

# ---- stop -----------------------------------------------------------------

if ($Stop) {
    if (-not (Test-Engine)) { Write-Host 'Docker is not running; nothing to stop.'; return }
    Write-Host 'Stopping Homesh...' -ForegroundColor Cyan
    $code = Invoke-HomeshCompose @('stop')
    if ($code -ne 0) { throw 'docker compose stop failed.' }
    Write-Host ''
    Write-Host 'Stopped. Nothing was deleted; starting it again picks up where it left off.' -ForegroundColor Green
    return
}

# ---- start ----------------------------------------------------------------

Start-Engine

Write-Host 'Starting Homesh...' -ForegroundColor Cyan
$args = if ($Rebuild) { @('up', '-d', '--build') } else { @('up', '-d') }
$code = Invoke-HomeshCompose $args
if ($code -ne 0) {
    throw 'docker compose failed. Run again with -Rebuild, or see: docker compose logs api'
}

if (-not (Wait-Healthy)) {
    Write-Host ''
    Write-Host 'The server started but never reported itself healthy.' -ForegroundColor Yellow
    Write-Host 'What it is complaining about:' -ForegroundColor Yellow
    Invoke-HomeshCompose @('logs', '--tail', '30', 'api') | Out-Null
    exit 1
}

Repair-RemovableGrants
Show-Where
