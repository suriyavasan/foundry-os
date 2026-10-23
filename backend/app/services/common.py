"""Small helpers shared by services (all tenancy-scoped)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound
from app.core.principal import Principal
from app.models.agent import Agent
from app.models.enums import AgentStatus
from app.models.work import Goal, Project, Task

_GONE = (AgentStatus.RETIRED, AgentStatus.REJECTED)


def creator_of(principal: Principal | None) -> tuple[str, str | None]:
    if principal is None:
        return "system", None
    return principal.type.value, principal.id


def get_agent_or_404(
    db: Session, company_id: str, agent_id: str, *, label: str = "Agent", allow_gone: bool = False
) -> Agent:
    agent = db.scalar(select(Agent).where(Agent.id == agent_id, Agent.company_id == company_id))
    if agent is None:
        raise NotFound(f"{label} not found")
    if not allow_gone and agent.status in _GONE:
        raise Conflict(f"{label} is {agent.status} and cannot be used")
    return agent


def get_goal_or_404(db: Session, company_id: str, goal_id: str) -> Goal:
    goal = db.scalar(select(Goal).where(Goal.id == goal_id, Goal.company_id == company_id))
    if goal is None:
        raise NotFound("Goal not found")
    return goal


def get_project_or_404(db: Session, company_id: str, project_id: str) -> Project:
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.company_id == company_id)
    )
    if project is None:
        raise NotFound("Project not found")
    return project


def get_task_or_404(db: Session, company_id: str, task_id: str) -> Task:
    task = db.scalar(select(Task).where(Task.id == task_id, Task.company_id == company_id))
    if task is None:
        raise NotFound("Task not found")
    return task
