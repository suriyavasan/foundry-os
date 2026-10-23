from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound
from app.core.principal import CompanyContext, require_human_admin
from app.models.agent import Agent
from app.models.department import Department
from app.models.enums import AgentStatus
from app.schemas.department import Department as DepartmentOut
from app.schemas.department import DepartmentCreate, DepartmentUpdate
from app.services import audit
from app.services.pagination import paginate

# agent_count counts employees still in the org: everything except retired/rejected.
_INACTIVE = (AgentStatus.RETIRED, AgentStatus.REJECTED)


def _counts(db: Session, ctx: CompanyContext, department_ids: list[str]) -> dict[str, int]:
    if not department_ids:
        return {}
    rows = db.execute(
        select(Agent.department_id, func.count(Agent.id))
        .where(
            Agent.company_id == ctx.company_id,
            Agent.department_id.in_(department_ids),
            Agent.status.not_in(_INACTIVE),
        )
        .group_by(Agent.department_id)
    ).all()
    return {dept_id: count for dept_id, count in rows}


def _view(dept: Department, count: int) -> DepartmentOut:
    return DepartmentOut(
        id=dept.id,
        company_id=dept.company_id,
        name=dept.name,
        description=dept.description,
        lead_agent_id=dept.lead_agent_id,
        agent_count=count,
        created_at=dept.created_at,
        updated_at=dept.updated_at,
    )


def _load(db: Session, ctx: CompanyContext, department_id: str) -> Department:
    dept = db.scalar(
        select(Department).where(
            Department.id == department_id, Department.company_id == ctx.company_id
        )
    )
    if dept is None:
        raise NotFound("Department not found")
    return dept


def _check_lead(db: Session, ctx: CompanyContext, agent_id: str) -> None:
    lead = db.scalar(select(Agent).where(Agent.id == agent_id, Agent.company_id == ctx.company_id))
    if lead is None:
        raise NotFound("Lead agent not found")
    if lead.status in _INACTIVE:
        raise Conflict("A retired or rejected agent cannot lead a department")


def list_departments(
    db: Session, ctx: CompanyContext, *, limit: int, cursor: str | None
) -> tuple[list[DepartmentOut], str | None]:
    stmt = select(Department).where(Department.company_id == ctx.company_id)
    rows, next_cursor = paginate(db, stmt, Department, limit=limit, cursor=cursor, descending=False)
    counts = _counts(db, ctx, [d.id for d in rows])
    return [_view(d, counts.get(d.id, 0)) for d in rows], next_cursor


def get_department(db: Session, ctx: CompanyContext, department_id: str) -> DepartmentOut:
    dept = _load(db, ctx, department_id)
    return _view(dept, _counts(db, ctx, [dept.id]).get(dept.id, 0))


def _name_taken(db: Session, ctx: CompanyContext, name: str, exclude_id: str | None) -> bool:
    stmt = select(Department.id).where(
        Department.company_id == ctx.company_id, Department.name == name
    )
    if exclude_id:
        stmt = stmt.where(Department.id != exclude_id)
    return db.scalar(stmt) is not None


def create_department(db: Session, ctx: CompanyContext, data: DepartmentCreate) -> DepartmentOut:
    require_human_admin(ctx, "create departments")
    if _name_taken(db, ctx, data.name, None):
        raise Conflict(f"A department named {data.name!r} already exists")
    if data.lead_agent_id:
        _check_lead(db, ctx, data.lead_agent_id)
    dept = Department(
        company_id=ctx.company_id,
        name=data.name,
        description=data.description,
        lead_agent_id=data.lead_agent_id,
    )
    db.add(dept)
    db.flush()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="department.created",
        target_type="department",
        target_id=dept.id,
        summary=f"Created department {dept.name}",
        data={"lead_agent_id": dept.lead_agent_id},
    )
    _commit(db)
    return _view(dept, 0)


def update_department(
    db: Session, ctx: CompanyContext, department_id: str, data: DepartmentUpdate
) -> DepartmentOut:
    require_human_admin(ctx, "update departments")
    dept = _load(db, ctx, department_id)
    changed: list[str] = []
    fields = data.model_fields_set
    if "name" in fields and data.name != dept.name:
        if _name_taken(db, ctx, data.name, dept.id):  # type: ignore[arg-type]
            raise Conflict(f"A department named {data.name!r} already exists")
        dept.name = data.name  # type: ignore[assignment]
        changed.append("name")
    if "description" in fields and data.description != dept.description:
        dept.description = data.description
        changed.append("description")
    if "lead_agent_id" in fields and data.lead_agent_id != dept.lead_agent_id:
        if data.lead_agent_id:
            _check_lead(db, ctx, data.lead_agent_id)
        dept.lead_agent_id = data.lead_agent_id
        changed.append("lead_agent_id")
    if changed:
        audit.record(
            db,
            company_id=ctx.company_id,
            principal=ctx.principal,
            action="department.updated",
            target_type="department",
            target_id=dept.id,
            summary=f"Updated department {dept.name}: {', '.join(changed)}",
            data={"changed": changed},
        )
        _commit(db)
    return _view(dept, _counts(db, ctx, [dept.id]).get(dept.id, 0))


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("Department conflicts with an existing record") from exc
