from __future__ import annotations

from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from app.schemas.common import ApiModel, RequestModel, Timestamp

CompanyStatusLiteral = Literal["active", "paused"]


def _check_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError(f"unknown IANA timezone: {value!r}") from exc
    return value


def _check_constraints(value: list[str]) -> list[str]:
    cleaned = [c.strip() for c in value]
    if any(not c for c in cleaned):
        raise ValueError("constraints must not contain empty strings")
    if any(len(c) > 1000 for c in cleaned):
        raise ValueError("each constraint must be at most 1000 characters")
    if len(cleaned) > 100:
        raise ValueError("at most 100 constraints")
    return cleaned


class Company(ApiModel):
    id: str
    name: str
    slug: str
    mission: str | None
    vision: str | None
    strategic_goal: str | None
    constraints: list[str]
    timezone: str
    status: CompanyStatusLiteral
    created_at: Timestamp
    updated_at: Timestamp


class FounderInput(RequestModel):
    name: str = Field(default="Atlas", min_length=1, max_length=120)
    model_provider: str | None = Field(default=None, max_length=80)
    model_name: str | None = Field(default=None, max_length=160)
    runtime: str = Field(default="unassigned", min_length=1, max_length=64)


class CompanyCreate(RequestModel):
    name: str = Field(min_length=1, max_length=120)
    mission: str | None = Field(default=None, max_length=10_000)
    vision: str | None = Field(default=None, max_length=10_000)
    strategic_goal: str | None = Field(default=None, max_length=10_000)
    constraints: list[str] = Field(default_factory=list)
    timezone: str = "UTC"
    founder: FounderInput = Field(default_factory=FounderInput)

    _tz = field_validator("timezone")(_check_timezone)
    _constraints = field_validator("constraints")(_check_constraints)


class CompanyUpdate(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    mission: str | None = Field(default=None, max_length=10_000)
    vision: str | None = Field(default=None, max_length=10_000)
    strategic_goal: str | None = Field(default=None, max_length=10_000)
    constraints: list[str] | None = None
    timezone: str | None = None
    status: CompanyStatusLiteral | None = None

    @field_validator("name", "timezone", "constraints", "status")
    @classmethod
    def _not_null(cls, v):  # explicit null is invalid for non-nullable columns
        if v is None:
            raise ValueError("must not be null")
        return v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v):
        return _check_timezone(v)

    @field_validator("constraints")
    @classmethod
    def _constraints(cls, v):
        return _check_constraints(v)
