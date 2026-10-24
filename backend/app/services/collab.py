"""Vertical collaboration between agents: escalation (up to the AI Co-founder) and requests for
help (to the requester's manager). Neither lets an agent assign work to someone else directly;
the control plane routes it."""

from __future__ import annotations

import hashlib

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, LimitExceeded
from app.models.agent import Agent
from app.models.enums import AgentStatus, TaskKind, TaskStatus
from app.models.work import Task
from app.schemas.autonomy import (
    EscalationCreate,
    EscalationResult,
    HelpRequestCreate,
    HelpRequestResult,
)
from app.schemas.work import TaskCreate
from app.services import agent_roles, audit, events, tasks, wakes
from app.services.agent_auth import AgentRunContext
from app.services.common import get_task_or_404

MAX_OPEN_HELP_REQUESTS = 3
OPEN_STATUSES = [s for s in TaskStatus if s not in tasks.TERMINAL_TASK_STATUSES]


def escalate(db: Session, rc: AgentRunContext, data: EscalationCreate) -> EscalationResult:
    """Tell the AI Co-founder you are stuck. Creates an ``escalation.raised`` event, wakes the
    Co-founder and, on request, blocks the task until it acts."""
    agent = rc.agent
    company_id = rc.ctx.company_id
    if agent_roles.role_of(db, agent) == agent_roles.FOUNDER:
        raise Conflict("The AI Co-founder is the top of the escalation chain; decide yourself")
    founder = wakes.active_founder(db, company_id)
    if founder is None:
        raise Conflict("There is no active AI Co-founder to escalate to")
    task = get_task_or_404(db, company_id, data.task_id or rc.run.task_id)
    text = (
        f"Escalation from {agent.name} ({agent.role_title}) on '{task.title}' [task {task.id}]: "
        + data.reason
    )
    if (
        data.block_task
        and task.assignee_agent_id == agent.id
        and task.status == TaskStatus.IN_PROGRESS
    ):
        tasks._block(
            db, task, rc.ctx.principal, f"Escalated: {data.reason}"[:1000], cancel_runs=False
        )
    request = wakes.request_wake(
        db,
        company_id,
        founder.id,
        "escalation",
        text,
        ref_type="task",
        ref_id=task.id,
    )
    audit.record(
        db,
        company_id=company_id,
        principal=rc.ctx.principal,
        action="escalation.raised",
        target_type="task",
        target_id=task.id,
        summary=f"{agent.name} escalated '{task.title}' to {founder.name}",
        data={"to_agent_id": founder.id, "run_id": rc.run.id},
    )
    events.emit(
        db,
        company_id,
        "escalation.raised",
        {
            "agent_id": agent.id,
            "task_id": task.id,
            "to_agent_id": founder.id,
            "reason": data.reason[:300],
        },
    )
    db.commit()
    return EscalationResult(
        escalated_to_agent_id=founder.id,
        escalated_to_name=founder.name,
        wake_status="queued" if request is not None else "none",
    )


def request_help(db: Session, rc: AgentRunContext, data: HelpRequestCreate) -> HelpRequestResult:
    """Ask your manager (else the AI Co-founder) for help: a task lands in their queue."""
    agent = rc.agent
    company_id = rc.ctx.company_id
    source = get_task_or_404(db, company_id, data.task_id or rc.run.task_id)
    target = db.get(Agent, agent.manager_agent_id) if agent.manager_agent_id else None
    if target is None or target.status != AgentStatus.ACTIVE or target.id == agent.id:
        target = wakes.active_founder(db, company_id)
    if target is None or target.id == agent.id:
        raise Conflict("There is nobody to ask for help")
    key = f"help:{source.id}:{hashlib.sha1(data.message.encode()).hexdigest()[:12]}"  # noqa: S324
    existing = db.scalar(
        select(Task).where(Task.company_id == company_id, Task.idempotency_key == key)
    )
    if existing is not None:
        return HelpRequestResult(
            task_id=existing.id,
            assigned_to_agent_id=existing.assignee_agent_id or target.id,
            assigned_to_name=target.name,
            created=False,
        )
    open_requests = db.scalar(
        select(func.count(Task.id)).where(
            Task.company_id == company_id,
            Task.created_by_id == agent.id,
            Task.idempotency_key.like("help:%"),
            Task.status.in_(OPEN_STATUSES),
        )
    )
    if (open_requests or 0) >= MAX_OPEN_HELP_REQUESTS:
        raise LimitExceeded(
            f"You already have {open_requests} open help requests; wait for answers",
            details={"limit": "open_help_requests", "max": MAX_OPEN_HELP_REQUESTS},
        )
    description = (
        f'{agent.name} ({agent.role_title}) asks for help on task "{source.title}" '
        f"[task id {source.id}]:\n\n{data.message}\n\n"
        "Help them: unblock or reassign their task, create what they need, or answer by "
        "completing this task with the answer in result_summary."
    )
    new_task, created = tasks._create_task_core(
        db,
        company_id,
        rc.ctx.principal,
        TaskCreate(
            title=f"Help {agent.name}: {data.message}"[:300],
            description=description,
            kind=TaskKind.WORK.value,
            priority=source.priority,
            project_id=source.project_id,
            assignee_agent_id=target.id,
            input_refs=[f"task:{source.id}"],
            idempotency_key=key,
        ),
        run=None,
    )
    db.commit()
    return HelpRequestResult(
        task_id=new_task.id,
        assigned_to_agent_id=target.id,
        assigned_to_name=target.name,
        created=created,
    )
