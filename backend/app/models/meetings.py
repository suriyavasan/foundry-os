"""M21: agent meetings (docs/specs/m21-agent-meetings.md).

A meeting is a short, bounded discussion between 2-4 agents (the organizer plus 1-3 invited). Each
speaking turn is an ordinary ``meeting`` task assigned to the speaker (``tasks.meeting_id``), so
dispatch, routing, slots and budgets apply as for any other work. The graph that sequences the
turns lives in ``app.engine.graph.meeting_graph`` (LangGraph thread ``meeting:<id>``).

Everything in ``meeting_messages`` and the free text of a meeting is written by AI employees: it is
data, never an instruction from the Founder."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.core.time import utcnow
from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime


class MeetingStatus(StrEnum):
    SCHEDULED = "scheduled"
    RUNNING = "running"
    CONCLUDED = "concluded"
    CANCELLED = "cancelled"
    FAILED = "failed"


TERMINAL_MEETING_STATUSES = frozenset(
    {MeetingStatus.CONCLUDED, MeetingStatus.CANCELLED, MeetingStatus.FAILED}
)


class ParticipantRole(StrEnum):
    ORGANIZER = "organizer"
    PARTICIPANT = "participant"


class Meeting(TimestampMixin, Base):
    __tablename__ = "meetings"
    __table_args__ = (
        Index("ix_meetings_company_id", "company_id"),
        Index("ix_meetings_company_status", "company_id", "status"),
        Index("ix_meetings_status_start_at", "status", "start_at"),
        Index("ix_meetings_organizer_agent_id", "organizer_agent_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    organizer_agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    agenda: Mapped[str] = mapped_column(String(2000), nullable=False)
    expected_outcome: Mapped[str] = mapped_column(String(500), nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"))
    # The task that prompted the meeting (informational; deliberately no foreign key: tasks point
    # at meetings through tasks.meeting_id, and the two must not reference each other).
    task_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16), default=MeetingStatus.SCHEDULED, nullable=False)
    start_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    round: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    max_rounds: Mapped[int] = mapped_column(Integer, default=2, server_default="2", nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    # {decisions: [{title, rationale}], action_items: [{id, title, description, owner_agent_id,
    #  accepted, task_id?, refused?}]}
    outcome: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Why a meeting was cancelled or failed.
    cancel_reason: Mapped[str | None] = mapped_column(String(500))


class MeetingParticipant(Base):
    __tablename__ = "meeting_participants"
    __table_args__ = (Index("ix_meeting_participants_company_id", "company_id"),)

    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    # Invitation order: the organizer is 0; turns follow this order within a round.
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    joined: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    passes: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)


class MeetingMessage(Base):
    __tablename__ = "meeting_messages"
    __table_args__ = (
        Index("ix_meeting_messages_company_id", "company_id"),
        Index("ix_meeting_messages_meeting_id", "meeting_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    # "turn" (a normal contribution) | "synthesis" (the organizer's final turn)
    kind: Mapped[str] = mapped_column(
        String(16), default="turn", server_default="turn", nullable=False
    )
    body: Mapped[str] = mapped_column(String(4000), nullable=False)
    # {proposed: [{id, title, description, owner_agent_id}], accepted: [ids], declined: [ids],
    #  pass: bool}
    proposals: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(36))
    run_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
