from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.schemas.agent import Agent
from app.schemas.audit import AuditEvent
from app.schemas.common import AvailabilityBlock
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


class Overview(BaseModel):
    company: Company
    founder: Agent | None
    agents: OverviewAgents
    departments: OverviewDepartments
    tasks: AvailabilityBlock
    spending: AvailabilityBlock
    approvals: AvailabilityBlock
    artifacts: AvailabilityBlock
    routines: AvailabilityBlock
    recent_activity: list[AuditEvent]
