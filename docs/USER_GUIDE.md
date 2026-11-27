# Homesh — User Guide

How to install, run and use it. Written against what exists today; features still
being built are marked **not yet**, so nothing here promises something that isn't there.

---

## 1. What you need

- **Docker** — Docker Desktop on Windows or macOS, Docker Engine on Linux
- A folder of media to point it at
- A browser that supports passkeys (Chrome, Edge, Safari, Firefox)

On Windows, Docker Desktop needs virtualisation. It's usually already enabled in the
BIOS; what's often missing is WSL. If Docker reports *"virtualisation support not
detected"*, run this in an **administrator** terminal and reboot:

```
wsl --install --no-distribution
```

`--no-distribution` is deliberate — Docker Desktop ships its own Linux environment, so
you don't need Ubuntu alongside it.

---

## 2. First run

```bash
git clone https://github.com/shahafdu/Homesh.git
cd Homesh
cp .env.example .env
```

Generate the two keys and paste them into `.env`:

```bash
python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

Run it twice — once for `MASTER_KEY`, once for `SECRET_KEY`. Set a `POSTGRES_PASSWORD`
too, and make sure it matches the one inside `DATABASE_URL`.

Point it at your media by editing `.env`:

```
MEDIA_HOST_PATH=D:/Media          # forward slashes, even on Windows
MEDIA_ROOTS=Library=/media/library
```

`MEDIA_HOST_PATH` is the folder on your machine. `MEDIA_ROOTS` names it inside the
container — `Library` becomes the mount point you see when browsing. **The folder is
mounted read-only**: Homesh indexes and streams, and has no business writing to your
library.

Then, on Windows, double-click **Start Homesh** in the Homesh folder. Elsewhere:

```bash
docker compose up -d --build
```

Open <http://localhost:8080>.

---

## 2a. Starting and stopping it afterwards

Double-click **Start Homesh** in the Homesh folder. It starts Docker Desktop if it
is not running, waits for the server, and prints the addresses to open.

From a terminal, the same thing with a few more options:

```powershell
.\tools\start-homesh.ps1            # start it
.\tools\start-homesh.ps1 -Status    # what is running, and where
.\tools\start-homesh.ps1 -Stop      # stop it; nothing is deleted
.\tools\start-homesh.ps1 -Rebuild   # start it, rebuilding first
```

**Why not just `docker compose up`.** That is most of the job but not all of it.
Docker's virtual machine is rebuilt every time Docker restarts, and Windows only
attaches *fixed* disks to it — so a folder you granted from an external drive comes
back **empty rather than missing**, which looks exactly like an empty folder and
sends you off to check the wrong thing. The script looks inside each granted folder
and re-attaches the drive when one is empty that should not be.

It also waits for the server's own healthcheck instead of guessing. Migrations run
at startup, so how long that takes depends on what changed.

### Waking the PC from your phone

If the PC is asleep, Homesh Connect can wake it. When the PC is not answering and
your phone is on the house Wi-Fi, the app shows **Wake the main server**. Press
it and wait about a minute: the app says when the PC is back, and **Open Homesh**
takes you to it. If it says it did not wake, press **Try again**, and check that
the PC has power.

Setting it up, once:

1. **In the PC's firmware (BIOS)**, turn on *Wake on LAN*. The name varies:
   *Power on by network* or *PCI-E wake* are common.
2. **In Windows**, open Device Manager, then your network adapter's properties.
   On *Power Management*, allow the device to wake the computer. On *Advanced*,
   turn on *Wake on Magic Packet* where it is listed.
3. **Find the adapter's address**: run `ipconfig /all` and copy the *Physical
   Address* of the adapter the PC uses for your network. Put it in `.env` as
   `WAKE_MAC=`, and run **Start Homesh** again.
4. **On the phone**, with the PC on, turn on notifications in Homesh Connect if
   they are not on already. That is how the app is recognised, and so how it
   learns what to wake.
5. **Try it**: put the PC to sleep and press the button.

A network cable is far more reliable for this than Wi-Fi on the PC. Waking from
sleep is what this is for. A PC that was shut down completely may not wake, and
if it does, Homesh starts only once somebody has signed in to Windows.

---

## 3. Creating your account

There is **no public registration**. The first account is created with a one-time code
printed to the server log:

```bash
docker compose logs api
```

Look for a box containing an eight-character code. Enter a username, a display name and
that code, then click **Create passkey**.

Your browser will ask where to store the passkey. On Windows, if only "phone" and
"security key" appear, Windows Hello isn't set up — go to **Settings → Accounts →
Sign-in options → PIN**, add one, and try again. A local passkey makes signing in far
quicker than scanning a QR code every time.

The first account becomes the administrator. Everyone after that must be invited by an
admin. The bootstrap code is regenerated on every restart and retired for good once an
account exists.

> **Note:** a passkey is bound to the exact hostname. One created on `localhost` will not
> work on a domain name later — you'll register a fresh one there. That's how WebAuthn
> works, not a defect.

---

## 4. Browsing

The opening screen lists your **sources**. Click one to enter its folder tree.

Everything you'd expect from a file manager: folders first, breadcrumbs at the top, and
a `..` row to go up.

**Filenames are the label.** Not a subtitle, not a fallback. If a file's tags are
corrupt or missing, you can still see and search exactly what it's called. Filenames are
set in a monospaced face so track numbers line up and extensions are scannable down the
column.

Files sort **naturally**: `track2` before `track10`, and `Episode 9` before `Episode 10`.

### Moving between files

The viewer has a selector in its header: **Photos** (or Videos, Songs,
Documents) and **All**.

- **By kind** — arrows and swipes stay among files like the one you opened.
  Stepping from a photo straight onto a spreadsheet is rarely what was meant.
- **All** — walks the whole folder in order, changing what it draws as the kind
  changes: a photo, then a film, then the song beside them.

Songs play in the viewer, and finishing one moves to the next file. Starting one
stops the app's own player, so you never get two things playing at once.

The choice is remembered on your account, so it follows you to the phone and the
television.

**Swipe left and right** moves between files, on every kind. The exceptions are
the three places a sideways drag already means something: a hex dump paging
across, a PDF scrolling, and a video's scrubber along the bottom edge.

### View modes

Pick from the toolbar; your choice is remembered on your account, so it follows you to
your phone and TV.

| Mode | Best for |
|---|---|
| **Details** | Everyday browsing — name, size, date |
| **Columns** | Folders with hundreds or thousands of files; names only, flowing into as many columns as fit |
| **Small tiles** | Scanning lots of photos quickly |
| **Large tiles** | Photos and video where you want a proper look |

Photos and video show real thumbnails. Files with no artwork — a track without an
embedded cover, a document — fall back to an icon for their kind.

### Sending a file to somebody

A file's menu has **Share…**, which hands the file itself to your phone's share
sheet — WhatsApp, mail, anywhere. Nobody receives a link to your server.

What arrives should open, so some files are changed on the way:

- **Documents** (Word, Excel, PowerPoint and the like) go as PDF.
- **Old video** (WMV, AVI, MOV, MKV, MPEG and the like) goes as MP4, no taller
  than 1080 lines.
- **Old sound** (WMA, AAC, AIFF, AMR and the like) goes as M4A, with its song
  details.

Video and sound are converted as you send them, and the converted copy is not
kept. Wait on that screen while it says *Converting for sending*. This is for
clips and songs: anything longer than ten minutes is refused, and so is a result
too big to attach. Send a Drive link for those. (The limit is
`SHARE_CONVERT_MAX_SECONDS` in `.env`.) Only one conversion runs at a time, so
you may be told the server is busy; try again in a few minutes.

A **Drive link** always shares the original file, never a converted one. MIDI
files hold notes rather than sound and cannot be converted.

On your home network, where the address starts `http://`, a browser will not
open the share sheet. There the menu's button reads **Download as MP4** or
**Download as M4A**, so you can send the copy from your files. To keep the
original instead, open the file and press **Download** at the top.

Converting waits for the main server: it is not done while the standby is
answering.

---

## 5. Searching

Type in the box at the top. Search covers **filenames and folder names**, so searching
`wall` finds everything inside `Pink Floyd/The Wall` even though no filename contains
the word.

It tolerates typos: `beech` finds `beach.mkv`, `trck` finds `track2.mp3`. Results show
the full path underneath, and clicking one takes you to its folder.

Non-Latin filenames work — Hebrew, Arabic, Japanese all search and display correctly.

**Not yet:** searching *inside* documents, or natural-language search over photos
("sunset on a beach"). Both are phase 6.

---

## 6. Settings

The gear icon, top right.

**Colour** — three palettes:

- **Listening Room** — warm graphite lit by valve-amp amber; built for a dim room
- **Studio** — cool slate with a VU-needle teal; quieter, more technical
- **Daylight** — warm paper and indigo; easiest for documents and long sessions

**Appearance** — Match system, Light, or Dark. "Match system" follows your device.

Both are stored on your account, not in the browser, so you set them once.

**Notifications on your phone** — lists the phones that get Homesh's messages
as notifications. In Homesh Connect press *Turn on notifications*: Android asks
for permission, then Homesh opens here asking for the phone's name. Press
*Continue*, and the app asks you to confirm. *Send a test* checks it
works, and *Remove* switches a phone off at once. These buttons are disabled
while the standby is answering, because phones are changed on the PC.

**The bell** — the bell in the top bar opens the last 50 messages from Homesh
and the apps beside it, newest first, and a dot says some are unread. Opening
it marks them read. A message links to the page it names; one from an app that
is no longer set up shows without a link.

**Alerts for administrators** — administrators' phones are also told when
someone signs in, adds or removes a passkey, signs a device in with a code,
joins by invitation, or when a screen asks to be paired; when sign-in is
failing repeatedly or something is knocking too often; and when someone *else*
changes something (playlists, song details, shared links, accounts, folders,
rooms, backups, assistant settings). Changes arrive as one message per person
per ten minutes, saying how many of each kind, never what they were; you are
never told about your own. Settings has two switches for these. A passkey that
may have been copied is always reported.

What it cannot tell you: a device merely joining the house network or the
tailnet, or opening the sign-in page and leaving, is invisible to Homesh,
because every caller reaches it looking like the same address. Your router, or
Tailscale's device approval, is the place for that.

---

## 7. Adding a folder of your own

In the app: **Sources** (the toolbar's ◫ button) → **Choose a folder…**

Windows' own folder picker opens **on the PC that runs Homesh** — the machine the
folders are actually on. Pick one and it appears in the list; press **Rescan** to
index it.

The button works by handing `homesh://add-folder` to Windows, the same way a Zoom
or Spotify link works. That registration happens the first time you double-click
**Add a folder to Homesh** in the Homesh folder, so do that once and the button
works from then on. (Pressing it from a phone does nothing — the picker would have
to open on a screen the folders are not on.)

### What the server can see

**Only the folders you have given it.** Not the drive they are on, not your user
folder, nothing else on the machine — and read-only, so it cannot alter even
those. Anything you have not granted is unreachable rather than merely unlisted.

To check, or to withdraw one:

```powershell
.\tools\grant-folder.ps1 -List
.\tools\grant-folder.ps1 -Revoke music
```

Revoking never touches the files. Remove the source in **Sources** to clear it
from the catalog as well.

### From Google Drive

Share the folder with the Homesh account as **Editor** — not Viewer, which cannot
grant the access the server needs — then press **Look for new folders**.

### A folder on an external drive

Handled automatically, but worth knowing why it needs handling. Windows attaches
only fixed disks to Docker's virtual machine, and only when it starts, so a folder
on a USB drive would otherwise mount as an empty directory rather than as an error.
The script detects a removable drive and attaches the granted folder itself.

The virtual machine is rebuilt when Docker restarts, so after a reboot run **Add a
folder to Homesh** again on that folder — it re-attaches it.

---

## 8. Slideshows

Open a folder of photographs and press **▶ Slideshow** in the toolbar.

It gathers every photo in that folder **and everything below it** — a year, with
months inside it, with a weekend inside that — then asks three things:

| | |
|---|---|
| **Plays** | Forever, or once through |
| **Order** | In order, or shuffled |
| **Each photo for** | 3s to a minute |
| **Transition** | Fade, slide, zoom, cut, or random |

**Random** picks a different transition for each photo, so a long slideshow does
not settle into a rhythm.

Then **Play here**, or **Play in a room…** to send it to a television. A room
running a slideshow advances by itself; the phone that started it can be put
down or taken out of the house.

While it plays: swipe or use the arrow keys to move, space to pause, Escape or
back to leave. The controls fade out and return on any movement.

### Playing forever

The default. It keeps going, fetching more from the folder as it needs them, and
never asks how many there are — so it starts the same whether the folder holds
fifty photos or a hundred thousand.

**Photos will repeat**, and that is the deal. Not repeating would mean
remembering every photo ever shown, for something that by definition never
finishes. On a wall, seeing a good one again is no loss.

- **Shuffled** — every batch is a fresh draw from the whole folder
- **In order** — walks through, and starts again at the beginning when it reaches
  the end

A room does the same thing: when its queue runs out it goes back to the folder
for more, so a television can be left on it. It fetches as the account that
started it, so a folder closed to that person tomorrow stops appearing tomorrow.

### Once through

Ten thousand photos are sent at a time — at five seconds each that is fourteen
hours. If the folder holds more, the screen says so.

**Shuffle then means a random sample of the whole folder**, not a shuffle of the
first ten thousand. With a hundred thousand photos indexed, two shuffled runs of
ten thousand share about a thousand, which is what a genuine sample of
everything looks like.

---

## 9. Keeping the catalog current

Every source is rescanned on a schedule, and you can press **Rescan** next to one in
**Sources** to do it now. A rescan never creates duplicates, and files that have
disappeared are marked *offline* rather than deleted — so the catalog still remembers
they existed and where.

The same sweep also notices when the **same file exists in two places** — the copy on
your PC and the copy on Drive — and joins them into one entry with two copies behind it,
so the library lists it once and plays whichever copy is reachable. Drive states its own
checksum for free; a local file has to be read, so only files that look like a duplicate
of something elsewhere are read at all, a couple of thousand at a time.

**Changes on the PC are noticed as they happen.** A small hidden program that Start
Homesh installs watches each folder you have granted. When files in one change and
then stay quiet for five minutes, Homesh rescans that folder, never more than once
every fifteen minutes. A large copy therefore makes one scan, not hundreds. The
watcher only tells Homesh the *name* of the folder; it gives no path and opens no
network port. `Start Homesh.cmd` with `-Status` says whether it is running.

**Google Drive** is noticed the same way without walking it: every fifteen minutes the server asks Drive what changed and scans the folders affected (all of them, when a change is too deep in a folder to tell whose it is). The daily sweep still runs as a safety net.

### Keeping photos reachable when the PC is off

Photos on a drive that is switched off cannot be opened. Homesh itself never
writes to your library and its Drive account owns no storage, so it cannot make
the copy. Something else on the PC copies the photo folder to Drive, and Homesh
does the rest: it finds the Drive copy of each photo, joins it to the original as
one entry with two copies, and plays whichever one is reachable.

Choose one way, once:

| Way | Cost | Notes |
|---|---|---|
| **Google Drive for desktop**, with the photo folder added under *Folders from your computer* and set to *Sync with Google Drive* | none | Maintained by Google. Recommended. Do not choose *Back up to Google Photos*: Homesh cannot see Google Photos |
| **rclone** on a schedule, signed in with your own Google account | a scheduled task and a sign-in to keep alive | More control over what is copied. Use `rclone copy`, not `rclone sync`, so a drive that is off or empty when the task runs can never empty the Drive copy |

Either way:

1. Set up the copy so the photos land in one folder on your Drive, **keeping
   their file names**. Homesh recognises two copies as one photo only when the
   name, the size and the checksum all match, so a tool that renames or
   re-compresses on the way breaks the join. (Drive for desktop puts the folder
   under *Computers* in Drive rather than *My Drive*; it can be shared from
   there like any other.)
2. Share that folder with the Homesh account as **Editor**, like any other Drive
   folder (see *From Google Drive* in section 7). Homesh looks for newly shared
   folders every hour; an administrator can press **Look for new folders** in
   **Sources** to have it now.
3. A new folder arrives **visible to administrators only**. Decide who else
   sees it in **People** (it is marked *needs a decision*), or the rest of the
   household will not see the Drive copies.
4. Once the folder is scanned it appears in Homesh. Joining the two copies of
   each photo happens in the regular sweep, a couple of thousand photos at a
   time, so a large collection is joined over several days rather than at once.
   Until a photo is joined it shows twice, once from each place.

Nothing here needs the copy to be quick: Homesh notices new files on Drive within
about a quarter of an hour of their arriving, and joins them in a later sweep.

---

## 10. Troubleshooting

**"This file is on a source that is currently offline"**
The catalog knows the file but the machine holding it isn't reachable. Expected when the
RAID or the PC is off — browsing and search keep working regardless; only playback of
that file is blocked.

**The bootstrap code doesn't work**
It's regenerated on every restart. Get the current one with `docker compose logs api`.
If an account already exists, the code is gone for good and new users must be invited.

**Windows Hello isn't offered when creating a passkey**
No PIN or biometric is enrolled. **Settings → Accounts → Sign-in options → PIN**.

**Docker won't start on Windows**
See §1 — this is nearly always WSL rather than the BIOS.

**Nothing appears after adding files**
Press **Rescan**. Check the folder is actually mounted:
`docker compose exec api ls /media/library`

**You switched the RAID off and a folder has gone quiet**
That is expected and Homesh keeps running: the catalog, the rooms, the
playlists and anything on Drive need no disk. **Sources** shows the folder as
*offline — the drive it is on is not connected*, which is a different sentence
from a scan failing, and browsing and searching it carry on working. Only
playing a file that lives nowhere else is blocked.

**There is nothing to do when you switch it back on.** Homesh notices within a
couple of minutes and picks the folder up again by itself, and notices it going
away the same way. That is a scheduled task — *Homesh - follow the storage* —
installed the first time you start Homesh, and it exists because a container is
handed its folders when it is created and cannot be given one afterwards. So
following the storage means rebuilding the container, and the only real question
was who has to notice. Not you.

The grant itself is never lost either way. It stays written down, marked
`# OFFLINE`, in the file that records what this PC has given the server.

**Homesh will not start at all after the RAID was off**
Fixed, but worth recognising if you see it on an older build: switching the
drive off leaves its mount inside Docker's virtual machine connected to
nothing, and Docker then fails with `mkdir /run/desktop/mnt/host/e: file
exists`. `Start Homesh` clears that before mounting now.

---

## 11. Playing things

**Music.** Click a track and the whole folder queues, so playing one file behaves like
an album. The player bar stays put as you browse elsewhere: play/pause, previous/next,
shuffle, seeking and volume. If a file turns out to be corrupt the player says so and
moves on rather than stalling.

In the browser, tracks play one into the next without a gap: the next one is fetched in
the last twenty seconds of the one before, so a live album or a continuous mix is not
broken up by silence. It costs at most one track of extra download.

With **shuffle** on, *next* picks at random and *previous* takes you back to what was
actually playing — not to the track above it in a list you are not listening in.

**Video, photos and documents** open in a full-screen viewer. Swipe or use the arrow
keys to move between files; Escape closes it. A selector in the header decides what
"next" means: only files of the same kind, or **All** — every file in the folder,
switching the viewer as it goes, songs included.

Video plays two ways and the app chooses:

- **Direct play** for anything your browser can decode — the original file, byte for
  byte, nothing re-encoded.
- **Converted as it plays** for the rest: MPEG-2, WMV, AVI, VOB, the formats home
  video actually arrives in. Nothing is stored and nothing is downloaded; the film
  starts in a few seconds. There is a **Position** box rather than a scrub bar, because
  a stream that is still being encoded has no index to seek in — type `12:30` and it
  starts again from there.

**Documents** are drawn page by page in the app rather than handed to the browser,
which is what makes them work on a phone. Word, Excel and PowerPoint files are
converted to PDF first, by a separate converter that runs walled off from everything
else on the server, so a booby-trapped document cannot reach your files or keys.
Long books load the pages you are reading rather than the whole file, so a 140 MB
rulebook opens in seconds. Documents over 60 MB are not converted; download them
instead. If a document says "Documents can't be shown right now", the converter is
not running -- start Homesh again on the PC (`Start Homesh.cmd`) and it comes back.

**Anything unopenable** — a `.MSWMM`, a file with no extension — can still be shown as
text or as hex, whichever answers the question.

---

## 12. Rooms

The **control tower** is the ◈ button. It lists the rooms, what each is playing, and
gives you transport controls for all of them from wherever you are standing.

Send something to a room from the file menu — **Send to a room** — or from the slideshow
setup. The *server* owns the session, not your phone: the phone can run out of battery
mid-album and the music keeps playing, and picking the room up on another phone shows the
same state.

What a room can be:

- **A television with the Homesh app** (§13) — video, music, photographs, documents.
- **The Denon receiver**, over HEOS, for music in a zone with no screen. The receiver has
  exactly one network player, so it can do one network stream at a time; two rooms of
  different music need two transports, which is why the main zone goes over HDMI from a
  TV box instead.
- **A Chromecast**, for the formats Google's receiver accepts. The cast button says when
  a file is not one of them, and points you at a room instead.

---

## 13. The TV app

Open `http://‹your server›/apk` on the television's browser, or side-load it with `adb`
— [`TV_APP.md`](TV_APP.md) has the steps. It finds the server by asking every address on
the network if broadcast does not answer, pairs with a code you approve from your phone,
and updates itself.

It shows its own version on screen while connecting, and the room card in the control
tower shows it too, so you can tell from your phone whether a box has taken an update.

The app has its own video player for the formats the web view cannot decode, which is
most of what a camcorder produces.

---

## 14. Playlists

The ♫ button. Create one, drag to reorder, share it with somebody in the house, or
**import** the `.m3u` and `.pls` files you already have — including Winamp's, whose paths
point at drive letters that no longer exist. Import repairs what it can by matching on
filename and reports what it could not find rather than silently dropping it.

---

## 15. People

**People** (the ◑ button, administrators only) invites somebody by link, and decides what
they can reach: whole library or chosen folders, all rooms or chosen rooms.

Two rules worth knowing:

- **Every folder and room has an audience** — everyone, administrators, or named people —
  and that applies *before* anybody's personal access. So "the whole library" means
  everything the household can see, not everything on the disk.
- **The owner cannot be removed**, restricted or demoted, by anyone, including themselves.
  Administrator is something you can grant; ownership is not, so granting it is never a
  way to lose the house.

---

## 16. Backups

**Settings → Backups**, administrators only.

What is backed up is everything the server *knows* — accounts and their passkeys, who may
see what, playlists, where everybody had got to, and the catalog with all its tags. Not
your media: those are your own files on your own disks, and copying terabytes is a
different job.

One is taken every hour, and they are kept one a day for a week and one a week for five
weeks, so there is always one from about a fortnight back and one from about a month
back. Settings shows them as a dropdown: choose one, then restore, download or delete it. You can take one at any moment, and you should before anything
drastic.

**Restoring** replaces the catalog, the accounts and the playlists with whatever was
there when the backup was taken. It happens in one go — either it all lands or nothing
does — and it saves a copy of the present state first, so restoring the wrong one is
undoable.

**Download a copy.** A backup that lives on the same disk as the thing it protects is
half a backup.

---

## 17. Assistant and AI

The **✦** button, administrators only. It sets who the assistant will ask, and shows what
has been asked. Asking it things from the app is not built yet.

- **Providers.** Whether OpenRouter and Gemini have a key, a **Test** button that sends one
  tiny request to a free model, and what has been spent against the monthly limit. Keys are
  put in the `.env` file on the PC ([how](AI.md)); the page never shows or asks for one.
- **Which model does what.** A list for each kind of work. Add models, move them up and
  down, test them. The first one answers; if it is busy or over its free limit, the next
  does. A model that has stopped being offered, or started to cost money while no limit is
  set, is marked until you remove it. Paid models can only be chosen once a monthly limit
  is set.
- **Night work.** The hours slow jobs may run in, and a pause button. The times are in the
  time zone of the browser that first saves a setting here. Nothing runs at night yet.
- **What was done.** The last 50 requests to a provider: when, who, what for, which model,
  whether it was answered, and how much text it took. Never the question or the answer.

On the standby the page can be read but not changed.

---

## 18. Not built yet

- **Asking the assistant, AI search and tagging.** Designed and agreed — see §7 of the
  architecture document — and deliberately behind backups, which are what make it safe to
  let a model write to the catalog.
- **ReplayGain**, so quiet and loud recordings play at the same level.
- **Gapless playback on the television and in rooms.** The browser player has it; a
  television or the receiver still starts each track on its own.
- **Google Photos.** Its API was closed to third-party apps in March 2025, so the copy on
  your own storage is the source. Nothing to do here but say so.
- **Noticing file changes as they happen**, rather than at the next sweep.
- **An always-on node.** Everything runs on the PC today. The catalog is designed to
  outlive the storage being switched off, and the pieces for splitting it out exist; it
  has not been deployed that way.

The [architecture document](ARCHITECTURE.md) covers all of it, including *why* certain
things work the way they do — for instance why your receiver cannot play different
network audio in two zones at once, and what we do about it.
