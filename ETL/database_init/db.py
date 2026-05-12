"""
Shared SQLAlchemy database connector for ETL scripts.

This module centralizes database URL construction and engine creation so every
ETL script connects to Postgres the same way.

Jay Martinez - 5/8/2026
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from sqlalchemy import URL, Engine, create_engine, text


DEFAULT_ENV_FILE = Path("Database/.env")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5432
DEFAULT_DRIVER = "postgresql+psycopg2"


@dataclass(frozen=True)
class DatabaseSettings:
    """Connection settings needed to create a SQLAlchemy engine."""

    database: str
    user: str
    password: str
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    driver: str = DEFAULT_DRIVER

    @classmethod
    def from_env(
        cls,
        env_file: Path = DEFAULT_ENV_FILE,
        environ: Mapping[str, str] | None = None,
    ) -> "DatabaseSettings":
        """Load Postgres settings from an env file and process environment."""

        values = read_env_file(env_file)
        values.update(dict(environ or os.environ))

        database = require_setting(values, "POSTGRES_DB")
        user = require_setting(values, "POSTGRES_USER")
        password = require_setting(values, "POSTGRES_PASSWORD")
        host = values.get("POSTGRES_HOST", DEFAULT_HOST)
        port = int(values.get("POSTGRES_PORT", DEFAULT_PORT))

        return cls(
            database=database,
            user=user,
            password=password,
            host=host,
            port=port,
        )

    def to_url(self) -> URL:
        """Build a SQLAlchemy URL while safely escaping credentials."""

        return URL.create(
            self.driver,
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
        )


class DatabaseConnector:
    """Small wrapper for creating reusable SQLAlchemy engines."""

    def __init__(
        self,
        database_url: str | URL | None = None,
        env_file: Path = DEFAULT_ENV_FILE,
        echo: bool = False,
        pool_pre_ping: bool = True,
    ) -> None:
        self.database_url = database_url
        self.env_file = env_file
        self.echo = echo
        self.pool_pre_ping = pool_pre_ping
        self._engine: Engine | None = None

    @property
    def engine(self) -> Engine:
        """Return a lazily-created SQLAlchemy engine."""

        if self._engine is None:
            self._engine = create_engine(
                self.url,
                echo=self.echo,
                future=True,
                pool_pre_ping=self.pool_pre_ping,
            )
        return self._engine

    @property
    def url(self) -> str | URL:
        """Return an explicit URL, DATABASE_URL, or one built from env values."""

        if self.database_url:
            return self.database_url

        if os.getenv("DATABASE_URL"):
            return os.environ["DATABASE_URL"]

        return DatabaseSettings.from_env(self.env_file).to_url()

    def test_connection(self) -> tuple[str, str]:
        """Run a lightweight query and return database and user names."""

        with self.engine.connect() as connection:
            row = connection.execute(
                text("SELECT current_database(), current_user")
            ).one()
        return str(row[0]), str(row[1])

    def dispose(self) -> None:
        """Close pooled connections held by this connector's engine."""

        if self._engine is not None:
            self._engine.dispose()
            self._engine = None


def read_env_file(path: Path) -> dict[str, str]:
    """Read simple KEY=VALUE lines from an env file."""

    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        values[key.strip()] = clean_env_value(value)

    return values


def clean_env_value(value: str) -> str:
    """Remove surrounding whitespace and matching quotes from an env value."""

    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "'\"":
        return cleaned[1:-1]
    return cleaned


def require_setting(values: Mapping[str, str], key: str) -> str:
    """Return a required setting or raise a configuration error."""

    value = values.get(key)
    if value:
        return value

    raise RuntimeError(
        f"Missing database setting {key}. "
        "Set DATABASE_URL or configure Database/.env."
    )


def get_engine(
    database_url: str | URL | None = None,
    env_file: Path = DEFAULT_ENV_FILE,
    echo: bool = False,
) -> Engine:
    """Create a SQLAlchemy engine for one-off scripts."""

    connector = DatabaseConnector(
        database_url=database_url,
        env_file=env_file,
        echo=echo,
    )
    return connector.engine
