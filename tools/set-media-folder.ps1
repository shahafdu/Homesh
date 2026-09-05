<#
.SYNOPSIS
    Point Homesh at a folder of media on this computer, and restart it.

.DESCRIPTION
    The server runs in a container, which sees only what has been mounted into
    it. That is why folders cannot simply be typed into the app: most paths do
    not exist from where the server is standing. This mounts one and restarts,
    after which the folder's contents appear in Settings, ready to be added.

    Read-only, always. Homesh indexes and streams; it has no business writing to
    a library, and the mount is what enforces that rather than the code
    remembering to.

.EXAMPLE
    .\tools\set-media-folder.ps1 D:\Media
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string] $Folder
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repo '.env'

if (-not (Test-Path -LiteralPath $Folder -PathType Container)) {
    Write-Host "There is no folder at $Folder" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path -LiteralPath $envFile)) {
    Write-Host "No .env at $envFile — is this the right repository?" -ForegroundColor Red
    exit 1
}

$full = (Resolve-Path -LiteralPath $Folder).Path
$count = @(Get-ChildItem -LiteralPath $full -Recurse -File -ErrorAction SilentlyContinue |
           Select-Object -First 200).Count
Write-Host "Folder   $full"
Write-Host ("Contains {0}{1} file(s)" -f $count, $(if ($count -ge 200) { '+' } else { '' }))

if ($count -eq 0) {
    Write-Host "  Nothing in it. Mounting anyway; add folders once there are some." -ForegroundColor Yellow
}

# Kept beside the old one rather than over it: this file holds the keys to the
# database and the Drive credential, and a bad edit is not something to discover
# later.
$backup = "$envFile.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item -LiteralPath $envFile -Destination $backup
Write-Host "Saved the old .env as $(Split-Path -Leaf $backup)" -ForegroundColor DarkGray

$lines = Get-Content -LiteralPath $envFile
if ($lines -match '^MEDIA_HOST_PATH=') {
    $lines = $lines -replace '^MEDIA_HOST_PATH=.*', "MEDIA_HOST_PATH=$full"
} else {
    $lines += "MEDIA_HOST_PATH=$full"
}
Set-Content -LiteralPath $envFile -Value $lines -Encoding utf8

Write-Host "Restarting the server..." -ForegroundColor Cyan
$ErrorActionPreference = 'Continue'   # compose writes progress to stderr
docker compose --project-directory $repo up -d --force-recreate api

Write-Host ""
Write-Host "Done. Open Settings — the folders inside $full are listed there," -ForegroundColor Green
Write-Host "and each can be added and scanned." -ForegroundColor Green
