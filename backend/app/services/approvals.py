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
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, InvalidTransition, NotFound, ValidationFailed
from app.core.principal import CompanyContext, Principal, Role
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.enums import (
    AgentKind,
    AgentStatus,
    ApprovalStatus,
    Approver,
    AutonomyMode,
)
from app.models.governance import Approval
from app.models.policy import CompanyPolicy
from app.schemas.governance import ApprovalCreate
from app.services import audit, events, policy
from app.services.agents import _find_secret_paths
from app.services.common import get_task_or_404
from app.services.pagination import paginate

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


def derive_approver(limits: CompanyPolicy, kind: str) -> Approver:
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
):
    stmt = select(Approval).where(Approval.company_id == ctx.company_id)
    if status:
        stmt = stmt.where(Approval.status == status)
    if approver:
        stmt = stmt.where(Approval.approver == approver)
    return paginate(db, stmt, Approval, limit=limit, cursor=cursor, descending=True)


def create_approval(
    db: Session, ctx: CompanyContext, data: ApprovalCreate, *, default_task_id: str | None = None
) -> Approval:
    if ctx.role == Role.VIEWER:
        raise Forbidden("Viewers have read-only access")
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
) -> Approval:
    """Insert an approval (derived approver, audit, event). No commit. ``subject`` is set only
    by services (e.g. the hiring service): it is what a decision on the approval acts on."""
    limits = policy.load_policy(db, company_id)
    approver = derive_approver(limits, kind)
    scope = dict(scope or {})
    approval = Approval(
        company_id=company_id,
        kind=kind,
        status=ApprovalStatus.PENDING,
        approver=approver,
        requested_by_agent_id=principal.id if principal.is_agent else None,
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
        data={"kind": approval.kind, "approver": approver.value, "amount_micros": amount_micros},
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
        },
    )
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
        if agent is None or agent.kind != AgentKind.FOUNDER or agent.status != AgentStatus.ACTIVE:
            raise Forbidden("Only the active founder agent can decide approvals")
        if approval.approver != Approver.FOUNDER:
            raise Forbidden("This approval requires a human decision")
        if approval.requested_by_agent_id == principal.id:
            raise Forbidden("An agent can never decide its own request")
    elif not ctx.is_human_admin:
        raise Forbidden("Only company owners and admins can decide approvals")

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
    db.commit()
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
