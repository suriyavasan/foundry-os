"""Finance reporting (owner API). Periods are company-timezone calendar days, inclusive."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CompanyCtx, DbSession
from app.schemas.common import Page
from app.schemas.finance import (
    CliUsage,
    ExternalExpense,
    ExternalExpenseCreate,
    FinanceBreakdown,
    FinanceGroupByLiteral,
    FinanceRun,
    FinanceSummary,
    FinanceTrend,
)
from app.services import finance
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}/finance", tags=["finance"])

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
Day = Annotated[str | None, Query(max_length=10, description="YYYY-MM-DD, company timezone")]


@router.get("/summary", response_model=FinanceSummary, response_model_by_alias=True)
def summary(
    ctx: CompanyCtx,
    db: DbSession,
    from_: Annotated[str | None, Query(alias="from", max_length=10)] = None,
    to: Day = None,
):
    return finance.summary(db, ctx, from_, to)


@router.get("/breakdown", response_model=FinanceBreakdown, response_model_by_alias=True)
def breakdown(
    ctx: CompanyCtx,
    db: DbSession,
    from_: Annotated[str | None, Query(alias="from", max_length=10)] = None,
    to: Day = None,
    group_by: FinanceGroupByLiteral = "agent",
):
    return finance.breakdown(db, ctx, group_by, from_, to)


@router.get("/trend", response_model=FinanceTrend, response_model_by_alias=True)
def trend(
    ctx: CompanyCtx,
    db: DbSession,
    from_: Annotated[str | None, Query(alias="from", max_length=10)] = None,
    to: Day = None,
):
    return finance.trend(db, ctx, from_, to)


@router.get("/cli-usage", response_model=CliUsage, response_model_by_alias=True)
def cli_usage(
    ctx: CompanyCtx,
    db: DbSession,
    from_: Annotated[str | None, Query(alias="from", max_length=10)] = None,
    to: Day = None,
):
    return finance.cli_usage(db, ctx, from_, to)


@router.get("/runs", response_model=Page[FinanceRun])
def runs(
    ctx: CompanyCtx,
    db: DbSession,
    from_: Annotated[str | None, Query(alias="from", max_length=10)] = None,
    to: Day = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, next_cursor = finance.runs_page(db, ctx, from_, to, limit=limit, cursor=cursor)
    return {"items": items, "next_cursor": next_cursor}


@router.get("/expenses", response_model=Page[ExternalExpense])
def list_expenses(
    ctx: CompanyCtx,
    db: DbSession,
    from_: Annotated[str | None, Query(alias="from", max_length=10)] = None,
    to: Day = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, next_cursor = finance.list_expenses(db, ctx, from_, to, limit=limit, cursor=cursor)
    return {"items": items, "next_cursor": next_cursor}


@router.post("/expenses", response_model=ExternalExpense, status_code=201)
def create_expense(body: ExternalExpenseCreate, ctx: CompanyCtx, db: DbSession):
    return finance.create_expense(db, ctx, body)
