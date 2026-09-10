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
    $pattern = '^\s*-\s*"(?<host>.+):' + [regex]::Escape($script:HomeshMountRoot) + '/(?<name>[^:"]+):ro"\s*$'
    foreach ($line in Get-Content $file) {
        if ($line -match $pattern) {
            $found += [pscustomobject]@{
                Name = $matches['name']
                Dir  = $matches['host'] -replace '/', '\'
            }
        }
    }
    return $found
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
    $inVm = '/mnt/host/' + $letter.ToLower() + ($folder.Substring(2) -replace '\\', '/')
    $sh = "mkdir -p '$inVm' && (mountpoint -q '$inVm' || mount -t drvfs '$folder' '$inVm')"

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
