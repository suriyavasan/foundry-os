"""Company standards: rules the Founder (a human) set for the whole company (docs/specs/m10 section 4).

Only humans create, edit or retire them -- the AI Co-founder can propose (a ``standard_proposal``
reflection) but a standard exists only once a human endorses the proposal (``reflections.respond``)
or writes it here. Active standards are injected into every agent's context and into the prompt of
every CLI run."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, LimitExceeded, NotFound
from app.core.principal import CompanyContext, Principal, require_human_admin
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.reflections import CompanyStandard, StandardStatus
from app.schemas.reflections import CompanyStandardCreate, CompanyStandardUpdate
from app.services import audit, events
from app.services.pagination import paginate

MAX_ACTIVE_STANDARDS = 25
CONTEXT_MAX_STANDARDS = 15
CONTEXT_LINE_CHARS = 400


def _human(ctx: CompanyContext) -> None:
    if not ctx.principal.is_human:
        raise Forbidden("Only people set the company's standards")


def get_standard(db: Session, company_id: str, standard_id: str) -> CompanyStandard:
    row = db.scalar(
        select(CompanyStandard).where(
            CompanyStandard.id == standard_id, CompanyStandard.company_id == company_id
        )
    )
    if row is None:
        raise NotFound("Standard not found")
    return row


def list_standards(
    db: Session, ctx: CompanyContext, *, limit: int, cursor: str | None, status: str | None = None
):
    _human(ctx)
    stmt = select(CompanyStandard).where(CompanyStandard.company_id == ctx.company_id)
    if status:
        stmt = stmt.where(CompanyStandard.status == status)
    return paginate(db, stmt, CompanyStandard, limit=limit, cursor=cursor, descending=True)


def active_count(db: Session, company_id: str) -> int:
    return int(
        db.scalar(
            select(func.count(CompanyStandard.id)).where(
                CompanyStandard.company_id == company_id,
                CompanyStandard.status == StandardStatus.ACTIVE,
            )
        )
        or 0
    )


def _emit(db: Session, row: CompanyStandard, type_: str) -> None:
    events.emit(
        db,
        row.company_id,
        type_,
        {"standard_id": row.id, "status": row.status, "reflection_id": row.source_reflection_id},
    )


def adopt(
    db: Session,
    company_id: str,
    principal: Principal,
    *,
    title: str,
    statement: str,
    source_reflection_id: str | None,
    applies_to: list[str] | None = None,
) -> CompanyStandard:
    """Create an active standard. ``principal`` must be a human (checked here, not only by callers).
    No commit."""
    if not principal.is_human:
        raise Forbidden("Only people set the company's standards")
    if active_count(db, company_id) >= MAX_ACTIVE_STANDARDS:
        raise LimitExceeded(
            f"A company can have {MAX_ACTIVE_STANDARDS} active standards; retire one first",
            details={"limit": "active_standards", "max": MAX_ACTIVE_STANDARDS},
        )
    now = utcnow()
    row = CompanyStandard(
        company_id=company_id,
        title=title[:200],
        statement=statement[:500],
        source_reflection_id=source_reflection_id,
        adopted_by_user_id=principal.id,
        adopted_at=now,
        status=StandardStatus.ACTIVE,
        applies_to=list(applies_to or []),
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        company_id=company_id,
        principal=principal,
        action="standard.adopted",
        target_type="standard",
        target_id=row.id,
        summary=f"Adopted company standard: {row.title}",
        data={"source_reflection_id": source_reflection_id, "applies_to": row.applies_to},
    )
    _emit(db, row, "standard.adopted")
    return row


def create_standard(
    db: Session, ctx: CompanyContext, data: CompanyStandardCreate
) -> CompanyStandard:
    _human(ctx)
    require_human_admin(ctx, "set company standards")
    row = adopt(
        db,
        ctx.company_id,
        ctx.principal,
        title=data.title,
        statement=data.statement,
        source_reflection_id=None,
        applies_to=data.applies_to,
    )
    db.commit()
    return row


def update_standard(
    db: Session, ctx: CompanyContext, standard_id: str, data: CompanyStandardUpdate
) -> CompanyStandard:
    _human(ctx)
    require_human_admin(ctx, "change company standards")
    row = get_standard(db, ctx.company_id, standard_id)
    changed: list[str] = []
    if "status" in data.model_fields_set and data.status != row.status:
        if data.status == StandardStatus.ACTIVE and active_count(db, ctx.company_id) >= (
            MAX_ACTIVE_STANDARDS
        ):
            raise LimitExceeded(
                f"A company can have {MAX_ACTIVE_STANDARDS} active standards; retire one first"
            )
        row.status = data.status  # type: ignore[assignment]
        changed.append("status")
    for field in ("title", "statement", "applies_to"):
        if field in data.model_fields_set:
            new = getattr(data, field)
            if getattr(row, field) != new:
                setattr(row, field, list(new) if isinstance(new, list) else new)
                changed.append(field)
    if not changed:
        raise Conflict("Nothing to change")
    row.updated_at = utcnow()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="standard.retired"
        if "status" in changed and row.status == "retired"
        else "standard.updated",
        target_type="standard",
        target_id=row.id,
        summary=f"Updated company standard '{row.title}': {', '.join(changed)}",
        data={"changed": changed},
    )
    _emit(db, row, "standard.updated")
    db.commit()
    return row


# ---------------------------------------------------------------- injection into agent context


def _applies(row: CompanyStandard, agent: Agent) -> bool:
    if not row.applies_to:
        return True
    mine = {
        str(agent.id),
        str(agent.role_family),
        str(agent.role_template or ""),
        str(agent.kind),
        "all",
    }
    return bool(mine & {str(a) for a in row.applies_to})


def active_for(db: Session, company_id: str, agent: Agent) -> list[CompanyStandard]:
    rows = db.scalars(
        select(CompanyStandard)
        .where(
            CompanyStandard.company_id == company_id,
            CompanyStandard.status == StandardStatus.ACTIVE,
        )
        .order_by(CompanyStandard.adopted_at, CompanyStandard.id)
        .limit(100)
    )
    return [r for r in rows if _applies(r, agent)][:CONTEXT_MAX_STANDARDS]


def context_lines(db: Session, company_id: str, agent: Agent) -> list[str]:
    """One compact line per active standard that applies to ``agent`` (run prompt / GET /context)."""
    return [
        f"{r.title}: {' '.join(r.statement.split())}"[:CONTEXT_LINE_CHARS]
        for r in active_for(db, company_id, agent)
    ]


def context_items(db: Session, company_id: str, agent: Agent) -> list[dict[str, Any]]:
    return [
        {"id": r.id, "title": r.title, "statement": r.statement}
        for r in active_for(db, company_id, agent)
    ]
