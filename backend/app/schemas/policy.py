from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from app.schemas.agent import MAX_BUDGET_MICROS, RoleFamilyLiteral
from app.schemas.common import Timestamp
from app.schemas.purchasing import PurchasePolicy, PurchasePolicyUpdate

AutonomyModeLiteral = Literal["autonomous", "supervised"]
SandboxBackendLiteral = Literal["auto", "none", "docker"]
ModelRoutingLiteral = Literal["free_first", "pinned", "quality_first"]
NewProjectRepositoryLiteral = Literal["local", "company_repo"]
ApprovalKindLiteral = Literal[
    "external_purchase",
    "budget_increase",
    "external_communication",
    "production_deploy",
    "sensitive_action",
    "hire",
    "other",
]


class Policy(PurchasePolicy):
    # M15: the purchasing fields come from PurchasePolicy (human-only, with code ceilings).
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
    # M5 autonomy limits (only humans can change them).
    max_agent_monthly_budget_micros: int = 25_000_000
    max_planning_runs_per_hour: int = 6
    max_open_tasks: int = 200
    circuit_breaker_failures: int = 8
    # M3b: the company default model (ModelProvider id + model name) and the sandbox for code tools.
    default_model_provider_id: str | None = None
    default_model_name: str | None = None
    sandbox_backend: SandboxBackendLiteral = "auto"
    # M6 governance (only humans can change them).
    budget_alert_thresholds: list[int] = [50, 80, 100]
    model_routing: ModelRoutingLiteral = "free_first"
    allow_paid_fallback: bool = True
    auto_spend_limit_micros: int = 0
    stall_seconds: int = 180
    max_tool_calls_per_run: int = 300
    max_artifacts_per_run: int = 50
    max_tokens_per_run: int = 3_000_000
    ai_disclosure_enabled: bool = True
    ai_disclosure_text: str | None = None
    # M10 (read-only here; set through PUT /ai-setup): api or hybrid, and the CLI for work tasks.
    work_mode: str = "api"
    work_runtime: str | None = None
    # M12 (only humans can change them).
    work_model_provider_id: str | None = None
    work_model_name: str | None = None
    cli_role_families: list[RoleFamilyLiteral] | None = None  # None = every role family
    cli_run_reservation_micros: int = 1_000_000
    management_reserved_slots: int = 1
    max_parallel_runs_per_agent: int = 1
    max_work_tasks_per_project: int = 15
    max_subtasks_per_task: int = 5
    circuit_breaker_cooldown_seconds: int = 1800  # 0 = only a human resumes
    initiative_enabled: bool = True
    initiative_interval_seconds: int = 21_600
    project_auto_complete: bool = True
    # M14 (only humans can change it).
    new_project_repository: NewProjectRepositoryLiteral = "local"
    created_at: Timestamp
    updated_at: Timestamp


class PolicyUpdate(PurchasePolicyUpdate):
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
    max_agent_monthly_budget_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)
    max_planning_runs_per_hour: int | None = Field(default=None, ge=1, le=1000)
    max_open_tasks: int | None = Field(default=None, ge=1, le=100_000)
    circuit_breaker_failures: int | None = Field(default=None, ge=0, le=1000)
    default_model_provider_id: str | None = Field(default=None, max_length=36)
    default_model_name: str | None = Field(default=None, min_length=1, max_length=160)
    sandbox_backend: SandboxBackendLiteral | None = None
    budget_alert_thresholds: list[int] | None = Field(default=None, max_length=10)
    model_routing: ModelRoutingLiteral | None = None
    allow_paid_fallback: bool | None = None
    auto_spend_limit_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)
    stall_seconds: int | None = Field(default=None, ge=30, le=3600)
    max_tool_calls_per_run: int | None = Field(default=None, ge=1, le=100_000)
    max_artifacts_per_run: int | None = Field(default=None, ge=1, le=10_000)
    max_tokens_per_run: int | None = Field(default=None, ge=1000, le=10**10)
    ai_disclosure_enabled: bool | None = None
    ai_disclosure_text: str | None = Field(default=None, max_length=500)
    # M12. ``work_model_*`` and ``cli_role_families`` accept null (clear / every role family).
    work_model_provider_id: str | None = Field(default=None, max_length=36)
    work_model_name: str | None = Field(default=None, min_length=1, max_length=160)
    cli_role_families: list[RoleFamilyLiteral] | None = Field(default=None, max_length=20)
    cli_run_reservation_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)
    management_reserved_slots: int | None = Field(default=None, ge=0, le=99)
    max_parallel_runs_per_agent: int | None = Field(default=None, ge=1, le=10)
    max_work_tasks_per_project: int | None = Field(default=None, ge=1, le=200)
    max_subtasks_per_task: int | None = Field(default=None, ge=1, le=50)
    circuit_breaker_cooldown_seconds: int | None = Field(default=None, ge=0, le=604_800)
    initiative_enabled: bool | None = None
    initiative_interval_seconds: int | None = Field(default=None, ge=600, le=2_592_000)
    project_auto_complete: bool | None = None
    # M14.
    new_project_repository: NewProjectRepositoryLiteral | None = None

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
        "max_agent_monthly_budget_micros",
        "max_planning_runs_per_hour",
        "max_open_tasks",
        "circuit_breaker_failures",
        "budget_alert_thresholds",
        "model_routing",
        "allow_paid_fallback",
        "auto_spend_limit_micros",
        "stall_seconds",
        "max_tool_calls_per_run",
        "max_artifacts_per_run",
        "max_tokens_per_run",
        "ai_disclosure_enabled",
        "cli_run_reservation_micros",
        "management_reserved_slots",
        "max_parallel_runs_per_agent",
        "max_work_tasks_per_project",
        "max_subtasks_per_task",
        "circuit_breaker_cooldown_seconds",
        "initiative_enabled",
        "initiative_interval_seconds",
        "project_auto_complete",
        "new_project_repository",
        # M15 purchasing (``purchase_monthly_budget_micros`` and ``purchase_checkout_member_id``
        # are the nullable ones: no cap / the owner)
        "purchasing_enabled",
        "purchase_auto_approve_micros",
        "purchase_agent_auto_monthly_micros",
        "purchase_cofounder_limit_micros",
        "purchase_cofounder_monthly_micros",
        "purchase_recurring_requires_human",
        "purchase_new_vendor_requires_human",
        "purchase_tolerance_bps",
        "purchase_tolerance_min_micros",
        "purchase_card_ttl_hours",
        "subscription_renewal_alert_days",
    )
    @classmethod
    def _not_null(cls, v):  # only the two budget caps are nullable (null = no limit)
        if v is None:
            raise ValueError("must not be null")
        return v

    @field_validator("budget_alert_thresholds")
    @classmethod
    def _thresholds(cls, v):
        if any(isinstance(p, bool) or p < 1 or p > 1000 for p in v):
            raise ValueError("thresholds are percentages between 1 and 1000")
        return sorted(set(v))

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

    @field_validator("cli_role_families")
    @classmethod
    def _families(cls, v):
        return None if v is None else list(dict.fromkeys(v))
