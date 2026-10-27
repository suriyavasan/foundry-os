"""Reviews as tasks, and failure analysis.

* When a task enters ``in_review`` an open **review task** (kind ``review``) is created for its
  reviewer: one per in-review task and revision (idempotency key ``review:<task>:<revision>``).
  The reviewer accepts or requests a revision with ``company_review_task`` on the *reviewed* task
  (a review task's own id is accepted too and resolved to the target). Recording the review closes
  the review task: it is completed when the reviewer is running it, cancelled when it never started.
* M12: a review task that ends without a decision (or fails) leaves its target ``in_review``; the
  autonomy watchdog re-issues it (``reissue_review``, key ``review:<task>:<rev>:r<n>``), then moves
  it to the Co-founder, then alerts. Leaving ``in_review`` closes every open review task of it.
* When a task fails for good (attempts or revisions exhausted) a **failure-analysis** task (also
  kind ``review``, key ``failure:<task>``) goes to the project lead, else the assignee's manager,
  else the AI Co-founder, with the error history and the options.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.principal import Principal
from app.models.agent import Agent
from app.models.enums import AgentKind, AgentStatus, TaskKind, TaskStatus
from app.models.run import TaskRun
from app.models.work import Project, Task
from app.schemas.work import TaskCreate
from app.services import task_briefs
from app.storage.base import ArtifactStorage

REVIEW_PREFIX = "review:"
FAILURE_PREFIX = "failure:"
TARGET_REF = "task:"
FAILURE_KINDS = (TaskKind.WORK.value, TaskKind.HIRING.value, TaskKind.ROUTINE.value)


def review_key(task: Task) -> str:
    return f"{REVIEW_PREFIX}{task.id}:{task.revision_count}"


def failure_key(db: Session, task: Task) -> str:
    """One analysis per failure: the run count makes a failure after a retry a new one."""
    runs = db.scalar(select(func.count(TaskRun.id)).where(TaskRun.task_id == task.id))
    return f"{FAILURE_PREFIX}{task.id}:{int(runs or 0)}"


def target_id_of(review_task: Task) -> str | None:
    """The reviewed / failed task id a review-kind task points at."""
    if review_task.kind != TaskKind.REVIEW:
        return None
    for ref in review_task.input_refs or []:
        if isinstance(ref, str) and ref.startswith(TARGET_REF):
            return ref[len(TARGET_REF) :]
    return None


def _by_key(db: Session, company_id: str, key: str) -> Task | None:
    return db.scalar(select(Task).where(Task.company_id == company_id, Task.idempotency_key == key))


def _assignee_name(db: Session, task: Task) -> str:
    if not task.assignee_agent_id:
        return "unassigned"
    return db.scalar(select(Agent.name).where(Agent.id == task.assignee_agent_id)) or "unknown"


# ---------------------------------------------------------------- review tasks


def _review_lines(db: Session, task: Task, extra: list[str] | None = None) -> str:
    lines = [
        f'Review the work of {_assignee_name(db, task)} on task "{task.title}" '
        f"[task id {task.id}] (revision {task.revision_count}).",
        "",
        "Acceptance criteria:",
        (task.acceptance_criteria or "(none)").strip(),
    ]
    if task.result_summary:
        lines += ["", f"Result summary from the assignee: {task.result_summary.strip()}"]
    if extra:
        lines += ["", *extra]
    lines += [
        "",
        f"The deliverables are shown below your task. Decide now: call company_review_task with "
        f"task_id={task.id} and decision=accept (the work meets every criterion) or "
        "decision=request_revision with a specific note on what to change. Then stop.",
    ]
    return "\n".join(lines)


def _create_review(
    db: Session,
    task: Task,
    reviewer_id: str,
    key: str,
    principal: Principal | None,
    now: datetime | None,
    extra: list[str] | None = None,
) -> Task:
    from app.services import tasks

    data = TaskCreate(
        title=f"Review: {task.title}"[:300],
        description=_review_lines(db, task, extra),
        kind=TaskKind.REVIEW.value,
        priority=task.priority,
        project_id=task.project_id,
        assignee_agent_id=reviewer_id,
        input_refs=[f"{TARGET_REF}{task.id}"],
        idempotency_key=key,
    )
    review, _ = tasks._create_task_core(db, task.company_id, principal, data, run=None, now=now)
    return review


def ensure_review_task(
    db: Session, task: Task, principal: Principal | None, now: datetime | None = None
) -> Task | None:
    """Create the (single) review task for a task that just entered ``in_review``. No commit."""
    from app.services import tasks

    reviewer_id = tasks.resolve_reviewer(db, task)
    if reviewer_id is None:
        return None
    key = review_key(task)
    existing = _by_key(db, task.company_id, key)
    if existing is not None:
        return existing
    return _create_review(db, task, reviewer_id, key, principal, now)


def open_review_tasks(db: Session, task: Task) -> list[Task]:
    """Every open review task that targets ``task`` (the original and any re-issue, any revision;
    never the failure-analysis task, which has its own key prefix)."""
    from app.services import tasks

    return [
        review
        for review in db.scalars(
            select(Task).where(
                Task.company_id == task.company_id,
                Task.kind == TaskKind.REVIEW,
                Task.idempotency_key.like(f"{REVIEW_PREFIX}{task.id}:%"),
            )
        )
        if TaskStatus(review.status) not in tasks.TERMINAL_TASK_STATUSES
    ]


def reissue_review(
    db: Session,
    task: Task,
    n: int,
    principal: Principal | None = None,
    *,
    reviewer_id: str | None = None,
    now: datetime | None = None,
) -> Task | None:
    """M12: a new review task for a task still ``in_review`` whose earlier review task ended
    without a decision. Key ``review:<task>:<revision>:r<n>`` (idempotent per attempt).
    ``reviewer_id`` names another reviewer (the Co-founder): it is recorded on the task, since only
    the resolved reviewer may record a decision. Returns None when nobody can review it. No
    commit."""
    from app.services import audit, tasks

    if task.status != TaskStatus.IN_REVIEW:
        return None
    chosen = reviewer_id or tasks.resolve_reviewer(db, task)
    if chosen is None or chosen == task.assignee_agent_id:
        return None
    agent = db.get(Agent, chosen)
    if (
        agent is None
        or agent.company_id != task.company_id
        or agent.status in (AgentStatus.RETIRED, AgentStatus.REJECTED)
    ):
        return None
    key = f"{review_key(task)}:r{n}"
    existing = _by_key(db, task.company_id, key)
    if existing is not None:
        return existing
    if reviewer_id is not None and tasks.resolve_reviewer(db, task) != reviewer_id:
        previous = task.reviewer_agent_id
        task.reviewer_agent_id = reviewer_id
        task.reviewer_member_id = None
        task.updated_at = now or task.updated_at
        audit.record(
            db,
            company_id=task.company_id,
            principal=principal,
            action="task.reviewer_changed",
            target_type="task",
            target_id=task.id,
            summary=f"Review of '{task.title}' moved to {agent.name}",
            data={"from": previous, "to": reviewer_id, "auto": True},
        )
    extra = [
        "An earlier review task for this work ended without a decision, so the work is still "
        "waiting. Record the decision this time; do not finish without it."
    ]
    return _create_review(db, task, chosen, key, principal, now, extra)


def close_for_task(
    db: Session, task: Task, principal: Principal | None, note: str | None = None
) -> None:
    """The reviewed task left ``in_review`` (review recorded, cancelled, blocked): finish every
    open review task that targets it. No commit."""
    from app.services import runs as run_service
    from app.services import tasks

    summary = note or "The review was recorded on the reviewed task."
    for review in open_review_tasks(db, task):
        if review.status == TaskStatus.IN_PROGRESS:
            review.result_summary = summary
            tasks.transition(
                db,
                review,
                TaskStatus.DONE,
                principal=principal,
                error=None,
                reason="review recorded",
            )
            tasks.recompute_dependents(db, review, principal=principal)
        else:  # not started: nothing left to do
            tasks.transition(
                db, review, TaskStatus.CANCELLED, principal=principal, reason="review recorded"
            )
            run_service.request_cancel_for_task(db, review, "task_cancelled", principal)


# ---------------------------------------------------------------- failure analysis


def _analysis_owner(db: Session, task: Task) -> Agent | None:
    """Project lead, else the assignee's manager, else the AI Co-founder (never retired agents)."""

    def usable(agent_id: str | None) -> Agent | None:
        if not agent_id:
            return None
        agent = db.get(Agent, agent_id)
        if (
            agent is None
            or agent.company_id != task.company_id
            or agent.status != AgentStatus.ACTIVE
        ):
            return None
        return agent

    if task.project_id:
        project = db.get(Project, task.project_id)
        lead = usable(project.lead_agent_id if project else None)
        if lead is not None and lead.id != task.assignee_agent_id:
            return lead
    if task.assignee_agent_id:
        assignee = db.get(Agent, task.assignee_agent_id)
        manager = usable(assignee.manager_agent_id if assignee else None)
        if manager is not None and manager.id != task.assignee_agent_id:
            return manager
    return db.scalar(
        select(Agent).where(
            Agent.company_id == task.company_id,
            Agent.kind == AgentKind.FOUNDER,
            Agent.status == AgentStatus.ACTIVE,
        )
    )


def ensure_failure_analysis(
    db: Session, task: Task, principal: Principal | None, now: datetime | None = None
) -> Task | None:
    """Create the failure-analysis task for a task that just failed for good. No commit."""
    from app.services import tasks

    if task.kind not in FAILURE_KINDS or (task.idempotency_key or "").startswith(
        (REVIEW_PREFIX, FAILURE_PREFIX)
    ):
        return None
    owner = _analysis_owner(db, task)
    if owner is None:
        return None
    key = failure_key(db, task)
    existing = _by_key(db, task.company_id, key)
    if existing is not None:
        return existing
    err = task.error or {}
    lines = [
        f'Task "{task.title}" [task id {task.id}] assigned to {_assignee_name(db, task)} '
        f"failed for good after {task.attempt_count} attempt(s) and {task.revision_count} "
        "revision(s).",
        f"Last error: {err.get('code', 'unknown')}: {str(err.get('message', ''))[:600]}",
        "",
        "Decide what happens next, then complete this task with a one-line summary:",
        f"1. Try again (a different agent if the cause was the agent): company_update_task "
        f"action=retry task_id={task.id} (optionally assignee_agent_id).",
        "2. Split the work into smaller tasks (company_create_task); the failed task stays failed.",
        "3. Give up: record the decision (company_record_decision); the failed task stays failed.",
    ]
    data = TaskCreate(
        title=f"Failure analysis: {task.title}"[:300],
        description="\n".join(lines),
        kind=TaskKind.REVIEW.value,
        priority="high" if task.priority in ("high", "urgent") else "normal",
        project_id=task.project_id,
        assignee_agent_id=owner.id,
        input_refs=[f"{TARGET_REF}{task.id}"],
        idempotency_key=key,
    )
    analysis, created = tasks._create_task_core(
        db, task.company_id, principal, data, run=None, now=now
    )
    if created:
        from app.services import audit

        audit.record(
            db,
            company_id=task.company_id,
            principal=principal,
            action="task.failure_analysis_created",
            target_type="task",
            target_id=analysis.id,
            summary=f"Failure analysis for '{task.title}' assigned to {owner.name}",
            data={"failed_task_id": task.id, "owner_agent_id": owner.id},
        )
    return analysis


# ---------------------------------------------------------------- agent-facing brief


def review_target(
    db: Session, storage: ArtifactStorage | None, review_task: Task
) -> dict[str, Any] | None:
    """What a reviewer / failure analyst needs to see about the target task (bounded)."""
    target_id = target_id_of(review_task)
    if not target_id:
        return None
    target = db.scalar(
        select(Task).where(Task.id == target_id, Task.company_id == review_task.company_id)
    )
    if target is None:
        return None
    err = target.error or {}
    return {
        "mode": "failure"
        if (review_task.idempotency_key or "").startswith(FAILURE_PREFIX)
        else "review",
        "task_id": target.id,
        "title": target.title,
        "status": target.status,
        "assignee": _assignee_name(db, target),
        "acceptance_criteria": target.acceptance_criteria,
        "result_summary": target.result_summary,
        "revision_count": target.revision_count,
        "error": {"code": err.get("code"), "message": str(err.get("message", ""))[:600]}
        if err
        else None,
        "artifacts": task_briefs.artifact_briefs(db, storage, target.company_id, target.id),
    }
