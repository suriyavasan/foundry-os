from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden
from app.core.principal import CompanyContext, require_human_admin
from app.core.time import utcnow
from app.models.enums import AgentKind
from app.models.work import Goal
from app.schemas.work import GoalCreate, GoalUpdate
from app.services import audit, events, wakes
from app.services.common import creator_of, get_agent_or_404, get_goal_or_404
from app.services.pagination import paginate

UPDATABLE = ("title", "description", "status", "priority", "owner_agent_id", "target_date")


def list_goals(
    db: Session,
    ctx: CompanyContext,
    *,
    limit: int,
    cursor: str | None,
    status: str | None = None,
    owner_agent_id: str | None = None,
):
    stmt = select(Goal).where(Goal.company_id == ctx.company_id)
    if status:
        stmt = stmt.where(Goal.status == status)
    if owner_agent_id:
        stmt = stmt.where(Goal.owner_agent_id == owner_agent_id)
    return paginate(db, stmt, Goal, limit=limit, cursor=cursor, descending=False)


def get_goal(db: Session, ctx: CompanyContext, goal_id: str) -> Goal:
    return get_goal_or_404(db, ctx.company_id, goal_id)


def _require_goal_writer(db: Session, ctx: CompanyContext, action: str) -> None:
    """Humans (owner/admin) and the AI Co-founder manage goals; nobody else."""
    if ctx.principal.is_agent:
        from app.models.agent import Agent

        agent = db.get(Agent, ctx.principal.id)
        if agent is None or agent.kind != AgentKind.FOUNDER:
            raise Forbidden(f"Only the AI Co-founder and humans can {action}")
        return
    require_human_admin(ctx, action)


def create_goal(db: Session, ctx: CompanyContext, data: GoalCreate) -> Goal:
    _require_goal_writer(db, ctx, "create goals")
    if data.owner_agent_id:
        get_agent_or_404(db, ctx.company_id, data.owner_agent_id, label="Owner agent")
    ctype, cid = creator_of(ctx.principal)
    goal = Goal(
        company_id=ctx.company_id,
        title=data.title,
        description=data.description,
        status=data.status,
        priority=data.priority,
        owner_agent_id=data.owner_agent_id,
        target_date=data.target_date,
        created_by_type=ctype,
        created_by_id=cid,
    )
    db.add(goal)
    db.flush()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="goal.created",
        target_type="goal",
        target_id=goal.id,
        summary=f"Created goal {goal.title}",
        data={"status": goal.status},
    )
    events.emit(db, ctx.company_id, "goal.created", {"goal_id": goal.id, "status": goal.status})
    wakes.wake_founder(
        db,
        ctx.company_id,
        "goal",
        f"Goal created: {goal.title}",
        ref_type="goal",
        ref_id=goal.id,
        actor_agent_id=ctx.principal.id if ctx.principal.is_agent else None,
    )
    db.commit()
    return goal


def update_goal(db: Session, ctx: CompanyContext, goal_id: str, data: GoalUpdate) -> Goal:
    _require_goal_writer(db, ctx, "update goals")
    goal = get_goal_or_404(db, ctx.company_id, goal_id)
    if data.owner_agent_id and "owner_agent_id" in data.model_fields_set:
        get_agent_or_404(db, ctx.company_id, data.owner_agent_id, label="Owner agent")
    changed: list[str] = []
    for field in UPDATABLE:
        if field in data.model_fields_set and getattr(goal, field) != getattr(data, field):
            setattr(goal, field, getattr(data, field))
            changed.append(field)
    if changed:
        goal.updated_at = utcnow()
        audit.record(
            db,
            company_id=ctx.company_id,
            principal=ctx.principal,
            action="goal.updated",
            target_type="goal",
            target_id=goal.id,
            summary=f"Updated goal {goal.title}: {', '.join(changed)}",
            data={"changed": changed},
        )
        events.emit(db, ctx.company_id, "goal.updated", {"goal_id": goal.id, "changed": changed})
        wakes.wake_founder(
            db,
            ctx.company_id,
            "goal",
            f"Goal changed ({', '.join(changed)}): {goal.title}",
            ref_type="goal",
            ref_id=goal.id,
            actor_agent_id=ctx.principal.id if ctx.principal.is_agent else None,
        )
        db.commit()
    return goal
