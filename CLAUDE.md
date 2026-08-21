# Agent context — Homesh

Everything an agent needs to resume this project cold. Keep it current: update the
status table and the task list whenever a phase moves.

---

## What this is

A self-hosted media server for **video, music, photos and documents**, built because
Plex failed on eight specific counts. Owner: Shahaf (`shahafdu@gmail.com`). Licence
AGPL-3.0. Repo: `github.com/shahafdu/Homesh` (public).

Named **Homesh** (home + mesh), ratified 10 August 2026. The rename touched the
package, image, logger names, session cookie, WebAuthn RP name, cache path, the
Postgres role and both databases. `RP_ID` stayed `localhost` deliberately —
changing it would invalidate every registered passkey.

### The eight problems it exists to solve

1. Paid licence for advanced features → AGPL, no tiers
2. Separate apps for music and video → one app, one catalog
3. Not suited to documents → documents are a first-class kind
4. Shows metadata only, so corrupt tags mean a mystery file → **filenames are indexed,
   displayed and searchable; metadata records its `origin` and never overwrites**
5. Inconvenient folder browsing → **the real directory tree is a primary view**
6. Wants AI search, AI categorisation, and Winamp playlist import
7. Wants phone-as-remote with playback on TVs or the Denon, and availability when the
   PC/RAID is off
8. No Google Photos access

---

## Where the real setup lives

`.local/SETUP.md` (untracked) holds the actual deployment: device inventory by
room, receiver configuration, and zone wiring. `.env` holds addresses and
secrets. Read both when resuming; never copy their contents into a tracked file.

---

## Working agreement

- **Do not ask Shahaf to test increments.** Verify via CI. Involve him only for real
  system integration, permissions, tokens, accounts, software installs, hardware.
- **Send mockups** for UI feedback, not a running app to click through.
- **Commit and push without asking.** Keep work backed up; keep CI green.
- He is product manager and QA. Do the heavy lifting; surface genuine blockers only.

---

## Current status

**[`STATUS.md`](STATUS.md) is the living task list** — what works, what is waiting
on Shahaf, what is known broken, and what comes next. Read it first when resuming,
and keep it current: this project is large enough that a change in one place
routinely breaks another, and the tracker is what makes that visible.

| Phase | State | Notes |
|---|---|---|
| 0 · Foundation | ✅ done | Compose stack, schema, passkeys, CI |
| 1 · Sources & catalog | ✅ done | Local connector, scanner, folder tree, search |
| 2 · Playback | ✅ done | Signed URLs, range streaming, thumbnails, audio player with folder queue, video direct-play, photo and document viewer |
| 3 · Control tower & zones | 🔨 | Denon control + zones/sessions done and verified against the real receiver; control tower UI next. 
| 4 · TV apps | ✅ | Android TV shell built, verified end to end on an emulator (pair → socket → play → position reported). No Gradle: aapt2/javac/d8/apksigner, 21 KB, built by CI every commit. webOS dropped; Tizen only if a screen turns out to have no box |
| 5 · Playlists & music intelligence | ⬜ | Winamp `.m3u`/`.pls` import with path repair, AcoustID tag repair |
| 6 · AI | ⬜ | See **AI design decisions** below — agreed with Shahaf, not yet built |
| 7 · Photo availability | ⬜ | RAID→Drive sync, Wake-on-LAN, Takeout gap-fill |
| 8 · Optional transcode | ⬜ | May never be needed — see §3.2 of ARCHITECTURE |
| 9 · Public release | ⬜ | Docs, screenshots, name decision |

**Tests: 312 passing. Migrations: 015. Lint: clean. CI green.**

### AI design decisions — agreed, not yet built

Settled in conversation and load-bearing for the smart-home work that follows.

**Tiered by cost, and gated by permission.** Local model for small work,
OpenRouter for the middle, a paid provider only for genuinely hard questions —
and the paid tier needs approval, granted per account rather than to everyone.
Provider is pluggable: your own key (Claude / Gemini / OpenAI), OpenRouter
including its free tier, a local model on the PC or a Pi, or none at all with the
feature simply absent.

**Model knowledge beats audio analysis for music.** A language model given
`Artist — Title` already knows what the song is; it does not need to hear it.
Audio analysis only earns its keep for recordings no model has heard of. This
corrected an earlier assumption of mine and makes "play something mellow" cheap:
roughly 95k tokens to classify the whole library once.

**Everything is cached as tags.** Judgements land in `item_metadata` with
`origin='ai'`, beside the file's own tags and never overwriting them. Embeddings
and previous search results are cached too — image embeddings, "same person as
in this photo", earlier answers. Later questions filter locally first and only
ask about what is not yet known, which also makes it work offline once warm.

**The AI holds no privileges of its own.** It calls the same authenticated API
the interface calls, as the user, so scope is enforced by the code that already
enforces it rather than by the model behaving well. It cannot add or remove
rooms, cannot change permissions, and cannot act beyond the asking user's own
access — enforced by the tool list and the API, never by prompt.

**Every action is auditable from inside the app.** A history of what the AI did,
readable in the interface — not something to go hunting for in logs on the PC.

**Backups, because the AI can change the database.** Daily, a week back, plus
retained points at two weeks and a month. Restore from within the app,
administrators only.

### Outstanding tasks

- [ ] Gapless audio playback and ReplayGain (the player is functional, not yet gapless)
- [ ] Metadata extraction — duration, artist, album (durations currently come from
      the media element, so listings show none)
- [ ] Automatic rescan on file change (scanning is manual)
- [ ] Google Drive connector — **blocked on Shahaf creating the OAuth client**
- [ ] Go agent + WireGuard (Mode B split; only needed when the core moves off the PC)
- [ ] Deploy to Oracle Always Free (phase 0.5)
- [ ] Install the TV app on the real boxes — `docs/TV_APP.md` has the ADB steps.
      A box installed from a different machine must be uninstalled first, because
      the signing key is per-machine and never committed
- [ ] Database backups and in-app restore — prerequisite for the AI work
- [ ] AI activity history, readable in the app

### Waiting on Shahaf

1. **Share the three Drive folders as Editor**, not Viewer — a viewer cannot
   grant access it does not have, so "Create a Drive link" fails with exactly
   that message until this changes. Leave "Editors can change permissions and
   share" enabled.
2. **Google Cloud OAuth client** — steps are in the session history; scope is
   `drive.file`, redirect `http://localhost:8080/api/sources/gdrive/callback`.
   Credentials go in `.env` as `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.
3. **A real media folder** to point at instead of the synthetic fixture.
4. **Repo visibility** decision (public today).

---

## Target hardware

The deployment profile this is designed against, kept generic on purpose — the
specifics of the house it runs in live in `.local/SETUP.md`, which is not
committed. A public repository should not publish which devices sit in which
rooms, when storage is unpowered, or which control interfaces are left open.

| Role | Profile |
|---|---|
| Server / agent host | A low-power mini PC — 4 efficiency cores, **no discrete GPU**. Sizing assumptions follow from this |
| Storage | Directly attached, **intermittently powered by design**. The availability model exists because of it |
| Always-on core | Free-tier arm64 cloud instance, or a Pi (§3.4 of ARCHITECTURE) |
| Displays | Several screens, mixed platforms. Many are fed by **Android TV set-top boxes** — the easy target, since a box takes Cast with no install and side-loads routinely. Target the box where one exists; a TV's own platform only where there is none |
| Audio | **Denon AVR-X1600H**. Address is DHCP and lives in `.env` as `DENON_HOST` |
| Network | DHCP throughout. **No real addresses in tracked files** — CI enforces this |

### Denon protocol facts, measured not assumed

These are properties of the model, documented by Denon and useful to anyone with
the same receiver — not facts about one household.

- AirPlay 2 ✅, HEOS ✅, **Chromecast ❌**
- **Exactly one HEOS player** → cannot run two network streams at once
- ZONE2 cannot take HDMI/coax/optical — network or analog only
- Two protocols: **HEOS CLI on :1255** (JSON, `play_stream` takes a URL) and the
  **Denon AVR telnet on :23** (power, volume, zones) — different protocols, same box
- Port 23 answers from standby only when Network Control is set to "Always On"
- Re-measure with `tools/probe-denon.ps1` (SSDP discovery, no IP needed)

**Consequence:** different audio in two zones requires two transports — a TV app
over HDMI for the main zone, HEOS for ZONE2.

---

## Architecture in brief

Full reasoning in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). The load-bearing ideas:

1. **The catalog is always up; the bytes may not be.** Index, thumbnails and search
   vectors live on an always-on node, so the whole library stays browsable and
   searchable with the RAID off. Only playback of local-only files is gated.
2. **The server owns playback state.** Sessions bind to *zones*, not devices — the phone
   can die mid-song and music keeps playing; moving rooms is a rebinding, not a re-cast.
3. **Identity, not IP.** Devices keyed by stable identity (SSDP USN, app instance id).
   Home components dial *outbound* only; no inbound ports ever.
4. **Direct play first.** Endpoints decode; the server does not encode. Remux (container
   swap) is nearly free; real transcode is a rare edge case deferred to phase 8.
5. **Runs anywhere.** Multi-arch images; topology is configuration, not code.

### Externally imposed constraints

- **Google Photos API closed to third parties (March 2025).** `photoslibrary.readonly`
  removed; apps only see media they uploaded. Hence the RAID copy is the photo source.
- **Personal Gmail + `drive.readonly`** ⇒ refresh tokens revoked every 7 days. Use
  `drive.file` instead; no verification, no expiry clock.
- **Oracle halved its free tier** (June 2026) to 2 OCPU / 12 GB, silently. Portability
  is therefore an architectural requirement, not a preference.

---

## Layout

```
server/app/       config, db, main, auth, security, prefs, library, scanner,
                  signing, stream, sources/{base,local}
server/migrations 001_init, 002_natural_sort, 003_search_indexes, 004_user_prefs,
                  005_renderer_pairing, 006_source_remote_id, 007_access_rules,
                  008_invites, 009_explicit_access, 010_audience,
                  011_device_links
server/tests/     conftest + scanner, library, security, prefs, streaming
web/src/          App, Browser, Settings, api, auth, library, prefs, styles.css
android/          TV shell — Manifest, java/com/homesh/tv/{MainActivity,SetupActivity,
                  Prefs,ServerAddress}, res/, test/ServerAddressTest.java
tools/            probe-denon.ps1, configure-network.ps1, run-tests.ps1,
                  build-tv-apk.sh
docs/             ARCHITECTURE.md, USER_GUIDE.md, TV_APP.md
```

---

## Running and testing

```powershell
.\tools\configure-network.ps1     # derive DENON_HOST + LAN_BASE_URL into .env
docker compose up -d --build      # stack on http://localhost:8080
.\tools\run-tests.ps1             # suite against the homesh_test database
docker compose logs api           # first-run bootstrap code lives here
```

⚠️ **Never point the test suite at the `homesh` database.** Fixtures truncate `users`
and `sources`; doing so once destroyed a registered passkey. `conftest.py` refuses any
database whose name lacks "test", and `run-tests.ps1` pins `homesh_test`.

Docker Desktop is a **per-user** install here — `%LOCALAPPDATA%\Programs\DockerDesktop`,
not Program Files. Its CLI is already on the user PATH; if a shell was started
without it, refresh:

```powershell
$env:Path = "$([Environment]::GetEnvironmentVariable('Path','Machine'));$([Environment]::GetEnvironmentVariable('Path','User'))"
```

After a power cut the engine may be down. Start it with
`& "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe"` and wait for
`docker info` to answer; the stack restarts itself.

GitHub API access (for CI status) works via the token in Windows Credential Manager:

```bash
TOKEN=$(printf "protocol=https\nhost=github.com\n\n" | git credential fill | grep ^password= | cut -d= -f2-)
```

`gh` is installed but not logged in — its token lacks `read:org`. Use the REST API.

---

## Conventions

- **Python**: FastAPI, SQLAlchemy Core with `text()` (SQL-first, matching the plain-SQL
  migrations). Ruff with `E,F,I,UP,B,S`; `B008` ignored (FastAPI `Depends` idiom).
- **SQL**: plain migrations, transactional, tracked in `schema_migrations`. Idempotent.
- **TypeScript**: React, strict mode, no CSS framework — tokens in `styles.css`.
- **Commits**: explain *why*, not what. Record measurements and the reasoning behind
  thresholds.
- **Comments**: explain decisions and non-obvious constraints; never narrate the code.

### Design rules the code must not violate

- Filename and path are first-class, always displayed, never replaced by metadata
- `item_metadata.origin` distinguishes file / musicbrainz / ai / user
- Vanished files are marked unavailable, never deleted
- No media URL is guessable or long-lived
- No inbound ports at home; agents dial out
- Path confinement checked *after* symlink resolution
- **No secure-context-only browser APIs.** Screens reach the server over plain
  http at a LAN address, which is not a secure context: `crypto.randomUUID`,
  `navigator.clipboard` and friends are undefined there. They work on the
  developer's localhost and fail on every device in the house — use
  `randomId()` from `web/src/id.ts` and guard the rest
- **Access is granted, never assumed.** An account reaches only what it has been
  given. `all_library` / `all_zones` store "everything" as its own fact, so an
  empty rule list means empty — the opposite default fails silently and totally
- **Every folder and room has an audience** — everyone / admins / selected —
  applied as a ceiling *before* personal grants, so whole-library access means
  everything open to the household, not everything on disk. An undecided
  audience (NULL) reads as admins-only: folders arrive by discovery, so they
  must arrive closed
- **The owner is fixed.** One account is `is_owner` and cannot be demoted,
  restricted or removed by anyone, itself included. Admin is grantable so a
  second adult can manage the house; ownership is not, so granting it is never a
  route to losing the house. Enforced by a partial unique index, not convention
