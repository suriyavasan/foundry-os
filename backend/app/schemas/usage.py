from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

GroupByLiteral = Literal["agent", "project", "model", "day"]


class CostByKind(BaseModel):
    """Micro-USD split by how trustworthy the figure is. Only ``verified`` is a real charge."""

    verified_micros: int = 0
    estimated_micros: int = 0
    unknown_micros: int = 0


class UsageRow(BaseModel):
    key: str | None
    label: str | None
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cost_micros: int
    by_cost_kind: CostByKind
    records: int


class UsageSummary(BaseModel):
    group_by: GroupByLiteral
    # ISO-8601 UTC instants; ``to`` is exclusive.
    from_: str = Field(alias="from")
    to: str
    items: list[UsageRow]
    totals: UsageRow

    model_config = ConfigDict(populate_by_name=True)
