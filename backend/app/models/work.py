from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.core.time import utcnow
from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime
from app.models.enums import (
    GoalStatus,
    Priority,
    ProjectStatus,
    TaskKind,
    TaskStatus,
)


class Goal(TimestampMixin, Base):
    __tablename__ = "goals"
    __table_args__ = (Index("ix_goals_company_id", "company_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=GoalStatus.PROPOSED, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default=Priority.NORMAL, nullable=False)
    owner_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    target_date: Mapped[date | None] = mapped_column(Date)
    created_by_type: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(String(36))


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_company_id", "company_id"),
        Index("ix_projects_goal_id", "goal_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    goal_id: Mapped[str | None] = mapped_column(ForeignKey("goals.id"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=ProjectStatus.PLANNED, nullable=False)
    lead_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    budget_micros: Mapped[int | None] = mapped_column(BigInteger)
    created_by_type: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(String(36))


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("company_id", "idempotency_key", name="uq_tasks_company_idempotency_key"),
        Index("ix_tasks_company_id", "company_id"),
        Index("ix_tasks_company_status", "company_id", "status"),
        Index("ix_tasks_assignee_agent_id", "assignee_agent_id"),
        Index("ix_tasks_assignee_member_id", "assignee_member_id"),
        Index("ix_tasks_project_id", "project_id"),
        Index("ix_tasks_parent_task_id", "parent_task_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"))
    parent_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    acceptance_criteria: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default=TaskKind.WORK, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default=TaskStatus.BACKLOG, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default=Priority.NORMAL, nullable=False)
    assignee_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    reviewer_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    # M7: a human colleague as assignee / reviewer (CompanyMember id). Mutually exclusive with the
    # agent columns. Human-assigned tasks are never dispatched to a runtime.
    assignee_member_id: Mapped[str | None] = mapped_column(ForeignKey("company_members.id"))
    reviewer_member_id: Mapped[str | None] = mapped_column(ForeignKey("company_members.id"))
    required_skills: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    budget_micros: Mapped[int | None] = mapped_column(BigInteger)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    revision_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delegation_depth: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    result_summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[dict | None] = mapped_column(JSON)
    input_refs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    output_refs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_by_type: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(String(36))
    # Extensions beyond the spec table: which run created it (per-run task cap) and the last
    # reviewer feedback (shown to the assignee on a revision).
    created_by_run_id: Mapped[str | None] = mapped_column(String(36))
    review_note: Mapped[str | None] = mapped_column(Text)
    # M4: why a backlog task is parked, e.g. "waiting_for_hire" (no employee has its skills yet).
    blocked_reason: Mapped[str | None] = mapped_column(String(80))
    idempotency_key: Mapped[str | None] = mapped_column(String(200))

    @property
    def assignee_ref(self) -> str | None:
        """``agent:<id>`` | ``member:<id>`` | None (M7)."""
        if self.assignee_agent_id:
            return f"agent:{self.assignee_agent_id}"
        return f"member:{self.assignee_member_id}" if self.assignee_member_id else None

    @property
    def reviewer_ref(self) -> str | None:
        if self.reviewer_agent_id:
            return f"agent:{self.reviewer_agent_id}"
        return f"member:{self.reviewer_member_id}" if self.reviewer_member_id else None


class TaskDependency(Base):
    __tablename__ = "task_dependencies"
    __table_args__ = (
        UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependencies_task_id"),
        Index("ix_task_dependencies_company_id", "company_id"),
        Index("ix_task_dependencies_depends_on_task_id", "depends_on_task_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    depends_on_task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
