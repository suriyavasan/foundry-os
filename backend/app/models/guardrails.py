"""M6 tables: guardrail rules and events, rate-limit hits, system state (emergency stop),
external expenses and threshold-alert marks."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.core.time import utcnow
from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime
from app.models.enums import GuardrailDecision, GuardrailScope


class GuardrailRule(TimestampMixin, Base):
    """A company policy row mapping an action (or a whole tier) to a decision for a scope.
    Human-editable only (owner/admin). Resolution: see ``app/guardrails/rules.py``."""

    __tablename__ = "guardrail_rules"
    __table_args__ = (Index("ix_guardrail_rules_company_id", "company_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    tier: Mapped[str] = mapped_column(String(32), nullable=False)
    # A specific action (e.g. ``email.send``); NULL = the whole tier.
    action: Mapped[str | None] = mapped_column(String(80))
    scope_type: Mapped[str] = mapped_column(String(12), default=GuardrailScope.ALL, nullable=False)
    scope_value: Mapped[str | None] = mapped_column(String(36))
    decision: Mapped[str] = mapped_column(
        String(24), default=GuardrailDecision.ALLOW, nullable=False
    )
    conditions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by_type: Mapped[str | None] = mapped_column(String(16))
    created_by_id: Mapped[str | None] = mapped_column(String(36))


class GuardrailEvent(Base):
    """One guardrail decision. Carries a payload digest only: never the payload, never a secret."""

    __tablename__ = "guardrail_events"
    __table_args__ = (
        Index("ix_guardrail_events_company_created", "company_id", "created_at"),
        Index("ix_guardrail_events_company_decision", "company_id", "decision"),
        Index("ix_guardrail_events_company_agent", "company_id", "agent_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(36))
    run_id: Mapped[str | None] = mapped_column(String(36))
    task_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    tier: Mapped[str] = mapped_column(String(32), nullable=False)
    # allow | peer_review | cofounder_approval | human_approval | deny | rate_limited
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    rule_id: Mapped[str | None] = mapped_column(String(36))
    approval_id: Mapped[str | None] = mapped_column(String(36))
    payload_digest: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)


class RateLimitHit(Base):
    """One counted action. Sliding windows are counts of recent rows (portable SQL)."""

    __tablename__ = "rate_limit_hits"
    __table_args__ = (
        Index("ix_rate_limit_hits_company_key", "company_id", "key", "occurred_at"),
        Index("ix_rate_limit_hits_agent_key", "company_id", "agent_id", "key", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(36))
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


class SystemState(Base):
    """Instance-wide key/value state that must survive restarts (the emergency stop)."""

    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class ExternalExpense(Base):
    """A cost outside model usage that a human recorded (domain, subscription, ...)."""

    __tablename__ = "external_expenses"
    __table_args__ = (Index("ix_external_expenses_company_incurred", "company_id", "incurred_on"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    vendor: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    amount_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    recurrence: Mapped[str] = mapped_column(String(16), default="one_time", nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"))
    incurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    created_by_type: Mapped[str | None] = mapped_column(String(16))
    created_by_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class BudgetAlertMark(Base):
    """Remembers that a threshold alert fired for (scope, period, threshold): resolving the alert
    must not make the same threshold fire again in the same period."""

    __tablename__ = "budget_alert_marks"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "scope",
            "scope_id",
            "period_key",
            "threshold",
            name="uq_budget_alert_marks_scope",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    # company_daily | company_monthly | agent_daily | agent_monthly
    scope: Mapped[str] = mapped_column(String(24), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(36), default="", nullable=False)
    period_key: Mapped[str] = mapped_column(String(12), nullable=False)
    threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
