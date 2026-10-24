"""M10 reflections: the AI Co-founder's observations, the Founder's reactions and the company
standards a human adopts from them (docs/specs/m10 section 4)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.core.time import utcnow
from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime


class ReflectionKind(StrEnum):
    OBSERVATION = "observation"
    STANDARD_PROPOSAL = "standard_proposal"
    RETRO = "retro"
    RISK = "risk"
    PRAISE = "praise"


class ReflectionStatus(StrEnum):
    OPEN = "open"
    ENDORSED = "endorsed"
    REVISED = "revised"
    REJECTED = "rejected"
    ADOPTED = "adopted"
    ARCHIVED = "archived"


class ReflectionReaction(StrEnum):
    ENDORSE = "endorse"
    CHALLENGE = "challenge"
    REFINE = "refine"
    COMMENT = "comment"


class StandardStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


class Reflection(TimestampMixin, Base):
    __tablename__ = "reflections"
    __table_args__ = (
        Index("ix_reflections_company_id", "company_id"),
        Index("ix_reflections_company_status", "company_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    author_agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(24), default=ReflectionKind.OBSERVATION, nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)  # markdown, at most 6 KB
    # [{type: task|artifact|run|project|agent, id, label}] - every id exists in this company.
    evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    proposed_standard: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), default=ReflectionStatus.OPEN, nullable=False)
    standard_id: Mapped[str | None] = mapped_column(String(36))  # set once a human adopts it


class ReflectionResponse(Base):
    __tablename__ = "reflection_responses"
    __table_args__ = (
        Index("ix_reflection_responses_company_id", "company_id"),
        Index("ix_reflection_responses_reflection_id", "reflection_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    reflection_id: Mapped[str] = mapped_column(ForeignKey("reflections.id"), nullable=False)
    author_type: Mapped[str] = mapped_column(String(16), nullable=False)  # human | agent
    author_id: Mapped[str | None] = mapped_column(String(36))
    reaction: Mapped[str] = mapped_column(
        String(16), default=ReflectionReaction.COMMENT, nullable=False
    )
    body: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class CompanyStandard(TimestampMixin, Base):
    """A rule the Founder (a human) set for the whole company. Injected into every agent's context."""

    __tablename__ = "company_standards"
    __table_args__ = (
        Index("ix_company_standards_company_id", "company_id"),
        Index("ix_company_standards_company_status", "company_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    statement: Mapped[str] = mapped_column(String(500), nullable=False)
    source_reflection_id: Mapped[str | None] = mapped_column(String(36))
    adopted_by_user_id: Mapped[str | None] = mapped_column(String(36))
    adopted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=StandardStatus.ACTIVE, nullable=False)
    # Role families / tags it applies to ([] = every agent).
    applies_to: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
