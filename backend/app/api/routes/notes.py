from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CompanyCtx, DbSession
from app.schemas.autonomy import (
    FounderNote,
    NoteCategoryLiteral,
    NoteCreate,
    NoteDetail,
    NoteReply,
    NoteReplyCreate,
    NoteStatusLiteral,
    NoteUpdate,
)
from app.schemas.common import Page
from app.services import notes
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}/notes", tags=["founder-notes"])
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


@router.get("", response_model=Page[FounderNote])
def list_notes(
    ctx: CompanyCtx,
    db: DbSession,
    status: NoteStatusLiteral | None = None,
    category: NoteCategoryLiteral | None = None,
    pinned: bool | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    rows, nxt = notes.list_notes(
        db, ctx, limit=limit, cursor=cursor, status=status, category=category, pinned=pinned
    )
    return {"items": notes.serialize(db, rows), "next_cursor": nxt}


@router.post("", response_model=FounderNote, status_code=201)
def create_note(body: NoteCreate, ctx: CompanyCtx, db: DbSession):
    note = notes.create_note(db, ctx, body)
    return notes.serialize(db, [note])[0]


@router.get("/{note_id}", response_model=NoteDetail)
def get_note(note_id: str, ctx: CompanyCtx, db: DbSession):
    note = notes.get_note(db, ctx, note_id)
    return notes.serialize(db, [note], detail=True)[0]


@router.post("/{note_id}/replies", response_model=NoteReply, status_code=201)
def reply(note_id: str, body: NoteReplyCreate, ctx: CompanyCtx, db: DbSession):
    return notes.add_human_reply(db, ctx, note_id, body.body)


@router.patch("/{note_id}", response_model=FounderNote)
def update_note(note_id: str, body: NoteUpdate, ctx: CompanyCtx, db: DbSession):
    note = notes.update_note(db, ctx, note_id, body)
    return notes.serialize(db, [note])[0]
