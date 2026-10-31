"""M22 human work: things an AI employee cannot do and asks a person to do.

An agent that is blocked on a signature, a phone call, KYC, a physical errand or a bank step used
to produce a vague block. Now it asks (``company_request_human_work``) and the request becomes an
ordinary task with ``requires_human``:

* assigned to a member (who must be an active, non-viewer member of THIS company) or left in the
  **human pool** (no assignee; any owner/admin/member may claim it, a second claim is a 409);
* never dispatched to a runtime (the dispatcher skips ``requires_human`` tasks);
* with ``blocks_my_task`` the agent's own task gets a plain ``TaskDependency`` on it: readiness
  keeps it in the backlog until the person completes the work (``complete_task_as_member``), and
  its next run sees the person's comment and artifacts in its ``dependencies`` context. The same
  holds for work-graph runs: both end in ``tasks.finish_success`` (``_park_for_human_work``);
* a person can decline it (owner/admin): the task is cancelled, the dependency removed, the
  requesting agent is woken with the reason and its task returns to the queue with a note;
* limits are code: 3 open per agent, 20 per company; a due-date reminder (due + 24 h, once) goes to
  the owners and admins. Nothing is cancelled by a timeout: a person's job is not a timer's.

The text an AI writes here is scanned for secrets and shown as plain text.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.errors import (
    Conflict,
    Forbidden,
    InvalidTransition,
    NotFound,
    RateLimited,
    ValidationFailed,
)
from app.core.principal import CompanyContext, Role, require_human_admin
from app.core.time import utcnow
from app.guardrails import dlp
from app.models.agent import Agent
from app.models.audit import AuditEvent
from app.models.company import Company
from app.models.enums import (
    TERMINAL_TASK_STATUSES,
    WAITING_FOR_HUMAN,
    AgentStatus,
    CompanyStatus,
    TaskKind,
    TaskStatus,
)
from app.models.run import TaskRun
from app.models.work import Task, TaskDependency
from app.schemas.human_work import HumanWorkRequest
from app.services import audit, events, notifications, org
from app.services.pagination import DEFAULT_LIMIT

logger = logging.getLogger("company_os.human_work")

CATEGORIES = ("signature", "call", "identity", "payment", "physical", "account", "legal", "other")
MAX_OPEN_PER_AGENT = 3
MAX_OPEN_PER_COMPANY = 20
MIN_DUE_HOURS = 1
MAX_DUE_HOURS = 720
REMINDER_AFTER_DUE = timedelta(hours=24)
SWEEP_BATCH = 100
DO_IT_YOURSELF = "Do it yourself, or ask the founder a question (company_ask_founder)."
S = TaskStatus
OPEN = [s.value for s in TaskStatus if s not in TERMINAL_TASK_STATUSES]


class HumanWorkLimit(RateLimited):
    """Too many open human tasks (429 ``human_work_limit``)."""

    code = "human_work_limit"
    default_message = f"Too many open human tasks. {DO_IT_YOURSELF}"


# ---------------------------------------------------------------- helpers


def _reject_secrets(text: str, what: str) -> None:
    if any(f.kind in ("secret", "own_token") for f in dlp.scan_text(text)):
        raise ValidationFailed(
            f"The {what} looks like it contains a secret (key, token or password). Never put "
            "secrets in a request for a person: say where they find them instead.",
            details=[{"loc": ["body", what], "msg": "looks like a secret"}],
        )


def get_human_task(db: Session, company_id: str, task_id: str) -> Task:
    task = db.scalar(
        select(Task).where(
            Task.id == task_id, Task.company_id == company_id, Task.requires_human.is_(True)
        )
    )
    if task is None:
        raise NotFound("Human work not found")
    return task


def _blocking_ids(db: Session, task_ids: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {t: [] for t in task_ids}
    if task_ids:
        for waiting, dep in db.execute(
            select(TaskDependency.task_id, TaskDependency.depends_on_task_id)
            .where(TaskDependency.depends_on_task_id.in_(task_ids))
            .order_by(TaskDependency.created_at, TaskDependency.id)
        ):
            out[dep].append(waiting)
    return out


def serialize(db: Session, tasks: list[Task], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or utcnow()
    agent_ids = {t.requested_by_agent_id for t in tasks if t.requested_by_agent_id}
    names = (
        {i: n for i, n in db.execute(select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids)))}
        if agent_ids
        else {}
    )
    members = org.names_of_members(
        db, {t.assignee_member_id for t in tasks if t.assignee_member_id}
    )
    blocking = _blocking_ids(db, [t.id for t in tasks])
    out: list[dict[str, Any]] = []
    for t in tasks:
        open_ = t.status in OPEN
        out.append(
            {
                "id": t.id,
                "company_id": t.company_id,
                "title": t.title,
                "description": t.description,
                "instructions": t.human_instructions,
                "category": t.human_category,
                "status": t.status,
                "priority": t.priority,
                "due_at": t.due_at,
                "overdue": bool(open_ and t.due_at is not None and t.due_at < now),
                "assignee_ref": t.assignee_ref,
                "assignee_name": members.get(t.assignee_member_id or ""),
                "pool": t.assignee_member_id is None,
                "requested_by_agent_id": t.requested_by_agent_id,
                "requested_by_agent_name": names.get(t.requested_by_agent_id or ""),
                "project_id": t.project_id,
                "blocking_task_ids": blocking.get(t.id, []),
                "result_summary": t.result_summary,
                "output_refs": list(t.output_refs),
                "created_at": t.created_at,
                "completed_at": t.completed_at,
            }
        )
    return out


def _event(task: Task) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "status": task.status,
        "assignee_member_id": task.assignee_member_id,
        "category": task.human_category,
    }


# ---------------------------------------------------------------- the agent asks


def request(
    db: Session, ctx: CompanyContext, run: TaskRun | None, data: HumanWorkRequest
) -> tuple[Task, bool, bool]:
    """An agent asks for a person. Returns (task, pool, blocks). Commits."""
    from app.services import tasks as task_service

    if not ctx.principal.is_agent or ctx.role != Role.AGENT:
        raise Forbidden("Only AI employees ask for human work")
    agent = db.scalar(
        select(Agent).where(Agent.id == ctx.principal.id, Agent.company_id == ctx.company_id)
    )
    if agent is None or agent.status != AgentStatus.ACTIVE:
        raise NotFound("Agent not found")
    for text, what in (
        (data.title, "title"),
        (data.instructions, "instructions"),
        (data.why_human, "why_human"),
    ):
        _reject_secrets(text, what)
    open_filter = (
        Task.company_id == ctx.company_id,
        Task.requires_human.is_(True),
        Task.status.in_(OPEN),
    )
    company_open = db.scalar(select(func.count(Task.id)).where(*open_filter)) or 0
    if company_open >= MAX_OPEN_PER_COMPANY:
        raise HumanWorkLimit(
            f"The company already has {MAX_OPEN_PER_COMPANY} open human tasks. {DO_IT_YOURSELF}",
            details={"limit": "open_human_tasks_company", "max": MAX_OPEN_PER_COMPANY},
        )
    own_open = (
        db.scalar(
            select(func.count(Task.id)).where(*open_filter, Task.requested_by_agent_id == agent.id)
        )
        or 0
    )
    if own_open >= MAX_OPEN_PER_AGENT:
        raise HumanWorkLimit(
            f"You already have {MAX_OPEN_PER_AGENT} open human tasks. {DO_IT_YOURSELF}",
            details={"limit": "open_human_tasks_agent", "max": MAX_OPEN_PER_AGENT},
        )
    member_id: str | None = None
    if data.assignee_member_id:
        # NotFound for another company's member, Conflict for a removed one, 422 for a viewer.
        member_id = org.get_assignable_member(db, ctx.company_id, data.assignee_member_id).id
    now = utcnow()
    hours = max(MIN_DUE_HOURS, min(MAX_DUE_HOURS, int(data.due_in_hours)))
    own_task = (
        db.scalar(select(Task).where(Task.id == run.task_id, Task.company_id == ctx.company_id))
        if run is not None and run.task_id
        else None
    )
    blocks = bool(
        data.blocks_my_task
        and own_task is not None
        and own_task.kind == TaskKind.WORK  # a wake, review or meeting turn is never held up
        and own_task.status not in TERMINAL_TASK_STATUSES
    )
    task = Task(
        company_id=ctx.company_id,
        project_id=own_task.project_id if own_task is not None else None,
        title=data.title,
        description=f"Needs a person: {data.why_human}",
        kind=TaskKind.WORK,
        status=S.BACKLOG,
        priority="high" if hours <= 24 else "normal",
        assignee_member_id=member_id,
        required_skills=[],
        due_at=now + timedelta(hours=hours),
        input_refs=[],
        output_refs=[],
        created_by_type="agent",
        created_by_id=agent.id,
        requires_human=True,
        human_category=data.category,
        human_instructions=data.instructions,
        requested_by_agent_id=agent.id,
    )
    db.add(task)
    db.flush()
    if blocks:
        assert own_task is not None
        db.add(
            TaskDependency(
                company_id=ctx.company_id, task_id=own_task.id, depends_on_task_id=task.id
            )
        )
        db.flush()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="human_work.requested",
        target_type="task",
        target_id=task.id,
        summary=f"{agent.name} asked a person: {task.title[:120]}",
        data={
            "category": data.category,
            "assignee_member_id": member_id,
            "pool": member_id is None,
            "blocks_task_id": own_task.id if blocks and own_task else None,
            "due_at": task.due_at.isoformat() if task.due_at else None,
        },
    )
    events.emit(db, ctx.company_id, "task.created", task_service.task_event_payload(task))
    events.emit(db, ctx.company_id, "human_work.created", _event(task))
    task_service.recompute_readiness(
        db, ctx.company_id, task_ids=[task.id], principal=ctx.principal
    )
    _notify_requested(db, task, agent)
    db.commit()
    return task, member_id is None, blocks


def _notify_requested(db: Session, task: Task, agent: Agent) -> None:
    title = f"{agent.name} needs a person: {task.title[:200]}"
    body = f"{(task.human_instructions or '')[:400]}"
    kwargs = {"related_type": "task", "related_id": task.id, "body": body or None}
    if task.assignee_member_id:
        notifications.notify_member(
            db, task.assignee_member_id, notifications.HUMAN_WORK, title, **kwargs
        )
    else:  # the pool: owners and admins are told; any member can still pick it up
        notifications.notify_admins(
            db,
            task.company_id,
            notifications.HUMAN_WORK,
            f"{title} (nobody has taken it yet)",
            **kwargs,
        )


# ---------------------------------------------------------------- people read, claim, decline


def list_human_work(
    db: Session,
    ctx: CompanyContext,
    *,
    status: str = "open",
    mine: bool = False,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> tuple[list[Task], str | None]:
    """Human tasks of the company: open ones first with the overdue before the rest (then the
    oldest due date), done ones newest first. ``mine``: assigned to the caller. Humans only."""
    if not ctx.principal.is_human:
        raise Forbidden("Human work is for people")
    stmt = select(Task).where(Task.company_id == ctx.company_id, Task.requires_human.is_(True))
    if status == "done":
        stmt = stmt.where(Task.status == S.DONE)
    else:
        stmt = stmt.where(Task.status.in_(OPEN))
    if mine:
        if ctx.member_id is None:
            return [], None
        stmt = stmt.where(Task.assignee_member_id == ctx.member_id)
    rows = list(db.scalars(stmt))
    now = utcnow()
    far = datetime.max.replace(tzinfo=now.tzinfo)
    if status == "done":
        rows.sort(key=lambda t: (t.completed_at or t.created_at, t.id), reverse=True)
    else:
        rows.sort(
            key=lambda t: (
                not (t.due_at is not None and t.due_at < now),
                t.due_at or far,
                t.created_at,
                t.id,
            )
        )
    try:
        offset = int(cursor) if cursor else 0
    except ValueError as exc:
        raise ValidationFailed("Invalid cursor") from exc
    page = rows[offset : offset + limit]
    return page, str(offset + limit) if offset + limit < len(rows) else None


def get_one(db: Session, ctx: CompanyContext, task_id: str) -> Task:
    if not ctx.principal.is_human:
        raise Forbidden("Human work is for people")
    return get_human_task(db, ctx.company_id, task_id)


def claim(db: Session, ctx: CompanyContext, task_id: str) -> Task:
    """A person takes a task from the human pool. Atomic: a second claim is a 409. Commits."""
    from app.services import tasks as task_service

    if not ctx.principal.is_human or ctx.role not in (Role.OWNER, Role.ADMIN, Role.MEMBER):
        raise Forbidden("Only owners, admins and members can take human work")
    if ctx.member_id is None:
        raise Forbidden("Only a member of this company can take human work")
    task = get_human_task(db, ctx.company_id, task_id)
    if task.status not in (S.BACKLOG, S.READY):
        raise InvalidTransition(
            f"This task is {task.status} and cannot be taken", details={"from": task.status}
        )
    now = utcnow()
    result = db.execute(
        update(Task)
        .where(
            Task.id == task.id,
            Task.company_id == ctx.company_id,
            Task.requires_human.is_(True),
            Task.assignee_member_id.is_(None),
            Task.assignee_agent_id.is_(None),
            Task.status.in_([S.BACKLOG.value, S.READY.value]),
        )
        .values(assignee_member_id=ctx.member_id, updated_at=now)
    )
    if result.rowcount != 1:  # somebody else took it first
        db.rollback()
        raise Conflict("Somebody already took this task")
    db.refresh(task)
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="human_work.claimed",
        target_type="task",
        target_id=task.id,
        summary=f"{ctx.principal.label} took human work: {task.title[:120]}",
        data={"member_id": ctx.member_id},
    )
    events.emit(db, ctx.company_id, "task.updated", task_service.task_event_payload(task))
    events.emit(db, ctx.company_id, "human_work.updated", _event(task))
    task_service.recompute_readiness(
        db, ctx.company_id, task_ids=[task.id], principal=ctx.principal
    )
    db.commit()
    return task


def release_dependents(db: Session, task: Task, reason: str) -> None:
    """A human-work task will never be done (declined, cancelled): the tasks that wait for it are
    released with a note, the requesting agent is woken. No commit."""
    from app.services import tasks as task_service
    from app.services import wakes

    now = utcnow()
    db.flush()
    edges = list(
        db.scalars(
            select(TaskDependency).where(
                TaskDependency.depends_on_task_id == task.id,
                TaskDependency.company_id == task.company_id,
            )
        )
    )
    released: list[str] = []
    note = (
        f"A person declined the human work '{task.title[:120]}' you asked for: {reason[:300]}. "
        "Find another way, or record why you cannot."
    )
    for edge in edges:
        waiting = db.scalar(
            select(Task).where(Task.id == edge.task_id, Task.company_id == task.company_id)
        )
        db.delete(edge)
        if waiting is None:
            continue
        if waiting.status not in TERMINAL_TASK_STATUSES:
            waiting.review_note = note
            waiting.updated_at = now
            if waiting.blocked_reason == WAITING_FOR_HUMAN:
                waiting.blocked_reason = None
            released.append(waiting.id)
    db.flush()
    if released:
        task_service.recompute_readiness(db, task.company_id, task_ids=released, principal=None)
    if task.requested_by_agent_id:
        wakes.wake_agent(
            db,
            task.company_id,
            task.requested_by_agent_id,
            "human_work",
            f"Human work declined: '{task.title[:120]}'. Reason: {reason[:200]}. Do not ask for "
            "the same thing again; find another way or record why you cannot.",
            ref_type="task",
            ref_id=task.id,
            now=now,
        )


def decline(db: Session, ctx: CompanyContext, task_id: str, reason: str) -> Task:
    """Owner/admin: this will not be done by a person. The task is cancelled, the agent that asked
    is woken with the reason and its blocked task returns to the queue. Commits."""
    from app.services import tasks as task_service

    require_human_admin(ctx, "decline human work")
    task = get_human_task(db, ctx.company_id, task_id)
    if task.status in TERMINAL_TASK_STATUSES:
        raise InvalidTransition(
            f"Human work that is {task.status} cannot be declined", details={"from": task.status}
        )
    text = reason.strip()
    if not text:
        raise ValidationFailed("A reason is required: the AI employee reads it")
    _reject_secrets(text, "reason")
    task_service.transition(db, task, S.CANCELLED, principal=ctx.principal, reason=text)
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="human_work.declined",
        target_type="task",
        target_id=task.id,
        summary=f"Declined human work: {task.title[:120]}",
        data={"reason": text[:300], "requested_by_agent_id": task.requested_by_agent_id},
    )
    events.emit(db, ctx.company_id, "human_work.updated", _event(task))
    release_dependents(db, task, text)
    db.commit()
    return task


# ---------------------------------------------------------------- deadlines


def sweep_reminders(db: Session, now: datetime, *, limit: int = SWEEP_BATCH) -> int:
    """Scheduler pass: an open human task more than 24 h past its due date reminds the owners and
    admins once (an audit row marks it done). Nothing is cancelled. Each task commits alone."""
    rows = list(
        db.execute(
            select(Task.id, Task.company_id)
            .join(Company, Company.id == Task.company_id)
            .where(
                Task.requires_human.is_(True),
                Task.status.in_(OPEN),
                Task.due_at.is_not(None),
                Task.due_at <= now - REMINDER_AFTER_DUE,
                Company.status == CompanyStatus.ACTIVE,
            )
            .order_by(Task.due_at, Task.id)
            .limit(limit * 3)
        ).all()
    )
    sent = 0
    for task_id, company_id in rows:
        if sent >= limit:
            break
        try:
            already = db.scalar(
                select(AuditEvent.id)
                .where(
                    AuditEvent.company_id == company_id,
                    AuditEvent.action == "human_work.reminded",
                    AuditEvent.target_id == task_id,
                )
                .limit(1)
            )
            if already:
                continue
            task = db.scalar(select(Task).where(Task.id == task_id, Task.company_id == company_id))
            if task is None or task.status not in OPEN:
                continue
            who = org.member_name(db, task.assignee_member_id) or "nobody (the human pool)"
            notifications.notify_admins(
                db,
                company_id,
                notifications.HUMAN_WORK,
                f"Overdue: {task.title[:200]}",
                related_type="task",
                related_id=task.id,
                body=f"An AI employee is still waiting for a person. Assigned to: {who}.",
            )
            audit.record(
                db,
                company_id=company_id,
                principal=None,
                action="human_work.reminded",
                target_type="task",
                target_id=task.id,
                summary=f"Reminded the owners: human work is overdue: {task.title[:120]}",
                data={"due_at": task.due_at.isoformat() if task.due_at else None},
            )
            db.commit()
            sent += 1
        except Exception:
            db.rollback()
            logger.exception("human work reminder failed for %s", task_id)
    return sent
