"""Inbound routing for the chat channels (M19): one function per kind of thing a linked person can do.

Default deny. An unlinked sender gets one fixed reply per hour and nothing else is processed or
stored beyond a dedupe row. A linked sender acts as themselves: their own principal and company
role (re-read from ``company_members`` on every message), through the same services the web uses
(``command_center.submit``, ``approvals.decide``, the founder-question service). Button payloads
are opaque ids that are re-validated against the database; nothing in them is trusted.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppError
from app.core.principal import CompanyContext, Role
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.chat import (
    ChannelStatus,
    ChatChannel,
    ChatDirection,
    ChatLink,
    ChatMessage,
    ChatMessageStatus,
)
from app.models.company import CompanyMember
from app.models.enums import ApprovalStatus, MemberStatus
from app.models.governance import Approval
from app.models.user import User
from app.services import approvals, audit, command_center
from app.services.channels import questions_seam, render
from app.services.channels import service as channel_service
from app.services.channels.common import (
    DENIED_REPLY_INTERVAL_SECONDS,
    DENIED_TEXT,
    MAX_FAILED_LINK_ATTEMPTS,
    MAX_INBOUND_CHARS,
    RATE_LIMIT_MESSAGES,
    RATE_LIMIT_WINDOW_SECONDS,
    RATE_LIMITED_TEXT,
    TOO_LONG_TEXT,
    UNSUPPORTED_TEXT,
    ChannelAuthError,
    Inbound,
    OutMessage,
    Reply,
    SendError,
)
from app.services.channels.hub import ChannelHub, SecretsUnavailable
from app.services.users import principal_for_user

logger = logging.getLogger("company_os.channels")

_LINK_RE = re.compile(r"^\s*(?:/start|/link|link)\s+([A-Za-z0-9\- ]{6,20})\s*$", re.IGNORECASE)
MAX_ANSWER_CHARS = 500
LISTED = 5

HELP_TEXT = (
    "You are talking to your company. Send any message and the AI Co-founder will pick it up and "
    "answer here.\n\n"
    "/status - company at a glance\n"
    "/questions - open questions from your agents\n"
    "/approvals - requests waiting for your decision\n"
    "/help - this list\n"
    "/unlink - disconnect this chat\n\n"
    "All Foundry commands (/goals, /tasks, /spend ...) work here too. Voice, files and stickers "
    "are not supported yet."
)


class ChannelRouter:
    def __init__(
        self,
        factory: sessionmaker[Session],
        hub: ChannelHub,
        *,
        clock: Callable[[], datetime] = utcnow,
        base_url_getter: Callable[[], str | None] | None = None,
    ) -> None:
        self.factory = factory
        self.hub = hub
        self.clock = clock
        self.base_url_getter = base_url_getter

    # ============================================================== full cycle (with I/O)

    def process(self, inbound: Inbound) -> None:
        """Handle one inbound event and send what the router wants said. Never raises."""
        try:
            self._process(inbound)
        except Exception:
            logger.exception("chat: could not process an inbound %s event", inbound.kind)

    def _process(self, inbound: Inbound) -> None:
        with self.factory() as db:
            channel = db.get(ChatChannel, inbound.channel_id)
            if channel is None or channel.status not in (
                ChannelStatus.ACTIVE,
                ChannelStatus.CONNECTING,
            ):
                return
            try:
                secrets = self.hub.secrets_for(db, channel)
            except SecretsUnavailable:
                channel_service.mark_error(
                    db, channel, "The stored credentials cannot be read; connect the app again."
                )
                return
            adapter = self.hub.adapter(channel.kind)
            config = dict(channel.config or {})
            if inbound.kind == "callback":
                adapter.ack_early(secrets, config, inbound)
            try:
                reply = self.handle(db, channel, inbound)
            except Exception:
                logger.exception("chat: handling failed")
                db.rollback()
                reply = Reply(
                    messages=[OutMessage("Something went wrong on our side. Nothing was changed.")]
                )
            channel_id, company_id = channel.id, channel.company_id
        for msg in reply.messages:
            self._send_direct(channel_id, company_id, secrets, config, inbound, reply.link_id, msg)
        if inbound.kind == "callback":
            if reply.clear_buttons:
                adapter.clear_buttons(secrets, config, inbound)
            adapter.ack_late(secrets, config, inbound, reply.ack_text)

    def _send_direct(
        self,
        channel_id: str,
        company_id: str,
        secrets: dict[str, str],
        config: dict[str, Any],
        inbound: Inbound,
        link_id: str | None,
        msg: OutMessage,
    ) -> None:
        with self.factory() as db:
            channel = db.get(ChatChannel, channel_id)
            if channel is None:
                return
            adapter = self.hub.adapter(channel.kind)
            ext: str | None = None
            status = ChatMessageStatus.SENT
            error: str | None = None
            try:
                ext = adapter.send(secrets, config, inbound.external_chat_id, msg)
            except SendError as exc:
                status, error = ChatMessageStatus.FAILED, str(exc)[:300]
                if exc.auth:
                    channel_service.mark_error(db, channel, str(exc))
            row = ChatMessage(
                company_id=company_id,
                channel_id=channel_id,
                link_id=link_id,
                direction=ChatDirection.OUT,
                external_message_id=ext,
                external_user_id=inbound.external_user_id,
                ref_type=msg.ref_type,
                ref_id=msg.ref_id,
                status=status,
                error=error,
                created_at=self.clock(),
            )
            db.add(row)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()

    # ============================================================== the decision (DB only)

    def handle(self, db: Session, channel: ChatChannel, inbound: Inbound) -> Reply:
        now = self.clock()
        if not inbound.is_private:
            return Reply()  # groups and servers are ignored in this milestone
        row = ChatMessage(
            company_id=channel.company_id,
            channel_id=channel.id,
            direction=ChatDirection.IN,
            external_message_id=inbound.external_message_id[:200],
            external_user_id=inbound.external_user_id[:64],
            status=ChatMessageStatus.RECEIVED,
            created_at=now,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            return Reply()  # already handled (a retried delivery)
        channel.last_event_at = now
        db.commit()  # the dedupe row stands even if what follows fails: at most once, never twice
        link = db.scalar(
            select(ChatLink).where(
                ChatLink.channel_id == channel.id,
                ChatLink.external_user_id == inbound.external_user_id,
                ChatLink.revoked_at.is_(None),
            )
        )
        if link is None:
            reply = self._unlinked(db, channel, inbound, row, now)
            db.commit()
            return reply
        row.link_id = link.id
        link.last_inbound_at = now
        link.external_chat_id = inbound.external_chat_id
        if inbound.display_name and not link.display_name:
            link.display_name = inbound.display_name
        # the person's CURRENT membership and role decide what they may do
        member = db.scalar(
            select(CompanyMember).where(
                CompanyMember.company_id == channel.company_id,
                CompanyMember.user_id == link.user_id,
                CompanyMember.status == MemberStatus.ACTIVE,
            )
        )
        user = db.get(User, link.user_id)
        if member is None or user is None:
            channel_service.revoke_link(db, link, None, reason="member_removed", commit=False)
            row.status = ChatMessageStatus.SKIPPED
            db.commit()
            return Reply(messages=[OutMessage(DENIED_TEXT)])
        ctx = CompanyContext(
            channel.company_id, principal_for_user(user), Role(member.role), member_id=member.id
        )
        db.flush()  # count this message too
        recent = db.scalar(
            select(func.count(ChatMessage.id)).where(
                ChatMessage.link_id == link.id,
                ChatMessage.direction == ChatDirection.IN,
                ChatMessage.created_at > now - timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS),
            )
        )
        db.commit()
        reply = Reply(link_id=link.id)
        if (recent or 0) > RATE_LIMIT_MESSAGES:
            row.status = ChatMessageStatus.SKIPPED
            db.commit()
            reply.messages = [OutMessage(RATE_LIMITED_TEXT)]
            return reply
        if inbound.kind == "unsupported":
            reply.messages = [OutMessage(UNSUPPORTED_TEXT)]
        elif inbound.kind == "callback":
            self._callback(db, channel, link, ctx, inbound, reply)
        else:
            self._text(db, channel, link, ctx, inbound, row, reply)
        db.commit()
        return reply

    # ------------------------------------------------------------ unlinked senders

    def _unlinked(
        self, db: Session, channel: ChatChannel, inbound: Inbound, row: ChatMessage, now: datetime
    ) -> Reply:
        match = _LINK_RE.match(inbound.text) if inbound.kind == "text" else None
        if match:
            failed = db.scalar(
                select(func.count(ChatMessage.id)).where(
                    ChatMessage.channel_id == channel.id,
                    ChatMessage.direction == ChatDirection.IN,
                    ChatMessage.external_user_id == inbound.external_user_id,
                    ChatMessage.ref_type == "link_fail",
                    ChatMessage.created_at > now - timedelta(hours=1),
                )
            )
            if (failed or 0) < MAX_FAILED_LINK_ATTEMPTS:
                outcome = channel_service.redeem_code(db, channel, inbound, match.group(1), now)
                # redeem_code commits on success and may roll back on a lost race: re-attach the row
                if outcome.ok:
                    return Reply(messages=[OutMessage(outcome.text)])
                db.execute(
                    update(ChatMessage)
                    .where(ChatMessage.id == row.id)
                    .values(ref_type="link_fail", status=ChatMessageStatus.SKIPPED)
                )
                return Reply(messages=[OutMessage(outcome.text)])
        row.status = ChatMessageStatus.SKIPPED
        recent = db.scalar(
            select(ChatMessage.id)
            .where(
                ChatMessage.channel_id == channel.id,
                ChatMessage.direction == ChatDirection.OUT,
                ChatMessage.ref_type == "denied",
                ChatMessage.external_user_id == inbound.external_user_id,
                ChatMessage.created_at > now - timedelta(seconds=DENIED_REPLY_INTERVAL_SECONDS),
            )
            .limit(1)
        )
        if recent:
            return Reply()
        msg = OutMessage(DENIED_TEXT)
        msg.ref_type = "denied"
        return Reply(messages=[msg])

    # ------------------------------------------------------------ text

    def _text(
        self,
        db: Session,
        channel: ChatChannel,
        link: ChatLink,
        ctx: CompanyContext,
        inbound: Inbound,
        row: ChatMessage,
        reply: Reply,
    ) -> None:
        text = inbound.text.strip()
        if not text:
            reply.messages = [OutMessage(UNSUPPORTED_TEXT)]
            return
        if len(text) > MAX_INBOUND_CHARS:
            reply.messages = [OutMessage(TOO_LONG_TEXT)]
            return
        head, _, rest = text.partition(" ")
        cmd = head.lower().split("@")[0]
        if cmd in ("/unlink", "unlink"):
            channel_service.revoke_link(db, link, ctx.principal, reason="chat")
            reply.messages = [
                OutMessage("Unlinked. This chat no longer talks to your company. Bye.")
            ]
            return
        if cmd in ("/help", "help", "/start") and not rest.strip():
            reply.messages = [OutMessage(HELP_TEXT)]
            return
        if cmd in ("/start", "/link", "link") and _LINK_RE.match(text):
            reply.messages = [
                OutMessage("This chat is already linked. Send /unlink to disconnect it.")
            ]
            return
        if cmd == "/questions":
            self._list_questions(db, channel, ctx, reply)
            return
        if cmd == "/approvals":
            self._list_approvals(db, channel, ctx, reply)
            return
        picked = self._question_reply(db, channel, link, inbound, text)
        if picked is not None:
            question_id, answer_text = picked
            self._answer_question(db, channel, link, ctx, question_id, answer_text, reply)
            return
        # everything else is the Command Center, exactly as on the web
        submitted = cmd.startswith("/")
        body = f"{cmd} {rest}".strip() if submitted else text
        try:
            result = command_center.submit(db, ctx, body)
        except AppError as exc:
            db.rollback()
            reply.messages = [OutMessage(render.clean(exc.message, 500))]
            return
        if result.get("input_kind") == "note":
            row.ref_type, row.ref_id = "note", result.get("note_id")
            reply.messages = [
                OutMessage(
                    "Passed to the AI Co-founder. I will answer here when it replies "
                    "(this can take a minute or two)."
                )
            ]
        else:
            row.ref_type, row.ref_id = "command", result.get("id")
            reply.messages = [OutMessage(render.render_output(result.get("output")))]

    # ------------------------------------------------------------ questions

    def _agent_name(self, db: Session, company_id: str, agent_id: str | None) -> str | None:
        if not agent_id:
            return None
        agent = db.scalar(select(Agent).where(Agent.id == agent_id, Agent.company_id == company_id))
        return agent.name if agent else None

    def _list_questions(
        self, db: Session, channel: ChatChannel, ctx: CompanyContext, reply: Reply
    ) -> None:
        if not questions_seam.available():
            reply.messages = [OutMessage("Founder questions are not available in this version.")]
            return
        if not ctx.is_human_admin:
            reply.messages = [OutMessage("Only owners and admins can answer questions.")]
            return
        rows = questions_seam.list_open(db, ctx.company_id, LISTED + 1)
        if not rows:
            reply.messages = [OutMessage("No open questions. Your agents are not waiting on you.")]
            return
        for q in rows[:LISTED]:
            reply.messages.append(
                render.question_message(
                    q, self._agent_name(db, q.company_id, q.agent_id), channel.kind
                )
            )
        if len(rows) > LISTED:
            reply.messages.append(OutMessage("More questions are open in Foundry."))

    def _question_reply(
        self, db: Session, channel: ChatChannel, link: ChatLink, inbound: Inbound, text: str
    ) -> tuple[str, str] | None:
        """(question id, answer) when this plain message answers a question we sent this person:
        a reply to the question message, or (WhatsApp list mode) a bare option number."""
        if not questions_seam.available():
            return None
        qid: str | None = None
        if inbound.reply_to_message_id:
            qid = db.scalar(
                select(ChatMessage.ref_id).where(
                    ChatMessage.channel_id == channel.id,
                    ChatMessage.link_id == link.id,
                    ChatMessage.direction == ChatDirection.OUT,
                    ChatMessage.external_message_id == inbound.reply_to_message_id,
                    ChatMessage.ref_type == "question",
                )
            )
            if qid:
                return qid, text
        if channel.kind == "whatsapp" and text.isdigit() and len(text) <= 2:
            n = int(text)
            recent = db.scalars(
                select(ChatMessage.ref_id)
                .where(
                    ChatMessage.channel_id == channel.id,
                    ChatMessage.link_id == link.id,
                    ChatMessage.direction == ChatDirection.OUT,
                    ChatMessage.ref_type == "question",
                    ChatMessage.status == ChatMessageStatus.SENT,
                )
                .order_by(ChatMessage.created_at.desc())
                .limit(3)
            )
            for candidate in recent:
                q = questions_seam.get_question(db, channel.company_id, candidate)
                if q is None or q.status != "open" or render.wa_uses_buttons(q):
                    continue
                options = list(q.options or [])
                if n == 0:
                    return q.id, q.default_answer
                if 1 <= n <= len(options):
                    return q.id, str(options[n - 1])
                return None
        return None

    def _answer_question(
        self,
        db: Session,
        channel: ChatChannel,
        link: ChatLink,
        ctx: CompanyContext,
        question_id: str,
        answer_text: str,
        reply: Reply,
    ) -> None:
        answer_text = answer_text.strip()
        if not answer_text or len(answer_text) > MAX_ANSWER_CHARS:
            reply.messages = [
                OutMessage(f"Answers must be between 1 and {MAX_ANSWER_CHARS} characters.")
            ]
            return
        try:
            q = questions_seam.answer(db, ctx, question_id, answer_text, via=channel.kind)
        except AppError as exc:
            db.rollback()
            reply.messages = [OutMessage(render.clean(exc.message, 300))]
            reply.ack_text = render.clean(exc.message, 150)
            return
        reply.messages = [
            OutMessage(f"Got it. Answered: {render.clean(q.answer or answer_text, 200)}")
        ]
        reply.ack_text = "Answered"
        reply.clear_buttons = True

    # ------------------------------------------------------------ approvals

    def _pending_for(self, db: Session, ctx: CompanyContext) -> list[Approval]:
        stmt = select(Approval).where(
            Approval.company_id == ctx.company_id,
            Approval.status == ApprovalStatus.PENDING,
            Approval.approver == "human",
        )
        if not ctx.is_human_admin:
            if ctx.member_id is None:
                return []
            stmt = stmt.where(Approval.approver_member_id == ctx.member_id)
        return list(db.scalars(stmt.order_by(Approval.created_at).limit(LISTED + 1)))

    def _list_approvals(
        self, db: Session, channel: ChatChannel, ctx: CompanyContext, reply: Reply
    ) -> None:
        if ctx.role == Role.VIEWER:
            reply.messages = [OutMessage("Viewers have read-only access.")]
            return
        rows = self._pending_for(db, ctx)
        if not rows:
            reply.messages = [OutMessage("Nothing is waiting for your decision.")]
            return
        base = self.base_url_getter() if self.base_url_getter else None
        reply.messages = [render.approval_message(a, base) for a in rows[:LISTED]]
        if len(rows) > LISTED:
            reply.messages.append(OutMessage("More approvals are waiting in Foundry."))

    # ------------------------------------------------------------ buttons

    def _sent_to_link(
        self, db: Session, channel: ChatChannel, link: ChatLink, ref_type: str, ref_id: str
    ) -> bool:
        """Only buttons we actually delivered to this person count."""
        return (
            db.scalar(
                select(ChatMessage.id)
                .where(
                    ChatMessage.channel_id == channel.id,
                    ChatMessage.company_id == channel.company_id,
                    ChatMessage.link_id == link.id,
                    ChatMessage.direction == ChatDirection.OUT,
                    ChatMessage.ref_type == ref_type,
                    ChatMessage.ref_id == ref_id,
                    ChatMessage.status == ChatMessageStatus.SENT,
                )
                .limit(1)
            )
            is not None
        )

    def _callback(
        self,
        db: Session,
        channel: ChatChannel,
        link: ChatLink,
        ctx: CompanyContext,
        inbound: Inbound,
        reply: Reply,
    ) -> None:
        parts = (inbound.callback_data or "").split(":")
        stale = "That button is no longer valid."
        if len(parts) != 3 or parts[0] not in ("q", "a") or not parts[1]:
            reply.ack_text = stale
            return
        kind, ref_id, arg = parts
        if not self._sent_to_link(
            db, channel, link, "question" if kind == "q" else "approval", ref_id
        ):
            reply.ack_text = stale
            return
        if kind == "q":
            self._question_button(db, channel, link, ctx, ref_id, arg, reply)
        else:
            self._approval_button(db, channel, ctx, ref_id, arg, reply)

    def _question_button(
        self,
        db: Session,
        channel: ChatChannel,
        link: ChatLink,
        ctx: CompanyContext,
        question_id: str,
        arg: str,
        reply: Reply,
    ) -> None:
        if not questions_seam.available():
            reply.ack_text = "Founder questions are not available in this version."
            return
        q = questions_seam.get_question(db, ctx.company_id, question_id)
        if q is None:
            reply.ack_text = "That question no longer exists."
            reply.clear_buttons = True
            return
        if q.status != "open":
            reply.ack_text = "This question is already closed."
            reply.clear_buttons = True
            return
        options = [str(o) for o in (q.options or [])]
        if arg == "d":
            answer_text = q.default_answer
        elif arg.isdigit() and int(arg) < len(options):
            answer_text = options[int(arg)]
        else:
            reply.ack_text = "That button is no longer valid."
            return
        self._answer_question(db, channel, link, ctx, question_id, answer_text, reply)

    def _approval_button(
        self,
        db: Session,
        channel: ChatChannel,
        ctx: CompanyContext,
        approval_id: str,
        arg: str,
        reply: Reply,
    ) -> None:
        if arg not in ("approve", "decline"):
            reply.ack_text = "That button is no longer valid."
            return
        approval = db.scalar(
            select(Approval).where(
                Approval.id == approval_id, Approval.company_id == ctx.company_id
            )
        )
        if approval is None:
            reply.ack_text = "That request no longer exists."
            reply.clear_buttons = True
            return
        if approval.status != ApprovalStatus.PENDING:
            reply.ack_text = f"Already {approval.status}."
            reply.messages = [OutMessage(f"This request is already {approval.status}.")]
            reply.clear_buttons = True
            return
        if not render.approval_is_chat_decidable(approval):
            reply.ack_text = "Open Foundry to decide this one."
            reply.messages = [OutMessage("Open Foundry to decide this request.")]
            return
        decision = "approve" if arg == "approve" else "reject"
        label = {"telegram": "Telegram", "discord": "Discord", "whatsapp": "WhatsApp"}[channel.kind]
        title = approval.title
        try:
            approvals.decide(db, ctx, approval_id, decision, f"Decided from {label}")
        except AppError as exc:
            db.rollback()
            reply.ack_text = render.clean(exc.message, 150)
            reply.messages = [OutMessage(render.clean(exc.message, 300))]
            return
        audit.record(
            db,
            company_id=ctx.company_id,
            principal=ctx.principal,
            action="chat.approval_decided",
            target_type="approval",
            target_id=approval_id,
            summary=f"Decided an approval from {label}",
            data={"via": channel.kind, "decision": decision},
        )
        db.commit()
        verb = "Approved" if decision == "approve" else "Declined"
        reply.ack_text = verb
        reply.messages = [OutMessage(f"{verb}: {render.clean(title, 200)}")]
        reply.clear_buttons = True


__all__ = ["ChannelAuthError", "ChannelRouter"]
