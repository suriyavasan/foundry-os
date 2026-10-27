"""Milestone 5 schemas. The names (``FounderNote``, ``NoteDetail``, ``NoteReply``, ``NoteCreate``,
``NoteReplyCreate``, ``NoteUpdate``, ``Alert``, ``MemoryEntry``, ``MemoryCreate``,
``CofounderSummary``) are the contract the web client's ``contract.check.ts`` looks for."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from app.schemas.common import ApiModel, RequestModel, Timestamp
from app.schemas.governance import Decision
from app.schemas.reflections import AgentReflection
from app.schemas.work import (
    ActorTypeLiteral,
    GoalStatusLiteral,
    PriorityLiteral,
    ProjectStatusLiteral,
    RunStatusLiteral,
    TaskStatusLiteral,
)

NoteStatusLiteral = Literal["new", "triaged", "archived"]
NoteCategoryLiteral = Literal[
    "directive", "goal", "requirement", "idea", "feedback", "question", "context", "noise"
]
NoteAuthorLiteral = Literal["human", "agent"]
# Record types a triage action may point at. ``hire`` is a hiring task or the hired agent.
NoteActionTypeLiteral = Literal[
    "goal", "project", "task", "schedule", "agent", "hire", "decision", "memory", "approval"
]
AlertSeverityLiteral = Literal["info", "warning", "critical"]
AlertStatusLiteral = Literal["open", "resolved"]
MemoryKindLiteral = Literal["fact", "decision", "requirement", "lesson", "research"]
MemoryStatusLiteral = Literal["proposed", "verified", "rejected", "superseded"]
MAX_NOTE_CHARS = 10_240


def _clean_tags(value: list[str], *, limit: int = 12, max_len: int = 60) -> list[str]:
    cleaned = [v.strip() for v in value if v and v.strip()]
    if len(cleaned) > limit or any(len(v) > max_len for v in cleaned):
        raise ValueError(f"at most {limit} entries of up to {max_len} characters")
    return list(dict.fromkeys(cleaned))


# ------------------------------------------------------------------ notes


class NoteAction(ApiModel):
    type: str
    ref_id: str
    label: str | None = None


class NoteTriage(ApiModel):
    category: NoteCategoryLiteral
    summary: str
    actions: list[NoteAction]
    confidence: float | None


class NoteReply(ApiModel):
    id: str
    note_id: str
    author_type: NoteAuthorLiteral
    author_id: str | None
    body: str
    created_at: Timestamp


class FounderNote(ApiModel):
    id: str
    company_id: str
    author_user_id: str | None
    body: str
    attachments: list[str]
    status: NoteStatusLiteral
    triage: NoteTriage | None
    triaged_by_run_id: str | None
    pinned: bool
    created_at: Timestamp
    updated_at: Timestamp
    last_reply: NoteReply | None = None
    reply_count: int = 0


class NoteDetail(FounderNote):
    replies: list[NoteReply]


class NoteCreate(RequestModel):
    body: str = Field(min_length=1, max_length=MAX_NOTE_CHARS)
    attachments: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("attachments")
    @classmethod
    def _dedupe(cls, v):
        return list(dict.fromkeys(v))


class NoteReplyCreate(RequestModel):
    body: str = Field(min_length=1, max_length=MAX_NOTE_CHARS)


class NoteUpdate(RequestModel):
    pinned: bool | None = None
    status: Literal["archived", "new", "triaged"] | None = None

    @field_validator("pinned", "status")
    @classmethod
    def _not_null(cls, v):
        if v is None:
            raise ValueError("must not be null")
        return v


# --- agent API (AI Co-founder)


class AgentNoteReply(ApiModel):
    author_type: NoteAuthorLiteral
    body: str
    created_at: Timestamp


class AgentNote(ApiModel):
    """A note as the AI Co-founder reads it (secret-looking text is redacted)."""

    id: str
    status: NoteStatusLiteral
    body: str
    created_at: Timestamp
    pinned: bool
    previous_triage: NoteTriage | None = None
    replies: list[AgentNoteReply] = Field(default_factory=list)


class NoteActionIn(RequestModel):
    type: NoteActionTypeLiteral
    ref_id: str = Field(min_length=1, max_length=64)


class NoteTriageIn(RequestModel):
    category: NoteCategoryLiteral
    summary: str = Field(min_length=1, max_length=1000)
    actions: list[NoteActionIn] = Field(default_factory=list, max_length=20)
    confidence: float | None = Field(default=None, ge=0, le=1)
    # Convenience: post the reply to the Founder in the same call.
    reply: str | None = Field(default=None, min_length=1, max_length=MAX_NOTE_CHARS)


# ------------------------------------------------------------------ alerts


class Alert(ApiModel):
    id: str
    company_id: str
    severity: AlertSeverityLiteral
    kind: str
    title: str
    body: str | None
    status: AlertStatusLiteral
    related_type: str | None
    related_id: str | None
    created_at: Timestamp
    resolved_at: Timestamp | None = None
    resolved_by_type: ActorTypeLiteral | None = None
    resolved_by_id: str | None = None


# ------------------------------------------------------------------ memory


class MemorySource(ApiModel):
    type: str
    id: str | None
    task_id: str | None = None
    label: str | None = None


class MemoryEntry(ApiModel):
    id: str
    company_id: str
    kind: MemoryKindLiteral
    title: str | None
    body: str
    status: MemoryStatusLiteral
    source: MemorySource | None
    tags: list[str]
    applies_to: list[str] = Field(default_factory=list)
    proposed_by_type: ActorTypeLiteral | None
    proposed_by_id: str | None
    verified_by_type: ActorTypeLiteral | None
    verified_by_id: str | None
    verified_at: Timestamp | None
    rejection_reason: str | None = None
    superseded_by_id: str | None = None
    created_at: Timestamp
    updated_at: Timestamp


class MemoryCreate(RequestModel):
    kind: MemoryKindLiteral
    title: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=1, max_length=10_000)
    tags: list[str] = Field(default_factory=list)
    # Lessons only: role families / role templates / agent ids the entry applies to ([] = all).
    applies_to: list[str] = Field(default_factory=list)

    @field_validator("tags", "applies_to")
    @classmethod
    def _tags(cls, v):
        return _clean_tags(v)

    @field_validator("title")
    @classmethod
    def _title(cls, v):
        return v or None


class MemoryDecisionBody(RequestModel):
    reason: str | None = Field(default=None, max_length=2000)


# ------------------------------------------------------------------ AI Co-founder


class CofounderPlanTask(ApiModel):
    id: str
    title: str
    status: TaskStatusLiteral
    assignee_agent_id: str | None


class CofounderPlanProject(ApiModel):
    id: str
    goal_id: str | None
    name: str
    status: ProjectStatusLiteral
    lead_agent_id: str | None
    tasks_done: int
    tasks_total: int
    open_tasks: list[CofounderPlanTask]


class CofounderGoal(ApiModel):
    id: str
    title: str
    status: GoalStatusLiteral
    priority: PriorityLiteral
    description: str | None = None


class CofounderBlocker(ApiModel):
    task_id: str
    title: str
    reason: str | None
    agent_id: str | None
    since: Timestamp | None


class CofounderLastRun(ApiModel):
    run_id: str
    task_id: str
    status: RunStatusLiteral
    started_at: Timestamp | None
    finished_at: Timestamp | None
    summary: str | None = None


class CofounderPlan(ApiModel):
    summary: str | None
    updated_at: Timestamp | None
    projects: list[CofounderPlanProject]


class CofounderSummary(ApiModel):
    agent_id: str | None
    goals: list[CofounderGoal]
    plan: CofounderPlan
    decisions: list[Decision]
    blockers: list[CofounderBlocker]
    alerts: list[Alert]
    last_run: CofounderLastRun | None
    wake_pending: bool
    unprocessed_notes: int


class CofounderWakeResult(ApiModel):
    status: str
    task_id: str | None


# ------------------------------------------------------------------ agent API: escalation, help


class EscalationCreate(RequestModel):
    reason: str = Field(min_length=1, max_length=2000)
    task_id: str | None = None
    # Also block the task (the escalating agent's own current task) until the AI Co-founder acts.
    block_task: bool = False


class EscalationResult(ApiModel):
    escalated_to_agent_id: str
    escalated_to_name: str
    wake_status: str


class HelpRequestCreate(RequestModel):
    message: str = Field(min_length=1, max_length=4000)
    task_id: str | None = None


class HelpRequestResult(ApiModel):
    task_id: str
    assigned_to_agent_id: str
    assigned_to_name: str
    created: bool


class AgentTaskAction(RequestModel):
    """Manager actions on a task they created/manage: retry (failed) or cancel."""

    reason: str | None = Field(default=None, max_length=1000)
    assignee_agent_id: str | None = None


# ------------------------------------------------------------------ agent API: state


class StateGoal(ApiModel):
    id: str
    title: str
    status: str
    priority: str
    description: str | None = None
    owner_agent_id: str | None = None
    # M12: how many projects of this goal are open / ended, so "no open project" is visible.
    open_projects: int = 0
    ended_projects: int = 0


class StateProject(ApiModel):
    id: str
    name: str
    status: str
    goal_id: str | None
    lead_agent_id: str | None
    lead_name: str | None = None
    budget_micros: int | None = None
    description: str | None = None
    tasks_total: int = 0
    tasks_done: int = 0
    tasks_open: int = 0


class StateTask(ApiModel):
    id: str
    title: str
    status: str
    kind: str
    priority: str
    assignee_agent_id: str | None
    assignee_name: str | None = None
    project_id: str | None = None
    parent_task_id: str | None = None
    blocked_reason: str | None = None
    error: str | None = None
    result_summary: str | None = None


class StateAgent(ApiModel):
    id: str
    name: str
    role_title: str
    role_family: str
    kind: str
    status: str
    manager_agent_id: str | None = None
    open_tasks: int = 0
    skills: list[str] = Field(default_factory=list)


class StateSchedule(ApiModel):
    id: str
    name: str
    agent_id: str
    enabled: bool
    interval_seconds: int | None
    cron: str | None
    next_run_at: Timestamp | None


class StateApproval(ApiModel):
    id: str
    kind: str
    title: str
    approver: str
    requested_by_agent_id: str | None


class StateBudget(ApiModel):
    month_budget_micros: int | None
    month_spent_micros: int
    month_held_micros: int
    day_budget_micros: int | None
    day_spent_micros: int
    # spent / budget in percent (None without a budget). Spend is measured, never guessed.
    month_utilisation_pct: float | None
    max_agent_monthly_budget_micros: int
    cost_note: str


class StateMemory(ApiModel):
    id: str
    kind: str
    title: str | None
    body: str


class StateNotes(ApiModel):
    unprocessed: int
    items: list[AgentNote] = Field(default_factory=list)


class StateStandard(ApiModel):
    id: str
    title: str
    statement: str


class AgentState(ApiModel):
    role: str
    # M12: "compact" for a local model or a context under 32K (fewer rows, shorter texts).
    profile: str = "full"
    company: dict[str, Any]
    goals: list[StateGoal]
    projects: list[StateProject]
    tasks: dict[str, Any]
    failures_24h: list[StateTask]
    approvals_pending: list[StateApproval]
    budget: StateBudget | None
    headcount: dict[str, Any]
    agents: list[StateAgent]
    hiring_requests: list[StateTask]
    decisions: list[dict[str, Any]]
    memory: list[StateMemory]
    memory_proposals: list[StateMemory] = Field(default_factory=list)
    alerts: list[dict[str, Any]]
    notes: StateNotes | None
    schedules: list[StateSchedule]
    limits: dict[str, Any]
    # M10: company standards in force; for the AI Co-founder also the reflections the Founder
    # reacted to and it has not answered yet.
    standards: list[StateStandard] = Field(default_factory=list)
    reflections_to_answer: list[AgentReflection] = Field(default_factory=list)
