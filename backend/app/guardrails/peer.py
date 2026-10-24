"""Maker-checker (docs/specs/m6 section 4): an action under ``peer_review`` is checked by a reviewer
agent that is NOT its author.

The reviewer is the author's manager, else a QA agent, else a marketing ("brand") agent, else the
AI Co-founder, else any other active non-HR agent; when nobody independent exists the engine
escalates to a human instead (it never lets the author review itself). The review is an ordinary
task (kind ``review``) in the reviewer's queue; the run spec carries the exact outbound content, the
DLP result and a checklist, and the reviewer decides with ``company_decide_peer_review``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.core.principal import CompanyContext
from app.guardrails import log
from app.models.agent import Agent
from app.models.company import Company
from app.models.enums import (
    AgentKind,
    AgentStatus,
    ApprovalStatus,
    Approver,
    RoleFamily,
    TaskKind,
    TaskStatus,
)
from app.models.governance import Approval
from app.models.work import Task
from app.schemas.work import TaskCreate

REF_PREFIX = "peer-review:"
KEY_PREFIX = "peerreview:"
CHECKLIST = (
    "Is every statement accurate and supported? Nothing invented, no promises we cannot keep.",
    "Is the tone right for the company and the recipient?",
    "Does it respect the company's constraints (listed below) and any legal or brand rule?",
    "Does it avoid pretending to be a human? It must read as written by the company's AI.",
    "Does it leak nothing internal (secrets, private data, plans)? The DLP result is below.",
)


def _active(db: Session, company_id: str, agent_id: str | None) -> Agent | None:
    if not agent_id:
        return None
    agent = db.scalar(select(Agent).where(Agent.id == agent_id, Agent.company_id == company_id))
    return agent if agent is not None and agent.status == AgentStatus.ACTIVE else None


def pick_reviewer(db: Session, author: Agent) -> Agent | None:
    """A reviewer independent of ``author`` (never the author), or None."""
    company_id = author.company_id
    manager = _active(db, company_id, author.manager_agent_id)
    if manager is not None and manager.id != author.id:
        return manager

    def first(*conditions) -> Agent | None:
        return db.scalar(
            select(Agent)
            .where(
                Agent.company_id == company_id,
                Agent.status == AgentStatus.ACTIVE,
                Agent.id != author.id,
                *conditions,
            )
            .order_by(Agent.created_at, Agent.id)
            .limit(1)
        )

    return (
        first(Agent.role_family == RoleFamily.QA)
        or first(Agent.role_family == RoleFamily.MARKETING)
        or first(Agent.kind == AgentKind.FOUNDER)
        or first(Agent.role_family != RoleFamily.HR)
    )


def _review_text(approval_id: str, author: Agent, tier: str, action: str) -> str:
    return (
        f"Peer review: {author.name} wants to run `{action}` (tier {tier}). The exact content is "
        "in "
        "your task below. Check it against the checklist, then decide with "
        f"company_decide_peer_review (approval_id {approval_id}): approve, request_changes (say "
        "what to change) or reject. You are not the author and you cannot approve your own work."
    )


def open_review(
    db: Session,
    *,
    approval: Approval,
    author: Agent,
    reviewer: Agent,
    author_task_id: str | None,
) -> Task:
    """Create the review task for ``approval`` (idempotent). No commit."""
    from app.services import tasks

    data = TaskCreate(
        title=f"Peer review: {approval.title}"[:300],
        description=_review_text(approval.id, author, approval.tier or "", approval.action or ""),
        kind=TaskKind.REVIEW.value,
        priority="high",
        assignee_agent_id=reviewer.id,
        input_refs=[f"{REF_PREFIX}{approval.id}"],
        idempotency_key=f"{KEY_PREFIX}{approval.id}",
    )
    task, _ = tasks._create_task_core(db, approval.company_id, None, data, run=None)
    evidence = dict(approval.evidence or {})
    evidence["review_task_id"] = task.id
    evidence["author_task_id"] = author_task_id
    approval.evidence = evidence
    return task


def review_task_for(db: Session, approval: Approval) -> Task | None:
    return db.scalar(
        select(Task).where(
            Task.company_id == approval.company_id,
            Task.idempotency_key == f"{KEY_PREFIX}{approval.id}",
        )
    )


def target_id_of(task: Task) -> str | None:
    for ref in task.input_refs or []:
        if isinstance(ref, str) and ref.startswith(REF_PREFIX):
            return ref[len(REF_PREFIX) :]
    return None


def review_target(db: Session, task: Task) -> dict[str, Any] | None:
    """What a peer reviewer sees (goes into the run spec as ``task.peer_review``)."""
    approval_id = target_id_of(task)
    if not approval_id:
        return None
    approval = db.scalar(
        select(Approval).where(Approval.id == approval_id, Approval.company_id == task.company_id)
    )
    if approval is None:
        return None
    author = (
        db.get(Agent, approval.requested_by_agent_id) if approval.requested_by_agent_id else None
    )
    company = db.get(Company, task.company_id)
    return {
        "approval_id": approval.id,
        "status": approval.status,
        "action": approval.action,
        "tier": approval.tier,
        "author": author.name if author else None,
        "outbound": approval.outbound,
        "preview": (approval.evidence or {}).get("payload_preview"),
        "dlp_findings": approval.dlp_findings or [],
        "checklist": list(CHECKLIST),
        "constraints": list(company.constraints or []) if company else [],
    }


def decide(
    db: Session, ctx: CompanyContext, approval_id: str, decision: str, note: str | None
) -> Approval:
    """A reviewer decides a peer review (``POST /peer-reviews/{id}/decide``)."""
    from app.services import approvals

    approval = approvals.get_approval(db, ctx, approval_id)
    if approval.approver != Approver.PEER:
        raise NotFound("Peer review not found")
    return approvals.decide(db, ctx, approval_id, decision, note)


def after_decision(
    db: Session, ctx: CompanyContext, approval: Approval, decision: str, note: str | None
) -> None:
    """Called by ``approvals.decide`` for guardrail approvals (no commit): finish the peer review
    task, log a rejection, and release tasks parked behind a paid-model approval."""
    from app.services import runs as run_service
    from app.services import tasks

    status = approval.status
    review = review_task_for(db, approval)
    if review is not None and TaskStatus(review.status) not in tasks.TERMINAL_TASK_STATUSES:
        if review.status == TaskStatus.IN_PROGRESS:
            review.result_summary = f"Peer review decided: {decision}"
            tasks.transition(
                db, review, TaskStatus.DONE, principal=ctx.principal, error=None, reason="decided"
            )
            tasks.recompute_dependents(db, review, principal=ctx.principal)
        else:  # not started: the decision came from elsewhere (e.g. a human)
            tasks.transition(
                db, review, TaskStatus.CANCELLED, principal=ctx.principal, reason="decided"
            )
            run_service.request_cancel_for_task(db, review, "task_cancelled", ctx.principal)
    if status in (ApprovalStatus.REJECTED, ApprovalStatus.CHANGES_REQUESTED):
        log.record(
            db,
            company_id=approval.company_id,
            agent_id=approval.requested_by_agent_id,
            task_id=approval.task_id,
            action=approval.action or "unknown",
            tier=approval.tier or "destructive",
            decision="deny",
            approval_id=approval.id,
            reason=f"{status} by {ctx.principal.label}" + (f": {note}" if note else ""),
        )
    if approval.action == "model.paid_fallback" and status == ApprovalStatus.APPROVED:
        tasks.release_budget_blocked(
            db, approval.company_id, ctx.principal, codes=("paid_model_approval",)
        )
