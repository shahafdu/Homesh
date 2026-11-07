# Publish the phone app (and the TV app, when it moved) as a GitHub release.
#
#   .\tools\publish-android-release.ps1          # after building a new version
#   .\tools\publish-android-release.ps1 -DryRun  # say what it would publish
#
# Why this is a script on the PC and not a CI job: the APKs are signed with this
# machine's key (never committed), and Android refuses to install an update
# signed by another one. CI builds with a throwaway key to prove the build works;
# only a build from here can go onto a phone that already has the app.
#
# Why it matters at all: the release page is how a phone gets the app when the
# server cannot be reached -- the situation the app exists for. It stood at 1.3.0
# for seven weeks while phones updated from the server, because publishing was a
# step done by hand and nothing said it had been skipped. run-tests.ps1 now warns
# when the built phone app is newer than the latest release.
#
# Refuses to publish a build whose sources differ from HEAD, one that is not
# pushed, or one the APK scan finds anything private in. Does nothing when the
# version is already released.

param([switch]$DryRun)

$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo
$slug = 'shahafdu/Homesh'

function Built($app) {
    $json = Join-Path $repo "build\homesh-$app.json"
    $apk = Join-Path $repo "build\homesh-$app.apk"
    if (-not (Test-Path $json) -or -not (Test-Path $apk)) { return $null }
    $info = Get-Content $json -Raw | ConvertFrom-Json
    $manifest = if ($app -eq 'tv') { 'android\AndroidManifest.xml' } else { "android\AndroidManifest.$app.xml" }
    $declared = ([xml](Get-Content (Join-Path $repo $manifest) -Raw)).manifest.versionName
    [pscustomobject]@{ App = $app; Version = $info.versionName; Apk = $apk; Current = ($info.versionName -eq $declared) }
}

# Built from what is committed and pushed, or not published at all: a release
# must be reproducible from the tag it names.
$dirty = git status --porcelain -- android tools/build-tv-apk.sh
if ($dirty) { Write-Host "REFUSING: android/ has uncommitted changes. Commit, rebuild, then publish." -ForegroundColor Red; exit 1 }
git fetch -q origin main 2>$null
$head = (git rev-parse HEAD).Trim()
$pushed = git branch -r --contains $head 2>$null | Select-String 'origin/main'
if (-not $pushed) { Write-Host "REFUSING: HEAD is not on origin/main yet. Push first." -ForegroundColor Red; exit 1 }

$phone = Built 'phone'
if (-not $phone -or -not $phone.Current) {
    Write-Host "REFUSING: build\homesh-phone.apk is missing or older than its manifest. Run: `$env:APP='phone'; bash tools/build-tv-apk.sh" -ForegroundColor Red
    exit 1
}
$tv = Built 'tv'

# Piped from PowerShell 5.1, the request arrives with a byte-order mark that git
# reads as part of "protocol" and rejects; a file in plain ASCII does not.
$ask = [IO.Path]::GetTempFileName()
[IO.File]::WriteAllText($ask, "protocol=https`nhost=github.com`n`n", [Text.Encoding]::ASCII)
$token = ((cmd /c "git credential fill < `"$ask`"") | Where-Object { $_ -like 'password=*' }) -replace '^password=', ''
Remove-Item $ask
if (-not $token) { Write-Host "No GitHub token in the credential manager." -ForegroundColor Red; exit 1 }
$headers = @{ Authorization = "Bearer $token"; Accept = 'application/vnd.github+json' }

$releases = Invoke-RestMethod -Headers $headers "https://api.github.com/repos/$slug/releases?per_page=50"
$tag = "android-$($phone.Version)"
if ($releases | Where-Object { $_.tag_name -eq $tag }) {
    Write-Host "Homesh Connect $($phone.Version) is already released ($tag). Nothing to do." -ForegroundColor DarkGray
    exit 0
}
$tvReleased = $releases | ForEach-Object { $_.assets.name } | Where-Object { $tv -and $_ -eq "homesh-tv-$($tv.Version).apk" }
$withTv = $tv -and $tv.Current -and -not $tvReleased

& python tools/scan-apk.py $phone.Apk $(if ($withTv) { $tv.Apk })
if ($LASTEXITCODE -ne 0) { exit 1 }

# What changed for the apps, from the commits that touched them since the last
# release. Filtered through the private word list as well, since older messages
# predate it.
$previous = $releases | Where-Object { $_.tag_name -like 'android-*' } | Select-Object -First 1
$since = if ($previous) { $previous.target_commitish } else { '' }
$range = if ($since -and (git cat-file -t $since 2>$null)) { "$since..HEAD" } else { 'HEAD~30..HEAD' }
$words = @()
$wordsFile = Join-Path $repo '.local\private-words.txt'
if (Test-Path $wordsFile) { $words = @(Get-Content $wordsFile | Where-Object { $_ -and $_ -notmatch '^#' }) }
$changes = @(git log --format='%s' $range -- android | Where-Object {
    $line = $_; -not ($words | Where-Object { $line -match [regex]::Escape($_) })
} | ForEach-Object { "- $_" })

$name = "Homesh Connect $($phone.Version)" + $(if ($withTv) { " and TV app $($tv.Version)" } else { '' })
$body = (@(
    $name + '.'
    ''
    'Install over any earlier version: every build is signed with the same key. Once installed, Check for updates on the app''s own screen keeps it current from your server.'
    ''
    'Changes to the apps since the last release:'
    ''
) + $changes) -join "`n"

Write-Host "Publishing $name as $tag at $($head.Substring(0, 7))" -ForegroundColor Cyan
if ($DryRun) { Write-Host $body; exit 0 }

$release = Invoke-RestMethod -Method Post -Headers $headers "https://api.github.com/repos/$slug/releases" `
    -ContentType 'application/json' -Body (@{ tag_name = $tag; target_commitish = $head; name = $name; body = $body } | ConvertTo-Json)

$uploads = @(@{ Path = $phone.Apk; Name = "homesh-phone-$($phone.Version).apk" })
if ($withTv) {
    $uploads += @{ Path = $tv.Apk; Name = "homesh-tv-$($tv.Version).apk" }
} else {
    # Every release carries both apps, so the latest one is all anybody needs.
    # A TV app that did not change is the file already released, copied across
    # as it is -- the same bytes, the same signature -- rather than rebuilt.
    $carried = $releases | ForEach-Object { $_.assets } | Where-Object { $_.name -like 'homesh-tv-*.apk' } | Select-Object -First 1
    if ($carried) {
        $copy = Join-Path ([IO.Path]::GetTempPath()) $carried.name
        Invoke-WebRequest -UseBasicParsing -Uri $carried.browser_download_url -OutFile $copy
        $uploads += @{ Path = $copy; Name = $carried.name }
    }
}
foreach ($u in $uploads) {
    $asset = Invoke-RestMethod -Method Post -Headers $headers -ContentType 'application/vnd.android.package-archive' `
        -InFile $u.Path "https://uploads.github.com/repos/$slug/releases/$($release.id)/assets?name=$($u.Name)"
    Write-Host "  $($asset.name)  $($asset.size) bytes" -ForegroundColor DarkGray
}
Write-Host "Released: $($release.html_url)" -ForegroundColor Green
