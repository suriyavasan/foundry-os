from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from app.schemas.agent import MAX_BUDGET_MICROS
from app.schemas.common import ApiModel, RequestModel, Timestamp

AutonomyModeLiteral = Literal["autonomous", "supervised"]
SandboxBackendLiteral = Literal["auto", "none", "docker"]
ApprovalKindLiteral = Literal[
    "external_purchase",
    "budget_increase",
    "external_communication",
    "production_deploy",
    "sensitive_action",
    "hire",
    "other",
]


class Policy(ApiModel):
    id: str
    company_id: str
    autonomy_mode: AutonomyModeLiteral
    human_approval_required_for: list[ApprovalKindLiteral]
    daily_budget_micros: int | None
    monthly_budget_micros: int | None
    max_concurrent_runs: int
    max_delegation_depth: int
    max_tasks_per_planning_cycle: int
    max_attempts_per_task: int
    max_revisions: int
    max_run_seconds: int
    max_headcount: int
    heartbeat_interval_seconds: int
    default_run_reservation_micros: int
    allowed_model_providers: list[str]
    auto_activate_hires: bool = True
    hire_default_monthly_budget_micros: int | None = None
    # M3b: the company default model (ModelProvider id + model name) and the sandbox for code tools.
    default_model_provider_id: str | None = None
    default_model_name: str | None = None
    sandbox_backend: SandboxBackendLiteral = "auto"
    created_at: Timestamp
    updated_at: Timestamp


class PolicyUpdate(RequestModel):
    autonomy_mode: AutonomyModeLiteral | None = None
    human_approval_required_for: list[ApprovalKindLiteral] | None = None
    daily_budget_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)
    monthly_budget_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)
    max_concurrent_runs: int | None = Field(default=None, ge=1, le=100)
    max_delegation_depth: int | None = Field(default=None, ge=0, le=20)
    max_tasks_per_planning_cycle: int | None = Field(default=None, ge=1, le=1000)
    max_attempts_per_task: int | None = Field(default=None, ge=1, le=20)
    max_revisions: int | None = Field(default=None, ge=0, le=20)
    max_run_seconds: int | None = Field(default=None, ge=1, le=86_400)
    max_headcount: int | None = Field(default=None, ge=1, le=1000)
    heartbeat_interval_seconds: int | None = Field(default=None, ge=60, le=86_400)
    default_run_reservation_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)
    allowed_model_providers: list[str] | None = None
    auto_activate_hires: bool | None = None
    hire_default_monthly_budget_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)
    default_model_provider_id: str | None = Field(default=None, max_length=36)
    default_model_name: str | None = Field(default=None, min_length=1, max_length=160)
    sandbox_backend: SandboxBackendLiteral | None = None

    @field_validator(
        "autonomy_mode",
        "human_approval_required_for",
        "max_concurrent_runs",
        "max_delegation_depth",
        "max_tasks_per_planning_cycle",
        "max_attempts_per_task",
        "max_revisions",
        "max_run_seconds",
        "max_headcount",
        "heartbeat_interval_seconds",
        "default_run_reservation_micros",
        "allowed_model_providers",
        "auto_activate_hires",
        "sandbox_backend",
    )
    @classmethod
    def _not_null(cls, v):  # only the two budget caps are nullable (null = no limit)
        if v is None:
            raise ValueError("must not be null")
        return v

    @field_validator("human_approval_required_for")
    @classmethod
    def _dedupe_kinds(cls, v):
        return list(dict.fromkeys(v))

    @field_validator("allowed_model_providers")
    @classmethod
    def _providers(cls, v):
        cleaned = [p.strip() for p in v]
        if any(not p or len(p) > 80 for p in cleaned) or len(cleaned) > 50:
            raise ValueError("providers must be 1-80 character strings (max 50)")
        return list(dict.fromkeys(cleaned))
