"""Company event outbox (decision D10). ``emit`` only adds to the caller's session, so the
event commits atomically with the change it describes."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models.run import CompanyEvent

EVENT_RETENTION_DAYS = 7


def emit(db: Session, company_id: str, type_: str, payload: dict[str, Any]) -> CompanyEvent:
    event = CompanyEvent(company_id=company_id, type=type_, payload=payload)
    db.add(event)
    return event


def latest_event_id(db: Session, company_id: str) -> int:
    return int(
        db.scalar(select(func.max(CompanyEvent.id)).where(CompanyEvent.company_id == company_id))
        or 0
    )


def fetch_after(
    db: Session, company_id: str, after_id: int, *, limit: int = 200
) -> list[CompanyEvent]:
    return list(
        db.scalars(
            select(CompanyEvent)
            .where(CompanyEvent.company_id == company_id, CompanyEvent.id > after_id)
            .order_by(CompanyEvent.id)
            .limit(limit)
        )
    )


def prune(db: Session, now: datetime | None = None) -> int:
    cutoff = (now or utcnow()) - timedelta(days=EVENT_RETENTION_DAYS)
    result = db.execute(delete(CompanyEvent).where(CompanyEvent.created_at < cutoff))
    return int(result.rowcount or 0)
