"""M5 tables: the Founder's notes, company memory, alerts and the wake queue."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.core.time import utcnow
from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime
from app.models.enums import AlertSeverity, AlertStatus, MemoryKind, MemoryStatus, NoteStatus


class FounderNote(TimestampMixin, Base):
    """A thought the human Founder wrote. Triaged by the AI Co-founder (``triage`` json:
    ``{category, summary, actions: [{type, ref_id, label}], confidence}``)."""

    __tablename__ = "founder_notes"
    __table_args__ = (
        Index("ix_founder_notes_company_id", "company_id"),
        Index("ix_founder_notes_company_status", "company_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    author_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=NoteStatus.NEW, nullable=False)
    triage: Mapped[dict | None] = mapped_column(JSON)
    triaged_by_run_id: Mapped[str | None] = mapped_column(String(36))
    triaged_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class NoteReply(Base):
    __tablename__ = "note_replies"
    __table_args__ = (
        Index("ix_note_replies_company_id", "company_id"),
        Index("ix_note_replies_note_id", "note_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    note_id: Mapped[str] = mapped_column(ForeignKey("founder_notes.id"), nullable=False)
    author_type: Mapped[str] = mapped_column(String(16), nullable=False)  # human | agent
    author_id: Mapped[str | None] = mapped_column(String(36))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class MemoryEntry(TimestampMixin, Base):
    """Company memory with provenance. Only ``verified`` entries reach agents' context."""

    __tablename__ = "memory_entries"
    __table_args__ = (
        Index("ix_memory_entries_company_id", "company_id"),
        Index("ix_memory_entries_company_status", "company_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default=MemoryKind.FACT, nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=MemoryStatus.PROPOSED, nullable=False)
    # {type: note|task|run|artifact|agent|human, id, task_id?, label?}
    source: Mapped[dict | None] = mapped_column(JSON)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Lessons: role families / tags / agent ids they apply to ([] = everyone).
    applies_to: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    proposed_by_type: Mapped[str | None] = mapped_column(String(16))
    proposed_by_id: Mapped[str | None] = mapped_column(String(36))
    verified_by_type: Mapped[str | None] = mapped_column(String(16))
    verified_by_id: Mapped[str | None] = mapped_column(String(36))
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    superseded_by_id: Mapped[str | None] = mapped_column(String(36))


class Alert(TimestampMixin, Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_company_id", "company_id"),
        Index("ix_alerts_company_status", "company_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default=AlertSeverity.WARNING, nullable=False)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=AlertStatus.OPEN, nullable=False)
    related_type: Mapped[str | None] = mapped_column(String(24))
    related_id: Mapped[str | None] = mapped_column(String(36))
    # At most one OPEN alert per dedupe key (a repeating condition raises once).
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolved_by_type: Mapped[str | None] = mapped_column(String(16))
    resolved_by_id: Mapped[str | None] = mapped_column(String(36))


class WakeRequest(Base):
    """A pending wake-up of an agent. The scheduler turns due requests into planning tasks:
    debounced (``not_before``), coalesced (one open wake task per agent) and rate limited."""

    __tablename__ = "wake_requests"
    __table_args__ = (
        Index("ix_wake_requests_company_id", "company_id"),
        Index("ix_wake_requests_not_before", "not_before"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    # [{trigger, text, ref_type?, ref_id?}] - why the agent is being woken, newest last.
    reasons: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    not_before: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
