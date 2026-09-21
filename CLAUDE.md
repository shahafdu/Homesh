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

### Nothing that identifies this setup goes in the repository

**Not secrets only — identifiers too.** This repository is public, and nobody
reading it may learn anything about the owner's network, machines, keys or
accounts: not a password, and equally not an SSH *public* key, a cloud region, a
tailnet or host name, a bucket namespace, an IP address, a device id, or which
room holds what. Something that grants no access can still find or fingerprint
the setup — an SSH server can be asked whether it accepts a given public key, so
a published key identifies the machine that does.

This was broken once, in September 2026: the setup guide printed the standby's
public key for pasting and the docs named its region. The key was replaced on the
machine, the docs were rewritten, and the guards were widened. The owner called
it a security breach, and it was.

- **Documentation describes how to make or find a value, never the value.**
  "Run `ssh-keygen … -f .local\standby_ed25519` and paste the `.pub` file" — not
  the key. "Your region" — not the region.
- **A concrete value belongs in `.local/` or `.env`**, both untracked, and a
  script reads it from there. Commit the script, never its input.
- **Tests and examples use placeholders**: RFC 5737 addresses (192.0.2.x),
  `examplenamespace`, a region nobody here uses.
- **Chat replies follow the same rule**: say where a value lives, do not print it.
- **Before every commit, ask of each concrete value: does this describe *this*
  setup?** The guards — pre-commit, pre-push, `run-tests.ps1` and CI — refuse
  private addresses, device ids, SSH keys and tailnet names. They cannot
  recognise a region or a room, so that part is judgement, and it is the part
  that failed.

---

## Before anything is called done

Pushing is not finishing. CI has been red on `main` more than once while the
work was described as finished, and the red job was the one guarding what must
never leave this repository.

```powershell
.\tools\run-tests.ps1        # suite, and the private-data scan first
git push origin main
.\tools\verify-ci.ps1        # blocks until CI completes; non-zero if red
```

`verify-ci.ps1` names every job and exits non-zero when any failed, so it can be
trusted rather than read hopefully. **Do not report work as done before it has
run and passed.**

Two git hooks refuse a private address, a device id, an SSH key or a tailnet
name — `pre-commit` on staged content, `pre-push` on every outgoing commit. They live in `tools/githooks` and
are wired up with:

```powershell
git config core.hooksPath tools/githooks
```

The pre-push hook exists because pre-commit only guards what is written today: a
commit made before the hook, an amend, or a rebase that resurrects an old line
all reach the remote otherwise. Everything is recoverable except a push to a
public repository.

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
| 1 · Sources & catalog | ✅ done | Local connector **and Drive** (service account, no OAuth), scanner, folder tree, search, duplicate joining |
| 2 · Playback | ✅ done | Signed URLs, range streaming, thumbnails, audio with folder queue, video direct-play **and live transcode**, photo and document viewer, slideshows |
| 3 · Control tower & zones | ✅ done | Denon verified against the real receiver; rooms, sessions, transport, seek, volume, occupancy, the tower UI |
| 4 · TV apps | ✅ done | Android TV app in daily use, **with its own video player** for what a WebView cannot decode. No Gradle: aapt2/javac/d8/apksigner, 33 KB, built by CI every commit. A phone launcher app too (`docs/PHONE_APP.md`). webOS dropped; Tizen only if a screen turns out to have no box |
| 5 · Playlists & music intelligence | 🔨 | Winamp import done — 41 lists, 98.6% matched, sharing and reordering work. AcoustID tag repair not started |
| 6 · AI | ⬜ | See **AI design decisions** below — agreed with Shahaf, not yet built. Backups landed first, deliberately |
| 7 · Photo availability | ⬜ | RAID→Drive sync, Wake-on-LAN, Takeout gap-fill |
| 8 · Optional transcode | ❌ overtaken | It was never optional for this library — see §3.2.1 of ARCHITECTURE for what actually happened |
| 9 · Public release | 🔨 | Public since August. Docs current as of 14 September 2026; screenshots outstanding |

**Tests: 683 passing. Migrations: 026. Lint: clean. CI green.**

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

**Backups, because the AI can change the database.** Taken hourly (for the
standby), kept daily for a week and weekly for five weeks -- which is what
guarantees the points at two weeks and a month (aiming at those marks directly never kept anything: the
dailies near them had already been pruned). Restore from within the app,
administrators only.

**Spending cannot be allowed to run away.** Raised by Shahaf and load-bearing:
software that calls a paid API on its own, on a schedule, is software that can
bill on its own when it has a bug. The rule is that the *platform* refuses the
spend rather than the code promising not to — an Oracle account that is never
upgraded cannot be charged at all, because there is no payment relationship to
charge against, and paid resources simply cannot be created. Where a paid
provider is genuinely wanted, it needs a per-account cap that the code enforces
before the call, not a budget alert that arrives after it. Alerts report; they
do not stop.

### Outstanding tasks

- [ ] Gapless audio playback and ReplayGain (the player is functional, not yet gapless)
- [ ] AcoustID tag repair (phase 5's second half)
- [ ] Automatic rescan on file change — sweeps are scheduled, but a change is noticed
      at the next sweep rather than when it happens
- [ ] Go agent + WireGuard (Mode B split; only needed when the core moves off the PC)
- [ ] **The standby on Oracle** (phase 0.5, as redesigned) — `docs/STANDBY.md`.
      Built: hourly backups, the standby's safe refresh, changes on the standby
      recorded and replayed on the PC, refusals and a banner. Waiting on the
      Oracle machine, the Tailscale rule and the join key (owner's part), then
      deployment, thumbnail sync, and Homesh Connect's third address
- [x] ~~Off-site backups~~ — Oracle Object Storage, S3-compatible, verified end to
      end on the real bucket. Drive could never have been the destination: a service
      account has no storage quota, so it cannot create a file even in a folder shared
      as Editor. `docs/OFFSITE_BACKUPS.md` is the setup
- [x] ~~Metadata extraction — duration, artist, album~~ — tags at scan time, durations
      98% for video and 94% for audio. A remote video is timed from both ends of the
      file, never from a prefix (`server/app/metadata.py` says why)
- [x] ~~Google Drive connector~~ — service account, five folders, no OAuth and no
      weekly expiry
- [x] ~~Install the TV app on the real boxes~~ — in daily use. A box installed from a
      different machine must be uninstalled first, because the signing key is
      per-machine and never committed
- [x] Database backups and in-app restore — daily, a week of them plus a
      fortnight and a month back, restorable from Settings by an administrator.
      Data only, in Postgres's own COPY format, with the schema coming from the
      migrations — see the note in `server/app/backups.py` on why not pg_dump
- [ ] AI activity history, readable in the app

### Waiting on Shahaf

1. **Share the Drive folders as Editor**, not Viewer — a service account owns no
   storage, so a viewer cannot grant access it does not itself have. "Create a
   Drive link" fails with exactly that message until this changes, and so would
   pushing a backup to Drive. Leave "Editors can change permissions and share"
   enabled.
2. ~~Google Cloud OAuth client~~ — **not needed.** Solved with a service account
   and ordinary Drive sharing; see §1.2 of ARCHITECTURE.
3. ~~A real media folder~~ — done. Two local grants (`E:/music`, `E:/Photos`) and
   five Drive folders, about 140,000 files.
4. ~~Repo visibility~~ — public.

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
- **Oracle cut its free tier** twice and silently — 2 OCPU / 12 GB in June 2026, and
  1 OCPU / 6 GB in some regions' consoles by September. Take the eligible size. Portability
  is therefore an architectural requirement, not a preference.

---

## Layout

```
server/app/       config, db, main, auth, security, people, access, prefs, library,
                  scanner, metadata, dedup, signing, stream, transcode, documents,
                  thumbs, sharing, playlists, zones, denon, renderers, discovery,
                  occupancy, lanaddr, upkeep, backups, crypt, offsite, standby,
                  thumbsync, signin_code, throttle, audiocache, ai, ai_api,
                  sources/{base,local,gdrive}
server/migrations 023 of them, plain SQL, tracked in `schema_migrations` and applied at
                  startup. `ls server/migrations` is the list; the recent ones are
                  021 (video lengths), 022 (shuffle history), 023 (replica fingerprints)
server/tests/     one module per surface, 477 tests
web/src/          App, Browser, Viewer, Player, Slideshow, Zones, Playlists, People,
                  Sources, Settings, PdfView, RawView, FileActions, PlayTo, Audience,
                  LinkDevice, + api/auth/library/prefs/zones/playlists/backups helpers
                  and styles.css. `web/src/tv/` is the television client, a separate
                  entry point (`tv.html`) sharing the same build
android/          TV shell — Manifest, java/com/homesh/tv/{MainActivity,SetupActivity,
                  Prefs,ServerAddress}, res/, test/ServerAddressTest.java
tools/            probe-denon.ps1, configure-network.ps1, run-tests.ps1,
                  build-tv-apk.sh, grant-folder.ps1, start-homesh.ps1,
                  homesh-common.ps1, verify-ci.ps1, scan-apk.py, githooks/
(repo root)       "Start Homesh.cmd", "Add a folder to Homesh.cmd", "Sign in to the
                  standby.cmd" — the jobs done by double-click rather than
                  through a terminal
docs/             ARCHITECTURE.md, USER_GUIDE.md, TV_APP.md, PHONE_APP.md,
                  OFFSITE_BACKUPS.md, STANDBY.md, AI.md, TLS.md
```

---

## Running and testing

```powershell
.\tools\start-homesh.ps1          # the whole sequence; "Start Homesh.cmd" double-clicks it
.\tools\start-homesh.ps1 -Status  # what is running, and the addresses to open
.\tools\start-homesh.ps1 -Rebuild # after a code change
.\tools\configure-network.ps1     # derive DENON_HOST + LAN_BASE_URL into .env
.\tools\run-tests.ps1             # suite against the homesh_test database
docker compose logs api           # first-run bootstrap code lives here
```

`start-homesh.ps1` exists because `docker compose up -d` is not the whole job,
and the missing steps are the ones that waste an evening. It starts Docker
Desktop when the engine is down, waits on the container's own healthcheck rather
than a guessed sleep, and — the part that is easy to forget — **checks whether
each granted folder actually has anything in it, and re-attaches the drive if
not.**

That last step is needed because Docker's virtual machine is rebuilt on every
restart and WSL2 attaches fixed disks only. A folder on an external drive comes
back as an empty directory rather than as an error, which looks exactly like an
empty folder. Attaching the drive *before* Docker starts avoids it; attaching it
afterwards does not, and nothing says so.

Shared helpers live in `tools/homesh-common.ps1`, dot-sourced by both this and
`grant-folder.ps1` — a second copy of the drvfs mount is the sort of thing that
drifts and is then debugged twice.

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
- **Pure ASCII in anything Windows opens as a file**: `.ps1`, `.vbs`, `.cmd` and
  `.env.example`. PowerShell 5.1 reads a `.ps1` without a byte-order mark as ANSI,
  so a UTF-8 dash becomes three characters and one of them ends a string early.
  `.env` has a milder version of the same problem: an editor that saves it back in
  the local code page turns a line of box-drawing dashes into several hundred
  characters of nonsense. Nothing breaks, and the file looks corrupt, which is
  nearly as bad in something somebody has to edit by hand.

### Design rules the code must not violate

- Filename and path are first-class, always displayed, never replaced by metadata
- `item_metadata.origin` distinguishes file / musicbrainz / ai / user
- Vanished files are marked unavailable, never deleted
- No media URL is guessable or long-lived
- No inbound ports at home; agents dial out
- Path confinement checked *after* symlink resolution
- **The server reaches only the folders it has been granted.** Each is a
  read-only mount under `/library`, made on the PC. No endpoint lists the host
  and none takes a path, so no request can widen what the server reaches. What
  was not granted is unreachable, not merely unlisted.

  **The button that does it is in the app**, and lives at `homesh://add-folder`
  — a protocol handler registered in HKCU by `tools/grant-folder.ps1`, the same
  mechanism as a Zoom or Spotify link. A web page cannot open a folder dialog on
  the machine, but it can ask Windows to open something that can. The page never
  learns the path and does not need to.

  Four attempts to get here, and the shape of the mistake was the same each
  time: reading a complaint about *ergonomics* as a request to widen *scope*.
  Shahaf objected to the app listing his folders unbidden; I banned host
  listing and shipped a terminal chore. He asked for a browser like every other
  media server; I mounted whole drives — and he refused that too, because
  adding a folder is a **one-time act at the machine**, exactly like sharing a
  folder in Drive, so convenience from a phone was never worth permanent read
  access to the disk. Then a double-click, which was still not a button.
  **The grant is the feature; the picker is only how you name it. Make the
  naming pleasant. Never widen the grant to make it convenient**
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
