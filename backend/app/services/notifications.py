"""In-app notifications (docs/specs/m7 section 5).

Kinds: ``task_assigned``, ``review_requested``, ``approval_needed``, ``note_replied``, ``alert``.
A notification belongs to one person (``user_id``); the producing code adds rows to the caller's
transaction (no commit). The company stream gets a ``notification.created`` event carrying only
ids -- never the title or body, because every member of the company can read the stream.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models.accounts import Notification
from app.models.company import CompanyMember
from app.models.enums import MemberRole, MemberStatus
from app.services import events, mail_dispatch
from app.services.pagination import paginate

TASK_ASSIGNED = "task_assigned"
REVIEW_REQUESTED = "review_requested"
APPROVAL_NEEDED = "approval_needed"
NOTE_REPLIED = "note_replied"
ALERT = "alert"


def notify_user(
    db: Session,
    user_id: str,
    kind: str,
    title: str,
    *,
    company_id: str | None = None,
    body: str | None = None,
    related_type: str | None = None,
    related_id: str | None = None,
) -> Notification:
    note = Notification(
        user_id=user_id,
        company_id=company_id,
        kind=kind,
        title=title[:300],
        body=body,
        related_type=related_type,
        related_id=related_id,
    )
    db.add(note)
    db.flush()
    if company_id:
        events.emit(
            db,
            company_id,
            "notification.created",
            {"notification_id": note.id, "user_id": user_id, "kind": kind},
        )
        # M8: sent after the commit, only to people who opted in, only with a working e-mail
        mail_dispatch.queue(db, user_id, company_id, kind, title, body)
    return note


def notify_member(
    db: Session, member_id: str | None, kind: str, title: str, **kwargs
) -> Notification | None:
    """Notify the person behind an active CompanyMember (a removed/disabled one hears nothing)."""
    if not member_id:
        return None
    member = db.get(CompanyMember, member_id)
    if member is None or member.status != MemberStatus.ACTIVE:
        return None
    return notify_user(db, member.user_id, kind, title, company_id=member.company_id, **kwargs)


def notify_admins(
    db: Session,
    company_id: str,
    kind: str,
    title: str,
    *,
    exclude_user_id: str | None = None,
    **kwargs,
) -> int:
    """Everyone who can act on company-level matters: active owners and admins."""
    stmt = select(CompanyMember.user_id).where(
        CompanyMember.company_id == company_id,
        CompanyMember.status == MemberStatus.ACTIVE,
        CompanyMember.role.in_([MemberRole.OWNER.value, MemberRole.ADMIN.value]),
    )
    count = 0
    for user_id in db.scalars(stmt):
        if user_id == exclude_user_id:
            continue
        notify_user(db, user_id, kind, title, company_id=company_id, **kwargs)
        count += 1
    return count


def list_for_user(
    db: Session, user_id: str, *, limit: int, cursor: str | None, unread_only: bool = False
) -> tuple[list[Notification], str | None, int]:
    stmt = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows, nxt = paginate(db, stmt, Notification, limit=limit, cursor=cursor, descending=True)
    return rows, nxt, unread_count(db, user_id)


def unread_count(db: Session, user_id: str) -> int:
    return int(
        db.scalar(
            select(func.count(Notification.id)).where(
                Notification.user_id == user_id, Notification.read_at.is_(None)
            )
        )
        or 0
    )


def mark_read(
    db: Session,
    user_id: str,
    *,
    ids: list[str] | None = None,
    all_: bool = False,
    now: datetime | None = None,
) -> int:
    """Mark the caller's own notifications read (other people's ids are silently ignored)."""
    stmt = (
        update(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        .values(read_at=now or utcnow())
    )
    if not all_:
        stmt = stmt.where(Notification.id.in_(ids or []))
    result = db.execute(stmt)
    db.commit()
    return int(result.rowcount or 0)
