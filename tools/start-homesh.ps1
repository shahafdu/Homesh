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

.PARAMETER Sync
    Follow the storage. Compares the granted folders against what is actually
    attached and, only if that has changed, re-attaches the drive and recreates
    the server so it can see it. Silent and quick when nothing has changed,
    which is almost always -- it is meant to be run on a timer, and the
    scheduled task that does so is installed on the first ordinary start.

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
    [switch] $Status,
    [switch] $Sync
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
        # A folder set aside because its drive is not here is not "empty", and
        # saying so right after "starting without it" read as a second fault.
        if (-not $g.Active) { continue }
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

function Install-Watcher {
    <#
        Register the scheduled task that follows the storage.

        Windows rather than the server, and the reason is not preference: a
        container cannot recreate itself without the Docker socket, and mounting
        that socket into a server that faces the house would hand anything that
        got into it root on this machine. The watcher belongs outside.

        Registered on an ordinary start so nobody has to know it exists, and
        idempotent so starting Homesh twice does not make two of them. It runs
        as the logged-in user, because Docker Desktop is a per-user install and
        a task running as SYSTEM cannot see it.
    #>
    $name = 'Homesh - follow the storage'
    $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($existing) { return }

    # Through a four-line script rather than straight to powershell.exe, and the
    # reason is the only thing anybody notices about this feature: a task that
    # launches powershell pops a console over whatever you are doing, every few
    # minutes, for ever. -WindowStyle Hidden does not prevent that -- the
    # console is created and then hidden, which is a flash rather than nothing.
    # Running under an S4U principal does prevent it and needs administrator
    # rights to register, which an ordinary start does not have.
    $shim = Join-Path $PSScriptRoot 'follow-storage.vbs'
    $action = New-ScheduledTaskAction -Execute 'wscript.exe' `
        -Argument ('"{0}"' -f $shim) `
        -WorkingDirectory (Get-HomeshRepo)

    # Every two minutes, for ever. The check costs a file test per granted
    # folder when nothing has changed, which is nothing at all.
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 5)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -StartWhenAvailable -Hidden `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

    try {
        Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
            -Settings $settings `
            -Description ('Notices when the drive holding a granted folder ' +
            'is switched on or off, and points Homesh at it. Installed by Start Homesh.') | Out-Null
        Write-Host '  installed the watcher that follows your storage' -ForegroundColor DarkGray
    } catch {
        # Not fatal. Everything works; it just needs a restart when the drive
        # comes back, which is how it worked before this existed.
        Write-Host "  could not install the storage watcher: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

# ---- follow the storage ---------------------------------------------------

if ($Sync) {
    <#
        Run on a timer. Does nothing at all unless the set of reachable granted
        folders has changed since the server was started.

        This exists because a Docker bind mount is resolved when the container
        is created, and never again. There is no way to hand a running container
        a folder that has just appeared, and no way for it to let go of one that
        has gone -- so following the storage means recreating the container, and
        the only question is who notices that it needs doing. Doing it here, on
        a timer, is the difference between "turn the RAID on and wait a moment"
        and "turn the RAID on, then go and restart the server".

        Quiet on purpose: it runs every couple of minutes for ever, and a line
        of output each time would bury the one that matters.
    #>
    if (-not (Test-Engine)) { return }

    $grants = @(Get-HomeshGrants)
    if ($grants.Count -eq 0) { return }

    # Compared against what the *container* actually has, rather than against
    # what the grant file says it should have. Those two can disagree -- the
    # file is edited, a container outlives a change, a drive dies under a mount
    # that is still listed -- and the file being wrong is exactly the case where
    # doing nothing is the wrong answer. Both sides are asked directly.
    $readable = Get-HomeshMounted
    if ($null -eq $readable) {
        # The server could not be asked. Not knowing is not the same as knowing
        # that nothing is mounted, and acting on the confusion recreated the
        # container every two minutes for as long as the mistake lasted.
        return
    }

    $changed = $false
    foreach ($g in $grants) {
        $here = Test-Path -LiteralPath $g.Dir
        $mounted = @($readable) -contains $g.Name
        if ($here -ne $mounted) { $changed = $true }
    }
    if (-not $changed) { return }

    Write-Host 'The storage changed. Following it...' -ForegroundColor Cyan
    foreach ($g in $grants) {
        if (Test-Path -LiteralPath $g.Dir) { Add-HomeshRemovableMount $g.Dir | Out-Null }
    }
    Sync-HomeshGrants | Out-Null

    # Forced, because a bind mount is read when the container is created: a
    # container that is already running cannot be shown a folder.
    Invoke-HomeshCompose @('up', '-d', '--force-recreate', 'api') -Quiet | Out-Null
    if (Wait-Healthy) {
        Write-Host 'The server is following the storage again.' -ForegroundColor Green
    }
    return
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

# Before anything is started, settle which granted folders are actually here.
# A folder on storage that is switched off cannot be mounted, and Docker's
# answer to that is to refuse to start the container at all -- which takes the
# catalog, the rooms and the music on Drive down with it, none of which needed
# that disk. Setting it aside costs the files on that drive and keeps the rest.
Sync-HomeshGrants | Out-Null
Install-Watcher

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
