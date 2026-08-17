<#
.SYNOPSIS
    Point Homesh at a new address, or back again.

.DESCRIPTION
    Changes PUBLIC_ORIGIN and RP_ID together, because they must agree: a passkey
    is bound to the origin it was created against, and the WebAuthn RP ID has to
    be that origin's host.

    That binding is the whole reason this script exists. Moving to a real
    hostname invalidates every passkey registered against the old one — not a
    bug, the point of the mechanism — so this prints the recovery route before it
    changes anything, and switching back restores the old passkeys exactly.

.EXAMPLE
    .\tools\set-origin.ps1 -Origin https://homesh.tailnet-name.ts.net

.EXAMPLE
    .\tools\set-origin.ps1 -Revert
#>
[CmdletBinding()]
param(
    # Full origin, scheme included. The RP ID is taken from its host.
    [string]$Origin,
    # Put back http://localhost:8080, which is what the first passkey was made against.
    [switch]$Revert
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repo '.env'

if (-not (Test-Path $envFile)) { throw "No .env at $envFile" }

if ($Revert) { $Origin = 'http://localhost:8080' }
if (-not $Origin) { throw "Give -Origin https://… or -Revert" }

try { $uri = [System.Uri]$Origin } catch { throw "That is not a URL: $Origin" }
if ($uri.Scheme -notin @('http', 'https')) { throw "Origin must be http or https" }

$rpId = $uri.Host
$origin = $uri.GetLeftPart([System.UriPartial]::Authority)

$lines = Get-Content $envFile
$before = ($lines | Where-Object { $_ -match '^RP_ID=' }) -replace '^RP_ID=', ''

Write-Host ""
Write-Host "  Origin : $origin" -ForegroundColor Cyan
Write-Host "  RP ID  : $rpId   (was: $before)" -ForegroundColor Cyan
Write-Host ""

if ($rpId -ne $before) {
    Write-Host "  Every passkey registered against '$before' will stop working." -ForegroundColor Yellow
    Write-Host "  That is how passkeys work, not a fault." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Before restarting, from a browser that is still signed in:" -ForegroundColor Yellow
    Write-Host "    Settings -> Use on another device  -> note the code" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Then at the new address: 'Use a code', then Settings ->" -ForegroundColor Yellow
    Write-Host "  'Add a passkey to this device'." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  If anything goes wrong:  .\tools\set-origin.ps1 -Revert" -ForegroundColor Yellow
    Write-Host "  which restores the old passkeys exactly." -ForegroundColor Yellow
    Write-Host ""
}

# Rewritten in place rather than appended: two PUBLIC_ORIGIN lines would leave
# the winner up to whichever the loader read last.
$updated = $lines |
    ForEach-Object {
        if ($_ -match '^PUBLIC_ORIGIN=') { "PUBLIC_ORIGIN=$origin" }
        elseif ($_ -match '^RP_ID=') { "RP_ID=$rpId" }
        else { $_ }
    }

if (-not ($updated | Where-Object { $_ -match '^PUBLIC_ORIGIN=' })) {
    $updated += "PUBLIC_ORIGIN=$origin"
}
if (-not ($updated | Where-Object { $_ -match '^RP_ID=' })) {
    $updated += "RP_ID=$rpId"
}

Set-Content -Path $envFile -Value $updated -Encoding utf8

Write-Host "  .env updated. Apply it with:" -ForegroundColor Green
Write-Host "    docker compose up -d" -ForegroundColor Green
Write-Host ""
