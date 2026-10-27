"""Reactions to task state changes that keep the layered loops moving (M5). Called from
``tasks.transition``; they only add rows to the caller's transaction (no commit)."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.principal import Principal
from app.models.agent import Agent
from app.models.enums import (
    TERMINAL_TASK_STATUSES,
    AlertSeverity,
    ProjectStatus,
    TaskKind,
    TaskStatus,
)
from app.models.work import Project, Task
from app.services import alerts, wakes

S = TaskStatus
WORK_KINDS = (TaskKind.WORK, TaskKind.REVIEW, TaskKind.HIRING)
WAKE_REQUEUE_DELAY_SECONDS = 120
WAKE_REQUEUE_MAX = 2
WAKE_REQUEUE_KIND = "wake_requeue"
WAKE_HEADER = "Why you were woken:"


def on_transition(
    db: Session,
    task: Task,
    previous: TaskStatus,
    target: TaskStatus,
    principal: Principal | None,
    now: datetime | None,
) -> None:
    from app.services import reviews, tasks

    if previous == S.IN_REVIEW and target != S.IN_REVIEW:
        reviews.close_for_task(db, task, principal)
    if target == S.IN_REVIEW and task.reviewer_member_id:
        tasks._notify_review_requested(db, task, principal)  # a person is asked to review
    if target == S.BLOCKED and principal is not None and principal.is_agent:
        _wake_for_blocked(db, task, principal, now)
    if target not in TERMINAL_TASK_STATUSES:
        return
    if task.kind == TaskKind.PLANNING and (task.idempotency_key or "").startswith(
        wakes.WAKE_KEY_PREFIX
    ):
        if target == S.FAILED:
            _requeue_wake(db, task, now)
        elif target == S.DONE:
            _forget_requeues(db, task)
    if task.parent_task_id:  # a waiting parent resumes once its last subtask is closed
        tasks.recompute_readiness(
            db, task.company_id, task_ids=[task.parent_task_id], principal=principal, now=now
        )
    if (
        target == S.FAILED
        and task.kind == TaskKind.REVIEW
        and (task.idempotency_key or "").startswith(reviews.REVIEW_PREFIX)
    ):  # the review failed: its target would hang in review (watchdog D1, entered at once)
        from app.services import autonomy_watchdog

        autonomy_watchdog.review_task_failed(db, task, now)
    if target == S.FAILED:
        reviews.ensure_failure_analysis(db, task, principal, now=now)
        alerts.raise_alert(
            db,
            task.company_id,
            kind="task_failed",
            severity=AlertSeverity.WARNING,
            title=f"Task failed: {task.title}"[:300],
            body=(task.error or {}).get("message"),
            related_type="task",
            related_id=task.id,
            dedupe_key=f"task_failed:{task.id}",
            wake_founder=False,  # the failure-analysis task already hands it to someone
        )
    if task.project_id and task.kind in WORK_KINDS:
        _check_project_finished(db, task, principal)


def _wake_for_blocked(db: Session, task: Task, principal: Principal, now: datetime | None) -> None:
    """An agent blocked a task: the project lead (else the assignee's manager, else the Co-founder)
    hears about it now and can unblock, reassign or escalate. Never the agent that blocked it."""
    reason = (task.error or {}).get("message") or "no reason given"
    if reason.startswith("Escalated:"):
        return  # the escalation itself wakes the Co-founder with the full reason
    text = (
        f"Task '{task.title}' [id {task.id}] was blocked by {principal.label}: {reason}. "
        "Unblock it if the cause is gone, reassign it, or escalate."
    )

    def usable(agent_id: str | None) -> Agent | None:
        agent = db.get(Agent, agent_id) if agent_id else None
        if (
            agent is None
            or agent.company_id != task.company_id
            or agent.status != "active"
            or agent.id == principal.id
        ):
            return None
        return agent

    project = db.get(Project, task.project_id) if task.project_id else None
    target = usable(project.lead_agent_id if project else None)
    if target is None and task.assignee_agent_id:
        assignee = db.get(Agent, task.assignee_agent_id)
        target = usable(assignee.manager_agent_id if assignee else None)
    if target is not None:
        wakes.wake_agent(
            db,
            task.company_id,
            target.id,
            "blocked",
            text,
            ref_type="task",
            ref_id=task.id,
            now=now,
        )
    else:
        wakes.wake_founder(
            db,
            task.company_id,
            "blocked",
            text,
            ref_type="task",
            ref_id=task.id,
            actor_agent_id=principal.id,
            now=now,
        )


def _wake_reasons(task: Task) -> list[str]:
    """The "Why you were woken" lines of a wake task, without bullets or "(also)" markers."""
    out: list[str] = []
    in_block = False
    for line in (task.description or "").splitlines():
        if line.startswith(WAKE_HEADER):
            in_block = True
        elif in_block and line.startswith("- "):
            text = line[2:].removeprefix("(also) ").strip()
            if text:
                out.append(text)
    return out


def _requeue_wake(db: Session, task: Task, now: datetime | None) -> None:
    """A planning (wake) run failed for good: its reasons are not lost. They are queued again after
    120 s, twice at most; the third failure raises the ``autonomy_stalled`` alert."""
    from app.core.time import utcnow
    from app.services import autonomy_watchdog

    if not task.assignee_agent_id:
        return
    now = now or utcnow()
    reasons = _wake_reasons(task)
    if not reasons:
        return
    step = autonomy_watchdog._intervene(
        db,
        task.company_id,
        WAKE_REQUEUE_KIND,
        "agent",
        task.assignee_agent_id,
        spacing=timedelta(0),
        max_steps=WAKE_REQUEUE_MAX,
        now=now,
        label="planning runs keep failing",
    )
    if step is None:
        return
    for text in reasons:
        wakes.request_wake(
            db,
            task.company_id,
            task.assignee_agent_id,
            "retry",
            text,
            delay_seconds=WAKE_REQUEUE_DELAY_SECONDS,
            now=now,
        )


def _forget_requeues(db: Session, task: Task) -> None:
    """A wake run finished: the agent's retry counter starts over."""
    from app.models.autonomy import AutonomyIntervention

    if not task.assignee_agent_id:
        return
    row = db.scalar(
        select(AutonomyIntervention).where(
            AutonomyIntervention.company_id == task.company_id,
            AutonomyIntervention.kind == WAKE_REQUEUE_KIND,
            AutonomyIntervention.subject_id == task.assignee_agent_id,
        )
    )
    if row is not None:
        alerts.resolve_by_key(
            db, task.company_id, f"stall:{WAKE_REQUEUE_KIND}:{task.assignee_agent_id}"
        )
        db.delete(row)


def _check_project_finished(db: Session, task: Task, principal: Principal | None) -> None:
    """No task of an open project is open any more: tell the lead (else the AI Co-founder), whether
    the work succeeded or not (a project whose tasks all failed must not be forgotten)."""
    db.flush()
    project = db.get(Project, task.project_id)
    if project is None or project.status not in (ProjectStatus.PLANNED, ProjectStatus.ACTIVE):
        return

    def count(*conditions) -> int:
        return int(
            db.scalar(
                select(func.count(Task.id)).where(
                    Task.company_id == task.company_id,
                    Task.project_id == project.id,
                    *conditions,
                )
            )
            or 0
        )

    open_tasks = count(
        Task.kind.in_([k.value for k in WORK_KINDS]),
        Task.status.notin_([s.value for s in TERMINAL_TASK_STATUSES]),
    )
    if open_tasks:
        return
    done = count(Task.kind == TaskKind.WORK, Task.status == S.DONE)
    if done:
        text = (
            f"All work in project '{project.name}' [id {project.id}] is finished. Review the "
            "results; mark the project completed (company_manage_project) or add follow-up tasks."
        )
    else:
        failed = count(Task.kind == TaskKind.WORK, Task.status == S.FAILED)
        text = (
            f"Every task in project '{project.name}' [id {project.id}] has ended without a "
            f"finished result ({failed} failed, the rest cancelled). Decide now: retry the failed "
            "tasks (company_update_task action=retry), plan new ones, or ask the Co-founder to "
            "cancel the project."
        )
    # No self-wake exclusion here: the lead that accepted the last review still has to close the
    # project, and this fires once per "everything ended" moment, so it cannot loop.
    lead = db.get(Agent, project.lead_agent_id) if project.lead_agent_id else None
    if lead is not None and lead.status == "active":
        wakes.wake_agent(
            db,
            task.company_id,
            lead.id,
            "project_done",
            text,
            ref_type="project",
            ref_id=project.id,
        )
    else:
        wakes.wake_founder(
            db, task.company_id, "project_done", text, ref_type="project", ref_id=project.id
        )


def on_agent_activated(db: Session, agent: Agent, principal: Principal | None) -> None:
    """A new colleague is active: its manager hears about it (a hire is what the AI Co-founder
    or a lead waits for), and an operations agent gets the daily failed-run triage routine."""
    from app.services import builtin_schedules, hiring
    from app.services import projects as project_service

    builtin_schedules.ensure_ops_schedule(db, agent, principal)
    if agent.kind == "lead":  # a new lead takes the projects that wait for one
        for project in hiring.orphan_projects(db, agent.company_id):
            project_service.assign_lead(
                db, project, agent, principal, reason="the lead was just hired"
            )
    if agent.manager_agent_id and agent.kind != "founder":
        actor = principal.id if principal is not None and principal.is_agent else None
        manager = db.get(Agent, agent.manager_agent_id)
        joined = f"{agent.name} ({agent.role_title}) [id {agent.id}] just joined"
        if manager is not None and manager.kind == "founder":
            text = (
                f"{joined}. Nothing to onboard: leads are given their projects automatically. "
                "Only act if a project still has no lead and this is a lead; do not create "
                "projects or tasks about the new hire."
            )
        else:
            text = (
                f"{joined} your team. Waiting tasks that need their skills are assigned "
                "automatically; give them more work with company_create_task in your projects."
            )
        wakes.wake_agent(
            db,
            agent.company_id,
            agent.manager_agent_id,
            "hire",
            text,
            ref_type="agent",
            ref_id=agent.id,
            actor_agent_id=actor,
        )
