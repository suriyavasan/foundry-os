"""Application settings (pydantic-settings, env prefix ``COMPANY_OS_``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_URL = "sqlite:///../data/company_os.db"
APP_VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COMPANY_OS_",
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = DEFAULT_DATABASE_URL
    # Only "local" exists in M1: a single owner account, acceptable ONLY on localhost.
    auth_mode: Literal["local"] = "local"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Hermes runtime. These use the plain HERMES_* names (no COMPANY_OS_ prefix).
    hermes_api_url: str | None = Field(
        default=None, validation_alias=AliasChoices("HERMES_API_URL", "COMPANY_OS_HERMES_API_URL")
    )
    hermes_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("HERMES_API_KEY", "COMPANY_OS_HERMES_API_KEY")
    )

    @property
    def resolved_database_url(self) -> str:
        """Relative SQLite paths are resolved against ``backend/`` (not the CWD)."""
        return resolve_database_url(self.database_url)

    def prepare_data_dir(self) -> None:
        """Create the parent directory of a file-based SQLite database."""
        path = sqlite_file_path(self.resolved_database_url)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)


def resolve_database_url(url: str) -> str:
    prefix = "sqlite:///"
    if url.startswith(prefix) and not url.startswith("sqlite:////"):
        raw = url[len(prefix) :]
        if raw and raw != ":memory:" and not raw.startswith("file:"):
            return f"{prefix}{(BACKEND_DIR / raw).resolve()}"
    return url


def sqlite_file_path(url: str) -> Path | None:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    raw = url[len(prefix) :]
    if not raw or raw == ":memory:" or raw.startswith("file:"):
        return None
    return Path(raw)


@lru_cache
def get_settings() -> Settings:
    return Settings()
