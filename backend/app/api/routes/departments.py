from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CompanyCtx, DbSession
from app.schemas.common import Page
from app.schemas.department import Department, DepartmentCreate, DepartmentUpdate
from app.services import departments
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}/departments", tags=["departments"])

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


@router.get("", response_model=Page[Department])
def list_departments(
    ctx: CompanyCtx, db: DbSession, limit: Limit = DEFAULT_LIMIT, cursor: str | None = None
):
    items, next_cursor = departments.list_departments(db, ctx, limit=limit, cursor=cursor)
    return {"items": items, "next_cursor": next_cursor}


@router.post("", response_model=Department, status_code=201)
def create_department(body: DepartmentCreate, ctx: CompanyCtx, db: DbSession):
    return departments.create_department(db, ctx, body)


@router.patch("/{department_id}", response_model=Department)
def update_department(department_id: str, body: DepartmentUpdate, ctx: CompanyCtx, db: DbSession):
    return departments.update_department(db, ctx, department_id, body)
