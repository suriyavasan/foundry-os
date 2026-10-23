from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.core.time import utcnow
from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime
from app.models.enums import ScheduleKind


class Schedule(TimestampMixin, Base):
    __tablename__ = "schedules"
    __table_args__ = (
        Index("ix_schedules_company_id", "company_id"),
        Index("ix_schedules_enabled_next_run_at", "enabled", "next_run_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default=ScheduleKind.ROUTINE, nullable=False)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    interval_seconds: Mapped[int | None] = mapped_column(Integer)
    cron: Mapped[str | None] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    task_template: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_fired_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ScheduleFire(Base):
    __tablename__ = "schedule_fires"
    __table_args__ = (
        UniqueConstraint("schedule_id", "scheduled_for", name="uq_schedule_fires_schedule_id"),
        Index("ix_schedule_fires_company_id", "company_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    schedule_id: Mapped[str] = mapped_column(ForeignKey("schedules.id"), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
