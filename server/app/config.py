"""Runtime configuration.

Everything is environment-driven so the same image runs unchanged on Windows,
a Raspberry Pi and a free cloud instance (ARCHITECTURE.md §3.4).
"""

from __future__ import annotations

import base64
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Core ────────────────────────────────────────────────────────────────
    public_origin: str = "http://localhost:8080"
    log_level: str = "INFO"

    # ── WebAuthn / passkeys ─────────────────────────────────────────────────
    rp_id: str = "localhost"
    rp_name: str = "Homesh"

    # ── Database ────────────────────────────────────────────────────────────
    database_url: str = "postgresql+psycopg://homesh:homesh@db:5432/homesh"

    # ── Cryptography ────────────────────────────────────────────────────────
    # 32 raw bytes, urlsafe-base64 encoded. Empty is tolerated only so the app
    # can boot and report a clear error rather than crashing on import.
    master_key: str = ""
    secret_key: str = ""

    # The key backups are encrypted with before they leave the house.
    #
    # Separate from the two above on purpose, and the reason is the one thing
    # that makes off-site storage safe: this key must exist somewhere the server
    # does not. Deriving it from MASTER_KEY would mean losing the machine loses
    # the backups too, which is the situation they are for. Empty disables
    # off-site copies entirely rather than sending anything in the clear.
    backup_key: str = ""

    # Where encrypted backups are sent. Empty means they stay on this disk.
    #
    # S3 in the protocol sense rather than the Amazon sense: Oracle Object
    # Storage, Cloudflare R2, Backblaze and AWS all speak it, so the provider is
    # a setting. Which matters, because the first choice was Google Drive and it
    # turned out to be impossible -- a service account owns no storage, so it
    # cannot create a file even in a folder shared with it as Editor.
    offsite_provider: str = ""
    offsite_region: str = ""
    offsite_bucket: str = ""
    offsite_access_key: str = ""
    offsite_secret_key: str = ""
    # Oracle's endpoint is per-tenancy and the tenancy is named by this. Nobody
    # else needs it; it is on the bucket's own page in their console.
    offsite_namespace: str = ""

    # A Drive folder that is shared with the service account and is not a
    # library. Vestigial in one sense -- backups no longer go to Drive, because
    # Google will not let a service account create a file -- and still needed in
    # another: the folder exists, it is still shared, and Drive discovery is
    # indiscriminate by design, so without this it registers itself as a source
    # and gets scanned.
    backup_folder: str = "Homesh Backups"

    # "primary" on the PC, "standby" on the Oracle machine. See docs/STANDBY.md.
    #
    # Primary is the default because it is the safe mistake: a machine that
    # wrongly believes it is the primary behaves exactly as Homesh always has,
    # while one that wrongly believes it is a standby refuses most changes and
    # replaces its own database with somebody else's backup every hour.
    homesh_role: str = "primary"

    # Where the standby answers, on the PC only. Handed to the phone app so it
    # can fall back there when the PC does not answer -- the PC is the one place
    # the phone reliably hears from first. Written into .env by
    # tools/deploy-standby.ps1; an address, so configuration and never code.
    standby_origin: str = ""

    media_url_ttl_minutes: int = Field(default=5, ge=1, le=60)

    # Receivers pull for the length of a whole track or film, so the short browser
    # TTL would expire mid-playback. Longer, but still bound to one item and one
    # user, and only handed to devices on the local network.
    cast_url_ttl_minutes: int = Field(default=240, ge=5, le=1440)

    # Thumbnails and derived files. The container mounts a volume here, but the app
    # must also run outside one — on a developer machine, or in CI — so this cannot
    # be a hardcoded absolute path.
    cache_dir: str = "/var/lib/homesh/cache"

    # Receiver address, used when SSDP discovery cannot run.
    #
    # Docker's bridge network does not forward multicast to the LAN, so a
    # containerised core cannot discover the AVR itself — confirmed in practice.
    # In the split topology the home agent does discovery and reports the address;
    # until then this seeds it. Still keyed by identity internally, so a DHCP
    # change only costs a re-seed rather than a redesign (ARCHITECTURE.md §5.7).
    denon_host: str = ""

    # The origin a device on the LAN can fetch media from, e.g.
    # http://192.0.2.10:8080. Not PUBLIC_ORIGIN, which is usually localhost and
    # means nothing to a receiver across the room: the receiver pulls the stream
    # itself rather than receiving it from the browser.
    lan_base_url: str = ""

    # How often every source is rescanned without being asked. 0 disables it.
    # Daily by design: Drive charges quota per listing, and photos dropped into a
    # shared folder are not urgent — anything that is has the manual button.
    scan_interval_hours: int = 24

    # Service-account key for Google Drive. A credential, so it lives outside the
    # repository and is referenced by path rather than pasted into config.
    gdrive_key_file: str = "/run/secrets/gdrive.json"

    # Local roots to index, as "Name=/path" pairs separated by ';'.
    # Deployment facts, so they live in the environment rather than the database —
    # a stored path would silently stop matching the container's mounts.
    media_roots: str = ""

    @property
    def parsed_media_roots(self) -> list[tuple[str, str]]:
        roots: list[tuple[str, str]] = []
        for chunk in self.media_roots.split(";"):
            chunk = chunk.strip()
            if not chunk:
                continue
            name, sep, path = chunk.partition("=")
            if not sep or not path.strip():
                raise ValueError(f"MEDIA_ROOTS entry must be 'Name=/path', got {chunk!r}")
            roots.append((name.strip(), path.strip()))
        return roots

    @field_validator("master_key", "secret_key")
    @classmethod
    def _validate_key(cls, v: str) -> str:
        if not v:
            return v
        try:
            raw = base64.urlsafe_b64decode(v)
        except Exception as exc:  # noqa: BLE001 - surfaced as a config error
            raise ValueError("must be urlsafe-base64") from exc
        if len(raw) != 32:
            raise ValueError(f"must decode to 32 bytes, got {len(raw)}")
        return v

    @property
    def is_configured(self) -> bool:
        """False until the operator has generated keys. Guards startup."""
        return bool(self.master_key and self.secret_key)

    @property
    def secure_cookies(self) -> bool:
        return self.public_origin.startswith("https://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
