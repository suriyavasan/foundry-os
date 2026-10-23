from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import CompanyCtx, CurrentPrincipal, DbSession, get_runtime_registry
from app.runtimes.registry import RuntimeRegistry
from app.schemas.common import Page
from app.schemas.company import Company, CompanyCreate, CompanyUpdate
from app.schemas.system import Overview
from app.services import companies, overview
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies", tags=["companies"])

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


@router.get("", response_model=Page[Company])
def list_companies(
    principal: CurrentPrincipal,
    db: DbSession,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, next_cursor = companies.list_companies(db, principal, limit=limit, cursor=cursor)
    return {"items": items, "next_cursor": next_cursor}


@router.post("", response_model=Company, status_code=201)
def create_company(body: CompanyCreate, principal: CurrentPrincipal, db: DbSession):
    return companies.create_company(db, principal, body)


@router.get("/{company_id}", response_model=Company)
def get_company(ctx: CompanyCtx, db: DbSession):
    return companies.get_company(db, ctx)


@router.patch("/{company_id}", response_model=Company)
def update_company(body: CompanyUpdate, ctx: CompanyCtx, db: DbSession):
    return companies.update_company(db, ctx, body)


@router.get("/{company_id}/overview", response_model=Overview)
def get_overview(
    ctx: CompanyCtx, db: DbSession, registry: RuntimeRegistry = Depends(get_runtime_registry)
):
    return overview.build_overview(db, ctx, registry)
