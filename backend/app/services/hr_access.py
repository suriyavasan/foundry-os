"""Who may do HR things. Enforced in services (never only in routes)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden
from app.core.principal import CompanyContext, Role
from app.models.agent import Agent
from app.models.enums import AgentKind, AgentStatus, RoleFamily


def _acting_agent(db: Session, ctx: CompanyContext) -> Agent | None:
    if not ctx.principal.is_agent:
        return None
    agent = db.scalar(
        select(Agent).where(Agent.id == ctx.principal.id, Agent.company_id == ctx.company_id)
    )
    if agent is None or agent.status != AgentStatus.ACTIVE:
        return None
    return agent


def is_active_hr(db: Session, ctx: CompanyContext) -> bool:
    agent = _acting_agent(db, ctx)
    return agent is not None and agent.role_family == RoleFamily.HR


def is_active_founder(db: Session, ctx: CompanyContext) -> bool:
    agent = _acting_agent(db, ctx)
    return agent is not None and agent.kind == AgentKind.FOUNDER


def require_hr(db: Session, ctx: CompanyContext, action: str = "do this") -> Agent:
    """The active HR agent of this company (agent API, HR-only endpoints)."""
    agent = _acting_agent(db, ctx)
    if agent is None or agent.role_family != RoleFamily.HR:
        raise Forbidden(f"Only the HR agent can {action}")
    return agent


def require_hr_or_founder(db: Session, ctx: CompanyContext, action: str = "do this") -> Agent:
    agent = _acting_agent(db, ctx)
    if agent is None or (agent.role_family != RoleFamily.HR and agent.kind != AgentKind.FOUNDER):
        raise Forbidden(f"Only HR or the founder can {action}")
    return agent


def require_human_or_hr(db: Session, ctx: CompanyContext, action: str = "do this") -> None:
    """Humans (owner/admin) or the HR agent. Viewers and every other agent get 403."""
    if ctx.role == Role.VIEWER:
        raise Forbidden("Viewers have read-only access")
    if ctx.principal.is_agent:
        require_hr(db, ctx, action)
    elif not ctx.is_human_admin:
        raise Forbidden(f"Only company owners and admins can {action}")
