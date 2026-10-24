"""Budget ledger: atomic reservations (enforcement) and usage records (reporting).

Concurrency (decision D9): ``reserve`` begins with
``UPDATE company_policies SET budget_lock_version = budget_lock_version + 1`` which takes the
write lock (SQLite) / a row lock (Postgres) held until the caller commits. Concurrent
reservers for the same company therefore run one at a time and each sees the others'
committed reservations. Callers must make ``acquire_budget_lock`` (or ``reserve``) the FIRST
statement of their transaction; on SQLite this matters because a read taken before the
write lock would pin an older snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session

from app.core.errors import BudgetExceeded, Conflict
from app.models.agent import Agent
from app.models.company import Company
from app.models.enums import CostKind, ReservationStatus
from app.models.ledger import BudgetReservation, UsageRecord
from app.models.policy import CompanyPolicy
from app.models.work import Project, Task


def acquire_budget_lock(db: Session, company_id: str) -> None:
    result = db.execute(
        update(CompanyPolicy)
        .where(CompanyPolicy.company_id == company_id)
        .values(budget_lock_version=CompanyPolicy.budget_lock_version + 1)
    )
    if result.rowcount != 1:
        raise Conflict("Company policy row is missing; cannot reserve budget")


@dataclass(frozen=True)
class Periods:
    day: date
    month: str  # YYYY-MM
    next_day_start: datetime  # UTC instant when the daily period rolls over
    next_month_start: datetime


def periods_for(now: datetime, timezone: str) -> Periods:
    tz = ZoneInfo(timezone)
    local = now.astimezone(tz)
    day = local.date()
    next_day = datetime(day.year, day.month, day.day, tzinfo=tz) + timedelta(days=1)
    next_day = datetime(next_day.year, next_day.month, next_day.day, tzinfo=tz)
    if local.month == 12:
        next_month = datetime(local.year + 1, 1, 1, tzinfo=tz)
    else:
        next_month = datetime(local.year, local.month + 1, 1, tzinfo=tz)
    return Periods(
        day=day,
        month=f"{local.year:04d}-{local.month:02d}",
        next_day_start=next_day.astimezone(UTC),
        next_month_start=next_month.astimezone(UTC),
    )


def _committed_expr():
    return func.coalesce(
        func.sum(
            case(
                (
                    BudgetReservation.status == ReservationStatus.HELD,
                    BudgetReservation.amount_micros,
                ),
                (
                    BudgetReservation.status == ReservationStatus.SETTLED,
                    func.coalesce(BudgetReservation.settled_micros, 0),
                ),
                else_=0,
            )
        ),
        0,
    )


def committed_micros(
    db: Session,
    company_id: str,
    *,
    agent_id: str | None = None,
    project_id: str | None = None,
    task_id: str | None = None,
    period_day: date | None = None,
    period_month: str | None = None,
) -> int:
    """Settled spend plus held reservations for a scope/period (reservations only)."""
    stmt = select(_committed_expr()).where(BudgetReservation.company_id == company_id)
    if agent_id:
        stmt = stmt.where(BudgetReservation.agent_id == agent_id)
    if project_id:
        stmt = stmt.where(BudgetReservation.project_id == project_id)
    if task_id:
        stmt = stmt.where(BudgetReservation.task_id == task_id)
    if period_day:
        stmt = stmt.where(BudgetReservation.period_day == period_day)
    if period_month:
        stmt = stmt.where(BudgetReservation.period_month == period_month)
    return int(db.scalar(stmt) or 0)


def _limit_checks(
    db: Session,
    company: Company,
    policy: CompanyPolicy,
    agent: Agent,
    task: Task,
    amount_micros: int,
    now: datetime,
) -> None:
    """Raise ``BudgetExceeded`` if adding ``amount_micros`` (on top of what is already committed,
    held reservations included) would break a task, project, agent or company limit."""
    company_id = company.id
    periods = periods_for(now, company.timezone)
    project = (
        db.scalar(
            select(Project).where(Project.id == task.project_id, Project.company_id == company_id)
        )
        if task.project_id
        else None
    )

    # (limit name, limit, committed, retry_after)
    checks: list[tuple[str, int | None, int, datetime | None]] = [
        (
            "task_budget",
            task.budget_micros,
            committed_micros(db, company_id, task_id=task.id)
            if task.budget_micros is not None
            else 0,
            None,
        ),
        (
            "project_budget",
            project.budget_micros if project else None,
            committed_micros(db, company_id, project_id=project.id)
            if project and project.budget_micros is not None
            else 0,
            None,
        ),
        (
            "agent_daily",
            agent.daily_budget_micros,
            committed_micros(db, company_id, agent_id=agent.id, period_day=periods.day)
            if agent.daily_budget_micros is not None
            else 0,
            periods.next_day_start,
        ),
        (
            "agent_monthly",
            agent.monthly_budget_micros,
            committed_micros(db, company_id, agent_id=agent.id, period_month=periods.month)
            if agent.monthly_budget_micros is not None
            else 0,
            periods.next_month_start,
        ),
        (
            "company_daily",
            policy.daily_budget_micros,
            committed_micros(db, company_id, period_day=periods.day)
            if policy.daily_budget_micros is not None
            else 0,
            periods.next_day_start,
        ),
        (
            "company_monthly",
            policy.monthly_budget_micros,
            committed_micros(db, company_id, period_month=periods.month)
            if policy.monthly_budget_micros is not None
            else 0,
            periods.next_month_start,
        ),
    ]
    for name, limit, committed, retry_after in checks:
        if limit is not None and committed + amount_micros > limit:
            raise BudgetExceeded(
                f"Budget limit {name} would be exceeded",
                details={
                    "limit": name,
                    "limit_micros": limit,
                    "committed_micros": committed,
                    "requested_micros": amount_micros,
                    "retry_after": retry_after.isoformat().replace("+00:00", "Z")
                    if retry_after
                    else None,
                },
            )


def reserve(
    db: Session,
    company_id: str,
    agent: Agent,
    task: Task,
    amount_micros: int,
    *,
    now: datetime,
) -> BudgetReservation:
    """Check every applicable limit and insert a ``held`` reservation, atomically with the
    caller's transaction. Raises ``BudgetExceeded`` (nothing is written beyond the lock bump)."""
    if amount_micros < 0:
        raise ValueError("amount_micros must be >= 0")
    acquire_budget_lock(db, company_id)  # FIRST statement of the reservation transaction
    company = db.scalar(select(Company).where(Company.id == company_id))
    policy = db.scalar(select(CompanyPolicy).where(CompanyPolicy.company_id == company_id))
    if company is None or policy is None:
        raise Conflict("Company or policy missing")
    _limit_checks(db, company, policy, agent, task, amount_micros, now)
    periods = periods_for(now, company.timezone)
    reservation = BudgetReservation(
        company_id=company_id,
        agent_id=agent.id,
        project_id=task.project_id,
        task_id=task.id,
        amount_micros=amount_micros,
        status=ReservationStatus.HELD,
        period_day=periods.day,
        period_month=periods.month,
    )
    db.add(reservation)
    db.flush()
    return reservation


def extend_reservation(
    db: Session, reservation: BudgetReservation, extra_micros: int, *, now: datetime
) -> BudgetReservation:
    """Grow a HELD reservation by ``extra_micros`` (mid-run budget checkpoint). Re-checks every
    limit for the extra amount; raises ``BudgetExceeded`` and changes nothing when it does not fit.
    The caller must hold the budget lock (``acquire_budget_lock`` as its first statement)."""
    if extra_micros <= 0:
        return reservation
    company = db.get(Company, reservation.company_id)
    policy = db.scalar(
        select(CompanyPolicy).where(CompanyPolicy.company_id == reservation.company_id)
    )
    agent = db.get(Agent, reservation.agent_id)
    task = db.get(Task, reservation.task_id) if reservation.task_id else None
    if company is None or policy is None or agent is None or task is None:
        raise Conflict("Reservation context is missing")
    _limit_checks(db, company, policy, agent, task, extra_micros, now)
    result = db.execute(
        update(BudgetReservation)
        .where(
            BudgetReservation.id == reservation.id,
            BudgetReservation.status == ReservationStatus.HELD,
        )
        .values(amount_micros=BudgetReservation.amount_micros + extra_micros)
    )
    if result.rowcount != 1:  # settled or released meanwhile
        raise Conflict("The reservation is no longer held")
    db.refresh(reservation)
    return reservation


def settle(db: Session, reservation: BudgetReservation, actual_micros: int) -> bool:
    """held -> settled at ``actual_micros``. Returns False if it was not held (idempotent)."""
    result = db.execute(
        update(BudgetReservation)
        .where(
            BudgetReservation.id == reservation.id,
            BudgetReservation.company_id == reservation.company_id,
            BudgetReservation.status == ReservationStatus.HELD,
        )
        .values(status=ReservationStatus.SETTLED, settled_micros=max(0, int(actual_micros)))
    )
    if result.rowcount == 1:
        db.refresh(reservation)
        return True
    return False


def release(db: Session, reservation: BudgetReservation) -> bool:
    """held -> released (nothing was spent). Idempotent."""
    result = db.execute(
        update(BudgetReservation)
        .where(
            BudgetReservation.id == reservation.id,
            BudgetReservation.company_id == reservation.company_id,
            BudgetReservation.status == ReservationStatus.HELD,
        )
        .values(status=ReservationStatus.RELEASED)
    )
    if result.rowcount == 1:
        db.refresh(reservation)
        return True
    return False


def record_usage(
    db: Session,
    *,
    company_id: str,
    agent_id: str,
    dedupe_key: str,
    project_id: str | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
    runtime: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    cost_micros: int = 0,
    cost_kind: CostKind | str = CostKind.UNKNOWN,
    occurred_at: datetime,
) -> UsageRecord:
    """Idempotent on (company_id, dedupe_key): a duplicate returns the existing row."""
    existing = db.scalar(
        select(UsageRecord).where(
            UsageRecord.company_id == company_id, UsageRecord.dedupe_key == dedupe_key
        )
    )
    if existing is not None:
        return existing
    record = UsageRecord(
        company_id=company_id,
        agent_id=agent_id,
        project_id=project_id,
        task_id=task_id,
        run_id=run_id,
        runtime=runtime,
        provider=provider,
        model=model,
        input_tokens=max(0, int(input_tokens or 0)),
        output_tokens=max(0, int(output_tokens or 0)),
        cached_tokens=max(0, int(cached_tokens or 0)),
        cost_micros=max(0, int(cost_micros or 0)),
        cost_kind=CostKind(cost_kind).value,
        dedupe_key=dedupe_key,
        occurred_at=occurred_at,
    )
    db.add(record)
    db.flush()
    return record
