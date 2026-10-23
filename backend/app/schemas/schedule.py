from __future__ import annotations

from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import ApiModel, RequestModel, Timestamp
from app.schemas.work import PriorityLiteral, TaskKindLiteral

ScheduleKindLiteral = Literal["heartbeat", "routine"]


class TaskTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=50_000)
    kind: TaskKindLiteral | None = None
    priority: PriorityLiteral = "normal"
    acceptance_criteria: str | None = Field(default=None, max_length=20_000)


def check_cron(value: str) -> str:
    value = " ".join(value.split())
    if len(value.split(" ")) != 5 or not croniter.is_valid(value):
        raise ValueError("cron must be a valid 5-field expression")
    return value


def check_tz(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError(f"unknown IANA timezone: {value!r}") from exc
    return value


class Schedule(ApiModel):
    id: str
    company_id: str
    name: str
    kind: ScheduleKindLiteral
    agent_id: str
    interval_seconds: int | None
    cron: str | None
    timezone: str
    task_template: dict[str, Any]
    enabled: bool
    next_run_at: Timestamp | None
    last_fired_at: Timestamp | None
    created_at: Timestamp
    updated_at: Timestamp


class ScheduleCreate(RequestModel):
    name: str = Field(min_length=1, max_length=200)
    kind: ScheduleKindLiteral = "routine"
    agent_id: str
    interval_seconds: int | None = Field(default=None, ge=60, le=366 * 86_400)
    cron: str | None = None
    timezone: str | None = None  # default: the company's timezone
    task_template: TaskTemplate = Field(default_factory=TaskTemplate)
    enabled: bool = True

    @field_validator("cron")
    @classmethod
    def _cron(cls, v):
        return check_cron(v) if v is not None else v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v):
        return check_tz(v) if v is not None else v

    @model_validator(mode="after")
    def _exactly_one_timing(self):
        if (self.interval_seconds is None) == (self.cron is None):
            raise ValueError("provide exactly one of interval_seconds or cron")
        return self


class ScheduleUpdate(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    agent_id: str | None = None
    interval_seconds: int | None = Field(default=None, ge=60, le=366 * 86_400)
    cron: str | None = None
    timezone: str | None = None
    task_template: TaskTemplate | None = None
    enabled: bool | None = None

    @field_validator("name", "agent_id", "task_template", "enabled", "timezone")
    @classmethod
    def _not_null(cls, v):
        if v is None:
            raise ValueError("must not be null")
        return v

    @field_validator("cron")
    @classmethod
    def _cron(cls, v):
        return check_cron(v) if v is not None else v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v):
        return check_tz(v)
