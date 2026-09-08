<#
.SYNOPSIS
    Give Homesh access to one folder on this PC.

.DESCRIPTION
    Opens the ordinary Windows folder picker. What you choose is mounted into
    the server read-only and becomes a source; nothing else on the machine is
    reachable by it.

    Double-click "Add a folder to Homesh.cmd" in the Homesh folder rather than
    running this directly -- it is the same thing without a terminal.

    Why it happens here rather than in the app: adding a folder is a one-time
    act performed at the machine that holds the folder, exactly like sharing a
    folder with the Homesh account in Google Drive. The alternative -- mounting
    whole drives so the server can browse them from a phone -- buys convenience
    on a job you do once, and pays for it by letting the server see the entire
    disk forever. That trade was rejected, rightly.

    A browser cannot do this at all, whichever way it is arranged: no browser
    tells a web page a real path. A file picker in the app returns names and
    bytes and never "E:\music".

.PARAMETER Path
    Skip the dialog and grant this folder. For scripting and headless hosts.

.PARAMETER List
    Show the folders already granted, and stop.

.PARAMETER Revoke
    Withdraw a folder by name. The files are untouched; the server loses access.

.EXAMPLE
    .\tools\grant-folder.ps1
    Pick a folder and grant it.
#>
[CmdletBinding()]
param(
    [string] $Path,
    [switch] $List,
    [string] $Revoke
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$overrideFile = Join-Path $repo 'docker-compose.override.yml'

# Windows hands the whole URL to the handler as an argument, so a click on
# "homesh://add-folder" in the app arrives here as $Path. It is a request to
# open the picker, not a folder to grant.
if ($Path -and $Path -like 'homesh:*') { $Path = $null }

function Register-Protocol {
    <#
        Teach Windows what "homesh://add-folder" means, so the app can have a
        real button that opens the real folder dialog.

        This is the same mechanism behind a Zoom or Spotify link, and it is the
        answer to a question that kept coming back: a web page cannot open a
        folder picker on the PC, but it can ask Windows to open something that
        can. The page never learns a path -- no browser will tell it one -- and
        it does not need to. The picking and the granting both happen here.

        HKCU, so no administrator rights and nothing machine-wide. Rewritten on
        every run, which keeps it correct if the repository is moved.
    #>
    $key = 'HKCU:\Software\Classes\homesh'
    $command = ('powershell -NoProfile -ExecutionPolicy Bypass -File "{0}" -Path "%1"' -f
                (Join-Path $PSScriptRoot 'grant-folder.ps1'))
    try {
        New-Item -Path "$key\shell\open\command" -Force | Out-Null
        Set-ItemProperty -Path $key -Name '(Default)' -Value 'URL:Homesh'
        Set-ItemProperty -Path $key -Name 'URL Protocol' -Value ''
        Set-ItemProperty -Path "$key\shell\open\command" -Name '(Default)' -Value $command
    } catch {
        # A locked-down profile can refuse this. The double-click still works,
        # so say so rather than failing the thing somebody actually asked for.
        Write-Host "  (could not register the in-app button: $_)" -ForegroundColor DarkGray
    }
}

Register-Protocol

# Where a granted folder lands inside the container. server/app/library.py
# registers every directory it finds here, so the two constants must agree.
$mountRoot = '/library'

function Get-Grants {
    # Parsed back out of the file this script writes, so the file stays the one
    # record of what was granted -- nothing to keep in step with it.
    if (-not (Test-Path $overrideFile)) { return @() }
    $found = @()
    $pattern = '^\s*-\s*"(?<host>.+):' + [regex]::Escape($mountRoot) + '/(?<name>[^:"]+):ro"\s*$'
    foreach ($line in Get-Content $overrideFile) {
        if ($line -match $pattern) {
            $found += [pscustomobject]@{
                Name = $matches['name']
                Dir  = $matches['host'] -replace '/', '\'
            }
        }
    }
    return $found
}

function Write-Grants($grants) {
    if ($grants.Count -eq 0) {
        if (Test-Path $overrideFile) { Remove-Item $overrideFile }
        return
    }

    $lines = @(
        '# Folders granted to Homesh.',
        '#',
        '# Generated -- change it through "Add a folder to Homesh" rather than by',
        '# hand. Not tracked by git: it names real paths on a real machine. Every',
        '# mount is read-only, and this file is the whole of what the server can',
        '# reach on this PC.',
        'services:',
        '  api:',
        '    volumes:'
    )
    foreach ($g in $grants) {
        # Forward slashes: a Windows path in YAML is otherwise a run of escape
        # characters waiting to be misread.
        $hostPath = $g.Dir -replace '\\', '/'
        $lines += ('      - "{0}:{1}/{2}:ro"' -f $hostPath, $mountRoot, $g.Name)
    }
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($overrideFile, $lines, $utf8)
}

function New-Name($folder, $taken) {
    # The folder's own name, which is what makes it recognisable in the app.
    # Lowercased and stripped because it becomes part of a mount point and of a
    # URL prefix, and those two disagree about what a legal character is.
    $base = (Split-Path -Leaf $folder).ToLower() -replace '[^a-z0-9]+', '-'
    $base = $base.Trim('-')
    if (-not $base) { $base = 'folder' }

    $name = $base
    $n = 2
    while ($taken -contains $name) {
        $name = "$base-$n"
        $n++
    }
    return $name
}

function Attach-IfRemovable($folder) {
    <#
        A USB disk is not attached to Docker's virtual machine by anything.

        WSL2 mounts fixed disks when it starts and never touches removable ones,
        so a folder on an external drive mounts into the container as an empty
        directory rather than as an error -- which reads as "the folder is
        empty" and sends somebody off to check the wrong thing entirely. drvfs
        mounts it perfectly well; nothing does it unasked.

        Only the granted folder is attached, not the drive: what was not granted
        stays out of reach at every level, not merely at the container's.
    #>
    $letter = ($folder.Substring(0, 1)).ToUpper()
    $partition = Get-Partition -DriveLetter $letter -ErrorAction SilentlyContinue
    if (-not $partition) { return }
    $disk = Get-Disk -Number $partition.DiskNumber -ErrorAction SilentlyContinue
    if (-not $disk -or $disk.BusType -ne 'USB') { return }

    Write-Host "  $letter`: is removable -- attaching it to Docker" -ForegroundColor DarkGray
    # Mirrored at the same path inside the VM, so the bind mount below resolves.
    $inVm = '/mnt/host/' + $letter.ToLower() + ($folder.Substring(2) -replace '\\', '/')
    $script = "mkdir -p '$inVm' && (mountpoint -q '$inVm' || mount -t drvfs '$folder' '$inVm')"
    & wsl.exe -d docker-desktop -e sh -c $script 2>&1 |
        ForEach-Object { if ($_) { Write-Host "    $_" -ForegroundColor DarkGray } }
}

function Restart-Server {
    Write-Host ''
    Write-Host 'Applying...' -ForegroundColor Cyan
    Push-Location $repo
    # Compose writes progress to stderr, and PowerShell 5.1 under 'Stop' turns
    # any stderr line from a native command into a terminating error -- so a
    # successful run aborts the script. The exit code is what means anything.
    $strict = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # Forced, because a bind mount is resolved when the container is created:
        # a change that does not recreate it does not reach a running container,
        # which looks exactly like the mount having failed.
        & docker compose up -d --force-recreate api 2>&1 |
            ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
        if ($LASTEXITCODE -ne 0) {
            throw 'docker compose failed. Is Docker Desktop running?'
        }
    } finally {
        $ErrorActionPreference = $strict
        Pop-Location
    }
}

# ---- list ----------------------------------------------------------------

$grants = @(Get-Grants)

if ($List) {
    if ($grants.Count -eq 0) {
        Write-Host 'Homesh has not been given any folder on this PC.'
    } else {
        Write-Host 'Homesh can read these folders, and nothing else on this PC:' -ForegroundColor Cyan
        foreach ($g in $grants) { Write-Host ('  {0,-20} {1}' -f $g.Name, $g.Dir) }
    }
    return
}

# ---- revoke --------------------------------------------------------------

if ($Revoke) {
    $gone = $grants | Where-Object { $_.Name -eq $Revoke }
    if (-not $gone) {
        Write-Host "No folder called '$Revoke'. Use -List to see the names." -ForegroundColor Yellow
        return
    }
    Write-Grants @($grants | Where-Object { $_.Name -ne $Revoke })
    Restart-Server
    Write-Host ''
    Write-Host "Revoked '$Revoke'. The files on disk were not touched." -ForegroundColor Green
    Write-Host 'Remove the source in Homesh to clear it from the catalog too.'
    return
}

# ---- pick ----------------------------------------------------------------

if (-not $Path) {
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = 'Choose a folder to give Homesh access to'
    $dialog.ShowNewFolderButton = $false
    # This PC, so every drive is one click away rather than buried under
    # whichever folder Windows happened to remember last.
    $dialog.RootFolder = [System.Environment+SpecialFolder]::MyComputer

    Write-Host 'Choose a folder...' -ForegroundColor Cyan
    if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
        Write-Host 'Nothing chosen.'
        return
    }
    $Path = $dialog.SelectedPath
}

$Path = (Resolve-Path -LiteralPath $Path).Path.TrimEnd('\')

if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
    throw "$Path is not a folder."
}

$already = $grants | Where-Object { $_.Dir -eq $Path }
if ($already) {
    Write-Host ''
    Write-Host "$Path is already in Homesh, as '$($already.Name)'." -ForegroundColor Yellow
    return
}

# A folder inside one already granted would be indexed twice, under two names.
$nested = $grants | Where-Object {
    $Path.StartsWith($_.Dir + '\', [StringComparison]::OrdinalIgnoreCase)
}
if ($nested) {
    Write-Host ''
    Write-Host "That folder is already inside '$($nested.Name)' ($($nested.Dir))," -ForegroundColor Yellow
    Write-Host 'so Homesh can already see it.'
    return
}

$name = New-Name $Path @($grants | ForEach-Object { $_.Name })

Write-Host ''
Write-Host "Giving Homesh read-only access to" -ForegroundColor Cyan
Write-Host "  $Path"
Write-Host "  as '$name'"

Attach-IfRemovable $Path
Write-Grants @($grants + [pscustomobject]@{ Name = $name; Dir = $Path })
Restart-Server

Write-Host ''
Write-Host 'Done.' -ForegroundColor Green
Write-Host 'Open Homesh, go to Sources, and press Rescan to index it.'
