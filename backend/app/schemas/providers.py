from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, field_validator

from app.schemas.agent import MAX_BUDGET_MICROS
from app.schemas.common import ApiModel, RequestModel, Timestamp

CredentialKindLiteral = Literal["api_key", "token", "other"]
ProviderKindLiteral = Literal["ollama", "openrouter", "anthropic", "openai", "openai_compatible"]


class Credential(ApiModel):
    """Metadata only. The value is never returned by any endpoint."""

    id: str
    company_id: str
    name: str
    kind: CredentialKindLiteral
    created_by_type: str
    created_by_id: str | None
    revoked_at: Timestamp | None
    # M6 scope: which control-plane provider uses it and what agents may do with it.
    provider: str | None = None
    allowed_actions: list[str] = []
    allowed_families: list[str] = []
    created_at: Timestamp
    updated_at: Timestamp


class CredentialScope(RequestModel):
    """Which provider executes calls made with a credential and what agents may do with it."""

    provider: str | None = Field(
        default=None, min_length=1, max_length=32, pattern=r"^[a-z0-9_.-]+$"
    )
    allowed_actions: list[str] | None = Field(default=None, max_length=50)
    allowed_families: list[str] | None = Field(default=None, max_length=20)


class CredentialCreate(CredentialScope):
    name: str = Field(min_length=1, max_length=120)
    kind: CredentialKindLiteral = "api_key"
    value: SecretStr = Field(min_length=1, max_length=8192)


class CredentialRotate(RequestModel):
    value: SecretStr = Field(min_length=1, max_length=8192)


class ProviderTest(ApiModel):
    status: Literal["ok", "warning", "error"]
    message: str
    checked_at: Timestamp
    model_available: bool | None = None
    tool_calling: bool | None = None
    context_length_configured: int | None = None
    context_length_served: int | None = None
    warnings: list[str] = []
    latency_ms: int | None = None


class ModelProvider(ApiModel):
    id: str
    company_id: str
    name: str
    kind: ProviderKindLiteral
    base_url: str | None
    credential_id: str | None
    default_model: str
    context_length: int
    is_local: bool
    enabled: bool
    input_price_per_mtok_micros: int | None
    output_price_per_mtok_micros: int | None
    last_test: ProviderTest | None
    is_company_default: bool = False
    created_at: Timestamp
    updated_at: Timestamp


class ModelProviderCreate(RequestModel):
    name: str = Field(min_length=1, max_length=120)
    kind: ProviderKindLiteral
    base_url: str | None = Field(default=None, max_length=500)
    credential_id: str | None = None
    default_model: str = Field(min_length=1, max_length=160)
    context_length: int = Field(default=32768, ge=8192, le=10_000_000)
    is_local: bool | None = None  # default: true for ollama, false otherwise
    enabled: bool = True
    input_price_per_mtok_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)
    output_price_per_mtok_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)
    make_default: bool = False  # also set as the company default model


class ModelProviderUpdate(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)
    credential_id: str | None = None
    default_model: str | None = Field(default=None, min_length=1, max_length=160)
    context_length: int | None = Field(default=None, ge=8192, le=10_000_000)
    is_local: bool | None = None
    enabled: bool | None = None
    input_price_per_mtok_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)
    output_price_per_mtok_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)

    @field_validator("name", "default_model", "context_length", "is_local", "enabled")
    @classmethod
    def _not_null(cls, v):
        if v is None:
            raise ValueError("must not be null")
        return v


class DetectedModel(ApiModel):
    name: str
    parameter_size: str | None = None
    quantization: str | None = None
    context_length_native: int | None = None
    tool_calling: bool | None = None  # None = unknown (older Ollama without capabilities)
    recommended: bool = False


class DetectResult(ApiModel):
    kind: Literal["ollama"] = "ollama"
    base_url: str
    reachable: bool
    models: list[DetectedModel] = []
    recommended_model: str | None = None
    served_context_length: int | None = None
    warnings: list[str] = []
    already_registered_provider_id: str | None = None
    registered: ModelProvider | None = None  # POST with register=true


class DetectRequest(RequestModel):
    base_url: str | None = Field(default=None, max_length=500)
    # Wire name is "register" (a pydantic attribute of that name would shadow ABCMeta.register).
    register_now: bool = Field(default=False, alias="register")
    model: str | None = Field(default=None, max_length=160)
    context_length: int = Field(default=32768, ge=8192, le=10_000_000)
    make_default: bool = True
