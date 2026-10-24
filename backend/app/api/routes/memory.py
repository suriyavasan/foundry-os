from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CompanyCtx, DbSession
from app.schemas.autonomy import (
    MemoryCreate,
    MemoryDecisionBody,
    MemoryEntry,
    MemoryKindLiteral,
    MemoryStatusLiteral,
)
from app.schemas.common import Page
from app.services import memory
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}/memory", tags=["memory"])
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


@router.get("", response_model=Page[MemoryEntry])
def list_memory(
    ctx: CompanyCtx,
    db: DbSession,
    status: MemoryStatusLiteral | None = None,
    kind: MemoryKindLiteral | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    tag: Annotated[str | None, Query(max_length=60)] = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    rows, nxt = memory.list_entries(
        db, ctx, limit=limit, cursor=cursor, status=status, kind=kind, q=q, tag=tag
    )
    return {"items": rows, "next_cursor": nxt}


@router.post("", response_model=MemoryEntry, status_code=201)
def create_memory(body: MemoryCreate, ctx: CompanyCtx, db: DbSession):
    return memory.create_as_human(db, ctx, body)


@router.get("/{entry_id}", response_model=MemoryEntry)
def get_memory(entry_id: str, ctx: CompanyCtx, db: DbSession):
    memory._require_member(ctx)
    return memory.get_entry(db, ctx, entry_id)


@router.post("/{entry_id}/verify", response_model=MemoryEntry)
def verify(entry_id: str, ctx: CompanyCtx, db: DbSession, body: MemoryDecisionBody | None = None):
    return memory.decide(db, ctx, entry_id, "verify", body.reason if body else None)


@router.post("/{entry_id}/reject", response_model=MemoryEntry)
def reject(entry_id: str, ctx: CompanyCtx, db: DbSession, body: MemoryDecisionBody | None = None):
    return memory.decide(db, ctx, entry_id, "reject", body.reason if body else None)
