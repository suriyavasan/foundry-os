"""Reflections and company standards (M10, owner API). The AI Co-founder writes through the agent API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CompanyCtx, DbSession
from app.schemas.common import Page
from app.schemas.reflections import (
    CompanyStandard,
    CompanyStandardCreate,
    CompanyStandardUpdate,
    Reflection,
    ReflectionDetail,
    ReflectionResponse,
    ReflectionResponseCreate,
    ReflectionStatusLiteral,
    StandardStatusLiteral,
)
from app.services import reflections, standards
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}", tags=["reflections"])
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


@router.get("/reflections", response_model=Page[Reflection])
def list_reflections(
    ctx: CompanyCtx,
    db: DbSession,
    status: ReflectionStatusLiteral | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, nxt = reflections.list_reflections(db, ctx, limit=limit, cursor=cursor, status=status)
    return {"items": items, "next_cursor": nxt}


@router.get("/reflections/{reflection_id}", response_model=ReflectionDetail)
def get_reflection(reflection_id: str, ctx: CompanyCtx, db: DbSession):
    return reflections.get_reflection(db, ctx, reflection_id)


@router.post(
    "/reflections/{reflection_id}/responses", response_model=ReflectionResponse, status_code=201
)
def respond(reflection_id: str, body: ReflectionResponseCreate, ctx: CompanyCtx, db: DbSession):
    """Endorse / challenge / refine / comment. Endorsing a standard proposal adopts the standard."""
    return reflections.respond(db, ctx, reflection_id, body)


@router.post("/reflections/{reflection_id}/archive", response_model=ReflectionDetail)
def archive(reflection_id: str, ctx: CompanyCtx, db: DbSession):
    return reflections.archive(db, ctx, reflection_id)


@router.get("/standards", response_model=Page[CompanyStandard])
def list_standards(
    ctx: CompanyCtx,
    db: DbSession,
    status: StandardStatusLiteral | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, nxt = standards.list_standards(db, ctx, limit=limit, cursor=cursor, status=status)
    return {"items": items, "next_cursor": nxt}


@router.post("/standards", response_model=CompanyStandard, status_code=201)
def create_standard(body: CompanyStandardCreate, ctx: CompanyCtx, db: DbSession):
    return standards.create_standard(db, ctx, body)


@router.patch("/standards/{standard_id}", response_model=CompanyStandard)
def update_standard(standard_id: str, body: CompanyStandardUpdate, ctx: CompanyCtx, db: DbSession):
    return standards.update_standard(db, ctx, standard_id, body)
