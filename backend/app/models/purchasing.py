"""M15 tables: card accounts, agent cards, purchase requests, card transactions, subscriptions.

Nothing here stores a card number. The adapter interface has no method that returns a PAN or CVC,
and these tables have no column that could hold one: a card is identified by its provider id and
described by ``last4`` / ``brand`` / expiry (non-sensitive without the PAN). API keys live in the
connected-accounts vault; a card account only references the connected account.

``agent_cards.purchase_request_id`` / ``subscription_id`` and ``purchase_requests.card_id`` /
``subscription_id`` point at each other, so they are plain indexed ids (no foreign key) to keep the
migration order simple and portable; the services keep them consistent.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
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
from app.models.enums import (
    CardAccountStatus,
    CardKind,
    CardMode,
    CardStatus,
    CardTxnMatch,
    CardTxnSource,
    PurchaseStatus,
    Recurrence,
    SubscriptionStatus,
)


class CardAccount(TimestampMixin, Base):
    """The company's connection to one card provider (or its own manual card). One default per
    company (enforced by the service)."""

    __tablename__ = "card_accounts"
    __table_args__ = (Index("ix_card_accounts_company_status", "company_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # CardProviderId
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=CardAccountStatus.SETUP, nullable=False)
    mode: Mapped[str] = mapped_column(String(8), default=CardMode.LIVE, nullable=False)
    # NULL for the manual provider (nothing to authenticate).
    connected_account_id: Mapped[str | None] = mapped_column(ForeignKey("connected_accounts.id"))
    # Stripe cardholder id / Mercury account id.
    external_ref: Mapped[str | None] = mapped_column(String(120))
    # Manual mode: only a brand and the LAST FOUR digits, never a full number.
    manual_brand: Mapped[str | None] = mapped_column(String(24))
    manual_last4: Mapped[str | None] = mapped_column(String(4))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_balance_micros: Mapped[int | None] = mapped_column(BigInteger)
    balance_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    sync_cursor: Mapped[str | None] = mapped_column(String(200))
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_by_type: Mapped[str | None] = mapped_column(String(16))
    created_by_id: Mapped[str | None] = mapped_column(String(36))


class AgentCard(TimestampMixin, Base):
    """A card issued for exactly one purchase or subscription. ``provider_card_id`` is the
    provider's id; the number itself is only ever seen by a human on the provider's own surface."""

    __tablename__ = "agent_cards"
    __table_args__ = (
        UniqueConstraint(
            "card_account_id", "provider_card_id", name="uq_agent_cards_provider_card"
        ),
        Index("ix_agent_cards_company_status", "company_id", "status"),
        Index("ix_agent_cards_purchase_request_id", "purchase_request_id"),
        Index("ix_agent_cards_subscription_id", "subscription_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    card_account_id: Mapped[str] = mapped_column(ForeignKey("card_accounts.id"), nullable=False)
    provider_card_id: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default=CardKind.SINGLE_USE, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=CardStatus.ACTIVE, nullable=False)
    purchase_request_id: Mapped[str | None] = mapped_column(String(36))
    subscription_id: Mapped[str | None] = mapped_column(String(36))
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    last4: Mapped[str] = mapped_column(String(4), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(24))
    exp_month: Mapped[int | None] = mapped_column(Integer)
    exp_year: Mapped[int | None] = mapped_column(Integer)
    limit_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # per_authorization | monthly | yearly | all_time
    limit_interval: Mapped[str] = mapped_column(String(24), nullable=False)
    merchant_lock: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    frozen_reason: Mapped[str | None] = mapped_column(String(64))
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class PurchaseRequest(TimestampMixin, Base):
    """An agent's request to buy something, with its decision, approval, card and settlement.
    ``amount_micros`` is USD micros; ``held_micros`` is a hold (never presented as a charge) until
    the settlement replaces it with ``settled_micros`` (``cost_kind`` verified)."""

    __tablename__ = "purchase_requests"
    __table_args__ = (
        UniqueConstraint("company_id", "dedupe_key", name="uq_purchase_requests_dedupe"),
        Index("ix_purchase_requests_company_status", "company_id", "status"),
        Index("ix_purchase_requests_company_agent_month", "company_id", "agent_id", "period_month"),
        Index("ix_purchase_requests_company_vendor", "company_id", "vendor_domain"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"))
    # Untrusted agent text: rendered as text, never as an auto-opening link.
    item: Mapped[str] = mapped_column(String(200), nullable=False)
    vendor: Mapped[str] = mapped_column(String(200), nullable=False)
    vendor_domain: Mapped[str] = mapped_column(String(253), nullable=False)
    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    amount_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    original_currency: Mapped[str | None] = mapped_column(String(3))
    recurrence: Mapped[str] = mapped_column(String(16), default=Recurrence.ONE_TIME, nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    alternatives: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(24), default=PurchaseStatus.REQUESTED, nullable=False
    )
    decision: Mapped[str | None] = mapped_column(String(16))  # PurchaseDecision
    checkout_path: Mapped[str | None] = mapped_column(String(24))  # CheckoutPath
    approval_id: Mapped[str | None] = mapped_column(ForeignKey("approvals.id"))
    card_id: Mapped[str | None] = mapped_column(String(36))
    subscription_id: Mapped[str | None] = mapped_column(String(36))
    checkout_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    held_micros: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    settled_micros: Mapped[int | None] = mapped_column(BigInteger)
    cost_kind: Mapped[str | None] = mapped_column(String(16))
    checkout_packet: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    period_month: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class CardTransaction(Base):
    """A provider transaction (or a human-confirmed manual one), idempotent on
    ``(card_account_id, provider_txn_id)``. ``raw`` holds the redacted provider object."""

    __tablename__ = "card_transactions"
    __table_args__ = (
        UniqueConstraint(
            "card_account_id", "provider_txn_id", name="uq_card_transactions_provider_txn"
        ),
        Index("ix_card_transactions_company_occurred", "company_id", "occurred_at"),
        Index("ix_card_transactions_purchase_request_id", "purchase_request_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    card_account_id: Mapped[str] = mapped_column(ForeignKey("card_accounts.id"), nullable=False)
    card_id: Mapped[str | None] = mapped_column(ForeignKey("agent_cards.id"))
    purchase_request_id: Mapped[str | None] = mapped_column(String(36))
    subscription_id: Mapped[str | None] = mapped_column(String(36))
    provider_txn_id: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # CardTxnKind
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # CardTxnStatus
    amount_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)  # USD, signed
    merchant_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    merchant_currency: Mapped[str | None] = mapped_column(String(3))
    merchant_name: Mapped[str | None] = mapped_column(String(200))
    merchant_mcc: Mapped[str | None] = mapped_column(String(8))
    merchant_country: Mapped[str | None] = mapped_column(String(2))
    match_status: Mapped[str] = mapped_column(
        String(16), default=CardTxnMatch.UNMATCHED, nullable=False
    )
    source: Mapped[str] = mapped_column(String(16), default=CardTxnSource.PROVIDER, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    raw: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class Subscription(TimestampMixin, Base):
    """Created when a recurring purchase settles; its agent reviews it before each renewal."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        Index("ix_subscriptions_company_status_renewal", "company_id", "status", "next_renewal_on"),
        Index("ix_subscriptions_purchase_request_id", "purchase_request_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    purchase_request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    vendor: Mapped[str] = mapped_column(String(200), nullable=False)
    vendor_domain: Mapped[str] = mapped_column(String(253), nullable=False)
    item: Mapped[str] = mapped_column(String(200), nullable=False)
    amount_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    recurrence: Mapped[str] = mapped_column(String(16), nullable=False)
    card_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(
        String(24), default=SubscriptionStatus.ACTIVE, nullable=False
    )
    started_on: Mapped[date] = mapped_column(Date, nullable=False)
    next_renewal_on: Mapped[date | None] = mapped_column(Date)
    last_charged_on: Mapped[date | None] = mapped_column(Date)
    last_charge_micros: Mapped[int | None] = mapped_column(BigInteger)
    review_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    cancel_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
