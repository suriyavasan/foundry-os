from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import RequestModel, Timestamp


class AgentTaskPatch(RequestModel):
    """The only task edits an agent may make: block/unblock and (re)assignment."""

    status: Literal["blocked", "unblocked"] | None = None
    reason: str | None = Field(default=None, max_length=1000)
    assignee_agent_id: str | None = None


class AgentTaskComplete(RequestModel):
    result_summary: str = Field(min_length=1, max_length=20_000)
    output_refs: list[str] = Field(default_factory=list, max_length=100)


class ProgressBody(RequestModel):
    message: str = Field(min_length=1, max_length=2000)


class ProgressOut(BaseModel):
    run_id: str
    seq: int


# ---- GET /context -------------------------------------------------------------


class CtxCompany(BaseModel):
    id: str
    name: str
    mission: str | None
    strategic_goal: str | None
    constraints: list[str]
    timezone: str


class CtxTaskBrief(BaseModel):
    id: str
    title: str
    status: str
    result_summary: str | None = None
    output_refs: list[str] = []


class CtxArtifactBrief(BaseModel):
    id: str
    title: str
    kind: str
    status: str
    current_version: int


class CtxTask(BaseModel):
    id: str
    title: str
    description: str | None
    acceptance_criteria: str | None
    kind: str
    priority: str
    status: str
    attempt_count: int
    revision_count: int
    delegation_depth: int
    budget_micros: int | None
    required_skills: list[str]
    input_refs: list[str]
    review_note: str | None
    due_at: Timestamp | None
    project_id: str | None
    parent: CtxTaskBrief | None
    dependencies: list[CtxTaskBrief]
    artifacts: list[CtxArtifactBrief]


class CtxAgent(BaseModel):
    id: str
    name: str
    role_title: str
    kind: str
    status: str | None = None
    responsibilities: str | None = None
    instructions: str | None = None
    tool_permissions: list[str] = []
    model_provider: str | None = None
    model_name: str | None = None
    role_family: str | None = None
    skills: list[str] = []  # slugs of assigned ACTIVE skills
    skill_tags: list[str] = []


class CtxPolicy(BaseModel):
    autonomy_mode: str
    human_approval_required_for: list[str]
    max_delegation_depth: int
    max_tasks_per_planning_cycle: int
    tasks_created_this_run: int
    max_attempts_per_task: int
    max_revisions: int
    max_run_seconds: int
    max_headcount: int
    own_daily_budget_micros: int | None
    own_monthly_budget_micros: int | None


class CtxRun(BaseModel):
    id: str
    attempt_number: int


class AgentContext(BaseModel):
    company: CtxCompany
    run: CtxRun
    task: CtxTask
    agent: CtxAgent
    manager: CtxAgent | None
    direct_reports: list[CtxAgent]
    colleagues: list[CtxAgent]
    policy: CtxPolicy
    # M4: agents (self, manager, reports, colleagues) carry their skill slugs and tags.
    skills_available: bool = True
