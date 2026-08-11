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

| Phase | State | Notes |
|---|---|---|
| 0 · Foundation | ✅ done | Compose stack, schema, passkeys, CI |
| 1 · Sources & catalog | ✅ done | Local connector, scanner, folder tree, search |
| 2 · Playback | ✅ done | Signed URLs, range streaming, thumbnails, audio player with folder queue, video direct-play, photo and document viewer |
| 3 · Control tower & zones | ⬜ | Server-owned sessions, renderer protocol, Denon via HEOS CLI + telnet |
| 4 · TV apps | ⬜ | Android TV, Tizen, webOS from the same React codebase |
| 5 · Playlists & music intelligence | ⬜ | Winamp `.m3u`/`.pls` import with path repair, AcoustID tag repair |
| 6 · AI | ⬜ | CPU-only CLIP/text embeddings, NL search; Whisper on-demand only |
| 7 · Photo availability | ⬜ | RAID→Drive sync, Wake-on-LAN, Takeout gap-fill |
| 8 · Optional transcode | ⬜ | May never be needed — see §3.2 of ARCHITECTURE |
| 9 · Public release | ⬜ | Docs, screenshots, name decision |

**Tests: 88 passing. Migrations: 004. Lint: clean. CI green.**

### Outstanding tasks

- [ ] Gapless audio playback and ReplayGain (the player is functional, not yet gapless)
- [ ] Metadata extraction — duration, artist, album (durations currently come from
      the media element, so listings show none)
- [ ] Automatic rescan on file change (scanning is manual)
- [ ] Google Drive connector — **blocked on Shahaf creating the OAuth client**
- [ ] Go agent + WireGuard (Mode B split; only needed when the core moves off the PC)
- [ ] Deploy to Oracle Always Free (phase 0.5)
- [ ] "Add another passkey" flow — currently a passkey can only be registered while
      creating an account, which is a real gap for a multi-device household

### Waiting on Shahaf

1. **Google Cloud OAuth client** — steps are in the session history; scope is
   `drive.file`, redirect `http://localhost:8080/api/sources/gdrive/callback`.
   Credentials go in `.env` as `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.
2. **A real media folder** to point at instead of the synthetic fixture.
3. **Repo visibility** decision (public today).

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
| Displays | LG webOS, Samsung Tizen, and Android TV — all three get an app from the same codebase |
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
server/migrations 001_init, 002_natural_sort, 003_search_indexes, 004_user_prefs
server/tests/     conftest + scanner, library, security, prefs, streaming
web/src/          App, Browser, Settings, api, auth, library, prefs, styles.css
tools/            probe-denon.ps1, run-tests.ps1
docs/             ARCHITECTURE.md, USER_GUIDE.md
```

---

## Running and testing

```powershell
docker compose up -d --build      # stack on http://localhost:8080
.\tools\run-tests.ps1             # suite against the homesh_test database
docker compose logs api           # first-run bootstrap code lives here
```

⚠️ **Never point the test suite at the `homesh` database.** Fixtures truncate `users`
and `sources`; doing so once destroyed a registered passkey. `conftest.py` refuses any
database whose name lacks "test", and `run-tests.ps1` pins `homesh_test`.

Docker Desktop lives outside the default PATH here. Refresh it first:

```powershell
$env:Path = "$([Environment]::GetEnvironmentVariable('Path','Machine'));$([Environment]::GetEnvironmentVariable('Path','User'))"
```

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
