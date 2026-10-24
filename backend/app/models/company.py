from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.core.time import utcnow
from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime
from app.models.enums import CompanyStatus, MemberRole, MemberStatus


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(140), unique=True, nullable=False)
    mission: Mapped[str | None] = mapped_column(Text)
    vision: Mapped[str | None] = mapped_column(Text)
    strategic_goal: Mapped[str | None] = mapped_column(Text)
    constraints: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=CompanyStatus.ACTIVE, nullable=False)


class CompanyMember(Base):
    __tablename__ = "company_members"
    __table_args__ = (
        UniqueConstraint("company_id", "user_id", name="uq_company_members_company_user"),
        Index("ix_company_members_company_id", "company_id"),
        Index("ix_company_members_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default=MemberRole.VIEWER, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    # M7: a member is an org node. ``manager_ref`` is ``agent:<id>`` | ``member:<id>`` | NULL (the
    # Founder). A removed member keeps the row (history); it just stops being a member.
    title: Mapped[str | None] = mapped_column(String(120))
    manager_ref: Mapped[str | None] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(
        String(12), default=MemberStatus.ACTIVE, server_default=MemberStatus.ACTIVE, nullable=False
    )
    removed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
