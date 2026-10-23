from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime
from app.models.enums import ApprovalStatus, Risk


class Approval(TimestampMixin, Base):
    """Authorizes exactly one action: ``consume`` is single-use and bound to ``scope_hash``."""

    __tablename__ = "approvals"
    __table_args__ = (
        Index("ix_approvals_company_id", "company_id"),
        Index("ix_approvals_company_status", "company_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default=ApprovalStatus.PENDING, nullable=False)
    approver: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_by_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    amount_micros: Mapped[int | None] = mapped_column(BigInteger)
    vendor: Mapped[str | None] = mapped_column(String(200))
    recurrence: Mapped[str | None] = mapped_column(String(16))
    risk: Mapped[str] = mapped_column(String(16), default=Risk.LOW, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    scope: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    scope_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_by_type: Mapped[str | None] = mapped_column(String(16))
    decided_by_id: Mapped[str | None] = mapped_column(String(36))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    decision_note: Mapped[str | None] = mapped_column(Text)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Decision(TimestampMixin, Base):
    """Decision log for autonomy transparency."""

    __tablename__ = "decisions"
    __table_args__ = (Index("ix_decisions_company_id", "company_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    made_by_type: Mapped[str] = mapped_column(String(16), nullable=False)
    made_by_id: Mapped[str | None] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    related_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    related_project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"))
    context: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
