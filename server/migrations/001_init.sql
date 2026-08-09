-- Hearth initial schema.
-- Design rationale lives in docs/ARCHITECTURE.md §9; the principles this schema
-- exists to enforce are in §2.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- fuzzy filename matching (playlist repair)
CREATE EXTENSION IF NOT EXISTS pgcrypto;     -- gen_random_uuid()

-- ───────────────────────────────────────────────────────────────────────────
-- Identity. Invite-only; there is no public registration path.
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    handle      TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    is_admin    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Passkeys. Primary factor: nothing phishable, nothing to leak (§6).
CREATE TABLE credentials (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    credential_id   BYTEA NOT NULL UNIQUE,
    public_key      BYTEA NOT NULL,
    -- Monotonic counter from the authenticator; a decrease signals cloning.
    sign_count      BIGINT NOT NULL DEFAULT 0,
    transports      TEXT[],
    nickname        TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at    TIMESTAMPTZ
);
CREATE INDEX credentials_user_idx ON credentials(user_id);

-- Device-bound, individually revocable from the UI.
CREATE TABLE auth_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    refresh_hash    BYTEA NOT NULL UNIQUE,   -- hash only; the token itself is never stored
    device_label    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked_at      TIMESTAMPTZ
);
CREATE INDEX auth_sessions_user_idx ON auth_sessions(user_id) WHERE revoked_at IS NULL;

CREATE TABLE audit_log (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
    event       TEXT NOT NULL,
    detail      JSONB NOT NULL DEFAULT '{}'::jsonb,
    ip          INET
);
CREATE INDEX audit_log_at_idx ON audit_log(at DESC);

-- ───────────────────────────────────────────────────────────────────────────
-- Sources and the catalog.
-- ───────────────────────────────────────────────────────────────────────────

CREATE TYPE source_kind AS ENUM ('local', 'gdrive', 'takeout');

CREATE TABLE sources (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind            source_kind NOT NULL,
    name            TEXT NOT NULL,
    mount_prefix    TEXT NOT NULL UNIQUE,    -- e.g. '/local/raid' — the unified namespace (§4)
    -- Envelope-encrypted: a DB dump alone must not yield OAuth tokens (§6).
    config_encrypted BYTEA,
    agent_id        UUID,
    last_seen_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TYPE item_kind AS ENUM ('audio', 'video', 'photo', 'doc', 'other');

CREATE TABLE items (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_hash BYTEA UNIQUE,               -- BLAKE3; NULL until hashed
    kind         item_kind NOT NULL,
    size_bytes   BIGINT,
    duration_ms  BIGINT,
    created_at   TIMESTAMPTZ,                -- content date (EXIF/mtime), not row date
    indexed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX items_kind_created_idx ON items(kind, created_at DESC);

-- One item can exist in several places (RAID + Drive). Playback picks whichever
-- replica is reachable, which is how cloud content keeps playing with the PC off (§4).
CREATE TABLE replicas (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id     UUID NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    source_id   UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    -- Principle #1: path and filename are first-class data, never a fallback (§2).
    dir_path    TEXT NOT NULL,
    filename    TEXT NOT NULL,
    ext         TEXT,
    mtime       TIMESTAMPTZ,
    available   BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (source_id, dir_path, filename)
);
CREATE INDEX replicas_item_idx ON replicas(item_id);
CREATE INDEX replicas_dir_idx  ON replicas(source_id, dir_path);
-- Trigram index: filename search must be fast and typo-tolerant.
CREATE INDEX replicas_filename_trgm_idx ON replicas USING gin (filename gin_trgm_ops);

CREATE TYPE metadata_origin AS ENUM ('file', 'musicbrainz', 'ai', 'user');

-- `origin` is what makes principle #1 enforceable: the UI can always distinguish
-- what the file itself claimed from what a service or a model guessed (§9).
CREATE TABLE item_metadata (
    item_id     UUID NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    value       TEXT,
    origin      metadata_origin NOT NULL,
    confidence  REAL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (item_id, key, origin)
);
CREATE INDEX item_metadata_key_idx ON item_metadata(key, value);

-- Computed at home on the PC's GPU, stored here so semantic search still works
-- when the PC is off (§7).
CREATE TABLE embeddings (
    item_id     UUID NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    chunk_idx   INT NOT NULL DEFAULT 0,
    model       TEXT NOT NULL,
    vector      vector(768) NOT NULL,
    PRIMARY KEY (item_id, chunk_idx, model)
);

-- ───────────────────────────────────────────────────────────────────────────
-- Playlists.
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE playlists (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT NOT NULL,
    source_format TEXT,                      -- 'm3u', 'pls', 'smart', NULL if native
    rules         JSONB,                     -- smart playlists
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE playlist_items (
    playlist_id  UUID NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    position     INT NOT NULL,
    item_id      UUID REFERENCES items(id) ON DELETE SET NULL,
    -- Kept even when item_id resolves, so an unresolvable entry is still visible
    -- to the user rather than silently vanishing during import repair (§5.2).
    original_ref TEXT NOT NULL,
    PRIMARY KEY (playlist_id, position)
);

-- ───────────────────────────────────────────────────────────────────────────
-- Renderers, zones, sessions — the control tower (§5.8).
-- ───────────────────────────────────────────────────────────────────────────

CREATE TYPE renderer_kind  AS ENUM ('tvapp', 'heos', 'cast', 'browser');
CREATE TYPE renderer_state AS ENUM ('ready', 'asleep', 'unavailable');

CREATE TABLE renderers (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind         renderer_kind NOT NULL,
    name         TEXT NOT NULL,
    -- SSDP USN or app instance id. Never an IP address (§5.7).
    device_key   TEXT NOT NULL UNIQUE,
    capabilities JSONB NOT NULL DEFAULT '{}'::jsonb,
    state        renderer_state NOT NULL DEFAULT 'unavailable',
    last_seen_at TIMESTAMPTZ
);

CREATE TABLE zones (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL UNIQUE,       -- 'Living Room', 'Balcony', 'Bedroom'
    renderer_id  UUID REFERENCES renderers(id) ON DELETE SET NULL,
    -- Ordered hardware commands to make sound actually come out, e.g. for the
    -- balcony: AVR power on -> Z2ON -> Z2NET -> set volume (§5.8).
    preroll      JSONB NOT NULL DEFAULT '[]'::jsonb,
    postroll     JSONB NOT NULL DEFAULT '[]'::jsonb,
    idle_timeout_s INT
);

CREATE TYPE playback_state AS ENUM ('idle', 'playing', 'paused', 'buffering');

-- Server-owned playback state. Survives the phone dying and the renderer
-- rebooting — that is the whole point of the control tower design (§5.8).
CREATE TABLE play_sessions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    zone_id      UUID NOT NULL UNIQUE REFERENCES zones(id) ON DELETE CASCADE,
    queue        JSONB NOT NULL DEFAULT '[]'::jsonb,   -- ordered item ids
    cursor       INT NOT NULL DEFAULT 0,
    position_ms  BIGINT NOT NULL DEFAULT 0,
    volume       INT CHECK (volume BETWEEN 0 AND 100),
    state        playback_state NOT NULL DEFAULT 'idle',
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Resume points, per user per item, synced across devices (§5.3).
CREATE TABLE progress (
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_id      UUID NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    position_ms  BIGINT NOT NULL,
    finished     BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, item_id)
);
