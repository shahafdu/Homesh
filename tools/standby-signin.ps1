<#
.SYNOPSIS
    Get a one-time code to sign in to the standby.

.DESCRIPTION
    The standby keeps passkeys of its own, because a passkey belongs to an
    address, and it starts with none. This asks the standby -- over the
    tailnet, with this PC's key -- for an eight-character code that signs one
    account in once, within ten minutes.

    Open the standby's address on the phone, choose "Use a code", type it in.
    Then Settings -> "Add a passkey to this device", and the code is never
    needed again.

    The code is shown here, on this screen, and nowhere else.

.PARAMETER Handle
    Whose account. The owner's when left out.

.EXAMPLE
    .\tools\standby-signin.ps1
#>
[CmdletBinding()]
param(
    [string]$Handle = ""
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

$key = "$repo\.local\standby_ed25519"
if (-not (Test-Path $key)) { throw "The SSH key for the standby is missing: $key" }

# The standby's name, derived from this PC's the same way the deploy script
# does it: same tailnet, different host. Nothing about it is in the repository.
$originLine = Get-Content "$repo\.env" | Where-Object { $_ -match '^PUBLIC_ORIGIN=https://[^.]+\.(.+)$' } | Select-Object -First 1
if (-not $originLine) { throw "PUBLIC_ORIGIN in .env is not a tailnet address." }
$null = $originLine -match '^PUBLIC_ORIGIN=https://[^.]+\.(.+)$'
$standby = "homesh-standby.$($Matches[1])"

# Only letters, digits, dash, dot and underscore: it is passed to a shell on the
# other machine, and a handle never needs anything else.
if ($Handle -and $Handle -notmatch '^[A-Za-z0-9._-]+$') { throw "That is not an account name." }

$command = "cd /opt/homesh && sudo docker compose -f docker-compose.standby.yml exec -T api python -m app.signin_code $Handle"

Write-Host ""
Write-Host "Asking the standby for a sign-in code..." -ForegroundColor Cyan
$ErrorActionPreference = 'Continue'
$out = & ssh -i $key -o BatchMode=yes -o ConnectTimeout=20 "ubuntu@$standby" $command 2>&1
$ok = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = 'Stop'

Write-Host ""
if ($ok) {
    $out | ForEach-Object { Write-Host "  $_" -ForegroundColor Green }
    Write-Host ""
    Write-Host "  On the phone: open https://$standby" -ForegroundColor White
    Write-Host "  choose 'Use a code', and type it in. Then Settings ->" -ForegroundColor White
    Write-Host "  'Add a passkey to this device' so it is never needed again." -ForegroundColor White
} else {
    Write-Host "  Could not get a code:" -ForegroundColor Red
    $out | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    # Only when the standby was never reached. An answer from it -- "no account
    # called ..." -- means the connection was fine and the name was not.
    if (-not ("$out" -match 'Cannot issue a code')) {
        Write-Host "  Is this PC connected to Tailscale?" -ForegroundColor Red
    }
}
Write-Host ""
