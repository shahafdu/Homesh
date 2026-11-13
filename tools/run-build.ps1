<#
.SYNOPSIS
    Builds the tasks in docs/design one after another, without anyone watching.

.DESCRIPTION
    For each task in tools/autobuild/tasks.json, in order:

      1. a Claude session of the task's model builds it (tools/autobuild/implement.md);
      2. this script checks for itself that the work was pushed and CI is green;
      3. an Opus session reviews it and fixes what it finds (tools/autobuild/review.md);
      4. the script checks CI again, and only then moves on.

    A task that fails twice is tried once more by Opus, then set aside.

    Some tasks need something only the owner has: a file, an approved mockup,
    an answer to a question. Those are skipped, not guessed at. Everything the
    owner is being asked for is written to .local\build\NEEDS-YOU.md, and a
    notification is shown. When nothing else can be built the script waits and
    looks again every few minutes.

    It uses the Claude subscription this PC is signed in to. ANTHROPIC_API_KEY
    is removed from the script's own environment so that nothing is billed to
    an API account by accident.

.PARAMETER Status
    Show where every task stands, write NEEDS-YOU.md, and exit.

.PARAMETER Only
    Build just this task id.

.PARAMETER Track
    Build only the tasks of one track, e.g. -Track notifications. The tracks
    are named in tools/autobuild/tasks.json.

.PARAMETER Once
    Exit when nothing can be built, rather than waiting for the owner.

.PARAMETER MaxTasks
    Stop after this many tasks have been finished.

.PARAMETER Tick
    Record that an owner entry is done, e.g. -Tick mockup-ai-page.

.PARAMETER Retry
    Put a failed task back in the queue, e.g. -Retry A5.

.EXAMPLE
    .\tools\run-build.ps1 -Status
    .\tools\run-build.ps1
    .\tools\run-build.ps1 -Tick mockup-faces
#>
[CmdletBinding()]
param(
    [switch]$Status,
    [string]$Only = '',
    [string]$Track = '',
    [switch]$Once,
    [int]$MaxTasks = 0,
    [string]$Tick = '',
    [string]$Retry = ''
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$buildDir   = Join-Path $repo '.local\build'
$logDir     = Join-Path $buildDir 'logs'
$helpDir    = Join-Path $buildDir 'help'
$reviewDir  = Join-Path $buildDir 'review'
$stateFile  = Join-Path $buildDir 'state.json'
$ownerFile  = Join-Path $buildDir 'owner.json'
$needsFile  = Join-Path $buildDir 'NEEDS-YOU.md'
$lockFile   = Join-Path $buildDir 'running.lock'
$runLog     = Join-Path $buildDir 'run.log'
$pauseFile  = Join-Path $buildDir 'PAUSED.txt'
$planFile   = Join-Path $PSScriptRoot 'autobuild\tasks.json'
$settings   = Join-Path $PSScriptRoot 'autobuild\settings.json'

foreach ($d in @($buildDir, $logDir, $helpDir, $reviewDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force $d | Out-Null }
}

$WaitMinutes      = 5      # between looks, while waiting for the owner
$LimitWaitMinutes = 30     # after the subscription says its limit is reached
$LimitRetries     = 48     # a day of waiting, then give up on that step
$SessionMinutes   = 180    # one Claude session may not run longer than this

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

function Write-Log([string]$text) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $text
    Write-Host $line
    Add-Content -Path $runLog -Value $line -Encoding utf8
}

function Read-Json([string]$path, $default) {
    if (-not (Test-Path $path)) { return $default }
    try { return (Get-Content $path -Raw -Encoding utf8 | ConvertFrom-Json) }
    catch { return $default }
}

function Write-Json([string]$path, $value) {
    $tmp = "$path.tmp"
    $value | ConvertTo-Json -Depth 8 | Out-File -FilePath $tmp -Encoding utf8
    Move-Item -Force $tmp $path
}

function Get-Prop($object, [string]$name) {
    if ($null -eq $object) { return $null }
    $p = $object.PSObject.Properties[$name]
    if ($null -eq $p) { return $null }
    return $p.Value
}

function Set-Prop($object, [string]$name, $value) {
    if ($null -eq $object.PSObject.Properties[$name]) {
        $object | Add-Member -NotePropertyName $name -NotePropertyValue $value
    } else {
        $object.$name = $value
    }
}

function Show-Notice([string]$title, [string]$text) {
    # A balloon from the notification area. Never allowed to stop the build.
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $icon = New-Object System.Windows.Forms.NotifyIcon
        $icon.Icon = [System.Drawing.SystemIcons]::Information
        $icon.Visible = $true
        $icon.ShowBalloonTip(20000, $title, $text, [System.Windows.Forms.ToolTipIcon]::Info)
        Start-Sleep -Seconds 6
        $icon.Dispose()
    } catch { }
}

# ---------------------------------------------------------------------------
# The plan, the state, and what the owner has supplied
# ---------------------------------------------------------------------------

$plan = Read-Json $planFile $null
if ($null -eq $plan) { throw "Cannot read $planFile" }

$state = Read-Json $stateFile ([pscustomobject]@{})
$owner = Read-Json $ownerFile ([pscustomobject]@{})

function Get-TaskState([string]$id) {
    $s = Get-Prop $state $id
    if ($null -eq $s) {
        $s = [pscustomobject]@{ status = 'todo'; attempts = 0; note = ''; finished = '' }
        Set-Prop $state $id $s
    }
    return $s
}

function Save-State { Write-Json $stateFile $state }

function Test-EnvValue([string]$name) {
    # Whether .env sets it to something. The value is never read into a variable
    # that is printed or logged.
    $envFile = Join-Path $repo '.env'
    if (-not (Test-Path $envFile)) { return $false }
    foreach ($line in Get-Content $envFile) {
        if ($line -match ('^\s*' + [regex]::Escape($name) + '\s*=\s*(\S+)')) { return $true }
    }
    return $false
}

function Test-Input([string]$name) {
    $def = Get-Prop $plan.inputs $name
    if ($null -eq $def) { return $true }
    switch ($def.check) {
        'file' { return (Test-Path (Join-Path $repo $def.path)) }
        'env'  { return (Test-EnvValue $def.name) }
        'tick' { return ((Get-Prop $owner $name) -eq $true) }
        default { return $false }
    }
}

function Get-Blockers($task) {
    # Why this task cannot start now. Empty means it can.
    $why = @()
    foreach ($n in @($task.needs)) {
        if ((Get-TaskState $n).status -ne 'done') { $why += "task $n" }
    }
    foreach ($i in @($task.inputs)) {
        if (-not (Test-Input $i)) { $why += "you: $i" }
    }
    $s = Get-TaskState $task.id
    if ($s.status -eq 'asked') {
        if (-not (Test-Path (Join-Path $helpDir ($task.id + '.answer.md')))) { $why += 'you: an answer' }
    }
    return $why
}

function Write-NeedsYou {
    $lines = @()
    $lines += '# Homesh build: what is waiting for you'
    $lines += ''
    $lines += ('Written {0} by tools\run-build.ps1. It is rewritten on every pass.' -f (Get-Date -Format 'yyyy-MM-dd HH:mm'))
    $lines += ''
    $count = 0

    # 1. Entries only the owner can supply.
    $wanted = @{}
    foreach ($t in $plan.tasks) {
        if ((Get-TaskState $t.id).status -eq 'done') { continue }
        foreach ($i in @($t.inputs)) {
            if (-not (Test-Input $i)) {
                if (-not $wanted.ContainsKey($i)) { $wanted[$i] = @() }
                $wanted[$i] += $t.id
            }
        }
    }
    if ($wanted.Count -gt 0) {
        $lines += '## Entries only you can supply'
        $lines += ''
        foreach ($name in ($wanted.Keys | Sort-Object)) {
            $def = Get-Prop $plan.inputs $name
            $lines += ('### {0}' -f $name)
            $lines += ''
            $lines += $def.ask
            $lines += ''
            $lines += ('Waiting on it: {0}' -f (($wanted[$name] | Sort-Object -Unique) -join ', '))
            if ($def.check -eq 'tick') {
                $lines += ''
                $lines += ('When done:  .\tools\run-build.ps1 -Tick {0}' -f $name)
            }
            $lines += ''
            $count++
        }
    }

    # 2. Questions a build session wrote down.
    $asked = @($plan.tasks | Where-Object { (Get-TaskState $_.id).status -eq 'asked' })
    if ($asked.Count -gt 0) {
        $lines += '## Questions from the build'
        $lines += ''
        foreach ($t in $asked) {
            $q = Join-Path $helpDir ($t.id + '.md')
            $a = Join-Path $helpDir ($t.id + '.answer.md')
            $lines += ('### {0} - {1}' -f $t.id, $t.title)
            $lines += ''
            if (Test-Path $q) { $lines += (Get-Content $q -Encoding utf8) } else { $lines += '(the session did not write its question down)' }
            $lines += ''
            if (Test-Path $a) {
                $lines += 'Answered. It will be tried again on the next pass.'
            } else {
                $lines += ('To answer: write your answer in  .local\build\help\{0}.answer.md' -f $t.id)
            }
            $lines += ''
            $count++
        }
    }

    # 3. Tasks set aside.
    $failed = @($plan.tasks | Where-Object { (Get-TaskState $_.id).status -eq 'failed' })
    if ($failed.Count -gt 0) {
        $lines += '## Set aside after failing'
        $lines += ''
        foreach ($t in $failed) {
            $s = Get-TaskState $t.id
            $lines += ('- **{0} - {1}**: {2}' -f $t.id, $t.title, $s.note)
            $lines += ('  Logs: .local\build\logs\{0}-*.txt   Try again: .\tools\run-build.ps1 -Retry {0}' -f $t.id)
            $count++
        }
        $lines += ''
    }

    # 4. What to do with what was built.
    $after = @($plan.tasks | Where-Object { (Get-TaskState $_.id).status -eq 'done' -and (Get-Prop $_ 'afterwards') })
    if ($after.Count -gt 0) {
        $lines += '## Built, and needs a step from you to start working'
        $lines += ''
        foreach ($t in $after) {
            $lines += ('- **{0} - {1}** (finished {2}): {3}' -f $t.id, $t.title, (Get-TaskState $t.id).finished, $t.afterwards)
        }
        $lines += ''
    }

    if ($count -eq 0) { $lines += 'Nothing is waiting for you.'; $lines += '' }

    $done = @($plan.tasks | Where-Object { (Get-TaskState $_.id).status -eq 'done' }).Count
    $lines += ('Progress: {0} of {1} tasks done.' -f $done, @($plan.tasks).Count)
    $lines | Out-File -FilePath $needsFile -Encoding utf8
    return $count
}

function Test-BuildRunning {
    # Whether the run that wrote the lock file is still alive. The lock holds a
    # process id and that process's start time: after a restart of the PC the
    # same id is soon handed to some other program, and the id alone would
    # then say a build was running for ever.
    if (-not (Test-Path $lockFile)) { return $false }
    $parts = ([string](Get-Content $lockFile -ErrorAction SilentlyContinue | Select-Object -First 1)).Split('|')
    if ($parts.Count -lt 2) { return $false }
    try {
        $proc = Get-Process -Id ([int]$parts[0]) -ErrorAction Stop
        return ([string]$proc.StartTime.Ticks -eq $parts[1])
    } catch { return $false }
}

function Test-InScope($task) {
    # Whether this run is meant to build this task.
    if ($Only -ne '' -and $task.id -ne $Only) { return $false }
    if ($Track -ne '' -and (Get-Prop $task 'track') -ne $Track) { return $false }
    return $true
}

function Show-Status {
    Write-Host ''
    $running = Test-BuildRunning
    if ($running) { Write-Host '  A build is running now.' } else { Write-Host '  No build is running.' }
    if (Test-Path $pauseFile) {
        if ($running) { Write-Host ('  PAUSED: ' + ((Get-Content $pauseFile) -join ' ')) }
        else { Write-Host ('  It stopped while paused: ' + ((Get-Content $pauseFile) -join ' ')) }
    }
    $half = @($plan.tasks | Where-Object { $st = (Get-TaskState $_.id).status; $st -eq 'building' -or $st -eq 'review' })
    if (-not $running -and $half.Count -gt 0) {
        Write-Host ('  Stopped in the middle of: ' + (($half | ForEach-Object { $_.id }) -join ', ') + '. Running the script again picks it up where it was.')
    }
    Write-Host ''
    $lastTrack = ''
    foreach ($t in $plan.tasks) {
        if ($Track -ne '' -and (Get-Prop $t 'track') -ne $Track) { continue }
        $tr = [string](Get-Prop $t 'track')
        if ($tr -ne $lastTrack) { Write-Host ('  -- ' + $tr + ' --'); $lastTrack = $tr }
        $s = Get-TaskState $t.id
        $why = ''
        if ($s.status -ne 'done' -and $s.status -ne 'failed') {
            $b = @(Get-Blockers $t)
            if ($b.Count -gt 0) { $why = 'waits for ' + ($b -join ', ') } else { $why = 'ready' }
        }
        if ($s.status -eq 'failed') { $why = $s.note }
        Write-Host ('  {0,-4} {1,-7} {2,-6} {3}  {4}' -f $t.id, $s.status, $t.model, $t.title, $why)
    }
    Write-Host ''
}

# ---------------------------------------------------------------------------
# Git and CI, checked by this script rather than taken on a session's word
# ---------------------------------------------------------------------------

function Get-Head { return (git rev-parse HEAD).Trim() }

function Test-TreeClean { return [string]::IsNullOrWhiteSpace((git status --porcelain | Out-String)) }

function Test-Pushed {
    git fetch origin main --quiet 2>$null
    return ((git rev-parse HEAD).Trim() -eq (git rev-parse origin/main).Trim())
}

function Test-CiGreen {
    & (Join-Path $PSScriptRoot 'verify-ci.ps1') *> (Join-Path $logDir 'last-ci.txt')
    return ($LASTEXITCODE -eq 0)
}

# ---------------------------------------------------------------------------
# One Claude session
# ---------------------------------------------------------------------------

function Invoke-Claude([string]$model, [string]$prompt, [string]$logName) {
    # Returns the session's final text, or $null when it could not be run.
    $logPath = Join-Path $logDir ($logName + '.txt')
    $promptPath = Join-Path $logDir ($logName + '.prompt.txt')
    $prompt | Out-File -FilePath $promptPath -Encoding utf8

    for ($try = 0; $try -le $LimitRetries; $try++) {
        $job = Start-Job -ArgumentList $repo, $promptPath, $model, $settings -ScriptBlock {
            param($repo, $promptPath, $model, $settings)
            Set-Location $repo
            Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue
            Remove-Item Env:ANTHROPIC_AUTH_TOKEN -ErrorAction SilentlyContinue
            Get-Content $promptPath -Raw -Encoding utf8 |
                & claude -p --model $model --permission-mode auto --settings $settings --output-format json 2>&1
            "EXIT:$LASTEXITCODE"
        }
        $finished = Wait-Job $job -Timeout ($SessionMinutes * 60)
        if ($null -eq $finished) {
            Stop-Job $job; Remove-Job $job -Force
            'TIMEOUT' | Out-File -FilePath $logPath -Encoding utf8
            Write-Log "    session ran past $SessionMinutes minutes and was stopped"
            return $null
        }
        $raw = (Receive-Job $job | Out-String)
        Remove-Job $job -Force
        $raw | Out-File -FilePath $logPath -Encoding utf8

        $text = $raw
        $isError = ($raw -notmatch 'EXIT:0')
        foreach ($line in ($raw -split "`n")) {
            $trim = $line.Trim()
            if ($trim.StartsWith('{') -and $trim.Contains('"result"')) {
                try {
                    $parsed = $trim | ConvertFrom-Json
                    if ($null -ne (Get-Prop $parsed 'result')) { $text = [string]$parsed.result }
                    if ((Get-Prop $parsed 'is_error') -eq $true) { $isError = $true }
                } catch { }
            }
        }

        if ($isError -and ($raw -match '(?i)usage limit|rate limit|limit reached|limit will reset|overloaded')) {
            Write-Log "    the subscription's limit was reached in the middle of $logName; waiting $LimitWaitMinutes minutes, then carrying on"
            if (-not (Test-Path $pauseFile)) {
                Show-Notice 'Homesh build is paused' "Claude's usage limit was reached during $logName. It carries on by itself when the limit resets."
            }
            ('since {0}, during {1}: the usage limit was reached. Trying again every {2} minutes.' -f (Get-Date -Format 'yyyy-MM-dd HH:mm'), $logName, $LimitWaitMinutes) |
                Out-File -FilePath $pauseFile -Encoding ascii
            # What the cut-off session had written stays in the working tree.
            # The session that takes over is told to look before it starts.
            ($prompt + "`n`nNOTE: an earlier session working on this was cut off part-way by a usage limit. " +
             "Its unfinished work may be in the working tree, and it may have committed or pushed some of it. " +
             "Run git status, git diff and git log first, then carry on from there. Do not start over.") |
                Out-File -FilePath $promptPath -Encoding utf8
            Start-Sleep -Seconds ($LimitWaitMinutes * 60)
            continue
        }
        if (Test-Path $pauseFile) {
            Remove-Item $pauseFile -ErrorAction SilentlyContinue
            Write-Log '    the limit has reset; carrying on'
        }
        if ($isError -and ($raw -match '(?i)not logged in|please run /login|invalid api key|authentication')) {
            Write-Log '    Claude is not signed in on this PC. Run "claude" once and sign in.'
            Show-Notice 'Homesh build stopped' 'Claude is not signed in on this PC.'
            throw 'Claude is not signed in.'
        }
        return $text
    }
    return $null
}

function Expand-Prompt([string]$template, $task, [string]$range, [string]$extra) {
    $text = Get-Content (Join-Path $PSScriptRoot ('autobuild\' + $template)) -Raw -Encoding utf8
    $text = $text.Replace('{ID}', $task.id).Replace('{TITLE}', $task.title).Replace('{DOC}', $task.doc)
    $text = $text.Replace('{RANGE}', $range).Replace('{EXTRA}', $extra)
    return $text
}

# ---------------------------------------------------------------------------
# One task, start to finish
# ---------------------------------------------------------------------------

function Invoke-Task($task) {
    $s = Get-TaskState $task.id
    $interrupted = ($s.status -eq 'building' -or $s.status -eq 'review')
    if (-not (Test-TreeClean) -and -not $interrupted) {
        throw 'The working tree has uncommitted changes. Commit or remove them first.'
    }
    if ((git rev-parse --abbrev-ref HEAD).Trim() -ne 'main') { throw 'Not on the main branch.' }

    $start = Get-Prop $s 'start'
    if ([string]::IsNullOrWhiteSpace($start)) {
        $start = Get-Head
        Set-Prop $s 'start' $start
    }

    $extra = ''
    $answer = Join-Path $helpDir ($task.id + '.answer.md')
    $question = Join-Path $helpDir ($task.id + '.md')
    if (Test-Path $answer) {
        $extra = "An earlier session stopped and asked the owner for help. Its question:`n`n" +
                 (Get-Content $question -Raw -ErrorAction SilentlyContinue) +
                 "`n`nThe owner's answer:`n`n" + (Get-Content $answer -Raw)
    }
    if ($interrupted) {
        # The script itself was stopped while this task was being worked on.
        # That attempt is taken up again, not counted as a failure.
        Write-Log ("  {0} was interrupted part-way; taking it up again" -f $task.id)
        $s.attempts = [Math]::Max(0, [int]$s.attempts - 1)
        $extra += "`n`nNOTE: an earlier session working on this task was interrupted part-way. " +
                  "Its unfinished work may be in the working tree, and it may have committed or pushed some of it. " +
                  "Run git status, git diff and git log first, then carry on from there. Do not start over."
    }
    $lastReview = Join-Path $reviewDir ($task.id + '.md')
    if ($s.attempts -gt 0 -and (Test-Path $lastReview)) {
        $extra += "`n`nAn earlier attempt at this task was reviewed and not accepted. The review:`n`n" +
                  (Get-Content $lastReview -Raw)
    }

    while ($true) {
        $s.attempts = [int]$s.attempts + 1
        $model = $task.model
        # Two tries by the task's own model, then one by the reviewer's.
        if ($s.attempts -ge 3) { $model = $plan.reviewer }
        if ($s.attempts -gt 3) {
            $s.status = 'failed'
            if ([string]::IsNullOrWhiteSpace($s.note)) { $s.note = 'three attempts did not produce an accepted result' }
            Save-State
            Write-Log ("  {0} set aside: {1}" -f $task.id, $s.note)
            Show-Notice 'Homesh build: a task was set aside' ("{0} - {1}" -f $task.id, $task.title)
            return $false
        }
        $s.status = 'building'; Save-State
        Write-Log ("  {0} attempt {1} with {2}" -f $task.id, $s.attempts, $model)

        $reply = Invoke-Claude $model (Expand-Prompt 'implement.md' $task '' $extra) ("{0}-{1}-build" -f $task.id, $s.attempts)

        if (-not (Test-TreeClean)) {
            # Whatever the session says, half-made changes do not stay.
            Write-Log '    the session left uncommitted changes; putting them aside'
            git stash push --include-untracked -m ("run-build {0} attempt {1}" -f $task.id, $s.attempts) | Out-Null
        }

        if ($null -ne $reply -and $reply -match 'RESULT:\s*HELP') {
            $s.status = 'asked'; $s.attempts = [int]$s.attempts - 1
            Remove-Item $answer -ErrorAction SilentlyContinue
            Save-State
            Write-Log ("  {0} is asking for your help" -f $task.id)
            Show-Notice 'Homesh build needs you' ("{0} has a question. See .local\build\NEEDS-YOU.md" -f $task.id)
            return $false
        }

        $problem = ''
        if ($null -eq $reply)                         { $problem = 'the session did not finish' }
        elseif ($reply -notmatch 'RESULT:\s*DONE')    { $problem = 'the session did not report it as done' }
        elseif ((Get-Head) -eq $start)                { $problem = 'nothing was committed' }
        elseif (-not (Test-Pushed))                   { $problem = 'the commits were not pushed' }
        elseif (-not (Test-CiGreen))                  { $problem = 'CI is not green' }

        if ($problem -ne '') {
            $s.note = $problem; Save-State
            Write-Log "    not accepted: $problem"
            $extra = "An earlier attempt at this task ended badly: $problem. Look at the state of the repository and CI first, and finish or repair what is there."
            continue
        }

        # Review, by the reviewer's model, with the right to fix.
        $s.status = 'review'; Save-State
        Write-Log ("  {0} review with {1}" -f $task.id, $plan.reviewer)
        $range = "$start..HEAD"
        $verdict = Invoke-Claude $plan.reviewer (Expand-Prompt 'review.md' $task $range '') ("{0}-{1}-review" -f $task.id, $s.attempts)

        if (-not (Test-TreeClean)) {
            git stash push --include-untracked -m ("run-build {0} review {1}" -f $task.id, $s.attempts) | Out-Null
        }

        $problem = ''
        if ($null -eq $verdict)                        { $problem = 'the review did not finish' }
        elseif ($verdict -notmatch 'VERDICT:\s*PASS')  { $problem = 'the review did not pass it' }
        elseif (-not (Test-Pushed))                    { $problem = "the review's fixes were not pushed" }
        elseif (-not (Test-CiGreen))                   { $problem = 'CI is not green after the review' }

        if ($problem -ne '') {
            $s.note = $problem; Save-State
            Write-Log "    not accepted: $problem"
            $extra = "An earlier attempt at this task was built and then not accepted: $problem."
            if (Test-Path $lastReview) { $extra += " The review:`n`n" + (Get-Content $lastReview -Raw) }
            continue
        }

        $s.status = 'done'; $s.note = ''; $s.finished = (Get-Date -Format 'yyyy-MM-dd HH:mm')
        Set-Prop $s 'commit' (Get-Head)
        Save-State
        Write-Log ("  {0} done at {1}" -f $task.id, (Get-Head).Substring(0, 7))
        return $true
    }
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if ($Tick -ne '') {
    if ($null -eq (Get-Prop $plan.inputs $Tick)) { throw "No owner entry is called '$Tick'. See .\tools\run-build.ps1 -Status" }
    Set-Prop $owner $Tick $true
    Write-Json $ownerFile $owner
    Write-Host "Recorded: $Tick"
    Write-NeedsYou | Out-Null
    return
}

if ($Retry -ne '') {
    $s = Get-TaskState $Retry
    $s.status = 'todo'; $s.attempts = 0; $s.note = ''
    Save-State
    Write-Host "$Retry is back in the queue."
    return
}

if ($Status) {
    Show-Status
    $n = Write-NeedsYou
    Write-Host ("{0} thing(s) waiting for you. See .local\build\NEEDS-YOU.md" -f $n)
    return
}

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { throw 'The claude command is not on the PATH.' }

if (Test-BuildRunning) { throw 'Another run-build.ps1 is already running.' }
('{0}|{1}' -f $PID, (Get-Process -Id $PID).StartTime.Ticks) | Out-File -FilePath $lockFile -Encoding ascii

try {
    Remove-Item $pauseFile -ErrorAction SilentlyContinue
    Write-Log 'run-build started'
    $finishedThisRun = 0
    $lastAsked = -1

    while ($true) {
        $owner = Read-Json $ownerFile ([pscustomobject]@{})
        $next = $null
        foreach ($t in $plan.tasks) {
            if (-not (Test-InScope $t)) { continue }
            $s = Get-TaskState $t.id
            if ($s.status -eq 'done' -or $s.status -eq 'failed') { continue }
            if (@(Get-Blockers $t).Count -eq 0) { $next = $t; break }
        }

        $waiting = Write-NeedsYou

        if ($null -ne $next) {
            Write-Log ("{0} - {1}" -f $next.id, $next.title)
            if (Invoke-Task $next) {
                $finishedThisRun++
                if ($MaxTasks -gt 0 -and $finishedThisRun -ge $MaxTasks) { break }
            }
            continue
        }

        $left = @($plan.tasks | Where-Object { $st = (Get-TaskState $_.id).status; $st -ne 'done' -and $st -ne 'failed' })
        $left = @($left | Where-Object { Test-InScope $_ })
        if ($left.Count -eq 0) {
            Write-Log 'Nothing left to build.'
            Show-Notice 'Homesh build finished' 'Every task is done or set aside. See .local\build\NEEDS-YOU.md'
            break
        }

        if ($waiting -ne $lastAsked) {
            Write-Log ("Nothing can be built until you supply something: {0} item(s) in .local\build\NEEDS-YOU.md" -f $waiting)
            Show-Notice 'Homesh build is waiting for you' ("{0} item(s). See .local\build\NEEDS-YOU.md" -f $waiting)
            $lastAsked = $waiting
        }
        if ($Once) { break }
        Start-Sleep -Seconds ($WaitMinutes * 60)
    }
}
finally {
    Write-NeedsYou | Out-Null
    Remove-Item $lockFile -ErrorAction SilentlyContinue
    Write-Log 'run-build stopped'
}
