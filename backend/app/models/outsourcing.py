"""M22 (backlog B3): outsourcing through HR (docs/specs/m22-human-work-and-outsourcing.md).

An ``OutsourceJob`` is a freelance job HR prepared: Foundry never posts, messages, hires or pays
on a marketplace by itself. A person approves the draft, does the outward-facing steps on the
marketplace's own site and records what happened here. Money is integer micro-USD; the paid amount
becomes a ``verified`` external expense (Foundry moves no money).

The title, brief, skills and notes are written by an AI employee (and edited by people): plain text.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.db.base import Base, TimestampMixin


class OutsourceStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    POSTED = "posted"
    HIRED = "hired"
    DELIVERED = "delivered"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    DECLINED = "declined"


OPEN_OUTSOURCE_STATUSES = (
    OutsourceStatus.DRAFT,
    OutsourceStatus.PENDING_APPROVAL,
    OutsourceStatus.APPROVED,
    OutsourceStatus.POSTED,
    OutsourceStatus.HIRED,
    OutsourceStatus.DELIVERED,
)
TERMINAL_OUTSOURCE_STATUSES = frozenset(
    {OutsourceStatus.CLOSED, OutsourceStatus.CANCELLED, OutsourceStatus.DECLINED}
)
MARKETPLACES = ("fiverr", "upwork", "other")


class OutsourceJob(TimestampMixin, Base):
    __tablename__ = "outsource_jobs"
    __table_args__ = (
        Index("ix_outsource_jobs_company_id", "company_id"),
        Index("ix_outsource_jobs_company_status", "company_id", "status"),
        Index("ix_outsource_jobs_task_id", "task_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    created_by_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    # The work this job serves (informational link; ``deliver`` hands the artifacts to it).
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    brief: Mapped[str] = mapped_column(Text, nullable=False)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    marketplace: Mapped[str] = mapped_column(String(16), nullable=False)
    budget_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default=OutsourceStatus.DRAFT, nullable=False)
    approval_id: Mapped[str | None] = mapped_column(ForeignKey("approvals.id"))
    listing_url: Mapped[str | None] = mapped_column(String(500))
    freelancer_name: Mapped[str | None] = mapped_column(String(200))
    freelancer_profile_url: Mapped[str | None] = mapped_column(String(500))
    agreed_price_micros: Mapped[int | None] = mapped_column(BigInteger)
    paid_micros: Mapped[int | None] = mapped_column(BigInteger)
    ledger_entry_id: Mapped[str | None] = mapped_column(ForeignKey("external_expenses.id"))
    delivered_artifact_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
