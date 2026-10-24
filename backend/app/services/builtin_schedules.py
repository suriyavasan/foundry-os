"""Built-in schedules of an autonomous company (spec M5 section 6), on the existing
Schedule/ScheduleFire machinery. Created with the company (and by migration 0005 for older ones):

* AI Co-founder: ``heartbeat`` (30 min), ``daily-standup`` (09:00), ``weekly-strategy-review``
  (Mon 09:30), ``daily-budget-review`` (18:00), all in the company timezone;
* HR: ``skills-review`` (monthly);
* a project lead: ``project-health-check`` every 6 h, one schedule per project that has a lead;
* operations: ``failed-run-triage`` (daily), only once an operations agent exists.

Schedules marked ``skip_when_idle`` do not fire (and cost no model run) while the company has
nothing to review. The schedules are ordinary rows: the Founder can pause or edit them.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.principal import Principal
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.company import Company
from app.models.enums import (
    AgentKind,
    AgentStatus,
    ProjectStatus,
    RoleFamily,
    ScheduleKind,
)
from app.models.schedule import Schedule
from app.models.work import Project
from app.services import audit, events
from app.services.schedules import compute_next

HEARTBEAT_NAME = "heartbeat"
HEARTBEAT_SECONDS = 1800
PROJECT_CHECK_SECONDS = 6 * 3600

STATE_HINT = "Call company_get_state first. "

COFOUNDER_SCHEDULES: list[dict[str, Any]] = [
    {
        "name": HEARTBEAT_NAME,
        "kind": ScheduleKind.HEARTBEAT.value,
        "interval_seconds": HEARTBEAT_SECONDS,
        "cron": None,
        "template": {
            "title": "Heartbeat: review the company",
            "description": STATE_HINT
            + "Handle what needs you: unprocessed Founder's notes, failed or blocked work, "
            "pending approvals, alerts, projects without a lead. If nothing needs action, "
            "complete this task and say so.",
            "kind": "planning",
            "skip_when_idle": True,
        },
    },
    {
        "name": "daily-standup",
        "kind": ScheduleKind.ROUTINE.value,
        "interval_seconds": None,
        "cron": "0 9 * * *",
        "template": {
            "title": "Daily standup",
            "description": STATE_HINT
            + "Write a short standup (progress, blockers, next steps) with "
            "company_submit_artifact (title 'Standup', kind report), then complete this task.",
            "skip_when_idle": True,
        },
    },
    {
        "name": "weekly-strategy-review",
        "kind": ScheduleKind.ROUTINE.value,
        "interval_seconds": None,
        "cron": "30 9 * * 1",
        "template": {
            "title": "Weekly strategy review",
            "description": STATE_HINT
            + "Compare goals with progress. Adjust goals and projects, fix ownership, plan hires, "
            "record the important decisions with company_record_decision, then complete this task.",
            "skip_when_idle": True,
        },
    },
    {
        "name": "daily-budget-review",
        "kind": ScheduleKind.ROUTINE.value,
        "interval_seconds": None,
        "cron": "0 18 * * *",
        "template": {
            "title": "Daily budget review",
            "description": STATE_HINT
            + "Check spend against the budgets in the state. You cannot raise budgets; if spend is "
            "a problem, record a decision and adjust the plan. Then complete this task.",
            "skip_when_idle": True,
        },
    },
]

HR_SCHEDULE: dict[str, Any] = {
    "name": "skills-review",
    "kind": ScheduleKind.ROUTINE.value,
    "interval_seconds": None,
    "cron": "0 10 1 * *",
    "template": {
        "title": "Monthly skills review",
        "description": "Review the skill library and the team: skills nobody uses, quarantined "
        "skills, recurring gaps. Install or author what is missing, then complete this task with a "
        "short summary.",
        "skip_when_idle": True,
    },
}

OPS_SCHEDULE: dict[str, Any] = {
    "name": "failed-run-triage",
    "kind": ScheduleKind.ROUTINE.value,
    "interval_seconds": None,
    "cron": "0 8 * * *",
    "template": {
        "title": "Failed-run triage",
        "description": STATE_HINT
        + "Review the failures of the last 24 h: group them by cause, say what to change, write "
        "the findings as a short report artifact and complete this task.",
        "skip_when_idle": True,
    },
}


def project_check_name(project: Project) -> str:
    return f"project-health-check: {project.name}"[:200]


def _project_template(project: Project) -> dict[str, Any]:
    return {
        "title": f"Project health check: {project.name}"[:300],
        "description": STATE_HINT
        + f"Check project '{project.name}' [id {project.id}]: stuck or failed tasks, missing "
        "owners, finished work to review. Fix what you can (create, reassign or retry tasks); "
        "escalate with company_escalate what you cannot. If all its work is finished, mark the "
        "project completed with company_manage_project. Then complete this task.",
        "project_id": project.id,
        "skip_when_idle": True,
    }


def _exists(db: Session, company_id: str, agent_id: str, name: str) -> Schedule | None:
    return db.scalar(
        select(Schedule).where(
            Schedule.company_id == company_id,
            Schedule.agent_id == agent_id,
            Schedule.name == name,
        )
    )


def _create(
    db: Session,
    company: Company,
    agent: Agent,
    spec: dict[str, Any],
    principal: Principal | None,
    *,
    name: str | None = None,
    template: dict[str, Any] | None = None,
    audit_it: bool = True,
) -> Schedule | None:
    name = name or spec["name"]
    if _exists(db, company.id, agent.id, name) is not None:
        return None
    now = utcnow()
    schedule = Schedule(
        company_id=company.id,
        name=name,
        kind=spec["kind"],
        agent_id=agent.id,
        interval_seconds=spec["interval_seconds"],
        cron=spec["cron"],
        timezone=company.timezone,
        task_template=dict(template if template is not None else spec["template"]),
        enabled=True,
        next_run_at=compute_next(
            interval_seconds=spec["interval_seconds"],
            cron=spec["cron"],
            timezone=company.timezone,
            after=now,
        ),
    )
    db.add(schedule)
    db.flush()
    if audit_it:
        audit.record(
            db,
            company_id=company.id,
            principal=principal,
            action="schedule.created",
            target_type="schedule",
            target_id=schedule.id,
            summary=f"Created built-in schedule '{schedule.name}' for {agent.name}",
            data={"agent_id": agent.id, "built_in": True, "enabled": True},
        )
    events.emit(db, company.id, "schedule.created", {"schedule_id": schedule.id})
    return schedule


def ensure_company_builtins(db: Session, company_id: str, principal: Principal | None) -> int:
    """Co-founder and HR schedules (idempotent). No commit. Returns the number created."""
    company = db.get(Company, company_id)
    if company is None:
        return 0
    founder = db.scalar(
        select(Agent).where(
            Agent.company_id == company_id,
            Agent.kind == AgentKind.FOUNDER,
            Agent.status.in_([AgentStatus.ACTIVE, AgentStatus.PAUSED]),
        )
    )
    hr = db.scalar(
        select(Agent)
        .where(
            Agent.company_id == company_id,
            Agent.role_family == RoleFamily.HR,
            Agent.status.in_([AgentStatus.ACTIVE, AgentStatus.PAUSED]),
        )
        .order_by(Agent.created_at)
    )
    names: list[str] = []
    if founder is not None:
        for spec in COFOUNDER_SCHEDULES:
            if _create(db, company, founder, spec, principal, audit_it=False) is not None:
                names.append(spec["name"])
    if hr is not None and _create(db, company, hr, HR_SCHEDULE, principal, audit_it=False):
        names.append(HR_SCHEDULE["name"])
    if names:  # one audit line for the whole set keeps the activity feed readable
        audit.record(
            db,
            company_id=company_id,
            principal=principal,
            action="schedule.builtins_created",
            target_type="schedule",
            target_id=None,
            summary=f"Created {len(names)} built-in schedules: {', '.join(names)}",
            data={"names": names},
        )
    return len(names)


def ensure_ops_schedule(db: Session, agent: Agent, principal: Principal | None) -> None:
    """Called when an agent becomes active: an operations agent gets ``failed-run-triage``."""
    if agent.role_family != RoleFamily.OPERATIONS or agent.status != AgentStatus.ACTIVE:
        return
    company = db.get(Company, agent.company_id)
    if company is not None:
        _create(db, company, agent, OPS_SCHEDULE, principal)


def sync_project_health_check(db: Session, project: Project, principal: Principal | None) -> None:
    """One ``project-health-check`` schedule per open project that has a lead; it follows the
    lead and is disabled when the project ends or loses its lead. No commit."""
    name = project_check_name(project)
    existing = None
    for row in db.scalars(
        select(Schedule).where(
            Schedule.company_id == project.company_id,
            Schedule.name.like("project-health-check:%"),
        )
    ):
        if (row.task_template or {}).get("project_id") == project.id:
            existing = row
            break
    open_project = project.status in (ProjectStatus.PLANNED, ProjectStatus.ACTIVE)
    lead = db.get(Agent, project.lead_agent_id) if project.lead_agent_id and open_project else None
    if (
        lead is None
        or lead.company_id != project.company_id
        or lead.status
        in (
            AgentStatus.RETIRED,
            AgentStatus.REJECTED,
        )
    ):
        if existing is not None and existing.enabled:
            existing.enabled = False
            existing.next_run_at = None
            existing.updated_at = utcnow()
            events.emit(db, project.company_id, "schedule.updated", {"schedule_id": existing.id})
        return
    company = db.get(Company, project.company_id)
    assert company is not None
    spec = {
        "kind": ScheduleKind.ROUTINE.value,
        "interval_seconds": PROJECT_CHECK_SECONDS,
        "cron": None,
    }
    if existing is None:
        _create(db, company, lead, spec, principal, name=name, template=_project_template(project))
        return
    changed = False
    now = utcnow()
    if existing.agent_id != lead.id:
        existing.agent_id = lead.id
        changed = True
    if existing.name != name:
        existing.name = name
        changed = True
    if not existing.enabled:
        existing.enabled = True
        existing.next_run_at = compute_next(
            interval_seconds=PROJECT_CHECK_SECONDS, cron=None, timezone=existing.timezone, after=now
        )
        changed = True
    if changed:
        existing.task_template = _project_template(project)
        existing.updated_at = now
        events.emit(db, project.company_id, "schedule.updated", {"schedule_id": existing.id})


def sync_heartbeat_interval(
    db: Session, company_id: str, interval_seconds: int, principal: Principal | None
) -> None:
    """The company policy's ``heartbeat_interval_seconds`` drives the built-in heartbeat."""
    for schedule in db.scalars(
        select(Schedule).where(
            Schedule.company_id == company_id,
            Schedule.name == HEARTBEAT_NAME,
            Schedule.kind == ScheduleKind.HEARTBEAT,
        )
    ):
        if schedule.interval_seconds == interval_seconds:
            continue
        schedule.interval_seconds = interval_seconds
        schedule.cron = None
        schedule.updated_at = utcnow()
        if schedule.enabled:
            schedule.next_run_at = compute_next(
                interval_seconds=interval_seconds,
                cron=None,
                timezone=schedule.timezone,
                after=utcnow(),
            )
        events.emit(db, company_id, "schedule.updated", {"schedule_id": schedule.id})
