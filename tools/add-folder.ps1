<#
.SYNOPSIS
    Add a folder on this PC to the Homesh library.

.DESCRIPTION
    Opens the ordinary Windows folder picker, mounts what you choose into the
    server read-only, and restarts it so the folder appears in the app.

    It works this way round because a browser cannot do it. A web page never
    learns a real path: a file picker hands over names and bytes, and the File
    System Access API hands over a handle, but "D:\Media" is deliberately
    withheld from every page by every browser. So a folder can be picked in a
    web app, or it can be picked by path, and only one of those is the folder
    you meant.

    The alternative was for the server to list this machine and let you click
    through it, which is what it did until that was rightly called out: a media
    server has no business enumerating somebody's disk to be told one path.
    This asks Windows instead, and Windows already has the dialog.

.PARAMETER Path
    Skip the dialog and add this folder. For scripting, and for a headless host.

.PARAMETER List
    Show the folders already added, and stop.

.PARAMETER Remove
    Remove a folder by name. The files are untouched; the server forgets them.

.EXAMPLE
    .\tools\add-folder.ps1
    Pick a folder and add it.

.EXAMPLE
    .\tools\add-folder.ps1 -Path D:\Music
#>
[CmdletBinding()]
param(
    [string] $Path,
    [switch] $List,
    [string] $Remove
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$overrideFile = Join-Path $repo 'docker-compose.override.yml'

# Where a mounted folder lands inside the container. server/app/library.py
# registers every directory it finds here, so the two constants must agree.
$mountRoot = '/library'

function Get-Mounts {
    # Parsed back out of the file this script writes, so the file stays the one
    # record of what is mounted -- nothing to keep in step with it.
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

function Write-Mounts($mounts) {
    if ($mounts.Count -eq 0) {
        if (Test-Path $overrideFile) { Remove-Item $overrideFile }
        return
    }

    $lines = @(
        '# Folders added with tools/add-folder.ps1.',
        '#',
        '# Generated -- change it through that script rather than by hand. Not tracked',
        '# by git: it names real paths on a real machine, which this repository must',
        '# not publish. Mounted read-only, because the server indexes and streams and',
        '# has no business writing to a library.',
        'services:',
        '  api:',
        '    volumes:'
    )
    foreach ($m in $mounts) {
        # Forward slashes: a Windows path in YAML is otherwise a run of escape
        # characters waiting to be misread.
        $hostPath = $m.Dir -replace '\\', '/'
        $lines += ('      - "{0}:{1}/{2}:ro"' -f $hostPath, $mountRoot, $m.Name)
    }
    # UTF-8 without a BOM, so Compose reads it the same way everywhere.
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

function Restart-Server {
    Write-Host ''
    Write-Host 'Applying...' -ForegroundColor Cyan
    Push-Location $repo
    # Compose writes its progress to stderr, and under $ErrorActionPreference
    # 'Stop' PowerShell 5.1 turns any line a native command puts there into a
    # terminating error -- so " Container media_server-db-1 Running" aborts the
    # script on a successful run. The exit code is the thing that means
    # anything here.
    $strict = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # Recreates the api container with the new mount. The database, the
        # cache and every other service are left alone.
        & docker compose up -d api 2>&1 | ForEach-Object { Write-Host "  $_" }
        if ($LASTEXITCODE -ne 0) {
            throw 'docker compose failed. Is Docker Desktop running?'
        }
    } finally {
        $ErrorActionPreference = $strict
        Pop-Location
    }
}

# ---- list ----------------------------------------------------------------

$mounts = @(Get-Mounts)

if ($List) {
    if ($mounts.Count -eq 0) {
        Write-Host 'No folders added yet. Run this script with no arguments to add one.'
    } else {
        Write-Host 'Folders in your library:' -ForegroundColor Cyan
        foreach ($m in $mounts) { Write-Host ('  {0,-20} {1}' -f $m.Name, $m.Dir) }
    }
    return
}

# ---- remove --------------------------------------------------------------

if ($Remove) {
    $gone = $mounts | Where-Object { $_.Name -eq $Remove }
    if (-not $gone) {
        Write-Host "No folder called '$Remove'. Use -List to see the names." -ForegroundColor Yellow
        return
    }
    Write-Mounts @($mounts | Where-Object { $_.Name -ne $Remove })
    Restart-Server
    Write-Host ''
    Write-Host "Removed '$Remove'. The files on disk were not touched." -ForegroundColor Green
    Write-Host 'Its entries stay in the catalog, marked unavailable, until the source'
    Write-Host 'is removed in Settings.'
    return
}

# ---- pick ----------------------------------------------------------------

if (-not $Path) {
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = 'Choose a folder to add to Homesh'
    $dialog.ShowNewFolderButton = $false
    # Start at This PC, so every drive is one click away rather than buried
    # under whichever folder Windows happened to remember last.
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

$already = $mounts | Where-Object { $_.Dir -eq $Path }
if ($already) {
    Write-Host ''
    Write-Host "$Path is already in your library, as '$($already.Name)'." -ForegroundColor Yellow
    return
}

# A folder inside one already mounted would be indexed twice, under two names.
$nested = $mounts | Where-Object {
    $Path.StartsWith($_.Dir + '\', [StringComparison]::OrdinalIgnoreCase)
}
if ($nested) {
    Write-Host ''
    Write-Host "That folder is already inside '$($nested.Name)' ($($nested.Dir))," -ForegroundColor Yellow
    Write-Host 'so it is in your library already.'
    return
}

$name = New-Name $Path @($mounts | ForEach-Object { $_.Name })

Write-Host ''
Write-Host "Adding $Path" -ForegroundColor Cyan
Write-Host "  as '$name', read-only"

Write-Mounts @($mounts + [pscustomobject]@{ Name = $name; Dir = $Path })
Restart-Server

Write-Host ''
Write-Host 'Added.' -ForegroundColor Green
Write-Host 'Open Homesh, go to Settings, and press "Look for new folders" to index it.'
