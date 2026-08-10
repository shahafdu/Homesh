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
