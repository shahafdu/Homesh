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
    rp_name: str = "Hearth"

    # ── Database ────────────────────────────────────────────────────────────
    database_url: str = "postgresql+psycopg://hearth:hearth@db:5432/hearth"

    # ── Cryptography ────────────────────────────────────────────────────────
    # 32 raw bytes, urlsafe-base64 encoded. Empty is tolerated only so the app
    # can boot and report a clear error rather than crashing on import.
    master_key: str = ""
    secret_key: str = ""

    media_url_ttl_minutes: int = Field(default=5, ge=1, le=60)

    # Thumbnails and derived files. The container mounts a volume here, but the app
    # must also run outside one — on a developer machine, or in CI — so this cannot
    # be a hardcoded absolute path.
    cache_dir: str = "/var/lib/hearth/cache"

    # Receiver address, used when SSDP discovery cannot run.
    #
    # Docker's bridge network does not forward multicast to the LAN, so a
    # containerised core cannot discover the AVR itself — confirmed in practice.
    # In the split topology the home agent does discovery and reports the address;
    # until then this seeds it. Still keyed by identity internally, so a DHCP
    # change only costs a re-seed rather than a redesign (ARCHITECTURE.md §5.7).
    denon_host: str = ""

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
