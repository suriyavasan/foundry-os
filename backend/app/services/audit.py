"""Audit trail. ``record`` only adds to the caller's session; the caller commits, so the
event lands in the same transaction as the state change it describes."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.core.principal import Principal
from app.models.audit import AuditEvent
from app.models.enums import ActorType
from app.services import events


def record(
    db: Session,
    *,
    company_id: str,
    principal: Principal | None,
    action: str,
    target_type: str,
    target_id: str | None,
    summary: str,
    data: dict[str, Any] | None = None,
) -> AuditEvent:
    if principal is None:
        actor_type, actor_id, actor_label = ActorType.SYSTEM, None, "System"
    else:
        actor_type, actor_id, actor_label = principal.type.value, principal.id, principal.label
    event = AuditEvent(
        id=new_id(),
        company_id=company_id,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label=actor_label,
        action=action,
        target_type=target_type,
        target_id=target_id,
        summary=summary,
        data=data or {},
    )
    db.add(event)
    # Mirror into the live-update outbox so the UI feed stays current (same transaction).
    events.emit(
        db,
        company_id,
        "activity",
        {
            "id": event.id,
            "action": action,
            "actor_type": actor_type,
            "actor_label": actor_label,
            "target_type": target_type,
            "target_id": target_id,
            "summary": summary,
        },
    )
    return event
