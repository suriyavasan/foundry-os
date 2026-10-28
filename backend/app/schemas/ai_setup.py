"""M10: the AI readiness gate and the coding-CLI runtimes (docs/specs/m10 sections 1-3)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import ApiModel, RequestModel, Timestamp

BlockingReasonLiteral = Literal["no_ai", "no_tested_ai", "no_heavy_ai", "no_everyday_ai"]
CliIdLiteral = Literal["claude_code", "codex", "opencode", "antigravity", "cursor"]
CliAuthModeLiteral = Literal["own_login", "api_key"]


class CliLastTest(ApiModel):
    ok: bool
    detail: str
    tested_at: Timestamp


class CliAuth(ApiModel):
    mode: CliAuthModeLiteral | None = None
    configured: bool = False
    credential_id: str | None = None
    last_test: CliLastTest | None = None
    # OpenCode only: run against one of the company's model providers (its endpoint and key).
    model_provider_id: str | None = None
    model: str | None = None


class CliSandbox(ApiModel):
    available: bool
    kind: Literal["os", "docker", "none"]
    detail: str


class CliInstall(ApiModel):
    available: bool  # npm is here, the CLI has an npm package and is not installed yet
    command: str | None = None  # None: no automatic install (see ``manual``)
    requires: list[str]
    status: Literal["none", "running", "succeeded", "failed"] = "none"
    # Run by the person themselves when there is no automatic install; never run by the server.
    manual: str | None = None


class CliLogin(ApiModel):
    """Claude Code's sign-in state (from ``claude auth status``; cached up to 30 s)."""

    signed_in: bool
    auth_method: str | None = None
    email: str | None = None


class CliRuntimeStatus(ApiModel):
    id: CliIdLiteral
    display_name: str
    installed: bool
    binary_path: str | None = None
    version: str | None = None
    auth_modes: list[CliAuthModeLiteral]
    auth: CliAuth
    sandbox: CliSandbox
    install: CliInstall
    login_hint: str
    login: CliLogin | None = None  # Claude Code only
    warnings: list[str] = []


class CliRuntimeList(ApiModel):
    items: list[CliRuntimeStatus]
    next_cursor: str | None = None


class CliInstallRequest(RequestModel):
    confirm: bool = False


class CliInstallLog(ApiModel):
    """The supervised install: state plus the (redacted) output so far."""

    id: CliIdLiteral
    status: Literal["none", "running", "succeeded", "failed"]
    command: str | None = None
    started_at: Timestamp | None = None
    finished_at: Timestamp | None = None
    exit_code: int | None = None
    lines: list[str] = []


class ClaudeLoginJob(ApiModel):
    state: Literal["idle", "running", "succeeded", "failed"]
    started_at: Timestamp | None = None
    finished_at: Timestamp | None = None
    # The sign-in link Claude Code printed (an authorization URL, not a credential).
    url: str | None = None
    message: str | None = None


class ClaudeLoginStatus(ApiModel):
    signed_in: bool
    auth_method: str | None = None
    email: str | None = None
    checked_at: Timestamp
    config_dir_ok: bool
    login: ClaudeLoginJob


class ClaudeLoginRequest(RequestModel):
    method: Literal["claudeai", "console"] = "claudeai"


class CliAuthUpdate(RequestModel):
    mode: CliAuthModeLiteral
    credential_id: str | None = Field(default=None, max_length=36)
    model_provider_id: str | None = Field(default=None, max_length=36)
    model: str | None = Field(default=None, max_length=160)


class AiSetupStatus(ApiModel):
    """M17: derived from the AI stack (``/ai-stack``); kept so the readiness gate keeps working."""

    ready: bool
    blocking_reason: BlockingReasonLiteral | None = None
    stack_count: int
    clis: list[CliRuntimeStatus]
