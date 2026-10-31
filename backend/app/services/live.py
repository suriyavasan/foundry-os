"""Activity-now: what each agent is doing, derived from runs and tasks (never stored)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.principal import CompanyContext
from app.models.agent import Agent
from app.models.company import Company
from app.models.enums import (
    ACTIVE_RUN_STATUSES,
    AgentStatus,
    CompanyStatus,
    RunStatus,
    TaskStatus,
)
from app.models.run import TaskRun
from app.models.work import Task
from app.schemas.agent import Agent as AgentOut
from app.schemas.agent import AgentLive, LiveRun, LiveTask
from app.services import agents as agent_service

_OFFLINE = (
    AgentStatus.PROPOSED,
    AgentStatus.RETIRING,
    AgentStatus.RETIRED,
    AgentStatus.REJECTED,
)


def compute_live(db: Session, company_id: str, agents: list[Agent]) -> dict[str, AgentLive]:
    if not agents:
        return {}
    ids = [a.id for a in agents]
    company_status = db.scalar(select(Company.status).where(Company.id == company_id))

    runs: dict[str, tuple[TaskRun, str]] = {}
    rows = db.execute(
        select(TaskRun, Task.title)
        .join(Task, Task.id == TaskRun.task_id)
        .where(
            TaskRun.company_id == company_id,
            TaskRun.agent_id.in_(ids),
            TaskRun.status.in_(ACTIVE_RUN_STATUSES),
        )
        .order_by(TaskRun.queued_at)
    ).all()
    for run, title in rows:
        runs[run.agent_id] = (run, title)  # latest queued wins

    blocked: dict[str, int] = defaultdict(int)
    for agent_id, count in db.execute(
        select(Task.assignee_agent_id, func.count(Task.id))
        .where(
            Task.company_id == company_id,
            Task.assignee_agent_id.in_(ids),
            Task.status == TaskStatus.BLOCKED,
        )
        .group_by(Task.assignee_agent_id)
    ):
        blocked[agent_id] = count
    pending: dict[str, int] = defaultdict(int)
    for agent_id, count in db.execute(
        select(Task.assignee_agent_id, func.count(Task.id))
        .where(
            Task.company_id == company_id,
            Task.assignee_agent_id.in_(ids),
            Task.status.in_(
                [TaskStatus.READY, TaskStatus.IN_PROGRESS, TaskStatus.REVISION_REQUESTED]
            ),
        )
        .group_by(Task.assignee_agent_id)
    ):
        pending[agent_id] = count
    last_finished: dict[str, datetime] = dict(
        db.execute(  # type: ignore[arg-type]
            select(TaskRun.agent_id, func.max(TaskRun.finished_at))
            .where(TaskRun.company_id == company_id, TaskRun.agent_id.in_(ids))
            .group_by(TaskRun.agent_id)
        ).all()
    )

    result: dict[str, AgentLive] = {}
    for agent in agents:
        current = runs.get(agent.id)
        task_ref = run_ref = None
        if current is not None:
            run, title = current
            task_ref = LiveTask(id=run.task_id, title=title)
            run_ref = LiveRun(id=run.id, status=run.status, started_at=run.started_at)

        if agent.status in _OFFLINE:
            result[agent.id] = AgentLive(
                state="offline",
                current_task=None,
                current_run=None,
                summary=f"Agent is {agent.status}",
                since=agent.status_changed_at,
            )
        elif agent.status == AgentStatus.PAUSED:
            result[agent.id] = AgentLive(
                state="paused",
                current_task=task_ref,
                current_run=run_ref,
                summary="Agent is paused",
                since=agent.status_changed_at,
            )
        elif company_status == CompanyStatus.PAUSED:
            result[agent.id] = AgentLive(
                state="paused",
                current_task=task_ref,
                current_run=run_ref,
                summary="Company is paused",
                since=None,
            )
        elif current is not None:
            run, title = current
            queued = run.status == RunStatus.QUEUED
            result[agent.id] = AgentLive(
                state="working",
                current_task=task_ref,
                current_run=run_ref,
                summary=run.activity_summary
                or (f"Queued: {title}" if queued else f"Working on: {title}"),
                since=run.started_at or run.queued_at,
            )
        elif blocked[agent.id] and not pending[agent.id]:
            result[agent.id] = AgentLive(
                state="blocked",
                current_task=None,
                current_run=None,
                summary=f"{blocked[agent.id]} task(s) blocked",
                since=None,
            )
        else:
            result[agent.id] = AgentLive(
                state="idle",
                current_task=None,
                current_run=None,
                summary=None,
                since=last_finished.get(agent.id) or agent.status_changed_at,
            )
    from app.services import meetings  # M21: local import (meetings -> tasks -> ...)

    for agent_id, meeting_id in meetings.running_for_agents(db, company_id, ids).items():
        if agent_id in result:
            result[agent_id].meeting_id = meeting_id
    return result


def serialize_agents(db: Session, company_id: str, agents: list[Agent]) -> list[AgentOut]:
    live = compute_live(db, company_id, agents)
    out = []
    for agent in agents:
        model = AgentOut.model_validate(agent)
        model.live = live.get(agent.id)
        out.append(model)
    return out


def get_activity_now(db: Session, ctx: CompanyContext, agent_id: str) -> AgentLive:
    agent = agent_service.get_agent(db, ctx, agent_id)
    return compute_live(db, ctx.company_id, [agent])[agent.id]
