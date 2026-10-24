"""M10 Command Center: one row per human input. Slash commands are answered on the spot; free text
is a Founder's note (``note_id``) that the AI Co-founder answers. Notes stay the storage for text."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.core.time import utcnow
from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime


class ExchangeStatus(StrEnum):
    DONE = "done"  # a slash command, answered
    PENDING = "pending"
    PROCESSING = "processing"
    ANSWERED = "answered"
    FAILED = "failed"


class CommandExchange(TimestampMixin, Base):
    __tablename__ = "command_exchanges"
    __table_args__ = (
        Index("ix_command_exchanges_company_created", "company_id", "created_at"),
        Index("ix_command_exchanges_note_id", "note_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(36))
    input_kind: Mapped[str] = mapped_column(String(16), nullable=False)  # command | note
    # Commands keep their text here; a note exchange reads the note (no second copy).
    input: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=ExchangeStatus.PENDING, nullable=False)
    output: Mapped[dict | None] = mapped_column(JSON)
    note_id: Mapped[str | None] = mapped_column(ForeignKey("founder_notes.id"))
    run_id: Mapped[str | None] = mapped_column(String(36))
    actions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
