"""M23 company intensity (docs/specs/m23-intensity.md)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IntensityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: int = Field(ge=1, le=5)
    # Required (true) for levels 4-5 when every enabled AI connection is a personal plan.
    confirm: bool = False


class Intensity(BaseModel):
    level: int
    name: str
    custom: bool
    # The values the current level writes into the policy, and what the policy holds right now.
    preset: dict[str, Any]
    effective: dict[str, Any]
    plan_only: bool
    warning: str | None = None
