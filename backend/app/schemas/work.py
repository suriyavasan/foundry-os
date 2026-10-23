from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator

from app.schemas.agent import MAX_BUDGET_MICROS
from app.schemas.common import ApiModel, RequestModel, Timestamp

PriorityLiteral = Literal["low", "normal", "high", "urgent"]
GoalStatusLiteral = Literal["proposed", "active", "achieved", "abandoned"]
ProjectStatusLiteral = Literal["planned", "active", "paused", "completed", "cancelled"]
TaskKindLiteral = Literal["work", "review", "planning", "hiring", "routine"]
TaskStatusLiteral = Literal[
    "backlog",
    "ready",
    "in_progress",
    "in_review",
    "revision_requested",
    "blocked",
    "done",
    "failed",
    "cancelled",
]
RunStatusLiteral = Literal[
    "queued", "starting", "running", "succeeded", "failed", "cancelled", "timed_out"
]
CostKindLiteral = Literal["verified", "estimated", "unknown"]
ActorTypeLiteral = Literal["human", "agent", "system"]


def _clean_strings(value: list[str], *, max_items: int = 100, max_len: int = 500) -> list[str]:
    cleaned = [v.strip() for v in value]
    if any(not v or len(v) > max_len for v in cleaned) or len(cleaned) > max_items:
        raise ValueError(f"expected at most {max_items} non-empty strings of <= {max_len} chars")
    return list(dict.fromkeys(cleaned))


# ------------------------------------------------------------------ goals


class ErrorInfo(ApiModel):
    """Structured failure details on a task. Always has a code and a message; may carry extra
    context (e.g. ``limit``/``retry_after`` for budget errors)."""

    model_config = ConfigDict(extra="allow")

    code: str
    message: str
    run_id: str | None = None


class RunErrorInfo(ApiModel):
    """Failure details on a single run (code + message, plus optional extra context)."""

    model_config = ConfigDict(extra="allow")

    code: str
    message: str


class Goal(ApiModel):
    id: str
    company_id: str
    title: str
    description: str | None
    status: GoalStatusLiteral
    priority: PriorityLiteral
    owner_agent_id: str | None
    target_date: date | None
    created_by_type: ActorTypeLiteral
    created_by_id: str | None
    created_at: Timestamp
    updated_at: Timestamp


class GoalCreate(RequestModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    status: GoalStatusLiteral = "proposed"
    priority: PriorityLiteral = "normal"
    owner_agent_id: str | None = None
    target_date: date | None = None


class GoalUpdate(RequestModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    status: GoalStatusLiteral | None = None
    priority: PriorityLiteral | None = None
    owner_agent_id: str | None = None
    target_date: date | None = None

    @field_validator("title", "status", "priority")
    @classmethod
    def _not_null(cls, v):
        if v is None:
            raise ValueError("must not be null")
        return v


# ------------------------------------------------------------------ projects


class Project(ApiModel):
    id: str
    company_id: str
    goal_id: str | None
    name: str
    description: str | None
    status: ProjectStatusLiteral
    lead_agent_id: str | None
    budget_micros: int | None
    created_by_type: ActorTypeLiteral
    created_by_id: str | None
    created_at: Timestamp
    updated_at: Timestamp


class ProjectCreate(RequestModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    goal_id: str | None = None
    status: ProjectStatusLiteral = "planned"
    lead_agent_id: str | None = None
    budget_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)


class ProjectUpdate(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    goal_id: str | None = None
    status: ProjectStatusLiteral | None = None
    lead_agent_id: str | None = None
    budget_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)

    @field_validator("name", "status")
    @classmethod
    def _not_null(cls, v):
        if v is None:
            raise ValueError("must not be null")
        return v


# ------------------------------------------------------------------ tasks


class TaskError(ApiModel):
    code: str
    message: str
    run_id: str | None = None


class Task(ApiModel):
    id: str
    company_id: str
    project_id: str | None
    parent_task_id: str | None
    title: str
    description: str | None
    acceptance_criteria: str | None
    kind: TaskKindLiteral
    status: TaskStatusLiteral
    priority: PriorityLiteral
    assignee_agent_id: str | None
    reviewer_agent_id: str | None
    required_skills: list[str]
    budget_micros: int | None
    attempt_count: int
    revision_count: int
    delegation_depth: int
    due_at: Timestamp | None
    started_at: Timestamp | None
    completed_at: Timestamp | None
    result_summary: str | None
    error: ErrorInfo | None
    input_refs: list[str]
    output_refs: list[str]
    review_note: str | None
    created_by_type: ActorTypeLiteral
    created_by_id: str | None
    created_by_run_id: str | None
    idempotency_key: str | None
    depends_on: list[str] = Field(default_factory=list)
    created_at: Timestamp
    updated_at: Timestamp


class TaskCreate(RequestModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=50_000)
    acceptance_criteria: str | None = Field(default=None, max_length=20_000)
    kind: TaskKindLiteral = "work"
    priority: PriorityLiteral = "normal"
    project_id: str | None = None
    parent_task_id: str | None = None
    assignee_agent_id: str | None = None
    reviewer_agent_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    budget_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)
    due_at: datetime | None = None
    input_refs: list[str] = Field(default_factory=list)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("required_skills", "input_refs")
    @classmethod
    def _strings(cls, v):
        return _clean_strings(v)

    @field_validator("depends_on")
    @classmethod
    def _deps(cls, v):
        return list(dict.fromkeys(v))

    @field_validator("due_at")
    @classmethod
    def _aware(cls, v):
        if v is not None and v.tzinfo is None:
            raise ValueError("due_at must include a timezone offset (UTC recommended)")
        return v


class TaskUpdate(RequestModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=50_000)
    acceptance_criteria: str | None = Field(default=None, max_length=20_000)
    kind: TaskKindLiteral | None = None
    priority: PriorityLiteral | None = None
    project_id: str | None = None
    assignee_agent_id: str | None = None
    reviewer_agent_id: str | None = None
    required_skills: list[str] | None = None
    budget_micros: int | None = Field(default=None, ge=0, le=MAX_BUDGET_MICROS)
    due_at: datetime | None = None
    input_refs: list[str] | None = None

    @field_validator("title", "kind", "priority", "required_skills", "input_refs")
    @classmethod
    def _not_null(cls, v):
        if v is None:
            raise ValueError("must not be null")
        return v

    @field_validator("required_skills", "input_refs")
    @classmethod
    def _strings(cls, v):
        return _clean_strings(v)

    @field_validator("due_at")
    @classmethod
    def _aware(cls, v):
        if v is not None and v.tzinfo is None:
            raise ValueError("due_at must include a timezone offset (UTC recommended)")
        return v


class TaskReasonBody(RequestModel):
    reason: str | None = Field(default=None, max_length=1000)


class DependencyCreate(RequestModel):
    depends_on_task_id: str


class ReviewBody(RequestModel):
    decision: Literal["accept", "request_revision"]
    note: str | None = Field(default=None, max_length=10_000)


# ------------------------------------------------------------------ runs


class TaskRun(ApiModel):
    id: str
    company_id: str
    task_id: str
    agent_id: str
    attempt_number: int
    status: RunStatusLiteral
    runtime: str
    runtime_execution_id: str | None
    idempotency_key: str
    queued_at: Timestamp
    started_at: Timestamp | None
    finished_at: Timestamp | None
    heartbeat_at: Timestamp | None
    worker_id: str | None
    cancel_requested: bool
    cancel_reason: str | None
    exit_reason: str | None
    error: RunErrorInfo | None
    input_tokens: int | None
    output_tokens: int | None
    cost_micros: int | None
    cost_kind: CostKindLiteral
    reservation_id: str | None
    activity_summary: str | None
    activity_at: Timestamp | None
    created_at: Timestamp
    updated_at: Timestamp


class RunEvent(ApiModel):
    id: str
    run_id: str
    seq: int
    type: str
    payload: dict[str, Any]
    occurred_at: Timestamp


class RunEventPage(ApiModel):
    """``next_cursor`` is the last ``seq`` returned; pass it back as ``after_seq``."""

    items: list[RunEvent]
    next_cursor: str | None = None


class RunCancelBody(RequestModel):
    reason: str | None = Field(default=None, max_length=500)
