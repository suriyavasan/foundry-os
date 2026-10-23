"""Skills (SKILL.md directories), their content-addressed blobs, per-agent assignments and
company-custom role templates (built-in templates are code: ``app/roles/builtin``)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.core.time import utcnow
from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime
from app.models.enums import ScanVerdict, SkillStatus, SkillTrust


class SkillBlob(Base):
    """A normalized skill directory stored once on disk at ``data/skills/blobs/<sha256>/``.
    Global (content-addressed, shared by companies), not tenant data."""

    __tablename__ = "skill_blobs"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class Skill(TimestampMixin, Base):
    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("company_id", "slug", name="uq_skills_company_id"),
        Index("ix_skills_company_id", "company_id"),
        Index("ix_skills_company_status", "company_id", "status"),
        Index("ix_skills_blob_sha256", "blob_sha256"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str | None] = mapped_column(String(80))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    version: Mapped[str | None] = mapped_column(String(40))
    author: Mapped[str | None] = mapped_column(String(200))
    license: Mapped[str | None] = mapped_column(String(80))
    source_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(400), nullable=False)
    blob_sha256: Mapped[str] = mapped_column(ForeignKey("skill_blobs.sha256"), nullable=False)
    trust: Mapped[str] = mapped_column(String(16), default=SkillTrust.COMMUNITY, nullable=False)
    scan_verdict: Mapped[str] = mapped_column(
        String(16), default=ScanVerdict.UNSCANNED, nullable=False
    )
    scan_report: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=SkillStatus.ACTIVE, nullable=False)
    requires_toolsets: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    required_env: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_by_type: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(String(36))


class AgentSkill(Base):
    __tablename__ = "agent_skills"
    __table_args__ = (
        UniqueConstraint("agent_id", "skill_id", name="uq_agent_skills_agent_id"),
        Index("ix_agent_skills_company_id", "company_id"),
        Index("ix_agent_skills_skill_id", "skill_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"), nullable=False)
    assigned_by_type: Mapped[str] = mapped_column(String(16), nullable=False)
    assigned_by_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class RoleTemplate(TimestampMixin, Base):
    """Company-custom role template (created by HR or a human for novel roles)."""

    __tablename__ = "role_templates"
    __table_args__ = (
        UniqueConstraint("company_id", "slug", name="uq_role_templates_company_id"),
        Index("ix_role_templates_company_id", "company_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    family: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    default_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    default_department: Mapped[str | None] = mapped_column(String(80))
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    default_toolsets: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    default_skill_slugs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    required_skill_tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_by_type: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(String(36))
