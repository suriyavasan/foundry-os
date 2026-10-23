from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.schemas.common import Timestamp


class RuntimeHealthOut(BaseModel):
    status: Literal["ok", "degraded", "unavailable", "not_configured"]
    detail: str
    checked_at: Timestamp


class RuntimeInfo(BaseModel):
    id: str
    display_name: str
    kind: str
    capabilities: list[str]
    health: RuntimeHealthOut
    config_hint: str


class RuntimeList(BaseModel):
    items: list[RuntimeInfo]
