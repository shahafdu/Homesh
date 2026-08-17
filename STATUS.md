# Where Homesh stands

Living status. Updated as things move; the point of it is that neither of us has
to reconstruct the state of a large, half-finished system from memory.

**Legend** — ✅ built and verified · 🟡 built, needs Shahaf to confirm ·
🔴 known broken · ⬜ not started · ⏳ waiting on Shahaf

Last updated: 18 August 2026 · 312 tests · 16 migrations · CI green

---

## Needs Shahaf

The list to work from. Everything else can proceed without you.

| | What | Why it matters |
|---|---|---|
| ⏳ | **Share three Drive folders as Editor**, not Viewer | "Create a Drive link" fails until then — a viewer cannot grant access it does not have |
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
- ✅ **HTTPS** — Tailscale, real certificate, reachable from your phone anywhere,
  nothing on the public internet

---

## Built, waiting on your eyes

Deployed and believed correct; not yet confirmed in use.

| | Item | Note |
|---|---|---|
| 🟡 | Share a file from the phone | Was blocked by http; now on https. Just given the file a proper media type, which is the next most likely cause |
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
| 🔴 | **Streaming stops after a few songs** | No cause yet. Needs reproducing with logs open — roughly how many songs in, and whether mid-song or between tracks |
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
