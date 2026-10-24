"""The AI Co-founder's panel: ``GET /cofounder/summary`` (goals, active plan, recent decisions,
blockers, alerts, last run) and ``POST /cofounder/wake``."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.principal import CompanyContext
from app.models.agent import Agent
from app.models.enums import (
    LIVE_AGENT_STATUSES,
    TERMINAL_TASK_STATUSES,
    AgentKind,
    GoalStatus,
    ProjectStatus,
    TaskKind,
    TaskStatus,
)
from app.models.governance import Decision
from app.models.run import TaskRun
from app.models.work import Goal, Project, Task
from app.schemas.autonomy import (
    Alert,
    CofounderBlocker,
    CofounderGoal,
    CofounderLastRun,
    CofounderPlan,
    CofounderPlanProject,
    CofounderPlanTask,
    CofounderSummary,
    CofounderWakeResult,
)
from app.schemas.governance import Decision as DecisionOut
from app.services import alerts, notes, wakes

OPEN = [s.value for s in TaskStatus if s not in TERMINAL_TASK_STATUSES]
ACTIVE_PROJECT = [ProjectStatus.PLANNED, ProjectStatus.ACTIVE, ProjectStatus.PAUSED]


def get_summary(db: Session, ctx: CompanyContext) -> CofounderSummary:
    company_id = ctx.company_id
    founder = db.scalar(
        select(Agent)
        .where(
            Agent.company_id == company_id,
            Agent.kind == AgentKind.FOUNDER,
            Agent.status.in_(LIVE_AGENT_STATUSES),
        )
        .order_by(Agent.created_at)
    )
    goals = list(
        db.scalars(
            select(Goal)
            .where(
                Goal.company_id == company_id,
                Goal.status.in_([GoalStatus.PROPOSED, GoalStatus.ACTIVE]),
            )
            .order_by(Goal.created_at)
            .limit(20)
        )
    )
    projects = list(
        db.scalars(
            select(Project)
            .where(Project.company_id == company_id, Project.status.in_(ACTIVE_PROJECT))
            .order_by(Project.created_at)
            .limit(20)
        )
    )
    plan_projects: list[CofounderPlanProject] = []
    for project in projects:
        counts = dict(
            db.execute(
                select(Task.status, func.count(Task.id))
                .where(
                    Task.company_id == company_id,
                    Task.project_id == project.id,
                    Task.kind == TaskKind.WORK,
                )
                .group_by(Task.status)
            ).all()
        )
        open_tasks = list(
            db.scalars(
                select(Task)
                .where(
                    Task.company_id == company_id,
                    Task.project_id == project.id,
                    Task.kind == TaskKind.WORK,
                    Task.status.in_(OPEN),
                )
                .order_by(Task.created_at.desc(), Task.id)
                .limit(8)
            )
        )
        plan_projects.append(
            CofounderPlanProject(
                id=project.id,
                goal_id=project.goal_id,
                name=project.name,
                status=project.status,  # type: ignore[arg-type]
                lead_agent_id=project.lead_agent_id,
                tasks_done=int(counts.get("done", 0)),
                tasks_total=int(sum(counts.values())),
                open_tasks=[
                    CofounderPlanTask(
                        id=t.id,
                        title=t.title,
                        status=t.status,  # type: ignore[arg-type]
                        assignee_agent_id=t.assignee_agent_id,
                    )
                    for t in open_tasks
                ],
            )
        )
    last_plan = None
    last_run = None
    wake_pending = False
    if founder is not None:
        last_plan = db.scalar(
            select(Task)
            .where(
                Task.company_id == company_id,
                Task.assignee_agent_id == founder.id,
                Task.kind.in_([TaskKind.PLANNING, TaskKind.ROUTINE]),
                Task.status == TaskStatus.DONE,
                Task.result_summary.is_not(None),
            )
            .order_by(Task.completed_at.desc())
            .limit(1)
        )
        run = db.scalar(
            select(TaskRun)
            .where(TaskRun.company_id == company_id, TaskRun.agent_id == founder.id)
            .order_by(TaskRun.queued_at.desc(), TaskRun.id)
            .limit(1)
        )
        if run is not None:
            run_task = db.get(Task, run.task_id)
            last_run = CofounderLastRun(
                run_id=run.id,
                task_id=run.task_id,
                status=run.status,  # type: ignore[arg-type]
                started_at=run.started_at,
                finished_at=run.finished_at,
                summary=(run_task.result_summary if run_task else None) or run.activity_summary,
            )
        wake_pending = wakes.open_wake_task(
            db, company_id, founder.id
        ) is not None or wakes.has_pending(db, company_id, founder.id)
    blocked = list(
        db.scalars(
            select(Task)
            .where(
                Task.company_id == company_id,
                Task.kind.in_([TaskKind.WORK, TaskKind.HIRING]),
                Task.status == TaskStatus.BLOCKED,
            )
            .order_by(Task.updated_at.desc())
            .limit(10)
        )
    )
    decisions = list(
        db.scalars(
            select(Decision)
            .where(Decision.company_id == company_id)
            .order_by(Decision.created_at.desc(), Decision.id)
            .limit(10)
        )
    )
    return CofounderSummary(
        agent_id=founder.id if founder else None,
        goals=[
            CofounderGoal(
                id=g.id,
                title=g.title,
                status=g.status,  # type: ignore[arg-type]
                priority=g.priority,  # type: ignore[arg-type]
                description=g.description,
            )
            for g in goals
        ],
        plan=CofounderPlan(
            summary=last_plan.result_summary if last_plan else None,
            updated_at=last_plan.completed_at if last_plan else None,
            projects=plan_projects,
        ),
        decisions=[DecisionOut.model_validate(d) for d in decisions],
        blockers=[
            CofounderBlocker(
                task_id=t.id,
                title=t.title,
                reason=(t.error or {}).get("message") or t.blocked_reason,
                agent_id=t.assignee_agent_id,
                since=t.updated_at,
            )
            for t in blocked
        ],
        alerts=[Alert.model_validate(a) for a in alerts.open_alerts(db, company_id, 10)],
        last_run=last_run,
        wake_pending=wake_pending,
        unprocessed_notes=notes.unprocessed_count(db, company_id),
    )


def wake(db: Session, ctx: CompanyContext) -> CofounderWakeResult:
    status, task_id = wakes.owner_wake(db, ctx)
    return CofounderWakeResult(status=status, task_id=task_id)
