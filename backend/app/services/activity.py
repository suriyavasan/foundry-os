from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.principal import CompanyContext
from app.models.audit import AuditEvent
from app.services.pagination import paginate


def list_activity(
    db: Session,
    ctx: CompanyContext,
    *,
    limit: int,
    cursor: str | None,
    target_type: str | None = None,
    target_id: str | None = None,
) -> tuple[list[AuditEvent], str | None]:
    stmt = select(AuditEvent).where(AuditEvent.company_id == ctx.company_id)
    if target_type:
        stmt = stmt.where(AuditEvent.target_type == target_type)
    if target_id:
        stmt = stmt.where(AuditEvent.target_id == target_id)
    return paginate(db, stmt, AuditEvent, limit=limit, cursor=cursor, descending=True)
