from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.base import Base, TimestampMixin
from app.models.enums import ArtifactKind, ArtifactStatus


class Artifact(TimestampMixin, Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        Index("ix_artifacts_company_id", "company_id"),
        Index("ix_artifacts_task_id", "task_id"),
        Index("ix_artifacts_project_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("task_runs.id"))
    created_by_type: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default=ArtifactKind.OTHER, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=ArtifactStatus.DRAFT, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class ArtifactVersion(TimestampMixin, Base):
    __tablename__ = "artifact_versions"
    __table_args__ = (
        UniqueConstraint("artifact_id", "version", name="uq_artifact_versions_artifact_id"),
        Index("ix_artifact_versions_company_id", "company_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_type: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(String(36))
    note: Mapped[str | None] = mapped_column(Text)
