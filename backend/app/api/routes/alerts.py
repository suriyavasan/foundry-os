from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CompanyCtx, DbSession
from app.schemas.autonomy import Alert, AlertSeverityLiteral, AlertStatusLiteral
from app.schemas.common import Page
from app.services import alerts
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}/alerts", tags=["alerts"])
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


@router.get("", response_model=Page[Alert])
def list_alerts(
    ctx: CompanyCtx,
    db: DbSession,
    status: AlertStatusLiteral | None = None,
    severity: AlertSeverityLiteral | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    rows, nxt = alerts.list_alerts(
        db, ctx, limit=limit, cursor=cursor, status=status, severity=severity
    )
    return {"items": rows, "next_cursor": nxt}


@router.get("/{alert_id}", response_model=Alert)
def get_alert(alert_id: str, ctx: CompanyCtx, db: DbSession):
    return alerts.get_alert(db, ctx, alert_id)


@router.post("/{alert_id}/resolve", response_model=Alert)
def resolve(alert_id: str, ctx: CompanyCtx, db: DbSession):
    return alerts.resolve_alert(db, ctx, alert_id)
