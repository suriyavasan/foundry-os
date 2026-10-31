"""Approvals: each authorizes exactly one scoped action, once.

* ``approver`` is derived from policy at creation: kinds in ``human_approval_required_for``
  (or any kind when the company is ``supervised``) need a human; the rest are decided by the
  founder agent.
* ``consume`` is the enforcement primitive other services call before doing the approved
  thing: it succeeds only for an approved, unconsumed approval whose scope hash matches, and
  marks it consumed atomically (conditional UPDATE).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, InvalidTransition, NotFound, ValidationFailed
from app.core.principal import CompanyContext, Principal, Role
from app.core.refs import parse_ref
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.company import Company
from app.models.enums import (
    AgentKind,
    AgentStatus,
    ApprovalStatus,
    Approver,
    AutonomyMode,
    CompanyStatus,
    TaskStatus,
)
from app.models.governance import Approval
from app.models.policy import CompanyPolicy
from app.schemas.governance import ApprovalCreate
from app.services import audit, events, notifications, org, policy
from app.services.agents import _find_secret_paths
from app.services.common import get_task_or_404
from app.services.pagination import paginate

logger = logging.getLogger("company_os.approvals")
_UNSET: Any = object()
SWEEP_BATCH = 100
_DECISIONS = {
    "approve": (ApprovalStatus.APPROVED, "approval.approved", "Approved"),
    "reject": (ApprovalStatus.REJECTED, "approval.rejected", "Rejected"),
    "request_changes": (
        ApprovalStatus.CHANGES_REQUESTED,
        "approval.changes_requested",
        "Requested changes on",
    ),
}


def canonical_scope(scope: dict[str, Any]) -> str:
    return json.dumps(scope, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def compute_scope_hash(scope: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_scope(scope).encode()).hexdigest()


# M22: outward-facing and money: a person decides, whatever the policy or autonomy mode says.
ALWAYS_HUMAN_KINDS = frozenset({"outsource_job"})


def derive_approver(limits: CompanyPolicy, kind: str) -> Approver:
    if kind in ALWAYS_HUMAN_KINDS:
        return Approver.HUMAN
    if limits.autonomy_mode == AutonomyMode.SUPERVISED:
        return Approver.HUMAN
    return Approver.HUMAN if kind in limits.human_approval_required_for else Approver.FOUNDER


def get_approval(db: Session, ctx: CompanyContext, approval_id: str) -> Approval:
    approval = db.scalar(
        select(Approval).where(Approval.id == approval_id, Approval.company_id == ctx.company_id)
    )
    if approval is None:
        raise NotFound("Approval not found")
    return approval


def list_approvals(
    db: Session,
    ctx: CompanyContext,
    *,
    limit: int,
    cursor: str | None,
    status: str | None = None,
    approver: str | None = None,
    tier: str | None = None,
):
    stmt = select(Approval).where(Approval.company_id == ctx.company_id)
    if tier:
        stmt = stmt.where(Approval.tier == tier)
    if status:
        stmt = stmt.where(Approval.status == status)
    if approver:
        stmt = stmt.where(Approval.approver == approver)
    return paginate(db, stmt, Approval, limit=limit, cursor=cursor, descending=True)


def create_approval(
    db: Session, ctx: CompanyContext, data: ApprovalCreate, *, default_task_id: str | None = None
) -> Approval:
    if ctx.role in (Role.VIEWER, Role.MEMBER):
        raise Forbidden("Only owners, admins and agents can request approvals")
    if data.kind in ALWAYS_HUMAN_KINDS:
        raise ValidationFailed(
            "An outsourcing approval is created with the job: HR drafts it with "
            "company_draft_outsource_job",
            details=[{"loc": ["body", "kind"], "msg": "not requestable here"}],
        )
    approver_member_id: str | None = None
    if data.approver_ref:
        if not ctx.is_human_admin:
            raise Forbidden("Only company owners and admins can route an approval to a person")
        ref = parse_ref(data.approver_ref, field="approver_ref")
        if not ref.is_member:
            raise ValidationFailed(
                "approver_ref must be a member (member:<id>)",
                details=[{"loc": ["body", "approver_ref"], "msg": "not a member reference"}],
            )
        approver_member_id = org.get_assignable_member(db, ctx.company_id, ref.id).id
    for blob, name in ((data.evidence, "evidence"), (data.scope, "scope")):
        if _find_secret_paths(blob):
            raise ValidationFailed(
                f"{name} must not contain secrets",
                details=[{"loc": ["body", name], "msg": "key looks like a secret"}],
            )
    task_id = data.task_id or default_task_id
    if task_id:
        get_task_or_404(db, ctx.company_id, task_id)
    approval = create_approval_record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        kind=data.kind,
        title=data.title,
        reason=data.reason,
        amount_micros=data.amount_micros,
        vendor=data.vendor,
        recurrence=data.recurrence,
        risk=data.risk,
        evidence=data.evidence,
        scope=data.scope,
        task_id=task_id,
        approver=Approver.HUMAN if approver_member_id else None,
        approver_member_id=approver_member_id,
    )
    db.commit()
    return approval


def create_approval_record(
    db: Session,
    *,
    company_id: str,
    principal: Principal,
    kind: str,
    title: str,
    reason: str | None = None,
    amount_micros: int | None = None,
    vendor: str | None = None,
    recurrence: str | None = None,
    risk: str = "low",
    evidence: dict[str, Any] | None = None,
    scope: dict[str, Any] | None = None,
    task_id: str | None = None,
    subject: tuple[str, str] | None = None,
    approver: Approver | None = None,
    tier: str | None = None,
    action: str | None = None,
    reviewer_agent_id: str | None = None,
    outbound: dict[str, Any] | None = None,
    dlp_findings: list[dict[str, Any]] | None = None,
    requested_by_agent_id: Any = _UNSET,
    approver_member_id: str | None = None,
) -> Approval:
    """Insert an approval (derived approver, audit, event). No commit. ``subject`` is set only
    by services (e.g. the hiring service): it is what a decision on the approval acts on. The
    guardrail engine passes ``approver`` (peer / founder / human) and the guarded tier + action."""
    limits = policy.load_policy(db, company_id)
    approver = approver or derive_approver(limits, kind)
    requester_id = (
        requested_by_agent_id
        if requested_by_agent_id is not _UNSET
        else (principal.id if principal.is_agent else None)
    )
    routed_note: str | None = None
    if approver == Approver.FOUNDER:
        # M18: the Co-founder may not decide its own request, and there may be no active Co-founder
        # at all. Either way the approval would be a dead end: a person decides it (and is told).
        founder = db.scalar(
            select(Agent).where(
                Agent.company_id == company_id,
                Agent.kind == AgentKind.FOUNDER,
                Agent.status.in_([AgentStatus.ACTIVE, AgentStatus.PAUSED]),
            )
        )
        if founder is None or founder.id == requester_id:
            approver = Approver.HUMAN
            routed_note = (
                "the Co-founder cannot decide its own request"
                if founder is not None
                else "there is no AI Co-founder to decide it"
            )
    now = utcnow()
    expires_at: datetime | None
    if subject is not None and subject[0] == "purchase_request":
        expires_at = None  # purchasing keeps its own 7-day rule
    elif approver == Approver.HUMAN:
        expires_at = now + timedelta(seconds=limits.approval_timeout_seconds)
    else:
        expires_at = now + timedelta(seconds=limits.agent_approval_timeout_seconds)
    scope = dict(scope or {})
    approval = Approval(
        company_id=company_id,
        kind=kind,
        status=ApprovalStatus.PENDING,
        approver=approver,
        requested_by_agent_id=requester_id,
        approver_member_id=approver_member_id,
        task_id=task_id,
        title=title,
        reason=reason,
        amount_micros=amount_micros,
        vendor=vendor,
        recurrence=recurrence,
        risk=risk,
        evidence=dict(evidence or {}),
        scope=scope,
        scope_hash=compute_scope_hash(scope),
        subject_type=subject[0] if subject else None,
        subject_id=subject[1] if subject else None,
        tier=tier,
        action=action,
        reviewer_agent_id=reviewer_agent_id,
        outbound=outbound,
        dlp_findings=dlp_findings,
        expires_at=expires_at,
    )
    db.add(approval)
    db.flush()
    audit.record(
        db,
        company_id=company_id,
        principal=principal,
        action="approval.requested",
        target_type="approval",
        target_id=approval.id,
        summary=f"Requested {approval.kind} approval: {approval.title}",
        data={
            "kind": approval.kind,
            "approver": approver.value,
            "amount_micros": amount_micros,
            "tier": tier,
            "action": action,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "routed_to_human": routed_note,
        },
    )
    events.emit(
        db,
        company_id,
        "approval.created",
        {
            "approval_id": approval.id,
            "kind": approval.kind,
            "approver": approver.value,
            "title": approval.title,
            "status": approval.status,
            "tier": tier,
        },
    )
    if approver == Approver.HUMAN:  # a person has to decide: tell them (M7 notifications)
        _notify_needed(db, approval, principal)
    elif approver == Approver.FOUNDER:  # M18: the Co-founder decides now, not at its next heartbeat
        from app.services import wakes

        wakes.wake_founder(
            db,
            company_id,
            "approval_pending",
            f"Approval needed: {approval.title}",
            ref_type="approval",
            ref_id=approval.id,
            actor_agent_id=requester_id,
            now=now,
        )
    return approval


def _notify_needed(db: Session, approval: Approval, principal: Principal | None) -> None:
    exclude = principal.id if principal is not None and principal.is_human else None
    title = f"Approval needed: {approval.title}"
    common = {"related_type": "approval", "related_id": approval.id, "body": approval.reason}
    if approval.approver_member_id:
        notifications.notify_member(
            db, approval.approver_member_id, notifications.APPROVAL_NEEDED, title, **common
        )
    else:
        notifications.notify_admins(
            db,
            approval.company_id,
            notifications.APPROVAL_NEEDED,
            title,
            exclude_user_id=exclude,
            **common,
        )


def assign_approver(
    db: Session, ctx: CompanyContext, approval_id: str, approver_ref: str | None
) -> Approval:
    """Owners and admins route a pending human approval to one person (who is then notified and may
    decide it), or back to "any owner/admin" with null. Owners/admins can always decide it too."""
    if not ctx.is_human_admin:
        raise Forbidden("Only company owners and admins can route an approval to a person")
    approval = get_approval(db, ctx, approval_id)
    if approval.status != ApprovalStatus.PENDING:
        raise InvalidTransition(
            f"Approval is already {approval.status}", details={"from": approval.status}
        )
    if approval.approver != Approver.HUMAN:
        raise Conflict("Only approvals that need a human can be routed to a person")
    member_id: str | None = None
    if approver_ref:
        ref = parse_ref(approver_ref, field="approver_ref")
        if not ref.is_member:
            raise ValidationFailed(
                "approver_ref must be a member (member:<id>)",
                details=[{"loc": ["body", "approver_ref"], "msg": "not a member reference"}],
            )
        member_id = org.get_assignable_member(db, ctx.company_id, ref.id).id
    if member_id != approval.approver_member_id:
        approval.approver_member_id = member_id
        approval.updated_at = utcnow()
        audit.record(
            db,
            company_id=ctx.company_id,
            principal=ctx.principal,
            action="approval.routed",
            target_type="approval",
            target_id=approval.id,
            summary=f"Routed approval '{approval.title}' to "
            + (org.member_name(db, member_id) or "any owner or admin"),
            data={"approver_ref": approver_ref},
        )
        events.emit(db, ctx.company_id, "approval.updated", {"approval_id": approval.id})
        if member_id:
            _notify_needed(db, approval, ctx.principal)
        db.commit()
    return approval


def decide(
    db: Session, ctx: CompanyContext, approval_id: str, decision: str, note: str | None
) -> Approval:
    if decision not in _DECISIONS:
        raise ValidationFailed("Unknown decision")
    principal = ctx.principal
    if ctx.role == Role.VIEWER:
        raise Forbidden("Viewers have read-only access")
    approval = get_approval(db, ctx, approval_id)
    if principal.is_agent:
        agent = db.scalar(
            select(Agent).where(Agent.id == principal.id, Agent.company_id == ctx.company_id)
        )
        if approval.requested_by_agent_id == principal.id:
            raise Forbidden("An agent can never decide its own request")
        if approval.approver == Approver.PEER:
            # Maker-checker: only the reviewer the engine picked (never the author) may decide.
            if (
                agent is None
                or agent.status != AgentStatus.ACTIVE
                or approval.reviewer_agent_id != principal.id
            ):
                raise Forbidden("Only the assigned reviewer can decide this peer review")
        else:
            if (
                agent is None
                or agent.kind != AgentKind.FOUNDER
                or agent.status != AgentStatus.ACTIVE
            ):
                raise Forbidden("Only the active founder agent can decide approvals")
            if approval.approver != Approver.FOUNDER:
                raise Forbidden("This approval requires a human decision")
    elif not ctx.is_human_admin and not (
        ctx.role == Role.MEMBER
        and ctx.member_id is not None
        and approval.approver_member_id == ctx.member_id
        and approval.approver == Approver.HUMAN
    ):
        raise Forbidden(
            "Only company owners and admins, or the person it is routed to, can decide approvals"
        )

    if approval.status != ApprovalStatus.PENDING:
        raise InvalidTransition(
            f"Approval is already {approval.status}", details={"from": approval.status}
        )
    status, action, verb = _DECISIONS[decision]
    now = utcnow()
    approval.status = status
    approval.decided_by_type = principal.type.value
    approval.decided_by_id = principal.id
    approval.decided_at = now
    approval.decision_note = note
    approval.updated_at = now
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=principal,
        action=action,
        target_type="approval",
        target_id=approval.id,
        summary=f"{verb} approval: {approval.title}",
        data={"kind": approval.kind, "approver": approval.approver, "decision": decision},
    )
    events.emit(
        db,
        ctx.company_id,
        "approval.decided",
        {
            "approval_id": approval.id,
            "status": status.value,
            "kind": approval.kind,
            "decided_by_type": principal.type.value,
        },
    )
    if approval.subject_type == "agent_hire":
        from app.services import hiring  # local: hiring depends on this module

        hiring.apply_hire_decision(db, ctx, approval, decision)
    elif approval.subject_type == "agent_plan":
        from app.services import tasks  # local: tasks depends on this module

        tasks.apply_plan_decision(db, ctx, approval, decision)
    if approval.subject_type == "domain_registration":
        from app.services import domains  # local: domains depends on this module

        db.flush()  # autoflush is off: the decision must be in the database before it is consumed
        domains.apply_decision(db, ctx, approval, decision)
    if approval.subject_type == "outsource_job":  # M22
        from app.services import outsourcing  # local: outsourcing depends on this module

        db.flush()
        outsourcing.apply_decision(db, ctx, approval, decision)
    if approval.subject_type == "guardrail":
        from app.guardrails import peer  # local: the guardrail engine depends on this module

        peer.after_decision(db, ctx, approval, decision, note)
    if approval.subject_type == "purchase_request":
        from app.services import purchasing  # local: purchasing depends on this module

        db.flush()
        purchasing.apply_decision(db, ctx, approval, decision)
    from app.services import graph_waits  # M18 Part B: a graph task parked on it runs again

    graph_waits.release_for_approval(db, approval, now)
    if approval.requested_by_agent_id and approval.subject_type != "agent_hire":
        # the requester continues once it is decided (a hire wakes the new hire's manager instead)
        from app.services import wakes

        wakes.wake_agent(
            db,
            ctx.company_id,
            approval.requested_by_agent_id,
            "approval",
            _wake_text(approval, status.value),
            ref_type="approval",
            ref_id=approval.id,
            actor_agent_id=principal.id if principal.is_agent else None,
        )
    db.commit()
    if approval.subject_type == "purchase_request" and approval.status == ApprovalStatus.APPROVED:
        from app.services import purchasing

        # The provider call happens after the decision is committed (never inside it); it never
        # raises: a failure is recorded on the purchase and retried by the scheduler.
        purchasing.issue_after_decision(db, approval)
    return approval


def _wake_text(approval: Approval, status: str) -> str:
    text = f"Approval {status}: {approval.title}"
    if approval.subject_type == "purchase_request":
        if status == ApprovalStatus.APPROVED:
            return (
                f"{text}. The purchase is approved: the Founder completes the checkout and you "
                "are woken when it is paid. Do not try to pay or ask for card details."
            )
        note = f" Note: {approval.decision_note}" if approval.decision_note else ""
        return f"{text}.{note} Do not request the same purchase again; find another way."
    if approval.subject_type == "outsource_job":  # M22
        if status == ApprovalStatus.APPROVED:
            return (
                f"{text}. A person will post the job on the marketplace themselves and record who "
                "was hired. You do not post, message or pay anyone: nothing more to do until a "
                "delivery is recorded."
            )
        note = f" Note: {approval.decision_note}" if approval.decision_note else ""
        return f"{text}.{note} Do not draft the same job again; adjust it or find another way."
    if approval.subject_type != "guardrail":
        return text
    if status == ApprovalStatus.APPROVED:
        return (
            f"{text}. Repeat the identical action now (same content, nothing changed): the "
            "approval is single-use and bound to exactly that content."
        )
    return f"{text}. Do NOT resend the same content; change it or choose another approach."


def consume_internal(
    db: Session,
    company_id: str,
    approval_id: str,
    scope: dict[str, Any],
    *,
    now: datetime | None = None,
) -> Approval:
    """``consume`` for the platform's own jobs (the card-issuing job): no principal and NO commit,
    so the approval is spent in the same transaction as the intent it authorizes."""
    approval = db.scalar(
        select(Approval).where(Approval.id == approval_id, Approval.company_id == company_id)
    )
    if approval is None:
        raise NotFound("Approval not found")
    if approval.status != ApprovalStatus.APPROVED:
        raise Conflict(f"Approval is {approval.status}, not approved")
    if approval.consumed_at is not None:
        raise Conflict("Approval has already been used")
    if compute_scope_hash(scope) != approval.scope_hash:
        raise Conflict("Action does not match the approved scope")
    now = now or utcnow()
    result = db.execute(
        update(Approval)
        .where(
            Approval.id == approval.id,
            Approval.company_id == company_id,
            Approval.status == ApprovalStatus.APPROVED,
            Approval.consumed_at.is_(None),
        )
        .values(consumed_at=now, updated_at=now)
    )
    if result.rowcount != 1:  # lost a race with a concurrent consumer
        raise Conflict("Approval has already been used")
    audit.record(
        db,
        company_id=company_id,
        principal=None,
        action="approval.consumed",
        target_type="approval",
        target_id=approval.id,
        summary=f"Used approval: {approval.title}",
        data={"kind": approval.kind},
    )
    db.refresh(approval)
    return approval


def consume(
    db: Session,
    ctx: CompanyContext,
    approval_id: str,
    scope: dict[str, Any],
    *,
    now: datetime | None = None,
) -> Approval:
    """Spend an approval on exactly the action described by ``scope`` (once)."""
    approval = get_approval(db, ctx, approval_id)
    if approval.status != ApprovalStatus.APPROVED:
        raise Conflict(f"Approval is {approval.status}, not approved")
    if approval.consumed_at is not None:
        raise Conflict("Approval has already been used")
    if compute_scope_hash(scope) != approval.scope_hash:
        raise Conflict("Action does not match the approved scope")
    now = now or utcnow()
    result = db.execute(
        update(Approval)
        .where(
            Approval.id == approval.id,
            Approval.company_id == ctx.company_id,
            Approval.status == ApprovalStatus.APPROVED,
            Approval.consumed_at.is_(None),
        )
        .values(consumed_at=now, updated_at=now)
    )
    if result.rowcount != 1:  # lost a race with a concurrent consumer
        db.rollback()
        raise Conflict("Approval has already been used")
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="approval.consumed",
        target_type="approval",
        target_id=approval.id,
        summary=f"Used approval: {approval.title}",
        data={"kind": approval.kind},
    )
    db.commit()
    db.refresh(approval)
    return approval


# ---------------------------------------------------------------- M18: deadlines


def _system_ctx(company_id: str) -> CompanyContext:
    """The context the deadline sweep hands to the subject handlers (hire, plan, domain, guardrail)
    when it declines an approval: no principal, so every audit row is attributed to the System. Only
    the *reject* branches run with it; none of them looks at the caller's role."""
    return CompanyContext(company_id=company_id, principal=None, role=Role.OWNER)  # type: ignore[arg-type]


def _timeout_label(seconds: int) -> str:
    return f"{max(1, round(seconds / 3600))}h"


def _return_waiting_task(db: Session, approval: Approval, now: datetime) -> None:
    """The task that waited on this approval (blocked with ``error.approval_id``) goes back to the
    queue with a note telling the agent the action was declined by timeout. No commit."""
    from app.models.work import Task
    from app.services import tasks

    if not approval.task_id:
        return
    task = db.scalar(
        select(Task).where(Task.id == approval.task_id, Task.company_id == approval.company_id)
    )
    if task is None or task.status != TaskStatus.BLOCKED:
        return
    if (task.error or {}).get("approval_id") != approval.id:
        return
    task.review_note = (
        "The approval you were waiting for was declined by timeout (nobody answered in time). "
        "Finish the task another way, or record why you cannot."
    )
    tasks._unblock(db, task, None)


def _escalate_to_human(db: Session, approval: Approval, now: datetime) -> None:
    """A Co-founder (or peer) approval nobody decided in time is handed to a person, once."""
    limits = policy.load_policy(db, approval.company_id)
    was = approval.approver
    if was == Approver.PEER:  # the reviewer's task is no longer needed
        from app.guardrails import peer
        from app.services import runs as run_service
        from app.services import tasks

        review = peer.review_task_for(db, approval)
        if review is not None and TaskStatus(review.status) not in tasks.TERMINAL_TASK_STATUSES:
            tasks.transition(db, review, TaskStatus.CANCELLED, principal=None, reason="escalated")
            run_service.request_cancel_for_task(db, review, "task_cancelled", None)
        approval.reviewer_agent_id = None
    approval.approver = Approver.HUMAN
    approval.approver_member_id = None
    approval.expires_at = now + timedelta(seconds=limits.approval_timeout_seconds)
    approval.updated_at = now
    audit.record(
        db,
        company_id=approval.company_id,
        principal=None,
        action="approval.escalated",
        target_type="approval",
        target_id=approval.id,
        summary=f"No decision from the {was}: '{approval.title}' now needs a person",
        data={"kind": approval.kind, "from": str(was), "to": "human"},
    )
    events.emit(
        db,
        approval.company_id,
        "approval.updated",
        {"approval_id": approval.id, "approver": "human", "status": approval.status},
    )
    _notify_needed(db, approval, None)


def _expire(db: Session, approval: Approval, now: datetime) -> None:
    """A human approval nobody decided in time is *declined* (status ``expired``), never approved.
    The requester is woken to continue another way. No commit."""
    limits = policy.load_policy(db, approval.company_id)
    note = (
        f"No answer within {_timeout_label(limits.approval_timeout_seconds)}; declined by timeout"
    )
    approval.status = ApprovalStatus.EXPIRED
    approval.decided_by_type = "system"
    approval.decided_by_id = None
    approval.decided_at = now
    approval.decision_note = note
    approval.updated_at = now
    audit.record(
        db,
        company_id=approval.company_id,
        principal=None,
        action="approval.expired",
        target_type="approval",
        target_id=approval.id,
        summary=f"Approval expired: {approval.title}",
        data={"kind": approval.kind, "approver": str(approval.approver), "reason": "timeout"},
    )
    events.emit(
        db,
        approval.company_id,
        "approval.decided",
        {
            "approval_id": approval.id,
            "status": ApprovalStatus.EXPIRED.value,
            "kind": approval.kind,
            "decided_by_type": "system",
        },
    )
    ctx = _system_ctx(approval.company_id)
    if approval.subject_type == "agent_hire":
        from app.services import hiring

        hiring.apply_hire_decision(db, ctx, approval, "reject")
    elif approval.subject_type == "agent_plan":
        from app.services import tasks

        tasks.apply_plan_decision(db, ctx, approval, "reject")
    elif approval.subject_type == "domain_registration":
        from app.services import domains

        db.flush()
        domains.apply_decision(db, ctx, approval, "reject")
    elif approval.subject_type == "outsource_job":  # M22
        from app.services import outsourcing

        db.flush()
        outsourcing.apply_decision(db, ctx, approval, "reject")
    elif approval.subject_type == "guardrail":
        from app.guardrails import peer

        peer.after_decision(db, ctx, approval, "reject", note)
    _return_waiting_task(db, approval, now)
    from app.services import graph_waits  # M18 Part B: a graph task parked on it runs again

    graph_waits.release_for_approval(db, approval, now)
    if approval.requested_by_agent_id:
        from app.services import wakes

        wakes.wake_agent(
            db,
            approval.company_id,
            approval.requested_by_agent_id,
            "approval_expired",
            f"Approval declined by timeout: {approval.title}. {note}. Do NOT repeat the same "
            "request: finish another way, or record why you cannot.",
            ref_type="approval",
            ref_id=approval.id,
            now=now,
        )


def sweep_deadlines(db: Session, now: datetime, *, limit: int = SWEEP_BATCH) -> dict[str, int]:
    """Scheduler pass (every 60 s): pending approvals past ``expires_at`` in active companies,
    oldest first, at most ``limit`` per call. A Co-founder/peer approval is handed to a human
    (once); a human one expires (declined). Each approval commits on its own; one failure never
    stops the others. Timeouts never approve anything."""
    rows = list(
        db.execute(
            select(Approval.id, Approval.company_id)
            .join(Company, Company.id == Approval.company_id)
            .where(
                Approval.status == ApprovalStatus.PENDING,
                Approval.expires_at.is_not(None),
                Approval.expires_at <= now,
                Company.status == CompanyStatus.ACTIVE,
            )
            .order_by(Approval.expires_at, Approval.id)
            .limit(limit)
        ).all()
    )
    out = {"escalated": 0, "expired": 0}
    for approval_id, company_id in rows:
        try:
            approval = db.scalar(
                select(Approval).where(
                    Approval.id == approval_id,
                    Approval.company_id == company_id,
                    Approval.status == ApprovalStatus.PENDING,
                )
            )
            if approval is None:
                continue
            if approval.approver == Approver.HUMAN:
                _expire(db, approval, now)
                out["expired"] += 1
            else:
                _escalate_to_human(db, approval, now)
                out["escalated"] += 1
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("approval deadline sweep failed for %s", approval_id)
    return out
