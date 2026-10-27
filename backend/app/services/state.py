"""``GET /api/agent/v1/state``: the live state of the company, sliced by the caller's role.

* the **AI Co-founder** sees everything (goals, projects, tasks, agents, budget, approvals,
  hiring requests, decisions, schedules, alerts, unprocessed notes);
* a **lead** sees its projects, the tasks in them, its reports and what it requested;
* **HR** sees headcount, the team's skills and the hiring requests;
* everyone else sees their own tasks, their project and their manager.

Every list is capped and every text clipped: a 7B-class model reads this in one tool call. The
projects list shows open projects first (newest first), then projects that ended in the last
7 days, at most 25, so a new project is never hidden behind old ones. When the run's model is
local or has a context under 32K the state uses a **compact profile** (fewer tasks and agents,
shorter clips); cloud runs get the complete but bounded state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models.agent import Agent
from app.models.autonomy import MemoryEntry
from app.models.company import Company
from app.models.enums import (
    LIVE_AGENT_STATUSES,
    TERMINAL_TASK_STATUSES,
    ApprovalStatus,
    GoalStatus,
    MemoryStatus,
    ProjectStatus,
    TaskKind,
    TaskStatus,
)
from app.models.governance import Approval, Decision
from app.models.provider import ModelProvider
from app.models.repository import ProjectRepository
from app.models.schedule import Schedule
from app.models.work import Goal, Project, Task
from app.schemas.autonomy import (
    AgentState,
    StateAgent,
    StateApproval,
    StateBudget,
    StateGoal,
    StateMemory,
    StateNotes,
    StateProject,
    StateRepository,
    StateSchedule,
    StateStandard,
    StateTask,
)
from app.services import (
    agent_roles,
    alerts,
    memory,
    notes,
    overview,
    policy,
    reflections,
    skills,
    standards,
)
from app.services.agent_auth import AgentRunContext

OPEN = [s for s in TaskStatus if s not in TERMINAL_TASK_STATUSES]
MAX_TASKS = 40
MAX_AGENTS = 30
MAX_PROJECTS = 25
MAX_GOALS = 20
ENDED_PROJECT_DAYS = 7
OPEN_PROJECT_STATUSES = (ProjectStatus.PLANNED, ProjectStatus.ACTIVE, ProjectStatus.PAUSED)
ENDED_PROJECT_STATUSES = (ProjectStatus.COMPLETED, ProjectStatus.CANCELLED)
SMALL_CONTEXT = 32_768


@dataclass(frozen=True)
class Profile:
    """How much of the state one run can read."""

    name: str
    tasks: int
    agents: int
    clip: int  # a hard ceiling for every text field


FULL = Profile("full", MAX_TASKS, MAX_AGENTS, 240)
COMPACT = Profile("compact", 15, 12, 120)


def profile_for(db: Session, rc: AgentRunContext) -> Profile:
    """Compact for a local model or one with a context under 32K, else full. The run's routed
    provider decides; a run without one (direct runtime) falls back to the agent's own model."""
    provider_id = rc.run.model_provider_id or rc.agent.model_provider
    if not provider_id:
        provider_id = policy.load_policy(db, rc.ctx.company_id).default_model_provider_id
    provider = (
        db.scalar(
            select(ModelProvider).where(
                ModelProvider.id == provider_id, ModelProvider.company_id == rc.ctx.company_id
            )
        )
        if provider_id
        else None
    )
    if provider is not None and (provider.is_local or provider.context_length < SMALL_CONTEXT):
        return COMPACT
    return FULL


def _clip(text: str | None, n: int = 240) -> str | None:
    if not text:
        return None
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 3] + "..."


def _task_row(t: Task, names: dict[str, str], clip: int = 200) -> StateTask:
    err = t.error or {}
    return StateTask(
        id=t.id,
        title=t.title,
        status=t.status,
        kind=t.kind,
        priority=t.priority,
        assignee_agent_id=t.assignee_agent_id,
        assignee_name=names.get(t.assignee_agent_id or ""),
        project_id=t.project_id,
        parent_task_id=t.parent_task_id,
        blocked_reason=t.blocked_reason,
        error=_clip(f"{err.get('code')}: {err.get('message')}", clip) if err else None,
        result_summary=_clip(t.result_summary, clip),
    )


def _repository_row(binding: ProjectRepository | None) -> StateRepository | None:
    if binding is None:
        return None
    return StateRepository(
        full_name=binding.full_name,
        merge_mode=str((binding.policy or {}).get("merge_mode") or "pr_human_merge"),
        status=binding.status,
    )


def _status_counts(db: Session, company_id: str, scope) -> dict[str, int]:
    stmt = select(Task.status, func.count(Task.id)).where(Task.company_id == company_id)
    if scope is not None:
        stmt = stmt.where(scope)
    counts = dict.fromkeys((s.value for s in TaskStatus), 0)
    for status, n in db.execute(stmt.group_by(Task.status)):
        counts[status] = int(n)
    return counts


def build_state(db: Session, rc: AgentRunContext) -> AgentState:
    ctx, agent = rc.ctx, rc.agent
    company_id = ctx.company_id
    role = agent_roles.role_of(db, agent)
    now = utcnow()
    company = db.get(Company, company_id)
    assert company is not None
    limits = policy.load_policy(db, company_id)
    founder = role == agent_roles.FOUNDER
    prof = profile_for(db, rc)
    task_clip = min(200, prof.clip)

    def clip(text: str | None, n: int = 240) -> str | None:
        return _clip(text, min(n, prof.clip))

    live_agents = list(
        db.scalars(
            select(Agent)
            .where(Agent.company_id == company_id, Agent.status.in_(LIVE_AGENT_STATUSES))
            .order_by(Agent.created_at, Agent.id)
        )
    )
    names = {a.id: a.name for a in live_agents}
    reports = [a for a in live_agents if a.manager_agent_id == agent.id]

    led_ids: list[str] = []
    if role == agent_roles.LEAD:
        led_ids = list(
            db.scalars(
                select(Project.id).where(
                    Project.company_id == company_id, Project.lead_agent_id == agent.id
                )
            )
        )
    scope = None
    if role == agent_roles.LEAD:
        scope = or_(
            Task.project_id.in_(led_ids) if led_ids else Task.id.is_(None),
            Task.assignee_agent_id == agent.id,
            Task.created_by_id == agent.id,
        )
    elif role == agent_roles.HR:
        scope = or_(Task.kind == TaskKind.HIRING, Task.assignee_agent_id == agent.id)
    elif role != agent_roles.FOUNDER:
        mine = [agent.id, *[r.id for r in reports]]
        scope = or_(Task.assignee_agent_id.in_(mine), Task.created_by_id == agent.id)

    # ---- goals & projects
    goal_stmt = select(Goal).where(
        Goal.company_id == company_id, Goal.status.in_([GoalStatus.PROPOSED, GoalStatus.ACTIVE])
    )
    project_stmt = select(Project).where(Project.company_id == company_id)
    if role == agent_roles.LEAD:
        project_stmt = project_stmt.where(Project.id.in_(led_ids or [""]))
    elif not founder and role != agent_roles.HR:
        own_project = rc.task.project_id
        project_stmt = project_stmt.where(Project.id == (own_project or ""))
    elif role == agent_roles.HR:
        project_stmt = project_stmt.where(Project.id == "")
    # Open projects first (newest first), then those that ended in the last 7 days.
    projects = list(
        db.scalars(
            project_stmt.where(Project.status.in_(OPEN_PROJECT_STATUSES))
            .order_by(Project.created_at.desc(), Project.id)
            .limit(MAX_PROJECTS)
        )
    )
    if len(projects) < MAX_PROJECTS:
        projects += list(
            db.scalars(
                project_stmt.where(
                    Project.status.in_(ENDED_PROJECT_STATUSES),
                    Project.updated_at >= now - timedelta(days=ENDED_PROJECT_DAYS),
                )
                .order_by(Project.updated_at.desc(), Project.id)
                .limit(MAX_PROJECTS - len(projects))
            )
        )
    if founder:
        goals = list(db.scalars(goal_stmt.order_by(Goal.created_at).limit(MAX_GOALS)))
    else:
        goal_ids = {p.goal_id for p in projects if p.goal_id}
        goals = (
            list(db.scalars(goal_stmt.where(Goal.id.in_(goal_ids)).order_by(Goal.created_at)))
            if goal_ids
            else []
        )
    goal_projects: dict[str, dict[str, int]] = {}
    if goals:
        ended = {s.value for s in ENDED_PROJECT_STATUSES}
        for gid, status, n in db.execute(
            select(Project.goal_id, Project.status, func.count(Project.id))
            .where(Project.company_id == company_id, Project.goal_id.in_([g.id for g in goals]))
            .group_by(Project.goal_id, Project.status)
        ):
            counts = goal_projects.setdefault(gid, {"open": 0, "ended": 0})
            counts["ended" if status in ended else "open"] += int(n)
    project_counts: dict[str, dict[str, int]] = {}
    if projects:
        for pid, status, n in db.execute(
            select(Task.project_id, Task.status, func.count(Task.id))
            .where(
                Task.company_id == company_id,
                Task.project_id.in_([p.id for p in projects]),
                Task.kind.in_([TaskKind.WORK, TaskKind.REVIEW, TaskKind.HIRING]),
            )
            .group_by(Task.project_id, Task.status)
        ):
            project_counts.setdefault(pid, {})[status] = int(n)
    repositories: dict[str, ProjectRepository] = {}
    if projects:  # M14: the repository a project is backed by (full name, merge mode, status)
        repositories = {
            r.project_id: r
            for r in db.scalars(
                select(ProjectRepository).where(
                    ProjectRepository.company_id == company_id,
                    ProjectRepository.project_id.in_([p.id for p in projects]),
                )
            )
        }
    project_rows = []
    for p in projects:
        c = project_counts.get(p.id, {})
        total = sum(c.values())
        done = c.get("done", 0)
        project_rows.append(
            StateProject(
                id=p.id,
                name=p.name,
                status=p.status,
                goal_id=p.goal_id,
                lead_agent_id=p.lead_agent_id,
                lead_name=names.get(p.lead_agent_id or ""),
                budget_micros=p.budget_micros,
                description=clip(p.description, 300),
                tasks_total=total,
                tasks_done=done,
                tasks_open=sum(n for s, n in c.items() if s in {x.value for x in OPEN}),
                repository=_repository_row(repositories.get(p.id)),
            )
        )

    # ---- tasks
    base = select(Task).where(Task.company_id == company_id)
    if scope is not None:
        base = base.where(scope)
    visible_kinds = base.where(
        or_(
            Task.kind.in_([TaskKind.WORK, TaskKind.REVIEW, TaskKind.HIRING]),
            Task.assignee_agent_id == agent.id,
        )
    )
    open_rows = list(
        db.scalars(
            visible_kinds.where(Task.status.in_(OPEN))
            .order_by(Task.updated_at.desc(), Task.id)
            .limit(prof.tasks)
        )
    )
    failures = list(
        db.scalars(
            visible_kinds.where(
                Task.status == TaskStatus.FAILED, Task.updated_at >= now - timedelta(hours=24)
            )
            .order_by(Task.updated_at.desc())
            .limit(10)
        )
    )
    unassigned = [
        t
        for t in open_rows
        if t.assignee_agent_id is None and t.assignee_member_id is None and t.kind == TaskKind.WORK
    ]
    blocked = [t for t in open_rows if t.status == TaskStatus.BLOCKED or t.blocked_reason]
    hiring_rows = list(
        db.scalars(
            select(Task)
            .where(
                Task.company_id == company_id,
                Task.kind == TaskKind.HIRING,
                Task.status.in_(OPEN),
                *([] if founder or role == agent_roles.HR else [Task.created_by_id == agent.id]),
            )
            .order_by(Task.created_at)
            .limit(10)
        )
    )
    tasks_block: dict[str, Any] = {
        "by_status": _status_counts(db, company_id, scope),
        "open": [_task_row(t, names, task_clip).model_dump() for t in open_rows],
        "blocked": [_task_row(t, names, task_clip).model_dump() for t in blocked[:10]],
        "unassigned": [_task_row(t, names, task_clip).model_dump() for t in unassigned[:10]],
    }

    # ---- people
    families: dict[str, int] = {}
    for a in live_agents:
        families[a.role_family] = families.get(a.role_family, 0) + 1
    load = (
        dict(
            db.execute(
                select(Task.assignee_agent_id, func.count(Task.id))
                .where(
                    Task.company_id == company_id,
                    Task.assignee_agent_id.in_(list(names)),
                    Task.status.in_(OPEN),
                )
                .group_by(Task.assignee_agent_id)
            ).all()
        )
        if names
        else {}
    )
    if founder or role == agent_roles.HR:
        shown = [a for a in live_agents if a.id != agent.id][: prof.agents]
    elif role == agent_roles.LEAD:
        shown = [
            a
            for a in live_agents
            if a.manager_agent_id == agent.id or a.id == agent.manager_agent_id
        ]
    else:
        shown = [
            a
            for a in live_agents
            if a.id == agent.manager_agent_id or a.manager_agent_id == agent.id
        ]
    caps = skills.capability_map(db, company_id, [a.id for a in shown])
    agent_rows = [
        StateAgent(
            id=a.id,
            name=a.name,
            role_title=a.role_title,
            role_family=a.role_family,
            kind=a.kind,
            status=a.status,
            manager_agent_id=a.manager_agent_id,
            open_tasks=int(load.get(a.id, 0)),
            skills=sorted(caps.get(a.id, (set(), set()))[0])[:8],
        )
        for a in shown
    ]

    # ---- approvals, budget, decisions, schedules, alerts, notes
    approval_stmt = select(Approval).where(
        Approval.company_id == company_id, Approval.status == ApprovalStatus.PENDING
    )
    if not founder:
        approval_stmt = approval_stmt.where(Approval.requested_by_agent_id == agent.id)
    approvals_rows = [
        StateApproval(
            id=a.id,
            kind=a.kind,
            title=a.title,
            approver=a.approver,
            requested_by_agent_id=a.requested_by_agent_id,
        )
        for a in db.scalars(approval_stmt.order_by(Approval.created_at).limit(10))
    ]
    budget = None
    if founder:
        spend = overview._spending_block(db, ctx, now)
        month_total = spend.month.spent_micros + spend.month.held_micros
        budget = StateBudget(
            month_budget_micros=spend.month.budget_micros,
            month_spent_micros=spend.month.spent_micros,
            month_held_micros=spend.month.held_micros,
            day_budget_micros=spend.today.budget_micros,
            day_spent_micros=spend.today.spent_micros,
            month_utilisation_pct=(
                round(100 * month_total / spend.month.budget_micros, 1)
                if spend.month.budget_micros
                else None
            ),
            max_agent_monthly_budget_micros=limits.max_agent_monthly_budget_micros,
            cost_note="Local models cost $0 (verified). Cloud costs may be estimates; you "
            "cannot raise budgets.",
        )
    decision_stmt = select(Decision).where(Decision.company_id == company_id)
    if not founder:
        decision_stmt = decision_stmt.where(Decision.made_by_id == agent.id)
    decisions = [
        {
            "id": d.id,
            "title": d.title,
            "rationale": clip(d.rationale, 200),
            "made_by": names.get(d.made_by_id or "", d.made_by_type),
            "created_at": d.created_at.isoformat().replace("+00:00", "Z"),
        }
        for d in db.scalars(decision_stmt.order_by(Decision.created_at.desc()).limit(8))
    ]
    schedule_stmt = select(Schedule).where(Schedule.company_id == company_id)
    if not founder:
        schedule_stmt = schedule_stmt.where(
            Schedule.agent_id.in_([agent.id, *[r.id for r in reports]])
        )
    schedules = [
        StateSchedule(
            id=s.id,
            name=s.name,
            agent_id=s.agent_id,
            enabled=s.enabled,
            interval_seconds=s.interval_seconds,
            cron=s.cron,
            next_run_at=s.next_run_at,
        )
        for s in db.scalars(schedule_stmt.order_by(Schedule.created_at).limit(20))
    ]
    open_alerts = (
        [
            {"id": a.id, "severity": a.severity, "kind": a.kind, "title": a.title}
            for a in alerts.open_alerts(db, company_id, 10)
        ]
        if founder
        else []
    )
    note_block = None
    if founder:
        pending = notes.agent_list(db, rc, status="new", limit=5)
        note_block = StateNotes(unprocessed=notes.unprocessed_count(db, company_id), items=pending)
    mem = [
        StateMemory(id=m.id, kind=m.kind, title=m.title, body=clip(m.body, 400) or "")
        for m in memory.relevant_for(db, company_id, agent)
    ]
    proposals = []
    if founder:
        proposals = [
            StateMemory(id=m.id, kind=m.kind, title=m.title, body=clip(m.body, 400) or "")
            for m in db.scalars(
                select(MemoryEntry)
                .where(
                    MemoryEntry.company_id == company_id,
                    MemoryEntry.status == MemoryStatus.PROPOSED,
                    MemoryEntry.proposed_by_id != agent.id,
                )
                .order_by(MemoryEntry.created_at)
                .limit(8)
            )
        ]
    return AgentState(
        role=role,
        profile=prof.name,
        company={
            "id": company.id,
            "name": company.name,
            "status": company.status,
            "mission": clip(company.mission, 400),
            "strategic_goal": clip(company.strategic_goal, 400),
            "constraints": list(company.constraints or [])[:10],
            "you": {"id": agent.id, "name": agent.name, "role_title": agent.role_title},
        },
        goals=[
            StateGoal(
                id=g.id,
                title=g.title,
                status=g.status,
                priority=g.priority,
                description=clip(g.description, 300),
                owner_agent_id=g.owner_agent_id,
                open_projects=goal_projects.get(g.id, {}).get("open", 0),
                ended_projects=goal_projects.get(g.id, {}).get("ended", 0),
            )
            for g in goals
        ],
        projects=project_rows,
        tasks=tasks_block,
        failures_24h=[_task_row(t, names, task_clip) for t in failures],
        approvals_pending=approvals_rows,
        budget=budget,
        headcount={
            "live": len(live_agents),
            "max": limits.max_headcount,
            "by_family": families,
        },
        agents=agent_rows,
        hiring_requests=[_task_row(t, names, task_clip) for t in hiring_rows],
        decisions=decisions,
        memory=mem,
        memory_proposals=proposals,
        alerts=open_alerts,
        notes=note_block,
        schedules=schedules,
        standards=[StateStandard(**i) for i in standards.context_items(db, company_id, agent)],
        reflections_to_answer=reflections.attention(db, company_id) if founder else [],
        limits={
            "autonomy_mode": limits.autonomy_mode,
            "max_open_tasks": limits.max_open_tasks,
            "max_tasks_per_planning_cycle": limits.max_tasks_per_planning_cycle,
            "max_delegation_depth": limits.max_delegation_depth,
            "max_headcount": limits.max_headcount,
            "max_agent_monthly_budget_micros": limits.max_agent_monthly_budget_micros,
            "max_work_tasks_per_project": limits.max_work_tasks_per_project,
            "max_subtasks_per_task": limits.max_subtasks_per_task,
        },
    )
