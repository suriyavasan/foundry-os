"""Outbound chat messages (M19): a durable outbox in ``chat_messages`` (direction ``out``).

Producers call ``on_*`` inside their own transaction, so a message exists only if the change that
caused it committed (the same guarantee as ``mail_dispatch``, without losing a message if the process
dies before sending). The channel's lease holder (``ChannelSupervisor``) drains the rows: it renders
the text from the referenced record at send time (the text itself is never stored), re-checks the link
and the 24-hour WhatsApp window, sends, and records ``sent`` / ``failed`` / ``skipped``. Failures retry
three times with backoff; a refused credential puts the channel in ``error`` and tells the admins in
the app.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.principal import CompanyContext, Role
from app.core.time import utcnow
from app.models.accounts import Notification
from app.models.agent import Agent
from app.models.autonomy import NoteReply
from app.models.chat import (
    DEFAULT_NOTIFY,
    ChannelStatus,
    ChatChannel,
    ChatDirection,
    ChatLink,
    ChatMessage,
    ChatMessageStatus,
)
from app.models.commands import CommandExchange, ExchangeStatus
from app.models.company import CompanyMember
from app.models.enums import ApprovalStatus, MemberStatus
from app.models.governance import Approval
from app.models.user import User
from app.models.work import Task
from app.services.channels import questions_seam, render
from app.services.channels import service as channel_service
from app.services.channels.common import (
    MAX_SEND_ATTEMPTS,
    WHATSAPP_WINDOW_SECONDS,
    OutMessage,
    SendError,
)
from app.services.channels.hub import ChannelHub, SecretsUnavailable
from app.services.users import principal_for_user

logger = logging.getLogger("company_os.channels")

BACKOFF_SECONDS = (5, 30)  # after the 1st and 2nd failure; the 3rd gives up
DIGEST_HOUR_UTC = 8


# ====================================================================== producers (no commit)


def _links_for_user(db: Session, company_id: str, user_id: str, pref: str) -> list[ChatLink]:
    rows = db.execute(
        select(ChatLink, ChatChannel)
        .join(ChatChannel, ChatChannel.id == ChatLink.channel_id)
        .where(
            ChatLink.company_id == company_id,
            ChatLink.user_id == user_id,
            ChatLink.revoked_at.is_(None),
            ChatChannel.status.in_([ChannelStatus.ACTIVE, ChannelStatus.CONNECTING]),
        )
    ).all()
    return [link for link, _ in rows if {**DEFAULT_NOTIFY, **(link.notify or {})}.get(pref)]


def enqueue(
    db: Session, link: ChatLink, ref_type: str, ref_id: str, *, now: datetime | None = None
) -> ChatMessage | None:
    """One pending outbound row per (link, ref); a repeat is ignored."""
    if db.scalar(
        select(ChatMessage.id)
        .where(
            ChatMessage.channel_id == link.channel_id,
            ChatMessage.link_id == link.id,
            ChatMessage.direction == ChatDirection.OUT,
            ChatMessage.ref_type == ref_type,
            ChatMessage.ref_id == ref_id,
        )
        .limit(1)
    ):
        return None
    row = ChatMessage(
        company_id=link.company_id,
        channel_id=link.channel_id,
        link_id=link.id,
        direction=ChatDirection.OUT,
        external_user_id=link.external_user_id,
        ref_type=ref_type,
        ref_id=ref_id[:64],
        status=ChatMessageStatus.PENDING,
        created_at=now or utcnow(),
    )
    db.add(row)
    db.flush()  # sessions do not autoflush: a second enqueue of the same ref must see this one
    return row


def on_notification(db: Session, note: Notification) -> None:
    """``notifications.notify_user`` calls this for every in-app notification. Questions, approvals
    that need a person and alerts also go to that person's linked chats."""
    if not note.company_id:
        return
    try:
        if note.kind == "founder_question" and note.related_id:
            for link in _links_for_user(db, note.company_id, note.user_id, "questions"):
                enqueue(db, link, "question", note.related_id)
        elif note.kind == "human_work" and note.related_id:
            # M22: an AI employee needs a person (or the task is overdue); same preference as
            # questions. One row per notification: a reminder is a message of its own.
            for link in _links_for_user(db, note.company_id, note.user_id, "questions"):
                enqueue(db, link, "human_work", note.id)
        elif note.kind == "approval_needed" and note.related_type == "approval" and note.related_id:
            for link in _links_for_user(db, note.company_id, note.user_id, "approvals"):
                enqueue(db, link, "approval", note.related_id)
        elif note.kind == "alert":
            for link in _links_for_user(db, note.company_id, note.user_id, "alerts"):
                enqueue(db, link, "alert", note.id)
    except Exception:  # chat must never break the transaction that notified
        logger.exception("chat: could not queue a notification")


def _origin_link(db: Session, company_id: str, note_id: str) -> ChatLink | None:
    """The chat a note was written in (if it was written in a chat)."""
    link_id = db.scalar(
        select(ChatMessage.link_id)
        .where(
            ChatMessage.company_id == company_id,
            ChatMessage.direction == ChatDirection.IN,
            ChatMessage.ref_type == "note",
            ChatMessage.ref_id == note_id,
            ChatMessage.link_id.is_not(None),
        )
        .limit(1)
    )
    link = db.get(ChatLink, link_id) if link_id else None
    return link if link is not None and link.revoked_at is None else None


def _wants(link: ChatLink, pref: str) -> bool:
    return bool({**DEFAULT_NOTIFY, **(link.notify or {})}.get(pref))


def on_note_reply(db: Session, note_id: str, company_id: str, reply_id: str) -> None:
    try:
        link = _origin_link(db, company_id, note_id)
        if link is not None and _wants(link, "replies"):
            enqueue(db, link, "reply", reply_id)
    except Exception:
        logger.exception("chat: could not queue a note reply")


def on_exchange(db: Session, ex: CommandExchange, *, has_agent_reply: bool) -> None:
    """A Command Center message from a chat failed, or was answered without a written reply (a
    triage). Replies are queued by ``on_note_reply``."""
    if not ex.note_id:
        return
    if ex.status == ExchangeStatus.FAILED or (
        ex.status == ExchangeStatus.ANSWERED and not has_agent_reply
    ):
        try:
            link = _origin_link(db, ex.company_id, ex.note_id)
            if link is not None and _wants(link, "replies"):
                enqueue(db, link, "command", f"{ex.id}:{ex.attempts}")
        except Exception:
            logger.exception("chat: could not queue a command result")


def enqueue_digests(db: Session, now: datetime) -> int:
    """Once a day (after 08:00 UTC) a status summary to every link that opted in. No commit."""
    if now.hour < DIGEST_HOUR_UTC:
        return 0
    day = now.strftime("%Y-%m-%d")
    count = 0
    links = db.scalars(
        select(ChatLink)
        .join(ChatChannel, ChatChannel.id == ChatLink.channel_id)
        .where(
            ChatLink.revoked_at.is_(None),
            ChatChannel.status == ChannelStatus.ACTIVE,
        )
    )
    for link in links:
        if _wants(link, "digest") and enqueue(db, link, "digest", day, now=now) is not None:
            count += 1
    return count


# ====================================================================== delivery


def _render(
    db: Session,
    hub: ChannelHub,
    base_url: str | None,
    channel: ChatChannel,
    link: ChatLink,
    row: ChatMessage,
) -> OutMessage | str:
    """The message for an outbox row, or a string (the reason it is skipped)."""
    cid = channel.company_id
    ref = row.ref_id or ""
    if row.ref_type == "reply":
        reply = db.scalar(select(NoteReply).where(NoteReply.id == ref, NoteReply.company_id == cid))
        if reply is None:
            return "gone"
        agent = db.get(Agent, reply.author_id) if reply.author_type == "agent" else None
        return OutMessage(render.agent_text(agent.name if agent else None, reply.body))
    if row.ref_type == "command":
        ex = db.scalar(
            select(CommandExchange).where(
                CommandExchange.id == ref.split(":")[0], CommandExchange.company_id == cid
            )
        )
        if ex is None:
            return "gone"
        from app.services import command_center

        out = command_center.serialize(db, [ex])[0].get("output")
        return OutMessage(render.render_output(out))
    if row.ref_type == "question":
        q = questions_seam.get_question(db, cid, ref)
        if q is None or q.status != "open":
            return "no_longer_open"
        agent = db.get(Agent, q.agent_id)
        return render.question_message(q, agent.name if agent else None, channel.kind)
    if row.ref_type == "approval":
        approval = db.scalar(select(Approval).where(Approval.id == ref, Approval.company_id == cid))
        if approval is None or approval.status != ApprovalStatus.PENDING:
            return "no_longer_pending"
        return render.approval_message(approval, base_url)
    if row.ref_type == "human_work":
        note = db.get(Notification, ref)
        if note is None or note.user_id != link.user_id or note.company_id != cid:
            return "gone"
        task = db.scalar(select(Task).where(Task.id == note.related_id, Task.company_id == cid))
        if task is None or task.status in ("done", "cancelled", "failed"):
            return "no_longer_open"
        return render.human_work_message(note.title, task, base_url)
    if row.ref_type == "alert":
        note = db.get(Notification, ref)
        if note is None or note.user_id != link.user_id or note.company_id != cid:
            return "gone"
        body = f"\n{render.clean(note.body, 800)}" if note.body else ""
        return OutMessage(f"Alert: {render.clean(note.title, 300)}{body}")
    if row.ref_type == "digest":
        return _digest(db, link)
    return "unknown_kind"


def _digest(db: Session, link: ChatLink) -> OutMessage | str:
    from app.services import command_center

    member = db.scalar(
        select(CompanyMember).where(
            CompanyMember.company_id == link.company_id,
            CompanyMember.user_id == link.user_id,
            CompanyMember.status == MemberStatus.ACTIVE,
        )
    )
    user = db.get(User, link.user_id)
    if member is None or user is None:
        return "member_removed"
    ctx = CompanyContext(
        link.company_id, principal_for_user(user), Role(member.role), member_id=member.id
    )
    result = command_center._cmd_status(db, ctx, "")  # the same summary as /status
    return OutMessage("Daily summary\n\n" + render.render_output(result.output))


def deliver(
    factory: sessionmaker[Session],
    hub: ChannelHub,
    row_id: str,
    now: datetime,
    *,
    base_url: str | None = None,
) -> str:
    """Send one pending row. Returns its new status."""
    with factory() as db:
        row = db.get(ChatMessage, row_id)
        if row is None or row.status != ChatMessageStatus.PENDING:
            return row.status if row else "gone"
        channel = db.get(ChatChannel, row.channel_id)
        link = db.get(ChatLink, row.link_id) if row.link_id else None

        def finish(status: str, error: str | None = None) -> str:
            row.status, row.error = status, error
            db.commit()
            return status

        if channel is None or channel.status not in (
            ChannelStatus.ACTIVE,
            ChannelStatus.CONNECTING,
        ):
            return finish(ChatMessageStatus.SKIPPED, "channel_inactive")
        if link is None or link.revoked_at is not None:
            return finish(ChatMessageStatus.SKIPPED, "unlinked")
        member = db.scalar(
            select(CompanyMember).where(
                CompanyMember.company_id == link.company_id,
                CompanyMember.user_id == link.user_id,
                CompanyMember.status == MemberStatus.ACTIVE,
            )
        )
        if member is None:
            return finish(ChatMessageStatus.SKIPPED, "member_removed")
        if row.ref_type == "question" and member.role not in ("owner", "admin"):
            return finish(ChatMessageStatus.SKIPPED, "not_admin")
        if channel.kind == "whatsapp" and (
            link.last_inbound_at is None
            or now - link.last_inbound_at >= timedelta(seconds=WHATSAPP_WINDOW_SECONDS)
        ):
            return finish(ChatMessageStatus.SKIPPED, "outside_24h_window")
        message = _render(db, hub, base_url, channel, link, row)
        if isinstance(message, str):
            return finish(ChatMessageStatus.SKIPPED, message)
        try:
            secrets = hub.secrets_for(db, channel)
        except SecretsUnavailable:
            channel_service.mark_error(
                db, channel, "The stored credentials cannot be read; connect the app again."
            )
            return ChatMessageStatus.SKIPPED
        adapter = hub.adapter(channel.kind)
        try:
            ext = adapter.send(secrets, dict(channel.config or {}), link.external_chat_id, message)
        except SendError as exc:
            text = str(exc)[:300]
            if exc.auth:
                row.status, row.error = ChatMessageStatus.FAILED, text
                channel_service.mark_error(db, channel, text)  # commits
                return ChatMessageStatus.FAILED
            row.attempts += 1
            if not exc.retryable or row.attempts >= MAX_SEND_ATTEMPTS:
                return finish(ChatMessageStatus.FAILED, text)
            row.error = text
            row.next_attempt_at = now + timedelta(seconds=BACKOFF_SECONDS[row.attempts - 1])
            db.commit()
            return ChatMessageStatus.PENDING
        row.external_message_id = ext
        row.status, row.error = ChatMessageStatus.SENT, None
        channel.last_event_at = now
        try:
            db.commit()
        except IntegrityError:  # a platform id seen twice: keep the delivery, drop the id
            db.rollback()
            row = db.get(ChatMessage, row_id)
            if row is not None:
                row.status, row.external_message_id = ChatMessageStatus.SENT, None
                db.commit()
        return ChatMessageStatus.SENT


def drain(
    factory: sessionmaker[Session],
    hub: ChannelHub,
    channel_id: str,
    now: datetime,
    *,
    limit: int = 20,
    base_url_getter: Callable[[], str | None] | None = None,
) -> int:
    """Deliver the due pending rows of one channel, oldest first. Returns how many were attempted."""
    with factory() as db:
        ids = list(
            db.scalars(
                select(ChatMessage.id)
                .where(
                    ChatMessage.channel_id == channel_id,
                    ChatMessage.direction == ChatDirection.OUT,
                    ChatMessage.status == ChatMessageStatus.PENDING,
                    or_(ChatMessage.next_attempt_at.is_(None), ChatMessage.next_attempt_at <= now),
                )
                .order_by(ChatMessage.created_at, ChatMessage.id)
                .limit(limit)
            )
        )
    base = base_url_getter() if base_url_getter else None
    for row_id in ids:
        try:
            deliver(factory, hub, row_id, now, base_url=base)
        except Exception:
            logger.exception("chat: delivery crashed")
    return len(ids)


def prune(db: Session, now: datetime, *, days: int = 30) -> int:
    """Old log rows (dedupe records, delivered messages). No commit."""
    from sqlalchemy import delete

    from app.models.chat import ChatLinkCode

    cutoff = now - timedelta(days=days)
    n = db.execute(
        delete(ChatMessage).where(
            ChatMessage.created_at < cutoff,
            ChatMessage.status != ChatMessageStatus.PENDING,
        )
    ).rowcount
    n += db.execute(delete(ChatLinkCode).where(ChatLinkCode.expires_at < cutoff)).rowcount
    return int(n or 0)


__all__: list[Any] = [
    "deliver",
    "drain",
    "enqueue",
    "enqueue_digests",
    "on_exchange",
    "on_note_reply",
    "on_notification",
    "prune",
]
