"""M19: chat channels (Telegram, Discord, WhatsApp Cloud API). See docs/specs/m19-chat-channels.md.

Secrets (bot tokens, the WhatsApp access token / app secret / verify token) live in one encrypted
credential (``credential_id``) and are never part of these rows' API output, audit data or events.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.core.time import utcnow
from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime


class ChannelKind(StrEnum):
    TELEGRAM = "telegram"
    DISCORD = "discord"
    WHATSAPP = "whatsapp"


class ChannelStatus(StrEnum):
    CONNECTING = "connecting"
    ACTIVE = "active"
    ERROR = "error"
    DISABLED = "disabled"


class ChatDirection(StrEnum):
    IN = "in"
    OUT = "out"


class ChatMessageStatus(StrEnum):
    RECEIVED = "received"
    PENDING = "pending"  # outbound, waiting for the channel's sender (durable outbox)
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


DEFAULT_NOTIFY = {
    "replies": True,
    "questions": True,
    "approvals": True,
    "alerts": True,
    "digest": False,
}


class ChatChannel(TimestampMixin, Base):
    __tablename__ = "chat_channels"
    __table_args__ = (
        UniqueConstraint("company_id", "kind", name="uq_chat_channels_company_kind"),
        Index("ix_chat_channels_company_id", "company_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=ChannelStatus.CONNECTING, nullable=False
    )
    credential_id: Mapped[str | None] = mapped_column(ForeignKey("credentials.id"))
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # non-secret only
    poll_cursor: Mapped[str | None] = mapped_column(String(64))
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(String(500))
    last_event_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class ChatLink(Base):
    """One chat identity (a Telegram user, a Discord user, a WhatsApp number) tied to one member."""

    __tablename__ = "chat_links"
    __table_args__ = (
        Index("ix_chat_links_company_id", "company_id"),
        Index("ix_chat_links_channel_id", "channel_id"),
        Index("ix_chat_links_user_id", "user_id"),
        # one live link per chat identity and channel; a revoked one frees it
        Index(
            "uq_chat_links_active_identity",
            "channel_id",
            "external_user_id",
            unique=True,
            sqlite_where=text("revoked_at IS NULL"),
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    channel_id: Mapped[str] = mapped_column(ForeignKey("chat_channels.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    external_chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    notify: Mapped[dict] = mapped_column(JSON, default=lambda: dict(DEFAULT_NOTIFY), nullable=False)
    last_inbound_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    linked_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ChatLinkCode(Base):
    __tablename__ = "chat_link_codes"
    __table_args__ = (
        Index("ix_chat_link_codes_company_id", "company_id"),
        Index("ix_chat_link_codes_code_hash", "code_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    channel_id: Mapped[str] = mapped_column(ForeignKey("chat_channels.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class ChatMessage(Base):
    """Inbound/outbound log. Inbound rows are the dedupe record and remember what a message became
    (``ref_type``/``ref_id``) so the answer can be routed back; outbound rows are the durable
    outbox (``pending`` until the channel's sender delivers them). The text of a message is never
    stored here: it lives in the note / reply / question / approval the row points at."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint(
            "channel_id", "direction", "external_message_id", name="uq_chat_messages_external"
        ),
        Index("ix_chat_messages_company_id", "company_id"),
        Index("ix_chat_messages_channel_status", "channel_id", "direction", "status"),
        Index("ix_chat_messages_ref", "channel_id", "ref_type", "ref_id"),
        Index("ix_chat_messages_link_created", "link_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    channel_id: Mapped[str] = mapped_column(ForeignKey("chat_channels.id"), nullable=False)
    link_id: Mapped[str | None] = mapped_column(ForeignKey("chat_links.id"))
    direction: Mapped[str] = mapped_column(String(3), nullable=False)
    external_message_id: Mapped[str | None] = mapped_column(String(200))
    external_user_id: Mapped[str | None] = mapped_column(String(64))
    # note | command | reply | question | approval | alert | digest | denied | link_fail
    ref_type: Mapped[str | None] = mapped_column(String(16))
    ref_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    error: Mapped[str | None] = mapped_column(String(300))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
