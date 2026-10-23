from __future__ import annotations

from pydantic import Field, field_validator

from app.schemas.common import ApiModel, RequestModel, Timestamp


class Department(ApiModel):
    id: str
    company_id: str
    name: str
    description: str | None
    lead_agent_id: str | None
    agent_count: int
    created_at: Timestamp
    updated_at: Timestamp


class DepartmentCreate(RequestModel):
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=5_000)
    lead_agent_id: str | None = None


class DepartmentUpdate(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=5_000)
    lead_agent_id: str | None = None

    @field_validator("name")
    @classmethod
    def _name_not_null(cls, v):
        if v is None:
            raise ValueError("must not be null")
        return v
