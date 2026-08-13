# Media Server — Feature Set & Architecture

> Name: **Homesh** — a welding of *home* and *mesh*. Chosen 10 August 2026.
> Status: **Phases 0-2 complete** — auth, catalog, search, thumbnails and playback all
> work. Phase 3 (control tower) in progress: receiver control and zone sessions are
> done and verified against real hardware; the phone UI is next. Roadmap in §10.

---

## 1. Two constraints that shape everything

Before the design, two externally-imposed facts that were verified against current Google
documentation. Both change what we can promise.

### 1.1 Google Photos can no longer be browsed by third-party apps

As of **31 March 2025**, Google removed the `photoslibrary.readonly`, `photoslibrary.sharing`
and `photoslibrary` scopes. The Library API now only returns media **that our own app
uploaded**. Any call that relies on the old read scopes returns `403 PERMISSION_DENIED`.

This is permanent and applies to every third-party app, not just ours. There is no API,
key, or verification tier that restores "list my whole Google Photos library."

The three remaining options:

| Option | What it gives | Cost |
|---|---|---|
| **A. Photos Picker API** | User picks items in Google's own UI; we get temporary access to just those | Useless for a library browser — no bulk, no sync, session-scoped |
| **B. Google Takeout ingest** | Full library, with metadata + album structure | Manual export, periodic, large downloads, we re-host thumbnails |
| **C. Treat Drive as the photo store** | Full API access, live sync, no restrictions | Requires photos to live in Drive |

**Decided: the RAID copy is the source of truth.** You already hold most photos on the RAID, so
that becomes the primary photo source and the Google Photos API problem stops being on the
critical path. Takeout stays available later to fill gaps for anything that exists only in
Google Photos.

This raises the obvious objection — the storage is not meant to run all the time. The
architecture already answers this:

- **Catalog, EXIF, thumbnails and AI search vectors live on the core node.** So browsing,
  timeline, map view, face groups and natural-language search over your entire photo library
  work perfectly with the RAID powered off. Only *full-resolution originals* need it.
- **You have paid Google storage.** So the strongest option is a **RAID → Drive sync job** that
  runs whenever the RAID is up, putting originals (or high-quality copies) in Drive. That gives
  genuinely always-on access to originals without the RAID spinning. Sizing depends on your
  library versus your plan — I'll measure before recommending originals vs. compressed.
- **Wake-on-LAN** as the fallback for the rare original that isn't synced: the app offers to wake
  the PC/RAID on demand rather than keeping it up. This suits the loud-fan problem well — the
  RAID is off until the moment you actually need a full-size file.

Whether the storage is additionally protected against power loss is a deployment choice,
independent of anything built here.

### 1.2 Google OAuth tokens die every 7 days unless the app is published

An OAuth app in **Testing** status with **External** user type has its refresh tokens revoked
after 7 days. You would have to re-authorize weekly — unacceptable.

`drive.readonly` is a **restricted** scope, so moving to Production normally triggers Google's
full verification including a third-party CASA security audit (expensive, slow, aimed at
commercial apps).

The clean way out: request **`drive.file`** instead of `drive.readonly` where possible, and for
full-library access use a **Google Cloud service account with domain-wide delegation** if you
have Workspace, or accept verification. There is a fourth path that works well for a personal
deployment: keep the app in Testing but have the agent **re-mint tokens automatically** — this
does not work, tokens are hard-revoked.

**Recommendation:** this needs a decision from you (see §11). It does not block any other work,
so I will build the Drive connector against a token abstraction and we can swap the acquisition
strategy later.

---

## 2. Design principles

These are direct answers to the eight problems you listed with Plex. Every one is a hard rule
the implementation must not violate.

1. **The filename is data, not a fallback.** Path and filename are first-class indexed,
   displayed, and searchable fields. Metadata is *additive* — it never replaces or hides the
   filename. Every item view shows both. (Fixes #4.)
2. **The folder tree is a first-class view.** Not a degraded mode, not "other". You can browse
   the real directory structure of every source, with the same fluency as a file manager.
   (Fixes #5.)
3. **One app, all media.** Video, audio, photos and documents share one catalog, one search,
   one player shell, one permission model. (Fixes #2, #3.)
4. **The catalog is always up; the bytes may not be.** The index, metadata, thumbnails and
   search vectors live on an always-on node. You can browse and search *everything* — including
   RAID content — while the PC is off. Only playback of local-only files is gated. (Fixes #7.)
5. **No feature gates.** MIT/AGPL, no license tiers, nothing withheld. (Fixes #1.)
6. **Your home network is never exposed.** All home→cloud connections are outbound-only. Zero
   inbound ports, zero port forwarding, zero UPnP on the router.
7. **Runs anywhere.** Windows PC, Raspberry Pi, or a free cloud instance, from the same build.
   Never architecturally wedded to one host or one provider — if a free tier degrades, we move
   in an evening. (See §3.4.)
8. **Control plane and playback plane are separate.** Your phone is a remote; the audio and
   video come out of whatever device you point it at. (Fixes #7.)

---

## 3. Architecture

Three planes, deliberately decoupled so that any one being down degrades rather than breaks.

```
┌─────────────────────────────────────────────────────────────────────┐
│  CONTROL PLANE — always on  (free cloud / Pi / PC — see §3.4)        │
│                                                                     │
│   ┌───────────┐  ┌──────────────┐  ┌─────────────┐  ┌────────────┐  │
│   │ API       │  │ Catalog      │  │ Auth        │  │ Job        │  │
│   │ (FastAPI) │  │ Postgres +   │  │ (passkeys)  │  │ workers    │  │
│   │           │  │ pgvector     │  │             │  │            │  │
│   └───────────┘  └──────────────┘  └─────────────┘  └────────────┘  │
│   ┌──────────────────────────────────────────────────────────────┐  │
│   │ Thumbnail + preview cache   │  Signed-URL minter  │  WG hub   │  │
│   └──────────────────────────────────────────────────────────────┘  │
└──────────┬───────────────────────────────┬──────────────────────────┘
           │ WireGuard (agent dials out)   │ HTTPS
           │                               │
┌──────────┴───────────────┐   ┌───────────┴─────────────────────────┐
│  DATA PLANE — intermittent│   │  CLIENT PLANE                       │
│                          │   │                                     │
│  Home PC + RAID          │   │  Phone (PWA)  ── remote control      │
│   • Homesh Agent (Go)    │   │  Browser / TV browser                │
│   • serves file ranges   │   │                                     │
│   • ffmpeg transcode     │   │  RENDER TARGETS:                     │
│   • thumbnail/embed gen  │   │   • Chromecast / Google TV           │
│                          │   │   • AirPlay 2 → Apple TV             │
│  Google Drive (always up)│   │   • Denon AVR via UPnP/AirPlay       │
│  Takeout archive         │   │   • Snapcast endpoints (multi-room)  │
└──────────────────────────┘   └─────────────────────────────────────┘
```

### 3.1 Why an always-on node is non-negotiable

Your requirement "available when my PC or RAID is off" plus "control from my phone, play on my
TV" cannot be satisfied by software on the home PC — if the PC is the server, the PC being off
is the end of the story. Something must be up 24/7.

**It does not have to cost money, and it must not be tied to one machine.** See §3.4 — the
always-on node is a *role*, and portability across Windows / Pi / free cloud is a hard
architectural requirement, not a nice-to-have.

### 3.4 Deployment topologies & portability

The same build artifacts must run on a Windows PC, a Raspberry Pi, and a free cloud instance.
That means **multi-arch images (`linux/amd64` + `linux/arm64`) from day one**, all configuration
through environment variables, no hard-coded hostnames, and storage paths behind an abstraction.
Topology is configuration, not code.

**Mode A — all-in-one.** Core and agent on the same machine. This is the development mode and
the "just get it running on the PC" mode. Not always-on, but everything works.

**Mode B — split.** Core on an always-on node, agent on the PC over WireGuard. This is the
target. The *only* difference from Mode A is a compose file and a peer config.

Moving A→B is a `pg_dump`, a restore, and re-pointing the agent. I will keep that path exercised
so it never rots.

#### Where the always-on node runs

| Target | Arch | Cost | Notes |
|---|---|---|---|
| **Oracle Cloud Always Free** | arm64 | **Free, permanent** | 2 OCPU / 12 GB / 200 GB block storage. Recommended starting point |
| **Raspberry Pi 5 at home** | arm64 | ~$80 once | Same image as Oracle — literally a drop-in swap. Data stays in your house |
| Windows PC | amd64 | Free | Mode A only; not always-on |
| Any paid VPS | amd64 | ~€4/mo | Escape hatch if the free options degrade |

**Recommended arc:** start on Oracle Always Free now (always-on, zero cost, zero hardware), then
move to the Pi when you buy it for the smart-home work. Both are arm64, so it is the same image
and a database restore — an evening's work, not a migration project.

**Honest risks with Oracle's free tier**, which you should know before we depend on it:
- On **15 June 2026 Oracle silently halved** the Always Free Ampere allowance from 4 OCPU/24 GB
  to 2 OCPU/12 GB, with no announcement or customer notice. They can do it again.
- Ampere A1 capacity is region-dependent; `Out of Capacity` errors on instance creation are
  common in popular regions and may take retries.
- **Idle reclaim:** instances under 10% CPU *and* 10% network over 7 days may be stopped. A
  personal media server is idle a lot. The standard mitigation is upgrading the account to
  Pay-As-You-Go — Always Free resources stay free, but reclaim no longer applies. I will confirm
  this at provisioning time and set up a health heartbeat regardless.

This is precisely why portability is an architectural requirement rather than a preference. If
Oracle degrades the tier again, we move to the Pi in an evening and lose nothing.

#### Getting traffic in

Two options, both free, both requiring **zero inbound ports** at home:

- **Cloudflare Tunnel** — free, includes TLS and a hostname, no port forwarding. Its old ToS
  §2.8 prohibited serving video and other large non-HTML files; **Cloudflare removed §2.8 in
  May 2023**, so media streaming through a free tunnel is no longer a terms violation. This is
  the right choice for a home-hosted core (Pi or PC).
- **WireGuard** — when the core is on a cloud instance with its own public IP, the agent dials
  out to it and no tunnel provider is involved at all.

We implement both; which is active is configuration.

### 3.2 Direct play is the default. Transcoding is a rare edge case.

**The server does not need a video encoder for the normal path.** The endpoint has a *decoder*;
the server would only need an *encoder* if it had to re-encode. In the overwhelming majority of
cases it does not — it just streams the original bytes and the TV decodes them in hardware. This
is *direct play*, and for the device fleet in §5.6 it covers nearly everything.

Three distinct operations, often conflated:

| Operation | What it does | Cost |
|---|---|---|
| **Direct play** | Stream the original file, byte for byte | ~zero — it's a file server |
| **Remux** | Change container only (MKV → fMP4 for HLS), stream copy, no re-encode | ~zero — trivial even on a Pi |
| **Transcode** | Actually re-encode the video | Expensive |

Most "it won't play in the browser / on Cast" problems are solved by **remux**, not transcode.
That distinction is what makes a free-tier ARM instance a perfectly adequate host.

Genuine transcode is needed only when:
1. the endpoint cannot decode the codec (old Xvid/VC-1, or 10-bit HEVC on a weak client),
2. bandwidth is the constraint — a 40 Mbps remux over your home uplink when you're out of the
   house,
3. image-based subtitles (PGS/VOBSUB) must be burned in for a client that can't render them.

When it *is* needed, it is **delegated to the home PC agent**, never to the core node. If the PC
is down, only cloud content is playable anyway, and that content is overwhelmingly already in a
directly-playable codec.

⚠️ **Hardware reality check.** An earlier draft assumed a desktop-class machine with a discrete
GPU. The target profile is a low-power mini PC — four Alder Lake-N efficiency cores, integrated
graphics, no discrete GPU — which also hosts the storage. Software transcoding on it would be
slow.

Alder Lake generally carries a Quick Sync media engine capable of H.264/HEVC hardware encode, and
Alder Lake-N is *expected* to retain it, but Intel's published material doesn't confirm the
N-series explicitly. **Not asserting it — we'll probe with `ffmpeg -hwaccels` and a real encode
once the agent is on that machine, and record the measured answer here.**

This does not endanger anything, because transcoding is phase 8 and optional (see above). Direct
play and remux are both nearly free and are what the fleet actually needs.

**Consequence for the roadmap:** transcoding drops out of the critical path entirely. It moves
from phase 2 to phase 6 as an optional capability. Phase 2 ships direct play + remux, which is
all your hardware actually needs.

### 3.3 Availability matrix

| | PC + RAID up | PC down |
|---|---|---|
| Browse / search everything | ✅ | ✅ (catalog + thumbs live on the core node) |
| Play Drive content | ✅ | ✅ |
| Play RAID content | ✅ | ❌ — shown greyed with "PC offline" |
| Photo thumbnails, all sources | ✅ | ✅ (cached) |
| Doc full-text search | ✅ | ✅ (text extracted at index time) |
| Transcode | full power | degraded fallback |

The important consequence: **searching never breaks.** You find the file, you see it exists, you
see its thumbnail, and the app tells you to wake the PC. Wake-on-LAN is a phase-4 nicety.

---

## 4. Sources & the unified namespace

Every source mounts into one virtual tree:

```
/local/raid/Music/...          ← Home agent, RAID
/local/pc/Videos/...           ← Home agent, PC internal
/drive/My Drive/...            ← Google Drive, live
/drive/Shared with me/...
/photos/2019/Greece/...        ← Takeout ingest, album+date structure preserved
```

Source connectors implement one interface: `list()`, `stat()`, `open_range()`, `watch()`.
Adding Dropbox/S3/SMB later is a new connector, nothing else changes.

**Deduplication:** content-hash (BLAKE3) on ingest. The same song on the RAID and in Drive
collapses to one catalog entry with two *replicas*. The player picks the available replica —
which is exactly how Drive content keeps playing when the RAID is down, transparently.

---

## 5. Feature set

### 5.1 Browse & organize
- Real folder-tree navigation across all sources, with breadcrumb + keyboard nav
- Library views (Albums/Artists/Movies/Shows/Albums-of-photos) layered *on top of*, never
  replacing, the tree
- Filename always visible; toggle "show raw filename as primary label" globally
- Sort by name, date, size, duration, rating — including natural sort (`track2` before `track10`)
- Saved views and pinned folders

### 5.2 Audio
- Gapless playback, ReplayGain, crossfade
- **Winamp playlist import** — `.m3u`, `.m3u8`, `.pls`, `.asx`, with fuzzy path re-resolution
  (your old playlists reference paths that have since moved; we match on filename + duration +
  hash and repair the links, reporting anything unresolvable)
- Smart playlists (rule-based, live-updating)
- **Metadata repair via acoustic fingerprinting** — Chromaprint → AcoustID → MusicBrainz. This
  directly fixes your corrupted-tag problem: we identify the actual song from the audio itself
  and *propose* correct tags. Proposals are reviewable and never silently overwrite; the
  filename is never touched.
- Queue management, history, play counts

### 5.3 Video
- Direct play + adaptive HLS transcode
- Subtitle support (embedded, external `.srt`/`.ass`, and auto-fetch)
- Resume points synced across devices
- Chapter/scene thumbnails on the scrub bar

### 5.4 Photos
- Timeline and album views, EXIF-driven
- Map view from geotags
- Face clustering (local, opt-in, never leaves your hardware)
- RAW/HEIC handling
- Natural-language visual search (see §7)

### 5.5 Documents
- In-browser preview: PDF, EPUB, DOCX/XLSX/PPTX, Markdown, plaintext, code
- Full-text search across document bodies
- AI summarization and Q&A over a document or a folder

### 5.6 Playback targeting — the remote-control model

This is requirement #7 and deserves its own design. The phone is a **remote**; it is never
obliged to be the thing producing sound or picture.

#### The device profile

Nearly every screen is fed by an Android TV set-top box rather than relying on the TV's own
platform, and that collapses this section's complexity. A box is the easy target — Cast works
with no install at all, side-loading is routine, and nothing expires. Both TV platforms, by
contrast, expire their developer certificates and need periodic reinstalling.

**Where a screen has a box, target the box.** A TV's own platform is only worth building for
when a screen has none — and a cheap stick is usually a better answer than a second app to
maintain forever. In practice this reduces three planned TV apps to one.

| Device | Platform | Cast? | AirPlay 2? | Our app? |
|---|---|---|---|---|
| LG | webOS | ❌ | ✅ (2019+; 2018 via update) | ✅ webOS app (HTML5) |
| Samsung | Tizen | ❌ | ✅ (2018+) | ✅ Tizen app (HTML5) |
| Non-smart TVs | via an Android TV box | ✅ | ❌ | ✅ Android TV app |
| Denon AVR-X1600H | HEOS | ❌ **no Chromecast** | ✅ | ❌ — network protocols only |

There is no single casting standard that covers this fleet. Cast reaches only the Android box;
AirPlay 2 reaches the TVs and the Denon but is painful to implement as a *sender* from a server.

#### The unlock: our own renderer protocol

Rather than implementing three incompatible casting stacks, **any device running our app
registers itself with the core as a renderer over a WebSocket.** The phone sends commands to the
core; the core relays them to the renderer; the renderer pulls media from a signed URL.

All four client targets are web technology, so this is **one React codebase**, packaged four
ways: browser PWA, Tizen app, webOS app, Android TV app.

The payoff is large: we get our own UI on the TV — filenames, folder tree, AI search, the lot —
which Cast and AirPlay would never give us. Casting protocols become *fallbacks* for hardware we
cannot install software on.

#### The Denon, which we cannot install onto

Confirmed capabilities of the X1600H: **AirPlay 2 ✅, HEOS Built-in ✅, Bluetooth ✅,
Chromecast ❌**. Two documented control surfaces, and they are separate protocols:

- **HEOS CLI — telnet port 1255**, ASCII commands, JSON responses. Denon publishes the spec.
  `play_stream` accepts a URL, so we hand it one of our signed URLs directly. This is our audio
  delivery path.
- **Denon/Marantz AVR telnet — port 23.** A *different* protocol, not HEOS CLI, even on
  HEOS-capable models. Controls power, input select, volume, and ZONE2. This is our
  orchestration path.

Together these make "play this album on the balcony" a single button: power on the AVR, enable
ZONE2, select the network source, set volume, push the stream URL.

#### Zone routing — two independent audio paths

The wiring this assumes:

- **Zone 1 (living room)** — TV connected over HDMI. The TV is *not* wired to ZONE2.
- **Zone 2 (balcony)** — fed by streaming from a phone to the AVR over the network, then
  selecting the zone in the HEOS app.
- These run **simultaneously with different content** — TV audio in the main zone while a
  network stream feeds ZONE2. Verified in practice.

This matches the manual's restriction: *"It is not possible to play the digital audio signals
input from the HDMI, COAXIAL or OPTICAL connectors in ZONE2. Use analog connections for ZONE2
playback."* HDMI cannot reach ZONE2 — so ZONE2 audio must arrive over the network or analog,
and network is what's already in use.

**The design follows the hardware.** The AVR has two independent audio paths, so we drive one
from each side:

```
Zone 1 (living room)   TV app (webOS/Tizen)  ──HDMI──►  AVR main zone
Zone 2 (balcony)       core node  ──HEOS CLI play_stream (signed URL)──►  AVR ZONE2
```

Different content in both zones, orchestrated from one app — and it works **without requiring
the AVR to handle two simultaneous network streams.** It is the automation of what the owner
already does by hand.

#### Measured, not assumed

`tools/probe-denon.ps1` was run against the live receiver. Results:

**HEOS CLI (`heos://player/get_players`) returns exactly one player.** The AVR presents itself as
a single HEOS device — `Denon AVR-X1600H`, firmware 3.139.173, wired. There is **no separate
player for ZONE2**, and `group/get_groups` is empty.

So the AVR **cannot run two different network streams at once.** That settles the open question:
the bonus case (network audio in the living room while the balcony plays something else) is not
available, and the HDMI/network split above is not merely convenient — it is the *only* way to
get different content into the two zones.

**The AVR control protocol (port 23) does expose ZONE2 fully.** Live state read back:

```
PWON      power on              SITV      main zone source = TV
ZMON      main zone on          MV43      master volume 43 (max 94)
Z2OFF     ZONE2 currently off   MUOFF     not muted
Z2NET     ZONE2 source = NET    SLPOFF    sleep timer off
Z259      ZONE2 volume 59
```

Two useful facts fall out. ZONE2's input is **already set to `NET`**, so we never have to switch
it. And ZONE2 power and volume are independently controllable over port 23 — so even though we
can't give ZONE2 its *own* stream, we have full command of the zone itself.

#### The resulting zone matrix

| Scenario | Works? | How |
|---|---|---|
| TV audio in living room + different music on balcony | ✅ | HDMI → zone 1, HEOS → ZONE2. What you do today |
| Same music in both zones | ✅ | One HEOS stream, both zones enabled |
| Music in living room (TV off) + *different* music on balcony | ❌ | One network player. Workaround: run the TV app for zone 1 |
| Music on balcony only, TV off | ✅ | HEOS → ZONE2, `Z2ON` |

The UI must model this honestly: if you ask for different network audio in both zones, we either
route living-room audio through the TV app over HDMI, or say plainly that the receiver can't and
offer to play the same thing in both. No silent surprises.

⚠️ **One small setting to check:** the receiver was powered on during the probe, so port 23
responding doesn't prove it responds from standby. **Setup → Network → Network Control → "Always
On"** is what lets us wake the receiver remotely. Without it, the app can control the AVR only
once it's already on.

#### Zones

"Living Room", "Balcony", "Bedroom" are named zones, each bound to a renderer (an app instance,
a Cast device, or the Denon + a zone id). You pick a zone, then pick media. Your Denon's two
speaker sets become two zones.

**Snapcast** for synchronized whole-house audio later, if you add cheap endpoints (Pi Zero + DAC,
~$25/room) for rooms the Denon doesn't reach — which dovetails with the smart-home work.

### 5.7 Device identity and dynamic IPs

Nearly every device on this network gets its address from DHCP, so **no IP address is ever
treated as configuration.** Devices are keyed by stable identity; the IP is a cache entry that
rediscovery refreshes.

| Device class | Stable key | How the current address is found |
|---|---|---|
| Denon AVR | SSDP `USN` — a device UUID that survives reboots and DHCP changes | SSDP `M-SEARCH` for `urn:schemas-denon-com:device:ACT-Denon:1` |
| Smart TVs | app-instance ID issued at first registration | Not needed — the TV app dials *out* to the core |
| Home PC agent | agent ID + client certificate | Not needed — the agent dials *out* over WireGuard |

Two thirds of the problem disappears because our own software connects outbound rather than being
connected to. That was chosen for security (§6, no inbound ports) and it pays a second dividend
here: the core never needs to know where anything is.

Only LAN devices we can't install software on — the Denon — need discovery, and SSDP handles it.
Verified working from the host: `tools/probe-denon.ps1` found the receiver and read back a
stable `uuid:…::urn:schemas-denon-com:device:ACT-Denon:1`.

⚠️ **Discovery cannot run from inside a bridged container.** Docker's bridge network does not
forward multicast to the LAN, so the containerised core finds nothing — confirmed in practice,
while *direct* connections to the receiver on ports 1255 and 23 work fine from the same
container. Discovery therefore belongs to the home agent, which runs on the host with real LAN
access; until that exists, `DENON_HOST` seeds the address. The identity-keyed model is unchanged
— only the component performing discovery moves.

A DHCP reservation on the router is still worth doing for the AVR — it makes debugging less
confusing — but the design does not require one, and nothing breaks without it.

> Device serial numbers and UUIDs are deliberately **not** committed to this repository, since it
> will be published. They belong in local runtime config only.

### 5.8 The control tower

One screen on your phone that shows **everything playing everywhere**, and lets you change any of
it without first "connecting" to a device. Living room, balcony, bedroom TV, all at once.

#### The design decision that makes it work

**The core owns playback state. Renderers execute.** A *session* — queue, current item, position,
volume, play/pause — lives on the server and is bound to a zone, not to a device or to your
phone.

This inverts the usual casting model, where the phone holds the state and the TV is a puppet, and
it buys several things at once:

- Any controller sees every session. The control tower is just a subscription to all of them.
- **Your phone can die and the music keeps playing.** It was never in the path.
- A TV that reboots reconnects and resumes from server state, mid-track.
- Two people's phones both control the house without fighting over ownership.
- "Move this to the bedroom" is a session rebinding, not a re-cast.

```
   Phone (controller)                    Core                     Renderers
   ─────────────────                     ────                     ─────────
   subscribe: all sessions   ──────►  session store  ──────►  Living Room  (Tizen/webOS app)
   command: pause balcony    ──────►  command bus    ──────►  Balcony      (Denon ZONE2)
           ◄────── live state fan-out ─────────────────────   Bedroom      (Tizen app)
```

All of it over one authenticated WebSocket per client, with heartbeats. If a renderer loses the
core it keeps playing from its buffer and re-syncs on reconnect — a network blip doesn't stop
the music.

#### Renderers are not interchangeable, and the UI must know it

Each renderer registers a capability descriptor, and the control tower adapts rather than
offering things that will fail:

| Renderer | Video | Audio | Seek | Volume | Transport |
|---|---|---|---|---|---|
| TV app (webOS / Tizen / Android TV) | ✅ | ✅ | ✅ | ✅ | our WebSocket protocol |
| Denon ZONE2 (balcony) | ❌ | ✅ | limited | ✅ via port 23 | HEOS `play_stream` + AVR telnet |
| Cast device | ✅ | ✅ | ✅ | ✅ | Cast SDK |
| Browser / phone itself | ✅ | ✅ | ✅ | ✅ | local playback |

So the balcony never gets offered a film, and a zone without seek support shows no scrub bar.

#### Zones carry hardware orchestration, not just a stream

A zone is more than a destination — it owns the steps needed to make sound actually come out.
The balcony zone, concretely:

```
Balcony:
  pre-roll:   AVR power on  ──►  Z2ON  ──►  Z2NET  ──►  set Z2 volume     (port 23)
  transport:  heos://player/play_stream  with a signed URL                (port 1255)
  post-roll:  Z2OFF on idle timeout
```

Network Control is now set to "Always On", so the pre-roll can wake the receiver from standby —
"play this on the balcony" genuinely is one button from a cold start.

#### Availability is shown, never guessed

Every zone displays a live reachability state: **ready**, **asleep** (wakeable — AVR standby,
Wake-on-LAN capable TV), or **unavailable** (TV physically off, app not running). The tower
offers to wake what it can and says plainly what it can't, rather than failing silently after
you press play.

The hardware constraint from §5.6 surfaces here too: if the balcony is streaming and you ask for
*different* network audio in the living room, the tower routes the living room through the TV app
if the TV is on, and otherwise tells you the receiver can't do both and offers to play the same
thing in each.

#### Bedroom and beyond

The bedroom TV needs no new engineering — if it's one of the Samsungs, it runs the same Tizen app
and appears in the tower as another zone. Every additional room is either a screen running our app
or a cheap Snapcast endpoint, and neither requires protocol work.

---

## 6. Security design

Your data is going on the public internet, so this is treated as a primary feature, not a
checklist at the end.

**Network**
- The home agent makes an **outbound-only** WireGuard connection to the core. No inbound ports at
  home, no port forwarding, no UPnP, nothing for a scanner to find.
- The agent additionally presents a **client certificate (mTLS)** to the core API. Even with the
  tunnel, an unauthenticated peer gets nothing.
- Core node firewall: 443 and the WireGuard port only. SSH key-only, no password auth, fail2ban.
  When fronted by Cloudflare Tunnel there is no open inbound port at all.

**Identity**
- **Passkeys (WebAuthn) as the primary factor** — nothing phishable, nothing to leak. TOTP as
  recovery. Argon2id if a password is ever needed.
- No public registration. Invite-only, admin-issued.
- Sessions: short-lived access token + rotating refresh, bound to device, revocable per-device
  from a UI. Secure/HttpOnly/SameSite=Strict cookies.

**Media access**
- No media URL is ever guessable or permanent. Every stream is served from an **HMAC-signed URL**
  scoped to `(item_id, user_id, expiry, client_class)`, typically 5-minute TTL, with a
  renewal handshake for long videos.
- Cast/DLNA receivers get their own signed URL scoped to the LAN, separately revocable.

**Secrets & data at rest**
- Google OAuth tokens are **envelope-encrypted** — per-record data key wrapped by a master key
  held in the environment, never in the database, never in the repo. A database dump alone
  grants an attacker nothing.
- Encryption at rest on the core node's volume (LUKS on Pi/VPS; Oracle block volumes are
  encrypted by default).
- Thumbnail cache is access-controlled, not a public static directory (a very common leak in
  self-hosted media servers).

**Application**
- Strict CSP, no inline script, Trusted Types
- Rate limiting on auth and search endpoints
- All input validated at the schema boundary (Pydantic)
- Path-traversal defenses on every connector — the agent refuses any path outside its
  configured roots, checked after symlink resolution
- Structured audit log: every auth event, permission change, and share link
- Automated dependency scanning + CodeQL in CI, since this will be public

**Privacy of AI features** — see §7; the design keeps your actual media on your hardware.

---

## 7. AI design

Two tiers, split on a privacy line.

**Tier 1 — local, bulk, private.** Runs on the home PC agent, for everything that touches actual
media content. **There is no GPU** — the agent host is a 4-core low-power CPU — so every model here is
chosen to be CPU-viable: quantised ONNX Runtime rather than PyTorch, small model variants, batch
throughput over latency.

| Job | Model | On that CPU | Verdict |
|---|---|---|---|
| Photo / keyframe embeddings | CLIP or SigLIP ViT-B/32, int8 ONNX | hours for a large library, once | ✅ overnight batch |
| Document text embeddings | small sentence encoder, int8 | fast; documents are few and small | ✅ fine |
| Face clustering | InsightFace, int8 | slow but bounded | ⚠️ opt-in, low priority |
| Speech transcription | whisper.cpp `base`, quantised | far too slow to run across a whole video library | ❌ not bulk — see below |

The first two are the ones that earn their keep, and both are **one-time batch jobs** whose cost
is paid once at import and then incrementally for new files. Slow is acceptable for work that
happens once, at night, on a machine that is idle anyway.

**Whisper is demoted from bulk to on-demand.** Transcribing everything is not viable on this
hardware. Instead: transcribe a single item when you ask for it, and let the results accumulate.
If whole-library transcription ever becomes worth it, it's a cloud batch job you opt into, not a
default.

Vectors are computed at home and **shipped to the core node's pgvector index**. The key trick
survives the hardware downgrade intact: the private computation happens on your hardware, but the
*result* lives on the always-on node — so semantic search over your photos keeps working when the
mini PC is off.

Two consequences worth stating plainly. Indexing a large library will take **a night or several**,
not minutes. And the design's data-locality argument still holds — embedding locally is right not
because the hardware is fast, but because shipping a hundred thousand photos to a cloud service
is worse on both privacy and bandwidth.

**Tier 2 — cloud, tiny payloads.** The Claude API handles only things where the payload is a few
hundred tokens of *your text, not your media*:
- Natural-language query → structured filter translation ("videos from the Greece trip longer
  than ten minutes" → a real query plan)
- Auto-categorization and tag suggestion from metadata
- Document summarization and Q&A (this one does send document text — it is per-request and
  opt-in, with a clear indicator)

Everything in Tier 2 is individually switchable off. With it off, the app degrades to local
embedding search and still works.

---

## 8. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Core API | **Python 3.12 + FastAPI** | The AI/media ecosystem (CLIP, Whisper, mutagen, pillow-heif) is Python-native, and this is the decisive factor |
| Database | **Postgres 16 + pgvector** | One engine for relational, full-text, and vector. No second datastore to operate |
| Jobs | **Postgres-backed queue** (arq-style) | Avoids a Redis dependency for a personal deployment; revisit if throughput demands it |
| Home agent | **Go** | Single ~10 MB static `.exe`, runs as a Windows service, no runtime for you to install or keep updated on your PC |
| Web client | **React + TypeScript, PWA** | One codebase packaged four ways: browser PWA, Tizen app, webOS app, Android TV app. See §5.6 |
| Media | **ffmpeg** | Remux + thumbnails on the critical path; transcode only as an optional late feature |
| Transport | **WireGuard** | Outbound-only, no third party, no ToS limits on streaming volume |
| Device control | **HEOS CLI (:1255)** + **Denon telnet (:23)** | Documented protocols; drive the AVR-X1600H's playback and ZONE2 orchestration |
| Deploy | **Docker Compose, multi-arch** | One `docker compose up` on Windows, Pi or cloud; `linux/amd64` + `linux/arm64` images built in CI. The agent ships as a separate native installer |
| Ingress | **Cloudflare Tunnel** or **WireGuard** | Both free, both zero-inbound-port; selected by config |

The three-language split (Python core / Go agent / TS client) is deliberate. The agent must be a
zero-dependency binary that a non-developer can install on Windows, and that rules out shipping a
Python runtime.

---

## 9. Data model sketch

```
sources        (id, kind[local|gdrive|takeout], config_encrypted, agent_id, last_seen)
items          (id, content_hash, kind[audio|video|photo|doc], duration, size,
                created_at, indexed_at)
replicas       (id, item_id, source_id, path, filename, mtime, available)   ← path+filename
                                                                              always preserved
metadata       (item_id, key, value, confidence, origin[file|musicbrainz|ai|user])
                                                    ↑ origin tracked so AI/remote guesses can
                                                      never masquerade as ground truth
embeddings     (item_id, chunk_idx, model, vector)  ← pgvector
playlists      (id, name, source_format, items[])

zones          (id, name, renderer_id, preroll[], postroll[], idle_timeout)
                                        ↑ ordered hardware commands (AVR power, Z2ON, volume)
renderers      (id, kind[tvapp|heos|cast|browser], device_key, capabilities_json,
                last_seen, state[ready|asleep|unavailable])
                     ↑ device_key is the SSDP USN / app instance id — never an IP (5.7)
play_sessions  (id, zone_id, queue[], cursor, position_ms, volume, state, updated_at)
                     ↑ server-owned playback state; survives phone and renderer restarts (5.8)

users, auth_sessions, credentials(webauthn), audit_log
```

The `origin` column on metadata is what makes principle #1 enforceable: the UI can always show
you what came from the file itself versus what a fingerprinting service or a model guessed.

---

## 10. Roadmap

Each phase ends in something you can actually use.

| Phase | Deliverable | Your involvement |
|---|---|---|
| **0. Foundation** | Repo, CI (multi-arch builds), Compose stack, Postgres schema, passkey auth, web shell running in Mode A on your PC | Nothing yet |
| **0.5. Go always-on** | Same stack deployed to Oracle Always Free, ingress + TLS, agent split out to Mode B | Create Oracle account; register your passkey |
| **1. Sources & catalog** | Drive connector, Go agent + WireGuard, unified tree, folder browser, filename-first UI, search | Google Cloud project + OAuth consent; install agent on PC |
| **2. Playback** | Audio player w/ gapless, video **direct play + remux** (no transcode), photo viewer, doc preview | QA on real content |
| **3. Control tower, renderers & zones** | Server-owned sessions, WebSocket renderer protocol, multi-zone control tower UI, zone orchestration, Denon via HEOS CLI + telnet | ~~Probe~~ done; ~~Network Control~~ done; physical testing |
| **4. TV apps** | **Android TV** — likely the only one needed, since nearly every screen has an Android box in front of it. webOS and Tizen only if a screen turns out to have none | Confirm which screens have a box |
| **5. Playlists & music intelligence** | Winamp import w/ path repair, smart playlists, AcoustID tag repair | Point me at your `.m3u` files |
| **6. AI** | Local CLIP/Whisper embedding pipeline, NL search, auto-tagging, doc Q&A | Anthropic API key |
| **7. Photo availability** | RAID → Drive sync, Wake-on-LAN, optional Takeout gap-fill | Decide originals vs. compressed after I measure |
| **8. Optional transcode** | Agent-side transcode for the edge cases in §3.2 | Only if we hit a file that needs it |
| **9. Public release** | Docs, AGPL-3.0, security policy, install guide, screenshots | Pick a name; approve going public |

Phases 0–2 give you a working replacement for Plex's core. Phases 3–4 are what make it better
than Plex for your living room. Note that transcoding — which I originally had in phase 2 — has
moved to phase 8 and may never be needed at all (§3.2).

---

## 11. Decisions

All resolved. Nothing is blocking a start.

1. ~~Always-on node~~ — **free cloud now** (Oracle Always Free), Pi later when bought for the
   smart-home work. Portable across Windows / Pi / cloud from the same artifacts (§3.4).
2. ~~Google Photos strategy~~ — **RAID copy is the source of truth.** Catalog and thumbnails on
   the core node keep browsing and search always-on; RAID→Drive sync and Wake-on-LAN handle
   originals. Takeout deferred to gap-fill (§1.1).
3. ~~Google account~~ — **personal `@gmail.com` with paid storage.** No Workspace, so the 7-day
   refresh-token expiry applies; mitigation in §11.1 below. Paid storage is what makes the
   RAID→Drive photo sync viable.
4. ~~Devices~~ — webOS, Tizen and Android TV displays, plus a **Denon AVR-X1600H**. Full matrix
   and protocol plan in §5.6.
5. ~~License~~ — **AGPL-3.0.** Requirement was "derivatives must also be open source." For
   software reached over a network, plain GPL leaves a loophole: someone can modify it, run it as
   a hosted service, and never distribute the source. AGPL closes exactly that. It's also what
   Jellyfin, Immich and Nextcloud use.

### 11.1 The remaining open technical problem

Personal Gmail + the restricted `drive.readonly` scope means an app in Testing status has its
refresh token **revoked every 7 days**. Three ways out, in preference order:

1. **Use `drive.file` instead of `drive.readonly`.** Non-restricted, so no verification and no
   7-day clock. The catch: the app only sees files it created or that you explicitly opened with
   it. Workable if we designate a specific Drive folder as the media root — which fits the
   RAID→Drive sync design anyway, since we'd be writing those files ourselves.
2. **Service account + a shared folder.** You share a Drive folder with the service account's
   address. No user-consent flow at all, so no token expiry. Clean, but only covers what you
   explicitly share.
3. **Publish the app and complete verification.** Correct long-term for an open-source project
   others will run, but it involves a CASA security assessment — not worth it before the project
   is real.

**Plan: build against (1), keep (2) as fallback, revisit (3) at public release.** The connector
sits behind a token-acquisition interface, so switching is a config change, not a rewrite. This
does not block phase 0 or 1.

---

## 12. Target hardware profile

The deployment this is designed against, kept generic deliberately. The specifics of any
particular installation — which devices sit in which rooms, when storage is powered, which
control interfaces are open — belong in a local, uncommitted note rather than in a public
repository.

| Role | Profile |
|---|---|
| Server / agent host | Low-power mini PC: 4 efficiency cores, integrated graphics, **no discrete GPU**. The only such machine — there is no second, beefier one |
| Bulk storage | Directly attached, **intermittently powered by design**. Not to be relied on for availability |
| Always-on core | Free-tier arm64 cloud instance, or a Pi later (§3.4) |
| Displays | **webOS**, **Tizen**, and **Android TV** — all three get an app from the same codebase |
| Audio | **Denon AVR-X1600H**: main zone plus ZONE2. HEOS + AirPlay 2, no Chromecast. One network player only (§5.6) |
| Network | DHCP throughout. Hence identity-not-IP addressing (§5.7), and no real address in any tracked file |

### Consequences already folded into the design

- **No GPU** → AI indexing is a CPU batch job measured in hours, Whisper is on-demand only (§7).
- **Modest CPU** → transcoding stays optional and last (§3.2); direct play and remux carry the load.
- **One PC, and it holds the RAID** → the agent and the storage share a single failure domain, so
  "PC off" and "RAID off" are the same event. The availability matrix in §3.3 already assumes this.
- **Everything on DHCP** → no IP is ever configuration (§5.7).
