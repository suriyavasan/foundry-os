from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.principal import CompanyContext
from app.models.agent import Agent
from app.models.department import Department
from app.models.enums import LIVE_AGENT_STATUSES, AgentKind, AgentStatus
from app.schemas.agent import Agent as AgentOut
from app.schemas.audit import AuditEvent as AuditEventOut
from app.schemas.common import AvailabilityBlock
from app.schemas.company import Company as CompanyOut
from app.schemas.system import (
    AgentStatusCounts,
    Overview,
    OverviewAgents,
    OverviewDepartments,
)
from app.services import activity, companies


def build_overview(db: Session, ctx: CompanyContext) -> Overview:
    company = companies.get_company(db, ctx)

    counts = dict.fromkeys((s.value for s in AgentStatus), 0)
    rows = db.execute(
        select(Agent.status, func.count(Agent.id))
        .where(Agent.company_id == ctx.company_id)
        .group_by(Agent.status)
    ).all()
    for status, count in rows:
        counts[status] = count

    founder = db.scalar(
        select(Agent)
        .where(
            Agent.company_id == ctx.company_id,
            Agent.kind == AgentKind.FOUNDER,
            Agent.status.in_(LIVE_AGENT_STATUSES),
        )
        .order_by(Agent.created_at)
        .limit(1)
    )
    dept_total = db.scalar(
        select(func.count(Department.id)).where(Department.company_id == ctx.company_id)
    )
    recent, _ = activity.list_activity(db, ctx, limit=10, cursor=None)

    return Overview(
        company=CompanyOut.model_validate(company),
        founder=AgentOut.model_validate(founder) if founder else None,
        agents=OverviewAgents(total=sum(counts.values()), by_status=AgentStatusCounts(**counts)),
        departments=OverviewDepartments(total=dept_total or 0),
        tasks=AvailabilityBlock(milestone="M3"),
        spending=AvailabilityBlock(milestone="M6"),
        approvals=AvailabilityBlock(milestone="M6"),
        artifacts=AvailabilityBlock(milestone="M7"),
        routines=AvailabilityBlock(milestone="M5"),
        recent_activity=[AuditEventOut.model_validate(e) for e in recent],
    )
