"""Shared types for the chat channels (M19): what an adapter hands the router and what the router
hands back. No I/O here."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

KINDS = ("telegram", "discord", "whatsapp")
NOTIFY_KEYS = ("replies", "questions", "approvals", "alerts", "digest")

MAX_INBOUND_CHARS = 4000
RATE_LIMIT_MESSAGES = 20
RATE_LIMIT_WINDOW_SECONDS = 5 * 60
LINK_CODE_TTL_SECONDS = 10 * 60
LINK_CODE_LENGTH = 8
# no 0/O/1/I/L: a code is typed from a screen into a phone
LINK_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
MAX_FAILED_LINK_ATTEMPTS = 5  # per sender per hour
DENIED_REPLY_INTERVAL_SECONDS = 3600
WHATSAPP_WINDOW_SECONDS = 24 * 3600
MAX_SEND_ATTEMPTS = 3

DENIED_TEXT = "This is a private Foundry bot. Link your account in Foundry → Settings → Channels."
UNSUPPORTED_TEXT = "Only text is supported here for now."
RATE_LIMITED_TEXT = "Slow down - too many messages in a short time. Try again in a few minutes."
TOO_LONG_TEXT = f"That message is too long (the limit is {MAX_INBOUND_CHARS} characters)."


class ChannelAuthError(Exception):
    """The platform refused the credentials (invalid token, revoked, no permission). The message
    is the platform's own wording and never contains the secret."""


class SendError(Exception):
    """A send failed. ``auth`` = the platform refused our credentials (channel goes to ``error``);
    ``retryable`` = worth another attempt (network, 5xx, 429)."""

    def __init__(self, message: str, *, auth: bool = False, retryable: bool = True) -> None:
        super().__init__(message)
        self.auth = auth
        self.retryable = retryable and not auth


@dataclass(frozen=True)
class Button:
    label: str
    data: str  # opaque payload ("q:<id>:<n>" / "a:<id>:approve"), re-validated server-side


@dataclass
class OutMessage:
    text: str
    buttons: list[Button] = field(default_factory=list)
    reply_to: str | None = None  # the platform's id of the message to quote
    # what this message is about; logged with the platform's message id so a reply can be routed
    ref_type: str | None = None
    ref_id: str | None = None


@dataclass
class Inbound:
    """One thing a chat user did, normalised across platforms."""

    channel_id: str
    external_user_id: str
    external_chat_id: str
    external_message_id: str  # unique per channel ("cb:..." for button presses)
    kind: str = "text"  # text | unsupported | callback
    text: str = ""
    reply_to_message_id: str | None = None
    callback_data: str | None = None
    callback_id: str | None = None  # platform id to acknowledge (Telegram/Discord)
    callback_message_id: str | None = None  # the message that carried the buttons
    display_name: str | None = None
    is_private: bool = True


@dataclass
class Reply:
    """What the router wants said back (sent straight away by the platform loop)."""

    messages: list[OutMessage] = field(default_factory=list)
    ack_text: str | None = None  # short toast for a button press
    clear_buttons: bool = False  # remove the buttons of the pressed message
    link_id: str | None = None  # the link the outbound rows of this reply belong to


def chunk_text(text: str, limit: int) -> list[str]:
    """Split on paragraph/line/space boundaries so no chunk is longer than ``limit``."""
    text = text or ""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n ")
    if rest:
        chunks.append(rest)
    return chunks or [""]


_MD = re.compile(r"([\\`*_~|>#\[\]()])")


def escape_discord(text: str) -> str:
    """Show text literally on Discord (no formatting, no pings: also sent with ``allowed_mentions``
    set to none). Keeps it readable: only markdown control characters are escaped."""
    return _MD.sub(r"\\\1", text).replace("@", "@​")
