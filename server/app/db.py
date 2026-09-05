"""Database engine and a deliberately small migration runner.

Plain SQL migrations rather than Alembic: the schema leans on pgvector, enums and
partial indexes, and those read far more clearly as SQL than as generated Python.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .config import get_settings

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,   # survives Postgres restarts without a cold error
            future=True,
        )
    return _engine


def run_migrations() -> list[str]:
    """Apply any migration files not yet recorded. Returns those applied.

    Each file runs inside a transaction, so a failure leaves nothing half-applied.
    """
    engine = get_engine()
    applied: list[str] = []

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    name       TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )

    with engine.connect() as conn:
        done = {row[0] for row in conn.execute(text("SELECT name FROM schema_migrations"))}

    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in done:
            continue
        log.info("applying migration %s", path.name)
        sql = path.read_text(encoding="utf-8")
        with engine.begin() as conn:
            conn.execute(text(sql))
            conn.execute(
                text("INSERT INTO schema_migrations (name) VALUES (:n)"),
                {"n": path.name},
            )
        applied.append(path.name)

    return applied


def check_connection() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - health check reports, never raises
        log.exception("database connection failed")
        return False
