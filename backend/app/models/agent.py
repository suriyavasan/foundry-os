from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.core.time import utcnow
from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime
from app.models.enums import AgentKind, AgentStatus, RoleFamily


class Agent(TimestampMixin, Base):
    __tablename__ = "agents"
    __table_args__ = (
        Index("ix_agents_company_id", "company_id"),
        Index("ix_agents_company_status", "company_id", "status"),
        Index("ix_agents_department_id", "department_id"),
        Index("ix_agents_manager_agent_id", "manager_agent_id"),
        # At most one live (non-retired, non-rejected) founder per company.
        Index(
            "uq_agents_one_live_founder",
            "company_id",
            unique=True,
            sqlite_where=text(
                "kind = 'founder' AND status IN ('proposed','active','paused','retiring')"
            ),
            postgresql_where=text(
                "kind = 'founder' AND status IN ('proposed','active','paused','retiring')"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role_title: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default=AgentKind.SPECIALIST, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=AgentStatus.PROPOSED, nullable=False)
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"))
    manager_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    reports_to_human: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    responsibilities: Mapped[str | None] = mapped_column(Text)
    instructions: Mapped[str | None] = mapped_column(Text)
    model_provider: Mapped[str | None] = mapped_column(String(80))
    model_name: Mapped[str | None] = mapped_column(String(160))
    runtime: Mapped[str] = mapped_column(String(64), default="unassigned", nullable=False)
    runtime_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    tool_permissions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    monthly_budget_micros: Mapped[int | None] = mapped_column(BigInteger)
    daily_budget_micros: Mapped[int | None] = mapped_column(BigInteger)
    # M4: the role template this employee was created from (built-in or company-custom slug)
    # and the avatar family the canvas draws (founder -> founder, else generic by default).
    role_template: Mapped[str | None] = mapped_column(String(64))
    role_family: Mapped[str] = mapped_column(
        String(24), default=RoleFamily.GENERIC, server_default=RoleFamily.GENERIC, nullable=False
    )
    status_changed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
