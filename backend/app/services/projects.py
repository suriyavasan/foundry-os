from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.principal import CompanyContext, require_human_admin
from app.core.time import utcnow
from app.models.work import Project
from app.schemas.work import ProjectCreate, ProjectUpdate
from app.services import audit, events
from app.services.common import (
    creator_of,
    get_agent_or_404,
    get_goal_or_404,
    get_project_or_404,
)
from app.services.pagination import paginate

UPDATABLE = ("name", "description", "goal_id", "status", "lead_agent_id", "budget_micros")


def list_projects(
    db: Session,
    ctx: CompanyContext,
    *,
    limit: int,
    cursor: str | None,
    status: str | None = None,
    goal_id: str | None = None,
):
    stmt = select(Project).where(Project.company_id == ctx.company_id)
    if status:
        stmt = stmt.where(Project.status == status)
    if goal_id:
        stmt = stmt.where(Project.goal_id == goal_id)
    return paginate(db, stmt, Project, limit=limit, cursor=cursor, descending=False)


def get_project(db: Session, ctx: CompanyContext, project_id: str) -> Project:
    return get_project_or_404(db, ctx.company_id, project_id)


def _check_refs(db: Session, ctx: CompanyContext, goal_id, lead_agent_id) -> None:
    if goal_id:
        get_goal_or_404(db, ctx.company_id, goal_id)
    if lead_agent_id:
        get_agent_or_404(db, ctx.company_id, lead_agent_id, label="Lead agent")


def create_project(db: Session, ctx: CompanyContext, data: ProjectCreate) -> Project:
    require_human_admin(ctx, "create projects")
    _check_refs(db, ctx, data.goal_id, data.lead_agent_id)
    ctype, cid = creator_of(ctx.principal)
    project = Project(
        company_id=ctx.company_id,
        goal_id=data.goal_id,
        name=data.name,
        description=data.description,
        status=data.status,
        lead_agent_id=data.lead_agent_id,
        budget_micros=data.budget_micros,
        created_by_type=ctype,
        created_by_id=cid,
    )
    db.add(project)
    db.flush()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="project.created",
        target_type="project",
        target_id=project.id,
        summary=f"Created project {project.name}",
        data={"status": project.status},
    )
    events.emit(
        db, ctx.company_id, "project.created", {"project_id": project.id, "status": project.status}
    )
    db.commit()
    return project


def update_project(
    db: Session, ctx: CompanyContext, project_id: str, data: ProjectUpdate
) -> Project:
    require_human_admin(ctx, "update projects")
    project = get_project_or_404(db, ctx.company_id, project_id)
    fields = data.model_fields_set
    _check_refs(
        db,
        ctx,
        data.goal_id if "goal_id" in fields else None,
        data.lead_agent_id if "lead_agent_id" in fields else None,
    )
    changed: list[str] = []
    for field in UPDATABLE:
        if field in fields and getattr(project, field) != getattr(data, field):
            setattr(project, field, getattr(data, field))
            changed.append(field)
    if changed:
        project.updated_at = utcnow()
        audit.record(
            db,
            company_id=ctx.company_id,
            principal=ctx.principal,
            action="project.updated",
            target_type="project",
            target_id=project.id,
            summary=f"Updated project {project.name}: {', '.join(changed)}",
            data={"changed": changed},
        )
        events.emit(
            db, ctx.company_id, "project.updated", {"project_id": project.id, "changed": changed}
        )
        if "budget_micros" in changed:
            from app.services import tasks

            tasks.release_budget_blocked(db, ctx.company_id, ctx.principal)
        db.commit()
    return project
