"""Chat channels: connect, list, enable/disable, remove, link codes and links (M19).

Authorization lives here: owners and admins manage channels; a person may always change or remove
their own link. Secrets are validated against the platform, sealed into one credential and never
returned again (WhatsApp's verify token is shown once, in the response that creates the channel).
Every state change writes an audit event in the same transaction; no secret enters audit data,
events or logs.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import secrets as pysecrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.principal import CompanyContext, Principal, Role, require_human_admin
from app.core.time import utcnow
from app.models.chat import (
    DEFAULT_NOTIFY,
    ChannelStatus,
    ChatChannel,
    ChatDirection,
    ChatLink,
    ChatLinkCode,
    ChatMessage,
    ChatMessageStatus,
)
from app.models.company import Company, CompanyMember
from app.models.enums import MemberStatus
from app.models.provider import Credential
from app.models.user import User
from app.schemas.channels import ChannelCreate, ChannelPatch, NotifyPrefs
from app.services import audit, events, notifications
from app.services.channels.common import (
    KINDS,
    LINK_CODE_ALPHABET,
    LINK_CODE_LENGTH,
    LINK_CODE_TTL_SECONDS,
    WHATSAPP_WINDOW_SECONDS,
    ChannelAuthError,
    Inbound,
)
from app.services.channels.hub import ChannelHub

logger = logging.getLogger("company_os.channels")

LABELS = {"telegram": "Telegram", "discord": "Discord", "whatsapp": "WhatsApp"}
COMING_LATER = (
    ("slack", "Slack"),
    ("signal", "Signal"),
    ("matrix", "Matrix"),
    ("email", "Email"),
    ("sms", "SMS"),
)


# ------------------------------------------------------------------ helpers


def system_principal() -> Principal | None:
    return None  # audit.record(principal=None) -> "System"


def human_principal(user: User) -> Principal:
    from app.services.users import principal_for_user

    return principal_for_user(user)


def _require_human(ctx: CompanyContext) -> None:
    if not ctx.principal.is_human:
        raise Forbidden("Agents cannot manage chat channels")


def get_channel(db: Session, company_id: str, channel_id: str) -> ChatChannel:
    row = db.scalar(
        select(ChatChannel).where(
            ChatChannel.id == channel_id, ChatChannel.company_id == company_id
        )
    )
    if row is None:
        raise NotFound("Channel not found")
    return row


def _emit(db: Session, channel: ChatChannel) -> None:
    events.emit(db, channel.company_id, "channel.updated", {"channel_id": channel.id})


def public_base(ic: Any) -> str | None:
    """The https base URL the internet can reach this install on, or None (the WhatsApp webhook
    needs it: M13 reachability)."""
    from app.services import reachability

    url = (reachability.public_url(ic) or ic.settings.public_url or "").rstrip("/")
    parts = urlsplit(url)
    host = parts.hostname
    if parts.scheme != "https" or not host or host == "localhost" or host.endswith(".local"):
        return None
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return url
    return None if (addr.is_private or addr.is_loopback or addr.is_link_local) else url


def webhook_url(base: str, channel_id: str) -> str:
    return f"{base}/api/webhooks/whatsapp/{channel_id}"


# ------------------------------------------------------------------ list / availability


def availability(ic: Any) -> list[dict[str, Any]]:
    has_url = public_base(ic) is not None
    out: list[dict[str, Any]] = []
    for kind in KINDS:
        needs = kind == "whatsapp"
        out.append(
            {
                "kind": kind,
                "label": LABELS[kind],
                "available": True,
                "reason": (
                    "Needs a public https address for Meta's webhook (Settings → Reachability)"
                    if needs and not has_url
                    else None
                ),
                "needs_public_url": needs,
            }
        )
    for kind, label in COMING_LATER:
        out.append(
            {
                "kind": kind,
                "label": label,
                "available": False,
                "reason": "Coming later",
                "needs_public_url": False,
            }
        )
    return out


def list_channels(db: Session, ctx: CompanyContext) -> list[ChatChannel]:
    _require_human(ctx)
    require_human_admin(ctx, "see chat channels")
    return list(
        db.scalars(
            select(ChatChannel)
            .where(ChatChannel.company_id == ctx.company_id)
            .order_by(ChatChannel.created_at, ChatChannel.id)
        )
    )


def active_links(db: Session, channel_id: str) -> list[ChatLink]:
    return list(
        db.scalars(
            select(ChatLink)
            .where(ChatLink.channel_id == channel_id, ChatLink.revoked_at.is_(None))
            .order_by(ChatLink.linked_at, ChatLink.id)
        )
    )


def serialize_channel(
    db: Session,
    channel: ChatChannel,
    *,
    ic: Any = None,
    verify_token: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utcnow()
    config = {
        k: (str(v) if v is not None else None)
        for k, v in (channel.config or {}).items()
        if k != "invite_url"
    }
    links = []
    for link in active_links(db, channel.id):
        user = db.get(User, link.user_id)
        window: bool | None = None
        if channel.kind == "whatsapp":
            window = bool(
                link.last_inbound_at
                and now - link.last_inbound_at < timedelta(seconds=WHATSAPP_WINDOW_SECONDS)
            )
        links.append(
            {
                "id": link.id,
                "channel_id": link.channel_id,
                "user_id": link.user_id,
                "user_name": user.display_name if user else None,
                "display_name": link.display_name,
                "notify": {**DEFAULT_NOTIFY, **(link.notify or {})},
                "linked_at": link.linked_at,
                "last_inbound_at": link.last_inbound_at,
                "window_open": window,
            }
        )
    url = None
    if channel.kind == "whatsapp" and ic is not None:
        base = public_base(ic)
        url = webhook_url(base, channel.id) if base else None
    return {
        "id": channel.id,
        "kind": channel.kind,
        "name": channel.name,
        "status": channel.status,
        "config": config,
        "last_error": channel.last_error,
        "last_event_at": channel.last_event_at,
        "created_at": channel.created_at,
        "links": links,
        "webhook_url": url,
        "verify_token": verify_token,
        "invite_url": (channel.config or {}).get("invite_url"),
    }


# ------------------------------------------------------------------ connect / update / remove


def _secrets_from(data: ChannelCreate) -> tuple[dict[str, str], dict[str, Any]]:
    if data.kind == "whatsapp":
        wa = data.whatsapp
        if wa is None:
            raise ValidationFailed(
                "WhatsApp needs phone_number_id, access_token and app_secret",
                details=[{"loc": ["body", "whatsapp"], "msg": "required for WhatsApp"}],
            )
        return (
            {
                "access_token": wa.access_token.get_secret_value().strip(),
                "app_secret": wa.app_secret.get_secret_value().strip(),
                "verify_token": pysecrets.token_urlsafe(24),
            },
            {"phone_number_id": wa.phone_number_id.strip(), "waba_id": wa.waba_id},
        )
    if data.token is None:
        raise ValidationFailed(
            f"{LABELS[data.kind]} needs a bot token",
            details=[{"loc": ["body", "token"], "msg": "required"}],
        )
    return {"token": data.token.get_secret_value().strip()}, {}


def _identity(kind: str, config: dict[str, Any]) -> str:
    return str(config.get("phone_number_id") if kind == "whatsapp" else config.get("bot_id") or "")


def _check_not_taken(db: Session, kind: str, config: dict[str, Any], *, except_id: str | None):
    """One bot (or WhatsApp number) can be served by one channel on this install: two pollers on
    one token would fight over its updates."""
    ident = _identity(kind, config)
    if not ident:
        return
    for other in db.scalars(select(ChatChannel).where(ChatChannel.kind == kind)):
        if other.id != except_id and _identity(kind, other.config or {}) == ident:
            raise Conflict(
                f"That {LABELS[kind]} account is already connected to a workspace on this install",
                details={"reason": "channel_in_use"},
            )


def connect(
    db: Session, ctx: CompanyContext, hub: ChannelHub, ic: Any, data: ChannelCreate
) -> tuple[ChatChannel, str | None]:
    """Validate against the platform, seal the secrets, create the channel. Returns the channel and
    (WhatsApp) the verify token, to be shown once."""
    _require_human(ctx)
    require_human_admin(ctx, "connect a chat app")
    existing = db.scalar(
        select(ChatChannel.id).where(
            ChatChannel.company_id == ctx.company_id, ChatChannel.kind == data.kind
        )
    )
    if existing:
        raise Conflict(
            f"{LABELS[data.kind]} is already connected; remove it first to connect another bot",
            details={"reason": "channel_exists"},
        )
    base = None
    if data.kind == "whatsapp":
        base = public_base(ic)
        if base is None:
            raise Conflict(
                "WhatsApp needs a public https address for Meta's webhook. Set one up in "
                "Settings → Reachability (a tunnel or a domain), then connect again.",
                details={"reason": "public_url_required", "hint": "reachability"},
            )
    secrets, params = _secrets_from(data)
    adapter = hub.adapter(data.kind)
    try:
        config = adapter.validate(secrets, params)
        _check_not_taken(db, data.kind, config, except_id=None)
        adapter.on_connect(secrets, config)
    except ChannelAuthError as exc:
        raise ValidationFailed(
            str(exc), details={"reason": "invalid_credentials", "kind": data.kind}
        ) from None
    config = {k: v for k, v in config.items() if v not in (None, "")}
    now = utcnow()
    name = (data.name or "").strip() or _default_name(data.kind, config)
    credential = Credential(
        company_id=ctx.company_id,
        name=f"Chat channel {data.kind} {now.strftime('%Y%m%dT%H%M%S')}"[:120],
        kind="other",
        encrypted_value=hub.seal(secrets),
        created_by_type=ctx.principal.type.value,
        created_by_id=ctx.principal.id,
    )
    db.add(credential)
    db.flush()
    channel = ChatChannel(
        company_id=ctx.company_id,
        kind=data.kind,
        name=name,
        status=ChannelStatus.CONNECTING if data.kind == "discord" else ChannelStatus.ACTIVE,
        credential_id=credential.id,
        config=config,
        created_by_user_id=ctx.principal.id,
    )
    db.add(channel)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Conflict(f"{LABELS[data.kind]} is already connected") from None
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="chat.channel_connected",
        target_type="chat_channel",
        target_id=channel.id,
        summary=f"Connected {LABELS[data.kind]}",
        data={"kind": data.kind, "name": name},  # never a token or an id of the bot account
    )
    _emit(db, channel)
    db.commit()
    return channel, secrets.get("verify_token")


def _default_name(kind: str, config: dict[str, Any]) -> str:
    if kind == "telegram" and config.get("bot_username"):
        return f"@{config['bot_username']}"
    if kind == "discord" and config.get("bot_username"):
        return str(config["bot_username"])
    if kind == "whatsapp":
        return str(config.get("display_phone") or config.get("verified_name") or "WhatsApp")
    return LABELS[kind]


def update_channel(
    db: Session, ctx: CompanyContext, hub: ChannelHub, channel_id: str, data: ChannelPatch
) -> ChatChannel:
    _require_human(ctx)
    require_human_admin(ctx, "change a chat app")
    channel = get_channel(db, ctx.company_id, channel_id)
    changed: dict[str, Any] = {}
    if data.name is not None and data.name != channel.name:
        channel.name = data.name
        changed["name"] = data.name
    if data.enabled is not None:
        if data.enabled and channel.status in (ChannelStatus.DISABLED, ChannelStatus.ERROR):
            adapter = hub.adapter(channel.kind)
            try:
                secrets = hub.secrets_for(db, channel)
                params = {"phone_number_id": (channel.config or {}).get("phone_number_id")}
                adapter.validate(secrets, params)
                adapter.on_connect(secrets, channel.config or {})
            except ChannelAuthError as exc:
                raise ValidationFailed(
                    str(exc), details={"reason": "invalid_credentials"}
                ) from None
            except Exception as exc:
                raise Conflict(
                    "The stored credentials cannot be used; remove the channel and connect it again",
                    details={"reason": "credentials_unavailable"},
                ) from exc
            channel.status = (
                ChannelStatus.CONNECTING if channel.kind == "discord" else ChannelStatus.ACTIVE
            )
            channel.last_error = None
            changed["enabled"] = True
        elif not data.enabled and channel.status != ChannelStatus.DISABLED:
            channel.status = ChannelStatus.DISABLED
            _skip_pending(db, channel.id, "channel_disabled")
            changed["enabled"] = False
    if not changed:
        return channel
    channel.updated_at = utcnow()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="chat.channel_updated",
        target_type="chat_channel",
        target_id=channel.id,
        summary=f"Updated {LABELS.get(channel.kind, channel.kind)}",
        data={"kind": channel.kind, **changed},
    )
    _emit(db, channel)
    db.commit()
    return channel


def remove_channel(db: Session, ctx: CompanyContext, channel_id: str) -> None:
    _require_human(ctx)
    require_human_admin(ctx, "remove a chat app")
    channel = get_channel(db, ctx.company_id, channel_id)
    kind, cid = channel.kind, channel.id
    if channel.credential_id:
        cred = db.get(Credential, channel.credential_id)
        if cred is not None and cred.revoked_at is None:
            cred.revoked_at = utcnow()
            cred.encrypted_value = ""  # a removed secret is not kept
    db.execute(delete(ChatMessage).where(ChatMessage.channel_id == cid))
    db.execute(delete(ChatLinkCode).where(ChatLinkCode.channel_id == cid))
    db.execute(delete(ChatLink).where(ChatLink.channel_id == cid))
    db.delete(channel)
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="chat.channel_removed",
        target_type="chat_channel",
        target_id=cid,
        summary=f"Removed {LABELS.get(kind, kind)} and unlinked everyone",
        data={"kind": kind},
    )
    events.emit(db, ctx.company_id, "channel.updated", {"channel_id": cid})
    db.commit()


def mark_error(db: Session, channel: ChatChannel, message: str) -> None:
    """The platform refused our credentials: stop using the channel, say why, tell the admins in
    the app. Commits."""
    if channel.status == ChannelStatus.ERROR:
        return
    channel.status = ChannelStatus.ERROR
    channel.last_error = message[:500]
    channel.updated_at = utcnow()
    db.flush()  # the alert below must not be queued to this very channel
    _skip_pending(db, channel.id, "channel_error")
    audit.record(
        db,
        company_id=channel.company_id,
        principal=None,
        action="chat.channel_error",
        target_type="chat_channel",
        target_id=channel.id,
        summary=f"{LABELS.get(channel.kind, channel.kind)} stopped: {message[:200]}",
        data={"kind": channel.kind},
    )
    _emit(db, channel)
    notifications.notify_admins(
        db,
        channel.company_id,
        notifications.ALERT,
        f"{LABELS.get(channel.kind, channel.kind)} needs attention",
        body=f"{message[:300]} Update the connection in Settings → Chat apps.",
        related_type="chat_channel",
        related_id=channel.id,
    )
    db.commit()


def _skip_pending(db: Session, channel_id: str, reason: str) -> None:
    db.execute(
        update(ChatMessage)
        .where(
            ChatMessage.channel_id == channel_id,
            ChatMessage.direction == ChatDirection.OUT,
            ChatMessage.status == ChatMessageStatus.PENDING,
        )
        .values(status=ChatMessageStatus.SKIPPED, error=reason)
    )


# ------------------------------------------------------------------ link codes (from the web)


def hash_code(code: str) -> str:
    return hashlib.sha256(normalize_code(code).encode()).hexdigest()


def normalize_code(code: str) -> str:
    return "".join(c for c in code.upper() if c.isalnum())


def issue_link_code(
    db: Session, ctx: CompanyContext, hub: ChannelHub, channel_id: str, now: datetime | None = None
) -> dict[str, Any]:
    _require_human(ctx)
    require_human_admin(ctx, "link a chat account")
    channel = get_channel(db, ctx.company_id, channel_id)
    if channel.status not in (ChannelStatus.ACTIVE, ChannelStatus.CONNECTING):
        raise Conflict(
            "This chat app is not running; fix or enable it first",
            details={"reason": "channel_not_active"},
        )
    now = now or utcnow()
    # one live code per person and channel
    db.execute(
        update(ChatLinkCode)
        .where(
            ChatLinkCode.channel_id == channel.id,
            ChatLinkCode.user_id == ctx.principal.id,
            ChatLinkCode.used_at.is_(None),
        )
        .values(used_at=now)
    )
    code = "".join(pysecrets.choice(LINK_CODE_ALPHABET) for _ in range(LINK_CODE_LENGTH))
    row = ChatLinkCode(
        company_id=ctx.company_id,
        channel_id=channel.id,
        user_id=ctx.principal.id,
        code_hash=hash_code(code),
        expires_at=now + timedelta(seconds=LINK_CODE_TTL_SECONDS),
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="chat.link_code_issued",
        target_type="chat_channel",
        target_id=channel.id,
        summary=f"Issued a {LABELS.get(channel.kind, channel.kind)} link code",
        data={"kind": channel.kind},  # never the code
    )
    db.commit()
    deep_link, instructions = hub.adapter(channel.kind).link_hint(channel.config or {}, code)
    return {
        "code": code,
        "expires_at": row.expires_at,
        "deep_link": deep_link,
        "instructions": instructions,
    }


# ------------------------------------------------------------------ links


@dataclass
class LinkOutcome:
    ok: bool
    text: str


def redeem_code(
    db: Session, channel: ChatChannel, inbound: Inbound, raw_code: str, now: datetime
) -> LinkOutcome:
    """A sender typed a code in the chat. Valid = this channel, unused, unexpired, the issuer still
    an active admin of the company. Every failure gets the same wording."""
    bad = LinkOutcome(False, "That code is not valid or has expired. Create a new one in Foundry.")
    code = normalize_code(raw_code)
    if len(code) != LINK_CODE_LENGTH:
        return bad
    row = db.scalar(
        select(ChatLinkCode).where(
            ChatLinkCode.company_id == channel.company_id,
            ChatLinkCode.channel_id == channel.id,
            ChatLinkCode.code_hash == hash_code(code),
        )
    )
    if row is None or row.used_at is not None or row.expires_at <= now:
        return bad
    member = db.scalar(
        select(CompanyMember).where(
            CompanyMember.company_id == channel.company_id,
            CompanyMember.user_id == row.user_id,
            CompanyMember.status == MemberStatus.ACTIVE,
        )
    )
    user = db.get(User, row.user_id)
    if member is None or user is None or member.role not in ("owner", "admin"):
        return bad
    row.used_at = now
    link = ChatLink(
        company_id=channel.company_id,
        channel_id=channel.id,
        user_id=row.user_id,
        external_user_id=inbound.external_user_id,
        external_chat_id=inbound.external_chat_id,
        display_name=(inbound.display_name or None),
        notify=dict(DEFAULT_NOTIFY),
        last_inbound_at=now,
        linked_at=now,
    )
    db.add(link)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return bad
    audit.record(
        db,
        company_id=channel.company_id,
        principal=human_principal(user),
        action="chat.linked",
        target_type="chat_link",
        target_id=link.id,
        summary=f"Linked a {LABELS.get(channel.kind, channel.kind)} chat to {user.display_name}",
        data={"kind": channel.kind, "channel_id": channel.id},
    )
    _emit(db, channel)
    company = db.get(Company, channel.company_id)
    db.commit()
    return LinkOutcome(
        True,
        f"Linked to {company.name if company else 'your company'} as {user.display_name}. "
        "Send /help to see what you can do here.",
    )


def revoke_link(
    db: Session,
    link: ChatLink,
    principal: Principal | None,
    *,
    reason: str,
    commit: bool = True,
) -> None:
    if link.revoked_at is not None:
        return
    now = utcnow()
    link.revoked_at = now
    _skip_pending_for_link(db, link.id, "unlinked")
    channel = db.get(ChatChannel, link.channel_id)
    audit.record(
        db,
        company_id=link.company_id,
        principal=principal,
        action="chat.unlinked",
        target_type="chat_link",
        target_id=link.id,
        summary="Unlinked a chat account",
        data={"kind": channel.kind if channel else None, "reason": reason},
    )
    if channel is not None:
        _emit(db, channel)
    if commit:
        db.commit()


def _skip_pending_for_link(db: Session, link_id: str, reason: str) -> None:
    db.execute(
        update(ChatMessage)
        .where(
            ChatMessage.link_id == link_id,
            ChatMessage.direction == ChatDirection.OUT,
            ChatMessage.status == ChatMessageStatus.PENDING,
        )
        .values(status=ChatMessageStatus.SKIPPED, error=reason)
    )


def revoke_links_for_member(db: Session, company_id: str, user_id: str, principal=None) -> int:
    """A person lost access to the company: their chat links stop working. No commit."""
    links = list(
        db.scalars(
            select(ChatLink).where(
                ChatLink.company_id == company_id,
                ChatLink.user_id == user_id,
                ChatLink.revoked_at.is_(None),
            )
        )
    )
    for link in links:
        revoke_link(db, link, principal, reason="member_removed", commit=False)
    return len(links)


def _get_link(db: Session, company_id: str, link_id: str) -> ChatLink:
    link = db.scalar(
        select(ChatLink).where(
            ChatLink.id == link_id,
            ChatLink.company_id == company_id,
            ChatLink.revoked_at.is_(None),
        )
    )
    if link is None:
        raise NotFound("Link not found")
    return link


def _own_or_admin(ctx: CompanyContext, link: ChatLink, action: str) -> None:
    _require_human(ctx)
    if ctx.role == Role.VIEWER:
        raise Forbidden("Viewers have read-only access")
    if link.user_id != ctx.principal.id and not ctx.is_human_admin:
        raise Forbidden(f"Only the linked person or an owner/admin can {action}")


def update_link_notify(
    db: Session, ctx: CompanyContext, link_id: str, prefs: NotifyPrefs
) -> ChatLink:
    _require_human(ctx)
    link = _get_link(db, ctx.company_id, link_id)
    if link.user_id != ctx.principal.id:  # an admin may unlink anyone, but not tune their alerts
        raise Forbidden("Only the linked person can change their notifications")
    merged = {**DEFAULT_NOTIFY, **(link.notify or {})}
    for key, value in prefs.model_dump(exclude_none=True).items():
        merged[key] = bool(value)
    link.notify = merged
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="chat.link_updated",
        target_type="chat_link",
        target_id=link.id,
        summary="Changed chat notifications",
        data={"notify": merged},
    )
    channel = db.get(ChatChannel, link.channel_id)
    if channel is not None:
        _emit(db, channel)
    db.commit()
    return link


def delete_link(db: Session, ctx: CompanyContext, link_id: str) -> None:
    link = _get_link(db, ctx.company_id, link_id)
    _own_or_admin(ctx, link, "unlink this chat")
    revoke_link(db, link, ctx.principal, reason="web")


def link_out(db: Session, link: ChatLink) -> dict[str, Any]:
    channel = db.get(ChatChannel, link.channel_id)
    user = db.get(User, link.user_id)
    window = None
    if channel is not None and channel.kind == "whatsapp":
        window = bool(
            link.last_inbound_at
            and utcnow() - link.last_inbound_at < timedelta(seconds=WHATSAPP_WINDOW_SECONDS)
        )
    return {
        "id": link.id,
        "channel_id": link.channel_id,
        "user_id": link.user_id,
        "user_name": user.display_name if user else None,
        "display_name": link.display_name,
        "notify": {**DEFAULT_NOTIFY, **(link.notify or {})},
        "linked_at": link.linked_at,
        "last_inbound_at": link.last_inbound_at,
        "window_open": window,
    }
