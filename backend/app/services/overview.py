from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.principal import CompanyContext
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.artifact import Artifact
from app.models.company import Company
from app.models.department import Department
from app.models.enums import (
    LIVE_AGENT_STATUSES,
    AgentKind,
    AgentStatus,
    ApprovalStatus,
    CostKind,
    TaskStatus,
)
from app.models.governance import Approval
from app.models.ledger import BudgetReservation, UsageRecord
from app.models.schedule import Schedule
from app.models.work import Task
from app.runtimes.registry import RuntimeRegistry
from app.schemas.artifact import Artifact as ArtifactOut
from app.schemas.audit import AuditEvent as AuditEventOut
from app.schemas.company import Company as CompanyOut
from app.schemas.system import (
    AgentStatusCounts,
    ApprovalsOverview,
    ArtifactsOverview,
    Overview,
    OverviewAgents,
    OverviewDepartments,
    RoutinesOverview,
    SpendingOverview,
    SpendPeriod,
    TasksOverview,
    UpcomingSchedule,
)
from app.services import activity, companies, ledger, live, policy


def _tasks_block(db: Session, ctx: CompanyContext, registry: RuntimeRegistry | None):
    by_status = dict.fromkeys((s.value for s in TaskStatus), 0)
    for status, count in db.execute(
        select(Task.status, func.count(Task.id))
        .where(Task.company_id == ctx.company_id)
        .group_by(Task.status)
    ):
        by_status[status] = count
    open_count = sum(
        n
        for s, n in by_status.items()
        if s not in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)
    )
    # Ready tasks whose assignee's runtime cannot execute them ("waiting for runtime").
    waiting = 0
    for runtime_id, count in db.execute(
        select(Agent.runtime, func.count(Task.id))
        .join(Agent, Agent.id == Task.assignee_agent_id)
        .where(Task.company_id == ctx.company_id, Task.status == TaskStatus.READY)
        .group_by(Agent.runtime)
    ):
        adapter = registry.get(runtime_id) if registry else None
        if adapter is None or not adapter.supports_execution:
            waiting += count
    blocked_by_budget = sum(
        1
        for err in db.scalars(
            select(Task.error).where(
                Task.company_id == ctx.company_id, Task.status == TaskStatus.BLOCKED
            )
        )
        if (err or {}).get("code") == "budget_exceeded"
    )
    return TasksOverview(
        total=sum(by_status.values()),
        open=open_count,
        by_status=by_status,
        waiting_for_runtime=waiting,
        blocked_by_budget=blocked_by_budget,
    )


def _spending_block(db: Session, ctx: CompanyContext, now: datetime) -> SpendingOverview:
    company = db.get(Company, ctx.company_id)
    limits = policy.load_policy(db, ctx.company_id)
    periods = ledger.periods_for(now, company.timezone)  # type: ignore[union-attr]
    tz = ZoneInfo(company.timezone)  # type: ignore[union-attr]
    day_start = datetime.combine(periods.day, time.min, tzinfo=tz)
    month_start = datetime(int(periods.month[:4]), int(periods.month[5:]), 1, tzinfo=tz)

    def period(start: datetime, end: datetime, held_filter, budget: int | None) -> SpendPeriod:
        kinds = dict.fromkeys((k.value for k in CostKind), 0)
        for kind, total in db.execute(
            select(UsageRecord.cost_kind, func.coalesce(func.sum(UsageRecord.cost_micros), 0))
            .where(
                UsageRecord.company_id == ctx.company_id,
                UsageRecord.occurred_at >= start,
                UsageRecord.occurred_at < end,
            )
            .group_by(UsageRecord.cost_kind)
        ):
            kinds[kind] = int(total)
        held = db.scalar(
            select(func.coalesce(func.sum(BudgetReservation.amount_micros), 0)).where(
                BudgetReservation.company_id == ctx.company_id,
                BudgetReservation.status == "held",
                held_filter,
            )
        )
        return SpendPeriod(
            spent_micros=sum(kinds.values()),
            held_micros=int(held or 0),
            by_cost_kind={
                "verified_micros": kinds["verified"],
                "estimated_micros": kinds["estimated"],
                "unknown_micros": kinds["unknown"],
            },
            budget_micros=budget,
        )

    return SpendingOverview(
        timezone=company.timezone,  # type: ignore[union-attr]
        today=period(
            day_start,
            periods.next_day_start,
            BudgetReservation.period_day == periods.day,
            limits.daily_budget_micros,
        ),
        month=period(
            month_start,
            periods.next_month_start,
            BudgetReservation.period_month == periods.month,
            limits.monthly_budget_micros,
        ),
    )


def build_overview(
    db: Session,
    ctx: CompanyContext,
    registry: RuntimeRegistry | None = None,
    *,
    now: datetime | None = None,
) -> Overview:
    now = now or utcnow()
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

    pending: dict[str, int] = {"human": 0, "founder": 0}
    for approver, count in db.execute(
        select(Approval.approver, func.count(Approval.id))
        .where(Approval.company_id == ctx.company_id, Approval.status == ApprovalStatus.PENDING)
        .group_by(Approval.approver)
    ):
        pending[approver] = count

    artifact_total = db.scalar(
        select(func.count(Artifact.id)).where(Artifact.company_id == ctx.company_id)
    )
    latest_artifacts = list(
        db.scalars(
            select(Artifact)
            .where(Artifact.company_id == ctx.company_id)
            .order_by(Artifact.created_at.desc(), Artifact.id.desc())
            .limit(5)
        )
    )

    schedule_rows = db.execute(
        select(Schedule.enabled, func.count(Schedule.id))
        .where(Schedule.company_id == ctx.company_id)
        .group_by(Schedule.enabled)
    ).all()
    upcoming = list(
        db.execute(
            select(Schedule, Agent.name)
            .join(Agent, Agent.id == Schedule.agent_id)
            .where(
                Schedule.company_id == ctx.company_id,
                Schedule.enabled.is_(True),
                Schedule.next_run_at.is_not(None),
            )
            .order_by(Schedule.next_run_at)
            .limit(5)
        ).all()
    )

    founder_out = live.serialize_agents(db, ctx.company_id, [founder])[0] if founder else None
    return Overview(
        company=CompanyOut.model_validate(company),
        founder=founder_out,
        agents=OverviewAgents(total=sum(counts.values()), by_status=AgentStatusCounts(**counts)),
        departments=OverviewDepartments(total=dept_total or 0),
        tasks=_tasks_block(db, ctx, registry),
        spending=_spending_block(db, ctx, now),
        approvals=ApprovalsOverview(
            pending_total=sum(pending.values()),
            pending_by_approver={"human": pending["human"], "founder": pending["founder"]},
        ),
        artifacts=ArtifactsOverview(
            total=artifact_total or 0,
            latest=[ArtifactOut.model_validate(a) for a in latest_artifacts],
        ),
        routines=RoutinesOverview(
            total=sum(n for _, n in schedule_rows),
            enabled=sum(n for enabled, n in schedule_rows if enabled),
            upcoming=[
                UpcomingSchedule(
                    id=s.id,
                    name=s.name,
                    kind=s.kind,
                    agent_id=s.agent_id,
                    agent_name=agent_name,
                    next_run_at=s.next_run_at,
                )
                for s, agent_name in upcoming
            ],
        ),
        recent_activity=[AuditEventOut.model_validate(e) for e in recent],
    )
