<#
    Shared by the scripts that start Homesh and that grant it a folder.

    Both need to read the list of granted folders and to attach a removable
    drive to Docker's virtual machine, and a second copy of the drvfs mount is
    exactly the sort of thing that drifts out of step and is then debugged twice.

    Dot-sourced, not run:  . "$PSScriptRoot\homesh-common.ps1"

    Pure ASCII on purpose. PowerShell 5.1 reads a .ps1 without a byte-order mark
    as ANSI, so a UTF-8 dash arrives as three characters and one of them ends a
    string early -- which does not fail where it was written but somewhere later
    and unrecognisably.
#>

# Where a granted folder lands inside the container. server/app/library.py
# registers every directory it finds here, so the two constants must agree.
$script:HomeshMountRoot = '/library'

function Get-HomeshRepo {
    Split-Path -Parent $PSScriptRoot
}

function Get-HomeshOverrideFile {
    Join-Path (Get-HomeshRepo) 'docker-compose.override.yml'
}

function Get-HomeshGrants {
    <#
        The folders Homesh has been given on this PC, read back out of the file
        that grants them. That file is the single record of what the server can
        reach, so nothing here keeps a second list to fall out of step with it.
    #>
    $file = Get-HomeshOverrideFile
    if (-not (Test-Path $file)) { return @() }

    $found = @()
    $pattern = '^(?<off>\s*#\s*OFFLINE\s*)?\s*-\s*"(?<host>.+):' +
        [regex]::Escape($script:HomeshMountRoot) + '/(?<name>[^:"]+):ro"\s*$'
    foreach ($line in Get-Content $file) {
        if ($line -match $pattern) {
            $found += [pscustomobject]@{
                Name   = $matches['name']
                Dir    = $matches['host'] -replace '/', '\'
                # A grant whose drive is not here today. Still a grant -- the
                # line stays in the file and comes back by itself -- but not a
                # mount Docker can make. See Sync-HomeshGrants.
                Active = -not $matches['off']
            }
        }
    }
    return $found
}

function Get-HomeshMounted {
    <#
        The granted folders the running server can actually read, asked of the
        server rather than inferred.

        A folder can be listed in the grant file and absent from the container
        (the container predates the grant), or present in the container and
        unreadable (the drive died under the mount, which answers ENODEV rather
        than going away). Neither is visible from the Windows side, so it is
        asked where it is true.
    #>
    $strict = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # One line per readable directory. `ls` on a dead mount fails, and the
        # name is then simply absent, which is the answer we want.
        $out = & docker compose exec -T api sh -c 'for d in /library/*/; do ls "$d" >/dev/null 2>&1 && basename "$d"; done' 2>$null
    } finally {
        $ErrorActionPreference = $strict
    }
    if (-not $out) { return @() }
    return @($out | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
}

function Sync-HomeshGrants {
    <#
        Leave out the folders whose drive is not here, so the server can start
        without them.

        The storage in this design is meant to be switched off -- "the catalog
        is always up; the bytes may not be" is the first principle in the
        architecture. It was not true. A bind mount names a path, Docker
        resolves it when it creates the container, and a path on a powered-down
        RAID does not resolve. So turning the RAID off did not degrade Homesh,
        it stopped it: the container refuses to start, and with it goes the
        catalog, the rooms and the music that lives on Drive and needed no disk
        at all.

        A grant is never forgotten here, only set aside. The line stays in the
        file behind an OFFLINE marker, so the record of what this PC has given
        the server is still whole and still readable, and the next start with
        the drive present puts it back.

        Returns the names it set aside.
    #>
    $file = Get-HomeshOverrideFile
    if (-not (Test-Path $file)) { return @() }

    $pattern = '^(?<off>\s*#\s*OFFLINE\s*)?(?<mount>\s*-\s*"(?<host>.+):' +
        [regex]::Escape($script:HomeshMountRoot) + '/(?<name>[^:"]+):ro")\s*$'

    $lines = @()
    $setAside = @()
    $restored = @()
    foreach ($line in Get-Content $file) {
        if ($line -notmatch $pattern) { $lines += $line; continue }

        $dir = $matches['host'] -replace '/', '\'
        $name = $matches['name']
        # Trimmed because a line that has been round this loop already carries
        # the marker and the indentation from last time.
        $mount = $matches['mount'].Trim()

        if (Test-Path -LiteralPath $dir) {
            if ($matches['off']) { $restored += $name }
            $lines += ('      ' + $mount)
        } else {
            if (-not $matches['off']) { $setAside += $name }
            $lines += ('      # OFFLINE ' + $mount)
        }
    }

    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($file, $lines, $utf8)

    foreach ($name in $restored) {
        Write-Host "  '$name' is back" -ForegroundColor Green
    }
    foreach ($name in $setAside) {
        Write-Host "  '$name' is on a drive that is not here -- starting without it" -ForegroundColor Yellow
    }
    return $setAside
}


function Test-HomeshRemovable($folder) {
    <# Whether this folder sits on a drive Windows treats as removable. #>
    if ($folder.Length -lt 2 -or $folder[1] -ne ':') { return $false }
    $letter = ($folder.Substring(0, 1)).ToUpper()
    $partition = Get-Partition -DriveLetter $letter -ErrorAction SilentlyContinue
    if (-not $partition) { return $false }
    $disk = Get-Disk -Number $partition.DiskNumber -ErrorAction SilentlyContinue
    return ($disk -and $disk.BusType -eq 'USB')
}

function Add-HomeshRemovableMount($folder) {
    <#
        Attach a folder on a removable drive to Docker's virtual machine.

        WSL2 mounts fixed disks when it starts and never touches removable ones,
        so a folder on an external drive appears inside the container as an
        empty directory rather than as an error. That reads as "the folder is
        empty" and sends somebody off to check the wrong thing entirely -- it
        cost an evening once already. drvfs mounts it perfectly well; nothing
        does it unasked.

        The virtual machine is rebuilt whenever Docker restarts, so this has to
        run again after every reboot. That is why the start script does it
        rather than leaving it to whoever remembers.

        Only the granted folder is attached, never the whole drive: what was not
        granted stays out of reach at every level, not merely the container's.
    #>
    if (-not (Test-HomeshRemovable $folder)) { return $false }

    $letter = ($folder.Substring(0, 1)).ToUpper()
    # Mirrored at the same path inside the VM, so the bind mount resolves.
    $root = '/mnt/host/' + $letter.ToLower()
    $inVm = $root + ($folder.Substring(2) -replace '\\', '/')

    # Clear a mount whose transport has died before making a new one.
    #
    # Switching the drive off does not remove its 9p mount inside the virtual
    # machine, it kills the connection underneath it -- so the path is still
    # listed in /proc/mounts while every access to it returns ENODEV. Docker
    # then cannot even create the directory it wants to bind to, and says so
    # obscurely: "mkdir /run/desktop/mnt/host/e: file exists". Measured, with
    # the RAID switched off and on again.
    # Written out twice rather than as a loop, and with no double quotes in
    # it: PowerShell rewrites quoting when it hands an argument to a native
    # command, and a shell loop variable does not survive the journey.
    $clear = "if grep -q ' $inVm ' /proc/mounts && ! ls '$inVm' >/dev/null 2>&1; then umount -l '$inVm'; fi; " +
        "if grep -q ' $root ' /proc/mounts && ! ls '$root' >/dev/null 2>&1; then umount -l '$root'; fi"
    $sh = "$clear; mkdir -p '$inVm' && (mountpoint -q '$inVm' || mount -t drvfs '$folder' '$inVm')"

    $strict = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & wsl.exe -d docker-desktop -e sh -c $sh 2>&1 |
            ForEach-Object { if ($_) { Write-Host "    $_" -ForegroundColor DarkGray } }
    } finally {
        $ErrorActionPreference = $strict
    }
    return $true
}

function Invoke-HomeshCompose {
    <#
        docker compose, with its output shown and its exit code believed.

        Compose writes progress to stderr, and PowerShell 5.1 under
        $ErrorActionPreference 'Stop' turns any stderr line from a native
        command into a terminating error -- so a perfectly successful run aborts
        the script on the words "Container media_server-db-1 Running". The exit
        code is the only thing here that means anything.
    #>
    param([string[]] $Arguments, [switch] $Quiet)

    Push-Location (Get-HomeshRepo)
    $strict = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & docker compose @Arguments 2>&1 | ForEach-Object {
            if (-not $Quiet -and $_) { Write-Host "  $_" -ForegroundColor DarkGray }
        }
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $strict
        Pop-Location
    }
}
