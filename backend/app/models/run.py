from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.core.time import utcnow
from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime
from app.models.enums import CostKind, RunStatus


class TaskRun(TimestampMixin, Base):
    """One execution attempt of a task. Business status lives on the Task."""

    __tablename__ = "task_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_task_runs_idempotency_key"),
        Index("ix_task_runs_company_id", "company_id"),
        Index("ix_task_runs_company_status", "company_id", "status"),
        Index("ix_task_runs_task_id", "task_id"),
        Index("ix_task_runs_agent_id", "agent_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.QUEUED, nullable=False)
    runtime: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_execution_id: Mapped[str | None] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    queued_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    worker_id: Mapped[str | None] = mapped_column(String(80))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Extensions: why a cancel was requested, and the latest human-readable activity line.
    cancel_reason: Mapped[str | None] = mapped_column(String(40))
    activity_summary: Mapped[str | None] = mapped_column(String(300))
    activity_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    exit_reason: Mapped[str | None] = mapped_column(Text)
    error: Mapped[dict | None] = mapped_column(JSON)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    cost_kind: Mapped[str] = mapped_column(String(16), default=CostKind.UNKNOWN, nullable=False)
    reservation_id: Mapped[str | None] = mapped_column(ForeignKey("budget_reservations.id"))
    # M6 model routing: which provider/model this run was routed to and why (NULL = the runtime
    # chooses, e.g. scripted or Hermes-gateway runs).
    routing_reason: Mapped[str | None] = mapped_column(String(300))
    model_provider_id: Mapped[str | None] = mapped_column(String(36))
    model_used: Mapped[str | None] = mapped_column(String(160))
    # M17: which AI connection (``ai_connections.id``; no FK: deleting a connection keeps history).
    ai_connection_id: Mapped[str | None] = mapped_column(String(36))
    # M12: where the run's files lived and what became of them:
    # {kind, base, commit, merged, conflict}.
    workspace_ref: Mapped[dict | None] = mapped_column(JSON)


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_run_events_run_id"),
        Index("ix_run_events_company_id", "company_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    run_id: Mapped[str] = mapped_column(ForeignKey("task_runs.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class Job(TimestampMixin, Base):
    """Durable queue. Leasing is a conditional UPDATE (portable; no SKIP LOCKED needed)."""

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_jobs_dedupe_key"),
        Index("ix_jobs_status_run_after", "status", "run_after"),
        Index("ix_jobs_company_id", "company_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"))
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    run_after: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    leased_by: Mapped[str | None] = mapped_column(String(80))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(String(200))


class CompanyEvent(Base):
    """Live-update outbox (decision D10). Written in the same transaction as the change."""

    __tablename__ = "company_events"
    __table_args__ = (Index("ix_company_events_company_id_id", "company_id", "id"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
