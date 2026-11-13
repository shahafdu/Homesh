# 05 - Music: gapless playback, loudness, tag repair

Read [`README.md`](README.md) first. M1 needs nothing else. M2 and M3 need the
worker and queue (A2) from [`01-ai-foundations.md`](01-ai-foundations.md).

What exists today: `web/src/usePlayer.ts` (`usePlayer`) plays audio through one
`Audio` element and moves to the next track on `ended`. The television plays
through its own native player (`android/.../NativeVideo.java`) or the page.
Tags are read at scan time by `server/app/metadata.py` into `item_metadata`
with `origin='file'`.

---

## 1. M1 - gapless playback in the web player

### 1.1 What "gapless" means here

Two tracks from the same album play one into the next with no audible silence
and no click. It matters for live albums, classical works and mixes. It does
**not** mean cross-fading; nothing is blended.

A browser cannot do this perfectly for every format, because MP3 and AAC files
carry padding the `Audio` element does not always trim. The design below gets
the gap down to a few milliseconds where the browser allows it, and never
makes anything worse than today.

### 1.2 Two elements, one waiting

In `usePlayer.ts`, keep two `Audio` elements: **current** and **next**.

1. When the current track has 20 seconds left (`duration - currentTime < 20`,
   checked in the existing `timeupdate` handler), and there is a following
   track in the queue, and it is audio, and its file is playable now: fetch
   its signed URL (`signedUrl(item_id)`), set it as `next.src`, set
   `next.preload = "auto"`, and call `next.load()`. Do this once per track.
2. On the current element's `ended`: if `next` is loaded for the track that is
   due (`next.readyState >= 3`), call `next.play()` immediately, swap the two
   references, and run the same state updates the existing `onEnd` path runs
   (position in the queue, now-playing display, media session). Otherwise fall
   back to today's path exactly.
3. Anything that changes what is due -- skip, previous, a tap on another
   track, shuffle toggled, the queue edited, stop -- clears `next`
   (`next.removeAttribute("src"); next.load()`) so a stale preload never
   plays.
4. Volume, mute and playback rate are applied to both elements whenever they
   change.
5. Every event listener the hook attaches (`timeupdate`, `loadedmetadata`,
   `ended`, `error`, `playing`) must follow the swap. The simplest correct
   way: attach the listeners to both elements once, and have each handler
   ignore events from the element that is not current
   (`if (e.target !== currentRef.current) return;`).

A signed URL lives five minutes (`MEDIA_URL_TTL_MINUTES`); preloading 20
seconds ahead is well inside that. Do not preload earlier.

**Tighter than `ended`** (only if step 2 leaves an audible gap in testing):
start `next` a little before the end. When `duration - currentTime < 0.25`,
schedule `next.play()` with `setTimeout` for the remaining time minus 30 ms.
Keep this behind a constant `EARLY_START_MS = 0` that can be raised; ship with
whatever value sounds right on a desktop browser and a phone, and write the
measured result in the commit message.

### 1.3 What does not change

- The setting is not exposed. Gapless is simply how it plays.
- Video never uses the second element.
- Playback in a room (the control tower) is unaffected: that is the room's
  device playing, not this browser.
- If the next track is on a drive that is off, nothing is preloaded and the
  existing skip-over behaviour applies.
- Data use: one extra track is buffered ahead. On a phone on mobile data that
  is the cost of gapless; it is acceptable because it is at most one track.

### 1.4 The television

The TV page plays audio with the same `Audio` approach inside the TV app's
WebView; apply the same two-element change in the TV client's player code
(read `web/src/tv/TvApp.tsx` for where audio is played). Rooms driven by the
receiver's own network player take one URL at a time and cannot be made
gapless from here; leave them.

### 1.5 Tests and checking

There is no browser test harness. Extract the decision logic into pure
functions in a new `web/src/gapless.ts` -- `shouldPreload(position, duration,
nextTrack)`, `canHandOver(readyState, dueId, loadedId)` -- so the type checker
covers them, and keep the DOM code thin. Check by ear with two files cut from
one continuous recording, on a desktop browser and on a phone, and say which
browsers in the commit message.

---

## 2. M2 - loudness

### 2.1 What it does

Tracks from different albums play at very different levels. Each track gets a
measured loudness, stored as metadata; the player turns quiet tracks up and
loud ones down so they sit at the same level. **No file is changed.**

### 2.2 Migration `metadata_computed`

```sql
-- A measurement is not a tag from the file, not a person's correction, and
-- not a model's judgement. It gets its own origin.
ALTER TYPE metadata_origin ADD VALUE IF NOT EXISTS 'computed';
```

Nothing else may be in this migration file: a value added to an enum cannot be
used in the same transaction that adds it.

### 2.3 Where a value comes from

Two sources, in this order of trust when read: a ReplayGain tag in the file,
then a measurement.

- **Tags** (`metadata.py`, in `read_audio`): when the file carries
  `REPLAYGAIN_TRACK_GAIN` / `replaygain_track_gain` (and the album
  equivalents), store `loudness.track_gain_db` and `loudness.album_gain_db`
  with `origin='file'`. Parse a value like `-6.54 dB` to a number; ignore one
  that does not parse or is outside -30 to +30.
- **Measurement** (`workkinds/loudness.py`): kind `loudness`, for audio items
  with no `loudness.track_gain_db` row of any origin.

Handler:

1. `ctx.open_item`. A local file is used in place. A remote one is used only
   if it is already in the audio cache (`audiocache.py`); otherwise the job
   waits (`BytesUnavailable`) -- do not download a library to measure it.
2. Run, with `childenv.child_env()`, arguments as a list, timeout 300 s:

   ```
   ffmpeg -nostdin -hide_banner -v info -i <path> -map 0:a:0 -af ebur128=peak=true -f null -
   ```

3. From stderr's summary read `I:` (integrated loudness, LUFS) and `Peak:`
   (true peak, dBFS). Gain is `-18.0 - I` (the ReplayGain 2 reference level).
4. Store with `origin='computed'`: `loudness.track_gain_db` (two decimals),
   `loudness.peak_db`. A file ffmpeg cannot decode is a `fail`.

One measurement takes a few seconds of CPU per track. It runs only in the
night window like every other kind.

### 2.4 Serving and applying it

- `GET /api/browse` and the playlist and queue responses already carry a
  `meta` object per file. Add `gain_db` and `peak_db` to it, chosen as: the
  `file` value if present, else `computed`. (Read how `meta` is built in
  `library.py` and extend that one place.)
- In `usePlayer.ts`, when a track starts, compute a factor between 0 and 1 and
  multiply the person's own volume setting by it before assigning
  `audio.volume`. **It only ever turns down**, so nothing can clip and nothing
  can become painfully loud:

  ```ts
  const HEADROOM_DB = 6;   // tracks this much quieter than the reference play at full
  export function levelFactor(gainDb: number | null | undefined): number {
    if (gainDb == null || !Number.isFinite(gainDb)) return 1;
    return Math.min(1, Math.pow(10, (gainDb - HEADROOM_DB) / 20));
  }
  ```

  The cost is that everything is somewhat quieter overall than before; the
  person turns the device up once. `peak_db` is stored for a later version
  that could turn quiet tracks up safely; it is not used yet.
- A track with no value plays as it does today.
- New preference `level_volume` (`"on"`, `"off"`, default `"on"`) in
  `server/app/prefs.py` `ALLOWED` and `web/src/prefs.ts`, with one switch in
  Settings: "Play every track at a similar volume". Off means `factor = 1`.
- Rooms and the television are unaffected in this version.

### 2.5 Tests

- `read_audio` on a fixture file with a ReplayGain tag stores a `file` row;
  a nonsense tag stores nothing;
- the handler on a generated tone (make it with ffmpeg in the test, as other
  tests make media) stores a `computed` gain within 0.5 dB of the expected
  value;
- a `file` value outranks a `computed` one in `meta`;
- a remote item not in the cache ends in `waiting`;
- the preference validates and round-trips;
- `levelFactor` (in a new `web/src/loudness.ts`) never returns more than 1 and
  returns 1 for a missing or non-finite value.

---

## 3. M3 - tag repair

### 3.1 What it does

Many files have missing or wrong tags. Each such track is fingerprinted from
its sound, looked up in a public database of recordings, and the artist, title
and album found there are stored **beside** the file's own tags. The file is
not touched, and a person's correction outranks everything.

### 3.2 What leaves the house

For each track looked up: an acoustic fingerprint (a compact summary of the
sound, from which the audio cannot be rebuilt) and the track's length go to
AcoustID; a recording id then goes to MusicBrainz. No file name, no path, no
tag. This must be stated on the settings page and is **off until an
administrator turns it on**.

### 3.3 Configuration

- `.env`: `ACOUSTID_API_KEY` (a free application key from AcoustID; the docs
  say how to get one, never what it is). `config.py`: `acoustid_api_key: str =
  ""`. Pass it to the worker in compose.
- AI settings (extend the Pydantic model in foundations 4.5): `"tag_repair":
  false`.
- Dockerfile, worker stage: install `libchromaprint-tools` (provides
  `fpcalc`).

### 3.4 Migration

None: `metadata_origin` already has `musicbrainz`.

### 3.5 Kind `tag-repair` (`workkinds/tag_repair.py`)

Planner: only when `tag_repair` is true and a key is set. Audio items that
have no `mb.recording_id` row and no `mb.checked` row, and that are missing an
artist or a title from `file` and `user` origins. At most 2000 per night.

Handler:

1. `ctx.open_item`; local files and cached remote files only, as in M2.
2. `fpcalc -json -length 120 -- <path>` with `childenv.child_env()`, timeout
   60 s -> `{"duration": ..., "fingerprint": "..."}`.
3. `POST https://api.acoustid.org/v2/lookup` with form fields `client`,
   `duration` (rounded), `fingerprint`, `meta=recordings+releasegroups+compress`.
   Send the key in the body as the API requires; never log the request.
4. Take the result with the highest `score`. Below 0.85, or no recordings:
   store `mb.checked = <today>` with `origin='musicbrainz'` and finish (so it
   is not asked again).
5. From the best recording: title, the first artist credit's name, and the
   first release group's title. Store with `origin='musicbrainz'`,
   `confidence = score`: `title`, `artist`, `album`, `mb.recording_id`.
6. Rate: at most 3 requests per second to AcoustID -- sleep 0.4 s between
   jobs of this kind. If MusicBrainz is called directly (only needed if the
   AcoustID response lacks a field you need), at most one request per second,
   with a `User-Agent` of `Homesh/<version> ( <the project's public repository
   URL> )` as MusicBrainz requires. No address or name of a person.
7. HTTP 429 or 5xx: `fail` (the queue backs off). A network error: `fail`.

Verify the request and response shapes against AcoustID's current
documentation before writing the parser, and keep a recorded sample response
as a test fixture.

### 3.6 Reading the result

One function decides what a track is called, used wherever a title or artist
is displayed or searched (read how `meta` is built today and change that one
place):

```
user  >  file  >  musicbrainz  >  ai
```

The **file name stays visible** wherever the title is shown (README rule 28).
In the file's detail view, a `musicbrainz` value is labelled: "Title: <value>
(found by its sound)". Beside it, two small buttons: "Right" (copies the
value to a `user` row, which removes the label) and "Wrong" (stores a `user`
row with the file's own value, or an empty one, so the lookup's answer is
never shown again for that field).

New routes in `library.py`:

| Route | Body | Does |
|---|---|---|
| `PUT /api/library/items/{item_id}/tags` | `{"title"?, "artist"?, "album"?, "ai.genre"?, "ai.mood"?}` each a string up to 300 characters, or null to remove the person's value | upserts `origin='user'` rows. Requires `may_access_item`; 404 otherwise. Rate-limited 120 per 5 minutes per account. Audited: `("library.tags", user.id, {"item": id, "keys": [...]}, ip)` |

Only these keys are accepted; anything else is 422. This route is **replayed**
on the standby: add `("PUT", rf"^/api/library/items/{_UUID}/tags$")` to
`_REPLAYED` in `standby.py` -- a correction is a personal change like a
playlist edit, and loses nothing by being applied later.

### 3.7 The settings block (in `Ai.tsx`, under Night work)

```
Fix missing song names
Homesh can recognise a song by its sound and fill in a missing artist,
title or album. To do that it sends a fingerprint of the sound -- not the
file, its name or where it is kept -- to AcoustID and MusicBrainz, two
public music databases.

  [ ] Look up songs with missing names
  1,240 looked up - 1,020 found - 220 not recognised
```

With no key: the switch is disabled and one line says "Set ACOUSTID_API_KEY in
the .env file on the PC first."

### 3.8 Tests

Mock HTTP with `httpx.MockTransport`; fake `fpcalc` by monkeypatching the
function that runs it.

- a confident match stores four `musicbrainz` rows and no `file` row changes;
- a weak match stores only `mb.checked`, and the planner does not pick the
  track again;
- nothing is planned while the setting is off or the key is absent;
- the request to AcoustID contains no file name or path (assert on the
  recorded request body);
- a 429 fails the job without storing anything;
- the reading order: `user` beats `file` beats `musicbrainz` beats `ai`;
- `PUT .../tags` refuses an unknown key, an item outside the scope (404), and
  writes only `user` rows; null removes the person's value and the next origin
  shows through;
- `standby.classify("PUT", "/api/library/items/<uuid>/tags")` is `replayed`.

---

## 4. Threats specific to this track

| Threat | What stops it |
|---|---|
| A crafted audio file attacks ffmpeg or fpcalc | both run in the worker container (no secrets beyond what it needs, read-only root, memory and pid limits), with a minimal environment, a timeout, and the path after `--` or as a list argument |
| The lookup reveals what the household listens to | off by default, explained before it is turned on, sends a fingerprint and a length only; noted here plainly: the databases' operators can see which recordings were looked up from this address |
| A wrong lookup overwrites good tags | never overwrites; shown only where the file and people gave nothing; labelled; one tap to reject for good |
| A person's tag edit carries markup or is huge | length limits, a fixed key list, rendered as text |
| Level-matching makes something painfully loud | it only ever turns down |
