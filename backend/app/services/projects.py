from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, LimitExceeded
from app.core.principal import CompanyContext, require_human_admin
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.enums import GoalStatus, ProjectStatus
from app.models.work import Goal, Project
from app.schemas.work import ProjectCreate, ProjectUpdate
from app.services import agent_roles, audit, builtin_schedules, events, policy, wakes
from app.services.common import (
    creator_of,
    get_agent_or_404,
    get_goal_or_404,
    get_project_or_404,
)
from app.services.pagination import paginate

UPDATABLE = ("name", "description", "goal_id", "status", "lead_agent_id", "budget_micros")
# What a project lead (not the founder) may change on the project it leads.
LEAD_UPDATABLE = {"description", "status"}
ENDED = (ProjectStatus.COMPLETED, ProjectStatus.CANCELLED)
OPEN_FOR_GOAL = (ProjectStatus.PLANNED, ProjectStatus.ACTIVE, ProjectStatus.PAUSED)


def list_projects(
    db: Session,
    ctx: CompanyContext,
    *,
    limit: int,
    cursor: str | None,
    status: str | None = None,
    goal_id: str | None = None,
):
    stmt = select(Project).where(Project.company_id == ctx.company_id)
    if status:
        stmt = stmt.where(Project.status == status)
    if goal_id:
        stmt = stmt.where(Project.goal_id == goal_id)
    return paginate(db, stmt, Project, limit=limit, cursor=cursor, descending=False)


def get_project(db: Session, ctx: CompanyContext, project_id: str) -> Project:
    return get_project_or_404(db, ctx.company_id, project_id)


def _check_refs(db: Session, ctx: CompanyContext, goal_id, lead_agent_id) -> None:
    if goal_id:
        get_goal_or_404(db, ctx.company_id, goal_id)
    if lead_agent_id:
        get_agent_or_404(db, ctx.company_id, lead_agent_id, label="Lead agent")


def _agent_role(db: Session, ctx: CompanyContext) -> str | None:
    if not ctx.principal.is_agent:
        return None
    agent = db.get(Agent, ctx.principal.id)
    if agent is None:
        raise Forbidden("Unknown agent")
    return agent_roles.role_of(db, agent)


def _check_agent_lead(db: Session, company_id: str, lead_agent_id: str | None) -> None:
    """An agent can only make a *lead* (kind ``lead``) the lead of a project: HR, specialists and
    the AI Co-founder itself are not project leads. Without one, hire it (company_request_hire)."""
    if not lead_agent_id:
        return
    lead = db.get(Agent, lead_agent_id)
    if (
        lead is None
        or lead.company_id != company_id
        or lead.kind != "lead"
        or lead.status != "active"
    ):
        raise Conflict(
            "That agent is not an active project lead. A project lead has the 'lead' role: if "
            "there is none yet, call company_request_hire (role_hint 'project lead' or "
            "'marketing lead'); the project gets its lead when HR has hired one."
        )


def _check_agent_budget(db: Session, company_id: str, budget_micros: int | None) -> None:
    """An agent-set project budget stays inside the company's monthly budget (or, without one,
    the per-agent cap): agents never hand out unlimited money."""
    if budget_micros is None:
        return
    limits = policy.load_policy(db, company_id)
    cap = limits.monthly_budget_micros
    which = "monthly_budget_micros"
    if cap is None:
        cap, which = limits.max_agent_monthly_budget_micros, "max_agent_monthly_budget_micros"
    if budget_micros > cap:
        raise LimitExceeded(
            f"A project budget set by an agent cannot exceed {cap} micro-USD ({which})",
            details={"limit": which, "max": cap, "requested": budget_micros},
        )


def create_project(db: Session, ctx: CompanyContext, data: ProjectCreate) -> Project:
    role = _agent_role(db, ctx)
    if role is None:
        require_human_admin(ctx, "create projects")
    elif role != agent_roles.FOUNDER:
        raise Forbidden("Only the AI Co-founder creates projects")
    _check_refs(db, ctx, data.goal_id, data.lead_agent_id)
    if role is not None:
        _check_agent_budget(db, ctx.company_id, data.budget_micros)
        _check_agent_lead(db, ctx.company_id, data.lead_agent_id)
    ctype, cid = creator_of(ctx.principal)
    project = Project(
        company_id=ctx.company_id,
        goal_id=data.goal_id,
        name=data.name,
        description=data.description,
        status=data.status,
        lead_agent_id=data.lead_agent_id,
        budget_micros=data.budget_micros,
        created_by_type=ctype,
        created_by_id=cid,
    )
    db.add(project)
    db.flush()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="project.created",
        target_type="project",
        target_id=project.id,
        summary=f"Created project {project.name}",
        data={"status": project.status},
    )
    events.emit(
        db, ctx.company_id, "project.created", {"project_id": project.id, "status": project.status}
    )
    _after_lead_change(db, ctx, project, lead_changed=bool(project.lead_agent_id))
    db.commit()
    return project


def _after_lead_change(
    db: Session, ctx: CompanyContext, project: Project, *, lead_changed: bool
) -> None:
    builtin_schedules.sync_project_health_check(db, project, ctx.principal)
    if project.lead_agent_id:
        from app.services import hiring

        hiring.cancel_lead_hiring(db, project, ctx.principal)
    if lead_changed and project.lead_agent_id and project.status not in ENDED:
        wakes.wake_agent(
            db,
            ctx.company_id,
            project.lead_agent_id,
            "assignment",
            f"You now lead project '{project.name}' [id {project.id}]: "
            + (project.description or "break it into tasks with acceptance criteria")[:200],
            ref_type="project",
            ref_id=project.id,
            actor_agent_id=ctx.principal.id if ctx.principal.is_agent else None,
        )


def update_project(
    db: Session, ctx: CompanyContext, project_id: str, data: ProjectUpdate
) -> Project:
    role = _agent_role(db, ctx)
    project = get_project_or_404(db, ctx.company_id, project_id)
    fields = data.model_fields_set
    if role is None:
        require_human_admin(ctx, "update projects")
    elif role == agent_roles.FOUNDER:
        pass
    elif project.lead_agent_id == ctx.principal.id:
        extra = {f for f in fields if f in UPDATABLE} - LEAD_UPDATABLE
        if extra:
            raise Forbidden(
                f"A project lead can only change {', '.join(sorted(LEAD_UPDATABLE))} "
                f"(not {', '.join(sorted(extra))}); escalate to the AI Co-founder"
            )
    else:
        raise Forbidden("Only the AI Co-founder or the project's lead can update a project")
    _check_refs(
        db,
        ctx,
        data.goal_id if "goal_id" in fields else None,
        data.lead_agent_id if "lead_agent_id" in fields else None,
    )
    if role is not None and "budget_micros" in fields:
        _check_agent_budget(db, ctx.company_id, data.budget_micros)
    if role is not None and "lead_agent_id" in fields:
        _check_agent_lead(db, ctx.company_id, data.lead_agent_id)
    changed: list[str] = []
    previous_lead = project.lead_agent_id
    previous_status = project.status
    for field in UPDATABLE:
        if field in fields and getattr(project, field) != getattr(data, field):
            setattr(project, field, getattr(data, field))
            changed.append(field)
    if changed:
        project.updated_at = utcnow()
        audit.record(
            db,
            company_id=ctx.company_id,
            principal=ctx.principal,
            action="project.updated",
            target_type="project",
            target_id=project.id,
            summary=f"Updated project {project.name}: {', '.join(changed)}",
            data={"changed": changed},
        )
        events.emit(
            db, ctx.company_id, "project.updated", {"project_id": project.id, "changed": changed}
        )
        if "budget_micros" in changed:
            from app.services import tasks

            tasks.release_budget_blocked(db, ctx.company_id, ctx.principal)
        lead_changed = "lead_agent_id" in changed and project.lead_agent_id != previous_lead
        if lead_changed or "status" in changed or "name" in changed:
            _after_lead_change(db, ctx, project, lead_changed=lead_changed)
        if "status" in changed and project.status != previous_status:
            actor = ctx.principal.id if ctx.principal.is_agent else None
            if project.status in (*ENDED, ProjectStatus.PAUSED):
                wakes.wake_founder(
                    db,
                    ctx.company_id,
                    "project",
                    f"Project '{project.name}' is now {project.status}.",
                    ref_type="project",
                    ref_id=project.id,
                    actor_agent_id=actor,
                )
            if project.status in ENDED:
                wake_for_goal_review(db, project, actor_agent_id=actor)
        db.commit()
    return project


def assign_lead(db: Session, project: Project, lead: Agent, principal, *, reason: str) -> None:
    """The platform (not an agent) gives an open project its lead: the lead is woken, its health
    check is scheduled. No commit."""
    project.lead_agent_id = lead.id
    project.updated_at = utcnow()
    audit.record(
        db,
        company_id=project.company_id,
        principal=principal,
        action="project.updated",
        target_type="project",
        target_id=project.id,
        summary=f"Project {project.name} now led by {lead.name}: {reason}",
        data={"changed": ["lead_agent_id"], "auto": True},
    )
    events.emit(
        db,
        project.company_id,
        "project.updated",
        {"project_id": project.id, "changed": ["lead_agent_id"]},
    )
    builtin_schedules.sync_project_health_check(db, project, principal)
    from app.services import hiring

    hiring.cancel_lead_hiring(db, project, principal)
    wakes.wake_agent(
        db,
        project.company_id,
        lead.id,
        "assignment",
        f"You now lead project '{project.name}' [id {project.id}]: "
        + (project.description or "break it into tasks with acceptance criteria")[:200],
        ref_type="project",
        ref_id=project.id,
    )


def wake_for_goal_review(
    db: Session,
    project: Project,
    *,
    actor_agent_id: str | None = None,
    now: datetime | None = None,
) -> bool:
    """M12: a project ended. When its goal is live and has no other open project, the Co-founder
    gets a goal-specific wake: mark the goal achieved / abandoned or create the next project. No
    commit. Returns whether a wake was requested."""
    if not project.goal_id:
        return False
    goal = db.scalar(
        select(Goal).where(Goal.id == project.goal_id, Goal.company_id == project.company_id)
    )
    if goal is None or goal.status not in (GoalStatus.PROPOSED, GoalStatus.ACTIVE):
        return False
    db.flush()
    still_open = db.scalar(
        select(func.count(Project.id)).where(
            Project.company_id == project.company_id,
            Project.goal_id == goal.id,
            Project.id != project.id,
            Project.status.in_([s.value for s in OPEN_FOR_GOAL]),
        )
    )
    if still_open:
        return False
    request = wakes.wake_founder(
        db,
        project.company_id,
        "goal_review",
        f"Goal '{goal.title}' [id {goal.id}] has no open projects (project '{project.name}' is "
        f"{project.status}). Mark the goal achieved or abandoned (company_manage_goal) if it is "
        "settled, or create the next project for it (company_manage_project with goal_id).",
        ref_type="goal",
        ref_id=goal.id,
        actor_agent_id=actor_agent_id,
        now=now,
    )
    return request is not None


def complete_by_system(
    db: Session, project: Project, *, reason: str, now: datetime | None = None
) -> bool:
    """M12: the platform (not an agent) completes an open project whose work is finished, when the
    company's ``project_auto_complete`` policy allows it (the caller checks). Audited as
    ``project.auto_completed``; the Co-founder hears about it and about the goal. No commit."""
    if project.status not in (ProjectStatus.PLANNED, ProjectStatus.ACTIVE):
        return False
    now = now or utcnow()
    project.status = ProjectStatus.COMPLETED
    project.updated_at = now
    audit.record(
        db,
        company_id=project.company_id,
        principal=None,
        action="project.auto_completed",
        target_type="project",
        target_id=project.id,
        summary=f"Project {project.name} completed automatically: {reason}",
        data={"reason": reason, "auto": True},
    )
    events.emit(
        db,
        project.company_id,
        "project.updated",
        {"project_id": project.id, "changed": ["status"]},
    )
    builtin_schedules.sync_project_health_check(db, project, None)
    wakes.wake_founder(
        db,
        project.company_id,
        "project",
        f"Project '{project.name}' [id {project.id}] was completed automatically: {reason}.",
        ref_type="project",
        ref_id=project.id,
        now=now,
    )
    wake_for_goal_review(db, project, now=now)
    return True


def clear_lead(db: Session, project: Project, *, reason: str, now: datetime | None = None) -> None:
    """M12: the platform removes a lead that can no longer lead (retired). The lead-hiring safety
    net (``hiring.resolve_project_leads``) then gives the project a new lead. No commit."""
    previous = project.lead_agent_id
    project.lead_agent_id = None
    project.updated_at = now or utcnow()
    audit.record(
        db,
        company_id=project.company_id,
        principal=None,
        action="project.updated",
        target_type="project",
        target_id=project.id,
        summary=f"Project {project.name} lost its lead: {reason}",
        data={"changed": ["lead_agent_id"], "auto": True, "previous_lead_agent_id": previous},
    )
    events.emit(
        db,
        project.company_id,
        "project.updated",
        {"project_id": project.id, "changed": ["lead_agent_id"]},
    )
    builtin_schedules.sync_project_health_check(db, project, None)
