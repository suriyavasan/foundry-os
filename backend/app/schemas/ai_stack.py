"""M17: the AI stack (docs/specs/m17-ai-stack.md)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import ApiModel, RequestModel, Timestamp

OptionLiteral = Literal[
    "claude_plan",
    "anthropic_api",
    "openai_api",
    "openrouter",
    "openai_compatible",
    "ollama",
    "cursor_plan",
    "cursor_api",
    "codex_api",
    "opencode",
    "antigravity_api",
]
UseForLiteral = Literal["everything", "heavy", "everyday"]
StateLiteral = Literal[
    "ready",
    "needs_test",
    "test_failed",
    "needs_sign_in",
    "not_installed",
    "paused",
    "disabled",
]
BlockingLiteral = Literal["no_ai", "no_tested_ai", "no_heavy_ai", "no_everyday_ai"]


class AiLastTest(ApiModel):
    ok: bool
    detail: str
    tested_at: Timestamp
    model: str | None = None


class AiStatus(ApiModel):
    state: StateLiteral
    message: str | None = None
    last_test: AiLastTest | None = None
    signed_in: bool | None = None  # plan connections
    account: str | None = None  # e-mail if the CLI reports one
    paused_until: Timestamp | None = None


class AiConnectionOut(ApiModel):
    id: str
    position: int
    rank_label: str
    label: str
    engine: Literal["api", "cli"]
    option: OptionLiteral
    provider_kind: str | None = None
    model_provider_id: str | None = None
    model: str | None = None
    cli_id: str | None = None
    auth_mode: Literal["plan", "api_key", "provider"] | None = None
    credential_id: str | None = None
    cli_provider_id: str | None = None
    use_for: UseForLiteral
    share: int
    enabled: bool
    status: AiStatus
    runs_24h: int
    # Concurrent runs this connection may serve (null: no limit). 1 for a Cursor plan sign-in.
    max_concurrency: int | None = None
    # Plan connections: the CLI's private home, relative to the data directory (not a secret).
    home_dir: str | None = None
    created_at: Timestamp


class SummaryEntry(ApiModel):
    connection_id: str
    label: str
    percent: int
    backup: bool = False


class AiSummary(ApiModel):
    heavy: list[SummaryEntry]
    everyday: list[SummaryEntry]


class AiInstall(ApiModel):
    available: bool
    command: str | None = None


class AiOption(ApiModel):
    option: OptionLiteral
    label: str
    group: Literal["plan", "api", "local"]
    engine: Literal["api", "cli"]
    cli_id: str | None = None
    provider_kind: str | None = None
    available: bool
    reason: str | None = None
    needs_install: bool
    install: AiInstall | None = None
    warning: str | None = None


class AiStackOut(ApiModel):
    items: list[AiConnectionOut]
    ready: bool
    blocking_reason: BlockingLiteral | None = None
    summary: AiSummary
    options: list[AiOption]


class AiConnectionCreate(RequestModel):
    option: OptionLiteral
    label: str | None = Field(default=None, min_length=1, max_length=120)
    use_for: UseForLiteral = "everything"
    share: int = Field(default=2, ge=0, le=4)
    model: str | None = Field(default=None, min_length=1, max_length=160)
    model_provider_id: str | None = Field(default=None, max_length=36)
    credential_id: str | None = Field(default=None, max_length=36)
    cli_provider_id: str | None = Field(default=None, max_length=36)


class AiConnectionPatch(RequestModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    use_for: UseForLiteral | None = None
    share: int | None = Field(default=None, ge=0, le=4)
    enabled: bool | None = None
    model: str | None = Field(default=None, min_length=1, max_length=160)


class AiOrder(RequestModel):
    ids: list[str] = Field(max_length=100)
