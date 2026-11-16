<#
.SYNOPSIS
    Tell Homesh when files change in a granted folder.

.DESCRIPTION
    A container cannot watch Windows folders, so this does, on the PC. When a
    folder has changed and then been quiet for five minutes it runs
    `python -m app.scanrequest <name>` inside the server, which only sets a
    flag; the server scans a little later. No network route is involved and no
    path is ever sent, only the name of an already-granted folder.

    Started hidden at sign-in by watch-folders.vbs. Logs folder names (the
    mounted labels) and exit codes to .local\watch-folders.log, never a file name.
#>
$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\homesh-common.ps1"

$SETTLE = New-TimeSpan -Minutes 5
$PASS_SECONDS = 30

# A second copy must not run.
$created = $false
$mutex = New-Object System.Threading.Mutex($true, 'Local\HomeshWatchFolders', [ref]$created)
if (-not $created) { exit 0 }

$repo = Get-HomeshRepo
$logFile = Join-Path $repo '.local\watch-folders.log'

function Write-WatchLog([string]$message) {
    try {
        $dir = Split-Path -Parent $logFile
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
        if ((Test-Path $logFile) -and ((Get-Item $logFile).Length -gt 1MB)) {
            Remove-Item $logFile -Force
        }
        Add-Content -Path $logFile -Value ('{0} {1}' -f (Get-Date -Format 's'), $message)
    } catch { }
}

# name -> last time something changed in that folder
$dirty = [hashtable]::Synchronized(@{})
# name -> @{ Dir; Watcher }
$watched = @{}
$overrideStamp = $null

function Add-Watch($grant) {
    $name = $grant.Name
    try {
        $w = New-Object System.IO.FileSystemWatcher
        $w.Path = $grant.Dir
        $w.IncludeSubdirectories = $true
        $w.InternalBufferSize = 65536
        $w.NotifyFilter = [System.IO.NotifyFilters]'FileName, DirectoryName, LastWrite, Size'
        $mark = {
            $table = $Event.MessageData.Table
            $table[$Event.MessageData.Name] = Get-Date
        }
        $data = [pscustomobject]@{ Table = $dirty; Name = $name }
        foreach ($kind in 'Created', 'Deleted', 'Renamed', 'Changed', 'Error') {
            Register-ObjectEvent -InputObject $w -EventName $kind `
                -SourceIdentifier "homesh-watch-$name-$kind" -Action $mark `
                -MessageData $data | Out-Null
        }
        $w.EnableRaisingEvents = $true
        $watched[$name] = @{ Dir = $grant.Dir; Watcher = $w }
        # Whatever changed while it was not watched is caught by a scan.
        $dirty[$name] = (Get-Date).AddTicks(-$SETTLE.Ticks)
        Write-WatchLog "watching $name"
    } catch {
        Write-WatchLog "could not watch $name"
    }
}

function Remove-Watch([string]$name) {
    $entry = $watched[$name]
    if (-not $entry) { return }
    foreach ($kind in 'Created', 'Deleted', 'Renamed', 'Changed', 'Error') {
        Unregister-Event -SourceIdentifier "homesh-watch-$name-$kind" -ErrorAction SilentlyContinue
    }
    try { $entry.Watcher.EnableRaisingEvents = $false; $entry.Watcher.Dispose() } catch { }
    $watched.Remove($name)
    Write-WatchLog "stopped watching $name"
}

function Send-Notice([string]$name) {
    Push-Location $repo
    try {
        & docker compose exec -T api python -m app.scanrequest $name 2>$null | Out-Null
        return $LASTEXITCODE
    } finally {
        Pop-Location
    }
}

Write-WatchLog 'started'
while ($true) {
    try {
        $override = Get-HomeshOverrideFile
        $stamp = if (Test-Path $override) { (Get-Item $override).LastWriteTimeUtc } else { $null }
        $changed = ($stamp -ne $overrideStamp)
        $overrideStamp = $stamp

        $grants = @(Get-HomeshGrants | Where-Object { $_.Active })
        $names = @($grants | ForEach-Object { $_.Name })

        # A folder removed from the grants.
        foreach ($name in @($watched.Keys)) {
            if ($names -notcontains $name) { Remove-Watch $name }
        }
        foreach ($grant in $grants) {
            $name = $grant.Name
            $exists = Test-Path -LiteralPath $grant.Dir
            if ($watched.ContainsKey($name)) {
                # A drive that was switched off: its watcher is dead.
                if (-not $exists) { Remove-Watch $name }
            } elseif ($exists) {
                Add-Watch $grant
            }
        }

        foreach ($name in @($dirty.Keys)) {
            if (((Get-Date) - $dirty[$name]) -gt $SETTLE) {
                $code = Send-Notice $name
                Write-WatchLog "notice $name exit $code"
                if ($code -eq 0) { $dirty.Remove($name) }
            }
        }
    } catch {
        Write-WatchLog 'pass failed'
    }
    Start-Sleep -Seconds $PASS_SECONDS
}
