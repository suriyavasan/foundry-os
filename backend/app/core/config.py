"""Application settings (pydantic-settings, env prefix ``COMPANY_OS_``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
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

    # --- Milestone 2: work engine -------------------------------------------------------
    # Directory for artifacts, workspaces and the auto-generated secret key. Default: the
    # SQLite file's directory (../data for the default URL), else <repo>/data.
    data_dir: Path | None = None
    # HMAC key for agent run tokens. Unset in local mode -> <data_dir>/secret.key (0600).
    secret_key: SecretStr | None = None
    # Registers the deterministic ScriptedRuntime (kind "test_fixture"). Never on by default.
    enable_test_runtimes: bool = False
    # Run the worker + scheduler loops inside the API process (one-command local dev).
    embedded_worker: bool = False
    max_upload_bytes: int = 25 * 1024 * 1024
    # Where runtimes reach the agent API (passed to them in ExecutionRequest).
    agent_api_url: str = "http://127.0.0.1:8700/api/agent/v1"
    worker_concurrency: int = 4
    engine_tick_seconds: float = 2.0  # scheduler tick (dispatch, schedules)
    engine_poll_seconds: float = 0.5  # worker queue poll

    # --- Milestone 4: skills & HR -----------------------------------------------------------
    # Checkout of our Hermes fork: source of the bundled/optional skills and of the skills guard.
    runtime_dir: Path | None = None  # default <repo>/runtime
    # The runtime's own virtualenv python (runs `-m company_agent.skills_scan`). Default
    # <runtime_dir>/.venv/bin/python. Missing -> non-builtin skill imports are refused
    # (fail closed).
    runtime_python: Path | None = None
    # TEMPORARY (until the M3b credential store): optional token for GitHub skill discovery/install.
    # Never logged or returned by the API. Unset -> unauthenticated, rate-limited requests.
    github_token: SecretStr | None = None
    github_api_url: str = "https://api.github.com"
    skill_scan_timeout_seconds: float = 120.0

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

    @property
    def resolved_data_dir(self) -> Path:
        if self.data_dir is not None:
            return self.data_dir if self.data_dir.is_absolute() else BACKEND_DIR / self.data_dir
        db_path = sqlite_file_path(self.resolved_database_url)
        if db_path is not None:
            return db_path.parent
        return REPO_DIR / "data"

    @property
    def artifacts_dir(self) -> Path:
        return self.resolved_data_dir / "artifacts"

    @property
    def workspaces_dir(self) -> Path:
        return self.resolved_data_dir / "workspaces"

    @property
    def skills_blobs_dir(self) -> Path:
        return self.resolved_data_dir / "skills" / "blobs"

    @property
    def resolved_runtime_dir(self) -> Path:
        if self.runtime_dir is None:
            return REPO_DIR / "runtime"
        return self.runtime_dir if self.runtime_dir.is_absolute() else REPO_DIR / self.runtime_dir

    @property
    def resolved_runtime_python(self) -> Path:
        if self.runtime_python is not None:
            return self.runtime_python
        return self.resolved_runtime_dir / ".venv" / "bin" / "python"

    def prepare_data_dir(self) -> None:
        """Create the parent directory of a file-based SQLite database and the data dir."""
        path = sqlite_file_path(self.resolved_database_url)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self.resolved_data_dir.mkdir(parents=True, exist_ok=True)


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
