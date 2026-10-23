from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from app.schemas.agent import Agent, AgentKindLiteral
from app.schemas.common import ApiModel, RequestModel

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


class RoleTemplate(ApiModel):
    slug: str
    family: RoleFamilyLiteral
    title: str
    description: str
    default_kind: AgentKindLiteral
    default_department: str | None
    instructions: str
    default_toolsets: list[str]
    default_skill_slugs: list[str]
    required_skill_tags: list[str]
    builtin: bool
    id: str | None = None  # company-custom templates only
    created_by_type: str | None = None
    created_by_id: str | None = None


class RoleTemplateCreate(RequestModel):
    slug: str = Field(min_length=1, max_length=64)
    family: RoleFamilyLiteral = "generic"
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    default_kind: AgentKindLiteral = "specialist"
    default_department: str | None = Field(default=None, max_length=80)
    instructions: str = Field(default="", max_length=20_000)
    default_toolsets: list[str] = Field(
        default_factory=lambda: ["company", "file", "web", "skills"]
    )
    default_skill_slugs: list[str] = Field(default_factory=list, max_length=40)
    required_skill_tags: list[str] = Field(default_factory=list, max_length=30)


def _clean_list(v: list[str]) -> list[str]:
    return list(dict.fromkeys(s.strip() for s in v if s and s.strip()))


class HireCreate(RequestModel):
    role_template: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    role_title: str | None = Field(default=None, min_length=1, max_length=120)
    manager_agent_id: str
    department: str | None = Field(default=None, min_length=1, max_length=80)
    skill_slugs: list[str] = Field(default_factory=list, max_length=40)
    model_provider: str | None = Field(default=None, max_length=80)
    model_name: str | None = Field(default=None, max_length=160)
    monthly_budget_micros: int | None = Field(default=None, ge=0, le=10**15)
    reason: str = Field(min_length=1, max_length=5000)
    task_id: str

    _slugs = field_validator("skill_slugs")(_clean_list)


class HireResult(ApiModel):
    agent: Agent
    auto_activated: bool
    approval_id: str | None
    approver: Literal["human", "founder"] | None
    reasons_not_auto_activated: list[str]
    skills: list[str]
    skipped_default_skills: list[str]


class HiringRequestCreate(RequestModel):
    required_skills: list[str] = Field(min_length=1, max_length=20)
    role_hint: str | None = Field(default=None, max_length=200)
    reason: str = Field(min_length=1, max_length=5000)
    task_id: str

    _skills = field_validator("required_skills")(_clean_list)

    @field_validator("required_skills")
    @classmethod
    def _nonempty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("required_skills must not be empty")
        return [s.lower() for s in v]


class HiringRequestResult(ApiModel):
    hiring_task_id: str
    created: bool
    status: str


class Colleague(ApiModel):
    id: str
    name: str
    role_title: str
    role_family: RoleFamilyLiteral
    role_template: str | None
    kind: AgentKindLiteral
    status: str
    department_id: str | None
    skills: list[str]  # slugs of assigned active skills
    skill_tags: list[str]
    open_tasks: int  # load
