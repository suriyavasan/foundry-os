"""M19: chat channels (docs/specs/m19-chat-channels.md). Secrets are write-only: no output model
carries a token, an access token, an app secret or a verify token after the one-time reveal."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr

from app.schemas.common import ApiModel, RequestModel, Timestamp

ChannelKindLiteral = Literal["telegram", "discord", "whatsapp"]
ChannelStatusLiteral = Literal["connecting", "active", "error", "disabled"]


class WhatsAppCredentials(RequestModel):
    phone_number_id: str = Field(min_length=1, max_length=40)
    access_token: SecretStr = Field(min_length=1, max_length=2048)
    app_secret: SecretStr = Field(min_length=1, max_length=256)
    waba_id: str | None = Field(default=None, max_length=40)


class ChannelCreate(RequestModel):
    kind: ChannelKindLiteral
    name: str | None = Field(default=None, max_length=120)
    token: SecretStr | None = Field(default=None, min_length=1, max_length=512)
    whatsapp: WhatsAppCredentials | None = None


class ChannelPatch(RequestModel):
    enabled: bool | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)


class NotifyPrefs(RequestModel):
    replies: bool | None = None
    questions: bool | None = None
    approvals: bool | None = None
    alerts: bool | None = None
    digest: bool | None = None


class LinkPatch(RequestModel):
    notify: NotifyPrefs


class LinkOut(ApiModel):
    id: str
    channel_id: str
    user_id: str
    user_name: str | None
    display_name: str | None
    notify: dict[str, bool]
    linked_at: Timestamp
    last_inbound_at: Timestamp | None
    # WhatsApp only: whether a free-form reply is currently allowed (24 h window), else null
    window_open: bool | None = None


class ChannelOut(ApiModel):
    id: str
    kind: ChannelKindLiteral
    name: str
    status: ChannelStatusLiteral
    config: dict[str, str | None]
    last_error: str | None
    last_event_at: Timestamp | None
    created_at: Timestamp
    links: list[LinkOut]
    # WhatsApp: the URL to paste into Meta's webhook settings
    webhook_url: str | None = None
    # shown once, in the response that created the channel (WhatsApp)
    verify_token: str | None = None
    # Discord: invite link for the bot
    invite_url: str | None = None


class AvailableKind(ApiModel):
    kind: str
    label: str
    available: bool
    reason: str | None = None
    needs_public_url: bool = False


class ChannelList(ApiModel):
    items: list[ChannelOut]
    available: list[AvailableKind]
    next_cursor: str | None = None


class LinkCodeOut(ApiModel):
    code: str
    expires_at: Timestamp
    deep_link: str | None
    instructions: str
