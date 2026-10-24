"""Reactions to task state changes that keep the layered loops moving (M5). Called from
``tasks.transition``; they only add rows to the caller's transaction (no commit)."""

from __future__ import annotations

from datetime import datetime

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
    if target not in TERMINAL_TASK_STATUSES:
        return
    if task.parent_task_id:  # a waiting parent resumes once its last subtask is closed
        tasks.recompute_readiness(
            db, task.company_id, task_ids=[task.parent_task_id], principal=principal, now=now
        )
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


def _check_project_finished(db: Session, task: Task, principal: Principal | None) -> None:
    """Every task of an open project is closed: tell the lead (else the AI Co-founder)."""
    db.flush()
    project = db.get(Project, task.project_id)
    if project is None or project.status not in (ProjectStatus.PLANNED, ProjectStatus.ACTIVE):
        return
    open_tasks = db.scalar(
        select(func.count(Task.id)).where(
            Task.company_id == task.company_id,
            Task.project_id == project.id,
            Task.kind.in_([k.value for k in WORK_KINDS]),
            Task.status.notin_([s.value for s in TERMINAL_TASK_STATUSES]),
        )
    )
    done = db.scalar(
        select(func.count(Task.id)).where(
            Task.company_id == task.company_id,
            Task.project_id == project.id,
            Task.kind == TaskKind.WORK,
            Task.status == S.DONE,
        )
    )
    if open_tasks or not done:
        return
    text = (
        f"All work in project '{project.name}' [id {project.id}] is finished. Review the results; "
        "mark the project completed (company_manage_project) or add follow-up tasks."
    )
    # No self-wake exclusion here: the lead that accepted the last review still has to close the
    # project, and this fires once per "everything finished" moment, so it cannot loop.
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
