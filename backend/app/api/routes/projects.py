from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CompanyCtx, DbSession
from app.schemas.common import Page
from app.schemas.work import Project, ProjectCreate, ProjectStatusLiteral, ProjectUpdate
from app.services import projects
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}/projects", tags=["projects"])
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


@router.get("", response_model=Page[Project])
def list_projects(
    ctx: CompanyCtx,
    db: DbSession,
    status: ProjectStatusLiteral | None = None,
    goal_id: str | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, nxt = projects.list_projects(
        db, ctx, limit=limit, cursor=cursor, status=status, goal_id=goal_id
    )
    return {"items": items, "next_cursor": nxt}


@router.post("", response_model=Project, status_code=201)
def create_project(body: ProjectCreate, ctx: CompanyCtx, db: DbSession):
    return projects.create_project(db, ctx, body)


@router.get("/{project_id}", response_model=Project)
def get_project(project_id: str, ctx: CompanyCtx, db: DbSession):
    return projects.get_project(db, ctx, project_id)


@router.patch("/{project_id}", response_model=Project)
def update_project(project_id: str, body: ProjectUpdate, ctx: CompanyCtx, db: DbSession):
    return projects.update_project(db, ctx, project_id, body)
