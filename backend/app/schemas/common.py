from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer

from app.core.time import format_ts

# ISO-8601 UTC with trailing Z in JSON output.
Timestamp = Annotated[datetime, PlainSerializer(format_ts, return_type=str, when_used="json")]


class ApiModel(BaseModel):
    """Base for response models (built from ORM objects)."""

    model_config = ConfigDict(from_attributes=True)


class RequestModel(BaseModel):
    """Base for request bodies: unknown fields are rejected (422)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Page[T](ApiModel):
    items: list[T]
    next_cursor: str | None = None


class ErrorBody(BaseModel):
    code: str
    message: str
    details: object | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class AvailabilityBlock(BaseModel):
    """Marks a subsystem that is not built yet. Never replaced by fake numbers."""

    available: bool = False
    milestone: str
