"""Model routing (docs/specs/m6 section 6): which provider/model a run uses, and why.

``policy.model_routing``:

* ``pinned`` -- the agent's own provider and model, whatever they cost;
* ``free_first`` (default) -- a free model when one exists (a local model, or a provider priced at
  zero), else the agent's own paid one. Using the paid one is allowed when
  ``policy.allow_paid_fallback`` is true; otherwise it is a ``spend``-tier action
  (``model.paid_fallback``) that needs the Co-founder's or a human's approval, per the rules
  and ``policy.auto_spend_limit_micros``;
* ``quality_first`` -- the best paid model (the highest output price is the only quality
  signal we have; an agent's own paid model wins), else a free one. The owner chose cost to matter
  less here, so there is no approval step; budgets still apply.

Roles and models (M12, ``choose(..., task=...)``). When the dispatcher passes the task:

* a **management run** (the task is not ``work``, or the agent has a management role) always uses
  the management model (``policy.default_model_*``, chosen in AI setup). ``free_first`` never moves
  it to a free or local model, and because the owner picked that model it is not a "paid
  fallback" needing approval (budgets still apply). It is displaced only by an owner pin
  (``runtime_config.pin_model``) or when the management provider is disabled;
* a **work run** uses the optional work model (``policy.work_model_*``) when one is set: the
  owner's consent, like a pin. Without one, the mode above applies;
* ``task=None`` keeps the pre-M12 behaviour exactly.

Routing only applies to the company-agent runtime (the only one that talks to model providers). The
decision is stored on the run (``routing_reason``, ``model_provider_id``, ``model_used``), shown in
finance reports.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.enums import ModelRouting, TaskKind
from app.models.policy import CompanyPolicy
from app.models.provider import ModelProvider
from app.models.work import Task
from app.services import agent_roles, model_providers

COMPANY_AGENT = "company_agent"
CLI_PREFIX = "cli:"
# Roles that always run on the company agent with the management model (spec M10 section 2).
MANAGEMENT_ROLES = frozenset(
    {agent_roles.FOUNDER, agent_roles.HR, agent_roles.LEAD, agent_roles.MANAGER}
)


def choose_runtime(
    db: Session, agent: Agent, task: Task, limits: CompanyPolicy
) -> tuple[str, str | None]:
    """(runtime id, why) for a run of ``task`` by ``agent`` (docs/specs/m10 section 2).

    * ``api`` mode: the agent's own runtime, as before;
    * ``hybrid``: management roles (founder, HR, leads, managers) and every non-work task
      (planning, review, hiring, routines) stay on the company agent with the management model;
      ``kind=work`` tasks of everyone else go to ``policy.work_runtime`` (a coding CLI);
    * ``policy.cli_role_families`` (null = every family): a work task of a role family outside the
      list stays on the company agent runtime, which has web tools ("needs web", e.g. research);
    * a per-agent override wins and only an owner can set it: an agent whose runtime is itself a
      CLI, or whose ``runtime_config`` pins it (``pin_runtime: true``).
    """
    if agent.runtime.startswith(CLI_PREFIX):
        return agent.runtime, "agent runtime override"
    if (agent.runtime_config or {}).get("pin_runtime") is True:
        return agent.runtime, "agent runtime pinned by the owner"
    if limits.work_mode != "hybrid" or not limits.work_runtime:
        return agent.runtime, None
    if task.kind != TaskKind.WORK:
        return agent.runtime, f"hybrid: {task.kind} tasks stay on the company agent"
    if agent_roles.role_of(db, agent) in MANAGEMENT_ROLES:
        return agent.runtime, "hybrid: management roles stay on the company agent"
    if agent.runtime != COMPANY_AGENT:
        return agent.runtime, None  # not wired to the company agent: nothing to route away from
    families = limits.cli_role_families
    if families is not None and agent.role_family not in families:
        return (
            agent.runtime,
            f"hybrid: needs web ({agent.role_family} work stays on the company agent)",
        )
    return limits.work_runtime, f"hybrid: work task on {limits.work_runtime}"


@dataclass(frozen=True)
class Route:
    provider_id: str | None = None
    model: str | None = None
    reason: str | None = None
    free: bool = False  # no provider charge: nothing to reserve
    needs_paid_approval: bool = False  # paid fallback while policy forbids it without approval

    @property
    def routed(self) -> bool:
        return self.provider_id is not None


def is_free(provider: ModelProvider) -> bool:
    if provider.is_local:
        return True
    return provider.input_price_per_mtok_micros == 0 and provider.output_price_per_mtok_micros == 0


def _model_of(limits: CompanyPolicy, provider: ModelProvider) -> str:
    if limits.default_model_provider_id == provider.id and limits.default_model_name:
        return limits.default_model_name
    return provider.default_model


def _candidates(db: Session, company_id: str, limits: CompanyPolicy) -> list[ModelProvider]:
    rows = list(
        db.scalars(
            select(ModelProvider)
            .where(ModelProvider.company_id == company_id, ModelProvider.enabled.is_(True))
            .order_by(ModelProvider.created_at, ModelProvider.id)
        )
    )
    allowed = list(limits.allowed_model_providers or [])
    if allowed:
        rows = [r for r in rows if r.id in allowed or r.name in allowed]
    rows.sort(key=lambda r: 0 if r.id == limits.default_model_provider_id else 1)
    return rows


def is_management_run(db: Session, agent: Agent, task: Task) -> bool:
    """Planning, review, hiring and routine tasks, and every task of a management role."""
    return task.kind != TaskKind.WORK or agent_roles.role_of(db, agent) in MANAGEMENT_ROLES


def _usable_provider(
    db: Session, company_id: str, provider_id: str | None, limits: CompanyPolicy
) -> ModelProvider | None:
    """The company's enabled provider ``provider_id`` (honouring ``allowed_model_providers``)."""
    if not provider_id:
        return None
    row = db.scalar(
        select(ModelProvider).where(
            ModelProvider.id == provider_id,
            ModelProvider.company_id == company_id,
            ModelProvider.enabled.is_(True),
        )
    )
    if row is None:
        return None
    allowed = list(limits.allowed_model_providers or [])
    if allowed and row.id not in allowed and row.name not in allowed:
        return None
    return row


def _role_route(db: Session, agent: Agent, limits: CompanyPolicy, task: Task) -> Route | None:
    """The management model for a management run, the work model for a work run (None: neither
    applies and the routing mode decides)."""
    if is_management_run(db, agent, task):
        row = _usable_provider(db, agent.company_id, limits.default_model_provider_id, limits)
        if row is None:
            return None  # the management provider is disabled or gone: fall back to the mode
        return Route(
            row.id,
            _model_of(limits, row),
            f"management: the management model ({row.name})",
            free=is_free(row),
        )
    if limits.work_model_provider_id and limits.work_model_name:
        row = _usable_provider(db, agent.company_id, limits.work_model_provider_id, limits)
        if row is None:
            return None
        return Route(
            row.id,
            limits.work_model_name,
            f"work model: set by the owner ({row.name})",
            free=is_free(row),
        )
    return None


def choose(
    db: Session,
    agent: Agent,
    limits: CompanyPolicy,
    runtime: str | None = None,
    task: Task | None = None,
) -> Route:
    runtime = runtime or agent.runtime
    if runtime != COMPANY_AGENT:
        return Route()
    try:
        own = model_providers.resolve_for_agent(db, agent)
    except model_providers.ModelResolutionError:
        return Route()  # the adapter reports the configuration error as it always did
    own_row = db.get(ModelProvider, own.provider_id)
    own_free = own_row is not None and is_free(own_row)
    own_route = Route(own.provider_id, own.model, None, free=own_free)

    if task is not None:
        if (agent.runtime_config or {}).get("pin_model") is True:
            return Route(
                own.provider_id, own.model, "pinned by the owner: the agent's own model", own_free
            )
        role_route = _role_route(db, agent, limits, task)
        if role_route is not None:
            return role_route

    mode = limits.model_routing
    if mode == ModelRouting.PINNED:
        return Route(own.provider_id, own.model, "pinned: the agent's own model", free=own_free)

    others = _candidates(db, agent.company_id, limits)
    free_list = [p for p in others if is_free(p)]
    if mode == ModelRouting.QUALITY_FIRST:
        paid = [p for p in others if not is_free(p)]
        if own_row is not None and not own_free:
            return Route(
                own.provider_id, own.model, "quality_first: the agent's own paid model", free=False
            )
        if paid:
            best = max(paid, key=lambda p: p.output_price_per_mtok_micros or 0)
            return Route(
                best.id,
                _model_of(limits, best),
                f"quality_first: the best paid model ({best.name})",
                free=False,
            )
        return Route(
            own.provider_id,
            own.model,
            "quality_first: no paid model exists; used the free one",
            free=own_free,
        )

    # free_first
    if own_free:
        return Route(
            own.provider_id, own.model, "free_first: the agent's own model is free", free=True
        )
    if free_list:
        pick = free_list[0]
        return Route(
            pick.id,
            _model_of(limits, pick),
            f"free_first: used the free model {pick.name} instead of paid {own.provider_name}",
            free=True,
        )
    if limits.allow_paid_fallback:
        return Route(
            own_route.provider_id,
            own_route.model,
            "free_first: no free model exists; paid fallback is allowed by policy",
            free=False,
        )
    return Route(
        own_route.provider_id,
        own_route.model,
        "free_first: no free model exists; the paid fallback needs approval",
        free=False,
        needs_paid_approval=True,
    )
