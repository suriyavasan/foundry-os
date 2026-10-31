from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, ForeignKey, Index, String, false
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime


class QuestionStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    DEFAULTED = "defaulted"  # the deadline passed: the agent's default stands
    CANCELLED = "cancelled"


ANSWER_VIAS = ("web", "telegram", "discord", "whatsapp", "slack", "api")


class FounderQuestion(TimestampMixin, Base):
    """M18: a question an agent has for the Founder. Never open-ended: it carries the default the
    agent uses (and keeps going with, unless ``blocking``) and a deadline after which the default
    stands. ``question`` and ``options`` are agent-authored text; a human reads them as such."""

    __tablename__ = "founder_questions"
    __table_args__ = (
        Index("ix_founder_questions_company_id", "company_id"),
        Index("ix_founder_questions_company_status", "company_id", "status"),
        Index("ix_founder_questions_status_deadline", "status", "deadline"),
        Index("ix_founder_questions_task_id", "task_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    run_id: Mapped[str | None] = mapped_column(String(36))
    question: Mapped[str] = mapped_column(String(1000), nullable=False)
    options: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    default_answer: Mapped[str] = mapped_column(String(500), nullable=False)
    blocking: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default=QuestionStatus.OPEN, nullable=False)
    answer: Mapped[str | None] = mapped_column(String(500))
    answered_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    answered_via: Mapped[str | None] = mapped_column(String(16))
    deadline: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    answered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
