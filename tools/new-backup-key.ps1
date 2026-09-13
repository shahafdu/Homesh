<#
    Generate the key that encrypts backups before they leave the house.

    Shown once, on purpose. The key is written into .env so the server can use
    it, and printed here so it can be put somewhere the server is not -- which
    is the whole point of it. A key that exists only on the machine being backed
    up protects the backups from a stranger and not from a fire.

    Losing it is not recoverable. There is no reset, no recovery copy and no way
    to read a backup without it; that is what makes storing them on somebody
    else's disk acceptable in the first place.
#>
[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repo '.env'

if (-not (Test-Path $envFile)) {
    Write-Host "No .env in $repo. Copy .env.example to .env first." -ForegroundColor Red
    exit 1
}

$lines = @(Get-Content $envFile)
$existing = $lines | Where-Object { $_ -match '^\s*BACKUP_KEY\s*=\s*\S' }

if ($existing -and -not $Force) {
    Write-Host ""
    Write-Host "A backup key is already set." -ForegroundColor Yellow
    Write-Host "Replacing it makes every existing off-site backup unreadable," -ForegroundColor Yellow
    Write-Host "because they were encrypted with the old one." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Re-run with -Force if that is what you intend."
    exit 1
}

# 32 bytes, urlsafe-base64, matching what the server expects.
$bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$key = [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_')

$updated = @()
$replaced = $false
foreach ($line in $lines) {
    if ($line -match '^\s*BACKUP_KEY\s*=') {
        $updated += "BACKUP_KEY=$key"
        $replaced = $true
    } else {
        $updated += $line
    }
}
if (-not $replaced) {
    $updated += ""
    $updated += "# Encrypts backups before they are sent off-site. Keep a copy of this"
    $updated += "# somewhere other than this machine, or an off-site backup is unreadable."
    $updated += "BACKUP_KEY=$key"
}

Set-Content -Path $envFile -Value $updated -Encoding utf8

Write-Host ""
Write-Host "  Your backup key" -ForegroundColor Cyan
Write-Host ""
Write-Host "    $key"
Write-Host ""
Write-Host "  Put this in your password manager now." -ForegroundColor Yellow
Write-Host "  It is in .env, which is on the machine being backed up -- so if that"
Write-Host "  machine is lost, this line is the only thing that can read the copies"
Write-Host "  that survived it."
Write-Host ""
Write-Host "  Restart Homesh for it to take effect:  .\tools\start-homesh.ps1 -Rebuild"
Write-Host ""
