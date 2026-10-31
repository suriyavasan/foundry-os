"""Finance reporting schemas (Milestone 6). Names match web/src/api/contract.check.ts."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ApiModel, RequestModel, Timestamp

FinanceGroupByLiteral = Literal["agent", "project", "provider", "model", "runtime"]
RecurrenceLiteral = Literal["one_time", "monthly", "yearly"]
CostKindLiteral = Literal["verified", "estimated", "unknown"]
RunStatusLiteral = Literal[
    "queued", "starting", "running", "succeeded", "failed", "cancelled", "timed_out"
]


class CostSplit(BaseModel):
    """Micro-USD by provenance: only ``verified`` is a charge; ``unknown`` is unpriced."""

    verified_micros: int
    estimated_micros: int
    unknown_micros: int


class FinanceTotals(CostSplit):
    input_tokens: int
    output_tokens: int
    runs: int


class FinanceWasted(CostSplit):
    runs: int


class BudgetStatus(BaseModel):
    scope: Literal["company", "agent"]
    agent_id: str | None
    period: Literal["day", "month"]
    limit_micros: int | None
    verified_micros: int
    estimated_micros: int


class FinancePurchases(BaseModel):
    """Agent purchases (M15), kept apart from model spend: a tool bought for $20 never uses up a
    model budget. ``held_micros`` is committed (approved, not yet charged); only
    ``settled_verified_micros`` is a charge."""

    period: str  # the company-timezone month this block describes (YYYY-MM)
    settled_verified_micros: int
    held_micros: int
    budget_micros: int | None


class FinanceSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    timezone: str
    totals: FinanceTotals
    wasted: FinanceWasted
    external_expenses_micros: int
    budgets: list[BudgetStatus]
    purchases: FinancePurchases


class FinanceBreakdownRow(CostSplit):
    key: str
    label: str | None
    input_tokens: int
    output_tokens: int
    runs: int


class FinanceBreakdown(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    group_by: FinanceGroupByLiteral
    from_: str = Field(alias="from")
    to: str
    items: list[FinanceBreakdownRow]


class FinanceTrendPoint(CostSplit):
    date: str


class FinanceTrend(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    points: list[FinanceTrendPoint]


class FinanceRun(ApiModel):
    run_id: str
    task_id: str
    task_title: str | None
    agent_id: str | None
    status: RunStatusLiteral
    started_at: Timestamp | None
    finished_at: Timestamp | None
    cost_micros: int | None
    cost_kind: CostKindLiteral
    model: str | None
    provider: str | None
    # M17: the AI connection the run was routed to (null for pinned / unrouted runs)
    ai_connection_id: str | None = None
    ai_connection: str | None = None


class ExternalExpense(ApiModel):
    id: str
    company_id: str
    vendor: str
    description: str | None
    amount_micros: int
    recurrence: RecurrenceLiteral
    project_id: str | None
    incurred_on: date
    created_by_type: Literal["human", "agent", "system"] | None = None
    created_by_id: str | None = None
    created_at: Timestamp
    # M15: where the amount comes from (``human`` entry, ``card`` transaction, ``provider_action``)
    # and how sure it is (``verified`` for a real charge; a card or confirmed amount always is).
    source: Literal["human", "card", "provider_action", "outsourcing"] = "human"
    cost_kind: Literal["verified", "estimated", "unknown"] = "verified"
    purchase_request_id: str | None = None


class ExternalExpenseCreate(RequestModel):
    vendor: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    amount_micros: int = Field(ge=0, le=10**15)
    recurrence: RecurrenceLiteral = "one_time"
    project_id: str | None = None
    incurred_on: date


class CliUsageRow(CostSplit):
    runtime: str
    runs: int
    succeeded: int
    failed: int
    input_tokens: int
    output_tokens: int
    duration_seconds: int
    subscription_runs: int
    agents: list[str]


class CliUsage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    timezone: str
    items: list[CliUsageRow]
