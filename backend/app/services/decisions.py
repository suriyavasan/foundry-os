from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden
from app.core.principal import CompanyContext, Role
from app.models.governance import Decision
from app.schemas.governance import DecisionCreate
from app.services import audit, events
from app.services.agents import _find_secret_paths
from app.services.common import creator_of, get_project_or_404, get_task_or_404
from app.services.pagination import paginate


def list_decisions(
    db: Session,
    ctx: CompanyContext,
    *,
    limit: int,
    cursor: str | None,
    related_task_id: str | None = None,
    related_project_id: str | None = None,
):
    stmt = select(Decision).where(Decision.company_id == ctx.company_id)
    if related_task_id:
        stmt = stmt.where(Decision.related_task_id == related_task_id)
    if related_project_id:
        stmt = stmt.where(Decision.related_project_id == related_project_id)
    return paginate(db, stmt, Decision, limit=limit, cursor=cursor, descending=True)


def create_decision(
    db: Session, ctx: CompanyContext, data: DecisionCreate, *, default_task_id: str | None = None
) -> Decision:
    if ctx.role in (Role.VIEWER, Role.MEMBER):
        raise Forbidden("Viewers and members have read-only access to the decision log")
    if _find_secret_paths(data.context):
        from app.core.errors import ValidationFailed

        raise ValidationFailed(
            "context must not contain secrets",
            details=[{"loc": ["body", "context"], "msg": "key looks like a secret"}],
        )
    task_id = data.related_task_id or default_task_id
    if task_id:
        get_task_or_404(db, ctx.company_id, task_id)
    if data.related_project_id:
        get_project_or_404(db, ctx.company_id, data.related_project_id)
    mtype, mid = creator_of(ctx.principal)
    decision = Decision(
        company_id=ctx.company_id,
        made_by_type=mtype,
        made_by_id=mid,
        title=data.title,
        rationale=data.rationale,
        related_task_id=task_id,
        related_project_id=data.related_project_id,
        context=dict(data.context),
    )
    db.add(decision)
    db.flush()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="decision.recorded",
        target_type="decision",
        target_id=decision.id,
        summary=f"Decision: {decision.title}",
        data={"related_task_id": task_id},
    )
    events.emit(
        db,
        ctx.company_id,
        "decision.created",
        {"decision_id": decision.id, "title": decision.title},
    )
    db.commit()
    return decision
