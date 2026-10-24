"""M8 tables: connected accounts, domains, the hosted control UI, the company e-mail identity.

Nothing here stores a secret. Provider tokens live in the M3b credential store; a connected account
only references them by id.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
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


class ConnectedAccount(TimestampMixin, Base):
    """A third-party account the Founder connected once (API token). Agents never see its secrets:
    provider actions run in the control plane (``app/integrations``)."""

    __tablename__ = "connected_accounts"
    __table_args__ = (
        Index("ix_connected_accounts_company_id", "company_id"),
        Index("ix_connected_accounts_company_provider", "company_id", "provider"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    credential_id: Mapped[str | None] = mapped_column(ForeignKey("credentials.id"))
    # field key -> credential id (Porkbun's secret key, a webhook signing secret ...)
    extra_credential_ids: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    scopes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="connected", nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    # Non-secret facts: account name / id, team id, the namecheap user name ...
    meta: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)
    created_by_type: Mapped[str | None] = mapped_column(String(16))
    created_by_id: Mapped[str | None] = mapped_column(String(36))
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class DomainRegistration(TimestampMixin, Base):
    """One attempt to obtain a domain: a registry pull request, an assisted dashboard flow, or a
    purchase. ``payload`` holds the exact content the human approval is bound to."""

    __tablename__ = "domain_registrations"
    __table_args__ = (Index("ix_domain_registrations_company_id", "company_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    path: Mapped[str] = mapped_column(String(20), nullable=False)
    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    registry_id: Mapped[str | None] = mapped_column(String(40))
    provider_id: Mapped[str | None] = mapped_column(String(40))
    registrar: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="awaiting_approval", nullable=False)
    approval_id: Mapped[str | None] = mapped_column(ForeignKey("approvals.id"))
    pr_url: Mapped[str | None] = mapped_column(String(500))
    pr_number: Mapped[int | None] = mapped_column(Integer)
    years: Mapped[int | None] = mapped_column(Integer)
    price_micros: Mapped[int | None] = mapped_column(BigInteger)
    max_price_micros: Mapped[int | None] = mapped_column(BigInteger)
    renewal_price_micros: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by_type: Mapped[str | None] = mapped_column(String(16))
    created_by_id: Mapped[str | None] = mapped_column(String(36))


class CompanyDomain(TimestampMixin, Base):
    """The company's current domain (at most one) and its DNS / TLS state."""

    __tablename__ = "company_domains"
    __table_args__ = (UniqueConstraint("company_id", name="uq_company_domains_company_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(253))
    path: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="none", nullable=False)
    dns_status: Mapped[str] = mapped_column(String(12), default="unknown", nullable=False)
    tls_status: Mapped[str] = mapped_column(String(12), default="unknown", nullable=False)
    dns_provider: Mapped[str | None] = mapped_column(String(32))
    zone_id: Mapped[str | None] = mapped_column(String(64))
    # Desired records with the result of the last check: [{type, name, value, ttl, status}]
    dns_records: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    steps: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    registration_id: Mapped[str | None] = mapped_column(ForeignKey("domain_registrations.id"))
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error: Mapped[str | None] = mapped_column(Text)


class HostedUi(TimestampMixin, Base):
    """The control UI deployed to Vercel (at most one per company)."""

    __tablename__ = "hosted_ui_deployments"
    __table_args__ = (UniqueConstraint("company_id", name="uq_hosted_ui_deployments_company_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), default="vercel", nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="none", nullable=False)
    project_name: Mapped[str | None] = mapped_column(String(100))
    project_id: Mapped[str | None] = mapped_column(String(64))
    deployment_id: Mapped[str | None] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(String(300))
    proxy_target: Mapped[str | None] = mapped_column(String(300))
    last_deployed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error: Mapped[str | None] = mapped_column(Text)


class EmailIdentity(TimestampMixin, Base):
    """Which provider gives the company its e-mail identity (at most one row per company)."""

    __tablename__ = "email_identities"
    __table_args__ = (UniqueConstraint("company_id", name="uq_email_identities_company_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(24))
    domain: Mapped[str | None] = mapped_column(String(253))
    error: Mapped[str | None] = mapped_column(Text)
    # Provider-specific non-secret settings (the Resend domain id, the Cloudflare zone id ...).
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class Mailbox(TimestampMixin, Base):
    __tablename__ = "mailboxes"
    __table_args__ = (
        UniqueConstraint("address", name="uq_mailboxes_address"),
        Index("ix_mailboxes_company_id", "company_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    address: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    owner_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="active", nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(200))
    error: Mapped[str | None] = mapped_column(Text)
    inbound_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    outbound_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_inbound_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_outbound_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class MailMessage(Base):
    """A message in or out of a mailbox. Inbound mail is UNTRUSTED data: it is stored verbatim,
    flagged when it looks like a prompt injection, and handed to agents only inside
    ``<untrusted source="email">`` blocks."""

    __tablename__ = "mail_messages"
    __table_args__ = (
        UniqueConstraint("mailbox_id", "external_id", name="uq_mail_messages_mailbox_external"),
        Index("ix_mail_messages_company_mailbox", "company_id", "mailbox_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    mailbox_id: Mapped[str] = mapped_column(ForeignKey("mailboxes.id"), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # inbound | outbound
    external_id: Mapped[str | None] = mapped_column(String(200))
    sender: Mapped[str | None] = mapped_column(String(320))
    recipients: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    subject: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str | None] = mapped_column(Text)
    untrusted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    injection_flags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class NotificationPreference(Base):
    """Which notification kinds a person wants by e-mail (opt-in; default none)."""

    __tablename__ = "notification_preferences"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    email_kinds: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
