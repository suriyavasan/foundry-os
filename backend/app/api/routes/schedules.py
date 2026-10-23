from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CompanyCtx, DbSession
from app.schemas.common import Page
from app.schemas.schedule import Schedule, ScheduleCreate, ScheduleKindLiteral, ScheduleUpdate
from app.services import schedules
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}/schedules", tags=["schedules"])
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


@router.get("", response_model=Page[Schedule])
def list_schedules(
    ctx: CompanyCtx,
    db: DbSession,
    enabled: bool | None = None,
    kind: ScheduleKindLiteral | None = None,
    agent_id: str | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, nxt = schedules.list_schedules(
        db, ctx, limit=limit, cursor=cursor, enabled=enabled, kind=kind, agent_id=agent_id
    )
    return {"items": items, "next_cursor": nxt}


@router.post("", response_model=Schedule, status_code=201)
def create_schedule(body: ScheduleCreate, ctx: CompanyCtx, db: DbSession):
    return schedules.create_schedule(db, ctx, body)


@router.get("/{schedule_id}", response_model=Schedule)
def get_schedule(schedule_id: str, ctx: CompanyCtx, db: DbSession):
    return schedules.get_schedule(db, ctx, schedule_id)


@router.patch("/{schedule_id}", response_model=Schedule)
def update_schedule(schedule_id: str, body: ScheduleUpdate, ctx: CompanyCtx, db: DbSession):
    return schedules.update_schedule(db, ctx, schedule_id, body)
