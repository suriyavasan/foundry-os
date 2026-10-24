"""Finance reporting (docs/specs/m6 section 6): summary / breakdown / trend / runs / expenses.

Periods are calendar days in the company's timezone; ``from`` and ``to`` are inclusive
``YYYY-MM-DD`` dates and are echoed back with the timezone, so a client never has to guess which
instants a day meant. Money is integer micro-USD, always split by provenance: ``verified`` is a
charge, ``estimated`` is not, ``unknown`` has no price. Failed, cancelled and timed-out runs are
reported as *wasted* cost.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import BadRequest, Forbidden, NotFound
from app.core.principal import CompanyContext, require_human_admin
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.company import Company
from app.models.guardrails import ExternalExpense
from app.models.ledger import UsageRecord
from app.models.provider import ModelProvider
from app.models.run import TaskRun
from app.models.work import Project, Task
from app.schemas.finance import ExternalExpenseCreate
from app.services import audit, events, policy
from app.services.pagination import decode_cursor, encode_cursor

MAX_RANGE_DAYS = 366
WASTED_STATUSES = ("failed", "cancelled", "timed_out")
KINDS = ("verified", "estimated", "unknown")


@dataclass(frozen=True)
class Period:
    from_date: date
    to_date: date
    timezone: str
    start: datetime  # UTC instant of 00:00 of from_date in the company timezone
    end: datetime  # UTC instant of 00:00 of the day after to_date (exclusive)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def _readable(ctx: CompanyContext) -> None:
    if not ctx.principal.is_human:
        raise Forbidden("Agents cannot read finance reports")


def _parse_day(value: str, name: str) -> date:
    try:
        if len(value) != 10:
            raise ValueError
        return date.fromisoformat(value)
    except ValueError as exc:
        raise BadRequest(f"'{name}' must be a date like 2026-10-24 (company timezone)") from exc


def _company(db: Session, company_id: str) -> Company:
    company = db.get(Company, company_id)
    if company is None:
        raise NotFound("Company not found")
    return company


def resolve_period(
    db: Session,
    ctx: CompanyContext,
    from_: str | None,
    to: str | None,
    *,
    now: datetime | None = None,
) -> Period:
    company = _company(db, ctx.company_id)
    tz = ZoneInfo(company.timezone or "UTC")
    today = (now or utcnow()).astimezone(tz).date()
    start_day = _parse_day(from_, "from") if from_ else today.replace(day=1)
    end_day = _parse_day(to, "to") if to else today
    if end_day < start_day:
        raise BadRequest("'to' must not be before 'from'")
    if (end_day - start_day).days + 1 > MAX_RANGE_DAYS:
        raise BadRequest(f"The range may span at most {MAX_RANGE_DAYS} days")
    return Period(
        from_date=start_day,
        to_date=end_day,
        timezone=company.timezone or "UTC",
        start=datetime.combine(start_day, time.min, tzinfo=tz).astimezone(UTC),
        end=datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC),
    )


def _split() -> dict[str, int]:
    return {"verified_micros": 0, "estimated_micros": 0, "unknown_micros": 0}


def _usage_where(ctx: CompanyContext, p: Period) -> list[Any]:
    return [
        UsageRecord.company_id == ctx.company_id,
        UsageRecord.occurred_at >= p.start,
        UsageRecord.occurred_at < p.end,
    ]


# ---------------------------------------------------------------- summary


def _spend_by_kind(db: Session, where: list[Any]) -> dict[str, int]:
    out = _split()
    rows = db.execute(
        select(UsageRecord.cost_kind, func.coalesce(func.sum(UsageRecord.cost_micros), 0))
        .where(*where)
        .group_by(UsageRecord.cost_kind)
    ).all()
    for kind, total in rows:
        if kind in KINDS:
            out[f"{kind}_micros"] = int(total or 0)
    return out


def _budgets(db: Session, ctx: CompanyContext, now: datetime, tz: ZoneInfo) -> list[dict[str, Any]]:
    limits = policy.load_policy(db, ctx.company_id)
    local = now.astimezone(tz)
    day_start = datetime.combine(local.date(), time.min, tzinfo=tz).astimezone(UTC)
    month_start = datetime.combine(local.date().replace(day=1), time.min, tzinfo=tz).astimezone(UTC)
    out: list[dict[str, Any]] = []

    def row(scope: str, agent_id: str | None, period: str, limit: int | None, since: datetime):
        where = [
            UsageRecord.company_id == ctx.company_id,
            UsageRecord.occurred_at >= since,
            UsageRecord.occurred_at <= now,
        ]
        if agent_id:
            where.append(UsageRecord.agent_id == agent_id)
        spent = _spend_by_kind(db, where)
        out.append(
            {
                "scope": scope,
                "agent_id": agent_id,
                "period": period,
                "limit_micros": limit,
                "verified_micros": spent["verified_micros"],
                "estimated_micros": spent["estimated_micros"],
            }
        )

    row("company", None, "day", limits.daily_budget_micros, day_start)
    row("company", None, "month", limits.monthly_budget_micros, month_start)
    agents = db.scalars(
        select(Agent)
        .where(
            Agent.company_id == ctx.company_id,
            (Agent.daily_budget_micros.is_not(None)) | (Agent.monthly_budget_micros.is_not(None)),
        )
        .order_by(Agent.created_at, Agent.id)
    )
    for agent in agents:
        if agent.daily_budget_micros is not None:
            row("agent", agent.id, "day", agent.daily_budget_micros, day_start)
        if agent.monthly_budget_micros is not None:
            row("agent", agent.id, "month", agent.monthly_budget_micros, month_start)
    return out


def summary(
    db: Session,
    ctx: CompanyContext,
    from_: str | None,
    to: str | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    _readable(ctx)
    now = now or utcnow()
    p = resolve_period(db, ctx, from_, to, now=now)
    where = _usage_where(ctx, p)
    totals = {**_spend_by_kind(db, where), "input_tokens": 0, "output_tokens": 0, "runs": 0}
    tokens = db.execute(
        select(
            func.coalesce(func.sum(UsageRecord.input_tokens), 0),
            func.coalesce(func.sum(UsageRecord.output_tokens), 0),
            func.count(func.distinct(UsageRecord.run_id)),
        ).where(*where)
    ).one()
    totals["input_tokens"], totals["output_tokens"], totals["runs"] = (int(x or 0) for x in tokens)

    wasted_where = [
        *where,
        UsageRecord.run_id.in_(select(TaskRun.id).where(TaskRun.status.in_(WASTED_STATUSES))),
    ]
    wasted = {
        **_spend_by_kind(db, wasted_where),
        "runs": int(
            db.scalar(select(func.count(func.distinct(UsageRecord.run_id))).where(*wasted_where))
            or 0
        ),
    }
    return {
        "from": p.from_date.isoformat(),
        "to": p.to_date.isoformat(),
        "timezone": p.timezone,
        "totals": totals,
        "wasted": wasted,
        "external_expenses_micros": expenses_total(db, ctx, p),
        "budgets": _budgets(db, ctx, now, p.tz),
    }


# ---------------------------------------------------------------- breakdown / trend / runs


def breakdown(
    db: Session,
    ctx: CompanyContext,
    group_by: str,
    from_: str | None,
    to: str | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    _readable(ctx)
    p = resolve_period(db, ctx, from_, to, now=now)
    column = {
        "agent": UsageRecord.agent_id,
        "project": UsageRecord.project_id,
        "provider": UsageRecord.provider,
        "model": UsageRecord.model,
        "runtime": UsageRecord.runtime,
    }[group_by]
    where = _usage_where(ctx, p)
    rows: dict[str, dict[str, Any]] = {}

    def row(key: str | None) -> dict[str, Any]:
        k = key or "unassigned"
        return rows.setdefault(
            k,
            {
                "key": k,
                "label": None,
                **_split(),
                "input_tokens": 0,
                "output_tokens": 0,
                "runs": 0,
            },
        )

    for key, kind, cost, t_in, t_out in db.execute(
        select(
            column,
            UsageRecord.cost_kind,
            func.coalesce(func.sum(UsageRecord.cost_micros), 0),
            func.coalesce(func.sum(UsageRecord.input_tokens), 0),
            func.coalesce(func.sum(UsageRecord.output_tokens), 0),
        )
        .where(*where)
        .group_by(column, UsageRecord.cost_kind)
    ):
        r = row(key)
        if kind in KINDS:
            r[f"{kind}_micros"] += int(cost or 0)
        r["input_tokens"] += int(t_in or 0)
        r["output_tokens"] += int(t_out or 0)
    for key, runs in db.execute(
        select(column, func.count(func.distinct(UsageRecord.run_id))).where(*where).group_by(column)
    ):
        row(key)["runs"] = int(runs or 0)

    keys = [k for k in rows if k != "unassigned"]
    labels: dict[str, str] = {}
    if keys and group_by == "agent":
        labels = dict(
            db.execute(
                select(Agent.id, Agent.name).where(
                    Agent.company_id == ctx.company_id, Agent.id.in_(keys)
                )
            ).all()
        )
    elif keys and group_by == "project":
        labels = dict(
            db.execute(
                select(Project.id, Project.name).where(
                    Project.company_id == ctx.company_id, Project.id.in_(keys)
                )
            ).all()
        )
    elif keys and group_by == "provider":
        labels = dict(
            db.execute(
                select(ModelProvider.id, ModelProvider.name).where(
                    ModelProvider.company_id == ctx.company_id, ModelProvider.id.in_(keys)
                )
            ).all()
        )
    for key, r in rows.items():
        r["label"] = labels.get(key) or (key if group_by in ("model", "runtime") else None)
    items = sorted(
        rows.values(),
        key=lambda r: (
            -(r["verified_micros"] + r["estimated_micros"] + r["unknown_micros"]),
            r["key"],
        ),
    )
    return {
        "group_by": group_by,
        "from": p.from_date.isoformat(),
        "to": p.to_date.isoformat(),
        "items": items,
    }


def trend(
    db: Session,
    ctx: CompanyContext,
    from_: str | None,
    to: str | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    _readable(ctx)
    p = resolve_period(db, ctx, from_, to, now=now)
    days: dict[str, dict[str, int]] = {}
    cursor_day = p.from_date
    while cursor_day <= p.to_date:
        days[cursor_day.isoformat()] = _split()
        cursor_day += timedelta(days=1)
    for occurred_at, kind, cost in db.execute(
        select(UsageRecord.occurred_at, UsageRecord.cost_kind, UsageRecord.cost_micros).where(
            *_usage_where(ctx, p)
        )
    ):
        day = occurred_at.astimezone(p.tz).date().isoformat()
        if day in days and kind in KINDS:
            days[day][f"{kind}_micros"] += int(cost or 0)
    return {
        "from": p.from_date.isoformat(),
        "to": p.to_date.isoformat(),
        "points": [{"date": d, **split} for d, split in days.items()],
    }


def runs_page(
    db: Session,
    ctx: CompanyContext,
    from_: str | None,
    to: str | None,
    *,
    limit: int,
    cursor: str | None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    _readable(ctx)
    p = resolve_period(db, ctx, from_, to, now=now)
    stmt = (
        select(TaskRun, Task.title, UsageRecord.model, UsageRecord.provider)
        .join(Task, Task.id == TaskRun.task_id)
        .outerjoin(
            UsageRecord,
            (UsageRecord.run_id == TaskRun.id) & (UsageRecord.company_id == TaskRun.company_id),
        )
        .where(
            TaskRun.company_id == ctx.company_id,
            TaskRun.finished_at.is_not(None),
            TaskRun.finished_at >= p.start,
            TaskRun.finished_at < p.end,
        )
    )
    if cursor:
        c_at, c_id = decode_cursor(cursor)
        stmt = stmt.where(
            (TaskRun.finished_at < c_at) | ((TaskRun.finished_at == c_at) & (TaskRun.id < c_id))
        )
    result = db.execute(
        stmt.order_by(TaskRun.finished_at.desc(), TaskRun.id.desc()).limit(limit + 1)
    ).all()
    next_cursor = None
    if len(result) > limit:
        result = result[:limit]
        last = result[-1][0]
        next_cursor = encode_cursor(last.finished_at, last.id)
    provider_ids = {prov for _, _, _, prov in result if prov} | {
        r.model_provider_id for r, *_ in result if r.model_provider_id
    }
    names = (
        dict(
            db.execute(
                select(ModelProvider.id, ModelProvider.name).where(
                    ModelProvider.company_id == ctx.company_id, ModelProvider.id.in_(provider_ids)
                )
            ).all()
        )
        if provider_ids
        else {}
    )
    items = []
    for run, title, model, provider in result:
        provider_id = run.model_provider_id or provider
        items.append(
            {
                "run_id": run.id,
                "task_id": run.task_id,
                "task_title": title,
                "agent_id": run.agent_id,
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "cost_micros": run.cost_micros,
                "cost_kind": run.cost_kind,
                "model": run.model_used or model,
                "provider": names.get(provider_id, provider_id) if provider_id else None,
            }
        )
    return items, next_cursor


# ---------------------------------------------------------------- external expenses


def _add_months(day: date, months: int) -> date:
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def occurrences(expense: ExternalExpense, first: date, last: date) -> int:
    """How many times ``expense`` falls on a day in [first, last]."""
    start = expense.incurred_on
    if expense.recurrence == "one_time":
        return 1 if first <= start <= last else 0
    step = 1 if expense.recurrence == "monthly" else 12
    count, k = 0, 0
    while True:
        when = _add_months(start, k * step)
        if when > last:
            return count
        if when >= first:
            count += 1
        k += 1


def expenses_total(db: Session, ctx: CompanyContext, p: Period) -> int:
    rows = db.scalars(select(ExternalExpense).where(ExternalExpense.company_id == ctx.company_id))
    return sum(r.amount_micros * occurrences(r, p.from_date, p.to_date) for r in rows)


def list_expenses(
    db: Session,
    ctx: CompanyContext,
    from_: str | None,
    to: str | None,
    *,
    limit: int,
    cursor: str | None,
) -> tuple[list[ExternalExpense], str | None]:
    _readable(ctx)
    rows = list(
        db.scalars(
            select(ExternalExpense)
            .where(ExternalExpense.company_id == ctx.company_id)
            .order_by(ExternalExpense.incurred_on.desc(), ExternalExpense.id.desc())
        )
    )
    if from_ or to:
        p = resolve_period(db, ctx, from_, to)
        rows = [r for r in rows if occurrences(r, p.from_date, p.to_date) > 0]
    try:
        offset = int(cursor) if cursor else 0
    except ValueError as exc:
        raise BadRequest("Invalid cursor") from exc
    page = rows[offset : offset + limit]
    return page, str(offset + limit) if offset + limit < len(rows) else None


def create_expense(
    db: Session, ctx: CompanyContext, data: ExternalExpenseCreate
) -> ExternalExpense:
    require_human_admin(ctx, "record expenses")
    if data.project_id:
        found = db.scalar(
            select(Project.id).where(
                Project.id == data.project_id, Project.company_id == ctx.company_id
            )
        )
        if found is None:
            raise NotFound("Project not found")
    row = ExternalExpense(
        company_id=ctx.company_id,
        vendor=data.vendor,
        description=data.description,
        amount_micros=data.amount_micros,
        recurrence=data.recurrence,
        project_id=data.project_id,
        incurred_on=data.incurred_on,
        created_by_type=ctx.principal.type.value,
        created_by_id=ctx.principal.id,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="expense.created",
        target_type="expense",
        target_id=row.id,
        summary=f"Recorded external expense: {row.vendor}",
        data={
            "vendor": row.vendor,
            "amount_micros": row.amount_micros,
            "recurrence": row.recurrence,
            "incurred_on": row.incurred_on.isoformat(),
        },
    )
    events.emit(
        db,
        ctx.company_id,
        "expense.created",
        {"expense_id": row.id, "vendor": row.vendor, "amount_micros": row.amount_micros},
    )
    db.commit()
    return row


__all__ = [
    "Period",
    "breakdown",
    "create_expense",
    "list_expenses",
    "occurrences",
    "resolve_period",
    "runs_page",
    "summary",
    "trend",
]
