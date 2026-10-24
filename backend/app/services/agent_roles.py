"""Which operating loop an agent belongs to. Authorization rules in the services key off this,
never off prompts.

* ``founder`` - the AI Co-founder (kind ``founder``): strategic loop, manages every other agent;
* ``hr`` - the HR partner (role family ``hr``): hiring and skills;
* ``lead`` - kind ``lead``: management loop inside the projects it leads;
* ``manager`` - any other agent that has live reports (or is a reviewer): can delegate to them;
* ``specialist`` - everyone else: executes, may only create tasks for itself.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.enums import LIVE_AGENT_STATUSES, AgentKind, RoleFamily

FOUNDER = "founder"
HR = "hr"
LEAD = "lead"
MANAGER = "manager"
SPECIALIST = "specialist"

ASSIGNERS = frozenset({FOUNDER, LEAD, MANAGER})


def has_live_reports(db: Session, agent: Agent) -> bool:
    return bool(
        db.scalar(
            select(func.count(Agent.id)).where(
                Agent.company_id == agent.company_id,
                Agent.manager_agent_id == agent.id,
                Agent.status.in_(LIVE_AGENT_STATUSES),
            )
        )
    )


def role_of(db: Session, agent: Agent) -> str:
    if agent.kind == AgentKind.FOUNDER:
        return FOUNDER
    if agent.role_family == RoleFamily.HR:
        return HR
    if agent.kind == AgentKind.LEAD:
        return LEAD
    if agent.kind == AgentKind.REVIEWER or has_live_reports(db, agent):
        return MANAGER
    return SPECIALIST


def can_assign_others(role: str) -> bool:
    return role in ASSIGNERS
