# 08 - Sending old formats as something that opens

Read [`README.md`](README.md) first. Needs nothing from the AI tracks.

| Task | Section |
|---|---|
| S1 Convert an old-format file when it is sent | 1 |
| S2 ffmpeg reads only what it was given | 2 |

What exists today: `web/src/share.ts` sends a file's bytes through the phone's
share sheet. Its `SHAREABLE` set is Chromium's own list of extensions a page may
attach. A document outside it goes as PDF (`/api/documents`), and a video outside
it goes as MP4 through the *watching* conversion in `server/app/transcode.py`,
which keeps the MP4 in its cache. Audio outside the list (wma, raw aac, ape, ra,
amr, ac3, aiff, mka and the like) is refused with "cannot attach". Drive links
(`server/app/sharing.py`) share the original file and are not changed by this
task.

---

## 1. S1 - convert when sending, keep nothing

### 1.1 What the owner asked for

- A file in a format modern software does not open is converted on the
  server **when it is sent as a file**, and the converted copy is what is sent.
- **Only short files.** This is for clips and songs, not films. A long file is
  refused with a sentence saying so, and that a Drive link is the way to send it.
- **Nothing is kept.** The converted copy exists only while it is being
  handed over, and is deleted afterwards. It never joins the watching cache.
- Links stay exactly as they are; a link never gets a converted target.

### 1.2 What is converted to what

| The file | Sent as |
|---|---|
| Video whose extension is not in `SHAREABLE`, or that `transcode.needs_conversion` names (wmv, avi, asf, mov, mkv, flv, 3gp, mpg, vob, ...) | MP4: H.264 + AAC, `-movflags +faststart`, scaled to at most 1080 lines, `-crf 23 -preset veryfast` |
| Audio whose extension is not in `SHAREABLE` (wma, aac, ape, ra, amr, ac3, aiff, mka, ...) | M4A: AAC 192 kb/s, tags copied (`-map_metadata 0`) |
| Anything already in `SHAREABLE` | Unchanged, as today (except the existing MPEG-2 case, which moves to this path) |

MIDI and other formats that have no audio stream to convert stay refused.

### 1.3 Limits

- `SHARE_CONVERT_MAX_SECONDS` in `.env`, default 600 (10 minutes). Duration
  comes from `items.duration_ms`; when that is unknown, ask ffprobe first. Over
  the limit: HTTP 413 with "Too long to convert for sending. Share a Drive link
  instead." Nothing is fetched before this check.
- The result must also fit `MAX_SHARE_BYTES` (256 MB, `share.ts`). ffmpeg gets
  `-fs` set to that ceiling, so an output that would be larger fails rather than filling the disk.
- At most one conversion at a time, sharing the watching conversion's slot
  (`transcode._slot`): a send waits for it, and is refused after 30 seconds of waiting, saying the server is
  busy converting.
- Rate limit `hit("share-convert", user, Limit(10, 10 minutes))`.

### 1.4 Server

New route `POST /api/items/{item_id}/send-copy` (in a new `server/app/sendcopy.py`):

1. `require_user`, then `may_access_item` before any other answer (404 otherwise).
2. Decide the target from the extension and `kind` (section 1.2); 415 if none.
3. Duration check (1.3).
4. ffmpeg reads the source through the same streaming path `transcode._run`
   uses, so a Drive file works as a local one does, and runs with `child_env()`.
5. Output goes to a temporary file under the transcode cache's parent in a
   `sendcopy/` directory, named by a random id, never the file name.
6. The response is a `FileResponse` whose `BackgroundTask` deletes the file once
   it is sent. A sweep at start-up and every 15 minutes deletes anything in
   `sendcopy/` older than one hour, so a dropped connection leaves nothing behind.
7. `Content-Disposition` names it `<original name without extension>.mp4` /
   `.m4a`.
8. Logged: item id, extensions, duration, output size, seconds taken. No file name.

POST because it starts real work; add it to `ALLOWED_MUTATING_ROUTES` in
`test_readonly.py` with a comment that it writes only its own temporary file.

### 1.5 Web

- `share.ts`: `route()` gains `{ via: "copy" }` for 1.2's cases (video and
  audio), replacing the video-to-MP4 route that went through the watching
  conversion. `prepareShare` posts to `send-copy` and shows progress as
  "Converting for sending..." (indeterminate) then the download fraction.
- A 413 shows its sentence and, where the file is on Drive, the existing
  "Create a Drive link" button beside it.
- Where the share sheet is unavailable (plain http on the house network),
  the existing Download button downloads **the converted copy** for these
  files, labelled "Download as MP4" / "Download as M4A", so the person can
  share it from their files.

### 1.6 Tests

Server (`test_sendcopy.py`), with a short generated WMV and WMA made by ffmpeg
in the test (no fixture files from the library):

- an account without access gets 404 and nothing is converted;
- a WMV comes back as MP4 that ffprobe reads as h264 + aac, a WMA as m4a/aac;
- the temporary file is gone after the response, and the sweep removes an
  old leftover;
- over `SHARE_CONVERT_MAX_SECONDS` gives 413 before any fetch;
- a file already in the shareable list gives 415 (it needs no copy);
- a second request while the slot is busy waits, and is refused with the busy
  message when the wait runs out;
- nothing is written to the transcode cache.

Web (`web/test/`): `route()` picks `copy` for wma, wmv, avi, mov, mkv and
not for mp3, mp4, m4a; the download label changes accordingly.

### 1.7 Afterwards

Rebuild the PC (`start-homesh.ps1 -Rebuild`) and redeploy the standby.

---

## 2. S2 - ffmpeg reads only what it was given

Found in S1's review. ffmpeg guesses an input's format from its bytes. A file
in a granted folder whose bytes are really a playlist (HLS, concat, or
similar) would make ffmpeg fetch other addresses on its own: a blind request
from inside the house. The watching conversion (`transcode.py`, both the
cached conversion and `live.mp4`) has no protocol whitelist at all; the send
copy (`sendcopy.py`) has one but must allow http, because the source itself
is served over http.

### 2.1 What changes

- Every ffmpeg and ffprobe call that reads a library file gets
  `-protocol_whitelist http,tcp` (no `file`, `https`, `crypto`, `data`).
- It also gets `-format_whitelist` with the demuxers this library needs: the
  video and audio containers in `NEEDS_CONVERSION`, the share list, and their
  usual demuxer names. Playlist and indirection demuxers (`hls`,
  `concat`, `ffconcat`, `image2`, `tee`, `data`, `subfile`) are never on it.
- The source URL ffmpeg reads is always the server's own streaming endpoint
  on the loopback address, built by one shared function. A source never
  names its own URL.
- One helper builds these input arguments (`server/app/ffinput.py`), used by
  `transcode.py`, `sendcopy.py` and any later caller. A test makes sure no
  module calls ffmpeg without it.

### 2.2 Tests (`test_ffinput.py`)

- an `.mp4` whose bytes are an HLS playlist naming another address: the
  conversion fails, and the fake address receives nothing (served from a
  local test server that records requests);
- a real short WMV still converts, through both the watching path and the send
  copy;
- `rg`-style check: every `"ffmpeg"` / `"ffprobe"` argv in `server/app`
  comes from the helper.

### 2.3 Afterwards

Rebuild the PC and redeploy the standby.
