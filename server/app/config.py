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
