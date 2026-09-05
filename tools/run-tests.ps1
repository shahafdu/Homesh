<#
.SYNOPSIS
    Runs the server test suite against a dedicated test database.

.DESCRIPTION
    The suite truncates `users` and `sources` between tests, so it must never touch
    a working database. This script always points it at `homesh_test`, creating that
    database if needed. conftest.py additionally refuses any database whose name
    does not contain "test", so both layers have to fail before real data is at risk.

    Source is mounted into the container rather than copied, so there is no stale
    copy to catch you out.

.EXAMPLE
    .\tools\run-tests.ps1

.EXAMPLE
    .\tools\run-tests.ps1 -Filter test_search
#>
[CmdletBinding()]
param(
    # Passed to pytest -k
    [string]$Filter,
    [switch]$Verbose_
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

# Docker Desktop installs outside the default PATH on some setups.
$env:Path = "$([System.Environment]::GetEnvironmentVariable('Path','Machine'));$([System.Environment]::GetEnvironmentVariable('Path','User'))"

$envLine = Get-Content .env | Where-Object { $_ -match '^POSTGRES_PASSWORD=' } | Select-Object -First 1
if (-not $envLine) { throw "POSTGRES_PASSWORD not found in .env" }
$pw = $envLine.Split('=', 2)[1]

$testDb = 'homesh_test'

Write-Host "Ensuring $testDb exists..." -ForegroundColor Cyan
$exists = docker compose exec -T db psql -U homesh -d postgres -tAc `
    "SELECT 1 FROM pg_database WHERE datname='$testDb'"
if ($exists -notmatch '1') {
    docker compose exec -T db psql -U homesh -d postgres -c "CREATE DATABASE $testDb" | Out-Null
    Write-Host "  created" -ForegroundColor Green
} else {
    Write-Host "  present" -ForegroundColor Green
}

$pytestArgs = @('tests', '-q')
if ($Verbose_) { $pytestArgs = @('tests', '-vv') }
# -k takes a whole expression, so it must survive as one shell word.
if ($Filter) { $pytestArgs += @('-k', "'$Filter'") }

# The same check CI runs, run first and locally.
#
# This repository is public and describes a real house, so a private address in
# a tracked file is refused. Finding that out from a red build twenty minutes
# after pushing — which is how it was found twice — is the slowest possible way
# to learn it, and the check costs a grep.
#
# git grep exits 1 when it finds nothing, which is the good case here. In
# PowerShell 5.1 a native non-zero exit sets $? false and, with the stricter
# preference this script starts under, turns into a terminating error — so the
# preference is relaxed first and the exit code read deliberately.
$ErrorActionPreference = 'Continue'
# The hooks live in the repository but a clone does not use them until it is
# told to. Setting it here means a fresh machine is guarded the first time
# anybody runs the tests, rather than the first time somebody remembers.
if ((git config core.hooksPath) -ne 'tools/githooks') {
    git config core.hooksPath tools/githooks
    Write-Host "Wired up the commit and push guards (core.hooksPath)." -ForegroundColor DarkGray
}

Write-Host "Checking for private addresses and device ids..." -ForegroundColor Cyan

$pattern = '(10|192\.168|172\.(1[6-9]|2[0-9]|3[01]))\.[0-9]{1,3}\.[0-9]{1,3}'
$leaks = @(git grep -nIE $pattern -- . 2>$null)
$ids   = @(git grep -nIE 'uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}' -- . 2>$null)

if ($leaks.Count -gt 0 -or $ids.Count -gt 0) {
    Write-Host "  A tracked file names a private address or a device id:" -ForegroundColor Red
    ($leaks + $ids) | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
    Write-Host "  Use an RFC 5737 documentation address (192.0.2.x, 198.51.100.x)." -ForegroundColor Red
    exit 1
}
Write-Host "  clean" -ForegroundColor DarkGray

Write-Host "Running tests against $testDb..." -ForegroundColor Cyan

# Compose writes progress ("Container ... Running") to stderr, which PowerShell 5.1
# turns into terminating error records. The exit code is the thing that matters.
$ErrorActionPreference = 'Continue'

docker compose run --rm `
    --volume "${repo}/server:/app" `
    --env "DATABASE_URL=postgresql+psycopg://homesh:$pw@db:5432/$testDb" `
    --env "MEDIA_ROOTS=" `
    --entrypoint sh `
    api -c "pip install --quiet pytest pytest-asyncio httpx >/dev/null 2>&1 && python -m pytest $($pytestArgs -join ' ')"

exit $LASTEXITCODE
