"""E-mail for the platform's own messages: invitations and (opt-in) notifications.

Notification rows are created inside other transactions. To send nothing for a transaction that
rolls back, and never to make an API call wait for a mail provider, ``queue`` only remembers the
message on the session; after a successful COMMIT the message goes to a small thread pool, which
re-checks everything in its own session: the person opted in to that kind, has an address, and the
company has a working e-mail identity. Content is checked for credential-shaped strings first.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from app.guardrails import dlp
from app.integrations.context import IntegrationContext
from app.models.company import Company
from app.models.reachability import NotificationPreference
from app.models.user import User
from app.services import email_identity

logger = logging.getLogger("company_os.mail")
INFO_KEY = "pending_emails"
NOTIFICATION_KINDS = (
    "task_assigned",
    "review_requested",
    "approval_needed",
    "note_replied",
    "alert",
)


def base_url(ic: IntegrationContext) -> str:
    st = ic.supervisor.status()
    if st["state"] == "running" and st["url"]:
        return st["url"].rstrip("/")
    return ic.settings.public_url.rstrip("/")


class EmailDispatcher:
    def __init__(
        self,
        factory: sessionmaker[Session],
        ic_getter,
        *,
        synchronous: bool = False,
    ) -> None:
        self._factory = factory
        self._ic = ic_getter
        self.synchronous = synchronous
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mail")
        event.listen(factory, "after_commit", self._after_commit)
        event.listen(factory, "after_rollback", self._discard)
        event.listen(factory, "after_soft_rollback", self._discard_soft)

    # -- session hooks
    def _after_commit(self, session: Session) -> None:
        for item in session.info.pop(INFO_KEY, []):
            if self.synchronous:
                self.deliver(item)
            else:
                self._pool.submit(self._safe, item)

    def _discard(self, session: Session) -> None:
        session.info.pop(INFO_KEY, None)

    def _discard_soft(self, session: Session, previous_transaction: Any) -> None:
        if previous_transaction.parent is None:
            session.info.pop(INFO_KEY, None)

    def _safe(self, item: dict[str, Any]) -> None:
        try:
            self.deliver(item)
        except Exception:
            logger.warning("notification e-mail failed", exc_info=False)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # -- sending
    def deliver(self, item: dict[str, Any]) -> bool:
        ic: IntegrationContext = self._ic()
        with self._factory() as db:
            user = db.get(User, item["user_id"])
            if user is None or not user.email:
                return False
            pref = db.get(NotificationPreference, user.id)
            if item["kind"] not in (pref.email_kinds if pref else []):
                return False
            company = db.get(Company, item["company_id"])
            if company is None:
                return False
            text = f"{item['title']}\n\n{item.get('body') or ''}".strip()
            if any(f.kind in ("secret", "own_token") for f in dlp.scan_text(text)):
                return False
            body = (
                f"{text}\n\nOpen Company OS: {base_url(ic)}\n\n"
                "You get this e-mail because you turned on e-mail notifications for this kind of "
                "event. Change it in Settings."
            )
            sent = email_identity.send_system_email(
                db, ic, company.id, user.email, f"[{company.name}] {item['title']}"[:200], body
            )
            db.commit()
            return sent


def queue(
    db: Session, user_id: str, company_id: str | None, kind: str, title: str, body: str | None
) -> None:
    """Remember a notification e-mail on the session; it is sent after the commit."""
    if company_id is None or kind not in NOTIFICATION_KINDS:
        return
    db.info.setdefault(INFO_KEY, []).append(
        {"user_id": user_id, "company_id": company_id, "kind": kind, "title": title, "body": body}
    )


def get_preferences(db: Session, user_id: str) -> list[str]:
    pref = db.get(NotificationPreference, user_id)
    return list(pref.email_kinds) if pref else []


def set_preferences(db: Session, user_id: str, kinds: list[str]) -> list[str]:
    from app.core.errors import ValidationFailed
    from app.core.time import utcnow

    unknown = [k for k in kinds if k not in NOTIFICATION_KINDS]
    if unknown:
        raise ValidationFailed(f"Unknown notification kind(s): {', '.join(unknown)}")
    clean = [k for k in NOTIFICATION_KINDS if k in kinds]
    pref = db.get(NotificationPreference, user_id)
    if pref is None:
        db.add(NotificationPreference(user_id=user_id, email_kinds=clean, updated_at=utcnow()))
    else:
        pref.email_kinds = clean
        pref.updated_at = utcnow()
    db.commit()
    return clean


def any_email_identity(db: Session, user_id: str) -> bool:
    """Whether some company the person belongs to has a working e-mail identity."""
    from app.models.company import CompanyMember
    from app.models.enums import MemberStatus

    ids = list(
        db.scalars(
            select(CompanyMember.company_id).where(
                CompanyMember.user_id == user_id, CompanyMember.status == MemberStatus.ACTIVE
            )
        )
    )
    return any(email_identity.email_connected(db, cid) for cid in ids)
