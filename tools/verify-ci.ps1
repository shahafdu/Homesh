<#
.SYNOPSIS
    Wait for CI on the pushed commit and report every job.

.DESCRIPTION
    Pushing is not finishing. CI has been red on main more than once while the
    work was described as done, because nobody looked - and the job that was red
    was the one guarding what must never leave this repository.

    Run this after every push. It blocks until the run for HEAD completes, then
    names each job and exits non-zero if any failed, so it can be trusted in a
    script rather than read hopefully.

.EXAMPLE
    .\tools\verify-ci.ps1
#>
[CmdletBinding()]
param(
    [int] $TimeoutMinutes = 20,
    # A specific commit rather than HEAD. Only for checking this script itself:
    # a tool that reports failure has to be watched failing at least once, and
    # pointing it at HEAD can only ever show the green half.
    [string] $Sha
)

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
$sha = if ($Sha) { $Sha } else { (git -C $repo rev-parse HEAD).Trim() }

# The token Windows already holds for pushing. gh is installed but not logged in
# and its token lacks read:org, so the REST API is the reliable route.
# Through a file rather than the pipeline. PowerShell rewrites line endings on
# the way to a native command, and git credential refuses the result outright:
# "refusing to work with credential missing protocol field".
$ask = [IO.Path]::GetTempFileName()
[IO.File]::WriteAllText($ask, "protocol=https`nhost=github.com`n`n")
$answer = & cmd /c "git credential fill < `"$ask`"" 2>$null
Remove-Item $ask -ErrorAction SilentlyContinue

$token = ($answer | Where-Object { $_ -like 'password=*' } | Select-Object -First 1)
if ($token) { $token = $token.Substring(9) }
if (-not $token) { Write-Host "No GitHub credential found." -ForegroundColor Red; exit 2 }

$headers = @{ Authorization = "Bearer $token"; 'User-Agent' = 'homesh-verify-ci' }
$api = 'https://api.github.com/repos/shahafdu/Homesh/actions/runs'

Write-Host "Waiting for CI on $($sha.Substring(0,7))..." -ForegroundColor Cyan
$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
$run = $null

while ((Get-Date) -lt $deadline) {
    $runs = (Invoke-RestMethod -Uri "$api`?per_page=15" -Headers $headers).workflow_runs
    $run = $runs | Where-Object { $_.head_sha -eq $sha } | Select-Object -First 1
    if ($run -and $run.status -eq 'completed') { break }
    if (-not $run) { Write-Host "  no run for this commit yet..." -ForegroundColor DarkGray }
    else { Write-Host "  $($run.status)..." -ForegroundColor DarkGray }
    Start-Sleep -Seconds 15
}

if (-not $run) { Write-Host "No CI run appeared for this commit." -ForegroundColor Red; exit 2 }
if ($run.status -ne 'completed') { Write-Host "CI still running after $TimeoutMinutes min." -ForegroundColor Yellow; exit 2 }

$jobs = (Invoke-RestMethod -Uri "$($run.jobs_url)?per_page=50" -Headers $headers).jobs
$failed = 0
foreach ($j in $jobs) {
    $colour = if ($j.conclusion -eq 'success') { 'Green' } else { 'Red' }
    Write-Host ("  {0,-9} {1}" -f $j.conclusion, $j.name) -ForegroundColor $colour
    if ($j.conclusion -ne 'success') {
        $failed++
        foreach ($s in $j.steps) {
            if ($s.conclusion -eq 'failure') { Write-Host "       failed step: $($s.name)" -ForegroundColor Red }
        }
    }
}

if ($failed -gt 0) {
    Write-Host "CI is RED - $failed job(s) failed. $($run.html_url)" -ForegroundColor Red
    exit 1
}
Write-Host "CI is green." -ForegroundColor Green
