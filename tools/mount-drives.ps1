<#
.SYNOPSIS
    Make this machine's drives browsable in Homesh.

.DESCRIPTION
    Writes docker-compose.override.yml with a read-only mount for every fixed
    drive Windows reports, so Sources -> "Add a folder from this computer" can
    walk them and you can pick a folder in the app.

    C: is mounted by docker-compose.yml already. This is for the others -- a
    second disk, a RAID volume, anything with media on it. Run it once, and
    again if you add a drive.

    Read-only, always. The server indexes and streams; it has no business
    writing to a library, and nothing here is read beyond folder names until a
    folder is actually picked in the app.

.PARAMETER List
    Show what is mounted, and stop.

.PARAMETER Exclude
    Drive letters to leave out, e.g. -Exclude G,H

.EXAMPLE
    .\tools\mount-drives.ps1
#>
[CmdletBinding()]
param(
    [switch] $List,
    [string[]] $Exclude = @()
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$overrideFile = Join-Path $repo 'docker-compose.override.yml'

function Get-Mounted {
    if (-not (Test-Path $overrideFile)) { return @() }
    $found = @()
    foreach ($line in Get-Content $overrideFile) {
        if ($line -match '^\s*-\s*"(?<host>[^"]+):/hostfs/(?<letter>[a-z]):ro"\s*$') {
            $found += [pscustomobject]@{
                Letter = $matches['letter'].ToUpper()
                Dir    = $matches['host']
            }
        }
    }
    return $found
}

if ($List) {
    $mounted = @(Get-Mounted)
    Write-Host 'C: is mounted by docker-compose.yml.' -ForegroundColor Cyan
    if ($mounted.Count -eq 0) {
        Write-Host 'No other drives are mounted.'
    } else {
        foreach ($m in $mounted) { Write-Host ("  {0}  ->  /hostfs/{1}" -f $m.Letter, $m.Letter.ToLower()) }
    }
    return
}

# DriveType 3 is a fixed disk. Network drives are deliberately left out: they
# are not reliably there when the server starts, and a mount that vanishes takes
# the container down with it.
$drives = Get-CimInstance Win32_LogicalDisk | Where-Object { $_.DriveType -eq 3 }

$skip = @('C') + ($Exclude | ForEach-Object { $_.TrimEnd(':').ToUpper() })
$wanted = @($drives | Where-Object { $skip -notcontains $_.DeviceID.TrimEnd(':') })

if ($wanted.Count -eq 0) {
    Write-Host 'Nothing to add -- C: is already mounted and there are no other fixed drives.'
    Write-Host 'Open Homesh, go to Sources, and press "Add a folder from this computer".'
    return
}

$lines = @(
    '# Written by tools/mount-drives.ps1.',
    '#',
    '# Generated -- change it through that script rather than by hand. Not tracked',
    '# by git: it names real paths on a real machine. Read-only, because the server',
    '# indexes and streams and has no business writing to a library.',
    'services:',
    '  api:',
    '    volumes:'
)
foreach ($d in $wanted) {
    $letter = $d.DeviceID.TrimEnd(':')
    Write-Host ("Mounting {0}  {1}" -f $d.DeviceID, $d.VolumeName) -ForegroundColor Cyan
    # "E:/", with the colon. Without it Compose reads "E/" as the name of a
    # volume it has never heard of and refuses the whole file.
    $lines += ('      - "{0}/:/hostfs/{1}:ro"' -f $d.DeviceID, $letter.ToLower())
}

$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($overrideFile, $lines, $utf8)

# A USB drive is not attached to the Docker VM by anything else.
#
# WSL2 auto-mounts fixed disks when it starts and never touches removable ones,
# so an external drive appears inside the container as an empty directory rather
# than as an error -- which reads as "the drive is empty" and sends somebody off
# to check the wrong thing entirely. drvfs will mount it perfectly well; nothing
# does it unasked.
#
# It does not survive Docker restarting, because the VM is rebuilt. Run this
# script again after a reboot, or whenever a drive comes back.
$usb = @($wanted | Where-Object {
    $partition = Get-Partition -DriveLetter $_.DeviceID.TrimEnd(':') -ErrorAction SilentlyContinue
    if (-not $partition) { return $false }
    (Get-Disk -Number $partition.DiskNumber -ErrorAction SilentlyContinue).BusType -eq 'USB'
})

foreach ($d in $usb) {
    $letter = $d.DeviceID.TrimEnd(':').ToLower()
    Write-Host ("Attaching {0} to the Docker VM (removable)" -f $d.DeviceID) -ForegroundColor Cyan
    $script = "mkdir -p /mnt/host/$letter && (mountpoint -q /mnt/host/$letter || mount -t drvfs '$($d.DeviceID)' /mnt/host/$letter)"
    & wsl.exe -d docker-desktop -e sh -c $script 2>&1 | ForEach-Object { if ($_) { Write-Host "  $_" } }
}

Write-Host ''
Write-Host 'Applying...' -ForegroundColor Cyan
Push-Location $repo
# Compose writes progress to stderr, and PowerShell 5.1 under 'Stop' turns any
# stderr line from a native command into a terminating error -- so a successful
# run aborts the script. The exit code is the only thing that means anything.
$strict = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    # Forced, because a bind mount is resolved when the container is created:
    # attaching the drive to the VM afterwards does not reach a container that
    # is already running, which looks exactly like the mount having failed.
    & docker compose up -d --force-recreate api 2>&1 | ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -ne 0) {
        throw 'docker compose failed. Is Docker Desktop running?'
    }
} finally {
    $ErrorActionPreference = $strict
    Pop-Location
}

Write-Host ''
Write-Host 'Done.' -ForegroundColor Green
Write-Host 'Open Homesh, go to Sources, and press "Add a folder from this computer".'
