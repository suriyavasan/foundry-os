"""M10: the AI readiness gate and the coding-CLI runtimes (docs/specs/m10 sections 1-3)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import ApiModel, RequestModel, Timestamp
from app.schemas.providers import ProviderTest

BlockingReasonLiteral = Literal["no_tested_provider", "provider_test_failed", "no_default_model"]
WorkModeLiteral = Literal["api", "hybrid"]
WorkRuntimeLiteral = Literal["company_agent", "cli:claude_code", "cli:codex", "cli:opencode"]
CliIdLiteral = Literal["claude_code", "codex", "opencode"]
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
    available: bool  # npm is here and the CLI is not installed yet
    command: str
    requires: list[str]
    status: Literal["none", "running", "succeeded", "failed"] = "none"


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


class CliAuthUpdate(RequestModel):
    mode: CliAuthModeLiteral
    credential_id: str | None = Field(default=None, max_length=36)
    model_provider_id: str | None = Field(default=None, max_length=36)
    model: str | None = Field(default=None, max_length=160)


class AiSetupManagement(ApiModel):
    provider_id: str | None = None
    provider_name: str | None = None
    kind: str | None = None
    model: str | None = None
    last_test: ProviderTest | None = None


class AiSetupStatus(ApiModel):
    ready: bool
    blocking_reason: BlockingReasonLiteral | None = None
    management: AiSetupManagement
    work_mode: WorkModeLiteral
    work_runtime: WorkRuntimeLiteral | None = None
    clis: list[CliRuntimeStatus]


class AiSetupUpdate(RequestModel):
    management_provider_id: str = Field(min_length=1, max_length=36)
    management_model: str = Field(min_length=1, max_length=160)
    work_mode: WorkModeLiteral = "api"
    work_runtime: WorkRuntimeLiteral | None = None
