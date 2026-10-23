from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.core.time import utcnow
from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime
from app.models.enums import CostKind, ReservationStatus


class BudgetReservation(TimestampMixin, Base):
    """Enforcement ledger: held reservations + settled amounts bound what may still be spent."""

    __tablename__ = "budget_reservations"
    __table_args__ = (
        Index("ix_budget_reservations_company_day", "company_id", "period_day"),
        Index("ix_budget_reservations_company_month", "company_id", "period_month"),
        Index("ix_budget_reservations_agent_id", "agent_id"),
        Index("ix_budget_reservations_project_id", "project_id"),
        Index("ix_budget_reservations_task_id", "task_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    # Bound after the run row exists. Plain column (no FK): task_runs.reservation_id owns the FK.
    run_id: Mapped[str | None] = mapped_column(String(36))
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    amount_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=ReservationStatus.HELD, nullable=False)
    settled_micros: Mapped[int | None] = mapped_column(BigInteger)
    period_day: Mapped[date] = mapped_column(Date, nullable=False)
    period_month: Mapped[str] = mapped_column(String(7), nullable=False)


class UsageRecord(Base):
    """Reporting ledger. Idempotent on (company_id, dedupe_key)."""

    __tablename__ = "usage_records"
    __table_args__ = (
        UniqueConstraint("company_id", "dedupe_key", name="uq_usage_records_company_id"),
        Index("ix_usage_records_company_occurred", "company_id", "occurred_at"),
        Index("ix_usage_records_agent_id", "agent_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    run_id: Mapped[str | None] = mapped_column(String(36))
    runtime: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(160))
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    cached_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    cost_micros: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    cost_kind: Mapped[str] = mapped_column(String(16), default=CostKind.UNKNOWN, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
