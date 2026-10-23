from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.schemas.agent import Agent
from app.schemas.artifact import Artifact
from app.schemas.audit import AuditEvent
from app.schemas.common import Timestamp
from app.schemas.company import Company


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "error"]
    version: str
    auth_mode: str


class MeUser(BaseModel):
    id: str
    email: str
    display_name: str


class MeMembership(BaseModel):
    company_id: str
    company_name: str
    role: Literal["owner", "admin", "viewer"]


class Me(BaseModel):
    user: MeUser
    memberships: list[MeMembership]


class AgentStatusCounts(BaseModel):
    proposed: int = 0
    active: int = 0
    paused: int = 0
    retiring: int = 0
    retired: int = 0
    rejected: int = 0


class OverviewAgents(BaseModel):
    total: int
    by_status: AgentStatusCounts


class OverviewDepartments(BaseModel):
    total: int


class TasksOverview(BaseModel):
    available: Literal[True] = True
    total: int
    open: int
    by_status: dict[str, int]
    waiting_for_runtime: int  # ready tasks whose assignee's runtime cannot execute yet
    blocked_by_budget: int


class CostSplit(BaseModel):
    verified_micros: int
    estimated_micros: int
    unknown_micros: int


class SpendPeriod(BaseModel):
    spent_micros: int  # from usage records (all cost kinds)
    held_micros: int  # reserved by queued/running runs, not yet spent
    by_cost_kind: CostSplit
    budget_micros: int | None  # company limit for the period; null = unlimited


class SpendingOverview(BaseModel):
    available: Literal[True] = True
    timezone: str
    today: SpendPeriod
    month: SpendPeriod


class ApprovalsOverview(BaseModel):
    available: Literal[True] = True
    pending_total: int
    pending_by_approver: dict[str, int]


class ArtifactsOverview(BaseModel):
    available: Literal[True] = True
    total: int
    latest: list[Artifact]


class UpcomingSchedule(BaseModel):
    id: str
    name: str
    kind: str
    agent_id: str
    agent_name: str
    next_run_at: Timestamp | None


class RoutinesOverview(BaseModel):
    available: Literal[True] = True
    total: int
    enabled: int
    upcoming: list[UpcomingSchedule]


class Overview(BaseModel):
    company: Company
    founder: Agent | None
    agents: OverviewAgents
    departments: OverviewDepartments
    tasks: TasksOverview
    spending: SpendingOverview
    approvals: ApprovalsOverview
    artifacts: ArtifactsOverview
    routines: RoutinesOverview
    recent_activity: list[AuditEvent]
