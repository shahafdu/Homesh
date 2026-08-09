# Hearth

A self-hosted media server for video, music, photos and documents that treats your
**filenames as data**, your **folders as a first-class view**, and your **phone as a
control tower** for every screen and speaker in the house.

It streams from Google Drive and from local storage through one catalog, and it keeps
working when the machine holding your files is switched off.

> **Status: early development.** Phases 0–2 are done. Passkey auth, the catalog, folder
> browsing, search, thumbnails and playback all work — audio with a folder queue, video
> by direct play, photos and documents. Casting to a TV or the Denon, Google Drive, and
> AI search are still ahead.
>
> [Architecture and roadmap](docs/ARCHITECTURE.md) · [User guide](docs/USER_GUIDE.md)

---

## Why

Built after living with Plex and running into eight specific walls:

| Problem | Hearth's answer |
|---|---|
| Paywalled features | AGPL-3.0. No tiers, nothing withheld |
| Separate apps for music and video | One app, one catalog, all media types |
| Documents unsupported | Documents are a first-class media kind |
| Shows metadata only — corrupt tags mean a mystery file | Filename and path are indexed, displayed and searchable; metadata records its `origin` and never overwrites |
| Folder browsing is an afterthought | The real directory tree is a primary view |
| No AI search over a large library | Local CLIP/text embeddings, natural-language search |
| Can't reach it when the server is off | Catalog and thumbnails live on an always-on node |
| No Google Photos access | Photos ingested from your own storage, not a closed API |

## Design in one diagram

```
CONTROL PLANE (always on)          DATA PLANE (intermittent)      CLIENTS
┌──────────────────────┐           ┌────────────────────┐        ┌──────────────┐
│ API · catalog        │◄──────────│ Home PC + RAID     │        │ Phone (PWA)  │
│ Postgres + pgvector  │ WireGuard │ agent, file ranges │        │ TV apps      │
│ auth · thumbnails    │ (outbound)│ thumbnails, vectors│        │ Denon ZONE2  │
└──────────────────────┘           └────────────────────┘        └──────────────┘
        ▲                          ┌────────────────────┐
        └──────────────────────────│ Google Drive       │
                                   └────────────────────┘
```

Three ideas carry most of the weight:

1. **The catalog is always up; the bytes may not be.** Browsing, search and thumbnails
   live on the always-on node, so your whole library stays searchable with the storage
   machine powered down.
2. **The server owns playback state.** Sessions are bound to *zones*, not devices — so
   your phone can die mid-song and the music keeps playing, and moving audio to another
   room is a rebinding rather than a re-cast.
3. **Identity, not IP.** Devices are keyed by stable identity; DHCP changes are
   self-healing. Home components dial *outbound* only — no inbound ports, ever.

## Running it

Requires Docker.

```bash
cp .env.example .env
# generate the two keys:
python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
docker compose up -d
```

Then open <http://localhost:8080>. On first run the server logs a one-time bootstrap
code — `docker compose logs api` — which you use to create the first account. There is
no public registration path; every account after the first is invited by an admin.

Authentication is **passkeys only** (WebAuthn). No passwords to leak or phish.

Full setup, including pointing it at your media and the Windows/WSL gotcha, is in the
[user guide](docs/USER_GUIDE.md).

### Development

```bash
docker compose up -d db api      # API on :8080
cd web && npm install && npm run dev   # client on :5173, proxies /api
```

## Runs anywhere

Multi-arch images (`linux/amd64` + `linux/arm64`) so the same build runs on a Windows
PC, a Raspberry Pi, or a free-tier ARM cloud instance. Topology is configuration:
all-in-one on one box, or core-in-the-cloud with an agent at home.

## Security

Personal data on the public internet, so this is a feature rather than a checklist:
outbound-only home connections with no inbound ports, passkeys, envelope-encrypted
OAuth tokens, short-lived HMAC-signed media URLs, access-controlled thumbnail cache,
and an audit log. Details in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §6.

Found a vulnerability? See [`SECURITY.md`](SECURITY.md).

## License

[AGPL-3.0](LICENSE). Derivatives must stay open — including ones offered as a hosted
service, which is the gap plain GPL leaves.
