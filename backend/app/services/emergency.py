"""The instance-wide emergency stop (docs/specs/m6 section 3).

One row in ``system_state`` (so it survives restarts). While it is active nothing is dispatched,
schedules and wakes do not fire, queued runs are cancelled on claim, the agent API answers 503 and
every live run was cancelled when the stop was pressed -- in EVERY company. Only an owner can press
it or lift it; an agent, even with a run token, gets 403.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden
from app.core.principal import Principal
from app.core.time import utcnow
from app.models.company import Company, CompanyMember
from app.models.enums import MemberRole
from app.models.guardrails import SystemState
from app.models.user import User
from app.services import audit, events

KEY = "emergency_stop"
CANCEL_REASON = "emergency_stop"
INACTIVE: dict[str, Any] = {
    "active": False,
    "activated_at": None,
    "activated_by": None,
    "reason": None,
    "runs_cancelled": None,
}


def _row(db: Session) -> SystemState | None:
    return db.get(SystemState, KEY)


def status(db: Session) -> dict[str, Any]:
    row = _row(db)
    if row is None or not (row.value or {}).get("active"):
        return dict(INACTIVE)
    value = row.value
    return {
        "active": True,
        "activated_at": datetime.fromisoformat(value["activated_at"])
        if value.get("activated_at")
        else None,
        "activated_by": value.get("activated_by"),
        "reason": value.get("reason"),
        "runs_cancelled": value.get("runs_cancelled"),
    }


def is_active(db: Session) -> bool:
    row = _row(db)
    return bool(row is not None and (row.value or {}).get("active"))


def require_owner(db: Session, principal: Principal) -> None:
    """The installation's owner (the account that claimed the install) who owns a company.
    The switch is instance-wide (every company), so with several accounts on one install (M7) the
    owner of some other company does not get it. Agents (run tokens included), members, viewers
    and other accounts: 403."""
    if not principal.is_human:
        raise Forbidden("Only a human owner can use the emergency stop")
    user = db.get(User, principal.id)
    if user is None or not user.is_local_owner:
        raise Forbidden("Only the installation owner can use the instance-wide emergency stop")
    owns = db.scalar(
        select(CompanyMember.id).where(
            CompanyMember.user_id == principal.id, CompanyMember.role == MemberRole.OWNER
        )
    )
    if owns is None:
        raise Forbidden("Only a company owner can use the emergency stop")


def stop(db: Session, principal: Principal, reason: str | None) -> dict[str, Any]:
    require_owner(db, principal)
    if is_active(db):
        return status(db)
    from app.services import runs  # local: runs -> safety -> ... must not import this module first

    now = utcnow()
    cancelled = 0
    company_ids = list(db.scalars(select(Company.id)))
    for company_id in company_ids:
        cancelled += runs.request_cancel_all(db, company_id, CANCEL_REASON, principal)
    value = {
        "active": True,
        "activated_at": now.isoformat(),
        "activated_by": principal.label,
        "reason": (reason or "").strip() or None,
        "runs_cancelled": cancelled,
    }
    row = _row(db)
    if row is None:
        db.add(SystemState(key=KEY, value=value, updated_at=now))
    else:
        row.value, row.updated_at = value, now
    for company_id in company_ids:
        audit.record(
            db,
            company_id=company_id,
            principal=principal,
            action="system.emergency_stop",
            target_type="system",
            target_id=None,
            summary="EMERGENCY STOP: all runs cancelled, dispatch disabled until the owner resumes",
            data={"reason": value["reason"], "runs_cancelled": cancelled},
        )
        events.emit(
            db,
            company_id,
            "system.emergency_stop",
            {"reason": value["reason"], "activated_by": principal.label},
        )
    db.commit()
    return status(db)


def resume(db: Session, principal: Principal) -> dict[str, Any]:
    require_owner(db, principal)
    if not is_active(db):
        return status(db)
    row = _row(db)
    assert row is not None
    row.value, row.updated_at = {"active": False}, utcnow()
    for company_id in db.scalars(select(Company.id)):
        audit.record(
            db,
            company_id=company_id,
            principal=principal,
            action="system.resumed",
            target_type="system",
            target_id=None,
            summary="Emergency stop lifted: dispatch resumes",
            data={},
        )
        events.emit(db, company_id, "system.resumed", {"resumed_by": principal.label})
    db.commit()
    return status(db)
