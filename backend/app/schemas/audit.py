from __future__ import annotations

from typing import Any, Literal

from app.schemas.common import ApiModel, Timestamp


class AuditEvent(ApiModel):
    id: str
    company_id: str
    actor_type: Literal["human", "agent", "system"]
    actor_id: str | None
    actor_label: str
    action: str
    target_type: str
    target_id: str | None
    summary: str
    data: dict[str, Any]
    created_at: Timestamp
