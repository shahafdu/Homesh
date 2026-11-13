# 06 - Library upkeep, photo availability, and the converter sandbox

Read [`README.md`](README.md) first. None of these tasks needs the AI tracks.

| Task | Section |
|---|---|
| L1 Notice changed folders on the PC as they happen | 1 |
| L2 Notice changes on Drive without walking it | 2 |
| L3 Copying the photo drive to Drive | 3 (a decision and a setup, no code) |
| L4 Wake the PC from Homesh Connect | 4 |
| L5 Google Takeout import | 5 |
| A5 The converter sandbox | 6 |

What exists today: `server/app/upkeep.py` rescans every source once a day
(`scan_forever`), through `library._scan_then_extract`. `scanner.scan_source`
scans a whole source. Granted folders are mounted read-only under `/library`
from `docker-compose.override.yml`, written by `tools/grant-folder.ps1`.
`tools/follow-storage.vbs` already runs a hidden PowerShell check on a
schedule; `tools/start-homesh.ps1` registers such tasks.

---

## 1. L1 - noticing changed folders

### 1.1 Why it is built this way

A container cannot watch the Windows folders: a change made on Windows raises
no file event inside Docker's virtual machine. So the watching happens on the
Windows side, and the server is only told "this folder changed".

It is told **without a new network route**. The watcher runs a command inside
the server's container (`docker compose exec`), which sets a flag in the
database. Nothing new listens on the network, there is no token to guard, and
no request can name a path: the command accepts only the name of a folder that
is already granted.

The scan that follows is the ordinary whole-folder scan, a little sooner.
Scanning one sub-folder would be faster, but the scanner decides what has
vanished by comparing everything it saw against everything it knew, and
getting that wrong marks good files as missing. Not worth it for a scan that
takes a minute at night.

### 1.2 Migration `scan_requested`

```sql
ALTER TABLE sources ADD COLUMN IF NOT EXISTS scan_requested_at TIMESTAMPTZ;

-- Small facts the upkeep loops need to remember between runs.
CREATE TABLE IF NOT EXISTS upkeep_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Add `DELETE FROM upkeep_state` to the `db` fixture.

### 1.3 Server

`server/app/notify.py` (new), run as `python -m app.notify <mounted-name>`:

- the argument must match `^[A-Za-z0-9 _.()-]{1,80}$` and contain no `/`,
  `\` or `..`; otherwise exit 2;
- find the source registered for that mounted folder (read
  `library._register_mounted` to see how a directory under `/library` maps to
  a row in `sources`, and look it up the same way); none: exit 3;
- `UPDATE sources SET scan_requested_at = now() WHERE id = :id`; exit 0;
- print nothing but one line on error. It never scans by itself.

`upkeep.py`: a new loop `requested_scans_forever()`, started in `lifespan`
beside `scan_forever` and cancelled with it. Every 60 seconds:

```sql
SELECT id, name FROM sources
WHERE scan_requested_at IS NOT NULL
  AND scan_state IS DISTINCT FROM 'running'
  AND scan_requested_at < now() - interval '2 minutes'      -- let a copy finish
  AND (scan_ended_at IS NULL OR scan_ended_at < now() - interval '15 minutes')
ORDER BY scan_requested_at
LIMIT 1
```

For the row found: clear `scan_requested_at`, then `await _scan_one(id, name)`
(the existing function). One at a time. Not on the standby
(`standby.is_standby()` -> the loop does nothing).

The two intervals are the debounce: a large copy raises thousands of events
over minutes and must produce one scan, and a folder that changes all day is
scanned at most every 15 minutes.

### 1.4 Windows side

`tools/watch-folders.ps1` (new; **pure ASCII**), started hidden at sign-in by
a `.vbs` launcher written like `follow-storage.vbs`, and registered by
`start-homesh.ps1` in the same way it registers the existing tasks (a
`-Status` run reports whether it is running).

What it does:

1. Dot-source `homesh-common.ps1`. Read the granted folders and their mounted
   names from the override file (reuse the function `grant-folder.ps1` uses to
   read it; move that function into `homesh-common.ps1` if it is not there).
2. For each folder that exists, create a `System.IO.FileSystemWatcher` with
   `IncludeSubdirectories = $true`, `InternalBufferSize = 65536`, and
   `NotifyFilter = FileName, DirectoryName, LastWrite, Size`. Subscribe to
   Created, Deleted, Renamed, Changed and Error with `Register-ObjectEvent`.
   Every event, including Error (a buffer overflow means "many things
   changed"), marks that folder's mounted name as dirty in a hashtable.
3. Loop every 30 seconds:
   - for each dirty name whose last notification was more than 5 minutes ago,
     run `docker compose exec -T api python -m app.notify <name>` from the
     repository folder, clear the mark on exit code 0, keep it otherwise;
   - for each granted folder with no live watcher (an external drive that was
     off): if the path exists now, create its watcher and mark it dirty; if a
     watched path has stopped existing, dispose of its watcher;
   - re-read the override file when its modified time changes (a folder was
     granted or removed).
4. Log to `.local\watch-folders.log`, at most 1 MB, then start over. Log
   mounted names and exit codes only -- never a file name.
5. Never throw out of the loop: every pass is inside `try/catch`.

A second copy must not run: take a named mutex at start and exit if it is
already held.

### 1.5 Tests

- `notify`: a valid name sets the flag; an unknown name exits 3; `../x`, an
  empty string and a 100-character name exit 2;
- the loop's query: a request 1 minute old is not picked; 3 minutes old is; a
  source scanned 5 minutes ago is not; a running scan is not;
- the standby does nothing;
- `tools/run-tests.ps1` already checks that `.ps1` files are ASCII where the
  project requires it; make sure the new script passes that check.

---

## 2. L2 - Drive changes without walking Drive

Today every Drive folder is listed in full once a day. Drive can instead say
what changed since last time.

`server/app/sources/gdrive.py`, new functions using the existing credentials
and client helpers:

```python
def start_token(key_path: Path) -> str
    # GET https://www.googleapis.com/drive/v3/changes/startPageToken
def changes_since(key_path: Path, token: str) -> tuple[list[dict], str]
    # GET .../drive/v3/changes?pageToken=...&pageSize=500
    #   &fields=nextPageToken,newStartPageToken,changes(fileId,removed,file(parents,trashed))
    #   &includeItemsFromAllDrives=true&supportsAllDrives=true
    # follows nextPageToken; returns (changes, the new start token)
```

Verify both against Google's current documentation before writing them.

`upkeep.py`, new loop `drive_changes_forever()`, every 15 minutes, primary
only, only when a Drive key file exists:

1. Read `upkeep_state['drive.page_token']`. Absent: store `start_token()` and
   stop (the daily sweep covers everything before now).
2. `changes_since(token)`. No changes: store the new token, stop.
3. For each change, find the source: `SELECT DISTINCT source_id FROM replicas
   WHERE remote_id = :file_id`; if none, check whether any of `file.parents`
   is a Drive source's own root id (`sources.remote_id`). A change that maps
   to nothing (a new file deep in a sub-folder) marks **every** Drive source.
4. `UPDATE sources SET scan_requested_at = now()` for the sources found. The
   loop in section 1.3 does the rest, with its debounce.
5. Store the new token **after** step 4 succeeds, so a failure repeats the
   same changes next time instead of losing them.
6. An HTTP error: log once, keep the old token, try again in 15 minutes. A
   token Google rejects as too old (HTTP 400 or 410 on the token): delete it;
   the next pass starts fresh.

The daily sweep stays exactly as it is. This only makes most changes appear
within about a quarter of an hour.

Tests (`test_gdrive.py`, with `httpx.MockTransport` as the existing Drive
tests do): the first run stores a token and requests nothing; a change to a
known file marks its source only; an unmappable change marks every Drive
source; a failed request keeps the token; a rejected token is dropped; paging
follows `nextPageToken`.

---

## 3. L3 - copying the photo drive to Drive

**No code in Homesh.** The server's Drive account is a service account, which
owns no storage and cannot create files, and the server's access to the
library is read-only by design. Something else does the copying; Homesh then
sees the Drive copy as a second copy of the same file through the existing
fingerprinting, and plays whichever copy is reachable.

The owner chooses one, and it is set up once on the PC:

| Way | Cost | Notes |
|---|---|---|
| **Google Drive for desktop**, mirroring the photo folder | none | maintained by Google; recommended |
| `rclone` on a schedule, with the owner's own Google sign-in | a scheduled task and a token to keep | more control over what is copied |

Either way: the destination folder on Drive is shared with the Homesh service
account like any other Drive folder (see the user guide), and it appears as a
folder in Homesh after the next scan.

The implementer's part is documentation only: a section in
`docs/USER_GUIDE.md`, "Keeping photos reachable when the PC is off", describing
both ways in general terms (no account, folder name or path from this house).

---

## 4. L4 - waking the PC from Homesh Connect

### 4.1 What it does

When the main server is off and the standby is answering, a person on the
house Wi-Fi can press "Wake the main server". The phone sends a wake-on-LAN
packet; a minute later the app is talking to the PC again.

It works only from inside the house network (the packet is a local
broadcast), and only if wake-on-LAN is enabled in the PC's firmware and
network adapter -- the owner's part, described in the user guide.

### 4.2 Configuration

`.env` on the PC: `WAKE_MAC=` the PC's network adapter address. It identifies
the machine, so it lives only in `.env` (README rule 5); `.env.example` gets
the empty variable and one ASCII comment. `config.py`: `wake_mac: str = ""`,
validated as six hex pairs when not empty.

`/tv.address` in `main.py` already returns the apps list, the standby and the
outside address **only to a signed-in caller**. Add `"wake": {"mac": "..."}`
under exactly the same condition, omitted when unset. The standby must have
it to offer the button, so `tools/deploy-standby.ps1` copies `WAKE_MAC` into
the standby's environment the same way it carries the other values it needs.

### 4.3 Android (Homesh Connect)

Read `docs/PHONE_APP.md`, `Reach.java` (which address answers) and the
activity that hosts the page and its bridge.

- When the app learns the address (it already stores what `/tv.address`
  returns), store the MAC with the other remembered values, the same way.
- New class `Wake.java`: `static void send(String mac)` builds the 102-byte
  packet (six `0xFF` bytes, then the six address bytes sixteen times) and
  sends it by UDP to `255.255.255.255` port 9 and to the Wi-Fi network's
  directed broadcast address, three times, 100 ms apart, on a background
  thread, with `setBroadcast(true)`. A unit test checks the bytes
  (`android/test/WakeTest.java`, run by `build-tv-apk.sh` like the others).
- Bridge object `HomeshWake`: `available()` is true when a MAC is stored and
  the phone is on Wi-Fi; `send()` checks `Origins.same(...)` then calls
  `Wake.send`. No new permission is needed beyond what the app has for
  networking; check `ACCESS_WIFI_STATE` is declared if the Wi-Fi check needs
  it.
- After `send()`, the app probes the PC's address every 10 seconds for 3
  minutes (reuse `Reach`), and reloads onto it when it answers.
- Bump `versionCode` and `versionName`; publish the release.

### 4.4 Web

The standby already shows a banner saying the main server is off (find it by
searching the web source for the standby banner text). When
`window.HomeshWake?.available()` is true, add a button in it:

```
The main server is off. You can browse and play what is in the cloud.
[ Wake the main server ]
```

Pressed: the button becomes "Waking... this takes about a minute" (disabled,
`role="status"`). After 3 minutes without the app switching over: "It did not
wake. It may be unplugged, or this phone may not be on the house Wi-Fi.
[Try again]". In a browser, or away from Wi-Fi, the button is absent.

### 4.5 Tests

- `/tv.address`: `wake` is present for a signed-in caller when set, absent for
  a stranger, absent when unset (extend `test_tv_app.py`);
- an invalid `WAKE_MAC` is refused at start-up with a clear message;
- `WakeTest`: the packet for a known address is byte-for-byte right; a
  malformed address sends nothing.

---

## 5. L5 - Google Takeout import

### 5.1 What it does

Photos that exist only in Google Photos are downloaded by the owner with
Google Takeout, unpacked into a folder, and that folder is granted to Homesh
like any other. Homesh then reads the date and description Google kept in the
small `.json` file beside each photo. Photos already in the library are
joined to their existing copy by the fingerprinting that exists today; only
what is new shows up as new.

A photo Google re-compressed is not byte-identical to the original and will
appear as a second item. That is expected and stated in the user guide.

### 5.2 Recognising the side files

A Takeout side file sits in the same directory as its media file and is named
after it: `<name>.json`, `<name>.supplemental-metadata.json`, or a shortened
form of either (Takeout cuts long names). New module
`server/app/takeout.py`:

```python
def sidecar_for(media_name: str, names_in_dir: set[str]) -> str | None
    # exact "<media_name>.json", then "<media_name>.supplemental-metadata.json",
    # then any *.json whose name starts with the first 46 characters of
    # media_name. None when nothing matches or more than one candidate does.

def read_sidecar(data: bytes) -> dict
    # json.loads on at most 256 KB; anything unparsable -> {}.
    # Returns only: taken_at (datetime, from photoTakenTime.timestamp, seconds
    # since 1970, accepted between 1900 and next year), description (str, at
    # most 2000 chars), latitude/longitude (floats, dropped when both are 0).

def is_sidecar(name: str, names_in_dir: set[str]) -> bool
    # a .json file that sidecar_for() would pick for some media file here
```

Verify the field names against a real Takeout export from the owner before
relying on them, and keep a small invented sample (no real names, places or
dates) as a test fixture.

### 5.3 Using them

- **Scanner** (`scanner.py`): a file for which `is_sidecar` is true is not
  indexed as an item. It is data about another file, not a document.
- **Metadata pass** (`metadata.py`, where photo and video tags are read): for
  a photo or video in a local source, when a side file exists, read it and
  store with `origin='file'`: `takeout.taken_at` (ISO text),
  `takeout.description`, `takeout.latitude`, `takeout.longitude`.
- **The content date** (`items.created_at`): EXIF wins when the file has it.
  When it does not, the side file's `taken_at` is used in preference to the
  file's modified time (an unpacked archive's modified times are the day it
  was unpacked). Find where `created_at` is set today and add this one case.
- Nothing is written to the Takeout folder. Nothing is moved.

No new route and no new screen. In the Folders page (`Sources.tsx`), a folder
in which side files were found shows one extra muted line: "Dates and
descriptions are read from the Google Takeout files in this folder." (The
count of side files read can be kept in `upkeep_state` under
`takeout.<source id>`.)

### 5.4 Tests (`test_takeout.py`)

- `sidecar_for`: the exact name, the supplemental form, a shortened name, two
  candidates (-> None), none;
- `read_sidecar`: a good file; garbage; a 1 MB file (ignored); a timestamp in
  the year 1800 (dropped); 0,0 coordinates (dropped);
- a scan of a directory with `a.jpg` and `a.jpg.json` indexes one item;
- a photo without EXIF takes its date from the side file; one with EXIF keeps
  the EXIF date;
- a stray `notes.json` with no matching media file is still indexed as a
  document.

---

## 6. A5 - the converter sandbox

### 6.1 Why

`server/app/documents.py` runs LibreOffice inside the server's container to
turn office files into PDFs. LibreOffice has a long history of bugs in which a
crafted document reads local files or runs code, and anyone with edit rights
on a shared Drive folder can put a document in the library. The November 2026
review gave the child process a minimal environment; this finishes the job by
moving it to a container that has nothing worth stealing: no secrets, no
library, no database, no network.

### 6.2 Image and compose

Split `server/Dockerfile` into stages so LibreOffice is only in the image
that needs it:

```
base       python, ffmpeg, the pinned requirements, the app code   (no LibreOffice)
server     FROM base -> the web build, the user, CMD uvicorn        (the api service)
worker     FROM server -> requirements-worker.txt                   (01-ai-foundations)
converter  FROM base -> LibreOffice and fonts, CMD python -m app.converter
```

`docker-compose.yml`:

```yaml
  converter:
    build:
      context: .
      dockerfile: server/Dockerfile
      target: converter
    restart: unless-stopped
    # Nothing: no env_file, no keys, no database address.
    environment: {}
    networks: [convert]
    expose: ["8091"]
    read_only: true
    tmpfs:
      - /tmp:size=1g
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    pids_limit: 128
    mem_limit: 1g
    cpus: 1.0

networks:
  convert:
    internal: true        # no route out of this network
```

Add `networks: [default, convert]` to `api` and to `worker`. The converter is
on `convert` only, so it can reach neither the database nor the internet, and
nothing but those two services can reach it.

The CI job that builds the image must build all the targets; read the
workflow and extend it. `tools/deploy-standby.ps1` and the standby's compose
file must run the converter too, or documents stop opening there.

### 6.3 `server/app/converter.py` (new; runs only in the converter)

A small FastAPI app, `uvicorn` on port 8091. It imports nothing from the rest
of the application except `childenv`.

| Route | Body | Answers |
|---|---|---|
| `GET /health` | - | `{"ok": true}` |
| `POST /convert?ext=<ext>` | the document's bytes, at most 60 MB | the PDF's bytes, `application/pdf` |

`/convert`:

1. `ext` must be in the fixed list that `documents.convertible()` accepts
   (move that list to a module both sides import, or duplicate it with a test
   that the two are equal). Otherwise 415.
2. Read the body in chunks; past 60 MB answer 413.
3. One conversion at a time (`asyncio.Lock`); a waiter gives up after 60 s
   with 503.
4. Make a fresh temporary directory. Write the input as `input.<ext>` -- the
   name is always this, never anything from the request. Run the same
   `soffice --headless --convert-to pdf` command `documents._run_soffice` runs
   today (move it here), with `childenv.child_env(HOME=<a fresh profile
   directory inside the temporary one>)`, in its own process group
   (`start_new_session=True`), with a 120-second timeout.
5. On timeout or a non-zero exit: kill the whole process group, answer 422
   with the plain text "could not convert this document".
6. On success: read the PDF (at most 300 MB, else 422), answer it.
7. In a `finally`: kill anything left in the process group, delete the
   temporary directory.

Nothing is logged except the extension, the sizes, the duration and the
outcome.

### 6.4 The server side

`server/app/convertclient.py` (new):

```python
class ConverterUnavailable(Exception): ...
class NotConvertible(Exception): ...
async def to_pdf(data: bytes, ext: str) -> bytes   # timeout 150 s
```

`CONVERTER_URL` is a new setting, default `http://converter:8091`.

`documents.py`: where it calls `_run_soffice` today, read the source file's
bytes (it already has them or a path) and call `to_pdf`; write the result into
the cache exactly as now. `ConverterUnavailable` -> 503 "Documents can't be
shown right now. Try again in a minute."; `NotConvertible` -> the same
response the route gives today for a failed conversion. **There is no fallback
to converting inside the server**: delete `_run_soffice` from `documents.py`
and remove LibreOffice from the server's image.

The web viewer already shows the server's message when a document fails to
open; check that the 503 text appears as a sentence and not as a blank page.

### 6.5 Tests

- `test_converter.py`, against the app in `converter.py` with the function
  that runs soffice monkeypatched: a good conversion returns the bytes; an
  unknown extension -> 415; an oversized body -> 413; a timeout -> 422 and the
  temporary directory is gone; the input file's name on disk is always
  `input.<ext>`;
- `test_documents` (extend the existing document tests) with a fake
  `convertclient`: the cached PDF is written; `ConverterUnavailable` -> 503;
- a test that reads `docker-compose.yml` and asserts the `converter` service
  has no `env_file`, an empty `environment`, no `volumes`, no `ports`, and is
  only on the internal network. This is the property the task exists for; a
  later edit must not undo it quietly.

### 6.6 What is still possible, stated plainly

A document that takes over LibreOffice now finds itself in a container with a
read-only filesystem, no credentials, no library, no way out to the network,
and limits on memory and processes. It could tamper with the PDF it returns
for that one document. The process group is killed after every conversion, so
nothing it starts survives to see the next document.

---

## 7. Threats specific to this track

| Threat | What stops it |
|---|---|
| The change notice is used to make the server read somewhere new | it is not a network route; it takes the name of an already-granted folder and nothing else; it only sets a flag |
| A flood of file events keeps the PC scanning | two-minute settle time, one scan per folder per 15 minutes, one scan at a time |
| The watcher's log leaks file names | it logs folder labels and exit codes only, in `.local/` |
| The PC's network address ends up in the repository | `WAKE_MAC` is read from `.env`, returned only to signed-in callers, never in a tracked file |
| A Takeout side file is hostile | size limit, strict parsing into a fixed set of typed fields, ranges checked, stored as text and rendered as text |
| A hostile document | section 6 |
