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

Then:

```bash
docker compose up -d --build
```

Open <http://localhost:8080>.

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

---

## 7. Keeping the catalog current

Scanning is manual for now: go to the root screen and press **Rescan** next to a source.
A rescan never creates duplicates, and files that have disappeared are marked *offline*
rather than deleted — so the catalog still remembers they exist and where.

**Not yet:** automatic rescanning on file changes.

---

## 8. Troubleshooting

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

---

## 9. Playing things

**Music.** Click a track and the whole folder queues, so playing one file behaves like
an album. The player bar stays put as you browse elsewhere. It has play/pause,
previous/next, seeking and volume.

If a file turns out to be corrupt, the player says so and moves to the next one rather
than stalling on it.

**Video, photos and documents** open in a full-screen viewer. Arrow keys move between
items of the same kind in that folder, Escape closes. Video is *direct play* — the
original file, decoded by your browser, with nothing re-encoded in between.

## 10. Not built yet

- Google Drive and Google Photos
- Casting to a TV or the Denon receiver; multi-room zones
- Playlist import, AI search, metadata repair
- Gapless playback, and durations shown in listings

The [architecture document](ARCHITECTURE.md) covers all of it, including *why* certain
things work the way they do — for instance why your receiver can't play different
network audio in two zones at once, and what we do about it.
