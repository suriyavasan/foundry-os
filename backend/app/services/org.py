"""The org graph with human members: refs, managers, cycle checks, member lookups.

Every query is filtered by ``company_id``. A ref (``agent:<id>`` / ``member:<id>``) that points at
another company's record is indistinguishable from one that does not exist (404).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.refs import Ref, parse_ref, ref_of
from app.models.agent import Agent
from app.models.company import CompanyMember
from app.models.enums import AgentStatus, MemberRole, MemberStatus
from app.models.user import User

# Roles that can be given work, reviews and approvals, and can manage others.
ASSIGNABLE_ROLES = (MemberRole.OWNER, MemberRole.ADMIN, MemberRole.MEMBER)
_GONE_AGENT = (AgentStatus.RETIRED, AgentStatus.REJECTED)


def get_member(
    db: Session, company_id: str, member_id: str, *, include_removed: bool = False
) -> CompanyMember:
    member = db.scalar(
        select(CompanyMember).where(
            CompanyMember.id == member_id, CompanyMember.company_id == company_id
        )
    )
    if member is None or (not include_removed and member.status == MemberStatus.REMOVED):
        raise NotFound("Member not found")
    return member


def get_assignable_member(db: Session, company_id: str, member_id: str) -> CompanyMember:
    """An active member who may be given work (not a viewer, not removed/disabled)."""
    member = get_member(db, company_id, member_id)
    if member.status != MemberStatus.ACTIVE:
        raise Conflict("That member is not active")
    if member.role not in ASSIGNABLE_ROLES:
        raise ValidationFailed(
            "Viewers cannot be given work, reviews or approvals",
            details=[{"loc": ["body", "assignee_ref"], "msg": "member is a viewer"}],
        )
    return member


def active_members(db: Session, company_id: str) -> list[CompanyMember]:
    return list(
        db.scalars(
            select(CompanyMember)
            .where(
                CompanyMember.company_id == company_id,
                CompanyMember.status == MemberStatus.ACTIVE,
            )
            .order_by(CompanyMember.created_at, CompanyMember.id)
        )
    )


def member_user(db: Session, member: CompanyMember) -> User:
    user = db.get(User, member.user_id)
    assert user is not None
    return user


def member_name(db: Session, member_id: str | None) -> str | None:
    if not member_id:
        return None
    return db.scalar(
        select(User.display_name)
        .join(CompanyMember, CompanyMember.user_id == User.id)
        .where(CompanyMember.id == member_id)
    )


def names_of_members(db: Session, member_ids: set[str]) -> dict[str, str]:
    if not member_ids:
        return {}
    rows = db.execute(
        select(CompanyMember.id, User.display_name)
        .join(User, User.id == CompanyMember.user_id)
        .where(CompanyMember.id.in_(member_ids))
    ).all()
    return {mid: name for mid, name in rows}


def agent_manager_ref(agent: Agent) -> str | None:
    return ref_of(agent.manager_agent_id, agent.manager_member_id)


def _parent(db: Session, company_id: str, ref: Ref) -> Ref | None:
    if ref.is_agent:
        row = db.execute(
            select(Agent.manager_agent_id, Agent.manager_member_id).where(
                Agent.id == ref.id, Agent.company_id == company_id
            )
        ).first()
        if row is None:
            return None
        text = ref_of(row[0], row[1])
    else:
        text = db.scalar(
            select(CompanyMember.manager_ref).where(
                CompanyMember.id == ref.id, CompanyMember.company_id == company_id
            )
        )
    return parse_ref(text) if text else None


def check_no_cycle(db: Session, company_id: str, subject: Ref, new_manager: Ref) -> None:
    """Walking up from ``new_manager`` must never reach ``subject`` (agents and humans alike)."""
    seen: set[Ref] = set()
    current: Ref | None = new_manager
    while current is not None and current not in seen:
        if current == subject:
            raise Conflict("Manager assignment would create a reporting cycle")
        seen.add(current)
        current = _parent(db, company_id, current)


def resolve_manager(
    db: Session, company_id: str, value: str, *, subject: Ref | None, field: str = "manager_ref"
) -> Ref:
    """Validate a ``manager_ref``: same company, live, human managers must be able to manage."""
    ref = parse_ref(value, field=field)
    if ref.is_agent:
        agent = db.scalar(select(Agent).where(Agent.id == ref.id, Agent.company_id == company_id))
        if agent is None:
            raise NotFound("Manager not found")
        if agent.status in _GONE_AGENT:
            raise Conflict("A retired or rejected agent cannot be a manager")
    else:
        get_assignable_member(db, company_id, ref.id)
    if subject is not None:
        check_no_cycle(db, company_id, subject, ref)
    return ref
