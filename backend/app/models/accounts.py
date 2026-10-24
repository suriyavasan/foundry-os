"""M7 account tables: sessions, API tokens, login throttling, invitations, notifications."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.core.time import utcnow
from app.db.base import Base
from app.db.types import UTCDateTime


class AuthSession(Base):
    """A browser session. The cookie carries a 256-bit random secret; only its SHA-256 is stored."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user_id", "user_id"),
        UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # public id
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)  # sliding
    absolute_expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ApiToken(Base):
    """A personal API token (CLI / scripts). Shown once; only the SHA-256 is stored."""

    __tablename__ = "api_tokens"
    __table_args__ = (
        Index("ix_api_tokens_user_id", "user_id"),
        UniqueConstraint("token_hash", name="uq_api_tokens_token_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    scope: Mapped[str] = mapped_column(String(8), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class LoginThrottle(Base):
    """Failed-login counters per key (``user:<username>`` / ``ip:<address>``) with exponential
    backoff. Keys of unknown usernames are tracked too, so the response never reveals whether
    a username exists."""

    __tablename__ = "login_throttles"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_failure_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Invitation(Base):
    __tablename__ = "invitations"
    __table_args__ = (
        Index("ix_invitations_company_id", "company_id"),
        UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str | None] = mapped_column(String(120))
    manager_ref: Mapped[str | None] = mapped_column(String(48))
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    accepted_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Notification(Base):
    """An in-app notification for one person (global per user; ``company_id`` says where)."""

    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    related_type: Mapped[str | None] = mapped_column(String(32))
    related_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
