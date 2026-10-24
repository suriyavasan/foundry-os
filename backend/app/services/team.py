"""Team: members as org nodes, roles, invitations, removal (docs/specs/m7 sections 3-4).

Role matrix (enforced here, not only in routes):

* owner  -- everything, including other owners and admins;
* admin  -- invite / change / remove ``member`` and ``viewer`` only; never owners or admins;
* member -- reads the company, works on what is assigned to them; no team changes;
* viewer -- read-only;
* agents -- never (guardrail tier ``identity``, human-only).

Nobody changes their own role or removes themselves, and a company always keeps one owner.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.principal import CompanyContext, Principal, require_human_admin
from app.core.refs import Ref, parse_ref
from app.core.time import utcnow
from app.models.accounts import Invitation
from app.models.agent import Agent
from app.models.company import Company, CompanyMember
from app.models.enums import AgentKind, AgentStatus, InvitationStatus, MemberRole, MemberStatus
from app.models.governance import Approval
from app.models.user import User
from app.schemas import team as schemas
from app.schemas.agent_api import AgentMember
from app.services import audit, events, org
from app.services.auth import AuthService, digest, normalize_email
from app.services.pagination import paginate

ROLES = tuple(r.value for r in MemberRole)
_ADMIN_MANAGEABLE = (MemberRole.MEMBER.value, MemberRole.VIEWER.value)


class EmailMismatch(Forbidden):
    code = "email_mismatch"
    default_message = "This invitation was sent to a different email address"


class AccountExists(Conflict):
    code = "account_exists"
    default_message = "An account with this email already exists; sign in and accept again"


# ---------------------------------------------------------------- role matrix


def _may_touch(ctx: CompanyContext, target_role: str) -> bool:
    if ctx.role == MemberRole.OWNER.value:
        return True
    return ctx.role == MemberRole.ADMIN.value and target_role in _ADMIN_MANAGEABLE


def _may_grant(ctx: CompanyContext, role: str) -> bool:
    return _may_touch(ctx, role)


def _owner_count(db: Session, company_id: str, *, excluding: str | None = None) -> int:
    stmt = select(func.count(CompanyMember.id)).where(
        CompanyMember.company_id == company_id,
        CompanyMember.role == MemberRole.OWNER.value,
        CompanyMember.status == MemberStatus.ACTIVE,
    )
    if excluding:
        stmt = stmt.where(CompanyMember.id != excluding)
    return int(db.scalar(stmt) or 0)


# ---------------------------------------------------------------- members


def to_member(member: CompanyMember, user: User, *, show_email: bool) -> schemas.Member:
    return schemas.Member(
        id=member.id,
        company_id=member.company_id,
        user_id=member.user_id,
        username=user.username or "",
        display_name=user.display_name,
        email=user.email if show_email else None,
        role=member.role,  # type: ignore[arg-type]
        title=member.title,
        manager_ref=member.manager_ref,
        status=member.status,  # type: ignore[arg-type]
        created_at=member.created_at,
    )


def _show_email(ctx: CompanyContext, member: CompanyMember) -> bool:
    return ctx.is_human_admin or member.id == ctx.member_id


def list_members(
    db: Session,
    ctx: CompanyContext,
    *,
    limit: int,
    cursor: str | None,
    include_removed: bool = False,
) -> tuple[list[schemas.Member], str | None]:
    if ctx.principal.is_agent:
        # Agents see colleagues through the agent API (names, titles, capacity), never accounts.
        raise Forbidden("Agents cannot list accounts")
    stmt = select(CompanyMember).where(CompanyMember.company_id == ctx.company_id)
    if not include_removed:
        stmt = stmt.where(CompanyMember.status != MemberStatus.REMOVED)
    rows, nxt = paginate(db, stmt, CompanyMember, limit=limit, cursor=cursor, descending=False)
    users = {
        u.id: u for u in db.scalars(select(User).where(User.id.in_([m.user_id for m in rows])))
    }
    return [to_member(m, users[m.user_id], show_email=_show_email(ctx, m)) for m in rows], nxt


def get_member_view(db: Session, ctx: CompanyContext, member_id: str) -> schemas.Member:
    if ctx.principal.is_agent:
        raise Forbidden("Agents cannot read accounts")
    member = org.get_member(db, ctx.company_id, member_id, include_removed=True)
    return to_member(member, org.member_user(db, member), show_email=_show_email(ctx, member))


def _label(db: Session, member: CompanyMember) -> str:
    return org.member_user(db, member).display_name


def update_member(
    db: Session, ctx: CompanyContext, member_id: str, data: schemas.MemberUpdate
) -> schemas.Member:
    require_human_admin(ctx, "change members")
    member = org.get_member(db, ctx.company_id, member_id)
    fields = data.model_fields_set
    is_self = member.id == ctx.member_id
    changed: list[str] = []
    if "role" in fields:
        if data.role is None:
            raise ValidationFailed("role must not be null")
        if is_self and data.role != member.role:
            raise Forbidden("You cannot change your own role")
        if data.role != member.role:
            if not _may_touch(ctx, member.role) or not _may_grant(ctx, data.role):
                raise Forbidden("Admins can only manage members and viewers; ask an owner")
            if member.role == MemberRole.OWNER.value and not _owner_count(
                db, ctx.company_id, excluding=member.id
            ):
                raise Conflict("A company needs at least one owner")
            member.role = data.role
            changed.append("role")
            if data.role == MemberRole.VIEWER.value:  # viewers cannot hold work
                release_work(db, member, ctx.principal, reason="role changed to viewer")
    if ("title" in fields or "manager_ref" in fields) and not (
        is_self or _may_touch(ctx, member.role)
    ):
        raise Forbidden("Admins can only manage members and viewers; ask an owner")
    if "title" in fields:
        title = (data.title or "").strip() or None
        if title != member.title:
            member.title = title
            changed.append("title")
    if "manager_ref" in fields:
        if data.manager_ref is None:
            new_ref = None
        else:
            new_ref = str(
                org.resolve_manager(
                    db, ctx.company_id, data.manager_ref, subject=Ref("member", member.id)
                )
            )
        if new_ref != member.manager_ref:
            member.manager_ref = new_ref
            changed.append("manager_ref")
    if changed:
        user = org.member_user(db, member)
        audit.record(
            db,
            company_id=ctx.company_id,
            principal=ctx.principal,
            action="member.updated",
            target_type="member",
            target_id=member.id,
            summary=f"Updated {user.display_name}: {', '.join(changed)}",
            data={"changed": changed, "role": member.role},
        )
        events.emit(db, ctx.company_id, "member.updated", {"member_id": member.id})
        db.commit()
    return to_member(member, org.member_user(db, member), show_email=_show_email(ctx, member))


def remove_member(db: Session, ctx: CompanyContext, member_id: str, auth: AuthService) -> None:
    require_human_admin(ctx, "remove members")
    member = org.get_member(db, ctx.company_id, member_id)
    if member.id == ctx.member_id:
        raise Forbidden("You cannot remove yourself")
    if not _may_touch(ctx, member.role):
        raise Forbidden("Admins can only manage members and viewers; ask an owner")
    if member.role == MemberRole.OWNER.value and not _owner_count(
        db, ctx.company_id, excluding=member.id
    ):
        raise Conflict("A company needs at least one owner")
    now = utcnow()
    user = org.member_user(db, member)
    release_work(db, member, ctx.principal, reason="member removed")
    member.status = MemberStatus.REMOVED.value
    member.removed_at = now
    # Their credentials die with their last membership; a person who still belongs to another
    # company keeps their sessions there (the company context check already shuts this one out).
    others = db.scalar(
        select(func.count(CompanyMember.id)).where(
            CompanyMember.user_id == member.user_id,
            CompanyMember.id != member.id,
            CompanyMember.status == MemberStatus.ACTIVE,
        )
    )
    credentials_revoked = not others
    if credentials_revoked:
        auth.revoke_all_credentials(db, member.user_id, now)
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="member.removed",
        target_type="member",
        target_id=member.id,
        summary=f"Removed {user.display_name} from the company",
        data={"role": member.role, "credentials_revoked": credentials_revoked},
    )
    events.emit(db, ctx.company_id, "member.removed", {"member_id": member.id})
    db.commit()


def _first_active_founder(db: Session, company_id: str) -> Agent | None:
    return db.scalar(
        select(Agent)
        .where(
            Agent.company_id == company_id,
            Agent.kind == AgentKind.FOUNDER.value,
            Agent.status == AgentStatus.ACTIVE.value,
        )
        .order_by(Agent.created_at, Agent.id)
        .limit(1)
    )


def handover_target(db: Session, member: CompanyMember) -> Ref | None:
    """Who inherits a leaving member's work and reports: their manager if still active, else the
    AI Co-founder, else nobody (tasks become unassigned)."""
    if member.manager_ref:
        try:
            ref = parse_ref(member.manager_ref)
        except ValidationFailed:
            ref = None
        if ref is not None and ref.is_agent:
            agent = db.scalar(
                select(Agent).where(
                    Agent.id == ref.id,
                    Agent.company_id == member.company_id,
                    Agent.status == AgentStatus.ACTIVE.value,
                )
            )
            if agent is not None:
                return ref
        elif ref is not None and ref.id != member.id:
            other = db.scalar(
                select(CompanyMember).where(
                    CompanyMember.id == ref.id,
                    CompanyMember.company_id == member.company_id,
                    CompanyMember.status == MemberStatus.ACTIVE.value,
                    CompanyMember.role.in_([r.value for r in org.ASSIGNABLE_ROLES]),
                )
            )
            if other is not None:
                return ref
    founder = _first_active_founder(db, member.company_id)
    return Ref("agent", founder.id) if founder else None


def release_work(
    db: Session, member: CompanyMember, principal: Principal | None, *, reason: str
) -> None:
    """Hand a leaving (or demoted) member's tasks, reports, reviews and approvals to their manager
    or the AI Co-founder. History is kept: nothing is deleted. No commit."""
    from app.services import tasks  # local: tasks depends on notifications/org

    target = handover_target(db, member)
    tasks.reassign_member_tasks(db, member, target, principal, reason=reason)
    db.execute(
        update(Approval)
        .where(Approval.approver_member_id == member.id)
        .values(approver_member_id=None)
    )
    target_text = str(target) if target else None
    # agents that reported to this person
    for agent in db.scalars(select(Agent).where(Agent.manager_member_id == member.id)):
        agent.manager_member_id = target.id if target and target.is_member else None
        agent.manager_agent_id = target.id if target and target.is_agent else agent.manager_agent_id
    # people that reported to this person
    for other in db.scalars(
        select(CompanyMember).where(
            CompanyMember.company_id == member.company_id,
            CompanyMember.manager_ref == f"member:{member.id}",
        )
    ):
        other.manager_ref = target_text


# ---------------------------------------------------------------- invitations


def invitation_status(inv: Invitation, now: datetime) -> str:
    if inv.revoked_at is not None:
        return InvitationStatus.REVOKED.value
    if inv.accepted_at is not None:
        return InvitationStatus.ACCEPTED.value
    if now >= inv.expires_at:
        return InvitationStatus.EXPIRED.value
    return InvitationStatus.PENDING.value


def to_invitation(inv: Invitation, now: datetime) -> schemas.Invitation:
    return schemas.Invitation(
        id=inv.id,
        company_id=inv.company_id,
        email=inv.email,
        role=inv.role,  # type: ignore[arg-type]
        title=inv.title,
        manager_ref=inv.manager_ref,
        status=invitation_status(inv, now),  # type: ignore[arg-type]
        expires_at=inv.expires_at,
        created_at=inv.created_at,
        accepted_at=inv.accepted_at,
    )


def _mask(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


def create_invitation(
    db: Session,
    ctx: CompanyContext,
    data: schemas.InvitationCreate,
    settings: Settings,
    *,
    now: datetime | None = None,
    ic=None,
) -> schemas.InvitationCreated:
    require_human_admin(ctx, "invite people")
    now = now or utcnow()
    email = normalize_email(data.email)
    if email is None:
        raise ValidationFailed(
            "Enter an email address",
            details=[{"loc": ["body", "email"], "msg": "required"}],
        )
    if not _may_grant(ctx, data.role):
        raise Forbidden("Admins can only invite members and viewers; ask an owner")
    manager_ref: str | None = None
    if data.manager_ref:
        manager_ref = str(org.resolve_manager(db, ctx.company_id, data.manager_ref, subject=None))
    already = db.scalar(
        select(CompanyMember.id)
        .join(User, User.id == CompanyMember.user_id)
        .where(
            CompanyMember.company_id == ctx.company_id,
            CompanyMember.status != MemberStatus.REMOVED.value,
            User.email == email,
        )
    )
    if already:
        raise Conflict("That person is already a member of this company")
    for old in db.scalars(
        select(Invitation).where(
            Invitation.company_id == ctx.company_id,
            Invitation.email == email,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
    ):
        old.revoked_at = now  # a re-invite replaces the previous link
    raw = secrets.token_urlsafe(32)
    inv = Invitation(
        company_id=ctx.company_id,
        email=email,
        role=data.role,
        title=(data.title or "").strip() or None,
        manager_ref=manager_ref,
        token_hash=digest(raw),
        expires_at=now + timedelta(days=settings.invitation_ttl_days),
        created_at=now,
        created_by_user_id=ctx.principal.id,
    )
    db.add(inv)
    db.flush()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="invitation.created",
        target_type="invitation",
        target_id=inv.id,
        summary=f"Invited {_mask(email)} as {inv.role}",
        data={"role": inv.role, "delivery": "link"},
    )
    events.emit(db, ctx.company_id, "invitation.created", {"invitation_id": inv.id})
    db.commit()
    base = to_invitation(inv, now)
    delivery, invite_url, token, note = "link", f"/invite/{raw}", raw, None
    if ic is not None:  # M8: the company's own e-mail identity, when it works
        delivery, invite_url, token, note = _deliver_invitation(db, ctx, ic, inv, raw, email, now)
    return schemas.InvitationCreated(
        **base.model_dump(),
        delivery=delivery,  # type: ignore[arg-type]
        invite_url=invite_url,
        token=token,
        delivery_note=note,
    )


def _deliver_invitation(db, ctx, ic, inv, raw, email, now):
    """E-mail the link from the company mailbox. On success the token exists only in that message
    (the response carries no link); without a working identity the link is shown once, as in M7."""
    from app.models.company import Company
    from app.services import email_identity, mail_dispatch

    link = f"/invite/{raw}", raw
    if not email_identity.email_connected(db, ctx.company_id):
        return "link", link[0], link[1], None
    company = db.get(Company, ctx.company_id)
    name = company.name if company else "the company"
    url = f"{mail_dispatch.base_url(ic)}/invite/{raw}"
    body = (
        f"{ctx.principal.label} invited you to join {name} on Foundry AI OS as {inv.role}.\n\n"
        f"Accept the invitation: {url}\n\n"
        f"The link works once and expires on {inv.expires_at:%Y-%m-%d}. If you did not expect "
        "this, ignore this message."
    )
    sent = email_identity.send_system_email(
        db, ic, ctx.company_id, email, f"You are invited to {name}", body
    )
    if not sent:
        db.rollback()
        return "link", link[0], link[1], "The e-mail could not be sent; share the link yourself."
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="invitation.emailed",
        target_type="invitation",
        target_id=inv.id,
        summary=f"Emailed the invitation to {_mask(email)}",
        data={"delivery": "email"},
    )
    db.commit()
    return "email", None, None, None


def list_invitations(
    db: Session, ctx: CompanyContext, *, limit: int, cursor: str | None
) -> tuple[list[schemas.Invitation], str | None]:
    require_human_admin(ctx, "see invitations")
    rows, nxt = paginate(
        db,
        select(Invitation).where(Invitation.company_id == ctx.company_id),
        Invitation,
        limit=limit,
        cursor=cursor,
        descending=True,
    )
    now = utcnow()
    return [to_invitation(r, now) for r in rows], nxt


def revoke_invitation(db: Session, ctx: CompanyContext, invitation_id: str) -> None:
    require_human_admin(ctx, "revoke invitations")
    inv = db.scalar(
        select(Invitation).where(
            Invitation.id == invitation_id, Invitation.company_id == ctx.company_id
        )
    )
    if inv is None:
        raise NotFound("Invitation not found")
    if not _may_grant(ctx, inv.role):
        raise Forbidden("Admins can only manage members and viewers; ask an owner")
    if inv.accepted_at is not None:
        raise Conflict("That invitation was already accepted")
    if inv.revoked_at is None:
        inv.revoked_at = utcnow()
        audit.record(
            db,
            company_id=ctx.company_id,
            principal=ctx.principal,
            action="invitation.revoked",
            target_type="invitation",
            target_id=inv.id,
            summary=f"Revoked the invitation for {_mask(inv.email)}",
        )
        events.emit(db, ctx.company_id, "invitation.revoked", {"invitation_id": inv.id})
        db.commit()


def accept_invitation(
    db: Session,
    auth: AuthService,
    token: str,
    data: schemas.InvitationAccept,
    *,
    caller: User | None,
    ip: str | None,
    user_agent: str | None,
    now: datetime | None = None,
) -> tuple[schemas.InvitationAccepted, str | None]:
    """Join the company. Signed in: the invited email must be the account's email. Otherwise the
    body carries the new account's credentials (the email of the invitation becomes the account's
    email). Returns the result and, for a freshly created account, the new session's cookie secret.
    Unknown, expired, used and revoked tokens are all the same 404."""
    now = now or utcnow()
    gone = NotFound("This invitation is not valid")
    if not token or len(token) > 128:
        raise gone
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == digest(token)))
    if inv is None or invitation_status(inv, now) != InvitationStatus.PENDING.value:
        raise gone
    company = db.get(Company, inv.company_id)
    if company is None:
        raise gone

    session_secret: str | None = None
    if caller is not None:
        if not caller.email or caller.email.lower() != inv.email.lower():
            raise EmailMismatch()
        user = caller
    else:
        if not (data.username and data.display_name and data.password):
            raise ValidationFailed(
                "Choose a username, display name and password to create your account",
                details=[
                    {"loc": ["body", f], "msg": "required"}
                    for f in ("username", "display_name", "password")
                    if not getattr(data, f)
                ],
            )
        if db.scalar(select(User.id).where(User.email == inv.email)):
            raise AccountExists()
        user = auth.create_user(
            db,
            username=data.username,
            display_name=data.display_name,
            password=data.password,
            email=inv.email,
            now=now,
        )

    member = db.scalar(
        select(CompanyMember).where(
            CompanyMember.company_id == inv.company_id, CompanyMember.user_id == user.id
        )
    )
    if member is not None and member.status == MemberStatus.ACTIVE.value:
        raise Conflict("You are already a member of this company")

    # Single use, atomically: a second concurrent accept updates nothing.
    consumed = db.execute(
        update(Invitation)
        .where(
            Invitation.id == inv.id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > now,
        )
        .values(accepted_at=now, accepted_by_user_id=user.id)
    )
    if consumed.rowcount != 1:
        db.rollback()
        raise gone

    manager_ref = inv.manager_ref
    if manager_ref:  # the manager may have left since the invitation was written
        try:
            org.resolve_manager(db, inv.company_id, manager_ref, subject=None)
        except (NotFound, Conflict, ValidationFailed):
            manager_ref = None
    if member is None:
        member = CompanyMember(
            company_id=inv.company_id,
            user_id=user.id,
            role=inv.role,
            title=inv.title,
            manager_ref=manager_ref,
            status=MemberStatus.ACTIVE.value,
        )
        db.add(member)
    else:  # coming back after having been removed
        member.role = inv.role
        member.title = inv.title
        member.manager_ref = manager_ref
        member.status = MemberStatus.ACTIVE.value
        member.removed_at = None
    db.flush()
    if caller is None:
        _, session_secret = auth.create_session(db, user, ip=ip, user_agent=user_agent, now=now)
    principal = Principal(type=auth.principal_for(user).type, id=user.id, label=user.display_name)
    audit.record(
        db,
        company_id=inv.company_id,
        principal=principal,
        action="member.joined",
        target_type="member",
        target_id=member.id,
        summary=f"{user.display_name} joined as {member.role}",
        data={"role": member.role, "invitation_id": inv.id},
    )
    events.emit(db, inv.company_id, "member.joined", {"member_id": member.id})
    db.commit()
    return (
        schemas.InvitationAccepted(
            company_id=company.id,
            company_name=company.name,
            role=member.role,  # type: ignore[arg-type]
            member_id=member.id,
        ),
        session_secret,
    )


def colleagues_for_agent(db: Session, ctx: CompanyContext) -> list[AgentMember]:
    """The people an agent can hand work to: name, title, role and load. No email / username."""
    from app.models.enums import TaskStatus
    from app.models.work import Task

    members = org.active_members(db, ctx.company_id)
    if not members:
        return []
    names = org.names_of_members(db, {m.id for m in members})
    load = dict(
        db.execute(
            select(Task.assignee_member_id, func.count(Task.id))
            .where(
                Task.company_id == ctx.company_id,
                Task.assignee_member_id.in_([m.id for m in members]),
                Task.status.notin_([TaskStatus.DONE.value, TaskStatus.CANCELLED.value]),
            )
            .group_by(Task.assignee_member_id)
        ).all()
    )
    return [
        AgentMember(
            ref=f"member:{m.id}",
            name=names.get(m.id, ""),
            title=m.title,
            role=m.role,
            manager_ref=m.manager_ref,
            open_tasks=int(load.get(m.id, 0)),
        )
        for m in members
        if m.role in [r.value for r in org.ASSIGNABLE_ROLES]
    ]
