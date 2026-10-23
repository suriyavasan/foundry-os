"""Read model for ``GET /api/agent/v1/context``: what an agent needs to do its job."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.artifact import Artifact
from app.models.company import Company
from app.models.enums import AgentStatus
from app.models.work import Task, TaskDependency
from app.schemas.agent_api import (
    AgentContext,
    CtxAgent,
    CtxArtifactBrief,
    CtxCompany,
    CtxPolicy,
    CtxRun,
    CtxTask,
    CtxTaskBrief,
)
from app.services import policy
from app.services.agent_auth import AgentRunContext

MAX_COLLEAGUES = 100


def _task_brief(task: Task) -> CtxTaskBrief:
    return CtxTaskBrief(
        id=task.id,
        title=task.title,
        status=task.status,
        result_summary=task.result_summary,
        output_refs=list(task.output_refs),
    )


def _agent_brief(agent: Agent, *, full: bool = False) -> CtxAgent:
    return CtxAgent(
        id=agent.id,
        name=agent.name,
        role_title=agent.role_title,
        kind=agent.kind,
        status=agent.status,
        responsibilities=agent.responsibilities,
        instructions=agent.instructions if full else None,
        tool_permissions=list(agent.tool_permissions) if full else [],
        model_provider=agent.model_provider if full else None,
        model_name=agent.model_name if full else None,
    )


def build_context(db: Session, rc: AgentRunContext) -> AgentContext:
    company_id = rc.ctx.company_id
    company = db.get(Company, company_id)
    assert company is not None
    limits = policy.load_policy(db, company_id)
    task, agent = rc.task, rc.agent

    parent = (
        db.scalar(select(Task).where(Task.id == task.parent_task_id, Task.company_id == company_id))
        if task.parent_task_id
        else None
    )
    deps = list(
        db.scalars(
            select(Task)
            .join(TaskDependency, TaskDependency.depends_on_task_id == Task.id)
            .where(TaskDependency.task_id == task.id, Task.company_id == company_id)
            .order_by(Task.created_at)
        )
    )
    artifacts = list(
        db.scalars(
            select(Artifact)
            .where(Artifact.company_id == company_id, Artifact.task_id == task.id)
            .order_by(Artifact.created_at)
        )
    )
    manager = (
        db.scalar(
            select(Agent).where(Agent.id == agent.manager_agent_id, Agent.company_id == company_id)
        )
        if agent.manager_agent_id
        else None
    )
    reports = list(
        db.scalars(
            select(Agent).where(
                Agent.company_id == company_id,
                Agent.manager_agent_id == agent.id,
                Agent.status.in_([AgentStatus.ACTIVE, AgentStatus.PAUSED, AgentStatus.PROPOSED]),
            )
        )
    )
    colleagues = list(
        db.scalars(
            select(Agent)
            .where(
                Agent.company_id == company_id,
                Agent.status == AgentStatus.ACTIVE,
                Agent.id != agent.id,
            )
            .order_by(Agent.created_at)
            .limit(MAX_COLLEAGUES)
        )
    )
    created = db.scalar(
        select(func.count(Task.id)).where(
            Task.company_id == company_id, Task.created_by_run_id == rc.run.id
        )
    )
    return AgentContext(
        company=CtxCompany(
            id=company.id,
            name=company.name,
            mission=company.mission,
            strategic_goal=company.strategic_goal,
            constraints=list(company.constraints),
            timezone=company.timezone,
        ),
        run=CtxRun(id=rc.run.id, attempt_number=rc.run.attempt_number),
        task=CtxTask(
            id=task.id,
            title=task.title,
            description=task.description,
            acceptance_criteria=task.acceptance_criteria,
            kind=task.kind,
            priority=task.priority,
            status=task.status,
            attempt_count=task.attempt_count,
            revision_count=task.revision_count,
            delegation_depth=task.delegation_depth,
            budget_micros=task.budget_micros,
            required_skills=list(task.required_skills),
            input_refs=list(task.input_refs),
            review_note=task.review_note,
            due_at=task.due_at,
            project_id=task.project_id,
            parent=_task_brief(parent) if parent else None,
            dependencies=[_task_brief(d) for d in deps],
            artifacts=[
                CtxArtifactBrief(
                    id=a.id,
                    title=a.title,
                    kind=a.kind,
                    status=a.status,
                    current_version=a.current_version,
                )
                for a in artifacts
            ],
        ),
        agent=_agent_brief(agent, full=True),
        manager=_agent_brief(manager) if manager else None,
        direct_reports=[_agent_brief(r) for r in reports],
        colleagues=[_agent_brief(c) for c in colleagues],
        policy=CtxPolicy(
            autonomy_mode=limits.autonomy_mode,
            human_approval_required_for=list(limits.human_approval_required_for),
            max_delegation_depth=limits.max_delegation_depth,
            max_tasks_per_planning_cycle=limits.max_tasks_per_planning_cycle,
            tasks_created_this_run=int(created or 0),
            max_attempts_per_task=limits.max_attempts_per_task,
            max_revisions=limits.max_revisions,
            max_run_seconds=limits.max_run_seconds,
            max_headcount=limits.max_headcount,
            own_daily_budget_micros=agent.daily_budget_micros,
            own_monthly_budget_micros=agent.monthly_budget_micros,
        ),
    )
