# Where Homesh stands

Living status. Updated as things move; the point of it is that neither of us has
to reconstruct the state of a large, half-finished system from memory.

**Legend** — ✅ built and verified · 🟡 built, needs Shahaf to confirm ·
🔴 known broken · ⬜ not started · ⏳ waiting on Shahaf

Last updated: 21 September 2026 · 683 tests · 26 migrations · CI green
(verified with `tools/verify-ci.ps1`, not assumed)

---

## Needs Shahaf

The list to work from. Everything else can proceed without you.

| | What | Why it matters |
|---|---|---|
| ✅ | **The Oracle standby machine** | Created and running Homesh: Ampere A1, 1 OCPU / 6 GB (all the console offers as Always Free-eligible), Ubuntu 24.04 aarch64, 100 GB boot volume. `tools/deploy-standby.ps1` installs and upgrades it. It pulled the newest backup out of the bucket by itself and restored 131,302 items, reads all five Drive folders, shows the PC's own folders as offline, and refuses what must not be changed there. Not reachable from your phone yet -- that is the Tailscale row below |
| ✅ | **Tailscale: the dead-end rule and a join key** | Done. The standby is on the tailnet with HTTPS; your devices reach it and it reaches nothing -- tested from the standby against the PC: HTTPS, the app's port, ping and SSH all refused. Its SSH to the internet is closed |
| ⏳ | **Update your passkey, once per device** | Each device signed in to the PC shows a strip at the top: *One tap and this device signs in to the standby too* -> **Update passkey**. That swaps its passkey for one under a name the PC and the standby share, and from the next hourly backup the standby accepts it -- no codes, nothing set up twice. Passkeys made before this still sign in to the PC. **Sign in to the standby** (the double-click) stays as a fallback for a device with no passkey |
| ✅ | **Photo slideshows** | Open a folder → **▶ Slideshow**. Recursive through subfolders. **Plays forever by default** — shuffled draws a fresh sample each time, in order pages through and wraps, and repeats are expected. 3s-1m per photo, fade/slide/zoom/cut/random transitions. Here or in a room, and a room refills its own queue. Verified on the real library: three pages cover 30,000 distinct photos of 105,162 with no overlap |
| ✅ | **Add a folder of your own media** | **Sources → Choose a folder...** opens the Windows folder picker on the PC, via a `homesh://` protocol handler. Double-click **Add a folder to Homesh** once to register it. `E:\music` is granted; the server reaches that folder and nothing else — `/hostfs` is gone and writes into the mount are refused |
| ⏳ | **Share a Drive folder as Editor** | Needed for one thing now: creating a Drive share link. Backups no longer depend on it -- they go to the Oracle bucket. A service account owns no storage of its own, so a *viewer* cannot grant access it does not itself have — the error says exactly that. Sharing one folder as Editor is the whole task |
| ⏳ | **Keep the backup key somewhere else** | Off-site backups are encrypted before they leave the house, with a key that stays here. That is what makes storing them online safe — and it means a copy of the key has to live somewhere the house does not: a password manager, a piece of paper in a drawer at work. Without it, a backup that survives the house is unreadable |

---

## Working and verified

Verified means measured or driven end to end, not merely compiled.

- ✅ **Catalog** — about 140,000 files across two granted folders on the PC and five
  on Drive. Filenames indexed, displayed and searchable; metadata never replaces them.
  A file that exists in both places is one entry with two copies behind it
- ✅ **Scanning** — daily and automatic, with progress; manual per folder
- ✅ **Tags** — title, artist, album read from ~70 KB per track rather than whole files
- ✅ **Search** — typo-tolerant; results act like files and can show themselves in place
- ✅ **Playback** — audio, photos, documents, video; direct play first
- ✅ **Documents** — doc, docx, xls, xlsx, ppt, pptx and the rest, rendered to PDF
  and drawn in the page (works on a phone, where an iframe cannot)
- ✅ **Video conversion** — MPEG-2, WMV, AVI and friends transcoded as they play,
  nothing stored; verified on a 13.6 GB wedding tape at 720p
- ✅ **Rooms** — zones, sessions, transport, volume, occupancy from the receiver
- ✅ **People** — invitations, per-folder and per-room access, owner who cannot be removed
- ✅ **Audiences** — every folder and room decides who it is for
- ✅ **Playlists** — 41 Winamp lists imported, 98.6% of tracks matched;
  create, rename, reorder, copy, share
- ✅ **Casting** — a Chromecast plays what Google's list allows; everything else
  says so and points at a room
- ✅ **TV app** — discovery, self-update (confirmed working on the bedroom box),
  pairing, native video player, remote control. Shows its own version on screen
  from 0.5.0
- ✅ **Sharing** — **confirmed across several file types.** Files go as files,
  documents as PDF, video converted to MP4 first, Drive links for anything too
  big. Passkey registered on the phone, so it works away from the house
- ✅ **Ordering** — Hebrew filenames on Windows play the track that was clicked
- ✅ **Details view on a phone** — title, artist and album under the filename
- ✅ **Browser playback** — every file type Shahaf has tried
- ✅ **Track lengths** — shown in listings
- ✅ **HTTPS** — Tailscale, real certificate, reachable from your phone anywhere,
  nothing on the public internet

---

## Built, waiting on your eyes

Deployed and believed correct; not yet confirmed in use.

| | Item | Note |
|---|---|---|
| 🟡 | **Which machine is serving you** | The bottom bar says **Local · your PC · v0.1.0** or, in amber, **Remote · Oracle standby · PC offline**. Pressing the standby one says what that means: browsing, search, Drive files and playlists work, and changes to playlists and settings are carried back to the PC |
| 🟡 | **Offline drives say so** | A top folder whose drive is off is tagged *offline*; inside one, an *offline folder* marker sits beside the path; its files are greyed. Playing from it stops at once with a reason, rather than walking the whole queue one failure at a time -- which was the twenty-second freeze. A file that is also on Drive still plays |
| 🟡 | **Any file can be opened** | Files nothing could preview were inert — a `.MSWMM` or one with no extension could not even be clicked. The viewer shows the bytes as text or hex, choosing whichever answers the question, and either can be switched to |
| 🔴 | **Install TV app 0.6.2 on the boxes** | Four rounds of fixes need this APK, from `‹server›/apk`. The crash on video and on stop was one bug — the video bridge was handed the player before it existed and held null for the life of the app. The film down one edge was another, and it took two goes: layout parameters were missing, and then `MATCH_PARENT` was not enough either, because a `VideoView` measures *smaller* than the frame to keep the film's shape and the leftover is aligned to the start edge — which on a Hebrew system is the right one. Centring fixed it. 0.6.2 also puts the web layer *above* the player, so the on-screen controls appear over a film rather than behind it. From 0.6.1 a screen reports its version on connecting and the room card shows it, so you can tell from your phone whether a box took an update |
| 🟡 | **Homesh Connect 1.3.0 -- falls back to the standby** | In daily use and the only interface Shahaf goes through. **1.3.0 opens the standby by itself when the PC does not answer**, and says so on its screen. It learns the standby's address from the PC, so it needs to have reached the PC once since the standby was set up. From 1.2.0, **Check for updates** at the bottom of the screen installs it; from 1.1.0, install by hand once from `‹server›/phone` or the [releases page](https://github.com/shahafdu/Homesh/releases/latest). The TV app did not change and no screen is offered anything |
| 🟡 | **The logo goes home** | From four folders deep, one tap |
| 🟡 | **Browsing this computer for a folder** | Settings browses your own storage — descend, breadcrumbs, **Add this folder** at any depth. The first attempt only listed the top of one mounted folder, which is not browsing: the folder somebody wants is three levels down |
| 🟡 | **Shuffle in a room is a switch** | It was an action, so the button looked identical whether or not it had been pressed. The state lives with the queue now, where several phones can see it |
| 🟡 | **Next at the end of a list** | Started the list again rather than sitting greyed out. Previous on the first still restarts that track — nobody presses previous hoping to reach the end |
| 🟡 | **Cast from the viewer** | The real cast mark, in the viewer's header. Lit for what a Chromecast plays — MP4, WebM, MP3, WAV, OGG, images — and greyed with a reason for the rest, which here is most of it: 152 of your 820 videos are MP4 |
| 🟡 | **A screen finds the server by sweeping** | Broadcast fails silently on many networks. It now asks all 254 addresses on this subnet directly — measured against the real network: 8.5s, found the server, no false positives |
| 🟡 | **A screen keeps looking for the server** | One attempt at launch was no use in the case that happens: the power returns, everything boots at once, and the television asks before the server has started |
| 🟡 | **Clearing a search** | The ✕ stays while there is text. The native one appears only on focus, so clearing took two taps |
| 🟡 | **The server finds its own address** | DHCP moved this machine from .206 to .205 and `LAN_BASE_URL` was silently wrong — so the TV install address pointed nowhere. It is a seed now: the server learns a working address from anything reaching it over the house network, and prefers that when the configured one stops answering |
| 🟡 | **The whole file menu in the viewer** | Share, print, send to a room and add to a playlist, from where you are looking at the file. Opening something used to be a dead end |
| 🟡 | **.txt sharing** | The server sends `text/plain; charset=utf-8`, and the browser matches its permitted list *exactly* — so the charset made it unrecognised. Parameters are stripped now |
| 🟡 | **Print** | On a phone it opens the share sheet, where Print already lives and already works — trying to print a PDF from a hidden frame downloads it instead. On a desktop it prints directly |
| 🟡 | **Search in this folder** | The search box moved down beside one button that switches **Everywhere** ↔ the folder name. Driven in a phone-sized browser: 13 of 50 results outside the folder, 0 after switching. Verified against the real library: 71 hits under one Hebrew folder, 8 under a sub-folder, nothing outside either |
| 🟡 | **"Start at" said 12:30** | It was placeholder text, which reads as a value. It shows the real position now and accepts h:mm:ss |
| 🟡 | **Next no longer stops the music** | Replacing the source rejects the pending play() with AbortError — which is what next *does*. Both the phone and the TV treated it as a failure; the TV put it on screen and killed the queue |
| 🟡 | **What a room will play next** | The tower lists the queue, marks what is playing, and any track can be tapped to jump to it |
| 🟡 | **Shuffle in a room** | Reorders what has not played yet; what is on keeps playing |
| 🟡 | **A playlist marks the playing track** | Opening one while it plays showed no sign of which track was on |
| 🟡 | **Tower bar for video too** | It read the catalog's length, which video has none of. The screen reports its own |
| 🟡 | **TV seeking** | 10s a press and presses accumulate, so the encoder restarts once rather than per tap, with a marker showing where it is heading |
| 🟡 | **wmv / avi on the TV** | The screen was handed the raw file and its decoder refused. It now gets the same live transcode the browser uses — verified: an .avi arrives as h264 640x480 + aac. No decoder bundled into the app |
| 🟡 | **The TV player fills the screen** | The title and bar used to take a strip off every frame. They float over the picture now and fade after four seconds; any remote key brings them back |
| 🟡 | **The remote shows it was heard** | Seeking jumps the bar immediately instead of waiting for the stream, and pause/seek/stop each flash a large confirmation. On a television a silent press is indistinguishable from a flat battery |
| 🟡 | **Closing the TV app frees the room** | The session stayed `playing` against a screen that no longer existed, so the tower lied and play did nothing. It goes idle and keeps its position |
| 🟡 | **Seek from the control tower** | A draggable position bar per room. Sent on release, not per pixel |
| 🟡 | **Two rooms claiming one stream** | The receiver has one HEOS player, so "HEOS is playing" names no room. It now asks which zone is switched to the network input. Verified against the receiver with ZONE2 off |
| 🟡 | **TV app address** | Two faults: it showed a ts.net name no television can resolve, and then the address was long enough that the TV browser searched Google for it. Now the house address on port 80 as `‹address›/apk` — no port to type — and it says to press Go rather than the search suggestion. **Not `/tv`**: that is the interface the installed app loads, and putting the download there blacked out every screen in the house |
| 🟡 | **Playlists in rooms** | Same room picker a file uses, given the whole list |
| 🟡 | **Back to the playing playlist** | The player bar now names where the queue came from and reopens it |
| 🟡 | **Drag to reorder** | A grip instead of up/down arrows; works with a thumb, and with arrow keys when focused |
| 🟡 | **Missing tracks look ordinary** | Greyed out and one line, rather than tinted and taller than everything else |
| 🟡 | **Look for new folders** in Settings | Sharing a folder with the Homesh account is how one is added; discovery used to run only at startup |
| 🟡 | TV app recovers from an unplayable file | Was showing "cannot reach server" and sticking |
| 🟡 | TV remote controls — seek, pause, stop | New in 0.4.0 |
| 🟡 | mp4 that decoded audio only | Now falls back to converting |
| 🟡 | First tap on a phone no longer reports a failure | Retries once before complaining |
| 🟡 | Seek bar usable before the track loads | Length comes from the catalog |
| 🟡 | Send to a room with a large folder | Cap was 500; your English folder is 1,533 |
| 🟡 | Header staying put while scrolling | |
| 🟡 | **A 140 MB rulebook opens** | Lazy *drawing* was only half of it and changed nothing for the big books: pdf.js downloads the whole file in the background by default, ranges or not, so nothing could be read until all of it had arrived. It now fetches only the parts being read, a megabyte at a time — each request costs about 1.4s of latency whatever its size, so the chunk size matters far more than the bytes. The page-watcher was also watching a box that never scrolls, which is why exactly two pages appeared and then nothing ever again |
| 🟡 | **The control tower answers at once** | It waited for the room before it redrew, so a command that genuinely takes a second on the television took a second to acknowledge on the phone. The button now shows what it is asking for immediately and corrects itself if the room disagrees |
| 🟡 | **Seeking from the tower shows on the screen** | It flashed 0:00, because the time was handed over in seconds to something that reads milliseconds. Pause and resume raise the overlay too, so any command from your phone is visible on the television — not only the ones sent with the remote |
| 🟡 | **Picking a track with shuffle on plays that track** | Choosing from the list went through the same path as *next*, which with shuffle on threw the choice away and picked at random. Nothing to do with mp4 against avi: jumping *backwards* took a different branch and worked, which is what made it look like a format problem. Shuffle decides what comes next; it does not overrule a file somebody pointed at |
| 🟡 | **Lesson videos know how long they are** | The progress bar on a conga lesson read 0:21 of 0:10, full, on the television and in the control tower. A video in a Drive folder is read as its first 256 KB, the same as everything else remote -- and ffprobe handed a quarter-megabyte of a film reports how long that quarter-megabyte lasts. 0.64 seconds for a 1.6 GB lesson. That is a number, so it was stored as the length. Every one of those twelve lessons had the length of its own opening. They are measured properly now -- the two ends of the file and its true size, two range requests rather than a download -- and a length that could not be true of a file that size is refused rather than written |
| 🟡 | **Previous, with shuffle on** | Shuffle picked at random whichever button was pressed, so next and previous were the same button. Previous now goes back to what was actually playing, several steps if you keep pressing. Also found beside it: a track *ending* ignored shuffle entirely and played the one below it, so shuffle applied only while somebody was pressing next -- put the phone down and the album played in order |
| 🟡 | **Long documents give their pages back** | Pages were released only when drawing a new one found too many held, so scrolling down a book accumulated a little more with each page -- the shape of a crash rather than of a cap. A page that leaves the screen is now released whether or not anything else is being drawn, the render is called off rather than finishing into a canvas nobody is looking at, and closing a document shuts down its reader instead of leaving one alive per book opened |
| 🟡 | **Video from Drive starts** | It did not. A direct-play film on Drive never began at all -- not slowly, never: the response opened and no bytes followed it, for as long as anybody was willing to wait. Two causes, both on the path of every byte. The token that authorises a Drive read was minted under a mutex held across the network call, so one mint that did not come back shut the whole folder until the server was restarted; it is minted five minutes early now, off the lock, and gives up after twenty seconds. And "is this folder still shared with us" was answered with a fresh connection to Google **per range request** -- forty handshakes for one film. Measured on the real library: direct play never → **1.9s**, local video 0.45s → **0.05s**, converted video 3.1s → **1.0s** |
| 🟡 | **Seeking an AVI or WMV in a room** | These are converted as they play, and a stream being encoded has no index to seek in -- so setting the position did nothing, the film restarted from the beginning, and the bar sat where it had been dropped. Moving through one now restarts the encoder at that point and counts from there, which is what the browser has always done with them |
| 🟡 | **Seeing a whole filename** | Tap the name in the viewer and it opens out in full; the type is spelled out beside the size, where it used to be buried at the end of a name that had been cut off. On a television, where there is nothing to tap, the name now runs to two lines instead of ending in an ellipsis |
| 🟡 | **`music` was two libraries** | One folder, catalogued twice -- the copy on the PC and the copy on Drive -- with nothing linking them: 9,189 songs on each side and not one entry in common. `items.content_hash` had been in the schema since the first migration and was empty for all 141,818 files, so nothing had ever noticed. Copies are now fingerprinted and the two become one entry with two copies behind it. Drive states its own checksum for free; local files are read, but only the ones with a lookalike elsewhere -- 53 GB rather than the whole library. **This also turns on the availability model**: playing from Drive when the PC is off needs one entry holding both copies, which is what §4 has always described and what has never once been true here |
| 🟡 | **Backups, and putting one back** | Settings → Backups, administrators only. **A dropdown now, not a list** -- grouped into this week, weekly, and copies taken before a restore -- with Restore, Download and Delete for the one chosen. Taken hourly, **kept one a day for a week and one a week for five** -- at most twelve, and there is always a point about a fortnight and about a month back. Every hourly of the last day used to be kept too, which is why there were thirteen from one day; only the newest was ever needed. The copy taken before a restore is kept a week on its own terms, so a restore stays undoable. Restoring takes a copy of the present state first, so restoring the wrong one is undoable. The media is not backed up and should not be -- this is everything the server knows *about* your files, which is the part that exists nowhere else. A backup can be downloaded, and should be: one that lives on the same disk as the thing it protects is half a backup |
| 🟡 | **Backups that leave the house** | **Working, on your own bucket.** Taken here, encrypted here with a key that never leaves, and pushed to Oracle Object Storage after each daily backup. Measured the first time it ran: 566,257 rows backed up in 12s, encrypted and sent in 4s, fetched back and decrypted in 6s, byte-identical. S3 in the protocol sense, so the provider is a setting -- Cloudflare R2 or Backblaze would be six lines of `.env`. The request signing is written out rather than pulling in fifty megabytes of AWS SDK to sign one upload a day, and is checked against Amazon's own published example |
ew-backup-key.ps1` run once so the key exists and you can put a copy of it somewhere this machine is not |
| 🟡 | **The duplicates are joined** | 5,274 entries merged on the real library -- 141,818 down to 136,544, with 5,000 files now known to exist in two places. Roughly half of `music` matched; the rest are same-name, same-size and genuinely different bytes, which is what the check is for. The pass keeps running with each sweep, so it converges rather than needing to be finished in one go |
| 🟡 | **An unplugged drive no longer breaks the fallback** | Found with your external drive actually detached, which is the first time the availability model has been tested for real -- and it failed. Asking a local source whether it is reachable does not answer False when the drive is gone; it raises, and the exception ended the search before the Drive copy was tried. A file that existed in both places became a server error rather than playing from the cloud. It answers now |
| 🟡 | **Switching the RAID off no longer stops Homesh** | It did, completely. A bind mount names a path and Docker resolves it when it creates the container, so a folder on a powered-down drive means the container refuses to start -- taking the catalog, the rooms, the playlists and the music on Drive with it, none of which needed that disk. A granted folder whose drive is away is now set aside before the stack starts, the grant stays written down behind an `# OFFLINE` marker, and it comes back by itself next time the drive is there. **Start Homesh** names the ones it set aside. Also fixed: switching the drive off leaves its mount inside Docker's virtual machine connected to nothing, which made the *next* start fail with `mkdir /run/desktop/mnt/host/e: file exists` -- that is cleared before remounting now |
| 🟡 | **The drive going on and off needs nothing from you** | **Sources** says *offline -- the drive it is on is not connected* the moment it is, asked fresh rather than remembered, and browsing and searching carry on. Switch the RAID back on and Homesh picks the folder up within a couple of minutes by itself. A container is handed its folders when it is created and cannot be given one afterwards, so following the storage means rebuilding it -- the only real question was who has to notice, and it is now a scheduled task rather than you |
| 🟡 | **The backups folder is not part of your library** | Sharing it with the server made it a source: Drive discovery is indiscriminate on purpose, so the folder this server writes *into* registered itself, got scanned, and would have sat in your library as a row of encrypted files. Skipped by name now, whatever its spacing or casing |
| 🟡 | **Two folders called `music`, told apart** | The root now says where each one lives -- *on this PC* or *Google Drive* -- beside its name. Joining the duplicate files underneath them did not help with this and was never going to: two sources are two sources however much they hold in common, and both are called what the folder is called. Only at the root, where the ambiguity is |

---

## Known broken

| | Item | What is known |
|---|---|---|
| 🟡 | **Stopping a playlist crashed the TV app** | Cause found and it was not the guess. `stop()` called a method reference on a null player, which throws as it is *created* — outside the try block meant to contain it. Same root cause as the video crash. Fixed in 0.5.9 and still in 0.6.1; needs confirming once a box has the new APK |

---

## Owed, from work already done

Started or promised and not finished. Listed separately because these are mine,
not decisions waiting on anybody.

1. ⬜ **Finish the encoder sweep** — every video opened through the encoder to
   prove the odd-dimension class is closed. Started twice: killed once by my own
   rebuild, then it timed out on one file at 900s and never completed. A sample
   of 20 across five formats passed
2. 🟡 **Duration backfill** — the pass said `kind = 'audio'` and had never touched a
   film. Extended to video with ffprobe, which took it from 78% to 98%.
   **That 98% was not worth what it looked like**, and the conga lessons are how
   it came out: a video in a Drive folder was timed from its first 256 KB, so it
   carried the length of its own opening rather than its own length. Those have
   been thrown away and measured again from both ends of the file — 7,497 of
   7,652 (**98%**), this time meaning it. The 155 without one are files nothing
   can time: truncated recordings whose `moov` atom was never written, and DVD
   `.VOB` fragments that carry no duration at all. 1,086 audio files also have
   none, where the declared bitrate cannot be trusted; that is a different
   problem and untouched

---

## Next, in order

1. ✅ **The standby is live** — on the tailnet as a dead end, public SSH
   closed, a way to sign in, thumbnails synced from the PC hourly, and the phone
   app falling back to it when the PC does not answer
2. 🟡 **Attempt limits on the unauthenticated doors** — sign-in, the first-run
   code, invitation lookups and device codes. Twenty failed sign-ins in five
   minutes, ten code attempts, and the first-run code retired after five wrong
   guesses. Counted per caller address, which here is coarser than it sounds:
   Docker's port mapping rewrites the source, so every client looks alike and
   the limits are in effect per server — chosen with that in mind
3. 🟡 **Audio caching** — a track fetched from Drive is kept on disk and read
   from there ever after. Measured on the real library: **1.0-1.2 s before the
   first byte from Drive, under a millisecond from the copy**. Audio only, never
   films; the first play is not slowed to fill it; 2 GB by default, longest
   unplayed evicted first

---

## AI — the layer is built, the uses are not

Decisions are settled and recorded in CLAUDE.md and now in `docs/AI.md`.

1. 🟡 **Provider layer** — OpenRouter with **gpt-oss**, free tier by default; the
   key lives in `.env` and the guards refuse one that reaches the repo. A paid
   model is refused outright while the monthly cap is zero, which it is until
   set; above zero the month's spend is summed and checked *before* each call.
   Spending is granted per account. Every attempt is recorded — refusals
   included, never the question or the answer — and readable at
   `/api/ai/history`
2. ⬜ **Offline tagging pass** — one run over the library, cached in
   `item_metadata` with `origin='ai'`, so later questions filter locally first
3. ⬜ **Commands** — play here, stop there, skip, build a list. Calls the same
   API as the interface, as the user, so permissions are enforced by the code
   that already enforces them
4. ⬜ **Find things** — natural language over the catalog, results actionable
5. ⬜ **Content search** — documents first (cheap and exact), then photos
   (CLIP embeddings), then audio and video transcription on demand only
6. ⬜ **Activity history** — what the AI did, readable in the app

**The AI cannot**: add or remove rooms · change permissions · act beyond the
asking user's own access · delete anything without confirmation · send anything
outside the house without you pressing something.

---

## Later phases

- ⬜ **Photo availability** — RAID→Drive sync, Wake-on-LAN, Takeout gap-fill
- ⬜ **Gapless audio and ReplayGain**
- ⬜ **Public release** — screenshots, documentation
