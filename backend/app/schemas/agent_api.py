from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import RequestModel, Timestamp


class AgentTaskPatch(RequestModel):
    """The only task edits an agent may make: block/unblock and (re)assignment."""

    status: Literal["blocked", "unblocked"] | None = None
    reason: str | None = Field(default=None, max_length=1000)
    # M18: blocking requires it -- what would unblock the task (checked in the service).
    unblock_condition: str | None = Field(default=None, max_length=300)
    assignee_agent_id: str | None = None
    assignee_ref: str | None = Field(default=None, max_length=48)  # M7: agent:<id> | member:<id>


class AgentMember(BaseModel):
    """A human colleague as the AI Co-founder sees them: name, title, capacity. Never an email,
    username or credential."""

    ref: str  # member:<id> -- what assignee_ref takes
    name: str
    title: str | None
    role: str
    manager_ref: str | None
    open_tasks: int


class AgentTaskComplete(RequestModel):
    result_summary: str = Field(min_length=1, max_length=20_000)
    output_refs: list[str] = Field(default_factory=list, max_length=100)


class ProposedCompletion(BaseModel):
    """M18 Part B: answer of ``complete`` inside a work-graph run -- the result is only *proposed*;
    the graph verifies it and completes the task."""

    ok: bool = True
    status: str = "proposed"
    task_id: str


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


class CtxArtifactBrief(BaseModel):
    id: str
    title: str
    kind: str
    status: str
    current_version: int
    # M5: a text excerpt (secret-looking spans redacted) of the current version, for the
    # artifacts of dependencies and subtasks so a worker can build on earlier results.
    excerpt: str | None = None


class CtxTaskBrief(BaseModel):
    id: str
    title: str
    status: str
    result_summary: str | None = None
    output_refs: list[str] = []
    assignee_name: str | None = None
    artifacts: list[CtxArtifactBrief] = []


class CtxProject(BaseModel):
    id: str
    name: str
    description: str | None
    status: str
    lead_agent_id: str | None
    lead_name: str | None


class CtxMemory(BaseModel):
    id: str
    kind: str
    title: str | None
    body: str


class CtxStandard(BaseModel):
    """A company standard set by the Founder: it binds every agent's work."""

    id: str
    title: str
    statement: str


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
    blocked_reason: str | None = None
    # M5
    project: CtxProject | None = None
    subtasks: list[CtxTaskBrief] = []
    # Review / failure-analysis tasks: the task under review and its deliverables.
    review_target: dict | None = None


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
    max_open_tasks: int = 0
    role: str = ""
    # M12: caps on agent-created work (per project) and on child tasks (per parent task).
    max_work_tasks_per_project: int = 15
    max_subtasks_per_task: int = 5


class CtxRun(BaseModel):
    id: str
    attempt_number: int


class CtxCredential(BaseModel):
    """A credential this agent may use through ``POST /provider-actions``. Metadata only: the
    value never leaves the control plane."""

    id: str
    name: str
    provider: str
    allowed_actions: list[str]


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
    # M5: verified company memory that applies to this agent (requirements, facts, lessons).
    memory: list[CtxMemory] = []
    # M10: the company standards in force that apply to this agent.
    standards: list[CtxStandard] = []
    # M6: credentials scoped for this agent's role family (ids to name in company_provider_action).
    credentials: list[CtxCredential] = []
