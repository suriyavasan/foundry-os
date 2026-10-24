"""Tasks: the business state machine, readiness, dependencies, delegation limits, review.

Task status and run status are separate: a task can be ``ready`` again after a failed run.
Building blocks (``transition``, ``recompute_readiness``, ``finish_success`` ...) never commit;
the public ``create/update/cancel/...`` functions commit exactly once, so the audit event and
the company event land in the same transaction as the state change.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import (
    Conflict,
    Forbidden,
    InvalidTransition,
    LimitExceeded,
    NotFound,
)
from app.core.principal import (
    CompanyContext,
    Principal,
    PrincipalType,
    Role,
    require_human_admin,
)
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.artifact import Artifact
from app.models.company import Company
from app.models.enums import (
    AWAITING_PLAN_APPROVAL,
    TERMINAL_TASK_STATUSES,
    WAITING_ON_SUBTASKS,
    AgentStatus,
    AutonomyMode,
    CompanyStatus,
    TaskKind,
    TaskStatus,
)
from app.models.run import TaskRun
from app.models.work import Project, Task, TaskDependency
from app.schemas.work import Task as TaskOut
from app.schemas.work import TaskCreate, TaskUpdate
from app.services import agent_roles, audit, autonomy_hooks, events, ledger, policy
from app.services.common import (
    creator_of,
    get_agent_or_404,
    get_project_or_404,
    get_task_or_404,
)
from app.services.pagination import paginate

S = TaskStatus

EDGES: dict[TaskStatus, frozenset[TaskStatus]] = {
    S.BACKLOG: frozenset({S.READY, S.BLOCKED, S.CANCELLED}),
    S.READY: frozenset({S.IN_PROGRESS, S.BACKLOG, S.BLOCKED, S.CANCELLED}),
    S.IN_PROGRESS: frozenset({S.IN_REVIEW, S.DONE, S.READY, S.FAILED, S.BLOCKED, S.CANCELLED}),
    S.IN_REVIEW: frozenset({S.DONE, S.REVISION_REQUESTED, S.BLOCKED, S.CANCELLED}),
    S.REVISION_REQUESTED: frozenset({S.READY, S.FAILED, S.BLOCKED, S.CANCELLED}),
    S.BLOCKED: frozenset({S.BACKLOG, S.CANCELLED}),  # unblock goes through backlog -> readiness
    S.FAILED: frozenset({S.BACKLOG}),  # explicit human/agent retry only
    S.DONE: frozenset(),
    S.CANCELLED: frozenset(),
}

_AUDIT_ACTION = {
    S.BACKLOG: "task.backlogged",
    S.READY: "task.ready",
    S.IN_PROGRESS: "task.started",
    S.IN_REVIEW: "task.in_review",
    S.REVISION_REQUESTED: "task.revision_requested",
    S.BLOCKED: "task.blocked",
    S.DONE: "task.done",
    S.FAILED: "task.failed",
    S.CANCELLED: "task.cancelled",
}
_UNSET: Any = object()
EDITABLE_OPEN = (
    S.BACKLOG,
    S.READY,
    S.IN_PROGRESS,
    S.IN_REVIEW,
    S.REVISION_REQUESTED,
    S.BLOCKED,
)
UPDATABLE = (
    "title",
    "description",
    "acceptance_criteria",
    "kind",
    "priority",
    "project_id",
    "assignee_agent_id",
    "reviewer_agent_id",
    "required_skills",
    "budget_micros",
    "due_at",
    "input_refs",
)


# ---------------------------------------------------------------- state machine


def task_event_payload(task: Task, *, previous: str | None = None) -> dict[str, Any]:
    payload = {
        "task_id": task.id,
        "status": task.status,
        "title": task.title,
        "assignee_agent_id": task.assignee_agent_id,
        "project_id": task.project_id,
        "parent_task_id": task.parent_task_id,
    }
    if previous is not None:
        payload["previous_status"] = previous
    return payload


def transition(
    db: Session,
    task: Task,
    to: TaskStatus,
    *,
    principal: Principal | None,
    reason: str | None = None,
    error: Any = _UNSET,
    now: datetime | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Move ``task`` along the state machine (no commit). Invalid edges -> 409."""
    current = TaskStatus(task.status)
    target = TaskStatus(to)
    if target not in EDGES[current]:
        raise InvalidTransition(
            f"Cannot move a task from {current.value} to {target.value}",
            details={"from": current.value, "to": target.value},
        )
    now = now or utcnow()
    task.status = target.value
    task.updated_at = now
    if target != S.BACKLOG:
        task.blocked_reason = None  # "waiting_for_hire" only makes sense while parked in backlog
    if target == S.IN_PROGRESS and task.started_at is None:
        task.started_at = now
    if target in TERMINAL_TASK_STATUSES:
        task.completed_at = now
    elif current in TERMINAL_TASK_STATUSES:
        task.completed_at = None
    if error is not _UNSET:
        task.error = error
    data: dict[str, Any] = {"from": current.value, "to": target.value, **(extra or {})}
    if reason:
        data["reason"] = reason
    audit.record(
        db,
        company_id=task.company_id,
        principal=principal,
        action=_AUDIT_ACTION[target],
        target_type="task",
        target_id=task.id,
        summary=f"Task '{task.title}' {current.value} -> {target.value}"
        + (f": {reason}" if reason else ""),
        data=data,
    )
    events.emit(
        db, task.company_id, "task.updated", task_event_payload(task, previous=current.value)
    )
    autonomy_hooks.on_transition(db, task, current, target, principal, now)


def _unfinished_dependency_counts(db: Session, task_ids: list[str]) -> dict[str, int]:
    if not task_ids:
        return {}
    rows = db.execute(
        select(TaskDependency.task_id, func.count(TaskDependency.id))
        .join(Task, Task.id == TaskDependency.depends_on_task_id)
        .where(TaskDependency.task_id.in_(task_ids), Task.status != S.DONE)
        .group_by(TaskDependency.task_id)
    ).all()
    return {task_id: count for task_id, count in rows}


def recompute_readiness(
    db: Session,
    company_id: str,
    *,
    task_ids: list[str] | None = None,
    agent_id: str | None = None,
    principal: Principal | None = None,
    now: datetime | None = None,
) -> list[Task]:
    """backlog/revision_requested -> ready iff the assignee is active, every dependency is
    done and the company is active; ready -> backlog when that stops holding. No commit."""
    db.flush()  # sessions don't autoflush: make pending status/assignee changes visible
    company_status = db.scalar(select(Company.status).where(Company.id == company_id))
    stmt = select(Task).where(
        Task.company_id == company_id,
        Task.status.in_([S.BACKLOG, S.READY, S.REVISION_REQUESTED]),
    )
    if task_ids is not None:
        if not task_ids:
            return []
        stmt = stmt.where(Task.id.in_(task_ids))
    if agent_id is not None:
        stmt = stmt.where(Task.assignee_agent_id == agent_id)
    candidates = list(db.scalars(stmt))
    if not candidates:
        return []
    assignee_ids = {t.assignee_agent_id for t in candidates if t.assignee_agent_id}
    active_agents = (
        set(
            db.scalars(
                select(Agent.id).where(
                    Agent.company_id == company_id,
                    Agent.id.in_(assignee_ids),
                    Agent.status == AgentStatus.ACTIVE,
                )
            )
        )
        if assignee_ids
        else set()
    )
    unfinished = _unfinished_dependency_counts(db, [t.id for t in candidates])
    waiting = [t.id for t in candidates if t.blocked_reason == WAITING_ON_SUBTASKS]
    open_children = _open_child_counts(db, waiting)
    changed: list[Task] = []
    for task in candidates:
        ok = (
            company_status == CompanyStatus.ACTIVE
            and task.assignee_agent_id in active_agents
            and unfinished.get(task.id, 0) == 0
            and task.blocked_reason != AWAITING_PLAN_APPROVAL
            and not (task.blocked_reason == WAITING_ON_SUBTASKS and open_children.get(task.id, 0))
        )
        current = TaskStatus(task.status)
        if ok and current in (S.BACKLOG, S.REVISION_REQUESTED):
            transition(db, task, S.READY, principal=principal, now=now)
            changed.append(task)
        elif not ok and current == S.READY:
            transition(db, task, S.BACKLOG, principal=principal, now=now)
            changed.append(task)
    return changed


def _open_child_counts(db: Session, task_ids: list[str]) -> dict[str, int]:
    if not task_ids:
        return {}
    rows = db.execute(
        select(Task.parent_task_id, func.count(Task.id))
        .where(
            Task.parent_task_id.in_(task_ids),
            Task.status.notin_([s.value for s in TERMINAL_TASK_STATUSES]),
        )
        .group_by(Task.parent_task_id)
    ).all()
    return {parent: int(n) for parent, n in rows}


def recompute_dependents(
    db: Session, task: Task, *, principal: Principal | None, now: datetime | None = None
) -> None:
    dependents = list(
        db.scalars(
            select(TaskDependency.task_id).where(
                TaskDependency.depends_on_task_id == task.id,
                TaskDependency.company_id == task.company_id,
            )
        )
    )
    recompute_readiness(db, task.company_id, task_ids=dependents, principal=principal, now=now)


def resolve_reviewer(db: Session, task: Task) -> str | None:
    """Explicit reviewer, else a QA agent for code deliverables, else the assignee's manager
    agent. Never the assignee itself."""
    candidate = task.reviewer_agent_id
    if candidate is None:
        candidate = _qa_reviewer(db, task)
    if candidate is None and task.assignee_agent_id:
        candidate = db.scalar(
            select(Agent.manager_agent_id).where(
                Agent.id == task.assignee_agent_id, Agent.company_id == task.company_id
            )
        )
    if candidate is None or candidate == task.assignee_agent_id:
        return None
    status = db.scalar(
        select(Agent.status).where(Agent.id == candidate, Agent.company_id == task.company_id)
    )
    if status is None or status in (AgentStatus.RETIRED, AgentStatus.REJECTED):
        return None
    return candidate


def _qa_reviewer(db: Session, task: Task) -> str | None:
    """A task whose deliverable is code is reviewed by an active QA agent when one exists."""
    has_code = db.scalar(
        select(Artifact.id)
        .where(
            Artifact.company_id == task.company_id,
            Artifact.task_id == task.id,
            Artifact.kind == "code",
        )
        .limit(1)
    )
    if has_code is None:
        return None
    return db.scalar(
        select(Agent.id)
        .where(
            Agent.company_id == task.company_id,
            Agent.role_family == "qa",
            Agent.status == AgentStatus.ACTIVE,
            Agent.id != (task.assignee_agent_id or ""),
        )
        .order_by(Agent.created_at, Agent.id)
        .limit(1)
    )


def needs_review(db: Session, task: Task) -> bool:
    return bool((task.acceptance_criteria or "").strip()) and resolve_reviewer(db, task) is not None


def finish_success(
    db: Session,
    task: Task,
    *,
    summary: str | None,
    output_refs: list[str] | None,
    principal: Principal | None,
    run_id: str | None,
    now: datetime | None = None,
) -> bool:
    """A run succeeded: in_progress -> in_review (if reviewable) else done. A task that still has
    open subtasks waits for them first (``waiting_on_subtasks``). No commit."""
    if task.status != S.IN_PROGRESS:
        return False
    if summary is not None:
        task.result_summary = summary
    if output_refs:
        task.output_refs = list(dict.fromkeys([*task.output_refs, *output_refs]))
    if _park_for_subtasks(db, task, principal=principal, run_id=run_id, now=now):
        return True
    if needs_review(db, task):
        transition(db, task, S.IN_REVIEW, principal=principal, now=now, extra={"run_id": run_id})
        from app.services import reviews

        reviews.ensure_review_task(db, task, principal, now=now)
    else:
        transition(
            db, task, S.DONE, principal=principal, now=now, error=None, extra={"run_id": run_id}
        )
        recompute_dependents(db, task, principal=principal, now=now)
    return True


MAX_RUNS_BEFORE_NO_MORE_WAITING = 6


def _park_for_subtasks(
    db: Session, task: Task, *, principal: Principal | None, run_id: str | None, now
) -> bool:
    """The assignee finished while subtasks are still open: the task goes back to the queue as
    ``waiting_on_subtasks`` and runs again (to integrate their results) once they are closed.
    Bounded: after six runs the task completes regardless."""
    if (
        db.scalar(
            select(func.count(Task.id)).where(
                Task.company_id == task.company_id,
                Task.parent_task_id == task.id,
                Task.status.notin_([s.value for s in TERMINAL_TASK_STATUSES]),
            )
        )
        == 0
    ):
        return False
    runs = db.scalar(select(func.count(TaskRun.id)).where(TaskRun.task_id == task.id)) or 0
    if runs >= MAX_RUNS_BEFORE_NO_MORE_WAITING:
        return False
    task.attempt_count = max(0, task.attempt_count - 1)  # the attempt itself succeeded
    transition(
        db,
        task,
        S.READY,
        principal=principal,
        now=now,
        reason="waiting on subtasks",
        extra={"run_id": run_id},
    )
    task.blocked_reason = WAITING_ON_SUBTASKS
    recompute_readiness(db, task.company_id, task_ids=[task.id], principal=principal, now=now)
    return True


def finish_failure(
    db: Session,
    task: Task,
    *,
    code: str,
    message: str,
    run_id: str | None,
    principal: Principal | None,
    now: datetime | None = None,
) -> bool:
    """A run failed/timed out: retry (-> ready) while attempts remain, else failed. No commit."""
    if task.status != S.IN_PROGRESS:
        return False
    limits = policy.load_policy(db, task.company_id)
    error = {"code": code, "message": message, "run_id": run_id}
    if task.attempt_count < limits.max_attempts_per_task:
        transition(
            db, task, S.READY, principal=principal, now=now, error=error, reason=f"retry: {code}"
        )
        recompute_readiness(db, task.company_id, task_ids=[task.id], principal=principal, now=now)
    else:
        transition(
            db,
            task,
            S.FAILED,
            principal=principal,
            now=now,
            error=error,
            reason=f"no attempts left: {code}",
        )
    return True


# ---------------------------------------------------------------- authorization helpers


def manages(db: Session, company_id: str, manager_id: str, subordinate_id: str | None) -> bool:
    """True if ``manager_id`` is an (indirect) manager of ``subordinate_id``."""
    seen: set[str] = set()
    current = subordinate_id
    while current and current not in seen:
        seen.add(current)
        current = db.scalar(
            select(Agent.manager_agent_id).where(
                Agent.id == current, Agent.company_id == company_id
            )
        )
        if current == manager_id:
            return True
    return False


def _require_writer(ctx: CompanyContext, action: str) -> None:
    require_human_admin(ctx, action)


# ---------------------------------------------------------------- creation


def _existing_by_key(db: Session, company_id: str, key: str | None) -> Task | None:
    if not key:
        return None
    return db.scalar(select(Task).where(Task.company_id == company_id, Task.idempotency_key == key))


def _would_cycle(db: Session, company_id: str, task_id: str, depends_on_id: str) -> bool:
    """Adding task_id -> depends_on_id creates a cycle iff task_id is reachable from
    depends_on_id by following existing dependencies."""
    if task_id == depends_on_id:
        return True
    seen = {depends_on_id}
    frontier = [depends_on_id]
    while frontier:
        rows = db.scalars(
            select(TaskDependency.depends_on_task_id).where(
                TaskDependency.company_id == company_id, TaskDependency.task_id.in_(frontier)
            )
        )
        frontier = []
        for nxt in rows:
            if nxt == task_id:
                return True
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return False


OPEN_STATUSES = [s for s in TaskStatus if s not in TERMINAL_TASK_STATUSES]
LOOP_GUARD_ALERT_AFTER = 5


def _resolve_parent(
    db: Session, company_id: str, data: TaskCreate, run: TaskRun | None
) -> Task | None:
    """Explicit parent, else (for agents) the task being worked on - but only a *work* task is a
    parent by default: planning, routine, review and hiring runs create top-level work."""
    if data.parent_task_id:
        return get_task_or_404(db, company_id, data.parent_task_id)
    if run is None:
        return None
    current = db.scalar(select(Task).where(Task.id == run.task_id, Task.company_id == company_id))
    return current if current is not None and current.kind == TaskKind.WORK else None


def _agent_creation_rules(
    db: Session,
    company_id: str,
    run: TaskRun,
    data: TaskCreate,
    parent: Task | None,
    project_id: str | None,
) -> tuple[Agent, str, str | None, str | None]:
    """Who may create what (enforced here, not in prompts). Returns
    (agent, role, project_id, assignee_agent_id)."""
    agent = db.get(Agent, run.agent_id)
    assert agent is not None and agent.company_id == company_id
    role = agent_roles.role_of(db, agent)
    assignee = data.assignee_agent_id
    if role in (agent_roles.SPECIALIST, agent_roles.HR):
        if assignee and assignee != agent.id:
            raise Forbidden(
                "You cannot assign tasks to other people. Create the task for yourself, or ask "
                "your lead with company_request_help."
            )
        assignee = agent.id
    elif role == agent_roles.LEAD:
        own_subtask = parent is not None and parent.assignee_agent_id == agent.id
        if not own_subtask:
            if project_id is None:
                led = list(
                    db.scalars(
                        select(Project).where(
                            Project.company_id == company_id,
                            Project.lead_agent_id == agent.id,
                            Project.status.in_(["planned", "active"]),
                        )
                    )
                )
                if len(led) != 1:
                    raise Forbidden(
                        "A lead creates tasks inside the projects it leads: pass project_id"
                        + (f" (one of: {', '.join(p.id for p in led)})" if led else "")
                    )
                project_id = led[0].id
            else:
                project = get_project_or_404(db, company_id, project_id)
                if project.lead_agent_id != agent.id:
                    raise Forbidden("Only the lead of a project can create tasks inside it")
    return agent, role, project_id, assignee


def _route_unassigned(
    db: Session,
    company_id: str,
    project_id: str | None,
    creator: Agent | None,
) -> str | None:
    """Project lead routing: a project task nobody owns goes to the project lead's queue; one the
    lead itself creates goes to the least loaded of its reports (else back to the lead)."""
    if not project_id:
        return None
    project = db.get(Project, project_id)
    if project is None or project.company_id != company_id or not project.lead_agent_id:
        return None
    lead = db.get(Agent, project.lead_agent_id)
    if lead is None or lead.status != AgentStatus.ACTIVE:
        return None
    if creator is not None and creator.id == lead.id:
        reports = list(
            db.scalars(
                select(Agent).where(
                    Agent.company_id == company_id,
                    Agent.manager_agent_id == lead.id,
                    Agent.status == AgentStatus.ACTIVE,
                )
            )
        )
        if reports:
            load = dict(
                db.execute(
                    select(Task.assignee_agent_id, func.count(Task.id))
                    .where(
                        Task.company_id == company_id,
                        Task.assignee_agent_id.in_([r.id for r in reports]),
                        Task.status.in_(OPEN_STATUSES),
                    )
                    .group_by(Task.assignee_agent_id)
                ).all()
            )
            return min(reports, key=lambda r: (load.get(r.id, 0), r.created_at, r.id)).id
    return lead.id


def _duplicate_open_task(
    db: Session, company_id: str, data: TaskCreate, project_id: str | None, assignee: str | None
) -> Task | None:
    wanted = " ".join(data.title.lower().split())
    stmt = select(Task).where(
        Task.company_id == company_id,
        Task.status.in_(OPEN_STATUSES),
        Task.project_id == project_id if project_id else Task.project_id.is_(None),
        Task.assignee_agent_id == assignee if assignee else Task.assignee_agent_id.is_(None),
    )
    for task in db.scalars(stmt.limit(500)):
        if " ".join(task.title.lower().split()) == wanted:
            return task
    return None


def _note_duplicate(db: Session, company_id: str, agent: Agent, existing: Task) -> None:
    from app.services import alerts

    audit.record(
        db,
        company_id=company_id,
        principal=Principal(
            type=PrincipalType.AGENT, id=agent.id, label=agent.name, company_id=company_id
        ),
        action="task.duplicate_suppressed",
        target_type="task",
        target_id=existing.id,
        summary=f"{agent.name} tried to create a duplicate of open task '{existing.title}'",
        data={"existing_task_id": existing.id},
    )
    db.flush()
    from app.models.audit import AuditEvent

    recent = db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.company_id == company_id,
            AuditEvent.action == "task.duplicate_suppressed",
            AuditEvent.actor_id == agent.id,
            AuditEvent.created_at >= utcnow() - timedelta(hours=1),
        )
    )
    if (recent or 0) >= LOOP_GUARD_ALERT_AFTER:
        alerts.raise_alert(
            db,
            company_id,
            kind="loop_guard",
            title=f"{agent.name} keeps creating duplicate tasks",
            body="The loop guard returned the existing open task each time. The agent may be "
            "stuck in a loop.",
            related_type="agent",
            related_id=agent.id,
            dedupe_key=f"loop_guard:{agent.id}",
        )


def _hold_for_plan_approval(db: Session, task: Task, agent: Agent, run: TaskRun) -> None:
    """Supervised mode: what a manager plans waits for a human (one approval per run)."""
    from app.models.governance import Approval
    from app.services import approvals

    task.blocked_reason = AWAITING_PLAN_APPROVAL
    db.flush()
    pending = db.scalar(
        select(Approval.id).where(
            Approval.company_id == task.company_id,
            Approval.subject_type == "agent_plan",
            Approval.subject_id == run.id,
        )
    )
    if pending is not None:
        return
    approvals.create_approval_record(
        db,
        company_id=task.company_id,
        principal=Principal(
            type=PrincipalType.AGENT,
            id=agent.id,
            label=agent.name,
            company_id=task.company_id,
        ),
        kind="other",
        title=f"Approve the plan {agent.name} is creating",
        reason="The company is in supervised mode: tasks created by managers wait for approval.",
        scope={"action": "release_plan", "run_id": run.id},
        task_id=run.task_id,
        subject=("agent_plan", run.id),
    )


def apply_plan_decision(db: Session, ctx: CompanyContext, approval, decision: str) -> None:
    """Called inside ``approvals.decide``: approving a plan releases its tasks, rejecting cancels
    them, requesting changes keeps them held. No commit."""
    held = list(
        db.scalars(
            select(Task).where(
                Task.company_id == ctx.company_id,
                Task.created_by_run_id == approval.subject_id,
                Task.blocked_reason == AWAITING_PLAN_APPROVAL,
                Task.status.in_(OPEN_STATUSES),
            )
        )
    )
    if decision == "approve":
        approval.consumed_at = utcnow()
        for task in held:
            task.blocked_reason = None
            task.updated_at = utcnow()
        recompute_readiness(
            db, ctx.company_id, task_ids=[t.id for t in held], principal=ctx.principal
        )
    elif decision == "reject":
        for task in held:
            transition(db, task, S.CANCELLED, principal=ctx.principal, reason="plan rejected")


def _create_task_core(
    db: Session,
    company_id: str,
    principal: Principal | None,
    data: TaskCreate,
    *,
    run: TaskRun | None,
    now: datetime | None = None,
) -> tuple[Task, bool]:
    existing = _existing_by_key(db, company_id, data.idempotency_key)
    if existing is not None:
        return existing, False

    limits = policy.load_policy(db, company_id)
    parent = _resolve_parent(db, company_id, data, run)
    # An agent's subtask stays in its parent's project unless it names another one.
    project_id = data.project_id or (parent.project_id if parent and run is not None else None)
    if project_id:
        get_project_or_404(db, company_id, project_id)
    assignee_id = data.assignee_agent_id
    if assignee_id:
        get_agent_or_404(db, company_id, assignee_id, label="Assignee")
    if data.reviewer_agent_id:
        get_agent_or_404(db, company_id, data.reviewer_agent_id, label="Reviewer")
    dep_ids = list(data.depends_on)
    for dep_id in dep_ids:
        get_task_or_404(db, company_id, dep_id)

    creator: Agent | None = None
    role: str | None = None
    if run is not None and data.kind != TaskKind.WORK:
        # Agents create work. Planning runs, reviews and hiring requests are made by the platform
        # (wakes, review hooks, request_hire); a stray kind from a small model becomes plain work.
        data = data.model_copy(update={"kind": TaskKind.WORK.value})
    if run is not None:
        creator, role, project_id, assignee_id = _agent_creation_rules(
            db, company_id, run, data, parent, project_id
        )
    elif principal is not None and principal.is_agent:
        creator = db.get(Agent, principal.id)
    if assignee_id is None and not data.required_skills and data.kind == TaskKind.WORK:
        assignee_id = _route_unassigned(db, company_id, project_id, creator)

    depth = parent.delegation_depth + 1 if parent is not None else 0
    if depth > limits.max_delegation_depth:
        raise LimitExceeded(
            f"Delegation depth {depth} exceeds the company limit of {limits.max_delegation_depth}",
            details={"limit": "max_delegation_depth", "max": limits.max_delegation_depth},
        )
    if run is not None:
        created = db.scalar(
            select(func.count(Task.id)).where(
                Task.company_id == company_id, Task.created_by_run_id == run.id
            )
        )
        if (created or 0) >= limits.max_tasks_per_planning_cycle:
            raise LimitExceeded(
                "This run already created the maximum number of tasks "
                f"({limits.max_tasks_per_planning_cycle})",
                details={
                    "limit": "max_tasks_per_planning_cycle",
                    "max": limits.max_tasks_per_planning_cycle,
                },
            )
        duplicate = _duplicate_open_task(db, company_id, data, project_id, assignee_id)
        if duplicate is not None:  # loop guard: the same open task is returned, not created twice
            assert creator is not None
            _note_duplicate(db, company_id, creator, duplicate)
            return duplicate, False
        if data.kind not in (TaskKind.REVIEW, TaskKind.HIRING, TaskKind.PLANNING):
            open_total = db.scalar(
                select(func.count(Task.id)).where(
                    Task.company_id == company_id, Task.status.in_(OPEN_STATUSES)
                )
            )
            if (open_total or 0) >= limits.max_open_tasks:
                raise LimitExceeded(
                    f"The company already has {open_total} open tasks (limit "
                    f"{limits.max_open_tasks}); finish or cancel some first",
                    details={"limit": "max_open_tasks", "max": limits.max_open_tasks},
                )
        if (
            data.budget_micros is not None
            and parent is not None
            and parent.budget_micros is not None
        ):
            allocated = db.scalar(
                select(func.coalesce(func.sum(Task.budget_micros), 0)).where(
                    Task.company_id == company_id,
                    Task.parent_task_id == parent.id,
                    Task.status != S.CANCELLED,
                )
            )
            spent = ledger.committed_micros(db, company_id, task_id=parent.id)
            remaining = parent.budget_micros - int(allocated or 0) - spent
            if data.budget_micros > remaining:
                raise LimitExceeded(
                    "Subtask budget exceeds the parent task's remaining budget",
                    details={
                        "limit": "parent_task_budget",
                        "remaining_micros": max(0, remaining),
                        "requested_micros": data.budget_micros,
                    },
                )

    ctype, cid = creator_of(principal)
    task = Task(
        company_id=company_id,
        project_id=project_id,
        parent_task_id=parent.id if parent else None,
        title=data.title,
        description=data.description,
        acceptance_criteria=data.acceptance_criteria,
        kind=data.kind,
        status=S.BACKLOG,
        priority=data.priority,
        assignee_agent_id=assignee_id,
        reviewer_agent_id=data.reviewer_agent_id,
        required_skills=list(data.required_skills),
        budget_micros=data.budget_micros,
        delegation_depth=depth,
        due_at=data.due_at,
        input_refs=list(data.input_refs),
        output_refs=[],
        created_by_type=ctype,
        created_by_id=cid,
        created_by_run_id=run.id if run is not None else None,
        idempotency_key=data.idempotency_key,
    )
    db.add(task)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = _existing_by_key(db, company_id, data.idempotency_key)
        if existing is None:
            raise
        return existing, False
    for dep_id in dep_ids:
        db.add(TaskDependency(company_id=company_id, task_id=task.id, depends_on_task_id=dep_id))
    db.flush()
    audit.record(
        db,
        company_id=company_id,
        principal=principal,
        action="task.created",
        target_type="task",
        target_id=task.id,
        summary=f"Created task '{task.title}'",
        data={
            "kind": task.kind,
            "assignee_agent_id": task.assignee_agent_id,
            "parent_task_id": task.parent_task_id,
            "project_id": task.project_id,
            "run_id": run.id if run else None,
        },
    )
    events.emit(db, company_id, "task.created", task_event_payload(task))
    if (
        run is not None
        and creator is not None
        and limits.autonomy_mode == AutonomyMode.SUPERVISED
        and role in agent_roles.ASSIGNERS
        and task.kind == TaskKind.WORK
    ):
        _hold_for_plan_approval(db, task, creator, run)
    if (
        creator is not None
        and task.assignee_agent_id is None
        and not task.required_skills
        and task.kind == TaskKind.WORK
        and run is not None
    ):
        from app.services import wakes

        wakes.wake_founder(
            db,
            company_id,
            "assignment",
            f"Task '{task.title}' [id {task.id}] has no owner and its project has no lead: "
            "assign it, or give the project a lead.",
            ref_type="task",
            ref_id=task.id,
            actor_agent_id=creator.id,
        )
    recompute_readiness(db, company_id, task_ids=[task.id], principal=principal, now=now)
    return task, True


def create_task(db: Session, ctx: CompanyContext, data: TaskCreate) -> tuple[Task, bool]:
    """Human-created task. Returns (task, created); created=False for an idempotent replay."""
    _require_writer(ctx, "create tasks")
    task, created = _create_task_core(db, ctx.company_id, ctx.principal, data, run=None)
    db.commit()
    return task, created


def create_system_task(
    db: Session, company_id: str, data: TaskCreate, *, now: datetime
) -> tuple[Task, bool]:
    """Task created by the platform itself (schedules). No commit."""
    return _create_task_core(db, company_id, None, data, run=None, now=now)


def create_task_as_agent(
    db: Session, ctx: CompanyContext, run: TaskRun, data: TaskCreate
) -> tuple[Task, bool]:
    if not ctx.principal.is_agent or ctx.principal.id != run.agent_id:
        raise Forbidden("Only the running agent can create tasks through its run")
    task, created = _create_task_core(db, ctx.company_id, ctx.principal, data, run=run)
    db.commit()
    return task, created


# ---------------------------------------------------------------- queries


def get_task(db: Session, ctx: CompanyContext, task_id: str) -> Task:
    return get_task_or_404(db, ctx.company_id, task_id)


def list_tasks(
    db: Session,
    ctx: CompanyContext,
    *,
    limit: int,
    cursor: str | None,
    status: str | None = None,
    project_id: str | None = None,
    assignee_agent_id: str | None = None,
    parent_task_id: str | None = None,
    kind: str | None = None,
):
    stmt = select(Task).where(Task.company_id == ctx.company_id)
    if status:
        stmt = stmt.where(Task.status == status)
    if project_id:
        stmt = stmt.where(Task.project_id == project_id)
    if assignee_agent_id:
        stmt = stmt.where(Task.assignee_agent_id == assignee_agent_id)
    if parent_task_id:
        stmt = stmt.where(Task.parent_task_id == parent_task_id)
    if kind:
        stmt = stmt.where(Task.kind == kind)
    return paginate(db, stmt, Task, limit=limit, cursor=cursor, descending=True)


def serialize_tasks(db: Session, tasks: list[Task]) -> list[TaskOut]:
    deps: dict[str, list[str]] = {t.id: [] for t in tasks}
    if tasks:
        rows = db.execute(
            select(TaskDependency.task_id, TaskDependency.depends_on_task_id)
            .where(TaskDependency.task_id.in_(list(deps)))
            .order_by(TaskDependency.created_at, TaskDependency.id)
        ).all()
        for task_id, dep_id in rows:
            deps[task_id].append(dep_id)
    out = []
    for task in tasks:
        model = TaskOut.model_validate(task)
        model.depends_on = deps[task.id]
        out.append(model)
    return out


# ---------------------------------------------------------------- updates


def update_task(db: Session, ctx: CompanyContext, task_id: str, data: TaskUpdate) -> Task:
    _require_writer(ctx, "edit tasks")
    task = get_task_or_404(db, ctx.company_id, task_id)
    fields = data.model_fields_set
    if TaskStatus(task.status) in TERMINAL_TASK_STATUSES:
        raise Conflict(f"A {task.status} task is read-only")
    if (
        task.status == S.IN_PROGRESS
        and "assignee_agent_id" in fields
        and data.assignee_agent_id != task.assignee_agent_id
    ):
        raise Conflict("Cannot reassign a task while it is running; block or cancel it first")
    if "project_id" in fields and data.project_id:
        get_project_or_404(db, ctx.company_id, data.project_id)
    if "assignee_agent_id" in fields and data.assignee_agent_id:
        get_agent_or_404(db, ctx.company_id, data.assignee_agent_id, label="Assignee")
    if "reviewer_agent_id" in fields and data.reviewer_agent_id:
        get_agent_or_404(db, ctx.company_id, data.reviewer_agent_id, label="Reviewer")

    changed: list[str] = []
    for field in UPDATABLE:
        if field not in fields:
            continue
        new = getattr(data, field)
        if isinstance(new, list):
            new = list(new)
        if getattr(task, field) != new:
            setattr(task, field, new)
            changed.append(field)
    if changed:
        task.updated_at = utcnow()
        audit.record(
            db,
            company_id=ctx.company_id,
            principal=ctx.principal,
            action="task.updated",
            target_type="task",
            target_id=task.id,
            summary=f"Updated task '{task.title}': {', '.join(changed)}",
            data={"changed": changed},
        )
        events.emit(db, ctx.company_id, "task.updated", task_event_payload(task))
        if "assignee_agent_id" in changed:
            task.blocked_reason = None
            recompute_readiness(db, ctx.company_id, task_ids=[task.id], principal=ctx.principal)
        if "budget_micros" in changed:
            release_budget_blocked(db, ctx.company_id, ctx.principal, task_ids=[task.id])
        db.commit()
    return task


def _agent_authority(db: Session, ctx: CompanyContext, task: Task) -> tuple[Agent, str, bool, bool]:
    """(agent, role, may_manage, is_creator): may_manage = the founder, the lead of the task's
    project, a manager of its assignee, or its creator."""
    principal = ctx.principal
    agent = db.get(Agent, principal.id)
    assert agent is not None
    role = agent_roles.role_of(db, agent)
    is_creator = task.created_by_type == "agent" and task.created_by_id == principal.id
    is_manager = manages(db, ctx.company_id, principal.id, task.assignee_agent_id)
    is_project_lead = False
    if task.project_id:
        is_project_lead = (
            db.scalar(
                select(Project.lead_agent_id).where(
                    Project.id == task.project_id, Project.company_id == ctx.company_id
                )
            )
            == principal.id
        )
    may_manage = role == agent_roles.FOUNDER or is_creator or is_manager or is_project_lead
    return agent, role, may_manage, is_creator


def agent_update_task(
    db: Session,
    ctx: CompanyContext,
    run: TaskRun,
    task_id: str,
    *,
    status: str | None,
    reason: str | None,
    assignee_agent_id: str | None,
    assignee_given: bool,
) -> Task:
    """Limited agent edit: block/unblock and (re)assignment, only on tasks the agent created
    or manages (or its own current task, for blocking). Only managers (founder, leads, anyone
    with reports) may assign work to someone else; a manager may also hand its *own running task*
    to a colleague (routing a task that landed in its queue)."""
    principal = ctx.principal
    task = get_task_or_404(db, ctx.company_id, task_id)
    agent, role, may_manage, is_creator = _agent_authority(db, ctx, task)
    is_own_current = task.id == run.task_id and task.assignee_agent_id == principal.id
    if status is not None:
        if status == "blocked":
            if not (may_manage or is_own_current):
                raise Forbidden("You can only block your own task or tasks you created/manage")
            _block(db, task, principal, reason, cancel_runs=not is_own_current)
        elif status == "unblocked":
            if not may_manage:
                raise Forbidden("Only the creator or a manager can unblock a task")
            _unblock(db, task, principal)
        else:  # pragma: no cover - schema restricts values
            raise Conflict("Unsupported status change")
    if assignee_given:
        if not agent_roles.can_assign_others(role) and assignee_agent_id != principal.id:
            raise Forbidden(
                "You cannot reassign tasks to other people. Ask your lead with "
                "company_request_help."
            )
        handoff = is_own_current and task.status == S.IN_PROGRESS
        if not (may_manage or handoff):
            raise Forbidden("You can only reassign tasks you created or manage")
        if TaskStatus(task.status) in TERMINAL_TASK_STATUSES or (
            task.status == S.IN_PROGRESS and not handoff
        ):
            raise Conflict("Cannot reassign a running or finished task")
        if assignee_agent_id:
            get_agent_or_404(db, ctx.company_id, assignee_agent_id, label="Assignee")
        if task.assignee_agent_id != assignee_agent_id:
            task.assignee_agent_id = assignee_agent_id
            task.updated_at = utcnow()
            audit.record(
                db,
                company_id=ctx.company_id,
                principal=principal,
                action="task.updated",
                target_type="task",
                target_id=task.id,
                summary=f"Reassigned task '{task.title}'",
                data={"changed": ["assignee_agent_id"], "run_id": run.id, "handoff": handoff},
            )
            events.emit(db, ctx.company_id, "task.updated", task_event_payload(task))
            if handoff:  # the running attempt ends here; the new assignee starts fresh
                task.attempt_count = max(0, task.attempt_count - 1)
                transition(
                    db, task, S.READY, principal=principal, reason="handed off to a colleague"
                )
            recompute_readiness(db, ctx.company_id, task_ids=[task.id], principal=principal)
    db.commit()
    return task


def retry_as_agent(
    db: Session, ctx: CompanyContext, task_id: str, *, reason: str | None, assignee_id: str | None
) -> Task:
    """A manager retries a failed task (optionally with another assignee)."""
    task = get_task_or_404(db, ctx.company_id, task_id)
    _, _, may_manage, _ = _agent_authority(db, ctx, task)
    if not may_manage:
        raise Forbidden("Only the founder, the project lead or the task's manager can retry it")
    if task.status != S.FAILED:
        raise InvalidTransition(
            f"Only failed tasks can be retried (task is {task.status})",
            details={"from": task.status, "to": "backlog"},
        )
    if assignee_id:
        get_agent_or_404(db, ctx.company_id, assignee_id, label="Assignee")
        task.assignee_agent_id = assignee_id
    task.attempt_count = 0
    transition(db, task, S.BACKLOG, principal=ctx.principal, error=None, reason=reason or "retry")
    recompute_readiness(db, ctx.company_id, task_ids=[task.id], principal=ctx.principal)
    db.commit()
    return task


def cancel_as_agent(db: Session, ctx: CompanyContext, task_id: str, reason: str | None) -> Task:
    from app.services import runs as run_service

    task = get_task_or_404(db, ctx.company_id, task_id)
    _, _, may_manage, _ = _agent_authority(db, ctx, task)
    if not may_manage:
        raise Forbidden("Only the founder, the project lead or the task's manager can cancel it")
    if TaskStatus(task.status) in TERMINAL_TASK_STATUSES:
        raise InvalidTransition(
            f"Cannot cancel a task that is {task.status}",
            details={"from": task.status, "to": "cancelled"},
        )
    transition(db, task, S.CANCELLED, principal=ctx.principal, reason=reason)
    run_service.request_cancel_for_task(db, task, "task_cancelled", ctx.principal)
    db.commit()
    return task


# ---------------------------------------------------------------- lifecycle actions


def _block(
    db: Session, task: Task, principal: Principal | None, reason: str | None, *, cancel_runs: bool
) -> None:
    from app.services import runs as run_service

    if TaskStatus(task.status) not in EDITABLE_OPEN or task.status == S.BLOCKED:
        raise InvalidTransition(
            f"Cannot block a task that is {task.status}",
            details={"from": task.status, "to": "blocked"},
        )
    was_running = task.status == S.IN_PROGRESS
    transition(
        db,
        task,
        S.BLOCKED,
        principal=principal,
        reason=reason,
        error={"code": "blocked", "message": reason or "Blocked", "run_id": None},
    )
    if cancel_runs and was_running:
        run_service.request_cancel_for_task(db, task, "task_blocked", principal)


def _unblock(db: Session, task: Task, principal: Principal | None) -> None:
    if task.status != S.BLOCKED:
        raise InvalidTransition(
            f"Cannot unblock a task that is {task.status}",
            details={"from": task.status, "to": "backlog"},
        )
    transition(db, task, S.BACKLOG, principal=principal, error=None, reason="unblocked")
    recompute_readiness(db, task.company_id, task_ids=[task.id], principal=principal)


def block_task(db: Session, ctx: CompanyContext, task_id: str, reason: str | None) -> Task:
    _require_writer(ctx, "block tasks")
    task = get_task_or_404(db, ctx.company_id, task_id)
    _block(db, task, ctx.principal, reason, cancel_runs=True)
    db.commit()
    return task


def unblock_task(db: Session, ctx: CompanyContext, task_id: str) -> Task:
    _require_writer(ctx, "unblock tasks")
    task = get_task_or_404(db, ctx.company_id, task_id)
    _unblock(db, task, ctx.principal)
    db.commit()
    return task


def cancel_task(db: Session, ctx: CompanyContext, task_id: str, reason: str | None) -> Task:
    from app.services import runs as run_service

    _require_writer(ctx, "cancel tasks")
    task = get_task_or_404(db, ctx.company_id, task_id)
    if TaskStatus(task.status) in TERMINAL_TASK_STATUSES:
        raise InvalidTransition(
            f"Cannot cancel a task that is {task.status}",
            details={"from": task.status, "to": "cancelled"},
        )
    transition(db, task, S.CANCELLED, principal=ctx.principal, reason=reason)
    run_service.request_cancel_for_task(db, task, "task_cancelled", ctx.principal)
    db.commit()
    return task


def retry_task(db: Session, ctx: CompanyContext, task_id: str) -> Task:
    _require_writer(ctx, "retry tasks")
    task = get_task_or_404(db, ctx.company_id, task_id)
    if task.status != S.FAILED:
        raise InvalidTransition(
            f"Only failed tasks can be retried (task is {task.status})",
            details={"from": task.status, "to": "backlog"},
        )
    task.attempt_count = 0
    transition(db, task, S.BACKLOG, principal=ctx.principal, error=None, reason="manual retry")
    recompute_readiness(db, ctx.company_id, task_ids=[task.id], principal=ctx.principal)
    db.commit()
    return task


def release_budget_blocked(
    db: Session,
    company_id: str,
    principal: Principal | None,
    *,
    task_ids: list[str] | None = None,
    due_before: datetime | None = None,
    codes: tuple[str, ...] = ("budget_exceeded",),
) -> int:
    """Re-evaluate tasks the dispatcher blocked on a limit (``codes``) after that limit or
    the spend period changed. With ``due_before`` only tasks whose ``retry_after`` has passed
    are released. No commit."""
    stmt = select(Task).where(Task.company_id == company_id, Task.status == S.BLOCKED)
    if task_ids is not None:
        stmt = stmt.where(Task.id.in_(task_ids))
    released: list[str] = []
    for task in db.scalars(stmt):
        err = task.error or {}
        if err.get("code") not in codes:
            continue
        if due_before is not None:
            retry_after = err.get("retry_after")
            if (
                not retry_after
                or datetime.fromisoformat(retry_after.replace("Z", "+00:00")) > due_before
            ):
                continue
        transition(
            db, task, S.BACKLOG, principal=principal, error=None, reason="limits re-evaluated"
        )
        released.append(task.id)
    if released:
        recompute_readiness(db, company_id, task_ids=released, principal=principal)
    return len(released)


# ---------------------------------------------------------------- dependencies


def add_dependency(db: Session, ctx: CompanyContext, task_id: str, depends_on_task_id: str) -> Task:
    _require_writer(ctx, "edit task dependencies")
    task = get_task_or_404(db, ctx.company_id, task_id)
    dep = get_task_or_404(db, ctx.company_id, depends_on_task_id)
    if TaskStatus(task.status) in TERMINAL_TASK_STATUSES:
        raise Conflict(f"A {task.status} task is read-only")
    if _would_cycle(db, ctx.company_id, task.id, dep.id):
        raise Conflict("This dependency would create a cycle")
    exists = db.scalar(
        select(TaskDependency.id).where(
            TaskDependency.task_id == task.id, TaskDependency.depends_on_task_id == dep.id
        )
    )
    if exists:
        raise Conflict("Dependency already exists")
    db.add(TaskDependency(company_id=ctx.company_id, task_id=task.id, depends_on_task_id=dep.id))
    db.flush()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="task.dependency_added",
        target_type="task",
        target_id=task.id,
        summary=f"Task '{task.title}' now depends on '{dep.title}'",
        data={"depends_on_task_id": dep.id},
    )
    events.emit(db, ctx.company_id, "task.updated", task_event_payload(task))
    recompute_readiness(db, ctx.company_id, task_ids=[task.id], principal=ctx.principal)
    db.commit()
    return task


def remove_dependency(db: Session, ctx: CompanyContext, task_id: str, dep_id: str) -> Task:
    _require_writer(ctx, "edit task dependencies")
    task = get_task_or_404(db, ctx.company_id, task_id)
    link = db.scalar(
        select(TaskDependency).where(
            TaskDependency.company_id == ctx.company_id,
            TaskDependency.task_id == task.id,
            TaskDependency.depends_on_task_id == dep_id,
        )
    )
    if link is None:
        raise NotFound("Dependency not found")
    db.delete(link)
    db.flush()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="task.dependency_removed",
        target_type="task",
        target_id=task.id,
        summary=f"Removed a dependency from task '{task.title}'",
        data={"depends_on_task_id": dep_id},
    )
    events.emit(db, ctx.company_id, "task.updated", task_event_payload(task))
    recompute_readiness(db, ctx.company_id, task_ids=[task.id], principal=ctx.principal)
    db.commit()
    return task


# ---------------------------------------------------------------- review


def review_task(
    db: Session, ctx: CompanyContext, task_id: str, decision: str, note: str | None
) -> Task:
    """Accept (-> done) or request a revision. Humans (owner/admin) or the resolved reviewer."""
    if ctx.role == Role.VIEWER:
        raise Forbidden("Viewers have read-only access")
    task = get_task_or_404(db, ctx.company_id, task_id)
    if task.kind == TaskKind.REVIEW:  # a review task's own id means "the task it is about"
        from app.services import reviews

        target = reviews.target_id_of(task)
        if target:
            task = get_task_or_404(db, ctx.company_id, target)
    if ctx.principal.is_agent:
        if resolve_reviewer(db, task) != ctx.principal.id:
            raise Forbidden("Only the task's reviewer can review it")
    elif not ctx.is_human_admin:
        raise Forbidden("Only company owners and admins can review tasks")
    if task.status != S.IN_REVIEW:
        raise InvalidTransition(
            f"Only tasks in review can be reviewed (task is {task.status})",
            details={"from": task.status},
        )
    principal = ctx.principal
    task.review_note = note
    if decision == "accept":
        transition(
            db,
            task,
            S.DONE,
            principal=principal,
            error=None,
            reason=note,
            extra={"review": "accept"},
        )
        recompute_dependents(db, task, principal=principal)
    else:
        limits = policy.load_policy(db, ctx.company_id)
        task.revision_count += 1
        task.attempt_count = 0  # each revision gets a fresh set of attempts
        if task.revision_count > limits.max_revisions:
            transition(
                db,
                task,
                S.REVISION_REQUESTED,
                principal=principal,
                reason=note,
                extra={"review": "request_revision"},
            )
            transition(
                db,
                task,
                S.FAILED,
                principal=principal,
                error={
                    "code": "max_revisions",
                    "message": f"More than {limits.max_revisions} revisions requested",
                    "run_id": None,
                },
                reason="max revisions exceeded",
            )
        else:
            transition(
                db,
                task,
                S.REVISION_REQUESTED,
                principal=principal,
                reason=note,
                extra={"review": "request_revision"},
            )
            recompute_readiness(db, ctx.company_id, task_ids=[task.id], principal=principal)
    db.commit()
    return task


def complete_task_as_agent(
    db: Session,
    ctx: CompanyContext,
    run: TaskRun,
    task_id: str,
    *,
    result_summary: str,
    output_refs: list[str],
) -> Task:
    """The assignee reports completion during its own run."""
    task = get_task_or_404(db, ctx.company_id, task_id)
    if task.id != run.task_id or task.assignee_agent_id != ctx.principal.id:
        raise Forbidden("Only the assignee can complete a task, during its own run")
    if task.status == S.DONE and task.kind == TaskKind.REVIEW:
        return task  # recording the review already completed this review task: idempotent
    if task.status != S.IN_PROGRESS:
        raise InvalidTransition(
            f"Only in-progress tasks can be completed (task is {task.status})",
            details={"from": task.status},
        )
    finish_success(
        db,
        task,
        summary=result_summary,
        output_refs=output_refs,
        principal=ctx.principal,
        run_id=run.id,
    )
    if task.kind == TaskKind.PLANNING:
        agent = db.get(Agent, ctx.principal.id)
        if agent is not None and agent.kind == "founder":
            from app.services import notes

            notes.link_created_records(db, ctx.company_id, run, agent)
    db.commit()
    return task
