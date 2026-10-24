from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, true
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.core.time import utcnow
from app.db.base import Base
from app.db.types import UTCDateTime
from app.models.enums import AutonomyMode

DEFAULT_HUMAN_APPROVAL_KINDS = ["external_purchase", "budget_increase"]
DEFAULT_HIRE_MONTHLY_BUDGET_MICROS = 5_000_000
DEFAULT_MAX_AGENT_MONTHLY_BUDGET_MICROS = 25_000_000


class CompanyPolicy(Base):
    """1:1 with Company. ``budget_lock_version`` is bumped at the start of every budget
    reservation transaction: the write serializes concurrent reservers (decision D9).
    ``updated_at`` is set explicitly by the policy service (the lock bump must not touch it)."""

    __tablename__ = "company_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False, unique=True)
    autonomy_mode: Mapped[str] = mapped_column(
        String(16), default=AutonomyMode.AUTONOMOUS, nullable=False
    )
    human_approval_required_for: Mapped[list[str]] = mapped_column(
        JSON, default=lambda: list(DEFAULT_HUMAN_APPROVAL_KINDS), nullable=False
    )
    daily_budget_micros: Mapped[int | None] = mapped_column(BigInteger)
    monthly_budget_micros: Mapped[int | None] = mapped_column(BigInteger)
    max_concurrent_runs: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    max_delegation_depth: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    max_tasks_per_planning_cycle: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    max_attempts_per_task: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    max_revisions: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    max_run_seconds: Mapped[int] = mapped_column(Integer, default=900, nullable=False)
    max_headcount: Mapped[int] = mapped_column(Integer, default=12, nullable=False)
    heartbeat_interval_seconds: Mapped[int] = mapped_column(Integer, default=900, nullable=False)
    default_run_reservation_micros: Mapped[int] = mapped_column(
        BigInteger, default=50_000, nullable=False
    )
    allowed_model_providers: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # M4 hiring policy. A hire is auto-activated only in autonomous mode with this flag set.
    auto_activate_hires: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    # Default monthly budget of a new hire when the hiring request names none ($5). NULL falls back
    # to the same constant: an agent can never be hired without a budget.
    hire_default_monthly_budget_micros: Mapped[int | None] = mapped_column(
        BigInteger, default=DEFAULT_HIRE_MONTHLY_BUDGET_MICROS
    )
    # M5 autonomy limits. Only humans can change them.
    # Hard cap on any monthly budget an agent-initiated action (a hire) may hand out ($25).
    max_agent_monthly_budget_micros: Mapped[int] = mapped_column(
        BigInteger,
        default=DEFAULT_MAX_AGENT_MONTHLY_BUDGET_MICROS,
        server_default=str(DEFAULT_MAX_AGENT_MONTHLY_BUDGET_MICROS),
        nullable=False,
    )
    # Planning runs (wake tasks + planning schedules) per agent and hour: a planning-storm brake.
    max_planning_runs_per_hour: Mapped[int] = mapped_column(
        Integer, default=6, server_default="6", nullable=False
    )
    # Open (non-terminal) tasks in the company; agent-created tasks beyond it are refused.
    max_open_tasks: Mapped[int] = mapped_column(
        Integer, default=200, server_default="200", nullable=False
    )
    # Failed runs within 10 minutes that auto-pause the company (0 = disabled).
    circuit_breaker_failures: Mapped[int] = mapped_column(
        Integer, default=8, server_default="8", nullable=False
    )
    # M3b: company default model (a ModelProvider id + model name) for agents without their own,
    # and the execution sandbox for code-running tools: auto | none | docker.
    default_model_provider_id: Mapped[str | None] = mapped_column(String(36))
    default_model_name: Mapped[str | None] = mapped_column(String(160))
    sandbox_backend: Mapped[str] = mapped_column(
        String(16), default="auto", server_default="auto", nullable=False
    )
    budget_lock_version: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
