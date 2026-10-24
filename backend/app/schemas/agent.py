from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from app.schemas.common import ApiModel, RequestModel, Timestamp

AgentKindLiteral = Literal["founder", "lead", "specialist", "reviewer"]
AgentStatusLiteral = Literal["proposed", "active", "paused", "retiring", "retired", "rejected"]

RoleFamilyLiteral = Literal[
    "founder",
    "hr",
    "engineering",
    "design",
    "marketing",
    "sales",
    "customer_success",
    "research",
    "qa",
    "operations",
    "finance",
    "generic",
]

MAX_BUDGET_MICROS = 10**15  # $1B; guards against absurd values / overflow


AgentStateLiteral = Literal["idle", "working", "blocked", "paused", "offline"]


class LiveTask(ApiModel):
    id: str
    title: str


class LiveRun(ApiModel):
    id: str
    status: Literal[
        "queued", "starting", "running", "succeeded", "failed", "cancelled", "timed_out"
    ]
    started_at: Timestamp | None


class AgentLive(ApiModel):
    """What the agent is doing right now (derived from runs/tasks; never stored)."""

    state: AgentStateLiteral
    current_task: LiveTask | None
    current_run: LiveRun | None
    summary: str | None
    since: Timestamp | None


class Agent(ApiModel):
    id: str
    company_id: str
    name: str
    role_title: str
    kind: AgentKindLiteral
    status: AgentStatusLiteral
    department_id: str | None
    manager_agent_id: str | None
    manager_ref: str | None = None  # M7: agent:<id> | member:<id> | null
    reports_to_human: bool
    responsibilities: str | None
    instructions: str | None
    model_provider: str | None
    model_name: str | None
    runtime: str
    runtime_config: dict[str, Any]
    tool_permissions: list[str]
    monthly_budget_micros: int | None
    daily_budget_micros: int | None
    role_template: str | None = None  # M4: role template slug this employee was created from
    role_family: RoleFamilyLiteral = "generic"  # avatar type the canvas draws
    created_at: Timestamp
    updated_at: Timestamp
    status_changed_at: Timestamp
    live: AgentLive | None = None  # activity-now; filled by services.live.serialize_agents


def _clean_tools(value: list[str]) -> list[str]:
    seen: list[str] = []
    for item in value:
        item = item.strip()
        if not item or len(item) > 100:
            raise ValueError("tool permissions must be 1-100 character strings")
        if item not in seen:
            seen.append(item)
    if len(seen) > 200:
        raise ValueError("at most 200 tool permissions")
    return seen


class AgentCreate(RequestModel):
    name: str = Field(min_length=1, max_length=120)
    role_title: str = Field(min_length=1, max_length=120)
    kind: AgentKindLiteral = "specialist"
    department_id: str | None = None
    manager_agent_id: str | None = None
    manager_ref: str | None = Field(default=None, max_length=48)  # M7: may name a human member
    reports_to_human: bool = False
    responsibilities: str | None = Field(default=None, max_length=10_000)
    instructions: str | None = Field(default=None, max_length=50_000)
    model_provider: str | None = Field(default=None, max_length=80)
    model_name: str | None = Field(default=None, max_length=160)
    runtime: str = Field(default="unassigned", min_length=1, max_length=64)
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    tool_permissions: list[str] = Field(default_factory=list)
    monthly_budget_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)
    daily_budget_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)
    # M4: apply a role template's defaults (kind, department, responsibilities, instructions,
    # tools, skills) for every field not given explicitly. Humans only.
    role_template: str | None = Field(default=None, min_length=1, max_length=64)
    skill_slugs: list[str] | None = Field(default=None, max_length=40)

    _tools = field_validator("tool_permissions")(_clean_tools)


class AgentUpdate(RequestModel):
    """status, kind and company_id are deliberately absent (extra fields are rejected)."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    role_title: str | None = Field(default=None, min_length=1, max_length=120)
    department_id: str | None = None
    manager_agent_id: str | None = None
    manager_ref: str | None = Field(default=None, max_length=48)
    responsibilities: str | None = Field(default=None, max_length=10_000)
    instructions: str | None = Field(default=None, max_length=50_000)
    model_provider: str | None = Field(default=None, max_length=80)
    model_name: str | None = Field(default=None, max_length=160)
    runtime: str | None = Field(default=None, min_length=1, max_length=64)
    runtime_config: dict[str, Any] | None = None
    tool_permissions: list[str] | None = None
    monthly_budget_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)
    daily_budget_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)

    @field_validator("name", "role_title", "runtime", "runtime_config", "tool_permissions")
    @classmethod
    def _not_null(cls, v):
        if v is None:
            raise ValueError("must not be null")
        return v

    @field_validator("tool_permissions")
    @classmethod
    def _tools(cls, v):
        return _clean_tools(v)


class LifecycleAction(RequestModel):
    reason: str | None = Field(default=None, max_length=500)
