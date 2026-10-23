"""HR and hiring: hires with policy-gated auto-activation, hiring requests, colleague search and
deterministic skill-gap detection.

Enforcement is code: only the active HR agent can hire; headcount and budget limits come from the
company policy; a hire becomes active on its own only when every auto-activation condition holds,
otherwise it stays ``proposed`` behind an Approval (kind ``hire``) whose decision activates (or
rejects) exactly that agent.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, LimitExceeded, NotFound
from app.core.principal import CompanyContext, Principal
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.company import Company
from app.models.department import Department
from app.models.enums import (
    LIVE_AGENT_STATUSES,
    PRIORITY_RANK,
    TERMINAL_TASK_STATUSES,
    AgentKind,
    AgentStatus,
    ApprovalStatus,
    AutonomyMode,
    CompanyStatus,
    Priority,
    RoleFamily,
    TaskKind,
    TaskStatus,
)
from app.models.governance import Approval
from app.models.policy import CompanyPolicy
from app.models.provider import ModelProvider
from app.models.skill import Skill
from app.models.work import Project, Task
from app.roles.loader import RESERVED_FAMILIES
from app.schemas.hr import HireCreate, HiringRequestCreate
from app.schemas.work import TaskCreate
from app.services import (
    approvals,
    audit,
    events,
    hr_access,
    policy,
    role_templates,
    skills,
    tasks,
)
from app.services.common import get_agent_or_404, get_task_or_404

WAITING_FOR_HIRE = "waiting_for_hire"
MAX_OPEN_HIRING_TASKS_PER_AGENT = 5
OPEN_TASK_STATUSES = [s for s in TaskStatus if s not in TERMINAL_TASK_STATUSES]


# ---------------------------------------------------------------- policy math


def live_headcount(db: Session, company_id: str) -> int:
    return int(
        db.scalar(
            select(func.count(Agent.id)).where(
                Agent.company_id == company_id, Agent.status.in_(LIVE_AGENT_STATUSES)
            )
        )
        or 0
    )


def allocated_monthly_budget(db: Session, company_id: str) -> int:
    """Monthly budgets already handed to agents that are in the org (active, paused or retiring).
    Pending (proposed) hires carry their request in the approval and are not counted."""
    return int(
        db.scalar(
            select(func.coalesce(func.sum(Agent.monthly_budget_micros), 0)).where(
                Agent.company_id == company_id,
                Agent.status.in_([AgentStatus.ACTIVE, AgentStatus.PAUSED, AgentStatus.RETIRING]),
            )
        )
        or 0
    )


def auto_activation_blockers(
    db: Session,
    company_id: str,
    limits: CompanyPolicy,
    *,
    requested_monthly_micros: int | None,
    headcount_before: int,
    allocated_before: int,
) -> list[str]:
    """Why a hire may NOT be activated automatically (empty list = it may). The conditions:
    autonomous mode, ``auto_activate_hires``, ``hire`` not reserved for humans, headcount below
    the cap, and the requested budget fitting the company's monthly budget."""
    reasons: list[str] = []
    if limits.autonomy_mode != AutonomyMode.AUTONOMOUS:
        reasons.append("autonomy_mode_not_autonomous")
    if not limits.auto_activate_hires:
        reasons.append("auto_activate_hires_disabled")
    if "hire" in (limits.human_approval_required_for or []):
        reasons.append("hire_requires_human_approval")
    if headcount_before >= limits.max_headcount:
        reasons.append("headcount_limit")
    company_budget = limits.monthly_budget_micros
    if (
        company_budget is not None
        and allocated_before + (requested_monthly_micros or 0) > company_budget
    ):
        reasons.append("budget_does_not_fit")
    return reasons


# ---------------------------------------------------------------- activation


def activate_agent(
    db: Session, company_id: str, agent: Agent, principal: Principal | None, reason: str
) -> None:
    """proposed -> active outside the human lifecycle endpoint (auto-activation / approval).
    No commit. Afterwards, tasks waiting for this capability are assigned."""
    now = utcnow()
    agent.status = AgentStatus.ACTIVE
    agent.status_changed_at = now
    agent.updated_at = now
    audit.record(
        db,
        company_id=company_id,
        principal=principal,
        action="agent.activated",
        target_type="agent",
        target_id=agent.id,
        summary=f"Activated agent {agent.name}: {reason}",
        data={"from": "proposed", "to": "active", "reason": reason},
    )
    events.emit(db, company_id, "agent.updated", {"agent_id": agent.id, "status": "active"})
    tasks.recompute_readiness(db, company_id, agent_id=agent.id, principal=principal)
    resolve_skill_gaps(db, company_id, principal=principal)


def apply_hire_decision(
    db: Session, ctx: CompanyContext, approval: Approval, decision: str
) -> None:
    """Called inside ``approvals.decide``: approving a hire activates the proposed agent (once);
    rejecting it rejects the agent; requesting changes leaves it proposed. No commit."""
    agent = db.scalar(
        select(Agent).where(Agent.id == approval.subject_id, Agent.company_id == ctx.company_id)
    )
    if agent is None or agent.status != AgentStatus.PROPOSED:
        return
    if decision == "approve" and approval.status == ApprovalStatus.APPROVED:
        now = utcnow()
        approval.consumed_at = now  # an approval authorizes exactly one action
        activate_agent(db, ctx.company_id, agent, ctx.principal, "hire approved")
    elif decision == "reject":
        now = utcnow()
        agent.status = AgentStatus.REJECTED
        agent.status_changed_at = now
        agent.updated_at = now
        audit.record(
            db,
            company_id=ctx.company_id,
            principal=ctx.principal,
            action="agent.rejected",
            target_type="agent",
            target_id=agent.id,
            summary=f"Rejected agent {agent.name}: hire approval rejected",
            data={"from": "proposed", "to": "rejected", "approval_id": approval.id},
        )
        events.emit(
            db, ctx.company_id, "agent.updated", {"agent_id": agent.id, "status": "rejected"}
        )


# ---------------------------------------------------------------- hiring


@dataclass
class HireOutcome:
    agent: Agent
    auto_activated: bool
    approval: Approval | None
    reasons: list[str]
    skills: list[str]
    skipped_default_skills: list[str] = field(default_factory=list)


def _find_or_create_department(
    db: Session, company_id: str, name: str, principal: Principal | None
) -> Department:
    dept = db.scalar(
        select(Department).where(Department.company_id == company_id, Department.name == name)
    )
    if dept is not None:
        return dept
    dept = Department(company_id=company_id, name=name)
    db.add(dept)
    db.flush()
    audit.record(
        db,
        company_id=company_id,
        principal=principal,
        action="department.created",
        target_type="department",
        target_id=dept.id,
        summary=f"Created department {name}",
        data={"name": name},
    )
    return dept


def _live_founder(db: Session, company_id: str) -> Agent | None:
    return db.scalar(
        select(Agent).where(
            Agent.company_id == company_id,
            Agent.kind == AgentKind.FOUNDER,
            Agent.status.in_(LIVE_AGENT_STATUSES),
        )
    )


def hire(db: Session, ctx: CompanyContext, data: HireCreate) -> HireOutcome:
    hr = hr_access.require_hr(db, ctx, "hire employees")
    company_id = ctx.company_id
    limits = policy.load_policy(db, company_id)
    view = role_templates.get_template(db, company_id, data.role_template)
    spec = view.spec
    if spec.family.value in RESERVED_FAMILIES or spec.default_kind == AgentKind.FOUNDER:
        raise Forbidden("HR cannot hire into founder or HR roles")
    manager = get_agent_or_404(db, company_id, data.manager_agent_id, label="Manager")
    task = get_task_or_404(db, company_id, data.task_id)

    headcount = live_headcount(db, company_id)
    if headcount >= limits.max_headcount:
        raise LimitExceeded(
            f"Headcount limit of {limits.max_headcount} reached",
            details={"limit": "max_headcount", "max": limits.max_headcount},
        )

    explicit = list(data.skill_slugs)
    known = {
        s.slug: s
        for s in db.scalars(
            select(Skill).where(
                Skill.company_id == company_id,
                Skill.slug.in_([*explicit, *spec.default_skill_slugs]),
            )
        )
    }
    for slug in explicit:
        skill = known.get(slug)
        if skill is None:
            raise NotFound(f"Skill not found: {slug}")
        if skill.status != "active":
            raise Conflict(f"Skill {slug!r} is {skill.status}; only active skills can be assigned")
    default_slugs = [
        s for s in spec.default_skill_slugs if s in known and known[s].status == "active"
    ]
    skipped_defaults = [s for s in spec.default_skill_slugs if s not in default_slugs]
    slugs = list(dict.fromkeys([*default_slugs, *explicit]))

    department_name = data.department or spec.default_department
    dept = (
        _find_or_create_department(db, company_id, department_name, ctx.principal)
        if department_name
        else None
    )
    founder = _live_founder(db, company_id)
    monthly = (
        data.monthly_budget_micros
        if data.monthly_budget_micros is not None
        else limits.hire_default_monthly_budget_micros
    )
    if data.model_provider is not None:
        known_provider = db.scalar(
            select(ModelProvider.id).where(
                ModelProvider.id == data.model_provider,
                ModelProvider.company_id == company_id,
                ModelProvider.enabled.is_(True),
            )
        )
        if known_provider is None:
            raise NotFound("Model provider not found")
    # Inherit the founder's runtime and model; fall back to the company default model.
    runtime = founder.runtime if founder else "unassigned"
    provider_id = data.model_provider or (founder.model_provider if founder else None)
    model_name = data.model_name or (founder.model_name if founder else None)
    if runtime == "unassigned" and limits.default_model_provider_id:
        runtime = "company_agent"
        provider_id = provider_id or limits.default_model_provider_id
        model_name = model_name or limits.default_model_name
    now = utcnow()
    agent = Agent(
        company_id=company_id,
        name=data.name,
        role_title=data.role_title or spec.title,
        kind=spec.default_kind.value,
        status=AgentStatus.PROPOSED,
        department_id=dept.id if dept else None,
        manager_agent_id=manager.id,
        reports_to_human=False,
        responsibilities=spec.description or None,
        instructions=spec.instructions or None,
        model_provider=provider_id,
        model_name=model_name,
        runtime=runtime,
        runtime_config={},
        tool_permissions=list(spec.default_toolsets),
        monthly_budget_micros=monthly,
        role_template=spec.slug,
        role_family=spec.family.value,
        status_changed_at=now,
    )
    db.add(agent)
    db.flush()
    skills.assign_slugs_internal(db, company_id, agent, slugs, principal=ctx.principal, strict=True)

    reasons = auto_activation_blockers(
        db,
        company_id,
        limits,
        requested_monthly_micros=monthly,
        headcount_before=headcount,
        allocated_before=allocated_monthly_budget(db, company_id),
    )
    approval: Approval | None = None
    audit.record(
        db,
        company_id=company_id,
        principal=ctx.principal,
        action="hire.created",
        target_type="agent",
        target_id=agent.id,
        summary=f"{hr.name} hired {agent.name} as {agent.role_title} ({spec.slug})",
        data={
            "role_template": spec.slug,
            "manager_agent_id": manager.id,
            "department": department_name,
            "skills": slugs,
            "monthly_budget_micros": monthly,
            "task_id": task.id,
            "reason": data.reason,
            "auto_activate": not reasons,
            "blockers": reasons,
        },
    )
    events.emit(
        db,
        company_id,
        "hire.created",
        {"agent_id": agent.id, "role_template": spec.slug, "auto_activated": not reasons},
    )
    events.emit(db, company_id, "agent.updated", {"agent_id": agent.id, "status": agent.status})
    if reasons:
        approval = approvals.create_approval_record(
            db,
            company_id=company_id,
            principal=ctx.principal,
            kind="hire",
            title=f"Hire {agent.name} as {agent.role_title}",
            reason=data.reason,
            risk="low",
            evidence={
                "role_template": spec.slug,
                "manager_agent_id": manager.id,
                "skills": slugs,
                "monthly_budget_micros": monthly,
                "not_auto_activated_because": reasons,
                "headcount": headcount + 1,
                "max_headcount": limits.max_headcount,
            },
            scope={"action": "activate_agent", "agent_id": agent.id},
            task_id=task.id,
            subject=("agent_hire", agent.id),
        )
    else:
        activate_agent(db, company_id, agent, ctx.principal, "hire within policy (auto-activated)")
    db.commit()
    return HireOutcome(
        agent=agent,
        auto_activated=not reasons,
        approval=approval,
        reasons=reasons,
        skills=slugs,
        skipped_default_skills=skipped_defaults,
    )


# ---------------------------------------------------------------- hiring requests


def _hr_agent(db: Session, company_id: str) -> Agent | None:
    return db.scalar(
        select(Agent)
        .where(
            Agent.company_id == company_id,
            Agent.role_family == RoleFamily.HR,
            Agent.status == AgentStatus.ACTIVE,
        )
        .order_by(Agent.created_at, Agent.id)
    )


def hiring_key(source_task_id: str, required: list[str]) -> str:
    digest = hashlib.sha1(",".join(sorted(set(required))).encode()).hexdigest()[:12]  # noqa: S324
    return f"hiring:{source_task_id}:{digest}"


def ensure_hiring_task(
    db: Session,
    company_id: str,
    source: Task,
    required: list[str],
    *,
    role_hint: str | None,
    reason: str | None,
    principal: Principal | None,
) -> tuple[Task, bool]:
    """The hiring task for (source task, skills): created once, assigned to the active HR agent."""
    hr = _hr_agent(db, company_id)
    if hr is None:
        raise Conflict("This company has no active HR agent to take the hiring request")
    required = sorted({r.strip().lower() for r in required if r.strip()})
    lines = [
        f"Required skills: {', '.join(required)}",
        f"Source task: {source.title} ({source.id})",
    ]
    if role_hint:
        lines.append(f"Role hint: {role_hint}")
    if reason:
        lines.append(f"Reason: {reason}")
    lines.append("Follow the hiring-playbook skill; complete this task with a short summary.")
    data = TaskCreate(
        title=f"Hire: {role_hint or ', '.join(required)}"[:300],
        description="\n".join(lines),
        kind=TaskKind.HIRING.value,
        priority=source.priority,
        assignee_agent_id=hr.id,
        idempotency_key=hiring_key(source.id, required),
    )
    task, created = tasks._create_task_core(db, company_id, principal, data, run=None)
    if created:
        audit.record(
            db,
            company_id=company_id,
            principal=principal,
            action="hire.requested",
            target_type="task",
            target_id=task.id,
            summary=f"Requested a hire for skills {', '.join(required)} (task '{source.title}')",
            data={"source_task_id": source.id, "required_skills": required, "hr_agent_id": hr.id},
        )
        events.emit(
            db,
            company_id,
            "hire.requested",
            {"hiring_task_id": task.id, "source_task_id": source.id},
        )
    return task, created


def request_hire(db: Session, ctx: CompanyContext, data: HiringRequestCreate) -> tuple[Task, bool]:
    """Agent API: a manager or the founder asks HR for a hire (idempotent per task + skills)."""
    if not ctx.principal.is_agent:
        raise Forbidden("Hiring requests come from agents; humans can create hiring tasks directly")
    agent = db.scalar(
        select(Agent).where(Agent.id == ctx.principal.id, Agent.company_id == ctx.company_id)
    )
    if agent is None or agent.status != AgentStatus.ACTIVE:
        raise Forbidden("Only active agents can request hires")
    has_reports = bool(
        db.scalar(
            select(func.count(Agent.id)).where(
                Agent.company_id == ctx.company_id,
                Agent.manager_agent_id == agent.id,
                Agent.status.in_(LIVE_AGENT_STATUSES),
            )
        )
    )
    if not (agent.kind in (AgentKind.FOUNDER, AgentKind.LEAD) or has_reports):
        raise Forbidden("Only managers and the founder can request hires")
    source = get_task_or_404(db, ctx.company_id, data.task_id)
    open_requests = int(
        db.scalar(
            select(func.count(Task.id)).where(
                Task.company_id == ctx.company_id,
                Task.kind == TaskKind.HIRING,
                Task.created_by_type == "agent",
                Task.created_by_id == agent.id,
                Task.status.in_(OPEN_TASK_STATUSES),
            )
        )
        or 0
    )
    key = hiring_key(source.id, data.required_skills)
    already = db.scalar(
        select(Task).where(Task.company_id == ctx.company_id, Task.idempotency_key == key)
    )
    if already is None and open_requests >= MAX_OPEN_HIRING_TASKS_PER_AGENT:
        raise LimitExceeded(
            f"You already have {open_requests} open hiring requests",
            details={"limit": "open_hiring_requests", "max": MAX_OPEN_HIRING_TASKS_PER_AGENT},
        )
    task, created = ensure_hiring_task(
        db,
        ctx.company_id,
        source,
        data.required_skills,
        role_hint=data.role_hint,
        reason=data.reason,
        principal=ctx.principal,
    )
    db.commit()
    return task, created


# ---------------------------------------------------------------- colleagues


def find_colleagues(
    db: Session,
    ctx: CompanyContext,
    *,
    skill_tags: list[str],
    family: str | None,
    status: str | None,
    limit: int = 100,
) -> list[dict]:
    stmt = select(Agent).where(
        Agent.company_id == ctx.company_id,
        Agent.status == (status or AgentStatus.ACTIVE),
    )
    if family:
        stmt = stmt.where(Agent.role_family == family)
    agents = list(db.scalars(stmt.order_by(Agent.created_at, Agent.id)))
    ids = [a.id for a in agents]
    caps = skills.capability_map(db, ctx.company_id, ids)
    load = _open_task_counts(db, ctx.company_id, ids)
    wanted = {t.strip().lower() for t in skill_tags if t.strip()}
    out = []
    for a in agents:
        slugs, tags = caps.get(a.id, (set(), set()))
        if wanted and not wanted <= tags:
            continue
        out.append(
            {
                "id": a.id,
                "name": a.name,
                "role_title": a.role_title,
                "role_family": a.role_family,
                "role_template": a.role_template,
                "kind": a.kind,
                "status": a.status,
                "department_id": a.department_id,
                "skills": sorted(slugs),
                "skill_tags": sorted(tags),
                "open_tasks": load.get(a.id, 0),
            }
        )
    out.sort(key=lambda c: (c["open_tasks"], c["name"].lower(), c["id"]))
    return out[:limit]


def _open_task_counts(db: Session, company_id: str, agent_ids: list[str]) -> dict[str, int]:
    if not agent_ids:
        return {}
    rows = db.execute(
        select(Task.assignee_agent_id, func.count(Task.id))
        .where(
            Task.company_id == company_id,
            Task.assignee_agent_id.in_(agent_ids),
            Task.status.in_(OPEN_TASK_STATUSES),
        )
        .group_by(Task.assignee_agent_id)
    ).all()
    return {agent_id: int(n) for agent_id, n in rows}


# ---------------------------------------------------------------- gap detection


@dataclass
class GapReport:
    assigned: list[tuple[str, str]] = field(default_factory=list)  # (task id, agent id)
    requested: list[str] = field(default_factory=list)  # hiring task ids created now
    waiting: list[str] = field(default_factory=list)  # task ids parked "waiting_for_hire"


def resolve_skill_gaps(
    db: Session,
    company_id: str,
    *,
    principal: Principal | None = None,
    now: datetime | None = None,
) -> GapReport:
    """Deterministic. For each unassigned backlog task with ``required_skills`` whose
    dependencies are done: assign the best active agent whose skill tags cover the requirement
    (same department as the project lead first, then least loaded, then oldest); if nobody
    qualifies, create the hiring request once and park the task as ``waiting_for_hire``.
    Idempotent; no commit."""
    report = GapReport()
    db.flush()
    company_status = db.scalar(select(Company.status).where(Company.id == company_id))
    if company_status != CompanyStatus.ACTIVE:
        return report
    candidates = [
        t
        for t in db.scalars(
            select(Task).where(
                Task.company_id == company_id,
                Task.status == TaskStatus.BACKLOG,
                Task.assignee_agent_id.is_(None),
                Task.kind != TaskKind.HIRING,
            )
        )
        if t.required_skills
    ]
    if not candidates:
        return report
    unfinished = tasks._unfinished_dependency_counts(db, [t.id for t in candidates])
    candidates = [t for t in candidates if unfinished.get(t.id, 0) == 0]
    rank = {p.value: PRIORITY_RANK[p] for p in Priority}
    candidates.sort(key=lambda t: (-rank.get(t.priority, 1), t.created_at, t.id))

    agents = list(
        db.scalars(
            select(Agent)
            .where(Agent.company_id == company_id, Agent.status == AgentStatus.ACTIVE)
            .order_by(Agent.created_at, Agent.id)
        )
    )
    caps = skills.capability_map(db, company_id, [a.id for a in agents])
    load = _open_task_counts(db, company_id, [a.id for a in agents])
    lead_dept: dict[str, str | None] = {}
    for project_id in {t.project_id for t in candidates if t.project_id}:
        lead_dept[project_id] = db.scalar(
            select(Agent.department_id)
            .join(Project, Project.lead_agent_id == Agent.id)
            .where(Project.id == project_id, Project.company_id == company_id)
        )

    for task in candidates:
        required = {r.strip().lower() for r in task.required_skills if r.strip()}
        if not required:
            continue
        want_dept = lead_dept.get(task.project_id) if task.project_id else None
        qualified = [a for a in agents if required <= caps.get(a.id, (set(), set()))[1]]
        if qualified:
            best = min(
                qualified,
                key=lambda a: (
                    0 if want_dept and a.department_id == want_dept else 1,
                    load.get(a.id, 0),
                    a.created_at,
                    a.id,
                ),
            )
            task.assignee_agent_id = best.id
            task.blocked_reason = None
            task.updated_at = now or utcnow()
            load[best.id] = load.get(best.id, 0) + 1
            audit.record(
                db,
                company_id=company_id,
                principal=principal,
                action="task.assigned",
                target_type="task",
                target_id=task.id,
                summary=f"Assigned task '{task.title}' to {best.name} (skills match)",
                data={"assignee_agent_id": best.id, "required_skills": sorted(required)},
            )
            events.emit(db, company_id, "task.updated", tasks.task_event_payload(task))
            tasks.recompute_readiness(
                db, company_id, task_ids=[task.id], principal=principal, now=now
            )
            report.assigned.append((task.id, best.id))
            continue
        try:
            hiring_task, created = ensure_hiring_task(
                db,
                company_id,
                task,
                sorted(required),
                role_hint=None,
                reason="No active employee has the skills this task requires.",
                principal=principal,
            )
        except Conflict:
            continue  # no HR agent yet: try again on the next pass
        if created:
            report.requested.append(hiring_task.id)
        if task.blocked_reason != WAITING_FOR_HIRE:
            task.blocked_reason = WAITING_FOR_HIRE
            task.updated_at = now or utcnow()
            events.emit(db, company_id, "task.updated", tasks.task_event_payload(task))
        report.waiting.append(task.id)
    return report
