from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CompanyCtx, DbSession
from app.schemas.audit import AuditEvent
from app.schemas.common import Page
from app.services import activity
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}/activity", tags=["activity"])


@router.get("", response_model=Page[AuditEvent])
def list_activity(
    ctx: CompanyCtx,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
):
    items, next_cursor = activity.list_activity(
        db, ctx, limit=limit, cursor=cursor, target_type=target_type, target_id=target_id
    )
    return {"items": items, "next_cursor": next_cursor}
