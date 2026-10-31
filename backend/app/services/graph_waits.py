"""M18 Part B: how a work-graph task waits for an approval (and is released again).

A graph run whose agent asked for approvals (guardrail holds, ``company_request_approval``) and has
not been given a decision ends cleanly: the task is parked as ``blocked_reason =
waiting_for_approval`` (see ``tasks._park_for_approval``). When *every* such approval is decided
(``approvals.decide``) or expired (the deadline sweep) the task returns to the queue; its next run
resumes the task's graph thread with the decisions (``resume_value``). Founder questions use the
equivalent code in ``questions.py``.

Only approvals the *assignee itself* asked for on this task count; hiring, plan, domain and
purchase approvals have their own flows and never park a task.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import WAITING_FOR_APPROVAL, ApprovalStatus
from app.models.governance import Approval
from app.models.work import Task
from app.services import secrets_scan

WAITING_SUBJECTS = (None, "guardrail")
MAX_NOTE = 300


def _waiting_filter(task: Task):
    return (
        Approval.company_id == task.company_id,
        Approval.task_id == task.id,
        Approval.requested_by_agent_id == task.assignee_agent_id,
        Approval.subject_type.is_(None) | (Approval.subject_type == "guardrail"),
    )


def pending_approval_ids(db: Session, task: Task) -> list[str]:
    """Pending approvals the task's assignee asked for on this task (oldest first)."""
    return list(
        db.scalars(
            select(Approval.id)
            .where(*_waiting_filter(task), Approval.status == ApprovalStatus.PENDING)
            .order_by(Approval.created_at, Approval.id)
        )
    )


def release_for_approval(db: Session, approval: Approval, now: datetime) -> None:
    """An approval was decided or expired: a task parked on it returns to the queue once none of its
    approvals is pending any more. No commit."""
    if not approval.task_id:
        return
    db.flush()  # autoflush is off: the new decision must be visible to the query below
    task = db.scalar(
        select(Task).where(Task.id == approval.task_id, Task.company_id == approval.company_id)
    )
    if task is None or task.blocked_reason != WAITING_FOR_APPROVAL:
        return
    if pending_approval_ids(db, task):
        return
    from app.services import tasks

    task.blocked_reason = None
    task.updated_at = now
    task.next_attempt_at = None
    tasks.recompute_readiness(db, task.company_id, task_ids=[task.id], principal=None, now=now)


def resume_value(db: Session, task: Task, approval_ids: list[str]) -> list[dict]:
    """What the resumed thread is told: each approval it waited on and how it ended (plain data)."""
    rows = {
        a.id: a
        for a in db.scalars(
            select(Approval).where(
                Approval.company_id == task.company_id, Approval.id.in_(approval_ids or [""])
            )
        )
    }
    out: list[dict] = []
    for approval_id in approval_ids:
        a = rows.get(approval_id)
        if a is None:
            continue
        out.append(
            {
                "id": a.id,
                "title": (secrets_scan.scrub(a.title) or "")[:120],
                "status": str(a.status),
                "note": (secrets_scan.scrub(a.decision_note) or "")[:MAX_NOTE] or None,
            }
        )
    return out


def resume_lines(decisions: list[dict]) -> list[str]:
    lines: list[str] = []
    for d in decisions:
        status = d.get("status")
        note = f" Note: {d['note']}" if d.get("note") else ""
        if status == ApprovalStatus.APPROVED:
            lines.append(
                f"Approval granted: {d.get('title')}.{note} Repeat the identical action now (same "
                "content, nothing changed): the approval is single-use and bound to it."
            )
        elif status == ApprovalStatus.PENDING:
            lines.append(f"Approval still pending: {d.get('title')}. Do not rely on it.")
        else:
            lines.append(
                f"Approval {status}: {d.get('title')}.{note} Do NOT repeat that action; finish "
                "another way, or record why you cannot."
            )
    return lines
