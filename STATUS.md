# Where Homesh stands

Living status. Updated as things move; the point of it is that neither of us has
to reconstruct the state of a large, half-finished system from memory.

**Legend** — ✅ built and verified · 🟡 built, needs Shahaf to confirm ·
🔴 known broken · ⬜ not started · ⏳ waiting on Shahaf

Last updated: 21 August 2026 · 332 tests · 16 migrations · CI green

---

## Needs Shahaf

The list to work from. Everything else can proceed without you.

| | What | Why it matters |
|---|---|---|
| ⏳ | **Check the bedroom box updates itself** to TV app 0.4.0 | It may still be on 0.2.0, which predates the native video player |
| ⏳ | **Add a passkey on your phone** at the ts.net address | Settings → Add a passkey. Then it is a fingerprint, and Share can work |
| ⏳ | **Point MEDIA_ROOTS at real local media**, or leave it empty | The synthetic test library was removed; nothing local is indexed now |
| ⏳ | **Revoke the temporary GitHub token** when convenient | You said it was temporary; it still works |

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
- ✅ **TV app** — discovery, self-update, pairing, native video player, remote control
- ✅ **Sharing** — songs share as files; Drive links work for every type
- ✅ **HTTPS** — Tailscale, real certificate, reachable from your phone anywhere,
  nothing on the public internet

---

## Built, waiting on your eyes

Deployed and believed correct; not yet confirmed in use.

| | Item | Note |
|---|---|---|
| 🟡 | **wmv / avi on the TV** | The screen was handed the raw file and its decoder refused. It now gets the same live transcode the browser uses — verified: an .avi arrives as h264 640x480 + aac. No decoder bundled into the app |
| 🟡 | **The TV player fills the screen** | The title and bar used to take a strip off every frame. They float over the picture now and fade after four seconds; any remote key brings them back |
| 🟡 | **The remote shows it was heard** | Seeking jumps the bar immediately instead of waiting for the stream, and pause/seek/stop each flash a large confirmation. On a television a silent press is indistinguishable from a flat battery |
| 🟡 | **Closing the TV app frees the room** | The session stayed `playing` against a screen that no longer existed, so the tower lied and play did nothing. It goes idle and keeps its position |
| 🟡 | **Seek from the control tower** | A draggable position bar per room. Sent on release, not per pixel |
| 🟡 | **Clicking a song played the wrong one** | Only Hebrew names, only Windows — the rows were sorted but the *unsorted* list went to the player with the sorted index. Latin names hid it because both orders agree there |
| 🟡 | **Two rooms claiming one stream** | The receiver has one HEOS player, so "HEOS is playing" names no room. It now asks which zone is switched to the network input. Verified against the receiver with ZONE2 off |
| 🟡 | **TV app address** | Two faults: it showed a ts.net name no television can resolve, and then the address was long enough that the TV browser searched Google for it. Now the house address on port 80 as `‹address›/apk` — no port to type — and it says to press Go rather than the search suggestion. **Not `/tv`**: that is the interface the installed app loads, and putting the download there blacked out every screen in the house |
| 🟡 | **Playlists in rooms** | Same room picker a file uses, given the whole list |
| 🟡 | **Back to the playing playlist** | The player bar now names where the queue came from and reopens it |
| 🟡 | **Drag to reorder** | A grip instead of up/down arrows; works with a thumb, and with arrow keys when focused |
| 🟡 | **Missing tracks look ordinary** | Greyed out and one line, rather than tinted and taller than everything else |
| 🟡 | **Odd-sized video converting** | H.264 cannot encode an odd height. `Bebe Complicado.avi` is 640x415, and x264 refused to start — which is why it played but would not share |
| 🟡 | **Sharing avi, wmv, mkv, mov** | Browsers keep a fixed list of file types a page may attach, and none of those are on it — `canShare()` does not check that list, which is why it said yes and the share then failed. They are converted to MP4 first, reusing the conversion the viewer already makes |
| 🟡 | Sharing documents | Sent as the PDF the server already renders — `.doc` is not on the browser's list either, and a PDF is what a phone can open |
| 🟡 | Sharing anything slow to fetch | A share is only permitted for a few seconds after the tap. Slow ones now show progress and turn into **Send now** — one extra tap, nothing re-downloaded |
| 🟡 | Sharing very large videos | Past 256 MB a phone cannot hold the file in memory at all; it says so and points at a Drive link |
| 🟡 | **Look for new folders** in Settings | Sharing a folder with the Homesh account is how one is added; discovery used to run only at startup |
| 🟡 | TV app recovers from an unplayable file | Was showing "cannot reach server" and sticking |
| 🟡 | TV remote controls — seek, pause, stop | New in 0.4.0 |
| 🟡 | wmv / avi / 3gp playing in the browser | 627 of your 802 videos |
| 🟡 | mp4 that decoded audio only | Now falls back to converting |
| 🟡 | First tap on a phone no longer reports a failure | Retries once before complaining |
| 🟡 | Seek bar usable before the track loads | Length comes from the catalog |
| 🟡 | Send to a room with a large folder | Cap was 500; your English folder is 1,533 |
| 🟡 | Header staying put while scrolling | |
| 🟡 | Track lengths shown | Derived from bitrate and size; **backfill for 9,950 tracks not yet run to completion** |

---

## Known broken

| | Item | What is known |
|---|---|---|
| 🟡 | **Streaming stops after a few songs** | **Cause found and fixed.** Not the songs — a reader left for the garbage collector to close, which deadlocked httpx's connection pool against itself. Once it happened, nothing that lives on Drive would load until a restart. Found in a stack dump: twelve threads waiting on a lock held by a thread waiting for itself |
| 🔴 | **Details columns on a phone** | Hidden below 720px with the metadata folded under the filename. If that is not what you see, I need to know what does |

---

## Next, in order

1. ⬜ **Shuffle scope** — within the folder or list, versus the whole library
2. ⬜ **Seek in rooms** — a position bar in the control tower
3. ⬜ **Rate limiting on sign-in** — listed in the architecture, never built, and
   worth having now the server has a real hostname
4. ⬜ **Database backups** — daily, a week back, plus two-week and one-month
   points; restore from inside the app, administrators only.
   **Prerequisite for anything with AI in it**
5. ⬜ **Audio caching** — first play fetches, later plays are instant.
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
