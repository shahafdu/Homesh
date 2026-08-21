# Where Homesh stands

Living status. Updated as things move; the point of it is that neither of us has
to reconstruct the state of a large, half-finished system from memory.

**Legend** — ✅ built and verified · 🟡 built, needs Shahaf to confirm ·
🔴 known broken · ⬜ not started · ⏳ waiting on Shahaf

Last updated: 22 August 2026 · 343 tests · 17 migrations · CI green (privacy
check was red for three commits — a private address in a test; see below)

---

## Needs Shahaf

The list to work from. Everything else can proceed without you.

| | What | Why it matters |
|---|---|---|
| ⏳ | **Point MEDIA_ROOTS at real local media**, or leave it empty | The synthetic test library was removed; nothing local is indexed now |

---

## Working and verified

Verified means measured or driven end to end, not merely compiled.

- ✅ **Catalog** — 16,600 files across three Drive folders. Filenames indexed,
  displayed and searchable; metadata never replaces them
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
| 🟡 | **Print** | Documents and photographs, to a printer or to PDF from the same dialog. In the file menu and in the viewer |
| 🟡 | **Search in this folder** | A toggle beside the result count. Verified against the real library: 71 hits under one Hebrew folder, 8 under a sub-folder, nothing outside either |
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

---

## Known broken

| | Item | What is known |
|---|---|---|

---

## Owed, from work already done

Started or promised and not finished. Listed separately because these are mine,
not decisions waiting on anybody.

1. ⬜ **Finish the encoder sweep** — every video opened through the encoder to
   prove the odd-dimension class is closed. Started twice: killed once by my own
   rebuild, then it timed out on one file at 900s and never completed. A sample
   of 20 across five formats passed
2. ⬜ **Finish the duration backfill** — ~9,950 tracks still have no length, so
   listings show none for them
3. ⬜ **Shuffle scope on the phone** — within the folder or list, versus the
   whole library. Still undecided; rooms got shuffle as an action instead

---

## Next, in order

1. ⬜ **Rate limiting on sign-in** — listed in the architecture, never built, and
   worth having now the server has a real hostname
2. ⬜ **Database backups** — daily, a week back, plus two-week and one-month
   points; restore from inside the app, administrators only.
   **Prerequisite for anything with AI in it**
3. ⬜ **Audio caching** — first play fetches, later plays are instant.
   Drive's own latency is ~1.4s per request and nothing else will remove it

---

## AI — agreed, not started

Decisions are settled and recorded in CLAUDE.md. Sequenced after backups.

1. ⬜ **Provider layer** — your own key (Claude / Gemini / OpenAI), OpenRouter,
   local model, or none. Tiered by cost, paid tier gated per account
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
- ⬜ **Oracle deployment** — the always-on node
- ⬜ **Gapless audio and ReplayGain**
- ⬜ **Public release** — screenshots, documentation
