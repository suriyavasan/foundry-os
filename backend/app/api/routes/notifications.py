from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentPrincipal, DbSession
from app.core.errors import Forbidden
from app.schemas import notifications as schemas
from app.services import notifications as service
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _human(principal) -> None:
    if not principal.is_human:
        raise Forbidden("Notifications belong to people")


@router.get("", response_model=schemas.NotificationList)
def list_notifications(
    principal: CurrentPrincipal,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: str | None = None,
    unread: bool = False,
):
    """The signed-in person's notifications, newest first, with the total unread count."""
    _human(principal)
    rows, nxt, unread_total = service.list_for_user(
        db, principal.id, limit=limit, cursor=cursor, unread_only=unread
    )
    return {"items": rows, "unread_count": unread_total, "next_cursor": nxt}


@router.post("/read", response_model=schemas.NotificationsReadResult)
def mark_read(body: schemas.NotificationsRead, principal: CurrentPrincipal, db: DbSession):
    _human(principal)
    marked = service.mark_read(db, principal.id, ids=body.ids, all_=bool(body.all))
    return {"marked": marked, "unread_count": service.unread_count(db, principal.id)}
