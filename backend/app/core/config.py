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
    # "password" (default, M7): username + password accounts, server sessions, CSRF, API tokens.
    # "local": the M3b-sec single-owner token gate, DEV ONLY -- it refuses non-loopback binds and
    # non-loopback clients.
    auth_mode: Literal["password", "local"] = "password"
    # The interface the server is bound to (the launcher / CLI sets it; uvicorn's own --host is not
    # visible to the app). `local` mode refuses to start when this is not a loopback address.
    bind_host: str = "127.0.0.1"
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

    # --- Milestone 3b: auth + company agent runtime ---------------------------------------------
    # Where the owner opens the app (used for the printed login link).
    public_url: str = "http://localhost:8700"
    # None = print the login URL only when stderr is a terminal; True/False force it.
    print_login_url: bool | None = None
    # Container image for sandboxed terminal/code tools (docker backend only; unverified: needs a
    # running Docker daemon).
    sandbox_docker_image: str = "python:3.12-slim"
    # Per-run wall time the adapter waits after SIGTERM before SIGKILL.
    runtime_kill_grace_seconds: float = 10.0
    runtime_health_timeout_seconds: float = 15.0

    # --- Milestone 7: accounts ---------------------------------------------------------------
    # Argon2id cost (OWASP-aligned defaults: 64 MiB, 3 passes). Tests lower them. Stored hashes
    # carry their own parameters, so raising these rehashes users at their next login.
    argon2_time_cost: int = 3
    argon2_memory_kib: int = 65536
    argon2_parallelism: int = 4
    session_idle_days: int = 14  # sliding window
    session_absolute_days: int = 30  # hard cap, never extended
    invitation_ttl_days: int = 7
    # Failed logins before the backoff starts, and its ceiling (per username and per IP).
    login_free_attempts: int = 3
    login_max_lockout_seconds: int = 900
    # Set the Secure flag on cookies even when the request looks like plain http (a TLS-terminating
    # proxy / tunnel in front). Requests that arrive as https always get Secure.
    cookie_secure: bool | None = None

    # --- Milestone 8: reachability & identity ------------------------------------------------
    # The port the server listens on (the launcher sets it; the tunnel forwards to it).
    port: int = 8700
    # Where verified helper binaries (cloudflared) are installed. Default <data_dir>/bin.
    bin_dir: Path | None = None
    # Explicit cloudflared / tailscale paths (override detection).
    cloudflared_path: Path | None = None
    tailscale_path: Path | None = None
    cloudflared_release_api: str = (
        "https://api.github.com/repos/cloudflare/cloudflared/releases/latest"
    )
    tunnel_health_interval_seconds: float = 30.0
    tunnel_start_timeout_seconds: float = 45.0
    # Restart backoff: 1s, 2s, 4s ... capped here; after this many failures in a row the tunnel
    # stops trying and reports an error.
    tunnel_max_backoff_seconds: float = 60.0
    tunnel_max_consecutive_failures: int = 8
    # The built web UI deployed to Vercel (the hosted control UI). Default <repo>/web/dist.
    web_dist_dir: Path | None = None
    # Serve web_dist_dir from the API port (SPA fallback, cache headers, CSP).
    serve_web: bool = True
    hosted_ui_max_bytes: int = 40 * 1024 * 1024
    # Largest webhook body accepted (inbound mail).
    webhook_max_bytes: int = 1024 * 1024
    # Resume a tunnel that was running when the server last stopped.
    tunnel_autoresume: bool = True

    # --- Milestone 10: coding-CLI runtimes -------------------------------------------------------
    # Extra directories searched (before PATH) for the claude / codex / opencode / npm binaries.
    cli_search_paths: list[str] = []

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
    def resolved_bin_dir(self) -> Path:
        if self.bin_dir is not None:
            return self.bin_dir if self.bin_dir.is_absolute() else BACKEND_DIR / self.bin_dir
        return self.resolved_data_dir / "bin"

    @property
    def resolved_web_dist(self) -> Path:
        if self.web_dist_dir is None:
            return REPO_DIR / "web" / "dist"
        return (
            self.web_dist_dir if self.web_dist_dir.is_absolute() else REPO_DIR / self.web_dist_dir
        )

    @property
    def logs_dir(self) -> Path:
        return self.resolved_data_dir / "logs"

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

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
