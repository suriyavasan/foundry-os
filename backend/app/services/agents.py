"""Agent (AI employee) business rules: lifecycle, hierarchy, self-modification guard."""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import (
    Conflict,
    Forbidden,
    InvalidTransition,
    LimitExceeded,
    NotFound,
    ValidationFailed,
)
from app.core.principal import CompanyContext, Role, require_human_admin
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.department import Department
from app.models.enums import LIVE_AGENT_STATUSES, AgentKind, AgentStatus, RoleFamily
from app.schemas.agent import AgentCreate, AgentUpdate, LifecycleAction
from app.services import audit, events, policy, role_templates, tasks
from app.services import skills as skills_service
from app.services.pagination import paginate

# Fields that gate what an agent may do or spend. Agents can never change these on
# themselves, and only humans (owner/admin) may change them on anyone.
ENFORCED_FIELDS = frozenset(
    {
        "tool_permissions",
        "monthly_budget_micros",
        "daily_budget_micros",
        "runtime",
        "runtime_config",
    }
)
DESCRIPTIVE_FIELDS = (
    "name",
    "role_title",
    "department_id",
    "manager_agent_id",
    "responsibilities",
    "instructions",
    "model_provider",
    "model_name",
)
PATCHABLE_FIELDS = DESCRIPTIVE_FIELDS + tuple(sorted(ENFORCED_FIELDS))

# action -> (allowed source statuses, target status, audit action, verb for summaries)
TRANSITIONS: dict[str, tuple[frozenset[AgentStatus], AgentStatus, str, str]] = {
    "activate": (
        frozenset({AgentStatus.PROPOSED}),
        AgentStatus.ACTIVE,
        "agent.activated",
        "Activated",
    ),
    "reject": (
        frozenset({AgentStatus.PROPOSED}),
        AgentStatus.REJECTED,
        "agent.rejected",
        "Rejected",
    ),
    "pause": (frozenset({AgentStatus.ACTIVE}), AgentStatus.PAUSED, "agent.paused", "Paused"),
    "resume": (frozenset({AgentStatus.PAUSED}), AgentStatus.ACTIVE, "agent.resumed", "Resumed"),
    "retire": (
        frozenset({AgentStatus.ACTIVE, AgentStatus.PAUSED}),
        AgentStatus.RETIRED,
        "agent.retired",
        "Retired",
    ),
}

# A config key is secret-looking if any of its words (split on _ - . space and camelCase)
# is one of these. Word-based so that e.g. ``max_tokens`` or ``keyboard`` are not flagged.
# Keep in sync with web/src/lib/secrets.ts.
SECRET_WORDS = frozenset(
    {
        "key",
        "apikey",
        "privatekey",
        "secret",
        "secrets",
        "token",
        "password",
        "passwd",
        "pwd",
        "passphrase",
        "credential",
        "credentials",
        "bearer",
        "cookie",
        "authorization",
    }
)
_WORD_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")


def looks_like_secret_key(name: str) -> bool:
    words = (w.lower() for w in _WORD_SPLIT.split(name) if w)
    return any(w in SECRET_WORDS for w in words)


_MAX_CONFIG_BYTES = 20_000
_TERMINAL = (AgentStatus.RETIRED, AgentStatus.REJECTED)


# ---------------------------------------------------------------- validation helpers


def _find_secret_paths(value: Any, path: tuple[str | int, ...] = ()) -> list[tuple[str | int, ...]]:
    found: list[tuple[str | int, ...]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = (*path, str(key))
            if looks_like_secret_key(str(key)):
                found.append(child_path)
            found.extend(_find_secret_paths(child, child_path))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            found.extend(_find_secret_paths(child, (*path, i)))
    return found


def validate_runtime_config(config: dict[str, Any]) -> None:
    """runtime_config is visible to agents/the UI: it must never carry secrets."""
    bad = _find_secret_paths(config)
    if bad:
        raise ValidationFailed(
            "runtime_config must not contain secrets; store credentials in the secret store",
            details=[
                {
                    "loc": ["body", "runtime_config", *p],
                    "msg": "key looks like a secret; store credentials in the secret store",
                }
                for p in bad
            ],
        )
    if len(json.dumps(config, default=str)) > _MAX_CONFIG_BYTES:
        raise ValidationFailed(
            "runtime_config is too large",
            details=[{"loc": ["body", "runtime_config"], "msg": "must be under 20KB"}],
        )


def _get_in_company(db: Session, ctx: CompanyContext, agent_id: str) -> Agent:
    agent = db.scalar(select(Agent).where(Agent.id == agent_id, Agent.company_id == ctx.company_id))
    if agent is None:
        raise NotFound("Agent not found")
    return agent


def _check_department(db: Session, ctx: CompanyContext, department_id: str) -> None:
    found = db.scalar(
        select(Department.id).where(
            Department.id == department_id, Department.company_id == ctx.company_id
        )
    )
    if found is None:
        raise NotFound("Department not found")


def _check_manager(
    db: Session, ctx: CompanyContext, manager_id: str, subject_id: str | None
) -> None:
    """Manager must be in the same company, still in the org, and must not create a cycle."""
    manager = _get_manager(db, ctx, manager_id)
    if manager.status in _TERMINAL:
        raise Conflict("A retired or rejected agent cannot be a manager")
    if subject_id is None:
        return
    seen: set[str] = set()
    current: Agent | None = manager
    while current is not None and current.id not in seen:
        if current.id == subject_id:
            raise Conflict("Manager assignment would create a reporting cycle")
        seen.add(current.id)
        current = (
            db.scalar(
                select(Agent).where(
                    Agent.id == current.manager_agent_id, Agent.company_id == ctx.company_id
                )
            )
            if current.manager_agent_id
            else None
        )


def _get_manager(db: Session, ctx: CompanyContext, manager_id: str) -> Agent:
    manager = db.scalar(
        select(Agent).where(Agent.id == manager_id, Agent.company_id == ctx.company_id)
    )
    if manager is None:
        raise NotFound("Manager agent not found")
    return manager


def _live_founder_exists(db: Session, ctx: CompanyContext, exclude_id: str | None = None) -> bool:
    stmt = select(func.count(Agent.id)).where(
        Agent.company_id == ctx.company_id,
        Agent.kind == AgentKind.FOUNDER,
        Agent.status.in_(LIVE_AGENT_STATUSES),
    )
    if exclude_id:
        stmt = stmt.where(Agent.id != exclude_id)
    return bool(db.scalar(stmt))


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("Agent conflicts with an existing record (e.g. a second founder)") from exc


# ---------------------------------------------------------------- queries


def list_agents(
    db: Session,
    ctx: CompanyContext,
    *,
    limit: int,
    cursor: str | None,
    status: str | None = None,
    department_id: str | None = None,
) -> tuple[list[Agent], str | None]:
    stmt = select(Agent).where(Agent.company_id == ctx.company_id)
    if status:
        stmt = stmt.where(Agent.status == status)
    if department_id:
        stmt = stmt.where(Agent.department_id == department_id)
    return paginate(db, stmt, Agent, limit=limit, cursor=cursor, descending=False)


def get_agent(db: Session, ctx: CompanyContext, agent_id: str) -> Agent:
    return _get_in_company(db, ctx, agent_id)


# ---------------------------------------------------------------- mutations


def create_agent(db: Session, ctx: CompanyContext, data: AgentCreate) -> Agent:
    if ctx.role == Role.VIEWER:
        raise Forbidden("Viewers have read-only access")
    fields = data.model_fields_set
    if ctx.principal.is_agent:
        # Agents may only *propose* ordinary colleagues; permissions/budgets/runtime and the
        # founder seat are human decisions.
        if fields & ENFORCED_FIELDS or data.kind == AgentKind.FOUNDER:
            raise Forbidden("Agents cannot set permissions, budgets or runtime, or create founders")
    elif not ctx.is_human_admin:
        raise Forbidden("Only company owners and admins can create agents")

    template = None
    if data.role_template or data.skill_slugs:
        if ctx.principal.is_agent:
            raise Forbidden("Agents cannot apply role templates or skills; ask HR (hiring request)")
        if data.role_template:
            template = role_templates.get_template(db, ctx.company_id, data.role_template)
    defaults = template.spec if template else None
    kind = data.kind if "kind" in fields or defaults is None else defaults.default_kind.value
    if defaults is not None and "tool_permissions" not in fields:
        data = data.model_copy(update={"tool_permissions": list(defaults.default_toolsets)})

    validate_runtime_config(data.runtime_config)
    if ctx.principal.is_agent:  # an autonomous hire may not exceed the company's headcount cap
        limits = policy.load_policy(db, ctx.company_id)
        headcount = db.scalar(
            select(func.count(Agent.id)).where(
                Agent.company_id == ctx.company_id, Agent.status.in_(LIVE_AGENT_STATUSES)
            )
        )
        if (headcount or 0) >= limits.max_headcount:
            raise LimitExceeded(
                f"Headcount limit of {limits.max_headcount} reached",
                details={"limit": "max_headcount", "max": limits.max_headcount},
            )
    if kind == AgentKind.FOUNDER and _live_founder_exists(db, ctx):
        raise Conflict("This company already has a founder")
    department_id = data.department_id
    if department_id:
        _check_department(db, ctx, department_id)
    elif defaults is not None and "department_id" not in fields and defaults.default_department:
        from app.services import hiring  # local: hiring -> approvals -> agents

        department_id = hiring._find_or_create_department(
            db, ctx.company_id, defaults.default_department, ctx.principal
        ).id
    if data.manager_agent_id:
        _check_manager(db, ctx, data.manager_agent_id, None)

    agent = Agent(
        company_id=ctx.company_id,
        name=data.name,
        role_title=data.role_title,
        kind=kind,
        status=AgentStatus.PROPOSED,
        department_id=department_id,
        manager_agent_id=data.manager_agent_id,
        reports_to_human=data.reports_to_human,
        responsibilities=(
            defaults.description or None
            if defaults is not None and "responsibilities" not in fields
            else data.responsibilities
        ),
        instructions=(
            defaults.instructions or None
            if defaults is not None and "instructions" not in fields
            else data.instructions
        ),
        role_template=defaults.slug if defaults is not None else None,
        role_family=(
            defaults.family.value
            if defaults is not None
            else (
                RoleFamily.FOUNDER.value if kind == AgentKind.FOUNDER else RoleFamily.GENERIC.value
            )
        ),
        model_provider=data.model_provider,
        model_name=data.model_name,
        runtime=data.runtime,
        runtime_config=dict(data.runtime_config),
        tool_permissions=list(data.tool_permissions),
        monthly_budget_micros=data.monthly_budget_micros,
        daily_budget_micros=data.daily_budget_micros,
        status_changed_at=utcnow(),
    )
    db.add(agent)
    db.flush()
    if defaults is not None or data.skill_slugs:
        wanted = list(
            dict.fromkeys(
                [*(defaults.default_skill_slugs if defaults else ()), *(data.skill_slugs or [])]
            )
        )
        skills_service.assign_slugs_internal(
            db, ctx.company_id, agent, wanted, principal=ctx.principal
        )
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="agent.created",
        target_type="agent",
        target_id=agent.id,
        summary=f"Proposed {agent.kind} agent {agent.name} ({agent.role_title})",
        data={"kind": agent.kind, "status": agent.status, "role_template": agent.role_template},
    )
    events.emit(db, ctx.company_id, "agent.updated", {"agent_id": agent.id, "status": agent.status})
    _commit(db)
    return agent


def update_agent(db: Session, ctx: CompanyContext, agent_id: str, data: AgentUpdate) -> Agent:
    if ctx.role == Role.VIEWER:
        raise Forbidden("Viewers have read-only access")
    fields = data.model_fields_set
    enforced = fields & ENFORCED_FIELDS
    principal = ctx.principal
    if principal.is_agent:
        if principal.id == agent_id and enforced:
            raise Forbidden(
                "Agents cannot modify their own tool permissions, budgets or runtime settings"
            )
        if enforced:
            raise Forbidden("Only humans can change tool permissions, budgets or runtime settings")
    elif not ctx.is_human_admin:
        raise Forbidden("Only company owners and admins can edit agents")

    agent = _get_in_company(db, ctx, agent_id)
    if agent.status == AgentStatus.RETIRED:
        raise Conflict("Retired agents are read-only")

    if "runtime_config" in fields:
        validate_runtime_config(data.runtime_config or {})
    if "department_id" in fields and data.department_id:
        _check_department(db, ctx, data.department_id)
    if "manager_agent_id" in fields and data.manager_agent_id:
        _check_manager(db, ctx, data.manager_agent_id, agent.id)

    changed: list[str] = []
    for field in PATCHABLE_FIELDS:
        if field not in fields:
            continue
        new = getattr(data, field)
        if isinstance(new, dict):
            new = dict(new)
        elif isinstance(new, list):
            new = list(new)
        if getattr(agent, field) != new:
            setattr(agent, field, new)
            changed.append(field)
    if changed:
        agent.updated_at = utcnow()
        audit.record(
            db,
            company_id=ctx.company_id,
            principal=principal,
            action="agent.updated",
            target_type="agent",
            target_id=agent.id,
            summary=f"Updated agent {agent.name}: {', '.join(changed)}",
            data={"changed": changed},  # names only: never values (may be large / sensitive)
        )
        events.emit(
            db, ctx.company_id, "agent.updated", {"agent_id": agent.id, "status": agent.status}
        )
        if {"monthly_budget_micros", "daily_budget_micros"} & set(changed):
            tasks.release_budget_blocked(db, ctx.company_id, principal)
        if "model_provider" in changed:
            tasks.release_budget_blocked(
                db, ctx.company_id, principal, codes=("provider_not_allowed",)
            )
        _commit(db)
    return agent


def transition_agent(
    db: Session, ctx: CompanyContext, agent_id: str, action: str, body: LifecycleAction | None
) -> Agent:
    if action not in TRANSITIONS:
        raise NotFound("Unknown lifecycle action")
    principal = ctx.principal
    if principal.is_agent and principal.id == agent_id:
        raise Forbidden("Agents cannot change their own lifecycle status")
    require_human_admin(ctx, f"{action} agents")

    agent = _get_in_company(db, ctx, agent_id)
    allowed_from, target, audit_action, verb = TRANSITIONS[action]
    current = AgentStatus(agent.status)
    if current not in allowed_from:
        allowed = ", ".join(sorted(s.value for s in allowed_from))
        raise InvalidTransition(
            f"Cannot {action} an agent that is {current.value} (allowed from: {allowed})",
            details={"from": current.value, "action": action},
        )
    if (
        action == "retire"
        and agent.kind == AgentKind.FOUNDER
        and not _live_founder_exists(db, ctx, exclude_id=agent.id)
    ):
        raise Conflict("The only founder cannot be retired")

    data: dict[str, Any] = {"from": current.value, "to": target.value}
    if body and body.reason:
        data["reason"] = body.reason
    if action == "retire":
        cleared = list(
            db.scalars(
                select(Department.id).where(
                    Department.company_id == ctx.company_id, Department.lead_agent_id == agent.id
                )
            )
        )
        if cleared:
            db.execute(
                update(Department)
                .where(Department.id.in_(cleared), Department.company_id == ctx.company_id)
                .values(lead_agent_id=None)
            )
            data["cleared_department_lead_of"] = cleared

    now = utcnow()
    agent.status = target
    agent.status_changed_at = now
    agent.updated_at = now
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=principal,
        action=audit_action,
        target_type="agent",
        target_id=agent.id,
        summary=f"{verb} agent {agent.name}" + (f": {body.reason}" if body and body.reason else ""),
        data=data,
    )
    events.emit(db, ctx.company_id, "agent.updated", {"agent_id": agent.id, "status": target.value})
    # Readiness depends on the assignee being active: pausing/retiring/activating re-evaluates.
    tasks.recompute_readiness(db, ctx.company_id, agent_id=agent.id, principal=principal)
    _commit(db)
    return agent
