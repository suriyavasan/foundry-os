"""M21: agent meetings, read by every member of the company; cancelled by owners and admins.

Everything said in a meeting is written by AI employees: clients show it as plain text / markdown
without raw HTML."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CompanyCtx, DbSession
from app.schemas.common import Page
from app.schemas.meetings import (
    MeetingCancel,
    MeetingDetail,
    MeetingOut,
    MeetingStatusLiteral,
)
from app.services import meetings
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}", tags=["meetings"])
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


@router.get("/meetings", response_model=Page[MeetingOut])
def list_meetings(
    ctx: CompanyCtx,
    db: DbSession,
    status: MeetingStatusLiteral | None = None,
    agent_id: str | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, nxt = meetings.list_meetings(
        db, ctx, status=status, agent_id=agent_id, limit=limit, cursor=cursor
    )
    return {"items": items, "next_cursor": nxt}


@router.get("/meetings/{meeting_id}", response_model=MeetingDetail)
def get_meeting(meeting_id: str, ctx: CompanyCtx, db: DbSession):
    return meetings.get_detail(db, ctx, meeting_id)


@router.post("/meetings/{meeting_id}/cancel", response_model=MeetingDetail)
def cancel_meeting(
    meeting_id: str, ctx: CompanyCtx, db: DbSession, body: MeetingCancel | None = None
):
    meetings.cancel(db, ctx, meeting_id, body.reason if body else None)
    return meetings.get_detail(db, ctx, meeting_id)
